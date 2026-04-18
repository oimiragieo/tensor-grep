# Post-v1.3 Safe Release Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Safely land the first post-`v1.3.0` patch only if one benchmark-governed `perf:` or additive `fix:` slice survives the full repo gates.

**Architecture:** Freeze the already-validated minor first. Then run one no-release benchmark-governance slice, followed by one performance slice at a time (`-c` first, `--cpu` second). If neither candidate survives the governed benchmarks, stop and ship no patch.

**Tech Stack:** Python CLI, Rust native front door, benchmark harnesses, semantic-release, validator-backed tests

---

## File Map

- `src/tensor_grep/perf_guard.py`: benchmark comparison helpers, provenance utilities, comparator-drift policy
- `benchmarks/check_regression.py`: benchmark gate CLI and failure semantics
- `benchmarks/run_benchmarks.py`: cold-path benchmark harness and artifact metadata
- `benchmarks/run_tool_comparison_benchmarks.py`: informational cross-tool comparison for `--cpu`
- `benchmarks/run_native_cpu_benchmarks.py`: governed native CPU benchmark cases
- `src/tensor_grep/cli/main.py`: search control-plane routing and passthrough decisions
- `src/tensor_grep/backends/ripgrep_backend.py`: ripgrep backend command wiring and count behavior
- `src/tensor_grep/core/pipeline.py`: backend selection and CPU-mode routing
- `tests/unit/test_perf_guard.py`: perf-guard unit tests
- `tests/unit/test_benchmark_scripts.py`: benchmark script contract tests
- `tests/unit/test_benchmark_artifacts_schema.py`: benchmark artifact schema contract
- `tests/unit/test_cli_modes.py`: CLI correctness guards for any search-path changes
- `tests/e2e/test_output_golden_contract.py`: user-visible output contract tests
- `docs/PAPER.md`: accepted or rejected performance history
- `docs/benchmarks.md`: public benchmark methodology, updated only after accepted artifacts

### Task 1: Freeze `v1.3.0` And Start A Clean Post-Release Worktree

**Files:**
- No file changes expected

- [ ] **Step 1: Verify the current release-ready branch stays isolated**

Run:

```powershell
git -C C:\Users\oimir\.config\superpowers\worktrees\tensor-grep\safe-release-clean status --short --branch
git -C C:\Users\oimir\.config\superpowers\worktrees\tensor-grep\safe-release-clean log --oneline -1
```

Expected:

- clean status
- head still points at the validated release-ready commit

- [ ] **Step 2: Ship `v1.3.0` before starting new remediation**

Use the release-bearing PR title:

```text
feat: ship AST JSON parity and CLI contract fixes
```

Merge rule:

- `Squash and merge`
- semantic-release owns the tag
- no manual tag creation

- [ ] **Step 3: Fetch the resulting `origin/main`**

Run:

```powershell
git fetch origin
git checkout main
git pull --ff-only origin main
```

Expected:

- local `main` matches the merge result that produced `v1.3.0`

- [ ] **Step 4: Create a fresh replay worktree for the patch program**

Run:

```powershell
git worktree add C:\Users\oimir\.config\superpowers\worktrees\tensor-grep\post-v1-3-safe-release -b post-v1-3-safe-release origin/main
```

Expected:

- new clean worktree rooted at the shipped `origin/main`

- [ ] **Step 5: Commit nothing in this task**

This task is environment setup only.

### Task 2: Enforce Comparator Drift Governance Before Any Performance Patch

**Files:**
- Modify: `src/tensor_grep/perf_guard.py`
- Modify: `benchmarks/check_regression.py`
- Modify: `tests/unit/test_perf_guard.py`
- Modify: `tests/unit/test_benchmark_scripts.py`

- [ ] **Step 1: Write the failing unit test for comparator drift**

Add to `tests/unit/test_perf_guard.py`:

```python
def test_detect_comparator_drift_reports_rg_time_s_changes():
    baseline = {"rows": [{"name": "x", "rg_time_s": 1.0}]}
    current = {"rows": [{"name": "x", "rg_time_s": 1.3}]}

    drift = detect_comparator_drift(
        baseline=baseline,
        current=current,
        comparator_key="rg_time_s",
        max_regression_pct=10.0,
    )

    assert len(drift) == 1
    assert "rg_time_s" in drift[0]
```

