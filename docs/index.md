# tensor-grep (tg)

`tensor-grep` is a native search and rewrite tool for large text corpora and codebases. The product combines:

- Rust-native CPU text search
- Rust-native AST search and rewrite
- indexed repeated-query acceleration
- optional GPU / NLP paths for the workloads that justify them
- machine-readable CLI and MCP surfaces for harnesses and editor tooling

## Start Here

- [README.md](../README.md) for the product overview and installation entry points
- [docs/benchmarks.md](benchmarks.md) for accepted benchmark lines and regression rules
- [docs/routing_policy.md](routing_policy.md) for backend routing behavior
- [docs/harness_api.md](harness_api.md) for machine-readable CLI contracts
- [docs/installation.md](installation.md) for install and package-manager guidance

## Enterprise / Operational Docs

- [docs/SUPPORT_MATRIX.md](SUPPORT_MATRIX.md)
- [docs/CONTRACTS.md](CONTRACTS.md)
- [docs/HOTFIX_PROCEDURE.md](HOTFIX_PROCEDURE.md)
- [docs/RELEASE_CHECKLIST.md](RELEASE_CHECKLIST.md)
- [docs/EXPERIMENTAL.md](EXPERIMENTAL.md)
- [docs/runbooks/](runbooks/)

## Product Positioning

- `rg` remains the cold generic text-search baseline.
- `tensor-grep` is strongest on native AST workflows, repeated-query acceleration, machine-readable harness flows, and managed enterprise rollout.
- GPU acceleration is benchmark-governed and hardware-specific, not a universal default.
