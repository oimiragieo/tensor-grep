# tensor-grep Post-Mission Continuation Plan

## For: Next agent picking up after the native CPU/GPU/index/rewrite milestones

## 2026-05-26 Current Handoff

release_docs_current_tag: v1.54.5

The current tagged state is `v1.54.5`, and the latest complete public PyPI/release-asset distribution is also `v1.54.5`. Stable installer and sidecar upgrade hardening shipped through `v1.12.34`, including managed-native front-door refresh after `tg upgrade`, refresh of stale tensor-grep-owned `tg.com` PATH bridges after upgrade, public native-front-door parity for advertised Python-backed shapes, Windows `.cmd` quoted-pattern handling, fresh-shell launcher diagnostics, Python-subprocess launcher diagnostics, top-level `validation_commands`, deterministic local `classify`, classify provider provenance, fixed multi-pattern native CPU search, GPU scale correctness gates, benchmark launcher attribution, scoped GPU device probing, `tg agent` Actionable Context Capsule, bounded capsule call-site evidence, mixed-language capsule confidence/validation alignment, edit JSON/rollback safety, ambiguous capsule tie metadata, workflow benchmark governance, release-doc synchronization, release wheel Cargo prefetch retries, native GPU/search accuracy hardening, explicit Windows Python subprocess launcher repair, agent capsule hardcase routing, rg flag-contract alias hardening, AST CLI contract hygiene, agent output-budget hygiene, Windows subprocess bridge ranking hardening, v1.12.33 column-override parity, stale repo-local `uv run tg` readiness diagnostics, and the `ripgrep binary resolution` capsule hardcase. Public v1.12.34 dogfood verified PyPI, GitHub assets, `uvx`, and the current rg/frontend/readiness hardening slices; the `v1.10.10` repair release remains the Windows subprocess launcher repair baseline. Public GPU remains experimental because managed GPU requests still route through `GpuSidecar` / unsupported instead of qualifying `NativeGpuBackend`, while native CPU fixed multi-pattern search is the accepted many-pattern improvement. Use [docs/SESSION_HANDOFF.md](SESSION_HANDOFF.md) as the live handoff for release status, current weak spots, release completion contract, and next-session commands. This continuation plan remains useful as the historical workstream map, but it is no longer the freshest operational state.

Historical release facts (snapshot from the v1.13.23 release line — this plan is the historical
workstream map per the note above; see [docs/SESSION_HANDOFF.md](SESSION_HANDOFF.md) for the live
release state and current install proof):

