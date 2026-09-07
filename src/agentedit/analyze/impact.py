# SPDX-License-Identifier: Apache-2.0
# SPDX-FileCopyrightText: 2026 thetaroot

"""Impact analysis on top of the persisted graph.

The engine answers the questions that matter *before* a change is made:

* ``dependents`` — who statically references this symbol?
* ``impact``    — dependents + module importers + transitive ripple.
* ``would_break`` — of those, which ones a concrete change (removed / renamed /
  signature / type) would actually break, graded with a confidence.

Confidence is structural honesty, not a guess: an edge that was resolved in the
same file or via an explicit import binding is near-certain; a global-unique
name match is weaker. Every prediction ships its basis so an agent can decide
how much to trust it.

Nothing here knows a language: module identity is read from the store
(``files.module_qname``), never re-derived from paths.
"""
from __future__ import annotations

import re
from typing import Any

from agentedit.model import (
    DEPENDENCY_EDGES,
    Affected,
    ImpactReport,
)
from agentedit.store.sqlite import GraphStore

_METHOD_CONFIDENCE: dict[str, float] = {
    "same": 0.9,
    "import": 0.95,
    "global": 0.6,
    "attr": 0.6,      # flow-lite dispatch: name bound to a real symbol, but
                      # the variable may be reassigned at runtime -> not 1.0
    "static": 0.5,
}
# Edges that break a *compiling* dependant when the target symbol is
# removed/renamed: usage edges plus static import-name references (an
# ``import { x }`` that is never called still fails to compile once ``x`` is
# gone). Signature/type changes only break call sites, not mere imports.
_USAGE_EDGES: tuple[str, ...] = ("calls", "inherits", "renders")
_DEPENDANT_EDGES: tuple[str, ...] = (*_USAGE_EDGES, "imports", "uses_type")


def _affected_from_edge(store: GraphStore, qname: str, edge: dict[str, Any]) -> Affected | None:
    node = store.get_symbol(edge["source_qname"])
    if node is None:
        return None
    conf = _METHOD_CONFIDENCE.get(edge["method"], 0.5)
    kind = edge["kind"]
    if kind == "imports":
        evidence = f"imports {edge['target_qname']} by name ({edge['method']})"
    else:
        evidence = f"{kind} {qname} ({edge['method']} resolution)"
    return Affected(
        qname=edge["source_qname"],
        kind=node["kind"],
        file_path=node["file_path"],
        relation="direct",
        edge_kind=kind,
        confidence=round(conf, 2),
        evidence=evidence,
    )


def _populate_surfaces(store: GraphStore, report: ImpactReport, qname: str) -> None:
    """Surface what a plain dependents query cannot see.

    * A **framework/plugin entry** (route-decorated function) is externally
      reachable even when nothing in the repo calls it — never report that as
      silent "no risk".
    * When no *resolved* dependents exist, name-matching **unresolved**
      references are listed as suspected (low-confidence pointers to check,
      never fabricated edges).
    """
    row = store.get_symbol(qname)
    if row is None:
        return
    if row.get("external_entry"):
        report.external_entry = True
        report.external_hint = row.get("external_hint")
        report.notes.append(
            "framework-registered entry (external surface): reachable via "
            "decorator/registration; zero static in-repo dependents is expected, "
            "not proof of safety"
        )
    if report.direct or report.transitive:
        return
    suspected = store.unresolved_suspected(str(row["name"]))
    if suspected:
        report.suspected = suspected
        report.notes.append(
            f"{len(suspected)} unresolved in-repo/dynamic reference(s) name-match "
            f"this symbol (suspected, low confidence — verify in code, not edges)"
        )
    if (not report.direct and not report.transitive
            and (report.suspected or report.external_entry)
            and report.risk == "none"):
        # Not provably safe: either framework-reachable or name-matched refs exist.
        report.risk = "low"


def _why_chain(root: str, node: str, parent: dict[str, str]) -> list[str]:
    """Root -> node path reconstructed from the BFS parent map."""
    chain: list[str] = [node]
    current = node
    while current != root:
        prev = parent.get(current)
        if prev is None or prev == current:
            break
        chain.append(prev)
        current = prev
    return list(reversed(chain))


