---
name: tensor-grep
description: Use when searching code, logs, or repositories with tensor-grep; validating rg or AST parity; using tg MCP tools; checking GPU/search routing; or producing agent-friendly context, source, refs, or blast-radius output.
---

# tensor-grep (tg)

## Current State

release_docs_current_tag: v1.12.40

As of 2026-05-14, the current tagged version is `v1.12.40`, and the latest complete public PyPI/release-asset distribution is also `v1.12.40`. Stable installer, PyPI metadata refresh, release-native asset publication, managed-native front-door refresh after `tg upgrade`, stale tensor-grep-owned `tg.com` bridge refresh after upgrade, native-front-door CLI parity for advertised public flags, Windows `.cmd` quoted-pattern launcher handling, native-first Windows PATH ordering, top-level validation-command JSON, local default `classify`, classify provider provenance, fixed multi-pattern native CPU search, GPU scale benchmark correctness gates, launcher-route observability, benchmark launcher attribution, scoped GPU device probing, benchmark launcher warnings, the opt-in `tg agent` Actionable Context Capsule, mixed-language capsule confidence/validation alignment, GPU benchmark recommendation hygiene, edit JSON/rollback safety, capsule validation-trust fixes, explicit language/file-name ranking, quoted Windows validation commands, docs governance, `$file` / `{file}` validation placeholder substitution, native CUDA correctness gates, ambiguous capsule alternatives, root help-menu diagnostics, foreign launcher diagnostics, benchmark promotion-gate taxonomy, agent workflow benchmark governance, capsule alternative-confidence capping, generic provider-token `secrets-basic` regex rules, release-docs synchronization, release wheel Cargo prefetch retries, native GPU/search accuracy hardening, explicit Windows Python subprocess launcher repair, agent capsule hardcase routing, and Windows subprocess bridge ranking hardening are in the public `v1.12.40` GitHub asset and PyPI release line.

Current release facts:

