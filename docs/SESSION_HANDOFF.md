# tensor-grep Session Handoff

Last updated: 2026-05-07

## Current Release State

- Latest released version: `v1.8.22`
- Latest release commit: `5a0d6d9 chore(release): v1.8.22 [skip ci]`
- Latest fix commit: `8a061ee fix: improve agent context trust and rg parity`
- GitHub release: <https://github.com/oimiragieo/tensor-grep/releases/tag/v1.8.22>
- Main CI run `25469910767`: passed through `publish-pypi`, artifact validation, and `publish-success-gate`
- Main CodeQL run `25469910279`: passed
- Release-commit CodeQL run `25470327515`: passed
- Local managed `tg --version`: `tensor-grep 1.8.22`
- PyPI latest and pinned install: `tensor-grep==1.8.22` resolves from PyPI
- Public update dogfood: `tg update` upgraded the managed install from `1.8.21` to `1.8.22`; profiled PowerShell, `cmd`, `pwsh -NoProfile`, Git Bash, and WSL resolved `tensor-grep 1.8.22`.
- Public shell dogfood: normal PowerShell, `cmd.exe`, Git Bash, and WSL regex alternation worked; `tg --version` prints one line by default, `tg --version --verbose` prints feature details, and help starts with `Usage: tg`.
- Public doctor dogfood: from outside the repo, `tg doctor --json` reported `version = 1.8.22`, `search_acceleration_backend = rust-core-extension`, and `path_tg_first_version_matches = true`.
- Repo-dev dogfood: `uv run tg doctor --json --no-lsp` reported `version = 1.8.22`, `native_tg_binary = null`, `rust_binary_version_status = stale-skipped`, `skipped_native_tg_binaries = 2`, and `search_acceleration_backend = rust-core-extension`.

## Release Completion Contract

A branch push or open PR starts PR CI only. It is not a release, not a released version, and not complete release state.

Release versioning starts only after a release-bearing PR is squash-merged to `main`, because semantic-release reads the final `main` commit subject.

A release-bearing PR is complete only after PR CI passes, the PR is squash-merged to `main`, main CI and semantic-release complete successfully, the release commit and tag exist on `origin/main`, `publish-success-gate` passes, `git fetch origin main --tags` is run, agents fast-forward local `main` to the release commit, and PyPI/public installer availability is verified.

Do not report final version state before the GitHub release, PyPI/package publish status, public install/update path, and local checkout have all been verified.

For docs/test/chore-only work, use a non-release PR title, wait for PR CI, and merge only when requested or clearly required. After merge, main CI should pass, but semantic-release should skip release publishing.

## What v1.8.12-v1.8.22 Fixed

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
- `tg --version` now prints one line by default for script-friendly version checks, while `tg --version --verbose` preserves feature/SIMD/Arrow details for humans.
- Installed CLI help now uses the public program name (`Usage: tg ...`) instead of the Python module path.
- `tg doctor --json` labels stale in-tree native binaries and includes remediation instead of leaving contributors to infer stale native state from a raw mismatch; current dev-path safety should skip stale implicit binaries unless `TG_NATIVE_TG_BINARY` pins one explicitly.
- Implicit native resolution now refuses stale in-tree standalone binaries for dev searches unless `TG_NATIVE_TG_BINARY` or `TG_MCP_TG_BINARY` explicitly pins one; `--format rg` is documented as the public exact ripgrep-style text-output mode.
- `context-render` and MCP context output now enforce agent trust invariants: `edit_plan_seed.primary_file`, `navigation_pack.primary_target.file`, selected files/sources, follow-up reads, and `rendered_context` must agree or report the issue through `context_consistency`.
- Default JSON/LLM context rendering preserves executable function body lines instead of reducing selected functions to signature-only output.
- Validation plans report `validation_plan[].detection`, avoid npm/package-manager commands without `package.json` evidence, avoid Python test commands without Python/test/project evidence, and omit commands entirely when no runner evidence exists.
- The validated compatibility set now covers deterministic `--files-with-matches --sort path`, `--files-without-match --sort path`, `--replace --sort path`, path separators on Windows, git ignored directories, binary exclusion by default, and match/no-match/parse-error/binary-skip exit-code behavior.
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
- PR #42 `fix: polish CLI version help and doctor diagnostics`: merged and released as `v1.8.20`
- PR #44 `fix: ignore stale native binaries in dev resolution`: merged and released as `v1.8.21`
- PR #46 `fix: improve agent context trust and rg parity`: merged and released as `v1.8.22`
- `uv run pytest tests/unit/test_install_scripts.py -q`: `18 passed` on the LF-shim fix branch
- `uv run pytest tests/unit/test_cli_bootstrap.py tests/unit/test_cli_modes.py tests/unit/test_public_docs_governance.py -q`: `287 passed` on the CLI polish branch
- PowerShell parser checks for `scripts/install.ps1` under both `pwsh` and Windows PowerShell: passed
- `git diff --check`: passed
- `uv run ruff check .`: passed
- `uv run ruff format --check --preview .`: passed
- `uv run mypy src/tensor_grep`: passed
- `uv run pytest -q`: `1845 passed, 16 skipped`
- `uv run pytest -q`: `1867 passed, 16 skipped` on the `v1.8.22` fix branch
- `python benchmarks/run_benchmarks.py --output artifacts/bench_run_benchmarks.json`: parity passed on all 10 rows; `check_regression.py --allow-env-mismatch` reported no tg benchmark regressions on the Python-version-mismatched host.
- Main CI run `25469910767`: passed through `publish-pypi`, `validate-pypi-artifacts`, and `publish-success-gate`
- Main CodeQL run `25469910279`: passed
- Release-commit CodeQL run `25470327515`: passed
- PyPI reports `tensor-grep 1.8.22` as latest and pinned `tensor-grep==1.8.22` resolves from PyPI.
- Public `v1.8.22` update dogfood passed profiled PowerShell, `cmd`, `pwsh -NoProfile`, Git Bash, WSL version resolution, PowerShell alternation through the normal `tg` shim, `cmd.exe` double-quoted alternation, Git Bash alternation, WSL alternation search, script-friendly one-line version output, public `Usage: tg` help, and public doctor PATH-version parity.

