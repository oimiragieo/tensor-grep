# Index Scaling Validation

- `benchmarks/run_index_scaling_benchmark.py` now enforces at least one `--scales` entry at `>= 10000` files.
- The artifact records `build_time_threshold_s=60.0`, per-row `build_within_threshold`, and `required_scale_validated` so the 10k build-time contract is explicit.
- Each query row now records indexed-vs-plain count parity via `indexed_matches`, `plain_matches`, and `counts_match`; row-level `query_correct` summarizes correctness.
- Local Windows validation command: `uv run python benchmarks/run_index_scaling_benchmark.py --output artifacts/bench_index_scaling.json`.
- Observed on this host (2026-03-16): 10k files built in `~1.52s`; indexed query medians were `~1.37s`, `~1.39s`, and `~1.84s` for `ERROR timeout`, `WARN retry budget`, and `trace_id=` respectively, all with parity against plain search counts.
