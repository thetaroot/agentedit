"""Metadata sanity: version single-sourced, packaging consistent."""
import importlib.metadata

import agentedit


def test_version_single_source() -> None:
    assert importlib.metadata.version("agentedit") == agentedit.__version__


def test_canonical_kinds_used_by_extractor() -> None:
    from agentedit.backends.typescript import parse_file

    with open("tests/fixtures/sample_ts/src/auth.ts", "rb") as fh:
        parsed = parse_file("src/auth.ts", fh.read())
    kinds = {s.kind for s in parsed.symbols}
    assert kinds <= set(agentedit.model.SYMBOL_KINDS)
    for s in parsed.symbols:
        assert s.module_qname == "src.auth"
