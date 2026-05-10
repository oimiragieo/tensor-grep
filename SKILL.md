---
name: tensor-grep
description: Use when searching code, logs, or repositories with tensor-grep; validating rg or AST parity; using tg MCP tools; checking GPU/search routing; or producing agent-friendly context, source, refs, or blast-radius output.
---

# tensor-grep (tg)

## Current State

release_docs_current_tag: v1.9.6

As of 2026-05-10, the current released version is `v1.9.6`. Stable installer, PyPI metadata refresh, release-native asset publication, managed-native front-door refresh after `tg upgrade`, native-front-door CLI parity for advertised public flags, Windows `.cmd` quoted-pattern launcher handling, native-first Windows PATH ordering, top-level validation-command JSON, local default `classify`, GPU scale benchmark correctness gates, launcher-route observability, benchmark launcher attribution, scoped GPU device probing, benchmark launcher warnings, the opt-in `tg agent` Actionable Context Capsule, mixed-language capsule confidence/validation alignment, GPU benchmark recommendation hygiene, edit JSON/rollback safety, capsule validation-trust fixes, explicit language/file-name ranking, quoted Windows validation commands, docs governance, `$file` / `{file}` validation placeholder substitution, native CUDA correctness gates, ambiguous capsule alternatives, root help-menu diagnostics, and foreign launcher diagnostics are released and publicly dogfooded.

Current release facts:

- Release commit: `e06d5de chore(release): v1.9.6 [skip ci]`
- Latest merged fix commit: `05ea29e fix: harden v1.9.5 dogfood blockers`
- Latest merged feature commit: `95bfd81 feat: add actionable agent context capsule`
- PR #84 `fix: harden v1.9.5 dogfood blockers` merged and released as `v1.9.6`
- PR #83 `fix: harden GPU gates and launcher diagnostics` merged and released
- PR #82 `fix: harden docs governance and validation placeholders` merged and released
- PR #81 `fix: harden agent ranking docs and validation quoting` merged and released
- PR #80 `fix: harden edit JSON and capsule validation trust` merged and released
- PR #78 `fix: harden agent capsule trust alignment` merged and released
- PR #76 `feat: add actionable agent context capsule` merged and released
- Previous GPU/benchmark warning fix commit: `e2bd7c2 fix: scope GPU probing and benchmark launcher warnings`
- PR #74 `fix: scope GPU probing and benchmark launcher warnings` merged and released as `v1.8.33`
- Previous launcher observability fix commit: `ab2635a fix: expose launcher route observability`
- Previous agent-contract fix commit: `015fad9 fix: harden public launcher and agent contracts`
- Previous launcher fix commit: `e6d09a5 fix: preserve quoted patterns in Windows cmd shim`
- Latest merged docs/product commit: `f311469 docs: define agent context capsule roadmap`
- PR #66 `docs: define agent context capsule roadmap` merged; Main CI run `25561521904` passed, CodeQL/dynamic main run `25561520180` passed, and semantic-release correctly skipped publishing.
- Main CI run `25631800575` passed through semantic-release, PyPI artifact validation, `publish-github-release-assets`, `publish-pypi`, and `publish-success-gate`; CodeQL runs `25631800328` and `25632107771` passed.
- PyPI latest and pinned public install both resolve `tensor-grep==1.9.6`
- GitHub release assets for `v1.9.6` include native CPU front doors, checksums, winget manifest, Homebrew formula, and publish instructions
- Public `v1.9.6` dogfood: direct managed native `~/.tensor-grep/bin/tg.exe` reports `tg 1.9.6`; bare `tg` on this Windows host was shadowed by unrelated Together CLI `tg.exe`, and `tg doctor --json` classifies it as `foreign` with explicit remediation. `tg update` advanced `1.9.5` to `1.9.6`, and local fresh-shell dogfood now passes after adding a tensor-grep `tg.com` bridge ahead of the foreign `.exe` because Machine PATH ordering was not writable.
- Prior public update dogfood: `tg update` from `v1.9.3` initially saw PyPI propagation lag, then installed sidecar `tensor-grep==1.9.4`, refreshed `~/.tensor-grep/bin/tg.exe`, and verified `tg 1.9.4`. Profiled PowerShell, `cmd`, `pwsh -NoProfile`, WSL, Git Bash, and direct managed native `tg.exe` resolved `tg 1.9.4`; `tg doctor --json` reported `version = 1.9.4`, `rust_binary_version_status = matches`, `search_acceleration_backend = standalone-native-tg`, `path_tg_first_launcher_kind = cmd-shim`, `fresh_shell_path_tg_first_launcher_kind = managed-native`, and a `path_tg_launcher_warning` for current shells that still route through the compatibility shim before fresh-shell PATH.
- Prior public installer dogfood: rerunning `scripts/install.ps1` for `v1.8.31` put `C:\Users\oimir\.tensor-grep\bin` ahead of compatibility shim directories on User PATH. A simulated fresh shell resolves `C:\Users\oimir\.tensor-grep\bin\tg.exe` before `C:\Users\oimir\bin\tg.cmd`.
- Public native CLI dogfood: `tg search --multiline`, `tg search -U`, `tg search --files`, `tg search --null`, `tg run -r`, and `tg classify --format json` all accept the advertised public shape on the installed front door.
- Public Windows launcher dogfood: `cmd /c tg`, direct managed `tg.cmd`, native `tg.exe`, and Python `subprocess.run([...])` all return exit `1` with empty stdout for fresh quoted no-match phrases.
- Fast gate before PR #76: `python scripts/agent_readiness.py --output artifacts/agent_readiness_agent_capsule.json` passed all checks.
- Repo-dev doctor/search dogfood confirms stale in-tree standalone binaries are skipped unless `TG_NATIVE_TG_BINARY` or `TG_MCP_TG_BINARY` explicitly pins one
- Post-`v1.9.6` local dogfood: native CUDA release search passes exact correctness on both RTX 4070 (`sm_89`) and RTX 5070 (`sm_120`) smoke corpora plus 1GB/5GB scale gates but remains slower than both `rg` and `tg_cpu`; GPU benchmark sidecar rows are marked unsupported for native CUDA scale gates unless the benchmark uses a CUDA-enabled native binary; root `tg --help` advertises current agent/GPU/launcher/validation contracts; and `tg doctor --json` classifies unrelated first-PATH `tg` commands such as Together CLI as `foreign` with explicit remediation. Local fresh-shell dogfood now passes after `tg update` moved from `1.9.5` to `1.9.6`, and a non-destructive `tg.com` bridge was placed ahead of the foreign `tg.exe` where Machine PATH ordering could not be changed.
- Latest handoff: `docs/SESSION_HANDOFF.md`

