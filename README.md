# AgentEdit

```bash
pipx install "https://github.com/thetaroot/agentedit/releases/download/v1.0.0/agentedit-1.0.0-py3-none-any.whl"
# or: pip install "git+https://github.com/thetaroot/agentedit@v1.0.0"
```

Deterministic, local impact analysis for coding agents: **know what your code
change will break before you make it.**

```bash
agentedit index .                          # build the local code graph
agentedit impact src.auth.authenticate     # who depends on it?
agentedit audit src.auth.authenticate      # one-call crash-audit before an edit
```

## What it does

- **Impact & crash-audit** — `impact`, `dependents`, `would_break`, `audit`
  with confidence-graded answers, why-chains, git rationale and a minimal
  read-set. Deterministic; no LLM in the analysis path.
- **Multi-repo** — one local *graph* holds many repositories; cross-repo
  dependencies exist only via declared, verified contracts, never guessed.
- **5 languages** — Python, TypeScript/JavaScript, Go, Rust, Java; each with a
  mutation eval corpus at 1.0/1.0 recall/precision ([docs/EVALS.md]).
- **Honest** — every edge records how it was resolved; unresolved usage is
  counted and surfaced as *suspected*, never silently assumed safe.
- **MCP server** — eight read-only tools (`impact`, `dependents`,
  `would_break`, `changes`, `search`, `symbols_in_file`, `audit`, `why`) for
  repo and graph mode. `agentedit mcp --repo <path>` or `--graph <name>`.

Details: [docs/ARCHITECTURE.md] · [docs/EVALS.md] · [CONTRIBUTING.md]

## License

Apache-2.0. AgentEdit is the standalone open-source extraction of SwiftGate's
`skelett` code-graph engine — see [NOTICE] and [THIRD_PARTY.md].

We build and use **SwiftGate** — if you find this open-source project helpful,
check it out at [swiftgateai.de](https://swiftgateai.de).

[docs/EVALS.md]: docs/EVALS.md
[docs/ARCHITECTURE.md]: docs/ARCHITECTURE.md
[CONTRIBUTING.md]: CONTRIBUTING.md
[NOTICE]: NOTICE
[THIRD_PARTY.md]: THIRD_PARTY.md
