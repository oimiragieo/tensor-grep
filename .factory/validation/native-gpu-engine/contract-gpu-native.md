# Validation Contract: Native GPU Engine (M2) & Advanced GPU (M3)

**Date drafted:** 2026-03-16
**Supersedes:** VAL-GPU-001 through VAL-GPU-008 (Python sidecar GPU path)
**Scope:** Replace Python sidecar GPU with native Rust CUDA via `cudarc` crate (NVRTC JIT)
**Target hardware:** RTX 4070 (compute 8.9) + RTX 5070 (compute 12.0)

---

## M2 — Native GPU Engine: Basic Functionality

### VAL-GPU-001: GPU substring search correctness parity with CPU

- **Title:** GPU native search returns identical matches to CPU path
- **Behavior:** For a set of ≥5 literal patterns on a 10MB corpus, `tg search --gpu-device-ids 0 --json <pattern> <corpus>` returns the same `total_matches`, `total_files`, and per-file match lines as `tg search --json <pattern> <corpus>` (CPU path).
- **Pass condition:** For every tested pattern, `total_matches` and `total_files` are identical between GPU and CPU outputs, and the sorted list of `(file, line_number, matched_text)` tuples is byte-identical.
- **Fail condition:** Any pattern produces a difference in match count, file count, or matched content.
- **Evidence:** Side-by-side JSON diff for each pattern saved to `artifacts/val-gpu-native/VAL-GPU-001-correctness.json`.

### VAL-GPU-002: GPU search on single file works

- **Title:** GPU search accepts and correctly processes a single file target
- **Behavior:** `tg search --gpu-device-ids 0 --json <pattern> <single-file>` produces correct matches for the given file, without error or fallback.
- **Pass condition:** Exit code 0, JSON output contains matches only from the specified file, match count agrees with CPU path.
- **Fail condition:** Non-zero exit, incorrect matches, error message, or silent fallback to CPU without `routing_backend=gpu_native` in output.
- **Evidence:** Terminal output saved to `artifacts/val-gpu-native/VAL-GPU-002-single-file.txt`.

### VAL-GPU-003: GPU search on directory works (batched multi-file)

- **Title:** GPU search on a directory batches multiple files into a single GPU operation
- **Behavior:** `tg search --gpu-device-ids 0 --json <pattern> <directory-with-100+-files>` produces correct results. The search processes files in batched GPU transfers rather than one-at-a-time.
- **Pass condition:** Exit code 0, `total_files` matches CPU path, `total_matches` matches CPU path, and `--verbose` output shows batch transfer evidence (e.g., files-per-batch > 1 or bulk transfer log).
- **Fail condition:** Incorrect match count, files missing from results, or evidence of per-file GPU dispatch.
- **Evidence:** JSON output + verbose log saved to `artifacts/val-gpu-native/VAL-GPU-003-batched-dir.txt`.

### VAL-GPU-004: --gpu-device-ids flag selects specific GPU

- **Title:** Explicit device ID selection routes to the specified GPU
- **Behavior:** `tg search --gpu-device-ids 1 --verbose --json <pattern> <corpus>` routes the search to device 1 (RTX 5070), not device 0 (RTX 4070). Running with `--gpu-device-ids 0` routes to device 0.
- **Pass condition:** `--verbose` stderr shows the selected device ordinal and device name matching the expected GPU for both device 0 and device 1.
- **Fail condition:** Search runs on a different device than requested, or device selection is ignored.
- **Evidence:** Verbose output for device 0 and device 1 saved to `artifacts/val-gpu-native/VAL-GPU-004-device-select.txt`.

### VAL-GPU-005: GPU auto-routing activates for files >50MB threshold

- **Title:** Smart routing sends large workloads to GPU automatically
- **Behavior:** Without explicit `--gpu-device-ids`, searching a corpus >50MB auto-routes to GPU when a CUDA device is available. Searching a corpus <50MB stays on CPU.
- **Pass condition:** For a 100MB corpus, `--verbose` shows `routing_backend=gpu_native` and `routing_reason` mentions size threshold. For a 10MB corpus, `--verbose` shows `routing_backend=CpuBackend`.
- **Fail condition:** 100MB corpus stays on CPU, or 10MB corpus routes to GPU, or auto-routing occurs when no GPU is present.
- **Evidence:** Verbose output for both corpus sizes saved to `artifacts/val-gpu-native/VAL-GPU-005-auto-routing.txt`.

### VAL-GPU-006: GPU falls back to CPU when no CUDA available

