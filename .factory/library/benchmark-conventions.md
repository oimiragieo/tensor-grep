# Benchmark Conventions

## Milestone Baseline Naming

Baseline files are stored in `benchmarks/baselines/` with this naming convention:

- **run_benchmarks.py** suite: `benchmarks/baseline_m1.json` (milestone 1), `benchmarks/baseline_m2.json`, etc.
- **Other suites**: `benchmarks/baseline_{suite}_{milestone}.json` — e.g. `benchmarks/baseline_hot_query_m1.json`

This convention is implemented in `check_regression.py:resolve_auto_baseline_path()`.

## Windows Benchmark Timing Variance

Windows benchmark timing has noticeable run-to-run variance at the 5% regression gate.
First runs may temporarily exceed thresholds before repeat runs pass. When benchmarking
on Windows:

- Always run at least N=3 samples (run_benchmarks.py already does median of 3)
- If a first run barely exceeds the 5% threshold, repeat before concluding regression
- Close non-essential processes to reduce variance

## Regression Gate Thresholds

- Default max regression: **5%** (configurable via `--threshold`)
- Minimum baseline time to gate: **0.1s** (scenarios below this are not regression-checked)
- Hot-query benchmarks: intra-run comparison (second_s vs first_s) + cross-run baseline comparison

## Worktree Benchmark Gotchas

When running benchmarks from isolated git worktrees:
- Set `$env:PYTHONPATH = '<worktree>\src'` so benchmark scripts find tensor_grep
- Ensure `bench_data/` directory is accessible (it may not be in the worktree; use the primary checkout's bench_data or copy it)
- Use `--no-ignore` when searching bench_data (*.log files are gitignored)
