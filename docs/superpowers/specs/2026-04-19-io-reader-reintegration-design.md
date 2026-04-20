# IO Reader Reintegration Design

Date: 2026-04-19

## Goal

Reconnect the designed GPU file-ingest layer without broad routing churn or unmeasured performance claims.

This slice is intentionally narrow:

1. prove where the planned reader layer is dead or unwired
2. restore one safe runtime seam
3. preserve existing launcher, AST, and chunked GPU behavior
4. keep benchmark claims unchanged unless new benchmark data earns them

## Current State

The repo's planned reader layer exists:

- `src/tensor_grep/io/reader_fallback.py`
- `src/tensor_grep/io/reader_cudf.py`
- `src/tensor_grep/io/reader_dstorage.py`
- `src/tensor_grep/io/reader_kvikio.py`

But the live runtime does not actually select among those readers.

- `src/tensor_grep/core/pipeline.py` chooses compute backends directly.
- `src/tensor_grep/backends/cudf_backend.py` performs its own ingest with:
  - Rust zero-copy Arrow ingestion when available
  - direct `cudf.read_text(...)` fallback
  - chunked zero-copy and distributed worker branches
- only `FallbackReader` is imported by current production call sites outside the cuDF backend

## Broken Contract

`src/tensor_grep/io/base.py` models only `read_lines(...)`, while the planned GPU readers expose `read_to_gpu(...)`.

That means the repo does not currently have a single public reader interface that can represent both:

- line-oriented CPU fallback
- GPU-oriented ingest helpers

This is why `reader_cudf`, `reader_dstorage`, and `reader_kvikio` were never safely dropped into runtime selection.

## External Validation

Recent official RAPIDS and NVIDIA guidance still supports a capability-gated design:

- cuDF leverages KvikIO for high-performance I/O and GDS-sensitive paths
- KvikIO behavior depends on compatibility mode and system support
- NVIDIA GDS best practices continue to emphasize alignment, runtime capability, and workload sizing

Research and community signal reinforce the same conclusion:

- direct-storage wins are workload-sensitive
- control-plane overhead still matters
- local exact search plus explicit accelerators remains the right architecture shape

Therefore the next move is not "always use GPU I/O". It is "restore the seam safely and benchmark later".

## Decision

Add a private reader-ingest helper inside `CuDFBackend` and route only the existing single-file non-zero-copy fallback through that helper.

Keep unchanged:

- `Pipeline` backend selection
- Rust zero-copy Arrow path
- chunked zero-copy path
- multi-GPU worker distribution
- public CLI surface

## Acceptance Criteria

1. A failing test first proves the single-file fallback now routes through a helper.
2. A failing test first proves that helper prefers `CuDFReader.read_to_gpu()`.
3. A failing test first proves fallback to raw `cudf.read_text(...)` remains intact.
4. `tests/unit/test_cudf_backend.py` stays green after the implementation.
5. Reader unit tests and the multi-GPU integration surface stay green.
6. Full repo gates stay green.
7. GPU benchmarks run, but no speed claim is published unless the result is accepted.

## Non-Goals

- redesign `IOBackend`
- enable `DStorageReader` by default
- enable `KvikIOReader` by default
- touch AST routing or sidecar workflow code
- change release or CI validators

## Release Intent

This slice is a correctness fix, not a feature release:

- preferred title: `fix: rewire cudf ingest through reader selector`

