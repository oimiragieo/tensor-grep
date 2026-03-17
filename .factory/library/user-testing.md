# User Testing

## Validation Surface

This mission validates through CLI benchmarks and automated tests. No web UI.

Surfaces:
- **CLI output correctness**: Run tg commands, compare output with rg
- **Benchmark scripts**: Existing + new scripts measuring throughput at various scales
- **GPU functionality**: Kernel launches, multi-GPU, error handling
- **Cross-backend parity**: Same patterns across all backends produce identical results

Tools:
- PowerShell for CLI execution
- Python benchmark scripts
- Rust test harness

## Validation Concurrency

**CLI surface**: Max 1 concurrent validator (benchmarks are timing-sensitive, parallel execution would corrupt results).

Machine: 131GB RAM, 8 cores / 16 threads, RTX 4070 + RTX 5070.
Benchmark scripts use hyperfine or Measure-Command internally — they manage their own parallelism.

## Testing Notes

- Cold search benchmarks: must ensure filesystem cache is cold between runs (or use consistent cache state)
- GPU benchmarks: first run warms CUDA context (~200ms), subsequent runs are warm. Benchmark both.
- Text parity: run 20+ diverse patterns, diff output byte-by-byte
- No web server, no browser testing needed


## Discovered Testing Knowledge
- Python CLI wrapper might throw unhandled stack traces when routing to native Rust GPU backend if CuDF/Torch are absent. Use the compiled rust binary 	g.exe directly for pure validation.

- When testing multiple patterns (like 'error' and 'warn'), bench_data might not contain files matching both. Create dummy log files if needed.
- GPU benchmarks (run_gpu_native_benchmarks.py) often output results to JSON in the artifacts/ directory instead of stdout. Inspect the JSON file to verify benchmark assertions.
