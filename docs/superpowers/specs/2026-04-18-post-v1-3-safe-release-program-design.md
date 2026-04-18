# Post-v1.3 Safe Release Program Design

**Date:** 2026-04-18

**Status:** Proposed

## Goal

Ship the already-validated `release-next-safe` branch as the current minor line, then reach the next safe release by landing only benchmark-governed, non-breaking fixes on top of that line.

## Assumptions

- `release-next-safe` is the validated `v1.3.0` candidate and should ship first.
- This program targets the release after that candidate, expected to be a patch line such as `v1.3.1`.
- If no accepted performance or additive bug-fix candidate survives the gates, there should be no package release. Governance-only work is allowed to land without a release.

## Current State

The validated `release-next-safe` line closes the previously release-blocking parity bugs:

- `-r` capture-group substitution
- `--files-without-match` false positives
- `-0` / `--null` separator correctness
- `--files` without a required pattern
- `--glob-case-insensitive`
- inline AST rules
- shell completions
- Windows `cp1252` AST output safety
- richer native AST match metadata (`range`, `metaVariables`)
- Rust AST match coverage
- native AST rewrite diff preview

The remaining work is not “fix everything.” The live unresolved areas are narrower:

1. cold-path text-search speed parity is still not where `rg` is
2. count-mode and `--cpu` performance remain the most plausible post-release patch targets
3. a few native-vs-Python JSON shape differences still exist and should not be “fixed” in a patch if they are schema-breaking
4. retrieval/ranking improvements for the intelligence layer are still under-measured relative to the quality bar expected for benchmark-governed work

## Research-Validated Scope

The external evidence supports a narrow patch program now and a separate retrieval-focused minor later.

### Retrieval evaluation should be first-class, not inferred

