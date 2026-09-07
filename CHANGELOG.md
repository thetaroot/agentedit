# Changelog

All notable changes to AgentEdit are documented here. Format follows
[Keep a Changelog](https://keepachangelog.com/en/1.0.0/); versioning is SemVer.

## [1.0.0] - 2026-09-06

First public release: **pure, deterministic multi-repo impact analysis** for
coding agents — no mutable knowledge layer, no LLM in the analysis path.

### Added
- Structural code graph per repository (tree-sitter), auto language detection.
- `impact`, `dependents`, `would_break` with confidence-graded, why-chained
  answers; `changes` change-surface; `search`, `symbols_in_file`.
- Flagship `audit`: one-call crash-audit brief (risk, grouped affected files as
  a read-set, git rationale, suspected unresolved references, resolution
  health). `why` for deterministic git rationale + mined candidates.
- **Multi-repo graphs**: named graphs hold any number of repositories; CLI and
  MCP queries fan out per member; cross-repo dependency edges exist only via
  declared, two-sided-verified contracts.
- **MCP stdio server** with eight read-only tools
  (`impact`, `dependents`, `would_break`, `changes`, `search`,
  `symbols_in_file`, `audit`, `why`) for repo and graph mode.
- **Watch** freshness engine and per-graph refresh.
- Five language backends: Python, TypeScript/JavaScript, Go, Rust, Java — each
  with an eval corpus at 1.0/1.0 recall/precision.
- Honest `unresolved` reporting in three buckets (external / in-repo /
  dynamic); framework/plugin entry marking; suspected name-match surfacing.

### Changed
- Distribution name, import module and console command are all **agentedit**.
  Releases are distributed as GitHub Release wheel assets and published to
  PyPI; PyPI is also used for the tree-sitter language dependencies.
- Existing databases from pre-1.0 releases are cleaned in place on first open
  (leftover `notes`/`knowledge` tables are dropped); the engine is structural
  only.

[1.0.0]: https://github.com/thetaroot/agentedit/releases/tag/v1.0.0
