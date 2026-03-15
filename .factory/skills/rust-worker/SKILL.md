---
name: rust-worker
description: Handles all Rust-side work: control plane, PyO3 isolation, ast-grep-core embedding, index subsystem, rewrite substrate, and benchmark gates.
---

# rust-worker

NOTE: Startup and cleanup are handled by `worker-base`. This skill defines the WORK PROCEDURE.

## When to Use This Skill

Use this worker for:
- Rust control plane changes (`rust_core/src/main.rs`, `backend_gpu.rs`, PyO3 configuration)
- ast-grep-core embedding and AST backend implementation (`backend_ast.rs`)
- Bigram index subsystem implementation
- Rewrite pipeline and patch output
- Benchmark gates (cargo hyperfine, cold-start measurement)

## Dirty Primary Checkout Strategy

If `C:\dev\projects\tensor-grep` has unrelated uncommitted changes on files you need to modify, **do NOT work there**. Create a clean isolated git worktree:

```powershell
git -C "C:\dev\projects\tensor-grep" worktree add "C:\dev\projects\tensor-grep-<feature-id>" HEAD
```

Work there, commit, then merge back:

```powershell
git -C "C:\dev\projects\tensor-grep" merge <branch> --no-edit
```

If merge fails due to conflicts, stash first:

```powershell
git -C "C:\dev\projects\tensor-grep" stash
git -C "C:\dev\projects\tensor-grep" merge <branch> --no-edit
git -C "C:\dev\projects\tensor-grep" stash pop
```

## Windows-Specific Notes

- **Shell**: PowerShell is the default shell. Use `$env:PYTHONPATH = '...\src'` syntax.
- **Cargo path**: `C:\Users\oimir\.cargo\bin\cargo.exe` (may not be on PATH).
- **init.sh not executable**: Run `uv pip install -e ".[dev,ast,nlp]"` instead.
- **PYTHONPATH in fresh worktrees**: `$env:PYTHONPATH = '<worktree>\src'; uv run pytest -q`.
- **Cargo for cyBERT/NLP changes**: Use `python benchmarks/run_gpu_benchmarks.py` (requires Triton; may skip if not running).

## Work Procedure

1. **Test-Driven Development FIRST**: Write a failing Rust test (in `rust_core/tests/` or inline `#[cfg(test)]`) or a failing Python integration test that asserts the new behavior. Run it and verify it fails. Only then implement.

2. Implement the Rust logic to make the test pass. Follow these rules:
   - MSRV Rust 1.79 — no newer features.
   - `pyo3 auto-initialize` is REMOVED (Milestone 1+). Never add it back.
   - Tree-sitter parsers are NOT `Sync` — use `thread_local!` or per-task allocation.
   - ast-grep-core API: `AstGrep::new(source, lang)`, `Pattern::new(pattern, lang)`, `root.find_all(pattern)`.
   - Index data structures: `Vec<u64>` for bit vectors, `HashMap<[u8;2], usize>` for bigram→bit mapping.

3. Run local Rust gates:
   ```powershell
   Set-Location "C:\dev\projects\tensor-grep\rust_core"
   & "C:\Users\oimir\.cargo\bin\cargo.exe" test
   & "C:\Users\oimir\.cargo\bin\cargo.exe" clippy -- -D warnings
   ```

4. Run local Python gates (backward compatibility check):
   ```powershell
   $env:PYTHONPATH = 'src'
   uv run pytest -q
   uv run ruff check .
   uv run mypy src/tensor_grep
   ```

5. If touching a performance-sensitive path, run the relevant benchmark and confirm no regression:
   - Text search changes: `python benchmarks/run_benchmarks.py --output artifacts/bench.json` then `python benchmarks/check_regression.py --baseline auto --current artifacts/bench.json`
   - AST changes: `python benchmarks/run_ast_benchmarks.py`
   - Hot-query changes: `python benchmarks/run_hot_query_benchmarks.py`
   - Rewrite changes: measure throughput on synthetic 5000-file corpus

## Final Step: Merge to Main

After all local gates pass and work is committed, merge back to the primary checkout's `main`:

```powershell
git -C "C:\dev\projects\tensor-grep" merge <feature-branch> --no-edit
```

This is required so validation workers test against `main`.

## Example Handoff

```json
{
  "salientSummary": "Removed pyo3 auto-initialize from Cargo.toml. CpuBackend search/count/replace paths now complete without Python init. Added failing test asserting no Python DLL load (measured via cold-start improvement proxy), then implemented lazy pyo3 init guard in backend_gpu.rs. cargo test passes (3+N tests). uv run pytest -q: 488 passed.",
  "whatWasImplemented": "Changed pyo3 features from [anyhow, auto-initialize, abi3-py311] to [anyhow, abi3-py311]. Added a lazy Python init guard in backend_gpu.rs around execute_gpu_pipeline and execute_python_module_fallback. Added cold-start benchmark test asserting tg.exe completes in <400ms for simple search.",
  "whatWasLeftUndone": "",
  "verification": {
    "commandsRun": [
      { "command": "cargo test", "exitCode": 0, "observation": "5 passed in 0.02s" },
      { "command": "uv run pytest -q", "exitCode": 0, "observation": "488 passed, 14 skipped" },
      { "command": "hyperfine --runs 10 '.\\target\\release\\tg.exe ERROR bench_data'", "exitCode": 0, "observation": "Mean: 0.312s (vs baseline 0.682s -- 370ms improvement)" }
    ]
  },
  "tests": {
    "added": [{ "file": "rust_core/tests/test_cold_start.rs", "cases": [{ "name": "test_simple_search_no_python_init", "verifies": "Cold-start under 400ms proxy for Python-free path" }] }],
    "updated": ["rust_core/Cargo.toml"]
  },
  "discoveredIssues": []
}
```

## When to Return to Orchestrator

- Benchmark degrades and you cannot isolate the regression without abandoning the feature.
- ast-grep-core API surface changed in a way that breaks the planned integration.
- Merge to main fails with conflicts you cannot resolve without potentially breaking unrelated features.
- Rust memory safety issue (UB risk) cannot be resolved within safe Rust bounds.
