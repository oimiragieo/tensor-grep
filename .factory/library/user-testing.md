## Validation Surface

The primary validation surfaces for tensor-grep are the `tg` CLI, its Python API bindings, and its `pytest` integration test suite.

## Validation Concurrency

Due to the extreme memory overhead of cuDF/Torch GPU contexts and Rust memory-mapping across a CI host:
- `CLI Tests`: Max 2 concurrent validators.
- `GPU Edge Tests`: Max 1 concurrent validator (strictly serialized to avoid VRAM collisions).

## Flow Validator Guidance: pytest

When testing assertions that are verified via the pytest test suite:
- Run the specific test files that cover the assertion behavior.
- Capture both stdout and exit code as evidence.
- On Windows, `uv run pytest` is the standard runner. Use `.venv\Scripts\python.exe -m pytest` if uv is not available.
- Worker isolation tests (VAL-CUDA-001) are in `tests/unit/test_cudf_backend.py`.
- Key tests for CUDA worker isolation:
  - `test_worker_isolation_sets_cuda_visible_devices_before_worker_imports` - verifies env vars are set before cudf/rmm import
  - `test_worker_isolation_uses_fresh_process_pool_children_on_windows` - verifies max_tasks_per_child=1
- Also verify the code structure: `_configure_cuda_worker_environment()` is called before `import cudf` / `import rmm` in `_process_chunk_on_device()`.
- No shared state concerns for read-only pytest runs.
