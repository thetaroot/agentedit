# Third-party software & data

AgentEdit is distributed under Apache-2.0 (see [LICENSE](LICENSE)). This file
documents bundled or referenced third-party components and their licenses.

## Runtime dependencies

| Component | License | Purpose |
|---|---|---|
| tree-sitter | MIT | parsing engine (PyPI wheel) |
| tree-sitter-typescript | MIT | TypeScript / TSX grammar |
| tree-sitter-javascript | MIT | JavaScript grammar |
| tree-sitter-python | MIT | Python grammar |
| tree-sitter-go | MIT | Go grammar |
| tree-sitter-rust | MIT | Rust grammar |
| tree-sitter-java | MIT | Java grammar |

Tree-sitter language grammars are loaded at runtime only for the languages
explicitly indexed; a grammar is never vendored into this repository.

## Evaluation tooling (not shipped)

The eval harness (`eval/`) runs real compilers / type-checkers as ground-truth
oracles. They are invoked on local corpora only and are not part of the
distributed package.

| Oracle | License | Used for |
|---|---|---|
| TypeScript (`tsc`) | Apache-2.0 | TypeScript/JavaScript eval |
| pyright | MIT | Python eval |
| go toolchain | BSD-3-Clause | Go eval |
| rustc / cargo | Apache-2.0 / MIT | Rust eval |
| javac | GPL-2.0-with-classpath-exception | Java eval |

## Provenance

AgentEdit is the standalone, open-source extraction of the code-graph engine
originally built as part of SwiftGate's `skelett` project
([thetaroot/swiftgate](https://github.com/thetaroot/swiftgate)). Design,
semantics and selected code carried forward are attributed in [NOTICE](NOTICE)
and in the file headers of the affected modules. The managed SwiftGate platform
is a separate, proprietary product.
