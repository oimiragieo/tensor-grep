# Validation Contract: Routing and Integration + Cross-Area Flows

**Milestone:** Performance Mission — Routing and Integration  
**Created:** 2026-03-16  
**Status:** Draft (pending implementation)

---

## Context

tensor-grep is adding a **native CPU engine** (embedded grep crates replacing rg subprocess) and a **native GPU engine** (cudarc + CUDA kernels replacing the Python sidecar). Smart routing will automatically select the optimal backend based on workload characteristics, with automatic crossover calibration.

### Existing Backends (pre-milestone)
| Backend | Routing Identity | Transport |
|---|---|---|
| rg passthrough | `CpuBackend` / `cpu-native` | Direct process spawn |
| CpuBackend (Rust native) | `CpuBackend` / `cpu-native` | In-process (memmap) |
| TrigramIndex | `TrigramIndex` / `index-accelerated` | In-process (index files) |
| GPU sidecar (Python) | `GpuSidecar` / `gpu-device-ids-explicit` | JSON-over-stdio IPC to Python |
| AstBackend (Rust) | `AstBackend` / `ast-native` | In-process (tree-sitter) |
| AstGrepWrapperBackend | `AstGrepWrapperBackend` / `ast_grep_json` | sg subprocess |
| CybertBackend (NLP) | `CybertBackend` / `nlp_cybert` | Python sidecar |

### New Backends (this milestone)
| Backend | Routing Identity | Transport |
|---|---|---|
| Native CPU search | `NativeCpuBackend` / `cpu-native-embedded` | In-process (grep crate) |
| Native GPU search | `NativeGpuBackend` / `gpu-native-cudarc` | In-process (cudarc CUDA kernels) |

### Smart Routing Decision Factors
- Corpus size (small < 50MB → CPU, large > 50MB → GPU candidate)
- Query repetition (repeated queries → index)
- Pattern type (AST patterns → ast-grep, NLP queries → cybert)
- GPU availability and measured performance
- Crossover calibration data (CPU vs GPU timing thresholds)

---

## Part 1: Routing Assertions (VAL-ROUTE-NNN)

### VAL-ROUTE-001: Small corpus routes to native CPU

**Assertion:** When corpus size < 50MB and no explicit backend override flags are set, smart routing selects the native CPU engine.

**Verification:**
1. Create a test corpus < 50MB (e.g., 10MB of log files).
2. Run `tg search PATTERN PATH` without `--gpu-device-ids`, `--index`, or `--force-cpu`.
3. Check `--verbose` stderr or `--json` output: `routing_backend` should be `NativeCpuBackend` (or the new native CPU identity).
4. Confirm `routing_reason` contains a size-based justification (e.g., `cpu-native-small-corpus`).

**Boundary conditions:**
- Corpus exactly at 50MB boundary should consistently route one way.
- Empty directories / single-file corpora route to CPU.

---

### VAL-ROUTE-002: Large corpus routes to native GPU when GPU available and proven faster

**Assertion:** When corpus size > 50MB, GPU is available, and crossover calibration data shows GPU is faster, smart routing selects the native GPU engine.

**Verification:**
1. Ensure crossover calibration data exists showing GPU wins above threshold.
2. Create a test corpus > 50MB (e.g., 100MB of log files).
3. Run `tg search PATTERN PATH` without explicit overrides.
4. Check output: `routing_backend` should be `NativeGpuBackend` (or native GPU identity).
5. Confirm `routing_reason` references measured calibration data.

**Boundary conditions:**
- If crossover calibration shows GPU never wins (current state per `docs/gpu_crossover.md`), routing must NOT select GPU even for large corpora.
- If GPU is available but uncalibrated, routing must NOT auto-select GPU.

---

### VAL-ROUTE-003: Repeated queries route to index on warm index

**Assertion:** When a warm, non-stale trigram index exists and the query is index-compatible (pattern ≥ 3 bytes, no `-v`, no `-C`, no `-m`, no `-w`, no `-g`), routing selects TrigramIndex regardless of corpus size.

