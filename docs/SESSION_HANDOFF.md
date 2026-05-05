# tensor-grep Session Handoff

Last updated: 2026-05-05

## Current Release State

- Latest released version: `v1.8.19`
- Latest release commit: `92c66ef chore(release): v1.8.19 [skip ci]`
- Latest fix commit: `a5fa279 fix: write WSL bash shims with LF newlines`
- GitHub release: <https://github.com/oimiragieo/tensor-grep/releases/tag/v1.8.19>
- Main CI run `25355804591`: passed through `publish-success-gate`
- Main CodeQL run `25355804637`: passed
- Release-commit CodeQL run `25356194065`: passed
- Local managed `tg --version`: `tensor-grep 1.8.19`
- PyPI latest and pinned install: `tensor-grep==1.8.19` resolves from PyPI
- Public Windows installer `TENSOR_GREP_VERSION=1.8.19`: installed `tensor-grep==1.8.19`, refreshed managed shims, and left profiled PowerShell, `cmd`, `pwsh -NoProfile`, Git Bash, and WSL resolving `1.8.19`.
- Public WSL shim dogfood: `/mnt/c/Users/oimir/bin/tg` has an LF-only shebang, `wsl bash -lc 'tg --version'` prints `tensor-grep 1.8.19`, and WSL `tg search "class|def" ...` works from `/mnt/c/dev/projects/tensor-grep`.

## Release Completion Contract

A branch push or open PR starts PR CI only. It is not a release, not a released version, and not complete release state.

Release versioning starts only after a release-bearing PR is squash-merged to `main`, because semantic-release reads the final `main` commit subject.

A release-bearing PR is complete only after PR CI passes, the PR is squash-merged to `main`, main CI and semantic-release complete successfully, the release commit and tag exist on `origin/main`, `publish-success-gate` passes, `git fetch origin main --tags` is run, agents fast-forward local `main` to the release commit, and PyPI/public installer availability is verified.

Do not report final version state before the GitHub release, PyPI/package publish status, public install/update path, and local checkout have all been verified.

For docs/test/chore-only work, use a non-release PR title, wait for PR CI, and merge only when requested or clearly required. After merge, main CI should pass, but semantic-release should skip release publishing.

## What v1.8.12-v1.8.19 Fixed

- Windows `--files-with-matches` no longer expands huge candidate file lists into the ripgrep subprocess argv, avoiding `WinError 206`.
- No-path `--files-with-matches` now preserves raw rg-style paths such as `AGENTS.md` instead of emitting `.\AGENTS.md`.
- `tg doctor --json` reports PATH tg candidates, first PATH version, and mismatch state so agents can detect stale command resolution.
- Windows installers prepend managed shim directories ahead of stale Python Scripts entries.
- Windows installers remove stale same-directory `tg.com`, `tg.exe`, `tg.bat`, and `tg.ps1` launchers before writing managed shims, avoiding PATHEXT shadowing.
- Windows installers place extras before pinned version specifiers, for example `tensor-grep[gpu-win,nlp,ast]==1.8.19`, so pinned installs actually install the package.
- Windows installers now install argv-safe PowerShell shims, a `.cmd` shim for `cmd.exe`, and a no-extension Git Bash / WSL shim; managed launchers force UTF-8 mode.
- The `.cmd` shim now enters a Python bridge instead of directly expanding raw `%*` into a child command, preserving quoted regex metacharacters for normal `cmd.exe` use.
- No-extension bash shims are WSL-aware: WSL gets `/mnt/c/...` paths and Git Bash gets `/c/...` paths.
- Generated bash shims are written with LF newlines so WSL does not see `/usr/bin/env: 'bash\r'` or pass a trailing CR through `"$@"`.
- Windows installers now uninstall the tensor-grep Python package that owns a stale `Python*\Scripts\tg.exe` when direct stale-launcher removal cannot clear a PATH shadow.
- Python path-list output uses the UTF-8-safe stdout path and preserves discovery order for `--files-with-matches` fallback output.
- PATH-entry scans skip inaccessible machine PATH directories instead of aborting installation after package install.
- `tg safeParseJSON --files-with-matches`, `tg search safeParseJSON --files-with-matches`, and `tg search --fixed-strings safeParseJSON . --files-with-matches` complete through the root-based rg route.
- Ripgrep backend fallback now parses non-JSON `--files-with-matches` output instead of treating it like match text.
- Plain path-list output uses one trailing LF and preserves `-0/--null` path-list behavior.
- Count plus `-0/--null` parsing is covered.
- `tg ast-info --json` exposes AST language identifiers for agents without scraping text help.

