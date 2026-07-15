# tensor-grep — Project Backlog & PR Tracker

> **Canonical prioritized work list.** Kept in sync with the CLI task store (`TaskUpdate`) and
> GitHub (`gh pr list` is the source of truth for PRs). **CEO status** = summarize SHIPPING + P0/P1.
> Update whenever a PR opens/merges or the queue changes. Task-store IDs (`#NNN`) cross-referenced.
> Last refreshed 2026-07-15 (backlog-steward tick). **Live PyPI is v1.76.9** (published; the v1.76.x
> "remaining AI-actionable backlog" wave #176 + dogfood/hardening follow-ups drained one-per-publish, ZERO broken *published* releases).
> Shipped 10 PRs: v1.76.0 #601 route-test / v1.76.1 #602 checkpoint-symlink / v1.76.2 #604 perf / v1.76.3 #603 daemon-guard /
> v1.76.4 #605 cuda-ceiling / v1.76.5 #606 orient-scope / v1.76.6 #608 agent-scope / v1.76.7 #610 daemon-coercion+rust-checkpoint-cleanup /
> v1.76.8 #611 checkpoint-symlink-disclosure (**security**) / v1.76.9 #612 GPU-calibrate-honesty; + #613 flaky-test hardening (no-release) -- PR queue EMPTY.
> Prior: v1.75.0->v1.75.4 GPU Phase-0 (#593/#594/#595/#596/#597, #173 reconcile); v1.73.0->v1.74.4
> (#584/#585/#131-F3/#164/#166/#591); v1.70.0->v1.72.1; v1.69.0-.3; #142.

**Process:** deep-dive/audit (cite `file:line`) → verify-against-code → Sonnet TDD build in
`isolation:'worktree'` → real-venv verify (`uv run --active --no-sync`; copy `rust_core.pyd`, set
VIRTUAL_ENV+PYTHONPATH — a worktree "tests pass" is a hypothesis) → `ruff check` + `ruff format
--preview` + `mypy` (+ `cargo fmt --check`/`clippy` for Rust) → **mandatory adversarial Opus gate** if
it touches apply_policy/mcp/cpu_backend/index_lock/session_daemon/backends → PR → drain
(one-merge-per-publish). Match model to task. Common-sense gate before pending the CEO.

**Legend:** `P0` ship-blocking/#1 gap · `P1` HIGH bug/moat · `P2` MED · `P3` LOW. Status:
`[shipping]` open PR · `[ready]` buildable · `[wip-blocked]` cap-blocked (>5 PRs) · `[blocked]` gated · `[done]`.

**Drain discipline (hard-won 2026-07-10):** verify publish via `/simple` full wheel-pattern
`tensor.grep-1.58.N` OR the release run's publish-pypi=success — NOT a top-level "completed/success"
(can be a non-release run), NOT `grep | head` (head masks grep's exit). Stamp-on-main = Semantic
Release done (safe once /simple lists it). A run `in_progress` on "Python Semantic Release" = native
wheel compile (~65min normal), don't panic-rerun. **WIP CAP: no new build while >5 PRs undrained.**

---

## ⭐ CURRENT STATE (2026-07-15) — authoritative; every section BELOW is HISTORICAL until the next full refresh

- **Live PyPI: v1.76.9 (2026-07-15, published). Post-#176 hardening + dogfood wave — 4 more PRs, ZERO broken *published* releases:** v1.76.7 **#610** gate-NIT hardening (session-daemon metadata coercion-safe removal via `_daemon_identity()` on both sides + Rust `create_checkpoint` fail-closed cleanup `remove_dir_all` on write-failure; Opus SHIP-WITH-NITS) · v1.76.8 **#611** checkpoint snapshot **SECURITY** — no longer follows symlinks (out-of-root file-disclosure): recreate-as-symlink instead of `std::fs::copy`, undo fail-closed via `_resolve_within_root` (Opus SHIP; F1 comment-accuracy + F2a Windows `ERROR_PRIVILEGE_NOT_HELD` message MUST-FIXes addressed + re-verified RED-GREEN) · v1.76.9 **#612** GPU `tg calibrate`/`doctor` guidance honest when this build ships no nvidia asset (CEO v1.76.6-dogfood ask — conditions on the Rust `#[cfg(feature="cuda")]` compile flag, splits the shared hint into no-cuda-build vs device-not-found so an nvidia-binary user is never told "not shipped"; Opus SHIP-WITH-NITS = #182) · **#613** widen the flaky `test_index_lock` heartbeat timing bound 0.6->2.0s for loaded CI runners (`test:` no-release; RED-GREEN verified 0.064s green vs 3.977s sabotaged). **PR queue EMPTY (0 open).** RELEASE-FAILURE NUANCE reinforced: v1.76.9's FIRST run FAILED on that timing-flaky heartbeat test (Semantic Release SKIPPED, no tag, PyPI not bumped) — a job-failure release does NOT self-heal (distinct from a push-race rejection), `gh run rerun --failed` cleared it (flaky passed on retry) and #613 hardens it against recurrence. **#90 CLOSED** — ast-grep "doctor false-available (exit-127 shim)" verified already-fixed in #130(b) (`is_available()` probe-RUNS each `which()`-resolved candidate via `ast-grep --version`, gates on exit 0); native dogfood confirmed. **AI-actionable backlog EMPTY** — remainder demand-deferred (#98 MCP-consolidation, #141 native-AstBackend), env-blocked (#89 WSL /mnt/c path, needs Linux), CEO-gated (#72 benchmark publish, #77 tg-ledger, GPU flag-flip held/Phase-2), or LOW opportunistic-batch (#182 = #612 gate NITs).
- **Live PyPI: v1.76.6 (2026-07-15, published). Directive #176 ("implement the remaining AI-actionable backlog") COMPLETE + a dogfood follow-up (#608) — a 7-PR wave, Sonnet-TDD in `isolation:'worktree'`, Opus-gated where load-bearing, drained one-per-publish, ZERO broken releases:** v1.76.0 **#601** promote `tg route-test` hidden->public (also closed a native-front-door gap — route-test was absent from the rust front door; dogfood-verified on the wheel) · v1.76.1 **#602** checkpoint/rollback write symlink-hardening (Opus SHIP — genuinely TOCTOU-safe incl. Windows `FILE_FLAG_OPEN_REPARSE_POINT` same-handle check, NOT the #110 O_NOFOLLOW-noop) · v1.76.2 **#604** perf `@lru_cache _expected_tg_version` + `tg importers` dead-provenance precision fix · v1.76.3 **#603** session-daemon removes only its OWN metadata (stale-daemon orphan-pileup guard; Opus SHIP-WITH-NITS) · v1.76.4 **#605** bound the cuda GPU implicit-walk to mirror the #105 native DoS ceiling (Opus SHIP-WITH-NITS, exact parity + fail-closed) · v1.76.5 **#606** `tg orient` `suggested_scope` excludes deweighted/ignored trees (no longer misdirects agents to `.claude`; dogfood-verified agent-studio `.claude`->`scripts/`) · v1.76.6 **#608** `tg agent`/`context-render` `suggested_scope` excludes ignored trees too — the #606 SIBLING that dogfooding the SHIPPED v1.76.5 wheel caught (tg agent STILL misdirected suggested_scope to `.claude` while suggested_ignore excluded it; CI + the #606 review both missed it; dogfood-verified before/after `.claude`->`scripts/`). **PR queue EMPTY (0 open).** One CI hiccup self-corrected: v1.76.3 hit a transient Windows dep-install flake -> `gh run rerun --failed` cleared it (a job-failure release does NOT self-heal, unlike a push-race rejection — banked). Cleanup done (6 agent worktrees + all branches pruned). **AI-actionable backlog is now EMPTY** — remainder is demand-deferred (#98 MCP-consolidation, #141 native-AstBackend), env-blocked (#89/#90, need Linux/WSL), or LOW nits (#178/#125; #179 shipped as #608). DOGFOOD LESSON reinforced: running the SHIPPED wheel after a fix catches sibling gaps that CI + the fix's own review miss — #179 was found dogfooding v1.76.5.
- **Live PyPI: v1.75.4 (2026-07-14, published).** The GPU Phase-0 program drained one-per-publish, ZERO
  broken releases: **v1.75.0** #593 `tg orient`/`tg agent` broaden `suggested_ignore` to whole vendor/
  skill trees (M1+M2, a CEO-dogfood-found gap in #164's `.claude` deweight) | **v1.75.1** #594 GPU
  Phase-0 P0-1 WSL probe path-domain bridging + a `cargo check --features cuda` anti-bit-rot CI gate |
  **v1.75.2** #595 GPU Phase-0 P0-2/P0-3 doctor probe failure-taxonomy + honest device-id validation |
  **v1.75.3** #596 GPU Phase-0 P0-4/P0-5 calibrated remediation message + loud nvidia->cpu installer
  downgrade | **v1.75.4** #597 GPU Phase-0 gate-nits (**#172**): doctor-probe precision + native
  error-kind taxonomy, 5 nits incl. the `cfg(any(cuda,test))` classifier fix that silently skipped 3
  tests under a default `cargo test`. Together this closes out **#171** (the GPU Phase-0 program) --
  full receipt in CURRENT LIVE BACKLOG below. **HONEST SCOPE (council must-fix MF-3):** this wave
  hardens the CPU-default GPU code path's correctness/observability under the existing default-OFF
  `TENSOR_GREP_RELEASE_NATIVE_ASSET_PROFILE` gate -- it does NOT promote GPU, change the CPU-default
  recommendation, or prove a speed crossover; full reframe in CEO-FACING GPU below. **#592** (prior
  docs reconcile to v1.74.x) merged `adf5750`; the PR queue was empty going into this wave and is empty
  again after it (see SHIPPING below).
- **Prior wave: v1.74.4 (2026-07-14, published).** The v1.73.0->v1.74.x
  wave — the CEO's v1.72.1 dogfood tail + the v1.74.0 WSL-saddle dogfood fix-queue (#164) — drained
  one-per-publish, ZERO broken releases: **v1.73.0** #584 `tg edit-plan` top-level `confidence` +
  `ask_user_before_editing` (agent parity) & #585 `--deadline` on source/docs-coverage/blast-radius-plan ·
  **v1.74.1** #131-F3 fail-closed `GpuSearchParams` flag completeness (replace/only-matching/max-filesize/
  color/no-ignore-vcs + `context`) · **v1.74.2** #164 embed mermaid in JSON when `--json --mermaid` combined
  (was: `--mermaid` silently dropped under `--json`) · **v1.74.3** #166 clean error + exit 2 for explicit
  `--gpu-device-ids` with no GPU backend (was a raw `ConfigurationError` traceback) · **v1.74.4 (releasing)**
  #164 `tg orient` deweight `.claude` tool-config trees + populate `suggested_ignore` (real-corpus validated:
  agent-studio 10/10 `.claude` in top-10 central_files -> 0/10; tensor-grep byte-identical). **HONEST
  CORRECTION (dogfood-the-shipped-artifact):** F3 (v1.74.1) hardened the rust GPU path, but dogfooding the
  live wheel proved `tg --gpu-device-ids` is handled ENTIRELY by the Python `Pipeline` (selects CuDF/Torch
  backend or raises `ConfigurationError`) and NEVER invokes the rust `handle_gpu_search` — so F3 is CLI
  dead-code. Corrected to the CEO, closed #131/#165, filed the real UX fix as #166 (shipped v1.74.3). **CEO
  1.74.0 dogfood FULLY addressed:** --mermaid (v1.74.2), GPU traceback (v1.74.3), orient-deweight (v1.74.4);
  session_id absence = not-a-bug (uniformly absent across agent/orient/callers, filed LOW observability);
  WSL timeouts = 9p artifacts (native-repro'd, complete). **#591** (`chore(test):`, no release) widened
  timing headroom on 2 flaky sidecar-IPC timeout tests (#167) — MERGED (`fc231ed`). **#592** (this docs
  reconcile) is the lone open PR (was branched from a stale local main at v1.74.0; rebased onto current
  main so its `pip-audit` sees the shipped setuptools 83.0.0, not the pre-bump 82.0.0).
- **Prior wave: v1.72.1 (2026-07-13) — the edit-plan/agent-parity + `--deadline` coverage wave, drained one-per-publish, ZERO broken releases, dogfood-verified where noted:** v1.71.3 **#159** `tg lsp` fail-closed with a clean "pip install tensor-grep[ast]" message on the missing `ast` extra (was a raw `ModuleNotFoundError` traceback; run `29281694988`) · v1.72.0 **#580** `tg edit-plan` structured top-level `validation_plan` (parity with `tg agent`; the CEO v1.71.1 dogfood ask #1) · v1.72.1 **#581** accept `--deadline`/`--no-deadline` on agent/edit-plan/context/context-render/map/orient + `--deadline` on defs (the CEO v1.71.3 dogfood HIGH — the exit-2 "No such option" cliff that burned agent loops; dogfood-verified on the wheel: all 7 accept it, enforced, correct exit codes, orient stays exit-0 per its NO-exit-2 contract). **#582 merged (test-only, `test(cli):`, no release)** — closes PR #581's Opus-gate coverage gaps (daemon-skip regression test w/ passing mutation-check + real-truncation exit-2 + agent-2nd-scan + `CONTRACTS.md` `tg context` nit); full CI matrix green (`6cb53a4`). **PR queue now EMPTY (0 open).** Docs-only, no release, both merged: #578 (4-skill WSL-artifact corrections) + #579 (prior backlog refresh).
- **Prior wave (v1.70.0-v1.71.2, 2026-07-13) — the v1.69.3-dogfood MED batch + audit sweep, drained one-per-publish, ZERO broken releases, all dogfood-verified on published wheels:** v1.70.0 **#152** sys.path.insert imports (2 HIGH) · v1.70.1 **#127** non-git `.gitignore` · v1.70.2 **#90b** `tg doctor` ast-grep exit-0 honesty · v1.71.0 **#153** `tg codemap` default deadline (agent-loop-safe) · v1.71.1 **#154** unscoped/multi-root search fast-refuse (<1s vs 60s timeout — enterprise gap #1) · v1.71.2 **#158** `tg scan` marked-root workspace refuse (the #154 sibling; verified on the wheel — fast-refuses a marked workspace parent). **#578** (docs, no release): 4-skill accuracy refresh correcting TWO false WSL-`/mnt/c` "regression" claims (whole-repo `tg agent` + `tg codemap` — native repro: agent ~26s, codemap 41s whole-repo `partial=false` complete). **CodeQL alert #13 (py/redos test fixture) resolved** (dismissed — false positive on a deliberate ReDoS fixture). **Moat FULLY dogfood-verified on real code** (orient / agent / `search --rank` / `--semantic` graceful-degrade / codemap + #158 scan) — all healthy.
- **Prior wave (v1.70.0) -- the CEO's 2 HIGH `sys.path.insert` fix (#152/#568, `feat` = minor bump), dogfood-verified on the published wheel.** CEO v1.69.3 dogfood found `tg imports`/`importers` did NOT resolve `sys.path.insert(0, .../lib)` path-hacked modules (`from ultrathink_routing import` -> `resolved=None`/`external=True`). Fix parses statically-resolvable `sys.path.insert/append` dirs as import search roots for BOTH the forward (`_python_imports_with_lines`) and reverse (`_python_imports_and_symbols`) resolvers in `repo_map.py`; dynamic/out-of-root exprs stay external (honest). **Verified live on the v1.70.0 wheel** (clean venv): forward resolves `.../lib/ultrathink_routing.py` (`external=False`); reverse `tg importers` -> `importer_count=1, importers=['main.py']`. The release recovered from a razor-thin timing flake in an UNRELATED perf test (`test_incremental_refresh`, missed the `<0.5x` bar by 0.0013s -- NOT a #152 regression): the rerun passed + `release-tag-smoke`=success on the wheel; **#569** (`6eaf384`, `test:`, no release) permanently de-flakes it (per-file sleep raised so the signal dominates the shared graph overhead). **DRAINING one-per-publish: #570** index `.gitignore` non-git-dir no-op fix (#127, `add_ignore` trio in `index.rs`, Opus-gate SHIP, 5 Rust tests) -> **v1.70.1**.
- **Prior wave (v1.69.3): #151 shipped (2026-07-13):** running the published wheel on 3 real external repos (flask/fastapi/requests) surfaced one genuine correctness gap -- `tg importers FILE [ROOT]` (ROOT defaults to CWD) returned an empty `importer_count` with NO signal when FILE is OUTSIDE ROOT (indistinguishable from "genuinely unimported"; silent-wrong for an agent shelling `tg importers /other/repo/file.py` from a different CWD). Fix (**#566** `00e4e99`, Sonnet-TDD -> **Opus gate SHIP** 7-axis adversarial, additive-only, MCP output-shape safe): a lexical containment check in `build_file_importers_from_map` stamps `file_outside_root` + an honest `scan_remediation`. **Dogfood-verified on the published v1.69.3 wheel:** outside-root -> `file_outside_root:true` + remediation; in-root -> `false` + correct `importer_count`. fastapi/requests batteries were clean (no new defects).
- **v1.69.0-.2 (prior wave):** **CEO v1.68.1 WSL-dogfood drain COMPLETE** (2026-07-13) - 3 genuine fixes built (Sonnet-TDD in `isolation:'worktree'`, Opus-gated where MCP-reaching), drained one-per-publish, **zero broken releases**, all **dogfood-verified on the published v1.69.2 wheel** (`release-tag-smoke` = success on the wheel): (a) **#562** `tg codemap --ignore` + `--deadline` (`codemap.py:862`, reuses `_apply_ignore_globs`; no MCP/backend surface) -> **v1.69.0**, both flags accepted + JSON emitted; (b) **#563** F2 nested-import recall (`repo_map.py` two `tree.body` -> `ast.walk(tree)` at :5827/:1813; `tg imports`/`importers` had silently missed function/class-scoped imports incl. the repo's own `main.py -> repo_map.py`; Opus SHIP) -> **v1.69.1**, verified nested `json`+`collections` now resolve alongside top-level `os`; (c) **#564** F3 `suggested_scope`-on-tie (`agent_capsule.py` new `_suggested_scope_from_tied_targets` :197, trigger :2375; the ambiguous-tie path now emits a narrowing scope (deepest common parent of the tied candidates) when they share a subtree, honest-null when the tie spans the whole repo -- both confirmed by dogfood; touches `tg_agent_capsule` MCP; **Opus SHIP** + gate-recommended `os.path.normpath` `..`-confinement hardening + probe test, 11/11 real-venv) -> **v1.69.2**, verified code+normpath-hardening shipped. **WSL-artifacts DEBUNKED (not chased):** codemap "60-180s/no JSON" = WSL 9p (native 33s complete); daemon "not warm" = a naive 2-run test that never hit cache (real ~90-150x cold->warm); env-blocked **#89/#90** need a Linux/WSL box.
- **Prior wave:** **Live PyPI was v1.68.2.** **Campaign #142 ("backlog-100") COMPLETE** — all 4 PRs drained one-per-publish, zero broken releases. **Post-campaign (docs-only, no release):** #559 backlog-reconcile + #560 AGENTS.md whole-repo ruff-scope hardening merged; local-git hygiene = 46 stale branches + 9 remote refs cleaned. Release-blocker learnings banked: `tensor-grep-whole-repo-ruff-format-gap-and-git-show-smudge-2026-07-12` (doc-code-block ruff-format + stale-lock rode into #553; hotfixed via #558) + `tensor-grep-windows-worktree-agents-mask-cross-platform-ci-2026-07-12` (#556 Windows-path tests failed Linux CI).
- **Campaign #142 4-PR queue DRAINED** (Sonnet-built, Opus-gated, one-per-publish): **#554** mcp default 512→2000 (#98) → v1.67.1 · **#555** daemon Tier-2 orient/agent (#108, ~16x latency — dogfood-verified 15.8s→0.95s on the PUBLISHED wheel) → v1.68.0 · **#556** apply_policy UNC-bypass + cross-platform test hardening (#126) → v1.68.1 · **#557** `--count-matches` honest-refuse (#121) → v1.68.2. The mandatory security/correctness gate caught+fixed PRE-MERGE: a UNC command-injection edge (#556), a contract-governance gap (#557), a cross-platform test hole (#556), and a daemon cold-rescue recall regression (#555).
- **Campaign #142 ("backlog-100")**: 4 Fable design-planner audits (`docs/plans/backlog-100/cluster-{1,2,3,4}-*.md`, 2026-07-12) re-verified this ENTIRE ledger, file:line-cited, against the real tree. Headline: **the ledger was badly stale** — most standing items were already shipped across 4 drain waves (#514–#537) that never got written back here. This refresh reconciles it.
- **Reconciled this campaign (already-fixed → dropped from the live backlog below; full per-item receipts in the cluster docs):**
  - **P0 #128/#130/#131 audit queue — 9 of 12 sub-items already fixed**, drain wave #514-#523: #128a ast-grep malformed-JSON→`BackendExecutionError` (`c9e54ef`/#515) · #128b nested-`.gitignore` in both Python walkers (`29269ef`/#522 + `5bf49ad`/#523) · #130a inventory `--deadline`→files=0 (`f88c2a0`/#516) · #130b `tg refs` "45s hang" **superseded/debunked** (deadline-bounded since #393/#478/#440; live repro = 9.16s, exit 2, `partial:true` — an honest partial, not a hang) · #130c checkpoint `IsADirectoryError` (`fad9c2e`/#517) · #130d doctor false `ast_grep.available` (`ac2e153`/#518) · #131 F1 PFAC doc claim (`1889a69`/#514) · F2 GPU benchmark `line_number` vs native `line` key (`7bbe15c`/#519) · F10 dead GPU code (`4a72fca`/#520). Only **#128d, #128c, F3** survive — see CURRENT LIVE BACKLOG. Cite: `cluster-1-p0-correctness.md`.
  - **#118** (#93 SUB-3 unscoped-refuse + SUB-2 companion) — fully shipped via `#506`+`#528`; the companion shipped as **`suggested_scope`** (the old ledger's "suggested_ignore" name never existed in code). **#130 features (a) validation_plan parity + (c) confidence-lift** — shipped via **`#475`** (`ae3ec6d`, v1.54.2, the #84 design). Only **#130(b) sys.path.insert** survives. Cite: `cluster-2-p1-moat.md`.
  - **#129** help-probe-timeout de-flake — closed, two independent control-run fixes (`#521` Python e2e + `#537` Rust sidecar-IPC). **#73** hygiene-guard blind spot (kvikio/dstorage readers) — closed, KEEP-AND-DOCUMENT shipped in `4a72fca`/`#520`. Cite: `cluster-3-p2-followups.md`.
  - **#22, #38, #44, #47, #48, #59, #62 — ALL CLOSED** (the 7 oldest ledger entries, PR3b-era through 2026-07-07): fixed, superseded, or re-homed on receipts (retention-cap #329/#427 · audit-manifest digest+verify system · lockfile #355/#376 · AST byte-budget cache #539 · render-flag guard · sidecar envelope #304 · version-soup structurally gated · daemon Tier-1 #492/#498 · recall+honesty wave #463/#504/#418 · exit-2 contract #419 · Go Stage-1 #420/#422/#431). **#38 (`tg diff-docs`) killed outright** — retirement line added to `PAPER.md` §3.10. **#63 converts to one small build item** (F19+F22+F26 lang-graph tail — see CURRENT LIVE BACKLOG). Full receipts: `cluster-4-stale-reconcile.md`.
- **Net effect:** CURRENT LIVE BACKLOG below is a full rewrite — every surviving item is re-cited against today's tree; #89/#90/#109 (Linux-blocked) carry forward unaudited (outside campaign #142's scope).
- **CEO-gated (the CEO's call):** benchmark publish #72 (the 7.5x-fewer-tokens-than-grep proof) · `tg ledger` #77 (local agent coordination) · GPU multi-week rebuild (conflicts with no-SaaS) · next-language expansion (Java/C#/C++/Ruby/PHP). See CEO-FACING below.
- **Strategic (standing CEO steer, still in force):** tool WORKS (moat = **7.5x fewer tokens than grep on definition-lookup**, benchmark-proven); finish the moat + shift to gotcontext wiring vs draining the self-refilling tail; no-SaaS (gotcontext.ai is the SaaS shell, not tg).

---

## SHIPPING — open PRs (drain one-per-publish) — task #117

**Queue empty -- 0 open PRs.** The v1.75.0->v1.75.4 GPU Phase-0 wave (#593/#594/#595/#596/#597) drained
one-per-publish, ZERO broken releases, closing out **#171** (GPU Phase-0 program, P0-1..P0-5) + **#172**
(gate-nits). Prior: v1.73.0->v1.74.4 (#584/#585/#131-F3/#164/#166/#591); v1.70.0->v1.72.1
(#152/#127/#90b/#153/#154/#158/#159/#580/#581); the v1.68.1 CEO WSL-dogfood 3-PR drain (#562/#563/#564
-> v1.69.0/.1/.2); campaign #142's 4-PR queue (#554-557 -> v1.67.1-v1.68.2) -- all clean. This BACKLOG
reconcile (`docs:`, no release, **#173**) is the next PR to open -- drain clear, no other build queued.
**After it merges, next move is CEO-gated or demand-gated** (see CURRENT LIVE BACKLOG).

## SHIPPED — live on PyPI up to **v1.76.9** (v1.76.0-.9 detail in CURRENT STATE above)

**v1.75.0-v1.75.4 window (2026-07-14, merged, on PyPI) -- GPU Phase-0 program #171 + gate-nits #172
complete:** #593 `tg orient`/`tg agent` broaden `suggested_ignore` to whole vendor/skill trees, M1+M2
(v1.75.0) | #594 GPU Phase-0 P0-1 WSL probe path-domain bridging + `cargo check --features cuda`
anti-bit-rot CI gate (v1.75.1) | #595 GPU Phase-0 P0-2/P0-3 doctor probe failure-taxonomy + honest
device-id validation (v1.75.2) | #596 GPU Phase-0 P0-4/P0-5 calibrated remediation message + loud
nvidia->cpu installer downgrade (v1.75.3) | #597 GPU Phase-0 gate-nits: doctor-probe precision + native
error-kind taxonomy, 5 nits incl. the `cfg(any(cuda,test))` classifier fix (v1.75.4). **Scope stays
CPU-default-honest** -- this hardens the gated-OFF GPU code path's correctness/observability; it does
not promote GPU or prove a speed crossover (full reframe: CEO-FACING GPU below).

**v1.73.0-v1.74.4 window (2026-07-14, merged, on PyPI):** #584 `tg edit-plan` top-level confidence +
ask_user_before_editing (v1.73.0) · #585 `--deadline` on source/docs-coverage/blast-radius-plan (v1.73.0) ·
#131-F3 fail-closed GpuSearchParams flag completeness (v1.74.1 — later dogfood-proved CLI-dead-code; the
rust GPU path is unreachable from `tg --gpu-device-ids`, which the Python Pipeline owns; #131/#165 closed) ·
#164 embed mermaid in JSON under `--json --mermaid` (v1.74.2) · #166 clean error + exit 2 for `--gpu-device-ids`
without a GPU backend (v1.74.3) · #164 orient deweight `.claude` tool-config + `suggested_ignore` (v1.74.4,
real-corpus validated). v1.74.0 (prior wave, CEO dogfood target).

**v1.71.3-v1.72.1 window (2026-07-13, merged, on PyPI):** #159/#577 `tg lsp` fail-closed on the missing `ast` extra (v1.71.3) · #580 `tg edit-plan` structured top-level `validation_plan`, parity with `tg agent` (v1.72.0) · #581 accept `--deadline`/`--no-deadline` on agent/edit-plan/context/context-render/map/orient + `--deadline` on defs (v1.72.1, dogfood-verified on the wheel: all 7 accept it, orient stays exit-0) · **#582** (`test(cli):`, merged, no release) closes #581's Opus-gate coverage gaps, full CI matrix green (`6cb53a4`). Docs-only, no release: #578 (4-skill WSL-artifact corrections) + #579 (prior backlog refresh).

**v1.70.0-v1.71.2 window (2026-07-13, merged, on PyPI):** #152/#568 sys.path.insert imports resolution — 2 HIGH (v1.70.0) · #127/#570 non-git `.gitignore` (v1.70.1) · #90b/#571 `tg doctor` ast-grep exit-0 honesty (v1.70.2) · #153/#573 `tg codemap` default deadline (v1.71.0) · #154/#574 unscoped/multi-root fast-refuse (v1.71.1) · #158/#576 `tg scan` marked-root workspace refuse (v1.71.2) · #572 skills + BACKLOG docs refresh (`docs:`) · #575 **CLOSED** (CodeQL py/redos suppression — non-functional inline comment; the API dismissal is the real fix).

**v1.59–v1.66.1 window (merged, on PyPI):** #541 index capability-validator · #542 AstBackend tree-sitter query-API repair · #543 warm-daemon default-ON flip (#94 latency lever) · #544 `--index` front-door routing · #545 `--rank` chunk cap · #2/#546 atomic + cross-process-locked index write · #547 backlog reconcile · #63/#548 iterative Go AST walk (no RecursionError) + Python `in_annotation` leak + registry-dispatch governance test · #92/#549 `tg classify --stdin/--text` · #550 ast-grep fail-closed · #551 wedged-python help-probe deflake · #552 launcher import-defer perf · #124-P2/#553 Ed25519 evidence-signing (v1.67.0) · #558 release-blocker hotfix · #554-557 campaign-100 (v1.67.1→v1.68.2, incl. #108 daemon Tier-2 -> v1.68.0, #126 apply_policy fail-open -> v1.68.1, #121 --count-matches -> v1.68.2) · #559 backlog-reconcile (docs) · #560 AGENTS.md whole-repo ruff-scope hardening (docs) · #561 backlog-refresh v1.68.1->v1.68.2 (docs) · **#562 codemap --ignore/--deadline (v1.69.0)** · **#563 nested-import recall (v1.69.1)** · **#564 suggested_scope-on-tie + normpath ..-confinement (v1.69.2)** · **#566 importers outside-root honest signal (v1.69.3, dogfood-found on flask)** · #565/#567 backlog refreshes (docs) · **#130b/#568 sys.path.insert import resolution (v1.70.0)**. Older detail below is HISTORICAL.

Prior batch: #499→v1.58.5 (tg_repo_map 512→2000) · #500→v1.58.6 (#110 write-path symlink TOCTOU) ·
#503→v1.58.7 + #505→v1.58.8 (two flaky-test root fixes) · #501→v1.58.9 (multi-pattern `-e`/`-f`) ·
#502→v1.58.10 (#49 MCP stdio byte-framing+DoS) · **#508→v1.58.11 releasing** (**H3/H4** checkpoint
arbitrary-read + disk-DoS — first codex-audit security fix live). Earlier: v1.58.0-v1.58.4 (daemon
Tier-1, native DoS, blast_radius+GPU-honesty, dual-help, ReDoS fail-closed).

---

## CODEX EXTERNAL AUDIT — HIGH WAVE COMPLETE (#123 [done])
All 5 HIGH verified still-real + fixed + adversarial-Opus-gated + PR'd (H1→#511, H2→#509, H3+H4→#508,
H5→#512, P1→#510). **The gate caught 3 real defects that would've shipped** (H5 POSIX no-op, H1
smart_case 5th silent-wrong, H2 defanged test).

## CEO DIRECTIVE 2026-07-10 (#99 [done]) — after the codex audit
**Do NOT build the SaaS.** Build tg features gotcontext.ai can wire into + focus on the tool
**WORKING** + optimally **PERFORMING**. Workstreams: (A) correctness=audit bugs; (B) perf=#94 + MED
perf; (C) wire-able=EvidenceReceipt (#124). gotcontext stays the CEO's product; we hand it clean
signed consumable tg outputs.

---

## CURRENT LIVE BACKLOG (reconciled 2026-07-13, task #162 — cross-checked against `git log` + live code, not just the ledger)

**Reconciled this pass (already shipped or resolved -> dropped from the active queue below; one-line receipts):**
- **#543** warm session-daemon default-ON flip + version-skew guard (#94) -> shipped `45000f4`, v1.65.0.
- **#544** route `--index` to the Rust capability validator (#138/#140) -> shipped `eaaaf0a`, v1.65.0.
- **#545** cap the plain-`--rank` corpus rechunk (#128d/MED-1) -> shipped `f43b7c0`, v1.65.1.
- **#2** index atomic+locked `.tg_index` write (audit A4) -> shipped `aa57254`/#546, v1.65.4.
- **#63** lang-graph crash/leak tail (Python `in_annotation` leak, Go unbounded recursion, registry-
  dispatch governance test) -> shipped `0fa47d6`/#548, v1.65.5.
- **#92** `tg classify --stdin`/`--text` literal mode -> shipped `7f11bc0`/#549, v1.65.6.
- **#130b** `sys.path.insert`/`append` import-awareness (imports/importers) -> shipped `abd58e2`/#568
  (re-tagged **#152** in later ledger entries, same fix), v1.70.0.
- **#124-P2** EvidenceReceipt signing (shipped as Ed25519, not HMAC as originally scoped — same intent:
  `tg evidence verify`/`keygen`/`pubkey`) -> shipped `5e046ed`/#553, v1.66.1.
- **#124-Gap1/Gap2** checkpoint undo persistence -> both confirmed live in code: `undo_argv`/
  `undo_command` are computed via `_undo_argv` (`checkpoint_store.py:264,871-872`) and returned on
  checkpoint create; the manifest `rollback` block is persisted in `evidence_receipt.py:651-666` and
  `apply_policy.py:988` payloads. Neither is in-memory-only anymore — no single PR to cite, closed
  incrementally across the checkpoint/evidence work.
- **#108** daemon Tier-2 (orient/agent capsules via the warm daemon) -> shipped `47174b4`/#555, v1.68.0.
- **#126** apply_policy fail-open edge (canonicalize exec parent) -> shipped `d8cf53c`/#556, v1.68.1.
- **#121** native `--count-matches` no-rg degrade -> shipped `87515df`/#557, v1.68.2.
- **#127** index-build `.gitignore` non-git-dir no-op -> shipped `2c07e0a`/#570, v1.70.1.
- **F3** GPU fail-closed capability matrix (`--gpu-device-ids` combined with ast/nlp/count/
  fixed-strings/context/line-regexp/word-regexp/LTL) -> confirmed shipped across a "round-4" audit
  pass, `pipeline.py:203-293` (each combo fails loud via `_raise_explicit_gpu_configuration_error`
  instead of silently dropping the flag). The `-o`/`--max-filesize`/`--color`/`--no-ignore-vcs` flags
  named in the original finding are output/filter concerns that never independently select a backend,
  so they were never a live instance of this gap.
- **Dead-code (partial):** `semantic_index.py` already carries the honesty docstring asked for
  (`semantic_index.py:1`, "kept SEPARATE from the Rust TGI v3 `.tg_index`"). NOT confirmed deleted:
  `sidecar.py::_classify_lines` (still defined, `sidecar.py:157`, a thin unused wrapper around
  `_classify_lines_with_metadata`) and `rust_core/src/backend_cpu.rs::replace_in_place`
  (`backend_cpu.rs:212`, still `pub fn`) — kept as a small LOW item below rather than marked shipped.
- **#171** GPU Phase-0 program (de-risking toward a possible Phase-1 `cuda-check` CI gate) -> SHIPPED:
  P0-1 WSL probe path-domain bridging + `cargo check --features cuda` anti-bit-rot CI gate (`7f8de84`/
  #594, v1.75.1) | P0-2/P0-3 doctor probe failure-taxonomy + honest device-id validation (`7350d77`/
  #595, v1.75.2) | P0-4/P0-5 calibrated remediation message + loud nvidia->cpu installer downgrade
  (`a4b3c05`/#596, v1.75.3). Phase 0 is now DONE; Phase 1 (flipping
  `TENSOR_GREP_RELEASE_NATIVE_ASSET_PROFILE`) is a reversible release-config decision, not a rebuild --
  see the reframed CEO-FACING GPU entry below.
- **#172** GPU Phase-0 gate-nits (doctor-probe precision + native error-kind taxonomy) -> shipped
  `3fd3af7`/#597, v1.75.4. 5 nits incl. a decisive one: `classify_gpu_route_failure` and its 3 tests
  were `#[cfg(feature = "cuda")]`-gated, so a default `cargo test` (no `--features cuda`) silently never
  ran them.
- **#173** this BACKLOG reconcile (docs-only, no release) -- CURRENT STATE/SHIPPING/SHIPPED refreshed to
  v1.75.4 + the CEO-FACING GPU section reframed for honesty (council must-fix MF-3, see below).

**Verify-flagged (not on the live task-store queue; also not confirmed shipped in this pass — flagged
for the next audit rather than re-opened as active work):**
- **#86** T7->T8 late-rerank (real-model latency receipt + golden-set ship/no-ship decision). T0-T6
  (foundation/ONNX encoder/`--semantic` wiring, `#471`-`#474`) shipped v1.51-v1.54; `#531` hardened the
  wall-clock deadline (audit A3, v1.63.2). No T7/T8-labeled commit found in `git log --oneline --all`;
  reads as shelved (`TG_LATE_RERANK` stays experimental/opt-in, `reranker.py:45`) rather than an open
  gap, but this pass could not confirm that either way.
- **#128c** session-daemon worker-semaphore (`TG_DAEMON_MAX_WORKERS`) — no matching symbol anywhere in
  `src/` (`session_daemon.py` has no semaphore/max-workers guard). Genuinely looks unbuilt; not on the
  live queue, so not re-added as active work, but it is the one item this pass could not verify as
  either shipped or intentionally dropped.

### Ready to build (no mandatory-gate blocker)
- **#58** promote `tg route-test` hidden->public (small feature follow-up).
- **#98** MCP tool consolidation (45->~10 task-shaped dispatch tools, non-breaking,
  `TG_MCP_TOOL_SURFACE=lean`) + staleness receipts (P2). Design previously recovered/verified
  (campaign #142). Note: `#554`/v1.67.1 shipped a much narrower precursor under the same tracking
  number (`tg_session_open` default `max_repo_files` 512->2000) — that is NOT this consolidation.
- **#141** native `AstBackend` vs the ast-grep wrapper — DSL divergence + `is_available` broadening
  (design-stage; needs a design pass before a TDD build).
- **#160** v1.71.3 dogfood Medium/Lower feature tail: `suggested_ignore`/orient-auto-deweight,
  complete-scan `suggested_scope`, dynamic-import string/getattr breadth, cold-doctor daemon-autostart
  hint — needs verify-against-code first (some sub-items may already be partially covered by shipped
  work; re-check before scoping a PR).

### LOW-severity follow-ups (non-blocking)
- **#115** symlink sweep — 3 unguarded `std::fs::write` sites (checkpoint metadata, checkpoint index,
  rollback-restore); the `write_bytes_refuse_symlink` helper already exists with one caller, mechanical
  swap to 4.
- **#125** H3+H4 gate follow-ups — checkpoint `except Exception`->`except BaseException`
  cleanup-on-abort + create-vs-undo symlink consistency. MCP-reachable (`tg_checkpoint_undo`).
- **#143** Opus-gate LOW follow-ups — `#543`'s race-test/symbol-timeout/`lru_cache` flip + `#140`'s
  `--` sentinel (non-blocking).
- **#155** `#152` Opus-gate LOW nits — dead reverse-tag block + an ordering comment.
- **Dead-code (partial, see reconciliation note above):** delete `sidecar.py::_classify_lines` (unused
  wrapper) + `rust_core/src/backend_cpu.rs::replace_in_place` if confirmed zero-caller; light Opus
  parity review for the Rust deletion (`cpu_backend` is a mandatory-gate surface).

### Blocked on a Linux/WSL box (env-blocked, not CEO-gated)
- **#89** WSL `/mnt/c` absolute-path resolution in the native backend.
- **#90** `tg scan` ast-grep Linux/WSL portability + doctor false-"available" exit-127. The
  doctor-honesty half already shipped (**#90b**/`fb3291b`, v1.70.2 — `tg doctor` no longer reports
  `available:true` for a non-runnable ast-grep shim); the Linux/WSL ast-grep portability piece itself
  is still open and unverifiable without a Linux/WSL box.
- **#109** cuda GPU implicit-walk ceiling.

### CEO-gated (full framing in CEO-FACING below)
- **#72** benchmark proof-point publish.
- **#131** GPU deep-dive audit + multi-week rebuild (conflicts with no-SaaS).

---

## CEO-FACING / strategic (the CEO's call — not auto-fired)
- **#72** benchmark proof-point publish (tokens-per-correct-answer; tg **7.5x fewer tokens than grep**
  on definition-lookup, oracle-validated). Reinforced by the dogfood + GPU "published accuracy gate"
  enterprise-gap below.
- **#77** `tg ledger` local agent context-sharing (thinktank-reviewed conditional narrow-yes; gated
  behind semantic-search shipping first).
- **GPU program -- REFRAMED 2026-07-14 (Phase-0 complete: #171 + #172; council must-fix MF-3 honesty
  gate baked into this reframe).** NVIDIA native assets are BUILT and locally correctness-proven on the
  dev box (device 0 `RTX 4070` `sm_89`, device 1 `RTX 5070` `sm_120`; see `docs/SESSION_HANDOFF.md` GPU
  dogfood notes and `docs/gpu_crossover.md`), gated OFF the public release by CI Actions var
  `TENSOR_GREP_RELEASE_NATIVE_ASSET_PROFILE` (default `native-frontdoor`, CPU-only; the opt-in flip is
  `native-frontdoor-gpu`, `.github/workflows/ci.yml:1121`). **So Phase 1 is a reversible release-config
  flag-flip decision, not the ~24wk/2-engineer rebuild this section previously described.**
  **CRITICAL HONESTY (do not violate `docs/CONTRACTS.md:80-82`):** flipping the var publishes ASSETS
  only -- it does NOT promote GPU. GPU auto-recommendation stays `false`; no speed crossover vs
  `rg`/`tg_cpu` is proven yet (`docs/gpu_crossover.md` still records "no crossover" for the measured
  workload classes); the reviewer-gated `public-gpu-proof.yml` speed-crossover gate is UNMET (manual
  `workflow_dispatch` only, requires a `self-hosted`/`gpu`/`tensor-grep-public-gpu-proof`-labeled runner,
  and its `environment: public-gpu-proof` lets maintainers require explicit approval before it runs --
  `docs/CI_PIPELINE.md`). Assets become downloadable; the CPU path remains the default and the
  recommended engine until a self-hosted GPU rig proves a crossover -- which it may not.
  **Phase 2** = attach the dev GPU box as that self-hosted runner to actually execute
  `public-gpu-proof.yml`'s speed-crossover proof. CEO-gated: needs the physical hardware attached. **Can
  still re-open the #99 "no-SaaS" wedge the CEO closed 2026-07-10 IF pursued as a funded buildout** --
  Phase 0's de-risking narrows the ask, it does not itself resolve that strategic fork. Campaign #142
  re-homes the old **#47** finding ("GPU public-proof", an NVIDIA-flavor native build) onto this same
  fork -- one CEO decision now covers both. Cite: `cluster-4-stale-reconcile.md` (#47). Phase-0 receipts:
  **#171**/**#172** (CURRENT LIVE BACKLOG above; releases in SHIPPED above). The earlier Phase-0
  honesty/correctness fix (**F3**, the GPU fail-closed capability matrix) also already shipped (see
  SHIPPED above).
- **Enterprise gaps** (dogfood-surfaced, design-scale): **multi-root workspace primitive** (orient/
  search/blast across sibling repos, no manual fan-out) · target-selection accuracy scoreboard
  (top-k/MRR) · cross-OS managed ast-grep · LSP proof-mode (availability ≠ navigation proof).
- **Next-language expansion** (Java/C#/C++/Ruby/PHP) — explicitly multi-week + CEO-gated (re-homed
  from **#62**; cite `cluster-4-stale-reconcile.md`). The Go Stage-1 pattern (registry + fail-closed
  grammar-missing + `resolution_gaps`, `3481742`/#420) is the proven template, so the marginal
  per-language cost is now much lower than when this roadmap was first scoped.
  `_provider_language_for_path` already maps java/c/cpp/csharp/php ids for the LSP-provider layer
  today, but the graph layer does not — the same drift class **#63**'s F22 governance test (shipped,
  `#548`/v1.65.5) now guards against.

## References
- Cross-session resume anchor (memory): `tensor-grep-drain-resume-2026-07-09.md` (live drain/audit/dogfood/GPU state).
- Full process rules: [AGENTS.md](https://github.com/oimiragieo/tensor-grep/blob/main/AGENTS.md).
