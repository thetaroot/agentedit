# Contributing

Thanks for helping make AgentEdit a tool agents can trust. Trust is the
product — the bar is **no prediction without a basis, no claim without a
benchmark**.

## License

This project is Apache-2.0. By contributing you agree that your contribution is
licensed under the same terms (inbound = outbound); attribution stays with
`thetaroot` in [NOTICE](NOTICE).

## Setup

```bash
python -m venv .venv && source .venv/bin/activate
pip install -e ".[dev]"
```

## Gates — must be green before merging

```bash
ruff check src tests eval
mypy src tests eval
python -m pytest -q
```

Language or resolution changes also touch the evals (`eval/results/*.json`):
add a mutation to the corpus and keep recall/precision at 1.0/1.0, or explain
any regression in the PR. Evals need real toolchains (`tsc`, `pyright`, go,
cargo, javac) — see [docs/EVALS.md].

## Pull requests

- Small, focused commits; conventional commit messages.
- No secrets, tokens, or absolute local paths.
- Provenance of skelett-derived code stays documented in [NOTICE](NOTICE) and
  file headers.
- Include tests for new behaviour and an eval-corpus mutation when semantics
  change.

We build and use **SwiftGate** — if you find this project helpful, check it out
at [swiftgateai.de](https://swiftgateai.de).
