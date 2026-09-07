# SPDX-License-Identifier: Apache-2.0
# SPDX-FileCopyrightText: 2026 thetaroot

"""Go language backend.

Deterministic extraction of a Go codebase via tree-sitter. Module identity is
the *package directory* (dotted, repo-relative) — files of one package share a
module id, so intra-package references resolve naturally and cross-package
references go through import aliases, exactly mirroring Go semantics.

Export marker: a symbol is exported iff its name starts with an uppercase
letter (visible outside its package).

Static extraction only: dynamic constructs are left unresolved and counted,
never guessed.
"""
from __future__ import annotations

import logging
import posixpath
from dataclasses import replace
from typing import Any

from agentedit.model import ImportDecl, ParsedFile, RawEdge, Symbol

logger = logging.getLogger(__name__)

_PARSERS: dict[str, Any] = {}


def _parser() -> Any:
    if "go" in _PARSERS:
        return _PARSERS["go"]
    import tree_sitter_go as go
    from tree_sitter import Language, Parser

    parser = Parser(Language(go.language()))
    _PARSERS["go"] = parser
    return parser


def language_for_path(path: str) -> str | None:
    if path.endswith(".go") and not path.endswith("_test.go"):
        return GoBackend.LANGUAGE
    return None


def module_id(path: str) -> str:
    """Dotted package-directory id for a repo-relative go file.

    Files in the repo root (no directory) are not indexed as a package.
    """
    dirname = posixpath.dirname(path)
    if not dirname:
        return ""
    parts = [p for p in dirname.split("/") if p and p != "."]
    while parts and parts[0] == "..":
        parts.pop(0)
    return ".".join(parts)


def parse_tree(path: str, source_bytes: bytes) -> Any:
    return _parser().parse(source_bytes).root_node


def _text(node: Any, source: bytes) -> str:
    return source[node.start_byte : node.end_byte].decode("utf-8", errors="replace")


def _line_range(node: Any) -> tuple[int, int]:
    return node.start_point[0] + 1, node.end_point[0] + 1


def _exported(name: str) -> bool:
    return bool(name) and name[0].isupper()


def parse_file(path: str, source_bytes: bytes) -> ParsedFile:
    root = parse_tree(path, source_bytes)
    module = module_id(path)
    if not module:
        raise ValueError(f"go file without a package directory is not indexable: {path}")
    module_name = module.rsplit(".", 1)[-1]
    result = ParsedFile(
        path=path,
        language=GoBackend.LANGUAGE,
        module_qname=module,
        symbols=[
            Symbol(
                qname=module,
                name=module_name,
                kind="module",
                file_path=path,
                line_start=1,
                line_end=max(1, source_bytes.count(b"\n") + 1),
                language=GoBackend.LANGUAGE,
                module_qname=module,
            )
        ],
    )
    for child in root.children:
        nt = child.type
        if nt == "import_declaration":
            _extract_imports(child, source_bytes, module, result)
        elif nt == "function_declaration":
            _emit_function(child, source_bytes, module, result)
        elif nt == "method_declaration":
            _emit_method(child, source_bytes, module, result)
        elif nt == "type_declaration":
            _emit_types(child, source_bytes, module, result)
        elif nt in ("const_declaration", "var_declaration"):
            _emit_values(child, source_bytes, module, result)
    result.symbols = [replace(s, module_qname=module) for s in result.symbols]
    if root.has_error:
        result.parse_errors.append(f"tree-sitter reported syntax errors in {path}")
    return result


# ---------------------------------------------------------------------------
# Declarations
# ---------------------------------------------------------------------------


def _emit_function(node: Any, source: bytes, module: str, result: ParsedFile) -> None:
    name_node = node.child_by_field_name("name")
    if name_node is None:
        return
    name = _text(name_node, source)
    if name.startswith(("Test", "Benchmark", "Example")):
        return
    qname = f"{module}.{name}"
    params = node.child_by_field_name("parameters")
    results = node.child_by_field_name("result")
    sig = _signature_of(params, results, source)
    line_start, line_end = _line_range(node)
    result.symbols.append(
        Symbol(
            qname=qname, name=name, kind="function", file_path=result.path,
            signature=sig, line_start=line_start, line_end=line_end,
            exported=_exported(name), language=GoBackend.LANGUAGE,
            module_qname=module,
        )
    )
    body = node.child_by_field_name("body")
    if body is not None:
        _collect_call_edges(body, source, qname, result)


def _emit_method(node: Any, source: bytes, module: str, result: ParsedFile) -> None:
    name_node = node.child_by_field_name("name")
    if name_node is None:
        return
    name = _text(name_node, source)
    receiver = node.child_by_field_name("receiver")
    owner = _receiver_type_name(receiver, source)
    if not owner:
        return
    qname = f"{module}.{owner}.{name}"
    params = node.child_by_field_name("parameters")
    results = node.child_by_field_name("result")
    sig = _signature_of(params, results, source)
    line_start, line_end = _line_range(node)
    result.symbols.append(
        Symbol(
            qname=qname, name=name, kind="method", file_path=result.path,
            signature=sig, line_start=line_start, line_end=line_end,
            exported=_exported(name), language=GoBackend.LANGUAGE,
            module_qname=module,
        )
    )
    body = node.child_by_field_name("body")
    if body is not None:
        _collect_call_edges(body, source, qname, result)


