---
name: rust-worker
description: Handles Rust PyO3 core extensions, memory mapping, and zero-copy string mutations.
---

# rust-worker

NOTE: Startup and cleanup are handled by `worker-base`. This skill defines the WORK PROCEDURE.

## When to Use This Skill

Use this worker for modifying `rust_core/`, specifically PyO3 memory boundaries, `memmap2` optimizations, and native regex replacement paths.

## Work Procedure

1. **Test-Driven Development (TDD) FIRST**: Write a Rust test in `rust_core/src/` or a Python integration test in `tests/` that asserts the specific memory mutation behavior or boundary condition. Verify it fails.
2. Implement the native Rust logic (e.g., using `memmap2::MmapMut` instead of `std::fs::read` for the replace path). Ensure exact semantic parity.
3. Build the core locally using `uv run maturin develop` if needed, or rely on `cargo test`.
4. Run local Python gates if exposed to the API: `uv run pytest -q`.
5. Benchmark the hot-path changes. If implementing zero-copy `--replace`, measure the throughput and memory allocation on a large synthetic Python file. REJECT the candidate if the throughput regresses the baseline.

## Example Handoff

```json
{
  "salientSummary": "Refactored `backend_cpu.rs` to use `MmapMut` for the `--replace` path instead of full RAM allocation. Added a Rust unit test and benchmarked a 50MB file, reducing memory allocation to near zero while maintaining sub-second replacement speeds.",
  "whatWasImplemented": "Replaced `std::fs::read` with `MmapOptions::new().map_mut` in `rust_core/src/backend_cpu.rs` `replace_file_regex`. Ensured file is safely mutated in place.",
  "whatWasLeftUndone": "",
  "verification.commandsRun": [
    {
      "command": "cd rust_core && cargo test",
      "exitCode": 0,
      "observation": "Rust unit tests pass."
    }
  ],
  "verification.interactiveChecks": [],
  "tests.added": [
    {
      "file": "rust_core/src/backend_cpu.rs",
      "cases": [{"name": "test_mmap_replace_in_place", "verifies": "Validates exact string replacement via MmapMut without allocations"}]
    }
  ],
  "discoveredIssues": []
}
```

## Windows-Specific Notes

- **Shell**: PowerShell is the default shell on Windows. Use `$env:PYTHONPATH = '...\src'` syntax, not `export`.
- **init.sh**: The `.factory/init.sh` script cannot be run directly. Instead run: `uv pip install -e ".[dev,ast,nlp]"`
- **PYTHONPATH**: When running pytest in a fresh isolated worktree, set PYTHONPATH: `$env:PYTHONPATH = '<worktree>\src'; uv run pytest -q`

## Final Step: Merge to Main

After all local gates pass and you have committed your work, **merge your feature branch into `main`** before finishing. This is required so that validation workers test against `main`.

**If working in an isolated worktree**, `main` is already checked out in the primary checkout. Do NOT try to `git checkout main` from within the worktree — it will fail. Instead, merge from the primary checkout using:

```powershell
git -C "C:\dev\projects\tensor-grep" merge <feature-branch> --no-edit
```

If the merge fails due to conflicts, stash first:

```powershell
git -C "C:\dev\projects\tensor-grep" stash
git -C "C:\dev\projects\tensor-grep" merge <feature-branch> --no-edit
git -C "C:\dev\projects\tensor-grep" stash pop
```

## When to Return to Orchestrator

- Memory safety or undefined behavior (UB) risks cannot be mitigated cleanly within safe Rust bounds.
- Throughput unexpectedly collapses due to OS page-fault overhead during MmapMut.
