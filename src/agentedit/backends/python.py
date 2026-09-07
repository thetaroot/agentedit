# SPDX-License-Identifier: Apache-2.0
# SPDX-FileCopyrightText: 2026 thetaroot

"""Python language backend.

Deterministic extraction of a Python codebase via tree-sitter. Same canonical
model as the TypeScript backend: every symbol carries a module id, imports are
named and resolvable, raw call edges are attributed to their owning symbol.

Module naming is dotted-path based, mirroring Python import semantics:

    src/pkg/core.py        ->  module id "src.pkg.core"
    src/pkg/__init__.py    ->  module id "src.pkg"   (the package itself)

Spec resolution handles absolute dotted imports and relative (``.`` / ``..``)
imports inside packages. This backend is *static*: what cannot be resolved
(``from x import *``, dynamic attribute access) is left unresolved and counted,
never guessed.
"""
from __future__ import annotations

import logging
import posixpath
import re
from dataclasses import replace
from typing import Any

from agentedit.model import Binding, ImportDecl, ParsedFile, RawEdge, Symbol

logger = logging.getLogger(__name__)

_PARSERS: dict[str, Any] = {}

#: Decorator attribute names that register a function as an HTTP/framework
#: route. Such symbols are externally reachable even with zero in-repo callers.
_ROUTE_METHODS = {
    "get", "post", "put", "patch", "delete", "options", "head",
    "route", "add_api_route", "api_route", "websocket", "before_request",
    "on_event", "on_message",
}


def _parser() -> Any:
    if "python" in _PARSERS:
        return _PARSERS["python"]
    import tree_sitter_python as py
    from tree_sitter import Language, Parser

    parser = Parser(Language(py.language()))
    _PARSERS["python"] = parser
    return parser


def language_for_path(path: str) -> str | None:
    if path.endswith(".py"):
        return PythonBackend.LANGUAGE
    return None


def module_id(path: str, offset: str | None = None) -> str:
    """Dotted module id for a repo-relative python path.

    ``offset`` names a leading repo-relative directory that is the *import
    root* (e.g. ``"src"`` for a src-layout repo whose imports reference the
    package *without* the ``src`` prefix). The offset is stripped so module
    ids line up with the way the code imports itself.
    """
    rel = path
    if offset:
        prefix = offset.rstrip("/") + "/"
        if rel.startswith(prefix):
            rel = rel[len(prefix):]
        elif rel == offset.rstrip("/"):
            rel = ""
    no_ext = rel[: -len(".py")] if rel.endswith(".py") else rel
    parts = [p for p in no_ext.split("/") if p and p != "."]
    while parts and parts[0] == "..":
        parts.pop(0)
    if parts and parts[-1] == "__init__":
        parts.pop()
    return ".".join(parts)



def _type_identifiers(node: Any, source: bytes) -> list[str]:
    """Identifiers referenced inside type annotations under ``node``."""
    out: list[str] = []
    stack: list[Any] = [node] if node is not None else []
    while stack:
        cur = stack.pop()
        if cur.type == "identifier":
            name = _text(cur, source).strip()
            if name and name not in out:
                out.append(name)
        stack.extend(cur.children)
    return out


def _emit_uses_type(result: ParsedFile, owner_qname: str, params: Any, returns: Any, source: bytes) -> None:
    names: list[str] = []
    # scan only inside `type` annotation subtrees of the parameters
    if params is not None:
        stack: list[Any] = [params]
        while stack:
            cur = stack.pop()
            if cur.type == "type":
                names.extend(_type_identifiers(cur, source))
            else:
                stack.extend(cur.children)
    if returns is not None:
        names.extend(_type_identifiers(returns, source))
    owner_name = owner_qname.rsplit(".", 1)[-1]
    for name in dict.fromkeys(names):
        if name != owner_name:
            result.edges.append(RawEdge(source=owner_qname, target=name, kind="uses_type"))

