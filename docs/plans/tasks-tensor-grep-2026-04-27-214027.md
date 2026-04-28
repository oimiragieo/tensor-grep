# AST Rewrite Apply Performance Recovery Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use `superpowers:subagent-driven-development` for implementation. Each task must start with a failing test, then minimal implementation, then benchmark verification.

**Goal:** Recover the native AST rewrite apply benchmark gate without breaking rewrite safety or harness contracts.

**Architecture:** Add a narrow Rust one-shot apply fast path for safe `tg run --rewrite ... --apply` shapes. Keep contract-heavy paths on the existing plan-first implementation. Accept only measured end-to-end wins.

**Tech Stack:** Rust, ast-grep Rust crates, tree-sitter, Python benchmark harness, pytest, Cargo tests, GitHub Actions.

---

## Files Likely To Change

- `rust_core/src/main.rs`: extract fast-path eligibility and route simple apply.
- `rust_core/src/backend_ast.rs`: use or minimally adjust `plan_and_apply` behavior if tests require it.
- `rust_core/tests/test_ast_rewrite.rs`: add fast-path eligibility and contract tests.
- `benchmarks/run_ast_rewrite_benchmarks.py`: add optional diagnostics only if needed.
- `docs/PAPER.md`: update after accepted or rejected benchmark result.
- `docs/benchmarks.md`: update after accepted benchmark result, or only if governance tests require correcting an existing public claim.
- `docs/tool_comparison.md`: update only after accepted benchmark result.
- `README.md`: update only after accepted benchmark result.

## Vertical Slices

1. Fast-path eligibility contract.
2. One-shot apply routing.
3. Safety contract preservation.
4. Benchmark recovery and documentation.

## TDD Plan

Every task follows red, green, refactor:

1. Write the failing test.
2. Run the narrow test and verify expected failure.
3. Implement the smallest code change.
4. Run the narrow test and verify pass.
5. Run relevant broader tests.
6. Run benchmark gate.
7. Commit only after verification.

## Task 1: Lock Fast-Path Eligibility

**Files:**

- Modify: `rust_core/src/main.rs`
- Test: `rust_core/tests/test_ast_rewrite.rs`

**Purpose:** Prevent unsafe future broadening of the apply fast path.

- [ ] Write a failing Rust test proving simple apply is eligible and contract-heavy flags are not.

Expected test intent:

```rust
#[test]
fn one_shot_apply_fast_path_is_only_enabled_for_safe_simple_apply() {
    let mut args = default_run_args_for_rewrite_apply();
    assert!(can_use_one_shot_apply_fast_path(&args));

    args.json = true;
    assert!(!can_use_one_shot_apply_fast_path(&args));

    args.json = false;
    args.diff = true;
    assert!(!can_use_one_shot_apply_fast_path(&args));

    args.diff = false;
    args.checkpoint = true;
    assert!(!can_use_one_shot_apply_fast_path(&args));

    args.checkpoint = false;
    args.verify = true;
    assert!(!can_use_one_shot_apply_fast_path(&args));

    args.verify = false;
    args.json = true;
    assert!(!can_use_one_shot_apply_fast_path(&args));

    args.json = false;
    args.filter = Some("example".into());
    assert!(!can_use_one_shot_apply_fast_path(&args));

    args.filter = None;
    args.lint_cmd = Some("ruff check".into());
    assert!(!can_use_one_shot_apply_fast_path(&args));
}
```

If `RunArgs` is not constructible from integration tests, extract the predicate to accept primitive booleans or add a focused unit near the parser module.

- [ ] Run failing test.

```powershell
C:/Users/oimir/.cargo/bin/cargo.exe test --manifest-path rust_core/Cargo.toml one_shot_apply_fast_path_is_only_enabled_for_safe_simple_apply
```

Expected: fail because the predicate does not exist.

- [ ] Implement the predicate with the narrowest safe conditions.

Minimum intended logic:

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

Adjust field names to the actual `RunArgs` definition.

- [ ] Re-run the focused test and confirm pass.

## Task 2: Route Simple Apply Through `plan_and_apply`

**Files:**

- Modify: `rust_core/src/main.rs`
- Possibly modify: `rust_core/src/backend_ast.rs`
- Test: `rust_core/tests/test_ast_rewrite.rs`