- [ ] **Step 2: Write the failing script test for gate behavior**

Add a narrow script test in `tests/unit/test_benchmark_scripts.py`:

```python
def test_check_regression_reports_comparator_drift_without_masking_tg_status(...):
    baseline = {...}
    current = {...}
    exit_code, stdout = run_check_regression(...)
    assert exit_code == 0
    assert "Comparator drift detected" in stdout
    assert "No benchmark regressions detected" in stdout
```

- [ ] **Step 3: Run the red phase**

Run:

```powershell
uv run pytest tests/unit/test_perf_guard.py -k comparator_drift -q
uv run pytest tests/unit/test_benchmark_scripts.py -k comparator_drift -q
```

Expected:

- both fail because drift helpers or reporting are incomplete on the shipped line

- [ ] **Step 4: Implement the minimal drift helper**

Add to `src/tensor_grep/perf_guard.py`:

```python
def detect_comparator_drift(
    baseline: dict[str, Any],
    current: dict[str, Any],
    *,
    comparator_key: str = "rg_time_s",
    max_regression_pct: float = 10.0,
    min_baseline_time_s: float = 0.2,
) -> list[str]: ...
```

Keep this helper read-only. It should annotate drift, not silently rewrite release decisions.

- [ ] **Step 5: Thread the helper into the CLI gate**

Update `benchmarks/check_regression.py` so it:

```python
drift = detect_comparator_drift(
    baseline=baseline,
    current=current,
    comparator_key="rg_time_s",
    max_regression_pct=args.max_regression_pct,
    min_baseline_time_s=args.min_baseline_time_s,
)
if drift:
    print("Comparator drift detected:")
    for msg in drift:
        print(f"- {msg}")
```

Do not fail the build yet in this task. This task is governance visibility first.

- [ ] **Step 6: Run the green phase**

Run:

```powershell
uv run pytest tests/unit/test_perf_guard.py -k comparator_drift -q
uv run pytest tests/unit/test_benchmark_scripts.py -k comparator_drift -q
uv run python benchmarks/check_regression.py --baseline auto --current artifacts/bench_run_benchmarks.release_refresh_candidate_rerun.json
```

Expected:

- unit tests pass
- the script prints drift diagnostics without masking the `tg` regression result

- [ ] **Step 7: Commit the governance slice**

```powershell
git add src/tensor_grep/perf_guard.py benchmarks/check_regression.py tests/unit/test_perf_guard.py tests/unit/test_benchmark_scripts.py
git commit -m "test: enforce comparator drift benchmark governance"
```

### Task 3: Reproduce The Live `-c` / `--count-matches` Line

**Files:**
- Modify no code in this task
- Modify only if the historical record is wrong: `docs/PAPER.md`

- [ ] **Step 1: Run the governed cold-path benchmark**

Run:

```powershell
python benchmarks/run_benchmarks.py --output artifacts/bench_run_benchmarks.post_v13_count_probe.json
python benchmarks/check_regression.py --baseline auto --current artifacts/bench_run_benchmarks.post_v13_count_probe.json
```

- [ ] **Step 2: Extract the count scenario row**

Run:

```powershell
uv run python -c "import json; rows=json.load(open('artifacts/bench_run_benchmarks.post_v13_count_probe.json', encoding='utf-8'))['rows']; row=next(r for r in rows if r['name']=='5. Count Matches'); print(row)"
```

Expected:

- one row for `5. Count Matches`
- concrete `tg_time_s`, `rg_time_s`, and `ratio_vs_rg`

- [ ] **Step 3: Decide whether a count candidate is still justified**

Decision rule:

- if the row is already inside the accepted threshold and no regression is reported, mark the count lane closed and do not change code
- if the row is still the worst governed slowdown, proceed to Task 4

- [ ] **Step 4: Commit only if `docs/PAPER.md` needs correction**

```powershell
git add docs/PAPER.md
git commit -m "docs: refresh recorded count-mode benchmark evidence"
```

### Task 4: Land At Most One Narrow `-c` Candidate

