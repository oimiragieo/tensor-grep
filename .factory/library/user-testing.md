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

## Flow Validator Guidance: contract-fixes assertions

### VAL-CONTRACT-001 (GPU pinning fatal error)
- **Test**: `uv run pytest tests/unit/test_pipeline.py::TestPipeline::test_should_raise_configuration_error_when_explicit_gpu_ids_have_no_available_gpu_backend -v` 
- **Also**: `uv run pytest tests/unit/test_pipeline.py::TestPipeline::test_pipeline_fallback_should_raise_configuration_error_when_explicit_gpu_ids_have_no_routable_chunk_plan -v`
- **Evidence**: Both tests must pass, confirming ConfigurationError is raised when GPU backends are unavailable but explicit device IDs are provided.

### VAL-CONTRACT-002 (AST fallback fatal error)
- **Test**: Run `uv run pytest tests/unit/test_pipeline.py -k "ast" -v` to find AST-related tests.
- **Code verification**: Check `src/tensor_grep/core/pipeline.py` for the explicit AST import failure → ConfigurationError path.
- **Evidence**: Test passes confirming ConfigurationError is raised when --ast is explicit but AST backends fail to import.

### VAL-CONTRACT-003 (NLP routing to CybertBackend)
- **Test**: `uv run pytest tests/unit/test_pipeline.py::TestPipeline::test_nlp_routing_should_select_cybert_backend_for_nlp_queries -v`
- **Also**: `uv run pytest tests/unit/test_cybert_backend.py -v` and `uv run pytest tests/e2e/test_cli_classify.py -v`
- **Evidence**: Pipeline correctly routes NLP queries to CybertBackend; classify CLI subcommand works.

### VAL-CROSS-001 (Benchmark governance)
- **Test**: Run `python benchmarks/run_benchmarks.py --output artifacts/bench_run_benchmarks.json` and `python benchmarks/run_hot_query_benchmarks.py --output artifacts/bench_hot_query_benchmarks.json`
- **Evidence**: No regression detected. Note: benchmarks may show SKIP for GPU paths if no GPU available — this is expected.

### VAL-CROSS-002 (CI/Release gate cleanliness)
- **Test**: Run in sequence: `uv run ruff check .`, `uv run mypy src/tensor_grep`, `uv run pytest -q`
- **Evidence**: All three commands exit with code 0 and zero errors.

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
