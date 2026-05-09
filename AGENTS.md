# AGENTS.md

This file explains how agents should work in `tensor-grep`.

## Goal

`tensor-grep` is trying to become a fast, scalable search tool that combines:

- `ripgrep`-class text search
- AST / structural search
- indexed repeated-query acceleration
- optional GPU / ML paths
- AI-harness-friendly search and edit behavior

The repo should be treated as a benchmark-governed, contract-heavy codebase. Do not optimize by guesswork.

## Current Handoff

As of 2026-05-09, the current released state is `v1.9.0`. The stable installer, release-native asset publication, managed-native `tg upgrade` refresh path, native-front-door CLI parity fixes, Windows `.cmd` quoted-pattern launcher fix, native-first Windows PATH ordering, top-level validation-command contract, local default `classify`, GPU scale benchmark correctness gates, launcher-route observability, benchmark launcher attribution, scoped GPU device probing, benchmark launcher warnings, and opt-in `tg agent` Actionable Context Capsule are released and publicly dogfooded. Current active branch work hardens mixed-language capsule confidence/validation alignment and GPU benchmark recommendation hygiene; after this lands, follow-up work should focus on capsule ranking/token economy, AST parity roadmap, GPU production viability, classify provider/cache UX, context/session latency, and keeping docs synchronized with release proof.

- Release commit: `19c7295 chore(release): v1.9.0 [skip ci]`
- Latest merged feature commit: `95bfd81 feat: add actionable agent context capsule`
- Recent fix commits:
  - `e2bd7c2 fix: scope GPU probing and benchmark launcher warnings`
  - `ab2635a fix: expose launcher route observability`
  - `015fad9 fix: harden public launcher and agent contracts`
  - `e6d09a5 fix: preserve quoted patterns in Windows cmd shim`
  - `7742258 fix: harden native front-door CLI parity`
  - `4dcc6d7 fix: refresh managed native front door after upgrade`
  - `8420cab fix: harden stable installer and upgrade resolution`
  - `6f82d14 fix: publish GitHub release native assets from main CI`
  - `7b38bbb perf: use native front door for managed installs`
  - `ef0c114 fix: harden v1.8.23 dogfood regressions`
  - `19e515d fix: add generated-root scan guardrails`
  - `8a061ee fix: improve agent context trust and rg parity`
  - `1bf2c76 fix: ignore stale native binaries in dev resolution`
  - `10cac14 fix: polish CLI version help and doctor diagnostics`
  - `a5fa279 fix: write WSL bash shims with LF newlines`
  - `98fa9ab fix: harden Windows and WSL installer shims`
  - `e2ebbd2 fix: uninstall stale Python tg launcher owners`
  - `6c2e59c fix: skip inaccessible PATH entries in Windows installer`
  - `32293c0 fix: harden Windows launchers and path-list output`
  - `f98a6e4 fix: correct Windows installer pinned extras`
  - `1a06cba fix: remove stale Windows tg launchers`
  - `379b22f fix: harden tg resolution and rg path parity`