def _decorator_flags(node: Any, source: bytes) -> tuple[bool, str | None]:
    """``(has_any_decorator, external_hint)`` for a ``decorated_definition``.

    A decorator marks a framework entry when its callee is a dotted attribute
    whose final name is a route-style registration (``router.post('/x')``,
    ``app.add_api_route(...)``). The matched decorator text becomes the hint so
    an audit can explain *how* the symbol is reachable.
    """
    external_hint: str | None = None
    has_any = False
    for child in node.children:
        if child.type != "decorator":
            continue
        has_any = True
        text = _text(child, source).lstrip("@").strip()
        if not text or external_hint is not None:
            continue
        base = re.split(r"[\(\s]", text, maxsplit=1)[0].split(".")[-1].strip()
        if "." in text.split("(")[0] and base in _ROUTE_METHODS:
            external_hint = text[:200]
    return has_any, external_hint


def _param_annotation_map(node: Any, source: bytes) -> dict[str, str]:
    """Map parameter name -> textual annotation for a function/method node.

    ``(self, *, brain=None, bundle_manager: BundleManager | None = None)``
    yields ``{"bundle_manager": "BundleManager | None", ...}``. Self/cls and
    unannotated parameters are skipped. The raw text is resolved later by the
    resolver (never trusted as a qname here).
    """
    out: dict[str, str] = {}
    params = node.child_by_field_name("parameters")
    if params is None:
        return out
    inner = _text(params, source).strip()
    if not inner.startswith("(") or not inner.endswith(")"):
        return out
    inner = inner[1:-1]
    depth = 0
    tokens: list[str] = []
    current: list[str] = []
    for ch in inner:
        if ch in "([{":
            depth += 1
            current.append(ch)
        elif ch in ")]}":
            depth -= 1
            current.append(ch)
        elif ch == "," and depth == 0:
            tokens.append("".join(current).strip())
            current = []
        else:
            current.append(ch)
    if "".join(current).strip():
        tokens.append("".join(current).strip())
    for token in tokens:
        token = token.strip().lstrip("*")
        if not token or ":" not in token:
            continue
        name, _, ann = token.partition(":")
        name = name.strip()
        ann = ann.strip()
        if name in ("self", "cls") or not name.isidentifier():
            continue
        if "=" in ann:
            ann = ann.split("=", 1)[0].strip()
        if ann:
            out[name] = ann
    return out


def parse_tree(path: str, source_bytes: bytes) -> Any:
    return _parser().parse(source_bytes).root_node


def _text(node: Any, source: bytes) -> str:
    return source[node.start_byte : node.end_byte].decode("utf-8", errors="replace")


def _line_range(node: Any) -> tuple[int, int]:
    return node.start_point[0] + 1, node.end_point[0] + 1


def parse_file(path: str, source_bytes: bytes) -> ParsedFile:
    """Deterministically extract one python file (default: no module offset)."""
    return _parse_file(path, source_bytes, offset=None)


def _parse_file(path: str, source_bytes: bytes, offset: str | None) -> ParsedFile:
    """Deterministically extract one python file."""
    root = parse_tree(path, source_bytes)
    module = module_id(path, offset)
    module_name = module.rsplit(".", 1)[-1] if module else path
    result = ParsedFile(
        path=path,
        language=PythonBackend.LANGUAGE,
        module_qname=module,
        symbols=[
            Symbol(
                qname=module,
                name=module_name,
                kind="module",
                file_path=path,
                line_start=1,
                line_end=max(1, source_bytes.count(b"\n") + 1),
                language=PythonBackend.LANGUAGE,
                module_qname=module,
            )
        ],
    )
    for child in root.children:
        _walk_statement(child, source_bytes, module, result, exported=True)
    _add_safe_nested_imports(root, source_bytes, module, result)
    result.symbols = [replace(s, module_qname=module) for s in result.symbols]
    if root.has_error:
        result.parse_errors.append(f"tree-sitter reported syntax errors in {path}")
    return result


