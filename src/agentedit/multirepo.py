# SPDX-License-Identifier: Apache-2.0
# SPDX-FileCopyrightText: 2026 thetaroot

"""Multi-repo workspace.

A workspace groups independently-indexed repositories so an agent can look a
symbol up and reason about its impact *across* repos, while every repo stays
fully correct *within itself*.

Honest boundary (documented, never violated): dependency *edges* never cross
repo boundaries. Cross-repo static resolution would require per-language
manifests and would only produce false claims, so we do not fabricate it.
Each repo gets its own store file (``<workspace>/<slug>.db``); queries are
repo-scoped (default) or cross-searched with every result tagged by repo.
"""
from __future__ import annotations

import json
import os
import re
from pathlib import Path
from typing import Any

from agentedit.index.indexer import index_repo
from agentedit.store.sqlite import GraphStore

_MANIFEST = "workspace.json"


def default_workspace_dir() -> str:
    env = os.environ.get("IMPACTMAP_WORKSPACE")
    if env:
        return env
    return str(Path.cwd() / ".agentedit-workspace")


def _manifest_path(workdir: str) -> str:
    return os.path.join(workdir, _MANIFEST)


def _slug_for(path: str, existing: set[str]) -> str:
    base = os.path.basename(os.path.abspath(path).rstrip("/")) or "repo"
    slug = re.sub(r"[^A-Za-z0-9_.-]+", "-", base).strip("-") or "repo"
    candidate, i = slug, 1
    while candidate in existing:
        candidate = f"{slug}-{i}"
        i += 1
    return candidate


def _load(workdir: str) -> dict[str, dict[str, str]]:
    p = _manifest_path(workdir)
    if os.path.isfile(p):
        with open(p, encoding="utf-8") as fh:
            data = json.load(fh)
        if isinstance(data, dict) and isinstance(data.get("repos"), dict):
            repos: dict[str, dict[str, str]] = data["repos"]
            return repos
    return {}


def _save(workdir: str, repos: dict[str, dict[str, str]]) -> None:
    Path(workdir).mkdir(parents=True, exist_ok=True)
    with open(_manifest_path(workdir), "w", encoding="utf-8") as fh:
        json.dump({"repos": repos}, fh, indent=2)


def add_repo(repo: str, workdir: str | None = None) -> str:
    """Index ``repo`` into the workspace; returns its unique slug."""
    workdir = workdir or default_workspace_dir()
    repo = os.path.abspath(repo)
    repos = _load(workdir)
    for slug, entry in repos.items():
        if os.path.abspath(entry["path"]) == repo:
            index_repo(repo, entry["db"])
            return slug
    slug = _slug_for(repo, set(repos))
    db = os.path.join(workdir, f"{slug}.db")
    index_repo(repo, db)
    repos[slug] = {"path": repo, "db": db}
    _save(workdir, repos)
    return slug


def remove_repo(slug: str, workdir: str | None = None) -> bool:
    workdir = workdir or default_workspace_dir()
    repos = _load(workdir)
    entry = repos.pop(slug, None)
    if entry is None:
        return False
    _save(workdir, repos)
    db = entry.get("db")
    if db and os.path.isfile(db):
        os.remove(db)
    return True


def list_repos(workdir: str | None = None) -> list[dict[str, str]]:
    workdir = workdir or default_workspace_dir()
    return [{"slug": slug, "path": entry["path"], "db": entry["db"]}
            for slug, entry in sorted(_load(workdir).items())]


def search(qname: str, workdir: str | None = None, limit: int = 25,
           slug_filter: str | None = None) -> list[dict[str, Any]]:
    workdir = workdir or default_workspace_dir()
    results: list[dict[str, Any]] = []
    for entry in list_repos(workdir):
        if slug_filter and entry["slug"] != slug_filter:
            continue
        store = GraphStore(entry["db"]).connect()
        try:
            for row in store.search_symbols(qname, limit=limit):
                results.append({"repo": entry["slug"], **row})
        finally:
            store.close()
    return results


def run_impact(qname: str, workdir: str | None = None, *,
               slug_filter: str | None = None, change: str = "removed",
               new_signature: str | None = None, mode: str = "impact") -> list[dict[str, Any]]:
    """Run impact/would_break across workspace repos that contain ``qname``.

    Returns one report per matching repo, tagged with the repo slug.
    """
    workdir = workdir or default_workspace_dir()
    reports: list[dict[str, Any]] = []
    for entry in list_repos(workdir):
        if slug_filter and entry["slug"] != slug_filter:
            continue
        store = GraphStore(entry["db"]).connect()
        try:
            if store.get_symbol(qname) is None:
                continue
            if mode == "would_break":
                from dataclasses import asdict

                from agentedit.analyze.impact import would_break

                rep = would_break(store, qname, change=change, new_signature=new_signature)
            else:
                from dataclasses import asdict

                from agentedit.analyze.impact import impact

                rep = impact(store, qname)
            reports.append({
                "repo": entry["slug"],
                "symbol": rep.root,
                "change": rep.change,
                "risk": rep.risk,
                "confidence": rep.confidence,
                "direct": [asdict(a) for a in rep.direct],
                "transitive": [asdict(a) for a in rep.transitive],
                "notes": rep.notes,
            })
        finally:
            store.close()
    return reports
