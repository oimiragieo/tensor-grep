# tensor-grep Session Handoff

Last updated: 2026-05-14

## Current Release State

release_docs_current_tag: v1.12.6

- Latest tagged version: `v1.12.6`
- Latest complete PyPI version: `v1.12.6`
- Latest tagged release PR: #113 `fix: accelerate fixed multi-pattern native search`
- Latest tagged merge commit: `a78e33c fix: harden post-release docs governance`
- Latest tagged release commit: `e33c2ba chore(release): v1.11.5 [skip ci]`
- Latest complete public release PR: #116 `fix: harden post-release docs governance`
- Latest complete public release commit: `e33c2ba chore(release): v1.11.5 [skip ci]`
- Latest fix commit: `a78e33c fix: harden post-release docs governance`
- Latest feature commit: `213d383 feat: add dogfood readiness verdict and checkpoint UX`
- GitHub release: <https://github.com/oimiragieo/tensor-grep/releases/tag/v1.12.6>
- `v1.11.0` publication caveat: main CI run `25834508800` passed the pre-release matrix and semantic-release, but release-native asset publication was cancelled; `publish-success-gate` failed, `publish-github-release-assets` / `publish-pypi` did not complete, and PyPI latest remains `1.10.10`.
- Main CI run `25860914920`: passed the pre-release matrix, semantic-release, `publish-github-release-assets`, `publish-pypi`, and `publish-success-gate`
- Main CodeQL run `25866868462`: passed on the `v1.11.5` release line
- Main CI run `25866871838`: passed the pre-release matrix, semantic-release, PyPI artifact validation, `publish-github-release-assets`, `publish-pypi`, and `publish-success-gate`
- PyPI pinned install: `uvx --refresh-package tensor-grep --from tensor-grep==1.12.6 tg --version` reports `tensor-grep 1.12.6`
- GitHub release assets: `v1.12.6` has uploaded native CPU front doors for Windows/Linux/macOS, checksums, winget manifest, Homebrew formula, and publish instructions
- Public `v1.11.5` dogfood verified PyPI, `uvx`, release assets, and post-release-safe docs governance. `v1.11.4` verifies the native GPU unavailable fallback to `NativeCpuBackend`, and `v1.11.3` uses `87d4ca4 fix: accelerate fixed multi-pattern native search` to run a single Aho-Corasick pass for safe fixed-string multi-pattern searches while keeping GPU promotion separate from this CPU workload-class win.
- Closed v1.11.5 post-release-safe docs governance gap: `a78e33c fix: harden post-release docs governance` separates auto-stamped current tag labels from latest verified release proof blocks so release commits remain locally testable after semantic-release bumps the version.
- Closed v1.11.4 native GPU unavailable and docs-governance gap: `361e0db fix: harden public GPU unavailable routing` prevents sidecar routing from looking like native GPU proof when no explicit sidecar is configured, and `2100122 fix: harden release docs stamp governance` keeps post-release-safe docs governance aligned with semantic-release tag stamping.
- Public `v1.11.2` dogfood verified PyPI, `uvx`, release assets, and classify provider provenance in JSON output. `v1.11.2` exposes `classification_backend` so harnesses can distinguish local deterministic classification from opt-in provider-backed classification.
- Closed v1.11.3 fixed multi-pattern lane gap: `v1.11.3` routes safe fixed-string multi-pattern native searches through one Aho-Corasick pass, preserves fallback for unsupported semantics, and records the local 100 fixed no-match patterns over 1GB CPU win separately from GPU promotion evidence.
- Closed v1.11.2 classify provenance gap: `v1.11.2` exposes `classification_backend` in JSON output so harnesses can distinguish local deterministic classification from opt-in provider-backed classification.
- Public `v1.10.10` managed-upgrade and launcher dogfood verified the pinned managed installer, direct managed native `tg.exe`, fresh `cmd /c tg --version`, fresh `pwsh -NoProfile -Command "tg --version"`, Python `subprocess.run(["tg", "--version"])`, PyPI, `uvx`, and GitHub assets. `tg repair-launcher --allow-foreign-rename` backed up the foreign Together CLI `tg.exe` before installing the verified managed native front door into that PATH slot.
- Public `v1.10.10` dogfood lesson: Python subprocess resolution is now repairable with explicit operator opt-in, while GPU remains public-experimental because managed GPU requests still report `GpuSidecar` / unsupported instead of qualifying `NativeGpuBackend`.
- Closed v1.10.10 Python subprocess launcher gap: `v1.10.10` adds `tg repair-launcher --allow-foreign-rename`, documents it in root help, preserves foreign launchers unless explicitly opted in, refreshes stale tensor-grep-owned PATH bridges, and verifies `cmd`, unprofiled `pwsh`, direct managed native, and Python `subprocess.run(["tg", "--version"])` at `tg 1.10.10` after repair.
- Closed v1.10.9 release docs governance gap: `v1.10.9` synchronizes release docs/governance, keeps public positioning on agent-native code intelligence with rg-compatible search rather than faster-grep claims, reports Python-subprocess foreign launcher blockers precisely, and marks sidecar-routed native GPU diagnostic probes unsupported instead of false failures.
- Closed v1.10.8 dogfood follow-up gap: `v1.10.8` verified the public managed upgrade and launcher route after the v1.10.7 GPU/search accuracy release while preserving the foreign Python subprocess blocker as an environment-level failure.
- Closed v1.10.7 GPU/search accuracy gap: native GPU JSON line numbers are accurate after blank lines, smart-case/hidden/max-depth/text semantics stay on CPU/sidecar routes when native GPU cannot faithfully execute them, and root help/docs describe the fallback behavior.
- Closed v1.10.0 agentic GPU evidence gap: `v1.10.0` adds opt-in `tg agent --gpu-device-ids ... --json` / MCP GPU evidence, keeps sidecar-routed GPU unsupported for promotion, and updates help/docs/contracts for the agentic GPU surface.
- Closed v1.9.11 release wheel retry gap: `v1.9.11` prefetches Cargo dependencies with retry/timeout settings before PyPI wheel and sdist builds, publishes all PyPI distributions, and passes `publish-success-gate`.
- Closed v1.9.10 dogfood follow-up gap: `v1.9.10` caps capsule alternative-target confidence at the selected primary target, adds regex-backed `secrets-basic` provider-token detection for generic `sk_live...` tokens, and synchronizes stale v1.9.9 release-governance prose. Its PyPI publish did not complete because a macOS wheel runner could not resolve `index.crates.io`; the v1.9.11 release-wheel retry follow-up hardens Cargo fetch retries and publishes the replacement patch.
- Closed v1.9.9 agent workflow benchmark governance gap: `v1.9.9` release adds `run_agent_workflow_benchmarks.py` so capsule/edit-loop confidence, alternatives, validation alignment, snippets, rollback, edit order, and phase timings are benchmarked as workflow evidence rather than raw cold exact-text speed proof.
- Closed v1.9.8 Windows bridge refresh gap: `v1.9.8` release refreshes stale tensor-grep-owned `tg.com` PATH bridges after upgrade, handles Windows `PATHEXT` / registry PATH separators explicitly, and preserves foreign `tg.exe` launchers as diagnostics instead of deleting unrelated tools.
- Closed v1.9.7 benchmark positioning gap: `v1.9.7` release clarifies GPU benchmark promotion gates by separating Python GPU scale rows that are unsupported for native CUDA promotion from native CUDA correctness-pass/speed-fail rows, keeps GPU no-crossover evidence explicit, and keeps cold exact-text positioning honest with `rg` as the baseline.
- Closed v1.9.6 dogfood blocker gap: `v1.9.6` updates the docs/governance release proof, makes directory-level `$file` / `{file}` validation run once per edited file, refreshes NVIDIA GPU install/release paths to CUDA 12.8 / `cu128` for RTX 5070 / Blackwell compatibility, keeps AMD Windows on CPU fallback, expands help diagnostics, and keeps foreign first-PATH `tg` launchers explicit without deleting unrelated tools.
- Closed GPU gates and launcher diagnostics gap: `v1.9.5` prevents sidecar-routed GPU rows from counting as native CUDA scale proof, keeps ambiguous capsule alternatives visible, documents the current help surface, and reports unrelated first-PATH `tg` launchers as `foreign` with remediation instead of deleting unrelated tools.
- Closed docs/version governance and validation placeholder gap: `v1.9.4` aligns public docs and governance tests with the current project release tag, and `tg run --apply --verify --lint-cmd 'python -m py_compile "$file"'` substitutes `$file` / `{file}` before validation so paths with spaces can be quoted safely.
- Closed explicit ranking and validation quoting gap: `v1.9.3` routes explicit Python invoice-tax intent to `src/payments.py:create_invoice`, keeps ambiguous multi-language intent low-confidence, and parses quoted Windows validation commands as argv rather than literal filename characters.
- Closed edit automation safety gap: `v1.9.2` emits parseable edit JSON for diff/apply, keeps human output out of JSON stdout, rolls changed files back after failed validation, and keeps capsule validation trust aligned when mismatched commands are filtered but valid target-language commands remain.
- Closed capsule trust-alignment gap: `v1.9.1` caps capsule confidence and requires ask-before-editing when explicit language hints, exact symbol intent, primary target language, selected snippets, and validation commands disagree; validation commands are filtered to match the primary target language unless cross-language dependency evidence exists; GPU benchmark auto-recommendation remains gated by required 1GB/5GB correctness and selected-GPU speed evidence.
- Prior Actionable Context Capsule gap: `v1.9.0` releases opt-in `tg agent --query ... --json` as a deterministic work packet with primary target metadata, route rationale, bounded snippets with line maps, validation evidence, edit order, rollback/checkpoint metadata, omissions/follow-up reads, confidence, call-site evidence status, and ask-before-editing recommendations.
- Prior GPU probe and benchmark-warning gaps: `v1.8.33` scopes explicit GPU device probing so `--gpu-device-ids 0` does not initialize or warn about unrelated unsupported GPUs, and benchmark scripts now emit top-level warnings when the timed `tg` entrypoint includes `.cmd`, `uv`, or Python-module overhead.
- Prior launcher observability and benchmark attribution gaps: `v1.8.32` exposes current-process and fresh-shell launcher route diagnostics in `tg doctor --json`, including `path_tg_first_launcher_kind`, `fresh_shell_path_tg_first_launcher_kind`, and `path_tg_launcher_warning`, and records `tg_launcher_command_kind` in cold benchmark artifacts so native-exe, `.cmd` shim, `uv`, and Python-module timings are not mixed in search-speed claims.
- Prior public launcher and agent contract gaps: `v1.8.31` puts the managed native front-door directory ahead of compatibility shim directories on Windows User PATH for fresh shells, exposes top-level `validation_commands` on both `context-render` and `edit-plan` JSON, keeps default `classify` deterministic/local unless `TENSOR_GREP_CLASSIFY_PROVIDER=cybert` opts into CyBERT/Triton, and extends GPU benchmark scale/correctness gates to 1GB/5GB rows.
- Prior Windows `.cmd` quoted-pattern gap: `v1.8.30` preserves quoted multi-word no-match patterns from `cmd.exe`, direct `tg.cmd`, and Python `subprocess.run([...])` instead of splitting them into shorter false-positive searches plus bogus paths.
- Prior native-front-door CLI parity gap: `v1.8.29` accepts or intentionally sidecar-routes `tg search --files`, `tg search --multiline` / `-U`, `tg search --null`, `tg run -r`, and `tg classify --format json`; `classify` falls back before expensive provider/model setup when unavailable; and the GPU benchmark harness treats no-match as a valid comparator outcome.
- Public `v1.9.9` dogfood: direct `C:\Users\oimir\.tensor-grep\bin\tg.exe --version` reports `tg 1.9.9`; PyPI latest and pinned public install resolve `tensor-grep==1.9.9`; `uvx --from tensor-grep==1.9.9 tg --version` reports `tensor-grep 1.9.9`; `tg update` from `1.9.8` installed sidecar `1.9.9` and refreshed the managed native front door; fresh `cmd`, unprofiled `pwsh`, and direct managed native report `tg 1.9.9`.
- Prior public update dogfood: `tg update` from `v1.9.3` initially hit PyPI propagation lag, then installed sidecar `tensor-grep==1.9.4`, refreshed `~/.tensor-grep/bin/tg.exe`, and verified `tg 1.9.4`. `tg --version`, `cmd /c tg --version`, `pwsh -NoProfile -Command "tg --version"`, WSL `tg --version`, Git Bash `tg --version`, and direct `C:\Users\oimir\.tensor-grep\bin\tg.exe --version` reported `tg 1.9.4`.
- Prior public installer dogfood: rerunning `scripts/install.ps1` for `v1.8.31` put `C:\Users\oimir\.tensor-grep\bin` ahead of compatibility shim directories on User PATH. A simulated fresh shell resolves `C:\Users\oimir\.tensor-grep\bin\tg.exe` before `C:\Users\oimir\bin\tg.cmd`.
- Prior public doctor dogfood: `tg doctor --json` reported `version = 1.9.4`, `rust_binary_version_status = matches`, `search_acceleration_backend = standalone-native-tg`, `path_tg_first_launcher_kind = cmd-shim`, `fresh_shell_path_tg_first_launcher_kind = managed-native`, `fresh_shell_path_tg_first_version_matches = true`, and `path_tg_launcher_warning` when the current process still resolves the compatibility shim before the managed native front door.
- Public capsule dogfood: `tg agent src/tensor_grep/cli --query "agent context capsule" --json --max-tokens 300 --max-files 2 --max-sources 2` returned a capsule with primary target, route rationale, snippets, omitted primary follow-up reads, confidence downgrade, rollback command/argv, and ask-before-editing metadata.
- Public `v1.9.0` dogfood follow-up found that ambiguous mixed-language invoice-tax capsule queries could be overconfident and pair a TypeScript primary target with pytest validation. `v1.9.1` adds mixed-language capsule regressions, shared `validation_alignment`, confidence caps for query-language/target-language conflicts, and GPU benchmark recommendation gates.
- Public native CLI dogfood: installed `tg 1.8.32` accepted `tg search --multiline`, `tg search -U`, `tg search --files`, `tg search --null`, `tg run -r`, and `tg classify --format json`.
- Public Windows launcher dogfood: `cmd /c tg`, direct `C:\Users\oimir\.tensor-grep\bin\tg.cmd`, native `tg.exe`, and Python `subprocess.run([...])` all return exit `1` with empty stdout for a fresh quoted no-match phrase.
- Public classify dogfood: `tg classify --format json tests\conftest.py` completed in 0.206s with local deterministic classifications.
- Post-`v1.9.6` / latest `v1.9.11` GPU dogfood: native CUDA release search passes exact match/file-set smoke correctness on both device 0 (`NVIDIA GeForce RTX 4070`, `sm_89`) and device 1 (`NVIDIA GeForce RTX 5070`, `sm_120`); PyTorch sidecar was refreshed from `2.6.0+cu124` to `2.11.0+cu128` and explicit sidecar searches now run on both devices. The native scale artifacts `artifacts/bench_gpu_native_device0_v195_blockers_fixed.json` and `artifacts/bench_gpu_native_device1_v195_blockers_fixed.json` pass 1GB and 5GB correctness on both GPUs, but no crossover exists: latest 5GB dogfood ratios are `35.46x` slower than `rg` on device 0 and `29.91x` slower than `rg` on device 1. A 2026-05-11 managed-front-door route audit confirms the public Windows `tg.exe --gpu-device-ids 0 --json ...` route reports `routing_backend = "GpuSidecar"` and `sidecar_used = true`; those rows are sidecar-contaminated and unsupported for native CUDA speed proof. This is compatibility and correctness proof, not a promotion claim.
- Post-`v1.9.6` local help-menu dogfood: root `tg --help` now advertises the current agent capsule surface, top-level `validation_commands`, `$file` / `{file}` validation placeholders, deterministic `--format rg --sort path`, generated-root guardrails, experimental GPU flags, local-vs-CyBERT classify behavior, `tg doctor --json` launcher fields, and `TG_SIDECAR_PYTHON` / `TG_NATIVE_TG_BINARY` / `TG_RG_PATH` / `TG_FORCE_CPU` / `TG_SIDECAR_TIMEOUT_MS` / `TENSOR_GREP_DEVICE_IDS` / `TENSOR_GREP_CLASSIFY_PROVIDER` / `TENSOR_GREP_TRITON_TIMEOUT_SECONDS` overrides.
- Post-`v1.9.6` local launcher blocker: agent-readiness found a foreign `C:\Users\oimir\AppData\Local\Programs\Python\Python314\Scripts\tg.exe` that reports `Together CLI (v2.12.0)` ahead of the managed native front door in Windows fresh-shell PATH. `tg doctor --json` classifies this as `foreign` and emits warning/remediation fields. Machine PATH ordering was not writable on this host, so local dogfood was repaired by placing a tensor-grep `tg.com` bridge in the same directory; Windows `PATHEXT` resolves `.COM` before `.EXE`, and fresh `cmd` / unprofiled `pwsh` now report `tg 1.9.6`. Product code must still avoid deleting unrelated launchers; move `C:\Users\oimir\.tensor-grep\bin` earlier in the effective PATH tier or rename the foreign command if the user owns it.
- Fast agent-readiness dogfood before PR #72: `python scripts/agent_readiness.py --output artifacts/agent_readiness_launcher_observability.json` passed all checks, including public version probes, `public-windows-launcher-quoted-patterns`, repo doctor, context consistency, deterministic rg parity edges, generated-root guardrails, AST smoke, MCP context-render smoke, and docs claim hygiene.
- Repo-dev dogfood: stale in-tree standalone binaries remain skipped unless explicitly pinned with `TG_NATIVE_TG_BINARY` or `TG_MCP_TG_BINARY`.

