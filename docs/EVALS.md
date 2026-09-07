# Evals

AgentEdit's claims are benchmarked, never asserted. Each language backend has a
mutation corpus; the ground-truth oracle is the real compiler/type-checker of
that language. Committed artifacts live in `eval/results/*.json` and carry no
absolute machine paths.

## Method

For every candidate symbol × applicable mutation the harness:

1. copies the corpus to a throwaway project,
2. applies the mutation (remove declaration / rename / add required param),
3. type-checks the mutated project with the real oracle,
4. compares the oracle's broken-file set with AgentEdit's predicted impact set
   (computed from the *pre-change* index),
5. aggregates precision/recall over mutations that broke at least one file.

A prediction counts as a false positive only when the oracle says the mutated
file's *consumers* still compile — so the metric measures real impact accuracy,
not graph density.

## Reproduction

```bash
python -m eval.run_eval --corpus tests/fixtures/eval_ts --oracle tsc \
  --mutation remove-declaration --mutation rename --mutation add-required-param \
  --out eval/results/ts.json
# …and per language: eval_py/pyright, eval_go/go, eval_rust/rust, eval_java/java
python -m eval.probe_dispatch   # engine gate: dispatch/inheritance/surfaces (py+TS)
```

Needs real toolchains on `PATH`: npm (`tsc`, `pyright`), go, cargo
(→ `RUSTUP_TOOLCHAIN=stable`), javac. Evals are therefore not part of CI;
`pytest` covers the deterministic core.

## Current results (V1.0, engine v4)

| Corpus | Oracle | cases | tp / fp / fn | precision | recall |
|---|---|---|---|---|---|
| TypeScript | tsc | 29 | 29 / 0 / 0 | 1.0 | 1.0 |
| Python | pyright | 31 | 31 / 0 / 0 | 1.0 | 1.0 |
| Go | go | 24 | 24 / 0 / 0 | 1.0 | 1.0 |
| Rust | cargo | 24 | 24 / 0 / 0 | 1.0 | 1.0 |
| Java | javac | 34 | 34 / 0 / 0 | 1.0 | 1.0 |
| probe_dispatch (engine) | fixed expectations | — | — | 1.0 | 1.0 |

The dispatch probe pins the engine-v4 guarantees that are expensive to catch
with a compiler oracle: same-class `self.` receivers, typed constructor
collaborators, inherited-member dispatch, and that nothing is ever fabricated
for statically-unknowable receivers (they must surface as suspected, not edges).
