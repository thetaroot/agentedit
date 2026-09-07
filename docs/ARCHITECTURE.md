# Architecture

AgentEdit is a **deterministic, local code-graph engine**. Nothing in the
extraction or analysis path uses a language model; every answer ships the
structural basis it was computed from.

```
source tree
   │  collect_files (language backend)
   ▼
parse_file (tree-sitter per language) ── symbols + raw edges + unresolved usage
   │
   ▼
index/indexer.py ─ resolve edges against the symbol universe ─ sqlite store
   │                                                    (symbols/edges/files/
   │                                                     unresolved, engine-versioned)
   ▼
analyze: impact / dependents / would_break / changes ─ why-chains, confidence
   ▼
audit.py (flagship one-call brief) · why.py (git rationale)
   ▼
cli.py · mcp_server.py (8 read-only tools) · watch.py (freshness)
```

## Module map (`src/agentedit/`)

| Module | Responsibility |
|---|---|
| `model.py` | Pure data types (`Symbol`, `Affected`, `ImpactReport`). Imports nothing. |
| `backends/` | One per language (`base.py` + python/typescript/go/rust/java). `collect_files`, `parse_file`, module-id from the store, spec_target, framework-decorator marks. |
| `index/indexer.py` | Incremental re-parse, edge resolution against the final symbol universe, engine-versioned stores, unresolved classification into external/in-repo/dynamic. |
| `index/resolver.py` | Edge resolution strategies — imports, global fallback, attribute/`self.` receiver dispatch, inheritance (`inherits`), typed constructor params; every edge records its `method`. |
| `store/sqlite.py` | Thin SQLite persistence. Purely structural since V1.0: tables `meta`, `files`, `symbols`, `edges`, `star_exports`, `unresolved`. Legacy `notes`/`knowledge` tables from pre-1.0 are dropped on connect. |
| `analyze/impact.py` | `dependents`, `impact`, `would_break` with BFS why-chains and confidence; honesty surfaces (framework entry, suspected unresolved name-matches). |
| `analyze/changes.py` | Change surface of the working tree: changed files + symbols elsewhere that depend on edited code. |
| `audit.py` | One-call crash-audit (impact + would_break + affected-file read-set + git rationale + resolution health). `why` extracts deterministic git facts and mined candidates. |
| `graphs.py` | Named graphs: membership over repo indexes (each repo keeps one index reused by many graphs) + declared cross-repo `contracts`. |
| `cli.py` | Repo and graph (`--graph`) commands; human printers. |
| `mcp_server.py` | stdio MCP server, protocol 2024-11-05, eight read-only tools. |
| `watch.py` | Freshness engine: one cycle per repo (or `graph refresh` per member), incremental index, change-surface report. |

## Key rules

- **Dependency direction:** `model` imports nothing; `extract/index/store/analyze`
  depend on `model`; `cli`/`mcp_server` are thin shells. No upward imports.
- **Engine-versioned stores:** when extraction/resolution semantics change, the
  stored `engine_version` no longer matches and the index forces a full re-parse
  (no stale symbols across upgrades).
- **Module identity** is stored per file (`files.module_qname`); analysis never
  re-derives it from paths.
- **Honesty surfaces:** unresolved usage is counted (never dropped) and bucketed
  `external` / `in-repo` / `dynamic`; route-decorated definitions are flagged
  `external_entry` with a hint instead of reported as safe/dead code; when a
  symbol has no resolved dependants but name-matching unresolved references
  exist, they are surfaced as *suspected* (low confidence, not edges).

## Multi-repo

A repo is indexed once; a graph is a thin set of memberships and declared
cross-repo contracts. A query with `--graph` (CLI) or graph mode (MCP) fans out
over every member, answers are repo-tagged, and reach across repositories is
reported only through contracts whose endpoints were both verified to be real
symbols at contract time — never guessed.

## Evals

`eval/` contains mutation-based corpora per language and a dispatch probe.
Oracles are real compilers/type-checkers (tsc, pyright, go, cargo, javac);
impact is measured as recall/precision of predicted breakage. See
[docs/EVALS.md] for reproduction.

[docs/EVALS.md]: EVALS.md
