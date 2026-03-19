# Native GPU crossover benchmark (2026-03-17)

Command used:

```powershell
python benchmarks/run_gpu_native_benchmarks.py --output artifacts/bench_run_gpu_native_benchmarks.json
```

Environment:

- Host: Windows 10 (`amd64`)
- `tg` binary: `rust_core/target/release/tg.exe` built with `--features cuda`
- GPU under test: device `0` (`NVIDIA GeForce RTX 4070`, `sm_89`)
- Corpus sizes: `10MB`, `100MB`, `500MB`, `1GB`
- Corpus layout: 8 synthetic log shards per size
- Query: fixed-string `gpu benchmark sentinel`
- Timing: median of 3 end-to-end CLI runs per command

The artifact also detected device `1` (`NVIDIA GeForce RTX 5070`), but the benchmark path on this host still records a missing kernel image for that card. Do not treat device discovery as proof of end-to-end 5070 benchmark coverage here.

## Result

No crossover was found.

`tg search --gpu-device-ids 0` stayed slower than both `rg` and `tg search --cpu` at every measured size, so GPU auto-routing still should **not** be enabled from size alone.

## Per-size benchmark data

| Corpus size | `rg` median | `tg --cpu` median | `tg --gpu-device-ids 0` median | GPU/rg ratio |
| --- | ---: | ---: | ---: | ---: | ---: |
| 10MB | 0.125s | 0.317s | 0.869s | 6.9329x |
| 100MB | 0.120s | 0.300s | 1.004s | 8.3682x |
| 500MB | 0.202s | 0.364s | 1.384s | 6.8427x |
| 1GB | 0.202s | 0.455s | 2.045s | 10.1223x |

Exact throughput and correctness metadata are recorded in `artifacts/bench_run_gpu_native_benchmarks.json`.

## Correctness parity

GPU and CPU match counts were identical at all four corpus sizes:

| Corpus size | CPU matches | GPU matches | Status |
| --- | ---: | ---: | --- |
| 10MB | 2 | 2 | PASS |
| 100MB | 14 | 14 | PASS |
| 500MB | 70 | 70 | PASS |
| 1GB | 143 | 143 | PASS |

## Error-handling validation

| Check | Result |
| --- | --- |
| Invalid device ID `99` | PASS — exits `2` and lists available CUDA devices |
| CUDA unavailable | PASS — reported explicitly in artifact/test coverage |
| Timeout simulation | PASS — exits `2` with a timeout error |
| Malformed / binary / empty files | PASS — GPU path returns valid JSON and handles the mixed fixture without crashing |

Some fault cases are simulation-backed through `TG_TEST_CUDA_BEHAVIOR`.

## Gap analysis

The best measured GPU/rg ratio was at `10MB`, where GPU was still **6.9329x slower** than `rg`.
The gap remained negative at every measured size and reached **10.1223x slower** than `rg` at `1GB`.

The current native GPU path is correctness-valid, but it is not yet performance-competitive for this literal-search workload on Windows.

## Optimizations needed before GPU crossover is plausible

1. Reduce end-to-end CLI overhead relative to kernel time
2. Keep kernel compilation and setup costs off the steady-state hot path
3. Improve transfer amortization before enabling any automatic GPU routing

## Routing decision

Keep explicit GPU search manual-only for now.

Do **not** auto-route large corpora to the native GPU path until the benchmark shows a real crossover against `rg`.

## Historical note

Earlier internal advanced-mode runs showed strong GPU pipeline throughput for isolated kernels and multi-pattern workloads. Those numbers are useful for optimization history, but they are not the governing end-to-end routing contract. The current public routing decision should follow the end-to-end crossover artifact above.
