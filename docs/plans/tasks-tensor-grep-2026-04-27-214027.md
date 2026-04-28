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
5. PyPI/MCP rewrite plan/apply release smoke recovery.

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

- [x] Write a failing Rust test proving simple apply is eligible and contract-heavy flags are not.

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
    args.lint_cmd = Some("ruff check".into());
    assert!(!can_use_one_shot_apply_fast_path(&args));
}
```

If `RunArgs` is not constructible from integration tests, extract the predicate to accept primitive booleans or add a focused unit near the parser module.

- [x] Run failing test.

```powershell
C:/Users/oimir/.cargo/bin/cargo.exe test --manifest-path rust_core/Cargo.toml one_shot_apply_fast_path_is_only_enabled_for_safe_simple_apply
```

Expected: fail because the predicate does not exist.

- [x] Implement the predicate with the narrowest safe conditions.

Minimum intended logic:

```rust
fn can_use_one_shot_apply_fast_path(args: &RunArgs) -> bool {
    args.apply
        && !args.diff
        && !args.json
        && !args.checkpoint
        && args.audit_manifest.is_none()
        && args.apply_edit_ids.is_empty()
        && !args.verify
        && args.lint_cmd.is_none()
        && args.test_cmd.is_none()
}
```

Adjust field names to the actual `RunArgs` definition. Current `RunArgs` has no `filter` or `interactive` fields, so those are not implementation guards for this slice.

- [x] Re-run the focused test and confirm pass.

## Task 2: Route Simple Apply Through `plan_and_apply`

**Files:**

- Modify: `rust_core/src/main.rs`
- Possibly modify: `rust_core/src/backend_ast.rs`
- Test: `rust_core/tests/test_ast_rewrite.rs`

**Purpose:** Remove avoidable plan-first apply overhead for the benchmarked simple apply path.

- [x] Write a failing test or instrumentation hook proving eligible simple apply calls the fast path.

- [x] Write or confirm equivalence tests proving fast-path results match the existing plan-first path for:

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

- [x] Run the test and verify it fails.

```powershell
C:/Users/oimir/.cargo/bin/cargo.exe test --manifest-path rust_core/Cargo.toml simple_apply_selects_one_shot_apply_fast_path
```

- [x] Implement routing so eligible `--apply` calls `backend.plan_and_apply(...)`.

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

- [x] Re-run focused tests and fix only concrete failures.

## Task 3: Preserve Safety And Contract Paths

**Files:**

- Modify: `rust_core/tests/test_ast_rewrite.rs`
- Possibly modify: `tests/e2e/test_routing_parity.py`

**Purpose:** Prove the fast path does not break non-benchmark rewrite contracts.

- [x] Add or run tests proving these paths stay plan-first:

```text
--diff
--json
--checkpoint
--audit-manifest
--verify
--lint-cmd
--test-cmd
--apply-edit-ids
--reject-edit-ids
batch rewrite
future filter or interactive mode, if introduced
```

- [x] Confirm destructive-write failure coverage remains intact.

Required behavior:

```text
write failure exits non-zero
no false success summary is emitted
failure identifies the failed file or operation clearly
direct-write fast-path semantics are documented, while the plan-first path keeps atomic temp-file rename semantics
```

- [x] Run existing rewrite safety tests.

```powershell
C:/Users/oimir/.cargo/bin/cargo.exe test --manifest-path rust_core/Cargo.toml --test test_ast_rewrite
```

Expected: all pass.

- [x] Run focused Python rewrite/workflow tests if CLI contracts are touched.

```powershell
uv run pytest tests/unit/test_ast_workflows.py tests/unit/test_ast_parity.py tests/e2e/test_routing_parity.py -q
```

Expected: all pass.

## Task 4: Benchmark Before Documentation

**Files:**

- Local artifacts only: `artifacts/bench_ast_rewrite.json`
- No docs until result is accepted or explicitly rejected.

**Purpose:** Confirm benchmark-governed acceptance.

- [x] Build native release binary.

```powershell
C:/Users/oimir/.cargo/bin/cargo.exe build --release --manifest-path rust_core/Cargo.toml
```

- [x] Run same-machine pre-change baseline before implementation if it has not already been captured in this branch.

```powershell
uv run python benchmarks/run_ast_rewrite_benchmarks.py --output artifacts/bench_ast_rewrite_baseline.json
```

Captured baseline in this worktree:

```text
tg_apply_median_s = 1.175020799972117
sg_apply_median_s = 0.6488509000046179
ratio_tg_vs_sg = 1.811
passed = false
```

- [x] Run AST rewrite benchmark after implementation.

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

Accepted result on the standard local artifact:

```text
artifacts/bench_ast_rewrite.json: tg_apply_median_s = 0.534, sg_apply_median_s = 0.643, ratio_tg_vs_sg = 0.831, passed = true
```

- [x] Benchmark passed; restore only the narrow one-shot apply performance claim.

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

Completed validation:

- `uv run ruff check .`: passed.
- `uv run mypy src/tensor_grep`: passed.
- `uv run pytest -q`: passed with `1698 passed, 22 skipped`.
- `C:/Users/oimir/.cargo/bin/cargo.exe fmt --manifest-path rust_core/Cargo.toml -- --check`: passed.
- `C:/Users/oimir/.cargo/bin/cargo.exe clippy --manifest-path rust_core/Cargo.toml --all-targets -- -D warnings`: passed.
- `C:/Users/oimir/.cargo/bin/cargo.exe test --manifest-path rust_core/Cargo.toml`: passed.
- `uv run python benchmarks/run_ast_rewrite_benchmarks.py --output artifacts/bench_ast_rewrite.json`: passed, `tg/sg = 0.831x`.
- `uv run python benchmarks/run_ast_benchmarks.py --output artifacts/bench_run_ast_benchmarks.json`: passed, `tg/sg = 0.849x`.
- `uv run python benchmarks/run_ast_workflow_benchmarks.py --output artifacts/bench_run_ast_workflow_benchmarks.json`: passed.
- `uv run pytest tests/unit/test_benchmark_docs.py tests/unit/test_benchmark_governance.py tests/unit/test_public_docs_governance.py -q`: passed with `27 passed`.
- `uv run pytest tests/unit/test_ast_workflows.py tests/unit/test_ast_parity.py tests/e2e/test_routing_parity.py -q`: passed with `61 passed` when `TG_RG_PATH` was pinned to the repo-owned ripgrep binary.
- `git diff --check`: passed.

Code review feedback processed:

- Updated public docs to cite the standard `artifacts/bench_ast_rewrite.json` artifact instead of a nonstandard ignored control artifact.
- Clarified that the safe one-shot fast path intentionally uses existing direct-write semantics, while atomic temp-file rename remains on the plan-first apply path.

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

Status: public docs updated from the accepted artifact; docs governance validation passed (`27 passed`).

## Task 7: Restore PyPI/MCP Rewrite Plan/Apply Smoke Coverage

**Files:**

- `rust_core/src/lib.rs`
- `src/tensor_grep/cli/ast_workflows.py`
- `src/tensor_grep/cli/mcp_server.py`
- `scripts/smoke_test_pypi_artifacts.py`
- `tests/unit/test_cli_modes.py`
- `tests/unit/test_mcp_server.py`
- `tests/unit/test_smoke_test_pypi_artifacts.py`
- `README.md`
- `docs/PAPER.md`
- `docs/installation.md`
- `CHANGELOG.md`

**Purpose:** Post-release smoke testing showed the PyPI wheel path could report native Rust features but fail `tg run --rewrite` plan/apply when no standalone native `tg` binary was installed.

TDD status:

- [x] Added a failing CLI test proving `tg run --rewrite` without `--apply` emits a rewrite plan instead of falling into AST search.
- [x] Added a failing MCP/PyPI-path test proving rewrite apply works through embedded Rust when `resolve_native_tg_binary()` is unavailable.
- [x] Added a failing Windows console compatibility test for `$$ARGS` variadic metavars.
- [x] Strengthened the PyPI artifact smoke unit test so release validation checks rewrite plan and apply.
- [x] Exposed Rust AST rewrite plan/apply via the PyO3 extension.
- [x] Routed Python CLI and MCP plan/apply through embedded Rust when no standalone native binary exists.
- [x] Documented the PyPI/MCP boundary and release-smoke fix.

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

CI note: current GitHub Actions does not run `run_ast_rewrite_benchmarks.py`, so attach or quote the local artifact result in the release report before making any public speed claim.

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
