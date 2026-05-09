# tensor-grep Session Handoff

Last updated: 2026-05-09

## Current Release State

release_docs_current_tag: v1.9.4

- Latest released version: `v1.9.2`
- Latest release commit: `8143ccb chore(release): v1.9.2 [skip ci]`
- Latest fix commit: `faf67ed fix: harden edit JSON and capsule validation trust`
- Latest feature commit: `95bfd81 feat: add actionable agent context capsule`
- GitHub release: <https://github.com/oimiragieo/tensor-grep/releases/tag/v1.9.2>
- Main CI run `25609611007`: passed through semantic-release, PyPI artifact validation, `publish-github-release-assets`, `publish-pypi`, and `publish-success-gate`
- Main CodeQL run `25609610737`: passed
- PyPI latest and pinned install: `tensor-grep==1.9.2` resolves from PyPI
- GitHub release assets: `v1.9.2` has uploaded native CPU front doors for Windows/Linux/macOS, checksums, winget manifest, Homebrew formula, and publish instructions
- Closed edit automation safety gap: `v1.9.2` emits parseable edit JSON for diff/apply, keeps human output out of JSON stdout, rolls changed files back after failed validation, and keeps capsule validation trust aligned when mismatched commands are filtered but valid target-language commands remain.
- Closed capsule trust-alignment gap: `v1.9.1` caps capsule confidence and requires ask-before-editing when explicit language hints, exact symbol intent, primary target language, selected snippets, and validation commands disagree; validation commands are filtered to match the primary target language unless cross-language dependency evidence exists; GPU benchmark auto-recommendation remains gated by required 1GB/5GB correctness and selected-GPU speed evidence.
- Prior Actionable Context Capsule gap: `v1.9.0` releases opt-in `tg agent --query ... --json` as a deterministic work packet with primary target metadata, route rationale, bounded snippets with line maps, validation evidence, edit order, rollback/checkpoint metadata, omissions/follow-up reads, confidence, call-site evidence status, and ask-before-editing recommendations.
- Prior GPU probe and benchmark-warning gaps: `v1.8.33` scopes explicit GPU device probing so `--gpu-device-ids 0` does not initialize or warn about unrelated unsupported GPUs, and benchmark scripts now emit top-level warnings when the timed `tg` entrypoint includes `.cmd`, `uv`, or Python-module overhead.
- Prior launcher observability and benchmark attribution gaps: `v1.8.32` exposes current-process and fresh-shell launcher route diagnostics in `tg doctor --json`, including `path_tg_first_launcher_kind`, `fresh_shell_path_tg_first_launcher_kind`, and `path_tg_launcher_warning`, and records `tg_launcher_command_kind` in cold benchmark artifacts so native-exe, `.cmd` shim, `uv`, and Python-module timings are not mixed in search-speed claims.
- Prior public launcher and agent contract gaps: `v1.8.31` puts the managed native front-door directory ahead of compatibility shim directories on Windows User PATH for fresh shells, exposes top-level `validation_commands` on both `context-render` and `edit-plan` JSON, keeps default `classify` deterministic/local unless `TENSOR_GREP_CLASSIFY_PROVIDER=cybert` opts into CyBERT/Triton, and extends GPU benchmark scale/correctness gates to 1GB/5GB rows.
- Prior Windows `.cmd` quoted-pattern gap: `v1.8.30` preserves quoted multi-word no-match patterns from `cmd.exe`, direct `tg.cmd`, and Python `subprocess.run([...])` instead of splitting them into shorter false-positive searches plus bogus paths.
- Prior native-front-door CLI parity gap: `v1.8.29` accepts or intentionally sidecar-routes `tg search --files`, `tg search --multiline` / `-U`, `tg search --null`, `tg run -r`, and `tg classify --format json`; `classify` falls back before expensive provider/model setup when unavailable; and the GPU benchmark harness treats no-match as a valid comparator outcome.
- Public update dogfood: `tg update` from `v1.9.1` installed sidecar `tensor-grep==1.9.2`, refreshed `~/.tensor-grep/bin/tg.exe`, and verified `tg 1.9.2`. `tg --version`, `cmd /c tg --version`, `pwsh -NoProfile -Command "tg --version"`, WSL `tg --version`, Git Bash `tg --version`, and direct `C:\Users\oimir\.tensor-grep\bin\tg.exe --version` report `tg 1.9.2`.
- Prior public installer dogfood: rerunning `scripts/install.ps1` for `v1.8.31` put `C:\Users\oimir\.tensor-grep\bin` ahead of compatibility shim directories on User PATH. A simulated fresh shell resolves `C:\Users\oimir\.tensor-grep\bin\tg.exe` before `C:\Users\oimir\bin\tg.cmd`.
- Public doctor dogfood: `tg doctor --json` reports `version = 1.9.2`, `rust_binary_version_status = matches`, `search_acceleration_backend = standalone-native-tg`, `path_tg_first_launcher_kind = cmd-shim`, `fresh_shell_path_tg_first_launcher_kind = managed-native`, `fresh_shell_path_tg_first_version_matches = true`, and `path_tg_launcher_warning` when the current process still resolves the compatibility shim before the managed native front door.
- Public capsule dogfood: `tg agent src/tensor_grep/cli --query "agent context capsule" --json --max-tokens 300 --max-files 2 --max-sources 2` returned a capsule with primary target, route rationale, snippets, omitted primary follow-up reads, confidence downgrade, rollback command/argv, and ask-before-editing metadata.
- Public `v1.9.0` dogfood follow-up found that ambiguous mixed-language invoice-tax capsule queries could be overconfident and pair a TypeScript primary target with pytest validation. `v1.9.1` adds mixed-language capsule regressions, shared `validation_alignment`, confidence caps for query-language/target-language conflicts, and GPU benchmark recommendation gates.
- Public native CLI dogfood: installed `tg 1.8.32` accepted `tg search --multiline`, `tg search -U`, `tg search --files`, `tg search --null`, `tg run -r`, and `tg classify --format json`.
- Public Windows launcher dogfood: `cmd /c tg`, direct `C:\Users\oimir\.tensor-grep\bin\tg.cmd`, native `tg.exe`, and Python `subprocess.run([...])` all return exit `1` with empty stdout for a fresh quoted no-match phrase.
- Public classify dogfood: `tg classify --format json tests\conftest.py` completed in 0.206s with local deterministic classifications.
- Fast agent-readiness dogfood before PR #72: `python scripts/agent_readiness.py --output artifacts/agent_readiness_launcher_observability.json` passed all checks, including public version probes, `public-windows-launcher-quoted-patterns`, repo doctor, context consistency, deterministic rg parity edges, generated-root guardrails, AST smoke, MCP context-render smoke, and docs claim hygiene.
- Repo-dev dogfood: stale in-tree standalone binaries remain skipped unless explicitly pinned with `TG_NATIVE_TG_BINARY` or `TG_MCP_TG_BINARY`.

