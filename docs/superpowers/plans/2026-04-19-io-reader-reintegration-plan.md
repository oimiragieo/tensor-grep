# IO Reader Reintegration Plan

Date: 2026-04-19
Spec: `docs/superpowers/specs/2026-04-19-io-reader-reintegration-design.md`

## Objective

Execute the smallest TDD-first patch that reconnects the planned cuDF reader seam without changing accepted routing behavior.

## Plan

### Phase 0: Baseline

- run narrow reader tests
- run the cuDF backend suite
- run the multi-GPU integration test surface
- record that this is a correctness-first slice with no benchmark claim

### Phase 1: RED

Add failing tests in `tests/unit/test_cudf_backend.py`:

- `test_should_route_single_file_cudf_fallback_through_reader_helper`
- `test_should_use_cudf_reader_before_raw_cudf_read_text`
- `test_should_fall_back_to_raw_cudf_read_text_when_cudf_reader_unavailable`

### Phase 2: GREEN

In `src/tensor_grep/backends/cudf_backend.py`:

- add a private `_read_text_series(...)` helper
- prefer `CuDFReader.read_to_gpu(...)`
- fall back to raw `cudf.read_text(...)`
- replace only the duplicated single-file fallback calls with that helper

### Phase 3: Focused Verification

- `uv run pytest tests/unit/test_cudf_backend.py -q`
- `uv run pytest tests/unit/test_reader_fallback.py tests/unit/test_reader_cudf.py tests/unit/test_reader_dstorage.py tests/unit/test_reader_kvikio.py -q`
- `uv run pytest tests/integration/test_multi_gpu_distribution.py -q`

### Phase 4: Full Gates

- `uv run ruff check .`
- `uv run mypy src/tensor_grep`
- `uv run pytest -q`

### Phase 5: Benchmark Discipline

- `uv run python benchmarks/run_gpu_benchmarks.py --output artifacts/bench_run_gpu_benchmarks.json`

If the result is neutral or noisy, keep the change as correctness-only and do not make a speed claim.

### Phase 6: Documentation

- save the spec and plan with the patch
- update `docs/PAPER.md` only with a correctness note, not a performance claim
- leave README and release validators alone unless the public contract actually changes

## Deferred Follow-Ups

Do not bundle these into this patch:

- repo-wide reader-interface redesign
- DStorage/KvikIO default activation
- AST workflow cleanup
- env-flag retirement

