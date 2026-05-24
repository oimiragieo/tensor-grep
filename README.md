<div align="center">
  <img src="docs/assets/logo.jpg" alt="tensor-grep logo" width="800"/>
</div>

# tensor-grep (tg)

Native search and rewrite tool for large text corpora and codebases. `tensor-grep` combines a Rust-native CPU text engine, Rust-native AST search/rewrite, indexed repeated-query acceleration, and a benchmark-governed native GPU path for large workloads.

`tensor-grep` has first class support on Windows, macOS and Linux. The native CPU engine embeds ripgrep's grep crates directly (no subprocess overhead) with chunk parallelism for large files. The native GPU engine uses Rust-native CUDA via `cudarc` with NVRTC JIT compilation, CUDA streams, pinned memory, and CUDA graphs. GPU routing stays opt-in unless local calibration proves a real end-to-end crossover.

Harness consumers should use the documented public contracts in [docs/harness_api.md](docs/harness_api.md) and the workflow guide in [docs/harness_cookbook.md](docs/harness_cookbook.md).

## Canonical Docs

Use these documents as the current product contract instead of relying on scattered examples:

- [docs/benchmarks.md](docs/benchmarks.md) for the accepted benchmark matrix, artifact naming, and regression rules
- [docs/tool_comparison.md](docs/tool_comparison.md) for the public workload-class comparison story against `rg`, `git grep`, `ast-grep`, and other comparator families
- [docs/gpu_crossover.md](docs/gpu_crossover.md) for the current native GPU crossover story and its limits
- [docs/routing_policy.md](docs/routing_policy.md) for current CPU/GPU/index/AST routing behavior
- [docs/harness_api.md](docs/harness_api.md) for machine-readable CLI and MCP contract shapes
- [docs/harness_cookbook.md](docs/harness_cookbook.md) for end-to-end harness workflows using `tg.exe search --json`, `tg.exe search --ndjson`, `tg.exe run --rewrite`, `tg.exe calibrate`, and `tg mcp`
- [docs/installation.md](docs/installation.md) for the supported install paths and operational install notes
- [docs/RELEASE_CHECKLIST.md](docs/RELEASE_CHECKLIST.md) for the current enterprise release and rollback runbook
- [docs/CI_PIPELINE.md](docs/CI_PIPELINE.md) for the current CI, release, audit, and dependency-maintenance automation
- [docs/SESSION_HANDOFF.md](docs/SESSION_HANDOFF.md) for the latest release state, known weak spots, and next-session guidance

The project is benchmark-governed. Public claims should follow the canonical docs above, not historical README snapshots.

## Enterprise Docs

These documents define the operating and governance surface for teams running `tensor-grep` in production:

- [docs/SUPPORT_MATRIX.md](docs/SUPPORT_MATRIX.md) for supported platforms, runtimes, and distribution channels
- [docs/CONTRACTS.md](docs/CONTRACTS.md) for compatibility guarantees around configs, caches, and machine-readable outputs
- [docs/HOTFIX_PROCEDURE.md](docs/HOTFIX_PROCEDURE.md) for patch, rollback, and verification process
- [docs/EXPERIMENTAL.md](docs/EXPERIMENTAL.md) for hidden and opt-in features that are intentionally outside the stable public CLI surface
- [docs/CI_PIPELINE.md](docs/CI_PIPELINE.md) for CI workflow structure, Dependabot policy, and scheduled audit remediation
- [SECURITY.md](SECURITY.md) for vulnerability reporting expectations
- [CONTRIBUTING.md](CONTRIBUTING.md) for contribution, validation, and release-intent rules

## Current Release State

release_docs_current_tag: v1.13.6

