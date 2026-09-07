"""Deterministic extraction checks on the sample fixture.

parse_file takes a *repo-relative* path (that is what becomes the module qname);
the bytes are read separately here from the fixture root.
"""
from __future__ import annotations

import pytest

from agentedit.backends.typescript import parse_file
from agentedit.model import ParsedFile

FIXTURE_ROOT = "tests/fixtures/sample_ts"


def _parse(relpath: str) -> ParsedFile:
    with open(f"{FIXTURE_ROOT}/{relpath}", "rb") as fh:
        return parse_file(relpath, fh.read())


@pytest.fixture(scope="session")
def auth_parsed() -> ParsedFile:
    return _parse("src/auth.ts")


def test_module_symbol(auth_parsed: ParsedFile) -> None:
    module = auth_parsed.symbols[0]
    assert module.kind == "module"
    assert module.qname == "src.auth"
    assert module.file_path == "src/auth.ts"


def test_symbols_are_module_qualified(auth_parsed: ParsedFile) -> None:
    qnames = {s.qname for s in auth_parsed.symbols}
    assert "src.auth.User" in qnames
    assert "src.auth.validateUser" in qnames
    assert "src.auth.authenticate" in qnames
    assert "src.auth.refreshSession" in qnames


def test_call_edge_inside_body(auth_parsed: ParsedFile) -> None:
    calls = [(e.source, e.target) for e in auth_parsed.edges if e.kind == "calls"]
    assert ("src.auth.authenticate", "validateUser") in calls


def test_import_and_call_edges() -> None:
    parsed = _parse("src/controller.ts")
    locals_ = {imp.local for imp in parsed.imports}
    assert {"authenticate", "refreshSession", "User"} <= locals_
    methods = {s.name for s in parsed.symbols if s.kind == "method"}
    assert "login" in methods
    calls = [(e.source, e.target) for e in parsed.edges if e.kind == "calls"]
    assert ("src.controller.AuthController.login", "authenticate") in calls


def test_interface_symbol(auth_parsed: ParsedFile) -> None:
    interfaces = [s for s in auth_parsed.symbols if s.kind == "interface"]
    assert [s.qname for s in interfaces] == ["src.auth.User"]