- **Title:** Graceful CPU fallback when CUDA runtime is absent
- **Behavior:** With `CUDA_VISIBLE_DEVICES=""` (or on a machine with no GPU), `tg search --gpu-device-ids 0 --json <pattern> <corpus>` either falls back to CPU with a warning or exits with a clear error.
- **Pass condition:** Either (a) search completes with correct results and stderr contains a warning about GPU unavailability, or (b) exits with non-zero code and a user-facing error message (no panic, no stack trace). The `cudarc` dynamic loading must not crash on missing `libcuda.so`/`nvcuda.dll`.
- **Fail condition:** Panic, segfault, stack trace, or silent incorrect results.
- **Evidence:** Terminal output saved to `artifacts/val-gpu-native/VAL-GPU-006-no-cuda-fallback.txt`.

### VAL-GPU-007: GPU falls back to CPU when GPU init fails

- **Title:** Graceful degradation on CUDA initialization failure
- **Behavior:** When CUDA is present but GPU initialization fails (e.g., insufficient driver version, device in exclusive-process mode), `tg search --gpu-device-ids 0 <pattern> <corpus>` handles the failure gracefully.
- **Pass condition:** Non-zero exit code with a clear error message naming the failure reason (e.g., "CUDA driver version insufficient"), no panic or raw error codes leaked to user.
- **Fail condition:** Panic, segfault, or cryptic CUDA error code without explanation.
- **Evidence:** Simulated or real failure output saved to `artifacts/val-gpu-native/VAL-GPU-007-init-fail.txt`.

### VAL-GPU-008: GPU search scales across corpus sizes

- **Title:** GPU search functions correctly at 10MB, 100MB, 500MB, and 1GB corpus sizes
- **Behavior:** `tg search --gpu-device-ids 0 --json <pattern> <corpus>` produces correct results at each of 4 corpus sizes: 10MB, 100MB, 500MB, 1GB.
- **Pass condition:** Correctness parity with CPU at all 4 sizes (match count and file count identical). No OOM, no timeout, no truncated results.
- **Fail condition:** Any size produces incorrect results, OOM crash, timeout, or partial output.
- **Evidence:** Per-size correctness comparison saved to `artifacts/val-gpu-native/VAL-GPU-008-scale-correctness.json`.

### VAL-GPU-009: GPU beats CPU at measured crossover point

- **Title:** Native GPU path achieves measured speedup over rg at crossover corpus size
- **Behavior:** Benchmark `tg search --gpu-device-ids 0 <pattern> <corpus>` against `rg <pattern> <corpus>` at corpus sizes from 10MB to 1GB (minimum 4 sizes). Identify the crossover point where GPU median time < rg median time.
- **Pass condition:** There exists a corpus size C ≥ 50MB where GPU median latency is ≤ rg median latency, with ≥3 samples per measurement. The crossover size is documented.
- **Fail condition:** GPU is slower than rg at all measured sizes up to 1GB, indicating the native path has not eliminated Python sidecar overhead.
- **Evidence:** Benchmark JSON with per-size timing data saved to `artifacts/val-gpu-native/VAL-GPU-009-crossover-bench.json`. Crossover analysis documented in `docs/gpu_crossover.md` (updated).

### VAL-GPU-010: Host-to-device transfer uses single bulk copy

- **Title:** File data is transferred to GPU in bulk, not per-line
- **Behavior:** For a multi-file directory search, the GPU path copies file contents to device memory in a single (or batched) `cuMemcpyHtoD` call per batch, not per-line or per-match.
- **Pass condition:** Code inspection confirms bulk copy pattern. `--verbose` or instrumented build shows transfer count ≪ line count (e.g., ≤ file count for single-file-per-transfer, or ≤ batch count for batched transfer). For a 1GB corpus with 291K lines, transfer count must be < 1000.
- **Fail condition:** Transfer count approaches line count, or code review shows per-line copy pattern.
- **Evidence:** Code path audit + instrumented verbose output saved to `artifacts/val-gpu-native/VAL-GPU-010-bulk-transfer.txt`.

### VAL-GPU-011: GPU results include routing metadata

- **Title:** JSON output includes `routing_backend="gpu_native"` and `routing_reason`
- **Behavior:** `tg search --gpu-device-ids 0 --json <pattern> <corpus>` returns JSON envelope with `routing_backend` set to `"gpu_native"` (not `"GpuSidecar"`) and a non-empty `routing_reason` field.
- **Pass condition:** `routing_backend == "gpu_native"` and `routing_reason` is a non-empty string in the JSON output.
- **Fail condition:** `routing_backend` is missing, set to `"GpuSidecar"`, or `routing_reason` is empty/missing.
- **Evidence:** JSON output saved to `artifacts/val-gpu-native/VAL-GPU-011-routing-metadata.txt`.