Latest tagged GitHub release: [`v1.13.6`](https://github.com/oimiragieo/tensor-grep/releases/tag/v1.13.6). GitHub assets and PyPI publication are verified by main CI before `publish-success-gate` passes.
Latest complete PyPI release: [`v1.13.6`](https://github.com/oimiragieo/tensor-grep/releases/tag/v1.13.6). This is also the latest complete release-asset distribution.
Latest verified release proof: `v1.12.34` completed in main CI run `26094452260`; latest CodeQL proof remains run `26064676072`.

Current positioning:

- `tg` is the agent-native search, context, AST, and edit-planning orchestration layer.
- `rg` remains the cold exact-text baseline. Use `--sort path --format rg` when automation needs deterministic ripgrep-shaped stdout.
- `ast-grep` remains the structural-search feature/performance baseline. `tg run` is a validated useful slice, not a full ast-grep replacement.
- GPU remains opt-in/experimental until local benchmarks prove a real end-to-end crossover. Default `classify` is now deterministic and local unless `TENSOR_GREP_CLASSIFY_PROVIDER=cybert` opts into the CyBERT/Triton path.
- Public GPU note: in `v1.12.32`, public managed GPU is not promotion-ready. The public managed binary falls back to `NativeCpuBackend` or reports unsupported for explicit GPU requests unless a CUDA-feature native build can produce `NativeGpuBackend` / `sidecar_used = false` evidence. GPU benchmark artifacts expose `promotion_evidence_contract`, `promotion_blockers`, and declared workload class so fallback or sidecar rows cannot look like promotion proof. The latest public managed many-pattern dogfood is not promotion-ready for GPU: the accepted improvement is a native CPU fixed multi-pattern fast path, not public GPU readiness. Native CUDA correctness rows are not public GPU readiness, and GPU remains experimental until public managed binaries produce 1GB and 5GB correctness and speed wins for the declared workload on RTX 4070 / RTX 5070 class devices. Many-pattern GPU claims require a single-invocation fair baseline such as `rg -F -e ... -e ...`; sequential `rg` loops are exploratory amortization evidence only. Public GPU promotion also requires managed NVIDIA front-door provenance from `tg-native-metadata.json` plus the dispatch-only `public-gpu-proof.yml` workflow running `benchmarks/run_gpu_native_benchmarks.py --public-managed-proof`, which must emit `public_managed_promotion_ready = true` and `public_gpu_proof = true` after direct `rg --json` 1GB/5GB correctness and speed wins over both `rg` and `tg_cpu`.
- The public native front door is now the performance-critical shell entrypoint. Advertised CLI flags must either execute there or route to the Python sidecar intentionally; help text that advertises flags the native parser rejects is a release blocker.
- Root search shortcuts are part of that native-front-door contract. `tg PATTERN PATH`, `tg -t js PATTERN PATH`, and `tg --count-matches PATTERN PATH` must behave as `tg search ...`, preserving the common rg-compatible flags instead of falling into positional-only parsing.
- `tg new project NAME` creates a named AST project directory; `--base-dir DIR` acts as the parent directory. Bare `tg new` and `tg new project` still initialize the current or configured `--base-dir` directly.
- `tg agent --query ... --json` is the first Actionable Context Capsule surface: a bounded, deterministic work packet with primary files/functions, alternative targets, route rationale, snippets with line maps, validation evidence, rollback/checkpoint metadata, omissions, confidence, optional native GPU route evidence, unresolved equal-confidence tie metadata, and an ask-before-editing recommendation. It is an opt-in agent command, not a mutation of raw `--format rg`, `--json`, or `--ndjson`.
- `tg agent --gpu-device-ids 0,1 --query ... --json` runs an opt-in batched GPU evidence scan for the selected devices and records `gpu_acceleration`; sidecar-routed or CPU-fallback results are reported as unsupported instead of being counted as GPU proof.
- Capsule confidence must be honest when query language hints, primary target language, selected snippets, and validation commands disagree. Mixed-language agent workflows use `validation_alignment` and ask-before-editing metadata instead of silently pairing a TypeScript target with pytest-only validation.

What `v1.12.34` closed:

- PR #163 `fix: harden v1.12.33 dogfood contracts` shipped the release as merge commit `c0cb613 fix: harden v1.12.33 dogfood contracts` and release commit `e069f67 chore(release): v1.12.34 [skip ci]`
- Latest merged feature commit before this release line: `a518cc6 feat: add agent success harness`
- main CI run `26094452260` passed semantic-release, `publish-github-release-assets`, `publish-pypi`, and `publish-success-gate`; latest CodeQL proof remains run `26064676072`
- GitHub release assets for `v1.12.34` include native CPU front doors, checksums, winget manifest, Homebrew formula, and publish instructions; `uvx --refresh-package tensor-grep --from tensor-grep==1.12.34 tg --version` reports `tensor-grep 1.12.34`
- The remaining `--column --no-column` rg override edge now accepts and forwards through both `tg search --format rg ...` and root-level `tg --format rg ...` with last-flag-wins behavior.
- Agent readiness now diagnoses stale repo-local `uv run tg` warmup drift, and the `ripgrep binary resolution` capsule hardcase is pinned so natural-language target selection does not regress to marker helpers.
- Post-release-safe docs governance is preserved: current tag labels advance with semantic-release while this latest verified proof block records exact CI, assets, PyPI, and `uvx` evidence. The release preserves conservative positioning: `tg` is agent-native code intelligence with rg-compatible common search, not a blanket faster-ripgrep or GPU-acceleration claim.

What `v1.12.32` closed:

- PR #161 `fix: harden dogfood readiness contracts` shipped the release as merge commit `6b00e6d fix: harden dogfood readiness contracts` and release commit `d708c25 chore(release): v1.12.32 [skip ci]`
- Latest merged feature commit before this release line: `a518cc6 feat: add agent success harness`
- main CI run `26064673640` passed semantic-release, `publish-github-release-assets`, `publish-pypi`, and `publish-success-gate`; CodeQL run `26064676072` passed
- GitHub release assets for `v1.12.32` include native CPU front doors, checksums, winget manifest, Homebrew formula, and publish instructions; `uvx --refresh-package tensor-grep --from tensor-grep==1.12.32 tg --version` reports `tensor-grep 1.12.32`
- Installed-public dogfood now includes an rg inverse/config override sweep so advertised no-op and inverse flags cannot regress into native-parser rejection without failing release readiness.
- `tg dogfood` now emits `world_class_readiness.status = "not_claimed"` with limitation surfaces for raw cold text search, full ast-grep surface, public GPU acceleration, LSP semantic provider proof, and enterprise target-selection metrics. A dogfood PASS is release-readiness evidence, not a blanket full-rg/full-ast/public-GPU/production-LSP claim.
- Post-release-safe docs governance is preserved: current tag labels advance with semantic-release while this latest verified proof block records the exact CI, CodeQL, assets, PyPI, and `uvx` evidence. The release preserves conservative positioning: `tg` is agent-native code intelligence with rg-compatible common search, not a blanket faster-ripgrep or GPU-acceleration claim.

What `v1.12.14` closed:

- PR #143 `fix: collect capsule call-site evidence` shipped the release as merge commit `21e5437 fix: collect capsule call-site evidence` and release commit `3be6879 chore(release): v1.12.14 [skip ci]`
- Latest merged feature commit before this release line: `a518cc6 feat: add agent success harness`
- main CI run `25951521056` passed semantic-release, `publish-github-release-assets`, `publish-pypi`, and `publish-success-gate`; CodeQL runs `25951520943` and `25951813292` passed
- GitHub release assets for `v1.12.14` include native CPU front doors, checksums, winget manifest, Homebrew formula, and publish instructions; `uvx --refresh-package tensor-grep --from tensor-grep==1.12.14 tg --version` reports `tensor-grep 1.12.14`
- Actionable Context Capsules now collect bounded call-site evidence for explicit high-confidence symbol queries, while fuzzy queries skip call-site collection and report why instead of inflating confidence.
- Post-release-safe docs governance is preserved: current tag labels can advance with semantic-release while this latest verified proof block records the exact CI, CodeQL, assets, PyPI, and `uvx` evidence. The release preserves conservative positioning: `tg` is agent-native code intelligence with rg-compatible common search, not a blanket faster-ripgrep or GPU-acceleration claim.

What `v1.12.13` closed:

- PR #142 `fix: harden agent bridge ranking` shipped the release as merge commit `8a73f8d fix: harden agent bridge ranking` and release commit `044b786 chore(release): v1.12.13 [skip ci]`
- Latest merged feature commit before this release line: `a518cc6 feat: add agent success harness`
- main CI run `25950189993` passed semantic-release, `publish-github-release-assets`, `publish-pypi`, and `publish-success-gate`; CodeQL runs `25950189762` and `25950454736` passed
- GitHub release assets for `v1.12.13` include native CPU front doors, checksums, winget manifest, Homebrew formula, and publish instructions; `uvx --refresh-package tensor-grep --from tensor-grep==1.12.13 tg --version` reports `tensor-grep 1.12.13`
- Agent ranking for Windows subprocess bridge hardening now prefers the substantive Rust bridge implementation over marker helpers, keeps equal-confidence alternatives visible, and requires confirmation when the evidence remains tied.
- The release preserves conservative positioning: `tg` is agent-native code intelligence with rg-compatible common search, not a blanket faster-ripgrep or GPU-acceleration claim.

What `v1.12.12` closed:

- PR #141 `fix: harden agent output budget hygiene` shipped the release as merge commit `b601366 fix: harden agent output budget hygiene` and release commit `8d362de chore(release): v1.12.12 [skip ci]`
- Latest merged feature commit before this release line: `a518cc6 feat: add agent success harness`
- main CI run `25948611239` passed semantic-release, `publish-github-release-assets`, `publish-pypi`, and `publish-success-gate`; CodeQL runs `25948611207` and `25949010544` passed
- GitHub release assets for `v1.12.12` include native CPU front doors, checksums, winget manifest, Homebrew formula, and publish instructions; `uvx --refresh-package tensor-grep --from tensor-grep==1.12.12 tg --version` reports `tensor-grep 1.12.12`
- Agent output-budget hygiene now caps source payloads and generated metadata under bounded context flags, excludes tool-owned temp/probe context from edit-plan related paths, and keeps public release docs synchronized with post-release-safe governance.
- The release preserves conservative positioning: `tg` is agent-native code intelligence with rg-compatible common search, not a blanket faster-ripgrep or GPU-acceleration claim.

What `v1.12.11` closed:

- PR #140 `fix: harden ast cli contract hygiene` shipped the release as merge commit `2aebac6 fix: harden ast cli contract hygiene (#140)` and release commit `5295f85 chore(release): v1.12.11 [skip ci]`
- Latest merged feature commit before this release line: `a518cc6 feat: add agent success harness`
- main CI run `25946534528` passed semantic-release, `publish-github-release-assets`, `publish-pypi`, and `publish-success-gate`; CodeQL runs `25946534395` and `25946912110` passed
- GitHub release assets for `v1.12.11` include native CPU front doors, checksums, winget manifest, Homebrew formula, and publish instructions; `uvx --refresh-package tensor-grep --from tensor-grep==1.12.11 tg --version` reports `tensor-grep 1.12.11`
- AST CLI hygiene is explicitly scoped: safe ast-grep-compatible aliases route or execute (`run --pattern`, `run --update-all`, `scan --rule`, `scan --filter`, positional scan paths, and `new --config`) while unsupported semantic matcher flags fail with deliberate diagnostics instead of implying full ast-grep compatibility.
- The release preserves post-release-safe docs governance, current tag labels, and conservative positioning: `tg run` remains a validated AST slice, not a blanket ast-grep replacement.

What `v1.12.10` closed:

- PR #139 `fix: harden rg flag contract aliases` shipped the release as merge commit `bbc08e4 fix: harden rg flag contract aliases (#139)` and release commit `114e290 chore(release): v1.12.10 [skip ci]`
- Latest merged feature commit before this release line: `a518cc6 feat: add agent success harness`
- main CI run `25944371829` passed semantic-release, `publish-github-release-assets`, `publish-pypi`, and `publish-success-gate`; CodeQL run `25944371422` passed
- GitHub release assets for `v1.12.10` include native CPU front doors, checksums, winget manifest, Homebrew formula, and publish instructions; `uvx --refresh-package tensor-grep --from tensor-grep==1.12.10 tg --version` reports `tensor-grep 1.12.10`
- rg flag-contract aliases remain explicit: accepted public surfaces include `--maxdepth`, `--sort-files`, `--no-ignore-dot`, `--no-ignore-exclude`, `--no-ignore-files`, `--no-ignore-global`, and `--no-ignore-parent`; `-0` / `--null` remains the compatible NUL-output path-list flag.
- `--print0` is intentionally not advertised as a compatible current-ripgrep flag because `rg 15.1.0` rejects it; docs and tests point users to `-0` / `--null`.

What `v1.12.9` closed:

- PR #138 `fix: harden v1.12.8 dogfood contracts` shipped the release as merge commit `21627d2 fix: harden v1.12.8 dogfood contracts` and release commit `b15f71a chore(release): v1.12.9 [skip ci]`
- Latest merged feature commit before this release line: `a518cc6 feat: add agent success harness`
- main CI run `25941933937` passed semantic-release, `publish-github-release-assets`, `publish-pypi`, and `publish-success-gate`; CodeQL run `25941933444` passed
- GitHub release assets for `v1.12.9` include native CPU front doors, checksums, winget manifest, Homebrew formula, and publish instructions; `uvx --refresh-package tensor-grep --from tensor-grep==1.12.9 tg --version` reports `tensor-grep 1.12.9`
- public native/dev CLI drift remains closed for accepted search flags and option-first structured search: `tg --json --no-ignore ...`, `tg search --passthrough`, `--unicode`, `--auto-hybrid-regex`, `--type-list`, `--pcre2-version`, and `tg search --version` execute or route intentionally
- root-level cold rg-shaped searches route through the rg-compatible front door for accepted common flags so benchmark rows do not pay Python/control-plane overhead when the requested contract is ripgrep-shaped stdout
- `tg run --pattern ...`, bounded `tg map`/`tg context`, and conservative Windows subprocess bridge ranking hardening shipped in the release line; `tg search --json` is tensor-grep aggregate JSON, `tg search --format rg --json` is the explicit ripgrep JSON Lines compatibility route, and `tg search --ndjson` remains tensor-grep flattened streaming rows, not the rg event schema

What `v1.11.5` closed:

- PR #116 `fix: harden post-release docs governance` shipped the release as merge commit `a78e33c fix: harden post-release docs governance` and release commit `e33c2ba chore(release): v1.11.5 [skip ci]`
- main CI run `25866871838` passed semantic-release, `publish-github-release-assets`, `publish-pypi`, and `publish-success-gate`; CodeQL run `25866868462` passed
- GitHub release assets for `v1.11.5` include native CPU front doors, checksums, winget manifest, Homebrew formula, and publish instructions; `uvx --refresh-package tensor-grep --from tensor-grep==1.11.5 tg --version` reports `tensor-grep 1.11.5`
- post-release-safe docs governance now separates auto-stamped current tag labels from latest verified proof blocks so semantic-release patch bumps do not make future docs-governance tests depend on a not-yet-written historical block

What `v1.11.4` closed:

- PR #114 `fix: harden public GPU unavailable routing` merged as `361e0db fix: harden public GPU unavailable routing`
- PR #115 `fix: harden release docs stamp governance` shipped the release as merge commit `2100122 fix: harden release docs stamp governance` and release commit `49a7c9a chore(release): v1.11.4 [skip ci]`
- main CI run `25863754902` passed semantic-release, `publish-github-release-assets`, `publish-pypi`, and `publish-success-gate`; CodeQL run `25863751937` passed
- GitHub release assets for `v1.11.4` include native CPU front doors, checksums, winget manifest, Homebrew formula, and publish instructions; `uvx --refresh-package tensor-grep --from tensor-grep==1.11.4 tg --version` reports `tensor-grep 1.11.4`
- explicit public GPU requests without an explicit sidecar now report native GPU unavailable and fall back to `NativeCpuBackend` instead of making sidecar routing look like promotion evidence
- post-release-safe docs governance: current tag labels can move with semantic-release while detailed proof blocks keep the last verified CI/CodeQL/release evidence accurate

What `v1.11.3` closed:

- PR #113 `fix: accelerate fixed multi-pattern native search` shipped the release as merge commit `87d4ca4 fix: accelerate fixed multi-pattern native search` and release commit `2731659 chore(release): v1.11.3 [skip ci]`
- main CI run `25860914920` passed semantic-release, `publish-github-release-assets`, `publish-pypi`, and `publish-success-gate`; CodeQL run `25860914488` passed
- GitHub release assets for `v1.11.3` include native CPU front doors, checksums, winget manifest, Homebrew formula, and publish instructions; `uvx --refresh-package tensor-grep --from tensor-grep==1.11.3 tg --version` reports `tensor-grep 1.11.3`
- fixed multi-pattern fixed-string native CPU search now uses a single Aho-Corasick pass instead of sequentially scanning once per pattern when requested semantics are safe for that route
- local release evidence for 100 fixed no-match patterns over 1GB measured the native `tg` fixed multi-pattern path at about `0.0239s` median versus fair `rg -F -e ... -e ...` multi-pattern at about `0.0347s`; this is a CPU workload-class win, not a GPU promotion claim
- public GPU remains experimental: managed GPU requests still route through `GpuSidecar` / unsupported rather than public `NativeGpuBackend` evidence

What `v1.11.2` closed:

- PR #110 `fix: expose classify provider provenance` shipped the release as merge commit `ada6a47 fix: expose classify provider provenance (#110)` and release commit `5679b22 chore(release): v1.11.2 [skip ci]`
- main CI run `25839425530` passed semantic-release, `publish-github-release-assets`, `publish-pypi`, and `publish-success-gate`; CodeQL run `25839425282` passed
- GitHub release assets for `v1.11.2` include native CPU front doors, checksums, winget manifest, Homebrew formula, and publish instructions; `uvx --refresh-package tensor-grep --from tensor-grep==1.11.2 tg --version` reports `tensor-grep 1.11.2`
- `tg classify --format json` now exposes explicit provider provenance, including `classification_backend`, so harnesses can distinguish local deterministic classification from opt-in provider-backed classification
- release docs governance now records `v1.11.2` as both the latest tagged release and the latest complete public PyPI/release-asset distribution
- public GPU remains experimental: managed GPU requests still route through `GpuSidecar` / unsupported rather than public `NativeGpuBackend` evidence

What `v1.11.1` closed:

- PR #109 `fix: harden agent capsule hardcases` shipped the release as merge commit `6ad69b5 fix: harden agent capsule hardcases (#109)` and release commit `01a255e chore(release): v1.11.1 [skip ci]`
- main CI run `25836697091` passed semantic-release, `publish-github-release-assets`, `publish-pypi`, and `publish-success-gate`; CodeQL run `25836696835` passed
- GitHub release assets for `v1.11.1` include native CPU front doors, checksums, winget manifest, Homebrew formula, and publish instructions; `uvx --refresh-package tensor-grep --from tensor-grep==1.11.1 tg --version` reports `tensor-grep 1.11.1`
- agent capsule hardcases now cover noisy generated roots and ambiguous polyglot invoice tasks so implementation files outrank preview/mention files when validation evidence supports the implementation target
- release docs governance now distinguishes the incomplete `v1.11.0` publication from the complete `v1.11.1` public distribution
- public GPU remains experimental: managed GPU requests still route through `GpuSidecar` / unsupported rather than public `NativeGpuBackend` evidence

What `v1.11.0` tagged but did not complete:

- PR #107 `feat: add dogfood readiness verdict and checkpoint UX` shipped to main as merge commit `213d383 feat: add dogfood readiness verdict and checkpoint UX` and release commit `46b6486 chore(release): v1.11.0 [skip ci]`
- main CI run `25834508800` passed the pre-release matrix and semantic-release, but the release workflow was cancelled during release-native asset publication; `publish-success-gate` failed and `publish-github-release-assets` / `publish-pypi` did not complete
- the GitHub release record for `v1.11.0` exists without uploaded public assets, and PyPI latest remains `1.10.10`; do not treat `v1.11.0` as a complete public distribution
- PR #108 `fix: expose GPU promotion blockers` later merged as `9ddd20b fix: expose GPU promotion blockers`; its main CI exposed stale docs-governance release proof, which is tracked as a follow-up rather than a GPU or Python test failure

What `v1.10.10` closed:

- PR #105 `fix: add explicit Windows subprocess launcher repair` shipped the release as merge commit `dd995fc fix: add explicit Windows subprocess launcher repair` and release commit `5bc5749 chore(release): v1.10.10 [skip ci]`
- main CI run `25829350863` passed semantic-release, `publish-github-release-assets`, `publish-pypi`, and `publish-success-gate`; CodeQL run `25829350222` passed
- GitHub release assets for `v1.10.10` include native CPU front doors, checksums, winget manifest, Homebrew formula, and publish instructions; `uvx --refresh-package tensor-grep --from tensor-grep==1.10.10 tg --version` reports `tensor-grep 1.10.10`
- public dogfood verified the pinned managed installer, fresh `cmd /c tg --version`, fresh `pwsh -NoProfile -Command "tg --version"`, direct managed native `tg.exe`, Python `subprocess.run(["tg", "--version"])`, PyPI, `uvx`, and GitHub assets
- `tg repair-launcher --allow-foreign-rename` now gives operators an explicit Windows repair route when Python subprocess resolution is blocked by a foreign `tg.exe` they own; it backs up the foreign executable before installing the verified managed native front door into that PATH slot
- public GPU remains experimental: managed GPU requests still route through `GpuSidecar` / unsupported rather than public `NativeGpuBackend` evidence

What `v1.10.9` closed:

- PR #103 `fix: harden v1.10.8 release docs governance` shipped the release as merge commit `b0df720 fix: harden v1.10.8 release docs governance` and release commit `d3812b0 chore(release): v1.10.9 [skip ci]`
- main CI run `25800094003` passed semantic-release, `publish-github-release-assets`, `publish-pypi`, and `publish-success-gate`; CodeQL run `25800093062` passed
- GitHub release assets for `v1.10.9` include native CPU front doors, checksums, winget manifest, Homebrew formula, and publish instructions; `uvx --refresh-package tensor-grep --from tensor-grep==1.10.9 tg --version` reports `tensor-grep 1.10.9`
- public managed-upgrade dogfood verified `tg upgrade`, fresh `cmd /c tg --version`, fresh `pwsh -NoProfile -Command "tg --version"`, direct managed native `tg.exe`, PyPI, `uvx`, and GitHub assets
- release docs/governance now track the `v1.10.8` dogfood findings: `tg` remains positioned as agent-native code intelligence with rg-compatible search, public GPU remains `GpuSidecar` / unsupported rather than promoted, and Python subprocess launcher blockers are reported as foreign Machine PATH conflicts rather than tensor-grep-owned cleanup targets
- current follow-up from that release was closed in `v1.10.10` by the explicit `tg repair-launcher --allow-foreign-rename` path; foreign launchers are still never deleted without operator opt-in

What `v1.10.8` closed:

- PR #102 `fix: harden v1.10.7 dogfood followups` shipped the release as merge commit `6ee1d53 fix: harden v1.10.7 dogfood followups` and release commit `0074fd2 chore(release): v1.10.8 [skip ci]`
- main CI run `25796273366` passed semantic-release, `publish-github-release-assets`, `publish-pypi`, and `publish-success-gate`; CodeQL run `25796273431` passed
- GitHub release assets for `v1.10.8` include native CPU front doors, checksums, winget manifest, Homebrew formula, and publish instructions; `uvx --refresh-package tensor-grep --from tensor-grep==1.10.8 tg --version` reports `tensor-grep 1.10.8`
- public managed-upgrade dogfood verified `tg upgrade`, fresh `cmd /c tg --version`, fresh `pwsh -NoProfile -Command "tg --version"`, direct managed native `tg.exe`, PyPI, `uvx`, and GitHub assets
- public docs/governance now describe `tg` as agent-native code intelligence with rg-compatible search rather than a faster-grep replacement; GPU remains public-experimental and sidecar-routed `GpuSidecar` native GPU diagnostics report unsupported instead of false failures
- current follow-up: Python `subprocess.run(["tg", ...])` still requires a non-foreign `tg.exe` earlier in the effective Windows process PATH; `tg doctor --json` reports the foreign Machine PATH blocker with remediation and does not delete unrelated launchers

What `v1.10.7` closed:

- PR #101 `fix: harden gpu search accuracy contracts` shipped the release as merge commit `57f9ada` and release commit `f4aac39 chore(release): v1.10.7 [skip ci]`
- main CI run `25774742206` passed semantic-release, `publish-github-release-assets`, `publish-pypi`, and `publish-success-gate`; CodeQL run `25775170033` passed
- GitHub release assets for `v1.10.7` include native CPU front doors, checksums, winget manifest, Homebrew formula, and publish instructions; `uvx --refresh-package tensor-grep --from tensor-grep==1.10.7 tg --version` reports `tensor-grep 1.10.7`
- public managed-upgrade dogfood verified `tg upgrade`, fresh `cmd /c tg --version`, fresh `pwsh -NoProfile -Command "tg --version"`, direct managed native `tg.exe`, PyPI, and GitHub assets
- native GPU search now reports accurate JSON line numbers after blank lines, preserves smart-case and hidden/max-depth/text semantics through CPU/sidecar paths, and falls back from native GPU when requested semantics are not yet faithfully supported
- public GPU remains experimental because managed GPU requests still route through `GpuSidecar` instead of qualifying `NativeGpuBackend`
- current follow-up: Python `subprocess.run(["tg", ...])` still requires a non-foreign `tg.exe` earlier in the effective Windows process PATH; a same-directory `tg.com` bridge fixes shells but cannot fix extensionless `CreateProcess` when a foreign Machine PATH `.exe` wins

What `v1.10.6` closed:

- PR #100 `fix: harden v1.10.5 dogfood blockers` shipped the release as merge commit `7a8c9cf` and release commit `b8680e8 chore(release): v1.10.6 [skip ci]`
- main CI run `25762981815` passed semantic-release, `publish-github-release-assets`, `publish-pypi`, and `publish-success-gate`; CodeQL run `25762981305` passed
- GitHub release assets for `v1.10.6` include native CPU front doors, checksums, winget manifest, Homebrew formula, and publish instructions; `uvx --refresh-package tensor-grep --from tensor-grep==1.10.6 tg --version` reports `tensor-grep 1.10.6`
- public managed-upgrade dogfood verified `tg upgrade`, fresh `cmd /c tg --version`, fresh `pwsh -NoProfile -Command "tg --version"`, direct managed native `tg.exe`, PyPI, and GitHub assets
- ambiguous invoice-task routing now prefers the implementation file and requires confirmation for equal-confidence alternatives; broad generated-root refusal now catches the CWD-is-generated-root case
- public GPU remains experimental because managed GPU requests still route through `GpuSidecar` instead of qualifying `NativeGpuBackend`
- current follow-up: Python `subprocess.run(["tg", ...])` must be checked separately because Windows `CreateProcess` can resolve a foreign same-directory `tg.exe` even when shells prefer a tensor-grep `tg.com` bridge; context/session latency must stay guarded so weak fuzzy matches do not trigger expensive blast-radius work

What `v1.10.5` closed:

- PR #99 `fix: harden v1.10.4 dogfood followups` shipped the release as merge commit `03db0ff` and release commit `72bd57c chore(release): v1.10.5 [skip ci]`
- main CI run `25753248700` passed semantic-release, `publish-github-release-assets`, `publish-pypi`, and `publish-success-gate`; CodeQL run `25753247506` passed
- GitHub release assets for `v1.10.5` include native CPU front doors, checksums, winget manifest, Homebrew formula, and publish instructions; `uvx --refresh-package tensor-grep --from tensor-grep==1.10.5 tg --version` reports `tensor-grep 1.10.5`
- public managed-upgrade dogfood verified `tg upgrade`, fresh `cmd /c tg --version`, fresh `pwsh -NoProfile -Command "tg --version"`, and direct managed native `tg.exe`
- hot-query regex repeats now report native/Rust routing rather than Python fallback, while the public GPU story remains experimental because managed GPU requests still route through `GpuSidecar` instead of qualifying `NativeGpuBackend`

What `v1.10.0` closed:

- `tg agent --gpu-device-ids ... --json` and MCP `tg_agent_capsule(..., gpu_device_ids=[...])` now expose opt-in agentic GPU route evidence through `gpu_acceleration`
- sidecar-routed GPU evidence is reported as unsupported and never promoted as native CUDA acceleration
- main CI run `25670325770` passed semantic-release, `publish-github-release-assets`, `publish-pypi`, and `publish-success-gate`; CodeQL run `25670325881` passed
- GitHub release assets for `v1.10.0` include native CPU front doors, checksums, winget manifest, Homebrew formula, and publish instructions; `uvx --refresh --from tensor-grep==1.10.0 tg --version` reports `tensor-grep 1.10.0`
- post-release dogfood found that a copied tensor-grep `tg.com` bridge can pass `--version` while sidecar-backed public commands fall through to ambient Python; the active bridge follow-up fixes that launcher state

What `v1.9.11` closed:

- release wheel retry hardening now adds Cargo dependency prefetch with retry/timeout settings before PyPI wheel and sdist builds, so transient crates.io DNS failures are retried before maturin runs
- `v1.9.11` published Linux, macOS, and Windows wheels plus the sdist to PyPI; `uvx --from tensor-grep==1.9.11 tg --version` reports `tensor-grep 1.9.11`
- main CI run `25647256985` passed semantic-release, `validate-pypi-artifacts`, `publish-github-release-assets`, `publish-pypi`, and `publish-success-gate`

What `v1.9.10` closed:

- dogfood follow-ups now cap capsule alternative target confidence at the selected primary target confidence
- `secrets-basic` scans now include regex-backed generic provider tokens such as `sk_live...` in Python, JavaScript, TypeScript, and Rust files
- stale v1.9.9 docs-governance text was synchronized to the release line; GitHub native CPU assets were published for `v1.9.10`, while PyPI publication failed because a transient crates.io DNS failure cancelled the wheel matrix. `v1.9.11` closes that release-wheel retry follow-up.

What `v1.9.9` closed:

- agent workflow benchmark governance now separates capsule/edit-loop evidence from raw cold exact-text speed claims
- `run_agent_workflow_benchmarks.py` records capsule confidence, alternatives, validation alignment, snippets, rollback, and edit-loop phase timings as workflow evidence
- public `v1.9.9` proof published native CPU assets and PyPI distributions; `uvx --from tensor-grep==1.9.9 tg --version` reports `tensor-grep 1.9.9`

What `v1.9.8` closed:

- `tg update` now refreshes stale tensor-grep-owned `tg.com` PATH bridges after the managed sidecar/native front door reaches a new version, while leaving unrelated foreign `tg.exe` launchers untouched
- Windows `PATHEXT` and registry PATH parsing use Windows semicolon semantics even in POSIX-hosted Windows simulations, keeping `.COM` bridge detection covered on Linux/macOS CI
- Public `v1.9.8` dogfood moved the managed sidecar and native front door from `1.9.7` to `1.9.8`; after invoking the released `1.9.8` updater, fresh `cmd` and unprofiled `pwsh` report `tg 1.9.8`

What `v1.9.7` closed:

- Python GPU scale rows are unsupported for native CUDA promotion, and benchmark artifacts now separate them from native CUDA rows where correctness passed but speed/promotion failed
- Native CUDA correctness passed on the local 1GB/5GB RTX 4070 and RTX 5070 dogfood gates. Single-pattern cold grep still has no crossover, but post-`v1.13.6` local CUDA-native measurements show 100 fixed-string 1GB probes beating sequential `rg` end-to-end.
- public benchmark and README positioning now say `tg` is an agent-native code intelligence layer with rg-compatible search; `rg` remains the cold exact text baseline

What `v1.9.6` closed:

- directory-level rewrite validation now expands `$file` / `{file}` placeholders once per edited file, while placeholder-free validation still runs once in the original target working directory
- NVIDIA install and release validation paths use CUDA 12.8 / `cu128` so RTX 5070 / Blackwell sidecar routes are compatible; AMD Windows stays on the CPU fallback path while Linux ROCm remains explicit
- root `tg --help` surfaces the current agent capsule, validation placeholder, generated-root guardrail, GPU experimental, classify-provider, launcher, and environment-override contracts
- `tg doctor --json` sanitizes sidecar-specific environment variables while probing PATH candidates and classifies first-PATH `tg` commands from unrelated tools as `foreign`, with explicit remediation instead of deleting or overwriting unrelated launchers
- native CUDA dogfood passes exact match/file-set correctness and 1GB/5GB scale correctness on both RTX 4070 (`sm_89`) and RTX 5070 (`sm_120`), but GPU remains slower than `rg` and `tg_cpu`, so sidecar/native GPU evidence stays experimental and out of speed marketing

What `v1.9.5` closed:

- GPU native gate attribution now distinguishes real CUDA-enabled native rows from Python/Torch sidecar routing, so sidecar work cannot be counted as native CUDA scale proof
- ambiguous `tg agent` capsules expose `alternative_targets` so cross-language candidates remain visible when the primary target is only one ranked choice
- root help diagnostics and `tg doctor --json` foreign-launcher reporting make first-PATH `tg` shadowing explicit for agent-readiness and Windows dogfood

What `v1.9.4` closed:

- docs-governance tests now track the current project release tag instead of pinning stale v1.9.2 language
- edit validation commands substitute `$file` and `{file}` placeholders with each edited file path before execution; placeholder-free validation commands still run once in the original target working directory
- quoted validation placeholders work on Windows paths with spaces; quote placeholders in docs and examples when paths may contain spaces

What `v1.9.3` closed:

- explicit language/file-name intent now routes `python invoice tax calculation` to `src/payments.py:create_invoice` with aligned Python validation evidence
- quoted Windows validation commands are parsed argv-safely instead of treating quotes as literal filename characters
- docs/readiness hygiene was brought green for the 1.9.3 release line

What `v1.9.2` closed:

- edit-mode `--diff --json` and `--apply --json` now emit parseable JSON-only stdout for agent use
- failed edit validation rolls changed files back and reports structured rollback metadata
- capsule validation trust no longer downgrades or asks before editing when mismatched commands were filtered but aligned validation remains

What `v1.9.1` closed:

- mixed-language `tg agent` capsules now cap confidence, expose `validation_alignment`, and require ask-before-editing when query language hints, exact symbol intent, primary target language, snippets, and validation commands disagree
- validation commands are filtered to stay compatible with the selected primary target language unless cross-language dependency evidence exists
- GPU benchmark auto-recommendation remains false unless required 1GB/5GB correctness checks pass and the selected GPU beats both `rg` and `tg_cpu` at the required scale and declared workload class; unrelated unsupported-device inventory warnings stay off selected-GPU timing rows

What `v1.9.0` closed:

- `tg agent --query ... --json` is released as the first Actionable Context Capsule surface for agent workflows, with primary target metadata, route rationale, bounded snippets with line maps, validation evidence, edit order, rollback/checkpoint metadata, omissions, confidence, and ask-before-editing recommendations
- the native front door sidecar-routes `tg agent` intentionally so public installs expose the same capsule contract as the Python CLI
- stable managed install scripts now prefer the matching release-native CPU `tg` front door when the GitHub release asset exists, while keeping the managed Python environment as the sidecar/fallback for Python-backed commands
- main CI builds, uploads, and verifies release-native CPU assets before PyPI publish, so `v1.9.0` includes `tg-windows-amd64-cpu.exe`, `tg-linux-amd64-cpu`, `tg-macos-amd64-cpu`, checksums, and package-manager bundle assets on the GitHub release
- stable installers clear stale `tensor-grep` package metadata, request the exact current non-yanked PyPI version when known, check native installer exit codes, and stage the managed environment plus front-door files before replacing `~/.tensor-grep`
- `tg upgrade` skips yanked PyPI releases, refreshes stale package metadata, verifies that the target Python can still import `tensor_grep`, refreshes the managed release-native front door to the verified sidecar version, and schedules a Windows retry helper when the running native `tg.exe` is still locked
- Windows `tg.com` bridge copies that outrank a foreign same-directory `tg.exe` must still resolve sidecar-backed commands through `~/.tensor-grep/.venv` and must point Python back at the managed native front door, so public `tg doctor` and `tg upgrade` are validated in addition to `tg --version`
- Windows managed installs put `~/.tensor-grep/bin` ahead of compatibility shim directories on User PATH so fresh `cmd` and unprofiled PowerShell resolve the native `tg.exe` first; Python `subprocess.run(["tg", ...])` still needs a non-foreign `.exe` earlier in the effective process PATH when Machine PATH contains another `tg.exe`
- `tg doctor --json` reports current-process and fresh-shell launcher route kinds, including `path_tg_first_launcher_kind`, `fresh_shell_path_tg_first_launcher_kind`, and `path_tg_launcher_warning`, so benchmark runs can distinguish managed native execution from compatibility shim timing
- cold-path benchmark artifacts record both configured `tg_launcher_mode` and actual `tg_launcher_command_kind`, keeping native-exe, `.cmd` shim, `uv`, and Python-module timings separate; benchmark scripts warn when the timed `tg` entrypoint is not the native executable
- explicit `--gpu-device-ids` routes probe only the selected CUDA ordinals, so choosing GPU 0 does not initialize or warn about unrelated unsupported GPUs
- the Rust front door treats `--format rg` as a no-op for ripgrep-compatible text output and preserves `--sort path` passthrough for deterministic automation
- the Rust front door accepts or intentionally routes the advertised public shapes `tg search --files`, `tg search --multiline` / `-U`, `tg search --null`, `tg run -r`, and `tg classify --format json`
- the Windows `.cmd` launcher preserves quoted multi-word no-match patterns from `cmd.exe`, direct `tg.cmd`, and Python `subprocess.run([...])` instead of splitting the phrase into a false-positive shorter search plus bogus paths
- `classify` is deterministic and local by default unless `TENSOR_GREP_CLASSIFY_PROVIDER=cybert` explicitly opts into CyBERT/Triton, and the GPU benchmark harness treats no-match as a valid comparator outcome
- `context-render` and `edit-plan` JSON both expose top-level `validation_commands` so agents do not need command-specific fallback parsing
- GPU benchmark defaults now include 1GB and 5GB scale rows and exact correctness checks for every >=1GB GPU corpus before any GPU promotion claim; explicit `--gpu-device-ids` routes should only initialize selected devices
- stale in-tree standalone native binaries remain skipped by default unless explicitly pinned with `TG_NATIVE_TG_BINARY`
- deterministic rg parity edges, context-render trust invariants, session stale-file handling, validation-command provenance, inline rule metadata, uppercase `API_KEY` secret detection, and broad generated-root refusal remain part of the accepted compatibility line

Active post-`v1.13.6` follow-up:

- harden the `v1.12.33` dogfood edge cases without broadening claims: native/root search accepts the rg config-override sequence `--column --no-column`, readiness now reports a stale repo-local `uv run tg` warmup as an unsynchronized entrypoint with a refresh command, and the ripgrep-binary-resolution natural-language hardcase stays pinned as a capsule regression test
- harden `v1.12.15` dogfood contract gaps: public native `tg search` must accept editor-facing `--vimgrep` and `--path-separator`, native-regex hot-query gates use absolute jitter for millisecond-scale rows, benchmark scripts refuse stale in-tree native binaries by default unless `--allow-claim-unsafe-launcher` marks the run as exploratory, and optional LSP provider routes must expose `lsp_proof` / fallback status instead of treating provider availability as proof of semantic navigation
- continue hardening `tg agent` / Actionable Context Capsule ranking for ambiguous multi-language queries, token economy, follow-up reads, call-site evidence, and validation evidence as an opt-in agent workflow, not a replacement for raw search output
- keep Python `subprocess.run(["tg", ...])` in readiness: `tg upgrade` should repair Windows User/current PATH when that can put the managed native `tg.exe` ahead of foreign same-name launchers, and should report a Machine PATH blocker when an unrelated foreign `tg.exe` still wins without deleting unrelated tools; `tg repair-launcher --allow-foreign-rename` is the explicit operator-approved path that backs up a foreign `tg.exe` before installing the managed native front door into that slot
- keep context/session latency guarded: direct validation evidence should reuse repo-map imports, and weak fuzzy symbols should not trigger expensive blast-radius work unless the target is explicit or sufficiently supported
- keep session edit-plan warm paths bounded: cached graph ranking caps broad query seeds, test matching only scans sources inside the requested file budget, edit-plan blast-radius work uses a small selected-context repo map with scope metadata, and daemon edit-plan requests use a longer response timeout than the fast connect probe
- agents must inspect top-level `ambiguity` before editing; `ambiguity.status = "tie_requires_confirmation"` is a hard stop for autonomous edits, and `tie_resolved` is acceptable only when `resolved_by` contains explicit evidence
- keep edit validation command parsing and `$file` / `{file}` placeholder substitution argv-safe for quoted Windows paths with spaces
- preserve the mixed-language capsule trust contract: explicit language intent, exact symbol intent, primary target language, `validation_alignment`, and `ask_user_before_editing` must agree or confidence must drop
- the capsule output is a deterministic work packet: primary file/function, route rationale, bounded snippets with line maps, validation evidence, risk, suggested edit order, checkpoint/rollback metadata, omission counts, confidence, call-site evidence status, and an "ask user before editing" recommendation when warranted. Capsule v1 leaves `related_call_sites` empty unless verified call-site evidence is explicitly collected.
- keep token economy explicit with hard budgets, grouped excerpts, truncation metadata, omitted section counts, and follow-up read commands so agents can recover detail without polluting the first response
- keep AST feature parity, GPU correctness/speed, classify provider/cache UX, and context/session performance tracked as blockers for a future "world-class one-tool" claim
- keep launcher-route diagnostics in `tg doctor --json` visible in dogfood before trusting Windows benchmark results
- validate Windows public launchers with sidecar-backed commands (`tg doctor --json`, `tg upgrade`) and Python `subprocess.run(["tg", ...])` as well as shell `tg --version`; version-only checks can miss a copied `tg.com` bridge that falls through to the wrong ambient Python or a foreign `.exe` route
- keep both `tg_launcher_mode` and `tg_launcher_command_kind` in cold benchmark artifacts so native-exe, `.cmd` shim, `uv`, and Python-module timings are not combined into one search-speed claim; stale in-tree native binaries must block benchmark-claim runs by default, and shim/interpreter warnings remain blocking evidence for performance comparisons
- keep GPU benchmark auto-recommendation disabled unless required 1GB/5GB correctness passes and a selected GPU beats both `rg` and `tg_cpu` at that required scale. Unsupported-device inventory warnings must stay top-level or on the unsupported device row, not on unrelated selected-GPU timings. Sidecar-routed GPU requests must be recorded and excluded from native CUDA scale-gate timings.
- keep `tg doctor --json` foreign-launcher diagnostics explicit. A foreign `tg.exe` such as another product's console launcher ahead of `~/.tensor-grep/bin` should produce `*_is_foreign`, warning, and remediation fields; this is an environment blocker, not an installer cleanup target unless tensor-grep owns that launcher.
- keep GPU experimental until the required 1GB/5GB correctness rows pass and a selected GPU beats both `rg` and `tg_cpu`; current RTX 4070/RTX 5070 smoke proof is correctness/compatibility evidence, not a speed claim
- post-`v1.13.6` dogfood keeps the near-term performance story on large-file CPU search, not GPU: public managed GPU remains `GpuSidecar` / unsupported, while CPU large-file rows are the strongest current win signal

Managed native-upgrade dogfood:

- direct managed native `C:\Users\oimir\.tensor-grep\bin\tg.exe --version` reports `tg 1.10.9` after `tg upgrade`
- PyPI pinned public install resolves `tensor-grep==1.10.9`
- `tg doctor --json` classifies unrelated first-PATH or Python-subprocess Together CLI `tg.exe` launchers as `foreign` with explicit remediation; where a tensor-grep `tg.com` bridge is needed to outrank that same-directory `.exe`, public dogfood must verify sidecar-backed commands and Python subprocess resolution as well as version output; if the operator owns the foreign command, `tg repair-launcher --allow-foreign-rename` backs it up before replacing the PATH slot with the managed native `tg.exe`

- `tg update` from `v1.9.3` initially saw PyPI propagation lag, then installed sidecar `tensor-grep==1.9.4` and refreshed the managed native front door to `tg 1.9.4`
- `tg doctor --json` now reports `version = 1.9.4`, `rust_binary_version_status = matches`, `search_acceleration_backend = standalone-native-tg`, `path_tg_first_launcher_kind = cmd-shim`, `fresh_shell_path_tg_first_launcher_kind = managed-native`, and a `path_tg_launcher_warning` when the current process still sees the slower shim route
- profiled PowerShell, `cmd`, `pwsh -NoProfile`, WSL, Git Bash, and direct managed native `tg.exe` all resolve `tg 1.9.4`
- prior `scripts/install.ps1` dogfood for `v1.8.31` updated User PATH so fresh shells resolve `C:\Users\oimir\.tensor-grep\bin\tg.exe` before compatibility shim directories
- this is installer correctness and release-readiness work; benchmark docs should not claim a cold-search speed win from it

Release proof:

- PR #101 from `57f9ada fix: harden gpu search accuracy contracts` merged and released
- release commit `f4aac39 chore(release): v1.10.7 [skip ci]`
- main CI run `25774742206` passed the pre-release test/benchmark matrix, semantic-release, `publish-github-release-assets`, `publish-pypi`, and `publish-success-gate`
- main CodeQL run `25775170033` passed on the v1.10.7 release line
- GitHub release assets for `v1.10.7` include native CPU front doors, checksums, winget manifest, Homebrew formula, and publish instructions
- PyPI package-specific release metadata lists all `tensor-grep 1.10.7` distributions; `tensor-grep==1.10.7` resolves from PyPI
- PR #100 from `7a8c9cf fix: harden v1.10.5 dogfood blockers` merged and released
- release commit `b8680e8 chore(release): v1.10.6 [skip ci]`
- main CI run `25762981815` passed the pre-release test/benchmark matrix, semantic-release, `publish-github-release-assets`, `publish-pypi`, and `publish-success-gate`
- main CodeQL run `25762981305` passed on the v1.10.6 release line
- GitHub release assets for `v1.10.6` include native CPU front doors, checksums, winget manifest, Homebrew formula, and publish instructions
- PyPI package-specific release metadata lists all `tensor-grep 1.10.6` distributions; `tensor-grep==1.10.6` resolves from PyPI
- PR #99 from `03db0ff fix: harden v1.10.4 dogfood followups` merged and released
- release commit `72bd57c chore(release): v1.10.5 [skip ci]`
- main CI run `25753248700` passed the pre-release test/benchmark matrix, semantic-release, `publish-github-release-assets`, `publish-pypi`, and `publish-success-gate`
- main CodeQL run `25753247506` passed on the v1.10.5 release line
- GitHub release assets for `v1.10.5` include native CPU front doors, checksums, winget manifest, Homebrew formula, and publish instructions
- PyPI package-specific release metadata lists all `tensor-grep 1.10.5` distributions; `tensor-grep==1.10.5` resolves from PyPI
- PR #93 from `34fd556 feat: add agentic GPU evidence capsule` merged and released
- Prior PR #91 from `8aecfea fix: harden release wheel retries` merged and released as `v1.9.11`
- release commit `0d0cbaa chore(release): v1.10.0 [skip ci]`
- main CI run `25670325770` passed the pre-release test/benchmark matrix, semantic-release, `publish-github-release-assets`, `publish-pypi`, and `publish-success-gate`
- main CodeQL run `25670325881` passed on the v1.10.0 release line
- GitHub release assets for `v1.10.0` include native CPU front doors, checksums, winget manifest, Homebrew formula, and publish instructions
- PyPI package-specific release metadata lists all `tensor-grep 1.10.0` distributions; `tensor-grep==1.10.0` resolves from PyPI
- PR #90 from `ca9df12 fix: harden v1.9.9 dogfood followups` merged and released
- release commit `6d04ad2 chore(release): v1.9.10 [skip ci]`
- main CI run `25645819170` passed the pre-release test/benchmark matrix, semantic-release, and `publish-github-release-assets`; PyPI publish was blocked by a transient crates.io DNS failure in the macOS wheel build and is being retried through the release-wheel retry follow-up
- main CodeQL run `25646156907` passed on the v1.9.10 release commit
- GitHub release assets for `v1.9.10` include native CPU front doors, checksums, winget manifest, Homebrew formula, and publish instructions
- PR #89 from `21449bf fix: add agent workflow benchmark governance` merged and released
- release commit `efa83e2 chore(release): v1.9.9 [skip ci]`
- main CI run `25643115892` passed semantic-release, `validate-pypi-artifacts`, `publish-github-release-assets`, `publish-pypi`, and `publish-success-gate`
- main CodeQL run `25643115694` passed
- GitHub release assets for `v1.9.9` include native CPU front doors, checksums, winget manifest, Homebrew formula, and publish instructions
- PyPI reports `tensor-grep 1.9.9`; `tensor-grep==1.9.9` resolves from PyPI
- PR #86 from `4ff7a77 fix: clarify GPU benchmark promotion gates` merged and released as `v1.9.7`
- PR #84 from `05ea29e fix: harden v1.9.5 dogfood blockers` merged and released as `v1.9.6`
- PR #82 merged and released from `646b089 fix: harden docs governance and validation placeholders`
- release commit `adde778 chore(release): v1.9.4 [skip ci]`
- main CI run `25614464124` passed semantic-release, `validate-pypi-artifacts`, `publish-github-release-assets`, `publish-pypi`, and `publish-success-gate`
- main CodeQL run `25614464010` and release-commit CodeQL run `25614702928` passed
- GitHub release assets for `v1.9.4` include native CPU front doors, checksums, winget manifest, Homebrew formula, and publish instructions
- PyPI reports `tensor-grep 1.9.4`; `tensor-grep==1.9.4` resolves from PyPI
- PR #81 merged and released from `73c5f91 fix: harden agent ranking docs and validation quoting`
- PR #80 merged and released from `faf67ed fix: harden edit JSON and capsule validation trust`
- release commit `8143ccb chore(release): v1.9.2 [skip ci]`
- main CI run `25609611007` passed semantic-release, `validate-pypi-artifacts`, `publish-github-release-assets`, `publish-pypi`, and `publish-success-gate`
- main CodeQL run `25609610737` passed
- GitHub release assets for `v1.9.2` include native CPU front doors, checksums, winget manifest, Homebrew formula, and publish instructions
- PyPI reports `tensor-grep 1.9.2`; `tensor-grep==1.9.2` resolves from PyPI
- PR #78 merged and released from `5791489 fix: harden agent capsule trust alignment`
- release commit `8f226ba chore(release): v1.9.1 [skip ci]`
- PR #76 merged and released from `95bfd81 feat: add actionable agent context capsule`
- release commit `19c7295 chore(release): v1.9.0 [skip ci]`
- main CI run `25601232312` passed semantic-release, `validate-pypi-artifacts`, `publish-github-release-assets`, `publish-pypi`, and `publish-success-gate`
- main CodeQL run `25601232120` passed on the release-bearing merge commit
- GitHub release assets for `v1.9.0` include native CPU front doors, checksums, winget manifest, Homebrew formula, and publish instructions
- PyPI reports `tensor-grep 1.9.0`; `tensor-grep==1.9.0` resolves from PyPI
- Public `tg agent src/tensor_grep/cli --query "agent context capsule" --json` returned a capsule with primary target, snippets, omissions/follow-up reads, confidence, rollback, and ask-before-editing metadata
- Public launcher dogfood verified `cmd /c tg`, direct `tg.cmd`, native `tg.exe`, and Python `subprocess.run([...])` return exit `1` with no stdout for a fresh quoted no-match phrase
- Public `tg classify --format json tests\conftest.py` completed in 0.206s with local deterministic classifications

## Stable Windows Test Confirmation

On this Windows host, the most reliable repo-wide confirmation path is the file-backed pytest runner:

```powershell
uv run python scripts/run_pytest_stable.py --log artifacts/pytest_full.log --report artifacts/pytest_full_report.json
```

Why this exists:

- raw long-running `uv run pytest -q` sessions can be noisy or ambiguous under Windows process/capture behavior
- the stable runner uses `--capture=tee-sys`, `console_output_style=classic`, and `faulthandler_timeout`
- it writes both a human-readable log and a machine-readable report artifact

Current accepted full-suite artifact:

- [`artifacts/pytest_full_report.json`](artifacts/pytest_full_report.json)

## Fast Agent Readiness Gate

Before pushing agent-facing changes, run the fast dogfood gate:

```powershell
python scripts/agent_readiness.py --output artifacts/agent_readiness.json
tg dogfood --output artifacts/dogfood_readiness.json
```

This checks the current `v1.13.6` shell/version resolution, `public-windows-launcher-quoted-patterns`, installed-public advertised search flag acceptance via `public-search-advertised-flag-sweep`, repo doctor sanity, foreign launcher diagnostics, `context_consistency`, `agent-capsule`, `agent-capsule-mixed-language`, `agent-capsule-hardcases`, deterministic rg edge parity, AST smoke, MCP context-render smoke, docs claim hygiene, and the current positioning: `rg` remains the cold exact-text baseline, `ast-grep` remains the structural-search feature/performance baseline, and `tg` is the agent-native orchestration layer. `tg dogfood` wraps the same gate with a release-readiness verdict for agent and CI logs.
It also tracks the managed native-upgrade contract so sidecar and release-native front-door versions stay aligned after `tg upgrade`.
It also covers the broad generated-root scan and workspace-root scan guard: unbounded `tg search --files` roots that combine hidden/no-ignore-style scanning with generated, cache, or dependency directories, and unbounded searches against a parent containing multiple child project roots, must be scoped, bounded, or explicitly opted in with `--allow-broad-generated-scan`.

## Bounded Heavy-Root AI Handoff

For large internal-library roots, `tensor-grep` supports a bounded context-render path that keeps the AI handoff compact and actionable without letting symbol navigation escape the capped repo-map universe.

Agent-facing broad-scan commands now default to bounded repo-map scans and report that boundary in JSON via `scan_limit`:

```powershell
tg context-render . --query "how auth routing works" --render-profile llm --max-repo-files 512 --json
tg defs . --symbol runCursorWorker --max-repo-files 512 --json
tg source . --symbol safeParseJSON --max-repo-files 512 --json
tg refs . --symbol prepareCursorWorkerInvocation --max-repo-files 512 --json
tg blast-radius . --symbol prepareCursorWorkerInvocation --max-repo-files 512 --json
```

Current accepted production proof:

- [`artifacts/external_validation/agent_studio_patch_driver_validation_summary_capped.json`](artifacts/external_validation/agent_studio_patch_driver_validation_summary_capped.json)
- `v1.9.6` release state and managed-native upgrade verification are summarized in [Current Release State](#current-release-state)
- blast-radius boundedness artifact: `artifacts/bench_blast_radius_benchmarks_v188_prefilter.json`

What the bounded path preserves:

- compact primary target selection
- `navigation_pack`
- phased read groups
- repo-level validation command

What is now contract-tested:

- `include_edit_plan_seed=False` keeps the fast lightweight path
- `include_edit_plan_seed=True` returns full `edit_plan_seed`, `candidate_edit_targets`, and `navigation_pack` while honoring `max_repo_files`
- broad `context-render`, `defs`, `source`, `refs`, `callers`, `impact`, and blast-radius CLI commands expose `--max-repo-files`
- Use scoped paths, globs, file types, and `--max-depth` for `tg search`; `--max-repo-files`, `--max-callers`, and `--max-files` are code-intelligence command budgets, not `tg search` flags.
- no-match symbol lookups return compact `no_match` payloads instead of dumping unrelated repo inventories
- CommonJS exported functions in `.cjs` files are discoverable by `map`, `defs`, `source`, `refs`, and context-render ranking
- repo fallback validation prefers package-manager scripts such as `pnpm test` over guessed `npx jest` when a real `package.json` test script exists
- `node --test` package scripts and `node:test` test files emit targeted file-level validation commands before the broader package-manager fallback
- broad blast-radius scans sample source/test buckets before miscellaneous root noise so capped `.` runs are less likely to miss real code
- `context-render --json` defaults to the LLM compact profile; explicit `--render-profile llm` / `compact` omit full inventories and raw source duplication while preserving rendered source, `navigation_pack`, and validation commands
- raw `blast-radius` output defaults to a 25-caller / 25-file agent budget and accepts `--max-callers` / `--max-files` for broader analysis, including capped per-file symbol summaries and total/returned/omitted counts
- capped blast-radius no-matches can seed literal symbol files outside the initial scan cap through a bounded scan before returning a compact no-match
- high-cap blast-radius caller scans now skip files that cannot contain the target symbol literal before running language-specific caller extraction, while preserving import/use evidence for alias/default-import/re-export callers
- both the CPU fallback and Rust extension backend skip binary blobs unless `-a/--text` or `--binary` explicitly opts in, avoiding `.pyc`/bytecode dumps in agent JSON
- `tg search PATTERN` defaults to the current directory, `--json` no-match emits a valid empty envelope, invalid regex exits distinctly from no-match before native delegation/scanning, and editor-facing flags such as `--column`, `--vimgrep`, and `--path-separator` are forwarded or formatted consistently
- generated `.claude/context` snapshots and common build/cache directories are skipped by default during Python fallback scans; rust-first bootstrap also falls back to the Python guardrail path for broad `.claude` roots, and `--no-ignore` should only be used when those generated files are the explicit target
- `tg search --type-list` has a built-in fallback when neither ripgrep nor a standalone native binary is available; `--pcre2-version` follows ripgrep and returns an error when no PCRE2-capable backend is available
- stale in-tree standalone binaries under `rust_core/target/*/tg(.exe)` are ignored for implicit native delegation unless `TG_NATIVE_TG_BINARY` pins one explicitly; `tg doctor --json` reports skipped stale candidates for contributor safety
- `tg search --format rg` is the public exact ripgrep-style text formatter; use `--sort path --format rg` when automation needs deterministic rg-shaped stdout

Use this when you need a fast planner-to-executor handoff on broad roots before paying for deeper planning.

[![CI Status](https://github.com/oimiragieo/tensor-grep/actions/workflows/ci.yml/badge.svg)](https://github.com/oimiragieo/tensor-grep/actions)
[![PyPI version](https://badge.fury.io/py/tensor-grep.svg)](https://pypi.org/project/tensor-grep/)

Dual-licensed under MIT or the UNLICENSE.

### CHANGELOG
Please see the [CHANGELOG.md](CHANGELOG.md) for a release history.

## Benchmark Snapshot

The canonical benchmark matrix lives in [docs/benchmarks.md](docs/benchmarks.md). One benchmark is never enough. The public comparison summary lives in [docs/tool_comparison.md](docs/tool_comparison.md), and the tables below are the current host-local snapshot on this Windows machine, not a universal claim.

Current quick tool comparison:

- artifact: [`artifacts/bench_tool_comparison.json`](artifacts/bench_tool_comparison.json)
- script: `uv run python benchmarks/run_tool_comparison_benchmarks.py --output artifacts/bench_tool_comparison.json`

| Scenario | ripgrep | `tg search` | `tg search --cpu` | `git grep --no-index` |
| --- | --- | --- | --- | --- |
| standard corpus | `0.227s` | `0.288s` | `0.288s` | `0.278s` |
| 200MB large file | `0.221s` | `0.220s` | `0.220s` | `0.232s` |

Current read:

- `rg` remains the cold generic text-search baseline
- the 2026-04-29 `v1.6.5` cold-path rerun preserved parity on all 10 rows, but `benchmarks/check_regression.py --baseline auto` failed because the `rg` comparator drifted and the case-insensitive `tg` row was 8.93% slower than the frozen Windows baseline
- cold-path attribution now confirms benchmark claims should use the explicit repo native binary; shell-discovered `tg` can be stale and is treated as environment-drift evidence
- dev-path native resolution now ignores stale implicit in-tree binaries; use `TG_NATIVE_TG_BINARY` only when intentionally pinning a standalone binary for benchmark-controlled runs
- `tg search` is effectively tied with `rg` on the 200MB row in the latest host-local comparison, while `rg` still wins the standard-corpus row
- host-local peer rows currently include `rg` and `git grep --no-index`; `ag`, `ack`, `ugrep`, and `grep` are omitted on this host because they are not installed
- native AST search, AST rewrite, repeated-query acceleration, and GPU are separate benchmark surfaces and should not be conflated with cold plain-text search

Current repeated-query snapshot:

- artifact: [`artifacts/bench_hot_query_benchmarks.json`](artifacts/bench_hot_query_benchmarks.json)
- repeated fixed string: `0.5671s -> 0.1470s`
- repeated regex prefilter: `0.5476s -> 0.1662s`
- both rows now include fresh-process overhead
- local benchmark note: run `uv run --extra bench python benchmarks/run_hot_query_benchmarks.py` for the fully provisioned path; without the benchmark extras, the fixed-string row records `SKIP` with an install hint instead of crashing

Current AI handoff comparison snapshot:

- artifact: [`artifacts/external_validation/external_agent_patch_driver_scorecard.json`](artifacts/external_validation/external_agent_patch_driver_scorecard.json)
- mean compactness score: `1.0`
- mean validation-fit score: `1.0`
- mean parallel-read reduction score: `0.916667`
- mean overall score: `0.972222`
- current read-group heuristic: same-directory related/test reads are prefetched into the primary phase when they stay local to the edit slice

Current repo-map lexical retrieval snapshot:

- baseline artifact: `artifacts/bench_repo_retrieval_lexical_base.json`
- accepted feature artifact: `artifacts/bench_repo_retrieval_lexical_feature.json`
- curated retrieval line moved from `recall_at_5 = 0.0`, `mrr_at_5 = 0.0`, `ndcg_at_5 = 0.0` on clean `origin/main` to `recall_at_5 = 1.0`, `mrr_at_5 = 1.0`, `ndcg_at_5 = 1.0`, `file_f1 = 0.333333`, `line_f1 = 0.222222`
- default smoke benchmark artifact: `artifacts/bench_repo_retrieval_benchmarks.json`; the committed dataset at `benchmarks/datasets/repo_retrieval_eval.jsonl` keeps repo-retrieval benchmarking runnable without local-only fixtures
- latest default smoke metrics: `recall_at_5 = 1.0`, `precision_at_5 = 0.333333`, `mrr_at_5 = 1.0`, `ndcg_at_5 = 1.0`, `file_f1 = 0.492064`, `line_f1 = 0.492064`
- current read: camelCase-to-snake_case symbol bridging and source-term fallback now recover the right planning file on the curated repo-map pack, while `context-render` and blast-radius remain in the same measured editor-plane band on this host instead of becoming a new cold-path speed claim

Current benchmark-governed strengths:

- native CPU benchmark line: with rg fallback disabled for native measurement, `tg --cpu` wins all four current native CPU rows, including `large_file_200mb_count` (`0.072s` vs `0.417s`) and `many_file_directory` (`0.159s` vs `0.236s`) in [`artifacts/bench_run_native_cpu_benchmarks.json`](artifacts/bench_run_native_cpu_benchmarks.json)
- native AST search beats `sg` on the current AST search surfaces in [docs/benchmarks.md](docs/benchmarks.md)
- AST rewrite remains functional and the one-shot apply path is under the `sg` ratio gate on the current local benchmark (`0.865x` in `artifacts/bench_ast_rewrite_post_v170_audit.json`)
- repeated-query acceleration remains the strongest warm-path win on unchanged corpora

Current CLI correctness line:

- plain-text and `--json` invocations now share the same routed command surface for `doctor`, `map`, `session`, `checkpoint`, `rulesets`, `context-render`, `edit-plan`, and the blast-radius family
- agent-navigation commands now favor reliable compact output over maximal inventory dumps: bounded scan metadata, compact no-match responses, deduped references, and package-script validation commands are covered by unit and MCP-adjacent checks
- release validation must include `uv run ruff format --check --preview .`; CI can fail preview-format drift even when `uv run ruff check .` passes
- after semantic-release publishes, fetch tags/main and fast-forward local `main` before checking version files, because the release commit is created after the fix commit
- `tg search --replace` rewrites emitted match text in ripgrep style without mutating files
- `tg search -o` now mirrors ripgrep single-file output formatting instead of forcing `file:line:text`
- `tg run --json` emits structured output even without `--apply`
- `tg search --files-with-matches` stays root-based and rg-compatible on the ripgrep path instead of expanding large Windows argv lists, and `tg ast-info --json` exposes AST language identifiers for agents

Important constraint:

- do not treat internal GPU pipeline throughput as the same thing as end-to-end CLI crossover
- current GPU routing decisions should follow [docs/gpu_crossover.md](docs/gpu_crossover.md), not isolated microbenchmarks
- broad `tg search --files ...` over generated artifact trees can still be expensive; current Windows launchers and Python path-list output force UTF-8, but scope file-list commands to the smallest useful root

## Product Contracts

`tensor-grep` enforces strict behavioral and output contracts to ensure reliable execution for both human users and AI agent harnesses.

- **ripgrep-Compatible Search Contract:** The current stable text-search contract is the validated compatibility set covered by the parity suite and contract benchmark runner, plus tensor-grep's documented `--ndjson` streaming extension. The rows currently covered are `-i/--ignore-case`, `-v/--invert-match`, `-C/--context`, `-A/--after-context`, `-B/--before-context`, `-g/--glob`, `-l/--files-with-matches`, `--files-without-match`, tensor-grep aggregate `--json`, explicit ripgrep JSON Lines via `--format rg --json`, `--ndjson`, `-F/--fixed-strings`, `-w/--word-regexp`, `-m/--max-count` including `--max-count=N`, `-t/--type`, `-./--hidden`, `-L/--follow`, `-S/--smart-case`, `-n/--line-number`, `--column`, `-c/--count`, `--count-matches`, and `-a/--text`. Additional rg-style flags may be exposed in `tg search --help`, but they are not part of the benchmarked compatibility claim until they are added to the contract suite.
- **Deterministic rg-style automation:** `--sort path` is the supported golden-output path for deterministic `--files-with-matches`, `--files-without-match`, `--replace`, and cross-platform path-list automation. Unsorted root-scale output remains semantic parity, not a promise of raw rg ordering.
- **Warm session contract:** Cached-session requests validate files captured in the session snapshot with size/mtime checks without walking the whole repository on every request. For agent loops, open a session once, start the localhost daemon, and send repeated daemon-routed edit-plan/context requests through that session so the first request can fill the daemon cache and later requests reuse it. Added-file discovery is reserved for `tg session refresh` and commands that opt into `--refresh-on-stale`, because broad added-file scans can dominate large workspace latency. Session edit-plan keeps graph ranking, test matching, and blast-radius metadata bounded to the selected context instead of re-expanding the whole cached map, and reports the blast-radius scope in `edit_plan_seed.blast_radius_scope`. `tg session list` and `tg session daemon status` discover nearby session scopes when the current directory has no direct session metadata, so parent/workspace shells do not misleadingly report an empty warm state. The localhost daemon keeps a short connect probe but uses a longer response timeout for edit-plan/context work.
- **Context-render trust contract:** `context-render` and MCP context output keep `edit_plan_seed.primary_file`, `navigation_pack.primary_target`, selected `files`, selected `sources`, and follow-up reads consistent, with `context_consistency` reporting omissions or confidence downgrades. The default JSON/LLM profile includes executable body lines for selected source blocks; compact rendering may strip comments, docstrings, blank lines, type-only imports, and boilerplate, but it must not remove all behavior from a selected function.
- **Validation command provenance:** Agent-facing validation hints use `validation_plan[].detection`; npm/package-manager commands require `package.json` evidence, Python commands require Python/test/project evidence, and absent runner evidence yields no command instead of a guessed one.
- **Routing Parity:** `tensor-grep` maintains exact character-for-character parity for text search outputs across all supported launcher modes (`native`, `bootstrap`, `python-m`). The only exception is `--help` text, which differs in word-wrapping layout between Clap (Rust) and Typer (Python) but guarantees the presence of valid `Usage:` instructions.
- **Golden-Output Scope:** The test suite snapshots exact, raw, and deterministic groupings and file path output directly from the engines. Native `tg.exe` intentionally does not support `-a` text parsing of binary fixtures; that binary-text case is handled by the Python `ripgrep` fallback and explicitly skipped in native-only contract tests.
- **Launcher Behavior:** The native Rust binary (`tg.exe`) acts as the primary front door, embedding AST search and fast-path text search. Unimplemented complex flags fall back to the Python sidecar. The Python wrapper (`python -m tensor_grep`) delegates structural and plain search commands back down to the native binary when available to guarantee uniform performance and path resolution. On Windows, `tensor-grep` intentionally rejects `PythonXY\Scripts\tg.exe` console-entrypoint shims when resolving that native path; use a release binary, an in-tree build, or `TG_NATIVE_TG_BINARY` when you need to force a specific native executable. A copied tensor-grep `tg.com` bridge outside `~/.tensor-grep/bin` must still discover the managed sidecar Python and managed native binary instead of the ambient Python install beside the bridge.
- **Non-Contract Fields:** Absolute temporary directory paths (normalized to `<TMP_DIR>` in tests), non-deterministic multi-threaded file ordering (stabilized via `-j 1` in tests or sorting where applicable), and specific help-text layouts are intentional non-contract fields.

## Why should I use `tensor-grep`?

- **Native CPU engine with measured workload-class wins.** The Rust text engine embeds ripgrep's grep crates directly, avoids subprocess overhead in the native path, and adds chunk parallelism for large files. See [docs/tool_comparison.md](docs/tool_comparison.md) and [docs/benchmarks.md](docs/benchmarks.md) for the current measured line.
- **Native AST search and rewrite.** `tg run` stays fully native for structural search, rewrite planning, diff, apply, and verify. PyPI wheels also expose Rust rewrite plan/apply through the PyO3 extension so simple CLI and MCP rewrite plan/apply paths work even when a standalone native `tg` binary is not installed.
- **Repeated-query acceleration.** The trigram index gives warm-query wins on unchanged corpora without changing the public search contract.
- **Harness-first machine interfaces.** JSON, NDJSON, diff, batch rewrite, and MCP are documented and regression-tested. Start with [docs/harness_api.md](docs/harness_api.md) and [docs/harness_cookbook.md](docs/harness_cookbook.md).
- **Lexical-first repo-map retrieval for AI planning.** Exact symbol queries stay anchored to definition files, camelCase queries bridge to snake_case symbols, and source-body evidence helps natural-language queries find the code that must be edited instead of unrelated service/test graph noise.
- **Smart routing with measured calibration.** `tg calibrate` writes routing evidence, but public claims still follow accepted benchmark artifacts. The active routing rules are documented in [docs/routing_policy.md](docs/routing_policy.md).
- **Benchmark-governed GPU path.** Native CUDA support exists, but current public dogfood keeps GPU experimental until 1GB/5GB correctness and speed beat both `rg` and `tg_cpu`. The current GPU story is documented in [docs/gpu_crossover.md](docs/gpu_crossover.md).
- **Experimental multi-pattern GPU search.** Pass multiple patterns with `-e pattern1 -e pattern2` when intentionally probing GPU-backed multi-pattern matching; do not treat this as a default speed claim.
- **Per-request GPU pinning from CLI.** `tg search ... --gpu-device-ids 0,1` pins the current command to selected GPUs with strict input validation and scoped probing, but selected devices still need benchmark proof before promotion.
- **It has a validated compatibility set for common ripgrep use.** `tg search` has a benchmarked compatibility contract for the day-to-day flags that matter most in code and log search, with the currently validated rows documented in [docs/CONTRACTS.md](docs/CONTRACTS.md).
- **Output replacement and actual rewrites are separate tools.** `tg search --replace` rewrites emitted match text in ripgrep style, while `tg run --rewrite ... --apply` performs real file edits through the AST rewrite path.
- **Managed semantic provider setup.** Run `tg lsp-setup` to provision pinned Node-backed LSP providers for optional experimental `lsp` / `hybrid` planning modes without depending on ad hoc workstation PATH state. Rust, Go, and C# toolchain-backed providers require the explicit `--include-toolchain-providers` flag. `tg doctor --with-lsp` reports provider availability and health separately; `available` only proves the provider binary was found, while navigation JSON reports `lsp_proof=false` and `lsp_evidence_status=fallback_native` when requests time out or return no usable semantic evidence. Rows count as LSP proof only when they include `lsp_provider_response=true` from a completed provider request. Use `tg lsp --debug-trace python --path .` to capture a one-shot JSON-RPC health trace, timeout budgets, status, and provider stderr tail for startup/probe failures.
- **Optional log classification.** `tg classify` uses deterministic local heuristics by default and only probes `cyBERT` / Triton when `TENSOR_GREP_CLASSIFY_PROVIDER=cybert` is set. `--format json` includes top-level `classification_backend` provenance plus Hugging Face cache/offline metadata and file/path/line/snippet metadata for each classification row, so agents can tell whether results came from local heuristics, the explicit provider, or a quiet provider fallback. Treat the model-backed path as an experimental helper, not a default agent primitive or hot search path. MCP `tg_classify_logs` follows the same local-first contract.
- **Unified Harness API.** Tensor-grep JSON outputs (`--json` and `--ndjson`) share a common envelope (`version`, `routing_backend`, `routing_reason`, `sidecar_used`) so harnesses and AI agents can reliably parse routing decisions. `tg search --format rg --json` is the exception: it deliberately streams ripgrep JSON Lines events without a tensor-grep envelope for tools that need rg's event schema. GPU-aware search JSON separates `requested_gpu_device_ids` from `routing_gpu_device_ids`; CPU fallback after a GPU request reports `routing_gpu_device_ids = []`, not a GPU proof. `tg doctor --json` exposes `gpu.search_runtime_probe` so sidecar-contaminated or CPU-fallback routes are visible before benchmark or agent claims. Schema documentation and example artifacts are at [`docs/harness_api.md`](docs/harness_api.md) and [`docs/examples/`](docs/examples/). A Rust-side schema compatibility test locks the contract against accidental breakage.
- **NDJSON Streaming Output.** `tg search --ndjson` emits one JSON object per matching line, enabling streaming consumption for large result sets without buffering the entire response.
- **Batch AST Rewrite.** `tg run --batch-rewrite config.json` accepts multiple pattern/replacement/language rules in a single invocation. Cross-pattern overlaps are detected and reported without corrupting files.
- **One-shot rewrite apply.** The one-shot CLI fast path `tg run --rewrite ... --apply` uses fused single-read direct writes for safe simple apply shapes. The explicit planned-edit apply path still uses the safer atomic temp-file rename contract, and contract-heavy paths such as JSON, diff, checkpoint, audit, validation, verify, selector, and batch rewrite stay on the plan-first path. Current speed claims follow the AST rewrite benchmark gate in [docs/benchmarks.md](docs/benchmarks.md).
- **Stale-File Detection.** Before applying rewrite edits, the engine verifies that each file's mtime hasn't changed since planning. Stale files are rejected with a clear error rather than silently applying outdated edits.
- **Encoding Safety.** Rewrites preserve UTF-8 BOM and CRLF line endings in non-edited ranges. Binary files are automatically skipped. Large files (>100 MB) are skipped with a warning. Non-ASCII content (CJK, emoji, combining characters) is handled without corruption.
- **Index Compression.** The trigram index binary format now uses varint encoding for posting lists, achieving ~73.5% size reduction compared to the legacy format. The compressed format is the default and maintains full backward compatibility.
- **Incremental Index Updates.** When files are added, removed, or modified, the trigram index performs targeted updates instead of full rebuilds, reusing unchanged file entries for faster index maintenance on large repos.
- **Regex Index Acceleration.** The index now handles alternation patterns (`foo|bar`), character classes, and Unicode patterns for prefiltering, extending the set of queries that benefit from index acceleration.
- **GPU Sidecar Error Hardening.** GPU sidecar errors (timeout, invalid device ID, CUDA unavailable, malformed output, sidecar crash) are caught and reported with clear, actionable messages instead of raw tracebacks.
- **Documented Routing Policy.** Explicit routing decision tree documented at [`docs/routing_policy.md`](docs/routing_policy.md) with 14 routing regression tests covering every backend selection path.

## Why shouldn't I use `tensor-grep`?

I'd like to try to convince you why you *shouldn't* use `tensor-grep`. This should give you a glimpse at some important downsides.

- **You only search small files.** `rg` is still the baseline for tiny cold searches. `tensor-grep` is the agent-native code-intelligence layer for repeated queries, AST workflows, context capsules, and harness loops where routing, validation, rollback, and confidence matter.
- **You want GPU to win automatically on every host.** It does not. GPU routing is benchmark-governed and hardware-specific. Read [docs/gpu_crossover.md](docs/gpu_crossover.md) before forcing a GPU claim.
- **You need tiny standalone binaries.** The fully bundled release artifacts are still large because they carry optional Python/NLP/CUDA compatibility layers for non-native paths.
- **You don't want heavy dependencies.** A full `tensor-grep` installation with AST and NLP capabilities requires installing `torch`, `torch-geometric`, `transformers`, and NVIDIA drivers. If you just want a 3MB fast search tool, stick to pure `ripgrep`.

## Installation

The binary name for `tensor-grep` is `tg`.

### Zero-Dependency Installation (Recommended)
To ensure PyTorch bindings and CUDA/ROCm versions exactly match your hardware without conflicting with your system Python, we recommend using our automated install scripts. These scripts use `uv` to intelligently probe your GPU and build a highly isolated Python 3.12 environment in the background.

The install scripts also run `tg lsp-setup --json` after creating the front-door `tg` command. That attempts the safe default managed provider setup under `~/.tensor-grep/providers` for pinned Node-backed providers and warns without failing the core install if optional provider setup is unavailable. Stable script installs prefer the matching release-native CPU `tg` binary as the public front door and expose the isolated Python environment through `TG_SIDECAR_PYTHON`; if the native asset is unavailable, the same front door falls back to `python -m tensor_grep`. If you install through `pip`, `uv`, or a package-manager path and need provider-backed planning, run `tg lsp-setup` manually. Use `tg lsp-setup --include-toolchain-providers` only when you want tensor-grep to copy or install Rust, Go, and C# provider binaries through local toolchains.

**Windows (PowerShell):**
```powershell
irm https://raw.githubusercontent.com/oimiragieo/tensor-grep/main/scripts/install.ps1 | iex
```

**Linux & macOS (Bash):**
```bash
curl -LsSf https://raw.githubusercontent.com/oimiragieo/tensor-grep/main/scripts/install.sh | bash
```

Installer defaults and channels:
- Default behavior installs the latest stable PyPI release.
- Set `TENSOR_GREP_VERSION` to pin a specific stable version (example: `TENSOR_GREP_VERSION=1.1.3`).
- Set `TENSOR_GREP_CHANNEL=main` to install directly from the GitHub `main` branch.
- At completion, the installer prints `tg --version` and returns to the directory where you started the script.
- Windows installer now installs managed PowerShell, `cmd.exe`, Git Bash, and WSL shims in `~/.local/bin` and `~/bin`, removes stale same-directory `tg.exe`/`tg.bat` launchers that would shadow the shim, moves those shim directories ahead of stale Python `Scripts` launchers on User PATH, updates both PowerShell 7 and Windows PowerShell profiles, replaces stale aliases, forces UTF-8 mode, and writes bash shims with LF newlines for WSL.
- PowerShell double quotes expand `$NAME` before tensor-grep receives literal patterns. Use single quotes for AST metavariables and regexes that contain `$`, or escape `$` as `` `$ `` inside double-quoted PowerShell strings.
- In PowerShell, invoke `tg` or `tg.ps1` for regex metacharacters. Directly invoking `tg.cmd` with an unescaped `|` is still parsed by `cmd.exe` before tensor-grep receives argv; cmd.exe metacharacters such as `|`, `&`, `<`, `>`, `^`, `(`, and `)` must be quoted or caret-escaped for `cmd.exe`.
- `tg doctor --json` includes `shell_escaping_guidance` with the same PowerShell and `cmd.exe` argv notes for automation.

If `tg --version` still reports an older version, check command resolution:
```powershell
Get-Command tg
where.exe tg
tg doctor --json
```

Examples:
```powershell
# Windows PowerShell: install from main
$env:TENSOR_GREP_CHANNEL = "main"
irm https://raw.githubusercontent.com/oimiragieo/tensor-grep/main/scripts/install.ps1 | iex
```

```bash
# Linux/macOS: install a specific stable release
TENSOR_GREP_VERSION=1.1.3 curl -LsSf https://raw.githubusercontent.com/oimiragieo/tensor-grep/main/scripts/install.sh | bash
```

### Python Package Managers (pip/uv)
If you're a Python programmer, `tensor-grep` can be installed via `pip` or `uv`.

```bash
# Basic CPU fallback installation
pip install tensor-grep

# Full installation with AST matching, NLP, and Linux GPU RAPIDS dependencies
uv pip install "tensor-grep[ast,nlp]" cudf-cu12 --extra-index-url https://pypi.nvidia.com
```

### Node.js (npx)
```bash
npx tensor-grep search "ERROR" .
```

The npm wrapper downloads the release-validated CPU binary for supported x64 platforms from GitHub Releases.

### Standalone Binaries (For IT/SecOps)
If you cannot run the install scripts or prefer a managed binary rollout, use the GitHub release assets and checksum manifest from the tagged release.

Current release assets include:
* `tg-windows-amd64-cpu.exe`
* `tg-linux-amd64-cpu`
* `tg-macos-amd64-cpu`

Operational notes:
- Each tagged release also publishes `CHECKSUMS.txt` and a `package-manager-bundle/` for Homebrew and Winget submission flows.
- Prefer the Python install path if you want `tg update` / `tg upgrade` to self-update the installed package.
- Experimental features remain opt-in and are documented in [docs/EXPERIMENTAL.md](docs/EXPERIMENTAL.md), not surfaced in the top-level help output.

### Docker
```bash
docker run --gpus all -v $(pwd):/workspace factory/tensor-grep:latest-cuda search "ERROR" /workspace/logs
```

## Whirlwind tour

The command line usage of `tensor-grep` doesn't differ much from other tools that perform a similar function. The full details can be found in `tg --help`.

To recursively search the current directory, while respecting all `.gitignore` files, ignore hidden files and directories and skip binary files:

```bash
$ tg foobar
```

(Note: The front door preserves common ripgrep-style argv routing, so you usually do not need to type `tg search foobar`. Just typing `tg foobar` routes through the search command.)

Make the search case insensitive with `-i`, invert the search with `-v` or show the 2 lines before and after every search result with `-C2`:

```bash
$ tg -i -v -C2 foobar
```

Force all matches to be surrounded by word boundaries with `-w`:

```bash
$ tg -w foobar
```

List files that do not contain a match while still honoring ignore rules by default:

```bash
$ tg search foobar . --files-without-match
```

Add `--no-ignore` when you want ignored files and directories included in the candidate set for this mode.

For broad file-list discovery, prefer a scoped path or a bound:

```bash
$ tg search --files src --hidden
$ tg search --files . --hidden --glob "*.py"
$ tg search --files . --hidden --max-depth 3
```

Unbounded generated/cache/dependency roots combined with hidden, no-ignore, or unrestricted scanning are refused by default. Parent directories that look like multi-project workspace roots are also refused for unbounded searches before the broad walk starts. Scope to one project, add `--glob`, `--type`, or `--max-depth`, or pass `--allow-broad-generated-scan` only when the large generated-tree or workspace-root walk is intentional.

Search only Python and Javascript files:

```bash
$ tg -tpy -tjs foobar
```

Force the native CPU engine (bypasses GPU even if available):

```bash
$ tg --cpu foobar
$ tg --force-cpu foobar
```

Select specific GPU devices for search:

```bash
$ tg --gpu-device-ids 0 foobar
$ tg --gpu-device-ids 0,1 foobar
```

Search for multiple fixed patterns in one native CPU pass:

```bash
$ tg -e "ERROR" -e "FATAL" -e "PANIC" ./logs
```

Calibrate CPU vs GPU crossover thresholds for your hardware:

```bash
$ tg calibrate
```

This measures search performance at various corpus sizes and writes a `.tg_crossover` config file. Only rely on automatic GPU routing when that local artifact shows a real end-to-end crossover; the current Windows benchmark keeps GPU search manual-only.

Inspect routable multi-GPU inventory and VRAM sizing:

```bash
$ tg devices
$ tg devices --format json
$ tg devices --json
```

### Streaming & Batch Operations

Emit search results as newline-delimited JSON (one object per match) for streaming consumption:

```bash
$ tg search --ndjson "ERROR" ./src ./tests ./docs
```

Apply multiple AST rewrite rules in a single pass with a JSON config file:

```bash
$ tg run --batch-rewrite rewrites.json ./src
$ tg run --batch-rewrite rewrites.json --apply ./src
$ tg run --batch-rewrite rewrites.json --apply --verify --json ./src
```

Example `rewrites.json`:
```json
{
  "rewrites": [
    {"pattern": "def $F($$$ARGS): return $EXPR", "replacement": "lambda $$$ARGS: $EXPR", "lang": "python"},
    {"pattern": "console.log($X)", "replacement": "logger.info($X)", "lang": "javascript"}
  ],
  "verify": true
}
```

### AI Assistant Integration (MCP)
`tensor-grep` includes a native Model Context Protocol (MCP) server. This lets AI assistants such as Claude Desktop or Cursor call search, AST/rewrite, device inventory, and optional classification tools through a structured local interface.

To use it with Claude Desktop, just add this to your `claude_desktop_config.json`:
```json
{
  "mcpServers": {
    "tensor-grep": {
      "command": "tg",
      "args": ["mcp"]
    }
  }
}
```

Available MCP tools now include:
- `tg_mcp_capabilities` (reports which MCP tools are local, embedded-safe, or native-required)
- `tg_search`
- `tg_ast_search`
- `tg_classify_logs`
- `tg_devices` (returns routable GPU IDs and VRAM inventory; supports JSON output)
- `tg_index_search` (trigram-indexed text search with auto-build/rebuild)
- `tg_rewrite_plan` (dry-run AST rewrite, returns JSON edit plan)
- `tg_rewrite_apply` (apply AST rewrite edits with optional byte-level verification)
- `tg_rewrite_diff` (unified diff preview of planned rewrites)
- `tg_agent_capsule` (Actionable Context Capsule JSON for pre-edit agent context, optional native GPU evidence via `gpu_device_ids`, validation, omissions, and rollback guidance)

Call `tg_mcp_capabilities` first when running from PyPI wheels or agent sandboxes. It reports whether a standalone native `tg` binary is available, whether embedded rewrite fallback is importable, and which tools require native `tg`.

For machine consumers of CLI JSON output (`tg search ... --json`), routing metadata is included:
- `version` (contract version, currently `1`)
- `routing_backend`
- `routing_reason`
- `sidecar_used`
- `requested_gpu_device_ids`
- `routing_gpu_device_ids`
- `routing_gpu_chunk_plan_mb`
- `routing_distributed`
- `routing_worker_count`

For streaming consumption, use `tg search ... --ndjson` to emit one JSON object per matching line (newline-delimited), ideal for piping to AI agents or large-result processing.

**AI Prompt Configuration:**
If you are building custom AI agents or bots, we provide an optimized prompt template explicitly outlining when and how AI models should use `tensor-grep`. Check out the [`SKILL.md`](SKILL.md) file to seamlessly inject our capabilities into your agent's system prompt!

### AST / Structural Searching
Run semantic code structure searches that ignore formatting, whitespace, and comments:

```powershell
tg run --lang python 'function $NAME($$$ARGS) { $$$BODY }' ./src --json
```

PowerShell expands `$NAME` inside double quotes. Use single quotes for AST metavariable patterns and rewrites on Windows.

### Log Classification
Scan a system log with deterministic local heuristics by default. Set `TENSOR_GREP_CLASSIFY_PROVIDER=cybert` only when you intentionally want the experimental CyBERT/Triton provider; JSON output reports provider, cache, and offline status.

```bash
$ tg classify /var/logs/syslog
```

## Building & Developing

`tensor-grep` uses a hybrid Rust & Python architecture with a native Rust binary for performance-critical paths.

### Python + Rust (PyO3) development

```bash
$ git clone https://github.com/oimiragieo/tensor-grep
$ cd tensor-grep

# Install dependencies using uv
$ uv pip install -e ".[dev,ast,nlp]"

# Build the Rust PyO3 core locally via Maturin
$ python -m maturin develop --release

# Run the Python test suite
$ pytest tests/
```

### Native Rust binary (CPU-only)

```bash
$ cd rust_core
$ cargo build --release
$ cargo test
```

### Native Rust binary with CUDA GPU support

Requires CUDA Toolkit 12.0+ installed and `nvcc` on PATH.

```bash
$ cd rust_core
$ cargo build --release --features cuda
$ cargo test --features cuda
```

The `cuda` feature links against `cudarc` (Rust-native CUDA bindings), compiles GPU kernels via NVRTC JIT, and caches PTX by architecture and kernel hash across CLI invocations. The post-`v1.13.6` native CUDA scale dogfood covers 1GB and 5GB correctness on both RTX 4070 (`sm_89`) and RTX 5070 (`sm_120`). Single-pattern cold grep still has no crossover; the measured CUDA speed lane is many fixed strings over large corpora. RTX 50-series / `sm_120` hosts need a CUDA 12.8+ compatible stack for PyTorch-backed sidecar flows and are not benchmark-promoted by device discovery or correctness alone. Managed NVIDIA installs now use PyTorch `cu128` wheels so Ada and Blackwell hosts have a compatible sidecar baseline before benchmark gates run.

## Hardware & Software Requirements

### CPU-only (no GPU needed)

The native CPU engine requires only a Rust toolchain. No GPU, CUDA, or Python runtime is needed for the native binary. Current performance claims should be taken from [docs/benchmarks.md](docs/benchmarks.md), not this README.

### Experimental Native CUDA

To evaluate native CUDA search, your system must meet these requirements. End-to-end GPU routing is still benchmark-governed and host-specific; see [docs/gpu_crossover.md](docs/gpu_crossover.md) for the current measured line. Public managed GPU remains experimental until public binaries produce `NativeGpuBackend`, `sidecar_used = false`, direct `rg --json` 1GB/5GB correctness, and speed wins over both `rg` and `tg_cpu`. Release-quality GPU promotion additionally requires managed NVIDIA front-door metadata in `tg-native-metadata.json` and a dispatch-only `public-gpu-proof.yml` run with `--public-managed-proof` where `public_managed_promotion_ready = true` and `public_gpu_proof = true`; a local CUDA-feature native build alone is not public GPU readiness.

* **Hardware:**
  * NVIDIA GPU (RTX 30/40 series recommended; RTX 50-series / sm_120 support depends on the CUDA/PyTorch stack described in [docs/runbooks/gpu-troubleshooting.md](docs/runbooks/gpu-troubleshooting.md))
  * Minimum 4GB VRAM (8GB+ recommended for massive corpora)
  * Multi-GPU supported; current gains are workload-dependent and documented in [docs/gpu_crossover.md](docs/gpu_crossover.md)
* **Software / Drivers:**
  * **NVIDIA Display Drivers:** v535.xx or newer
  * **CUDA Toolkit:** 12.0 or newer for native CUDA builds; CUDA 12.8+ is recommended for dual RTX 4070 / RTX 5070 hosts and required for PyTorch-backed RTX 50-series / sm_120 compatibility
* **Build:** `cargo build --release --features cuda` in the `rust_core` directory

### Python backends (optional)

The native CPU, AST, index, and primary GPU paths live in Rust. Python remains optional for NLP classification and compatibility sidecar paths:
* **Linux / WSL2:** NVIDIA RAPIDS `cuDF` (`cudf-cu12`) for optional sidecar-backed GPU integrations.
* **Windows Native:** PyTorch CUDA 12.8 (`cu128`) for optional NVIDIA NLP and compatibility flows.
* **AMD ROCm:** Linux-first PyTorch ROCm 7.2 is the managed AMD install target. Windows ROCm support is narrower and GPU-file-search paths must fall back to CPU/`rg` unless the host passes explicit device and correctness checks.
* **All platforms:** `uv pip install "tensor-grep[ast,nlp]"` for optional AST/NLP Python extras where needed.

## Future Work

The `v1.x` line is feature-complete for the current native search, AST, and editor-plane surface. The remaining work is intentionally narrow:

- add any lexical reranking or AST-shaped chunking only when it beats the accepted lexical-first repo-map line on both retrieval quality and editor-plane benchmarks
- add tighter multi-agent signal surfaces on top of the existing JSON/NDJSON, session, and MCP contracts instead of inventing another parallel agent protocol
- publish a broader reproducible comparator pack for tools such as `ag`, `ack`, `ugrep`, and GNU `grep` alongside the current `rg` and `git grep` rows
- graduate or retire the experimental resident AST worker based on benchmark-governed evidence, not intuition
- keep benchmark-governed security and compliance acceleration on top of the existing rulesets and audit surfaces
- keep managed provider / editor-plane integrations honest and contract-tested
- continue supply-chain hardening, package-manager validation, and operational docs for team ownership
- preserve benchmark history and rejected experiments so future work stays measurable instead of speculative

## Tips

### Routing first, forcing later

- use `tg calibrate` before considering auto GPU routing, and keep GPU manual-only unless the artifact shows a real crossover
- use `--gpu-device-ids` only when you have a workload that actually benefits
- use `--index` for warm repeated-query workflows
- use `tg session refresh <session_id> [PATH]` or `--refresh-on-stale` when a cached session must discover newly added files; ordinary cached-session reads stay on snapshot-file checks to avoid broad repo walks
- use `--ndjson` for large result streams
- use plan -> diff -> apply+verify for structural edits

For current backend selection rules, see [docs/routing_policy.md](docs/routing_policy.md).