def _walk_statement(node: Any, source: bytes, module: str, result: ParsedFile,
                    exported: bool, decorated: bool = False,
                    external_hint: str | None = None) -> None:
    nt = node.type
    if nt in ("import_statement", "import_from_statement"):
        _extract_imports(node, source, module, result)
        return
    if nt == "decorated_definition":
        # ``@decorator``-wrapped defs/classes: index the inner definition.
        inner = _definition_body(node)
        if inner is not None:
            any_deco, hint = _decorator_flags(node, source)
            _walk_statement(inner, source, module, result, exported,
                            decorated=any_deco, external_hint=hint)
        return
    if nt == "class_definition":
        _emit_class(node, source, module, result, exported,
                    decorated=decorated, external_hint=external_hint)
        return
    if nt == "function_definition":
        _emit_function(node, source, module, result, exported,
                       decorated=decorated, external_hint=external_hint)
        return
    if nt == "expression_statement":
        # module-level assignment -> constant (value symbol)
        _emit_assignment(node, source, module, result, exported)


def _definition_body(node: Any) -> Any | None:
    """Inner ``class_definition``/``function_definition`` of a decorator wrap.

    tree-sitter-python models ``@deco def f`` as a ``decorated_definition``
    holding one or more ``decorator`` nodes plus the real definition. Every
    walker that consumes top-level defs or class methods must unwrap it or
    decorated symbols (FastAPI routes, @classmethod/@property, decorated
    classes) silently vanish from the graph.
    """
    for child in node.children:
        if child.type in ("class_definition", "function_definition"):
            return child
    return None


def _emit_function(node: Any, source: bytes, module: str, result: ParsedFile,
                   exported: bool, decorated: bool = False,
                   external_hint: str | None = None) -> None:
    name_node = node.child_by_field_name("name")
    if name_node is None:
        return
    name = _text(name_node, source)
    qname = f"{module}.{name}"
    params = node.child_by_field_name("parameters")
    returns = node.child_by_field_name("return_type")
    sig = _text(params, source) if params is not None else "()"
    if returns is not None:
        sig += " -> " + _text(returns, source)
    line_start, line_end = _line_range(node)
    result.symbols.append(
        Symbol(
            qname=qname, name=name, kind="function", file_path=result.path,
            signature=sig, line_start=line_start, line_end=line_end,
            exported=exported, language=PythonBackend.LANGUAGE,
            module_qname=module, decorated=decorated,
            external_entry=external_hint is not None, external_hint=external_hint,
        )
    )
    _emit_uses_type(result, qname, params, returns, source)
    _collect_call_edges(node, source, qname, result)
    _collect_bindings(node, source, qname, class_qname=None, result=result)


def _emit_class(node: Any, source: bytes, module: str, result: ParsedFile,
                exported: bool, decorated: bool = False,
                external_hint: str | None = None) -> None:
    name_node = node.child_by_field_name("name")
    if name_node is None:
        return
    name = _text(name_node, source)
    qname = f"{module}.{name}"
    line_start, line_end = _line_range(node)
    result.symbols.append(
        Symbol(
            qname=qname, name=name, kind="class", file_path=result.path,
            signature=None, line_start=line_start, line_end=line_end,
            exported=exported, language=PythonBackend.LANGUAGE,
            module_qname=module, decorated=decorated,
            external_entry=external_hint is not None, external_hint=external_hint,
        )
    )
    # inheritance bases
    superclass = node.child_by_field_name("superclasses")
    if superclass is not None:
        # tree-sitter-python models ``class X(Base, mixin.Base2)`` as an
        # argument_list whose named children are the base expressions.
        for inner in superclass.named_children:
            expr = _dotted(inner, source)
            if expr:
                result.edges.append(RawEdge(source=qname, target=expr, kind="inherits"))
    body = node.child_by_field_name("body")
    if body is None:
        return
    for stmt in body.children:
        inner = stmt if stmt.type != "decorated_definition" else _definition_body(stmt)
        if inner is None:
            continue
        if inner.type != "function_definition":
            continue
        any_deco, hint = _decorator_flags(stmt, source) if stmt.type == "decorated_definition" else (False, None)
        _emit_method(inner, source, module, qname, result,
                     decorated=any_deco, external_hint=hint)


