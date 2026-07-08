# tensor-grep (tg)

`tensor-grep` is a native search and rewrite tool for large text corpora and codebases. The product combines:

- Rust-native CPU text search
- Rust-native AST search and rewrite
- indexed repeated-query acceleration
- optional GPU / NLP paths for the workloads that justify them
- machine-readable CLI and MCP surfaces for harnesses and editor tooling

## Start Here

- [Project README on GitHub](https://github.com/oimiragieo/tensor-grep/blob/main/README.md) for the product overview and installation entry points
- [docs/benchmarks.md](benchmarks.md) for accepted benchmark lines and regression rules
- [docs/tool_comparison.md](tool_comparison.md) for the public workload-class comparison story
- [docs/routing_policy.md](routing_policy.md) for backend routing behavior
- [docs/harness_api.md](harness_api.md) for machine-readable CLI contracts
- [docs/multi_agent_context_plane.md](multi_agent_context_plane.md) for how concurrent agents share machine-computed code facts through the session store and warm daemon
- [docs/installation.md](installation.md) for install and package-manager guidance
- [docs/CI_PIPELINE.md](CI_PIPELINE.md) for CI, release, Dependabot, and scheduled audit automation

## Enterprise / Operational Docs

- [docs/SUPPORT_MATRIX.md](SUPPORT_MATRIX.md)
- [docs/CONTRACTS.md](CONTRACTS.md)
- [docs/HOTFIX_PROCEDURE.md](HOTFIX_PROCEDURE.md)
- [docs/RELEASE_CHECKLIST.md](RELEASE_CHECKLIST.md)
- [docs/CI_PIPELINE.md](CI_PIPELINE.md)
- [docs/EXPERIMENTAL.md](EXPERIMENTAL.md)
- [docs/runbooks/cache-management.md](runbooks/cache-management.md)
- [docs/runbooks/gpu-troubleshooting.md](runbooks/gpu-troubleshooting.md)
- [docs/runbooks/resident-worker.md](runbooks/resident-worker.md)

## Product Positioning

- `rg` remains the cold generic text-search baseline.
- `tg --cpu` can beat `rg` on selected count-heavy workloads and materially narrow the large-file gap, but comparison claims stay workload-specific.
- `tensor-grep` is strongest on native AST workflows, repeated-query acceleration, machine-readable harness flows, and managed enterprise rollout.
- Bounded heavy-root context renders can now include full edit-plan seed metadata without escaping the capped repo-map file universe.
- GPU acceleration is benchmark-governed and hardware-specific, not a universal default.
- **New in v1.14.0+:** `tg search PATTERN PATH --rank` adds local BM25 re-ranking of text-search results (pure-Python, no API key, no GPU). `tg orient [PATH]` produces a one-call codebase orientation capsule (most central files by import in-degree, entry points, symbol map, AST snippets — pure-CPU). See the [README](https://github.com/oimiragieo/tensor-grep/blob/main/README.md) for usage. The post-release dogfood harness at `scripts/dogfood/` verifies these and all other public command shapes against the installed binary. Current releases also expose agent-contract completeness signals: `tg callers` and `tg blast-radius` emit `result_incomplete` when a scan was truncated by a cap, plus a caveat on resolved zero-caller results, so harnesses never mistake a capped or zero-caller result for confirmed dead code.
