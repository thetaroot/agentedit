# SPDX-License-Identifier: Apache-2.0
# SPDX-FileCopyrightText: 2026 thetaroot

"""Edge resolution: raw source references -> concrete symbol qnames.

A ``RawEdge``'s target is the text as written (``helper()``, ``ui.Button``,
``"../lib/auth"``). The resolver turns that text into a qname that exists in
the index, using three increasingly speculative strategies:

1. **scope / same-module** — the identifier resolves inside the enclosing
   scope or another symbol in the same module (deterministic, high confidence).
2. **import binding**        — the identifier is a local import alias/named
   import; we resolve the specifier (via the language backend) and the imported
   name to a symbol there (deterministic, high confidence).
3. **global-unique**         — the bare name exists exactly once repo-wide
   (heuristic, lower confidence; catches barrel-file re-exports).

The resolution *method* is recorded per edge so the impact layer can grade
confidence honestly. Targets that resolve nowhere are dropped (not stored) —
storing noise would poison every downstream traversal.

Nothing in this module knows a specific language: module identity comes from
``Symbol.module_qname`` (never re-derived from paths) and import specifiers are
resolved by the active :class:`~agentedit.backends.base.LanguageBackend`.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field

from agentedit.backends.base import LanguageBackend
from agentedit.model import ImportDecl, ParsedFile, RawEdge, Symbol

#: Recursion ceiling for flow-lite binding expansion (self.x -> x -> …).
_MAX_BINDING_DEPTH = 4
#: Ancestor walk ceiling for class-member lookup (self.x / super().x).
_MAX_CLASS_DEPTH = 10
#: Receiver tokens whose member calls resolve against the enclosing class.
_RECEIVER_ROOTS = {"self", "cls", "this", "type"}


def _annotation_candidates(text: str) -> list[str]:
    """Dotted type-name candidates in an annotation, in source order.

    ``"BundleManager | None"`` -> ``["BundleManager", "None"]``;
    ``"dict[str, BrainService]"`` -> ``["dict", "str", "BrainService"]``.
    The resolver tries each until one resolves; builtins that never resolve are
    skipped naturally, so only a real in-repo type produces a binding.
    """
    out: list[str] = []
    for match in re.finditer(
        r"[A-Za-z_][A-Za-z0-9_]*(?:\.[A-Za-z_][A-Za-z0-9_]*)*", text
    ):
        token = match.group(0)
        if token in ("None", "True", "False"):
            continue
        if token not in out:
            out.append(token)
    return out


@dataclass
class Index:
    """All known symbols of the project, keyed for resolution."""

    all_qnames: set[str] = field(default_factory=set)
    qname_module: dict[str, str] = field(default_factory=dict)              # qname -> module id
    module_symbols: dict[str, dict[str, list[str]]] = field(default_factory=dict)  # module -> name -> [qnames]
    name_unique: dict[str, str] = field(default_factory=dict)               # name -> qname when unique repo-wide
    star_exports: dict[str, set[str]] = field(default_factory=dict)         # module -> star-exported target modules
    parents: dict[str, list[str]] = field(default_factory=dict)             # class qname -> resolved base qnames

    @classmethod
    def from_symbols(cls, symbols: list[Symbol]) -> Index:
        idx = cls()
        counts: dict[str, int] = {}
        for s in symbols:
            module = s.module_qname
            if not module:
                continue
            idx.all_qnames.add(s.qname)
            idx.qname_module[s.qname] = module
            idx.module_symbols.setdefault(module, {}).setdefault(s.name, []).append(s.qname)
            if s.kind == "module":
                continue  # module names never count for uniqueness
            counts[s.name] = counts.get(s.name, 0) + 1
        for name, count in counts.items():
            if count == 1:
                for s in symbols:
                    if s.name == name and s.kind != "module":
                        idx.name_unique[name] = s.qname
                        break
        return idx


class Resolver:
    """Resolves the raw edges of one parsed file against a project :class:`Index`."""

    def __init__(
        self,
        index: Index,
        backend: LanguageBackend,
        star_exports: dict[str, set[str]] | None = None,
    ):
        self._idx = index
        self._backend = backend
        self._star = star_exports or {}

    # -- public ---------------------------------------------------------------

    def resolve_inherits(self, parsed: ParsedFile) -> list[tuple[str, str]]:
        """Resolve only a file's ``inherits`` edges -> (class, base) pairs.

        Used by the indexer to build the class ``parents`` map *before* call
        resolution, so ``self``/``super`` member lookup can walk real bases.
        """
        imports_by_local = {i.local: i for i in parsed.imports}
        out: list[tuple[str, str]] = []
        for edge in parsed.edges:
            if edge.kind != "inherits":
                continue
            hit = self._resolve_call(edge.source, edge, imports_by_local, parsed)
            if hit is not None:
                out.append((edge.source, hit[0]))
        return out

    def resolve_file(self, parsed: ParsedFile) -> list[tuple[str, str, str, str]]:
        """Return fully resolved (source, target, kind, method) edges."""
        edges, _raw = self.resolve_file_with_keys(parsed)
        return edges

    def resolve_file_with_keys(
        self, parsed: ParsedFile,
    ) -> tuple[list[tuple[str, str, str, str]], set[tuple[str, str, str]]]:
        """Resolved edges plus the *raw* keys (source, raw_target, kind) that
        resolved. The raw-key set is what the indexer uses to decide which raw
        usage edges stayed unresolved — comparing against the resolved qname
        would count every call as unresolved."""
        imports_by_local = {i.local: i for i in parsed.imports}
        resolved: list[tuple[str, str, str, str]] = []
        resolved_raw: set[tuple[str, str, str]] = set()

        # 1) module imports + 2) import-name edges
        seen_modules: set[str] = set()
        for imp in parsed.imports:
            if imp.local == "*" and imp.imported == "*":
                continue  # barrel re-exports are tracked separately
            target_mod = self._first_existing(imp.spec, parsed.path)
            if target_mod is None:
                continue
            if target_mod not in seen_modules:
                seen_modules.add(target_mod)
                resolved.append((parsed.module_qname, target_mod, "imports", "static"))
            if imp.imported not in ("*", "default"):
                target = self._named_anywhere(imp.imported, target_mod)
                if target is None:
                    # Re-export through a module we cannot see (python module
                    # attribute re-export, deep barrel): a repo-wide unique name
                    # is the honest last resort (global = lower confidence).
                    target = self._idx.name_unique.get(imp.imported)
                if target is not None:
                    label = "import" if self._idx.qname_module.get(target) == target_mod else "global"
                    resolved.append((parsed.module_qname, target, "imports", label))

        # 3) call / inherits / renders edges.
        for edge in parsed.edges:
            hit = self._resolve_call(edge.source, edge, imports_by_local, parsed)
            if hit is not None:
                target_qname, label = hit
                resolved.append((edge.source, target_qname, edge.kind, label))
                resolved_raw.add((edge.source, edge.target, edge.kind))

        return resolved, resolved_raw

    # -- resolution primitives ------------------------------------------------

    def _first_existing(self, spec: str, from_path: str) -> str | None:
        """First spec candidate that is a known module in the index.

        Falls back to a *unique suffix* match so monorepos whose sub-app dir is
        the real python import root resolve: file ``svc/services/brain.py`` has
        module id ``svc.services.brain`` while the code imports it as
        ``services.brain`` (``svc/`` on ``sys.path``). Suffix resolution only
        fires when exactly one in-repo module matches — with two apps both
        shipping ``services.brain`` it stays unresolved rather than guessing.
        """
        for candidate in self._backend.spec_candidates(spec, from_path):
            if candidate in self._idx.qname_module:
                return candidate
        return self._module_by_suffix(spec, from_path)

    def _module_by_suffix(self, spec: str, from_path: str) -> str | None:
        if not spec or "." not in spec:
            return None
        suffix = f".{spec}"
        modules = sorted(set(self._idx.qname_module.values()))
        candidates = sorted(m for m in modules if m.endswith(suffix))
        if not candidates:
            return None
        if len(candidates) == 1:
            return candidates[0]
        # Several apps expose the same dotted package. Prefer the one sharing
        # the importing file's app prefix; if none is local, stay ambiguous.
        from_module = self._idx.qname_module.get(from_path, "") or ""
        local = [m for m in candidates if from_module.startswith(m.split(".")[0] + ".")]
        if len(local) == 1:
            return local[0]
        return None

    def _resolve_call(
        self,
        source: str,
        edge: RawEdge,
        imports_by_local: dict[str, ImportDecl],
        parsed: ParsedFile,
        depth: int = 0,
    ) -> tuple[str, str] | None:
        """Resolve a single raw edge; returns (target_qname, method_label)."""
        pieces = [p for p in edge.target.split(".") if p]
        if not pieces:
            return None

        # Flow-lite bindings first: ``self._brain.handle_mcp`` / ``b.compute``
        # where ``self._brain``/``b`` were assigned a constructible symbol.
        if depth <= _MAX_BINDING_DEPTH:
            bound = self._resolve_via_binding(source, pieces, imports_by_local, parsed, depth)
            if bound is not None:
                return bound

        if len(pieces) == 1:
            return self._resolve_single(source, pieces[0], imports_by_local, parsed)

        root, rest = pieces[0], pieces[1:]

        # Receiver dispatch: ``self.run()`` / ``cls.run()`` / ``this.run()`` /
        # ``type(self).run()`` resolve against the enclosing class, and
        # ``super().run()`` against its bases. Deterministic member lookup on
        # the class (and its ancestors) — never a guess.
        if root in _RECEIVER_ROOTS and rest:
            class_qname = self._enclosing_class(source)
            if class_qname is not None:
                hit = self._member_on_class(class_qname, rest[0])
                if hit is not None:
                    if len(rest) == 1:
                        return hit, "same"
                    deep = self._deepest_through_class(hit, rest[1:])
                    return (deep if deep is not None else hit), "same"
        if root == "super" and rest:
            class_qname = self._enclosing_class(source)
            if class_qname is not None:
                bases = self._idx.parents.get(class_qname, [])
                hit = self._member_on_classes(bases, rest[0])
                if hit is not None:
                    if len(rest) == 1:
                        return hit, "same"
                    deep = self._deepest_through_class(hit, rest[1:])
                    return (deep if deep is not None else hit), "same"

        # root is an import namespace/named alias -> resolve inside that module.
        imp = imports_by_local.get(root)
        if imp is not None:
            target_mod = self._first_existing(imp.spec, parsed.path)
            if target_mod is not None:
                if imp.imported == "*":
                    hit = self._deepest(target_mod, rest)
                    if hit:
                        return hit, "import"
                elif imp.imported != "default":
                    hit = self._deepest(target_mod, [imp.imported, *rest])
                    if hit:
                        return hit, "import"

        # root resolves to a local symbol (class/object) — member on it.
        local_hit = self._same_scope_or_module(source, root)
        if local_hit:
            hit = self._deepest(local_hit, rest)
            if hit:
                return hit, "same"

        # root resolves globally-unique to a class/object elsewhere.
        global_root = self._idx.name_unique.get(root)
        if global_root:
            hit = self._deepest(global_root, rest)
            if hit:
                return hit, "global"

        return None

    def _resolve_single(
        self,
        source: str,
        name: str,
        imports_by_local: dict[str, ImportDecl],
        parsed: ParsedFile,
    ) -> tuple[str, str] | None:
        scope_hit = self._same_scope_or_module(source, name)
        if scope_hit:
            return scope_hit, "same"

        # Imported binding: {helper} from './x'.
        imp = imports_by_local.get(name)
        if imp is not None:
            target_mod = self._first_existing(imp.spec, parsed.path)
            if target_mod is not None and imp.imported not in ("*", "default"):
                hit = self._named_anywhere(imp.imported, target_mod)
                if hit:
                    return hit, "import"
                gq = self._idx.name_unique.get(imp.imported)
                if gq:
                    return gq, "global"

        # Global-unique fallback (catches barrel re-exports).
        gq = self._idx.name_unique.get(name)
        if gq:
            return gq, "global"
        return None

    # -- helpers --------------------------------------------------------------

    def _resolve_via_binding(
        self,
        source: str,
        pieces: list[str],
        imports_by_local: dict[str, ImportDecl],
        parsed: ParsedFile,
        depth: int,
    ) -> tuple[str, str] | None:
        """Expand ``self.attr`` / local-variable dispatch through an assignment
        binding recorded by the python backend.

        Only fires when the bound source text resolves to a real symbol and the
        member chain after the bound name exists as a symbol — never guessing.
        Emitted edges are labelled ``attr`` (honest, lower confidence: the
        variable may be rebound at runtime).
        """
        if not parsed.bindings:
            return None
        owners = self._binding_owners(source)
        local_map: dict[str, tuple[str, str | None]] = {}
        for b in parsed.bindings:
            if b.owner in owners:
                local_map.setdefault(b.local, (b.target, b.annotation))
        if not local_map:
            return None
        for k in range(len(pieces), 0, -1):
            local = ".".join(pieces[:k])
            entry = local_map.get(local)
            if entry is None:
                continue
            target, annotation = entry
            if not target or target == local:
                continue
            root = self._resolve_binding_root(source, target, imports_by_local, parsed, depth)
            if root is None and annotation:
                for candidate in _annotation_candidates(annotation):
                    root = self._resolve_binding_root(
                        source, candidate, imports_by_local, parsed, depth
                    )
                    if root is not None:
                        break
            if root is None:
                continue
            if k < len(pieces):
                hit = self._deepest_through_class(root, pieces[k:])
                if hit is not None and hit != root:
                    return hit, "attr"
            else:
                return root, "attr"
        return None

    def _resolve_binding_root(
        self,
        source: str,
        target: str,
        imports_by_local: dict[str, ImportDecl],
        parsed: ParsedFile,
        depth: int,
    ) -> str | None:
        if "." in target:
            hit = self._resolve_call(
                source,
                RawEdge(source=source, target=target, kind="calls"),
                imports_by_local,
                parsed,
                depth=depth + 1,
            )
            return hit[0] if hit else None
        hit = self._resolve_single(source, target, imports_by_local, parsed)
        return hit[0] if hit else None

    def _binding_owners(self, source: str) -> list[str]:
        owners = [source]
        if "." in source:
            parent = source.rsplit(".", 1)[0]
            if parent in self._idx.all_qnames and parent not in self._idx.qname_module.values():
                owners.append(parent)
        return owners

    def _enclosing_class(self, source: str) -> str | None:
        """qname of the class that owns ``source`` when it is a method."""
        if "." not in source:
            return None
        parent = source.rsplit(".", 1)[0]
        if parent in self._idx.all_qnames and parent not in self._idx.qname_module.values():
            return parent
        return None

    def _class_chain(self, start: str) -> list[str]:
        """BFS over ``start`` and its resolved ancestors (bounded)."""
        out: list[str] = []
        seen: set[str] = set()
        queue = [start]
        while queue and len(out) < _MAX_CLASS_DEPTH:
            cur = queue.pop(0)
            if cur in seen:
                continue
            seen.add(cur)
            out.append(cur)
            queue.extend(self._idx.parents.get(cur, []))
        return out

    def _member_on_class(self, class_qname: str, name: str) -> str | None:
        for cls in self._class_chain(class_qname):
            candidate = f"{cls}.{name}"
            if candidate in self._idx.all_qnames and candidate != class_qname:
                return candidate
        return None

    def _member_on_classes(self, base_qnames: list[str], name: str) -> str | None:
        for base in base_qnames:
            for cls in self._class_chain(base):
                candidate = f"{cls}.{name}"
                if candidate in self._idx.all_qnames:
                    return candidate
        return None

    def _deepest_through_class(self, base_qname: str, rest: list[str]) -> str | None:
        """Deepest existing symbol on a member chain, walking class ancestors.

        ``_deepest`` only matches literal qnames; for a member reached through a
        class that inherits it, walk each ancestor at every prefix depth.
        """
        chain = self._class_chain(base_qname)
        for depth in range(len(rest), 0, -1):
            suffix = ".".join(rest[:depth])
            for node in chain:
                candidate = f"{node}.{suffix}"
                if candidate in self._idx.all_qnames:
                    return candidate
        return None

    def _same_scope_or_module(self, source: str, name: str) -> str | None:
        source_module = self._idx.qname_module.get(source, "")
        parent = source.rsplit(".", 1)[0] if "." in source else source
        # same enclosing scope (sibling method/class) first
        candidate = f"{parent}.{name}"
        if candidate in self._idx.all_qnames and candidate != source:
            return candidate
        # same module top-level
        if source_module:
            mod_candidates = self._idx.module_symbols.get(source_module, {}).get(name, [])
            if len(mod_candidates) == 1:
                return mod_candidates[0]
            if len(mod_candidates) > 1:
                return None  # ambiguous overload in module
        return None

    def _single_in_module(self, module: str, name: str) -> str | None:
        candidates = self._idx.module_symbols.get(module, {}).get(name, [])
        if len(candidates) == 1:
            return candidates[0]
        return None

    def _named_anywhere(self, name: str, start_module: str) -> str | None:
        """Find ``name`` through `export *` barrel re-export chains.

        A name imported from a barrel (``import { foo } from './index'`` where
        ``index`` does ``export * from './foo'``) lives in the re-exported
        module, not the barrel. We BFS across star edges and resolve to the
        target **only if it is unambiguous** — one hit anywhere in the star
        closure, and no module with duplicate candidates. If two star branches
        both export ``foo`` we return None rather than guess (a wrong edge is
        worse than a dropped one).
        """
        hits: set[str] = set()
        ambiguous = False
        queue = [start_module]
        seen: set[str] = set()
        while queue:
            module = queue.pop(0)
            if module in seen:
                continue
            seen.add(module)
            candidates = self._idx.module_symbols.get(module, {}).get(name, [])
            if len(candidates) == 1:
                hits.add(candidates[0])
            elif len(candidates) > 1:
                ambiguous = True
            queue.extend(sorted(self._star.get(module, ())))
        if hits:
            # prefer the direct symbol; a same-named submodule only counts when
            # the name is *not* otherwise exported by the module.
            if ambiguous or len(hits) != 1:
                return None
            return next(iter(hits))
        # Python: `from pkg import submodule` binds an importable submodule.
        submodule = f"{start_module}.{name}"
        if submodule in self._idx.qname_module:
            return submodule
        return None

    def _deepest(self, base_qname: str, rest: list[str]) -> str | None:
        """Deepest existing symbol on a member chain.

        ``ui.Button`` -> ``<mod>.Button``; ``auth.api.login`` -> the method
        ``login`` if it exists, else the next-existing ancestor symbol.
        """
        for depth in range(len(rest), 0, -1):
            candidate = f"{base_qname}.{'.'.join(rest[:depth])}"
            if candidate in self._idx.all_qnames:
                return candidate
        return None
