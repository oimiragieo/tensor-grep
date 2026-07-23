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

## Backlog & working process

The canonical prioritized work list lives in **[docs/BACKLOG.md](docs/BACKLOG.md)** — read it for what to
work next and how. It is kept in sync with the CLI task store; GitHub (`gh pr list`) is the source of truth
for PRs. **Subagents:** treat each backlog item's description + files + status as your brief. **CEO status** =
summarize its SHIPPING + P0/P1 sections.

The standing multi-model pipeline for any substantive item: deep-dive → **Fable audit** (find + fix-idea,
cite `file:line`) → **Exa** recency + competitive research (you are trained on stale data — verify current
facts) → plan (superpowers skills) → thinktank/Fable review the plan → **Sonnet build, TDD** → verify in the
REAL venv (`uv run --no-sync`; a worktree "tests pass" is a hypothesis, re-run in the main venv) →
`ruff check` + `ruff format --preview` + `mypy` → codex/Fable review the PR → **PR → drain**
(one-merge-per-publish, the push-race rule) → repeat until no issues. Isolate code agents with
`isolation:'worktree'`. Match model to task (haiku scan / sonnet build / opus+fable review). Run the
common-sense gate before pending any question to the CEO. Keep docs (this file, `docs/BACKLOG.md`,
`docs/SESSION_HANDOFF.md`, skills, CLAUDE.md) synchronized as work lands.

## Campaign Orchestration Disciplines (2026-07-08, hard-won)

Running a multi-PR drain+build campaign so fixes *land* instead of piling up. Each rule is a fix for a
concrete failure observed this session.

- **A1 — WIP cap.** No new *build* dispatch while >5 PRs are undrained OR the `main` gate is red. A red
  gate is a drop-everything hotfix that jumps the queue. Prevents "churning not completing" — generating
  faster than the ~40–66 min/publish drain empties (backlog stays constant-size = the smell).
- **A2 — A self-firing drain-cron beats a long-lived background drain.** A short-lived per-fire cron that
  merges ONE lowest-CLEAN PR (`gh pr merge --squash --delete-branch`, push-race-checked) is robust; a
  long-lived `drain.sh &` background process kept *dying* during the long CI/publish waits (and an inner
  `&` in a `run_in_background` wrapper orphaned it). Each fire is short-lived, so nothing can be killed
  mid-run. Push-race gate per fire: the latest `chore(release)` tag must be on PyPI AND `main` CI
  `completed` before merging.
- **A3 -- Mandatory adversarial security gate before merge.** Every security PR -- touching `apply_policy`
  / `mcp_server` / `*_backend` / an index-or-session lock / auth / money / migration / native asset /
  installer / doctor-probe construction -- gets an Opus "try to BREAK it, cite `file:line`, default
  FIX-FIRST if uncertain" review *before* merge. Not a rubber stamp: this session it returned SHIP on some
  and caught real issues on others (a symlink RCE bypass; a lock-release TOCTOU). The native-asset /
  installer / doctor-probe trigger was added after the v1.75.1-v1.75.3 GPU wave (#594-#596: WSL
  path-domain probe bridging, doctor probe failure taxonomy, calibrate/installer remediation) ran every PR
  through this same gate and it returned real `SHIP-WITH-NIT` / `SHIP` verdicts off 8/8 clean probes rather
  than a rubber stamp. `codex` is the nominal second vendor but its WSL path is unreliable -> Opus is the
  reliable substitute. Verdict shape: `SHIP` | `FIX-FIRST(+file:line + repro + minimal fix)`.
- **A4 — Resume a dead agent from its transcript.** A background subagent that dies with "terminated
  early due to an API error: 500" is REVIVED by `SendMessage` to its `agentId` (partial work intact) — do
  NOT re-dispatch fresh (loses the work). Happened 3× this session; all recovered.
- **A5 — Don't kill a build on staleness.** A complex build (a redesign + heavy test rewiring) legitimately
  runs >10–15 min between output flushes. A "stale > N min" heuristic kill destroys a *working* agent (a
  build was killed twice before its kill-note proved it was mid-work). Trust the completion notification;
  diagnose a suspected hang from the kill-note's last line, not an mtime guess.
- **A6 — Anti-hang test protocol.** Wrap every test run in a shell `timeout` (`timeout 120 uv run
  --no-sync … pytest … --timeout=15`), and write the fix *before* the red-phase adversarial test — a
  ReDoS/deadlock red-test executed against un-fixed code IS the hang it is testing. Distinguish
  slow-but-protected from hung by exit code (124 timeout / 137 SIGKILL), not elapsed time.
- **A7 — Harvest a worktree agent's work, then re-verify.** A worktree agent's "tests pass" is a
  hypothesis (its venv may lack the compiled `rust_core` ext). Cherry-pick its commit onto a fresh branch
  off `origin/main`, re-verify in the real venv + `ruff`/`format --preview`/`mypy` + a live smoke, THEN the
  gate, THEN PR.
- **A8 — Fable is reachable only via `Agent(model:fable)`.** A Workflow `agent()` call cannot reach Fable —
  it silently falls back to the session model. Dispatch Fable design/audit seats as `Agent` subagents,
  never inside a `Workflow`.
