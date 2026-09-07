# SPDX-License-Identifier: Apache-2.0
# SPDX-FileCopyrightText: 2026 thetaroot

"""AgentEdit domain model.

Pure dataclasses shared by every layer. Nothing in this module imports from
other layers — it is the leaf of the dependency graph.

This module's symbol/edge vocabulary is a clean-room re-expression of the
model originally built in SwiftGate's ``skelett`` code-graph engine (see
NOTICE). The extraction is deliberately structural: qualified names are
deterministic products of file paths + scope nesting, never of an LLM.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

# ---------------------------------------------------------------------------
# Symbol / edge kinds
# ---------------------------------------------------------------------------

SYMBOL_KINDS = (
    "module",        # one per file; qname is the canonical module id
    "function",
    "method",
    "class",
    "interface",
    "type_alias",
    "enum",
    "constant",
    "component",     # function returning JSX (React/Vue style)
)
# Every language backend must emit only these canonical kinds. Language-specific
# notions (structs/traits/records/...) are normalised onto this vocabulary.

# Edges are stored fully resolved: both endpoints are existing symbol qnames.
EDGE_KINDS = (
    "imports",    # module -> module / module -> *symbol* (name import)
    "calls",      # symbol -> symbol
    "inherits",   # class/interface -> class/interface (extends / implements)
    "renders",    # component -> component (JSX usage)
    "uses_type",  # symbol -> type symbol referenced in an annotation/signature
)
# Kinds we chase when computing "who depends on X".
DEPENDENCY_EDGES = ("calls", "inherits", "renders", "imports", "uses_type")


# ---------------------------------------------------------------------------
# Symbols
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class Symbol:
    """A single named entity in the code graph."""

    qname: str                # globally unique within an index
    name: str                 # local name (last path segment)
    kind: str
    file_path: str            # repo-relative path
    signature: str | None = None
    docstring: str | None = None
    line_start: int = 0
    line_end: int = 0
    exported: bool = False
    language: str = "typescript"
    module_qname: str = ""    # canonical module/namespace id of the containing file
    decorated: bool = False   # any decorator wrapped this definition
    external_entry: bool = False  # route/plugin-style decorator => framework entry
    external_hint: str | None = None  # matched decorator source text, e.g. router.post('/x')


# ---------------------------------------------------------------------------
# Extraction output (raw, pre-resolution)
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class ImportDecl:
    """One import as written in source, before path resolution."""

    module: str               # module qname of the importing file
    local: str                # local binding name (alias or bare identifier)
    imported: str             # imported name; '*' for namespace, 'default' for default
    spec: str                 # raw module specifier as written ("./foo", "react", ...)


@dataclass(frozen=True, slots=True)
class RawEdge:
    """An edge whose target is still a raw source reference.

    Resolution to a concrete ``Symbol.qname`` happens in the indexer, which has
    the full per-module name index and the import map at its disposal.
    """

    source: str               # qname of the enclosing symbol
    target: str               # raw text as written (identifier / member expr)
    kind: str


@dataclass(frozen=True, slots=True)
class Binding:
    """Flow-lite name binding for attribute/variable dispatch (python).

    ``owner`` is the scope that owns the binding: the *class* qname for
    ``self.<attr>`` assignments (visible to every method), or the *function/
    method* qname for a bare local variable. ``local`` is the written left
    side (``self._brain`` or ``client``); ``target`` is the dotted source text
    the name was bound from (``BrainService``). Resolution to a real symbol
    happens in the resolver, so the two are never conflated.
    """

    owner: str
    local: str
    target: str
    annotation: str | None = None  # ctor-param type hint when target is a param


@dataclass(slots=True)
class ParsedFile:
    """Deterministic extraction result for a single file."""

    path: str
    language: str
    module_qname: str
    symbols: list[Symbol] = field(default_factory=list)
    imports: list[ImportDecl] = field(default_factory=list)
    edges: list[RawEdge] = field(default_factory=list)
    bindings: list[Binding] = field(default_factory=list)
    parse_errors: list[str] = field(default_factory=list)


# ---------------------------------------------------------------------------
# Analysis output
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class Affected:
    """One affected symbol in an impact report."""

    qname: str
    kind: str
    file_path: str
    relation: str             # "direct", "transitive"
    edge_kind: str | None     # how we reached it (None for the root)
    confidence: float         # 0..1 structural-certainty
    evidence: str = ""        # human-readable why
    path: list[str] = field(default_factory=list)  # why-chain root -> this symbol


@dataclass(slots=True)
class ImpactReport:
    """Result of an impact / would-break query."""

    root: str | None
    change: str = ""
    direct: list[Affected] = field(default_factory=list)
    transitive: list[Affected] = field(default_factory=list)
    risk: str = "unknown"     # none | low | medium | high
    confidence: float = 0.0
    unresolved: list[str] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)      # analysis notes (why-chains, semantics)
    suspected: list[dict[str, Any]] = field(default_factory=list)  # unresolved refs
    external_entry: bool = False   # root is a framework/plugin entry point
    external_hint: str | None = None

    @property
    def affected_files(self) -> list[str]:
        seen: list[str] = []
        for a in [*self.direct, *self.transitive]:
            if a.file_path not in seen:
                seen.append(a.file_path)
        return seen
