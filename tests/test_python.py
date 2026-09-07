"""Python backend: extraction + end-to-end index/impact."""
from __future__ import annotations

import shutil
from pathlib import Path
from typing import Any

from agentedit.analyze.impact import impact, would_break
from agentedit.backends.python import module_id, parse_file
from agentedit.index.indexer import index_repo
from agentedit.store.sqlite import GraphStore

FIXTURE = "tests/fixtures/sample_py"


def _parse(relpath: str) -> Any:
    with open(f"{FIXTURE}/{relpath}", "rb") as fh:
        return parse_file(relpath, fh.read())


def test_module_id_rules() -> None:
    assert module_id("src/pkg/core.py") == "src.pkg.core"
    assert module_id("src/pkg/__init__.py") == "src.pkg"
    assert module_id("app.py") == "app"


def test_python_symbols_and_calls() -> None:
    parsed = _parse("controller.py")
    qnames = {s.qname for s in parsed.symbols}
    assert "controller.AuthController" in qnames
    assert "controller.AuthController.login" in qnames
    locals_ = {i.local for i in parsed.imports}
    assert {"authenticate", "refresh_session"} <= locals_
    calls = [(e.source, e.target) for e in parsed.edges if e.kind == "calls"]
    assert ("controller.AuthController.login", "authenticate") in calls


def test_python_index_and_impact(tmp_path: Path) -> None:
    repo = tmp_path / "work"
    shutil.copytree(FIXTURE, repo, dirs_exist_ok=True)
    db = str(tmp_path / "graph.db")
    summary: dict[str, Any] = index_repo(str(repo), db)
    assert summary["language"] == "python"
    assert summary["symbols"] >= 13

    store = GraphStore(db).connect()
    try:
        report = impact(store, "auth.authenticate")
        direct = {a.qname for a in report.direct}
        assert "controller.AuthController.login" in direct
        assert "controller" in direct  # import-name dependant
        assert report.risk == "high"

        wb = would_break(store, "auth.authenticate", change="removed")
        assert wb.risk == "high"
        assert {a.qname for a in wb.direct} == {"controller.AuthController.login", "controller"}
    finally:
        store.close()