- **A9 -- Probe liveness via `SendMessage` before any `TaskStop`.** A background subagent's output-file
  mtime/size is UNRELIABLE (0KB for 40-57 min while foreground-compiling). The reliable alive-vs-paused
  tell is a `SendMessage` probe: a reply of "Message queued...at its next tool round" means ALIVE;
  "had no active task; resumed from transcript" means it WAS PAUSED. Corroborate with Pyright
  `<new-diagnostics>` on its file-writes plus the active build-process count. Cross-ref A5 ("don't kill on
  staleness") -- this probe is the mechanism A5's "trust the completion notification" actually relies on.
  Codified as the global skill `agent-liveness-probe` — load it before killing, restarting, or
  `TaskStop`-ing anything that looks stalled.
- **A10 -- A no-verdict council seat is a FAILED seat, not a blocker.** The codex thinktank seat can hang
  on an MCP-auth spin (cloudflare/sentry `invalid_token`) -> 0KB output, no anchored verdict. Treat it as
  FAILED: kill it, sweep the orphaned processes it left behind (20+ stale codex processes found in one
  session), and synthesize from the surviving Opus lenses instead of waiting on it.
- **A11 -- Design-review-before-build** (CEO directive #174). Fable designs a plan -> a thinktank council
  certifies the PLAN itself is sound and ready (not findings, not a diff) -> bake must-fixes into the plan
  -> Sonnet builds TDD-first (worktree, foreground-gate) -> mandatory adversarial Opus gate (now including
  native-asset/installer/doctor-probe work, see the A3 extension above) -> drain one PR per publish. This
  sequence caught a CI-reddening fix, an ordering bug, and a GPU-oversell claim BEFORE any code was built
  this session.
- **A12 -- CPU-safe shared-server discipline.** This desktop is a SHARED machine (Operating Rule #3); other
  AI/omega-* services run concurrently. CPU-heavy work (loading/inferring a dense-embedding model, a full-
  corpus rerank sweep, a wide benchmark matrix, a cold `cargo check`) must NOT run locally and starve them —
  route it to cloud `Agent` subagents or GitHub Actions CI. The entire `tg find` build+eval campaign (#189)
  ran this way: zero local CPU. A bounded probe (a handful of queries, not the full golden set) is fine to
  sanity-check wiring; push the real evaluation to CI/a subagent. The cron tick itself is cloud-side and is
  not the problem — local process SPAWNS (codex/droid/gemini/cargo/rustc) are. Receipt: a 2026-07-16 GPU
  deep-dive fanned out local codex+droid + a cold cuda `cargo check` and saturated the CPU (3 orphaned codex
  procs killed).
- **A13 — Rapid-window batch-merge collapses N release cycles to 1 (C-batch).** Several independently-green,
  already-CI-passing PRs can land ~15-20s apart in one gate-open window as a SINGLE combined release;
  intermediate concurrency-cancelled/rejected-looking runs on the earlier pushes in that window are benign
  as long as the newest `main` run goes fully green. Receipts: v1.91.0 and v1.93.0 (the latter combining
  #703-706: run `29890576036` rejected-only, `29890612228` published). Distinguish deliberately from the
  ACCIDENTAL v1.17.23/#318/#319 push-race (an unintended two-writer collision, not a planned drain).
- **A14 — Event-driven release watching + a cron floor (C-event).** Prefer a background `gh run watch
  <run-id> --exit-status` (chained off its own ~10-min expiry notification) over blind long-interval polling
  when waiting on a release; pair it with a cron floor (e.g. :02/:32-style offsets) that embeds the FULL
  remaining pipeline instructions in the prompt itself so completion survives a crash or context loss.
- **A15 — Session-only crons die on crash/reboot; always recreate (C-cron).** A `/loop` invocation is
  session-bound and is not a durability substitute for a `CronCreate` drain-cron; `MEMORY.md` is the
  crash-safe state carrier that lets a recreated cron resume correctly (proven across a real PC crash
  mid-campaign).
- **A16 — Pin-first ranking gate (C-pin).** Before touching any scorer/graph/ranking code, write a test that
  pins the CURRENT ranked output GREEN on base; after the change, the only acceptable diff is the intended
  one — any legitimate-entry reorder is a STOP-finding, not noise to relax away. Receipt: #709,
  `test_blast_radius_legitimate_dependent_ranking_pin`.
- **A17 — Scheduler-independent concurrency tests (C-concurrency).** Never assert wall-clock thread overlap
  (a starved runner serializes legitimately and false-fails); assert the CONTRACT with `threading.Event`
  handshakes plus bounded acquire attempts (independence case + the converse mutual-exclusion case). This
  killed a 2-release flaky. Receipt: #701, `test_index_lock_is_per_root_not_global`.
- **A18 — A build agent's self-gate is a hypothesis, not clearance (C-independent-gate, extends A3).** A
  SEPARATE, independently-framed gate can still return SHIP-WITH-NITS on one pass and a distinct verdict on
  a re-drafted pass of the same PR — re-draft until the independent gate (not the build agent's own review)
  says SHIP. Receipt: #698.
- **A19 — Fold safety/honesty nits before merge; bank cosmetic ones (C-nit).** A gate finding that changes
  observable behavior (a fail-open read, a misleading status, a missing migration-honesty note) folds into
  the SAME PR before merge; a purely cosmetic nit (naming, comment wording, a stale citation) is banked as a
  follow-up and batch-closed later. Receipts: #704/#706 folded pre-merge, #708 batch-closed the banked
  cosmetic set.
- **A20 — Published-wheel verdict-table dogfood closes a campaign (C-wheel).** Before declaring a multi-PR
  campaign done, probe every fixed item against the ACTUALLY PUBLISHED wheel in a clean env (`uvx --from
  tensor-grep@<ver>`), one PASS/FAIL row per item backed by the raw JSON, not a verdict word alone —
  pre-build fixtures, read the raw JSON before scoring (a probe-shape misread reads as a false fail), and
  watch for pipe exit-code masking (`cmd | tail` reports `tail`'s exit code, not `cmd`'s). Receipt:
  2026-07-22, 7/7 clean.
- **A21 — The per-task-pinned accuracy gate is the loop-4 instrument (C-loop4).**
  `tests/eval/test_agent_accuracy.py::test_agent_accuracy_gate` (`assert not misses`) surfaces exactly the
  kind of ranking/routing regression a code-review gate rationalizes away — it caught #250 (a `tg prepare`
  CLI-dispatcher misroute), which was then fixed and locked as a new permanent pinned task. Every real
  misroute found in the wild becomes a new permanent pinned task; this is a capability-regression gate,
  distinct from a contract test.

## Current Handoff

release_docs_current_tag: v1.93.4

As of 2026-06-26, the current tagged release state is `v1.93.4`, and the latest complete public PyPI/release-asset distribution is also `v1.93.4`. The stable installer, release-native asset publication, managed-native `tg upgrade` refresh path, stale tensor-grep-owned `tg.com` bridge refresh after upgrade, native-front-door CLI parity fixes, Windows `.cmd` quoted-pattern launcher fix, native-first Windows PATH ordering, top-level validation-command contract, local default `classify`, classify provider provenance, fixed multi-pattern native CPU search, GPU scale benchmark correctness gates, launcher-route observability, benchmark launcher attribution, scoped GPU device probing, benchmark launcher warnings, opt-in `tg agent` Actionable Context Capsule, mixed-language capsule confidence/validation alignment, GPU benchmark recommendation hygiene, edit JSON/rollback safety, explicit language/file-name agent ranking, Windows validation-command quoting, docs/version governance, `$file` / `{file}` validation placeholder substitution, native CUDA correctness gates, ambiguous capsule alternative-target surfacing, root help-menu diagnostics, foreign launcher diagnostics, benchmark promotion-gate taxonomy, agent workflow benchmark governance, capsule alternative-confidence capping, generic provider-token `secrets-basic` regex rules, release-docs synchronization, release wheel Cargo prefetch retries, native GPU/search accuracy hardening, explicit Windows Python subprocess launcher repair, agent capsule hardcase routing, Windows subprocess bridge ranking hardening, and long-lived agent-loop memory/cache caps are released through `v1.93.4` GitHub assets and PyPI. Follow-up work should focus on context/session latency, GPU production viability, token economy, call-site evidence, AST parity roadmap, classify provider/cache UX, and keeping docs synchronized with release proof.

**2026-07-14 Current-Handoff addendum -- GPU Phase-0 hardening wave (v1.75.1-v1.75.4, audit #171).** Four
PRs closed audit #171's P0-1 through P0-5 GPU findings, each behind the mandatory Opus adversarial gate
(SHIP / SHIP-WITH-NIT verdicts, 8/8 probes clean): `#594` (v1.75.1) bridged a WSL path-domain mismatch in
the doctor/agent GPU probes (a Windows-target binary resolved from WSL cannot open a `/tmp/...` sentinel
path -- the probe now detects cross-domain, translates the path via `wslpath -w`, and fails closed to a
distinct `path_domain_mismatch` status instead of a generic "failed") and added a `cargo check --features
cuda` anti-bit-rot CI gate so the `cuda` Cargo feature -- normally compiled only by release legs gated on
the `TENSOR_GREP_RELEASE_NATIVE_ASSET_PROFILE` repository variable equalling `native-frontdoor-gpu` -- is
checked on every PR instead of rotting silently between releases; `#595` (v1.75.2) replaced the doctor's
opaque GPU-probe `status="failed"` with a structured `native_error_kind` taxonomy (`failed_path_bridging`
/ `failed_input` / `failed_gpu_unavailable` / `failed_other`) and added an honest out-of-range
`--gpu-device-ids` warning instead of an indistinguishable silent CPU fallback; `#596` (v1.75.3) added a
`calibrate` remediation message on both native bail arms plus a loud nvidia-requested/cpu-delivered
installer downgrade warning; `#597` (v1.75.4) closed 5 gate-nits from the Opus review of the prior three
(evidence-path translation, doctor version dedup/reorder, a cross-domain-conditional `path_not_found`
fix, an invalid-device-id classification fix, and co-gating `sanitize_cuda_detail` plus its callers under
`#[cfg(any(feature = "cuda", test))]` so a default `cargo test` actually compiles and runs its unit tests
instead of silently skipping them -- see the CI/Release Rules bullet on this pattern below). Separately,
`#593` (v1.75.0) shipped an unrelated `tg orient` / `tg agent` improvement (M1+M2: broadened
`suggested_ignore` whole vendor/skill-tree detection with a new STRONG-0 promotion tier) that landed in
the same version range by coincidence of publish order, not as part of the GPU wave -- verify-before-cite
matters even for a version range handed down in a task brief. See `docs/gpu_crossover.md` for the GPU
promotion-status read and the Roadmap Sequencing section for the Phase 0/1/2 framing this wave completes
Phase 0 of.

**2026-07-16 addendum -- `tg find` CPU semantic moat (v1.77.0-v1.78.1, campaign #189).** Three build
waves plus an MCP tool shipped whole-repo natural-language code search -- the CPU-only ColGrep-class
response: BM25 + local CPU dense embeddings -> weighted RRF -> optional MaxSim -> budget-fitted
`file:line` output. `#626` (v1.77.0) shipped the CLI `tg find` through the standard 4-site registration
path with a fail-closed matrix (`BackendExecutionError` -> exit-2; internal chunk-cap /
`--max-repo-files` / `--deadline` truncation -> `result_incomplete=true` + exit-2, never a silent
partial-as-complete). `#627` (v1.78.0) shipped the MCP `tg_find` tool as its OWN PR to de-risk the
LLM-facing surface (see `docs/harness_api.md` for the contract). `#628` shipped the default-OFF
`TG_FIND_DENSE_WEIGHT` adaptive knob (byte-identical no-op at `1.0`), landing inside the `v1.78.1` patch
release together with the unrelated `#632` `mcp` CVE-2026-52870 dependency floor bump; `#630` (on top of
`v1.78.1`, unreleased `chore:` commit) hardened the knob's query classifier from a `split_terms`
morpheme-count floor to a whitespace-word-count gate plus a `math.isfinite` nan/inf clamp -- still
default-OFF, NOT the flip. BM25-only degrade is visible/legitimate (`rank_fallback_reason`).
**Process note:** both Opus gates caught real defects the plan missed -- a query-time
`DenseUnavailableError` that would have crashed instead of BM25-degrading (a Backend Fail-Closed
Contract violation, fixed `045fadc`), and a missed MCP contract-version bump (fixed `3fcca06`; see the
5th-registration-site note below).

**2026-07-22 Current-Handoff addendum -- session-capture wave (v1.91.1 -> v1.93.2, 15 shipped items,
A1-A15 in `scratchpad/ground_truth_v1932.md`).** Headline shape: a cold-path SLA fix, a ranking-accuracy
fix, three honesty/fail-closed fixes (dynamic-import resolution, GPU cross-domain probing, blast-radius
scoring), one intra-file-parallelism ship scoped to a single fallback engine, one test-harness hardening
(per-task-pinned accuracy gate), and a UX/coordination batch (install-dense hint unification, doctor
autostart honesty, `tg prepare --out`/`--claim` agent-id-hint, `tg ledger` PATH canonicalization).
`#691` (v1.91.1) bounded the quadratic reverse-import BFS + 4 sibling call sites under `--deadline`
(26.6s -> 9.5s class). `#693`/#250 (v1.91.2) demoted thin CLI-dispatcher wrappers below real
implementations in `tg prepare`/`tg agent` primary-target ranking, taking the per-task-pinned
agent-accuracy gate (`#696`/#252) from 15/16 to 16/16 -- this is the loop-4 receipt (A21/C-loop4 above).
`#695` (v1.91.3) shipped intra-file rayon parallelism ONLY on the `backend_cpu.rs` PyO3/FFI fallback
path (fresh-pip/`TG_DISABLE_NATIVE_TG`/no-rg); the default `native_search.rs` streaming path stays
deliberately serial for its tested >=25ms first-match contract -- do not cite one engine's numbers for
the other (see `tensor-grep-architecture-contract`'s A3 split). `#697` (v1.92.0) shipped the
default-OFF `TG_CAPSULE_INLINE_CALLERS` inline-annotation env var. `#698`/#253 (v1.92.1) closed a
chunk-parallel binary-detection gap via an independent-gate re-draft (A18/C-independent-gate). `#699`/
#254 (v1.92.2) hardened the flat `_score_symbol` scorer with a word-boundary bonus and a test-file
demotion (see `code-search-and-retrieval-reference` section 3). `#701` redesigned the index-lock
concurrency test to a scheduler-independent Event-handshake contract (A17/C-concurrency), killing a
2-release flaky. `#702` (v1.92.3) closed the flag-less bootstrap unscoped-search fast-refuse gap (the
same `IMPLICIT_SEARCH_WALK_FILE_CEILING=1500` constant now fires on all 3 doors). `#703`-`#706` landed
in one rapid-window batch-merge as combined release v1.93.0 (A13/C-batch): dynamic-import honesty
(`dynamic_unresolved`, never a same-named decoy), the WSL cross-domain GPU-probe fix, a UX/honesty
batch (install-dense hint, doctor `session_daemon.autostart`, `tg prepare --out`/`agent_id_hint`), and
the `tg ledger` PATH-canonicalization fix (claim/release/list now resolve to the nearest `.git`
ancestor; Slice 2 record/find UNCHANGED). `#708` (v1.93.1) batch-closed banked cosmetic gate-nits
(A19/C-nit). `#709` (v1.93.2) closed the blast-radius scoring-prefilter's fuzzy-match of
`dynamic_unresolved` literals, behind a pin-first ranking gate (A16/C-pin) that proved zero legitimate
reorder. **Research retirements from the same wave (durable, do not re-chase):** cAST structural
chunking REJECTED as default (net-wash quality, 24.4x slower, 38% bigger chunks -- see
`tensor-grep-failure-archaeology` Battle 17); dense int8/PCA compression DEFERRED (numpy is ~2x SLOWER
without SIMD, banked #255); many-pattern Aho-Corasick has a LIVE dedup over-count bug, guarded not
fixed (#694, banked #255); warm-session search serving is a BIG-REFACTOR (the daemon holds a symbol
map, not a search index; free partial win: `tg mcp`'s long-lived process keeps CPUBackend caches warm);
GPU-for-search has NO crossover at any scale and the shipped kernel is brute-force, NOT PFAC (publish
stays HOLD, #169). Meta-lesson: verify every "cheap win" against the live code before building -- 5 of
5 candidates this wave came back negative/big-refactor/secondary-path once checked.

- Recent fix commits:
  - `a840cd4 fix(search): tg search --rank errored in plain-text mode (#275)`
  - `1137537 fix(license): declare Apache-2.0 consistently across Cargo.toml + npm (#271)`
  - `b0c7cf6 fix: harden v1.13.14 dogfood contracts`
  - `1e09e59 fix: bound agent-loop memory and dogfood contracts`
  - `21e5437 fix: collect capsule call-site evidence`
  - `8a73f8d fix: harden agent bridge ranking`
  - `b601366 fix: harden agent output budget hygiene`
  - `2aebac6 fix: harden ast cli contract hygiene (#140)`
  - `bbc08e4 fix: harden rg flag contract aliases (#139)`
  - `21627d2 fix: harden v1.12.8 dogfood contracts`
  - `f848748 fix: route cold rg-shaped searches to rg (#137)`
  - `c2e483a fix: harden exe bridge agent ranking (#136)`
  - `cdbdfcc fix: accept ast run pattern aliases (#135)`
  - `3940b15 fix: bound map and context agent outputs (#134)`
  - `0f03e58 fix: cap compat routing artifact payloads (#132)`
  - `b746dec fix: bound edit-plan repo scans (#131)`
  - `55c1f1d fix: harden v1.12.7 release positioning governance (#133)`
  - `da44a2f fix: harden v1.12.6 dogfood cli contracts`
  - `1783e92 fix: harden Windows subprocess exe bridge`
  - `f75e24a fix: harden gpu proof benchmark hygiene`
  - `affe7a7 fix: keep rust validation for agent cli intents`
  - `6b2016c fix: clarify ast subset positioning`
  - `b038ed5 fix: restore compat schema governance`
  - `aeead68 fix: align public search flag routing`
  - `a78e33c fix: harden post-release docs governance`
  - `2100122 fix: harden release docs stamp governance`
  - `361e0db fix: harden public GPU unavailable routing`
  - `87d4ca4 fix: accelerate fixed multi-pattern native search`
  - `ada6a47 fix: expose classify provider provenance (#110)`
  - `6ad69b5 fix: harden agent capsule hardcases (#109)`
  - `9ddd20b fix: expose GPU promotion blockers`
  - `dd995fc fix: add explicit Windows subprocess launcher repair`
  - `b0df720 fix: harden v1.10.8 release docs governance`
  - `6ee1d53 fix: harden v1.10.7 dogfood followups`
  - `57f9ada fix: harden gpu search accuracy contracts`
  - `03db0ff fix: harden v1.10.4 dogfood followups`
  - `8aecfea fix: harden release wheel retries`
  - `ca9df12 fix: harden v1.9.9 dogfood followups`
  - `21449bf fix: add agent workflow benchmark governance`
  - `f300cf3 fix: refresh stale tg.com bridge after upgrade`
  - `4ff7a77 fix: clarify GPU benchmark promotion gates`
  - `05ea29e fix: harden v1.9.5 dogfood blockers`
  - `23e5f52 fix: harden GPU gates and launcher diagnostics`
  - `646b089 fix: harden docs governance and validation placeholders`
  - `73c5f91 fix: harden agent ranking docs and validation quoting`
  - `faf67ed fix: harden edit JSON and capsule validation trust`
  - `5791489 fix: harden agent capsule trust alignment`
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

**Historical release proof (pre-v1.17.11 — retained for the audit trail). The authoritative current-release facts are the `release_docs_current_tag` / current-tag fields above; the run IDs below are OLD (v1.11.0–v1.13.x) and are NOT proof of the current release:**

- `v1.11.0` GitHub release: <https://github.com/oimiragieo/tensor-grep/releases/tag/v1.11.0> exists, but main CI run `25834508800` was cancelled during release-native asset publication; `publish-success-gate` failed and PyPI latest remains `1.10.10`.
- Main CI run `26513809791`: passed the pre-release matrix, semantic-release, PyPI artifact validation, `publish-github-release-assets`, `publish-pypi`, and `publish-success-gate`
- Main dynamic/CodeQL run `26513808787`: passed on the `3c0c213` merge commit
- Release commit `bd7035c`: published `v1.13.23` with `[skip ci]` after main CI completed
- Previous `v1.13.22` proof runs `26473492381` and `26473490540` remain retained as historical release proof
- Previous `v1.13.21` proof runs `26450640497` and `26450639894` remain retained as historical release proof
- Previous `v1.13.20` proof runs `26437847778` and `26437847528` remain retained as historical release proof
- Previous `v1.13.19` proof runs `26431129535` and `26431129155` remain retained as historical release proof
- Previous `v1.13.18` proof runs `26425383595` and `26425914836` remain retained as historical release proof
- Previous `v1.13.15` proof runs `26386327552`, `26386327168`, `26386976717`, and `26386978124` remain retained as historical release proof
- Main CI run `25951521056`: passed the pre-release matrix, semantic-release, PyPI wheel/sdist validation, `publish-github-release-assets`, `publish-pypi`, and `publish-success-gate`
- Main CodeQL run `25951813292`: passed on the `v1.12.14` release line
- PyPI pinned install: `uvx --refresh-package tensor-grep --from tensor-grep==1.93.4 tg --version` reports `tensor-grep 1.93.4`
- GitHub release: <https://github.com/oimiragieo/tensor-grep/releases/tag/v1.93.4>
- Main CI run `25866871838`: passed the pre-release matrix, semantic-release, PyPI artifact validation, `publish-github-release-assets`, `publish-pypi`, and `publish-success-gate`
- GitHub release assets: `tg-windows-amd64-cpu.exe`, `tg-linux-amd64-cpu`, `tg-macos-amd64-cpu`, checksums, winget manifest, Homebrew formula, and publish instructions are uploaded and verified on `v1.12.14`
- Public `v1.12.14` dogfood: release CI, assets, PyPI, and `uvx --refresh-package tensor-grep --from tensor-grep==1.12.14 tg --version` verified `tensor-grep 1.12.14`; the release includes `21e5437 fix: collect capsule call-site evidence` while preserving `8a73f8d fix: harden agent bridge ranking`, `b601366 fix: harden agent output budget hygiene`, `2aebac6 fix: harden ast cli contract hygiene (#140)`, `bbc08e4 fix: harden rg flag contract aliases (#139)`, and the accepted v1.12.8-v1.12.13 dogfood contract fixes. Public managed GPU is not promotion-ready.
- Public `v1.12.12` dogfood: release CI, assets, PyPI, and `uvx --refresh-package tensor-grep --from tensor-grep==1.12.12 tg --version` verified `tensor-grep 1.12.12`; the release includes `b601366 fix: harden agent output budget hygiene` while preserving `2aebac6 fix: harden ast cli contract hygiene (#140)`, `bbc08e4 fix: harden rg flag contract aliases (#139)`, `21627d2 fix: harden v1.12.8 dogfood contracts`, `f848748 fix: route cold rg-shaped searches to rg (#137)`, `da44a2f fix: harden v1.12.6 dogfood cli contracts`, bounded map/context output, `tg run --pattern`, Windows subprocess bridge ranking hardening, `a78e33c fix: harden post-release docs governance`, `361e0db fix: harden public GPU unavailable routing`, `2100122 fix: harden release docs stamp governance`, and the `87d4ca4 fix: accelerate fixed multi-pattern native search` CPU lane from `v1.11.3`. Explicit public GPU requests without sidecar configuration report native GPU unavailable and fall back to `NativeCpuBackend`; public managed GPU is not promotion-ready.
- Public `v1.11.5` dogfood: release CI, assets, PyPI, and `uvx --refresh-package tensor-grep --from tensor-grep==1.11.5 tg --version` verified `tensor-grep 1.11.5`; the release includes `a78e33c fix: harden post-release docs governance` while preserving `361e0db fix: harden public GPU unavailable routing`, `2100122 fix: harden release docs stamp governance`, and the `87d4ca4 fix: accelerate fixed multi-pattern native search` CPU lane from `v1.11.3`.
- Public `v1.11.2` dogfood: release CI, assets, PyPI, and `uvx --refresh-package tensor-grep --from tensor-grep==1.11.2 tg --version` verified `tensor-grep 1.11.2`; the release also exposes classify provider provenance so JSON harnesses can distinguish local deterministic classification from opt-in provider-backed classification.
- Public `v1.10.10` GPU evidence remains experimental: explicit managed GPU requests still report `GpuSidecar` / unsupported rather than a qualifying `NativeGpuBackend` row, so no GPU speed promotion is made.
- Public `v1.10.8` dogfood: release CI, assets, PyPI, `uvx --refresh-package tensor-grep --from tensor-grep==1.10.8 tg --version`, managed `tg upgrade`, fresh `cmd /c tg --version`, fresh `pwsh -NoProfile -Command "tg --version"`, and direct managed native `tg.exe` all verified `1.10.8`. Python `subprocess.run(["tg", "--version"])` still resolved the foreign Together CLI `tg.exe` from Machine PATH on this host; `tg doctor --json` reported the route as `foreign` with Machine PATH remediation and did not delete or overwrite unrelated launchers.
- Public `v1.10.7` dogfood: release CI, assets, PyPI, managed `tg upgrade`, fresh `cmd /c tg --version`, fresh `pwsh -NoProfile -Command "tg --version"`, and managed native `tg.exe` all verified `tg 1.10.7`. The remaining public-launcher blocker was Python `subprocess.run(["tg", ...])` resolving a foreign Together CLI `tg.exe` when Windows `CreateProcess` chooses `.exe` ahead of the tensor-grep `.com` bridge in the same directory.
- Public `v1.9.11` source/GitHub/PyPI dogfood: the release-wheel retry follow-up prefetches Cargo dependencies before PyPI artifact builds, publishes all PyPI distributions, and `uvx --from tensor-grep==1.9.11 tg --version` reports `tensor-grep 1.9.11`.
- Public `v1.9.10` source/GitHub-asset dogfood: the release contains the v1.9.9 dogfood follow-ups, but PyPI publication was incomplete until the v1.9.11 release-wheel retry follow-up published a replacement patch.
- Public `v1.9.9` dogfood: direct managed native `C:\Users\oimir\.tensor-grep\bin\tg.exe --version` reports `tg 1.9.9`; PyPI `tensor-grep==1.9.9` resolves; `uvx --from tensor-grep==1.9.9 tg --version` reports `tensor-grep 1.9.9`; `tg update` advanced the managed sidecar and front door from `1.9.8` to `1.9.9`; fresh `cmd`, unprofiled `pwsh`, and the managed native front door report `tg 1.9.9`.
- Prior public update dogfood: `tg update` from `v1.9.3` initially hit PyPI propagation lag, then installed sidecar `tensor-grep==1.9.4`, scheduled/refreshed the managed native front door, and verified `tg 1.9.4`. Profiled PowerShell, `cmd`, `pwsh -NoProfile`, WSL, Git Bash, and direct managed native `tg.exe` resolved `tg 1.9.4`; `tg doctor --json` reported `version = 1.9.4`, `rust_binary_version_status = matches`, `search_acceleration_backend = standalone-native-tg`, `path_tg_first_launcher_kind = cmd-shim`, `fresh_shell_path_tg_first_launcher_kind = managed-native`, and a `path_tg_launcher_warning` for current shells that still route through the compatibility shim before fresh-shell PATH.
- Prior public installer dogfood: rerunning `scripts/install.ps1` for `v1.8.31` put `C:\Users\oimir\.tensor-grep\bin` ahead of compatibility shim directories on User PATH. A simulated fresh shell resolves `C:\Users\oimir\.tensor-grep\bin\tg.exe` before `C:\Users\oimir\bin\tg.cmd`.
- Public launcher dogfood: `cmd /c tg`, direct managed `tg.cmd`, native `tg.exe`, and Python `subprocess.run([...])` preserve fresh quoted no-match phrases and return exit `1` without false-positive stdout.
- Post-`v1.9.6` local dogfood: native CUDA release search passes exact match/file-set correctness on both RTX 4070 (`sm_89`) and RTX 5070 (`sm_120`) smoke corpora plus 1GB/5GB scale gates, but remains slower than both `rg` and `tg_cpu`; GPU sidecar rows are marked unsupported for native CUDA scale gates unless the benchmark uses a CUDA-enabled native binary; root `tg --help` advertises current agent/GPU/launcher/validation settings; and `tg doctor --json` classifies unrelated first-PATH `tg` commands such as Together CLI as `foreign` with explicit remediation. On this host, local fresh-shell dogfood was repaired non-destructively by placing a tensor-grep `tg.com` bridge ahead of the foreign `tg.exe` in the same directory after `tg update` moved from 1.9.5 to 1.9.6, because Machine PATH ordering was not writable.
- Session handoff: `docs/SESSION_HANDOFF.md`
- Current follow-up work is tracked in `docs/SESSION_HANDOFF.md`: keep release-native assets verified, preserve the managed installer fallback when assets are absent, keep sidecar and native front-door versions aligned after `tg upgrade`, keep current-process vs fresh-shell launcher routing visible in `tg doctor`, preserve benchmark launcher command-kind attribution and warnings, harden the opt-in `tg agent` context capsule/token-economy surface without changing raw search contracts, keep mixed-language capsule confidence/validation alignment honest, and keep GPU/provider paths experimental until correctness, speed, and UX are proven.

The latest accepted release line fixed the Windows `--files-with-matches` rg-backed argument-vector failure, raw rg-style no-path `--files-with-matches` output, malformed pinned Windows installer extras, root-based path-list output, `-0/--null` path-list/count parsing, `tg ast-info --json`, argv-safe PowerShell shims, UTF-8 path-list output, inaccessible PATH-entry handling, managed shim installation, stale Python package cleanup when an old `Python*\Scripts\tg.exe` shadows managed shims, argv-safe `.cmd` bridging, Git Bash / WSL no-extension shims, WSL-aware `/mnt/c/...` paths, LF-only generated bash shims, one-line default version output with verbose details behind `--verbose`, public `Usage: tg` help text, explicit `doctor` diagnostics for stale in-tree native binaries, implicit stale-native skipping for dev searches, public `--format rg` help text for exact ripgrep-style output, context-render/MCP trust invariants, validation command provenance, sorted rg parity edges for files-with-matches, files-without-match, replacement output, and PCRE2 output, multiline rg parity forwarding, exact-symbol context ranking over camel/snake bridge heuristics, explicit language/file-name ranking for Python intent, session stale-file filtering and no-runner validation consistency, embedded checkpoint fallback for MCP rewrite apply when standalone native `tg` is unavailable, inline scan rule severity/message preservation, uppercase `API_KEY` secret scanning, explicit broad generated-root scan refusal unless callers bound the search or opt in, managed native front-door refresh after `tg upgrade`, native-front-door parity for `tg search --files`, `tg search --multiline` / `-U`, `tg search --null`, `tg run -r`, and `tg classify --format json`, classify fallback before expensive provider/model setup when unavailable, GPU benchmark no-match correctness handling, Windows `.cmd` quoted multi-word no-match patterns from `cmd.exe`, direct `tg.cmd`, and Python `subprocess.run([...])`, Windows installer User PATH ordering that puts the managed native front-door directory ahead of compatibility shim directories, top-level `validation_commands` on both `context-render` and `edit-plan` JSON, deterministic local default `classify` unless `TENSOR_GREP_CLASSIFY_PROVIDER=cybert` opts into CyBERT/Triton, GPU benchmark defaults/correctness checks for 1GB and 5GB scale rows, explicit GPU device probing that does not initialize or warn about unselected GPUs, benchmark script warnings when timings include shim or interpreter overhead, stale in-tree native binary benchmark refusal by default, parseable edit JSON and rollback on validation failure, quoted Windows validation commands with spaces, `$file` / `{file}` validation placeholder substitution, per-edited-file validation for directory rewrites, and docs-governance tests aligned with current release metadata.

Known current weak spots:

- `rg` remains the raw cold exact-text benchmark; `tg` should be treated as the agent-native code intelligence layer.
- `ast-grep` remains the structural-search feature/performance baseline; `tg run` is a useful validated AST slice, not a blanket ast-grep replacement.
- `context-render` and MCP context output are agent trust surfaces. `edit_plan_seed.primary_file`, `navigation_pack.primary_target.file`, selected files/sources, follow-up reads, and `rendered_context` must agree or `context_consistency` must report the omission and confidence downgrade.
- Agents must inspect top-level `ambiguity` before editing. `ambiguity.status = "tie_requires_confirmation"` is a hard stop for autonomous edits. `ambiguity.status = "tie_resolved"` is acceptable only when `ambiguity.resolved_by` contains explicit evidence.
- Default JSON/LLM context rendering must include executable behavior for selected functions. Compact rendering can strip low-value text, but it must not reduce selected code to signatures unless a future summary-only profile explicitly asks for that.
- Validation commands are hints with provenance. Require `validation_plan[].detection`, do not suggest npm/package-manager commands without `package.json` evidence, do not suggest Python test commands without Python/test/project evidence, and omit commands entirely when no runner evidence exists.
- Validation commands must align with the selected primary target language unless verified cross-language dependency evidence exists. `validation_alignment` should report filtered mismatches; do not silently pair a TypeScript primary target with pytest-only validation or a Python primary target with JS-only validation.
- Unbounded broad generated-root scans are hostile to unattended agents. `tg search --files --hidden` and no-ignore/unrestricted fallback scans now refuse roots that are generated/cache/dependency directories, or that contain them, unless the request is bounded by `--glob`, `--type`, or `--max-depth`, or explicitly opts in with `--allow-broad-generated-scan`. Use scoped paths, globs, file types, and `--max-depth` for `tg search` before reaching for opt-in. `--max-repo-files`, `--max-callers`, and `--max-files` are code-intelligence command budgets, not `tg search` flags.
- `tg map`/`tg orient` and `tg inventory` scan different file-count tiers by design, not by bug: `tg map`/`tg orient` AST-index a bounded set of files (`--max-repo-files` defaults to `DEFAULT_AGENT_REPO_MAP_LIMIT = 2000`, `src/tensor_grep/cli/repo_map.py`, full parse per file; note the separate per-file caller-scan ceiling `CALLER_SCAN_FILE_CEILING = 512`), `tg inventory` walks up to `DEFAULT_MAX_INVENTORY_FILES = 50000` files (`src/tensor_grep/cli/inventory.py`, stat + 8KB sniff, no parse), and a raw `tg search` scans the full tree with no file-count cap. Do not read a larger `tg inventory` total than `tg map`'s `files` count on the same repo as a discrepancy to fix.
- Prefer `blast-radius` over `impact --symbol` when direct symbol impact matters.
- Windows launcher/path-list hardening should force UTF-8 for managed shims and Python path-list output; still scope broad file-list commands to avoid generated-tree volume.
- If `cmd /c tg --version`, `pwsh -NoProfile -Command "tg --version"`, or Python `subprocess.run(["tg", "--version"])` resolves a tensor-grep-owned or self-identifying tensor-grep `Python*\Scripts\tg.exe` ahead of the managed native front door, treat it as installer regression evidence. The Windows installer and `tg repair-launcher` should remove verified-owned launchers or back up self-identifying orphaned tensor-grep launchers instead of only warning about them. If that command reports another product's version, treat it as a foreign PATH-shadow blocker: report remediation and keep readiness failing, but do not delete or overwrite the unrelated launcher unless the operator explicitly runs `tg repair-launcher --allow-foreign-rename`, which backs it up first. Python subprocess resolution is a separate Windows contract because `CreateProcess` can choose a foreign same-directory `tg.exe` even when shells prefer a tensor-grep `tg.com` bridge through `PATHEXT`.
- Normal PowerShell should invoke `tg` or `tg.ps1`. Directly invoking `C:\Users\oimir\bin\tg.cmd` from PowerShell with an unescaped metacharacter such as `|` is still a `cmd.exe` parser limitation; quote the argument for `cmd.exe` or use the PowerShell shim. The quoted multi-word no-match pattern case from `cmd.exe`, direct `tg.cmd`, and Python `subprocess.run([...])` is a public launcher contract and must not split into a shorter false-positive search plus bogus paths.
- Implicit native-binary resolution must ignore stale in-tree binaries such as `rust_core/target/debug/tg.exe` and `rust_core/target/release/tg.exe`. `uv run tg doctor --json` should report them under `skipped_native_tg_binaries`, set `rust_binary_version_status = stale-skipped`, and keep `search_acceleration_backend = rust-core-extension` when the embedded extension is available. Rebuild with `C:/Users/oimir/.cargo/bin/cargo.exe build --manifest-path rust_core/Cargo.toml --release` or pin `TG_NATIVE_TG_BINARY` to opt in to a specific standalone binary.
- Raw unsorted output ordering is semantic parity, not golden stdout parity. Use `--sort path` when deterministic path ordering matters and `--format rg` when automation needs exact ripgrep-style text formatting. Sorted files-with-matches, files-without-match, and replacement output are rg parity regression surfaces in the validated compatibility set.
- `tg search --json` is tensor-grep aggregate JSON, not ripgrep JSON Lines. `tg search --format rg --json` is the explicit ripgrep JSON Lines compatibility route and deliberately emits raw rg events without the tensor-grep envelope. `tg search --ndjson` is tensor-grep's flattened streaming row schema, not the rg event schema. Do not describe default `--json` or `--ndjson` as rg JSON compatibility.
- `edit-plan`, MCP `tg_edit_plan`, and session edit-plan should keep the agent command-surface budget flags aligned with `agent` / `context-render` (`--max-files`, `--max-sources`, `--max-tokens`, and related schema fields) while preserving the core contract that edit-plan emits no rendered source text.
- `tg new` must never silently ignore unknown scaffold arguments and write root files. Unsupported shapes should fail before writing; supported rule/test/util scaffolds must respect `--base-dir` and create only the requested item.
- Stable managed install scripts and `tg upgrade` are part of the public launcher contract. When release-native assets exist, the public front door should launch the matching native `tg` binary first and set `TG_SIDECAR_PYTHON` / `TG_NATIVE_TG_BINARY`; Python remains the sidecar or fallback, not the normal exact-text first hop. On Windows, put the managed native front-door directory ahead of compatibility shim directories on User PATH so `cmd`, unprofiled PowerShell, and Python subprocess calls resolve `~/.tensor-grep/bin/tg.exe` before the slower argv-safe `.cmd` bridge. A release that updates installer URLs is incomplete until GitHub release assets are uploaded and verified, not merely PyPI-published. Stable installers should clear stale package metadata before resolving `tensor-grep`, check native installer command exit codes before committing the staged install, and stage the new managed environment plus front-door files before replacing an existing install. `tg upgrade` should skip yanked PyPI releases, never report "latest PyPI version" from unchanged local metadata without verifying the target Python can import `tensor_grep`, refresh the managed release-native front door to the verified sidecar version, schedule a Windows retry helper when the running native `tg.exe` is locked, and require the scheduled Windows self-upgrade helper to verify the expected version too.
- `tg doctor --json` should expose launcher route state, not just version parity. Check `path_tg_first_launcher_kind`, `fresh_shell_path_tg_first_launcher_kind`, `python_subprocess_path_tg_first_launcher_kind`, `path_tg_launcher_warning`, and any `*_is_foreign` / `*_foreign_remediation` fields before interpreting Windows benchmark results; an existing shell can still be using the slower compatibility shim after User PATH has been fixed for fresh shells, Python subprocesses can resolve differently from shells, and unrelated tools can own a different `tg` command.
- Cold-path benchmark artifacts should include both `tg_launcher_mode` and `tg_launcher_command_kind`. Benchmark scripts should emit top-level warnings when the timed `tg` command is a `.cmd` shim, `uv`, Python-module route, or stale in-tree native tg binary. Stale in-tree native binaries must block claim-quality benchmark scripts by default unless the operator passes `--allow-claim-unsafe-launcher` for exploratory timing. Do not compare or market timings until native-exe, `.cmd` shim, `uv`, Python-module, and stale-binary routes are separated in the artifact with `tg_binary_version_status`.
- The native front door must not reject public flags advertised by the Python CLI. If a surface is still Python-backed, route it to the sidecar deliberately and add a public-native regression test plus dogfood coverage for the installed command shape. Current parity-sensitive examples are `tg search --files`, `tg search --multiline` / `-U`, `tg search --null`, `tg run -r`, `tg classify --format json`, advertised rg-style search flags, and option-first root `tg ...` forwarding.
- `classify` should be quiet and deterministic by default. It should use local heuristics unless `TENSOR_GREP_CLASSIFY_PROVIDER=cybert` explicitly opts into the CyBERT/Triton provider, and provider failures should fall back before tokenizer/model loading.
- GPU benchmark correctness must treat no-match as a real comparator outcome. `rg` exit code `1` with empty output is valid when `tg` also returns no matches. GPU scale gates should include 1GB and 5GB rows and exact match/file-set correctness for every >=1GB GPU corpus before any GPU promotion claim. Explicit `--gpu-device-ids` routing must not initialize or warn about unselected GPUs.
- GPU benchmark auto-recommendation must remain false unless required 1GB/5GB correctness checks pass and a selected GPU beats both `rg` and `tg_cpu` at the required scale and declared workload class. The current CUDA-native speed wedge is many fixed strings over a large corpus; single-pattern cold grep remains an `rg` lane. Unsupported-device inventory warnings must not be attached to unrelated selected-GPU timing rows. Any GPU-requested CPU fallback or sidecar route must surface `gpu_evidence_status = unsupported`, `gpu_proof = false`, `native_gpu_unavailable`, and `not_gpu_proof_reason`; unsupported rows must use `promotion_evidence = false`. Public managed GPU promotion additionally requires managed NVIDIA front-door provenance from `tg-native-metadata.json`, direct `rg --json` 1GB/5GB match-identity correctness, and `benchmarks/run_gpu_native_benchmarks.py --public-managed-proof` producing `public_managed_promotion_ready = true` and `public_gpu_proof = true` from the dispatch-only `public-gpu-proof.yml` workflow; local CUDA-feature binaries are implementation evidence, not public managed promotion proof.
- `edit-plan` and `context-render` JSON should expose top-level `validation_commands` so agents do not need command-specific parsing to find the validation list.
- Token-efficiency work must be opt-in and contract-aware. Lessons from `rtk` point toward a bounded agent output profile with hard caps, grouped excerpts, truncation, and omission counts; do not change raw `--format rg`, `--json`, or `--ndjson` semantics to save tokens.
- The product wedge is not "faster grep." It is an agentic code-intelligence runtime: given a task, identify what matters, explain why, emit bounded context, suggest validation, preserve rollback, and report confidence. `tg agent` / Actionable Context Capsule is the opt-in command for that workflow.
- The Actionable Context Capsule contract includes the primary file/function, route rationale, bounded source snippets with line maps, detected validation commands, risk level, suggested edit order, checkpoint or rollback metadata, omission counts, confidence, call-site evidence status, and an "ask user before editing" recommendation when uncertainty or risk is high. Capsule v1 leaves `related_call_sites` empty unless verified call-site evidence is explicitly collected.
- Capsule confidence must be honest when query language hints, exact symbol intent, primary target language, selected snippets, and validation commands disagree. In mismatch cases, cap both `confidence.overall` and `primary_target.confidence`, expose `query_language_hints`, `primary_target_language`, `validation_alignment`, and `validation_filtered_count` in `context_consistency`, and require ask-before-editing.
- Future search-intent routing should label evidence honestly as `parser-backed`, `rg-backed`, `graph-derived`, `heuristic`, `LSP-confirmed`, or `stale/uncertain`. The router can combine text search, AST, symbol graph, imports, tests, and docs, but it must report the route instead of hiding backend choice.
- LSP provider availability is not proof of working semantic navigation. Treat `tg lsp-setup` / `tg doctor --with-lsp` availability as install evidence only; provider-backed navigation must report `health_status`, `health_check`, `lsp_proof`, `lsp_evidence_status`, and `not_lsp_proof_reason` when it falls back to native evidence. A navigation row counts as LSP proof only when it carries `lsp_provider_response = true` from a completed provider request; `provenance = "lsp-*"` alone is not enough. Keep `lsp` / `hybrid` optional and experimental until real provider-backed requests are latency-bounded, reliable, and measurably better on accepted hardcase artifacts.
- `tg callers` and `tg blast-radius` JSON carry an additive `result_incomplete` field (v1.17.0, #281). `result_incomplete = true` means the scan hit an output or scan cap and the call-site list is TRUNCATED — do not treat a truncated zero-caller result as confirmed dead code. A clean scan that resolves zero callers emits a separate "resolved zero-caller" caveat, and even then is not proof of dead code: the call graph cannot see set/list/decorator/dispatch-table registration sites. Cross-check with `tg scan` or pattern grep before removing a zero-caller symbol.
- `tg callers` is Python-first (`docs/harness_api.md`): call-site resolution matches Python AST call nodes most reliably and can under-match or run for minutes on large TypeScript/JS repos. Dogfood receipt (v1.19.3): on a TS-heavy repo, `tg refs` returned 14 reference sites for a symbol where `tg callers` returned 1. Prefer `tg refs` for TS/JS symbol navigation; still cross-check with `tg scan`/grep per the registration-completeness blind-spot note above.
- Running `tg search PATTERN` with no path (or `tg search --glob X -l` without a scoped path) against this repo hangs ~600 s then errors: tg's own index dirs (`.tensor-grep/`, `_tg_refs/`, `.tg_semantic_index/`) and the vendored `benchmarks/external_repos/` tree are not auto-excluded and hit the default `TG_RG_TIMEOUT_SECONDS=600`. Scoped search runs in ~0.4 s. Workaround: always scope `tg search` to an explicit path (e.g. `tg search PATTERN src/`). Planned fix: own-dir excludes + fail-fast timeout + trigram-hybrid index.
- BM25/IDF-ranked surfaces (`tg search --rank`, agent-capsule, local semantic search) are sensitive to corpus changes: adding code that introduces or repeats query-adjacent terms lowers those terms' corpus-wide IDF, which can flip a ranking result and silently degrade a safety behavior. This IDF blast-radius is invisible to the call graph (no caller/callee edge exists for a ranking shift). Harden tie/marker detection to be robust to IDF shifts rather than relaxing a failing test — relaxing masks a real degradation. Tracked as capsule-hardening Task #4 (ledger B3).

## Operating Rules

1. Start with a failing test when behavior changes.
2. Make the smallest defensible change.
3. Run local gates before pushing, but keep them scoped on this desktop unless the user explicitly approves heavy validation. Prefer targeted tests locally and use PR/main CI for full pytest, full Rust test/clippy matrices, benchmark suites, release asset builds, and other high-memory gates.
4. Benchmark every hot-path change.
5. Reject regressions even if the code is otherwise clean.
6. Do not change workflow, release, or docs contracts without updating the validator-backed tests.
7. Do not run `wsl --shutdown`, restart WSL, stop Docker/WSL services, kill WSL processes, or reboot/restart the host as memory cleanup without explicit user approval. Other agents use WSL. If memory pressure is observed, first collect read-only process/memory evidence, stop only tensor-grep-owned processes you started, and ask before touching unrelated processes.
8. On ANY red CI check — not only a release-publish failure — decode the structured job result FIRST:
   `gh run view <id> --json jobs`, find the failing job, read its actual `--log-failed` / the failing
   test's −/+ diff, before theorizing from a traceback. A contract change (ruff / exit-code / JSON schema)
   is usually PINNED by a governance test; update the pin in the SAME PR rather than loosening the test.
   See `tensor-grep-debugging-playbook`, and the push-race-specific instance under Push Discipline.

## Adding a Command or Flag

Adding a top-level `tg COMMAND` requires four registration points or the new command silently misroutes:

1. `KNOWN_COMMANDS` in `src/tensor_grep/cli/commands.py` — the Python-side known-command registry.
2. A `Commands::X` passthrough variant and a matching dispatch arm in `rust_core/src/main.rs` — the native front door must know about it.
3. `PUBLIC_TOP_LEVEL_COMMANDS` in `tests/e2e/test_routing_parity.py` — the contract test that enforces parity between Python and native.
4. A `@app.command` function in `main.py` — the Typer app entry point.

Adding a search flag (e.g. `tg search --myflag`) requires two front doors or the flag leaks to ripgrep and causes an `rg: unrecognized flag` crash at runtime:

1. `SEARCH_PYTHON_PASSTHROUGH_FLAGS` in `rust_core/src/main.rs` — the native binary's allowlist.
2. `bootstrap._TG_ONLY_SEARCH_FLAGS` in `src/tensor_grep/cli/bootstrap.py` — the Python bootstrap's allowlist (the Python front door runs before the Typer app and forwards plain searches to rg).

Missing either slot lets the flag reach ripgrep for users who install the published binary while your CliRunner tests pass cleanly — exactly how the `--rank` crash shipped undetected.

**Registration-completeness is a universal bug class, not a tg quirk.** "Add a thing that must be registered in N places, miss one, it fails *quietly*" hit tg here (the `--rank` flag missed one of two front doors) and a downstream user's billing code (a new `/v1` route missed the cron registration + a `test_route_scope_coverage` exemption — green tests, broken route). Before claiming any registration change is done, **enumerate all N sites**. `tg callers <registration-function>` lists every *callable* registration in ~1s — but the call graph **cannot see set/list/decorator registrations** (an allow-list like `bootstrap._TG_ONLY_SEARCH_FLAGS`, `@router.post`, dispatch tables), and those are often the missed site (`--rank` lives in a *set*, not a call — `callers` would never have found it), so **grep / `tg scan` those**. Confirm your new entry appears in *all* sites. This is the default audit path (`tg callers` for blast radius → `tg scan` for pattern bugs → `tg doctor --with-lsp` for diagnostics); the principle is Hard Rule 6 in `verify-plan-against-code`, and the call-graph blind spots are in `tensor-grep-code-audit` (P7).

As of v1.17.1 (#282), the CI registration-completeness gate is BLOCKING — a registration mismatch fails the CI run, not just warns. The checker's member extractor is now string/comment-aware, so `#`-commented entries are no longer surfaced as false registered members.

**A new MCP tool function is a FIFTH registration site, not one of the four above.** Every tool's JSON
envelope embeds `mcp_contract_version` from the single `_TG_MCP_SERVER_CONTRACT_VERSION` constant
(`mcp_server.py`) — bump it whenever a tool's request/response shape changes. Same "enumerate all N
sites" bug class: the `tg_find` MCP PR (#627) shipped with an un-bumped contract version, caught only by
the mandatory adversarial Opus gate, not by tests or CI.

## Dogfood the Real Binary, Not CliRunner

The `tg` entry point is `tensor_grep.cli.bootstrap:main_entry`. It intercepts plain text searches and forwards them to ripgrep **before** the Typer app sees the argv. `CliRunner` invokes the Typer app directly and bypasses this front door entirely — so bugs in the bootstrap routing layer are invisible to unit tests.

After adding or changing a search flag or command, dogfood the **installed published binary** using the harness at `scripts/dogfood/` (Dockerfile + `dogfood_features.py`). The harness installs the real PyPI wheel and runs every public command shape through the actual `tg` binary. Do not rely on `CliRunner` alone for routing coverage.

## Verify AI-Drafted Plans Against the Real Code Before Building

Before implementing a plan produced by an AI subagent or any external planning pass, check every factual claim in the plan against the real source files by citing `file:line`. A claim with no citation should be treated as a hypothesis, not a fact.

This matters because AI-generated plans have a consistent failure mode: they identify plausible-sounding edit locations that do not match the actual code structure (dead code paths, renamed symbols, already-fixed lines). A verification pass that reads the real files before implementation is not overhead — it is the gate that prevents wasted cycles. A council or read-only review that cites file:line evidence caught 5 blockers in two unverified plans in a single session.

Re-run any validation a subagent claims to have passed — subagents can assert success without executing. For PRs that ship generated or detached code (install scripts, Windows self-upgrade helpers), adversarial-review by EXECUTING the code, not only reading it: `compile()` + `exec()` the generated string and assert the behavior (e.g. that the checksum gate fires BEFORE `os.replace`, and that the fail-closed branch is reachable). Test behavior, not substrings.

After building, run a mandatory post-build ADVERSARIAL AUDIT — a distinct named stage from the pre-build planning council. This audit caught a HIGH CUDA-fork hazard that 203 passing tests missed. A finding or claim with no `file:line` citation is DISCARDED. Re-audit → fix-wave → re-audit until ZERO must-fix findings remain; that zero-finding state is the convergence gate before promoting a build to a draft PR.

## Backend Fail-Closed Contract

Every `ComputeBackend` MUST raise `BackendExecutionError` on a real failure — never return a clean empty / `0-match` `SearchResult` (see `backends/base.py`), and never silently swap to a different engine that cannot preserve the requested semantics. The search loop catches `BackendExecutionError` to fall back **visibly** (e.g. to CPU); a swallowed failure or a silent engine swap reaches the user (or a coding agent) as a trustworthy "no matches" — the one failure a context tool cannot afford.

This contract is violated repeatedly. The recurring anti-pattern is a bare `except Exception:` that returns an empty result or falls through to a different engine. Instances fixed across audits: the Rust/PCRE2 bridge (ran `--pcre2` through the Python-regex engine), the ast-grep wrapper OOM mask (a killed subprocess read as a clean 0-match), the tree-sitter query swallow (invalid pattern → silent 0-match), and CyBERT's classify fallback (keyword-heuristic hits labeled as real model output). When a path CAN fall back to a different engine:

- **Fail closed** for any flag/contract the fallback cannot preserve (e.g. `--pcre2` through a non-PCRE2 engine): raise, do not swap.
- If a degraded fallback is legitimate (e.g. heuristic classification when the model is down), make the swap **visible**: set a `fallback_reason` (and a distinct `routing_reason`) on the `SearchResult` so JSON/CLI consumers can tell degraded output from real output. Never label heuristic output as model output.
- Validate an untrusted response shape before indexing (e.g. a model's class count vs a fixed label list) so a mismatch degrades gracefully instead of raising an uncaught `IndexError` that a broad `except` then swallows.

The same discipline applies beyond backends: any router/pipeline that can silently override an explicit user intent (e.g. an explicit `--gpu` request quietly routed to CPU) must instead raise `ConfigurationError` or emit a diagnostic. A systemic `SafeBackendMixin` + a fault-injection conformance CI gate (every registered backend must raise, not return empty, when its engine call fails) is the planned structural fix so this stops recurring one file at a time.

## AST Native/Wrapper Two-Engine Divergence (task #141)

`tg`'s AST surfaces (`tg run`, `tg scan`, the MCP `tg_ast_search` tool) can be served by two backends with two different, incompatible query DSLs: `AstGrepWrapperBackend` (`backends/ast_wrapper_backend.py`) shells out to the `ast-grep` binary and understands the full ast-grep pattern language, including metavariables (`$NAME`, `$$$ARGS`), selectors, and strictness options; `AstBackend` (`backends/ast_backend.py`) parses in-process via tree-sitter and understands only a narrow native query shape (a bare identifier, or an s-expression starting with `(`) — it has **no concept of ast-grep metavariables at all**. Given `$NAME` it cannot reproduce the wrapper's capture semantics.

This divergence is already fail-closed, at three verified sites (re-verified against `origin/main` `1135d30`; grep the symbol, not the line number, since these shift release to release — a regression test locks each one in, see below):

1. `Pipeline._supports_native_ast_pattern` (`core/pipeline.py:52-60`) — the shared classifier. Only a bare identifier (`re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", pattern)`) or a pattern starting with `(` counts as native-shaped; anything containing `$` (or any other non-identifier character, or more than one token) returns `False`.
2. `Pipeline.__init__`'s AST branch (`core/pipeline.py:230-233`) — when `_supports_native_ast_pattern` is `False` and the ast-grep wrapper is unavailable, it raises `ConfigurationError` via `_raise_explicit_ast_configuration_error` instead of silently falling through to native tree-sitter.
3. `_select_ast_backend_for_pattern` (`cli/ast_workflows.py:928-1004`, the `tg run`/`tg scan` selector) — mirrors the same classification (`pattern_kind == "wrapper"`) and raises the identical `ConfigurationError` at line 990 when the wrapper is required but absent.
4. `tg_ast_search` (`cli/mcp_server.py:4630-4653`) wraps the `Pipeline(...)` construction in `try/except ConfigurationError` and converts it to the structured `{"error": {"code": "unavailable", ...}}` JSON shape instead of letting a raw exception escape as an unhandled FastMCP `ToolError`. Note: this call site never threads `query_pattern` into the `SearchConfig` it builds, so `_supports_native_ast_pattern` is unconditionally `False` there — every `tg_ast_search` pattern (metavariable-shaped or not) requires the wrapper at this construction step; native `AstBackend` is structurally unreachable through the MCP tool regardless of the caller's pattern.

Regression coverage locking this in: `tests/unit/test_pipeline.py` (`test_supports_native_ast_pattern_should_reject_ast_grep_metavariable_syntax`, `test_should_reject_ast_grep_metavariable_pattern_when_wrapper_is_unavailable`) and `tests/unit/test_ast_workflows.py` (`test_select_ast_backend_should_reject_ast_grep_metavariable_pattern_when_wrapper_is_unavailable`) each assert `ConfigurationError` for a genuine `$NAME`/`$$$ARGS` pattern with the wrapper unavailable — even with the native backend AVAILABLE, to prove its presence never lets a metavariable pattern silently mis-route. `tests/unit/test_mcp_server.py` (`test_tg_ast_search_fails_closed_for_metavariable_pattern_when_wrapper_unavailable`) drives the real `Pipeline` (not a mock) through `tg_ast_search` to prove the same refusal surfaces as the structured JSON error at the MCP boundary.

**The native-shaped-pattern fallback is deliberate, not a bug.** When ast-grep is absent but a pattern IS native-shaped (a bare identifier or an s-expression), both `Pipeline` and `_select_ast_backend_for_pattern` fall through to the native `AstBackend` instead of refusing — this is intentional so a CPU-only box without the `ast-grep` binary installed still gets *some* AST search capability rather than none. Do not "fix" this into a hard refusal; that would regress a deliberately-supported capability.

**Reconciling the two DSLs (native metavariable support, or making native the CPU-perf default) is task #141 and stays demand-gated** — it is a design pass, not a small change, and only worth doing once a concrete consumer needs native-tree-sitter performance for patterns the wrapper already serves correctly. See `docs/BACKLOG.md` for current status.

## Roadmap Sequencing (2026-07-02, GPU phase structure added 2026-07-14)

The GPU native-backend program runs a 3-phase sequence gated on evidence, not a blanket "hold until N CPU
wins ship" rule:

- **Phase 0 -- shipped, gated OFF by default.** The correctness taxonomy, the loud non-promotional CPU
  fallback, the `doctor`/proof fields, and (v1.75.1-v1.75.4, audit #171 P0-1..P0-5 -- see the Current
  Handoff addendum above) the WSL path-domain probe bridging, doctor probe failure taxonomy, honest
  `--gpu-device-ids` validation, `calibrate` remediation messaging, and the loud nvidia->cpu installer
  downgrade warning are all SHIPPED and locally correctness-proven (RTX 4070 / RTX 5070, 1GB/5GB). The
  native `cuda` Cargo feature only compiles into release assets when the repository variable
  `TENSOR_GREP_RELEASE_NATIVE_ASSET_PROFILE` is explicitly set to `native-frontdoor-gpu`; the shipped
  default (`native-frontdoor`) never builds or ships a GPU asset, so Phase 0 landing is a code-complete,
  correctness-gated capability with zero public exposure until an operator opts in.
- **Phase 1 -- reversible flag-flip, not yet authorized.** Flipping the release variable to build and ship
  a GPU native asset is a reversible, single-variable change, but shipping the ASSET is not the same as
  PROMOTING it: no crossover has been proven (GPU remains slower than `rg` / `tg_cpu` for single-pattern
  search; see `docs/gpu_crossover.md`), and the public promotion gate
  (`.github/workflows/public-gpu-proof.yml`, dispatch-only) has not been run to a `public_gpu_proof =
  true` / `public_managed_promotion_ready = true` verdict -- the exact requirements are pinned in
  [docs/CONTRACTS.md](docs/CONTRACTS.md) (the "Public managed GPU promotion" bullets, currently around
  lines 80-82). Do not flip the variable to promote GPU as a default route until that gate passes.
  **2026-07-21 re-adjudication (B-GPU):** re-tested across 10MB-5GB corpora -- still **no crossover at
  any scale** (historical worst ~30-35x slower at 5GB; even the best-case 100-pattern fixed-string lane
  loses to fair-baseline `rg -F -e ...`), and the shipped `gpu_text_search_positions` kernel is a
  **position-parallel brute-force byte-compare, not a PFAC/Aho-Corasick automaton**
  (`docs/gpu_crossover.md:133-138` -- PFAC remains documented future work, not shipped code). Public
  CUDA-asset publishing is on a deliberate **HOLD** (CEO decision, #169); release checksums currently
  ship 3 CPU-only rows. Do not describe the shipped kernel as PFAC, and do not re-propose "just publish
  the GPU asset" without re-reading this verdict first.
- **Phase 2 -- self-hosted GPU CI runner, CEO-gated.** Proving Phase 1's crossover claim at 1GB/5GB scale
  in CI (rather than only on local RTX 4070/5070 dogfood boxes) requires a self-hosted GPU-capable runner
  wired into `public-gpu-proof.yml`. That is a real recurring infra cost and access-control surface, so
  provisioning it is explicitly CEO-gated, not an engineering-capacity decision.

The original CPU-only "3 wins before GPU advances" gate (2026-07-02) is superseded by this phase
structure, but its first win already shipped and validates the sequencing logic: **local hybrid semantic
search** (BM25 + CPU dense embeddings fused with RRF, no API key, no GPU) -- the #1 validated user ask --
shipped as `tg search --semantic` (`retrieval_dense.py` + `retrieval_fusion.py`, default-OFF, gated on
the `semantic` extra; see the `tensor-grep-semantic-search-campaign` skill). Reference architecture:
MinishLab `Semble` (tree-sitter chunking + `potion-code-16M` Model2Vec + BM25 + RRF, CPU-only, MIT). The
other two original CPU-only items -- `tg registration-check` productized as a first-class command, and a
Bloom-filter n-gram chunk prefilter for the slow non-literal-regex full-scan path in `rust_core` -- have
not shipped and remain live backlog items, independent of GPU phase gating.

Rationale (unchanged): the project's own docs place raw search speed (where GPU competes) in the
**parity tier, not the moat**; the heuristic auto-GPU route is effectively dead code whenever ripgrep is
installed (the common case). The moat is the **agent-native context layer** (`orient` / `callers` /
blast-radius / the token-efficient capsule), so engineering capacity funds that first. Explicit
`--gpu-device-ids` stays supported and must fail loud when it cannot be honored (see the Backend
Fail-Closed Contract).

## Security Hardening Patterns (Round-3 audit lens)

A round-3 security sweep (shipped v1.17.23–v1.17.25) fixed four recurring classes. Each is a **sweep target**, not a skill: current models already apply the fix when *writing fresh code* (baseline-tested), so these live here to be checked proactively — the bugs lived in already-committed code where no one re-verified. When you touch the named area, confirm the pattern holds.

- **Symlink-follow disclosure** (any tree walk or copy that snapshots/restores a user/repo tree). Following symlinks copies the *content* of out-of-root targets into the snapshot — and can re-materialize them on restore. Use `os.walk(root, followlinks=False)` + `shutil.copy2(src, dst, follow_symlinks=False)`. Fixed in `checkpoint_store.py` (`_filesystem_snapshot_entries` + all 3 copy sites).
- **Pre-auth unbounded read / no timeout** (any socket/pipe handler that reads *before* authenticating). Bound the read (`readline(max_bytes + 1)` + refuse over-cap) and set a socket timeout **before** the auth check, or an unauthenticated client exhausts memory or pins a worker thread. Fixed in `session_daemon.py` (`_read_bounded_request_line` + handler `timeout`).
- **Atomic-write permission window** (any temp-then-rename of a sensitive file, e.g. a token). Create the temp at the restrictive mode from byte one via `os.open(path, O_WRONLY | O_CREAT | O_EXCL, mode)` — never `write_text()`-then-`chmod`, which leaves a world-readable window; `O_EXCL` also refuses a pre-existing temp/symlink. Fixed in `session_store.py` (`_write_json_atomic`).
- **Native-argv flag injection** (CWE-88; the MCP-276 threat class — a *live* CVE family in MCP servers: CVE-2026-5058 aws-mcp-server, CVE-2026-23744, CVE-2026-30623 Anthropic MCP SDK). Any builder that appends a user/LLM-controlled value as a positional to a subprocess/native `tg`/`rg`/`git` command. A list-argv (`shell=False`) stops *shell* injection but **not** *flag* injection: a value beginning with `-` is parsed by the child's own option parser as a flag. Insert a `--` end-of-options sentinel **before** the user positionals. CAVEATS worth knowing: `--` protects only what comes *after* it (a user positional *before* `--` is still injectable); it does not gate `--flag=VALUE`; and not every binary honors it — **dogfood the real binary** (`tg search -- --weird` matches; `tg search --weird` errors). Fixed in `mcp_server.py` (`_build_rewrite_command`, `_build_index_search_command`); **remaining tg sweep** (tracked): the other native-argv builders + MCP write-path confinement. The three defenses layer — validate the value, list-argv, and `--` — and none alone is complete.

## EvidenceReceipt Signing (Ed25519)

`tg evidence emit` always attaches a keyless `receipt_sha256` integrity digest; `--sign` additionally Ed25519-signs it (`tg evidence verify` / `keygen` / `pubkey`), so a separate downstream consumer (e.g. gotcontext) can verify a receipt without ever holding a key that could forge one — the reason this uses an asymmetric algorithm rather than `tg audit`'s same-operator HMAC-SHA256 (`audit_manifest.py`). All crypto is isolated in `src/tensor_grep/cli/evidence_signing.py`. Two points worth knowing when touching this area:
- **S2 trust-bootstrap**: an embedded public key only proves internal self-consistency, never authenticity — `verify` always reports the signer's fingerprint (recomputed from the actual key bytes, never a claimed label) and only upgrades `key_trusted` to `True` against an out-of-band pinned `--trusted-key`/`TG_EVIDENCE_TRUSTED_KEYS`, compared with `hmac.compare_digest`. `--require-trusted` is the flag that fails `valid` closed on an unpinned key.
- **Fail-closed**: `--sign` with no resolvable key (or `cryptography` unavailable) is a non-zero exit with no receipt written — never a silent unsigned fallback (the `--pcre2` anti-pattern this contract exists to prevent). Full wire format + canonicalization rule: [docs/CONTRACTS.md](docs/CONTRACTS.md#8-evidencereceipt-signing-tg-evidence-emit---sign--tg-evidence-verify); design spec: `docs/plans/backlog-100/cluster-124-evidence-signing.md`.

## Skills

Three kinds of skills apply to this repo; load the relevant one before non-trivial work.

- **Using `tg` itself** — `.claude/skills/tensor-grep/SKILL.md` (+ `REFERENCE.md`): the agent-usage skill for the command surface (`search`, `search --rank`, `orient`, `map`, `agent`, `session`, AST, blast-radius). Keep it in sync whenever commands/flags change.
- **Working ON `tg` (build + release discipline)** — reusable global skills at `~/.claude/skills/`:
  - `dogfood-the-shipped-artifact` — after a release, install the published wheel in clean Docker and run the REAL `tg` binary across every feature; never trust CliRunner (it bypasses the bootstrap front door). Harness: `scripts/dogfood/`.
  - `verify-plan-against-code` — before building an AI/subagent-drafted plan, verify every seam claim (file paths, the command/flag registration sites above, routing) against the real code with `file:line` citations; bake corrections in first.
  - `supply-chain-hardening` — before writing any download / extract / install / self-upgrade / toolchain-bootstrap code, apply the 5 checks (zip-slip guard, byte-capped/time-bound downloads, fail-closed checksum incl. detached helpers, `--locked` pinned CI tools, fail-closed unverified toolchains). Shipped patterns: #283/#284/#285/#287.
  - `worktree-fanout-verification-gate` — before integrating agent branches from a worktree fan-out: remove worktrees before checkout (`git worktree remove --force <path>` — else checkout is blocked and tests silently run main's code); re-run pytest/ruff/mypy in the real venv (worktrees have no `.venv`; agents' "tests pass" claims are hypotheses until then); run `ruff format --preview` on ALL agent-touched files (not only hand-fixed ones); and treat scoped-local-green as a hypothesis, not a merge signal.
  - `anti-hang-test-protocol` — hang-class test hygiene: wrap every test run in a shell timeout, and write the fix BEFORE the red-phase adversarial test (a ReDoS/deadlock red-test executed against un-fixed code IS the hang it is testing).
  - `instrumented-build-gate` — measure real demand before building a speculative feature.
  - `agent-liveness-probe` — before killing, restarting, or `TaskStop`-ing a background subagent that looks stalled, probe liveness via `SendMessage` rather than trusting output-file mtime/size (see A9 above).
  (the global-skill half of this list is manually maintained — no CI gate — diff it by hand against `CLAUDE.md`'s copy.)
- **Carrying the project forward -- the in-repo skill library** (`.claude/skills/tensor-grep-*` + `code-search-and-retrieval-reference`, **26 skills**): the onboarding handbook so a new engineer or a Sonnet-class session can debug, extend, validate, and advance `tg` without the original authors. Each auto-loads by its `description`; load the one matching your task. Index by intent -- this exact bucket list is kept byte-identical with `CLAUDE.md`'s skill index; `tests/unit/test_skill_index_sync.py` fails if either doc drifts from the real `.claude/skills/` folder set:
  - **Change safely:** `tensor-grep-change-control` (the gates), `tensor-grep-debugging-playbook`, `tensor-grep-failure-archaeology` (don't re-fight settled battles), `tensor-grep-validation-and-qa`.
  - **Understand:** `tensor-grep-architecture-contract`, `code-search-and-retrieval-reference` (domain theory), `tensor-grep-config-and-flags`.
  - **Operate:** `tensor-grep-build-and-env`, `tensor-grep-run-and-operate`, `tensor-grep-diagnostics-and-tooling`, `tensor-grep-docs-and-writing`, `tensor-grep-release-and-positioning`, `tensor-grep-workspace-dogfood` (multi-repo stress dogfood), `tensor-grep-enterprise-agent` (enterprise readiness gaps + agent hard-stops), `tensor-grep-prepare` (one-call edit readiness), `tensor-grep-ledger` (advisory multi-agent claim/finding-reuse), `tensor-grep-find-and-route` (whole-repo hybrid find + route-test), `tensor-grep-multi-project-search` (scoped cross-repo search), `tensor-grep-enterprise-review-bundle` (review-bundle create/verify), `tensor-grep-gpu` (experimental GPU probes).
  - **Advance (SOTA):** `tensor-grep-semantic-search-campaign`, `tensor-grep-benchmark-and-proof-toolkit`, `tensor-grep-research-frontier`, `tensor-grep-research-methodology`, `tensor-grep-large-repo-scale-campaign` (bounding scale/deadline on large repos).
  - **Orchestrate:** `tensor-grep-backlog-campaign` (the multi-PR drain+build campaign playbook).
- When working ON tensor-grep, use `tg search`/`tg defs`/`tg callers` for code navigation rather than generic grep/find — this exercises the tool's own surfaces and catches routing regressions early (mind the scoped-path workaround above).
- `.claude/skill_rules.json` is Claude-Code harness config for the global `skill_activation_gate.py` hook (trigger keywords that auto-suggest a skill) — it is **not a product contract** and is invisible to `test_skill_index_sync.py` (it has no `SKILL.md`); update its per-skill trigger entries when a skill is added/renamed, but do not treat its content as authoritative over a skill's own frontmatter `description`.

These encode the "Adding a Command or Flag", "Dogfood the Real Binary", and "Verify AI-Drafted Plans" sections above as reusable, project-independent skills.


## Dogfood follow-up workflow

When public dogfood identifies multiple independent fixes, preserve the process that has been working:

1. Turn each concrete failure or feature gap into PR-sized slices; do not collapse independent fixes into one broad PR.
2. Before implementation, use Exa research for current external contracts and tooling behavior that the fix depends on, especially `rg`, `ast-grep`, CUDA/GPU, packaging, GitHub Actions, and agent-evaluation surfaces.
3. Run a thinktank or equivalent independent planning review when the dogfood item changes product positioning, benchmark interpretation, GPU promotion criteria, or release workflow. The council must cite `file:line` for every seam claim; uncited claims are hypotheses, not facts.
4. Before fan-out: commit the corrected plan to the shared branch OR inline the full slice spec in every agent prompt. Worktrees branch off HEAD and will not contain uncommitted files — a plan written but not committed is invisible to fan-out agents. Decompose the corrected plan into worktree-isolated agent slices.
5. For each slice, write or update the contract test first, implement the smallest fix, run the targeted suite, then run lint and format before moving on.
6. ORCHESTRATOR VERIFICATION GATE — after every agent branch returns, the orchestrator must verify before integration: (a) remove each worktree (`git worktree remove --force <path>`) before checking out the branch in the main repo — an un-removed worktree blocks checkout and causes a main-repo test run to silently execute main's code, not the branch's; (b) re-run pytest/ruff/mypy in the real venv, since worktrees have no `.venv` and agents' "tests pass" / "N tests green" claims are hypotheses until re-run there; (c) run `ruff format --preview` on EVERY file in `git diff main --name-only`, not only hand-fixed files — agents couldn't run ruff, so their files come back un-`--preview`-formatted; (d) treat scoped-local-green as a hypothesis, not a merge signal — lint/format run repo-wide, one unrelated failing test reddens the whole test-python job, and corpus side-effects are outside scoped test scope. See the global skill `worktree-fanout-verification-gate`.
7. Integrate the verified slices onto one branch, resolving any overlaps.
8. ADVERSARIAL AUDIT (3 lenses + chairman) — run a citation-enforced adversarial audit of the integrated diff; this is a mandatory stage distinct from the pre-build planning council (the post-build audit caught a HIGH CUDA-fork hazard that 203 passing tests missed). A finding with no `file:line` citation is discarded. Re-audit → fix-wave → re-audit until ZERO must-fix findings remain. The endpoint is a DRAFT PR; never auto-merge.
9. Ask Gemini for a bounded read-only review of each PR diff before merge; treat its findings as hypotheses until checked against local files and tests.
10. Push each branch, wait for PR CI, squash-merge intentionally, then watch main CI. Release-bearing work is not complete until semantic-release, assets, PyPI, and public release dogfood pass.

Maintain a per-slice evidence ledger in `docs/SESSION_HANDOFF.md`, `SKILL.md`, and this file when operating practice changes. Each slice entry must record PR order, slice scope, Exa research anchors, thinktank or planning consensus, subagent ownership, Gemini review result, validation commands, PR CI, and main CI. Optional or triggered items may be marked `not applicable` only with a rationale. For release-bearing slices, additionally require semantic-release, release assets, PyPI, and public release dogfood evidence.

Current dogfood slice ledger:

- PR order: 13; scope: close the `v1.13.20` dogfood daemon-upgrade and LSP-diagnostic follow-up by snapshotting pre-upgrade session daemon state, restarting the daemon after direct or scheduled Windows upgrade handoff loss, stripping inherited Python runtime variables from managed LSP provider launch environments, and suppressing stale Pyright SRE mismatch stderr tails once a current provider request proves healthy while preserving failed-proof stderr; Exa anchors: CPython/uv SRE mismatch reports connecting the error to mismatched Python runtime/stdlib environment; thinktank/planning consensus: read-only subagent reviews required using the pre-upgrade daemon root and preserving failed-proof stderr; subagent ownership: Popper and Copernicus read-only plan review, implementation local; Claude Opus review: PASS with low findings, addressed by preserving non-SRE suppressed stderr as `provider_recent_stderr` and carrying daemon restart roots into the scheduled Windows helper; validation: targeted upgrade/LSP tests, focused LSP suites, ruff, preview format, mypy, and diff whitespace passed locally; PR CI: PR #233 passed; main CI: semantic-release published `v1.13.21` at `1b62da7`, main CI run `26450640497` passed, CodeQL/dynamic run `26450639894` passed, and public `uvx --refresh-package tensor-grep --from tensor-grep==1.13.21 tg --version` proof passed.
- PR order: 12; scope: harden the `v1.13.19` built-in dogfood timeout gap by giving `tg dogfood` a wrapper timeout, passing an incremental child `--output` to `scripts/agent_readiness.py`, preserving partial running reports, and cleaning up the launched child process tree by PID only; Exa anchors: Python subprocess timeout semantics and psutil process-tree termination guidance; thinktank/planning consensus: not applicable because this is an internal harness lifecycle fix, with Zeno read-only subagent review confirming the timeout and descendant-cleanup root cause; subagent ownership: Zeno read-only call-path review, implementation local; Claude Opus review: no blocker/high findings (`OPUS_REVIEW: PASS`); validation: targeted dogfood/readiness/docs tests, ruff, preview format, mypy, and diff whitespace passed locally; PR CI/main CI: PR #231 passed, squash merge produced `6525853`, semantic-release published `v1.13.20` at `c41d475`, main CI run `26437847778` passed, CodeQL/dynamic run `26437847528` passed, and public `uvx --refresh-package tensor-grep --from tensor-grep==1.13.20 tg --version` proof passed.
- PR order: 11; scope: harden the `v1.13.18` daemon-cache dogfood gap by letting capped or truncated implicit session snapshots bypass added-file stale detection for daemon-routed top-level `context-render` / `edit-plan` cache writes while preserving explicit added-file refresh for complete sessions; Exa anchors: not applicable because this is internal daemon/session cache behavior; thinktank/planning consensus: systematic-debugging trace plus read-only subagent review isolated the stale-detection failure before `response_cache.put()` and required an added-file refresh regression test; subagent ownership: Wegener read-only plan/diff review, implementation local; Claude Opus review: no blocking findings, optional capped-modification stale-refresh test added; validation: targeted docs/session tests pass (`47 passed`), `uv run --no-sync ruff check .`, `uv run --no-sync ruff format --check --preview . --exclude .tmp --exclude .tensor-grep --exclude src/.tensor-grep`, `uv run --no-sync mypy src/tensor_grep`, and `git diff --check` pass locally; full pytest/Rust matrices and benchmark suites intentionally deferred to PR/main CI unless the user approves heavy desktop validation; PR CI/main CI: PR #230 passed, squash merge produced `0c9155f`, semantic-release published `v1.13.19` at `b9197a6`, main CI run `26431129535` passed, CodeQL/dynamic run `26431129155` passed, and public `uvx --refresh-package tensor-grep --from tensor-grep==1.13.19 tg --version` proof passed.
- PR order: 10; scope: harden `v1.13.17` dogfood regressions by making non-JSON rg-shaped explicit no-ignore searches prefer ripgrep passthrough when `rg` is available while preserving the native fallback when it is not, preserving tensor-grep aggregate JSON semantics, resolving top-level `context-render` / `edit-plan` daemon requests to absolute directory roots so repeated relative invocations can populate and hit the daemon response cache, and documenting desktop memory-safety operating rules for local validation; Exa anchors: official ripgrep guide/manpage behavior for `--no-ignore` and `-u` disabling ignore filtering; thinktank/planning consensus: read-only subagent review agreed the no-ignore fast path should stay in the rg-shaped non-JSON lane and the daemon cache fix should normalize request paths at the top-level caller boundary; subagent ownership: McClintock read-only plan/diff review, implementation local; Claude Opus review: accepted findings for direct JSON/NDJSON passthrough tests, no-ignore-vcs coverage, guarded daemon path normalization, daemon-start assertions, and absolute cleanup; validation: targeted daemon path/cache tests, targeted Rust routing test, ruff, preview format check, cargo fmt check, and diff whitespace check passed locally; full pytest/Rust matrices and benchmark suites intentionally deferred to PR/main CI unless the user approves heavy desktop validation; PR CI/main CI: PR #229 passed, squash merge produced `77a73b2`, semantic-release published `v1.13.18` at `4a0dad0`, main CI run `26425383595` passed, CodeQL/dynamic run `26425914836` passed, and public `uvx --refresh-package tensor-grep --from tensor-grep==1.13.18 tg --version` proof passed.
- PR order: 7; scope: close concrete `v1.13.11` dogfood regressions by deduplicating `defs --provider hybrid` native/LSP definition rows while preserving LSP proof, bounding checkpoint discovery cache priming at the user-home boundary so Windows standalone `checkpoint create` does not write `C:\Users\.tensor-grep`, separating MCP protocol/CLI version fields in capabilities, sharpening the PowerShell `Start-Process`/`tg.ps1` MCP stdio warning, suppressing stale LSP stderr tails once a provider request proves healthy, routing `tg audit --help` to audit help instead of search, and broadening `secrets-basic` fake API key detection; Exa anchors: official MCP lifecycle/version negotiation docs and LSP 3.17 `Location`/range semantics for merge identity; thinktank/planning consensus: compressed read-only review through subagents because the separate thinktank spawn hit the agent thread limit; Aquinas recommended explicit MCP protocol versus CLI fields, Cicero recommended post-merge LSP/native dedupe with LSP proof preservation and quiet successful provider status, and Ohm recommended home-bounded checkpoint discovery plus explicit native-`tg.exe` MCP stdio warning; subagent ownership: Aquinas (MCP), Cicero (hybrid/LSP), Ohm (checkpoint/doctor/audit); Gemini review: unavailable because `gemini-3-flash-preview --approval-mode plan` stalled after startup/tool noise and was killed without a report; validation: targeted checkpoint, semantic-provider, LSP-provider, trust/audit, MCP, doctor, scan, docs, and integration tests pass locally; `uv run pytest -q` passes (`2451 passed, 16 skipped`); `uv run ruff check .`; `uv run ruff format --check --preview .`; `uv run mypy src/tensor_grep`; full Rust crate tests; cargo fmt check; `uv run python scripts/agent_readiness.py --no-shell-probes --no-wsl-probe --json` passes (`13 passed, 0 failed`); direct Windows checkpoint-create smoke, direct agent-studio hybrid-defs smoke, audit-help smoke, MCP-capabilities smoke, public-command contract smoke, and `git diff --check` pass locally; PR CI/main CI: pending.
- PR order: 1; scope: accept and forward remaining rg config-override flags (`--pcre2-unicode`, `--ignore`, `--messages`, `--require-git`, `--no-hidden`) in native/Python search and add installed-public sweep coverage; Exa anchors: ripgrep manpage option inversion/config behavior plus ripgrep guide automatic-filtering defaults; thinktank/planning consensus: local planning review, external council not applicable for this parser/forwarding contract slice; subagent ownership: not applicable; Gemini review: unavailable because Gemini CLI 0.42.0 hung on a one-token read-only model probe and was killed; validation: Rust crate tests, full pytest, lint, format, mypy, and diff whitespace checks pass locally; PR CI/main CI: pending.
- PR order: 1; scope: make `run_agent_success_harness.py` refuse stale in-tree native `tg` binaries by default and mark `--allow-claim-unsafe-launcher` runs as exploratory; Exa anchors: not applicable beyond existing benchmark-governance policy; thinktank/planning consensus: local planning review aligned with `run_benchmarks.py` stale-binary refusal; subagent ownership: not applicable; Gemini review: unavailable because Gemini CLI 0.42.0 hung on a one-token read-only model probe and was killed; validation: Rust crate tests, full pytest, lint, format, mypy, and diff whitespace checks pass locally; PR CI/main CI: pending.
- PR order: 1; scope: accept and forward the 25 remaining ripgrep inverse/config-override flags found by `parser_sweep_1_12_31_codex.json`, including `--no-auto-hybrid-regex`, `--no-pcre2-unicode`, `--no-text`, `--no-binary`, `--no-follow`, `--ignore-dot`, `--ignore-vcs`, `--no-json`, and `--no-stats`, and batch those 25 installed-public sweep probes into one command to avoid adding dogfood latency; Exa anchors: current ripgrep guide/manpage behavior for config override flags plus local `rg 15.1.0` acceptance sweep; thinktank/planning consensus: local planning review only, external council not applicable because this is parser/forwarding contract work and does not alter GPU/LSP/product positioning; subagent ownership: not applicable, no subagents requested for this turn; Gemini review: unavailable because `gemini-3.1-pro-preview` returned an invalid empty stream and `gemini-2.5-flash` stalled after startup; validation: targeted parser/backend/readiness tests, full `test_public_native_cli_parity`, direct built-native acceptance of all 25 flags, full Python/Rust suites, lint, format, mypy, diff whitespace, and fast readiness pass locally; PR CI/main CI: pending.
- PR order: 1; scope: add `world_class_readiness.status = "not_claimed"` plus `agent_target_selection_metrics` to `tg dogfood` reports so a PASS cannot be mistaken for full rg replacement, full ast-grep replacement, public GPU promotion, production LSP proof, or enterprise target-selection accuracy; Exa anchors: ripgrep JSON/config-override docs, ast-grep CLI docs, Cursor/Sourcegraph agentic context docs, and NVIDIA CUDA profiling/transfer guidance; thinktank/planning consensus: Gemini plan-mode read-only review rejected a separate `next_pr_slices` planning array as source-of-truth duplication and recommended adding the missing target-selection surface to the existing limitations contract; subagent ownership: not applicable, no Codex subagents requested for this turn; Gemini review: completed for planning, final diff-review retry unavailable because `gemini-3.1-pro-preview` returned an invalid empty stream and `gemini-2.5-flash` stalled after startup; validation: targeted dogfood/docs tests, full Python/Rust suites, lint, format, mypy, diff whitespace, and fast readiness pass locally; PR CI/main CI: pending.
- PR order: 2; scope: make GPU promotion workload-scoped in benchmark artifacts and public dogfood/docs, including `promotion_scope = "declared_workload_class_only"`, fair many-pattern baseline `rg -F -e ... -e ...`, and candidate classes for `many_fixed_patterns_single_dispatch` / `resident_repeated_query`; Exa anchors: CUDA-grep final/checkpoint reports on transfer amortization and many-regex workloads, NVIDIA CUDA Graphs and pinned-memory async transfer docs, and ripgrep `-F`/`-e` multiple-pattern docs; thinktank/planning consensus: read-only GPU proof and release-governance seats both recommended an artifact/schema hardening PR rather than CUDA kernel work; subagent ownership: Jason reviewed GPU performance/proof, Lovelace reviewed release/governance; Gemini review: unavailable because `gemini-3.1-pro-preview` returned an invalid empty stream and `gemini-2.5-flash` stalled after startup; validation: targeted GPU benchmark contract, dogfood, public docs, benchmark-script, and readiness tests; `uv run ruff check .`; `uv run ruff format --check --preview .`; `uv run mypy src/tensor_grep`; `cargo fmt --manifest-path rust_core/Cargo.toml --check`; `cargo test --manifest-path rust_core/Cargo.toml`; `uv run pytest -q` (`2248 passed, 16 skipped`); `uv run python scripts/agent_readiness.py --no-shell-probes --no-wsl-probe --json` (`12 passed, 0 failed`); and `git diff --check` pass locally; PR CI/main CI: pending.
- PR order: 3; scope: add public managed GPU proof plumbing with `tg-native-metadata.json`, Python upgrade/install script metadata writers, `--public-managed-proof`, and artifact fields `public_managed_promotion_ready` / `public_gpu_proof`; Exa anchors: NVIDIA Blackwell compatibility guidance, cudarc 0.19 CUDA 13/dynamic-loading docs, and GitHub Actions GPU runner docs; thinktank/planning consensus: Gemini plan-mode review rejected path-shape-only proof and recommended explicit managed front-door provenance; subagent ownership: attempted read-only Codex explorer, but the agent thread limit was reached, so implementation stayed local; Gemini review: planning review completed with file-read limitation, final diff review not run yet; validation: targeted runtime/installer/GPU benchmark/docs tests (`91 passed`), `uv run pytest -q` (`2261 passed, 16 skipped`), `uv run ruff check .`, `uv run ruff format --check --preview .`, `uv run mypy src/tensor_grep`, and `git diff --check` pass locally; PR CI/main CI: pending.
- PR order: 4; scope: add a dispatch-only public managed GPU proof workflow and strengthen the native GPU proof gate so public promotion requires fixed GPU runner labels, managed NVIDIA asset verification, direct `rg --json` 1GB/5GB correctness, `NativeGpuBackend`, `sidecar_used = false`, and speed wins over both `rg` and `tg_cpu`; Exa anchors: GitHub Actions self-hosted/GPU runner docs, NVIDIA Blackwell/CUDA compatibility docs, CUDA compute-capability docs, and ripgrep JSON output semantics; thinktank/planning consensus: Mill/Mencius/Descartes agreed to separate public proof workflow/governance from local CUDA implementation evidence and to reject weak `promotion_ready` summaries; subagent ownership: Mill reviewed workflow scope, Mencius reviewed release/security workflow requirements, Descartes reviewed benchmark proof semantics; Gemini review: unavailable; `gemini-3.1-pro-preview` stalled after startup with no report and was stopped; validation: targeted GPU benchmark contract, benchmark-script, release-workflow validator, and release asset validator tests pass locally; PR CI/main CI: pending.
- PR order: 1; scope: close the `v1.12.33` rg column-override edge by accepting and forwarding `--column --no-column` through both `tg search --format rg ...` and root-level `tg --format rg ...`, add installed-native sweep coverage, improve stale repo-local `uv run tg` warmup diagnostics, and pin the `ripgrep binary resolution` capsule hardcase; Exa anchors: ripgrep inverse/config-override docs where last flag wins, ripgrep JSON/output docs for preserving rg-vs-tg schema boundaries, Sourcegraph/Cody context docs for agent target-selection evidence, LSP initialize-timeout evidence for keeping LSP experimental, and CUDA-grep transfer-amortization notes for keeping GPU unpromoted; thinktank/planning consensus: two read-only seats recommended this narrow contract/readiness/capsule regression slice and explicitly rejected raw-speed, GPU, LSP, or ast-grep claim changes; subagent ownership: thinktank seats Lagrange and Hegel reviewed the plan, implementation stayed local due tight parser/readiness coupling; Gemini review: attempted with gemini CLI 0.42.0 / gemini-2.5-flash in read-only plan mode; unavailable because the model returned an invalid empty stream / malformed tool call; validation: targeted rg contract/parity tests, readiness stale-entrypoint and flag-sweep tests, agent hardcase test, Rust parser unit test, Rust public-native parity test, full Rust crate tests, full pytest, lint, format, mypy, fast readiness, and diff whitespace passed locally; PR CI/main CI: PR #163 passed, squash merge produced `c0cb613`, main CI run `26094452260` passed semantic-release, GitHub release assets, PyPI publish, and `publish-success-gate`; release/public proof: `v1.12.34` tag/release assets exist and `uvx --refresh-package tensor-grep --from tensor-grep==1.12.34 tg --version` reports `tensor-grep 1.12.34`.

## Required Local Validation

Run these before push for normal code changes:

```powershell
uv run ruff check .
uv run ruff format --check --preview .
uv run mypy src/tensor_grep
uv run pytest -q
```

CI runs `ruff format --check --preview .`. Running only `uv run ruff check .` is not enough to prove formatter parity, and running `ruff format` WITHOUT `--preview` actively REVERTS preview-style formatting on disk — a "clean" bare `ruff format` will undo CI-mandated style and red the next `ruff format --check --preview` run even when local lint passes. Always pass `--preview` to `ruff format` locally; never pass it to `ruff check`. The trailing `.` (whole repo) is load-bearing too: under `--preview`, ruff formats Python code fences INSIDE Markdown, so a scoped run (`ruff format --check --preview src/tensor_grep tests`) passes locally yet MISSES an unformatted `docs/**/*.md` snippet — which reds CI's release-gating `static-analysis` job and blocked v1.67.0. Always run the whole-repo `.` form; never a `src`/`tests` subset.

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
tg dogfood --output artifacts/dogfood_readiness.json
```

This 3-5 minute gate checks public shell version resolution, `public-version-python-subprocess`, `public-windows-launcher-quoted-patterns`, installed-public advertised search flag acceptance via `public-search-advertised-flag-sweep`, repo doctor sanity, `context_consistency`, `agent-capsule`, `agent-capsule-mixed-language`, `agent-capsule-hardcases`, deterministic rg edge parity, broad generated-root scan guardrails, AST smoke, MCP context-render smoke, and docs claim hygiene. `tg dogfood` wraps the same readiness gate with a one-page verdict and JSON envelope. It complements, not replaces, the full local validation gate.

For release dogfood, include this compact public path checklist:

```powershell
gh release view <tag>
pip index versions tensor-grep
uvx --refresh-package tensor-grep --from tensor-grep==<tag> tg --version
tg upgrade
cmd /c tg --version
pwsh -NoProfile -Command "tg --version"
tg doctor --json
```

`tg doctor --json` must show matching sidecar/native versions and should expose any current-shell, fresh-shell, or Python-subprocess foreign launcher route.

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

`repeated_regex_native` must stay on native/Rust routing such as `cpu_rust_regex`; do not force a Python fallback in hot-query probes. For sub-10ms benchmark rows, use an absolute jitter tolerance in addition to ratio checks.

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

### Agent capsule / edit-loop workflow benchmark

```powershell
python benchmarks/run_agent_workflow_benchmarks.py --output artifacts/bench_agent_workflow.json
python benchmarks/run_agent_success_harness.py --output artifacts/bench_agent_success_harness.json
```

Use this for:

- `tg agent` capsule routing
- confidence / alternative target surfacing
- validation alignment and filtering
- rollback, edit order, and whole-loop edit latency
- end-to-end query intent -> context -> edit seed -> apply -> verify -> rollback success

This is workflow evidence, not a cold exact-text search speed claim.

### GPU / NLP backend benchmark

```powershell
python benchmarks/run_gpu_benchmarks.py --output artifacts/bench_run_gpu_benchmarks.json
```

Notes:

- `cyBERT` may skip if Triton is unavailable.
- Treat `SKIP` as expected infrastructure state, not a fake failure.

### Retrieval-quality (NL search) benchmark

```powershell
python benchmarks/eval_late_rerank_quality.py --output artifacts/bench_find_quality.json
```

Use for `tg find` / `tg_find` ranking changes (`TG_FIND_DENSE_WEIGHT`, RRF channels, chunker, late-rerank).
This is a QUALITY benchmark (ndcg@10 / recall@10 on the NL golden set + literal/identifier golden slices),
NOT a speed benchmark — run it IN ADDITION to the CLI search benchmark when the change touches the CPU
search path. Bidirectionally-oracle-validate any new golden query before trusting a delta (an empty/wrong
answer must FAIL the grader). Add a per-query paired win/loss/tie report before gating a ship on a bare
40-query mean (see the global `paired-test-power-discipline` skill). `TG_LATE_RERANK` stays OFF — it
regresses vs plain BM25, entangled with a non-role-aware doc encoder; a harness gap, not a verdict on MaxSim.

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

Any new download / extract / install / self-upgrade helper must apply the v1.17.2–v1.17.5 supply-chain patterns (see the `supply-chain-hardening` skill): (a) zip-slip guard — validate every member path against the resolved dest before `extractall` (reuse the production `_safe_extract_zip`); (b) time-bound + byte-capped downloads — `urlopen(timeout=...)` / socket timeout + a byte cap (256 MiB for native assets); (c) checksum-gated fail-closed installs — embed the expected SHA from `CHECKSUMS.txt` and verify before `os.replace`, INCLUDING in the detached Windows self-upgrade helpers; (d) `--locked` + exact version pins for CI tools (e.g. `cargo-audit==0.22.2 --locked`, `cargo-deny --locked`) — an unpinned `cargo install` can pull a breaking upstream release mid-CI.
(e) uv's `.ps1` installer LACKS binary checksum verification (uv issue #13074) while the `.sh` self-verifies (uv >=0.11.0, pinned 0.11.25); Windows fix = download the pinned uv RELEASE BINARY + verify a COMMITTED dual-arch (x86_64 + aarch64) SHA-256 fail-closed before use (implemented in `scripts/install.ps1` + a new `scripts/uv_checksums.json`, landing with PR #302 — not yet on `main`); discipline: ALWAYS download + `Get-FileHash` to CONFIRM a committed SHA — never trust an agent's "fetched from the sidecar" value.
(f) ACCEPTED BOOTSTRAP TRUST BOUNDARY (documented, not a gap): the toolchain bootstrappers are trusted-over-HTTPS + version-pinned, NOT checksum-gated like the release artifacts WE download — uv's `.sh` self-verifies its binary (uv >=0.11.0, pinned 0.11.25), and rustup is fetched via `curl https://sh.rustup.rs | sh` in the semantic-release `build_command` (pyproject.toml) then pinned with `rustup default 1.96.0` (rustup self-verifies the toolchain). This is a deliberately different posture from (a)-(e), which checksum-gate artifacts WE fetch/extract. De-piping rustup to a pinned-binary + committed-checksum download is a tracked follow-up — it touches the release `build_command`, so it is ATTENDED (do not change it autonomously).
(g) **Runtime-dependency CVE response (#632 / v1.78.1).** Unlike (a)–(f) (code WE write), a disclosed CVE
in a THIRD-PARTY runtime dependency is caught by the `Dependency & License Audit` workflow's strict-on-
fixable `pip-audit` / `cargo-audit` gate — and it reds **every open PR**, unrelated to any diff. Decode the
audit's OWN structured output for the exact package + fixed-version; bump the `pyproject.toml`/`Cargo.toml`
FLOOR (e.g. `mcp>=1.2.0` → `mcp>=1.27.2`), NOT just a lock relock — a floor-only relock can silently regress
below the patch on a future bare resolve. Regenerate the lockfile, then re-run the FULL dependent test
surface unmodified (`tests/unit/test_mcp_server.py`, `tests/unit/test_mcp_tg_find.py`,
`tests/integration/test_mcp_stdio_protocol.py`, `tests/unit/test_harness_api_docs.py`) — a passing
dependency bump with zero code changes is the expected GOOD outcome, not a reason to skip verification.

Any Rust helper reachable only from a `#[cfg(feature = "cuda")]`-gated test must be re-gated
`#[cfg(any(feature = "cuda", test))]` -- co-gating every helper it transitively calls -- instead of
staying plain `#[cfg(feature = "cuda")]`. A default `cargo test` (no `--features cuda`, what CI's
release-gating static-analysis job runs) never compiles a plain-`cuda`-gated test at all, so a test
written against a plain-`cuda`-gated helper silently never runs; separately, un-gating only the test
without also re-gating
the helper leaves the helper with zero default-build callers and fails `cargo clippy -- -D warnings` on
`dead_code`. `any(feature = "cuda", test)` solves both at once: the helper compiles whenever `cuda` is
enabled (unchanged production behavior) OR whenever `cfg(test)` is set, so the test has something to call
and is not itself dead code, while staying absent from the default non-test release build. Precedent:
`GpuRouteFailureKind` / `sanitize_cuda_detail` / `classify_gpu_route_failure` in `rust_core/src/main.rs`
(gate-nit #172 NIT-4 / MF-1, shipped in `#597` / v1.75.4).

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

Merge one release-bearing PR at a time and wait for main CI + semantic-release to finish before merging the next. Concurrent squash-merges to `main` can race at the semantic-release step and produce a skipped release or a wrong version bump. `chore:` / `docs:` / `test:` titles do not bump the version — but that is NOT a licence to merge them while a prior release is in flight (see the push-race note directly below). "Safe to interleave" means *after the prior release has fully published* (its `chore(release): vX` commit is on `main` and PyPI shows the new version), not merely after its PR CI is green.

### Release publish is not instant — the push-race (hard-won, re-confirmed 2026-07-02)

The real publish is the **`Semantic Release` job inside `.github/workflows/ci.yml`** (gated `github.ref == 'refs/heads/main' && github.event_name == 'push'`), NOT `release.yml` (which is `workflow_dispatch`-only, so a manually-pushed `v*` tag can no longer bypass semantic-release). That job **compiles the native assets before it publishes, so it runs for ~6 minutes** — and that whole window is a race window.

If ANY other merge lands on `main` during that window — *including a no-release `docs:`/`chore:` PR* — the merge advances `main`, and the in-flight release job's final `git push origin main` (the `chore(release)` version-bump commit) is **rejected non-fast-forward** (`! [rejected]  main -> main`), so **that version never publishes**. The CI concurrency group is necessary but INSUFFICIENT: it serializes runs, not the human/agent act of clicking merge. Receipt: `v1.17.23` (a security batch, #318) failed to publish because the GPU-pause `docs:` PR (#319) was merged while #318's release job was still compiling assets.

Recovery — **do NOT panic-rerun**: the failure self-heals. The next push-to-`main` CI run re-runs `Semantic Release`, and because the version is **derived from the git tags** (not the failed run's state), it recomputes the correct next version and covers the orphaned `fix:`/`feat:` commit. Just confirm that next run's `Semantic Release` job succeeds and the tag/PyPI version appears; the fix's *code* was already on `main` regardless — only the publish step was behind.

Diagnosing a "didn't publish": decode the structured job result FIRST (`gh run view <id> --json jobs` → find `Semantic Release` → read `--log-failed`). Do not theorize from tracebacks. A `! [rejected]  main -> main` line is the push-race signature; a genuinely different failure is a different problem.

Preferred approach:

1. use a clean replay worktree
2. rebase/reset to current `origin/main`
3. rerun narrow checks and relevant benchmarks
4. push only the accepted change
5. open a PR with the correct conventional title and wait for PR CI/CodeQL to pass
6. if the change is release-bearing and intended to ship now, squash-merge the PR to `main`
7. wait for main CI and semantic-release complete successfully, plus CodeQL, `publish-github-release-assets`, PyPI/package artifact validation, `publish-pypi`, and `publish-success-gate`
8. also check the `release-tag-smoke` JOB's own conclusion inside the release run (`gh run view <id>
   --json jobs`), not just latest-main-green -- it is `needs`-gated on `[release, publish-success-gate]`
   (not `continue-on-error`), checks out the actual published tag and runs `agent_readiness.py` against
   it, and sat red across `v1.64.4`+ while PyPI kept publishing, masking a real regression for 4 releases
9. verify the GitHub release assets, PyPI latest version, and any affected public installer/update path. PyPI/public installer availability is verified before final release status is reported
10. after semantic-release completes, `git fetch origin main --tags` and fast-forward local `main` to the release commit before reporting the final version state

Do not report a release-bearing fix as complete after only a branch push, open PR, or green PR checks. The final report must name the PR, merge commit, main CI run, CodeQL run, released tag/version, PyPI/package publish status, and any local/public installer dogfood result.

For docs/test/chore-only work, use a non-release PR title, wait for PR CI, and merge only when requested or clearly required. After merge, main CI should pass but semantic-release should skip release publishing.

### Build ahead of a release gate (pipelining)

The push-race gate above blocks the *merge* step, not the *build* step. Once `origin/main` has advanced
past a collision-blocked PR's base, that PR can safely rebase, rebuild, and re-run its full local/CI
validation **in parallel with an in-flight release** -- only the final squash-merge must still wait for
the prior release to fully publish (the `chore(release)` commit on `main` plus PyPI). Doing the
rebase/rebuild/verify work eagerly, instead of sitting idle until the release window closes, saves
roughly 40 minutes per PR across a multi-PR drain campaign. This is the same shape as three well-known
patterns, named here so it is recognizable rather than reinvented: a **merge queue** / speculative CI
(validate against a projected future base before the real merge lands), a **release train** (fixed
publish cadence; work queues up between departures without blocking on any single publish), and
**build-once-promote-everywhere** (a single verified artifact is promoted through gates rather than
rebuilt at each one). Only the merge itself is push-race-gated; the build is not.

## PR Title And Release Intent

AI-generated PRs must use conventional titles so CI can infer semantic-release intent.

Use this schema:

- `feat: ...` => minor release
- `fix: ...` or `perf: ...` => patch release
- `feat!: ...` or `fix!: ...` => major release
- `docs: ...`, `test: ...`, `chore: ...`, `ci: ...`, `build: ...` => no release

Release-bearing PRs must use `Squash and merge` so the validated PR title becomes the commit subject on `main`.

Do not manually create release tags when semantic-release is active.

## Local Dev Gotchas (Windows, hard-won)

Small, non-obvious traps that have each cost a real cycle on this desktop. None are version-specific.

- **`git commit -m "..."` with backticks runs command substitution.** A message containing `` `...` `` (e.g. a fenced identifier) is interpreted by the shell and mangles the commit. Use `git commit -F <file>` or a single-quoted `<<'EOF'` heredoc for any message with backticks, `$`, or `!`.
- **cargo/rustc are off `PATH` here — and a "hanging" Rust build is almost always a false alarm.** Use `C:/Users/oimir/.cargo/bin/cargo.exe` (or prepend `~/.cargo/bin` to `PATH`). What looks like a hang is slow LTO that *completes*: `maturin develop` is ~15 s, a `--release` build is minutes. Do not kill it as hung; let it finish. (The build command for stale in-tree binaries is under the doctor note above.)
- **Verify FFI / PyO3 bridge changes against the REAL compiled extension, not mocks.** This is the "Dogfood the Real Binary" trap one layer down: mock-based tests passed green while the *real* bridge was dead (it dropped every forwarded flag and silently fell back to the Python engine). Prove a bridge change with a live runtime call into the built extension, then confirm the flag actually reached `rg`.
- **After a squash-merge, apply follow-up fixes by SYMBOL, not by line number.** Merges shift every line below the change; a plan that says "fix `main.py:8468`" is stale the moment anything above it lands. Re-anchor on the function/const name (grep or `tg defs`) before editing.
- **A dependency UPPER-cap can silently downgrade the whole install on a newer Python.** If an upper bound (e.g. `typer<0.25`) has no release compatible with a new Python, `pip`/`uv` resolve the *entire package* DOWN to a stale version with NO error — `requires-python>=X` has no upper bound to catch it. When a fresh Python yields a stale `tg`, suspect a transitive cap (typer/click/pydantic), not `requires-python`.
- **Windows symlink creation needs privilege.** Tests that create symlinks must `pytest.skip` on `OSError` / `NotImplementedError`, or they false-fail on an unprivileged run.
- **A stray `nul` file in the tree is a Windows `2>nul` redirect artifact.** Use `2>$null` (PowerShell) or `2>/dev/null` (bash); clean up with `rm -f ./nul`.
- **CRLF makes a local bare `ruff format --check` false-alarm** over LF-committed blobs. Run `ruff format --preview <files>` (which normalizes) before commit — see "Required Local Validation" for why `--preview` is mandatory and must never be passed to `ruff check`.
- **The full local gate is four steps, not two — and re-run them after your LAST edit.** `ruff check` + `pytest` passing is NOT green: the CI "Formatting & Linting" job also runs `ruff format --check --preview .` (a *formatter*, distinct from the `ruff check` *linter* — a post-edit line-wrap or over-long comment passes `ruff check` but fails `ruff format --check`) AND `mypy src/tensor_grep` (catches type errors nothing else flags, e.g. assigning to a `Final` attribute like click's `UsageError.message` — mutate it and mypy errors; raise a fresh `UsageError(...)` instead). Running only `ruff check` + `pytest` — or running the gate before an *intermediate* edit that a later edit then invalidates — cost two drain-blocking CI failures in a single session (a mypy `Final`-assign and a `ruff format` line-wrap). Run all four (`ruff check` · `ruff format --check --preview` · `mypy src/tensor_grep` · `pytest`) on the touched files AFTER the final edit.

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

