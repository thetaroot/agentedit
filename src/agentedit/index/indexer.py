# SPDX-License-Identifier: Apache-2.0
# SPDX-FileCopyrightText: 2026 thetaroot

"""Repository indexer.

Walks a repo, deterministically extracts every file of the governing language
backend, resolves edges against the full symbol index and persists the result
into the SQLite :class:`GraphStore`. The core knows nothing about a language —
all language behaviour comes from the active
:class:`~agentedit.backends.base.LanguageBackend`.

Incrementality: a file is only re-parsed when its sha256 (or its module id)
differs from the last indexed run. Symbol/edge replacement happens per file
inside a transaction, so a partially-updated graph never becomes visible.
"""
from __future__ import annotations

import hashlib
import logging
import os
import posixpath
import time
from typing import Any

from agentedit.backends import LanguageBackend, backend_for_path, detect_backend
from agentedit.backends.python import PythonBackend
from agentedit.index.resolver import Index, Resolver
from agentedit.model import RawEdge, Symbol
from agentedit.store.sqlite import GraphStore

logger = logging.getLogger(__name__)

_IGNORED_DIRS = {
    ".git", "node_modules", ".venv", "venv", "dist", "build", "out",
    "coverage", ".next", ".cache", "vendor", "target", "__pycache__",
    ".pytest_cache", ".mypy_cache", ".ruff_cache", "public", "assets",
}
_MAX_FILE_BYTES = 2_000_000

#: Runtime-only receiver names: dispatch through them is real in-repo code
#: flow but statically unknowable — counted as ``dynamic``, never as an
#: external package. Covers python (self/cls/...) and typescript (this).
_PY_DYNAMIC_ROOTS = {"self", "cls", "super", "type", "locals", "globals",
                     "vars", "__class__", "this"}

#: Top-level dirs that mark a python repo as src-layout (import root != repo root).
_PY_MODULE_ROOT_DIRS = {"src", "lib", "python"}

#: Extraction-semantics version. Bump when a change can alter how *unchanged*
#: files are parsed (new symbol kinds, decorator handling, bindings, …).
#: Stored in ``meta.engine_version``; a mismatch forces a full re-parse so no
#: stale/missing symbol ever survives an engine upgrade (no-gaps guarantee).
CURRENT_ENGINE_VERSION = 4


def _detect_python_module_root(files: list[str]) -> str | None:
    """If all python files live under one ``src/``/``lib/``-style dir and none at
    the repo root, that dir is the import root and its prefix must be stripped
    from module ids so they match ``import <pkg>...`` statements."""
    tops: dict[str, int] = {}
    for rel in files:
        if rel.endswith(".py") and "/" not in rel:
            return None  # python at repo root -> root is the import root
        if rel.endswith(".py"):
            top = rel.split("/", 1)[0]
            tops[top] = tops.get(top, 0) + 1
    candidates = [d for d in _PY_MODULE_ROOT_DIRS if tops.get(d, 0) > 0]
    if len(candidates) == 1:
        return candidates[0]
    return None


def _sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def collect_files(root: str) -> list[str]:
    """Repo-relative paths of every file any registered backend claims."""
    root_path = os.path.abspath(root)
    found: list[str] = []
    for dirpath, dirnames, filenames in os.walk(root_path):
        dirnames[:] = sorted(
            d for d in dirnames
            if d not in _IGNORED_DIRS
            and not d.startswith(".")
            and not os.path.islink(os.path.join(dirpath, d))
        )
        for fn in filenames:
            full = os.path.join(dirpath, fn)
            if os.path.islink(full):
                continue  # never read content through a symlink (escape guard)
            rel = posixpath.normpath(os.path.relpath(full, root_path))
            backend = backend_for_path(rel)
            if backend is None or backend.should_skip(rel):
                continue
            if os.path.getsize(full) > _MAX_FILE_BYTES:
                continue
            found.append(rel)
    return sorted(found)


def _resolve_star_target(spec: str, from_path: str, backend: LanguageBackend,
                         known: set[str]) -> str | None:
    for candidate in backend.spec_candidates(spec, from_path):
        if candidate in known:
            return candidate
    return None


