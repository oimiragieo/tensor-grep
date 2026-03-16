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