**Verification:**
1. Build an index: `tg search --index PATTERN PATH`.
2. Run a second search without `--index` flag.
3. Check `--verbose` stderr: `routing_backend=TrigramIndex`, `routing_reason=index-accelerated`.
4. Confirm the index path takes priority over both CPU and GPU auto-routing.

**Existing coverage:** `rust_core/tests/test_index.rs::test_tg_search_auto_routes_to_warm_index`, `rust_core/tests/test_routing.rs::test_routing_warm_index_auto_routes_when_query_is_compatible`.

---

### VAL-ROUTE-004: AST patterns route to AstBackend

**Assertion:** `tg run` commands always route to `AstBackend` regardless of corpus size, GPU availability, or index state.

**Verification:**
1. Run `tg run "def $FUNC():" PATH --json`.
2. Check output: `routing_backend=AstBackend`, `routing_reason=ast-native`.
3. Confirm this holds even if `--gpu-device-ids` is also passed (AST takes priority).
4. Confirm this holds even if a warm index exists.

**Existing coverage:** `rust_core/tests/test_ast_backend.rs`, `docs/routing_policy.md` ("AST run -> always AstBackend").

---

### VAL-ROUTE-005: GPU fallback to CPU when GPU unavailable

**Assertion:** When smart routing would select GPU but no GPU is available (no CUDA devices, driver missing, or GPU init fails), routing falls back to native CPU without error.

**Verification:**
1. Set `CUDA_VISIBLE_DEVICES=""` or ensure no GPU is present.
2. Run `tg search PATTERN PATH` on a large corpus that would normally trigger GPU routing.
3. Check output: `routing_backend` should be a CPU backend, not GPU.
4. Confirm `routing_reason` indicates fallback (e.g., `cpu-native-gpu-unavailable`).
5. Exit code should be 0 (successful search, not an error).

**Boundary conditions:**
- GPU driver crash during init should be caught and fall back gracefully.
- Partial GPU availability (some devices unusable, like RTX 5070 with sm_120) should fall back for those specific devices.

---

### VAL-ROUTE-006: Native CPU fallback to rg subprocess when native CPU has issues

**Assertion:** When native CPU search encounters an unexpected error and rg is available on PATH, routing falls back to rg subprocess passthrough.

**Verification:**
1. Simulate native CPU backend failure (e.g., corrupt memmap, unsupported regex).
2. Confirm rg is on PATH.
3. Run `tg search PATTERN PATH`.
4. Check output: `routing_backend` should indicate rg passthrough.
5. Confirm `routing_reason` indicates fallback (e.g., `rg-fallback-native-cpu-error`).
6. Search results should still be correct.

**Boundary conditions:**
- If rg is NOT on PATH either, the search should fail with a clear error message, not hang or silently return empty results.
- Fallback should preserve all user-specified flags (`-i`, `-F`, `-c`, etc.).

---

### VAL-ROUTE-007: Crossover calibration measures CPU vs GPU and stores thresholds

**Assertion:** An explicit calibration step measures native CPU vs native GPU search times across multiple corpus sizes and stores the crossover thresholds to a persistent config file.

**Verification:**
1. Run crossover calibration command (e.g., `tg calibrate` or benchmark script).
2. Confirm it produces timing data for at least 4 corpus sizes (1MB, 10MB, 100MB, 1GB).
3. Confirm thresholds are written to a config file (e.g., `~/.config/tensor-grep/crossover.json` or `.tg_crossover`).
4. Confirm the threshold file includes: corpus size breakpoint, CPU median time, GPU median time, crossover recommendation.
5. If GPU never wins (current state), confirm the stored recommendation is "cpu-always" or equivalent.

**Boundary conditions:**
- Calibration on a system with no GPU should complete without error and store "cpu-only" thresholds.
- Stale calibration data (from before a GPU driver update) should be detectable.

---

### VAL-ROUTE-008: routing_reason populated for all backend selections

**Assertion:** Every search result (in JSON, NDJSON, verbose, and MCP tool responses) includes a non-empty `routing_reason` string explaining why the backend was selected.

**Verification:**
1. For each backend path (native CPU, native GPU, index, AST, rg passthrough, GPU sidecar):
   a. Run a search that triggers that backend.
   b. Check `--json` output: `routing_reason` is a non-empty string.
   c. Check `--verbose` stderr: `routing_reason=<value>` is present.