- Main CI run `25601232312`: passed through semantic-release, PyPI wheel/sdist validation, `publish-github-release-assets`, `publish-pypi`, and `publish-success-gate`
- Main CodeQL run `25601232120`: passed on the release-bearing merge commit
- PyPI latest and pinned install: `tensor-grep==1.9.0` resolves from PyPI
- GitHub release: <https://github.com/oimiragieo/tensor-grep/releases/tag/v1.9.0>
- GitHub release assets: `tg-windows-amd64-cpu.exe`, `tg-linux-amd64-cpu`, `tg-macos-amd64-cpu`, checksums, winget manifest, Homebrew formula, and publish instructions are uploaded and verified on `v1.9.0`
- Public update dogfood: `tg update` from `v1.8.33` installed sidecar `tensor-grep==1.9.0`, refreshed the managed native front door, and verified `tg 1.9.0`. Profiled PowerShell, `cmd`, `pwsh -NoProfile`, WSL, Git Bash, and direct managed native `tg.exe` resolve `tg 1.9.0`; `tg doctor --json` reports `version = 1.9.0`, `rust_binary_version_status = matches`, `search_acceleration_backend = standalone-native-tg`, `path_tg_first_launcher_kind = cmd-shim`, `fresh_shell_path_tg_first_launcher_kind = managed-native`, and a `path_tg_launcher_warning` for current shells that still route through the compatibility shim before fresh-shell PATH. Public `tg agent src/tensor_grep/cli --query "agent context capsule" --json` returned an Actionable Context Capsule with primary target, snippets, omissions, follow-up reads, confidence, and ask-before-editing metadata.
- Prior public installer dogfood: rerunning `scripts/install.ps1` for `v1.8.31` put `C:\Users\oimir\.tensor-grep\bin` ahead of compatibility shim directories on User PATH. A simulated fresh shell resolves `C:\Users\oimir\.tensor-grep\bin\tg.exe` before `C:\Users\oimir\bin\tg.cmd`.
- Public launcher dogfood: `cmd /c tg`, direct managed `tg.cmd`, native `tg.exe`, and Python `subprocess.run([...])` preserve fresh quoted no-match phrases and return exit `1` without false-positive stdout.
- Session handoff: `docs/SESSION_HANDOFF.md`
- Current follow-up work is tracked in `docs/SESSION_HANDOFF.md`: keep release-native assets verified, preserve the managed installer fallback when assets are absent, keep sidecar and native front-door versions aligned after `tg upgrade`, keep current-process vs fresh-shell launcher routing visible in `tg doctor`, preserve benchmark launcher command-kind attribution and warnings, harden the opt-in `tg agent` context capsule/token-economy surface without changing raw search contracts, keep mixed-language capsule confidence/validation alignment honest, and keep GPU/provider paths experimental until correctness, speed, and UX are proven.

The latest accepted release line fixed the Windows `--files-with-matches` rg-backed argument-vector failure, raw rg-style no-path `--files-with-matches` output, malformed pinned Windows installer extras, root-based path-list output, `-0/--null` path-list/count parsing, `tg ast-info --json`, argv-safe PowerShell shims, UTF-8 path-list output, inaccessible PATH-entry handling, managed shim installation, stale Python package cleanup when an old `Python*\Scripts\tg.exe` shadows managed shims, argv-safe `.cmd` bridging, Git Bash / WSL no-extension shims, WSL-aware `/mnt/c/...` paths, LF-only generated bash shims, one-line default version output with verbose details behind `--verbose`, public `Usage: tg` help text, explicit `doctor` diagnostics for stale in-tree native binaries, implicit stale-native skipping for dev searches, public `--format rg` help text for exact ripgrep-style output, context-render/MCP trust invariants, validation command provenance, sorted rg parity edges for files-with-matches, files-without-match, replacement output, and PCRE2 output, multiline rg parity forwarding, exact-symbol context ranking over camel/snake bridge heuristics, session stale-file filtering and no-runner validation consistency, embedded checkpoint fallback for MCP rewrite apply when standalone native `tg` is unavailable, inline scan rule severity/message preservation, uppercase `API_KEY` secret scanning, explicit broad generated-root scan refusal unless callers bound the search or opt in, managed native front-door refresh after `tg upgrade`, native-front-door parity for `tg search --files`, `tg search --multiline` / `-U`, `tg search --null`, `tg run -r`, and `tg classify --format json`, classify fallback before expensive provider/model setup when unavailable, GPU benchmark no-match correctness handling, Windows `.cmd` quoted multi-word no-match patterns from `cmd.exe`, direct `tg.cmd`, and Python `subprocess.run([...])`, Windows installer User PATH ordering that puts the managed native front-door directory ahead of compatibility shim directories, top-level `validation_commands` on both `context-render` and `edit-plan` JSON, deterministic local default `classify` unless `TENSOR_GREP_CLASSIFY_PROVIDER=cybert` opts into CyBERT/Triton, GPU benchmark defaults/correctness checks for 1GB and 5GB scale rows, explicit GPU device probing that does not initialize or warn about unselected GPUs, and benchmark script warnings when timings include shim or interpreter overhead.

