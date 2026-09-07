"""V1.0 purity: the store is structural only — no knowledge layer tables.

Databases created by pre-V1.0 releases (which carried a ``notes`` and later a
``knowledge`` table) must be cleaned in place on connect.
"""
from __future__ import annotations

import shutil
import sqlite3
from pathlib import Path

from agentedit.index.indexer import index_repo
from agentedit.store.sqlite import GraphStore

FIXTURE = "tests/fixtures/sample_ts"


def _table_names(db: str) -> set[str]:
    conn = sqlite3.connect(db)
    try:
        rows = conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table'"
        ).fetchall()
        return {str(r[0]) for r in rows}
    finally:
        conn.close()


def _repo_with_db(tmp_path: Path) -> tuple[str, str]:
    repo = tmp_path / "repo"
    shutil.copytree(FIXTURE, repo, dirs_exist_ok=True)
    db = str(repo / ".agentedit" / "graph.db")
    return str(repo), db


def test_fresh_store_has_no_knowledge_tables(tmp_path: Path) -> None:
    _, db = _repo_with_db(tmp_path)
    index_repo(str(tmp_path / "repo"), db)
    names = _table_names(db)
    assert "knowledge" not in names
    assert "notes" not in names


def test_legacy_store_is_cleaned_on_connect(tmp_path: Path) -> None:
    """A 1.2.x-era db (notes+knowledge tables present) is cleaned in place."""
    repo = tmp_path / "repo"
    shutil.copytree(FIXTURE, repo, dirs_exist_ok=True)
    db_path = repo / ".agentedit"
    db_path.mkdir(parents=True, exist_ok=True)
    db = str(db_path / "graph.db")

    conn = sqlite3.connect(db)
    try:
        conn.executescript(
            "CREATE TABLE notes (id INTEGER PRIMARY KEY, qname TEXT, tag TEXT, "
            "text TEXT, source TEXT, created TEXT);\n"
            "CREATE TABLE knowledge (id INTEGER PRIMARY KEY, scope TEXT, ref TEXT, "
            "kind TEXT, text TEXT, status TEXT, origin TEXT, author TEXT, "
            "version INTEGER, supersedes INTEGER, created TEXT, updated TEXT);\n"
            "INSERT INTO knowledge(scope, ref, kind, text) VALUES "
            "('symbol', 'src.auth.authenticate', 'note', 'legacy');\n"
        )
        conn.commit()
    finally:
        conn.close()

    store = GraphStore(db).connect()
    store.close()

    assert _table_names(db).isdisjoint({"notes", "knowledge"})


def test_connect_is_idempotent_across_reopens(tmp_path: Path) -> None:
    repo, db = _repo_with_db(tmp_path)
    index_repo(repo, db)
    for _ in range(3):
        store = GraphStore(db).connect()
        store.close()
    assert _table_names(db).isdisjoint({"notes", "knowledge"})