## Current Post-v1.11.3 Scope

Current release branch is publication-complete for `v1.11.5`: PR #116 `fix: harden post-release docs governance` was squash-merged as `a78e33c`, release commit `e33c2ba chore(release): v1.11.5 [skip ci]` exists, main CI run `25866871838` passed tests/assets, GitHub asset upload, PyPI publish, and `publish-success-gate`, and CodeQL run `25866868462` passed. Public dogfood verified PyPI, release assets, `uvx`, and post-release-safe docs governance. The `v1.10.10` Windows subprocess launcher repair remains the public launcher repair baseline; keep foreign launcher handling opt-in and auditable rather than deleting unrelated tools.

The public Windows `.cmd` bridge quoted multi-word no-match follow-up shipped in `v1.8.30`. The Windows native-first PATH, agent JSON validation-command, local default classify, and GPU scale benchmark follow-ups shipped in `v1.8.31`. The launcher-route observability and benchmark launcher-attribution follow-up shipped in `v1.8.32`. The explicit GPU probe scoping and benchmark launcher warning follow-up shipped in `v1.8.33`. The Actionable Context Capsule v1 shipped in `v1.9.0`; mixed-language capsule trust alignment and GPU recommendation hygiene shipped in `v1.9.1`; edit JSON/rollback safety shipped in `v1.9.2`; explicit Python ranking and quoted validation commands shipped in `v1.9.3`; docs-governance and validation placeholders shipped in `v1.9.4`; native CUDA gate hardening, capsule alternatives, help diagnostics, and foreign launcher diagnostics shipped in `v1.9.5`; directory validation, CUDA 12.8 install paths, help coverage, and release-proof governance shipped in `v1.9.6`; GPU benchmark promotion-gate taxonomy and cold-search positioning shipped in `v1.9.7`; stale tensor-grep-owned `tg.com` bridge refresh after upgrade shipped in `v1.9.8`; agent workflow benchmark governance shipped in `v1.9.9`; capsule confidence/secrets/docs dogfood follow-ups shipped in source/GitHub assets in `v1.9.10`; and release wheel Cargo prefetch retries shipped in `v1.9.11`.

