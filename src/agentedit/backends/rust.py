# SPDX-License-Identifier: Apache-2.0
# SPDX-FileCopyrightText: 2026 thetaroot

"""Rust language backend.

Deterministic extraction of a Rust codebase via tree-sitter. Module identity
is the crate-relative module path (dotted): ``src/format.rs`` -> ``format``,
``src/pkg/mod.rs`` -> ``pkg``, ``src/pkg/bar.rs`` -> ``pkg.bar``.

Only `pub` items are cross-module visible (``exported``). ``use`` declarations
are modelled like python imports: a single-name ``use crate::x::Item;`` binds
``Item``; ``use crate::x;`` binds the module ``x`` as a namespace. Call targets
are normalised ``::`` -> ``.`` so the shared resolver handles them.

Static extraction only — macros, generics-heavy and dynamic constructs are left
unresolved and counted, never guessed.
"""
from __future__ import annotations

import logging
from dataclasses import replace
from typing import Any

from agentedit.model import ImportDecl, ParsedFile, RawEdge, Symbol

logger = logging.getLogger(__name__)

_PARSERS: dict[str, Any] = {}


def _parser() -> Any:
    if "rust" in _PARSERS:
        return _PARSERS["rust"]
    import tree_sitter_rust as rs
    from tree_sitter import Language, Parser

    parser = Parser(Language(rs.language()))
    _PARSERS["rust"] = parser
    return parser


def language_for_path(path: str) -> str | None:
    if path.endswith(".rs"):
        return RustBackend.LANGUAGE
    return None


def module_id(path: str) -> str:
    """Crate-relative dotted module id for a repo-relative rust file."""
    parts = [p for p in path.split("/") if p and p != "."]
    while parts and parts[0] == "..":
        parts.pop(0)
    if parts and parts[0] == "src":
        parts.pop(0)
    if parts:
        stem = parts[-1].rsplit(".", 1)[0]
        if stem in ("mod", "lib", "main"):
            parts.pop()
        else:
            parts[-1] = stem
    return ".".join(parts)


def parse_tree(path: str, source_bytes: bytes) -> Any:
    return _parser().parse(source_bytes).root_node


def _text(node: Any, source: bytes) -> str:
    return source[node.start_byte : node.end_byte].decode("utf-8", errors="replace")


def _line_range(node: Any) -> tuple[int, int]:
    return node.start_point[0] + 1, node.end_point[0] + 1


def parse_file(path: str, source_bytes: bytes) -> ParsedFile:
    root = parse_tree(path, source_bytes)
    module = module_id(path)
    if not module:
        raise ValueError(f"rust crate-root file is not a module: {path}")
    module_name = module.rsplit(".", 1)[-1]
    result = ParsedFile(
        path=path,
        language=RustBackend.LANGUAGE,
        module_qname=module,
        symbols=[
            Symbol(
                qname=module,
                name=module_name,
                kind="module",
                file_path=path,
                line_start=1,
                line_end=max(1, source_bytes.count(b"\n") + 1),
                language=RustBackend.LANGUAGE,
                module_qname=module,
            )
        ],
    )
    for child in root.children:
        nt = child.type
        if nt == "use_declaration":
            _extract_use(child, source_bytes, module, result)
        elif nt == "function_item":
            _emit_function(child, source_bytes, module, result)
        elif nt == "struct_item":
            _emit_named_type(child, source_bytes, module, result, "class")
        elif nt == "trait_item":
            _emit_named_type(child, source_bytes, module, result, "interface")
        elif nt == "enum_item":
            _emit_named_type(child, source_bytes, module, result, "enum")
        elif nt == "type_item":
            _emit_named_type(child, source_bytes, module, result, "type_alias")
        elif nt in ("const_item", "static_item"):
            _emit_const(child, source_bytes, module, result)
        elif nt == "impl_item":
            _emit_impl(child, source_bytes, module, result)
    result.symbols = [replace(s, module_qname=module) for s in result.symbols]
    if root.has_error:
        result.parse_errors.append(f"tree-sitter reported syntax errors in {path}")
    return result


# ---------------------------------------------------------------------------
# Emitters
# ---------------------------------------------------------------------------


def _pub_prefix(node: Any, source: bytes) -> bool:
    name_node = node.child_by_field_name("name")
    end = name_node.start_byte if name_node is not None else node.start_byte + 200
    pre = _text(node, source)[: max(0, end - node.start_byte)]
    return "pub" in [t.strip() for t in pre.replace("\n", " ").split()]


def _emit_function(node: Any, source: bytes, module: str, result: ParsedFile) -> None:
    name_node = node.child_by_field_name("name")
    if name_node is None:
        return
    name = _text(name_node, source)
    qname = f"{module}.{name}"
    params = node.child_by_field_name("parameters")
    ret = node.child_by_field_name("return_type")
    sig_parts = [t for t in (_text(params, source) if params is not None else None,
                             _text(ret, source) if ret is not None else None) if t]
    line_start, line_end = _line_range(node)
    result.symbols.append(
        Symbol(
            qname=qname, name=name, kind="function", file_path=result.path,
            signature=" ".join(sig_parts) if sig_parts else None,
            line_start=line_start, line_end=line_end,
            exported=_pub_prefix(node, source), language=RustBackend.LANGUAGE,
            module_qname=module,
        )
    )
    body = node.child_by_field_name("body")
    if body is not None:
        _collect_call_edges(body, source, qname, result)


