# User Testing Guide — tensor-grep

## Surface

CLI tool (`tg.exe` binary at `rust_core/target/release/tg.exe`). All validation runs via shell commands (PowerShell on Windows).

## Tools

- **Execute** (shell commands) for all CLI validation
- `tg.exe` binary: `C:\dev\projects\tensor-grep\rust_core\target\release\tg.exe`
- `sg` (ast-grep): available via `C:\Users\oimir\AppData\Roaming\npm\sg.CMD`
- `rg` (ripgrep): available at `C:\dev\projects\tensor-grep\benchmarks\ripgrep-14.1.0-x86_64-pc-windows-msvc\rg.exe`
- `hyperfine`: available at `C:\Users\oimir\.cargo\bin\hyperfine.exe`
- Python: `python` (3.14.0), run with `uv run` for project dependencies
- Corpus generator: `benchmarks/gen_corpus.py`
- Benchmark scripts: `benchmarks/run_*.py`

## Environment

- OS: Windows 10 (build 26200), PowerShell
- Python 3.14.0 via uv
- No services needed — pure CLI tool
- Working directory: `C:\dev\projects\tensor-grep`
- PYTHONPATH: set `$env:PYTHONPATH = 'src'` when needed

## Key Artifacts

- `artifacts/bench_ast_multilang.json` — multi-language AST benchmark results
- `artifacts/bench_harness_loop.json` — harness loop benchmark results
- `artifacts/bench_index_scaling.json` — index scaling benchmark results
- `artifacts/bench_rewrite_5k.json` — large-scale rewrite benchmark results (5k files)
- `artifacts/bench_run_ast_benchmarks.json` — existing AST benchmark baseline

## Validation Concurrency

- CLI validators: max 5 concurrent (CLI invocations are lightweight, ~200MB RAM each)
- Corpus generation: max 3 concurrent (disk I/O bound, ~100MB each)
- Overall machine headroom: ~80GB free, 16 logical CPUs

## Flow Validator Guidance: CLI

### Isolation Rules
- Each flow validator should use its own temp directory for any corpus generation
- Do NOT modify files in `artifacts/` — read-only for validation
- Do NOT modify benchmark scripts in `benchmarks/` — read-only
- Use `$env:PYTHONPATH = 'src'` for Python imports

### Testing Approach
- For corpus generators: generate to a temp dir, validate file existence and syntax
- For benchmark artifacts: read existing JSON artifacts and validate structure/fields
- For benchmark scripts: run them with `--help` or smoke-test parameters if needed
- Use `sg` (ast-grep) to validate generated source files are syntactically valid

