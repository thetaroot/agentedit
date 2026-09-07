# SPDX-License-Identifier: Apache-2.0
# SPDX-FileCopyrightText: 2026 thetaroot

"""Watch / proactive change detection.

The engine's value compounds when the graph is *fresh*. ``watch`` keeps it
fresh: every cycle it incrementally re-indexes the repo (no-op when nothing
changed, ~sha reads) and, when files did change, prints a proactive summary —
the exact change surface an agent should look at:

    [watch] 1 file changed: src/auth.ts
    [watch] affected elsewhere:
              direct: src.controller.AuthController.login   (src/controller.ts)
              file-level importers: src.controller

A true *push* into arbitrary MCP clients is not portable (each client has its
own injection mechanism), so proactive value is delivered as a continuously
fresh graph plus a deterministic, machine-readable summary on every detected
edit — the client/agent reads the surface whenever it acts.
"""
from __future__ import annotations

import os
import time
from typing import Any

from agentedit.analyze.changes import changes
from agentedit.defaults import default_db
from agentedit.index.indexer import index_repo
from agentedit.store.sqlite import GraphStore


def run_once(repo: str, db: str | None = None) -> dict[str, Any]:
    """One detect-and-report cycle. Returns the proactive summary."""
    db = db or default_db(repo)
    summary = index_repo(repo, db)
    changed_files: list[str] = summary.get("changed_files", [])
    affected: list[dict[str, Any]] = []
    store = GraphStore(db).connect()
    try:
        if changed_files:
            report = changes(store, os.path.abspath(repo), changed_files=changed_files)
            affected = report["affected"]
    finally:
        store.close()
    return {
        "repo": summary["repo"],
        "changed_files": changed_files,
        "affected": affected,
        "symbols": summary["symbols"],
        "edges": summary["edges"],
        "reconciled": summary.get("reconciled", 0),
        "unresolved": summary.get("unresolved", 0),
        "duration_s": summary.get("duration_s", 0.0),
    }


def _render(result: dict[str, Any], prefix: str = "[watch] ") -> list[str]:
    lines: list[str] = []
    changed = result["changed_files"]
    affected = result["affected"]
    if not changed:
        lines.append(f"{prefix}no changes (graph fresh: {result['symbols']} symbols, {result['edges']} edges)")
    else:
        lines.append(f"{prefix}{len(changed)} file(s) changed: {', '.join(changed)}")
        direct = [a for a in affected if a["relation"] == "direct"]
        file_level = [a for a in affected if a["relation"] == "file-level"]
        if direct:
            lines.append(f"{prefix}direct dependants elsewhere:")
            for a in direct:
                lines.append(f"{prefix}  {a['qname']}  ({a['file_path']})")
        if file_level:
            lines.append(f"{prefix}file-level importers:")
            for a in file_level:
                lines.append(f"{prefix}  {a['qname']}  ({a['file_path']})")
        if not affected:
            lines.append(f"{prefix}no external dependants detected")
    lines.append(f"{prefix}index: {result['symbols']} symbols, {result['edges']} edges "
                 f"({result['reconciled']} reconciled, {result['unresolved']} unresolved, {result['duration_s']}s)")
    return lines


def watch(repo: str, interval: float = 1.0, *, once: bool = False, db: str | None = None) -> int:
    """Continuously index and report; Ctrl-C stops cleanly (exit 0)."""
    db = db or default_db(repo)
    try:
        while True:
            result = run_once(repo, db)
            for line in _render(result):
                print(line, flush=True)
            if once:
                return 0
            time.sleep(max(0.05, interval))
    except KeyboardInterrupt:
        return 0
