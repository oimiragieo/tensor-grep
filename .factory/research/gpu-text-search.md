# GPU Text Search Research Summary

Key findings for workers:

## GPU CAN beat CPU for text search:
- Gutierrez (2025): 12.3x at 100MB, 42.1x at 2GB on RTX 4090
- PFAC: up to 60 GB/s multi-pattern throughput
- Crossover: ~50MB substring, ~10MB multi-pattern, ~500MB regex

## Current architecture is the bottleneck (NOT GPU hardware):
- Python sidecar adds 1.5-2.5s startup (process creation + torch import)
- TorchBackend does per-line GPU transfers (291K transfers for 1GB)
- No batch processing — Python for-loop, not GPU parallelism

## Bandwidth math (RTX 4070):
- GPU memory bandwidth: 504 GB/s → scan 1GB in ~2ms
- PCIe 4.0 x16 transfer: ~22 GB/s → copy 1GB in ~45ms
- Kernel + transfer for 1GB: ~48ms total
- ripgrep on 1GB: ~200-300ms
- GPU should win 4-6x at 1GB with native path

## cudarc (Rust CUDA bindings):
- v0.19.3, 1074 stars, 2.8M downloads, actively maintained
- Safe Rust wrappers for CUDA driver, NVRTC, cuFILE (GPUDirect Storage)
- Dynamic loading — graceful fallback without GPU
- Eliminates Python overhead entirely

## Implementation plan:
1. Replace Python sidecar with cudarc in Rust binary
2. Brute-force substring CUDA kernel (thread per position)
3. Smart routing: CPU for <50MB, GPU for >50MB
4. Batched multi-file search (entire repo in GPU memory)
5. CUDA streams for overlapping I/O and compute
6. Warp-parallel search for long lines (cuDF approach)

## RTX 5070 note:
- sm_120 needs torch 2.7+ / CUDA 12.6+
- cudarc with NVRTC can target any arch via PTX JIT

## Full report: artifacts/gpu_text_search_research_report.md
