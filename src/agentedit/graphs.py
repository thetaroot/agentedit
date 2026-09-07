# SPDX-License-Identifier: Apache-2.0
# SPDX-FileCopyrightText: 2026 thetaroot

"""Graphs: named containers over repo indexes.

A *graph* is a user-facing object: a named, durable set of repositories the
agent connects to via MCP. Each repo keeps its own index (one SQLite store,
reused by any number of graphs — indexed once, referenced many times). The
graph store itself is a tiny SQLite file holding membership only.

Freshness: ``refresh_graph`` runs one watch cycle per member (repositories are
no-ops when unchanged), so answers always reflect the current working tree.
"""
from __future__ import annotations

import os
import re
import sqlite3
from typing import Any

from agentedit.defaults import default_db

_SCHEMA = """
CREATE TABLE IF NOT EXISTS meta (
    key   TEXT PRIMARY KEY,
    value TEXT
);
CREATE TABLE IF NOT EXISTS repos (
    path TEXT PRIMARY KEY
);
CREATE INDEX IF NOT EXISTS idx_repos_path ON repos(path);
CREATE TABLE IF NOT EXISTS contracts (
    id             INTEGER PRIMARY KEY AUTOINCREMENT,
    consumer_repo  TEXT NOT NULL,
    consumer_qname TEXT NOT NULL,
    provider_repo  TEXT NOT NULL,
    provider_qname TEXT NOT NULL,
    kind           TEXT NOT NULL DEFAULT 'calls'
);
CREATE INDEX IF NOT EXISTS idx_contracts_provider ON contracts(provider_repo, provider_qname);
"""


def default_graphs_dir() -> str:
    env = os.environ.get("AGENTEDIT_GRAPHS")
    if env:
        return env
    return os.path.join(os.getcwd(), ".agentedit-graphs")


def graph_slug(name: str) -> str:
    slug = re.sub(r"[^A-Za-z0-9_.-]+", "-", name).strip("-") or "graph"
    return slug


def validate_graph_name(name: str) -> str:
    if not re.fullmatch(r"[A-Za-z0-9_.-]{1,120}", name):
        raise ValueError(
            "graph name may only contain letters, digits, '.', '_', '-'"
        )
    return name


def graph_db_path(name: str, graphs_dir: str | None = None) -> str:
    graphs_dir = graphs_dir or default_graphs_dir()
    return os.path.join(graphs_dir, f"{validate_graph_name(name)}.db")


def exists(name: str, graphs_dir: str | None = None) -> bool:
    return os.path.isfile(graph_db_path(name, graphs_dir))


def create(name: str, graphs_dir: str | None = None) -> str:
    path = graph_db_path(name, graphs_dir)
    if os.path.isfile(path):
        raise ValueError(f"graph already exists: {name}")
    os.makedirs(os.path.dirname(path), exist_ok=True)
    conn = sqlite3.connect(path)
    try:
        conn.executescript(_SCHEMA)
        conn.execute("INSERT OR REPLACE INTO meta(key, value) VALUES ('name', ?)", (name,))
        conn.commit()
    finally:
        conn.close()
    return path


def delete(name: str, graphs_dir: str | None = None) -> bool:
    path = graph_db_path(name, graphs_dir)
    if not os.path.isfile(path):
        return False
    os.remove(path)
    return True


def list_graphs(graphs_dir: str | None = None) -> list[str]:
    graphs_dir = graphs_dir or default_graphs_dir()
    if not os.path.isdir(graphs_dir):
        return []
    names: list[str] = []
    for fn in sorted(os.listdir(graphs_dir)):
        if fn.endswith(".db"):
            names.append(fn[: -len(".db")])
    return names


def _connect(name: str, graphs_dir: str | None = None) -> tuple[str, sqlite3.Connection]:
    path = graph_db_path(name, graphs_dir)
    if not os.path.isfile(path):
        raise ValueError(f"graph not found: {name}")
    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    return path, conn


def add_repo(name: str, repo: str, graphs_dir: str | None = None) -> None:
    """Ensure ``repo`` is indexed and add it to the graph."""
    from agentedit.index.indexer import index_repo

    repo_path = os.path.abspath(repo)
    if not os.path.isdir(repo_path):
        raise ValueError(f"not a directory: {repo}")
    db = default_db(repo_path)
    if not os.path.isfile(db):
        index_repo(repo_path, db)
    _, conn = _connect(name, graphs_dir)
    try:
        conn.execute("INSERT OR IGNORE INTO repos(path) VALUES (?)", (repo_path,))
        conn.commit()
    finally:
        conn.close()


def remove_repo(name: str, repo: str, graphs_dir: str | None = None) -> bool:
    repo_path = os.path.abspath(repo)
    _, conn = _connect(name, graphs_dir)
    try:
        cur = conn.execute("DELETE FROM repos WHERE path = ?", (repo_path,))
        conn.commit()
        return cur.rowcount > 0
    finally:
        conn.close()


