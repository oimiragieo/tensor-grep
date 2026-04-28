# AST Rewrite Apply Performance Recovery Design

Project: `tensor-grep`
Date: 2026-04-27 21:40:27 America/New_York

## Current System Context

The native AST backend owns the structural rewrite path. The current benchmarked one-shot apply command is:

```powershell
python benchmarks/run_ast_rewrite_benchmarks.py --output artifacts/bench_ast_rewrite.json
```

The latest artifact shows `tg` applying 50,000 rewrites across 1,000 files but losing to `sg` on apply:

- `tg apply median = 1.428814s`
- `sg apply median = 0.818713s`
- `ratio = 1.745x`
- threshold: `<= 1.1`

Relevant code surfaces:

- `rust_core/src/main.rs`: CLI routing for `run --rewrite --apply`.
- `rust_core/src/backend_ast.rs`: planning, rewrite edit structures, apply paths, safety checks, direct write path, atomic write path.
- `benchmarks/run_ast_rewrite_benchmarks.py`: benchmark gate and artifact schema.
- `rust_core/tests/test_ast_rewrite.rs`: rewrite correctness and safety coverage.
- `tests/unit/test_ast_benchmark_gate.py`: benchmark resolver and gate coverage.

## Proposed Solution

Create a measured, safe one-shot apply fast path for the narrow benchmarked shape while preserving the plan-first path for contract-heavy flows.

The core idea:

1. Extract an explicit `can_use_one_shot_apply_fast_path(args)` predicate in Rust.
2. Route simple `tg run --rewrite ... --apply` through `AstBackend::plan_and_apply`.
3. Keep existing `plan_rewrites` plus `apply_rewrites` for paths that need plan materialization before writing:
   - `--diff`
   - no `--apply`
   - `--json` when payload needs pre-apply plan state
   - checkpoint
   - audit manifest
   - apply-edit-id filtering
   - rewrite filtering
   - interactive or confirmation flows
   - lint/test commands
   - validation or verification if tests show pre-apply plan state is required
4. Benchmark before and after on the same machine. Keep the change only if the end-to-end apply gate improves without contract regressions.

## Architecture And Component Changes

### Rust CLI Routing

Modify `rust_core/src/main.rs` only after a failing test exists.

Planned shape:

```rust
fn can_use_one_shot_apply_fast_path(args: &RunArgs) -> bool {
    args.apply
        && !args.diff
        && !args.json
        && !args.checkpoint
        && args.audit_manifest.is_none()
        && args.apply_edit_ids.is_empty()
        && args.filter.is_none()
        && !args.verify
        && !validation_requested(args)
        && !interactive_requested(args)
        && args.lint_cmd.is_none()
        && args.test_cmd.is_none()
}
```

The exact field names must be verified against `RunArgs` before implementation. The test should lock the predicate against accidental broadening.

### AST Backend

Use existing backend functions where possible:

- Existing safe path: `plan_rewrites` -> `filter_rewrite_plan` -> `AstBackend::apply_rewrites`.
- Candidate fast path: `AstBackend::plan_and_apply`.

If `plan_and_apply` misses behavior required by the simple apply surface, extend it minimally and test first. Do not duplicate the rewrite engine.

### Benchmark Script

Keep `benchmarks/run_ast_rewrite_benchmarks.py` schema stable. If deeper diagnostics are useful, add optional fields without removing existing keys:

- `apply_mode`
- `fast_path_eligible`
- `tg_apply_median_s`
- `sg_apply_median_s`
- `ratio_tg_vs_sg`

## Data Model Changes

No durable data model changes are planned.

Possible in-memory changes:

- Avoid storing full original source in the hot apply path when no later JSON/audit/verify contract needs it.
- Avoid global edit materialization where per-file direct apply is sufficient.

These are implementation candidates, not requirements. Tests and benchmarks decide.

## API And CLI Changes

No public CLI changes.

Public behavior must remain:

```powershell
tg run --lang python --rewrite "lambda $$$ARGS: $EXPR" "def $F($$$ARGS): return $EXPR" PATH --apply
```