Known current weak spots:

- `rg` remains the raw cold exact-text benchmark; `tg` should be treated as the agent-native code intelligence layer.
- `ast-grep` remains the structural-search feature/performance baseline; `tg run` is a useful validated AST slice, not a blanket ast-grep replacement.
- `context-render` and MCP context output are agent trust surfaces. `edit_plan_seed.primary_file`, `navigation_pack.primary_target.file`, selected files/sources, follow-up reads, and `rendered_context` must agree or `context_consistency` must report the omission and confidence downgrade.
- Default JSON/LLM context rendering must include executable behavior for selected functions. Compact rendering can strip low-value text, but it must not reduce selected code to signatures unless a future summary-only profile explicitly asks for that.
- Validation commands are hints with provenance. Require `validation_plan[].detection`, do not suggest npm/package-manager commands without `package.json` evidence, do not suggest Python test commands without Python/test/project evidence, and omit commands entirely when no runner evidence exists.
- Validation commands must align with the selected primary target language unless verified cross-language dependency evidence exists. `validation_alignment` should report filtered mismatches; do not silently pair a TypeScript primary target with pytest-only validation or a Python primary target with JS-only validation.
- Unbounded broad generated-root scans are hostile to unattended agents. `tg search --files --hidden` and no-ignore/unrestricted fallback scans now refuse roots that contain generated/cache/dependency directories unless the request is bounded by `--glob`, `--type`, or `--max-depth`, or explicitly opts in with `--allow-broad-generated-scan`. Use scoped paths, globs, file types, and `--max-depth` for `tg search` before reaching for opt-in. `--max-repo-files`, `--max-callers`, and `--max-files` are code-intelligence command budgets, not `tg search` flags.
- Prefer `blast-radius` over `impact --symbol` when direct symbol impact matters.
- Windows launcher/path-list hardening should force UTF-8 for managed shims and Python path-list output; still scope broad file-list commands to avoid generated-tree volume.
- If `cmd /c tg --version` or `pwsh -NoProfile -Command "tg --version"` resolves an old `Python*\Scripts\tg.exe`, treat it as installer regression evidence. The Windows installer should remove or uninstall tensor-grep-owned stale Python launchers instead of only warning about them.
- Normal PowerShell should invoke `tg` or `tg.ps1`. Directly invoking `C:\Users\oimir\bin\tg.cmd` from PowerShell with an unescaped metacharacter such as `|` is still a `cmd.exe` parser limitation; quote the argument for `cmd.exe` or use the PowerShell shim. The quoted multi-word no-match pattern case from `cmd.exe`, direct `tg.cmd`, and Python `subprocess.run([...])` is a public launcher contract and must not split into a shorter false-positive search plus bogus paths.
- Implicit native-binary resolution must ignore stale in-tree binaries such as `rust_core/target/debug/tg.exe` and `rust_core/target/release/tg.exe`. `uv run tg doctor --json` should report them under `skipped_native_tg_binaries`, set `rust_binary_version_status = stale-skipped`, and keep `search_acceleration_backend = rust-core-extension` when the embedded extension is available. Rebuild with `C:/Users/oimir/.cargo/bin/cargo.exe build --manifest-path rust_core/Cargo.toml --release` or pin `TG_NATIVE_TG_BINARY` to opt in to a specific standalone binary.
- Raw unsorted output ordering is semantic parity, not golden stdout parity. Use `--sort path` when deterministic path ordering matters and `--format rg` when automation needs exact ripgrep-style text formatting. Sorted files-with-matches, files-without-match, and replacement output are rg parity regression surfaces in the validated compatibility set.
- Stable managed install scripts and `tg upgrade` are part of the public launcher contract. When release-native assets exist, the public front door should launch the matching native `tg` binary first and set `TG_SIDECAR_PYTHON` / `TG_NATIVE_TG_BINARY`; Python remains the sidecar or fallback, not the normal exact-text first hop. On Windows, put the managed native front-door directory ahead of compatibility shim directories on User PATH so `cmd`, unprofiled PowerShell, and Python subprocess calls resolve `~/.tensor-grep/bin/tg.exe` before the slower argv-safe `.cmd` bridge. A release that updates installer URLs is incomplete until GitHub release assets are uploaded and verified, not merely PyPI-published. Stable installers should clear stale package metadata before resolving `tensor-grep`, check native installer command exit codes before committing the staged install, and stage the new managed environment plus front-door files before replacing an existing install. `tg upgrade` should skip yanked PyPI releases, never report "latest PyPI version" from unchanged local metadata without verifying the target Python can import `tensor_grep`, refresh the managed release-native front door to the verified sidecar version, schedule a Windows retry helper when the running native `tg.exe` is locked, and require the scheduled Windows self-upgrade helper to verify the expected version too.
- `tg doctor --json` should expose launcher route state, not just version parity. Check `path_tg_first_launcher_kind`, `fresh_shell_path_tg_first_launcher_kind`, and `path_tg_launcher_warning` before interpreting Windows benchmark results; an existing shell can still be using the slower compatibility shim after User PATH has been fixed for fresh shells.
- Cold-path benchmark artifacts should include both `tg_launcher_mode` and `tg_launcher_command_kind`. Benchmark scripts should emit top-level warnings when the timed `tg` command is a `.cmd` shim, `uv`, or Python-module route. Do not compare or market timings until native-exe, `.cmd` shim, `uv`, and Python-module routes are separated in the artifact.
- The native front door must not reject public flags advertised by the Python CLI. If a surface is still Python-backed, route it to the sidecar deliberately and add a public-native regression test for the installed command shape. Current parity-sensitive examples are `tg search --files`, `tg search --multiline` / `-U`, `tg search --null`, `tg run -r`, and `tg classify --format json`.
- `classify` should be quiet and deterministic by default. It should use local heuristics unless `TENSOR_GREP_CLASSIFY_PROVIDER=cybert` explicitly opts into the CyBERT/Triton provider, and provider failures should fall back before tokenizer/model loading.
- GPU benchmark correctness must treat no-match as a real comparator outcome. `rg` exit code `1` with empty output is valid when `tg` also returns no matches. GPU scale gates should include 1GB and 5GB rows and exact match/file-set correctness for every >=1GB GPU corpus before any GPU promotion claim. Explicit `--gpu-device-ids` routing must not initialize or warn about unselected GPUs.
- GPU benchmark auto-recommendation must remain false unless required 1GB/5GB correctness checks pass and a selected GPU beats both `rg` and `tg_cpu` at the required scale. Unsupported-device inventory warnings must not be attached to unrelated selected-GPU timing rows.
- `edit-plan` and `context-render` JSON should expose top-level `validation_commands` so agents do not need command-specific parsing to find the validation list.
- Token-efficiency work must be opt-in and contract-aware. Lessons from `rtk` point toward a bounded agent output profile with hard caps, grouped excerpts, truncation, and omission counts; do not change raw `--format rg`, `--json`, or `--ndjson` semantics to save tokens.
- The product wedge is not "faster grep." It is an agentic code-intelligence runtime: given a task, identify what matters, explain why, emit bounded context, suggest validation, preserve rollback, and report confidence. `tg agent` / Actionable Context Capsule is the opt-in command for that workflow.
- The Actionable Context Capsule contract includes the primary file/function, route rationale, bounded source snippets with line maps, detected validation commands, risk level, suggested edit order, checkpoint or rollback metadata, omission counts, confidence, call-site evidence status, and an "ask user before editing" recommendation when uncertainty or risk is high. Capsule v1 leaves `related_call_sites` empty unless verified call-site evidence is explicitly collected.
- Capsule confidence must be honest when query language hints, exact symbol intent, primary target language, selected snippets, and validation commands disagree. In mismatch cases, cap both `confidence.overall` and `primary_target.confidence`, expose `query_language_hints`, `primary_target_language`, `validation_alignment`, and `validation_filtered_count` in `context_consistency`, and require ask-before-editing.
- Future search-intent routing should label evidence honestly as `parser-backed`, `rg-backed`, `graph-derived`, `heuristic`, `LSP-confirmed`, or `stale/uncertain`. The router can combine text search, AST, symbol graph, imports, tests, and docs, but it must report the route instead of hiding backend choice.

