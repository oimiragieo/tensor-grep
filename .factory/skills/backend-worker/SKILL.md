---
name: backend-worker
description: Handles Python-based orchestration, routing, caching, and CI integration.
---

# backend-worker

NOTE: Startup and cleanup are handled by `worker-base`. This skill defines the WORK PROCEDURE.

## When to Use This Skill

Use this worker for Python architectural fixes, pipeline routing, environment scoping, or memory/cache improvements in the `tensor_grep.backends` or `tensor_grep.core` modules.

## Work Procedure

1. **Test-Driven Development (TDD) FIRST**: Write a failing unit or integration test in `tests/` that exposes the specific defect or proves the new behavior contract. Run the test and verify it fails.
2. Implement the Python logic to make the test pass.
3. If changing explicit pipeline fallbacks, ensure capability fallback remains observable but intent violations (e.g. `--gpu-device-ids`) fail loudly with `ConfigurationError`.
4. Run local gates: `uv run ruff check .`, `uv run mypy src/tensor_grep`, and `uv run pytest -q`. Fix any failures.
5. If you touched hot performance paths (e.g., AST caching or NLP routing), you MUST benchmark the change. Run `python benchmarks/run_benchmarks.py` and `python benchmarks/run_hot_query_benchmarks.py`. Ensure throughput has not degraded.

## Example Handoff

```json
{
  "salientSummary": "Fixed silent pipeline fallback when --ast backend fails. Added `test_ast_fallback_fatal` which initially failed, then updated `pipeline.py` to raise ConfigurationError instead of degrading to regex. Ran local gates and `run_benchmarks.py` confirming no regression in standard text-search throughput.",
  "whatWasImplemented": "Modified tensor_grep.core.pipeline to throw ConfigurationError on ast_import_error. Added integration test asserting exit code != 0.",
  "whatWasLeftUndone": "",
  "verification.commandsRun": [
    {
      "command": "uv run pytest tests/unit/test_pipeline.py",
      "exitCode": 0,
      "observation": "Passed successfully."
    }
  ],
  "verification.interactiveChecks": [],
  "tests.added": [
    {
      "file": "tests/unit/test_pipeline.py",
      "cases": [{"name": "test_ast_fallback_fatal", "verifies": "Raises ConfigurationError on missing AST dependencies"}]
    }
  ],
  "discoveredIssues": []
}
```

## Final Step: Merge to Main

After all local gates pass and you have committed your work, **merge your feature branch into `main`** before finishing. This is required so that validation workers test against `main`.

**If working in an isolated worktree**, `main` is already checked out in the primary checkout. Do NOT try to `git checkout main` from within the worktree — it will fail. Instead, merge from the primary checkout using:

```powershell
git -C "C:\dev\projects\tensor-grep" merge <feature-branch> --no-edit
```

This works even if the primary checkout has untracked/unstaged changes, as long as there are no conflicting edits. If the merge fails due to conflicts, stash changes first:

```powershell
git -C "C:\dev\projects\tensor-grep" stash
git -C "C:\dev\projects\tensor-grep" merge <feature-branch> --no-edit
git -C "C:\dev\projects\tensor-grep" stash pop
```

## When to Return to Orchestrator

- The benchmark degraded and you cannot isolate the regression without abandoning the feature.
- You encounter an explicit contradiction in `docs/paper.md` or existing `benchmarks/baselines/`.