def _emit_named_type(node: Any, source: bytes, module: str, result: ParsedFile, kind: str) -> None:
    name_node = node.child_by_field_name("name")
    if name_node is None:
        return
    name = _text(name_node, source)
    line_start, line_end = _line_range(node)
    result.symbols.append(
        Symbol(
            qname=f"{module}.{name}", name=name, kind=kind, file_path=result.path,
            signature=None, line_start=line_start, line_end=line_end,
            exported=_pub_prefix(node, source), language=RustBackend.LANGUAGE,
            module_qname=module,
        )
    )


def _emit_const(node: Any, source: bytes, module: str, result: ParsedFile) -> None:
    name_node = node.child_by_field_name("name")
    if name_node is None:
        return
    name = _text(name_node, source)
    line_start, line_end = _line_range(node)
    result.symbols.append(
        Symbol(
            qname=f"{module}.{name}", name=name, kind="constant", file_path=result.path,
            signature=None, line_start=line_start, line_end=line_end,
            exported=_pub_prefix(node, source), language=RustBackend.LANGUAGE,
            module_qname=module,
        )
    )


def _emit_impl(node: Any, source: bytes, module: str, result: ParsedFile) -> None:
    type_node = node.child_by_field_name("type")
    if type_node is None:
        return
    owner = _text(type_node, source).strip().lstrip("&").split("<")[0].strip()
    if not owner or owner.startswith("impl"):
        return
    decl = next((c for c in node.children if c.type == "declaration_list"), None)
    if decl is None:
        return
    for child in decl.children:
        if child.type == "function_item":
            name_node = child.child_by_field_name("name")
            if name_node is None:
                continue
            name = _text(name_node, source)
            qname = f"{module}.{owner}.{name}"
            params = child.child_by_field_name("parameters")
            ret = child.child_by_field_name("return_type")
            sig_parts = [t for t in (_text(params, source) if params is not None else None,
                                     _text(ret, source) if ret is not None else None) if t]
            line_start, line_end = _line_range(child)
            result.symbols.append(
                Symbol(
                    qname=qname, name=name, kind="method", file_path=result.path,
                    signature=" ".join(sig_parts) if sig_parts else None,
                    line_start=line_start, line_end=line_end,
                    exported=_pub_prefix(child, source), language=RustBackend.LANGUAGE,
                    module_qname=module,
                )
            )
            body = child.child_by_field_name("body")
            if body is not None:
                _collect_call_edges(body, source, qname, result)


def _collect_call_edges(body: Any, source: bytes, owner_qname: str, result: ParsedFile) -> None:
    stack: list[Any] = [body]
    while stack:
        n = stack.pop()
        if n.type == "call_expression":
            func = n.child_by_field_name("function")
            if func is not None:
                target = _text(func, source).replace("::", ".").strip()
                if target and not target.startswith(("self.", "Self::")):
                    result.edges.append(RawEdge(source=owner_qname, target=target, kind="calls"))
        elif n.type == "function_item":
            continue  # nested fn has its own symbol/edges when top-level/impl
        stack.extend(n.children)


# ---------------------------------------------------------------------------
# use declarations
# ---------------------------------------------------------------------------


def _extract_use(node: Any, source: bytes, module: str, result: ParsedFile) -> None:
    text = _text(node, source).replace("\n", " ").strip()
    body = text[len("use") :].strip().rstrip(";").strip()
    alias = ""
    if " as " in body:
        body, alias = body.split(" as ", 1)
        alias = alias.strip()
    path = [seg.strip() for seg in body.replace("{", "::{").split("::") if seg.strip()]
    if path and path[0] in ("crate", "self", "super"):
        path.pop(0)
    if path and path[0] == "super":
        path.pop(0)
    if not path:
        return
    # brace groups and globs are out of scope -> record nothing (honest)
    if any(ch in seg for seg in path for ch in ("{", "}", "*")):
        return
    if len(path) == 1:
        # use crate::x;  -> module namespace import
        local = path[0]
        result.imports.append(ImportDecl(module=module, local=local, imported="*", spec=path[0]))
        return
    imported = path[-1]
    spec = ".".join(path[:-1])
    local = alias or imported
    result.imports.append(ImportDecl(module=module, local=local, imported=imported, spec=spec))


class RustBackend:
    LANGUAGE = "rust"

    language = LANGUAGE

    def language_for_path(self, path: str) -> str | None:
        return language_for_path(path)

    def should_skip(self, path: str) -> bool:
        m = module_id(path)
        return not m

    def module_id(self, path: str) -> str:
        return module_id(path)

    def parse_file(self, path: str, source_bytes: bytes) -> ParsedFile:
        return parse_file(path, source_bytes)

    def spec_candidates(self, spec: str, from_path: str) -> list[str]:
        """Rust use-path module part -> candidate crate-relative module ids."""
        # spec already crate-relative dotted (crate::/self::/super:: stripped);
        # try the dotted form plus parent-segment suffix variants.
        segments = [s for s in spec.split(".") if s]
        candidates = []
        for i in range(len(segments)):
            candidates.append(".".join(segments[i:]))
        return candidates
