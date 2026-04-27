# GPU Troubleshooting Runbook

This guide helps administrators diagnose and resolve GPU-related issues in tensor-grep.

## Common Issues

### 1. CUDA Out of Memory (OOM)
- **Symptom:** Searches fail with `CUDA out of memory` errors.
- **Diagnosis:** Run `tg doctor` to check VRAM utilization and available memory.
- **Resolution:**
  - Reduce batch size or thread concurrency in `sgconfig.yml`.
  - Force CPU fallback by setting `TG_FORCE_CPU=1`.
  - Ensure no other heavy ML workloads are consuming VRAM on the same device.

### 2. Driver Mismatch
- **Symptom:** CUDA driver version is insufficient for the compiled PyTorch bindings.
- **Diagnosis:** Run `tg doctor` to verify the driver version against the required CUDA version (e.g., CUDA 12.x).
- **Resolution:**
  - Update NVIDIA drivers on the host.
  - Alternatively, use the CPU-only binary.

### 3. Forcing CPU Fallback
To bypass GPU acceleration entirely for a session or globally:
```bash
export TG_FORCE_CPU=1
tg search "pattern" ./logs
tg search --force-cpu "pattern" ./logs
```

Windows PowerShell:
```powershell
$env:TG_FORCE_CPU = "1"
tg search "pattern" ./logs
tg search --force-cpu "pattern" ./logs
```

### 4. Device Pinning and Inventory
Use `tg devices --json` to inspect routable device IDs before pinning a workload:

```bash
tg devices --json
tg search --gpu-device-ids 0,1 "pattern" ./logs
```

Operational controls:

- `TENSOR_GREP_DEVICE_IDS=0,1` limits the device IDs tensor-grep may route to.
- `CUDA_VISIBLE_DEVICES=0` limits device visibility for CUDA/PyTorch processes.
- `TG_SIDECAR_TIMEOUT_MS=30000` raises the Python sidecar timeout for slow GPU startup.
- `TENSOR_GREP_TRITON_TIMEOUT_SECONDS=5` controls Triton-backed NLP probe timeouts.

If `tg devices` reports no operational GPUs, GPU benchmarks should record `SKIP` and avoid generating synthetic benchmark corpora.
