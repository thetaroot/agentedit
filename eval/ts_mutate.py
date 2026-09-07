"""AST-based TypeScript mutations for the eval benchmark.

Each mutation edits the declaration node (located via tree-sitter, using the
same grammar as the indexer) so the resulting code *stays valid* while any
external dependant stops type-checking:

* ``remove-declaration``   — delete the whole (export) statement.
* ``rename``               — rename the symbol; importers and callers break.
* ``add-required-param``   — append a required parameter; call sites break.
"""
from __future__ import annotations

from collections.abc import Iterator
from typing import Any

from agentedit.backends.typescript import parse_tree

_DECL_TYPES = frozenset({
    "function_declaration", "async_function_declaration", "generator_function_declaration",
    "class_declaration", "interface_declaration", "type_alias_declaration",
    "enum_declaration", "lexical_declaration",
})
_NAME_FIELD_TYPES = frozenset({"identifier", "type_identifier"})


def _walk(node: Any) -> Iterator[Any]:
    stack = [node]
    while stack:
        current = stack.pop()
        yield current
        stack.extend(current.children)


def find_declaration(root: Any, name: str, start_row: int, source: bytes) -> Any | None:
    """Locate the declaration node for a symbol (by name + start row)."""
    best: Any = None
    best_span = 10**9
    for node in _walk(root):
        if node.type not in _DECL_TYPES:
            continue
        name_node = node.child_by_field_name("name")
        if name_node is None:
            continue
        if name_node.type not in _NAME_FIELD_TYPES:
            continue
        if source[name_node.start_byte:name_node.end_byte].decode() != name:
            continue
        span = abs(node.start_point[0] - start_row)
        if span < best_span:
            best, best_span = node, span
    return best


def _name_span(decl: Any, source: bytes) -> tuple[int, int]:
    name_node = decl.child_by_field_name("name")
    assert name_node is not None
    return name_node.start_byte, name_node.end_byte


def _export_span(root: Any, decl: Any) -> tuple[int, int]:
    """Span of the whole statement to remove (incl. the ``export`` keyword)."""
    parent = decl.parent
    while parent is not None and parent.type not in (
        "export_statement", "program", "statement_block", "source_file",
    ):
        parent = parent.parent
    if parent is not None and parent.type == "export_statement":
        return parent.start_byte, parent.end_byte
    return decl.start_byte, decl.end_byte


def _apply(text: str, edits: list[tuple[int, int, str]]) -> str:
    for start, end, replacement in sorted(edits, reverse=True):
        text = text[:start] + replacement + text[end:]
    return text


def mutate(path: str, source: bytes, name: str, start_row: int, mutation: str) -> str | None:
    """Return the mutated source text, or None if the mutation is not applicable."""
    text = source.decode("utf-8")
    root = parse_tree(path, source)
    decl = find_declaration(root, name, start_row, source)
    if decl is None:
        return None

    if mutation == "remove-declaration":
        start, end = _export_span(root, decl)
        return _apply(text, [(start, end, "")])

    if mutation == "rename":
        start, end = _name_span(decl, source)
        return _apply(text, [(start, end, name + "Changed")])

    if mutation == "add-required-param":
        params = decl.child_by_field_name("parameters")
        if params is None:
            # const arrow functions keep their parameters under the value.
            value = decl.child_by_field_name("value")
            if value is not None:
                params = value.child_by_field_name("parameters")
        if params is None:
            return None
        if params.end_byte > params.start_byte and text[params.end_byte - 1] == ")":
            insert_at = params.end_byte - 1
            inner = text[params.start_byte:insert_at].strip()
            addition = "" if inner == "(" else ", "
            return _apply(text, [(insert_at, insert_at, addition + "agenteditReq: string")])
        return None

    return None