def _emit_method(node: Any, source: bytes, module: str, class_qname: str,
                 result: ParsedFile, decorated: bool = False,
                 external_hint: str | None = None) -> None:
    name_node = node.child_by_field_name("name")
    if name_node is None:
        return
    name = _text(name_node, source)
    qname = f"{class_qname}.{name}"
    params = node.child_by_field_name("parameters")
    sig = _text(params, source) if params is not None else "()"
    line_start, line_end = _line_range(node)
    result.symbols.append(
        Symbol(
            qname=qname, name=name, kind="method", file_path=result.path,
            signature=sig, line_start=line_start, line_end=line_end,
            language=PythonBackend.LANGUAGE, module_qname=module,
            decorated=decorated,
            external_entry=external_hint is not None, external_hint=external_hint,
        )
    )
    _emit_uses_type(result, qname, params, node.child_by_field_name("return_type"), source)
    _collect_call_edges(node, source, qname, result)
    _collect_bindings(node, source, qname, class_qname=class_qname, result=result)


def _emit_assignment(node: Any, source: bytes, module: str, result: ParsedFile, exported: bool) -> None:
    # only single-name, top-level assignments count as constants
    for child in node.children:
        if child.type != "assignment":
            continue
        left = child.child_by_field_name("left")
        if left is None or left.type != "identifier":
            continue
        name = _text(left, source)
        if not name.isidentifier() or name.startswith("_"):
            continue
        line_start, line_end = _line_range(child)
        result.symbols.append(
            Symbol(
                qname=f"{module}.{name}", name=name, kind="constant",
                file_path=result.path, signature=None,
                line_start=line_start, line_end=line_end,
                exported=exported, language=PythonBackend.LANGUAGE,
                module_qname=module,
            )
        )
        break


# ---------------------------------------------------------------------------
# Edges
# ---------------------------------------------------------------------------


def _collect_call_edges(node: Any, source: bytes, owner_qname: str, result: ParsedFile) -> None:
    """Collect call expressions in the function/method body (calls + attributes)."""
    body = None
    for c in node.children:
        if c.type == "block":
            body = c
            break
    if body is None:
        return
    stack = [body]
    while stack:
        n = stack.pop()
        if n.type == "call":
            func = n.child_by_field_name("function")
            if func is not None:
                target = _dotted(func, source)
                if target:
                    result.edges.append(RawEdge(source=owner_qname, target=target, kind="calls"))
        elif n.type == "function_definition":
            continue  # nested defs get their own symbol/edges only if top-level/class
        stack.extend(n.children)


def _collect_bindings(node: Any, source: bytes, scope_qname: str,
                      class_qname: str | None, result: ParsedFile) -> None:
    """Flow-lite bindings for attribute/variable dispatch.

    ``self.<attr> = X`` inside any method binds ``attr`` on the *class*
    (visible to every method of it); a bare local ``x = X`` binds only within
    its own function. We record the written dotted source text (``X``) so the
    resolver can later re-resolve it to a real symbol and chain member calls
    (``self._brain.handle_mcp()``) onto it. Nested function/class bodies are
    their own scopes and are skipped.
    """
    body = None
    for c in node.children:
        if c.type == "block":
            body = c
            break
    if body is None:
        return
    ann_map = _param_annotation_map(node, source)
    stack: list[Any] = [body]
    while stack:
        n = stack.pop()
        if n.type in ("function_definition", "class_definition",
                      "decorated_definition", "lambda"):
            continue
        if n.type == "assignment":
            left = n.child_by_field_name("left")
            right = n.child_by_field_name("right")
            local = _binding_local(left, source)
            target = _binding_target(right, source)
            if local and target and local != target:
                owner = (class_qname or scope_qname) if local.startswith("self.") else scope_qname
                annotation = ann_map.get(target) if target in ann_map else None
                result.bindings.append(
                    Binding(owner=owner, local=local, target=target,
                            annotation=annotation)
                )
            continue
        stack.extend(n.children)


