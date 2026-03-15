---
name: backend-worker
description: Handles Python-side work: sidecar IPC receiver, compatibility test suites, benchmark regression gates, and Python-layer changes for the Rust control plane migration.
---

# backend-worker

NOTE: Startup and cleanup are handled by `worker-base`. This skill defines the WORK PROCEDURE.

## When to Use This Skill

Use this worker for:
- Python sidecar protocol (`src/tensor_grep/sidecar.py`)
- CLI parity test suite (`benchmarks/run_compat_checks.py`)
- Benchmark regression gates and baseline management
- Minor Python-layer compatibility fixes when Rust migration breaks Python tests
- JSON output schema validation files

## Dirty Primary Checkout Strategy

If `C:\dev\projects\tensor-grep` has unrelated uncommitted changes on files you need to modify, **do NOT work there**. Create a clean isolated git worktree:

```powershell
git -C "C:\dev\projects\tensor-grep" worktree add "C:\dev\projects\tensor-grep-<feature-id>" HEAD
```

Work there, commit, then merge back:

```powershell
git -C "C:\dev\projects\tensor-grep" merge <branch> --no-edit
```

If merge fails due to conflicts:

```powershell
git -C "C:\dev\projects\tensor-grep" stash
git -C "C:\dev\projects\tensor-grep" merge <branch> --no-edit
git -C "C:\dev\projects\tensor-grep" stash pop
```

## Windows-Specific Notes

- **Shell**: PowerShell is the default shell. Use `$env:PYTHONPATH = '...\src'` syntax.
- **init.sh not executable**: Run `uv pip install -e ".[dev,ast,nlp]"` instead.
- **PYTHONPATH in fresh worktrees**: `$env:PYTHONPATH = '<worktree>\src'; uv run pytest -q`.
- **Cargo path**: `C:\Users\oimir\.cargo\bin\cargo.exe` (may not be on PATH). Use full path when needed.
- **Benchmark rg.zip**: If `rg` is not on PATH, `run_benchmarks.py` now auto-extracts `benchmarks/rg.zip`.

## Work Procedure

1. **Test-Driven Development FIRST**: Write a failing test in `tests/` that exposes the specific defect or proves the new behavior contract. Run the test and verify it fails.

2. Implement the Python logic to make the test pass. Follow:
   - Sidecar protocol: JSON over stdin/stdout, fields: `{command, args, payload}` → `{status, result, error}`.
   - Compat checks: compare sorted line-sets of `tg.exe` vs `rg` for all 8 scenarios.
   - Schema files: use JSON Schema Draft-7 format in `tests/schemas/`.

3. Run local Python gates:
   ```powershell
   uv run ruff check .
   uv run mypy src/tensor_grep
   $env:PYTHONPATH = 'src'; uv run pytest -q
   ```

4. Run relevant benchmarks if touching performance paths:
   ```powershell
   python benchmarks/run_benchmarks.py --output artifacts/bench.json
   python benchmarks/check_regression.py --baseline auto --current artifacts/bench.json
   ```

## Final Step: Merge to Main

After all local gates pass and work is committed, merge back:

```powershell
git -C "C:\dev\projects\tensor-grep" merge <feature-branch> --no-edit
```

## Example Handoff

```json
{
  "salientSummary": "Implemented CLI parity test suite in benchmarks/run_compat_checks.py. All 8 scenarios show zero divergent lines between tg.exe and rg. Added tests/schemas/tg_output.schema.json for routing metadata. pytest -q: 488 passed. compat_report.json shows 8/8 PASS.",
  "whatWasImplemented": "Created benchmarks/run_compat_checks.py which runs all 8 benchmark scenarios for both tg.exe and rg, sorts output, and diffs. Also validates --json flag emits routing_backend/routing_reason/sidecar_used fields. Committed tests/schemas/tg_output.schema.json.",
  "whatWasLeftUndone": "",
  "verification": {
    "commandsRun": [
      { "command": "python benchmarks/run_compat_checks.py", "exitCode": 0, "observation": "8/8 PASS, compat_report.json written" },
      { "command": "uv run pytest -q", "exitCode": 0, "observation": "488 passed, 14 skipped" }
    ]
  },
  "tests": {
    "added": [{ "file": "benchmarks/run_compat_checks.py", "cases": [{ "name": "all 8 scenarios parity", "verifies": "tg.exe output matches rg" }] }],
    "updated": []
  },
  "discoveredIssues": []
}
```

## When to Return to Orchestrator

- Benchmark degraded after Rust migration and you cannot determine if it is a Python-layer or Rust-layer issue.
- Sidecar IPC protocol has a fundamental design conflict with how GPU paths work.
- The Python test suite has failures caused by Rust-side behavior changes outside this feature's scope.
