# SPDX-License-Identifier: Apache-2.0
# SPDX-FileCopyrightText: 2026 thetaroot

"""Crash-audit (flagship) and git-rationale (``why``).

An agent is about to edit. Instead of running several single-purpose queries
and starting its own costly audit, it calls ``audit`` once and receives a
deterministic brief:

* what would a change to the target ripple into (impact + would_break),
* grouped affected files with relation + confidence (a cheap read-set),
* suspected unresolved references (never silent "0 risk"),
* git rationale for the code it is about to touch (facts + candidates),
* resolution health of the repo (how much of the answer is import-precise).

``why`` is the same git-rationale as a standalone answer: deterministic facts
(commit/author/date/subject anchored to the symbol's line range) plus candidate
observations mined from those commit messages.
"""
from __future__ import annotations

import os
import re
import subprocess
from typing import Any

from agentedit.analyze.impact import impact, would_break
from agentedit.store.sqlite import GraphStore

_CANDIDATE_HINTS = re.compile(
    r"\b(fix(es|ed)?|break(s|ing|s)?|must|never|always|because|migration|"
    r"renam(e|ed)|remov(e|ed)|deprecat(e|ed)|refactor(ed)?|revert(ed)?|why|"
    r"behaviour change|behavior change|contract)\b",
    re.IGNORECASE,
)


def is_file_target(target: str) -> bool:
    return ("/" in target or target.endswith((".ts", ".tsx", ".js", ".jsx",
                                              ".py", ".go", ".rs", ".java")))


def _git_facts(repo: str, file_path: str, start: int, end: int,
               limit: int = 12) -> list[dict[str, Any]]:
    """Deterministic commit facts touching a line range of a file."""
    if not os.path.isdir(os.path.join(repo, ".git")):
        return []
    try:
        proc = subprocess.run(
            ["git", "-C", repo, "log", "-L", f"{start},{end}:{file_path}",
             "--format=%H%x1f%an%x1f%ad%x1f%s", "--date=short", "-n", str(limit)],
            capture_output=True, text=True, timeout=30,
        )
    except (subprocess.SubprocessError, FileNotFoundError):
        return []
    facts: list[dict[str, Any]] = []
    for raw in proc.stdout.splitlines():
        if not raw or "\x1f" not in raw:
            continue
        parts = raw.split("\x1f")
        if len(parts) < 4:
            continue
        facts.append({"commit": parts[0], "author": parts[1],
                      "date": parts[2], "subject": parts[3]})
    return facts


def _candidates_from_facts(facts: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Candidate observations mined from commit subjects (never confirmed)."""
    out: list[dict[str, Any]] = []
    for f in facts:
        if _CANDIDATE_HINTS.search(f["subject"]):
            out.append({
                "commit": f["commit"],
                "author": f["author"],
                "text": f"{f['subject']} ({f['date']})",
                "status": "candidate",
                "origin": "git",
            })
    return out


def why(store: GraphStore, repo: str, qname: str,
        limit: int = 12) -> dict[str, Any]:
    """Deterministic git rationale for a symbol + in-memory candidates."""
    symbol = store.get_symbol_with_lines(qname)
    if symbol is None:
        return {"symbol": qname, "facts": [], "candidates": [],
                "notes": ["symbol not found"]}
    file_path = str(symbol["file_path"])
    start = int(symbol.get("line_start") or 1)
    end = int(symbol.get("line_end") or start)
    facts = _git_facts(repo, file_path, start, end, limit=limit)
    return {
        "symbol": qname,
        "file": file_path,
        "lines": [start, end],
        "facts": facts,
        "candidates": _candidates_from_facts(facts),
        "git": os.path.isdir(os.path.join(repo, ".git")),
    }


def audit(store: GraphStore, repo: str, target: str,
          *, limit_files: int = 15) -> dict[str, Any]:
    """One-call crash-audit brief for a symbol or a file."""
    if is_file_target(target):
        return _audit_file(store, repo, target, limit_files=limit_files)
    return _audit_symbol(store, repo, target, limit_files=limit_files)


def _audit_symbol(store: GraphStore, repo: str, qname: str, *,
                  limit_files: int) -> dict[str, Any]:
    report = impact(store, qname)
    wb = would_break(store, qname, change="removed")
    files: dict[str, dict[str, Any]] = {}
    for node in [*report.direct, *report.transitive]:
        bucket = files.setdefault(node.file_path, {"relations": [], "conf": 0.0})
        bucket["relations"].append(node.relation)
        bucket["conf"] = max(bucket["conf"], node.confidence)
    file_list = [
        {"file": path, "relations": sorted(set(b["relations"])),
         "confidence": round(b["conf"], 2)}
        for path, b in sorted(files.items())
    ]
    read_set = [f["file"] for f in file_list][:limit_files]
    root_row = store.get_symbol(qname)
    external_entry = bool(root_row is not None and root_row.get("external_entry"))
    if not read_set and report.root and root_row is not None:
        read_set = [str(root_row["file_path"])]
    return {
        "kind": "symbol",
        "target": qname,
        "change": "removed",
        "risk": wb.risk,
        "confidence": wb.confidence,
        "symbol_notes": report.notes,
        "files": file_list,
        "affected_files_count": len(file_list),
        "read_set": read_set,
        "suspected": report.suspected,
        "external_entry": external_entry,
        "external_hint": (root_row.get("external_hint")
                          if root_row is not None else None),
        "why": why(store, repo, qname),
        "resolution": store.edges_by_method(),
        "root_symbol": qname,
    }


def _audit_file(store: GraphStore, repo: str, rel_path: str, *,
                limit_files: int) -> dict[str, Any]:
    rows = store.symbols_in_file(rel_path)
    if not rows:
        return {"kind": "file", "target": rel_path, "notes": ["file not indexed"]}
    qnames = [str(r["qname"]) for r in rows if r["kind"] != "module"]
    if not qnames:
        qnames = [str(r["qname"]) for r in rows]
    all_files: dict[str, dict[str, Any]] = {}
    worst: dict[str, Any] = {"risk": "low", "confidence": 0.0}
    for qname in qnames:
        report = impact(store, qname)
        wb = would_break(store, qname, change="removed")
        if wb.confidence > worst["confidence"]:
            worst = {"risk": wb.risk, "confidence": wb.confidence}
        for node in [*report.direct, *report.transitive]:
            bucket = all_files.setdefault(node.file_path, {"relations": [], "conf": 0.0})
            bucket["relations"].append(node.relation)
            bucket["conf"] = max(bucket["conf"], node.confidence)
    file_list = [
        {"file": path, "relations": sorted(set(b["relations"])),
         "confidence": round(b["conf"], 2)}
        for path, b in sorted(all_files.items())
    ]
    read_set = [f["file"] for f in file_list][:limit_files]
    if not read_set:
        read_set = [rel_path]
    return {
        "kind": "file",
        "target": rel_path,
        "change": "removed",
        "risk": worst["risk"],
        "confidence": round(worst["confidence"], 2),
        "symbols_audited": len(qnames),
        "files": file_list,
        "affected_files_count": len(file_list),
        "read_set": read_set,
        "resolution": store.edges_by_method(),
    }
