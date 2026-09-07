"""Dispatch / inheritance probe (audit evidence, engine v4).

Covers the E2E critical-miss classes that a single-mutation type-checker eval
cannot express:

* python ``self.x()`` / ``super().x()`` / inherited-``self`` member resolution;
* python typed constructor-parameter collaborator (``self.x = brain: T``);
* python class-inheritance edges (``inherits``) — historically dropped by a
  wrong tree-sitter AST assumption;
* python untyped dynamic collaborators stay *unresolved* and are surfaced as
  suspected references (never fabricated edges);
* python route-decorated defs are marked as framework/plugin entries;
* TypeScript ``this.x()`` + ``extends`` parity for the same features.

Run:  python -m eval.probe_dispatch
Each category must reach recall/precision 1.0/1.0 (exit code 1 otherwise).
"""
from __future__ import annotations

import os
import shutil
import sqlite3
import sys
import tempfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import cast

from agentedit.defaults import default_db
from agentedit.index.indexer import index_repo

ROOT = Path(__file__).resolve().parent.parent
PY_FIXTURE = str(ROOT / "tests" / "fixtures" / "sample_py_v4")
TS_FIXTURE = str(ROOT / "tests" / "fixtures" / "eval_ts_dispatch")


@dataclass
class Expect:
    present: list[tuple[str, str]] = field(default_factory=list)
    absent: list[tuple[str, str]] = field(default_factory=list)
    suspects: list[str] = field(default_factory=list)
    external: list[str] = field(default_factory=list)


PY_TRUTH: dict[str, Expect] = {
    "py-same-class-self": Expect(
        present=[("app.Holder.go", "app.Holder._helper"),
                 ("app.Holder._helper", "app.Holder.more")],
    ),
    "py-typed-ctor": Expect(
        present=[("app.Holder.go", "brain.BrainService.run")],
    ),
    "py-super-inherited": Expect(
        present=[("app.Sub.work", "app.Base.base_run")],
    ),
    "py-inherits-edge": Expect(
        present=[("app.Sub", "app.Base")],
    ),
    "py-no-fabrication": Expect(
        absent=[("app.Holder.probe_untyped",
                 "brain.BrainService.persist_unique")],
        suspects=["persist_unique"],
    ),
    "py-external-entry": Expect(external=["app.create_item"]),
}

TS_TRUTH: dict[str, Expect] = {
    "ts-this": Expect(
        present=[("src.main.Child.work", "src.main.Base.base"),
                 ("src.main.Child.run", "src.main.Child.helper")],
    ),
    "ts-inherits-edge": Expect(present=[("src.main.Child", "src.main.Base")]),
}


def _index(fixture: str) -> tuple[str, str]:
    work = tempfile.mkdtemp(prefix="im-dispatch-")
    shutil.copytree(os.path.abspath(fixture), os.path.join(work, "repo"),
                    dirs_exist_ok=True)
    repo = os.path.join(work, "repo")
    db = default_db(repo)
    index_repo(repo, db)
    return db, work


def _edge_pairs(db: str) -> set[tuple[str, str]]:
    c = sqlite3.connect(f"file:{db}?mode=ro", uri=True)
    try:
        return {(str(r[0]), str(r[1]))
                for r in c.execute("SELECT source_qname, target_qname FROM edges")}
    finally:
        c.close()


def _external(db: str, qname: str) -> bool:
    c = sqlite3.connect(f"file:{db}?mode=ro", uri=True)
    try:
        row = c.execute(
            "SELECT external_entry FROM symbols WHERE qname = ?", (qname,)
        ).fetchone()
        return bool(row[0]) if row else False
    finally:
        c.close()


def _suspect_count(db: str, member: str) -> int:
    c = sqlite3.connect(f"file:{db}?mode=ro", uri=True)
    try:
        row = c.execute(
            "SELECT count(*) FROM unresolved WHERE member = ? AND bucket != 'external'",
            (member,),
        ).fetchone()
        return int(row[0]) if row else 0
    finally:
        c.close()


def _check(db: str, truth: dict[str, Expect]) -> dict[str, dict[str, object]]:
    edges = _edge_pairs(db)
    out: dict[str, dict[str, object]] = {}
    for label, exp in truth.items():
        tp = sum(1 for s, t in exp.present if (s, t) in edges)
        fn = sum(1 for s, t in exp.present if (s, t) not in edges)
        fp = sum(1 for s, t in exp.absent if (s, t) in edges)
        tp += sum(1 for m in exp.suspects if _suspect_count(db, m) > 0)
        fn += sum(1 for m in exp.suspects if _suspect_count(db, m) == 0)
        tp += sum(1 for q in exp.external if _external(db, q))
        fn += sum(1 for q in exp.external if not _external(db, q))
        recall = tp / (tp + fn) if tp + fn else 1.0
        precision = tp / (tp + fp) if tp + fp else 1.0
        out[label] = {"tp": tp, "fp": fp, "fn": fn,
                      "recall": recall, "precision": precision}
    return out


def run() -> dict[str, dict[str, object]]:
    results: dict[str, dict[str, object]] = {}
    py_db, py_work = _index(PY_FIXTURE)
    try:
        results.update(_check(py_db, PY_TRUTH))
    finally:
        shutil.rmtree(py_work, ignore_errors=True)
    ts_db, ts_work = _index(TS_FIXTURE)
    try:
        results.update(_check(ts_db, TS_TRUTH))
    finally:
        shutil.rmtree(ts_work, ignore_errors=True)
    return results


def main() -> int:
    res = run()
    print(f"{'probe':28s} {'TP':>3} {'FP':>3} {'FN':>3} {'recall':>7} {'precision':>9}")
    worst = 1.0
    for label, raw in res.items():
        recall = float(cast(float, raw["recall"]))
        precision = float(cast(float, raw["precision"]))
        worst = min(worst, recall, precision)
        print(f"{label:28s} {raw['tp']:>3} {raw['fp']:>3} {raw['fn']:>3} "
              f"{recall:7.2f} {precision:9.2f}")
    print(f"\nWORST recall/precision: {worst:.2f}")
    return 0 if worst >= 1.0 else 1


if __name__ == "__main__":
    sys.exit(main())
