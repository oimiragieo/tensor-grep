# Native GPU crossover benchmark (2026-04-29)

Command used:

```powershell
uv run python benchmarks/run_gpu_native_benchmarks.py --output artifacts/bench_run_gpu_native_benchmarks.json
```

Environment:

- Host: Windows 10 (`amd64`)
- `tg` binary: `rust_core/target/release/tg.exe` (`tg 1.6.5`)
- GPU under test: device `0` (`NVIDIA GeForce RTX 4070`, `sm_89`)
- Corpus sizes: `10MB`, `100MB`, `500MB`, `1GB`
- Corpus layout: 8 synthetic log shards per size
- Query: fixed-string `gpu benchmark sentinel`
- Timing: median of 3 end-to-end CLI runs per command

The artifact also detected device `1` (`NVIDIA GeForce RTX 5070`), but the benchmark path on this host still records a missing kernel image for that card. Do not treat device discovery as proof of end-to-end 5070 benchmark coverage here. Current PyTorch upstream guidance for RTX 50-series / Blackwell `sm_120` is to use CUDA 12.8 or CUDA 13.0 builds; `torch==2.6.0+cu124` remains insufficient for those sidecar-backed flows.

## Result

No crossover was found.

`tg search --gpu-device-ids 0` stayed slower than both `rg` and `tg search --cpu` where it completed, then timed out on larger corpora. GPU auto-routing still should **not** be enabled from size alone.

## Per-size benchmark data

| Corpus size | `rg` median | `tg --cpu` median | `tg --gpu-device-ids 0` median | GPU/rg ratio | Result |
| --- | ---: | ---: | ---: | ---: | --- |
| 10MB | 0.129s | 0.133s | 0.436s | 3.3879x | no crossover |
| 100MB | 0.126s | 0.136s | 1.143s | 9.0860x | no crossover |
| 500MB | 0.143s | 0.164s | timeout | n/a | FAIL |
| 1GB | 0.197s | 0.185s | timeout | n/a | FAIL |

Exact throughput and correctness metadata are recorded in `artifacts/bench_run_gpu_native_benchmarks.json`.

## Correctness parity

GPU and CPU match counts were identical only on the sizes that completed:

| Corpus size | CPU matches | GPU matches | Status |
| --- | ---: | ---: | --- |
| 10MB | 2 | 2 | PASS |
| 100MB | 14 | 14 | PASS |
| 500MB | n/a | n/a | FAIL - sidecar timeout |
| 1GB | n/a | n/a | FAIL - sidecar timeout |

## Error-handling validation

| Check | Result |
| --- | --- |
| Invalid device ID `99` | FAIL in current artifact expectation check |
| CUDA unavailable / NVRTC failure | FAIL in current artifact expectation check |
| Timeout simulation | FAIL in current artifact expectation check |
| Malformed / binary / empty files | PASS — GPU path returns valid JSON and handles the mixed fixture without crashing |

Some fault cases are simulation-backed through `TG_TEST_CUDA_BEHAVIOR`.

## Gap analysis

The best measured GPU/rg ratio was at `10MB`, where GPU was still **3.3879x slower** than `rg`.
The gap remained negative where the command completed, and larger corpora timed out before they could establish correctness or throughput.

The current native GPU path is explicit and benchmarkable, but this artifact is not correctness-valid across all measured sizes and is not performance-competitive for this literal-search workload on Windows.

## Optimizations needed before GPU crossover is plausible

1. Reduce end-to-end CLI overhead relative to kernel time
2. Keep kernel compilation and setup costs off the steady-state hot path
3. Improve transfer amortization before enabling any automatic GPU routing

## Routing decision

Keep explicit GPU search manual-only for now.

Do **not** auto-route large corpora to the native GPU path until the benchmark shows a real crossover against `rg`.

## Historical note

Earlier internal advanced-mode runs showed strong GPU pipeline throughput for isolated kernels and multi-pattern workloads. Those numbers are useful for optimization history, but they are not the governing end-to-end routing contract. The current public routing decision should follow the end-to-end crossover artifact above.