## Verified Before Release Closeout

- PR #39 `fix: harden Windows and WSL installer shims`: merged and released as `v1.8.18`
- PR #40 `fix: write WSL bash shims with LF newlines`: merged and released as `v1.8.19`
- `uv run pytest tests/unit/test_install_scripts.py -q`: `18 passed` on the LF-shim fix branch
- PowerShell parser checks for `scripts/install.ps1` under both `pwsh` and Windows PowerShell: passed
- `git diff --check`: passed
- `uv run ruff check .`: passed
- `uv run ruff format --check --preview .`: passed
- `uv run mypy src/tensor_grep`: passed
- `uv run pytest -q`: `1840 passed, 16 skipped`
- Main CI run `25355804591`: passed through `publish-pypi`, `validate-pypi-artifacts`, and `publish-success-gate`
- Main CodeQL run `25355804637`: passed
- Release-commit CodeQL run `25356194065`: passed
- PyPI reports `tensor-grep 1.8.19` as latest and pinned `tensor-grep==1.8.19` resolves from PyPI.
- Public `v1.8.19` installer dogfood passed profiled PowerShell, `cmd`, `pwsh -NoProfile`, Git Bash, WSL version resolution, WSL LF-only shim inspection, PowerShell alternation through the normal `tg` shim, `cmd.exe` double-quoted alternation, and WSL alternation search.

## What Works Well Now

- Scoped text search, JSON, NDJSON, multi-root search, globs, `--column`, `--vimgrep`, `--path-separator`, `--type-list`, and invalid-regex diagnostics are stable enough for agent workflows.
- Normal PowerShell `tg`, `cmd /c tg`, `pwsh -NoProfile -Command "tg ..."`, Git Bash `tg`, and WSL `tg` resolve the public `1.8.19` install on this host.
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
- Root-scale `--files-with-matches`, `--count`, and `--force-cpu` can still differ from raw `rg` in output ordering even when the file set and counts match. Do not claim golden stdout order parity unless a test proves it for that mode.
- Directly invoking `C:\Users\oimir\bin\tg.cmd` from PowerShell with an unescaped metacharacter such as `|` is still a `cmd.exe` parser limitation; use normal PowerShell `tg` / `tg.ps1` or quote the metacharacter argument for `cmd.exe`.
- Always verify command resolution with `tg --version`, `cmd /c tg --version`, `pwsh -NoProfile -Command "tg --version"`, `where.exe tg`, `Get-Command tg -All`, and WSL `wsl bash -lc 'command -v tg; tg --version'` after installer changes. A stale `Python*\Scripts\tg.exe` returning an older tensor-grep version is a release blocker.

## Next Highest-Value Work

1. Add progress, partial output, or stronger guardrails for broad generated-root scans.
2. Calibrate or de-emphasize `impact --symbol` so agents prefer `blast-radius` for direct impact.
3. Improve `doctor` diagnostics when the in-tree standalone native binary is stale.
4. Decide whether root-scale search modes promise semantic parity or golden stdout order parity against `rg`, then test and document that contract.
5. Keep dogfooding `tg` first and record exact failing commands, exit codes, and outputs as product evidence.

## Safe Next-Session Commands

```powershell
git status --short --branch
git log -3 --oneline
uv run tg --version
uv run tg doctor --json
python -m pip index versions tensor-grep --index-url https://pypi.org/simple --no-cache-dir
gh release view v1.8.19 --json tagName,publishedAt,url
tg --version
cmd /c tg --version
pwsh -NoProfile -Command "tg --version"
where.exe tg
Get-Command tg -All
wsl bash -lc 'command -v tg; tg --version'
uv run tg search --fixed-strings "safeParseJSON" src tests docs -C 2
uv run pytest tests/unit/test_ripgrep_backend.py tests/unit/test_cli_modes.py tests/unit/test_ast_parity.py -q
```

Avoid broad generated-root file-list probes unless the task needs them:

```powershell
tg search --files "AGENTS.md" . --hidden
```