2. Confirm `routing_reason` is never `null`, `""`, or `"unknown"` in successful searches.

**Existing coverage:** `rust_core/tests/test_schema_compat.rs` validates `routing_reason` is non-empty for all example artifacts. Tests across `test_cpu_backend.py`, `test_torch_backend.py`, `test_ripgrep_backend.py`, etc. assert specific reason strings.

---

### VAL-ROUTE-009: routing_backend populated for all backend selections

**Assertion:** Every search result includes a non-empty `routing_backend` string identifying which backend handled the search.

**Verification:**
1. For each backend path, run a search and check `--json` output.
2. `routing_backend` must be one of the known backend names: `NativeCpuBackend`, `NativeGpuBackend`, `CpuBackend`, `RipgrepBackend`, `TrigramIndex`, `GpuSidecar`, `AstBackend`, `AstGrepWrapperBackend`, `TorchBackend`, `CuDFBackend`, `CybertBackend`, `StringZillaBackend`, `RustCoreBackend`.
3. `routing_backend` must never be `null` or empty.

**Existing coverage:** `rust_core/tests/test_schema_compat.rs::assert_common_envelope` checks `routing_backend` non-empty. `tests/e2e/snapshots/` contains golden JSON with specific backend names.

---

### VAL-ROUTE-010: --force-cpu overrides auto-routing

**Assertion:** When `--force-cpu` (or `--cpu` flag) is passed, routing always selects a CPU backend regardless of corpus size, GPU availability, or index state.

**Verification:**
1. Set up conditions that would normally trigger GPU routing (large corpus, GPU available, calibration says GPU wins).
2. Run `tg search --cpu PATTERN PATH --json`.
3. Confirm `routing_backend` is a CPU backend (not GPU, not index).
4. Confirm `routing_reason` includes `force_cpu` (e.g., `force_cpu_rust`, `force_cpu_python_cpu`).

**Existing coverage:** `src/tensor_grep/core/pipeline.py:182-188` handles `force_cpu` flag. Pipeline tests verify CPU selection when `force_cpu=True`.

---

### VAL-ROUTE-011: --gpu-device-ids overrides auto-routing

**Assertion:** When `--gpu-device-ids` is passed, routing selects GPU backend for the specified devices, bypassing size-based auto-routing and index auto-routing.

**Verification:**
1. Build a warm index and have a small corpus that would normally route to CPU or index.
2. Run `tg search --gpu-device-ids 0 PATTERN PATH`.
3. Confirm `routing_backend` is a GPU backend.
4. Confirm `routing_reason` references explicit GPU device selection (e.g., `gpu-device-ids-explicit`).

**Existing coverage:** `docs/routing_policy.md` documents `--gpu-device-ids` takes priority over warm index auto-routing. `rust_core/tests/test_routing.rs` tests explicit GPU routing.

**Priority ordering (per `docs/routing_policy.md`):**
`--index` > `--gpu-device-ids` > warm index auto-route > `--json` CPU search > cold rg passthrough.

---

### VAL-ROUTE-012: --index overrides auto-routing

**Assertion:** When `--index` is passed, routing selects TrigramIndex, bypassing GPU routing and size-based auto-routing.

**Verification:**
1. Set up conditions that would normally trigger GPU routing.
2. Run `tg search --index PATTERN PATH --json`.
3. Confirm `routing_backend=TrigramIndex`, `routing_reason=index-accelerated`.

**Existing coverage:** `rust_core/tests/test_routing.rs`, `docs/routing_policy.md` documents `--index` as highest-priority explicit override.

---

### VAL-ROUTE-013: No auto-route to GPU without measured benchmark proof

**Assertion:** Smart routing MUST NOT auto-select GPU unless crossover calibration data exists AND shows GPU is at least 20% faster than CPU for the estimated workload size.

**Verification:**
1. Delete any crossover calibration data.
2. Run `tg search PATTERN PATH` on a very large corpus (1GB+).
3. Confirm routing does NOT select GPU (routes to CPU instead).
4. Confirm `routing_reason` does not mention GPU.
5. Run calibration, but with results showing GPU is slower (current state per `docs/gpu_crossover.md`).
6. Confirm subsequent searches still route to CPU.