## Operating Rules

1. Start with a failing test when behavior changes.
2. Make the smallest defensible change.
3. Run local gates before pushing.
4. Benchmark every hot-path change.
5. Reject regressions even if the code is otherwise clean.
6. Do not change workflow, release, or docs contracts without updating the validator-backed tests.

## Required Local Validation

Run these before push for normal code changes:

```powershell
uv run ruff check .
uv run ruff format --check --preview .
uv run mypy src/tensor_grep
uv run pytest -q
```

CI runs Ruff formatting in preview mode. Running only `uv run ruff check .` is not enough to prove formatter parity.

`uv run pytest -q` can take substantially longer than 70-90 seconds on this Windows machine when the full JS/TS and e2e surface is hot; use a timeout of at least 120 seconds for narrow suites and a much larger timeout for the full suite when running it through automation.

For focused changes, run the relevant narrow suite first, then the full suite if the change is intended to land:

```powershell
uv run pytest tests/unit/test_cpu_backend.py -q
uv run pytest tests/unit/test_cli_bootstrap.py -q
uv run pytest tests/unit/test_release_assets_validation.py -q
```

For fast pre-push dogfood on agent-critical surfaces, run the agent-readiness dogfood gate:

```powershell
python scripts/agent_readiness.py --output artifacts/agent_readiness.json
```

