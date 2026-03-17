# Native GPU crossover benchmark (2026-03-17)

Command used:

```powershell
python benchmarks/run_gpu_native_benchmarks.py --advanced --output artifacts/bench_run_gpu_native_benchmarks.json
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
| 10MB | 0.146s | 0.020s | 0.812s | 12.92 MB/s | 5.5545x |
| 100MB | 0.152s | 0.035s | 1.093s | 95.91 MB/s | 7.2150x |
| 500MB | 0.190s | 0.098s | 1.562s | 335.60 MB/s | 8.2127x |
| 1GB | 0.200s | 0.162s | 2.377s | 451.77 MB/s | 11.8762x |

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

The best measured GPU/rg ratio was at `10MB`, where GPU was still **5.5545x slower** than `rg`.
The gap widened at larger sizes, reaching **11.8762x slower** than `rg` at `1GB`.

The current native GPU path is correctness-valid, but it is not yet performance-competitive for this literal-search workload on Windows.

## Optimizations needed before GPU crossover is plausible

1. Cache NVRTC-compiled kernels across CLI invocations
2. Overlap host-to-device transfers with kernel execution via CUDA streams
3. Move large transfers to pinned host memory

## Routing decision

Keep explicit GPU search manual-only for now.

Do **not** auto-route large corpora to the native GPU path until the benchmark shows a real crossover against `rg`.

## Advanced native GPU benchmark (`--advanced`)

The advanced mode now records internal GPU pipeline timings through the hidden `__gpu-native-stats` benchmark hook in addition to the end-to-end CLI crossover numbers above. These timings are used for the native-engine performance assertions that care about GPU pipeline throughput rather than CLI startup / result-materialization overhead.

### Throughput vs sequential `rg` on large sparse-match corpora

| Corpus size | Patterns | `rg` median | GPU pipeline median | Speedup vs `rg` |
| --- | ---: | ---: | ---: | ---: |
| 100MB | 4 | 0.5506s | 0.0086s | 64.2192x |
| 500MB | 4 | 0.7214s | 0.0420s | 17.1846x |
| 1GB | 4 | 0.8695s | 0.0902s | 9.6405x |

This satisfies the `VAL-GPU-018` requirement because the GPU pipeline exceeded `10x` sequential `rg` throughput at both `100MB` and `500MB`.

### Other advanced findings

- Stream overlap benefit: `4.69%`
- Pinned vs pageable transfer throughput: `1.07x` in favor of pinned buffers
- Multi-pattern GPU vs sequential CPU on 1GB: `2.6806x` faster
- Dual GPU vs single GPU on 1GB: `49.48%` faster with identical match counts
- CUDA graphs on 160-file batches: `62.80%` wall-time reduction
- OOM validation: clear user-facing error for a simulated `13 GiB` allocation failure

The long-line benchmark exercises both warp and block dispatch paths successfully, but it is still not an end-to-end throughput win over CPU on this Windows host.
