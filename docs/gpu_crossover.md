# Native GPU Crossover Benchmark

## Current post-`v1.9.6` / `v1.9.11` GPU dogfood Read

The post-`v1.9.6` native CUDA dogfood, refreshed by the latest `v1.9.11` GPU dogfood, is a correctness improvement, not a speed promotion.

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
| RTX 4070 (`sm_89`) | 1GB and 5GB correctness passed | `35.46x` slower than `rg` | latest `v1.9.11` 5GB dogfood read: GPU 0 still loses badly |
| RTX 5070 (`sm_120`) | 1GB and 5GB correctness passed | `29.91x` slower than `rg` | latest `v1.9.11` 5GB dogfood read: GPU 1 still loses badly |

The latest user dogfood also reported the native harness as `passed = false` because the speed target and error-test expectations did not pass. That is the intended decision: correctness evidence is necessary, but it is not enough to enable or market GPU auto-routing.

## 2026-05-11 Route And CPU-Staging Audit

The latest local route audit found that the public managed Windows front door is not a clean native CUDA timing source for `--gpu-device-ids`: a direct JSON probe reports `routing_backend = "GpuSidecar"` and `sidecar_used = true`. An in-tree debug binary without the CUDA feature also falls through the Python sidecar and can time out there. Treat any artifact without explicit `NativeGpuBackend` / `sidecar_used = false` route metadata as sidecar-contaminated and unsupported for native CUDA speed proof.

The accepted remediation is twofold:

1. The native GPU benchmark must probe the runtime backend before timing GPU rows and must not time or promote sidecar-routed rows.
2. The CUDA ingest path must make CPU staging measurable. Native JSON/verbose output now exposes host file-read time, host preprocess time, host-to-pinned copy time, CPU staging bytes, pageable-host staging bytes, H2D transfer time, kernel time, and wall time.

The native ingest implementation now applies the same data-movement principles used by CUDA and RAPIDS guidance:

- load file chunks directly into reusable pinned host buffers instead of reading into a pageable `Vec<u8>` and then copying into pinned memory;
- keep chunking explicit so H2D transfer and kernel work can overlap through existing streams and double buffering;
- keep sidecar, CPU fallback, H2D transfer, and kernel execution visible as separate metrics instead of collapsing them into a single "GPU" timing;
- reserve future GPUDirect Storage work for platforms where direct storage-to-GPU DMA is available, because that is the correct next step to remove the remaining host I/O bounce;
- reserve NVLink/P2P work for multi-GPU systems whose topology actually supports peer access, instead of assuming PCIe-attached developer GPUs have that path.

Agent workflow GPU use follows the same rule. `tg agent --gpu-device-ids ... --json` may run a batched fixed-string evidence scan through the selected native GPU route, records the result in `gpu_acceleration`, and only marks the evidence as used when the route reports `NativeGpuBackend` with `sidecar_used = false`. Sidecar-routed output remains unsupported compatibility evidence and does not change the no-crossover positioning.

Reference principles:

- CUDA Best Practices, host/device transfer guidance: <https://docs.nvidia.com/cuda/cuda-c-best-practices-guide/index.html#data-transfer-between-host-and-device>
- CUDA Programming Guide, peer-to-peer memory access: <https://docs.nvidia.com/cuda/cuda-c-programming-guide/index.html#peer-to-peer-memory-access>
- GPUDirect Storage cuFile API guidance: <https://docs.nvidia.com/gpudirect-storage/api-reference-guide/index.html>
- RAPIDS cuDF CSV chunking/byte-range API: <https://docs.rapids.ai/api/cudf/stable/user_guide/api_docs/api/cudf.read_csv/>

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