**Purpose:** Remove avoidable plan-first apply overhead for the benchmarked simple apply path.

- [ ] Write a failing test or instrumentation hook proving eligible simple apply calls the fast path.

- [ ] Write failing equivalence tests proving fast-path results match the existing plan-first path for:

```text
file contents after apply
exit code
stdout/stderr summary
no-match behavior
overlap rejection reporting
```

Suggested low-risk strategy:

- Extract a small helper `apply_ast_rewrite_for_args(...)`.
- Test the branch decision with dependency injection or a mode enum.
- Add a test-only force-plan-first mode if needed so the same fixture can compare both paths without duplicating CLI setup.

Expected behavior:

```rust
#[test]
fn simple_apply_selects_one_shot_apply_fast_path() {
    let args = default_run_args_for_rewrite_apply();
    assert_eq!(select_rewrite_apply_mode(&args), RewriteApplyMode::OneShotFastPath);
}
```

- [ ] Run the test and verify it fails.

```powershell
C:/Users/oimir/.cargo/bin/cargo.exe test --manifest-path rust_core/Cargo.toml simple_apply_selects_one_shot_apply_fast_path
```

- [ ] Implement routing so eligible `--apply` calls `backend.plan_and_apply(...)`.

Target behavior:

```rust
if can_use_one_shot_apply_fast_path(args) {
    let pattern = run_pattern(args)?;
    let plan = backend.plan_and_apply(pattern, replacement, &args.lang, path)?;
    if plan.edits.is_empty() && plan.rejected_overlaps.is_empty() {
        eprintln!("[rewrite] no matches found, nothing to rewrite");
        return Ok(());
    }
    if !plan.rejected_overlaps.is_empty() {
        eprintln!("[rewrite] {} overlapping edit(s) rejected", plan.rejected_overlaps.len());
    }
    eprintln!("[rewrite] applied {} edit(s)", plan.edits.len());
    return Ok(());
}
```

Keep existing plan-first code for all ineligible paths.

- [ ] Re-run focused tests and fix only concrete failures.

## Task 3: Preserve Safety And Contract Paths

**Files:**

- Modify: `rust_core/tests/test_ast_rewrite.rs`
- Possibly modify: `tests/e2e/test_routing_parity.py`

**Purpose:** Prove the fast path does not break non-benchmark rewrite contracts.

- [ ] Add or run tests proving these paths stay plan-first:

```text
--diff
--json
--checkpoint
--audit-manifest
--verify
--lint-cmd
--test-cmd
--apply-edit-ids
--filter
interactive or confirmation mode, if present
```

- [ ] Add or run a destructive-write failure test.

Required behavior:

```text
write failure exits non-zero
no false success summary is emitted
failure identifies the failed file or operation clearly
per-file atomicity is not weaker than the current plan-first apply path
```

- [ ] Run existing rewrite safety tests.

```powershell
C:/Users/oimir/.cargo/bin/cargo.exe test --manifest-path rust_core/Cargo.toml --test test_ast_rewrite
```

Expected: all pass.

- [ ] Run focused Python rewrite/workflow tests if CLI contracts are touched.

```powershell
uv run pytest tests/unit/test_ast_workflows.py tests/unit/test_ast_parity.py tests/e2e/test_routing_parity.py -q
```

Expected: all pass.

## Task 4: Benchmark Before Documentation

**Files:**

- Local artifacts only: `artifacts/bench_ast_rewrite.json`
- No docs until result is accepted or explicitly rejected.

**Purpose:** Confirm benchmark-governed acceptance.

- [ ] Build native release binary.

```powershell
C:/Users/oimir/.cargo/bin/cargo.exe build --release --manifest-path rust_core/Cargo.toml
```

- [ ] Run same-machine pre-change baseline before implementation if it has not already been captured in this branch.

```powershell
uv run python benchmarks/run_ast_rewrite_benchmarks.py --output artifacts/bench_ast_rewrite_baseline.json
```

- [ ] Run AST rewrite benchmark after implementation.

```powershell
uv run python benchmarks/run_ast_rewrite_benchmarks.py --output artifacts/bench_ast_rewrite.json
```

Accepted target:

```text
ratio_tg_vs_sg <= 1.1
passed = true
plan_median_s <= baseline plan_median_s * 1.10
diff_median_s <= baseline diff_median_s * 1.10
```

- [ ] If benchmark fails, do not claim performance recovery. Record exact blocker and decide whether to iterate or reject the attempt.

## Task 5: Run Full Verification Gates

**Files:**

- No additional code unless a gate identifies a concrete failure.

**Commands:**

```powershell
uv run ruff check .
uv run mypy src/tensor_grep
uv run pytest -q
C:/Users/oimir/.cargo/bin/cargo.exe fmt --manifest-path rust_core/Cargo.toml -- --check
C:/Users/oimir/.cargo/bin/cargo.exe clippy --manifest-path rust_core/Cargo.toml --all-targets -- -D warnings
```

Additional hot-path checks:

```powershell
C:/Users/oimir/.cargo/bin/cargo.exe test --manifest-path rust_core/Cargo.toml
uv run python benchmarks/run_ast_rewrite_benchmarks.py --output artifacts/bench_ast_rewrite.json
uv run python benchmarks/run_ast_benchmarks.py --output artifacts/bench_run_ast_benchmarks.json
uv run python benchmarks/run_ast_workflow_benchmarks.py --output artifacts/bench_run_ast_workflow_benchmarks.json
```

Expected:

- lint/type/test gates pass.
- AST search remains accepted.
- AST workflow does not regress materially.
- AST rewrite apply gate either passes or is documented as rejected.

## Task 6: Update Public Docs Only From Artifact

**Files:**

- `docs/PAPER.md`
- `docs/benchmarks.md`
- `docs/tool_comparison.md`
- `README.md`

**Rules:**

- If `artifacts/bench_ast_rewrite.json` passes, restore a narrow benchmark-backed rewrite apply claim.
- If it fails, document the rejected optimization in `docs/PAPER.md` and preserve the current conservative public claim.
- Do not touch `README.md`, `docs/benchmarks.md`, or `docs/tool_comparison.md` for a rejected attempt unless a governance test shows an existing public claim is inaccurate.
- Do not update GPU, cold text search, or MCP claims unless rerun artifacts justify it.

Required docs validation:

```powershell
uv run pytest tests/unit/test_benchmark_docs.py tests/unit/test_benchmark_governance.py tests/unit/test_public_docs_governance.py -q
```

## CI Checks To Run Before Push

Required:

```powershell
uv run ruff check .
uv run mypy src/tensor_grep
uv run pytest -q
C:/Users/oimir/.cargo/bin/cargo.exe fmt --manifest-path rust_core/Cargo.toml -- --check
C:/Users/oimir/.cargo/bin/cargo.exe clippy --manifest-path rust_core/Cargo.toml --all-targets -- -D warnings
```

Hot-path required:

```powershell
C:/Users/oimir/.cargo/bin/cargo.exe test --manifest-path rust_core/Cargo.toml
uv run python benchmarks/run_ast_rewrite_benchmarks.py --output artifacts/bench_ast_rewrite.json
```

Optional but recommended:

```powershell
uv run python benchmarks/run_ast_benchmarks.py --output artifacts/bench_run_ast_benchmarks.json
uv run python benchmarks/run_ast_workflow_benchmarks.py --output artifacts/bench_run_ast_workflow_benchmarks.json
```

## Release Steps

- Use `perf: recover AST rewrite apply fast path` only if the benchmark gate passes and docs are updated.
- Use `fix:` only if the work is correctness-only.
- Use `docs:` if the result is a planning or rejected-attempt documentation update only.
- Do not manually create tags. Let semantic-release handle release intent.
- After push, monitor GitHub Actions.

## Rollback Plan

- Revert the routing predicate and fast-path branch.
- Keep any failing/rejected benchmark documentation if the attempt revealed durable evidence.
- Re-run AST rewrite benchmark and full gates.
- Push a `fix:` rollback only if main is broken; otherwise keep rollback local until reviewed.

## Definition Of Done

- A failing test was written first and observed failing.
- The smallest Rust change makes it pass.
- Existing rewrite safety contracts pass.
- `benchmarks/run_ast_rewrite_benchmarks.py` produces an accepted artifact or a documented rejection.
- Required local gates pass.
- Public docs match artifact evidence.
- CI is green after push.
