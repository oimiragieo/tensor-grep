# GPU crossover benchmark (2026-03-16)

Command used:

```powershell
python benchmarks/run_gpu_benchmarks.py --output artifacts/bench_gpu_scale.json
```

Environment used by the benchmark:

- Host: Windows 10 (`amd64`)
- `tg` binary: `rust_core/target/release/tg.exe`
- GPU sidecar Python: `.venv_cuda\Scripts\python.exe`
- PyTorch: `2.6.0+cu124`
- Corpus sizes: `1MB`, `10MB`, `100MB`, `1GB`
- Corpus layout: 8 synthetic log shards per size, explicit GPU-compatible literal pattern (`"gpu benchmark sentinel"`)
- Timing mode: single-sample `perf_counter` timing (the 1GB GPU path was too slow to make multi-sample local runs practical)

## Result

No crossover was found.

On this host, the explicit GPU path never beat either `rg` or default `tg` CPU/ripgrep routing at any measured size up to `1GB`, so `--gpu-auto` was **not** added.

## Per-size timing data

| Corpus size | `rg` | `tg` CPU/rg path | RTX 4070 (`--gpu-device-ids 0`) | RTX 5070 (`--gpu-device-ids 1`) |
| --- | ---: | ---: | ---: | --- |
| 1MB | 0.165s | 0.167s | 3.647s | unsupported |
| 10MB | 0.196s | 0.223s | 4.944s | unsupported |
| 100MB | 0.158s | 0.169s | 17.962s | unsupported |
| 1GB | 0.188s | 0.201s | 163.930s | unsupported |

## Transfer / startup overhead

The smallest corpus gives the clearest lower-bound proxy for startup + transfer overhead:

- RTX 4070 vs `rg` at `1MB`: **+3.481s**
- RTX 4070 vs default `tg` CPU path at `1MB`: **+3.480s**

That extra latency never amortized away. By `10MB`, the GPU path was still **+4.748s** slower than `rg`, and by `1GB` it was **+163.741s** slower.

In practice, the current Windows explicit-GPU text-search path is dominated by sidecar/PyTorch startup plus per-line GPU work overhead rather than delivering a throughput win.

## Correctness check

GPU correctness was verified against `rg` on the `10MB` corpus for three patterns:

1. `gpu benchmark sentinel`
2. `WARN retry budget exhausted`
3. `Database connection timeout`

For the RTX 4070 run, both **match counts** and **matched file sets** were identical to `rg` for all three patterns.

The RTX 5070 was not included in correctness validation because the current CUDA-enabled PyTorch build cannot execute kernels on that GPU.

## RTX 5070 status

The benchmark probe detected the second GPU, but PyTorch reported it as unsupported:

> NVIDIA GeForce RTX 5070 with CUDA capability `sm_120` is not compatible with the current PyTorch installation.

Attempting a real search on device `1` fails with `CUDA error: no kernel image is available for execution on the device`.

## When GPU should / shouldn't be used

### Should use GPU

- Not for the current text-search path on this Windows host.
- Re-evaluate only after the CUDA stack changes materially (for example: PyTorch build with RTX 5070 support, a real GPU regex kernel, or a much lower-overhead sidecar/runtime path).

### Shouldn't use GPU

- General text search on this host, even at `1GB`
- Any auto-routing decision based on size alone
- Any workload pinned to RTX 5070 with the current `2.6.0+cu124` PyTorch build

## `--gpu-auto` decision

`--gpu-auto` was **not** added.

Reason: the benchmark never produced a row where GPU was at least **20% faster** than `rg`; instead, the only operational GPU (RTX 4070) was slower at every measured size, and the RTX 5070 is currently unsupported by the active PyTorch/CUDA stack.