- `v1.11.0` main CI run `25834508800` passed pre-release checks and semantic-release, but release-native asset publication was cancelled; `publish-success-gate` failed and PyPI latest remains `1.10.10`.
- Main CI run `26513809791`: passed pre-release checks, semantic-release, `publish-github-release-assets`, `publish-pypi`, and `publish-success-gate`.
- Main dynamic/CodeQL run `26513808787`: passed on the `3c0c213` merge commit.
- Release commit `bd7035c`: published `v1.13.23` with `[skip ci]` after main CI completed.
- Previous `v1.13.22` proof runs `26473492381` and `26473490540` remain retained as historical release proof.
- Previous `v1.13.21` proof runs `26450640497` and `26450639894` remain retained as historical release proof.
- Previous `v1.13.20` proof runs `26437847778` and `26437847528` remain retained as historical release proof.
- Previous `v1.13.19` proof runs `26431129535` and `26431129155` remain retained as historical release proof.
- Previous `v1.13.18` proof runs `26425383595` and `26425914836` remain retained as historical release proof.
- Previous `v1.13.15` proof runs `26386327552`, `26386327168`, `26386976717`, and `26386978124` remain retained as historical release proof.
- Previous release CI run `26094452260`: passed pre-release checks, semantic-release, `publish-github-release-assets`, `publish-pypi`, and `publish-success-gate`.
- Previous verified release CI run `25951521056`: passed pre-release checks, semantic-release, `publish-github-release-assets`, `publish-pypi`, and `publish-success-gate`.
- Main CI run `25866871838`: passed the pre-release matrix, semantic-release, PyPI artifact validation, `publish-github-release-assets`, `publish-pypi`, and `publish-success-gate`.
- Previous CodeQL run `25951813292`: passed on the v1.12.14 release line.
- PyPI pinned public install: `uvx --refresh-package tensor-grep --from tensor-grep==1.13.22 tg --version` reports `tensor-grep 1.13.22`.
- GitHub release assets for `v1.13.23` include native CPU front doors, checksums, winget manifest, Homebrew formula, and publish instructions.
- Public `v1.12.14` dogfood verified post-release-safe docs governance, public launcher resolution, bounded capsule call-site evidence, Windows subprocess bridge ranking hardening, agent output-budget hygiene, AST CLI contract hygiene, bounded map/context output, `tg run --pattern`, root cold rg-shaped routing, accepted native/dev CLI flag alignment, `tg new --base-dir`, edit-plan budget flags, explicit rg JSON Lines routing via `--format rg --json`, explicit JSON/NDJSON schema positioning, and rg flag-contract aliases; public managed GPU remains not promotion-ready and falls back to CPU or unsupported rows unless a CUDA-feature native build proves `NativeGpuBackend` with `sidecar_used = false`.
- Current retained release-baseline commits: `361e0db fix: harden public GPU unavailable routing`, `2100122 fix: harden release docs stamp governance`, and `87d4ca4 fix: accelerate fixed multi-pattern native search`.
- Historical `v1.10.10` launcher dogfood: direct `C:\Users\oimir\.tensor-grep\bin\tg.exe --version`, fresh `cmd`, unprofiled `pwsh`, and Python `subprocess.run(["tg", "--version"])` all reported `tg 1.10.10` after the explicit repair command.
- Prior managed native-upgrade dogfood: `tg update` from `v1.9.3` installed sidecar `tensor-grep==1.9.4` and refreshed the native front door to `tg 1.9.4` after transient PyPI propagation lag.
- Public capsule dogfood: `tg agent src/tensor_grep/cli --query "agent context capsule" --json` returns the Actionable Context Capsule contract.
- Prior public installer dogfood: rerunning `scripts/install.ps1` for `v1.8.31` put `C:\Users\oimir\.tensor-grep\bin` ahead of compatibility shim directories on User PATH, and a simulated fresh shell resolves the native managed front door first.
- Public native CLI dogfood: installed `tg 1.8.32` accepted `tg search --multiline`, `tg search -U`, `tg search --files`, `tg search --null`, `tg run -r`, and `tg classify --format json`.
- Public doctor dogfood: `tg doctor --json` reports `path_tg_first_launcher_kind = cmd-shim`, `fresh_shell_path_tg_first_launcher_kind = managed-native`, and `path_tg_launcher_warning` when an existing shell still routes through the compatibility shim before fresh-shell PATH.
- Public Windows launcher dogfood: `cmd /c tg`, direct `tg.cmd`, native `tg.exe`, and Python `subprocess.run([...tg.cmd...])` preserve quoted multi-word no-match patterns and return exit `1` with no false-positive stdout.
- Post-`v1.9.6` local dogfood: native CUDA release search passes correctness smoke checks and 1GB/5GB scale correctness on both RTX 4070 (`sm_89`) and RTX 5070 (`sm_120`), and PyTorch sidecar searches work on both after refreshing to `2.11.0+cu128`; GPU remains slower than both `rg` and `tg_cpu`, so this is compatibility evidence rather than a speed claim. Root `tg --help` advertises current agent/GPU/launcher/validation settings, and first-PATH `tg` commands from unrelated tools are classified as `foreign` in `tg doctor --json` with explicit readiness remediation. On this host, fresh Windows shell dogfood passes after adding a tensor-grep `tg.com` bridge ahead of the foreign `tg.exe` because Machine PATH ordering was not writable.

Current product read:

