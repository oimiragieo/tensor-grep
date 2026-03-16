# Native GPU crossover benchmark (2026-03-16)

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

## Result

No crossover was found.

`tg search --gpu-device-ids 0` stayed slower than both `rg` and `tg search --cpu` at every measured size, so GPU auto-routing still should **not** be enabled from size alone.

## Per-size benchmark data

| Corpus size | `rg` median | `tg --cpu` median | `tg --gpu-device-ids 0` median | GPU throughput | GPU/rg ratio |
| --- | ---: | ---: | ---: | ---: | ---: |
| 10MB | 0.129s | 0.017s | 0.622s | 16.85 MB/s | 4.8352x |
| 100MB | 0.145s | 0.029s | 0.722s | 145.23 MB/s | 4.9703x |
| 500MB | 0.151s | 0.079s | 1.153s | 454.69 MB/s | 7.6446x |
| 1GB | 0.179s | 0.143s | 1.846s | 581.72 MB/s | 10.2875x |

Exact throughput in bytes/s is recorded in `artifacts/bench_run_gpu_native_benchmarks.json`.

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
| NVRTC compilation failure simulation | PASS — exits `2` with `CUDA kernel compilation failed: ...` |
| Timeout simulation | PASS — exits `2` with `GPU operation timed out after 300ms` |
| Malformed / binary / empty files | PASS — GPU path returns valid JSON and handles the mixed fixture without crashing |

Timeout and NVRTC coverage are currently simulation-backed through `TG_TEST_CUDA_BEHAVIOR`.

## Gap analysis

The best measured GPU/rg ratio was at `10MB`, where GPU was still **4.8352x slower** than `rg`.
The gap widened at larger sizes, reaching **10.2875x slower** than `rg` at `1GB`.

The current native GPU path is correctness-valid, but it is not yet performance-competitive for this literal-search workload on Windows.

## Optimizations needed before GPU crossover is plausible

1. Cache NVRTC-compiled kernels across CLI invocations
2. Overlap host-to-device transfers with kernel execution via CUDA streams
3. Move large transfers to pinned host memory

## Routing decision

Keep explicit GPU search manual-only for now.

Do **not** auto-route large corpora to the native GPU path until the benchmark shows a real crossover against `rg`.