## Current Post-v1.9.2 Scope

Current release branch is closed. Use a new branch from `origin/main` for follow-up work. The latest fix release is PR #80 `fix: harden edit JSON and capsule validation trust` at `faf67ed`; main CI run `25609611007` and CodeQL run `25609610737` passed, and semantic-release released `v1.9.2`.

The public Windows `.cmd` bridge quoted multi-word no-match follow-up shipped in `v1.8.30`. The Windows native-first PATH, agent JSON validation-command, local default classify, and GPU scale benchmark follow-ups shipped in `v1.8.31`. The launcher-route observability and benchmark launcher-attribution follow-up shipped in `v1.8.32`. The explicit GPU probe scoping and benchmark launcher warning follow-up shipped in `v1.8.33`. The Actionable Context Capsule v1 shipped in `v1.9.0`; mixed-language capsule trust alignment and GPU recommendation hygiene shipped in `v1.9.1`.

Active post-`v1.9.2` implementation scope:

- Harden `tg agent` / Actionable Context Capsule ranking, explicit language/file-name intent weighting, token economy, follow-up reads, call-site evidence, and workflow benchmarks without changing raw `--format rg`, `--json`, or `--ndjson` semantics.
- Keep edit validation command parsing argv-safe for quoted Windows paths with spaces.
- Preserve mixed-language capsule trust: explicit query language hints, exact symbol intent, primary target language, selected snippets, and validation commands must agree or `confidence.overall` / `primary_target.confidence` must be capped and `ask_user_before_editing.required` must become true.
- Keep validation hints aligned with the selected primary target language unless verified cross-language dependency evidence exists. `validation_alignment` should report filtered mismatches so a TypeScript target is not silently paired with pytest-only validation.
- Keep Windows managed installer/update dogfood checking both the update path and a fresh-shell PATH environment; existing parent shells may keep old PATH until restarted.
- Keep current-process vs fresh-shell launcher routing visible in `tg doctor --json` with `path_tg_first_launcher_kind`, `fresh_shell_path_tg_first_launcher_kind`, and `path_tg_launcher_warning` so slower compatibility-shim timing is visible.
- Keep benchmark `tg_launcher_command_kind` in the environment block so native-exe, `.cmd` shim, `uv`, and Python-module routes are not mixed in cold-path claims. Treat benchmark warnings about shim/interpreter overhead as blocking evidence for performance comparisons.
- Keep `classify` provider/cache UX explicit and fast. The default local path is quick; CyBERT/Triton remains opt-in and must not warn or block agent loops when unavailable.
- Keep GPU experimental until native streaming correctness and speed are proven on the 1GB/5GB gates. Explicit `--gpu-device-ids` routing must stay scoped to selected devices and must not initialize or warn about unrelated unsupported GPUs.
- Keep AST parity roadmap work separate from `tg run`'s validated useful slice; do not imply full ast-grep replacement.

The immediate `v1.8.28` native-front-door CLI parity follow-up shipped in `v1.8.29`:

- Native `search` forwards `--multiline` / `-U`, `--null`, and `--files` shapes instead of rejecting Python-advertised flags.
- Native `run` preserves the short `-r` alias for `--rewrite`.
- Native `classify` accepts `--format json` and sidecar-routes the command instead of timing out in a native parser dead end.
- `classify` now uses a deterministic fallback before tokenizer/model loading when the provider stack is unavailable.
- GPU benchmark correctness accepts `rg` exit code `1` as a valid no-match comparator result when `tg` also returns zero matches.
- This is contract correctness for the public native front door, not a new speed claim.

