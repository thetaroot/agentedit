"""P2 regressions — unresolved classification (external / in-repo / dynamic).

The audit showed the raw ``unresolved`` counter mixed third-party imports
(fastapi, httpx, …) with genuine in-repo gaps and python runtime dispatch.
The index summary must now report three honest buckets.
"""
from __future__ import annotations

from pathlib import Path

from agentedit.index.indexer import _classify_unresolved_root, index_repo


def test_classify_root_buckets() -> None:
    module_tops = {"svc", "web"}
    symbol_names = {"BrainService", "standalone"}
    assert _classify_unresolved_root("httpx", module_tops, symbol_names) == "external"
    assert _classify_unresolved_root("os", module_tops, symbol_names) == "external"
    assert _classify_unresolved_root("self", module_tops, symbol_names) == "dynamic"
    assert _classify_unresolved_root("cls", module_tops, symbol_names) == "dynamic"
    assert _classify_unresolved_root("svc", module_tops, symbol_names) == "in_repo"
    assert _classify_unresolved_root("BrainService", module_tops, symbol_names) == "in_repo"


def test_index_summary_reports_unresolved_buckets(tmp_path: Path) -> None:
    repo = tmp_path / "work"
    (repo / "app").mkdir(parents=True)
    (repo / "app" / "__init__.py").write_text("")
    (repo / "app" / "svc.py").write_text(
        "import httpx\n"
        "import os\n"
        "class BrainService:\n"
        "    def run(self):\n"
        "        return httpx.get('http://x')\n"
        "    def crash(self):\n"
        "        self._never_bound.call()\n"
        "        BrainService.not_there()\n"
        "def main():\n"
        "    os.getcwd()\n"
    )
    db = str(tmp_path / "graph.db")
    summary = index_repo(str(repo), db)
    assert summary["unresolved_external"] >= 2  # httpx.get, os.getcwd
    assert summary["unresolved_dynamic"] >= 1   # self._never_bound.call
    assert summary["unresolved_in_repo"] >= 1   # BrainService.not_there (real symbol root)
    assert summary["unresolved"] == (
        summary["unresolved_external"]
        + summary["unresolved_dynamic"]
        + summary["unresolved_in_repo"]
    )