Current product read:

- `rg` remains the benchmark for raw cold exact-text search.
- `ast-grep` remains the structural-search feature/performance baseline; `tg run` is a validated useful slice, not full ast-grep equivalence.
- `tg` is strongest as agent-native code intelligence: scoped search, JSON/NDJSON, repo maps, defs, source, refs, callers, context bundles, blast-radius, AST search, rewrite planning, GPU inventory, and MCP.
- The native front door must accept advertised public flags or intentionally route them to the sidecar. The current release line covers `tg search --files`, `tg search --multiline` / `-U`, `tg search --null`, `tg run -r`, `tg classify --format json`, Windows `.cmd` quoted multi-word no-match patterns, native-first Windows PATH ordering for fresh managed shells, and launcher-route observability for current-process versus fresh-shell PATH drift.
- The quoted multi-word no-match pattern case from `cmd.exe`, direct `tg.cmd`, and Python `subprocess.run([...])` is a public Windows launcher contract. A split pattern can become a shorter false-positive search plus bogus paths, so keep `public-windows-launcher-quoted-patterns` in the fast agent-readiness gate.
- Stable managed installs should prefer the matching release-native CPU front door when the GitHub release asset exists, while keeping the isolated Python environment as sidecar/fallback via `TG_SIDECAR_PYTHON` and `TG_NATIVE_TG_BINARY`. Installer changes should preserve the staged replacement contract so a failed install cannot break an existing public shim, including checking native installer command exit codes before the staged swap. On Windows, the managed native front-door directory should be ahead of compatibility `.cmd` shim directories on PATH so `cmd`, unprofiled PowerShell, and Python subprocess calls resolve `~/.tensor-grep/bin/tg.exe` before the slower argv-safe bridge. `tg upgrade` must verify the sidecar import/version before claiming success, including the scheduled Windows self-upgrade path, and managed native front doors must be refreshed when the verified sidecar version moves ahead of `tg.exe`.
- `tg doctor --json` is the first check for launcher drift. Inspect `path_tg_first_launcher_kind`, `fresh_shell_path_tg_first_launcher_kind`, `path_tg_launcher_warning`, and any `*_is_foreign` / `*_foreign_remediation` fields before trusting Windows benchmark timings; existing shells can retain the slower compatibility shim even after fresh User PATH resolves the native front door, and unrelated tools can own a different `tg` command.
- Cold-path benchmark artifacts should separate configured launcher mode from actual timed command kind. Use `environment.tg_launcher_mode` for the experiment and `environment.tg_launcher_command_kind` to distinguish native-exe, `.cmd` shim, `uv`, and Python-module routes. Treat benchmark warnings about shim/interpreter overhead as blocking for performance comparisons.
- Explicit `--gpu-device-ids` routing should only probe selected CUDA ordinals. Selecting GPU 0 must not initialize or warn about unrelated unsupported devices such as GPU 1.
- GPU benchmark auto-recommendation must stay false unless required 1GB/5GB correctness checks pass and a selected GPU beats both `rg` and `tg_cpu` at required scale. Unsupported-device inventory warnings should stay top-level or on the unsupported device row, not on unrelated selected-GPU timing rows.
- `--format rg --sort path` is the deterministic rg-shaped stdout contract. Token-saving output work should be a separate opt-in agent profile, not a mutation of raw rg/json/ndjson contracts.
- `tg agent` / Actionable Context Capsule is the product wedge: an opt-in workflow packet with primary file/function, route rationale, bounded snippets with line maps, validation evidence, edit order, checkpoint/rollback metadata, omission counts, confidence, call-site evidence status, and an "ask user before editing" recommendation when evidence is weak. Capsule v1 leaves `related_call_sites` empty unless verified call-site evidence is explicitly collected. Evidence labels should distinguish `parser-backed`, `rg-backed`, `graph-derived`, `heuristic`, `LSP-confirmed`, and `stale/uncertain` conclusions.
- Capsule confidence must be honest when query language hints, exact symbol intent, primary target language, selected snippets, and validation commands disagree. In mismatch cases, cap both `confidence.overall` and `primary_target.confidence`, expose `query_language_hints`, `primary_target_language`, `validation_alignment`, and `validation_filtered_count`, and require ask-before-editing.
- Product-roadmap docs are current through PR #66, and capsule v1 shipped in PR #76. Future sessions should harden capsule behavior behind explicit contracts and regression tests, not reinterpret the roadmap as permission to alter raw search output.
- `context-render` / MCP context output must keep `edit_plan_seed.primary_file`, `navigation_pack.primary_target.file`, selected files/sources, and follow-up reads consistent. Check `context_consistency` when debugging agent handoff quality.
- Default JSON/LLM context rendering must include executable body lines for selected functions. Compactness may strip comments, docstrings when optimized, blank lines, type-only imports, and boilerplate, but it is not a summary-only profile.
- `tg ast-info --json` exposes AST language identifiers for agents without help-text scraping.
- GPU support exists and local devices can be detected, but GPU routing is benchmark-governed. Do not claim GPU speedups without the matching benchmark artifact; GPU scale gates should include 1GB and 5GB rows plus exact match/file-set correctness for every >=1GB GPU corpus before any promotion claim.
- `classify` is local and deterministic by default. Use `TENSOR_GREP_CLASSIFY_PROVIDER=cybert` only when intentionally probing the CyBERT/Triton provider path, and keep provider failures quiet/fast for agent loops.
- `edit-plan` and `context-render` JSON should both expose top-level `validation_commands`; use that field first before inspecting nested `navigation_pack` or `edit_plan_seed`.
- Broad generated roots need bounds. Unbounded `tg search --files --hidden` scans and no-ignore/unrestricted fallback scans through generated/cache/dependency directories are refused unless bounded with `--glob`, `--type`, or `--max-depth`, or explicitly opted in with `--allow-broad-generated-scan`. Use scoped paths, globs, file types, and `--max-depth` for `tg search` before reaching for opt-in. `--max-repo-files`, `--max-callers`, and `--max-files` are code-intelligence command budgets, not `tg search` flags.

