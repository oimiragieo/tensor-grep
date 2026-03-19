# Windows CUDA Worker Isolation Pattern

On Windows, Python multiprocessing uses `spawn()` (not `fork()`), which means each worker process starts fresh.
CUDA context initialization happens at the first `import cudf` or `import torch` call.

## Key Rules

1. **Set `CUDA_VISIBLE_DEVICES` before importing cudf/rmm/torch** in spawned workers.
   If you import first, the CUDA runtime initializes on all visible GPUs, and setting the env var afterward has no effect.

2. **Use `max_tasks_per_child=1`** on the process pool to prevent worker reuse across different GPU assignments.
   Without this, a worker pinned to GPU 0 could be reused for GPU 1 work, causing cross-device context contamination.

3. **Return logical device 0** after pinning. `CUDA_VISIBLE_DEVICES` remaps physical GPUs to logical indices, so after setting `CUDA_VISIBLE_DEVICES=3`, the target device is always logical `0`.

4. **Linux doesn't need this** — `fork()` inherits the parent context without spawn corruption. The fix is gated on `os.name == 'nt'`.

## Implementation Location

- `src/tensor_grep/backends/cudf_backend.py`: `_configure_cuda_worker_environment()` and `_create_process_pool()`