- `tg` is production-usable for scoped agent search, source lookup, refs, context bundles, and bounded blast-radius.
- `rg` remains the benchmark for raw cold exact-text search.
- `ast-grep` remains the structural-search feature/performance baseline; `tg run` is a useful validated slice, not full ast-grep equivalence.
- GPU exists and devices are detected locally, but GPU routing remains experimental, opt-in, and benchmark-governed.
- Public managed GPU is not promotion-ready until public assets produce `NativeGpuBackend`, `sidecar_used = false`, correctness, and speed wins over both `rg` and `tg_cpu`; treat current public `GpuSidecar` evidence as unsupported.
- Recent correctness work improved agent context trust and deterministic rg parity without changing the speed story: context rendering should keep edit seed, navigation target, selected sources, and MCP output consistent; default LLM rendering should preserve executable body lines; validation plans should only emit commands with runner evidence; and the rg claim should stay a validated compatibility set.
- Broad generated and multi-project workspace roots have an explicit guardrail path: unbounded `tg search --files --hidden` scans through generated/cache/dependency child directories, direct content searches against generated/cache/dependency roots, and unbounded searches against a parent containing multiple child project roots should be refused unless bounded with `--glob`, `--type`, or `--max-depth`, or explicitly opted in with `--allow-broad-generated-scan`. Explicit `--no-ignore` content searches over ordinary project roots follow ripgrep even when ignored generated child directories exist.
- Windows/WSL installer shims are materially cleaner. Direct `.cmd` invocation from PowerShell still cannot receive an unescaped `|` because `cmd.exe` parses it before the batch file receives argv; use normal PowerShell `tg` / `tg.ps1` for regex metacharacters.
- Dev-path native safety should ignore stale in-tree standalone binaries unless `TG_NATIVE_TG_BINARY` pins one explicitly; `uv run tg doctor --json` should report skipped stale candidates instead of letting searches validate through old native code.
- Fresh-shell PATH dogfood should distinguish tensor-grep-owned stale launchers from foreign `tg` commands. A foreign launcher such as Together CLI ahead of `~/.tensor-grep/bin` is an environment blocker that should fail readiness with remediation, not an installer cleanup target.
- Python subprocess launcher dogfood is separate from shell dogfood. `subprocess.run(["tg", ...])` can hit a foreign `.exe` route that `cmd`/PowerShell hide through `PATHEXT`, so readiness should check it explicitly and `tg doctor --json` should expose the route. If the foreign command is operator-owned, `tg repair-launcher --allow-foreign-rename` can back it up and put the managed native `tg.exe` in the winning PATH slot.
- Raw unsorted root output is semantic parity. Use `--sort path --format rg` for automation that needs deterministic ripgrep-style stdout.
- Release-native install/update hardening: stable script installs should prefer the matching release-native CPU front door and use the isolated Python environment as sidecar/fallback. Installer/update hardening now covers stale package metadata, post-upgrade imports, native installer exit codes, staged replacement, sidecar/native version alignment after `tg upgrade`, public native CLI parity for advertised search/run/classify flags, fresh Windows PATH precedence for the managed native front door, and doctor/benchmark attribution for the actual launcher route; do not change benchmark docs from installer work.
- `edit-plan` and `context-render` JSON expose top-level `validation_commands` for agent contract consistency; preserve that shape in future edits.
- `classify` stays deterministic and local by default; use `TENSOR_GREP_CLASSIFY_PROVIDER=cybert` only for intentional CyBERT/Triton provider probes.
- GPU benchmark gates include 1GB and 5GB rows and exact match/file-set correctness for every >=1GB corpus before any GPU promotion claim.
- Token-output follow-up from `rtk-ai/rtk`: add a future opt-in agent-bounded output profile with grouped excerpts, hard caps, truncation, and omission counts. Do not mutate raw `--format rg`, `--json`, or `--ndjson` to save tokens.
- The next standout product surface should be `tg agent` / Actionable Context Capsule, not another raw grep wrapper. The target output is a deterministic work packet with primary file/function, route rationale, bounded snippets with line maps, related call sites, validation evidence, risk, edit order, checkpoint/rollback metadata, omission counts, confidence, and an "ask user before editing" recommendation when the evidence is weak. Mixed-language capsules must keep query language hints, exact symbol intent, primary target language, `validation_alignment`, and confidence honest instead of hiding contradictions behind a single ranked target.
- Agents must inspect top-level `ambiguity` before editing. `ambiguity.status = "tie_requires_confirmation"` is a hard stop for autonomous edits; `tie_resolved` requires explicit `resolved_by` evidence.
- Search-intent routing should be explicit about evidence type. Future capsules should label each conclusion as `parser-backed`, `rg-backed`, `graph-derived`, `heuristic`, `LSP-confirmed`, or `stale/uncertain` so agents know what is proven and what is inferred.

Current next work:

