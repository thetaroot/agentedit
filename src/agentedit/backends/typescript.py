# SPDX-License-Identifier: Apache-2.0
# SPDX-FileCopyrightText: 2026 thetaroot

"""TypeScript / JavaScript language backend.

Deterministic extraction via tree-sitter. Adapted from the TypeScript parser
originally written for SwiftGate's ``skelett`` code-graph engine (see NOTICE).
It is a *structural* extractor: symbols and edges are produced by walking the
tree-sitter AST, never by an LLM.

Qualified names are file-relative and deterministic:

    src/lib/auth.ts           ->  module id "src.lib.auth"
    class UserService         ->  "src.lib.auth.UserService"
    authenticate(...) method  ->  "src.lib.auth.UserService.authenticate"

Every top-level declaration is prefixed with the module id, which makes qnames
globally unique across the repo and stable across re-parses.
"""
from __future__ import annotations

import logging
import posixpath
from dataclasses import replace
from typing import Any

from agentedit.model import ImportDecl, ParsedFile, RawEdge, Symbol

logger = logging.getLogger(__name__)

_TARGET_EXTENSIONS = (
    "", ".ts", ".tsx", ".js", ".jsx", ".mjs", ".cjs",
    "/index.ts", "/index.tsx", "/index.js", "/index.jsx",
)

_SYMBOLISED_BODY_TYPES = frozenset(
    {"function_declaration", "function_expression", "arrow_function", "method_definition", "class_declaration"}
)

_PARSERS: dict[str, Any] = {}


def _parser(language: str) -> Any:
    if language in _PARSERS:
        return _PARSERS[language]
    from tree_sitter import Language, Parser

    if language == "typescript":
        import tree_sitter_typescript as ts

        parser = Parser(Language(ts.language_typescript()))
    elif language == "tsx":
        import tree_sitter_typescript as ts

        parser = Parser(Language(ts.language_tsx()))
    else:  # javascript
        import tree_sitter_javascript as js

        parser = Parser(Language(js.language()))
    _PARSERS[language] = parser
    return parser


def _grammar_for_path(path: str) -> str:
    """Parser grammar needed for a file: typescript | tsx | javascript."""
    if path.endswith(".ts"):
        return "typescript"
    if path.endswith(".tsx") or path.endswith(".jsx"):
        return "tsx"
    return "javascript"


def language_for_path(path: str) -> str | None:
    """Canonical backend language for ``path`` (None if not a TS/JS file)."""
    if (
        path.endswith(".ts") or path.endswith(".tsx") or path.endswith(".jsx")
        or path.endswith((".js", ".mjs", ".cjs"))
    ):
        return TypeScriptBackend.LANGUAGE
    return None


def module_qname_for_path(path: str) -> str:
    """Turn a repo-relative file path into the module id ('.'/'/' replaced, no ext)."""
    no_ext = path.rsplit(".", 1)[0]
    parts = [p for p in no_ext.split("/") if p and p != "."]
    while parts and parts[0] == "..":
        parts.pop(0)
    return ".".join(parts)


def _text(node: Any, source: bytes) -> str:
    return source[node.start_byte : node.end_byte].decode("utf-8", errors="replace")


def _line_range(node: Any) -> tuple[int, int]:
    return node.start_point[0] + 1, node.end_point[0] + 1


def _span_text(node: Any, source: bytes) -> str:
    return _text(node, source).strip()


def parse_tree(path: str, source_bytes: bytes) -> Any:
    """Parse ``source_bytes`` into a tree-sitter tree root for this file.

    Public so tooling (evaluation mutators, diagnostics) can walk the AST of a
    TS/JS file with the exact same grammar selection as the indexer.
    """
    grammar = _grammar_for_path(path)
    parser = _parser(grammar)
    return parser.parse(source_bytes).root_node



def _type_identifiers(nodes: list[Any], source: bytes) -> list[str]:
    """Collect type_identifier leaves under annotation/type nodes."""
    out: list[str] = []
    stack: list[Any] = list(nodes)
    while stack:
        n = stack.pop()
        if n is None:
            continue
        if n.type == "type_identifier":
            name = _text(n, source).strip()
            if name and name not in out:
                out.append(name)
        stack.extend(n.children)
    return out


