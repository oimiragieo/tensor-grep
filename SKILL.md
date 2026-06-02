---
name: tensor-grep
description: Use when searching code, logs, or repositories with tensor-grep; validating rg or AST parity; using tg MCP tools; checking GPU/search routing; or producing agent-friendly context, source, refs, or blast-radius output.
---

# tensor-grep (tg)

## Current State

release_docs_current_tag: v1.13.31

As of 2026-05-26, the current tagged version is `v1.13.31`, and the latest complete public PyPI/release-asset distribution is also `v1.13.31`. Stable installer, PyPI metadata refresh, release-native asset publication, managed-native front-door refresh after `tg upgrade`, stale tensor-grep-owned `tg.com` bridge refresh after upgrade, native-front-door CLI parity for advertised public flags, Windows `.cmd` quoted-pattern launcher handling, native-first Windows PATH ordering, top-level validation-command JSON, local default `classify`, classify provider provenance, fixed multi-pattern native CPU search, GPU scale benchmark correctness gates, launcher-route observability, benchmark launcher attribution, scoped GPU device probing, benchmark launcher warnings, the opt-in `tg agent` Actionable Context Capsule, mixed-language capsule confidence/validation alignment, GPU benchmark recommendation hygiene, edit JSON/rollback safety, capsule validation-trust fixes, explicit language/file-name ranking, quoted Windows validation commands, docs governance, `$file` / `{file}` validation placeholder substitution, native CUDA correctness gates, ambiguous capsule alternatives, root help-menu diagnostics, foreign launcher diagnostics, benchmark promotion-gate taxonomy, agent workflow benchmark governance, capsule alternative-confidence capping, generic provider-token `secrets-basic` regex rules, release-docs synchronization, release wheel Cargo prefetch retries, native GPU/search accuracy hardening, explicit Windows Python subprocess launcher repair, agent capsule hardcase routing, Windows subprocess bridge ranking hardening, broad multi-project workspace-root scan guardrails, `tg doctor` Windows shell escaping diagnostics, and long-lived agent-loop memory/cache caps are in the public `v1.13.31` GitHub asset and PyPI release line.

Current release facts:

- Current release tag: `v1.13.31`.
- GitHub release: <https://github.com/oimiragieo/tensor-grep/releases/tag/v1.13.31>.
- PyPI/public install proof: `uvx --refresh-package tensor-grep --from tensor-grep==1.13.31 tg --version` reports `tensor-grep 1.13.31`.
- Latest verified release proof: PR #236 `fix: repair owned python launchers`; merge commit `3c0c213 fix: repair owned python launchers`; release commit `bd7035c chore(release): v1.13.23 [skip ci]`; main CI run `26513809791`; main dynamic/CodeQL run `26513808787`.
- Root-forwarding slice proof: PR #185 `c631a1a fix: preserve root search flag forwarding (#185)` merged and released as `v1.12.50`; release commit `a89e049 chore(release): v1.12.50 [skip ci]`; main CI run `26260569216` passed; CodeQL run `26261222395` passed; Dependency Graph run `26261223749` passed; Security Audit run `26261585797` passed.

Recent release history:

