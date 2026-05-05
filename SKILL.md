---
name: tensor-grep
description: Use when searching code, logs, or repositories with tensor-grep; validating rg or AST parity; using tg MCP tools; checking GPU/search routing; or producing agent-friendly context, source, refs, or blast-radius output.
---

# tensor-grep (tg)

## Current State

As of 2026-05-05, the current released version is `v1.8.20`.

Current release facts:

- Release commit: `4f7b59c chore(release): v1.8.20 [skip ci]`
- Latest fix commit: `10cac14 fix: polish CLI version help and doctor diagnostics`
- PR #42 `fix: polish CLI version help and doctor diagnostics` merged, released, and publicly dogfooded
- Main CI run `25379489045` passed through `publish-success-gate`; CodeQL runs `25379488260` and `25380155733` passed
- PyPI latest and pinned public installer dogfood both resolve `tensor-grep==1.8.20`
- Latest handoff: `docs/SESSION_HANDOFF.md`

Current product read:

- `rg` remains the benchmark for raw cold exact-text search.
- `tg` is strongest as agent-native code intelligence: scoped search, JSON/NDJSON, repo maps, defs, source, refs, callers, context bundles, blast-radius, AST search, rewrite planning, GPU inventory, and MCP.
- `tg ast-info --json` exposes AST language identifiers for agents without help-text scraping.
- GPU support exists and local devices can be detected, but GPU routing is benchmark-governed. Do not claim GPU speedups without the matching benchmark artifact.
- Broad generated roots need bounds. Use scoped paths, globs, file types, and `--max-depth` for `tg search`; `--max-repo-files`, `--max-callers`, and `--max-files` are code-intelligence command budgets, not `tg search` flags.

Known current weak spots:

- Broad `tg search --files ...` over generated artifact trees can still be expensive; the managed Windows launchers and Python path-list output should force UTF-8, but scope file-list commands to the smallest useful root.
- Windows command resolution must be checked across profiled PowerShell, `pwsh -NoProfile`, and `cmd`. Old tensor-grep-owned `Python*\Scripts\tg.exe` launchers should now be removed or uninstalled by the Windows installer; any recurrence is release-regression evidence.
- WSL and Git Bash no-extension shims are part of the Windows installer contract. Verify WSL with `wsl bash -lc 'tg --version'` after shim changes.
- In PowerShell, invoke `tg` or `tg.ps1` for regex metacharacters. Direct `tg.cmd` invocation with unescaped `|` is parsed by `cmd.exe` before the batch file receives argv.
- `tg --version` is one-line by default for scripts; use `tg --version --verbose` for feature/SIMD/Arrow details.
- Installed help should show `Usage: tg`, not `Usage: python -m tensor_grep`.
- `impact --symbol` can be noisier than `blast-radius`; use `blast-radius` for direct symbol impact.
- `validation_commands` can be generic and should be treated as hints.
- `uv run tg doctor --json` can report a stale in-tree standalone binary; current output should label it as `in-tree-debug` or `in-tree-release`, set `rust_binary_version_status = stale`, and include rebuild/pinning remediation before agents trust standalone-native behavior.

## Release Completion Contract

A branch push or open PR starts PR CI only. It is not a release, not a released version, and not complete release state.

Release versioning starts only after a release-bearing PR is squash-merged to `main`, because semantic-release reads the final `main` commit subject.

A release-bearing PR is complete only after PR CI passes, the PR is squash-merged to `main`, main CI and semantic-release complete successfully, the release commit and tag exist on `origin/main`, `publish-success-gate` passes, `git fetch origin main --tags` is run, agents fast-forward local `main` to the release commit, and PyPI/public installer availability is verified.

Do not report final version state before the GitHub release, PyPI/package publish status, public install/update path, and local checkout have all been verified.

## Start Here

Confirm command resolution and version before trusting behavior:

```powershell
Get-Command tg -ErrorAction SilentlyContinue | Format-List Source,CommandType,Version
Get-Command tg -All -ErrorAction SilentlyContinue | Format-Table -AutoSize CommandType,Source,Version
Get-Alias tg -ErrorAction SilentlyContinue | Format-List Definition,ResolvedCommandName
tg --version
cmd /c tg --version
pwsh -NoProfile -Command "tg --version"
where.exe tg
uv run tg doctor --json
```