This 3-5 minute gate checks public shell version resolution, `public-windows-launcher-quoted-patterns`, repo doctor sanity, `context_consistency`, `agent-capsule`, `agent-capsule-mixed-language`, deterministic rg edge parity, broad generated-root scan guardrails, AST smoke, MCP context-render smoke, and docs claim hygiene. It complements, not replaces, the full local validation gate.

## Benchmark Rules

Never claim a speedup without measured numbers.

Use the right benchmark for the area you changed:

### End-to-end CLI text search

```powershell
python benchmarks/run_benchmarks.py --output artifacts/bench_run_benchmarks.json
python benchmarks/check_regression.py --baseline auto --current artifacts/bench_run_benchmarks.json
```

This is the main `tg` vs `rg` comparison. Use this for:

- plain search routing
- startup / launcher changes
- text-search control-plane changes

### Repeated-query / hot cache paths

```powershell
python benchmarks/run_hot_query_benchmarks.py --output artifacts/bench_hot_query_benchmarks.json
```

Use this for:

- StringZilla index changes
- CPU regex prefilter changes
- persisted cache / decode / posting-list changes

### AST single-query benchmark

```powershell
python benchmarks/run_ast_benchmarks.py --output artifacts/bench_run_ast_benchmarks.json
```

### AST workflow startup benchmark

```powershell
python benchmarks/run_ast_workflow_benchmarks.py --output artifacts/bench_run_ast_workflow_benchmarks.json
```

Use this for:

- `run`
- `scan`
- `test`
- AST workflow startup / batching / wrapper orchestration

### GPU / NLP backend benchmark

```powershell
python benchmarks/run_gpu_benchmarks.py --output artifacts/bench_run_gpu_benchmarks.json
```

Notes:

- `cyBERT` may skip if Triton is unavailable.
- Treat `SKIP` as expected infrastructure state, not a fake failure.

## Performance Discipline

Use these rules consistently:

1. Compare against the current accepted baseline, not memory.
2. Reject candidates that are slower or only “faster” in a microprofile while slower end-to-end.
3. Keep both cold-start and repeated-query measurements in mind.
4. Do not update docs or the paper with speed claims until the benchmark line is accepted.
5. If a candidate is correct but slower, revert it and record the attempt.

## CI / Release Rules

CI is not just a test runner. It enforces:

- formatting
- linting
- typing
- cross-platform behavior
- release workflow contracts
- package-manager workflow contracts
- artifact/version parity

