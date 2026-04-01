# AGENTS.md

This file explains how agents should work in `tensor-grep`.

## Goal

`tensor-grep` is trying to become a fast, scalable search tool that combines:

- `ripgrep`-class text search
- AST / structural search
- indexed repeated-query acceleration
- optional GPU / ML paths
- AI-harness-friendly search and edit behavior

The repo should be treated as a benchmark-governed, contract-heavy codebase. Do not optimize by guesswork.

## Operating Rules

1. Start with a failing test when behavior changes.
2. Make the smallest defensible change.
3. Run local gates before pushing.
4. Benchmark every hot-path change.
5. Reject regressions even if the code is otherwise clean.
6. Do not change workflow, release, or docs contracts without updating the validator-backed tests.

## Required Local Validation

Run these before push for normal code changes:

```powershell
uv run ruff check .
uv run mypy src/tensor_grep
uv run pytest -q
```

`uv run pytest -q` takes about 70-90 seconds on this Windows machine, so use a timeout of at least 120 seconds when running it through automation.

For focused changes, run the relevant narrow suite first, then the full suite if the change is intended to land:

```powershell
uv run pytest tests/unit/test_cpu_backend.py -q
uv run pytest tests/unit/test_cli_bootstrap.py -q
uv run pytest tests/unit/test_release_assets_validation.py -q
```

## Benchmark Rules

Never claim a speedup without measured numbers.

Use the right benchmark for the area you changed:

### End-to-end CLI text search

```powershell
python benchmarks/run_benchmarks.py --output artifacts/bench_run_benchmarks.json
python benchmarks/check_regression.py --baseline auto --current artifacts/bench_run_benchmarks.json
```

This is the main `tg` vs `rg` comparison. Use this for:

- plain search routing
- startup / launcher changes
- text-search control-plane changes

### Repeated-query / hot cache paths

```powershell
python benchmarks/run_hot_query_benchmarks.py --output artifacts/bench_hot_query_benchmarks.json
```

Use this for:

- StringZilla index changes
- CPU regex prefilter changes
- persisted cache / decode / posting-list changes

### AST single-query benchmark

```powershell
python benchmarks/run_ast_benchmarks.py --output artifacts/bench_run_ast_benchmarks.json
```

### AST workflow startup benchmark

```powershell
python benchmarks/run_ast_workflow_benchmarks.py --output artifacts/bench_run_ast_workflow_benchmarks.json
```

Use this for:

- `run`
- `scan`
- `test`
- AST workflow startup / batching / wrapper orchestration

### GPU / NLP backend benchmark

```powershell
python benchmarks/run_gpu_benchmarks.py --output artifacts/bench_run_gpu_benchmarks.json
```

Notes:

- `cyBERT` may skip if Triton is unavailable.
- Treat `SKIP` as expected infrastructure state, not a fake failure.

## Performance Discipline

Use these rules consistently:

1. Compare against the current accepted baseline, not memory.
2. Reject candidates that are slower or only “faster” in a microprofile while slower end-to-end.
3. Keep both cold-start and repeated-query measurements in mind.
4. Do not update docs or the paper with speed claims until the benchmark line is accepted.
5. If a candidate is correct but slower, revert it and record the attempt.

## CI / Release Rules

CI is not just a test runner. It enforces:

- formatting
- linting
- typing
- cross-platform behavior
- release workflow contracts
- package-manager workflow contracts
- artifact/version parity

Do not casually edit:

- `.github/workflows/ci.yml`
- `.github/workflows/release.yml`
- `scripts/validate_release_assets.py`

If you change workflow, docs, or release behavior, expect to update validator-backed tests too.

Important test surface:

- `tests/unit/test_release_assets_validation.py`
- workflow/package-manager/release validator suites

## Routing / Architecture Guidance

Be honest about workload classes.

- Cold generic text search:
  - `rg` is still the baseline.
  - control-plane overhead matters more than backend cleverness.
- Repeated text search:
  - indexing can beat cold grep-style tools.
- AST workflows:
  - batching and orchestration matter as much as backend logic.
- GPU:
  - only wins when workload size and arithmetic intensity amortize transfer and startup cost.

Do not assume:

- more caching is always faster
- compiled onefile binaries are always faster
- GPU is always faster
- a micro-optimization is worth landing without end-to-end proof

## Native vs Python Reality

The repo has proven:

- Python-side startup cuts help
- repeated-query indexing helps
- AST batching helps
- onefile Nuitka binaries are not currently the speed path on Windows for plain passthrough

If the goal is to close the remaining gap to raw `rg`, the likely next step is a more native launcher/control-plane path, not more Python micro-tuning.

## Push Discipline

Do not push from a dirty worktree if `origin/main` moved and the local tree has unrelated changes.

Preferred approach:

1. use a clean replay worktree
2. rebase/reset to current `origin/main`
3. rerun narrow checks and relevant benchmarks
4. push only the accepted change

## PR Title And Release Intent

AI-generated PRs must use conventional titles so CI can infer semantic-release intent.

Use this schema:

- `feat: ...` => minor release
- `fix: ...` or `perf: ...` => patch release
- `feat!: ...` or `fix!: ...` => major release
- `docs: ...`, `test: ...`, `chore: ...`, `ci: ...`, `build: ...` => no release

Release-bearing PRs must use `Squash and merge` so the validated PR title becomes the commit subject on `main`.

Do not manually create release tags when semantic-release is active.

## Documentation Discipline

When a candidate is accepted or explicitly rejected, update:

- `docs/PAPER.md` if it changes the optimization history or benchmark story
- `README.md` / `docs/benchmarks.md` only after accepted benchmark changes

The paper should preserve failed attempts too, so future agents do not retry the same losing ideas.

## Bottom Line

Work like this:

1. test first
2. smallest change
3. local lint/type/test
4. benchmark
5. reject regressions
6. push only measured wins or required correctness/CI fixes

