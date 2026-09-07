"""MCP handshake + tool dispatch against the sample fixture."""
from __future__ import annotations

import json
from typing import Any, cast

from agentedit.mcp_server import McpServer

FIXTURE = "tests/fixtures/sample_ts"


def _call(
    server: McpServer,
    method: str,
    params: dict[str, Any] | None = None,
    ident: int = 1,
) -> dict[str, Any] | None:
    return server.handle({"jsonrpc": "2.0", "id": ident, "method": method, "params": params or {}})


def _result(payload: dict[str, Any] | None) -> dict[str, Any]:
    assert payload is not None and "result" in payload
    return cast(dict[str, Any], payload["result"])


def test_handshake() -> None:
    server = McpServer(FIXTURE)
    out = _call(server, "initialize", {"protocolVersion": "2024-11-05"})
    result = _result(out)
    assert result["serverInfo"]["name"] == "agentedit"
    assert result["protocolVersion"] == "2024-11-05"


def test_negotiates_known_newer_protocol() -> None:
    server = McpServer(FIXTURE)
    out = _call(server, "initialize", {"protocolVersion": "2025-06-18"})
    assert _result(out)["protocolVersion"] == "2025-06-18"


def test_ping_returns_empty_result() -> None:
    server = McpServer(FIXTURE)
    out = _call(server, "ping")
    assert out is not None and out["result"] == {}
    assert "error" not in out


def test_tools_list_exposes_all_tools() -> None:
    server = McpServer(FIXTURE)
    tools = _result(_call(server, "tools/list"))["tools"]
    assert {t["name"] for t in tools} == {
        "impact", "dependents", "would_break", "changes", "search",
        "symbols_in_file", "audit", "why",
    }


def test_impact_tool_auto_indexes_and_answers() -> None:
    import os

    db = "/tmp/agentedit_mcp_test.db"
    if os.path.isfile(db):
        os.remove(db)
    server = McpServer(FIXTURE)
    server._db = db
    out = _call(server, "tools/call", {"name": "impact", "arguments": {"symbol": "src.auth.authenticate"}})
    payload = json.loads(_result(out)["content"][0]["text"])
    assert payload["symbol"] == "src.auth.authenticate"
    direct = {a["qname"] for a in payload["direct"]}
    assert "src.controller.AuthController.login" in direct