- PR #236 `3c0c213 fix: repair owned python launchers` merged and released as `v1.13.23`; release commit `bd7035c chore(release): v1.13.23 [skip ci]`; main CI run `26513809791` passed semantic-release, native asset publication, PyPI publication, and `publish-success-gate`; main dynamic/CodeQL run `26513808787` passed; public `uvx --refresh-package tensor-grep --from tensor-grep==1.13.23 tg --version` reports `tensor-grep 1.13.23`.
- PR #235 `995b414 fix: harden v1.13.21 dogfood contracts` merged and released as `v1.13.22`; release commit `5a2ad6b chore(release): v1.13.22 [skip ci]`; main CI run `26473492381` passed semantic-release, native asset publication, PyPI publication, and `publish-success-gate`; main dynamic/CodeQL run `26473490540` passed; public `uvx --refresh-package tensor-grep --from tensor-grep==1.13.22 tg --version` reports `tensor-grep 1.13.22`.
- PR #233 `b69bc5b fix: harden upgrade daemon and lsp diagnostics (#233)` merged and released as `v1.13.21`; release commit `1b62da7 chore(release): v1.13.21 [skip ci]`; main CI run `26450640497` passed semantic-release, native asset publication, PyPI publication, and `publish-success-gate`; main dynamic/CodeQL run `26450639894` passed; public `uvx --refresh-package tensor-grep --from tensor-grep==1.13.21 tg --version` reports `tensor-grep 1.13.21`.
- PR #231 `6525853 fix: harden dogfood timeout reporting` merged and released as `v1.13.20`; release commit `c41d475 chore(release): v1.13.20 [skip ci]`; main CI run `26437847778` passed semantic-release, native asset publication, PyPI publication, and `publish-success-gate`; main dynamic/CodeQL run `26437847528` passed; public `uvx --refresh-package tensor-grep --from tensor-grep==1.13.20 tg --version` reports `tensor-grep 1.13.20`.
- PR #230 `0c9155f fix: harden daemon response cache writes` merged and released as `v1.13.19`; release commit `b9197a6 chore(release): v1.13.19 [skip ci]`; main CI run `26431129535` passed semantic-release, native asset publication, PyPI publication, and `publish-success-gate`; main dynamic/CodeQL run `26431129155` passed; public `uvx --refresh-package tensor-grep --from tensor-grep==1.13.19 tg --version` reports `tensor-grep 1.13.19`.
- PR #229 `77a73b2 fix: harden v1.13.17 dogfood followups (#229)` merged and released as `v1.13.18`; release commit `4a0dad0 chore(release): v1.13.18 [skip ci]`; main CI run `26425383595` passed semantic-release, native asset publication, PyPI publication, and `publish-success-gate`; main dynamic/CodeQL run `26425914836` passed; public `uvx --refresh-package tensor-grep --from tensor-grep==1.13.18 tg --version` reports `tensor-grep 1.13.18`.
- PR #228 `b0e5c27 fix: harden v1.13.16 dogfood followups (#228)` merged and released as `v1.13.17`; release commit `101b8a2 chore(release): v1.13.17 [skip ci]`; main CI run `26420296271` passed semantic-release, native asset publication, PyPI publication, and `publish-success-gate`; main dynamic/CodeQL run `26420295981` passed; public `uvx --refresh-package tensor-grep --from tensor-grep==1.13.17 tg --version` reports `tensor-grep 1.13.17`.
- PR #225 `b0c7cf6 fix: harden v1.13.14 dogfood contracts` merged and released as `v1.13.15`; release commit `d209528 chore(release): v1.13.15 [skip ci]`; main CI run `26386327552` passed semantic-release, native asset publication, PyPI publication, and `publish-success-gate`; main dynamic/CodeQL run `26386327168`, release-commit dynamic run `26386976717`, and Dependency Graph run `26386978124` passed; public `uvx --refresh-package tensor-grep --from tensor-grep==1.13.15 tg --version` reports `tensor-grep 1.13.15`.
- PR #223 `1e09e59 fix: bound agent-loop memory and dogfood contracts` merged and released as `v1.13.14`; release commit `83be919 chore(release): v1.13.14 [skip ci]`; main CI run `26378619592` passed semantic-release, native asset publication, PyPI publication, and `publish-success-gate`; CodeQL run `26379114812` and Dependency Graph run `26379116504` passed on the release commit; public `uvx --refresh-package tensor-grep --from tensor-grep==1.13.14 tg --version` reports `tensor-grep 1.13.14`.
- PR #184 `0e3d666 fix: bound session warm edit-plan work (#184)` merged and released as `v1.12.49`; Release commit: `4e5d08c chore(release): v1.12.49 [skip ci]`; Main CI run `26257991226` passed; CodeQL run `26258780866` passed; Dependency Graph run `26258783151` passed.
- PR #183 `c0c7955 fix: keep session requests on warm path (#183)` merged and released as `v1.12.48`; Release commit: `1becb07 chore(release): v1.12.48 [skip ci]`; Main CI run `26251294335` passed; CodeQL/push run `26252289849` passed.
- PR #182 `eea05c6 fix: restore dogfood docs claim wording (#182)` merged and released as `v1.12.47`; Release commit: `9c538ba chore(release): v1.12.47 [skip ci]`; Main CI run `26236451411` passed; CodeQL/push run `26236447550` passed.
- PR #181 `524f6d4 fix: expose windows shell escaping diagnostics` merged and released as `v1.12.46`; release commit `6fb1c0d chore(release): v1.12.46 [skip ci]`; main CI run `26213038896` passed; CodeQL run `26213037961` passed.
- PR #180 `e15e99d fix: guard broad workspace root searches` merged and released as `v1.12.45`; release commit `a2312c7 chore(release): v1.12.45 [skip ci]`.
- Latest merged feature commit: `a518cc6 feat: add agent success harness`.
- Historical v1.12.34 proof retained for docs-governance context: `c0cb613 fix: harden v1.12.33 dogfood contracts`; `e069f67 chore(release): v1.12.34 [skip ci]`; Main CI run `26094452260`; CodeQL proof run `26064676072`; prior CodeQL run `25951813292`.
- PR #143 `21e5437 fix: collect capsule call-site evidence` merged and released as `v1.12.14`.
- PR #142 `8a73f8d fix: harden agent bridge ranking` merged and released as `v1.12.13`.
- PR #141 `b601366 fix: harden agent output budget hygiene` merged and released as `v1.12.12`.
- PR #140 `2aebac6 fix: harden ast cli contract hygiene (#140)` merged and released as `v1.12.11`.
- PR #139 `bbc08e4 fix: harden rg flag contract aliases (#139)` merged and released as `v1.12.10`.
- PR #138 `21627d2 fix: harden v1.12.8 dogfood contracts` merged and released as `v1.12.9`.
- PR #137 `f848748 fix: route cold rg-shaped searches to rg (#137)` merged and released as `v1.12.8`.
- PR #128 `da44a2f fix: harden v1.12.6 dogfood cli contracts` merged and released as `v1.12.7`.
- PR #116 `a78e33c fix: harden post-release docs governance` merged and released as `v1.11.5`.
- PR #114 `361e0db fix: harden public GPU unavailable routing` and PR #115 `2100122 fix: harden release docs stamp governance` merged and released as `v1.11.4`.
- PR #113 `87d4ca4 fix: accelerate fixed multi-pattern native search` merged and released as `v1.11.3`.
- PR #110 `fix: expose classify provider provenance` merged and released as `v1.11.2`.
- PR #105 `fix: add explicit Windows subprocess launcher repair` merged and released as `v1.10.10`; merge commit `dd995fc fix: add explicit Windows subprocess launcher repair`.
- PR #101 `fix: harden gpu search accuracy contracts` merged and released as `v1.10.7`.
- PR #100 `fix: harden v1.10.5 dogfood blockers` merged and released as `v1.10.6`.
- PR #91 `fix: harden release wheel retries` merged and released as `v1.9.11`.
- PR #90 `fix: harden v1.9.9 dogfood followups` merged and released as `v1.9.10` on GitHub assets; PyPI publication was completed by the v1.9.11 release-wheel retry follow-up.
- PR #89 `fix: add agent workflow benchmark governance` merged and released as `v1.9.9`.
- PR #87 `fix: refresh stale tg.com bridge after upgrade` merged and released as `v1.9.8`.
- PR #86 `fix: clarify GPU benchmark promotion gates` merged and released as `v1.9.7`.
- PR #84 `fix: harden v1.9.5 dogfood blockers` merged and released as `v1.9.6`.
- PR #83 `fix: harden GPU gates and launcher diagnostics` merged and released.
- PR #82 `fix: harden docs governance and validation placeholders` merged and released.
- PR #81 `fix: harden agent ranking docs and validation quoting` merged and released.
- PR #80 `fix: harden edit JSON and capsule validation trust` merged and released.
- PR #78 `fix: harden agent capsule trust alignment` merged and released.
- PR #76 `feat: add actionable agent context capsule` merged and released.
- Previous GPU/benchmark warning fix commit: `e2bd7c2 fix: scope GPU probing and benchmark launcher warnings`.
- PR #74 `fix: scope GPU probing and benchmark launcher warnings` merged and released as `v1.8.33`.
- Previous launcher observability fix commit: `ab2635a fix: expose launcher route observability`.
- Previous agent-contract fix commit: `015fad9 fix: harden public launcher and agent contracts`.
- Previous launcher fix commit: `e6d09a5 fix: preserve quoted patterns in Windows cmd shim`.
- Latest merged docs/product commit: `f311469 docs: define agent context capsule roadmap`.
- PR #66 `docs: define agent context capsule roadmap` merged; Main CI run `25561521904` passed, CodeQL/dynamic main run `25561520180` passed, and semantic-release correctly skipped publishing.
- `v1.11.0` main CI run `25834508800` passed the pre-release matrix and semantic-release, but release-native asset publication was cancelled; `publish-success-gate` failed and PyPI latest remained `1.10.10` until later releases.
- Latest handoff: `docs/SESSION_HANDOFF.md`.

