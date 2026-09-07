"""P4: declared cross-repo/-language contract edges + propagation mini-eval."""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from agentedit import graphs
from agentedit.graphs import (
    add_contract,
    contracts_for_provider,
    create,
    list_contracts,
    remove_contract,
)
from agentedit.mcp_server import McpServer


def _make_py_provider(root: Path) -> str:
    pkg = root / "pkg"
    (pkg).mkdir(parents=True)
    (pkg / "__init__.py").write_text("", encoding="utf-8")
    (pkg / "mod.py").write_text("def run():\n    return 1\n", encoding="utf-8")
    return str(root)


def _make_ts_consumer(root: Path) -> str:
    src = root / "src"
    src.mkdir(parents=True)
    (src / "client.ts").write_text(
        "export function callRun() {\n    return 1;\n}\n", encoding="utf-8")
    return str(root)


def test_contract_declare_list_remove(tmp_path: Path) -> None:
    gdir = str(tmp_path / "g")
    create("g", gdir)
    provider = _make_py_provider(tmp_path / "py")
    consumer = _make_ts_consumer(tmp_path / "ts")
    graphs.add_repo("g", provider, gdir)
    graphs.add_repo("g", consumer, gdir)

    cid = add_contract("g", consumer_repo=consumer, consumer_qname="src.client.callRun",
                       provider_repo=provider, provider_qname="pkg.mod.run",
                       kind="calls", graphs_dir=gdir)
    rows = list_contracts("g", gdir)
    assert len(rows) == 1 and rows[0]["id"] == cid
    prov = contracts_for_provider("g", provider, "pkg.mod.run", gdir)
    assert len(prov) == 1 and prov[0]["consumer_qname"] == "src.client.callRun"
    assert remove_contract("g", cid, gdir) is True


def test_contract_rejects_unknown_symbol(tmp_path: Path) -> None:
    gdir = str(tmp_path / "g")
    create("g", gdir)
    provider = _make_py_provider(tmp_path / "py")
    consumer = _make_ts_consumer(tmp_path / "ts")
    graphs.add_repo("g", provider, gdir)
    graphs.add_repo("g", consumer, gdir)
    with pytest.raises(ValueError):
        add_contract("g", consumer_repo=consumer, consumer_qname="nope.missing",
                     provider_repo=provider, provider_qname="pkg.mod.run",
                     graphs_dir=gdir)


def test_graph_impact_reaches_across_repo_via_contract(tmp_path: Path) -> None:
    """Mini-eval: a provider change must surface the contract consumer file.

    The TS consumer has no static link to the Python provider (different
    language), so only the declared contract can carry the dependency. With
    the contract in place the predicted broken file == the declared consumer
    (fp/fn = 0 against the manifest).
    """
    gdir = str(tmp_path / "g")
    create("g", gdir)
    provider = _make_py_provider(tmp_path / "py")
    consumer = _make_ts_consumer(tmp_path / "ts")
    graphs.add_repo("g", provider, gdir)
    graphs.add_repo("g", consumer, gdir)
    add_contract("g", consumer_repo=consumer, consumer_qname="src.client.callRun",
                 provider_repo=provider, provider_qname="pkg.mod.run",
                 kind="calls", graphs_dir=gdir)

    server = McpServer(graph="g", graphs_dir=gdir)
    out = server.handle({"jsonrpc": "2.0", "id": 1, "method": "tools/call",
                         "params": {"name": "impact",
                                    "arguments": {"symbol": "pkg.mod.run"}}})
    assert out is not None and "result" in out
    payload = json.loads(out["result"]["content"][0]["text"])
    # provider repo matched
    assert {r["repo"] for r in payload["repos"]} == {provider}
    # contract consumer surfaced across the boundary
    consumers = payload.get("contract_consumers", [])
    assert consumers, payload
    predicted = {c["file_path"] for c in consumers}
    assert predicted == {"src/client.ts"}
