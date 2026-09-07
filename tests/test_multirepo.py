"""Multi-repo workspace: add/list/search/scoped impact across repos."""
from __future__ import annotations

import shutil
from pathlib import Path

from agentedit.multirepo import add_repo, list_repos, remove_repo, run_impact, search

FIXTURE = "tests/fixtures/sample_ts"


def test_workspace_add_list_search(tmp_path: Path) -> None:
    ws = str(tmp_path / "ws")
    repo_b = tmp_path / "other"
    shutil.copytree(FIXTURE, repo_b, dirs_exist_ok=True)

    slug_a = add_repo(FIXTURE, ws)
    slug_b = add_repo(str(repo_b), ws)
    assert slug_a != slug_b

    entries = list_repos(ws)
    assert {e["slug"] for e in entries} == {slug_a, slug_b}

    hits = search("authenticate", ws)
    assert len(hits) >= 2
    assert {h["repo"] for h in hits} == {slug_a, slug_b}

    # scoped impact
    only = run_impact("src.auth.authenticate", ws, slug_filter=slug_a)
    assert len(only) == 1 and only[0]["repo"] == slug_a
    assert {a["qname"] for a in only[0]["direct"]} >= {"src.controller.AuthController.login"}

    # cross-repo report (each repo self-contained, no fabricated cross edges)
    across = run_impact("src.auth.authenticate", ws)
    assert {r["repo"] for r in across} == {slug_a, slug_b}

    assert remove_repo(slug_b, ws) is True
    assert {e["slug"] for e in list_repos(ws)} == {slug_a}
