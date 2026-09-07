# AGENTS.md — AgentEdit

Working rules for any agent/developer in this repository.

## What this is
A deterministic, local change-intelligence layer for coding agents: structural
code graph (tree-sitter) + impact/audit/why analysis + MCP. Engine core is
language-agnostic; languages are pluggable backends. Supports named graphs of
several repositories (multi-repo).

## How we work (non-negotiable)
- **Quality > language count.** Never claim a capability the tests/eval don't prove.
- **Gates before moving on.** A change ships only when its exit criteria are
  green (ruff, mypy strict, pytest, eval).
- **No bug moves forward.** Every defect found becomes a failing test first,
  then is fixed.
- **Honesty is a feature.** Confidence, `unresolved`, external/suspected
  surfaces and eval numbers are public; never fake certainty the graph doesn't have.
- **No mutable surface for agents.** The structural graph is rebuilt from code
  and stays read-only; there is no knowledge/write layer.

## Commands
```bash
source .venv/bin/activate
pip install -e ".[dev]"

ruff check src tests eval          # lint
# pytest (any invocation; tests/conftest.py puts the repo root on sys.path)
python -m pytest -q
mypy src tests eval                # strict typing (whole repo)
# evals: per-language oracles, each writes its OWN artifact (never overwrite):
python -m eval.run_eval --corpus tests/fixtures/eval_ts --oracle tsc --mutation remove-declaration --mutation rename --mutation add-required-param --out eval/results/ts.json
python -m eval.run_eval --corpus tests/fixtures/eval_py --oracle pyright --mutation remove-declaration --mutation rename --mutation add-required-param --out eval/results/py.json
python -m eval.run_eval --corpus tests/fixtures/eval_go --oracle go --mutation remove-declaration --mutation rename --mutation add-required-param --out eval/results/go.json
python -m eval.run_eval --corpus tests/fixtures/eval_rust --oracle rust --mutation remove-declaration --mutation rename --mutation add-required-param --out eval/results/rust.json
python -m eval.run_eval --corpus tests/fixtures/eval_java --oracle java --mutation remove-declaration --mutation rename --mutation add-required-param --out eval/results/java.json
# note: evals need go/cargo(->RUSTUP_TOOLCHAIN=stable)/javac on PATH and npm for tsc+pyright; not in CI
python -m eval.probe_dispatch   # engine gate: dispatch/inheritance/surfaces (py+TS)
agentedit index <repo>             # CLI: build the graph
agentedit impact <qname> [--repo <r> | --graph <g>]
agentedit audit <qname|file> [--repo <r> | --graph <g>]
agentedit would-break <qname> --change <kind> [--repo <r> | --graph <g>]
agentedit why <qname> [--repo <r> | --graph <g>]
agentedit graph create|add-repo|contract-add|refresh <name>
agentedit mcp [--repo <r> | --graph <g>]
```

## Architecture rules
- **Dependency direction:** `model` imports nothing; `extract/index/store/analyze`
  depend on `model`; `cli`/`mcp_server` are thin shells. No upward imports.
- **No LLM in the extraction path.** Parsing is deterministic.
- **New language = new backend** (see docs/ARCHITECTURE.md): `collect_files`,
  `parse_file`, module-id, `spec_target`. The core
  (`resolver/store/analyze/eval`) stays untouched.
- **analyze never derives module identity from file paths** — it reads
  `files.module_qname` from the store.
- **Edge resolution records its method** (`same`/`import`/`global`) — new
  strategies must degrade confidence, never fake it.
- Cross-repo edges exist only via declared, verified graph contracts; every
  `Affected` ships its basis/evidence (why-chains).

## Provenance
AgentEdit is the standalone open-source extraction of SwiftGate's `skelett`
code-graph engine. Keep attribution current in `NOTICE` and file headers when
code/semantics are carried forward.