Released product-surface follow-up: `tg agent` / Actionable Context Capsule is implemented as an opt-in sidecar-routed workflow in `v1.9.0`. The capsule includes primary file/function, route rationale, bounded source snippets with line maps, validation evidence, suggested edit order, checkpoint/rollback metadata, omission counts, confidence, call-site evidence status, and an "ask user before editing" recommendation when uncertainty or risk is high. Capsule v1 leaves `related_call_sites` empty unless verified call-site evidence is explicitly collected. It improves token economy without changing raw `--format rg`, `--json`, or `--ndjson` semantics.

Prior benchmark evidence from the `v1.8.25` native-front-door PR:

- `python benchmarks/run_benchmarks.py --binary rust_core/target/release/tg.exe --launcher-mode explicit_binary --output artifacts/bench_run_benchmarks_native_frontdoor_pr.json`: parity passed on all 10 rows
- `python benchmarks/check_regression.py --baseline auto --current artifacts/bench_run_benchmarks_native_frontdoor_pr.json`: refused comparison because the frozen baseline uses Python `3.12.12` and this host shell uses Python `3.14.4`
- `python benchmarks/check_regression.py --baseline auto --current artifacts/bench_run_benchmarks_native_frontdoor_pr.json --allow-env-mismatch`: no `tg` regressions detected; rg comparator drift was faster on all rows
- measured medians on this host: `tg = 0.259509s`, `rg = 0.112597s`

Do not claim a cold-search speed win from this branch; it is launcher/control-plane correctness evidence and keeps `rg` as the cold exact-text baseline.

## Release Completion Contract

A branch push or open PR starts PR CI only. It is not a release, not a released version, and not complete release state.

Release versioning starts only after a release-bearing PR is squash-merged to `main`, because semantic-release reads the final `main` commit subject.

A release-bearing PR is complete only after PR CI passes, the PR is squash-merged to `main`, main CI and semantic-release complete successfully, the release commit and tag exist on `origin/main`, GitHub release assets are uploaded and verified, `publish-success-gate` passes, `git fetch origin main --tags` is run, agents fast-forward local `main` to the release commit, and PyPI/public installer availability is verified.

Do not report final version state before the GitHub release assets, PyPI/package publish status, public install/update path, and local checkout have all been verified.

For docs/test/chore-only work, use a non-release PR title, wait for PR CI, and merge only when requested or clearly required. After merge, main CI should pass, but semantic-release should skip release publishing.

## What v1.8.12-v1.9.2 Fixed

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
- `--pcre2 --sort path` now stays on the rg passthrough path for exact deterministic output, and multiline searches forward `--multiline` / `--multiline-dotall` to ripgrep.
- Exact symbol context queries such as `createInvoice` now rank literal exact symbols above camel/snake bridge matches.
- Session stale checks ignore non-context files such as `.gitignore`, logs, and generated noise; no-runner sessions no longer invent repo-wide Python test commands without runner evidence.
- MCP rewrite apply can create embedded Python checkpoints when a standalone native `tg` binary is unavailable.
- Inline scan rules preserve `severity` and `message` metadata in JSON output.
- The built-in secrets ruleset catches uppercase `API_KEY = "..."` assignments.
- Unbounded broad generated-root scans now refuse hidden file-list requests and no-ignore/unrestricted fallback scans through generated/cache/dependency directories unless callers bound the scan with `--glob`, `--type`, or `--max-depth`, or explicitly opt in with `--allow-broad-generated-scan`.
- Windows installers now uninstall the tensor-grep Python package that owns a stale `Python*\Scripts\tg.exe` when direct stale-launcher removal cannot clear a PATH shadow.
- Python path-list output uses the UTF-8-safe stdout path and preserves discovery order for `--files-with-matches` fallback output.
- PATH-entry scans skip inaccessible machine PATH directories instead of aborting installation after package install.
- `tg safeParseJSON --files-with-matches`, `tg search safeParseJSON --files-with-matches`, and `tg search --fixed-strings safeParseJSON . --files-with-matches` complete through the root-based rg route.
- Ripgrep backend fallback now parses non-JSON `--files-with-matches` output instead of treating it like match text.
- Plain path-list output uses one trailing LF and preserves `-0/--null` path-list behavior.
- Count plus `-0/--null` parsing is covered.
- `tg ast-info --json` exposes AST language identifiers for agents without scraping text help.
- The `v1.8.26` release moved release-native CPU asset build/upload/verification into main CI after semantic-release, so GitHub release assets are present before PyPI publish and public installers can use the matching native front door.
- The `v1.8.27` release hardened stable installers and sidecar upgrade resolution against stale package metadata, yanked releases, missing post-upgrade imports, unchecked native installer failures, and broken staged replacement.
- The `v1.8.28` release refreshes the managed release-native front door after sidecar upgrades, including the Windows retry-helper path for locked `tg.exe` replacement.
- The `v1.8.29` release hardens public-native CLI parity for advertised search/run/classify flags and fixes the GPU no-match correctness benchmark harness.
- The `v1.8.30` release preserves quoted multi-word no-match patterns through the Windows `.cmd` bridge for `cmd.exe`, direct `tg.cmd`, and Python `subprocess.run([...])`.
- The `v1.8.31` release hardens public launcher and agent contracts: native-first Windows managed PATH ordering for fresh shells, top-level `validation_commands` in `context-render` and `edit-plan`, local deterministic default `classify`, and GPU scale correctness gates for 1GB/5GB rows.
- The `v1.8.32` release adds `doctor` launcher-kind diagnostics and benchmark launcher command-kind attribution. It is release correctness/observability, not a cold-search speed claim.
- The `v1.8.33` release scopes explicit GPU device probing to requested CUDA ordinals and makes benchmark scripts warn when timed entrypoints include `.cmd`, `uv`, or Python-module overhead. It is release correctness/observability, not a GPU speed claim.
- The `v1.9.0` release adds `tg agent` / Actionable Context Capsule as an opt-in agent work packet with primary target metadata, route rationale, bounded snippets with line maps, validation evidence, edit order, rollback/checkpoint metadata, omission counts, confidence, and ask-before-editing guidance. It is token-economy/context orchestration, not a mutation of raw rg/json/ndjson output.
- The `v1.9.1` release hardens mixed-language capsule confidence/validation alignment, filters incompatible validation commands unless cross-language evidence exists, and keeps GPU auto-recommendation false unless the required 1GB/5GB correctness and selected-GPU speed gates pass.
- The `v1.9.2` release hardens edit JSON and rollback safety, keeps validation-failure apply output parseable, and avoids over-downgrading capsules when filtered cross-language commands still leave aligned validation evidence.

