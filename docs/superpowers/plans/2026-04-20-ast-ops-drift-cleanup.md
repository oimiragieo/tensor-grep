# Contract Repair And AST Drift Cleanup Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Repair the next two live repo contract bugs after reader reintegration, then remove lower-risk AST/operator drift without changing accepted backend-selection strategy.

**Architecture:** This patch is correctness-only. It should make the documented `TG_FORCE_CPU` env override behave like explicit `--cpu`, fix AST project-data cache freshness for traversed tree directories, and then remove stale localized branches and operator drift. Dormant `DStorageReader` / `KvikIOReader` activation is explicitly out of scope.

**Tech Stack:** Python, Typer CLI, pytest, repo benchmark harnesses, docs governance tests

---

### Task 1: Pin `TG_FORCE_CPU` As A Real Runtime Contract

**Files:**
- Modify: `tests/unit/test_cli_bootstrap.py`
- Modify: `tests/unit/test_cli_modes.py`
- Modify: `src/tensor_grep/cli/bootstrap.py`
- Modify: `src/tensor_grep/cli/main.py`
- Test: `tests/unit/test_cli_bootstrap.py`
- Test: `tests/unit/test_cli_modes.py`

- [ ] **Step 1: Write the failing tests**

Add focused tests proving that `TG_FORCE_CPU=1` behaves like explicit `--cpu` in both entry paths:

- bootstrap/native-launch path
- direct Typer CLI path

Prefer extending the existing CPU-delegation tests instead of creating a new test surface.

- [ ] **Step 2: Run the narrow tests to verify they fail**

Run:

- `uv run pytest tests/unit/test_cli_bootstrap.py -k force_cpu -q`
- `uv run pytest tests/unit/test_cli_modes.py -k force_cpu -q`

Expected: FAIL for the env-driven path because the documented env var is not currently consumed.

- [ ] **Step 3: Write the minimal implementation**

Introduce a single truthy-env read for `TG_FORCE_CPU` and route it through the same effective behavior as explicit `--cpu`.

Constraints:

- do not create a second CPU-routing policy
- do not regress the existing `--cpu` CLI flag
- keep bootstrap and Typer behavior aligned

- [ ] **Step 4: Re-run the narrow tests**

Run:

- `uv run pytest tests/unit/test_cli_bootstrap.py -k force_cpu -q`
- `uv run pytest tests/unit/test_cli_modes.py -k force_cpu -q`

Expected: PASS

- [ ] **Step 5: Commit**

Commit message: `fix: honor documented TG_FORCE_CPU runtime override`

### Task 2: Repair AST Project-Data Cache Invalidation

**Files:**
- Modify: `tests/unit/test_ast_workflows.py`
- Modify: `src/tensor_grep/cli/ast_workflows.py`
- Test: `tests/unit/test_ast_workflows.py`

- [ ] **Step 1: Write the failing cache test**

Add or extend a focused test proving that traversed tree directories participate in AST project-data freshness checks. The repro should fail when nested-tree structure changes but cached `project_data_v6.json` is still reused.

- [ ] **Step 2: Run the narrow AST cache test to verify it fails**

Run:

- `uv run pytest tests/unit/test_ast_workflows.py -k ast_project_data_cache_invalidation -q`

Expected: FAIL once the reproduction is tightened to cover traversed-directory freshness instead of only file-list changes.

- [ ] **Step 3: Write the minimal implementation**

Fix the cache key/input path so `_collect_candidate_files(...)` and `_load_ast_project_data(...)` preserve the traversed tree-directory set and include it in freshness checks.

Constraints:

- preserve the public cache-file shape unless the failing test proves it must change
- do not refactor AST backend selection in this patch
- keep the edit local to project-data collection and invalidation logic

- [ ] **Step 4: Re-run the AST workflow tests**

Run:

- `uv run pytest tests/unit/test_ast_workflows.py -k ast_project_data_cache_invalidation -q`
- `uv run pytest tests/unit/test_ast_workflows.py -k scan_command -q`

Expected: PASS

- [ ] **Step 5: Commit**

Commit message: `fix: restore ast project cache freshness for tree dirs`

### Task 3: Align Operator Diagnostics And Experimental Docs