- PR #143 `21e5437 fix: collect capsule call-site evidence` merged and released as `v1.12.14`
- PR #142 `8a73f8d fix: harden agent bridge ranking` merged and released as `v1.12.13`
- PR #141 `b601366 fix: harden agent output budget hygiene` merged and released as `v1.12.12`
- PR #140 `2aebac6 fix: harden ast cli contract hygiene (#140)` merged and released as `v1.12.11`
- PR #139 `bbc08e4 fix: harden rg flag contract aliases (#139)` merged and released as `v1.12.10`
- PR #138 `21627d2 fix: harden v1.12.8 dogfood contracts` merged and released as `v1.12.9`
- PR #137 `f848748 fix: route cold rg-shaped searches to rg (#137)` merged and released as `v1.12.8`
- PR #128 `da44a2f fix: harden v1.12.6 dogfood cli contracts` merged and released as `v1.12.7`
- PR #116 `a78e33c fix: harden post-release docs governance` merged and released as `v1.11.5`
- PR #114 `361e0db fix: harden public GPU unavailable routing` and PR #115 `2100122 fix: harden release docs stamp governance` merged and released as `v1.11.4`
- PR #113 `87d4ca4 fix: accelerate fixed multi-pattern native search` merged and released as `v1.11.3`
- Latest verified release proof merge commit: `c0cb613 fix: harden v1.12.33 dogfood contracts`
- Latest verified release proof commit: `e069f67 chore(release): v1.12.34 [skip ci]`
- Latest verified proof public release commit: `e069f67 chore(release): v1.12.34 [skip ci]`
- Latest merged fix commit: `c0cb613 fix: harden v1.12.33 dogfood contracts`
- Latest merged feature commit: `a518cc6 feat: add agent success harness`
- PR #110 `fix: expose classify provider provenance` merged and released as `v1.11.2`
- PR #105 `fix: add explicit Windows subprocess launcher repair` merged and released as `v1.10.10`
- Merge commit: `dd995fc fix: add explicit Windows subprocess launcher repair`
- PR #101 `fix: harden gpu search accuracy contracts` merged and released as `v1.10.7`
- PR #100 `fix: harden v1.10.5 dogfood blockers` merged and released as `v1.10.6`
- PR #91 `fix: harden release wheel retries` merged and released as `v1.9.11`
- PR #90 `fix: harden v1.9.9 dogfood followups` merged and released as `v1.9.10` on GitHub assets; PyPI publication was completed by the v1.9.11 release-wheel retry follow-up
- PR #89 `fix: add agent workflow benchmark governance` merged and released as `v1.9.9`
- PR #87 `fix: refresh stale tg.com bridge after upgrade` merged and released as `v1.9.8`
- PR #86 `fix: clarify GPU benchmark promotion gates` merged and released as `v1.9.7`
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
- `v1.11.0` main CI run `25834508800` passed the pre-release matrix and semantic-release, but release-native asset publication was cancelled; `publish-success-gate` failed and PyPI latest remains `1.10.10`.
- Main CI run `26094452260` passed the pre-release matrix, semantic-release, PyPI artifact validation, `publish-github-release-assets`, `publish-pypi`, and `publish-success-gate`. Latest CodeQL proof run `26064676072` passed on the `v1.12.32` release line; prior CodeQL run `25951813292` passed on the `v1.12.14` release line.
- PyPI pinned public install resolves with `uvx --refresh-package tensor-grep --from tensor-grep==1.12.14 tg --version`
- GitHub release assets for `v1.12.14` include native CPU front doors, checksums, winget manifest, Homebrew formula, and publish instructions
- Public `v1.12.14` dogfood verified PyPI, `uvx`, GitHub assets, public launcher resolution, bounded capsule call-site evidence, agent bridge ranking hardening, agent output-budget hygiene, AST CLI contract hygiene, bounded map/context output, `tg run --pattern`, root cold rg-shaped routing, accepted search/native-front-door CLI drift fixes, `tg new --base-dir`, edit-plan budget flags, explicit rg JSON Lines routing via `--format rg --json`, explicit JSON/NDJSON schema positioning, and rg flag-contract aliases; public managed GPU remains not promotion-ready and falls back to CPU or unsupported rows unless a CUDA-feature native build proves `NativeGpuBackend` with `sidecar_used = false`.
- Public `v1.11.5` dogfood verified PyPI, `uvx`, GitHub assets, and post-release-safe docs governance; `v1.11.4` remains the native GPU unavailable fallback to `NativeCpuBackend` release, `v1.11.3` remains the fixed multi-pattern native CPU release, and `v1.10.10` remains the historical Windows subprocess launcher repair release.
- Public `v1.11.2` dogfood verified PyPI, `uvx`, GitHub assets, and classify provider provenance in JSON output.
- PR #102 `fix: harden v1.10.7 dogfood followups` merged and released as `v1.10.8`
- Public `v1.9.9` dogfood: direct managed native `~/.tensor-grep/bin/tg.exe` reports `tg 1.9.9`; `uvx --from tensor-grep==1.9.9 tg --version` reports `tensor-grep 1.9.9`; `tg update` advanced `1.9.8` to `1.9.9`, refreshed the managed native front door, and fresh `cmd` plus unprofiled `pwsh` report `tg 1.9.9`.
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
- The native front door must accept advertised public flags or intentionally route them to the sidecar. The current release line covers `tg search --files`, `tg search --multiline` / `-U`, `tg search --null`, `tg run -r`, `tg classify --format json`, advertised rg-style search flags, option-first root `tg ...` forwarding, Windows `.cmd` quoted multi-word no-match patterns, native-first Windows PATH ordering for fresh managed shells, and launcher-route observability for current-process versus fresh-shell PATH drift.
- Post-`v1.12.6` dogfood hardening in progress: keep Python/dev and native/public search flag surfaces aligned for accepted rg-compatibility aliases such as `--passthrough`, `--unicode`, `--auto-hybrid-regex`, and `tg search --version`; top-level structured search flags such as `tg --json --no-ignore PATTERN PATH` must parse through the native front door; `tg new` must either create the requested scaffold under the requested base directory or fail before writing files; `edit-plan`, MCP `tg_edit_plan`, and session edit-plan should accept `max_sources` / `max_tokens` for agent command-surface parity while still emitting no rendered source text.
- The quoted multi-word no-match pattern case from `cmd.exe`, direct `tg.cmd`, and Python `subprocess.run([...])` is a public Windows launcher contract. A split pattern can become a shorter false-positive search plus bogus paths, so keep `public-windows-launcher-quoted-patterns` in the fast agent-readiness gate.
- Stable managed installs should prefer the matching release-native CPU front door when the GitHub release asset exists, while keeping the isolated Python environment as sidecar/fallback via `TG_SIDECAR_PYTHON` and `TG_NATIVE_TG_BINARY`. Installer changes should preserve the staged replacement contract so a failed install cannot break an existing public shim, including checking native installer command exit codes before the staged swap. On Windows, the managed native front-door directory should be ahead of compatibility `.cmd` shim directories on PATH so `cmd`, unprofiled PowerShell, and Python subprocess calls resolve `~/.tensor-grep/bin/tg.exe` before the slower argv-safe bridge. If a copied tensor-grep `tg.com` bridge is used to outrank a foreign same-directory `tg.exe`, it must still discover `~/.tensor-grep/.venv` for sidecar commands and point `TG_NATIVE_TG_BINARY` back to the managed native front door. Python subprocess resolution must be checked directly because `CreateProcess` can choose a foreign `.exe` route that shell `PATHEXT` hides. `tg upgrade` must verify the sidecar import/version before claiming success, including the scheduled Windows self-upgrade path, and managed native front doors must be refreshed when the verified sidecar version moves ahead of `tg.exe`.
- `tg doctor --json` is the first check for launcher drift. Inspect `path_tg_first_launcher_kind`, `fresh_shell_path_tg_first_launcher_kind`, `python_subprocess_path_tg_first_launcher_kind`, `path_tg_launcher_warning`, and any `*_is_foreign` / `*_foreign_remediation` fields before trusting Windows benchmark timings; existing shells can retain the slower compatibility shim even after fresh User PATH resolves the native front door, Python subprocesses can resolve differently from shells, and unrelated tools can own a different `tg` command.
- Cold-path benchmark artifacts should separate configured launcher mode from actual timed command kind. Use `environment.tg_launcher_mode` for the experiment and `environment.tg_launcher_command_kind` to distinguish native-exe, `.cmd` shim, `uv`, and Python-module routes. Also preserve `tg_binary_version_status`; stale in-tree native binaries block claim-quality benchmark scripts by default unless `--allow-claim-unsafe-launcher` marks the run as exploratory. Treat benchmark warnings about shim/interpreter overhead as blocking for performance comparisons.
- Explicit `--gpu-device-ids` routing should only probe selected CUDA ordinals. Selecting GPU 0 must not initialize or warn about unrelated unsupported devices such as GPU 1.
- GPU benchmark auto-recommendation must stay false unless required 1GB/5GB correctness checks pass and a selected GPU beats both `rg` and `tg_cpu` at required scale. Unsupported-device inventory warnings should stay top-level or on the unsupported device row, not on unrelated selected-GPU timing rows. GPU-requested CPU fallback or sidecar compatibility output must report `gpu_evidence_status = unsupported`, `gpu_proof = false`, `native_gpu_unavailable`, and `not_gpu_proof_reason`; unsupported rows should carry `promotion_evidence = false`.
- `--format rg --sort path` is the deterministic rg-shaped stdout contract. Token-saving output work should be a separate opt-in agent profile, not a mutation of raw rg/json/ndjson contracts.
- `tg search --json` is tensor-grep aggregate JSON, not rg JSON Lines. `tg search --format rg --json` is the explicit rg JSON Lines compatibility route and emits raw rg events without the tensor-grep envelope. `tg search --ndjson` is tensor-grep's flattened streaming row schema, not the rg event schema. Use `--format rg` for rg-shaped output and keep schema claims explicit.
- `tg agent` / Actionable Context Capsule is the product wedge: an opt-in workflow packet with primary file/function, route rationale, bounded snippets with line maps, validation evidence, edit order, checkpoint/rollback metadata, omission counts, confidence, call-site evidence status, and an "ask user before editing" recommendation when evidence is weak. Capsule v1 leaves `related_call_sites` empty unless verified call-site evidence is explicitly collected. Evidence labels should distinguish `parser-backed`, `rg-backed`, `graph-derived`, `heuristic`, `LSP-confirmed`, and `stale/uncertain` conclusions.
- LSP provider-backed navigation is optional and experimental. `tg lsp-setup` / `tg doctor --with-lsp` provider availability means the binary is installed, not that semantic requests work; inspect `health_status`, `health_check`, `lsp_proof`, `lsp_evidence_status`, and `not_lsp_proof_reason` before treating an `lsp` / `hybrid` result as provider-confirmed. Rows require `lsp_provider_response = true` from a completed provider request before they can contribute to `lsp_proof`.
- Before editing from an agent capsule, inspect top-level `ambiguity`. `ambiguity.status = "tie_requires_confirmation"` is a hard stop for autonomous edits. `ambiguity.status = "tie_resolved"` is acceptable only when `ambiguity.resolved_by` contains explicit evidence.
- `tg agent --gpu-device-ids ... --json` and MCP `tg_agent_capsule(..., gpu_device_ids=[...])` are opt-in GPU evidence paths. They should expose `gpu_acceleration`, require `NativeGpuBackend` with `sidecar_used = false` before using the evidence, and report sidecar-routed GPU as unsupported.
- Feature or tool changes must update the matching implementation, tests, docs/contracts, README, CLI help (`tg --help` and command-specific help), native front-door help when applicable, MCP signatures/docs when agent-facing, and this skill when repo operating practice changes.
- Capsule confidence must be honest when query language hints, exact symbol intent, primary target language, selected snippets, and validation commands disagree. In mismatch cases, cap both `confidence.overall` and `primary_target.confidence`, expose `query_language_hints`, `primary_target_language`, `validation_alignment`, and `validation_filtered_count`, and require ask-before-editing.
- Product-roadmap docs are current through PR #66, and capsule v1 shipped in PR #76. Future sessions should harden capsule behavior behind explicit contracts and regression tests, not reinterpret the roadmap as permission to alter raw search output.
- `context-render` / MCP context output must keep `edit_plan_seed.primary_file`, `navigation_pack.primary_target.file`, selected files/sources, and follow-up reads consistent. Check `context_consistency` when debugging agent handoff quality.
- Default JSON/LLM context rendering must include executable body lines for selected functions. Compactness may strip comments, docstrings when optimized, blank lines, type-only imports, and boilerplate, but it is not a summary-only profile.
- `tg ast-info --json` exposes AST language identifiers for agents without help-text scraping.
- GPU support exists and local devices can be detected, but GPU routing is benchmark-governed. Public managed GPU promotion requires a current managed NVIDIA native front door with `tg-native-metadata.json`, `NativeGpuBackend`, `sidecar_used = false`, direct `rg --json` 1GB/5GB match-identity correctness, speed wins over both `rg` and `tg_cpu`, and `benchmarks/run_gpu_native_benchmarks.py --public-managed-proof` emitting `public_managed_promotion_ready = true` plus `public_gpu_proof = true` from the dispatch-only `public-gpu-proof.yml` workflow. Local CUDA-feature binaries can prove implementation mechanics but are not public GPU readiness by themselves.
- `classify` is local and deterministic by default. Use `TENSOR_GREP_CLASSIFY_PROVIDER=cybert` only when intentionally probing the CyBERT/Triton provider path, and keep provider failures quiet/fast for agent loops.
- `edit-plan` and `context-render` JSON should both expose top-level `validation_commands`; use that field first before inspecting nested `navigation_pack` or `edit_plan_seed`.
- Broad generated roots need bounds. Unbounded `tg search --files --hidden` scans and no-ignore/unrestricted fallback scans through generated/cache/dependency directories are refused unless bounded with `--glob`, `--type`, or `--max-depth`, or explicitly opted in with `--allow-broad-generated-scan`. Use scoped paths, globs, file types, and `--max-depth` for `tg search` before reaching for opt-in. `--max-repo-files`, `--max-callers`, and `--max-files` are code-intelligence command budgets, not `tg search` flags.