def _emit_types(node: Any, source: bytes, module: str, result: ParsedFile) -> None:
    for spec in node.children:
        if spec.type != "type_spec":
            continue
        name_node = spec.child_by_field_name("name")
        type_node = spec.child_by_field_name("type")
        if name_node is None or type_node is None:
            continue
        name = _text(name_node, source)
        qname = f"{module}.{name}"
        line_start, line_end = _line_range(spec)
        if type_node.type in ("struct_type", "interface_type"):
            kind = "class" if type_node.type == "struct_type" else "interface"
        else:
            kind = "type_alias"
        result.symbols.append(
            Symbol(
                qname=qname, name=name, kind=kind, file_path=result.path,
                signature=_text(type_node, source),
                line_start=line_start, line_end=line_end,
                exported=_exported(name), language=GoBackend.LANGUAGE,
                module_qname=module,
            )
        )
        if type_node.type == "interface_type":
            _collect_type_edges(type_node, source, qname, result)
        # method_set on struct/interface handled by method_declaration elsewhere


def _emit_values(node: Any, source: bytes, module: str, result: ParsedFile) -> None:
    for spec in node.children:
        if spec.type != "value_spec":
            continue
        name_node = spec.child_by_field_name("name")
        if name_node is None or name_node.type != "identifier":
            continue
        name = _text(name_node, source)
        qname = f"{module}.{name}"
        line_start, line_end = _line_range(spec)
        kind = "constant"
        result.symbols.append(
            Symbol(
                qname=qname, name=name, kind=kind, file_path=result.path,
                signature=None, line_start=line_start, line_end=line_end,
                exported=_exported(name), language=GoBackend.LANGUAGE,
                module_qname=module,
            )
        )


# ---------------------------------------------------------------------------
# Imports
# ---------------------------------------------------------------------------


def _extract_imports(node: Any, source: bytes, module: str, result: ParsedFile) -> None:
    specs: list[Any] = []
    stack: list[Any] = list(node.children)
    while stack:
        cur = stack.pop()
        if cur.type == "import_spec":
            specs.append(cur)
        else:
            stack.extend(cur.children)
    for spec in specs:
        path_node = spec.child_by_field_name("path")
        if path_node is None:
            continue
        import_path = _text(path_node, source).strip("\"")
        name_node = spec.child_by_field_name("name")
        if name_node is not None:
            local = _text(name_node, source).strip()
            if local in (".", "_"):
                continue
        else:
            local = import_path.rstrip("/").split("/")[-1]
        result.imports.append(
            ImportDecl(module=module, local=local, imported="*", spec=import_path)
        )


# ---------------------------------------------------------------------------
# Body scanning
# ---------------------------------------------------------------------------


def _collect_call_edges(body: Any, source: bytes, owner_qname: str, result: ParsedFile) -> None:
    stack: list[Any] = [body]
    while stack:
        n = stack.pop()
        if n.type == "call_expression":
            func = n.child_by_field_name("function")
            if func is not None:
                target = _callable_text(func, source)
                if target and target != "go" and target != "defer":
                    result.edges.append(RawEdge(source=owner_qname, target=target, kind="calls"))
        elif n.type == "func_literal":
            continue
        stack.extend(n.children)


def _callable_text(func: Any, source: bytes) -> str:
    if func.type == "identifier":
        return _text(func, source).strip()
    if func.type == "selector_expression":
        operand = func.child_by_field_name("operand")
        field = func.child_by_field_name("field")
        left = _callable_text(operand, source) if operand is not None else ""
        right = _text(field, source).strip() if field is not None else ""
        return f"{left}.{right}" if left and right else ""
    if func.type == "index_expression" or func.type == "type_arguments":
        # generic call like slices.Sort[T](...) — take the callee head
        child = func.child_by_field_name("operand") or (func.children[0] if func.children else None)
        return _callable_text(child, source) if child is not None else ""
    return ""


def _collect_type_edges(type_node: Any, source: bytes, owner_qname: str, result: ParsedFile) -> None:
    """Interface embeddings: interface A { B } -> A inherits B."""
    for child in type_node.children:
        if child.type == "type_elem" or child.type == "type_identifier":
            name = _text(child, source).strip()
            if name and name != owner_qname.rsplit(".", 1)[-1]:
                result.edges.append(RawEdge(source=owner_qname, target=name, kind="inherits"))


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _signature_of(params: Any, results: Any, source: bytes) -> str | None:
    parts = []
    if params is not None:
        parts.append(_text(params, source))
    if results is not None:
        parts.append(_text(results, source))
    return " ".join(parts) if parts else None


def _receiver_type_name(receiver: Any, source: bytes) -> str | None:
    """Type name of the receiver (``(r *Queue)``/``(r Queue)`` -> ``Queue``)."""
    if receiver is None:
        return None
    inner = _text(receiver, source).strip().strip("()")
    tokens = [t.strip() for t in inner.replace("*", " ").split() if t.strip()]
    return tokens[-1] if tokens else None


class GoBackend:
    LANGUAGE = "go"

    language = LANGUAGE

    def language_for_path(self, path: str) -> str | None:
        return language_for_path(path)

    def should_skip(self, path: str) -> bool:
        return not posixpath.dirname(path)

    def module_id(self, path: str) -> str:
        return module_id(path)

    def parse_file(self, path: str, source_bytes: bytes) -> ParsedFile:
        return parse_file(path, source_bytes)

    def spec_candidates(self, spec: str, from_path: str) -> list[str]:
        """Go import paths -> candidate package-dir module ids.

        An internal import is the module-path-prefixed package dir; we propose
        every suffix of the import path (the longest internal suffix is the
        package dir). External imports (stdlib, third-party) resolve to no
        existing module and are dropped by the resolver.
        """
        segments = [seg for seg in spec.split("/") if seg]
        candidates: list[str] = []
        for i in range(len(segments)):
            candidates.append(".".join(segments[i:]))
        return candidates