def _emit_uses_type(result: ParsedFile, owner_qname: str, scope_nodes: list[Any], source: bytes) -> None:
    for name in _type_identifiers(scope_nodes, source):
        if name != owner_qname.rsplit(".", 1)[-1]:
            result.edges.append(RawEdge(source=owner_qname, target=name, kind="uses_type"))

def parse_file(path: str, source_bytes: bytes) -> ParsedFile:
    """Parse one TS/JS file and return deterministic symbols + raw edges."""
    root = parse_tree(path, source_bytes)

    module_id = module_qname_for_path(path)
    module_name = module_id.rsplit(".", 1)[-1]
    result = ParsedFile(
        path=path,
        language=TypeScriptBackend.LANGUAGE,
        module_qname=module_id,
        symbols=[
            Symbol(
                qname=module_id,
                name=module_name,
                kind="module",
                file_path=path,
                line_start=1,
                line_end=max(1, source_bytes.count(b"\n") + 1),
                language=TypeScriptBackend.LANGUAGE,
                module_qname=module_id,
            )
        ],
    )

    for child in root.children:
        _walk_statement(child, source_bytes, module_id, result, exported=False)

    # Every emitted symbol must carry its module id (core invariant).
    result.symbols = [replace(s, module_qname=module_id) for s in result.symbols]

    if root.has_error:
        result.parse_errors.append(f"tree-sitter reported syntax errors in {path}")

    return result


# ---------------------------------------------------------------------------
# Top-level statement walk
# ---------------------------------------------------------------------------


def _walk_statement(node: Any, source: bytes, module_qname: str, result: ParsedFile, exported: bool) -> None:
    nt = node.type

    if nt == "export_statement":
        inner = node.child_by_field_name("declaration")
        if inner is not None:
            _walk_statement(inner, source, module_qname, result, exported=True)
        _extract_re_exports(node, source, module_qname, result)
        return

    if nt in ("function_declaration", "async_function_declaration", "generator_function_declaration"):
        _emit_function(node, source, module_qname, result, exported)
    elif nt == "class_declaration":
        _emit_class(node, source, module_qname, result, exported)
    elif nt == "interface_declaration":
        _emit_interface(node, source, module_qname, result, exported)
    elif nt == "type_alias_declaration":
        _emit_type_alias(node, source, module_qname, result, exported)
    elif nt == "enum_declaration":
        _emit_enum(node, source, module_qname, result, exported)
    elif nt == "lexical_declaration":
        _emit_lexical(node, source, module_qname, result, exported)
    elif nt == "import_statement":
        _extract_imports(node, source, module_qname, result)


# ---------------------------------------------------------------------------
# Declaration emitters
# ---------------------------------------------------------------------------


def _emit_function(
    node: Any, source: bytes, module_qname: str, result: ParsedFile, exported: bool
) -> None:
    name = _name_of(node, source)
    if not name:
        return
    qname = f"{module_qname}.{name}"
    params = node.child_by_field_name("parameters")
    return_type = node.child_by_field_name("return_type")
    body = node.child_by_field_name("body")
    sig = _signature_of(params, return_type, source)
    line_start, line_end = _line_range(node)

    is_component = _is_react_component(name, body, source) if body is not None else False
    kind = "component" if is_component else "function"
    result.symbols.append(
        Symbol(
            qname=qname, name=name, kind=kind, file_path=result.path,
            signature=sig, line_start=line_start, line_end=line_end,
            exported=exported, language=result.language,
        )
    )
    _emit_uses_type(result, qname, [params, return_type], source)
    if body is not None:
        _collect_call_edges(body, source, qname, result)