Active post-`v1.12.6` implementation scope:

- Continue hardening `tg agent` / Actionable Context Capsule ranking for ambiguous multi-language intent, token economy, follow-up reads, call-site evidence, and workflow benchmarks without changing raw `--format rg`, `--json`, or `--ndjson` semantics.
- Agents must inspect top-level `ambiguity` before editing. `ambiguity.status = "tie_requires_confirmation"` is a hard stop for autonomous edits; `tie_resolved` is acceptable only when `resolved_by` evidence is explicit.
- Keep Windows public launcher dogfood checking shell routes, sidecar-backed commands, and Python `subprocess.run(["tg", ...])`. Python subprocess resolution can differ from shell `PATHEXT` resolution and should be reported in `tg doctor --json` as its own route.
- `tg upgrade` should repair Windows User/current PATH when that can put the managed native `tg.exe` ahead of foreign same-name launchers for Python subprocesses, and should report a Machine PATH blocker when an unrelated foreign `tg.exe` still wins. Keep unrelated launchers as diagnostics instead of deletion targets unless the operator explicitly runs `tg repair-launcher --allow-foreign-rename`, which backs up the foreign `tg.exe` before installing the verified managed native front door into that PATH slot.
- Context/session latency must stay guarded: direct validation evidence should reuse repo-map imports, and weak fuzzy symbols should not trigger expensive blast-radius work unless the target is explicit or sufficiently supported.
- Keep edit validation command parsing and `$file` / `{file}` placeholder substitution argv-safe for quoted Windows paths with spaces.
- Preserve mixed-language capsule trust: explicit query language hints, exact symbol intent, primary target language, selected snippets, and validation commands must agree or `confidence.overall` / `primary_target.confidence` must be capped and `ask_user_before_editing.required` must become true.
- Keep validation hints aligned with the selected primary target language unless verified cross-language dependency evidence exists. `validation_alignment` should report filtered mismatches so a TypeScript target is not silently paired with pytest-only validation.
- Keep Windows managed installer/update dogfood checking both the update path and a fresh-shell PATH environment; existing parent shells may keep old PATH until restarted.
- Keep current-process vs fresh-shell launcher routing visible in `tg doctor --json` with `path_tg_first_launcher_kind`, `fresh_shell_path_tg_first_launcher_kind`, and `path_tg_launcher_warning` so slower compatibility-shim timing is visible.
- Keep foreign first-PATH `tg` diagnostics separate from tensor-grep-owned stale-launcher cleanup. If a candidate reports another product's version, `tg doctor --json` should classify it as `foreign`, readiness should fail with remediation, and installer logic should not delete unrelated tools.
- Keep benchmark `tg_launcher_command_kind` in the environment block so native-exe, `.cmd` shim, `uv`, and Python-module routes are not mixed in cold-path claims. Benchmark artifacts should also preserve `tg_binary_version_status` and warn on stale in-tree native tg binaries so stale dev builds cannot look like current release proof. Treat benchmark warnings about shim/interpreter overhead as blocking evidence for performance comparisons.
- Keep `classify` provider/cache UX explicit and fast. The default local path is quick; CyBERT/Triton remains opt-in and must not warn or block agent loops when unavailable.
- Keep GPU experimental for public managed installs until native streaming correctness and speed are proven on the 1GB/5GB gates by public `NativeGpuBackend`, `sidecar_used = false` assets. Local post-`v1.12.6` CUDA-feature work now shows a real high-intensity lane: public `tg search -F --gpu-device-ids 0 --json -e ...` through the local CUDA native binary ran 100 fixed-string patterns over 1GB at `1301.676ms` versus `7222.304ms` for sequential `rg` no-match probes, and a 100-pattern mixed-match probe with 2665 emitted matches ran `2488.768ms` versus `6676.904ms` for sequential `rg`. Single-pattern GPU still loses (`1093.778ms` versus `73.838ms` for a 1GB no-match), so the product story is "many fixed patterns over large corpora," not blanket faster grep. Explicit `--gpu-device-ids` routing must stay scoped to selected devices and sidecar-routed rows must not count as native CUDA scale-gate timings. GPU-requested CPU fallback or sidecar compatibility output should report `gpu_evidence_status = unsupported`, `gpu_proof = false`, `native_gpu_unavailable`, and `not_gpu_proof_reason`, with unsupported rows marked `promotion_evidence = false`. Native CUDA JSON/verbose output now reports CPU staging bytes/time, pageable-host staging bytes, H2D transfer time, and kernel time so future artifacts can separate CPU bleed from device work.
- Current GPU-readiness follow-up is still diagnostic, not promotion. The planned benchmark artifact surface adds advisory bottleneck summaries, source provenance, and host-tail accounting from native pipeline samples so future optimization targets are evidence-based. Missing pipeline evidence should report `NOT_AVAILABLE`, and advisory bottleneck fields must not influence `gpu_auto_recommendation` or `promotion_ready`.
- GPUDirect Storage/cuFile is a future opt-in Linux experiment only if host-tail/storage evidence dominates after instrumentation. It can remove CPU bounce buffers for suitable coarse streaming transfers, but requires platform, filesystem, O_DIRECT/alignment, buffer-registration reuse, batching/asynchrony, and topology proof; do not attach it to Windows/default readiness or GPU promotion claims.
- Release-native GPU front-door assets are opt-in profile work, not default GPU readiness. Default release assets remain CPU-only `native-frontdoor`; `TENSOR_GREP_RELEASE_NATIVE_ASSET_PROFILE=native-frontdoor-gpu` enables additional Linux/Windows NVIDIA assets while macOS stays CPU-only. Installers and `tg upgrade` may prefer an NVIDIA asset only when explicitly requested with `TENSOR_GREP_NATIVE_FRONTDOOR_FLAVOR=nvidia` or `TG_NATIVE_FRONTDOOR_REQUESTED_FLAVOR=nvidia`; they must fall back to CPU and record asset flavor without implying performance promotion.
- Keep `tg agent --gpu-device-ids ... --json` as an opt-in agentic evidence path: batch query terms through the selected native GPU route, expose `gpu_acceleration`, and mark evidence used only when the runtime reports `NativeGpuBackend` with `sidecar_used = false`. Local dogfood on the managed Windows front door currently reports `status = "unsupported"`, `routing_backend = "GpuSidecar"`, and `sidecar_used = true`, which is the intended no-promotion behavior until the front door is a clean native CUDA route.
- Keep AST parity roadmap work separate from `tg run`'s validated useful slice; do not imply full ast-grep replacement.

