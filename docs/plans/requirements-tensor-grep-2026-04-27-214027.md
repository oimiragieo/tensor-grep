# AST Rewrite Apply Performance Recovery Requirements

Project: `tensor-grep`
Date: 2026-04-27 21:40:27 America/New_York
Recommended next work: recover the native AST rewrite apply benchmark gate.

## Problem Statement

`tensor-grep` v1.6.3 is stable and CI-green, but the latest benchmark refresh demoted AST rewrite apply from a performance claim. The current artifact `artifacts/bench_ast_rewrite.json` shows:

- `tg apply median = 1.428814s`
- `sg apply median = 0.818713s`
- `tg/sg ratio = 1.745x`
- gate: `max_ratio_tg_vs_sg <= 1.1`
- result: `passed = false`

This matters because AST rewrite is a core AI-harness editing surface. Correctness exists, but performance credibility is currently weaker than the public product story should allow.

## Business Objective

Restore a benchmark-governed AST rewrite apply claim so `tensor-grep` can credibly position itself as an agent-friendly search and edit substrate, not just a structural search tool.

Success means the next implementation can say: AST rewrite apply is either performance-recovered against `sg` or the exact blocker is measured, documented, and converted into a smaller follow-up.

## Target Users And Stakeholders

- CLI users applying structural rewrites with `tg run --rewrite ... --apply`.
- Agent harness users using MCP or JSON rewrite workflows.
- Maintainers responsible for benchmark, release, and docs accuracy.
- CEO/business stakeholders relying on defensible performance claims.

## User Stories

- As a developer, I can run one-shot AST rewrite apply without paying unnecessary plan/apply overhead.
- As an AI harness, I can call rewrite apply and keep JSON, verification, checkpoint, audit, and validation contracts stable.
- As a maintainer, I can run a focused benchmark and know whether rewrite apply is accepted or rejected.
- As a business owner, I can publish rewrite-performance claims only when the benchmark gate proves them.

## Candidate Work Ranking

| Rank | Candidate | Business impact | Urgency | Complexity | Risk | Testability | Recommendation |
| --- | --- | ---: | ---: | ---: | ---: | ---: | --- |
| 1 | AST rewrite apply performance recovery | High | High | Medium | Medium | High | Do next |
| 2 | Native cold-path control-plane rewrite | High | Medium | High | High | Medium | Design later |
| 3 | GPU fault/crossover hardening | Medium | Medium | Medium | Medium | High | Defer until rewrite is recovered |
| 4 | MCP resident worker for repeated rewrite/index | Medium | Low | Medium | Medium | Medium | Defer pending benchmark proof |
| 5 | Additional docs/paper cleanup | Low | Low | Low | Low | High | Bundle only when tied to accepted results |

## Functional Requirements

FR1. Preserve existing CLI behavior for `tg run --rewrite REPLACEMENT PATTERN PATH`.

FR2. Recover or improve `tg run --rewrite ... --apply` performance for the benchmarked simple one-shot apply shape.

FR3. Do not change `--diff`, dry-run JSON plan, batch rewrite, checkpoint, audit manifest, apply-edit-id filtering, validation, verification, no-match output, overlap reporting, stderr summaries, or exit-code behavior unless a failing test proves the change is required.

FR4. Prefer a safe fast path only for simple one-shot apply shapes where contracts allow it:

- `--apply` is set.
- `--diff` is not set.
- no checkpoint or audit manifest is requested.
- no edit-id filtering is requested.
- no interactive flow is requested.
- no JSON output is requested.
- no validation, verification, lint command, or test command is requested.
- no rewrite filter is requested.

FR5. Keep stale-file, overlap rejection, BOM/CRLF, binary-file skip, UTF-8 boundary, and large-file protections intact.

FR6. Keep benchmark artifacts schema-compatible with `benchmarks/run_ast_rewrite_benchmarks.py`.

FR7. Update `docs/PAPER.md` for both accepted and rejected attempts. Update public benchmark/product docs (`README.md`, `docs/benchmarks.md`, `docs/tool_comparison.md`) only after an accepted artifact, unless an existing public claim must be corrected by governance tests.

## Non-Functional Requirements

NFR1. No correctness regression in AST rewrite tests.

NFR2. No broad rewrite of CI, release, or validator contracts.

NFR3. No unmeasured speed claims.

NFR4. Cross-platform behavior must hold on Windows, macOS, and Linux CI.

NFR5. The implementation must be small enough to review and revert.

## Acceptance Criteria And TDD Mapping

