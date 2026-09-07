"""P1: graphs — object model, shared repos, MCP per graph."""
from __future__ import annotations

import json
import os
import shutil
from pathlib import Path
from typing import Any

from agentedit import graphs
from agentedit.mcp_server import McpServer

FIXTURE = "tests/fixtures/sample_ts"


def _copy_fixture(tmp_path: Path, name: str) -> str:
    dst = tmp_path / name
    shutil.copytree(FIXTURE, dst, dirs_exist_ok=True)
    return str(dst)


def _call(server: McpServer, method: str, params: dict[str, Any] | None = None,
          ident: int = 1) -> dict[str, Any] | None:
    return server.handle({"jsonrpc": "2.0", "id": ident, "method": method, "params": params or {}})


def test_graph_lifecycle_shared_repo(tmp_path: Path) -> None:
    gdir = str(tmp_path / "graphs")
    repo_a = _copy_fixture(tmp_path, "ra")
    repo_b = _copy_fixture(tmp_path, "rb")

    graphs.create("web", gdir)
    graphs.create("back", gdir)
    assert set(graphs.list_graphs(gdir)) == {"web", "back"}

    graphs.add_repo("web", repo_a, gdir)
    graphs.add_repo("web", repo_b, gdir)   # second member
    graphs.add_repo("back", repo_a, gdir)  # repo_a shared across graphs
    assert graphs.repo_paths("web", gdir) == [repo_a, repo_b]
    assert graphs.repo_paths("back", gdir) == [repo_a]

    info = graphs.inspect("web", gdir)
    assert info["name"] == "web" and len(info["repos"]) == 2

    assert graphs.remove_repo("web", repo_b, gdir) is True
    assert graphs.repo_paths("web", gdir) == [repo_a]

    assert graphs.delete("back", gdir) is True
    assert set(graphs.list_graphs(gdir)) == {"web"}
    assert graphs.delete("nope", gdir) is False


def test_mcp_graph_fanout(tmp_path: Path) -> None:
    gdir = str(tmp_path / "graphs")
    repo_a = _copy_fixture(tmp_path, "ra")
    repo_b = _copy_fixture(tmp_path, "rb")
    graphs.create("g", gdir)
    graphs.add_repo("g", repo_a, gdir)
    graphs.add_repo("g", repo_b, gdir)

    server = McpServer(graph="g", graphs_dir=gdir)
    out = _call(server, "initialize")
    assert out is not None and "result" in out

    # search tags every repo
    res = _call(server, "tools/call", {"name": "search", "arguments": {"query": "authenticate"}}, ident=2)
    assert res is not None and "result" in res
    payload = json.loads(res["result"]["content"][0]["text"])
    assert len(payload["results"]) == 2
    assert {r["repo"] for r in payload["results"]} == {repo_a, repo_b}

    # impact only reports repos that contain the symbol
    imp = _call(server, "tools/call",
                {"name": "impact", "arguments": {"symbol": "src.auth.authenticate"}}, ident=3)
    assert imp is not None and "result" in imp
    payload = json.loads(imp["result"]["content"][0]["text"])
    assert payload["graph"] == "g"
    assert len(payload["repos"]) == 2
    direct = {a["qname"] for r in payload["repos"] for a in r["report"]["direct"]}
    assert "src.controller.AuthController.login" in direct

    # unknown symbol: honest empty, not guessed
    miss = _call(server, "tools/call",
                 {"name": "impact", "arguments": {"symbol": "nope.missing"}}, ident=4)
    assert miss is not None and "result" in miss
    payload = json.loads(miss["result"]["content"][0]["text"])
    assert payload["risk"] == "none" and payload["repos"] == []


def test_graph_refresh_reports_changes(tmp_path: Path) -> None:
    gdir = str(tmp_path / "graphs")
    repo = _copy_fixture(tmp_path, "repo")
    graphs.create("g", gdir)
    graphs.add_repo("g", repo, gdir)

    idle = graphs.refresh_once("g", gdir)
    assert all(not m["changed_files"] for m in idle["members"])

    auth = os.path.join(repo, "src", "auth.ts")
    with open(auth, "a", encoding="utf-8") as fh:
        fh.write("\nexport function extra_p1() { return 1; }\n")
    run = graphs.refresh_once("g", gdir)
    assert any("src/auth.ts" in m["changed_files"] for m in run["members"])
