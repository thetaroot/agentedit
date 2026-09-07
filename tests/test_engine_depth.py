"""Phase-2 engine depth: uses_type edges, member-diff, why-chains."""
from __future__ import annotations

from pathlib import Path

from agentedit.analyze.impact import dependents, would_break
from agentedit.backends.python import parse_file as py_parse
from agentedit.backends.typescript import parse_file as ts_parse
from agentedit.index.indexer import index_repo
from agentedit.store.sqlite import GraphStore


def _write_ts_project(tmp_path: Path) -> None:
    (tmp_path / "a.ts").write_text(
        "export interface Job { id: string; priority: number; }\n"
        "export function touch(job: Job): string { return job.id; }\n"
    )
    (tmp_path / "b.ts").write_text(
        "import { Job, touch } from './a';\n"
        "export function run(): string {\n"
        "  const job: Job = { id: 'x', priority: 1 };\n"
        "  return touch(job);\n"
        "}\n"
    )


def test_uses_type_edge_ts(tmp_path: Path) -> None:
    _write_ts_project(tmp_path)
    db = str(tmp_path / "g.db")
    index_repo(str(tmp_path), db)
    store = GraphStore(db).connect()
    try:
        kinds = {e["kind"] for e in store.edges_to("a.Job")}
        assert "uses_type" in kinds
        sources = {e["source_qname"] for e in store.edges_to("a.Job", ("uses_type",))}
        assert "a.touch" in sources
    finally:
        store.close()


def test_member_diff_and_dependants(tmp_path: Path) -> None:
    _write_ts_project(tmp_path)
    db = str(tmp_path / "g.db")
    index_repo(str(tmp_path), db)
    store = GraphStore(db).connect()
    try:
        report = would_break(store, "a.Job", change="members", new_signature="{ id: string }")
        assert report.risk == "high"
        assert any("priority" in n for n in report.notes)
        qnames = {a.qname for a in report.direct}
        assert "a.touch" in qnames  # annotation user
    finally:
        store.close()


def test_why_chain_paths(tmp_path: Path) -> None:
    _write_ts_project(tmp_path)
    db = str(tmp_path / "g.db")
    index_repo(str(tmp_path), db)
    store = GraphStore(db).connect()
    try:
        report = dependents(store, "a.touch", max_depth=2)
        runs = [a for a in report.direct if a.qname == "b.run"]
        assert runs
        assert runs[0].path == ["a.touch", "b.run"]
    finally:
        store.close()


def test_python_uses_type_extraction() -> None:
    parsed = py_parse(
        "m.py",
        b"from pkg import Counter\nclass C:\n    def inc(self, n: Counter) -> None:\n        pass\n",
    )
    type_edges = [(e.source, e.target) for e in parsed.edges if e.kind == "uses_type"]
    assert ("m.C.inc", "Counter") in type_edges


def test_ts_signature_scan_is_bounded() -> None:
    # a plain string-typed signature must not create spurious type edges
    parsed = ts_parse("x.ts", b'export function f(a: string, b: number): boolean { return true; }')
    type_edges = [e.target for e in parsed.edges if e.kind == "uses_type"]
    assert type_edges == []