## Verified Before Release Closeout

- PR #39 `fix: harden Windows and WSL installer shims`: merged and released as `v1.8.18`
- PR #40 `fix: write WSL bash shims with LF newlines`: merged and released as `v1.8.19`
- PR #42 `fix: polish CLI version help and doctor diagnostics`: merged and released as `v1.8.20`
- PR #44 `fix: ignore stale native binaries in dev resolution`: merged and released as `v1.8.21`
- PR #46 `fix: improve agent context trust and rg parity`: merged and released as `v1.8.22`
- PR #54 `fix: add generated-root scan guardrails`: merged and released as `v1.8.23`
- PR #56 `fix: harden v1.8.23 dogfood regressions`: merged and released as `v1.8.24`
- PR #59 `perf: use native front door for managed installs`: merged and released as `v1.8.25`
- PR #60 `fix: publish GitHub release native assets from main CI`: merged and released as `v1.8.26`
- PR #61 `fix: harden stable installer and upgrade resolution`: merged and released as `v1.8.27`
- PR #62 `fix: refresh managed native front door after upgrade`: merged and released as `v1.8.28`
- PR #64 `fix: harden native front-door CLI parity`: merged and released as `v1.8.29`
- PR #68 `fix: preserve quoted patterns in Windows cmd shim`: merged and released as `v1.8.30`
- PR #70 `fix: harden public launcher and agent contracts`: merged and released as `v1.8.31`
- PR #72 `fix: expose launcher route observability`: merged and released as `v1.8.32`
- PR #74 `fix: scope GPU probing and benchmark launcher warnings`: merged and released as `v1.8.33`
- PR #76 `feat: add actionable agent context capsule`: merged and released as `v1.9.0`
- PR #78 `fix: harden agent capsule trust alignment`: merged and released as `v1.9.1`
- PR #80 `fix: harden edit JSON and capsule validation trust`: merged and released as `v1.9.2`
- `uv run pytest tests/unit/test_install_scripts.py -q`: `18 passed` on the LF-shim fix branch
- `uv run pytest tests/unit/test_cli_bootstrap.py tests/unit/test_cli_modes.py tests/unit/test_public_docs_governance.py -q`: `287 passed` on the CLI polish branch
- PowerShell parser checks for `scripts/install.ps1` under both `pwsh` and Windows PowerShell: passed
- `git diff --check`: passed
- `uv run ruff check .`: passed
- `uv run ruff format --check --preview .`: passed
- `uv run mypy src/tensor_grep`: passed
- `uv run pytest -q`: `1845 passed, 16 skipped`
- `uv run pytest -q`: `1867 passed, 16 skipped` on the `v1.8.22` fix branch
- `uv run pytest -q`: `1878 passed, 16 skipped` on the `v1.8.23` generated-root guard branch
- `uv run pytest -q`: `1891 passed, 16 skipped` on the `v1.8.24` dogfood-regression branch
- `python scripts/agent_readiness.py --output artifacts/agent_readiness.json`: passed before PR #56 merge, including public version probes, context consistency, deterministic rg parity edges, broad generated-root scan guard, AST smoke, MCP smoke, and docs claim hygiene.
- `python benchmarks/run_benchmarks.py --output artifacts/bench_run_benchmarks.json`: parity passed on all 10 rows on the PR #56 branch; `check_regression.py --baseline auto --current artifacts/bench_run_benchmarks.json --allow-env-mismatch` reported no tg benchmark regressions.
- `python scripts/agent_readiness.py --output artifacts/agent_readiness.json`: passed, including `broad-generated-scan-guard`
- `python benchmarks/run_benchmarks.py --output artifacts/bench_run_benchmarks.json`: parity passed on all 10 rows; `check_regression.py --allow-env-mismatch` reported no tg benchmark regressions on the Python-version-mismatched host.
- Main CI run `25527718815`: passed through `publish-pypi`, `validate-pypi-artifacts`, and `publish-success-gate`
- Main CodeQL run `25527718311`: passed
- Release-commit CodeQL run `25528154549`: passed
- Main CI run `25535886184`: passed through `publish-github-release-assets`, `publish-pypi`, and `publish-success-gate`.
- GitHub release asset verifier passed for `v1.8.26` with the `native-frontdoor` profile.
- PyPI reports `tensor-grep 1.8.26` as latest and pinned `tensor-grep==1.8.26` resolves from PyPI JSON.
- Main CI run `25538976953`: passed through `publish-github-release-assets`, `publish-pypi`, and `publish-success-gate`.
- Main CodeQL run `25538976656`: passed.
- Release-commit CodeQL run `25539436754`: passed.
- Main CI run `25541354485`: passed through `publish-github-release-assets`, `publish-pypi`, and `publish-success-gate`.
- Main CodeQL run `25541353932`: passed.
- Release-commit CodeQL run `25541905895`: passed.
- PR #62 local branch checks: `uv run ruff check .`, `uv run ruff format --check --preview .`, and `uv run mypy src/tensor_grep` passed.
- PR #62 targeted tests: `uv run pytest tests/unit/test_cli_modes.py tests/unit/test_public_docs_governance.py tests/unit/test_agent_readiness_script.py -q`: `276 passed in 16.75s`.
- PR #62 full suite: `uv run pytest -q`: `1886 passed, 50 skipped in 231.31s`.
- Post-release fast gate: `python scripts/agent_readiness.py --output artifacts/agent_readiness_post_v1828.json`: all 13 checks passed.
- GitHub release asset verifier passed for `v1.8.28` with the `native-frontdoor` profile.
- Public upgrade dogfood verified `tg upgrade` from `v1.8.27` to sidecar `tensor-grep==1.8.28`, the scheduled Windows native-front-door retry helper, and final profiled PowerShell / `cmd` / `pwsh -NoProfile` / WSL resolution to `tg 1.8.28`.
- PyPI reports `tensor-grep 1.8.28` as latest and pinned `tensor-grep==1.8.28` resolves from PyPI JSON.
- Main CI run `25557263658`: passed through `publish-github-release-assets`, `publish-pypi`, and `publish-success-gate`.
- Main CodeQL run `25557263900`: passed.
- GitHub release assets are uploaded for `v1.8.29`, including `tg-windows-amd64-cpu.exe`, `tg-linux-amd64-cpu`, `tg-macos-amd64-cpu`, `CHECKSUMS.txt`, `BUNDLE_CHECKSUMS.txt`, `oimiragieo.tensor-grep.yaml`, `tensor-grep.rb`, and `PUBLISH_INSTRUCTIONS.md`.
- PyPI version-specific page and simple index expose `tensor-grep 1.8.29`; `python -m pip index versions tensor-grep --no-cache-dir` reports `1.8.29`.
- Public upgrade dogfood verified `tg upgrade` from `v1.8.28` to sidecar `tensor-grep==1.8.29`, the scheduled Windows native-front-door retry helper, and final profiled PowerShell / `cmd` / `pwsh -NoProfile` / Git Bash / WSL resolution to `tg 1.8.29`.
- Public native CLI dogfood verified `tg search --multiline`, `tg search -U`, `tg search --files`, `tg search --null`, `tg run -r`, and `tg classify --format json` on installed `tg 1.8.29`.
- Main CI run `25569020620`: passed through `publish-github-release-assets`, `publish-pypi`, and `publish-success-gate`.
- Main CodeQL run `25569020092`: passed.
- GitHub release assets are uploaded for `v1.8.30`, including `tg-windows-amd64-cpu.exe`, `tg-linux-amd64-cpu`, `tg-macos-amd64-cpu`, `CHECKSUMS.txt`, `BUNDLE_CHECKSUMS.txt`, `oimiragieo.tensor-grep.yaml`, `tensor-grep.rb`, and `PUBLISH_INSTRUCTIONS.md`.
- PyPI version-specific page and simple index expose `tensor-grep 1.8.30`; `python -m pip index versions tensor-grep --no-cache-dir` reports `1.8.30`.
- Public update dogfood verified `tg update` from `v1.8.29` to sidecar `tensor-grep==1.8.30`, the scheduled Windows native-front-door retry helper, and final profiled PowerShell / `cmd` / `pwsh -NoProfile` / Git Bash / WSL resolution to `tg 1.8.30`.
- Public launcher dogfood verified `cmd /c tg`, direct `tg.cmd`, native `tg.exe`, and Python `subprocess.run([...tg.cmd...])` preserve `"gpu no-such-phrase"` as one no-match pattern and return exit `1` with no false-positive stdout.
- Main CI run `25576067952`: passed through `publish-github-release-assets`, `publish-pypi`, and `publish-success-gate`.
- Main CodeQL/dynamic run `25576067576`: passed on the release-bearing merge commit; release-commit dynamic run `25576666702` passed.
- GitHub release assets are uploaded for `v1.8.31`, including `tg-windows-amd64-cpu.exe`, `tg-linux-amd64-cpu`, `tg-macos-amd64-cpu`, `CHECKSUMS.txt`, `BUNDLE_CHECKSUMS.txt`, `oimiragieo.tensor-grep.yaml`, `tensor-grep.rb`, and `PUBLISH_INSTRUCTIONS.md`.
- PyPI JSON exposes `tensor-grep 1.8.31`; pinned `tensor-grep==1.8.31` resolves from PyPI JSON.
- Public update dogfood verified `tg update` from `v1.8.30` to sidecar `tensor-grep==1.8.31`, the scheduled Windows native-front-door retry helper, and final profiled PowerShell / `cmd` / `pwsh -NoProfile` resolution to `tg 1.8.31`.
- Public installer dogfood verified `scripts/install.ps1` puts `C:\Users\oimir\.tensor-grep\bin` ahead of compatibility shim directories on User PATH, and a simulated fresh shell resolves the native managed front door first.
- Public launcher dogfood verified `cmd /c tg`, direct managed `tg.cmd`, and native `tg.exe` preserve a fresh quoted no-match phrase as one pattern and return exit `1` with no false-positive stdout.
- Public classify dogfood verified local deterministic `tg classify --format json tests\conftest.py` in 0.206s.
- Main CI run `25581373995`: passed through `publish-github-release-assets`, `publish-pypi`, and `publish-success-gate`.
- Main CodeQL/dynamic run `25581373725`: passed on the release-bearing merge commit; release-commit dynamic run `25581894666` passed.
- GitHub release assets are uploaded for `v1.8.32`, including `tg-windows-amd64-cpu.exe`, `tg-linux-amd64-cpu`, `tg-macos-amd64-cpu`, `CHECKSUMS.txt`, `BUNDLE_CHECKSUMS.txt`, `oimiragieo.tensor-grep.yaml`, `tensor-grep.rb`, and `PUBLISH_INSTRUCTIONS.md`.
- PyPI JSON exposes `tensor-grep 1.8.32`; pinned `tensor-grep==1.8.32` resolves from PyPI JSON.
- Public update dogfood verified `tg update` from `v1.8.31` to sidecar `tensor-grep==1.8.32`, the scheduled Windows native-front-door retry helper, and final profiled PowerShell / `cmd` / `pwsh -NoProfile` resolution to `tg 1.8.32`.
- Public doctor dogfood verified `path_tg_first_launcher_kind = cmd-shim`, `fresh_shell_path_tg_first_launcher_kind = managed-native`, and `path_tg_launcher_warning` when the current process still sees the compatibility shim before fresh-shell PATH.
- Main CI run `25586858341`: passed through `publish-github-release-assets`, `publish-pypi`, and `publish-success-gate`.
- Main CodeQL run `25586857874`: passed on the release-bearing merge commit.
- GitHub release assets are uploaded for `v1.8.33`, including `tg-windows-amd64-cpu.exe`, `tg-linux-amd64-cpu`, `tg-macos-amd64-cpu`, `CHECKSUMS.txt`, `BUNDLE_CHECKSUMS.txt`, `oimiragieo.tensor-grep.yaml`, `tensor-grep.rb`, and `PUBLISH_INSTRUCTIONS.md`.
- PyPI JSON exposes `tensor-grep 1.8.33`; pinned `tensor-grep==1.8.33` resolves from PyPI JSON.
- Public update dogfood verified `tg update` from `v1.8.32` to sidecar `tensor-grep==1.8.33`, the scheduled Windows native-front-door retry helper, and final profiled PowerShell / `cmd` / `pwsh -NoProfile` / native `tg.exe` resolution to `tg 1.8.33`.
- Public doctor dogfood verified `version = 1.8.33`, `rust_binary_version_status = matches`, `search_acceleration_backend = standalone-native-tg`, `path_tg_first_launcher_kind = cmd-shim`, `fresh_shell_path_tg_first_launcher_kind = managed-native`, and `path_tg_launcher_warning` when the current process still sees the compatibility shim before fresh-shell PATH.
- Main CI run `25601232312`: passed through `publish-github-release-assets`, `publish-pypi`, and `publish-success-gate`.
- Main CodeQL run `25601232120`: passed on the release-bearing merge commit.
- GitHub release assets are uploaded for `v1.9.0`, including `tg-windows-amd64-cpu.exe`, `tg-linux-amd64-cpu`, `tg-macos-amd64-cpu`, `CHECKSUMS.txt`, `BUNDLE_CHECKSUMS.txt`, `oimiragieo.tensor-grep.yaml`, `tensor-grep.rb`, and `PUBLISH_INSTRUCTIONS.md`.
- PyPI JSON exposes `tensor-grep 1.9.0`; pinned `tensor-grep==1.9.0` resolves from PyPI JSON.
- Public update dogfood verified `tg update` from `v1.8.33` to sidecar `tensor-grep==1.9.0`, and final profiled PowerShell / `cmd` / `pwsh -NoProfile` / WSL / Git Bash / native `tg.exe` resolution to `tg 1.9.0`.
- Public doctor dogfood verified `version = 1.9.0`, `rust_binary_version_status = matches`, `search_acceleration_backend = standalone-native-tg`, `path_tg_first_launcher_kind = cmd-shim`, `fresh_shell_path_tg_first_launcher_kind = managed-native`, and `path_tg_launcher_warning` when the current process still sees the compatibility shim before fresh-shell PATH.
- Public capsule dogfood verified `tg agent src/tensor_grep/cli --query "agent context capsule" --json` returns the Actionable Context Capsule contract.
- Main CI run `25604843919`: passed through `publish-github-release-assets`, `publish-pypi`, and `publish-success-gate`.
- Main CodeQL runs `25604843742` and release-commit `25605123376`: passed.
- GitHub release assets are uploaded for `v1.9.1`, including `tg-windows-amd64-cpu.exe`, `tg-linux-amd64-cpu`, `tg-macos-amd64-cpu`, `CHECKSUMS.txt`, `BUNDLE_CHECKSUMS.txt`, `oimiragieo.tensor-grep.yaml`, `tensor-grep.rb`, and `PUBLISH_INSTRUCTIONS.md`.
- PyPI JSON exposes `tensor-grep 1.9.1`; pinned `tensor-grep==1.9.1` resolves from PyPI JSON.
- Public update dogfood verified `tg update` from `v1.9.0` to sidecar `tensor-grep==1.9.1`, and final profiled PowerShell / `cmd` / `pwsh -NoProfile` / WSL / Git Bash / native `tg.exe` resolution to `tg 1.9.1`.
- Public doctor dogfood verified `version = 1.9.1`, `rust_binary_version_status = matches`, `search_acceleration_backend = standalone-native-tg`, `path_tg_first_launcher_kind = cmd-shim`, `fresh_shell_path_tg_first_launcher_kind = managed-native`, and `path_tg_launcher_warning` when the current process still sees the compatibility shim before fresh-shell PATH.
- Fast agent-readiness dogfood after `v1.9.1` passed public version probes, launcher quoted-patterns, rg parity edges, generated-root guardrails, AST smoke, MCP context-render smoke, `agent-capsule`, and `agent-capsule-mixed-language`; first run timed out `repo-doctor`/`context-render-trust` during a cold Rust editable rebuild before warm rerun.