def repo_paths(name: str, graphs_dir: str | None = None) -> list[str]:
    _, conn = _connect(name, graphs_dir)
    try:
        rows = conn.execute("SELECT path FROM repos").fetchall()
        return sorted(str(r["path"]) for r in rows)
    finally:
        conn.close()


def inspect(name: str, graphs_dir: str | None = None) -> dict[str, Any]:
    _, conn = _connect(name, graphs_dir)
    try:
        meta_name = conn.execute(
            "SELECT value FROM meta WHERE key='name'"
        ).fetchone()
        repos = [str(r["path"]) for r in conn.execute("SELECT path FROM repos")]
        return {"name": meta_name["value"] if meta_name else name, "repos": sorted(repos)}
    finally:
        conn.close()


def entries(name: str, graphs_dir: str | None = None) -> list[dict[str, str]]:
    """Members as {'repo', 'db'} pairs with an existing or missing store."""
    return [{"repo": p, "db": default_db(p)} for p in repo_paths(name, graphs_dir)]


def add_contract(name: str, *, consumer_repo: str, consumer_qname: str,
                 provider_repo: str, provider_qname: str, kind: str = "calls",
                 graphs_dir: str | None = None) -> int:
    """Declare that ``consumer_qname`` (in ``consumer_repo``) depends on the
    provider symbol across a repo/language boundary.

    Cross-repo coupling is *only* ever created from these explicit contracts
    (or, later, a schema file) — never guessed. Both symbols must exist in
    their member stores so a typo cannot silently create a fake edge.
    """
    from agentedit.store.sqlite import GraphStore

    consumer = GraphStore(default_db(os.path.abspath(consumer_repo))).connect()
    try:
        if consumer.get_symbol(consumer_qname) is None:
            raise ValueError(
                f"consumer symbol not found in {consumer_repo}: {consumer_qname}"
            )
    finally:
        consumer.close()
    provider = GraphStore(default_db(os.path.abspath(provider_repo))).connect()
    try:
        if provider.get_symbol(provider_qname) is None:
            raise ValueError(
                f"provider symbol not found in {provider_repo}: {provider_qname}"
            )
    finally:
        provider.close()

    _, conn = _connect(name, graphs_dir)
    try:
        cur = conn.execute(
            "INSERT INTO contracts(consumer_repo, consumer_qname, provider_repo, "
            "provider_qname, kind) VALUES (?, ?, ?, ?, ?)",
            (os.path.abspath(consumer_repo), consumer_qname,
             os.path.abspath(provider_repo), provider_qname, kind),
        )
        conn.commit()
        rowid = cur.lastrowid
        assert rowid is not None
        return int(rowid)
    finally:
        conn.close()


def list_contracts(name: str, graphs_dir: str | None = None) -> list[dict[str, Any]]:
    _, conn = _connect(name, graphs_dir)
    try:
        rows = conn.execute(
            "SELECT id, consumer_repo, consumer_qname, provider_repo, "
            "provider_qname, kind FROM contracts ORDER BY id"
        ).fetchall()
        return [dict(r) for r in rows]
    finally:
        conn.close()


def remove_contract(name: str, contract_id: int, graphs_dir: str | None = None) -> bool:
    _, conn = _connect(name, graphs_dir)
    try:
        cur = conn.execute("DELETE FROM contracts WHERE id = ?", (contract_id,))
        conn.commit()
        return cur.rowcount > 0
    finally:
        conn.close()


def contracts_for_provider(name: str, provider_repo: str, provider_qname: str,
                           graphs_dir: str | None = None) -> list[dict[str, Any]]:
    _, conn = _connect(name, graphs_dir)
    try:
        rows = conn.execute(
            "SELECT id, consumer_repo, consumer_qname, kind FROM contracts "
            "WHERE provider_repo = ? AND provider_qname = ?",
            (os.path.abspath(provider_repo), provider_qname),
        ).fetchall()
        return [dict(r) for r in rows]
    finally:
        conn.close()


def ensure_indexed(entry: dict[str, str]) -> str:
    """Index a member on first use; return its db path."""
    from agentedit.index.indexer import index_repo

    if not os.path.isfile(entry["db"]):
        index_repo(entry["repo"], entry["db"])
    return entry["db"]


def refresh_once(name: str, graphs_dir: str | None = None) -> dict[str, Any]:
    """One watch cycle over every member; no-op on members without changes."""
    from agentedit.watch import run_once

    repos = repo_paths(name, graphs_dir)
    member_results: list[dict[str, Any]] = []
    for repo in repos:
        member_results.append(run_once(repo, default_db(repo)))
    return {
        "graph": name,
        "repos": repos,
        "members": member_results,
    }