### Boundaries
- No shared state between validators
- Each validator can create temp dirs under `C:\dev\projects\tensor-grep\artifacts\` with a unique prefix
- Clean up temp dirs after validation
- Do NOT run full benchmark suites (expensive) — validate existing artifacts and smoke-test as needed

## Flow Validator Guidance: routing-and-safety

### Routing Assertions (VAL-ROUTE-*)
- Use `tg.exe` at `C:\dev\projects\tensor-grep\rust_core\target\release\tg.exe`
- For `--verbose` routing checks, routing info goes to stderr
- For `--json` routing checks, check `routing_backend` and `routing_reason` in JSON stdout
- `bench_data` directory at `C:\dev\projects\tensor-grep\bench_data` has 4 files including 2 large server logs (~112MB each)
- For index tests: build index with `tg.exe search --index "ERROR" bench_data`, then subsequent searches should auto-route to TrigramIndex
- Index file is `.tg_index` in the search directory (e.g., `bench_data\.tg_index`)
- GPU sidecar may not be available — GPU tests may need to be marked "blocked" if Python GPU backends are not set up
- The `docs/routing_policy.md` file documents all routing decisions
- For routing test suite (VAL-ROUTE-010): use `cargo test --test test_routing` in `rust_core/`

### Safety Assertions (VAL-SAFE-*)
- Safety tests require creating temporary test files with specific content
- Use `artifacts\val-safety\` as the working directory for safety test files
- For BOM tests: create files starting with `\xEF\xBB\xBF` (UTF-8 BOM bytes)
- For CRLF tests: create files with `\r\n` line endings
- For binary tests: create files with NUL bytes in first 8192 bytes
- For large file tests: create a file > 100MB (e.g., 101MB)
- For stale-file tests: run plan, modify file, then try apply
- For atomic write tests: check no `.tg_tmp_*` files remain after operations
- For non-ASCII tests: use CJK characters (e.g., こんにちは)
- `tg run --rewrite REPLACEMENT PATTERN PATH` does dry-run (no file modification)
- `tg run --rewrite REPLACEMENT --apply PATTERN PATH` applies edits
- `tg run --rewrite REPLACEMENT --apply --verify PATTERN PATH` applies and verifies
- The Rust code for rewrite is in `rust_core/src/backend_ast.rs`

### Cross-Area Assertions
- VAL-CROSS-013: verify() uses byte-level exact matching — verify by running apply+verify where replacement doesn't match original pattern

## Flow Validator Guidance: gpu-and-index-scaling

### GPU Assertions (VAL-GPU-*)
- `tg.exe` binary: `C:\dev\projects\tensor-grep\rust_core\target\release\tg.exe`
- GPU crossover docs: `C:\dev\projects\tensor-grep\docs\gpu_crossover.md`
- GPU benchmark artifact: `C:\dev\projects\tensor-grep\artifacts\bench_gpu_scale.json`
- GPU benchmark script: `C:\dev\projects\tensor-grep\benchmarks\run_gpu_benchmarks.py`
- GPU Python backends may be unavailable — known pre-existing issue. If `tg.exe search --gpu-device-ids 0` errors with "GPU backends unavailable", GPU sidecar tests that require actual GPU search should be marked "blocked" UNLESS the assertion specifically tests error handling (VAL-GPU-002, VAL-GPU-003, VAL-GPU-004, VAL-GPU-008 test error paths and can be validated even without working GPU).
- For VAL-GPU-001: validate docs/gpu_crossover.md has measured numbers for >=4 corpus sizes
- For VAL-GPU-002: test `tg.exe search --gpu-device-ids 99 "ERROR" .` → non-zero exit, clear stderr
- For VAL-GPU-003: test `$env:CUDA_VISIBLE_DEVICES=""; tg.exe search --gpu-device-ids 0 "ERROR" .` → non-zero exit, clear error
- For VAL-GPU-004: sidecar crash test — may need to simulate by killing sidecar process mid-search
- For VAL-GPU-005: check `tg.exe search --help` for --gpu-auto, and read docs/gpu_crossover.md for justification
- For VAL-GPU-006: compare GPU vs CPU match counts for >=3 patterns (if GPU available)
- For VAL-GPU-007: validate bench_gpu_scale.json has >=4 corpus sizes
- For VAL-GPU-008: test malformed sidecar output handling (the error handling code is in Rust)
- Working directory for temp files: `C:\dev\projects\tensor-grep\artifacts\val-gpu\`
- Use `bench_data` directory for search tests

### Index Assertions (VAL-IDX-*)
- `tg.exe` binary: `C:\dev\projects\tensor-grep\rust_core\target\release\tg.exe`
- Index file: `.tg_index` in the search directory
- Index format: starts with `TGI\x00` + version byte (5 bytes total)
- For compression tests (VAL-IDX-001): use run_index_scaling_benchmark or build index on a test corpus, compare sizes
- For incremental tests (VAL-IDX-002/003/004): create a temp corpus, build index, modify/add/remove files, rebuild, verify
- For regex tests (VAL-IDX-005/009/010): run `tg.exe search --index` with regex patterns, compare results to non-indexed search
- For scaling test (VAL-IDX-006): generate 10k+ file corpus, build index, measure time, verify correctness
- For existing tests (VAL-IDX-007): run `cargo test` in rust_core/ and `uv run pytest -k index -q`
- For format compatibility (VAL-IDX-008/011): check that old-format indices trigger rebuild with warning
- Working directory for temp corpus: `C:\dev\projects\tensor-grep\artifacts\val-index\`
- Use gen_corpus.py for large corpus generation: `uv run python benchmarks/gen_corpus.py --kind text-bench --out <dir> --files N`
- IMPORTANT: Clean up .tg_index files and temp dirs after tests
- Cargo path: `C:\Users\oimir\.cargo\bin\cargo.exe`

### Cross-Area (gpu-and-index-scaling)
- VAL-CROSS-003: No benchmark regression — validate bench artifacts, run check_regression.py
- VAL-CROSS-011: Index format magic bytes — hex dump first 5 bytes of .tg_index file after M4 changes

## Flow Validator Guidance: harness-workflow

### MCP Tool Assertions (VAL-WORK-001..004, VAL-WORK-009, VAL-WORK-010, VAL-WORK-013)
- MCP server is at `src/tensor_grep/cli/mcp_server.py`
- Run MCP tools via: `cd C:\dev\projects\tensor-grep && $env:PYTHONPATH='src'; uv run python -c "import asyncio; from tensor_grep.cli.mcp_server import <tool>; print(asyncio.run(<tool>(...)))"`
- Alternatively, use the `mcp` module's test client or call tool functions directly
- MCP tests exist in `tests/unit/test_mcp_server.py` — run with `uv run pytest tests/unit/test_mcp_server.py -v`
- Each MCP tool must return JSON with `routing_backend` and `routing_reason` fields
- For VAL-WORK-013: invoke with invalid paths/patterns, verify structured error (not traceback)
- For VAL-CROSS-002: MCP tool responses must match v1 JSON schema from docs/examples/

### NDJSON & JSON Assertions (VAL-WORK-005, VAL-WORK-006, VAL-WORK-011)
- `tg.exe` binary: `C:\dev\projects\tensor-grep\rust_core\target\release\tg.exe`
- `--ndjson` flag on `tg search` emits one JSON object per line
- `--json` flag unchanged — single JSON document
- `--json` and `--ndjson` together → error
- `tg run --rewrite --ndjson` → error
- Use `bench_data` directory for search targets
- Parse each line of ndjson output independently with `json.loads()`

### Batch Rewrite Assertions (VAL-WORK-007, VAL-WORK-008, VAL-WORK-012)
- `tg run --batch-rewrite <config.json> PATH`
- Config schema: `{"rewrites": [{"pattern": "...", "replacement": "...", "lang": "..."}], "verify": true|false}`
- Create temp dirs for batch rewrite tests — never test on actual project files
- For overlap tests: create patterns that produce overlapping edits
- Invalid configs (missing fields, wrong types) → clear error

### Cross-Area Assertions (harness-workflow)
- VAL-CROSS-001: Schema compat test passes — `cargo test` in rust_core/ includes schema compat test
- VAL-CROSS-004: `tg run` never spawns Python — test with Python unavailable or check process tree
- VAL-CROSS-005: Dry-run rewrite never modifies files — check mtimes before/after
- VAL-CROSS-006: Overlap validation before write — overlapping edits → rejected, file unchanged
- VAL-CROSS-007: Batch rewrite uses atomic writes (same path as M3)
- VAL-CROSS-008: BOM/CRLF/binary safety in batch rewrite
- VAL-CROSS-009: Stale-file detection in batch rewrite
- VAL-CROSS-010: E2E agent workflow (search → plan → apply → verify) uses unified v1 JSON
- VAL-CROSS-012: All test suites pass — cargo test >= 95 passed, pytest >= 510 passed, ruff clean, mypy clean

### Isolation Rules (harness-workflow)
- Each flow validator uses its own temp directory under `C:\dev\projects\tensor-grep\artifacts\val-hw-<group>\`
- Do NOT modify project source files
- Do NOT modify existing artifacts
- Clean up temp dirs and .tg_index files after validation
- MCP tool validators share no state with CLI validators

### GPU Assertions (harness-workflow)
- VAL-GPU-006: GPU search correctness parity with CPU. If GPU Python backends unavailable, validate by reading bench_gpu_scale.json for historical correctness evidence. If available, run live comparison for >= 3 patterns.
