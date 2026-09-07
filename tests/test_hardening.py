"""P0 hardening: src-layout import-root, lazy-import bindings, parse metrics."""
from __future__ import annotations

from pathlib import Path

from agentedit.index.indexer import index_repo


def _write(path: Path, rel: str, content: str) -> Path:
    p = path / rel
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(content, encoding="utf-8")
    return p


def test_src_layout_auto_module_root(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    _write(repo, "src/pkg/__init__.py", "")
    _write(repo, "src/pkg/mod.py", "def f():\n    return 1\n")
    _write(repo, "src/pkg/use.py",
           "from pkg.mod import f\n\n\ndef go():\n    return f()\n")
    db = str(tmp_path / "g.db")
    summary = index_repo(str(repo), db)
    assert summary["language"] == "python"
    assert summary["parse_error_files"] == []
    import sqlite3
    c = sqlite3.connect(db)
    mods = {r[0] for r in c.execute("SELECT module_qname FROM files")}
    assert "pkg.mod" in mods and "pkg.use" in mods
    assert not any(m.startswith("src.") for m in mods), mods
    edge = c.execute(
        "SELECT method FROM edges WHERE kind='calls' AND source_qname='pkg.use.go'"
    ).fetchone()
    assert edge is not None and edge[0] == "import", edge
    c.close()


def test_src_layout_auto_not_applied_when_python_at_root(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    _write(repo, "core.py", "def f():\n    return 1\n")
    _write(repo, "src/pkg/mod.py", "def g():\n    return 2\n")
    db = str(tmp_path / "g.db")
    index_repo(str(repo), db)
    import sqlite3
    c = sqlite3.connect(db)
    mods = {r[0] for r in c.execute("SELECT module_qname FROM files")}
    assert "src.pkg.mod" in mods, mods  # root is import root -> src prefix kept
    c.close()


def test_lazy_import_binding_resolves(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    _write(repo, "core.py", "def f():\n    return 1\n")
    _write(repo, "cli.py",
           "def main():\n    from core import f\n    return f()\n")
    db = str(tmp_path / "g.db")
    index_repo(str(repo), db)
    import sqlite3
    c = sqlite3.connect(db)
    edge = c.execute(
        "SELECT method, target_qname FROM edges "
        "WHERE kind='calls' AND source_qname='cli.main'"
    ).fetchone()
    assert edge is not None
    assert edge[0] == "import", edge
    assert edge[1] == "core.f", edge
    c.close()


def test_conflicting_lazy_imports_not_promoted(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    _write(repo, "m1.py", "def x():\n    return 1\n")
    _write(repo, "m2.py", "def x():\n    return 2\n")
    _write(repo, "cli.py",
           "def a():\n    from m1 import x\n    return x()\n\n"
           "def b():\n    from m2 import x\n    return x()\n")
    db = str(tmp_path / "g.db")
    index_repo(str(repo), db)
    import sqlite3
    c = sqlite3.connect(db)
    edges = c.execute(
        "SELECT source_qname, method FROM edges WHERE kind='calls'"
    ).fetchall()
    # ambiguous bindings must NOT become high-confidence import edges
    assert edges == [], edges
    c.close()


def test_parse_error_and_method_metrics(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    _write(repo, "good.py", "def f():\n    return 1\n")
    _write(repo, "bad.py", "def broken(:\n  pass\n")
    db = str(tmp_path / "g.db")
    summary = index_repo(str(repo), db)
    assert summary["parse_error_files"] == ["bad.py"]
    by_method = summary["edges_by_method"]
    assert isinstance(by_method, dict)
    # good.f is only referenced nowhere; still file/module edge exists? Ensure call metrics sane
    import sqlite3
    c = sqlite3.connect(db)
    total = c.execute("SELECT COUNT(*) FROM edges").fetchone()[0]
    assert total == sum(by_method.values())
    c.close()