**Rationale:** Per `docs/gpu_crossover.md`, the current GPU path never beats rg/CPU at any size up to 1GB. The 20% threshold matches the existing `analyze_gpu_auto_recommendation()` function in `benchmarks/run_gpu_benchmarks.py`.

---

### VAL-ROUTE-014: Index + GPU integration (index narrows, GPU searches candidates)

**Assertion:** When both index and GPU are available, the system can use index to narrow candidate files and then use GPU for the final search on those candidates.

**Verification:**
1. Build a trigram index on a large corpus.
2. Configure routing to use index+GPU hybrid mode.
3. Run a search and confirm:
   a. Index is queried first (visible in `--verbose` output).
   b. Candidate files from index are passed to GPU backend.
   c. Final results are correct (match count equals CPU-only search).
4. Confirm `routing_reason` reflects the hybrid approach (e.g., `index-gpu-hybrid`).

**Boundary conditions:**
- If index narrows to 0 files, no GPU work should be dispatched.
- If GPU fails mid-search, fall back to CPU for remaining candidates.

---

### VAL-ROUTE-015: Harness loop works with all backend paths

**Assertion:** The full agent harness loop (search → plan → apply → verify) completes successfully regardless of which search backend is selected for the search phase.

**Verification:**
1. For each backend (native CPU, rg passthrough, index, GPU if available):
   a. Run `tg run "def $FUNC():" PATH --json` (search phase uses AstBackend always for AST patterns).
   b. Run `tg run --rewrite "def $FUNC():" "def $FUNC() -> None:" PATH --json` (plan phase).
   c. Run `tg run --rewrite "..." "..." --apply --json PATH` (apply + verify phase).
   d. Confirm all phases complete with valid JSON and correct `routing_backend`/`routing_reason`.
2. The harness loop benchmark (`benchmarks/run_harness_loop_benchmark.py`) should pass with all backends.

**Existing coverage:** `VAL-CROSS-010` (E2E agent workflow uses unified JSON), `benchmarks/run_harness_loop_benchmark.py`.

---

## Part 2: Cross-Area Assertions (VAL-CROSS-NNN)

### VAL-CROSS-001: Search results identical between native CPU and rg passthrough

**Assertion:** For any given pattern and corpus, native CPU search produces byte-identical match content and identical match counts compared to rg passthrough.

**Verification:**
1. For at least 5 representative patterns (literal, regex, case-insensitive, fixed-string, with context lines):
   a. Run `tg search --json PATTERN PATH` via native CPU backend.
   b. Run `rg --json PATTERN PATH` directly.
   c. Compare: total_matches, matched_file_paths, per-file match counts, and match line text.
2. All must be identical.

**Boundary conditions:**
- Unicode patterns (CJK, emoji, combining characters).
- Binary files (should be skipped by both).
- Files with mixed line endings (CRLF, LF).
- Very long lines (> 64KB).
- Empty files.
- Symlinks.

---

### VAL-CROSS-002: Search results identical between native GPU and native CPU

**Assertion:** For any given pattern and corpus, native GPU search produces identical match counts and match content compared to native CPU search.

**Verification:**
1. For at least 3 patterns at each of 3 corpus sizes (10MB, 100MB, 1GB):
   a. Run via native GPU: `tg search --gpu-device-ids 0 --json PATTERN PATH`.
   b. Run via native CPU: `tg search --force-cpu --json PATTERN PATH`.
   c. Compare total_matches and per-file match counts.
2. All must be identical.

**Existing evidence:** `docs/gpu_crossover.md` shows 3-pattern correctness parity between GPU sidecar and rg for 10MB corpus.

**Boundary conditions:**
- Multi-line matches.
- Regex with backreferences (should fail consistently on both or be handled identically).
- Very large single files (> 1GB) where GPU memory pressure matters.

---

### VAL-CROSS-003: JSON envelope correct for all backends

**Assertion:** All JSON outputs from every backend include the unified v1 envelope fields: `version` (u32), `routing_backend` (non-empty string), `routing_reason` (non-empty string), `sidecar_used` (bool).

