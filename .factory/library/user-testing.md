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