def _classify_unresolved_root(root: str, module_tops: set[str],
                              symbol_names: set[str]) -> str:
    """Bucket an unresolved call target by its leading token.

    * ``in_repo`` — the root names a repo module/top-level package or a known
      in-repo symbol: a real dependency we failed to link (a gap to close).
    * ``external`` — the root names nothing in this repo (stdlib or a
      third-party package): out of scope, expected noise.
    * ``dynamic`` — python runtime receivers (``self``/``cls``/…): real code
      flow that is statically unknowable.
    Returns ``"in_repo"`` by default for names we cannot place — the honest,
      strict reading that keeps gaps visible.
    """
    if root in _PY_DYNAMIC_ROOTS:
        return "dynamic"
    if root in module_tops or root in symbol_names:
        return "in_repo"
    return "external"


def _unresolved_buckets(
    unresolved_edges: list[RawEdge],
    module_tops: set[str],
    symbol_names: set[str],
) -> dict[str, int]:
    buckets = {"external": 0, "in_repo": 0, "dynamic": 0}
    for edge in unresolved_edges:
        root = edge.target.split(".", 1)[0]
        bucket = _classify_unresolved_root(root, module_tops, symbol_names)
        buckets[bucket] += 1
    return buckets


def index_repo(
    repo_path: str,
    db_path: str,
    *,
    force: bool = False,
    backend: LanguageBackend | None = None,
    module_root: str | None = None,
) -> dict[str, Any]:
    """Index (or incrementally update) ``repo_path`` into ``db_path``.

    ``backend`` is normally auto-detected (dominant language by file count).
    ``module_root`` forces a python import-root offset (repo-relative dir such
    as ``src``); when omitted a src-layout is auto-detected.
    Returns a summary dict with counts and timings.
    """
    start = time.time()
    root = os.path.abspath(repo_path)
    if not os.path.isdir(root):
        raise ValueError(f"not a directory: {repo_path}")

    store = GraphStore(db_path).connect()
    try:
        store.set_meta("repo_root", root)
        files = collect_files(root)
        active = backend or detect_backend(files)
        if active is None:
            summary = {
                "repo": root, "language": None, "files": 0, "changed": 0,
                "removed": 0, "untouched": 0, "reconciled": 0, "unresolved": 0,
                "unresolved_external": 0, "unresolved_in_repo": 0,
                "unresolved_dynamic": 0,
                "symbols": 0, "edges": 0,
                "duration_s": round(time.time() - start, 2),
            }
            return summary

        # src-layout python: strip the import-root prefix from module ids.
        if backend is None and active.language == "python":
            offset = module_root or _detect_python_module_root(files)
            if offset and isinstance(active, PythonBackend) and active.root_offset != offset:
                active = PythonBackend(root_offset=offset)

        # scope to the active backend's files only
        files = [rel for rel in files if active.language_for_path(rel) is not None]
        existing = store.index_files()

        # sha of every file on disk now.
        current_shas: dict[str, str] = {}
        for rel in files:
            with open(os.path.join(root, rel), "rb") as fh:
                current_shas[rel] = _sha256(fh.read())

        # A stored engine_version older than the current extraction semantics
        # forces a full re-parse: unchanged files may have been extracted under
        # a buggy/older rule (e.g. decorated definitions silently missing).
        try:
            stored_engine = int(store.get_meta("engine_version") or "0")
        except ValueError:
            stored_engine = 0
        reparse_all = force or stored_engine != CURRENT_ENGINE_VERSION

        # decide what changed
        changed = [
            rel for rel in files
            if reparse_all
            or existing.get(rel, {}).get("deleted")
            or existing.get(rel, {}).get("sha256") != current_shas[rel]
            or existing.get(rel, {}).get("module_qname") != active.module_id(rel)
        ]
        removed = [
            rel for rel in existing
            if not existing[rel].get("deleted") and rel not in current_shas
        ]
        changed_paths = set(changed)

        # parse changed/new files
        parsed_files = []
        for rel in changed:
            with open(os.path.join(root, rel), "rb") as fh:
                parsed_files.append(active.parse_file(rel, fh.read()))
        parse_error_files = [p.path for p in parsed_files if p.parse_errors]

        # build the resolution index from baseline + overlay
        base_symbols = _load_non_changed_symbols(store, changed)
        overlay = [s for pf in parsed_files for s in pf.symbols]
        index = Index.from_symbols([*base_symbols, *overlay])

        # barrel (export *) chains — baseline from DB, overlay from parsed files
        known_modules = set(index.qname_module.values())
        star_map: dict[str, set[str]] = {}
        for row in store.star_exports_all():
            if row["path"] not in changed_paths:
                star_map.setdefault(active.module_id(row["path"]), set()).add(row["target"])
        star_by_file: dict[str, list[str]] = {}
        for parsed in parsed_files:
            targets: list[str] = []
            for imp in parsed.imports:
                if imp.local == "*" and imp.imported == "*":
                    target = _resolve_star_target(imp.spec, parsed.path, active, known_modules)
                    if target:
                        targets.append(target)
            if targets:
                star_map.setdefault(parsed.module_qname, set()).update(targets)
                star_by_file[parsed.path] = targets

        resolver = Resolver(index, active, star_map)
        untouched = [p for p in files if p not in changed_paths]

        # Class ancestry (parents map) for self./super() member resolution:
        # baseline pairs come from the persisted inherits edges of unchanged
        # files; classes re-parsed this run are re-derived so a changed base
        # never leaves a stale ancestor behind.
        parents: dict[str, list[str]] = {}
        if reparse_all or changed or removed:
            parents_overlay: dict[str, list[str]] = {}
            if parsed_files:
                pre = Resolver(index, active, star_map)
                for parsed in parsed_files:
                    for source, target in pre.resolve_inherits(parsed):
                        if target not in parents_overlay.setdefault(source, []):
                            parents_overlay[source].append(target)
            for source, target in store.inherits_pairs():
                if source in parents_overlay:
                    continue
                if target not in parents.setdefault(source, []):
                    parents[source].append(target)
            for source, targets in parents_overlay.items():
                parents.setdefault(source, []).extend(targets)
        index.parents = parents

        # Classification vocabulary for honest unresolved reporting.
        module_ids = set(index.qname_module.values())
        module_tops = {m.split(".", 1)[0] for m in module_ids}
        symbol_names = set(index.name_unique)

        # persist per file — two phases so edges may reference symbols in files
        # processed later in the same run.
        unresolved_usage_kinds = ("calls", "inherits", "renders")
        unresolved = 0
        unresolved_buckets = {"external": 0, "in_repo": 0, "dynamic": 0}
        for parsed in parsed_files:
            with open(os.path.join(root, parsed.path), "rb") as fh:
                content = fh.read()
            line_count = content.count(b"\n") + 1
            store.replace_symbols(
                path=parsed.path,
                language=active.language,
                module_qname=parsed.module_qname,
                sha256=_sha256(content),
                symbols=list(parsed.symbols),
                line_count=line_count,
            )
            store.replace_star_exports(parsed.path, star_by_file.get(parsed.path, []))
            if parsed.parse_errors:
                logger.warning("parse errors in %s: %s", parsed.path, parsed.parse_errors[0])
        for parsed in parsed_files:
            resolved_edges, resolved_raw = resolver.resolve_file_with_keys(parsed)
            store.replace_edges(parsed.path, resolved_edges)
            unresolved_usage_edges = [
                e for e in parsed.edges
                if e.kind in unresolved_usage_kinds
                and (e.source, e.target, e.kind) not in resolved_raw
            ]
            store.replace_unresolved(parsed.path, [
                {
                    "source_qname": e.source,
                    "target_text": e.target,
                    "kind": e.kind,
                    "bucket": _classify_unresolved_root(
                        e.target.split(".", 1)[0], module_tops, symbol_names),
                    "member": e.target.rsplit(".", 1)[-1] if "." in e.target else e.target,
                }
                for e in unresolved_usage_edges
            ])
            buckets = _unresolved_buckets(unresolved_usage_edges, module_tops, symbol_names)
            for key, value in buckets.items():
                unresolved_buckets[key] += value
            unresolved += len(unresolved_usage_edges)

        for rel in removed:
            store.mark_deleted(rel)
        store.drop_deleted()
        store.set_meta("engine_version", str(CURRENT_ENGINE_VERSION))

        # Reconcile: files that were *not* re-parsed may now resolve differently
        # because the symbol universe changed — an importer of a changed module,
        # or any file with global-fallback edges. Re-resolve them against the
        # final store so no stale edge survives (no-gaps guarantee). Only runs
        # when something actually changed — on a clean idle cycle the universe
        # is identical, so re-resolving would just rewrite identical rows.
        reconciled = 0
        if changed or removed:
            reconciled = _reconcile(store, active, root, changed_paths,
                                    parsed_module_ids={p.module_qname for p in parsed_files},
                                    all_files=set(files))

        result: dict[str, Any] = {
            "repo": root,
            "language": active.language,
            "files": len(files),
            "changed": len(changed),
            "changed_files": sorted(changed),
            "removed": len(removed),
            "untouched": len(untouched),
            "reconciled": reconciled,
            "unresolved": unresolved,
            "unresolved_external": unresolved_buckets["external"],
            "unresolved_in_repo": unresolved_buckets["in_repo"],
            "unresolved_dynamic": unresolved_buckets["dynamic"],
            "parse_error_files": sorted(parse_error_files),
            "edges_by_method": store.edges_by_method(),
            "symbols": store.count_rows("symbols"),
            "edges": store.count_rows("edges"),
            "duration_s": round(time.time() - start, 2),
        }
        return result
    finally:
        store.close()


