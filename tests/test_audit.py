"""P3: audit (flagship) + why over fixtures and a real git repo."""
from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path
from typing import Any

from agentedit.audit import audit, is_file_target, why
from agentedit.index.indexer import index_repo
from agentedit.mcp_server import McpServer
from agentedit.store.sqlite import GraphStore

FIXTURE = "tests/fixtures/sample_ts"


def _repo(db_dir: Path, name: str = "repo") -> tuple[str, str]:
    repo = db_dir / name
    shutil.copytree(FIXTURE, repo, dirs_exist_ok=True)
    db = str(db_dir / f"{name}.db")
    index_repo(str(repo), db)
    return str(repo), db


def test_is_file_target() -> None:
    assert is_file_target("src/auth.ts")
    assert not is_file_target("src.auth.authenticate")


def test_audit_symbol_delivers_brief(tmp_path: Path) -> None:
    repo, db = _repo(tmp_path)
    store = GraphStore(db).connect()
    try:
        aud = audit(store, repo, "src.auth.authenticate")
        assert aud["kind"] == "symbol" and aud["target"] == "src.auth.authenticate"
        assert aud["risk"] in ("high", "medium", "low")
        # direct dependants' files present with relations + confidence
        files = {f["file"] for f in aud["files"]}
        assert "src/controller.ts" in files
        assert all(f["confidence"] >= 0.0 for f in aud["files"])
        assert isinstance(aud["read_set"], list) and aud["read_set"]
        assert isinstance(aud["resolution"], dict)
    finally:
        store.close()


def test_audit_file_mode(tmp_path: Path) -> None:
    repo, db = _repo(tmp_path)
    store = GraphStore(db).connect()
    try:
        aud = audit(store, repo, "src/auth.ts")
        assert aud["kind"] == "file"
        assert aud["symbols_audited"] >= 1
        assert any(f["file"] == "src/controller.ts" for f in aud["files"])
    finally:
        store.close()


def _git_init(repo: str) -> None:
    subprocess.run(["git", "-C", repo, "init", "-q", "-b", "main"], check=True)
    subprocess.run(["git", "-C", repo, "config", "user.email", "t@t"], check=True)
    subprocess.run(["git", "-C", repo, "config", "user.name", "t"], check=True)
    subprocess.run(["git", "-C", repo, "add", "-A"], check=True)
    subprocess.run(["git", "-C", repo, "commit", "-qm", "chore: baseline"], check=True)


def test_why_returns_git_facts_and_candidates(tmp_path: Path) -> None:
    repo = str(tmp_path / "repo")
    Path(repo, "src").mkdir(parents=True)
    src = Path(repo) / "src" / "a.ts"
    src.write_text("export function target() {\n    return 1;\n}\n", encoding="utf-8")
    _git_init(repo)
    db = str(tmp_path / "g.db")
    index_repo(repo, db)

    # edit INSIDE target's line range with an instructive message
    src.write_text("export function target() {\n    return 2; // never 1\n}\n", encoding="utf-8")
    subprocess.run(["git", "-C", repo, "add", "-A"], check=True)
    subprocess.run(["git", "-C", repo, "commit", "-qm",
                    "fix: never allow sync calls in target"], check=True)
    index_repo(repo, db)

    store = GraphStore(db).connect()
    try:
        res = why(store, repo, "src.a.target")
        assert res["git"] is True
        assert res["facts"], res
        assert any("never allow sync" in f["subject"] for f in res["facts"])
        assert any(c["status"] == "candidate" and c["origin"] == "git"
                   for c in res["candidates"])
    finally:
        store.close()


def test_mcp_audit_and_why_tools(tmp_path: Path) -> None:
    repo, db = _repo(tmp_path)
    _git_init(repo)
    server = McpServer(repo)
    server._db = db

    def call(name: str, arguments: dict[str, Any], ident: int) -> dict[str, Any]:
        out = server.handle({"jsonrpc": "2.0", "id": ident, "method": "tools/call",
                             "params": {"name": name, "arguments": arguments}})
        assert out is not None and "result" in out
        payload = json.loads(out["result"]["content"][0]["text"])
        assert isinstance(payload, dict)
        return payload

    aud = call("audit", {"target": "src.auth.authenticate"}, 2)
    assert aud["risk"] in ("high", "medium", "low") and aud["read_set"]
    w = call("why", {"symbol": "src.auth.authenticate"}, 3)
    assert "facts" in w