1. Keep the fast agent-readiness gate (`python scripts/agent_readiness.py --output artifacts/agent_readiness.json`, or `tg dogfood --output artifacts/dogfood_readiness.json` for the verdict envelope) covering context-render trust, `context_consistency`, `agent-capsule-hardcases`, sorted rg edge parity, broad generated-root scan guardrails, AST smoke, MCP smoke, shell version probes, Python subprocess version probes, launcher route diagnostics, and docs claim checks.
2. Add progress or partial output for explicitly opted-in broad generated-root scans.
3. Calibrate or de-emphasize `impact --symbol` so agents prefer `blast-radius` for direct symbol impact.
4. Track AST parity roadmap, GPU readiness, and model-backed classify provider/cache UX as blockers for a future "100% ready" claim.
5. Build the opt-in agent-bounded output profile only with explicit contracts and regression tests, starting from the Actionable Context Capsule shape above.
6. Keep GPU auto-recommendation off the marketing path unless required 1GB/5GB correctness checks pass and a selected GPU beats both `rg` and `tg_cpu` at required scale.
7. Continue dogfooding and preserve exact failing commands as product evidence.

Agent product-surface backlog:

1. Search Intent Router: choose text, AST, symbols, imports, tests, docs, or blended routes from a natural task, then explain the chosen route and evidence type.
2. Patch Planning Without Editing: produce files, functions, likely tests, risk, and edit order without touching the worktree.
3. Safe Rewrite Loop: combine structural rewrite, plan, checkpoint, verification, and audit manifest before apply.
4. Test Selection Engine: rank the smallest useful validation set for a target file, symbol, or patch, with detected evidence over guesses.
5. Failure-Aware CI Triage: map logs back to files/functions/tests and produce fix candidates with provenance.
6. Repo Memory: persist useful repo maps, symbol/test associations, generated roots, recent failures, and stale-state markers across repeated agent loops.
7. Truthful confidence labels: surface parser-backed, rg-backed, graph-derived, heuristic, LSP-confirmed, and stale/uncertain evidence on every capsule claim.
8. Agent Token Economy Mode: provide grouped excerpts, hard budgets, omissions, and next-read suggestions through an opt-in agent command such as `tg search-agent`, not through raw search output mutation.

## Status

This document replaces the older continuation plan that assumed:

- `rg` subprocess control-plane routing was still the main text path
- GPU was still Python-sidecar-first
- harness JSON contracts, routing policy, index hardening, and workflow integration were incomplete

That is no longer the repo state.

The project now has:

- native CPU text search in Rust
- native AST search in Rust
- native AST rewrite plan/diff/apply/verify in Rust
- native trigram index build/load/query/update paths
- experimental native GPU engine in Rust with smart routing and calibration gates
- harness-facing JSON and NDJSON outputs
- MCP workflow integration

The next phase is not architecture invention. It is:

1. API freeze
2. benchmark freeze
3. reliability / soak hardening
4. external harness adoption
5. governance

---

## Current State

### Validation baseline

At mission close:

- Rust tests: `145`
- Python tests: `549`
- mission validation assertions: `75/75`
- lint / type / benchmark gates: green

### Product capabilities

| Capability | Status | Notes |
|---|---|---|
| Native CPU text engine | Production | Embedded grep crates, no `rg` subprocess required for the fast path |
| Native AST search | Production | Faster than `sg` on accepted benchmark corpus |
| Native AST rewrite | Production | Plan / diff / apply / verify, deterministic edit IDs |
| Native GPU engine | Experimental / opt-in | Rust CUDA path exists, but public dogfood still shows 100MB/1GB/5GB GPU losses or timeouts; do not market GPU speed until accepted correctness and speed artifacts prove crossover |
| Index subsystem | Production | Binary persistence, invalidation, compression, incremental update path |
| Harness API | Production v1 | Unified JSON envelope, documented examples, compatibility tests |
| Workflow integration | Production first cut | MCP tools, NDJSON, batch rewrite API |

### Key invariants that must remain true

1. Native hot paths stay native:
   - text search
   - AST search
   - AST rewrite
   - index query
   - GPU search when explicitly opted in and benchmark-proven for the workload
2. All machine-readable outputs stay single-document unless explicitly NDJSON.
3. JSON envelope stays coherent:
   - `version`
   - `routing_backend`
   - `routing_reason`
   - `sidecar_used`
4. Rewrite dry-run never mutates files.
5. Rewrite apply validates overlaps before any write reaches disk.
6. Verification uses byte-level exact text matching, not heuristic AST membership.
7. Index files remain self-identifying and versioned (`TGI\\x00` + version byte).
8. Auto-routing stays benchmark-governed, not guess-driven.

### Accepted benchmark story

Treat the current benchmark line as the accepted baseline until superseded by a measured win.

Key claims already established:

- cold generic text search is near-`rg`
- large-file CPU path beats `rg`
- native GPU path remains experimental; current public dogfood has not accepted a 1GB/5GB correctness-and-speed crossover
- AST search beats `sg` on the accepted benchmark corpus
- rewrite plan/apply is at or near practical parity for harness use
- warm indexed search beats cold scans on repeated-query workloads

Do not reopen already-closed architecture work unless a benchmark regression forces it.

---

## What remains

### Workstream 1: API Freeze

Goal:
- treat the harness-facing contracts as public API and freeze them intentionally

Why:
- the project is now usable by agents and external harnesses
- undocumented or drifting JSON/NDJSON/MCP contracts will become the next source of instability

Tasks:

1. Version all machine-facing contracts explicitly:
   - search JSON
   - rewrite plan JSON
   - apply+verify JSON
   - calibrate JSON
   - NDJSON row format
   - MCP request/response payloads

2. Add compatibility policy docs:
   - additive field changes
   - breaking field changes
   - version bump rules

3. Add or extend compatibility tests:
   - parse golden artifacts
   - validate required fields and types
   - reject accidental contract drift

4. Publish example artifacts for the current accepted line:
   - normal search
   - indexed search
   - rewrite plan
   - apply+verify
   - GPU search
   - NDJSON stream sample
   - MCP tool response sample

Acceptance:
- all public output shapes are documented
- all public output shapes have locked compatibility tests
- no undocumented machine-facing payload remains

---

### Workstream 2: Benchmark Freeze

Goal:
- freeze the benchmark matrix that supports product claims

Why:
- the repo has enough power now that benchmark drift is the main risk
- routing and marketing claims must stay tied to reproducible measurements

Tasks:

1. Publish the benchmark matrix in docs:
   - cold text search
   - large-file CPU
   - large-file GPU
   - multi-GPU
   - repeated-query indexed
   - AST search
   - rewrite plan / diff / apply / verify
   - harness loop

2. Freeze artifacts / baseline naming conventions for:
   - CPU search
   - GPU search
   - AST search
   - rewrite
   - harness loop
   - index scaling

3. Add schema tests for benchmark artifacts:
   - explicit backend labels
   - environment capture
   - threshold metadata
   - artifact version / suite naming

4. Add benchmark publication guidance:
   - what counts as accepted
   - what must be re-run before updating docs
   - how to reject noisy or misleading wins

Acceptance:
- benchmark artifacts are reproducible and machine-comparable
- the benchmark matrix in docs matches the actual scripts
- no accepted performance claim is undocumented

---

### Workstream 3: Reliability and Soak Hardening

Goal:
- move from “passes tests” to “survives repeated real usage”

Why:
- the next failures are more likely to be long-run or degraded-state failures than simple unit regressions

Tasks:

1. Add soak scenarios:
   - repeated index build / update / query cycles
   - repeated calibrate / route / search cycles
   - repeated search -> plan -> diff -> apply -> verify cycles

2. Add fault-injection scenarios:
   - corrupt index
   - incompatible index version
   - partial GPU failure
   - CUDA unavailable at runtime
   - malformed GPU output
   - interrupted rewrite apply
   - stale-file races

3. Add mixed-repo safety scenarios:
   - BOM
   - CRLF / LF
   - non-ASCII
   - binary files
   - large-file guards

4. Define crash-recovery expectations:
   - atomic write guarantees
   - index rebuild guarantees
   - routing fallback behavior

Acceptance:
- the system recovers or fails cleanly under repeated and degraded conditions
- no silent corruption paths remain

---

### Workstream 4: External Harness Adoption

Goal:
- prove the tool works as the default search/edit substrate for real harness consumers

Why:
- internal tests are no longer enough
- the next proof point is consumption by external agent loops

Tasks:

1. Add one or two end-to-end harness integration fixtures that use only public interfaces:
   - CLI JSON / NDJSON
   - MCP tools

2. Publish a harness cookbook:
   - search flow
   - indexed search flow
   - rewrite planning flow
   - diff review flow
   - apply+verify flow
   - calibrate / routing interpretation

3. Add adoption smoke tests:
   - parse search JSON
   - parse rewrite plan JSON
   - parse combined apply+verify JSON
   - consume NDJSON streaming
   - invoke MCP rewrite/index tools

4. Add guidance for large data and large repos:
   - when to use index
   - when GPU should win
   - when GPU should not be forced

Acceptance:
- at least one real harness workflow is validated end-to-end against public APIs only
- docs are sufficient for another team or agent to consume the tool without repo spelunking

---

### Workstream 5: Governance