---

## M3 — Advanced GPU

### VAL-GPU-012: CUDA streams overlap I/O and compute

- **Title:** CUDA stream pipelining overlaps host-to-device copy with kernel execution
- **Behavior:** When searching a multi-file corpus, the GPU backend uses ≥2 CUDA streams so that data transfer for batch N+1 overlaps with kernel execution on batch N.
- **Pass condition:** Code inspection confirms ≥2 streams with overlapped scheduling. Profiled or instrumented run shows wall-clock time < (sum of all transfer times + sum of all kernel times), indicating overlap. Alternatively, `nsys` / CUDA event timing shows concurrent copy+compute.
- **Fail condition:** Single stream used, or all transfers complete before any kernel launches.
- **Evidence:** Code audit + profiling data saved to `artifacts/val-gpu-native/VAL-GPU-012-cuda-streams.txt`.

### VAL-GPU-013: Pinned memory used for host-to-device transfers

- **Title:** Host buffers use CUDA pinned (page-locked) memory for transfers
- **Behavior:** The GPU backend allocates host-side transfer buffers via `cuMemAllocHost` (pinned memory) rather than pageable `malloc`.
- **Pass condition:** Code inspection confirms `cuMemAllocHost` or `cudarc` pinned-memory API usage for transfer buffers. Transfer throughput at 1GB corpus is ≥ 10 GB/s (PCIe 4.0 x16 theoretical ~22 GB/s, pinned typically achieves 12-20 GB/s vs 4-8 GB/s pageable).
- **Fail condition:** Pageable memory used, or measured transfer throughput < 5 GB/s at 1GB.
- **Evidence:** Code audit + transfer bandwidth measurement saved to `artifacts/val-gpu-native/VAL-GPU-013-pinned-memory.txt`.

### VAL-GPU-014: Multi-pattern search on GPU

- **Title:** GPU backend supports searching for multiple patterns in a single pass
- **Behavior:** `tg search --gpu-device-ids 0 --json -e "pattern1" -e "pattern2" -e "pattern3" <corpus>` executes all patterns in a single GPU dispatch, not three sequential dispatches.
- **Pass condition:** Correctness: combined results match union of per-pattern CPU results. Performance: wall-clock time for 3-pattern search is < 2x single-pattern time (not 3x). Code confirms single kernel launch for multi-pattern.
- **Fail condition:** Multi-pattern produces incorrect union, or time scales linearly with pattern count (sequential dispatch), or per-pattern kernel launches observed.
- **Evidence:** Correctness comparison + timing data saved to `artifacts/val-gpu-native/VAL-GPU-014-multi-pattern.json`.

### VAL-GPU-015: Multi-GPU support splits work across devices

- **Title:** Work is distributed across RTX 4070 + RTX 5070 with `--gpu-device-ids 0,1`
- **Behavior:** `tg search --gpu-device-ids 0,1 --json <pattern> <1GB-corpus>` distributes files across both GPUs and merges results.
- **Pass condition:** `--verbose` output shows both device 0 and device 1 active. Total matches equal CPU path. Both devices process a non-trivial share of files (neither device processes < 10% of total files). Results are correctly merged (no duplicates, no missing files).
- **Fail condition:** One device does all work, results are incorrect after merge, duplicate matches, or missing files.
- **Evidence:** Verbose output + correctness comparison saved to `artifacts/val-gpu-native/VAL-GPU-015-multi-gpu.txt`.

### VAL-GPU-016: Warp-parallel search for long lines

- **Title:** Lines exceeding warp width are searched collaboratively by multiple threads
- **Behavior:** For files containing lines > 1024 characters, the GPU kernel distributes line scanning across a warp (32 threads) rather than assigning one thread per byte position.
- **Pass condition:** Code inspection confirms warp-cooperative search logic (e.g., `__shfl_sync`, shared memory reduction, or thread-group collaboration per line). Correctness: search on a file with 10KB lines produces correct matches matching CPU path. Performance: search on 1GB corpus with long lines is not slower than corpus with short lines.
- **Fail condition:** No warp-parallel logic in kernel, or long-line corpus produces incorrect results or significant performance degradation vs short-line corpus.
- **Evidence:** Kernel code audit + long-line correctness test saved to `artifacts/val-gpu-native/VAL-GPU-016-warp-parallel.txt`.

