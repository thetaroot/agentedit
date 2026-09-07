"""Signature-diff semantics, adapted from SwiftGate's test_would_break_signature."""
from agentedit.analyze.impact import _diff_signature, _is_optional


def test_diff_signature_empty() -> None:
    assert _diff_signature("", "") == {"added": [], "removed": [], "old_count": 0, "new_count": 0}


def test_diff_signature_added() -> None:
    d = _diff_signature("(a: int)", "(a: int, b: string)")
    assert d["added"] == ["b"]
    assert d["removed"] == []
    assert d["old_count"] == 1
    assert d["new_count"] == 2


def test_diff_signature_removed() -> None:
    d = _diff_signature("(a: int, b: string, c: boolean)", "(a: int)")
    assert d["removed"] == ["b", "c"]
    assert d["added"] == []
    assert d["old_count"] == 3


def test_diff_signature_identical() -> None:
    d = _diff_signature("(x: number, y: string)", "(x: number, y: string)")
    assert d["added"] == []
    assert d["removed"] == []
    assert d["old_count"] == d["new_count"] == 2


def test_diff_signature_no_parens() -> None:
    d = _diff_signature("class Foo", "class Bar")
    assert d["old_count"] == 0
    assert d["new_count"] == 0


def test_diff_signature_ignores_defaults_and_types() -> None:
    d = _diff_signature("(a: number = 5, b: string)", "(a: number, b: string)")
    assert d["added"] == []
    assert d["removed"] == []


def test_optional_param_detection() -> None:
    assert _is_optional("b?: string")
    assert _is_optional("c = 5")
    assert not _is_optional("a: string")


def test_nested_types_do_not_confuse_split() -> None:
    d = _diff_signature("(opts: { foo: string; bar: number })", "(opts: { foo: string })")
    assert d["old_count"] == 1
    assert d["new_count"] == 1