**Verification:**
1. For each backend (NativeCpuBackend, NativeGpuBackend, TrigramIndex, AstBackend, RipgrepBackend, GpuSidecar):
   a. Run a search with `--json`.
   b. Parse JSON output.
   c. Assert `version` is integer ≥ 1.
   d. Assert `routing_backend` is non-empty string.
   e. Assert `routing_reason` is non-empty string.
   f. Assert `sidecar_used` is boolean.
2. For new native backends: `sidecar_used` must be `false` (no Python subprocess).

**Existing coverage:** `rust_core/tests/test_schema_compat.rs` validates all 5 committed example artifacts. `tests/unit/test_harness_api_docs.py` validates harness API contract.

---

### VAL-CROSS-004: --json output works with native CPU engine

**Assertion:** `tg search --json PATTERN PATH` produces valid, parseable JSON when using the native CPU engine.

**Verification:**
1. Run `tg search --json ERROR bench_data` with native CPU backend active.
2. Parse stdout as JSON.
3. Confirm required fields: `total_matches`, `total_files`, `matches[]`, `routing_backend`, `routing_reason`.
4. Confirm `matches[].file`, `matches[].line_number`, `matches[].text` are present for each match.
5. Confirm the output is valid single-document JSON (not concatenated).

---

### VAL-CROSS-005: --ndjson output works with native CPU engine

**Assertion:** `tg search --ndjson PATTERN PATH` produces valid newline-delimited JSON when using the native CPU engine.

**Verification:**
1. Run `tg search --ndjson ERROR bench_data` with native CPU backend active.
2. Each line of stdout must be valid JSON when parsed independently.
3. Confirm per-match records include `file`, `line_number`, `text`.
4. Confirm a summary/metadata record includes `routing_backend`, `routing_reason`.
5. Confirm `--json` and `--ndjson` are mutually exclusive (conflict error if both passed).

---

### VAL-CROSS-006: --json output works with native GPU engine

**Assertion:** `tg search --gpu-device-ids 0 --json PATTERN PATH` produces valid JSON with correct envelope when using the native GPU engine.

**Verification:**
1. Run with GPU available and `--gpu-device-ids 0 --json`.
2. Parse stdout as JSON.
3. Confirm unified envelope fields (version, routing_backend, routing_reason, sidecar_used).
4. For native GPU: `sidecar_used` must be `false`.
5. Confirm `routing_gpu_device_ids` includes the requested device ID(s).

---

### VAL-CROSS-007: Existing AST workflows unaffected by search engine changes

**Assertion:** All existing `tg run`, `tg scan`, `tg test`, and `tg new` AST workflows produce identical results before and after the native CPU/GPU engine changes.

**Verification:**
1. Run the AST benchmark suite: `python benchmarks/run_ast_benchmarks.py`.
2. Confirm tg/sg ratio is within the accepted threshold (≤ 3.0).
3. Run `tg run "def $FUNC():" PATH --json` and confirm routing is still `AstBackend`/`ast-native`.
4. Run `tg run --rewrite "..." "..." --apply --json PATH` and confirm rewrite plan/apply/verify cycle works.
5. Run `cargo test` in `rust_core/` — all AST-related tests must pass.

**Existing coverage:** `benchmarks/run_ast_benchmarks.py`, `benchmarks/run_ast_workflow_benchmarks.py`, `rust_core/tests/test_ast_backend.rs`, `rust_core/tests/test_ast_rewrite.rs`.

---

### VAL-CROSS-008: Existing MCP tools work with new search backends

**Assertion:** All MCP server tools (`tg_search`, `tg_run`, `tg_rewrite_plan`, `tg_rewrite_apply`, `tg_index_search`) continue to function correctly with the new native backends.

**Verification:**
1. Run `uv run pytest tests/unit/test_mcp_server.py -q` — all tests must pass.
2. For `tg_search` tool: confirm it accepts and returns results with correct `routing_backend` and `routing_reason`.
3. For `tg_index_search` tool: confirm it returns `routing_backend=TrigramIndex`.
4. For `tg_run` / `tg_rewrite_plan` / `tg_rewrite_apply`: confirm `routing_backend=AstBackend`.
5. Confirm MCP tool responses include `routing_backend` and `routing_reason` fields (per `SKILL.md` requirement).