### VAL-GPU-017: CUDA graphs reduce kernel launch overhead for batched files

- **Title:** CUDA graph capture eliminates per-batch kernel launch overhead
- **Behavior:** For repeated searches (e.g., same pattern across many batches of files), the GPU backend uses CUDA graph capture to replay the kernel launch sequence without per-launch driver overhead.
- **Pass condition:** Code confirms `cuGraphLaunch` or equivalent `cudarc` graph API usage. Latency for 100-batch search with CUDA graphs is measurably lower than without (≥10% reduction in total kernel launch overhead, measured via profiling or A/B comparison).
- **Fail condition:** No CUDA graph usage, or graphs provide no measurable launch overhead reduction.
- **Evidence:** Code audit + A/B timing saved to `artifacts/val-gpu-native/VAL-GPU-017-cuda-graphs.txt`.

### VAL-GPU-018: GPU throughput at 100MB+ is 10x+ over rg

- **Title:** Native GPU path achieves ≥10x throughput over rg at large corpus sizes
- **Behavior:** Benchmark `tg search --gpu-device-ids 0 <pattern> <corpus>` against `rg <pattern> <corpus>` at 100MB, 500MB, and 1GB corpus sizes.
- **Pass condition:** At ≥1 corpus size ≥ 100MB, GPU median throughput (bytes/second) is ≥ 10x rg median throughput, measured over ≥5 samples. Based on bandwidth math: GPU memory bandwidth 504 GB/s should enable ~2ms for 1GB scan vs rg's ~200ms.
- **Fail condition:** GPU throughput is < 10x rg at all measured sizes ≥ 100MB.
- **Evidence:** Benchmark JSON with throughput calculations saved to `artifacts/val-gpu-native/VAL-GPU-018-throughput-10x.json`.

### VAL-GPU-019: Multi-GPU throughput exceeds single GPU

- **Title:** Two-GPU search is faster than single-GPU search
- **Behavior:** Compare `tg search --gpu-device-ids 0 <pattern> <1GB-corpus>` (single GPU) vs `tg search --gpu-device-ids 0,1 <pattern> <1GB-corpus>` (dual GPU) at 1GB.
- **Pass condition:** Dual-GPU median latency is < 0.85x single-GPU median latency (≥15% improvement), measured over ≥5 samples. Both produce identical match counts.
- **Fail condition:** Dual-GPU is slower than or equal to single-GPU, or results differ.
- **Evidence:** A/B benchmark saved to `artifacts/val-gpu-native/VAL-GPU-019-multi-gpu-throughput.json`.

---

## Error Handling

### VAL-GPU-020: Invalid device ID produces clear error message

- **Title:** Non-existent GPU device ID produces a user-facing error
- **Behavior:** `tg search --gpu-device-ids 99 <pattern> <corpus>` where device 99 does not exist.
- **Pass condition:** Non-zero exit code. Stderr contains a message mentioning device ID 99 and listing available device IDs. No panic, no stack trace, no raw CUDA error code.
- **Fail condition:** Panic, stack trace, raw error code, or silent fallback without notification.
- **Evidence:** Terminal output saved to `artifacts/val-gpu-native/VAL-GPU-020-invalid-device.txt`.

### VAL-GPU-021: CUDA out-of-memory handling

- **Title:** GPU OOM produces a clear error and does not crash the process
- **Behavior:** When a corpus exceeds available GPU VRAM (e.g., attempting to allocate 24GB on a 12GB GPU without chunking), the GPU backend detects the OOM condition.
- **Pass condition:** Non-zero exit code with error message mentioning "out of memory" or "insufficient GPU memory" and suggesting a smaller corpus or CPU fallback. No segfault, no CUDA unrecoverable error state. Process exits cleanly (no orphaned GPU contexts).
- **Fail condition:** Segfault, CUDA fatal error, process hang, or corrupt output.
- **Evidence:** OOM simulation output saved to `artifacts/val-gpu-native/VAL-GPU-021-oom.txt`.

### VAL-GPU-022: NVRTC kernel compilation failure handling

