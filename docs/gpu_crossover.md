# Native GPU Crossover Benchmark

## Current post-`v1.9.6` Read

The post-`v1.9.6` native CUDA dogfood is a correctness improvement, not a speed promotion.

- Native CUDA release search passes 1GB and 5GB correctness on both RTX 4070 (`sm_89`) and RTX 5070 (`sm_120`).
- There is still no crossover for literal search: GPU remains slower than `rg` and `tg_cpu`.
- Python GPU scale rows are unsupported for native CUDA promotion when they route through the Python/Torch sidecar instead of a CUDA-enabled native `tg` binary.
- Native CUDA correctness passed, but speed/promotion failed; keep GPU experimental and opt-in.

Current benchmark taxonomy:

| Surface | Meaning | Promotion status |
| --- | --- | --- |
| Python GPU scale (`run_gpu_benchmarks.py`) | Measures Python/Torch sidecar behavior and device availability. | Unsupported for native CUDA promotion unless `scale_gate_summary.native_cuda_scale_gate.status = SUPPORTED`. |
| Native CUDA scale (`run_gpu_native_benchmarks.py`) | Measures release-native `tg --gpu-device-ids ...` correctness and speed against `rg` and `tg_cpu`. | Requires 1GB and 5GB correctness plus a speed win over both baselines. |

Current native no-crossover evidence:

| Device | Correctness | Best recorded no-crossover ratio | Latest 5GB dogfood read |
| --- | --- | ---: | --- |
| RTX 4070 (`sm_89`) | 1GB and 5GB correctness passed | `22.9183x` slower than `rg` | `rg 0.282s`, `tg_cpu 0.238s`, `tg_gpu 9.259s` |
| RTX 5070 (`sm_120`) | 1GB and 5GB correctness passed | `24.1120x` slower than `rg` | `rg 0.260s`, `tg_cpu 0.254s`, `tg_gpu 9.117s` |

The latest user dogfood also reported the native harness as `passed = false` because the speed target and error-test expectations did not pass. That is the intended decision: correctness evidence is necessary, but it is not enough to enable or market GPU auto-routing.

## Required Promotion Rule

Do not promote GPU speed from device discovery, sidecar availability, or correctness alone. A promotion-ready artifact must show all of the following:

1. Native CUDA backend, not only Python/Torch sidecar rows.
2. Exact match and file-set correctness at every required 1GB and 5GB corpus.
3. GPU faster than both `rg` and `tg_cpu` at the required scale.
4. No failed error-handling or throughput gates.

Until those are true, the public routing decision is explicit GPU search only.

## Historical v1.7 Artifact (Superseded)

Earlier `v1.7.0` native GPU crossover work used:

```powershell
uv run python benchmarks/run_gpu_native_benchmarks.py --output artifacts/bench_run_gpu_native_benchmarks_post_v170_audit.json
```

That artifact covered device `0` (`NVIDIA GeForce RTX 4070`, `sm_89`) on `10MB`, `100MB`, `500MB`, and `1GB` synthetic log corpora. It found no crossover: GPU completed the small rows slower than `rg` and timed out on larger rows. Device `1` (`NVIDIA GeForce RTX 5070`, `sm_120`) was detected but blocked by the then-current CUDA/PyTorch sidecar stack.

Historical per-size data:

| Corpus size | `rg` median | `tg --cpu` median | `tg --gpu-device-ids 0` median | GPU/rg ratio | Result |
| --- | ---: | ---: | ---: | ---: | --- |
| 10MB | 0.104s | 0.113s | 0.409s | 3.9499x | no crossover |
| 100MB | 0.110s | 0.116s | 1.033s | 9.4159x | no crossover |
| 500MB | 0.126s | 0.131s | timeout | n/a | FAIL |
| 1GB | 0.144s | 0.150s | timeout | n/a | FAIL |

The historical artifact remains useful optimization history, but the post-`v1.9.6` decision above is the current contract.
