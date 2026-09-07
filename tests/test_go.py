"""Go backend: extraction + end-to-end index/impact."""
from __future__ import annotations

import shutil
from pathlib import Path
from typing import Any

from agentedit.analyze.impact import impact, would_break
from agentedit.backends.go import module_id, parse_file
from agentedit.index.indexer import index_repo
from agentedit.model import ParsedFile
from agentedit.store.sqlite import GraphStore

FIXTURE = "tests/fixtures/sample_go"


def _parse(relpath: str) -> ParsedFile:
    with open(f"{FIXTURE}/{relpath}", "rb") as fh:
        return parse_file(relpath, fh.read())


def test_go_module_id_and_exported() -> None:
    assert module_id("pkg/auth/auth.go") == "pkg.auth"
    parsed = _parse("pkg/auth/auth.go")
    names = {s.qname: s for s in parsed.symbols}
    assert "pkg.auth.Authenticate" in names
    assert names["pkg.auth.Authenticate"].exported is True


def test_go_method_and_cross_package_call() -> None:
    parsed = _parse("pkg/api/api.go")
    qnames = {s.qname for s in parsed.symbols}
    assert "pkg.api.Controller.Login" in qnames
    imports = {i.local for i in parsed.imports}
    assert "auth" in imports
    calls = [(e.source, e.target) for e in parsed.edges if e.kind == "calls"]
    assert ("pkg.api.Controller.Login", "auth.Authenticate") in calls


def test_go_index_and_impact(tmp_path: Path) -> None:
    repo = tmp_path / "work"
    shutil.copytree(FIXTURE, repo, dirs_exist_ok=True)
    db = str(tmp_path / "graph.db")
    summary: dict[str, Any] = index_repo(str(repo), db)
    assert summary["language"] == "go"
    store = GraphStore(db).connect()
    try:
        report = impact(store, "pkg.auth.Authenticate")
        assert {a.qname for a in report.direct} == {"pkg.api.Controller.Login"}
        wb = would_break(store, "pkg.auth.Authenticate", change="removed")
        assert wb.risk == "high"
    finally:
        store.close()