def _member_names(text: str) -> set[str]:
    """Approximate set of member names in a type/class body text."""
    return set(re.findall(r"([A-Za-z_$][\w$]*)\s*(?=:\s|\(|\?)", text))


def dependents(store: GraphStore, qname: str, *, max_depth: int = 2) -> ImpactReport:
    """Who depends on ``qname`` — direct + transitive, with why-chains.

    Each :class:`Affected` carries its full ``path`` from the root symbol to
    itself (e.g. ``auth.authenticate -> controller.login -> routes.handle``),
    so an agent sees *why* something is affected, not just that it is.
    """
    report = ImpactReport(root=qname)
    visited: set[str] = set()
    frontier = {qname}
    parent: dict[str, str] = {}

    depth = 0
    while frontier and depth < max_depth:
        next_frontier: set[str] = set()
        for current in sorted(frontier):
            for edge in store.edges_to(current, DEPENDENCY_EDGES):
                source = edge["source_qname"]
                if source in visited or source == qname:
                    continue
                node = store.get_symbol(source)
                if node is None:
                    continue
                conf = _METHOD_CONFIDENCE.get(edge["method"], 0.5)
                relation = "direct" if depth == 0 else "transitive"
                parent.setdefault(source, current)
                chain = _why_chain(qname, source, parent)
                evidence = f"{edge['kind']} {current} ({edge['method']})"
                aff = Affected(
                    qname=source,
                    kind=node["kind"],
                    file_path=node["file_path"],
                    relation=relation,
                    edge_kind=edge["kind"],
                    confidence=round(conf, 2),
                    evidence=evidence,
                    path=chain,
                )
                if relation == "direct":
                    report.direct.append(aff)
                else:
                    report.transitive.append(aff)
                next_frontier.add(source)
        visited.update(frontier)
        frontier = next_frontier - visited
        depth += 1

    report.risk = "high" if report.direct else "none"
    if report.direct:
        report.confidence = round(max(a.confidence for a in report.direct), 2)
    report.notes.append(f"{len(report.direct)} direct, {len(report.transitive)} transitive dependents")
    report.direct.sort(key=lambda a: a.qname)
    report.transitive.sort(key=lambda a: a.qname)
    _populate_surfaces(store, report, qname)
    return report


def impact(store: GraphStore, qname: str) -> ImpactReport:
    """Who depends on a symbol — structural + import-name dependants.

    Import-name edges (a file statically ``import { x }`` of this symbol) are
    direct dependants: removing/renaming the symbol breaks the import even if
    it is never called. Transitive ripple = dependants of the direct set.
    """
    report = dependents(store, qname)
    if store.get_symbol(qname) is None:
        report.notes.append(f"symbol not found: {qname}")
    return report


# ---------------------------------------------------------------------------
# would_break
# ---------------------------------------------------------------------------


