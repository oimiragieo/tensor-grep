# tensor-grep Session Handoff

Last updated: 2026-05-04

## Current Release State

- Latest released version: `v1.8.14`
- Latest release commit: `f6e2981 chore(release): v1.8.14 [skip ci]`
- Latest fix commit: `f98a6e4 fix: correct Windows installer pinned extras`
- GitHub release: <https://github.com/oimiragieo/tensor-grep/releases/tag/v1.8.14>
- CI run `25324763737`: passed
- CodeQL run `25324762648`: passed
- Local `uv run tg --version`: `tensor-grep 1.8.14`
- PyPI latest: `1.8.14`
- Public Windows installer `TENSOR_GREP_VERSION=1.8.14`: installed `tensor-grep==1.8.14`, refreshed managed shims, and fresh PowerShell/cmd PATH probes resolved `tg --version` to `tensor-grep 1.8.14`

## Release Completion Contract

A branch push or open PR starts PR CI only. It is not a release, not a released version, and not complete release state.

Release versioning starts only after a release-bearing PR is squash-merged to `main`, because semantic-release reads the final `main` commit subject.

A release-bearing PR is complete only after PR CI passes, the PR is squash-merged to `main`, main CI and semantic-release complete successfully, the release commit and tag exist on `origin/main`, `publish-success-gate` passes, `git fetch origin main --tags` is run, agents fast-forward local `main` to the release commit, and PyPI/public installer availability is verified.

Do not report final version state before the GitHub release, PyPI/package publish status, public install/update path, and local checkout have all been verified.

## What v1.8.12-v1.8.14 Fixed

- Windows `--files-with-matches` no longer expands huge candidate file lists into the ripgrep subprocess argv, avoiding `WinError 206`.
- No-path `--files-with-matches` now preserves raw rg-style paths such as `AGENTS.md` instead of emitting `.\AGENTS.md`.
- `tg doctor --json` reports PATH tg candidates, first PATH version, and mismatch state so agents can detect stale command resolution.
- Windows installers prepend managed shim directories ahead of stale Python Scripts entries.
- Windows installers remove stale same-directory `tg.com`, `tg.exe`, `tg.bat`, and `tg.ps1` launchers before writing `tg.cmd`, avoiding PATHEXT shadowing.
- Windows installers place extras before pinned version specifiers, for example `tensor-grep[gpu-win,nlp,ast]==1.8.14`, so pinned installs actually install the package.
- `tg safeParseJSON --files-with-matches`, `tg search safeParseJSON --files-with-matches`, and `tg search --fixed-strings safeParseJSON . --files-with-matches` complete through the root-based rg route.
- Ripgrep backend fallback now parses non-JSON `--files-with-matches` output instead of treating it like match text.
- Plain path-list output uses one trailing LF and preserves `-0/--null` path-list behavior.
- Count plus `-0/--null` parsing is covered.
- `tg ast-info --json` exposes AST language identifiers for agents without scraping text help.

## Verified Before Release Closeout

- `uv run ruff check .`: passed
- `uv run ruff format --check --preview .`: passed
- `uv run mypy src/tensor_grep`: passed
- `uv run pytest -q`: `1825 passed, 16 skipped` on the v1.8.14 installer fix branch before PR merge
- `python benchmarks/run_benchmarks.py --output artifacts/bench_run_benchmarks_v1811_files_with_matches_final.json`: passed parity rows
- `python benchmarks/check_regression.py --baseline auto --current artifacts/bench_run_benchmarks_v1811_files_with_matches_final.json --allow-env-mismatch`: no `tg` regressions detected
- Live smoke probes for no-path/root `--files-with-matches`, `-0`, and `ast-info --json`: passed
- External Spark review after fixes: no blockers reported
- PR #34 `fix: correct Windows installer pinned extras`: merged and released as `v1.8.14`
- Main CI run `25324763737`: passed through `publish-success-gate`
- CodeQL run `25324762648`: passed
- PyPI JSON and `pip index` reported latest `1.8.14`
- Public `v1.8.14` installer dogfood completed and fresh PATH probes in PowerShell and cmd resolved `tg --version` to `tensor-grep 1.8.14`

## What Works Well Now

- Scoped text search, JSON, NDJSON, multi-root search, globs, `--column`, `--vimgrep`, `--path-separator`, `--type-list`, and invalid-regex diagnostics are stable enough for agent workflows.
- `defs`, `source`, `refs`, `callers`, `context-render`, and `blast-radius` are useful for scoped repo navigation and planning.
- Symbol outputs are compact on hits and no-matches; CommonJS symbol extraction and reference dedupe are materially improved.
- Bounded blast-radius defaults and output-limit metadata make scoped impact checks safer for agent loops.
- MCP entrypoint is present via `tg mcp --help`; MCP tool behavior is covered by the repo tests.
- GPU devices are detected locally; GPU routing remains benchmark-governed and should not be marketed as automatic crossover.

## Known Weak Spots

- `rg` remains the raw cold exact-text benchmark. `tg` should win on agent-native code intelligence, not by pretending every grep workload is faster.
- Broad generated roots can still be agent-hostile. Use scoped paths, globs, file types, and `--max-depth` for `tg search`; `--max-repo-files`, `--max-callers`, and `--max-files` are code-intelligence command budgets, not `tg search` flags.
- `impact --symbol` is still less trustworthy than `blast-radius` for direct symbol impact.
- `validation_commands` can still be generic. Treat targeted commands as hints, not proof of full coverage.
- Local `uv run tg doctor --json` can find a stale in-tree standalone binary at `rust_core/target/release/tg.exe`. Rebuild with `C:/Users/oimir/.cargo/bin/cargo.exe build --release` or pin `TG_NATIVE_TG_BINARY` before trusting standalone-native diagnostics.
- Broad `tg search --files ...` over generated artifact trees can still be expensive. The managed Windows launchers and Python path-list output should force UTF-8, but scope file-list commands to the smallest useful root.

## Next Highest-Value Work

1. Add progress, partial output, or stronger guardrails for broad generated-root scans.
2. Calibrate or de-emphasize `impact --symbol` so agents prefer `blast-radius` for direct impact.
3. Improve `doctor` diagnostics when the in-tree standalone native binary is stale.
4. Keep dogfooding `tg` first and record exact failing commands, exit codes, and outputs as product evidence.

## Safe Next-Session Commands

```powershell
git status --short --branch
git log -3 --oneline
uv run tg --version
uv run tg doctor --json
python -m pip index versions tensor-grep --no-cache-dir
gh release view v1.8.14 --json tagName,publishedAt,url
uv run tg search --fixed-strings "safeParseJSON" src tests docs -C 2
uv run pytest tests/unit/test_ripgrep_backend.py tests/unit/test_cli_modes.py tests/unit/test_ast_parity.py -q
```

Avoid broad generated-root file-list probes unless the task needs them:

```powershell
tg search --files "AGENTS.md" . --hidden
```
