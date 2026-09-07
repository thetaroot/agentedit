# SPDX-License-Identifier: Apache-2.0
# SPDX-FileCopyrightText: 2026 thetaroot

"""Working-tree change surface.

Given the set of files touched in the working tree (vs HEAD), report which
symbols in *unchanged* files depend on symbols defined in the changed files —
i.e. what the current edit might ripple into. Operates on the last indexed
(pre-change) graph, which is exactly the baseline an agent edits against.

Language behaviour comes exclusively from the backend registry: which files to
consider and how to name an (as yet unindexed) file's module.
"""
from __future__ import annotations

import os
from typing import Any

from agentedit.backends import backend_for_path
from agentedit.store.sqlite import GraphStore
from agentedit.workspace import git

# Usage edges + static import-name edges: a symbol referenced by name is a
# compile-level dependant whether or not it is called.
_DEPENDANT_EDGES: tuple[str, ...] = ("calls", "inherits", "renders", "imports")


def changes(
    store: GraphStore,
    repo: str,
    changed_files: list[str] | None = None,
) -> dict[str, Any]:
    files = changed_files or git.changed_files(repo)
    files = [f for f in files if _supported(f)]
    changed_set = set(files)
    indexed = store.index_files()

    affected: list[dict[str, Any]] = []
    affected_files: list[str] = []
    seen: set[tuple[str, str]] = set()

    for path in changed_set:
        # 1) dependants (by usage or by name-import) of exported symbols in the
        #    changed file — unless the dependant is itself in the change set.
        for sym in store.symbols_in_file(path):
            if not sym["exported"]:
                continue
            for edge in store.edges_to(sym["qname"], _DEPENDANT_EDGES):
                dep = store.get_symbol(edge["source_qname"])
                if dep is None or dep["file_path"] in changed_set:
                    continue
                relation = "file-level" if edge["kind"] == "imports" else "direct"
                _add(affected, seen, affected_files,
                     qname=dep["qname"], kind=dep["kind"], file_path=dep["file_path"],
                     relation=relation, edge_kind=edge["kind"])
        # 2) whole-file dependants: only when the file was *deleted* does every
        #    module importer break (they import a module that no longer exists).
        if not os.path.isfile(os.path.join(repo, path)):
            module = indexed[path]["module_qname"] if path in indexed else None
            if module is None:
                backend = backend_for_path(path)
                module = backend.module_id(path) if backend else None
            if module:
                for edge in store.edges_to(module, ("imports",)):
                    dep = store.get_symbol(edge["source_qname"])
                    if dep is None or dep["file_path"] in changed_set:
                        continue
                    _add(affected, seen, affected_files,
                         qname=dep["qname"], kind=dep["kind"], file_path=dep["file_path"],
                         relation="file-level", edge_kind="imports")

    affected.sort(key=lambda a: (a["qname"], a["relation"]))
    return {
        "changed_files": files,
        "affected": affected,
        "affected_files": affected_files,
    }


def _supported(path: str) -> bool:
    backend = backend_for_path(path)
    if backend is None:
        return False
    return not backend.should_skip(path)


def _add(
    affected: list[dict[str, Any]],
    seen: set[tuple[str, str]],
    affected_files: list[str],
    *,
    qname: str,
    kind: str,
    file_path: str,
    relation: str,
    edge_kind: str,
) -> None:
    key = (relation, qname)
    if key in seen:
        return
    seen.add(key)
    affected.append({
        "qname": qname,
        "kind": kind,
        "file_path": file_path,
        "relation": relation,
        "edge_kind": edge_kind,
    })
    if file_path not in affected_files:
        affected_files.append(file_path)
