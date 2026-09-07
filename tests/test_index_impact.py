"""End-to-end: index the sample fixture, then run impact/would_break."""
from __future__ import annotations

import os
import shutil
from pathlib import Path
from typing import Any

import pytest

from agentedit.analyze.impact import impact, would_break
from agentedit.index.indexer import index_repo
from agentedit.store.sqlite import GraphStore

FIXTURE = "tests/fixtures/sample_ts"
DB = "/tmp/agentedit_test_graph.db"


def _fresh_index() -> GraphStore:
    if os.path.isfile(DB):
        os.remove(DB)
    summary = index_repo(os.path.abspath(FIXTURE), DB)
    assert summary["symbols"] >= 14
    return GraphStore(DB).connect()


def test_impact_finds_cross_file_caller() -> None:
    store = _fresh_index()
    try:
        report = impact(store, "src.auth.authenticate")
        direct = {a.qname for a in report.direct}
        assert "src.controller.AuthController.login" in direct
        assert report.risk == "high"
    finally:
        store.close()


def test_would_break_signature_adds_required_param() -> None:
    store = _fresh_index()
    try:
        report = would_break(
            store, "src.auth.authenticate",
            change="signature",
            new_signature="(userId: string, secret: string): boolean",
        )
        assert report.risk == "high"
        assert report.direct
        assert any("required param" in n for n in report.notes)
    finally:
        store.close()


def test_would_break_return_change_is_medium() -> None:
    store = _fresh_index()
    try:
        report = would_break(
            store, "src.auth.authenticate",
            change="return_type",
            new_signature="(userId: string, token: string): Promise<boolean>",
        )
        assert report.risk == "medium"
    finally:
        store.close()


def test_would_break_removed_symbol_unknown() -> None:
    store = _fresh_index()
    try:
        report = would_break(store, "src.auth.nothere")
        assert "not found" in report.notes[0]
    finally:
        store.close()


def test_reindex_is_idempotent() -> None:
    s1 = index_repo(os.path.abspath(FIXTURE), DB, force=True)
    s2 = index_repo(os.path.abspath(FIXTURE), DB)
    assert s2["changed"] == 0
    assert s1["symbols"] == s2["symbols"]


def test_incremental_reindex_preserves_incoming_edges(tmp_path: Path) -> None:
    """Re-parsing an edited file must not drop other files' edges into it."""
    repo = tmp_path / "work"
    shutil.copytree(FIXTURE, repo, dirs_exist_ok=True)
    db = str(tmp_path / "graph.db")

    index_repo(str(repo), db)
    store = GraphStore(db).connect()
    before_sym = len(store.edges_to("src.auth.authenticate"))
    before_mod = len(store.edges_to("src.auth", ("imports",)))
    store.close()

    # edit auth.ts (append a function) and re-index incrementally
    auth = repo / "src" / "auth.ts"
    auth.write_text(auth.read_text() + "\nexport function extra() { return 1; }\n")
    s2 = index_repo(str(repo), db)
    assert s2["changed"] == 1

    store = GraphStore(db).connect()
    after_sym = len(store.edges_to("src.auth.authenticate"))
    after_mod = len(store.edges_to("src.auth", ("imports",)))
    store.close()
    # 2 incoming edges into authenticate: the call (login) + the name-import
    # (controller.ts `import { authenticate }`); 1 whole-module importer.
    assert after_sym == before_sym == 2
    assert after_mod == before_mod == 1


def test_index_rejects_missing_directory() -> None:
    with pytest.raises(ValueError):
        index_repo("/definitely/not/a/repo", "/tmp/agentedit_nope.db", force=True)


def test_empty_dir_index_is_empty(tmp_path: Path) -> None:
    summary: dict[str, Any] = index_repo(str(tmp_path), str(tmp_path / "g.db"))
    assert summary["language"] is None
    assert summary["files"] == 0


def test_reconcile_drops_stale_edge_when_name_becomes_ambiguous(tmp_path: Path) -> None:
    """A new barrel export must invalidate (not guess) prior name-import edges."""
    repo = tmp_path / "work"
    src = repo / "src"
    src.mkdir(parents=True)
    (src / "a.ts").write_text("export function helper() { return 1; }\n")
    (src / "barrel.ts").write_text("export * from './a';\n")
    (src / "user.ts").write_text(
        "import { helper } from './barrel';\n"
        "export function call() { return helper(); }\n"
    )
    db = str(tmp_path / "graph.db")
    index_repo(str(repo), db)

    store = GraphStore(db).connect()
    before = [e for e in store.edges_to("src.a.helper", ("imports",))]
    assert any(e["source_qname"] == "src.user" for e in before)
    store.close()

    # add a second exporter of the same name -> the import becomes ambiguous
    (src / "b.ts").write_text("export function helper() { return 2; }\n")
    (src / "barrel.ts").write_text("export * from './a';\nexport * from './b';\n")
    s2 = index_repo(str(repo), db)
    assert s2["changed"] >= 1
    assert s2["reconciled"] >= 1

    store = GraphStore(db).connect()
    try:
        to_a = [e for e in store.edges_to("src.a.helper", ("imports",))]
        to_b = [e for e in store.edges_to("src.b.helper", ("imports",))]
        assert not any(e["source_qname"] == "src.user" for e in to_a)
        assert not any(e["source_qname"] == "src.user" for e in to_b)
    finally:
        store.close()


def test_index_summary_reports_reconcile_and_unresolved() -> None:
    summary = index_repo(os.path.abspath(FIXTURE), DB, force=True)
    assert "reconciled" in summary and "unresolved" in summary
