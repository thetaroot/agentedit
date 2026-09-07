"""AST-based Python mutations for the eval benchmark.

Mirrors eval/ts_mutate semantics for python: locate a declaration by name and
start row, then edit spans so the code stays valid while external dependants
stop type-checking under pyright.
"""
from __future__ import annotations

from collections.abc import Iterator
from typing import Any

from agentedit.backends.python import parse_tree

_DECL_TYPES = frozenset({"function_definition", "class_definition", "decorated_definition"})


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
        # unwrap decorators (decorated_definition wraps the real definition)
        target = node.child_by_field_name("definition") if node.type == "decorated_definition" else node
        if target is None or target.type not in ("function_definition", "class_definition"):
            continue
        name_node = target.child_by_field_name("name")
        if name_node is None:
            continue
        if source[name_node.start_byte:name_node.end_byte].decode() != name:
            continue
        span = abs(target.start_point[0] - start_row)
        if span < best_span:
            best, best_span = target, span
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
        start = decl.start_byte
        # include any decorators and a leading newline boundary
        while start > 0 and text[start - 1] in " \t":
            start -= 1
        return _apply(text, [(start, decl.end_byte, "")])

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
            return _apply(text, [(insert_at, insert_at, addition + "agenteditReq")])
        return None

    return None
