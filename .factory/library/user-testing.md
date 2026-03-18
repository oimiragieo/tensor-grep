# User Testing

## Validation Surface

This mission validates through CLI benchmarks and automated tests. No web UI.

Surfaces:
- **AST parity check**: `python benchmarks/run_ast_parity_check.py` -- 40 patterns, 4 languages, tg vs sg byte-accurate
- **AST benchmark**: `python benchmarks/run_ast_benchmarks.py` -- tg vs sg cold one-shot, gate: ratio < 1.1
- **Rewrite benchmark**: `python benchmarks/run_ast_rewrite_benchmarks.py` -- tg apply vs sg apply, gate: ratio < 1.1
- **Text search regression**: `python benchmarks/run_benchmarks.py` + `check_regression.py`
- **CLI output correctness**: Run tg commands, compare output with rg and sg
- **Benchmark scripts**: Existing + new scripts measuring throughput at various scales
- **GPU functionality**: Kernel launches, multi-GPU, error handling
- **Cross-backend parity**: Same patterns across all backends produce identical results

Tools:
- PowerShell for CLI execution
- Python benchmark scripts
- Rust test harness

## Validation Concurrency

**CLI surface**: Max 3 concurrent validators (benchmark scripts are CPU-intensive but independent; memory is not a constraint at 128GB).

Machine: 128GB RAM, 16 logical cores, RTX 4070 + RTX 5070.
Benchmark scripts use hyperfine or time.perf_counter internally.

**Important**: When running benchmark gates, close other CPU-intensive processes to avoid noise. Use `--warmup 3 --min-runs 10` for hyperfine.

## Testing Notes

- Cold search benchmarks: use consistent cache state (hyperfine handles this with warmup runs)
- AST parity: compares tg vs sg on 40 patterns across 4 languages. Any divergence = bug.
- GPU benchmarks: first run warms CUDA context (~200ms), subsequent runs are warm
- Text parity: run 20+ diverse patterns, diff output byte-by-byte
- No web server, no browser testing needed
- Pre-mission baselines:
  - AST search: tg 253ms vs sg 185ms = ratio 1.37x
  - Rewrite apply (1000 files): tg 1.891s vs sg 0.816s = ratio 2.32x
  - Pre-mission binary size: tg.exe = 9,943,040 bytes

## Discovered Testing Knowledge

- Python CLI wrapper might throw unhandled stack traces when routing to native Rust GPU backend if CuDF/Torch are absent. Use the compiled rust binary tg.exe directly for pure validation.
- When testing multiple patterns (like 'error' and 'warn'), bench_data might not contain files matching both. Create dummy log files if needed.
- GPU benchmarks (run_gpu_native_benchmarks.py) often output results to JSON in the artifacts/ directory instead of stdout. Inspect the JSON file to verify benchmark assertions.
- `uv run pytest -q` takes about 70-90 seconds, use 120s timeout.

## Flow Validator Guidance: CLI surface
This surface relies exclusively on the CLI tools in the repository and benchmark scripts.
- Ensure all commands are run from the repository root `C:\dev\projects\tensor-grep`.
- Functional tests (`cargo test`, `pytest`, `cargo build`) can be run normally.
- Performance tests (benchmarks) MUST be run sequentially without any other CPU-intensive processes running in parallel to prevent noise.
- Do NOT use agent-browser or tuistory.

