"""V1.0: multi-repo analysis through the CLI (impact/audit/why --graph).

Launch claim under test: one local graph can hold several repositories and the
CLI fans a single query out over every member, answers are repo-tagged, and
cross-repo reach is reported only through declared, verified contracts.
"""
from __future__ import annotations

import shutil
from pathlib import Path

import pytest

from agentedit.cli import main as cli_main
from agentedit.store.sqlite import GraphStore

TS_FIXTURE = "tests/fixtures/sample_ts"


def _write_ts(repo: Path, rel: str, body: str) -> None:
    p = repo / rel
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(body, encoding="utf-8")


@pytest.fixture()
def graph_env(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> tuple[Path, Path, Path]:
    graphs_dir = tmp_path / "graphs"
    graphs_dir.mkdir()
    monkeypatch.setenv("AGENTEDIT_GRAPHS", str(graphs_dir))

    repo_a = tmp_path / "repo_a"
    shutil.copytree(TS_FIXTURE, repo_a)

    repo_b = tmp_path / "repo_b"
    _write_ts(repo_b, "src/core/client.ts",
              "class Client {\n"
              "  call(): void {\n"
              "    // cross-repo call is declared as a contract below\n"
              "  }\n"
              "}\n")
    return graphs_dir, repo_a, repo_b


def _consumer_qname(repo_b: Path) -> str:
    store = GraphStore(str(repo_b / ".agentedit" / "graph.db")).connect()
    try:
        rows = store.search_symbols("Client")
    finally:
        store.close()
    method = [r for r in rows if "call" in str(r["qname"])]
    return str(method[0]["qname"])


def test_cli_graph_impact_audit_why_and_contracts(
    graph_env: tuple[Path, Path, Path], capsys: pytest.CaptureFixture[str],
) -> None:
    graphs_dir, repo_a, repo_b = graph_env

    assert cli_main(["index", str(repo_a)]) == 0
    assert cli_main(["index", str(repo_b)]) == 0
    capsys.readouterr()

    assert cli_main(["graph", "create", "g"]) == 0
    assert cli_main(["graph", "add-repo", "g", str(repo_a)]) == 0
    assert cli_main(["graph", "add-repo", "g", str(repo_b)]) == 0

    consumer = _consumer_qname(repo_b)
    assert cli_main(["graph", "contract-add", "g",
                     "--consumer-repo", str(repo_b), "--consumer", consumer,
                     "--provider-repo", str(repo_a),
                     "--provider", "src.auth.authenticate"]) == 0
    capsys.readouterr()

    # impact over the graph: provider answered from repo_a, in-repo dependant
    # present, and the cross-repo contract consumer surfaced.
    assert cli_main(["impact", "src.auth.authenticate", "--graph", "g"]) == 0
    out = capsys.readouterr().out
    assert f"[{repo_a}]" in out
    assert "src.controller.AuthController.login" in out  # in-repo dependant
    assert "CONTRACT CONSUMERS (cross-repo, declared)" in out
    assert consumer in out and str(repo_b) in out

    # audit + why also run repo-tagged over the graph.
    assert cli_main(["audit", "src.auth.authenticate", "--graph", "g"]) == 0
    out = capsys.readouterr().out
    assert f"[{repo_a}]" in out and "AUDIT" in out

    assert cli_main(["why", "src.auth.authenticate", "--graph", "g"]) == 0
    out = capsys.readouterr().out
    assert f"[{repo_a}]" in out and "WHY" in out

    # a symbol absent from every member is reported honestly.
    assert cli_main(["impact", "nope.missing", "--graph", "g"]) == 0
    assert "no member of graph 'g' contains nope.missing" in capsys.readouterr().out