def _emit_class(node: Any, source: bytes, module_qname: str, result: ParsedFile, exported: bool) -> None:
    name = _name_of(node, source)
    if not name:
        return
    qname = f"{module_qname}.{name}"
    heritage = _heritage_node(node)
    line_start, line_end = _line_range(node)
    result.symbols.append(
        Symbol(
            qname=qname, name=name, kind="class", file_path=result.path,
            signature=_span_text(heritage, source) if heritage is not None else None,
            line_start=line_start, line_end=line_end,
            exported=exported, language=result.language,
        )
    )
    for base in _heritage_targets(node, source):
        result.edges.append(RawEdge(source=qname, target=base, kind="inherits"))

    body = node.child_by_field_name("body")
    if body is None:
        return
    for child in body.children:
        if child.type == "method_definition":
            _emit_method(child, source, module_qname, qname, result)


def _emit_method(
    node: Any, source: bytes, module_qname: str, class_qname: str, result: ParsedFile
) -> None:
    name_node = node.child_by_field_name("name")
    if name_node is None:
        return
    name = _text(name_node, source)
    qname = f"{class_qname}.{name}"
    params = node.child_by_field_name("parameters")
    return_type = node.child_by_field_name("return_type")
    body = node.child_by_field_name("body")
    line_start, line_end = _line_range(node)
    result.symbols.append(
        Symbol(
            qname=qname, name=name, kind="method", file_path=result.path,
            signature=_signature_of(params, return_type, source),
            line_start=line_start, line_end=line_end,
            language=result.language,
        )
    )
    _emit_uses_type(result, qname, [params, return_type], source)
    if body is not None:
        _collect_call_edges(body, source, qname, result)


def _emit_interface(node: Any, source: bytes, module_qname: str, result: ParsedFile, exported: bool) -> None:
    name = _name_of(node, source)
    if not name:
        return
    qname = f"{module_qname}.{name}"
    body = node.child_by_field_name("body")
    line_start, line_end = _line_range(node)
    result.symbols.append(
        Symbol(
            qname=qname, name=name, kind="interface", file_path=result.path,
            signature=_span_text(body, source) if body is not None else None,
            line_start=line_start, line_end=line_end,
            exported=exported, language=result.language,
        )
    )
    for base in _heritage_targets(node, source):
        result.edges.append(RawEdge(source=qname, target=base, kind="inherits"))


def _emit_type_alias(node: Any, source: bytes, module_qname: str, result: ParsedFile, exported: bool) -> None:
    name = _name_of(node, source)
    if not name:
        return
    qname = f"{module_qname}.{name}"
    value = node.child_by_field_name("value")
    line_start, line_end = _line_range(node)
    result.symbols.append(
        Symbol(
            qname=qname, name=name, kind="type_alias", file_path=result.path,
            signature=_span_text(value, source) if value is not None else None,
            line_start=line_start, line_end=line_end,
            exported=exported, language=result.language,
        )
    )


def _emit_enum(node: Any, source: bytes, module_qname: str, result: ParsedFile, exported: bool) -> None:
    name = _name_of(node, source)
    if not name:
        return
    qname = f"{module_qname}.{name}"
    line_start, line_end = _line_range(node)
    result.symbols.append(
        Symbol(
            qname=qname, name=name, kind="enum", file_path=result.path,
            signature=None, line_start=line_start, line_end=line_end,
            exported=exported, language=result.language,
        )
    )


def _emit_lexical(node: Any, source: bytes, module_qname: str, result: ParsedFile, exported: bool) -> None:
    for declarator in node.children:
        if declarator.type != "variable_declarator":
            continue
        name_node = declarator.child_by_field_name("name")
        value = declarator.child_by_field_name("value")
        if name_node is None or value is None or name_node.type != "identifier":
            continue
        name = _text(name_node, source)
        qname = f"{module_qname}.{name}"
        line_start, line_end = _line_range(declarator)

        if value.type in ("arrow_function", "function_expression"):
            params = value.child_by_field_name("parameters")
            return_type = value.child_by_field_name("return_type")
            body = value.child_by_field_name("body")
            sig = _signature_of(params, return_type, source)
            is_component = _is_react_component(name, body, source) if body is not None else False
            kind = "component" if is_component else "function"
            result.symbols.append(
                Symbol(
                    qname=qname, name=name, kind=kind, file_path=result.path,
                    signature=sig, line_start=line_start, line_end=line_end,
                    exported=exported, language=result.language,
                )
            )
            _emit_uses_type(result, qname, [params, return_type], source)
            if body is not None:
                _collect_call_edges(body, source, qname, result)
        else:
            # Module-level const that isn't a function — still a first-class
            # symbol so that removing/renaming it surfaces as impact.
            result.symbols.append(
                Symbol(
                    qname=qname, name=name, kind="constant", file_path=result.path,
                    signature=None, line_start=line_start, line_end=line_end,
                    exported=exported, language=result.language,
                )
            )


