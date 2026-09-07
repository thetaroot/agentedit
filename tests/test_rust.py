"""Rust backend: extraction + end-to-end index/impact."""
from __future__ import annotations

from pathlib import Path

from agentedit.analyze.impact import impact
from agentedit.backends.rust import module_id, parse_file
from agentedit.index.indexer import index_repo
from agentedit.model import ParsedFile
from agentedit.store.sqlite import GraphStore


def _parse(relpath: str) -> ParsedFile:
    with open(f"tests/fixtures/eval_rust/{relpath}", "rb") as fh:
        return parse_file(relpath, fh.read())


def test_rust_module_ids() -> None:
    assert module_id("src/format.rs") == "format"
    assert module_id("src/pkg/mod.rs") == "pkg"
    assert module_id("src/pkg/bar.rs") == "pkg.bar"
    assert module_id("src/lib.rs") == ""


def test_rust_pub_export_and_use() -> None:
    parsed = _parse("src/service.rs")
    names = {s.name: s for s in parsed.symbols}
    assert names["enqueue_payment"].exported is True
    imports = {i.local: (i.imported, i.spec) for i in parsed.imports}
    assert imports["format_money"] == ("format_money", "format")
    calls = [(e.source, e.target) for e in parsed.edges if e.kind == "calls"]
    assert ("service.payment_summary", "format_money") in calls


def test_rust_index_and_impact(tmp_path: Path) -> None:
    src = tmp_path / "src"
    src.mkdir(parents=True)
    (src / "lib.rs").write_text("mod a;\nmod b;\n")
    (src / "a.rs").write_text("pub fn helper() -> u32 { 1 }\n")
    (src / "b.rs").write_text("use crate::a::helper;\npub fn run() -> u32 { helper() }\n")
    db = str(tmp_path / "g.db")
    summary = index_repo(str(tmp_path), db)
    assert summary["language"] == "rust"
    store = GraphStore(db).connect()
    try:
        report = impact(store, "a.helper")
        assert "b.run" in {a.qname for a in report.direct}
    finally:
        store.close()