def would_break(
    store: GraphStore,
    qname: str,
    *,
    change: str = "removed",
    new_signature: str | None = None,
) -> ImpactReport:
    """Predict breakage for a concrete change to ``qname``.

    removed/renamed/type  — every static dependant breaks, including files that
        only ``import { x }`` the symbol (compile error even when unused).
    signature/return_type — only *call sites* are at risk; a mere import of the
        (still existing) name does not break.
    """
    root = store.get_symbol(qname)
    if root is None:
        return ImpactReport(root=qname, notes=[f"symbol not found: {qname}"])

    report = ImpactReport(root=qname, change=change)
    change = change.lower()

    # Which dependants does this kind of change actually break?
    if change in ("removed", "renamed", "type", "members"):
        kinds = _DEPENDANT_EDGES
    elif change in ("signature", "return_type"):
        kinds = _USAGE_EDGES
    else:
        kinds = _USAGE_EDGES
    for edge in store.edges_to(qname, kinds):
        affected = _affected_from_edge(store, qname, edge)
        if affected is not None:
            report.direct.append(affected)

    # Risk from the change semantics.
    sig_note = ""
    if change == "signature" and new_signature:
        old = root["signature"] or ""
        diff = _diff_signature(old, new_signature)
        required_added = [p for p in diff["added"] if not _is_optional(p)]
        if required_added:
            report.risk = "high"
            sig_note = f"adds required param(s): {', '.join(required_added)}"
        elif diff["added"] or diff["removed"]:
            report.risk = "medium"
            sig_note = "signature change without required-param addition"
        else:
            report.risk = "low"
            sig_note = "signature unchanged"
        report.notes.append(
            f"signature {diff['old_count']}->{diff['new_count']} params "
            f"(+{len(diff['added'])} / -{len(diff['removed'])}); {sig_note}"
        )
    elif change in ("removed", "renamed"):
        report.risk = "high"
        report.notes.append(f"{change} of a referenced symbol breaks every static dependant")
    elif change in ("type", "return_type"):
        report.risk = "medium"
        report.notes.append("type/return change: dependants using the value may need updates")
    elif change == "members" and new_signature is not None:
        old_members = _member_names(root["signature"] or "")
        new_members = _member_names(new_signature)
        removed = sorted(old_members - new_members)
        added = sorted(new_members - old_members)
        if removed:
            report.risk = "high"
            report.notes.append(f"removes member(s): {', '.join(removed)}")
        elif added:
            report.risk = "medium"
            report.notes.append(f"adds member(s): {', '.join(added)}")
        else:
            report.risk = "low"
            report.notes.append("member set unchanged")
        report.notes.append(f"members {len(old_members)} -> {len(new_members)}")
    else:
        report.risk = "high" if report.direct else "none"
        report.notes.append(f"change type '{change}'")

    if report.direct:
        report.confidence = round(max(a.confidence for a in report.direct), 2)

    report.direct.sort(key=lambda a: a.qname)
    report.notes.append(f"{len(report.direct)} direct break-risks")
    _populate_surfaces(store, report, qname)
    return report


# ---------------------------------------------------------------------------
# signature diff (semantics lifted from SwiftGate's skelett.graph_query)
# ---------------------------------------------------------------------------


def _top_level_params(s: str) -> list[str]:
    """Split a parameter list on top-level commas, ignoring nested brackets."""
    inner = _paren_content(s)
    out: list[str] = []
    depth = 0
    current: list[str] = []
    for ch in inner:
        if ch in "([{":
            depth += 1
            current.append(ch)
        elif ch in ")]}":
            depth -= 1
            current.append(ch)
        elif ch == "," and depth == 0:
            out.append("".join(current).strip())
            current = []
        else:
            current.append(ch)
    if current and "".join(current).strip():
        out.append("".join(current).strip())
    return out


def _paren_content(s: str) -> str:
    start = s.find("(")
    if start < 0:
        return ""
    depth = 0
    for i in range(start, len(s)):
        if s[i] == "(":
            depth += 1
        elif s[i] == ")":
            depth -= 1
            if depth == 0:
                return s[start + 1 : i]
    return ""


def _param_name(token: str) -> str:
    token = token.strip()
    if "=" in token:
        token = token.split("=", 1)[0]
    if ":" in token:
        token = token.split(":", 1)[0]
    return token.strip().removeprefix("...").removesuffix("?")


def _is_optional(token: str) -> bool:
    t = token.strip()
    return "?" in t.split(":")[0] or "=" in t.split(":")[0] or t.startswith("...")


def _diff_signature(old: str, new: str) -> dict[str, Any]:
    old_params = [_param_name(t) for t in _top_level_params(old)]
    new_params = [_param_name(t) for t in _top_level_params(new)]
    old_full = _top_level_params(old)
    new_full = _top_level_params(new)
    old_set = {_param_name(t) for t in old_full}
    new_set = {_param_name(t) for t in new_full}
    return {
        "added": [_param_name(t) for t in new_full if _param_name(t) not in old_set],
        "removed": [_param_name(t) for t in old_full if _param_name(t) not in new_set],
        "old_count": len(old_params),
        "new_count": len(new_params),
    }
