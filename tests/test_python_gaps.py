"""P0 audit regressions (2026-09-05):

1. Decorated python definitions (``@decorator`` wraps) must be indexed —
   previously FastAPI routes, ``@classmethod``/``@property`` methods and
   decorated classes silently vanished from the graph.
2. Multi-root monorepos: an absolute import written against a sub-app's
   ``sys.path`` root (``from services.brain import …`` in ``svc/``) must
   resolve to that app's module id (``svc.services.brain``) via a *unique*
   suffix match — not only via the low-confidence global-name fallback.
3. Lazy/function-level imports already promote bindings; the gap was the
   decorated target symbol itself being missing.
"""
from __future__ import annotations

import shutil
from pathlib import Path

from agentedit.backends.python import PythonBackend, parse_file
from agentedit.index.indexer import index_repo
from agentedit.model import ParsedFile
from agentedit.store.sqlite import GraphStore

MULTIROOT = "tests/fixtures/eval_py_multiroot"


def _parse_text(source: str) -> ParsedFile:
    return parse_file("svc/main.py", source.encode("utf-8"))


def test_decorated_module_function_and_class_indexed() -> None:
    parsed = _parse_text(
        "from services.brain import BrainBuilder\n"
        "@app.get('/x')\n"
        "async def route():\n"
        "    return BrainBuilder.build()\n"
        "\n"
        "class C:\n"
        "    @classmethod\n"
        "    def build(cls):\n"
        "        return 1\n"
        "\n"
        "    @property\n"
        "    def val(self):\n"
        "        return self.build()\n"
        "\n"
        "@deco\n"
        "class D:\n"
        "    pass\n"
    )
    qnames = {s.qname for s in parsed.symbols if s.kind != "module"}
    assert "svc.main.route" in qnames
    assert "svc.main.C" in qnames
    assert "svc.main.C.build" in qnames
    assert "svc.main.C.val" in qnames
    assert "svc.main.D" in qnames
    calls = [(e.source, e.target) for e in parsed.edges if e.kind == "calls"]
    assert ("svc.main.route", "BrainBuilder.build") in calls


def test_multiroot_suffix_import_is_import_method(tmp_path: Path) -> None:
    repo = tmp_path / "work"
    shutil.copytree(MULTIROOT, repo, dirs_exist_ok=True)
    db = str(tmp_path / "graph.db")
    summary = index_repo(str(repo), db)
    assert summary["symbols"] >= 15

    store = GraphStore(db).connect()
    try:
        rows = store.edges_from("svc.api.use")
        methods = {(r["target_qname"], r["kind"], r["method"]) for r in rows}
        # cross-root absolute import now resolves through the import map, not
        # the global-name fallback.
        assert ("svc.services.brain.standalone", "calls", "import") in methods
        # same-app api -> brain also resolves as a real import edge.
        assert ("svc.services.brain.BrainService", "uses_type", "import") in methods
    finally:
        store.close()


def test_multiroot_lazy_import_edges(tmp_path: Path) -> None:
    repo = tmp_path / "work"
    shutil.copytree(MULTIROOT, repo, dirs_exist_ok=True)
    db = str(tmp_path / "graph.db")
    index_repo(str(repo), db)

    store = GraphStore(db).connect()
    try:
        rows = store.edges_from("svc.gym.build_brain")
        targets = {r["target_qname"] for r in rows}
        assert "svc.services.brain.BrainBuilder.build" in targets
        methods = {r["method"] for r in rows if r["target_qname"].startswith("svc.services.brain")}
        assert "import" in methods
    finally:
        store.close()


def test_multiroot_ambiguous_suffix_stays_unresolved(tmp_path: Path) -> None:
    repo = tmp_path / "work"
    (repo / "alpha").mkdir(parents=True)
    (repo / "beta").mkdir(parents=True)
    (repo / "alpha" / "services").mkdir(parents=True)
    (repo / "beta" / "services").mkdir(parents=True)
    (repo / "alpha" / "__init__.py").write_text("")
    (repo / "alpha" / "services" / "__init__.py").write_text("")
    (repo / "beta" / "__init__.py").write_text("")
    (repo / "beta" / "services" / "__init__.py").write_text("")
    (repo / "alpha" / "services" / "brain.py").write_text(
        "class BrainService:\n    pass\n"
    )
    (repo / "beta" / "services" / "brain.py").write_text(
        "class BrainService:\n    pass\n"
    )
    (repo / "alpha" / "main.py").write_text(
        "from services.brain import BrainService\n"
        "def use(b: BrainService):\n"
        "    return b\n"
    )
    db = str(tmp_path / "graph.db")
    index_repo(str(repo), db)

    store = GraphStore(db).connect()
    try:
        rows = store.edges_from("alpha.main.use")
        # Ambiguous across two apps: must NOT fabricate an import edge.
        assert not any(r["method"] == "import" for r in rows)
    finally:
        store.close()


def test_probe_multiroot_recall_is_gate() -> None:
    """Multi-root recall probe must hold 1.0/1.0 (regression gate for P0)."""
    from eval.probe_multiroot import run

    results = run()
    assert results, "probe returned no targets"
    for target, m in results.items():
        assert m["recall"] == 1.0, f"recall drop on {target}: {m}"
        assert m["precision"] == 1.0, f"precision drop on {target}: {m}"


def test_backend_parse_decorated_is_deterministic() -> None:
    src = "@deco\ndef f():\n    return 1\n"
    a = _parse_text(src)
    b = _parse_text(src)
    assert [s.qname for s in a.symbols] == [s.qname for s in b.symbols]
    assert any(s.qname == "svc.main.f" for s in a.symbols)


def test_python_backend_accepts_decorated_offset_parse() -> None:
    """Decorated symbols survive the real backend parse path (offset=None)."""
    backend = PythonBackend()
    pf = backend.parse_file(
        "web/handlers.py",
        b"@router.get('/x')\ndef handler():\n    return 1\n",
    )
    assert any(s.qname == "web.handlers.handler" for s in pf.symbols)