Current product read:

- `rg` remains the benchmark for raw cold exact-text search.
- `ast-grep` remains the structural-search feature/performance baseline; `tg run` is a validated useful slice, not full ast-grep equivalence.
- `tg` is strongest as agent-native code intelligence: scoped search, JSON/NDJSON, repo maps, defs, source, refs, callers, context bundles, blast-radius, AST search, rewrite planning, GPU inventory, and MCP.
- The native front door must accept advertised public flags or intentionally route them to the sidecar. The current release line covers `tg search --files`, `tg search --multiline` / `-U`, `tg search --null`, `tg run -r`, `tg classify --format json`, advertised rg-style search flags, option-first root `tg ...` forwarding, Windows `.cmd` quoted multi-word no-match patterns, native-first Windows PATH ordering for fresh managed shells, and launcher-route observability for current-process versus fresh-shell PATH drift. Root shortcut syntax should preserve common search flags: use `tg PATTERN PATH`, `tg -t js PATTERN PATH`, or `tg --count-matches PATTERN PATH` when you want the root entrypoint to behave as `tg search ...`.
- The current release line keeps Python/dev and native/public search flag surfaces aligned for accepted rg-compatibility aliases such as `--passthrough`, `--unicode`, `--auto-hybrid-regex`, and `tg search --version`; top-level structured search flags such as `tg --json --no-ignore PATTERN PATH` must parse through the native front door; `tg new` must either create the requested scaffold under the requested base directory or fail before writing files; `edit-plan`, MCP `tg_edit_plan`, and session edit-plan should accept `max_sources` / `max_tokens` for agent command-surface parity while still emitting no rendered source text.
- The quoted multi-word no-match pattern case from `cmd.exe`, direct `tg.cmd`, and Python `subprocess.run([...])` is a public Windows launcher contract. A split pattern can become a shorter false-positive search plus bogus paths, so keep `public-windows-launcher-quoted-patterns` in the fast agent-readiness gate.
- Stable managed installs should prefer the matching release-native CPU front door when the GitHub release asset exists, while keeping the isolated Python environment as sidecar/fallback via `TG_SIDECAR_PYTHON` and `TG_NATIVE_TG_BINARY`. Installer changes should preserve the staged replacement contract so a failed install cannot break an existing public shim, including checking native installer command exit codes before the staged swap. On Windows, the managed native front-door directory should be ahead of compatibility `.cmd` shim directories on PATH so `cmd`, unprofiled PowerShell, and Python subprocess calls resolve `~/.tensor-grep/bin/tg.exe` before the slower argv-safe bridge. If a copied tensor-grep `tg.com` bridge is used to outrank a foreign same-directory `tg.exe`, it must still discover `~/.tensor-grep/.venv` for sidecar commands and point `TG_NATIVE_TG_BINARY` back to the managed native front door. Python subprocess resolution must be checked directly because `CreateProcess` can choose a foreign `.exe` route that shell `PATHEXT` hides. `tg upgrade` must verify the sidecar import/version before claiming success, including the scheduled Windows self-upgrade path, and managed native front doors must be refreshed when the verified sidecar version moves ahead of `tg.exe`.
- `tg doctor --json` is the first check for launcher drift and Windows shell pitfalls. Inspect `path_tg_first_launcher_kind`, `fresh_shell_path_tg_first_launcher_kind`, `python_subprocess_path_tg_first_launcher_kind`, `path_tg_launcher_warning`, `shell_escaping_guidance`, and any `*_is_foreign` / `*_foreign_remediation` fields before trusting Windows benchmark timings; existing shells can retain the slower compatibility shim even after fresh User PATH resolves the native front door, Python subprocesses can resolve differently from shells, PowerShell double quotes expand `$NAME`, and unrelated tools can own a different `tg` command.
- Cold-path benchmark artifacts should separate configured launcher mode from actual timed command kind. Use `environment.tg_launcher_mode` for the experiment and `environment.tg_launcher_command_kind` to distinguish native-exe, `.cmd` shim, `uv`, and Python-module routes. Also preserve `tg_binary_version_status`; stale in-tree native binaries block claim-quality benchmark scripts by default unless `--allow-claim-unsafe-launcher` marks the run as exploratory. Treat benchmark warnings about shim/interpreter overhead as blocking for performance comparisons.
- Explicit `--gpu-device-ids` routing should only probe selected CUDA ordinals. Selecting GPU 0 must not initialize or warn about unrelated unsupported devices such as GPU 1.
- GPU benchmark auto-recommendation must stay false unless required 1GB/5GB correctness checks pass and a selected GPU beats both `rg` and `tg_cpu` at required scale. Unsupported-device inventory warnings should stay top-level or on the unsupported device row, not on unrelated selected-GPU timing rows. GPU-requested CPU fallback or sidecar compatibility output must report `gpu_evidence_status = unsupported`, `gpu_proof = false`, `native_gpu_unavailable`, and `not_gpu_proof_reason`; unsupported rows should carry `promotion_evidence = false`.
- `--format rg --sort path` is the deterministic rg-shaped stdout contract. Token-saving output work should be a separate opt-in agent profile, not a mutation of raw rg/json/ndjson contracts.
- `tg search --json` is tensor-grep aggregate JSON, not rg JSON Lines. `tg search --format rg --json` is the explicit rg JSON Lines compatibility route and emits raw rg events without the tensor-grep envelope. `tg search --ndjson` is tensor-grep's flattened streaming row schema, not the rg event schema. Use `--format rg` for rg-shaped output and keep schema claims explicit.
- `tg agent` / Actionable Context Capsule is the product wedge: an opt-in workflow packet with primary file/function, route rationale, bounded snippets with line maps, validation evidence, edit order, checkpoint/rollback metadata, omission counts, confidence, call-site evidence status, and an "ask user before editing" recommendation when evidence is weak. Capsule v1 leaves `related_call_sites` empty unless verified call-site evidence is explicitly collected. Evidence labels should distinguish `parser-backed`, `rg-backed`, `graph-derived`, `heuristic`, `LSP-confirmed`, and `stale/uncertain` conclusions.
- LSP provider-backed navigation is optional and experimental. Provider availability is not navigation proof. `tg lsp-setup` / `tg doctor --with-lsp` provider availability means the binary is installed, not that semantic requests work; inspect `health_status`, `health_check`, `lsp_proof`, `lsp_evidence_status`, and `not_lsp_proof_reason` before treating an `lsp` / `hybrid` result as provider-confirmed. Rows require `lsp_provider_response = true` from a completed provider request before they can contribute to `lsp_proof`.
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
- Broad generated roots need bounds. Unbounded `tg search --files --hidden` scans through generated/cache/dependency child directories are refused unless bounded with `--glob`, `--type`, or `--max-depth`, or explicitly opted in with `--allow-broad-generated-scan`. Explicit `--no-ignore` content searches over an ordinary project root follow ripgrep and may traverse ignored generated children; direct generated roots such as `.venv` still require scoping or `--allow-broad-generated-scan`. Use scoped paths, globs, file types, and `--max-depth` for `tg search` before reaching for opt-in. `--max-repo-files`, `--max-callers`, and `--max-files` are code-intelligence command budgets, not `tg search` flags.

