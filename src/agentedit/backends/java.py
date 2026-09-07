# SPDX-License-Identifier: Apache-2.0
# SPDX-FileCopyrightText: 2026 thetaroot

"""Java language backend.

Deterministic extraction of a Java codebase via tree-sitter. Module identity
is the dotted package directory: ``app/format/MoneyFormatter.java`` ->
module ``app.format`` (files live under directories matching their package,
as in a standard source root). Only ``public`` symbols are cross-package
visible (``exported``).

Imports are single-name class imports (``import app.format.MoneyFormatter;``)
modelled like python items; static method calls of the form
``Class.method(...)`` resolve through the import alias to the method symbol.
Static extraction only — dynamic/reflective constructs are left unresolved and
counted, never guessed.
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
    if "java" in _PARSERS:
        return _PARSERS["java"]
    import tree_sitter_java as jv
    from tree_sitter import Language, Parser

    parser = Parser(Language(jv.language()))
    _PARSERS["java"] = parser
    return parser


def language_for_path(path: str) -> str | None:
    if path.endswith(".java"):
        return JavaBackend.LANGUAGE
    return None


def module_id(path: str) -> str:
    """Dotted package-directory id for a repo-relative java file."""
    parts = [p for p in posixpath.dirname(path).split("/") if p and p != "."]
    while parts and parts[0] == "..":
        parts.pop(0)
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
        raise ValueError(f"java file without a package directory is not indexable: {path}")
    module_name = module.rsplit(".", 1)[-1]
    result = ParsedFile(
        path=path,
        language=JavaBackend.LANGUAGE,
        module_qname=module,
        symbols=[
            Symbol(
                qname=module,
                name=module_name,
                kind="module",
                file_path=path,
                line_start=1,
                line_end=max(1, source_bytes.count(b"\n") + 1),
                language=JavaBackend.LANGUAGE,
                module_qname=module,
            )
        ],
    )
    for child in root.children:
        nt = child.type
        if nt == "import_declaration":
            _extract_import(child, source_bytes, module, result)
        elif nt == "class_declaration":
            _emit_type(child, source_bytes, module, result, "class")
        elif nt == "interface_declaration":
            _emit_type(child, source_bytes, module, result, "interface")
        elif nt == "enum_declaration":
            _emit_type(child, source_bytes, module, result, "enum")
        elif nt == "record_declaration":
            _emit_type(child, source_bytes, module, result, "class")
    result.symbols = [replace(s, module_qname=module) for s in result.symbols]
    if root.has_error:
        result.parse_errors.append(f"tree-sitter reported syntax errors in {path}")
    return result


def _is_public(node: Any, source: bytes) -> bool:
    name_node = node.child_by_field_name("name")
    end = name_node.start_byte if name_node is not None else node.start_byte
    head = _text(node, source)[: max(0, end - node.start_byte)]
    return "public" in head.split()


def _emit_type(node: Any, source: bytes, module: str, result: ParsedFile, kind: str) -> None:
    name_node = node.child_by_field_name("name")
    if name_node is None:
        return
    name = _text(name_node, source)
    qname = f"{module}.{name}"
    line_start, line_end = _line_range(node)
    exported = _is_public(node, source)
    result.symbols.append(
        Symbol(
            qname=qname, name=name, kind=kind, file_path=result.path,
            signature=None, line_start=line_start, line_end=line_end,
            exported=exported, language=JavaBackend.LANGUAGE,
            module_qname=module,
        )
    )
    # inheritance
    superclass = node.child_by_field_name("superclass")
    interfaces = node.child_by_field_name("interfaces")
    for base_node in (superclass, interfaces):
        if base_node is None:
            continue
        for base in _super_type_names(base_node, source):
            if base and base != name:
                result.edges.append(RawEdge(source=qname, target=base, kind="inherits"))
    # methods
    body = node.child_by_field_name("body")
    if body is None:
        return
    for child in body.children:
        if child.type == "method_declaration":
            mname_node = child.child_by_field_name("name")
            if mname_node is None:
                continue
            mname = _text(mname_node, source)
            if mname == name:  # constructor
                continue
            mqname = f"{qname}.{mname}"
            mline_start, mline_end = _line_range(child)
            params = child.child_by_field_name("parameters")
            result.symbols.append(
                Symbol(
                    qname=mqname, name=mname, kind="method", file_path=result.path,
                    signature=_text(params, source) if params is not None else "()",
                    line_start=mline_start, line_end=mline_end,
                    exported=exported or _is_public(child, source),
                    language=JavaBackend.LANGUAGE, module_qname=module,
                )
            )
            body_node = child.child_by_field_name("body")
            if body_node is not None:
                _collect_call_edges(body_node, source, mqname, result)


def _super_type_names(node: Any, source: bytes) -> list[str]:
    out: list[str] = []
    stack: list[Any] = [node]
    while stack:
        cur = stack.pop()
        if cur.type == "type_identifier":
            out.append(_text(cur, source).strip())
        elif cur.type in ("object_creation_expression", "method_invocation"):
            continue
        stack.extend(cur.children)
    return out


def _collect_call_edges(body: Any, source: bytes, owner_qname: str, result: ParsedFile) -> None:
    stack: list[Any] = [body]
    while stack:
        n = stack.pop()
        if n.type == "method_invocation":
            text = _text(n, source)
            paren = text.find("(")
            target = text[:paren].strip() if paren >= 0 else ""
            if target and not target.startswith("this.") and not target.startswith("super."):
                result.edges.append(RawEdge(source=owner_qname, target=target.replace(" ", ""), kind="calls"))
            stack.extend(n.children)
        elif n.type in ("class_declaration", "lambda_expression"):
            continue
        else:
            stack.extend(n.children)


def _extract_import(node: Any, source: bytes, module: str, result: ParsedFile) -> None:
    text = _text(node, source)
    if text.startswith("import static"):
        return  # static imports: names enter the type scope; out of static scope
    body = text[len("import") :].strip().rstrip(";").strip()
    segments = [seg for seg in body.split(".") if seg]
    if len(segments) < 2:
        return
    imported = segments[-1]
    if imported == "*":
        return
    spec = ".".join(segments[:-1])
    result.imports.append(ImportDecl(module=module, local=imported, imported=imported, spec=spec))


class JavaBackend:
    LANGUAGE = "java"

    language = LANGUAGE

    def language_for_path(self, path: str) -> str | None:
        return language_for_path(path)

    def should_skip(self, path: str) -> bool:
        return False

    def module_id(self, path: str) -> str:
        return module_id(path)

    def parse_file(self, path: str, source_bytes: bytes) -> ParsedFile:
        return parse_file(path, source_bytes)

    def spec_candidates(self, spec: str, from_path: str) -> list[str]:
        """Java package path -> candidate module ids (suffix variants)."""
        segments = [s for s in spec.split(".") if s]
        candidates = []
        for i in range(len(segments)):
            candidates.append(".".join(segments[i:]))
        return candidates
