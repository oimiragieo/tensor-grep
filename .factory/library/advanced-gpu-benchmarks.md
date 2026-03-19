## Advanced GPU benchmark notes

- `benchmarks/run_gpu_native_benchmarks.py --advanced` now records two timing views: the existing end-to-end CLI `rows[*].tg_gpu.median_s`, and advanced internal GPU pipeline timings under `advanced.*` using the hidden `tg.exe __gpu-native-stats` JSON hook.
- The advanced throughput / multi-pattern / multi-GPU assertions are based on `pipeline.wall_time_ms` from the JSON payload, not the outer process wall clock. This avoids counting CLI startup and match materialization when validating native GPU engine throughput.
- The advanced throughput corpus intentionally uses sparse matches plus 4 patterns across 100MB/500MB/1GB to keep GPU match buffers below VRAM limits while still benchmarking multi-pattern scanning. Current 1GB result is ~9.64x vs sequential rg; 100MB and 500MB are above the 10x target.
- Evidence artifacts for the native GPU contract are generated under `artifacts/val-gpu-native/` from the final `artifacts/bench_run_gpu_native_benchmarks.json` run.
