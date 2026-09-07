"""Watch / proactive: fresh graph + change-surface reporting."""
from __future__ import annotations

import shutil
from pathlib import Path

from agentedit.index.indexer import index_repo
from agentedit.store.sqlite import GraphStore
from agentedit.watch import run_once

FIXTURE = "tests/fixtures/sample_ts"


def _setup(tmp_path: Path) -> tuple[Path, str]:
    repo = tmp_path / "work"
    shutil.copytree(FIXTURE, repo, dirs_exist_ok=True)
    db = str(repo / ".agentedit" / "graph.db")
    index_repo(str(repo), db)
    return repo, db


def test_watch_once_reports_edit_surface(tmp_path: Path) -> None:
    repo, db = _setup(tmp_path)

    # untouched -> no changes reported
    idle = run_once(str(repo), db)
    assert idle["changed_files"] == []

    auth = repo / "src" / "auth.ts"
    auth.write_text(auth.read_text() + "\nexport function extra() { return 1; }\n")

    result = run_once(str(repo), db)
    assert "src/auth.ts" in result["changed_files"]
    qnames = {a["qname"] for a in result["affected"]}
    assert "src.controller.AuthController.login" in qnames
    assert "src.controller" in qnames


def test_watch_keeps_graph_fresh(tmp_path: Path) -> None:
    repo, db = _setup(tmp_path)
    auth = repo / "src" / "auth.ts"
    auth.write_text(auth.read_text() + "\nexport function fresh() { return 2; }\n")

    run_once(str(repo), db)
    store = GraphStore(db).connect()
    try:
        assert store.get_symbol("src.auth.fresh") is not None
    finally:
        store.close()
