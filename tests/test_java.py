"""Java backend: extraction + end-to-end index/impact."""
from __future__ import annotations

from pathlib import Path

from agentedit.analyze.impact import impact
from agentedit.backends.java import module_id, parse_file
from agentedit.index.indexer import index_repo
from agentedit.model import ParsedFile
from agentedit.store.sqlite import GraphStore

FIXTURE = "tests/fixtures/eval_java"


def _parse(relpath: str) -> ParsedFile:
    with open(f"{FIXTURE}/{relpath}", "rb") as fh:
        return parse_file(relpath, fh.read())


def test_java_module_id_and_public_class() -> None:
    assert module_id("app/format/MoneyFormatter.java") == "app.format"
    parsed = _parse("app/format/MoneyFormatter.java")
    classes = {s.name: s for s in parsed.symbols if s.kind == "class"}
    assert "MoneyFormatter" in classes
    assert classes["MoneyFormatter"].exported is True
    methods = {s.name for s in parsed.symbols if s.kind == "method"}
    assert "formatMoney" in methods


def test_java_imports_and_calls() -> None:
    parsed = _parse("app/service/PaymentService.java")
    imports = {i.local: (i.imported, i.spec) for i in parsed.imports}
    assert imports["MoneyFormatter"] == ("MoneyFormatter", "app.format")
    calls = [(e.source, e.target) for e in parsed.edges if e.kind == "calls"]
    assert ("app.service.PaymentService.paymentSummary", "MoneyFormatter.formatMoney") in calls


def test_java_index_and_impact(tmp_path: Path) -> None:
    import shutil

    repo = tmp_path / "work"
    shutil.copytree(FIXTURE, repo, dirs_exist_ok=True)
    db = str(tmp_path / "g.db")
    summary = index_repo(str(repo), db)
    assert summary["language"] == "java"
    store = GraphStore(db).connect()
    try:
        report = impact(store, "app.format.MoneyFormatter.formatMoney")
        direct = {a.qname for a in report.direct}
        assert "app.service.PaymentService.paymentSummary" in direct
    finally:
        store.close()