## What Works Well Now

- Scoped text search, JSON, NDJSON, multi-root search, globs, `--column`, `--vimgrep`, `--path-separator`, `--type-list`, and invalid-regex diagnostics are stable enough for agent workflows.
- Normal PowerShell `tg`, `cmd /c tg`, `pwsh -NoProfile -Command "tg ..."`, Git Bash `tg`, and WSL `tg` resolved through the public Windows install by `v1.8.25`; installer/update changes must re-run those probes before release closeout.
- `tg --version` is script-friendly by default; use `tg --version --verbose` for feature/SIMD/Arrow diagnostics.
- Stable managed installs prefer the release-native front door when the matching GitHub asset exists; Python remains the sidecar/fallback instead of the first hop for normal shell `tg`. `v1.8.31` fresh installer dogfood confirms User PATH puts `~/.tensor-grep/bin` ahead of compatibility shim directories, `v1.8.32` doctor output distinguishes current-process shim drift from fresh-shell managed-native resolution, `v1.8.33` benchmark scripts warn when timings still include shim/interpreter overhead, and `v1.9.0` update dogfood keeps sidecar/native versions aligned.
- Public help starts with `Usage: tg`, including `python -m tensor_grep --help` and installed command help paths.
- `defs`, `source`, `refs`, `callers`, `context-render`, and `blast-radius` are useful for scoped repo navigation and planning.
- Released context work tightens `context-render` / MCP trust: source-body evidence ranks natural queries, default LLM rendering preserves executable body lines, `context_consistency` reports seed/render/navigation agreement, and validation commands carry detection provenance.
- Symbol outputs are compact on hits and no-matches; CommonJS symbol extraction and reference dedupe are materially improved.
- Bounded blast-radius defaults and output-limit metadata make scoped impact checks safer for agent loops.
- Unbounded broad generated-root searches refuse by default before walking generated/cache/dependency roots; use `--allow-broad-generated-scan` only when that large walk is intentional.
- MCP entrypoint is present via `tg mcp --help`; MCP tool behavior is covered by the repo tests.
- GPU devices are detected locally; GPU routing remains benchmark-governed and should not be marketed as automatic crossover.
- GPU benchmark auto-recommendation must remain false unless required 1GB/5GB correctness checks pass and a selected GPU beats both `rg` and `tg_cpu` at required scale. Unsupported-device inventory warnings should stay top-level or on the unsupported device row, not on unrelated selected-GPU timing rows.
- `ast-grep` remains the structural-search feature/performance baseline; `tg run` is a useful validated slice, not full ast-grep equivalence.
- The native front door must not advertise a Python CLI surface that it rejects. If a command remains Python-backed, route it to the sidecar deliberately and regression-test the public native invocation shape.
- Token-efficiency work is now anchored by the explicit `tg agent` capsule command, not a mutation of raw search outputs. Keep improving hard budgets, line maps, omission counts, validation evidence, checkpoint/rollback metadata, and confidence without changing raw search contracts.

