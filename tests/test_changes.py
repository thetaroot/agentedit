"""changes tool needs a real git repo — build one in a tmp dir from the fixture."""
from __future__ import annotations

import shutil
import subprocess
from pathlib import Path
from typing import Any

from agentedit.analyze.changes import changes
from agentedit.index.indexer import index_repo
from agentedit.store.sqlite import GraphStore

FIXTURE = "tests/fixtures/sample_ts"


def test_changes_reports_ripple(tmp_path: Path) -> None:
    repo = tmp_path / "work"
    shutil.copytree(FIXTURE, repo, dirs_exist_ok=True)
    subprocess.run(["git", "init", "-q", str(repo)], check=True)
    subprocess.run(["git", "-C", str(repo), "config", "user.email", "t@t"], check=True)
    subprocess.run(["git", "-C", str(repo), "config", "user.name", "t"], check=True)
    subprocess.run(["git", "-C", str(repo), "add", "-A"], check=True)
    subprocess.run(["git", "-C", str(repo), "commit", "-qm", "init"], check=True)

    db = str(tmp_path / "graph.db")
    index_repo(str(repo), db)

    # simulate editing auth.ts
    auth = repo / "src" / "auth.ts"
    auth.write_text(auth.read_text() + "\nexport function x() { return 1; }\n")

    store = GraphStore(db).connect()
    try:
        report: dict[str, Any] = changes(store, str(repo))
        assert "src/auth.ts" in report["changed_files"]
        affected = {a["qname"] for a in report["affected"]}
        assert "src.controller.AuthController.login" in affected
        assert "src.controller" in affected  # module importer
    finally:
        store.close()
