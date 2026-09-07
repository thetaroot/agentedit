"""Engine v4 regression: receiver dispatch, ctor typing, inheritance edges,
framework entries and honest unresolved surfacing (E2E critical-miss fixes)."""
from __future__ import annotations

import os
from pathlib import Path

from agentedit.analyze.impact import dependents, impact
from agentedit.index.indexer import index_repo
from agentedit.store.sqlite import GraphStore

FIXTURE = "tests/fixtures/sample_py_v4"


def _db(tmp_path: Path, name: str = "graph.db") -> str:
    db = str(tmp_path / name)
    if os.path.isfile(db):
        os.remove(db)
    index_repo(FIXTURE, db)
    return db


def _store(db: str) -> GraphStore:
    return GraphStore(db).connect()


def test_same_class_self_calls_resolve(tmp_path: Path) -> None:
    store = _store(_db(tmp_path))
    try:
        dep = dependents(store, "app.Holder.more")
        qnames = {a.qname for a in dep.direct}
        assert "app.Holder._helper" in qnames
    finally:
        store.close()


def test_typed_ctor_collaborator_resolves(tmp_path: Path) -> None:
    store = _store(_db(tmp_path))
    try:
        # self._brain.run() -> BrainService.run via the __init__ annotation.
        dep = dependents(store, "brain.BrainService.run")
        assert any(a.qname == "app.Holder.go" for a in dep.direct)
    finally:
        store.close()


def test_untyped_ctor_call_stays_unresolved_and_is_surfaced(tmp_path: Path) -> None:
    store = _store(_db(tmp_path))
    try:
        # No resolved dependents for persist_unique …
        dep = dependents(store, "brain.BrainService.persist_unique")
        assert not dep.direct and not dep.transitive
        # … but the unresolved self._untyped.persist_unique is surfaced.
        assert dep.suspected, "expected a suspected (name-based) reference"
        assert any("probe_untyped" in str(s["source_qname"]) for s in dep.suspected)
        # And the report is not silently "none".
        assert dep.risk in ("low",)
        assert any("unresolved" in n for n in dep.notes)
    finally:
        store.close()


def test_python_inheritance_edges_emitted(tmp_path: Path) -> None:
    store = _store(_db(tmp_path))
    try:
        pairs = store.inherits_pairs()
        assert ("app.Sub", "app.Base") in pairs
    finally:
        store.close()


def test_super_and_inherited_self_method_resolve(tmp_path: Path) -> None:
    store = _store(_db(tmp_path))
    try:
        edges = store.edges_from("app.Sub.work")
        targets = {e["target_qname"] for e in edges}
        assert "app.Base.base_run" in targets
    finally:
        store.close()


def test_route_decorator_marked_external_entry(tmp_path: Path) -> None:
    store = _store(_db(tmp_path))
    try:
        row = store.get_symbol("app.create_item")
        assert row is not None
        assert bool(row.get("external_entry"))
        assert "router.post" in (row.get("external_hint") or "")
        rep = impact(store, "app.create_item")
        assert rep.external_entry
        assert any("framework" in n for n in rep.notes)
        assert rep.risk != "none"
    finally:
        store.close()


def test_resolved_calls_are_not_reported_unresolved(tmp_path: Path) -> None:
    db = _db(tmp_path)
    store = GraphStore(db).connect()
    try:
        # self.more() / self.go() now resolve -> no dynamic unresolved rows for them.
        assert store.unresolved_suspected("more") == []
        assert store.unresolved_suspected("go") == []
    finally:
        store.close()