[ContextBench](https://arxiv.org/html/2602.05892v1) shows that coding-agent retrieval should be evaluated with explicit file/block/line recall, precision, F1, and efficiency metrics instead of only final task success. That validates *not* mixing retrieval/ranking experiments into the immediate patch line without a dedicated scorecard.

The [RAG benchmark Reddit post](https://www.reddit.com/r/Rag/comments/1rlt0su/your_rag_benchmark_is_lying_to_you_and_i_have_the.json) is informal, but directionally consistent: MRR alone is a poor proxy for agent usefulness, and agent-facing retrieval should be measured by whether returned context is actually sufficient.

### Hybrid lexical-first retrieval is promising, but too large for this patch

[Repository-level Code Search with Neural Retrieval Methods](https://arxiv.org/abs/2502.07067) supports lexical-first candidate generation plus reranking for repository search. [RANGER](https://arxiv.org/html/2509.25257v1) suggests graph-guided routing can help natural-language retrieval, especially for repository-scale tasks.

Those results justify a later minor release for the intelligence layer, but they do **not** justify putting graph retrieval, vector search, or ranking overhauls into the immediate post-`v1.3.0` patch. That would be new product surface, not safe remediation.

The [grepika Reddit thread](https://www.reddit.com/r/ClaudeAI/comments/1r46hch/built_a_claude_code_plugin_that_gives_it_a/) reinforces the practical value of compact lexical/trigram/FTS ranking for agent tooling, but it is still anecdotal. Treat it as directional signal, not release evidence.

### Performance governance needs stronger host-awareness than ad hoc reruns

[Risk-Aware Batch Testing for Performance Regression Detection](https://arxiv.org/html/2604.00222v1) and [Investigating the Impact of Isolation on Synchronized Benchmarks](https://arxiv.org/html/2511.03533v1) both reinforce the same operational lesson: noisy or mismatched environments can create false conclusions. That validates the repo’s existing benchmark discipline and supports tightening comparator-drift and provenance rules before trusting a new performance patch.

### Release mechanics should stay fully conventional

The repo contract in `docs/CI_PIPELINE.md` already matches the [semantic-release project documentation](https://github.com/semantic-release/semantic-release):

- `feat:` => minor
- `fix:` / `perf:` => patch
- `feat!:` / `fix!:` => major
- governance-only `docs:` / `test:` / `build:` / `ci:` / `chore:` => no release

This remains the right policy. The next safe release should be a patch only if it contains an accepted `fix:` or `perf:` change.

## Release Boundary

### Ship now

`v1.3.0` should come from the already validated `release-next-safe` branch. No additional scope should be folded into that branch.

### Build next

The next safe release should be a **patch**, not a minor. Its allowed scope is:

- benchmark-governance hardening needed to trust the patch candidate
- one accepted performance fix for `-c` / `--count-matches` or `--cpu`
- optional additive, non-breaking bugfixes discovered while doing that work

### Explicitly out of scope for the patch

- graph databases or GraphRAG
- vector retrieval or embedding infrastructure
- broad intelligence-layer ranking changes
- JSON schema rewrites that change public field contracts
- major AST test-framework expansion
- public benchmark marketing copy without accepted artifacts

Those belong in a later minor release, not the immediate patch program.

## Program Structure

The program should run as four narrow workstreams:

1. `v1.3.0` freeze and handoff
2. benchmark governance hardening (no release)
3. count-mode performance remediation
4. `--cpu` performance remediation

The release decision happens only after workstreams 2-4 complete.

## Workstream 1: `v1.3.0` Freeze And Handoff

Purpose:

- ship the validated minor from `release-next-safe`
- avoid poisoning the current release with new investigation work
- start all post-release work from a clean replay worktree based on the shipped line

Definition of done:

- the release-bearing PR is squash-merged
- semantic-release owns the tag
- the post-release worktree starts from the resulting `origin/main`

## Workstream 2: Benchmark Governance Hardening

Purpose:

- make comparator drift explicit and enforceable
- keep host provenance tied to accepted artifacts
- ensure future performance patches fail closed when the benchmark comparison is untrustworthy

Likely files:

- `src/tensor_grep/perf_guard.py`
- `benchmarks/check_regression.py`
- `benchmarks/run_benchmarks.py`
- `tests/unit/test_perf_guard.py`
- `tests/unit/test_benchmark_scripts.py`
- `tests/unit/test_benchmark_artifacts_schema.py`

Release intent:

- `test:` or `build:`
- no package release on this workstream alone

Definition of done:

- a clean post-`v1.3.0` replay run can distinguish real `tg` regressions from comparator drift
- the governance path has validator-backed tests

## Workstream 3: Count-Mode Performance Remediation

Purpose:

- investigate and, if justified, reduce `-c` / `--count-matches` overhead without regressing correctness

Likely files:

- `src/tensor_grep/cli/main.py`
- `src/tensor_grep/backends/ripgrep_backend.py`
- `benchmarks/run_benchmarks.py`
- `tests/unit/test_cli_modes.py`
- `tests/e2e/test_output_golden_contract.py`

Release intent:

- `perf:` if the accepted slice is purely performance
- `fix:` only if a real correctness defect is uncovered

Definition of done:

- the governing benchmark artifact improves or the candidate is rejected and recorded
- no public speed claim changes without an accepted artifact

## Workstream 4: `--cpu` Performance Remediation

Purpose:

- investigate and, if justified, reduce `--cpu` overhead relative to default and `rg`

Likely files:

- `src/tensor_grep/cli/main.py`
- `src/tensor_grep/core/pipeline.py`
- `benchmarks/run_tool_comparison_benchmarks.py`
- `benchmarks/run_native_cpu_benchmarks.py`
- `tests/unit/test_cli_modes.py`

Release intent:

- `perf:` if the slice is accepted
- otherwise no release

Definition of done:

- the accepted CPU artifact shows a real win on the governed row set
- or the candidate is rejected and the release does not happen

## Release Decision Rule

After the governance slice lands:

1. If both performance candidates are rejected, stop. No package release.
2. If exactly one accepted `perf:` or `fix:` slice survives, ship a patch release from that slice.
3. If the only changes are governance/docs/tests, merge them normally with no release.
4. If a candidate requires a public JSON schema change or a broader intelligence-layer change, defer it to a future minor release.

## Future Minor: Retrieval Scorecard Program

The next *minor* after the patch should be a retrieval-quality program for the differentiated intelligence layer, not a hidden side effect of the patch program.

That future minor should add:

- a gold-context evaluation set for `context`, `context-render`, `blast-radius`, and related commands
- metrics closer to ContextBench: file/block/line precision, recall, F1, and efficiency
- agent-usefulness metrics instead of only search-style ranking metrics
- measured lexical-first plus reranking experiments before any graph or vector infrastructure expansion

## Push And Release Discipline

Follow the repo’s existing rules exactly:

1. use a clean replay worktree
2. run `uv run ruff check .`
3. run `uv run mypy src/tensor_grep`
4. run `uv run pytest -q`
5. run the exact governing benchmark commands for the changed workstream
6. only push the accepted slice
7. squash-merge release-bearing PRs
8. let semantic-release create the tag

## Bottom Line

The next safe release is **not** “fix everything from the parity report.” The safe path is:

1. ship `v1.3.0` from the already validated branch
2. harden benchmark governance on `main`
3. try one narrow `-c` or `--cpu` performance slice at a time
4. release only if one of those slices produces an accepted artifact-backed patch