**Files:**
- Modify: `tests/unit/test_cli_modes.py`
- Modify: `src/tensor_grep/cli/main.py`
- Modify if the backend path proves culpable: `src/tensor_grep/backends/ripgrep_backend.py`
- Modify docs only after accepted or rejected measurement: `docs/PAPER.md`

- [ ] **Step 1: Add a correctness guard before optimizing**

Add a narrow test to `tests/unit/test_cli_modes.py`:

```python
def test_count_mode_passthrough_preserves_match_count_and_exit_code(monkeypatch):
    fake_backend = _FakeRipgrepBackend()
    ...
    result = runner.invoke(app, ["search", "-c", "ERROR", "."])
    assert result.exit_code == 0
    assert fake_backend.called is True
```

- [ ] **Step 2: Run the red phase**

Run:

```powershell
uv run pytest tests/unit/test_cli_modes.py -k count_mode_passthrough -q
```

- [ ] **Step 3: Implement one candidate only**

If the reproduced profile points to control-plane overhead, try the smallest fast lane in `src/tensor_grep/cli/main.py`:

```python
if can_passthrough_rg and config.count and not stats:
    with nvtx_range("search.passthrough_rg", color="green"):
        exit_code = rg_backend.search_passthrough(paths_to_search, pattern, config=config)
    sys.exit(0 if exit_code == 0 else 1)
```

If that fast lane already exists on the shipped line, pick one different candidate and only one different candidate, such as avoiding redundant candidate-file enumeration before the passthrough call.

- [ ] **Step 4: Run the green phase**

Run:

```powershell
uv run pytest tests/unit/test_cli_modes.py -k count_mode_passthrough -q
uv run pytest tests/e2e/test_output_golden_contract.py -k "count or count_matches" -q
```

- [ ] **Step 5: Measure the candidate**

Run:

```powershell
python benchmarks/run_benchmarks.py --output artifacts/bench_run_benchmarks.count_candidate.json
python benchmarks/check_regression.py --baseline auto --current artifacts/bench_run_benchmarks.count_candidate.json
```

- [ ] **Step 6: Accept or reject**

Acceptance rule:

- keep the candidate only if the `5. Count Matches` line improves and the governed benchmark gate stays green
- otherwise revert the candidate and record the rejection in `docs/PAPER.md`

- [ ] **Step 7: Commit only if accepted**

```powershell
git add src/tensor_grep/cli/main.py src/tensor_grep/backends/ripgrep_backend.py tests/unit/test_cli_modes.py tests/e2e/test_output_golden_contract.py docs/PAPER.md
git commit -m "perf(search): reduce count-mode control-plane overhead"
```

### Task 5: Reproduce The Live `--cpu` Line

**Files:**
- Modify no code in this task

- [ ] **Step 1: Run the informational cross-tool comparison**

Run:

```powershell
python benchmarks/run_tool_comparison_benchmarks.py --output artifacts/bench_tool_comparison.post_v13.json
```

- [ ] **Step 2: Run the governed native CPU benchmark**

Run:

```powershell
python benchmarks/run_native_cpu_benchmarks.py --output artifacts/bench_run_native_cpu.post_v13.json
```

- [ ] **Step 3: Identify the worst governed CPU row**

Run:

```powershell
uv run python -c "import json; rows=json.load(open('artifacts/bench_run_native_cpu.post_v13.json', encoding='utf-8'))['rows']; worst=max(rows, key=lambda r: r.get('ratio_vs_rg') or 0); print(worst)"
```

- [ ] **Step 4: Decide whether a CPU candidate is justified**

Decision rule:

- if every governed row is already inside threshold, stop this lane
- otherwise continue to Task 6

### Task 6: Land At Most One Narrow `--cpu` Candidate

**Files:**
- Modify: `tests/unit/test_cli_modes.py`
- Modify one of: `src/tensor_grep/cli/main.py`, `src/tensor_grep/core/pipeline.py`
- Modify docs only after accepted or rejected measurement: `docs/PAPER.md`

- [ ] **Step 1: Add the narrowest correctness guard that matches the suspected path**

Example in `tests/unit/test_cli_modes.py`:

