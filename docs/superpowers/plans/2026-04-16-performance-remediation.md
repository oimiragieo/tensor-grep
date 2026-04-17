# Performance Remediation Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Investigate and remediate the known `-c`, `--cpu`, and AST workflow performance regressions using accepted benchmark artifacts and without mixing correctness changes into the same slices.

**Architecture:** Every performance topic follows the same loop: establish the current accepted comparator and artifact, reproduce the regression, try one narrow candidate, measure it with the governing harness, and either land it with docs or reject it and record the failure in `docs/PAPER.md`. No unmeasured speedup claims.

**Tech Stack:** Benchmark harnesses, native and Python launchers, Rust front door, Python control plane, benchmark governance docs

---

### Task 1: Reproduce the Current `-c` Regression Line

**Files:**
- Modify only if historical recording must be corrected: `docs/PAPER.md`
- Modify no code in this task

- [ ] **Step 1: Run the cold-path benchmark**

Run: `python benchmarks/run_benchmarks.py --output artifacts/bench_run_benchmarks.json`

- [ ] **Step 2: Run the comparator gate**

Run: `python benchmarks/check_regression.py --baseline auto --current artifacts/bench_run_benchmarks.json`

If the local Python version blocks baseline comparison, record that honestly and keep the artifact anyway.

- [ ] **Step 3: Extract the `Count Matches` row**

```python
row = next(r for r in rows if r["scenario"] == "Count Matches")
print(row["tg_time_s"], row["rg_time_s"], row["ratio_vs_rg"])
```

- [ ] **Step 4: Commit only if the historical docs need correction**

```bash
git add docs/PAPER.md
git commit -m "docs: refresh recorded count-mode regression evidence"
```

### Task 2: Investigate One Narrow `-c` Candidate

**Files:**
- Modify: the smallest code surface implicated by the reproduced regression
- Modify: the narrowest relevant tests
- Modify docs only after accepted or rejected measurement: `docs/PAPER.md`

- [ ] **Step 1: Add or tighten a correctness guard if the candidate changes behavior**

```python
def test_count_matches_contract_still_matches_ripgrep(...): ...
```

- [ ] **Step 2: Implement one candidate only**

Examples:

```python
# cache resolution
_cached_runtime_binary = ...
```

or

```rust
// count path fast lane
if config.count && plain_shape { ... }
```

Do not stack multiple candidates in one patch.

- [ ] **Step 3: Run narrow validation**

Run the exact tests touched by the candidate first.

- [ ] **Step 4: Measure the candidate**

Run: `python benchmarks/run_benchmarks.py --output artifacts/bench_run_benchmarks_candidate.json`

Run: `python benchmarks/check_regression.py --baseline auto --current artifacts/bench_run_benchmarks_candidate.json`

- [ ] **Step 5: Accept or reject**

If the candidate improves the target row without regressing the accepted gate, keep it.

If it regresses or fails the gate, revert it and record the rejected attempt in `docs/PAPER.md`.

- [ ] **Step 6: Commit**

```bash
git add benchmarks/ src/tensor_grep/ rust_core/ tests/ docs/PAPER.md
git commit -m "perf(search): reduce count mode control-plane overhead"
```

### Task 3: Reproduce and Investigate `--cpu` Overhead

**Files:**
- Modify only after a measured candidate exists

- [ ] **Step 1: Establish the current `--cpu` line**

Run: `python benchmarks/run_tool_comparison_benchmarks.py --output artifacts/bench_tool_comparison.json`

Run: `python benchmarks/run_native_cpu_benchmarks.py --output artifacts/bench_native_cpu.json`

- [ ] **Step 2: Identify the worst row**

Use the artifacts to identify which workload shape still makes `--cpu` unacceptably slower than default or `rg`.

- [ ] **Step 3: Build one candidate for the identified row**

Possible areas:

```python
# reduce repeated preprocessing or backend setup
```

or

```rust
// preserve a native fast path for safe cpu-only shapes
```

- [ ] **Step 4: Run narrow validation and re-measure**

Run the relevant tests first, then rerun only the governing CPU benchmarks.

- [ ] **Step 5: Accept or reject with docs discipline**

Only update `README.md` or `docs/PAPER.md` if the measured line actually changed.

### Task 4: Reproduce and Investigate AST Workflow Overhead

**Files:**
- Modify only after the reproduced gap is narrowed to a specific startup or wrapper cost

- [ ] **Step 1: Establish the current AST lines**

Run: `python benchmarks/run_ast_benchmarks.py --output artifacts/bench_run_ast_benchmarks.json`

Run: `python benchmarks/run_ast_workflow_benchmarks.py --output artifacts/bench_run_ast_workflow_benchmarks.json`

Run: `python benchmarks/run_ast_rewrite_benchmarks.py --output artifacts/bench_run_ast_rewrite_benchmarks.json`

- [ ] **Step 2: Separate direct match cost from startup/orchestration cost**

If the slowdown is in workflow startup, keep it out of backend matching code. If it is in direct matching, keep it out of scan/test orchestration.

- [ ] **Step 3: Implement one narrow candidate**

```rust
// startup cache or wrapper reuse
```

or

```python
# avoid redundant wrapper normalization
```

- [ ] **Step 4: Run narrow validation and re-measure**

Run AST-specific tests first, then the exact governing AST benchmarks.

- [ ] **Step 5: Accept or reject**

Land only accepted results. Rejected attempts belong in `docs/PAPER.md`, not in the codebase.

### Task 5: Final Performance Documentation Pass

**Files:**
- Modify if and only if there are accepted artifacts: `docs/PAPER.md`, `docs/benchmarks.md`, `README.md`

- [ ] **Step 1: Check every claimed speedup against a fresh artifact**

Do not reuse stale benchmark numbers from earlier runs.

- [ ] **Step 2: Update the docs by workload class**

Keep the text honest:

- cold text search claims belong to the cold-path artifact
- CPU mode claims belong to CPU-specific artifacts
- AST claims belong to AST benchmark artifacts

- [ ] **Step 3: Run validator-backed doc checks**

Run: `uv run pytest tests/unit/test_release_assets_validation.py -q`

Run: `python scripts/validate_release_assets.py`

- [ ] **Step 4: Commit docs separately**

```bash
git add docs/PAPER.md docs/benchmarks.md README.md
git commit -m "docs: record accepted performance remediation results"
```

### Task 6: Final Full Validation for the Performance Workstream

**Files:**
- No file changes expected

- [ ] **Step 1: Run the repo gates**

Run: `uv run ruff check .`

Run: `uv run mypy src/tensor_grep`

Run: `uv run pytest -q`

- [ ] **Step 2: Re-run the exact accepted benchmark surfaces**

Run only the benchmark commands corresponding to the performance slices that actually landed.