Goal:
- keep the wins from regressing

Why:
- once the core product exists, governance becomes the multiplier

Tasks:

1. Keep regression gates explicit for:
   - cold text search
   - large-file CPU
   - GPU crossover
   - multi-GPU
   - indexed warm-query
   - AST search
   - rewrite plan/apply
   - harness loop

2. Keep routing regression tests aligned with current routing docs and calibration logic.

3. Keep docs honest:
   - remove stale caveats as code changes
   - do not let routing docs drift from code
   - do not let examples drift from contract tests

4. Keep `AGENTS.md` current when architectural invariants change.

Acceptance:
- the repo stays benchmark-governed and contract-heavy instead of drifting back into guesswork

---

## Execution Order

Recommended order:

1. API Freeze
2. Benchmark Freeze
3. Reliability and Soak Hardening
4. External Harness Adoption
5. Governance cleanup and lock-in

This order is intentional:

- freeze the contract before more consumers rely on it
- freeze the benchmark matrix before more claims are made
- harden reliability before broader adoption
- prove harness use before calling the rollout complete

---

## Working Method

Keep using the repo rules from `AGENTS.md`:

1. failing test first
2. smallest defensible change
3. focused tests
4. full validation
5. relevant benchmark
6. reject regressions
7. update docs only after acceptance

Required validation before push for code changes:

```powershell
uv run ruff check .
uv run ruff format --check --preview .
uv run mypy src/tensor_grep
uv run pytest -q
```

Rust:

```powershell
cd rust_core
cargo test
```

Use the right benchmark for the changed surface. Do not claim a speedup without measured numbers on the current accepted baseline.

---

## What NOT to do

- Do not reopen already-closed architecture migrations without benchmark evidence.
- Do not reintroduce Python into the hot path.
- Do not widen auto-routing behavior without measured crossover proof.
- Do not break machine-facing contracts casually.
- Do not let benchmark docs diverge from actual scripts.
- Do not push from a dirty worktree when replay-worktree discipline is required.
- Do not describe a pushed branch or open PR as complete release work. It is only ready for review/merge until the release completion contract in `AGENTS.md` and `docs/SESSION_HANDOFF.md` has passed.

---

## Progress Tracker

Use this section to update progress as work lands.

- [x] API Freeze
- [x] Benchmark Freeze
- [x] Reliability and Soak Hardening
- [x] External Harness Adoption
- [x] Governance

Current in-flight slice:

- API Freeze:
  - added committed contract artifacts for `calibrate.json`, `search.ndjson`, and `mcp_rewrite_diff.json`
  - extended `docs/harness_api.md` with Calibrate JSON, Search NDJSON, MCP Tool Responses, and Compatibility Policy
  - extended schema compatibility coverage in both Python and Rust tests so these surfaces are now locked by the repo
- Benchmark Freeze:
  - converted `docs/benchmarks.md` from a dated snapshot into a benchmark matrix plus artifact/governance reference
  - normalized benchmark artifact stamping for `run_benchmarks.py`, `run_native_cpu_benchmarks.py`, and `run_ast_workflow_benchmarks.py`
  - added benchmark artifact schema tests covering the committed benchmark JSON surface
- Reliability and Soak Hardening:
  - added repeated-mutation index coverage to prove warm index rebuild/update cycles stay correct across add/modify/delete loops
  - added repeated `tg calibrate` overwrite coverage so the persisted crossover contract stays stable across reruns
  - added GPU sidecar recovery coverage proving a malformed payload failure does not poison the next successful invocation
- External Harness Adoption:
  - added `docs/harness_cookbook.md` covering public CLI JSON, NDJSON, rewrite, diff, apply+verify, MCP, and calibrate/routing flows
  - added README links pointing harness consumers to the contract and cookbook docs instead of requiring repo spelunking
  - added CLI-only adoption smoke coverage for search -> ndjson -> rewrite plan -> diff -> apply+verify using the native binary
  - added MCP adoption smoke coverage proving `tg_index_search`, `tg_rewrite_plan`, `tg_rewrite_diff`, and `tg_rewrite_apply` round-trip against the real native binary
- Governance:
  - added README governance links for the canonical benchmark, routing, harness contract, and harness cookbook docs
  - added public-doc regression coverage locking those links and the current routing policy backend inventory/decision rules

When a workstream is completed:

1. update this file
2. update any affected docs
3. record accepted benchmark lines in `docs/PAPER.md` when appropriate