## Known Weak Spots

- `rg` remains the raw cold exact-text benchmark. `tg` should win on agent-native code intelligence, not by pretending every grep workload is faster.
- `ast-grep` remains the structural-search feature/performance baseline until the AST compatibility roadmap is closed with tests and benchmark evidence.
- Broad generated roots remain agent-hostile when callers opt into them. Unbounded `tg search --files --hidden` scans and no-ignore/unrestricted fallback scans through generated/cache/dependency directories are refused unless the request is bounded with `--glob`, `--type`, or `--max-depth`, or explicitly opts in with `--allow-broad-generated-scan`. Use scoped paths, globs, file types, and `--max-depth` for `tg search` before reaching for opt-in. `--max-repo-files`, `--max-callers`, and `--max-files` are code-intelligence command budgets, not `tg search` flags.
- `impact --symbol` now marks `preferred_command = "blast-radius"` because it remains a broader planning signal; prefer `blast-radius` for direct symbol impact.
- `validation_commands` can still be heuristic when stack evidence is partial. Treat targeted commands as hints, not proof of full coverage; require `validation_plan[].detection`, do not trust npm/package-manager hints without `package.json` evidence, and omit commands entirely when no runner evidence exists.
- `validation_alignment` is part of the agent trust story. A selected primary target language should not silently receive validation commands from an incompatible runner family; mismatches should be filtered or explicitly recorded with confidence downgrade.
- Local `uv run tg doctor --json` can find stale in-tree standalone binaries at `rust_core/target/debug/tg.exe` or `rust_core/target/release/tg.exe`. Current dev-path safety should ignore them for implicit native delegation, report them under `skipped_native_tg_binaries`, set `rust_binary_version_status = stale-skipped`, and keep `search_acceleration_backend = rust-core-extension` when the embedded extension is available. Rebuild with `C:/Users/oimir/.cargo/bin/cargo.exe build --manifest-path rust_core/Cargo.toml --release` or pin `TG_NATIVE_TG_BINARY` to opt in to a specific standalone binary.
- Explicitly opted-in broad `tg search --files ...` over generated artifact trees can still be expensive. The managed launchers and Python path-list output should force UTF-8, but scope file-list commands to the smallest useful root.
- Public installer/update reliability is a release contract, not an open fire. Stable installs and `tg upgrade` must not trust stale package metadata, must verify the target Python can still import `tensor_grep`, must check native installer exit codes, must not remove a working managed install before the replacement environment and front-door files succeed, and must keep the managed native front door aligned with the verified sidecar version.
- `tg doctor --json` is now the first launcher-drift check. Inspect `path_tg_first_launcher_kind`, `fresh_shell_path_tg_first_launcher_kind`, and `path_tg_launcher_warning` before trusting Windows timing results from an existing shell.
- Cold-path benchmark artifacts should include `tg_launcher_command_kind` alongside the configured `tg_launcher_mode`; do not combine native-exe, `.cmd` shim, `uv`, and Python-module timings in one speed claim. Benchmark warnings about shim/interpreter overhead are blocking evidence for performance comparisons.
- Root-scale unsorted `--files-with-matches`, `--count`, and `--force-cpu` can still differ from raw `rg` in output ordering even when the file set and counts match. Use `--sort path` for deterministic path ordering and `--format rg` for exact ripgrep-style text formatting before claiming golden stdout parity; sorted files-with-matches, files-without-match, and replacement output are now regression-covered parity edges on the active branch.
- Directly invoking `C:\Users\oimir\bin\tg.cmd` from PowerShell with an unescaped metacharacter such as `|` is still a `cmd.exe` parser limitation; use normal PowerShell `tg` / `tg.ps1` or quote the metacharacter argument for `cmd.exe`.
- Always verify command resolution with `tg --version`, `cmd /c tg --version`, `pwsh -NoProfile -Command "tg --version"`, `where.exe tg`, `Get-Command tg -All`, and WSL `wsl bash -lc 'command -v tg; tg --version'` after installer changes. A stale `Python*\Scripts\tg.exe` returning an older tensor-grep version is a release blocker.