Dogfood follow-up workflow:

- Split dogfood feedback into PR-sized slices with one behavioral theme per branch; do not collapse independent fixes into one broad PR.
- Use Exa research before coding when the slice depends on current external behavior such as `rg -F -e`, `ast-grep`, CUDA/Blackwell support, GitHub Actions, release packaging, or agent-evaluation harnesses.
- Run a thinktank or equivalent independent planning review for benchmark interpretation, GPU promotion policy, product positioning, and release workflow changes.
- Ask Gemini for a bounded read-only diff review before each PR merge, then verify any finding locally before changing code.
- For every slice: start with the contract test, implement the smallest fix, run the targeted suite, run lint and format, push the PR, wait for PR CI, squash-merge, then watch main CI.
- Maintain a per-slice evidence ledger for dogfood follow-up work. Each slice entry must record PR order, slice scope, Exa research anchors, thinktank or planning consensus, subagent ownership, Gemini review result, validation commands, PR CI, and main CI. Optional or triggered items may be marked `not applicable` only with a rationale.
- For release-bearing slices, final status also requires semantic-release, release assets, PyPI/package publication, and public release dogfood evidence.

Current post-`v1.13.31` dogfood slice ledger:

- PR order: 13; scope: close the `v1.13.20` dogfood daemon-upgrade and LSP-diagnostic follow-up by snapshotting pre-upgrade session daemon state, restarting the daemon after direct or scheduled Windows upgrade handoff loss, stripping inherited Python runtime variables from managed LSP provider launch environments, and suppressing stale Pyright SRE mismatch stderr tails once a current provider request proves healthy while preserving failed-proof stderr; Exa anchors: CPython/uv SRE mismatch reports connecting the error to mismatched Python runtime/stdlib environment; thinktank/planning consensus: read-only subagent reviews required using the pre-upgrade daemon root and preserving failed-proof stderr; subagent ownership: Popper and Copernicus read-only plan review, implementation local; Claude Opus review: PASS with low findings, addressed by preserving non-SRE suppressed stderr as `provider_recent_stderr` and carrying daemon restart roots into the scheduled Windows helper; validation: targeted upgrade/LSP tests, focused LSP suites, ruff, preview format, mypy, and diff whitespace passed locally; PR CI: PR #233 passed; main CI: semantic-release published `v1.13.21` at `1b62da7`, main CI run `26450640497` passed, CodeQL/dynamic run `26450639894` passed, and public `uvx --refresh-package tensor-grep --from tensor-grep==1.13.21 tg --version` proof passed.
- PR order: 12; scope: harden the `v1.13.19` built-in dogfood timeout gap by giving `tg dogfood` a wrapper timeout, passing an incremental child `--output` to `scripts/agent_readiness.py`, preserving partial running reports, and cleaning up the launched child process tree by PID only; Exa anchors: Python subprocess timeout semantics and psutil process-tree termination guidance; thinktank/planning consensus: not applicable because this is an internal harness lifecycle fix, with Zeno read-only subagent review confirming the timeout and descendant-cleanup root cause; subagent ownership: Zeno read-only call-path review, implementation local; Claude Opus review: no blocker/high findings (`OPUS_REVIEW: PASS`); validation: targeted dogfood/readiness/docs tests, ruff, preview format, mypy, and diff whitespace passed locally; PR CI/main CI: PR #231 passed, squash merge produced `6525853`, semantic-release published `v1.13.20` at `c41d475`, main CI run `26437847778` passed, CodeQL/dynamic run `26437847528` passed, and public `uvx --refresh-package tensor-grep --from tensor-grep==1.13.20 tg --version` proof passed.
- PR order: 11; scope: harden the `v1.13.18` daemon-cache dogfood gap by letting capped or truncated implicit session snapshots bypass added-file stale detection for daemon-routed top-level `context-render` / `edit-plan` cache writes while preserving explicit added-file refresh for complete sessions; Exa anchors: not applicable because this is internal daemon/session cache behavior; thinktank/planning consensus: systematic-debugging trace plus read-only subagent review isolated the stale-detection failure before `response_cache.put()` and required an added-file refresh regression test; subagent ownership: Wegener read-only plan/diff review, implementation local; Claude Opus review: no blocking findings, optional capped-modification stale-refresh test added; validation: targeted docs/session tests pass (`47 passed`), `uv run --no-sync ruff check .`, `uv run --no-sync ruff format --check --preview . --exclude .tmp --exclude .tensor-grep --exclude src/.tensor-grep`, `uv run --no-sync mypy src/tensor_grep`, and `git diff --check` pass locally; full pytest/Rust matrices and benchmark suites intentionally deferred to PR/main CI unless the user approves heavy desktop validation; PR CI/main CI: PR #230 passed, squash merge produced `0c9155f`, semantic-release published `v1.13.19` at `b9197a6`, main CI run `26431129535` passed, CodeQL/dynamic run `26431129155` passed, and public `uvx --refresh-package tensor-grep --from tensor-grep==1.13.19 tg --version` proof passed.
- PR order: 10; scope: harden `v1.13.17` dogfood regressions by making non-JSON rg-shaped explicit no-ignore content searches prefer ripgrep passthrough when `rg` is available while preserving the native fallback when it is not, preserving tensor-grep aggregate JSON semantics, resolving top-level `context-render` / `edit-plan` daemon requests to absolute directory roots so repeated relative invocations can populate and hit the daemon response cache, and documenting desktop memory-safety operating rules for local validation; Exa anchors: official ripgrep guide/manpage behavior for `--no-ignore` and `-u` disabling ignore filtering; thinktank/planning consensus: read-only subagent review agreed the no-ignore fast path should stay in the rg-shaped non-JSON lane and the daemon cache fix should normalize request paths at the top-level caller boundary; subagent ownership: McClintock read-only plan/diff review, implementation local; Claude Opus review: accepted findings for direct JSON/NDJSON passthrough tests, no-ignore-vcs coverage, guarded daemon path normalization, daemon-start assertions, and absolute cleanup; validation: targeted daemon path/cache tests, targeted Rust routing test, ruff, preview format check, cargo fmt check, and diff whitespace check passed locally; full pytest/Rust matrices and benchmark suites intentionally deferred to PR/main CI unless the user approves heavy desktop validation; PR CI/main CI: PR #229 passed, squash merge produced `77a73b2`, semantic-release published `v1.13.18` at `4a0dad0`, main CI run `26425383595` passed, CodeQL/dynamic run `26425914836` passed, and public `uvx --refresh-package tensor-grep --from tensor-grep==1.13.18 tg --version` proof passed.
- PR order: 8; scope: bound long-lived agent-loop memory surfaces and close `v1.13.13` dogfood follow-ups by adding LRU caps for CPU/StringZilla/AST/repo-context caches, byte caps for session/daemon response caches, LSP workspace-client/open-document eviction with `didClose`/client stop cleanup, checkpoint discovery artifact visibility, clearer path-first `defs --symbol` deprecation help, and capsule LSP tie proof preservation; Exa anchors: Python `functools.lru_cache` retention semantics, LSP `didOpen`/`didClose`, psutil memory metric caveats, and ripgrep benchmark/sort behavior; thinktank/planning consensus: byte-account session caches, stop evicted LSP clients, preserve path-first positional command consistency, keep GPU claims off, and verify with CI/public install; Claude Opus review: findings accepted for daemon/store fingerprint parity, LSP cleanup, oversized cache preservation, checkpoint truncation metadata, JSON retry, and stale CPU cache return; validation: targeted tests, full pytest `2486 passed, 16 skipped`, ruff, preview format check, mypy, Rust cargo fmt/clippy/tests, release asset validators, no-shell agent readiness, PR CI, main CI, CodeQL, Dependency Graph, GitHub release assets, PyPI, and public `uvx` install proof passed; PR CI/main CI: PR #223 passed, squash merge produced `1e09e59`, semantic-release published `v1.13.14` at `83be919`.
- PR order: 7; scope: close concrete `v1.13.11` dogfood regressions by deduplicating `defs --provider hybrid` native/LSP definition rows while preserving LSP proof, bounding checkpoint discovery cache priming at the user-home boundary so Windows standalone `checkpoint create` does not write `C:\Users\.tensor-grep`, separating MCP protocol/CLI version fields in capabilities, sharpening the PowerShell `Start-Process`/`tg.ps1` MCP stdio warning, suppressing stale LSP stderr tails once a provider request proves healthy, routing `tg audit --help` to audit help instead of search, and broadening `secrets-basic` fake API key detection; Exa anchors: official MCP lifecycle/version negotiation docs and LSP 3.17 `Location`/range semantics for merge identity; thinktank/planning consensus: compressed read-only review through subagents because the separate thinktank spawn hit the agent thread limit; Aquinas recommended explicit MCP protocol versus CLI fields, Cicero recommended post-merge LSP/native dedupe with LSP proof preservation and quiet successful provider status, and Ohm recommended home-bounded checkpoint discovery plus explicit native-`tg.exe` MCP stdio warning; subagent ownership: Aquinas (MCP), Cicero (hybrid/LSP), Ohm (checkpoint/doctor/audit); Gemini review: unavailable because `gemini-3-flash-preview --approval-mode plan` stalled after startup/tool noise and was killed without a report; validation: targeted checkpoint, semantic-provider, LSP-provider, trust/audit, MCP, doctor, scan, docs, and integration tests pass locally; `uv run pytest -q` passes (`2451 passed, 16 skipped`); `uv run ruff check .`; `uv run ruff format --check --preview .`; `uv run mypy src/tensor_grep`; full Rust crate tests; cargo fmt check; `uv run python scripts/agent_readiness.py --no-shell-probes --no-wsl-probe --json` passes (`13 passed, 0 failed`); direct Windows checkpoint-create smoke, direct agent-studio hybrid-defs smoke, audit-help smoke, MCP-capabilities smoke, public-command contract smoke, and `git diff --check` pass locally; PR CI/main CI: PR #222 passed, squash merge produced `323e83a`, semantic-release published `v1.13.13` at `581c412`, and public install proof passed.
- PR order: 1; scope: guard broad multi-project workspace-root searches while preserving scoped `tg search --files` workflows; Exa anchors: not applicable beyond existing generated-root guardrail policy; thinktank/planning consensus: local planning review; subagent ownership: not applicable; Gemini review: unavailable; validation: targeted guardrail/docs tests plus local gates; PR CI/main CI: PR #180 passed, squash merge produced `e15e99d`, main CI released `v1.12.45`.
- PR order: 2; scope: expose Windows shell escaping diagnostics in `tg doctor`, `tg --help`, README, contracts, and this skill so PowerShell `$NAME` expansion and `cmd.exe` metacharacter escaping are visible; Exa anchors: Microsoft PowerShell quoting/parsing docs and Microsoft cmd metacharacter docs; thinktank/planning consensus: not applicable for this narrow UX/docs contract; subagent ownership: not applicable; Gemini review: pass with low-risk notes; validation: targeted CLI/docs tests, ruff, format check, mypy, diff whitespace, PR CI, main CI, CodeQL, GitHub release assets, PyPI, and public `uvx` install proof; PR CI/main CI: PR #181 passed, squash merge produced `524f6d4`, main CI released `v1.12.46`.
- PR order: 3; scope: restore the README docs-claim wording for `broad generated-root scan`, sync this skill, and add governance coverage so dogfood catches stale wording/proof drift; Exa anchors: not applicable for this narrow docs-governance wording fix; thinktank/planning consensus: not applicable; subagent ownership: not applicable; Gemini review: pass; validation: docs-governance tests, targeted e2e CLI timeout tests, no-shell agent readiness, no-shell `tg dogfood`, PR CI, main CI, CodeQL/push run, GitHub release assets, PyPI, and public `uvx` install proof; PR CI/main CI: PR #182 passed, squash merge produced `eea05c6`, main CI released `v1.12.47`.
- PR order: 4; scope: keep cached-session requests on the warm path by avoiding full repo added-file scans unless callers explicitly refresh, and make `session list` / `session daemon status` discover nearby scopes; Exa anchors: ripgrep docs confirmed `--count-matches` and `--sort path` behavior for separate CLI parity/perf slices, not this session fix; thinktank/planning consensus: Gemini read-only review agreed stale detection was walking the repo on every cached request and recommended this narrow session slice; subagent ownership: not applicable; Gemini review: PASS; validation: targeted session/docs tests, full pytest, mypy, ruff, format check, diff whitespace, no-shell agent readiness, no-shell `tg dogfood`, and session benchmark artifact passed locally; PR CI/main CI: PR #183 passed, squash merge produced `c0c7955`, main CI released `v1.12.48`.
- PR order: 5; scope: bound cached-session edit-plan graph/test/blast-radius work and split daemon connect timeout from edit-plan response timeout so dogfood-sized session edit-plan requests do not hang or fail fast; Exa anchors: not applicable because this is internal session/cache behavior; thinktank/planning consensus: local systematic-debugging/TDD trace isolated reverse import graph, broad pagerank seeds, unrelated framework-test scans, full-map edit-plan blast radius, and daemon read timeout; subagent ownership: not applicable; Gemini review: PASS on the diff-piped retry after an initial plan-mode run could not inspect the worktree; validation: targeted session/repo-map/framework tests, dogfood-sized local session probes, full pytest `2331 passed, 16 skipped`, ruff, ruff format check, mypy, cargo fmt check via explicit cargo path, git diff whitespace check, root `tg --help`, no-shell/no-WSL `tg dogfood`, PR CI, main CI, CodeQL, Dependency Graph, GitHub release assets, PyPI, and public `uvx` install proof; PR CI/main CI: PR #184 passed, squash merge produced `0e3d666`, main CI released `v1.12.49`.
- PR order: 6; scope: preserve root `tg` shortcut forwarding for option-first common search flags, including `tg -t js PATTERN PATH` and `tg --count-matches PATTERN PATH`, and add dogfood coverage for those shapes; Exa anchors: not applicable because this is internal native-front-door argument routing for already-supported rg-compatible flags; thinktank/planning consensus: local TDD trace isolated the missing native allowlist/parser entries; subagent ownership: not applicable; Gemini review: PASS on retry with `gemini-3-flash-preview` after pro-capacity failure; validation: targeted native routing tests, targeted agent-readiness sweep tests, full Rust crate, full pytest `2331 passed, 16 skipped`, ruff, ruff format check, mypy, cargo fmt check, diff whitespace, no-shell/no-WSL agent readiness, root/native help smoke, and direct native `-t` / `--count-matches` smoke pass locally; PR CI/main CI: PR #185 passed, squash merge produced `c631a1a`, main CI run `26260569216` passed, semantic-release published `v1.12.50` at `a89e049`, CodeQL/Dependency Graph/Security Audit passed, GitHub assets and PyPI/uvx public install proof verified.