def _binding_local(left: Any, source: bytes) -> str:
    """Left side of an assignment usable as a dispatch name, else ''."""
    if left is None:
        return ""
    if left.type == "identifier":
        return _text(left, source).strip()
    if left.type == "attribute":
        parts: list[str] = []
        current: Any = left
        while current is not None:
            if current.type == "identifier":
                parts.insert(0, _text(current, source).strip())
                break
            if current.type == "attribute":
                attr = current.child_by_field_name("attribute")
                parts.insert(0, _text(attr, source).strip() if attr is not None else "")
                current = current.child_by_field_name("object")
            else:
                break
        local = ".".join(p for p in parts if p)
        if local.startswith("self.") and len(local.split(".")) <= 2:
            return local
    return ""


def _binding_target(right: Any, source: bytes) -> str:
    """Dotted base of the assigned value (constructor class, factory, module
    member); '' when the value carries no useful symbol base."""
    if right is None:
        return ""
    if right.type == "call":
        func = right.child_by_field_name("function")
        return _dotted(func, source) if func is not None else ""
    return _dotted(right, source)


def _dotted(node: Any, source: bytes) -> str:
    """'a.b.c' dotted_name/attribute/identifier chain -> 'a.b.c', or ''.

    ``super()`` / ``type(...)`` roots are normalised to the bare receiver
    token (``super``/``type``) so ``super().run()`` becomes ``super.run`` and
    the resolver can walk the enclosing class's bases.
    """
    if node is None:
        return ""
    if node.type == "dotted_name":
        return _text(node, source).strip()
    if node.type == "call":
        fn = node.child_by_field_name("function")
        if fn is not None and fn.type == "identifier":
            fname = _text(fn, source).strip()
            if fname in ("super", "type"):
                return fname
        return ""
    parts: list[str] = []
    current: Any = node
    while current is not None:
        if current.type == "identifier":
            parts.insert(0, _text(current, source).strip())
            break
        if current.type == "call":
            fn = current.child_by_field_name("function")
            if fn is not None and fn.type == "identifier":
                fname = _text(fn, source).strip()
                if fname in ("super", "type"):
                    parts.insert(0, fname)
                    break
            break
        if current.type == "attribute":
            attr = current.child_by_field_name("attribute")
            obj = current.child_by_field_name("object")
            if attr is not None:
                parts.insert(0, _text(attr, source).strip())
            current = obj
        else:
            break
    return ".".join(p for p in parts if p)


# ---------------------------------------------------------------------------
# Imports
# ---------------------------------------------------------------------------


def _collect_import_decls(node: Any, source: bytes, module: str) -> list[ImportDecl]:
    """Import declarations under ``node`` (any depth), verbatim."""
    found: list[ImportDecl] = []
    stack: list[Any] = list(node.children)
    while stack:
        cur = stack.pop()
        if cur.type in ("import_statement", "import_from_statement"):
            _extract_imports_into(cur, source, module, found)
        else:
            stack.extend(cur.children)
    return found


def _add_safe_nested_imports(root: Any, source: bytes, module: str, result: ParsedFile) -> None:
    """Add function-/class-body imports as file-level name bindings.

    A lazy ``from x import y`` inside a function body still binds ``y``
    statically. We promote it to a file-level binding **only when that is
    unambiguous**: the local name must not already be bound to a *different*
    target by a top-level import (scope shadowing), and every nested binding of
    the same local name must agree. Otherwise the declaration is left out and
    the reference keeps resolving via the honest global-name fallback instead
    of risking a wrong high-confidence edge.
    """
    declared = {i.local for i in result.imports}
    nested = _collect_import_decls(root, source, module)
    by_local: dict[str, set[tuple[str, str]]] = {}
    for imp in nested:
        if imp.local == "*":
            continue
        by_local.setdefault(imp.local, set()).add((imp.spec, imp.imported))
    for local, targets in by_local.items():
        if local in declared:
            continue  # top-level already binds the name -> shadowing ambiguity
        if len(targets) != 1:
            continue  # conflicting nested bindings of the same name
        spec, imported = next(iter(targets))
        result.imports.append(ImportDecl(module=module, local=local, imported=imported, spec=spec))