## Next Highest-Value Work

1. Keep the agent-readiness dogfood gate (`python scripts/agent_readiness.py --output artifacts/agent_readiness.json`) fast and representative; it should cover context trust, `agent-capsule`, rg sorted edges, broad generated-root scan guardrails, AST smoke, MCP smoke, shell version probes, and docs claim checks.
2. Add progress or partial output for explicitly opted-in broad generated-root scans.
3. Keep dogfooding `impact --symbol` preferred-command metadata so agents consistently choose `blast-radius` for direct impact.
4. Track AST parity roadmap gaps, GPU benchmark/no-match cleanup, and `classify` provider/cache UX as blockers for a future "world-class" claim, not as blockers for this launcher/control-plane PR.
5. Harden the opt-in `tg agent` capsule budget profile inspired by `rtk`: grouped by file, capped globally and per file, with line truncation and omission counts. Keep raw `--format rg`, `--json`, and `--ndjson` contracts unchanged.
6. Keep the `agent-capsule-mixed-language` readiness check in the fast dogfood gate so explicit language intent, exact symbol intent, validation alignment, and ask-before-editing behavior stay regression-covered.
7. Keep dogfooding `tg` first and record exact failing commands, exit codes, and outputs as product evidence.

## Safe Next-Session Commands

```powershell
git status --short --branch
git log -3 --oneline
uv run tg --version
uv run tg doctor --json
python -m pip index versions tensor-grep --index-url https://pypi.org/simple --no-cache-dir
gh release view v1.9.1 --json tagName,publishedAt,url,assets
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

Avoid broad generated-root file-list probes unless the task needs them. Bound the request or opt in explicitly:

```powershell
tg search --files . --hidden --glob "*.py"
tg search --files . --hidden --max-depth 3
tg search --files . --hidden --no-ignore --allow-broad-generated-scan
```
