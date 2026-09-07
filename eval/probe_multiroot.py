"""Multi-root python recall probe (audit evidence, A-2026-09).

Reproduces the E2E finding: on a python repo whose sub-app dir (``svc/``) is
the real sys.path root, AgentEdit falls back to ``global`` edges and drops
function-level (lazy) imports entirely.

Run:  python -m eval.probe_multiroot
Prints a dependants recall/precision table per curated target.
After the multi-root + nested-import fix (P0) every row must reach 1.0/1.0.
"""
from __future__ import annotations

import os
import shutil
import sqlite3
import sys
import tempfile
from typing import cast

from agentedit.defaults import default_db
from agentedit.index.indexer import index_repo

FIXTURE = os.path.join(os.path.dirname(__file__), "..", "tests",
                       "fixtures", "eval_py_multiroot")

#: target qname (as indexed today) -> {file -> expected direct dependant}
TRUTH = {
    "svc.services.brain.BrainService": {
        "svc/api.py": True,        # top-level import, instance use
        "web/main.py": True,       # cross-app import, instance use
        "svc/gym.py": False,       # NOT a dependant (only BrainBuilder is)
    },
    "svc.services.brain.BrainBuilder": {
        "svc/gym.py": True,        # lazy/nested import -> today: MISSING (FN)
    },
    "svc.services.brain.standalone": {
        "svc/api.py": True,        # top-level import, direct call
        "web/main.py": False,
        "svc/gym.py": False,
    },
}


def dependant_files(db: str, target: str) -> set[str]:
    c = sqlite3.connect(f"file:{db}?mode=ro", uri=True)
    try:
        rows = c.execute(
            "select distinct s.file_path from edges e "
            "join symbols t on t.qname=e.target_qname "
            "join symbols s on s.qname=e.source_qname "
            "where t.qname=?", (target,)
        ).fetchall()
    finally:
        c.close()
    return {r[0] for r in rows}


def run() -> dict[str, dict[str, object]]:
    work = tempfile.mkdtemp(prefix="im-multiroot-")
    shutil.copytree(os.path.abspath(FIXTURE), os.path.join(work, "repo"),
                    dirs_exist_ok=True)
    repo = os.path.join(work, "repo")
    db = default_db(repo)
    index_repo(repo, db)

    results = {}
    for target, expected in TRUTH.items():
        found = dependant_files(db, target)
        tp = len(found & {f for f, exp in expected.items() if exp})
        fn = sum(1 for f, exp in expected.items() if exp and f not in found)
        fp = len(found - {f for f, exp in expected.items() if exp})
        results[target] = {
            "found": sorted(found), "tp": tp, "fn": fn, "fp": fp,
            "recall": tp / (tp + fn) if tp + fn else 1.0,
            "precision": tp / (tp + fp) if tp + fp else 1.0,
        }
    shutil.rmtree(work, ignore_errors=True)
    return results


def main() -> int:
    res = run()
    print(f"{'target':42s} {'found':30s} TP FN FP recall prec")
    worst = 1.0
    for target, raw in res.items():
        recall = float(cast(float, raw["recall"]))
        precision = float(cast(float, raw["precision"]))
        worst = min(worst, recall, precision)
        found = ", ".join(cast(list[str], raw["found"]))[:30]
        print(f"{target:42s} {found:30s} "
              f"{raw['tp']} {raw['fn']} {raw['fp']}  {recall:.2f}  {precision:.2f}")
    print(f"\nWORST recall/precision: {worst:.2f}")
    return 0 if worst >= 1.0 else 1


if __name__ == "__main__":
    sys.exit(main())