**Existing coverage:** `tests/unit/test_mcp_server.py` (currently 10+ test functions validating routing metadata in MCP responses).

---

### VAL-CROSS-009: Benchmark regression check passes

**Assertion:** No existing benchmark scenario becomes slower after the native engine changes. Regression threshold: 5% (default per `benchmarks/check_regression.py`).

**Verification:**
1. Run `python benchmarks/run_benchmarks.py --output artifacts/bench_run_benchmarks.json`.
2. Run `python benchmarks/check_regression.py --baseline auto --current artifacts/bench_run_benchmarks.json`.
3. Exit code must be 0 (no regressions detected).
4. Run `python benchmarks/run_hot_query_benchmarks.py --output artifacts/bench_hot_query_benchmarks.json`.
5. Confirm hot-query regression status is PASS.

**Existing coverage:** CI workflow (`.github/workflows/ci.yml`) includes `benchmark-regression` job.

---

### VAL-CROSS-010: Index search results consistent with native CPU search

**Assertion:** TrigramIndex search results are a superset of or identical to native CPU search results for the same pattern (index may pre-filter files but final match validation must agree with CPU).

**Verification:**
1. Build index: `tg search --index PATTERN PATH`.
2. Run indexed search: `tg search PATTERN PATH --json` (auto-routes to warm index).
3. Run native CPU search: `tg search --force-cpu PATTERN PATH --json`.
4. Compare `total_matches` — must be identical.
5. Compare per-file match counts — must be identical.

**Existing coverage:** `benchmarks/run_index_scaling_benchmark.py` includes indexed-vs-plain search parity checks.

---

### VAL-CROSS-011: GPU multi-file batch results match file-by-file CPU results

**Assertion:** When GPU processes multiple files in a single batch (entire corpus in GPU memory), the results must match processing each file individually via CPU.

**Verification:**
1. Create a corpus with 100+ files.
2. Run `tg search --gpu-device-ids 0 --json PATTERN PATH` (GPU batch mode).
3. Run `tg search --force-cpu --json PATTERN PATH` (CPU file-by-file).
4. Compare total_matches and per-file match counts — must be identical.
5. Specifically verify that batch boundary handling doesn't drop matches at file boundaries.

**Boundary conditions:**
- Files with 0 matches should appear in both or neither result set consistently.
- Files with matches at the very first or very last byte.
- Mix of very small (1 byte) and very large (100MB) files in the same batch.

---

### VAL-CROSS-012: Large file (1GB) search produces identical results CPU vs GPU

**Assertion:** Searching a single 1GB file produces identical results whether using native CPU or native GPU.

**Verification:**
1. Generate or use a 1GB test file with known match count.
2. Run via native CPU: `tg search --force-cpu --json PATTERN FILE`.
3. Run via native GPU: `tg search --gpu-device-ids 0 --json PATTERN FILE`.
4. Compare `total_matches` — must be identical.
5. Spot-check match line numbers and text for first/last 10 matches.

**Boundary conditions:**
- File larger than GPU memory (should trigger chunked processing or graceful fallback).
- Pattern that matches on every line (stress test for result collection).
- Pattern that matches 0 times (should return 0 consistently).

---

### VAL-CROSS-013: Error messages consistent format across all backends

**Assertion:** Error messages from all backends follow a consistent format with the same structure, enabling reliable parsing by harness/agent systems.

**Verification:**
1. Trigger common error conditions for each backend:
   a. File not found.
   b. Permission denied.
   c. Invalid regex pattern.
   d. GPU device not available (GPU backends only).
   e. Index corrupt/missing (index backend only).
2. For each error:
   a. Confirm exit code is non-zero.
   b. Confirm error message goes to stderr (not stdout).
   c. Confirm error message format is parseable (consistent prefix/structure).
   d. Confirm JSON output (if `--json` was specified) includes error indication rather than malformed JSON.

**Boundary conditions:**
- Concurrent errors (multiple files with permission denied) should all be reported.
- Backend-specific errors should still include `routing_backend` context where possible.
- Error messages must not leak internal paths, stack traces, or sensitive information to stdout.