**Files:**
- Modify: `tests/unit/test_cli_modes.py`
- Modify: `tests/unit/test_enterprise_docs_governance.py`
- Modify: `src/tensor_grep/cli/main.py`
- Modify: `docs/EXPERIMENTAL.md`
- Test: `tests/unit/test_cli_modes.py`
- Test: `tests/unit/test_enterprise_docs_governance.py`

- [ ] **Step 1: Write the failing tests**

Add narrow assertions that:

- `doctor --json` includes the live experimental/runtime env surface needed by operators:
  - `TG_FORCE_CPU`
  - `TG_RUST_EARLY_POSITIONAL_RG`
  - `TG_RESIDENT_AST`
- `docs/EXPERIMENTAL.md` mentions `TG_RUST_EARLY_POSITIONAL_RG`, `TG_FORCE_CPU`, and `TG_RESIDENT_AST`

- [ ] **Step 2: Run the focused tests to verify they fail**

Run:

- `uv run pytest tests/unit/test_cli_modes.py -k doctor_json_includes_runtime_session_and_lsp -q`
- `uv run pytest tests/unit/test_enterprise_docs_governance.py -k experimental -q`

Expected: FAIL where doctor/docs are still incomplete.

- [ ] **Step 3: Write the minimal implementation**

Update the doctor env allowlist and the experimental-doc section without adding unrelated env flags.

- [ ] **Step 4: Re-run the focused tests**

Run:

- `uv run pytest tests/unit/test_cli_modes.py -k doctor_json_includes_runtime_session_and_lsp -q`
- `uv run pytest tests/unit/test_enterprise_docs_governance.py -k experimental -q`

Expected: PASS

- [ ] **Step 5: Commit**

Commit message: `fix: align operator diagnostics with experimental runtime flags`

### Task 4: Remove Dead AST Locals And Stale Narration

**Files:**
- Modify: `src/tensor_grep/cli/ast_workflows.py`
- Modify: `src/tensor_grep/io/directory_scanner.py`
- Modify: `benchmarks/run_ast_workflow_benchmarks.py`
- Test: `tests/unit/test_ast_workflows.py`
- Test: `tests/unit/test_directory_scanner.py`
- Test: `tests/unit/test_benchmark_scripts.py`

- [ ] **Step 1: Run the existing guards first**

Run:

- `uv run pytest tests/unit/test_ast_workflows.py -k scan_command -q`
- `uv run pytest tests/unit/test_directory_scanner.py -q`
- `uv run pytest tests/unit/test_benchmark_scripts.py -k run_ast_workflow_benchmarks -q`

Expected: PASS on baseline

- [ ] **Step 2: Make the smallest cleanup edit**

Target only:

- unused `DirectoryScanner(cfg)` local in `scan_command()`
- stale `_describe_ast_backend_mode()` copy
- redundant `and not base_path.is_file()` clause
- stale explanatory comments in `run_ast_workflow_benchmarks.py`

Do not change benchmark command behavior unless a failing test proves the behavior itself is wrong.

- [ ] **Step 3: Re-run the focused suites**

Run:

- `uv run pytest tests/unit/test_ast_workflows.py -k scan_command -q`
- `uv run pytest tests/unit/test_directory_scanner.py -q`
- `uv run pytest tests/unit/test_benchmark_scripts.py -k run_ast_workflow_benchmarks -q`

Expected: PASS

- [ ] **Step 4: Commit**

Commit message: `fix: remove stale ast workflow drift`

### Task 5: Full Verification

**Files:**
- Verify only

- [ ] **Step 1: Run lint**

Run: `uv run ruff check .`
Expected: PASS

- [ ] **Step 2: Run typing**

Run: `uv run mypy src/tensor_grep`
Expected: PASS

- [ ] **Step 3: Run full tests**

Run: `uv run pytest -q`
Expected: PASS

- [ ] **Step 4: Run the relevant benchmark harness**

Run: `uv run python benchmarks/run_ast_workflow_benchmarks.py --output artifacts/bench_run_ast_workflow_benchmarks.json`

Expected: successful artifact generation. This patch is correctness-only, so do not claim a speedup without an accepted before/after comparison.

- [ ] **Step 5: Commit**

Commit message: `fix: repair ast contracts and operator drift`
