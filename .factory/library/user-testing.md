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

## Flow Validator Guidance: Rust CLI / cargo test

When testing assertions related to the Rust core (`rust_core/`):
- **Cargo path**: Cargo is at `$env:USERPROFILE\.cargo\bin\cargo.exe`. Must add to PATH: `$env:PATH = "$env:USERPROFILE\.cargo\bin;$env:PATH"`.
- **Running tests**: `cd C:\dev\projects\tensor-grep\rust_core && cargo test 2>&1`
- **Replace tests**: The replace test suite is at `rust_core/tests/test_replace.rs` and includes:
  - `test_replace_path_uses_mutable_memmap_instead_of_full_file_reads` — source-level verification that `MmapMut` is used and `std::fs::read` is absent in the replace path
  - `test_rust_replace_in_place_literal` — literal string replacement
  - `test_rust_replace_in_place_regex_capture_groups` — regex capture group replacement
  - `test_rust_replace_preserves_formatting` — whitespace/tab preservation
  - `test_rust_replace_handles_mixed_growth_and_shrink_matches` — mixed growth/shrink replacements
- **Binary path**: The pre-built Rust binary is at `C:\dev\projects\tensor-grep\benchmarks\tg_rust.exe`.
- **Replacement via binary**: `.\tg_rust.exe <pattern> <path> --replace <replacement> [--fixed-strings] [--ignore-case]`
- **Code analysis**: The replace implementation is in `rust_core/src/backend_cpu.rs`. The key methods are:
  - `replace_in_place()` — public entry point
  - `replace_file_literal()` — fast-path for literal string replacements using `MmapMut`
  - `replace_file_regex()` — regex replacements using `MmapMut`
  - `write_replacements_with_mmap()` — shared helper that grows file via `set_len`, creates `MmapMut`, applies in-place
  - `apply_replacements_in_place()` — in-place byte mutation on mmap buffer
- **Benchmark data**: Synthetic replace data at `C:\dev\projects\tensor-grep\benchmarks\dummy_replace_data\large_dataset.txt` (~9.4 MB).
- No shared state concerns for read-only cargo test runs or benchmarks on separate temp files.