```python
def test_cpu_mode_preserves_explicit_backend_selection(monkeypatch):
    ...
    result = runner.invoke(app, ["search", "--cpu", "ERROR", "."])
    assert result.exit_code == 0
    assert _LAST_PIPELINE_CONFIG.force_cpu is True
```

- [ ] **Step 2: Run the red phase**

Run:

```powershell
uv run pytest tests/unit/test_cli_modes.py -k cpu_mode_preserves_explicit_backend_selection -q
```

- [ ] **Step 3: Implement one routing candidate only**

If the reproduced overhead points to repeated preprocessing, try one narrow change such as preserving a direct CPU-safe path before distributed or GPU-aware planning is built:

```python
if cpu and selected_backend_name == "RipgrepBackend" and not stats:
    ...
```

If the evidence instead points to pipeline setup, move exactly one setup cost out of the hot path in `src/tensor_grep/core/pipeline.py`.

- [ ] **Step 4: Run the green phase**

Run:

```powershell
uv run pytest tests/unit/test_cli_modes.py -k cpu_mode_preserves_explicit_backend_selection -q
uv run pytest tests/e2e/test_output_golden_contract.py -k cpu_ -q
```

- [ ] **Step 5: Measure the candidate**

Run:

```powershell
python benchmarks/run_native_cpu_benchmarks.py --output artifacts/bench_run_native_cpu.cpu_candidate.json
python benchmarks/run_tool_comparison_benchmarks.py --output artifacts/bench_tool_comparison.cpu_candidate.json
```

- [ ] **Step 6: Accept or reject**

Acceptance rule:

- keep the candidate only if the governed CPU benchmark improves on the targeted row and no other governed row regresses
- otherwise revert it and document the rejected attempt in `docs/PAPER.md`

- [ ] **Step 7: Commit only if accepted**

```powershell
git add src/tensor_grep/cli/main.py src/tensor_grep/core/pipeline.py tests/unit/test_cli_modes.py tests/e2e/test_output_golden_contract.py docs/PAPER.md
git commit -m "perf(search): cut cpu mode overhead on governed workloads"
```

### Task 7: Release Decision, Docs, And Final Gates

**Files:**
- Modify only if an accepted release-bearing slice exists: `docs/PAPER.md`, `docs/benchmarks.md`

- [ ] **Step 1: Decide whether this branch is release-bearing**

Decision matrix:

- only `test:` / `build:` / `docs:` commits landed => no package release
- one accepted `perf:` or additive `fix:` commit landed => patch release
- any breaking schema or public CLI change appeared => stop and defer to a future minor

- [ ] **Step 2: Update docs only for accepted artifact-backed results**

If and only if a candidate was accepted:

```markdown
- update `docs/PAPER.md` with the accepted result or rejected attempts
- update `docs/benchmarks.md` only if the accepted benchmark line changed public methodology or accepted numbers
```

Do not touch `README.md` benchmark claims unless the accepted artifact genuinely changes them.

- [ ] **Step 3: Run the repo gates**

Run:

```powershell
uv run ruff check .
uv run mypy src/tensor_grep
uv run pytest -q
uv run python scripts/validate_release_assets.py
```

- [ ] **Step 4: Re-run the exact accepted benchmark surfaces**

Run only what matches the landed slice:

```powershell
python benchmarks/run_benchmarks.py --output artifacts/bench_run_benchmarks.release_candidate.json
python benchmarks/check_regression.py --baseline auto --current artifacts/bench_run_benchmarks.release_candidate.json
python benchmarks/run_native_cpu_benchmarks.py --output artifacts/bench_run_native_cpu.release_candidate.json
```

- [ ] **Step 5: Commit docs separately if needed**

```powershell
git add docs/PAPER.md docs/benchmarks.md
git commit -m "docs: record accepted post-v1.3 benchmark results"
```

- [ ] **Step 6: Open the release PR only if the branch is truly release-bearing**

If the accepted slice is count-mode:

```text
perf: reduce count-mode control-plane overhead
```

If the accepted slice is CPU-mode:

```text
perf: cut cpu mode overhead on governed workloads
```

Merge rule:

- `Squash and merge`
- semantic-release owns the tag
- no manual release tagging