Known current weak spots:

- Broad `tg search --files ...` over generated artifact trees and multi-project workspace roots is now guarded when unbounded, but still scope file-list commands to the smallest useful root for latency, disk, and token budget.
- Windows command resolution must be checked across profiled PowerShell, `pwsh -NoProfile`, and `cmd`. Verified tensor-grep-owned `Python*\Scripts\tg.exe` launchers ahead of the managed native front door should be removed by the Windows installer or `tg repair-launcher`; self-identifying orphaned tensor-grep Python Scripts launchers should be backed up by `tg repair-launcher`; any recurrence is release-regression evidence. A `Python*\Scripts\tg.exe` that reports another product's version is a foreign PATH-shadow blocker instead: report/remediate it, but do not delete it automatically.
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
- Cached-session requests should stay warm. Normal `tg session ...` reads validate only snapshot files by size/mtime and should not walk the full repo to discover added files; use `tg session refresh ...` or `--refresh-on-stale` when newly added files must enter the session map. Session edit-plan graph ranking, test matching, and blast-radius metadata must stay bounded to selected context budgets and report `edit_plan_seed.blast_radius_scope`, and daemon edit-plan/context requests use a short connect probe with a longer work response timeout. Top-level native-provider `tg context-render` and `tg edit-plan` reuse an already-running daemon through an implicit session keyed by root and `--max-repo-files` so repeated calls can hit `response_cache_*` without letting a small-budget scan cap later larger requests. `tg session list` and `tg session daemon status` discover nearby session scopes when the current directory has no direct session metadata.
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