Use scoped `tg` discovery first:

```powershell
tg search --fixed-strings "<query>" src tests docs README.md
tg search --json "<query>" src tests docs
tg search --ndjson "<query>" src tests docs
```

Avoid broad generated-root file lists unless the task needs them:

```powershell
tg search --files "AGENTS.md" . --hidden
```

## Core CLI Workflows

| Task | Command |
| --- | --- |
| Basic search | `tg "pattern" [path]` |
| Explicit search | `tg search "pattern" src tests docs` |
| Fixed string | `tg -F "literal.string" src` |
| Context lines | `tg -C 3 "pattern" src` |
| JSON aggregate | `tg search --json "pattern" src` |
| NDJSON stream | `tg search --ndjson "pattern" src tests docs` |
| Files with matches | `tg search "pattern" src --files-with-matches` |
| AST search | `tg run --lang python 'def $NAME($$$ARGS): $$$BODY' src --json` |
| AST language identifiers | `tg ast-info --json` |
| Source lookup | `tg source src --symbol someSymbol --json` |
| Refs lookup | `tg refs src --symbol someSymbol --json` |
| Blast radius | `tg blast-radius src --symbol someSymbol --json` |
| Context bundle | `tg context-render src --query "how routing works" --render-profile llm --json` |
| Device inventory | `tg devices --json` |
| MCP server | `tg mcp` |

PowerShell expands `$NAME` and `$$$ARGS` inside double quotes. Use single quotes for AST metavariable patterns.

## MCP Surface

Start the server with:

```powershell
tg mcp
```

Useful MCP tools include:

- `tg_mcp_capabilities`
- `tg_search`
- `tg_ast_search`
- `tg_classify_logs`
- `tg_devices`
- `tg_index_search`
- `tg_rewrite_plan`
- `tg_rewrite_apply`
- `tg_rewrite_diff`

Call `tg_mcp_capabilities` first in PyPI wheels, sandboxes, and agent hosts so the client knows whether a standalone native `tg` binary is available.

## Validation

For code changes, follow `AGENTS.md` and run:

```powershell
uv run ruff check .
uv run ruff format --check --preview .
uv run mypy src/tensor_grep
uv run pytest -q
```

For hot-path or benchmark-relevant changes, run the matching benchmark before updating claims:

```powershell
python benchmarks/run_benchmarks.py --output artifacts/bench_run_benchmarks.json
python benchmarks/check_regression.py --baseline auto --current artifacts/bench_run_benchmarks.json
python benchmarks/run_hot_query_benchmarks.py --output artifacts/bench_hot_query_benchmarks.json
python benchmarks/run_ast_benchmarks.py --output artifacts/bench_run_ast_benchmarks.json
python benchmarks/run_ast_workflow_benchmarks.py --output artifacts/bench_run_ast_workflow_benchmarks.json
python benchmarks/run_gpu_benchmarks.py --output artifacts/bench_run_gpu_benchmarks.json
```

GPU benchmark `SKIP` is valid infrastructure state when dependencies such as Torch, cuDF, CUDA, or Triton are unavailable. Do not convert a skip into a speed claim.

## Common Mistakes

| Mistake | Correction |
| --- | --- |
| Claiming `tg` is always faster than `rg` | Keep `rg` as the cold exact-text benchmark; position `tg` as agent-native code intelligence. |
| Searching with `rg` by habit inside this repo | Use `tg search` first, then `rg` for parity or fallback. |
| Running broad generated-root scans | Scope the path and use output/scan limits. |
| Trusting stale native diagnostics | Check `uv run tg doctor --json`; rebuild or pin `TG_NATIVE_TG_BINARY` if versions diverge. |
| Claiming GPU wins from device detection | Run the GPU benchmark and record the accepted artifact. |
| Updating docs from memory | Update docs only from repo evidence, CI evidence, or benchmark artifacts. |

## Exit Codes

| Code | Meaning |
| --- | --- |
| 0 | Matches found or command succeeded |
| 1 | No matches found |
| 2 | Error occurred |