Dogfood follow-up workflow:

- Split future dogfood feedback into PR-sized slices with one behavioral theme per branch; do not collapse independent fixes into one broad PR.
- Use Exa research before implementation when a slice depends on current external behavior such as `rg -F -e`, `ast-grep`, CUDA/Blackwell support, GitHub Actions, release packaging, or agent-evaluation harnesses.
- Run a thinktank or equivalent independent planning review for benchmark interpretation, GPU promotion policy, product positioning, and release workflow changes.
- Ask Gemini for a bounded read-only diff review before each PR merge, then verify any finding locally before changing code.
- For each slice: write/update the contract test first, implement the smallest fix, run the targeted suite, run lint and format, push the PR, wait for PR CI, squash-merge, then watch main CI.
- Release-bearing work is not complete until semantic-release, GitHub release assets, PyPI/package publication, and public release dogfood all pass.
- Use `benchmarks/run_agent_success_harness.py --output artifacts/bench_agent_success_harness.json` when dogfood asks for an end-to-end agent success proof from query intent through context, edit seed, checkpointed apply, verification, and rollback. Treat the artifact as workflow evidence, not a raw search speed claim.

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

## What v1.8.12-v1.9.6 Fixed

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
- The `v1.9.3` release hardens explicit language/file-name agent ranking and quoted Windows validation command parsing while keeping ambiguous multi-language routing low-confidence.
- The `v1.9.5` release hardens GPU native gate attribution, capsule alternatives, root help diagnostics, and foreign launcher diagnostics.
- The `v1.9.6` release fixes the `v1.9.5` dogfood blockers: directory-level rewrite validation expands `$file` / `{file}` once per edited file, CUDA install/release paths use `cu128` for RTX 5070 / Blackwell compatibility, AMD Windows remains a CPU fallback, root help lists current operational settings, and docs/governance proof records the release accurately.
- The `v1.9.4` release fixes stale docs-governance expectations and substitutes `$file` / `{file}` validation placeholders before executing validation commands.

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
- PR #81 `fix: harden agent ranking docs and validation quoting`: merged and released as `v1.9.3`
- PR #83 `fix: harden GPU gates and launcher diagnostics`: merged and released as `v1.9.5`
- PR #82 `fix: harden docs governance and validation placeholders`: merged and released as `v1.9.4`
- PR #84 `fix: harden v1.9.5 dogfood blockers`: merged and released as `v1.9.6`
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

1. Keep the agent-readiness dogfood gate (`python scripts/agent_readiness.py --output artifacts/agent_readiness.json`, or `tg dogfood --output artifacts/dogfood_readiness.json` for the verdict envelope) fast and representative; it should cover context trust, `agent-capsule`, `agent-capsule-hardcases`, rg sorted edges, broad generated-root scan guardrails, AST smoke, MCP smoke, shell version probes, and docs claim checks.
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