Dogfood follow-up workflow:

- Split dogfood feedback into PR-sized slices with one behavioral theme per branch; do not collapse independent fixes into one broad PR.
- Use Exa research before coding when the slice depends on current external behavior such as `rg -F -e`, `ast-grep`, CUDA/Blackwell support, GitHub Actions, release packaging, or agent-evaluation harnesses.
- Run a thinktank or equivalent independent planning review for benchmark interpretation, GPU promotion policy, product positioning, and release workflow changes.
- Ask Gemini for a bounded read-only diff review before each PR merge, then verify any finding locally before changing code.
- For every slice: start with the contract test, implement the smallest fix, run the targeted suite, run lint and format, push the PR, wait for PR CI, squash-merge, then watch main CI.
- Maintain a per-slice evidence ledger for dogfood follow-up work. Each slice entry must record PR order, slice scope, Exa research anchors, thinktank or planning consensus, subagent ownership, Gemini review result, validation commands, PR CI, and main CI. Optional or triggered items may be marked `not applicable` only with a rationale.
- For release-bearing slices, final status also requires semantic-release, release assets, PyPI/package publication, and public release dogfood evidence.

Current post-`v1.12.40` dogfood slice ledger:

- PR order: 1; scope: accept and forward remaining rg config-override flags (`--pcre2-unicode`, `--ignore`, `--messages`, `--require-git`, `--no-hidden`) in native/Python search and add them to the installed-public sweep; Exa anchors: ripgrep manpage option inversion/config behavior plus ripgrep guide automatic-filtering defaults; thinktank/planning consensus: local planning review, external council not applicable for this parser/forwarding contract slice; subagent ownership: not applicable; Gemini review: unavailable because Gemini CLI 0.42.0 hung on a one-token read-only model probe and was killed; validation: Rust crate tests, full pytest, lint, format, mypy, and diff whitespace checks pass locally; PR CI/main CI: pending.
- PR order: 1; scope: make `run_agent_success_harness.py` refuse stale in-tree native `tg` binaries by default and mark `--allow-claim-unsafe-launcher` runs as exploratory; Exa anchors: not applicable beyond existing benchmark-governance policy; thinktank/planning consensus: local planning review aligned with `run_benchmarks.py` stale-binary refusal; subagent ownership: not applicable; Gemini review: unavailable because Gemini CLI 0.42.0 hung on a one-token read-only model probe and was killed; validation: Rust crate tests, full pytest, lint, format, mypy, and diff whitespace checks pass locally; PR CI/main CI: pending.
- PR order: 1; scope: accept and forward the 25 remaining ripgrep inverse/config-override flags found by `parser_sweep_1_12_31_codex.json`, including `--no-auto-hybrid-regex`, `--no-pcre2-unicode`, `--no-text`, `--no-binary`, `--no-follow`, `--ignore-dot`, `--ignore-vcs`, `--no-json`, and `--no-stats`, and batch those 25 installed-public sweep probes into one command to avoid adding dogfood latency; Exa anchors: current ripgrep guide/manpage behavior for config override flags plus local `rg 15.1.0` acceptance sweep; thinktank/planning consensus: local planning review only, external council not applicable because this is parser/forwarding contract work and does not alter GPU/LSP/product positioning; subagent ownership: not applicable, no subagents requested for this turn; Gemini review: unavailable because `gemini-3.1-pro-preview` returned an invalid empty stream and `gemini-2.5-flash` stalled after startup; validation: targeted parser/backend/readiness tests, full `test_public_native_cli_parity`, direct built-native acceptance of all 25 flags, full Python/Rust suites, lint, format, mypy, diff whitespace, and fast readiness pass locally; PR CI/main CI: pending.
- PR order: 1; scope: add `world_class_readiness.status = "not_claimed"` plus `agent_target_selection_metrics` to `tg dogfood` reports so a PASS cannot be mistaken for full rg replacement, full ast-grep replacement, public GPU promotion, production LSP proof, or enterprise target-selection accuracy; Exa anchors: ripgrep JSON/config-override docs, ast-grep CLI docs, Cursor/Sourcegraph agentic context docs, and NVIDIA CUDA profiling/transfer guidance; thinktank/planning consensus: Gemini plan-mode read-only review rejected a separate `next_pr_slices` planning array as source-of-truth duplication and recommended adding the missing target-selection surface to the existing limitations contract; subagent ownership: not applicable, no Codex subagents requested for this turn; Gemini review: completed for planning, final diff-review retry unavailable because `gemini-3.1-pro-preview` returned an invalid empty stream and `gemini-2.5-flash` stalled after startup; validation: targeted dogfood/docs tests, full Python/Rust suites, lint, format, mypy, diff whitespace, and fast readiness pass locally; PR CI/main CI: pending.
- PR order: 2; scope: make GPU promotion workload-scoped in benchmark artifacts and public dogfood/docs, including `promotion_scope = "declared_workload_class_only"`, fair many-pattern baseline `rg -F -e ... -e ...`, and candidate classes for `many_fixed_patterns_single_dispatch` / `resident_repeated_query`; Exa anchors: CUDA-grep final/checkpoint reports on transfer amortization and many-regex workloads, NVIDIA CUDA Graphs and pinned-memory async transfer docs, and ripgrep `-F`/`-e` multiple-pattern docs; thinktank/planning consensus: read-only GPU proof and release-governance seats both recommended an artifact/schema hardening PR rather than CUDA kernel work; subagent ownership: Jason reviewed GPU performance/proof, Lovelace reviewed release/governance; Gemini review: unavailable because `gemini-3.1-pro-preview` returned an invalid empty stream and `gemini-2.5-flash` stalled after startup; validation: targeted GPU benchmark contract, dogfood, public docs, benchmark-script, and readiness tests; `uv run ruff check .`; `uv run ruff format --check --preview .`; `uv run mypy src/tensor_grep`; `cargo fmt --manifest-path rust_core/Cargo.toml --check`; `cargo test --manifest-path rust_core/Cargo.toml`; `uv run pytest -q` (`2248 passed, 16 skipped`); `uv run python scripts/agent_readiness.py --no-shell-probes --no-wsl-probe --json` (`12 passed, 0 failed`); and `git diff --check` pass locally; PR CI/main CI: pending.
- PR order: 3; scope: add public managed GPU proof plumbing with `tg-native-metadata.json`, Python upgrade/install script metadata writers, `--public-managed-proof`, and artifact fields `public_managed_promotion_ready` / `public_gpu_proof`; Exa anchors: NVIDIA Blackwell compatibility guidance, cudarc 0.19 CUDA 13/dynamic-loading docs, and GitHub Actions GPU runner docs; thinktank/planning consensus: Gemini plan-mode review rejected path-shape-only proof and recommended explicit managed front-door provenance; subagent ownership: attempted read-only Codex explorer, but the agent thread limit was reached, so implementation stayed local; Gemini review: planning review completed with file-read limitation, final diff review not run yet; validation: targeted runtime/installer/GPU benchmark/docs tests (`91 passed`), `uv run pytest -q` (`2261 passed, 16 skipped`), `uv run ruff check .`, `uv run ruff format --check --preview .`, `uv run mypy src/tensor_grep`, and `git diff --check` pass locally; PR CI/main CI: pending.
- PR order: 4; scope: add a dispatch-only public managed GPU proof workflow and strengthen the native GPU proof gate so public promotion requires fixed GPU runner labels, managed NVIDIA asset verification, direct `rg --json` 1GB/5GB correctness, `NativeGpuBackend`, `sidecar_used = false`, and speed wins over both `rg` and `tg_cpu`; Exa anchors: GitHub Actions self-hosted/GPU runner docs, NVIDIA Blackwell/CUDA compatibility docs, CUDA compute-capability docs, and ripgrep JSON output semantics; thinktank/planning consensus: Mill/Mencius/Descartes agreed to separate public proof workflow/governance from local CUDA implementation evidence and to reject weak `promotion_ready` summaries; subagent ownership: Mill reviewed workflow scope, Mencius reviewed release/security workflow requirements, Descartes reviewed benchmark proof semantics; Gemini review: unavailable; `gemini-3.1-pro-preview` stalled after startup with no report and was stopped; validation: targeted GPU benchmark contract, benchmark-script, release-workflow validator, and release asset validator tests pass locally; PR CI/main CI: pending.
- PR order: 1; scope: close the `v1.12.33` rg column-override edge by accepting and forwarding `--column --no-column` through both `tg search --format rg ...` and root-level `tg --format rg ...`, add installed-native sweep coverage, improve stale repo-local `uv run tg` warmup diagnostics, and pin the `ripgrep binary resolution` capsule hardcase; Exa anchors: ripgrep inverse/config-override docs where last flag wins, ripgrep JSON/output docs for preserving rg-vs-tg schema boundaries, Sourcegraph/Cody context docs for agent target-selection evidence, LSP initialize-timeout evidence for keeping LSP experimental, and CUDA-grep transfer-amortization notes for keeping GPU unpromoted; thinktank/planning consensus: two read-only seats recommended this narrow contract/readiness/capsule regression slice and explicitly rejected raw-speed, GPU, LSP, or ast-grep claim changes; subagent ownership: thinktank seats Lagrange and Hegel reviewed the plan, implementation stayed local due tight parser/readiness coupling; Gemini review: attempted with gemini CLI 0.42.0 / gemini-2.5-flash in read-only plan mode; unavailable because the model returned an invalid empty stream / malformed tool call; validation: targeted rg contract/parity tests, readiness stale-entrypoint and flag-sweep tests, agent hardcase test, Rust parser unit test, Rust public-native parity test, full Rust crate tests, full pytest, lint, format, mypy, fast readiness, and diff whitespace passed locally; PR CI/main CI: PR #163 passed, squash merge produced `c0cb613`, main CI run `26094452260` passed semantic-release, GitHub release assets, PyPI publish, and `publish-success-gate`; release/public proof: `v1.12.34` tag/release assets exist and `uvx --refresh-package tensor-grep --from tensor-grep==1.12.34 tg --version` reports `tensor-grep 1.12.34`.

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
- Public launcher dogfood must exercise at least one sidecar-backed command (`tg doctor --json` or `tg upgrade`) in addition to `tg --version`; a copied `tg.com` bridge can pass version probes while still falling through to the wrong ambient Python for sidecar commands.
- GPU is not production-ready from device detection alone. Single-pattern public dogfood still loses badly or routes through sidecar; many-pattern CUDA-native wins are workload-specific and must be backed by accepted end-to-end artifacts before claims move into public docs.
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
cmd /c tg doctor --json
pwsh -NoProfile -Command "tg doctor --json"
where.exe tg
uv run tg doctor --json
```

Release dogfood checklist:

```powershell
gh release view <tag>
pip index versions tensor-grep
uvx --refresh-package tensor-grep --from tensor-grep==<tag> tg --version
tg upgrade
cmd /c tg --version
pwsh -NoProfile -Command "tg --version"
tg doctor --json
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
- `tg_agent_capsule` (`gpu_device_ids` / `gpu_timeout_s` are optional native GPU evidence knobs; sidecar-routed GPU is unsupported evidence)

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
tg dogfood --output artifacts/dogfood_readiness.json
```

This gate checks public shell version resolution, `public-windows-launcher-quoted-patterns`, installed-public advertised search flag acceptance via `public-search-advertised-flag-sweep`, repo doctor sanity, `context_consistency`, `agent-capsule`, `agent-capsule-mixed-language`, `agent-capsule-hardcases`, deterministic rg edge parity, broad generated-root scan guardrails, AST smoke, MCP context-render smoke, docs claim hygiene, current `v1.12.40` positioning, foreign launcher diagnostics, and the managed native-upgrade contract. `tg dogfood` wraps the same gate with a compact verdict and JSON report. It does not replace the full validation gate.

For hot-path or benchmark-relevant changes, run the matching benchmark before updating claims:

```powershell
python benchmarks/run_benchmarks.py --output artifacts/bench_run_benchmarks.json
python benchmarks/check_regression.py --baseline auto --current artifacts/bench_run_benchmarks.json
python benchmarks/run_hot_query_benchmarks.py --output artifacts/bench_hot_query_benchmarks.json
python benchmarks/run_ast_benchmarks.py --output artifacts/bench_run_ast_benchmarks.json
python benchmarks/run_ast_workflow_benchmarks.py --output artifacts/bench_run_ast_workflow_benchmarks.json
python benchmarks/run_agent_success_harness.py --output artifacts/bench_agent_success_harness.json
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
| Adding a feature/tool without public-surface updates | Update README, docs/contracts, root and command help, native help, MCP docs/signatures when relevant, and this skill when operating practice changes. |

## Exit Codes

| Code | Meaning |
| --- | --- |
| 0 | Matches found or command succeeded |
| 1 | No matches found |
| 2 | Error occurred |
