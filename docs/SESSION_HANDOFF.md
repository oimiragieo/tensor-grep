# tensor-grep Session Handoff

Last updated: 2026-05-04

## Current Release State

- Latest released version: `v1.8.17`
- Latest release commit: `c4e8498 chore(release): v1.8.17 [skip ci]`
- Latest fix commit: `e2ebbd2 fix: uninstall stale Python tg launcher owners`
- GitHub release: <https://github.com/oimiragieo/tensor-grep/releases/tag/v1.8.17>
- CI run `25344850358`: passed through `publish-success-gate`
- CodeQL run `25344849431`: passed
- Local managed `tg --version`: `tensor-grep 1.8.17`
- PyPI latest and pinned install: `tensor-grep==1.8.17` resolves from PyPI
- Public Windows installer `TENSOR_GREP_VERSION=1.8.17`: installed `tensor-grep==1.8.17`, removed stale managed-user shims, and left profiled PowerShell, `cmd`, and `pwsh -NoProfile` resolving `1.8.17`.

## Release Completion Contract

A branch push or open PR starts PR CI only. It is not a release, not a released version, and not complete release state.

Release versioning starts only after a release-bearing PR is squash-merged to `main`, because semantic-release reads the final `main` commit subject.

A release-bearing PR is complete only after PR CI passes, the PR is squash-merged to `main`, main CI and semantic-release complete successfully, the release commit and tag exist on `origin/main`, `publish-success-gate` passes, `git fetch origin main --tags` is run, agents fast-forward local `main` to the release commit, and PyPI/public installer availability is verified.

Do not report final version state before the GitHub release, PyPI/package publish status, public install/update path, and local checkout have all been verified.

## What v1.8.12-v1.8.17 Fixed

- Windows `--files-with-matches` no longer expands huge candidate file lists into the ripgrep subprocess argv, avoiding `WinError 206`.
- No-path `--files-with-matches` now preserves raw rg-style paths such as `AGENTS.md` instead of emitting `.\AGENTS.md`.
- `tg doctor --json` reports PATH tg candidates, first PATH version, and mismatch state so agents can detect stale command resolution.
- Windows installers prepend managed shim directories ahead of stale Python Scripts entries.
- Windows installers remove stale same-directory `tg.com`, `tg.exe`, `tg.bat`, and `tg.ps1` launchers before writing `tg.cmd`, avoiding PATHEXT shadowing.
- Windows installers place extras before pinned version specifiers, for example `tensor-grep[gpu-win,nlp,ast]==1.8.17`, so pinned installs actually install the package.
- Windows installers now install argv-safe PowerShell shims, a `.cmd` shim for `cmd.exe`, and a no-extension Git Bash shim; managed launchers force UTF-8 mode.
- Windows installers now uninstall the tensor-grep Python package that owns a stale `Python*\Scripts\tg.exe` when direct stale-launcher removal cannot clear a PATH shadow.
- Python path-list output uses the UTF-8-safe stdout path and preserves discovery order for `--files-with-matches` fallback output.
- PATH-entry scans skip inaccessible machine PATH directories instead of aborting installation after package install.
- `tg safeParseJSON --files-with-matches`, `tg search safeParseJSON --files-with-matches`, and `tg search --fixed-strings safeParseJSON . --files-with-matches` complete through the root-based rg route.
- Ripgrep backend fallback now parses non-JSON `--files-with-matches` output instead of treating it like match text.
- Plain path-list output uses one trailing LF and preserves `-0/--null` path-list behavior.
- Count plus `-0/--null` parsing is covered.
- `tg ast-info --json` exposes AST language identifiers for agents without scraping text help.

## Verified Before Release Closeout

- `uv run ruff check .`: passed
- `uv run ruff format --check --preview .`: passed
- `uv run mypy src/tensor_grep`: passed
- `uv run pytest -q`: `1835 passed, 16 skipped` on the v1.8.15 Windows launcher/path-list fix branch before PR merge
- `python benchmarks/run_benchmarks.py --output artifacts/bench_run_benchmarks_v1811_files_with_matches_final.json`: passed parity rows
- `python benchmarks/check_regression.py --baseline auto --current artifacts/bench_run_benchmarks_v1811_files_with_matches_final.json --allow-env-mismatch`: no `tg` regressions detected
- Live smoke probes for no-path/root `--files-with-matches`, `-0`, and `ast-info --json`: passed
- External Spark review after fixes: no blockers reported
- PR #35 `fix: harden Windows launchers and path-list output`: merged and released as `v1.8.15`
- PR #36 `fix: skip inaccessible PATH entries in Windows installer`: merged and released as `v1.8.16`
- PR #37 `fix: uninstall stale Python tg launcher owners`: merged and released as `v1.8.17`
- Main CI run `25344850358`: passed through `publish-success-gate`
- CodeQL run `25344849431`: passed
- PyPI reports `tensor-grep 1.8.17` as latest and pinned `tensor-grep==1.8.17` resolves from PyPI.
- Public `v1.8.17` installer dogfood passed cross-shell resolution, regex alternation through wrappers, and Windows legacy-encoding path-list smoke checks.

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
- Always verify Windows command resolution with `tg --version`, `cmd /c tg --version`, `pwsh -NoProfile -Command "tg --version"`, `where.exe tg`, and `Get-Command tg -All` after installer changes. A stale `Python*\Scripts\tg.exe` returning an older tensor-grep version is a release blocker.

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
gh release view v1.8.17 --json tagName,publishedAt,url
uv run tg search --fixed-strings "safeParseJSON" src tests docs -C 2
uv run pytest tests/unit/test_ripgrep_backend.py tests/unit/test_cli_modes.py tests/unit/test_ast_parity.py -q
```

Avoid broad generated-root file-list probes unless the task needs them:

```powershell
tg search --files "AGENTS.md" . --hidden
```
