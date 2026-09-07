"""AST-based Rust mutations for the eval benchmark (mirrors ts/py/go mutators)."""
from __future__ import annotations

from collections.abc import Iterator
from typing import Any

from agentedit.backends.rust import parse_tree

_DECL_TYPES = frozenset({"function_item", "struct_item", "enum_item", "trait_item", "type_item", "const_item"})


def _walk(node: Any) -> Iterator[Any]:
    stack = [node]
    while stack:
        current = stack.pop()
        yield current
        stack.extend(current.children)


def find_declaration(root: Any, name: str, start_row: int, source: bytes) -> Any | None:
    best: Any = None
    best_span = 10**9
    for node in _walk(root):
        if node.type not in _DECL_TYPES:
            continue
        name_node = node.child_by_field_name("name")
        if name_node is None:
            continue
        if source[name_node.start_byte:name_node.end_byte].decode() != name:
            continue
        span = abs(node.start_point[0] - start_row)
        if span < best_span:
            best, best_span = node, span
    return best


def _apply(text: str, edits: list[tuple[int, int, str]]) -> str:
    for start, end, replacement in sorted(edits, reverse=True):
        text = text[:start] + replacement + text[end:]
    return text


def mutate(path: str, source: bytes, name: str, start_row: int, mutation: str) -> str | None:
    text = source.decode("utf-8")
    root = parse_tree(path, source)
    decl = find_declaration(root, name, start_row, source)
    if decl is None:
        return None

    if mutation == "remove-declaration":
        return _apply(text, [(decl.start_byte, decl.end_byte, "")])

    if mutation == "rename":
        name_node = decl.child_by_field_name("name")
        if name_node is None:
            return None
        return _apply(text, [(name_node.start_byte, name_node.end_byte, name + "Changed")])

    if mutation == "add-required-param":
        params = decl.child_by_field_name("parameters")
        if params is None:
            return None
        if params.end_byte > params.start_byte and text[params.end_byte - 1] == ")":
            insert_at = params.end_byte - 1
            inner = text[params.start_byte:insert_at].strip()
            addition = "" if inner == "(" else ", "
            return _apply(text, [(insert_at, insert_at, addition + "_req: u32")])
        return None

    return None
