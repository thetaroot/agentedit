"""Mutation generator.

Mutations are deterministic source edits that change one exported symbol so the
*code stays valid* but any external dependant no longer type-checks:

    remove-declaration   — delete an exported declaration (callers/importers break)
    rename               — rename the symbol (importers/callers break)
    add-required-param   — append a required param (call sites break)

The evaluator then measures whether AgentEdit's predicted impact set contains
the files that actually break (tsc = oracle).
"""
from __future__ import annotations

from typing import Any

from agentedit.store.sqlite import GraphStore
from eval import go_mutate, java_mutate, py_mutate, rust_mutate, ts_mutate

_REMOVABLE_KINDS = {"function", "constant", "class", "interface", "type_alias", "enum", "method"}
# Kinds a mutation is applicable to (None = any removable kind).
_MUTATION_KINDS: dict[str, set[str] | None] = {
    "remove-declaration": None,
    "rename": None,
    "add-required-param": {"function", "method", "constant"},
}

_SUPPORTED_MUTATIONS = ("remove-declaration", "rename", "add-required-param")


def mutation_candidates(store: GraphStore, *, min_cross: int = 0) -> list[dict[str, Any]]:
    """Exported symbols whose removal is a valid mutation target.

    ``min_cross`` filters to symbols with at least that many *external* static
    dependants (so the eval can focus on recall-relevant cases), default 0 so
    negative controls (symbols nobody references) are included too.
    """
    out: list[dict[str, Any]] = []
    for row in store.all_symbol_rows():
        if row["kind"] not in _REMOVABLE_KINDS:
            continue
        if not row["exported"]:
            continue
        file_module = row["file_path"]
        # external static dependants: usage + import-name edges not in this file
        cross = 0
        for edge in store.edges_to(row["qname"], ("calls", "inherits", "renders", "imports")):
            node = store.get_symbol(edge["source_qname"])
            if node is not None and node["file_path"] != file_module:
                cross += 1
        if cross < min_cross:
            continue
        row["cross"] = cross
        out.append(row)
    return out


def is_applicable(symbol: dict[str, Any], mutation: str) -> bool:
    allowed = _MUTATION_KINDS.get(mutation)
    return allowed is None or symbol["kind"] in allowed


def apply_mutation(source_path: str, symbol: dict[str, Any], mutation: str) -> str | None:
    """Return the mutated source text, or None if not applicable for this file."""
    if mutation not in _SUPPORTED_MUTATIONS:
        raise ValueError(f"unknown mutation {mutation}")
    import types as _types
    mutator: _types.ModuleType
    if source_path.endswith(".py"):
        mutator = py_mutate
    elif source_path.endswith(".go"):
        mutator = go_mutate
    elif source_path.endswith(".rs"):
        mutator = rust_mutate
    elif source_path.endswith(".java"):
        mutator = java_mutate
    else:
        mutator = ts_mutate
    with open(source_path, "rb") as fh:
        source = fh.read()
    result = mutator.mutate(source_path, source, symbol["name"], symbol["line_start"] - 1, mutation)
    return result if isinstance(result, str) else None


