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
tg search "pattern"
```
