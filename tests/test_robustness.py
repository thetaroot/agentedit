"""P3 regressions — robustness & CLI behaviour.

* Symlinks (file and directory) must never leak content into the graph.
* A corrupted graph DB yields a friendly, actionable CLI error (exit 1), not
  a raw traceback.
* The "no index" hint guides the user to build the graph for the right repo.
"""
from __future__ import annotations

import os
from pathlib import Path

import pytest

from agentedit.cli import main
from agentedit.index.indexer import index_repo
from agentedit.store.sqlite import GraphStore


def _write_py(repo: Path, rel: str, body: str) -> None:
    p = repo / rel
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(body, encoding="utf-8")


def test_symlinked_files_and_dirs_are_not_indexed(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    outside = tmp_path / "outside"
    repo.mkdir()
    outside.mkdir()
    (outside / "secret.py").write_text("class Secret:\n    pass\n", encoding="utf-8")
    _write_py(repo, "real.py", "def ok():\n    pass\n")
    os.symlink(outside / "secret.py", repo / "linked.py")
    os.symlink(outside, repo / "linkeddir")

    db = str(tmp_path / "graph.db")
    summary = index_repo(str(repo), db)
    indexed = sorted(summary["changed_files"])
    assert "real.py" in indexed
    assert "linked.py" not in indexed

    store = GraphStore(db).connect()
    try:
        paths = {r["file_path"] for r in store.all_symbol_rows()}
        assert not any("linked" in p or "secret" in p for p in paths)
    finally:
        store.close()


def test_corrupt_db_returns_friendly_error(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    repo = tmp_path / "repo"
    (repo / ".agentedit").mkdir(parents=True)
    (repo / ".agentedit" / "graph.db").write_text("NOTASQLITE", encoding="utf-8")
    _write_py(repo, "a.py", "def f():\n    pass\n")

    code = main(["impact", "x", "--repo", str(repo)])
    assert code == 1
    captured = capsys.readouterr()
    assert "not a database" in captured.err
    assert "re-index" in captured.err
    assert "Traceback" not in captured.err


def test_missing_index_guides_the_user(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    _write_py(repo, "a.py", "def f():\n    pass\n")

    code = main(["impact", "f", "--repo", str(repo)])
    assert code == 1
    captured = capsys.readouterr()
    assert "no index at" in captured.err
    assert "agentedit index" in captured.err
    assert "Traceback" not in captured.err


def test_cli_smoke_end_to_end(tmp_path: Path, capsys: pytest.CaptureFixture[str],
                              monkeypatch: pytest.MonkeyPatch) -> None:
    repo = tmp_path / "repo"
    monkeypatch.setenv("AGENTEDIT_GRAPHS", str(tmp_path / "graphs"))
    _write_py(repo, "app/__init__.py", "")
    _write_py(repo, "app/services/__init__.py", "class Service:\n    pass\n")
    _write_py(repo, "app/services/brain.py", "class Brain:\n    pass\n")
    _write_py(
        repo,
        "app/controller.py",
        "from services import Service\n"
        "def make() -> Service:\n"
        "    return Service()\n",
    )

    assert main(["index", str(repo)]) == 0
    capsys.readouterr()

    assert main(["impact", "app.services.Service", "--repo", str(repo)]) == 0
    assert main(["audit", "app.services.Service", "--repo", str(repo)]) == 0
    assert main(["search", "Service", "--repo", str(repo)]) == 0
    assert main(["graph", "create", "g"]) == 0
    assert main(["graph", "add-repo", "g", str(repo)]) == 0
    assert main(["graph", "inspect", "g"]) == 0
    out = capsys.readouterr().out
    assert "1 repo" in out