## What Works Well Now

- Scoped text search, JSON, NDJSON, multi-root search, globs, `--column`, `--vimgrep`, `--path-separator`, `--type-list`, and invalid-regex diagnostics are stable enough for agent workflows.
- Normal PowerShell `tg`, `cmd /c tg`, `pwsh -NoProfile -Command "tg ..."`, Git Bash `tg`, and WSL `tg` resolve the public `1.8.22` install on this host.
- `tg --version` is script-friendly by default; use `tg --version --verbose` for feature/SIMD/Arrow diagnostics.
- Public help starts with `Usage: tg`, including `python -m tensor_grep --help` and installed command help paths.
- `defs`, `source`, `refs`, `callers`, `context-render`, and `blast-radius` are useful for scoped repo navigation and planning.
- Released context work tightens `context-render` / MCP trust: source-body evidence ranks natural queries, default LLM rendering preserves executable body lines, `context_consistency` reports seed/render/navigation agreement, and validation commands carry detection provenance.
- Symbol outputs are compact on hits and no-matches; CommonJS symbol extraction and reference dedupe are materially improved.
- Bounded blast-radius defaults and output-limit metadata make scoped impact checks safer for agent loops.
- MCP entrypoint is present via `tg mcp --help`; MCP tool behavior is covered by the repo tests.
- GPU devices are detected locally; GPU routing remains benchmark-governed and should not be marketed as automatic crossover.
- `ast-grep` remains the structural-search feature/performance baseline; `tg run` is a useful validated slice, not full ast-grep equivalence.

## Known Weak Spots

- `rg` remains the raw cold exact-text benchmark. `tg` should win on agent-native code intelligence, not by pretending every grep workload is faster.
- `ast-grep` remains the structural-search feature/performance baseline until the AST compatibility roadmap is closed with tests and benchmark evidence.
- Broad generated roots can still be agent-hostile. Use scoped paths, globs, file types, and `--max-depth` for `tg search`; `--max-repo-files`, `--max-callers`, and `--max-files` are code-intelligence command budgets, not `tg search` flags.
- `impact --symbol` is still less trustworthy than `blast-radius` for direct symbol impact.
- `validation_commands` can still be heuristic when stack evidence is partial. Treat targeted commands as hints, not proof of full coverage; require `validation_plan[].detection`, do not trust npm/package-manager hints without `package.json` evidence, and omit commands entirely when no runner evidence exists.
- Local `uv run tg doctor --json` can find stale in-tree standalone binaries at `rust_core/target/debug/tg.exe` or `rust_core/target/release/tg.exe`. Current dev-path safety should ignore them for implicit native delegation, report them under `skipped_native_tg_binaries`, set `rust_binary_version_status = stale-skipped`, and keep `search_acceleration_backend = rust-core-extension` when the embedded extension is available. Rebuild with `C:/Users/oimir/.cargo/bin/cargo.exe build --manifest-path rust_core/Cargo.toml --release` or pin `TG_NATIVE_TG_BINARY` to opt in to a specific standalone binary.
- Broad `tg search --files ...` over generated artifact trees can still be expensive. The managed Windows launchers and Python path-list output should force UTF-8, but scope file-list commands to the smallest useful root.
- Root-scale unsorted `--files-with-matches`, `--count`, and `--force-cpu` can still differ from raw `rg` in output ordering even when the file set and counts match. Use `--sort path` for deterministic path ordering and `--format rg` for exact ripgrep-style text formatting before claiming golden stdout parity; sorted files-with-matches, files-without-match, and replacement output are now regression-covered parity edges on the active branch.
- Directly invoking `C:\Users\oimir\bin\tg.cmd` from PowerShell with an unescaped metacharacter such as `|` is still a `cmd.exe` parser limitation; use normal PowerShell `tg` / `tg.ps1` or quote the metacharacter argument for `cmd.exe`.
- Always verify command resolution with `tg --version`, `cmd /c tg --version`, `pwsh -NoProfile -Command "tg --version"`, `where.exe tg`, `Get-Command tg -All`, and WSL `wsl bash -lc 'command -v tg; tg --version'` after installer changes. A stale `Python*\Scripts\tg.exe` returning an older tensor-grep version is a release blocker.

## Next Highest-Value Work

1. Keep the agent-readiness dogfood gate (`python scripts/agent_readiness.py --output artifacts/agent_readiness.json`) fast and representative; it should cover context trust, rg sorted edges, AST smoke, MCP smoke, shell version probes, and docs claim checks.
2. Add progress, partial output, or stronger guardrails for broad generated-root scans.
3. Calibrate or de-emphasize `impact --symbol` so agents prefer `blast-radius` for direct impact.
4. Track public shim performance, AST parity roadmap gaps, GPU benchmark/no-match cleanup, and `classify` provider/cache UX as blockers for a future "world-class" claim, not as blockers for this correctness PR.
5. Keep dogfooding `tg` first and record exact failing commands, exit codes, and outputs as product evidence.

## Safe Next-Session Commands

```powershell
git status --short --branch
git log -3 --oneline
uv run tg --version
uv run tg doctor --json
python -m pip index versions tensor-grep --index-url https://pypi.org/simple --no-cache-dir
gh release view v1.8.22 --json tagName,publishedAt,url
python scripts/agent_readiness.py --output artifacts/agent_readiness.json
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
