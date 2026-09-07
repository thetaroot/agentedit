"""Run the AgentEdit mutation benchmark.

Usage (from repo root, agentedit installed editable):

    pip install -e .
    python -m eval.run_eval --corpus tests/fixtures/eval_ts
    python -m eval.run_eval --mutation rename --mutation add-required-param

Requires node/npm on the first run (installs typescript into a temp tool dir).
"""
from __future__ import annotations

import argparse
import sys
from typing import Any

from eval.harness import run

DEFAULT_MUTATIONS = ["remove-declaration"]


def main() -> int:
    parser = argparse.ArgumentParser(description="AgentEdit mutation benchmark (tsc oracle)")
    parser.add_argument("--corpus", default="tests/fixtures/eval_ts", help="path to a type-checkable TS project")
    parser.add_argument("--min-cross", type=int, default=1, help="min external dependants per candidate")
    parser.add_argument("--limit", type=int, default=0, help="run only the first N candidates (0 = all)")
    parser.add_argument("--out", default="eval/results/latest.json")
    parser.add_argument(
        "--mutation", action="append", dest="mutations", default=None,
        choices=["remove-declaration", "rename", "add-required-param"],
        help="mutation kinds to run (repeatable; default: remove-declaration)",
    )
    parser.add_argument(
        "--oracle", choices=["tsc", "pyright", "go", "rust", "java"], default="tsc",
        help="ground-truth type-checker for the corpus (default: tsc)",
    )
    args = parser.parse_args()

    mutations = args.mutations or DEFAULT_MUTATIONS
    summary: dict[str, Any] = run(
        args.corpus, min_cross=args.min_cross, limit=args.limit, out_path=args.out,
        mutations=mutations, oracle_kind=args.oracle,
    )

    agg: dict[str, Any] = summary["aggregate"]
    print("=" * 72)
    print(f"corpus                 {summary['corpus']}")
    print(f"cases                  {summary['cases']} ({summary['cases_with_breakage']} with real breakage)"
          f" ({summary['not_applicable']} skipped)")
    print(f"mutation types         {', '.join(summary['mutation_types'])}")
    print(f"aggregate precision    {agg['precision']}")
    print(f"aggregate recall       {agg['recall']}")
    print(f"tp / fp / fn           {agg['tp']} / {agg['fp']} / {agg['fn']}")
    for kind, b in summary["by_mutation"].items():
        print(
            f"  {kind:<22} precision={b['precision']} recall={b['recall']} "
            f"tp/fp/fn={b['tp']}/{b['fp']}/{b['fn']} (n={b['cases']})"
        )
    print("=" * 72)
    for r in summary["results"]:
        rec = r["recall"] if r["recall"] is not None else "-"
        prec = r["precision"] if r["precision"] is not None else "-"
        print(f"  {r['mutation']:<20} {r['symbol']:<40} recall={rec} precision={prec} broken={len(r['broken_files'])}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