def _extract_imports(node: Any, source: bytes, module: str, result: ParsedFile) -> None:
    _extract_imports_into(node, source, module, result.imports)


def _extract_imports_into(node: Any, source: bytes, module: str,
                          dest: list[ImportDecl]) -> None:
    children = node.children

    if node.type == "import_statement":
        for c in children:
            if c.type == "aliased_import":
                name_node = c.child_by_field_name("name")
                alias_node = c.child_by_field_name("alias")
                dotted = _text(name_node, source) if name_node is not None else ""
                alias = _text(alias_node, source) if alias_node is not None else dotted.split(".")[0]
                if dotted:
                    dest.append(
                        ImportDecl(module=module, local=alias, imported="*", spec=dotted)
                    )
            elif c.type == "dotted_name":
                dotted = _dotted(c, source)
                if dotted:
                    dest.append(
                        ImportDecl(module=module, local=dotted.split(".")[0], imported="*", spec=dotted)
                    )
        return

    # import_from_statement:  from <module_name> import <names>
    import_index = next((i for i, c in enumerate(children) if c.type == "import"), None)
    head = children[:import_index] if import_index is not None else children
    tail = children[import_index + 1 :] if import_index is not None else []

    rel = 0
    spec = ""
    for c in head:
        if c.type == "relative_import":
            rel += len([d for d in c.children if d.type == "."]) or 1
        elif c.type == "dotted_name":
            spec = _dotted(c, source)
    prefix = "." * rel + spec

    for c in tail:
        if c.type == "aliased_import":
            nn = c.child_by_field_name("name")
            alias = c.child_by_field_name("alias")
            imported = _text(nn, source) if nn is not None else ""
            local = _text(alias, source) if alias is not None else imported
            dest.append(ImportDecl(module=module, local=local, imported=imported, spec=prefix))
        elif c.type == "dotted_name":
            imported = _dotted(c, source)
            if imported:
                dest.append(ImportDecl(module=module, local=imported, imported=imported, spec=prefix))
        elif _text(c, source) == "*":
            dest.append(ImportDecl(module=module, local="*", imported="*", spec=prefix))


class PythonBackend:
    LANGUAGE = "python"

    language = LANGUAGE

    def __init__(self, root_offset: str | None = None) -> None:
        #: repo-relative dir that is the import root (src-layout): stripped from
        #: module ids so they match how the code imports itself (``import pkg.x``).
        self.root_offset = root_offset

    def language_for_path(self, path: str) -> str | None:
        return language_for_path(path)

    def should_skip(self, path: str) -> bool:
        return False

    def module_id(self, path: str) -> str:
        return module_id(path, self.root_offset)

    def parse_file(self, path: str, source_bytes: bytes) -> ParsedFile:
        return _parse_file(path, source_bytes, self.root_offset)

    def spec_candidates(self, spec: str, from_path: str) -> list[str]:
        """Map a python import specifier to candidate module ids.

        ``from_path`` is the importing file (repo-relative); its module id
        provides the package context for relative (``.``/``..``) imports.
        """
        if spec.startswith("."):
            rel = len(spec) - len(spec.lstrip("."))
            tail = spec[rel:].strip(".")
            base_parts = _package_parts(from_path, self.root_offset)
            # relative: go up (rel-1) from the current package
            if rel > 1:
                up = rel - 1
                if len(base_parts) < up:
                    return []
                base_parts = base_parts[: len(base_parts) - up]
            if tail:
                base_parts = [*base_parts, *tail.split(".")]
            return [".".join(base_parts)] if base_parts else []
        if not spec:
            return []
        # absolute dotted module path from the import root
        return [spec]


def _package_parts(from_path: str, offset: str | None = None) -> list[str]:
    """Module-id parts of the *package* containing ``from_path``."""
    module = module_id(from_path, offset)
    parts = module.split(".")
    # module_id already drops a trailing __init__; if the file itself is a
    # module (not a package), the current package is everything but the module.
    basename = posixpath.basename(from_path)
    if basename == "__init__.py":
        return parts
    return parts[:-1] if parts else []
