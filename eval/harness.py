"""Benchmark harness: AgentEdit predictions vs the TypeScript compiler.

For every candidate mutation the harness:

1. copies the corpus to a throwaway project,
2. applies the mutation,
3. type-checks the mutated project (tsc = ground truth),
4. compares tsc's broken-file set with AgentEdit's predicted impact set
   (computed from the *pre-change* index),
5. aggregates precision/recall over all mutations that actually broke a file
   outside the mutated one.
"""
from __future__ import annotations

import json
import os
import tempfile
from typing import Any

from agentedit.index.indexer import index_repo
from agentedit.store.sqlite import GraphStore
from eval import oracle
from eval.mutations import apply_mutation, is_applicable, mutation_candidates


def predicted_files(store: GraphStore, symbol: dict[str, Any], mutation: str) -> set[str]:
    """Files AgentEdit believes a change of ``symbol`` would ripple into.

    remove/rename break *imports* too; signature changes only break call sites.
    """
    kinds: tuple[str, ...]
    if mutation in ("remove-declaration", "rename"):
        kinds = ("calls", "inherits", "renders", "imports")
    else:
        kinds = ("calls", "inherits", "renders")
    pred: set[str] = {symbol["file_path"]}
    for edge in store.edges_to(symbol["qname"], kinds):
        node = store.get_symbol(edge["source_qname"])
        if node is not None:
            pred.add(node["file_path"])
    return pred


def _metrics(symbol: dict[str, Any], oracle_files: list[str], predicted: set[str]) -> dict[str, Any]:
    mutated = symbol["file_path"]
    oracle_out = {f for f in oracle_files if f != mutated}
    pred_out = {f for f in predicted if f != mutated}
    tp = len(oracle_out & pred_out)
    fn = len(oracle_out - pred_out)
    fp = len(pred_out - oracle_out)
    return {
        "symbol": symbol["qname"],
        "kind": symbol["kind"],
        "file": mutated,
        "cross": symbol.get("cross", 0),
        "broken_files": sorted(oracle_out),
        "predicted_files": sorted(pred_out),
        "tp": tp, "fp": fp, "fn": fn,
        "recall": round(tp / (tp + fn), 3) if (tp + fn) else None,
        "precision": round(tp / (tp + fp), 3) if (tp + fp) else None,
    }


def _portable_path(corpus: str) -> str:
    """Store the corpus as repo-relative when it lives under the working tree,
    so committed artifacts carry no absolute machine paths."""
    rel = os.path.relpath(corpus, os.getcwd())
    return rel if not rel.startswith("..") else corpus


def run(
    corpus_dir: str,
    *,
    min_cross: int = 1,
    limit: int = 0,
    out_path: str | None = None,
    mutations: list[str] | None = None,
    oracle_kind: str = "tsc",
) -> dict[str, Any]:
    corpus = os.path.abspath(corpus_dir)
    work = tempfile.mkdtemp(prefix="agentedit-eval-")
    base = os.path.join(work, "base")
    oracle.copy_tree(corpus, base)

    # index the clean corpus
    db = os.path.join(work, "graph.db")
    index_repo(base, db)
    store = GraphStore(db).connect()

    runner = oracle.ensure_oracle(work, oracle_kind)
    oracle.clean_typecheck(base, runner, oracle_kind)

    candidates = mutation_candidates(store, min_cross=min_cross)
    if limit:
        candidates = candidates[:limit]
    mutation_kinds = mutations or ["remove-declaration"]
    not_applicable = 0

    results: list[dict[str, Any]] = []
    case = 0
    for _i, sym in enumerate(candidates, start=1):
        for mutation in mutation_kinds:
            if not is_applicable(sym, mutation):
                not_applicable += 1
                continue
            case += 1
            print(f"  [{case}] {mutation:<20} {sym['qname']}", flush=True)
            mut_dir = os.path.join(work, f"mut-{case}")
            oracle.copy_tree(base, mut_dir)
            src = os.path.join(mut_dir, sym["file_path"])
            mutated_text = apply_mutation(src, sym, mutation)
            if mutated_text is None:
                not_applicable += 1
                print("        (not applicable for file — skipped)", flush=True)
                continue
            with open(src, "w", encoding="utf-8") as fh:
                fh.write(mutated_text)
            broken = oracle.typecheck(mut_dir, runner, oracle_kind)
            pred = predicted_files(store, sym, mutation)
            row = _metrics(sym, broken, pred)
            row["mutation"] = mutation
            results.append(row)

    store.close()

    total_tp = sum(r["tp"] for r in results)
    total_fp = sum(r["fp"] for r in results)
    total_fn = sum(r["fn"] for r in results)
    positive = [r for r in results if r["tp"] + r["fn"] > 0]

    by_kind: dict[str, dict[str, int]] = {}
    for r in results:
        bucket = by_kind.setdefault(r["mutation"], {"tp": 0, "fp": 0, "fn": 0})
        bucket["tp"] += r["tp"]
        bucket["fp"] += r["fp"]
        bucket["fn"] += r["fn"]

    summary = {
        "corpus": _portable_path(corpus),
        "cases": len(results),
        "not_applicable": not_applicable,
        "mutation_types": mutation_kinds,
        "cases_with_breakage": len(positive),
        "aggregate": {
            "precision": round(total_tp / (total_tp + total_fp), 3) if total_tp + total_fp else None,
            "recall": round(total_tp / (total_tp + total_fn), 3) if total_tp + total_fn else None,
            "tp": total_tp, "fp": total_fp, "fn": total_fn,
        },
        "by_mutation": {
            kind: {
                "cases": sum(1 for r in results if r["mutation"] == kind),
                "precision": round(b["tp"] / (b["tp"] + b["fp"]), 3) if b["tp"] + b["fp"] else None,
                "recall": round(b["tp"] / (b["tp"] + b["fn"]), 3) if b["tp"] + b["fn"] else None,
                "tp": b["tp"], "fp": b["fp"], "fn": b["fn"],
            }
            for kind, b in by_kind.items()
        },
        "results": results,
    }
    if out_path:
        os.makedirs(os.path.dirname(os.path.abspath(out_path)) or ".", exist_ok=True)
        with open(out_path, "w", encoding="utf-8") as fh:
            json.dump(summary, fh, indent=2)
        print(f"wrote {out_path}")
    return summary