On Windows, `tg doctor --json` includes `shell_escaping_guidance`. Use it to catch PowerShell `$NAME` expansion and `cmd.exe` metacharacter escaping issues before blaming `tg` parsing.

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

Avoid broad generated-root or whole-workspace file lists unless the task needs them:

```powershell
# Avoid this unless the whole workspace scan is intentional:
tg search --files C:\dev\projects --hidden --no-ignore
```

Use one of these instead for agent-safe file discovery:

```powershell
tg search --files . --hidden --glob "AGENTS.md"
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
| AST search | `tg run --lang python function_definition src --json` |
| AST language identifiers | `tg ast-info --json` |
| Source lookup | `tg source src someSymbol --json` |
| Refs lookup | `tg refs src someSymbol --json` |
| Blast radius | `tg blast-radius src someSymbol --json` |
| Context bundle | `tg context-render src --query "how routing works" --render-profile llm --json` |
| Device inventory | `tg devices --json` |
| MCP server | `tg mcp` |
| **LSP setup** | `tg lsp-setup [--json]` |
| **LSP server** | `tg lsp --provider native` or `tg lsp --provider hybrid` |
| **Edit Planning** | `tg edit-plan src --query "change invoice tax"` |
| **Interactive Session** | `tg session open [PATH] --json` |
| **Session Daemon** | `tg session daemon start [PATH] --json` |
| **Create Checkpoint (Rewind)** | `tg checkpoint create [PATH] --json` |
| **List Checkpoints** | `tg checkpoint list [PATH] --json` |
| **Rollback / Rewind to checkpoint** | `tg checkpoint undo <checkpoint_id> [PATH] --json` |

AST structural patterns are exact. A pattern such as `'def $NAME($$$ARGS): $$$BODY'`
does not match Python functions with return annotations; use node-kind search such as
`function_definition` or a `tg scan` rule with `kind: function_definition` when the
task is to enumerate all functions regardless of signature shape.

## Advanced Features: LSP, Editing, and Checkpoints (Rewind)

### 1. LSP (Language Server Protocol) Integration
`tensor-grep` contains an optional LSP coordinator for semantic navigation experiments (`defs`, `source`, `refs`, `callers`, `blast-radius`).
- **Setup**: Run `tg lsp-setup [--json]` to install managed LSP providers into `~/.tensor-grep/providers`.
- **Diagnostics**: Run `tg doctor --with-lsp --json` and inspect `health_status`, `health_check`, `lsp_proof`, `lsp_evidence_status`, and `not_lsp_proof_reason`.
- **Server**: Run `tg lsp --provider native`, `tg lsp --provider lsp`, or `tg lsp --provider hybrid`. Provider availability is not navigation proof.

### 2. Machine-Readable Edit-Planning and Sessions
For agentic editing loops, `tg` supports structured edit tracking and map caches.
- **Edit Plan**: `tg edit-plan` constructs a plan of edits across files matching a natural language query, specifying targets and files to touch.
- **Session Open**: `tg session open [PATH] --json` creates a cached repo-map session for repeated edit loops.
- **Session Refresh**: `tg session refresh <session_id> [PATH] --json` refreshes the cached repo map and performs added/removed/modified file discovery.
- **Session Daemon**: `tg session daemon start [PATH] --json` starts or reuses the warm localhost daemon for repeated repo-map and symbol requests. Daemon edit-plan/context requests keep a short connect probe and a longer work response timeout. Top-level native-provider `tg context-render` and `tg edit-plan` reuse an already-running daemon via an implicit session keyed by root and `--max-repo-files`. `tg session list` and `tg session daemon status` discover nearby scopes when the current directory has no direct session metadata.

### 3. Checkpoints & Rollbacks (Rewind)
Before initiating a complex code rewrite, agents should create a checkpoint when rollback evidence matters.
- **Checkpoint Creation**: `tg checkpoint create [PATH] --json` creates a checkpoint scoped to the current editable tree or supplied path.
- **Listing Checkpoints**: `tg checkpoint list [PATH] --json` lists available checkpoints; add `--discover` to recursively discover checkpoint scopes.
- **Undo / Rollback (Rewind)**: `tg checkpoint undo <checkpoint_id> [PATH] --json` restores the selected checkpoint for that scope.


PowerShell expands `$NAME` and `$$$ARGS` inside double quotes. For literal patterns, use single quotes or escape `$`. In `cmd.exe`, quote or caret-escape metacharacters such as `|`, `&`, `<`, `>`, `^`, `(`, and `)`.

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

This gate checks public shell version resolution, `public-windows-launcher-quoted-patterns`, installed-public advertised search flag acceptance via `public-search-advertised-flag-sweep`, repo doctor sanity, `context_consistency`, `agent-capsule`, `agent-capsule-mixed-language`, `agent-capsule-hardcases`, deterministic rg edge parity, broad generated-root scan guardrails, AST smoke, MCP context-render smoke, docs claim hygiene, current `v1.13.31` positioning, foreign launcher diagnostics, and the managed native-upgrade contract. `tg dogfood` wraps the same gate with a compact verdict and JSON report. It does not replace the full validation gate.

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