Do not casually edit:

- `.github/workflows/ci.yml`
- `.github/workflows/release.yml`
- `scripts/validate_release_assets.py`

If you change workflow, docs, or release behavior, expect to update validator-backed tests too.

Read `docs/CI_PIPELINE.md` before editing CI, release, Dependabot, or audit automation. That file is the canonical contract for how the pipeline is supposed to behave and what follow-up validators must change with it.

Important test surface:

- `tests/unit/test_release_assets_validation.py`
- workflow/package-manager/release validator suites

## Routing / Architecture Guidance

Be honest about workload classes.

- Cold generic text search:
  - `rg` is still the baseline.
  - control-plane overhead matters more than backend cleverness.
- Repeated text search:
  - indexing can beat cold grep-style tools.
- AST workflows:
  - batching and orchestration matter as much as backend logic.
- GPU:
  - only wins when workload size and arithmetic intensity amortize transfer and startup cost.

Do not assume:

- more caching is always faster
- compiled onefile binaries are always faster
- GPU is always faster
- a micro-optimization is worth landing without end-to-end proof

## Native vs Python Reality

The repo has proven:

- Python-side startup cuts help
- repeated-query indexing helps
- AST batching helps
- onefile Nuitka binaries are not currently the speed path on Windows for plain passthrough

If the goal is to close the remaining gap to raw `rg`, the likely next step is a more native launcher/control-plane path, not more Python micro-tuning.

## Push Discipline

Do not push from a dirty worktree if `origin/main` moved and the local tree has unrelated changes.

A branch push or open PR starts PR CI only. It is not a release, not a released version, and not complete release state. Release versioning starts only after a release-bearing PR is squash-merged to `main`, because semantic-release reads the final `main` commit subject.

Preferred approach:

1. use a clean replay worktree
2. rebase/reset to current `origin/main`
3. rerun narrow checks and relevant benchmarks
4. push only the accepted change
5. open a PR with the correct conventional title and wait for PR CI/CodeQL to pass
6. if the change is release-bearing and intended to ship now, squash-merge the PR to `main`
7. wait for main CI and semantic-release complete successfully, plus CodeQL, `publish-github-release-assets`, PyPI/package artifact validation, `publish-pypi`, and `publish-success-gate`
8. verify the GitHub release assets, PyPI latest version, and any affected public installer/update path. PyPI/public installer availability is verified before final release status is reported
9. after semantic-release completes, `git fetch origin main --tags` and fast-forward local `main` to the release commit before reporting the final version state

Do not report a release-bearing fix as complete after only a branch push, open PR, or green PR checks. The final report must name the PR, merge commit, main CI run, CodeQL run, released tag/version, PyPI/package publish status, and any local/public installer dogfood result.

For docs/test/chore-only work, use a non-release PR title, wait for PR CI, and merge only when requested or clearly required. After merge, main CI should pass but semantic-release should skip release publishing.

## PR Title And Release Intent

AI-generated PRs must use conventional titles so CI can infer semantic-release intent.

Use this schema:

- `feat: ...` => minor release
- `fix: ...` or `perf: ...` => patch release
- `feat!: ...` or `fix!: ...` => major release
- `docs: ...`, `test: ...`, `chore: ...`, `ci: ...`, `build: ...` => no release

Release-bearing PRs must use `Squash and merge` so the validated PR title becomes the commit subject on `main`.

Do not manually create release tags when semantic-release is active.

## Documentation Discipline

When a candidate is accepted or explicitly rejected, update:

- `docs/PAPER.md` if it changes the optimization history or benchmark story
- `README.md` / `docs/benchmarks.md` only after accepted benchmark changes

The paper should preserve failed attempts too, so future agents do not retry the same losing ideas.

## Bottom Line

Work like this:

1. test first
2. smallest change
3. local lint/type/test
4. benchmark
5. reject regressions
6. push only measured wins or required correctness/CI fixes

Do not use code-intelligence budget flags as `tg search` options; scope `tg search` with paths, globs, file types, and depth.