Exit codes, stderr summaries, file edits, and safety behavior must remain compatible.

Fast-path equivalence must cover:

- byte-for-byte file contents after apply.
- exit status for success, no-match, overlap rejection, and write failure.
- stdout/stderr summaries.
- no-match behavior.
- overlap reporting.

## UI/UX Changes

No frontend or UI work. This project is a CLI/library/tooling project.

## Auth, Billing, Deployment, And Integrations

Not applicable. There is no SaaS auth/billing surface in this scope.

MCP and harness contracts are indirectly protected by keeping JSON, checkpoint, audit, and validation paths on the existing plan-first route unless separately tested.

## Security, Privacy, Performance, And Reliability

Security:

- Do not weaken stale-file checks.
- Do not bypass binary-file or large-file skip behavior.
- Do not write outside matched paths.

Privacy:

- No new telemetry or external network calls.

Performance:

- Primary metric: `ratio_tg_vs_sg <= 1.1` for `benchmarks/run_ast_rewrite_benchmarks.py`.
- Baseline requirement: run the benchmark once before implementation on the same machine and save it as `artifacts/bench_ast_rewrite_baseline.json`.
- Secondary metrics: plan and diff medians must not regress by more than 10% versus the same-machine baseline unless the regression is documented and explicitly accepted.

Reliability:

- Keep deterministic edit ordering for plan/diff outputs.
- Keep direct-write behavior scoped to one-shot apply only.
- Preserve atomic-write path for explicit plan-then-apply APIs.
- Do not accept a fast path that reports success after a partial write failure.
- Do not weaken per-file atomicity relative to the current plan-first apply path. If `plan_and_apply` cannot match current failure semantics, either harden it first or reject the fast-path attempt.

## Alternatives Considered

### Alternative 1: Optimize Existing Plan-Then-Apply Path

Pros:

- Lower semantic risk for JSON/audit/checkpoint flows.

Cons:

- May not remove the largest overhead if duplicate reads/source retention dominate.
- Could require broad refactoring before proving value.

Decision: keep as fallback if the fast-path eligibility design fails.

### Alternative 2: Make `plan_and_apply` The Default For All Apply Flows

Pros:

- Maximum performance opportunity.

Cons:

- High risk to checkpoint, audit, filtering, verification, and JSON contracts.

Decision: reject for this slice.

### Alternative 3: Defer Rewrite And Work On GPU Or Cold Text Search

Pros:

- GPU and cold search are visible product areas.

Cons:

- GPU has no current crossover and large rows time out.
- Cold text search has several rejected native shortcut attempts.
- AST rewrite has the clearest current failing gate and implementation seam.

Decision: do AST rewrite first.

## Research Findings

- ast-grep remains the correct comparator for structural rewrite because its official CLI supports one-shot run rewrites and `--update-all`: https://ast-grep.github.io/reference/cli.html
- ast-grep optimization guidance highlights avoiding duplicate traversal and using selective rule structures, which maps to this repo's suspected duplicate plan/apply work: https://ast-grep.github.io/blog/optimize-ast-grep.html
- Public codemod benchmarking continues to show ast-grep as a top structural rewrite baseline, so recovering the `sg` gate has market value: https://github.com/codemod/benchmark
- Tree-sitter incremental parsing APIs are relevant for future repeated edit sessions, but not required for this first one-shot apply recovery: https://github.com/tree-sitter/tree-sitter/blob/master/lib/binding_rust/README.md
- GPU regex research and RAPIDS Glushkov NFA work are promising but not applicable to this immediate rewrite apply bottleneck.

## Open Questions

- Does `RunArgs` already expose every needed flag for a clean fast-path predicate, or does the predicate need to live lower in the rewrite handler?
- Does `plan_and_apply` produce enough `RewritePlan` metadata for future JSON-compatible fast paths, or should JSON remain plan-first?
- Is the current slowdown mostly duplicate file I/O, source cloning, global sort/id assignment, or stale checks?

These questions should be answered by failing tests, profiling, and the benchmark artifact before implementation is accepted.