Known current weak spots:

- Broad `tg search --files ...` over generated artifact trees can still be expensive; the managed Windows launchers and Python path-list output should force UTF-8, but scope file-list commands to the smallest useful root.
- Windows command resolution must be checked across profiled PowerShell, `pwsh -NoProfile`, and `cmd`. Old tensor-grep-owned `Python*\Scripts\tg.exe` launchers should now be removed or uninstalled by the Windows installer; any recurrence is release-regression evidence. A `Python*\Scripts\tg.exe` that reports another product's version is a foreign PATH-shadow blocker instead: report/remediate it, but do not delete it automatically.
- WSL and Git Bash no-extension shims are part of the Windows installer contract. Verify WSL with `wsl bash -lc 'tg --version'` after shim changes.
- In PowerShell, invoke `tg` or `tg.ps1` for regex metacharacters. Direct `tg.cmd` invocation with unescaped `|` is parsed by `cmd.exe` before the batch file receives argv.
- `tg --version` is one-line by default for scripts; use `tg --version --verbose` for feature/SIMD/Arrow details.
- Installed help should show `Usage: tg`, not `Usage: python -m tensor_grep`.
- `impact --symbol` can be noisier than `blast-radius`; use `blast-radius` for direct symbol impact.
- `validation_commands` can be heuristic and should be treated as hints.
- `validation_plan[]` rows should include `detection` (`detected`, `heuristic`, or `generic`). JavaScript package-manager commands require `package.json` evidence; Python commands require tests, project markers, or Python layout evidence; when no runner evidence exists, emit no command rather than a fake `npm test` or `uv run pytest`.
- Validation commands must align with the selected primary target language unless verified cross-language dependency evidence exists. `validation_alignment` reports filtered mismatches; do not silently pair a TypeScript primary target with pytest-only validation or a Python primary target with JS-only validation.
- Implicit native resolution should ignore stale in-tree standalone binaries. `uv run tg doctor --json` should report them under `skipped_native_tg_binaries`, set `rust_binary_version_status = stale-skipped`, and keep searches on the Rust extension or Python path unless `TG_NATIVE_TG_BINARY` explicitly pins a standalone binary.
- Raw unsorted output ordering is semantic parity. Use `--sort path` for deterministic path ordering and `--format rg` for exact ripgrep-style text formatting. Sorted files-with-matches, files-without-match, and replacement output are regression-covered rg parity edges.
- Stable managed install scripts and `tg upgrade` must not trust stale package metadata immediately after publish and must not delete a working managed install before the replacement environment and front-door files have installed successfully. PowerShell native installer steps must check `$LASTEXITCODE` before the staged swap. `tg upgrade` must skip yanked PyPI releases, must not report "latest PyPI version" from unchanged local metadata without post-upgrade import/version verification, and must refresh or schedule refresh of the managed native front door when the sidecar package version changes. A PyPI-only publish is not enough when installers point at GitHub assets; release assets must be uploaded and verified first.
- GPU is not production-ready from device detection alone. The latest public dogfood still shows GPU losing badly or timing out at 100MB, 1GB, and 5GB; keep GPU claims behind accepted end-to-end artifacts.
- Edit validation supports `$file` and `{file}` placeholders in validation command templates. For applied rewrites, placeholder commands run once per edited file; placeholder-free commands run once in the original target working directory. Quote the placeholder for Windows paths with spaces, for example `--lint-cmd 'python -m py_compile "$file"'`.

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