- **Title:** Kernel compilation failure produces a clear error
- **Behavior:** If NVRTC JIT compilation fails (e.g., targeting an unsupported compute capability, or corrupted kernel source), the error is caught and reported.
- **Pass condition:** Non-zero exit code with error message mentioning "kernel compilation" or "NVRTC" and the specific failure reason. No panic. User is advised to check CUDA toolkit compatibility.
- **Fail condition:** Panic, raw NVRTC error code without explanation, or silent fallback that hides the compilation failure.
- **Evidence:** Simulated compilation failure output saved to `artifacts/val-gpu-native/VAL-GPU-022-nvrtc-fail.txt`.

### VAL-GPU-023: Timeout for long-running GPU operations

- **Title:** GPU operations that exceed timeout threshold are terminated
- **Behavior:** If a GPU kernel or transfer exceeds a configurable timeout (default ≤30s), the operation is cancelled and the process exits cleanly.
- **Pass condition:** After timeout, process exits with non-zero code and error message mentioning "timeout" and the elapsed duration. No GPU resource leak (context is destroyed). Timeout is configurable via environment variable or flag.
- **Fail condition:** Process hangs indefinitely, GPU resources leaked, or no timeout mechanism exists.
- **Evidence:** Timeout simulation output saved to `artifacts/val-gpu-native/VAL-GPU-023-timeout.txt`.

### VAL-GPU-024: Malformed/corrupt file graceful handling on GPU

- **Title:** Binary and malformed files do not crash the GPU path
- **Behavior:** `tg search --gpu-device-ids 0 --json <pattern> <directory-with-binary-and-corrupt-files>` where the directory contains: (a) a binary file with NUL bytes, (b) a file with invalid UTF-8 sequences, (c) a zero-byte empty file, (d) a file with a single line of 10MB with no newline.
- **Pass condition:** Exit code 0. Binary files are either skipped (with appropriate log) or searched without crash. Invalid UTF-8 files are handled (searched or skipped). Empty files produce no matches. Single-long-line file produces correct matches. No segfault, no GPU kernel abort, no corrupt output. JSON output is valid.
- **Fail condition:** Segfault, kernel abort, invalid JSON output, process crash on any input file type.
- **Evidence:** Directory contents + JSON output saved to `artifacts/val-gpu-native/VAL-GPU-024-malformed-files.txt`.

---

## Summary

| ID | Milestone | Category | Title |
|---|---|---|---|
| VAL-GPU-001 | M2 | Correctness | GPU substring search correctness parity with CPU |
| VAL-GPU-002 | M2 | Functionality | GPU search on single file works |
| VAL-GPU-003 | M2 | Functionality | GPU search on directory works (batched multi-file) |
| VAL-GPU-004 | M2 | Functionality | --gpu-device-ids flag selects specific GPU |
| VAL-GPU-005 | M2 | Routing | GPU auto-routing activates for files >50MB threshold |
| VAL-GPU-006 | M2 | Fallback | GPU falls back to CPU when no CUDA available |
| VAL-GPU-007 | M2 | Fallback | GPU falls back to CPU when GPU init fails |
| VAL-GPU-008 | M2 | Scale | GPU search scales across corpus sizes (10MB–1GB) |
| VAL-GPU-009 | M2 | Performance | GPU beats CPU at measured crossover point |
| VAL-GPU-010 | M2 | Architecture | Host-to-device transfer uses single bulk copy |
| VAL-GPU-011 | M2 | Contract | GPU results include routing metadata |
| VAL-GPU-012 | M3 | Performance | CUDA streams overlap I/O and compute |
| VAL-GPU-013 | M3 | Performance | Pinned memory used for transfers |
| VAL-GPU-014 | M3 | Functionality | Multi-pattern search on GPU |
| VAL-GPU-015 | M3 | Functionality | Multi-GPU support splits work across devices |
| VAL-GPU-016 | M3 | Performance | Warp-parallel search for long lines |
| VAL-GPU-017 | M3 | Performance | CUDA graphs reduce launch overhead |
| VAL-GPU-018 | M3 | Performance | GPU throughput at 100MB+ is 10x+ over rg |
| VAL-GPU-019 | M3 | Performance | Multi-GPU throughput exceeds single GPU |
| VAL-GPU-020 | Error | Safety | Invalid device ID error message |
| VAL-GPU-021 | Error | Safety | CUDA out-of-memory handling |
| VAL-GPU-022 | Error | Safety | NVRTC kernel compilation failure handling |
| VAL-GPU-023 | Error | Safety | Timeout for long-running GPU operations |
| VAL-GPU-024 | Error | Safety | Malformed/corrupt file graceful handling on GPU |

**Total assertions:** 24 (11 M2 + 8 M3 + 5 Error Handling)
