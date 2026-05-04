# tensor-grep Session Handoff

Last updated: 2026-05-04

## Current Release State

- Latest released version: `v1.8.11`
- Latest release commit: `05e6d95 chore(release): v1.8.11 [skip ci]`
- Latest fix commit: `636e8ff fix: harden files-with-matches rg routing`
- GitHub release: <https://github.com/oimiragieo/tensor-grep/releases/tag/v1.8.11>
- CI run `25296218480`: passed
- CodeQL run `25296218031`: passed
- Local `uv run tg --version`: `tensor-grep 1.8.11`

## What v1.8.11 Fixed

- Windows `--files-with-matches` no longer expands huge candidate file lists into the ripgrep subprocess argv, avoiding `WinError 206`.
- `tg safeParseJSON --files-with-matches`, `tg search safeParseJSON --files-with-matches`, and `tg search --fixed-strings safeParseJSON . --files-with-matches` complete through the root-based rg route.
- Ripgrep backend fallback now parses non-JSON `--files-with-matches` output instead of treating it like match text.
- Plain path-list output uses one trailing LF and preserves `-0/--null` path-list behavior.
- Count plus `-0/--null` parsing is covered.
- `tg ast-info --json` now exposes AST grammar inventory for agents without scraping text help.

## Verified Before Release Closeout

- `uv run ruff check .`: passed
- `uv run ruff format --check --preview .`: passed
- `uv run mypy src/tensor_grep`: passed
- `uv run pytest -q`: `1820 passed, 16 skipped`
- `python benchmarks/run_benchmarks.py --output artifacts/bench_run_benchmarks_v1811_files_with_matches_final.json`: passed parity rows
- `python benchmarks/check_regression.py --baseline auto --current artifacts/bench_run_benchmarks_v1811_files_with_matches_final.json --allow-env-mismatch`: no `tg` regressions detected
- Live smoke probes for no-path/root `--files-with-matches`, `-0`, and `ast-info --json`: passed
- External Spark review after fixes: no blockers reported

## What Works Well Now

- Scoped text search, JSON, NDJSON, multi-root search, globs, `--column`, `--vimgrep`, `--path-separator`, `--type-list`, and invalid-regex diagnostics are stable enough for agent workflows.
- `defs`, `source`, `refs`, `callers`, `context-render`, and `blast-radius` are useful for scoped repo navigation and planning.
- Symbol outputs are compact on hits and no-matches; CommonJS symbol extraction and reference dedupe are materially improved.
- Bounded blast-radius defaults and output-limit metadata make scoped impact checks safer for agent loops.
- MCP entrypoint is present via `tg mcp --help`; MCP tool behavior is covered by the repo tests.
- GPU devices are detected locally; GPU routing remains benchmark-governed and should not be marketed as automatic crossover.

## Known Weak Spots

- `rg` remains the raw cold exact-text benchmark. `tg` should win on agent-native code intelligence, not by pretending every grep workload is faster.
- Broad generated roots can still be agent-hostile. Scope paths and use `--max-repo-files`, `--max-callers`, and `--max-files` before broad impact analysis.
- `impact --symbol` is still less trustworthy than `blast-radius` for direct symbol impact.
- `validation_commands` can still be generic. Treat targeted commands as hints, not proof of full coverage.
- Local `uv run tg doctor --json` can find a stale in-tree standalone binary at `rust_core/target/release/tg.exe`; in the latest check it reported `tg 1.8.3` while the Python package expected `1.8.11`. Rebuild with `C:/Users/oimir/.cargo/bin/cargo.exe build --release` or pin `TG_NATIVE_TG_BINARY` before trusting standalone-native diagnostics.
- Broad `tg search --files ...` over generated artifact trees on Windows can still hit a legacy-console Unicode encoding failure. Scope file-list commands or use UTF-8-safe output until that path-list bug is fixed.

## Next Highest-Value Work

1. Fix Windows Unicode path-list output for broad `tg search --files`.
2. Add progress, partial output, or stronger guardrails for broad generated-root scans.
3. Calibrate or de-emphasize `impact --symbol` so agents prefer `blast-radius` for direct impact.
4. Improve `doctor` diagnostics when the in-tree standalone native binary is stale.
5. Keep dogfooding `tg` first and record exact failing commands, exit codes, and outputs as product evidence.

## Safe Next-Session Commands

```powershell
git status --short --branch
git log -3 --oneline
uv run tg --version
uv run tg doctor --json
uv run tg search --fixed-strings "safeParseJSON" src tests docs -C 2
uv run pytest tests/unit/test_ripgrep_backend.py tests/unit/test_cli_modes.py tests/unit/test_ast_parity.py -q
```

Avoid broad file-list probes like this until the Windows path-list encoding issue is fixed:

```powershell
tg search --files "AGENTS.md" . --hidden
```