def _load_non_changed_symbols(store: GraphStore, changed: list[str]) -> list[Symbol]:
    changed_set = set(changed)
    return [
        Symbol(
            qname=r["qname"],
            name=r["name"],
            kind=r["kind"],
            file_path=r["file_path"],
            signature=r["signature"],
            line_start=r["line_start"],
            line_end=r["line_end"],
            exported=bool(r["exported"]),
            module_qname=r["module_qname"],
            decorated=bool(r.get("decorated")),
            external_entry=bool(r.get("external_entry")),
            external_hint=r.get("external_hint"),
        )
        for r in store.all_symbol_rows()
        if r["file_path"] not in changed_set
    ]


def _symbols_from_rows(rows: list[dict[str, Any]]) -> list[Symbol]:
    return [
        Symbol(
            qname=r["qname"],
            name=r["name"],
            kind=r["kind"],
            file_path=r["file_path"],
            signature=r["signature"],
            line_start=r["line_start"],
            line_end=r["line_end"],
            exported=bool(r["exported"]),
            module_qname=r["module_qname"],
            decorated=bool(r.get("decorated")),
            external_entry=bool(r.get("external_entry")),
            external_hint=r.get("external_hint"),
        )
        for r in rows
    ]


def _reconcile(
    store: GraphStore,
    backend: LanguageBackend,
    root: str,
    changed_paths: set[str],
    parsed_module_ids: set[str],
    all_files: set[str],
) -> int:
    """Re-resolve edges of unchanged files whose resolution universe changed.

    Two groups can hold stale edges after an incremental update:

    * **importers of a changed module** — a barrel may have started re-exporting
      a duplicate name, making a previously unambiguous import-name edge
      ambiguous (it must now be dropped, not guessed);
    * **files with global-fallback edges** — a new symbol of the same name may
      have made a "unique" name non-unique (the edge must be dropped) or a new
      import became resolvable.

    Re-resolving against the *final* store closes those gaps.
    """
    candidates: set[str] = set(store.global_edge_files())
    candidates.update(store.module_importer_files(tuple(parsed_module_ids)))
    candidates.update(store.unresolved_files())
    candidates.difference_update(changed_paths)
    candidates.intersection_update(all_files)
    if not candidates:
        return 0

    index = Index.from_symbols(_symbols_from_rows(store.all_symbol_rows()))
    star_map: dict[str, set[str]] = {}
    for row in store.star_exports_all():
        star_map.setdefault(backend.module_id(row["path"]), set()).add(row["target"])
    parents: dict[str, list[str]] = {}
    for source, target in store.inherits_pairs():
        if target not in parents.setdefault(source, []):
            parents[source].append(target)
    index.parents = parents
    resolver = Resolver(index, backend, star_map)
    module_ids = set(index.qname_module.values())
    module_tops = {m.split(".", 1)[0] for m in module_ids}
    symbol_names = set(index.name_unique)
    unresolved_usage_kinds = ("calls", "inherits", "renders")

    for rel in sorted(candidates):
        path = os.path.join(root, rel)
        with open(path, "rb") as fh:
            parsed = backend.parse_file(rel, fh.read())
        resolved_edges, resolved_raw = resolver.resolve_file_with_keys(parsed)
        store.replace_edges(rel, resolved_edges)
        unresolved_usage_edges = [
            e for e in parsed.edges
            if e.kind in unresolved_usage_kinds
            and (e.source, e.target, e.kind) not in resolved_raw
        ]
        store.replace_unresolved(rel, [
            {
                "source_qname": e.source,
                "target_text": e.target,
                "kind": e.kind,
                "bucket": _classify_unresolved_root(
                    e.target.split(".", 1)[0], module_tops, symbol_names),
                "member": e.target.rsplit(".", 1)[-1] if "." in e.target else e.target,
            }
            for e in unresolved_usage_edges
        ])
    return len(candidates)