# ---------------------------------------------------------------------------
# Edges from callable bodies
# ---------------------------------------------------------------------------


def _collect_call_edges(body: Any, source: bytes, owner_qname: str, result: ParsedFile) -> None:
    """Find call expressions + JSX component usages inside ``body``.

    Nested symbolised entities (methods, classes, other const functions) are
    emitted separately and their bodies are skipped here so that edges are
    attributed to the innermost known symbol.
    """
    stack = [body]
    while stack:
        n = stack.pop()
        nt = n.type
        if nt == "call_expression":
            func = n.child_by_field_name("function")
            if func is not None and func.type in ("identifier", "member_expression", "property_identifier"):
                target = _text(func, source)
                result.edges.append(RawEdge(source=owner_qname, target=target, kind="calls"))
        elif nt == "jsx_self_closing_element" or nt == "jsx_opening_element":
            tag = n.child_by_field_name("name")
            if tag is not None:
                tag_text = _text(tag, source)
                if tag_text and tag_text[0].isupper():
                    result.edges.append(RawEdge(source=owner_qname, target=tag_text, kind="renders"))
        if nt in _SYMBOLISED_BODY_TYPES:
            continue
        stack.extend(n.children)


# ---------------------------------------------------------------------------
# Imports / re-exports
# ---------------------------------------------------------------------------


def _extract_imports(node: Any, source: bytes, module_qname: str, result: ParsedFile) -> None:
    source_node = node.child_by_field_name("source")
    if source_node is None:
        return
    spec = _text(source_node, source).strip("\"'`")

    clause = next((c for c in node.children if c.type == "import_clause"), None)
    if clause is None:
        return  # bare side-effect import: no local bindings

    for sub in clause.children:
        if sub.type == "identifier":
            result.imports.append(
                ImportDecl(module=module_qname, local=_text(sub, source), imported="default", spec=spec)
            )
        elif sub.type == "named_imports":
            for spec_node in sub.children:
                if spec_node.type != "import_specifier":
                    continue
                imported_node = spec_node.child_by_field_name("name")
                alias_node = spec_node.child_by_field_name("alias")
                if imported_node is None:
                    continue
                imported = _text(imported_node, source)
                local = _text(alias_node, source) if alias_node is not None else imported
                result.imports.append(
                    ImportDecl(module=module_qname, local=local, imported=imported, spec=spec)
                )
        elif sub.type == "namespace_import":
            ns = sub.child_by_field_name("name")
            if ns is not None:
                result.imports.append(
                    ImportDecl(module=module_qname, local=_text(ns, source), imported="*", spec=spec)
                )


def _extract_re_exports(node: Any, source: bytes, module_qname: str, result: ParsedFile) -> None:
    """export {x} from './y' / export * from './y' — model as imports from y.

    Named clauses produce a real local binding; ``export *`` produces a marker
    ImportDecl (local='*', imported='*') used by the resolver to chase names
    through barrel files.
    """
    source_node = node.child_by_field_name("source")
    if source_node is None:
        return
    spec = _text(source_node, source).strip("\"'`")
    clause = next((c for c in node.children if c.type == "export_clause"), None)
    if clause is None:
        # export * from '...' (or export * as ns from '...')
        result.imports.append(
            ImportDecl(module=module_qname, local="*", imported="*", spec=spec)
        )
        return
    for spec_node in clause.children:
        if spec_node.type != "export_specifier":
            continue
        name_node = spec_node.child_by_field_name("name")
        alias_node = spec_node.child_by_field_name("alias")
        if name_node is None:
            continue
        imported = _text(name_node, source)
        local = _text(alias_node, source) if alias_node is not None else imported
        result.imports.append(ImportDecl(module=module_qname, local=local, imported=imported, spec=spec))