Use one of these instead for agent-safe file discovery:

```powershell
tg search --files src --hidden
tg search --files . --hidden --glob "*.py"
tg search --files . --hidden --max-depth 3
```

Only pass `--allow-broad-generated-scan` when the generated/cache/dependency tree walk is intentional.

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
- `tg_agent_capsule`

Call `tg_mcp_capabilities` first in PyPI wheels, sandboxes, and agent hosts so the client knows whether a standalone native `tg` binary is available.

## Validation

For code changes, follow `AGENTS.md` and run:

```powershell
uv run ruff check .
uv run ruff format --check --preview .
uv run mypy src/tensor_grep
uv run pytest -q
```

For fast agent-readiness dogfood before push, run:

```powershell
python scripts/agent_readiness.py --output artifacts/agent_readiness.json
```

This gate checks public shell version resolution, `public-windows-launcher-quoted-patterns`, repo doctor sanity, `context_consistency`, `agent-capsule`, `agent-capsule-mixed-language`, deterministic rg edge parity, broad generated-root scan guardrails, AST smoke, MCP context-render smoke, docs claim hygiene, current `v1.9.6` positioning, foreign launcher diagnostics, and the managed native-upgrade contract. It does not replace the full validation gate.

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
| Claiming `tg` is always faster than `rg` | Keep `rg` as the cold exact-text benchmark; position `tg` as agent-native code intelligence with a validated compatibility set. |
| Searching with `rg` by habit inside this repo | Use `tg search` first, then `rg` for parity or fallback. |
| Running broad generated-root scans | Scope the path, use `--glob` / `--type` / `--max-depth`, or opt in with `--allow-broad-generated-scan` only when the generated-tree walk is intentional. |
| Saving tokens by changing raw search contracts | Add an opt-in bounded agent formatter/profile; leave `--format rg`, `--json`, and `--ndjson` stable. |
| Trusting stale native diagnostics | Check `uv run tg doctor --json`; stale in-tree binaries should be `stale-skipped`, not selected implicitly. Rebuild or pin `TG_NATIVE_TG_BINARY` to opt in. |
| Trusting invented validation commands | Check `validation_plan[].detection`; package-manager commands require `package.json`, Python commands require Python/test/project evidence, and absent evidence should mean no command. |
| Claiming GPU wins from device detection | Run the GPU benchmark and record the accepted artifact. |
| Updating docs from memory | Update docs only from repo evidence, CI evidence, or benchmark artifacts. |

## Exit Codes

| Code | Meaning |
| --- | --- |
| 0 | Matches found or command succeeded |
| 1 | No matches found |
| 2 | Error occurred |
