"""P1 regressions — attribute / local-variable dispatch edges (python).

Audit finding: ``self._brain = BrainService()`` followed by
``self._brain.handle_mcp()`` was invisible to the graph, so real blast radius
(handlers behind an attribute) was missed. Flow-lite bindings make these
resolve to the real method with the honest ``attr`` label, and never fabricate
edges for unassigned attributes.
"""
from __future__ import annotations

import shutil
from pathlib import Path

from agentedit.index.indexer import index_repo
from agentedit.store.sqlite import GraphStore

FIXTURE = "tests/fixtures/sample_py_attr"


def _index(tmp_path: Path) -> GraphStore:
    repo = tmp_path / "work"
    shutil.copytree(FIXTURE, repo, dirs_exist_ok=True)
    db = str(tmp_path / "graph.db")
    index_repo(str(repo), db)
    store = GraphStore(db).connect()
    return store


def test_self_attr_dispatch_creates_method_edge(tmp_path: Path) -> None:
    store = _index(tmp_path)
    try:
        rows = store.edges_from("svc.gate.Gateway.run")
        target_methods = {
            (r["target_qname"], r["method"]) for r in rows
            if r["kind"] == "calls"
        }
        assert ("svc.services.brain.BrainService.handle_mcp", "attr") in target_methods
    finally:
        store.close()


def test_local_var_dispatch_creates_method_edge(tmp_path: Path) -> None:
    store = _index(tmp_path)
    try:
        rows = store.edges_from("svc.gate.local_dispatch")
        targets = {(r["target_qname"], r["method"]) for r in rows}
        assert ("svc.services.brain.BrainService.handle_mcp", "attr") in targets
    finally:
        store.close()


def test_unassigned_attribute_produces_no_edge(tmp_path: Path) -> None:
    store = _index(tmp_path)
    try:
        rows = store.edges_from("svc.gate.Gateway.missing")
        # Nothing may be fabricated for an attribute that was never bound.
        assert rows == []
    finally:
        store.close()


def test_audit_catches_attribute_dispatch_dependant(tmp_path: Path) -> None:
    from agentedit.analyze.impact import impact

    store = _index(tmp_path)
    try:
        report = impact(store, "svc.services.brain.BrainService.handle_mcp")
        files = {a.file_path for a in report.direct}
        assert "svc/gate.py" in files
        by_qname = {a.qname: a for a in report.direct}
        gw = by_qname.get("svc.gate.Gateway.run")
        assert gw is not None and gw.confidence <= 0.6
        assert by_qname.get("svc.gate.local_dispatch") is not None
    finally:
        store.close()