# ---------------------------------------------------------------------------
# Shared helpers
# ---------------------------------------------------------------------------


def _name_of(node: Any, source: bytes) -> str | None:
    name_node = node.child_by_field_name("name")
    if name_node is None:
        return None
    return _text(name_node, source)


def _signature_of(params: Any, return_type: Any, source: bytes) -> str | None:
    sig = _span_text(params, source) if params is not None else "()"
    if return_type is not None:
        sig += " " + _span_text(return_type, source)
    return sig or None


def _heritage_node(node: Any) -> Any | None:
    """Class/interface heritage node.

    Grammar versions differ: some expose a ``class_heritage``/``heritage``
    *field*, others only a child node of that type. Scan both so `extends`
    bases are never silently dropped.
    """
    for field in ("class_heritage", "heritage"):
        found = node.child_by_field_name(field)
        if found is not None:
            return found
    for child in node.named_children:
        if child.type in ("class_heritage", "heritage"):
            return child
    return None


def _heritage_targets(node: Any, source: bytes) -> list[str]:
    heritage = _heritage_node(node)
    if heritage is None:
        return []
    targets: list[str] = []

    def collect(sub: Any) -> None:
        if sub.type in ("identifier", "type_identifier",
                        "member_expression", "nested_type_identifier"):
            targets.append(_text(sub, source))
        elif sub.type == "type_arguments":  # extends Foo<Bar>
            type_node = sub.child_by_field_name("type")
            if type_node is not None:
                collect(type_node)

    has_wrappers = any(
        c.type in ("extends_clause", "implements_clause")
        for c in heritage.children
    )
    if has_wrappers:
        for child in heritage.children:
            if child.type not in ("extends_clause", "implements_clause"):
                continue
            for sub in child.children:
                collect(sub)
    else:
        for sub in heritage.children:
            collect(sub)
    return targets


def _is_react_component(name: str, body: Any, source: bytes) -> bool:
    if not name or not name[:1].isupper():
        return False
    stack = [body]
    while stack:
        n = stack.pop()
        if n.type in ("jsx_element", "jsx_self_closing_element", "jsx_fragment"):
            return True
        stack.extend(n.children)
    return False


class TypeScriptBackend:
    """LanguageBackend for TypeScript / JavaScript / TSX / JSX.

    One backend owns the TS family: the parser picks the grammar per file, but
    the canonical language id, module naming and spec resolution are shared.
    """

    LANGUAGE = "typescript"

    language = LANGUAGE

    def language_for_path(self, path: str) -> str | None:  # noqa: F811
        return language_for_path(path)

    def should_skip(self, path: str) -> bool:
        return path.endswith(".d.ts")

    def module_id(self, path: str) -> str:
        return module_qname_for_path(path)

    def parse_file(self, path: str, source_bytes: bytes) -> ParsedFile:  # noqa: F811
        return parse_file(path, source_bytes)

    def spec_candidates(self, spec: str, from_path: str) -> list[str]:
        """Map a TS/JS import specifier to candidate module ids.

        Handles relative specifiers (``./x``, ``../y/z`` with extension/index
        resolution) and the naive ``@/`` root alias. Bare specifiers
        (node_modules) and everything else resolve to no candidates.
        """
        if spec.startswith("@") and "/" in spec:
            root_candidates = []
            base = spec[1:]  # strip '@'
            for ext in _TARGET_EXTENSIONS:
                root_candidates.append(module_qname_for_path(posixpath.normpath(base + ext)))
            return root_candidates
        if not spec.startswith("."):
            return []
        base_dir = posixpath.dirname(from_path)
        return [
            module_qname_for_path(posixpath.normpath(posixpath.join(base_dir, spec + ext)))
            for ext in _TARGET_EXTENSIONS
        ]