| ID | Acceptance criterion | Required tests and benchmarks |
| --- | --- | --- |
| AC1 | Simple one-shot apply uses the intended fast path only when safe. | New Rust unit test around `handle_ast_rewrite_apply` eligibility or extracted predicate; failing first. |
| AC2 | Eligible fast-path output is equivalent to the current plan-first path for the simple apply surface. | New parity test comparing byte-for-byte file edits, exit codes, stdout/stderr summaries, no-match behavior, and overlap reporting between plan-first and fast-path modes. |
| AC3 | Dry-run plan output is unchanged. | Existing `rust_core/tests/test_ast_rewrite.rs`; add fixture if fast-path extraction changes plan code. |
| AC4 | `--diff` output is unchanged. | Existing rewrite diff tests plus focused `tg run --rewrite ... --diff` CLI test. |
| AC5 | Checkpoint, audit, JSON, filter, interactive, lint/test, verify, and validation paths do not use unsafe fast path. | New Rust or e2e tests for fast-path ineligibility when those flags are present. |
| AC6 | Rewrite safety contracts remain intact. | Existing BOM/CRLF/binary/stale/overlap tests in Rust AST rewrite coverage plus a write-failure test that proves no silent partial success. |
| AC7 | Benchmark gate passes or records a measured rejection against a same-machine baseline. | Run `python benchmarks/run_ast_rewrite_benchmarks.py --output artifacts/bench_ast_rewrite_baseline.json` before implementation and `python benchmarks/run_ast_rewrite_benchmarks.py --output artifacts/bench_ast_rewrite.json` after implementation; target `ratio_tg_vs_sg <= 1.1` and no plan/diff median regression greater than 10% without documented reason. |
| AC8 | No repo-wide regression. | `uv run ruff check .`, `uv run mypy src/tensor_grep`, `uv run pytest -q`, Rust `cargo fmt --check`, Rust clippy/tests, and relevant benchmark reruns. |

## Out Of Scope

- GPU crossover optimization.
- Broad native cold-path rewrite.
- New AST languages.
- MCP resident worker architecture.
- CI/release workflow edits.
- Changing public rewrite semantics for checkpoint, audit, validation, or JSON payloads.

## Assumptions

- `rust_core/src/backend_ast.rs` contains a `plan_and_apply` path that may be usable for simple one-shot apply.
- `rust_core/src/main.rs` currently plans first and then calls `AstBackend::apply_rewrites` for apply flows.
- The current benchmark regression is caused by avoidable orchestration or duplicate work, not by required safety checks alone.
- If the fast path cannot preserve contracts, the implementation should stop and document the measured blocker rather than forcing the optimization.

## Risks And Mitigations

| Risk | Mitigation |
| --- | --- |
| Fast path bypasses safety behavior. | Gate it with an explicit eligibility predicate and tests for every excluded flag. |
| Benchmark win only appears locally. | Run same benchmark, same artifact, and CI benchmark-regression before publishing claims. |
| JSON/audit/checkpoint contracts drift. | Keep those paths on existing plan-first implementation until separately designed. |
| Destructive writes fail halfway through a run. | Require non-zero exit, no false success summary, clear failure reporting, and no weaker per-file atomicity than the current plan-first apply path. |
| `sg` comparator changes or PATH resolves wrong binary. | Use resolver checks already in benchmark scripts and record `sg_binary` in artifact. |
| Large docs/paper claims go stale again. | Update public docs only after accepted artifacts, and preserve rejected attempts in `docs/PAPER.md`. |

## Tool And Research Evidence Summary

Repository evidence:

- `README.md` and `docs/tool_comparison.md` now mark AST rewrite apply as a performance follow-up.
- `docs/benchmarks.md` records the failed `v1.6.3` rewrite gate.
- `docs/PAPER.md` preserves the failed benchmark line and demotes prior rewrite-speed claims to historical context.
- `benchmarks/run_ast_rewrite_benchmarks.py` provides the governing artifact and threshold.
- `rust_core/src/backend_ast.rs` contains both plan/apply and plan-and-apply rewrite paths.
- `rust_core/src/main.rs` routes one-shot apply through plan-first handling today.

Current research:

- ast-grep official docs position `run`, `scan`, `test`, and `--update-all` as the relevant structural rewrite comparator: https://ast-grep.github.io/reference/cli.html
- ast-grep performance guidance emphasizes profiling, reducing duplicate tree traversal, and using selective matching: https://ast-grep.github.io/blog/optimize-ast-grep.html
- Current public codemod benchmarks show ast-grep remains a strong rewrite baseline, so beating or matching it is commercially meaningful: https://github.com/codemod/benchmark
- Tree-sitter Rust docs support parser/tree reuse and incremental parsing as relevant future design options, but this slice should first remove avoidable one-shot apply overhead: https://github.com/tree-sitter/tree-sitter/blob/master/lib/binding_rust/README.md
- GPU regex research remains relevant but deferred: BitGen, RAPIDS cuDF Glushkov NFA work, and arXiv regex testing all point to future GPU work, not this immediate fix.