---

## Assertion Summary

### Routing Assertions

| ID | Title | Priority |
|---|---|---|
| VAL-ROUTE-001 | Small corpus routes to native CPU | P0 |
| VAL-ROUTE-002 | Large corpus routes to GPU when proven faster | P0 |
| VAL-ROUTE-003 | Repeated queries route to warm index | P0 |
| VAL-ROUTE-004 | AST patterns always route to AstBackend | P0 |
| VAL-ROUTE-005 | GPU fallback to CPU when unavailable | P0 |
| VAL-ROUTE-006 | Native CPU fallback to rg when native has issues | P1 |
| VAL-ROUTE-007 | Crossover calibration measures and stores thresholds | P1 |
| VAL-ROUTE-008 | routing_reason always populated | P0 |
| VAL-ROUTE-009 | routing_backend always populated | P0 |
| VAL-ROUTE-010 | --force-cpu overrides auto-routing | P0 |
| VAL-ROUTE-011 | --gpu-device-ids overrides auto-routing | P0 |
| VAL-ROUTE-012 | --index overrides auto-routing | P0 |
| VAL-ROUTE-013 | No auto-route to GPU without measured proof | P0 |
| VAL-ROUTE-014 | Index + GPU hybrid integration | P2 |
| VAL-ROUTE-015 | Harness loop works with all backends | P1 |

### Cross-Area Assertions

| ID | Title | Priority |
|---|---|---|
| VAL-CROSS-001 | Native CPU vs rg result parity | P0 |
| VAL-CROSS-002 | Native GPU vs native CPU result parity | P0 |
| VAL-CROSS-003 | JSON envelope correct for all backends | P0 |
| VAL-CROSS-004 | --json works with native CPU | P0 |
| VAL-CROSS-005 | --ndjson works with native CPU | P0 |
| VAL-CROSS-006 | --json works with native GPU | P0 |
| VAL-CROSS-007 | AST workflows unaffected | P0 |
| VAL-CROSS-008 | MCP tools work with new backends | P0 |
| VAL-CROSS-009 | Benchmark regression check passes | P0 |
| VAL-CROSS-010 | Index search consistent with native CPU | P0 |
| VAL-CROSS-011 | GPU multi-file batch vs CPU file-by-file parity | P1 |
| VAL-CROSS-012 | 1GB file identical results CPU vs GPU | P1 |
| VAL-CROSS-013 | Consistent error message format across backends | P1 |

---

## Dependencies and Pre-requisites

1. **Native CPU engine** must be implemented before VAL-ROUTE-001, VAL-CROSS-001, VAL-CROSS-004, VAL-CROSS-005.
2. **Native GPU engine** must be implemented before VAL-ROUTE-002, VAL-CROSS-002, VAL-CROSS-006, VAL-CROSS-011, VAL-CROSS-012.
3. **Smart routing logic** must be implemented before VAL-ROUTE-001 through VAL-ROUTE-006.
4. **Crossover calibration** must be implemented before VAL-ROUTE-007 and VAL-ROUTE-013.
5. **Index + GPU hybrid** (VAL-ROUTE-014) may be deferred to a later milestone if the integration proves complex.

## Existing Test Coverage Map

| Validation Area | Existing Tests |
|---|---|
| Routing decisions | `rust_core/tests/test_routing.rs` (14 integration tests) |
| Index auto-routing | `rust_core/tests/test_index.rs` (3 auto-route tests) |
| Schema compat | `rust_core/tests/test_schema_compat.rs` (5 example artifacts) |
| CPU backend | `tests/unit/test_cpu_backend.py` (15+ tests) |
| GPU backends | `tests/unit/test_torch_backend.py`, `tests/unit/test_cudf_backend.py` |
| AST backend | `rust_core/tests/test_ast_backend.rs`, `tests/unit/test_ast_backend.py` |
| MCP server | `tests/unit/test_mcp_server.py` (10+ tests) |
| Routing policy | `tests/unit/test_routing_policy_docs.py` |
| E2E snapshots | `tests/e2e/snapshots/test_output_snapshots/` |
| Benchmark regression | `benchmarks/check_regression.py`, CI workflow |
