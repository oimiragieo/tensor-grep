# tensor-grep — Project Backlog & PR Tracker

> **Canonical prioritized work list.** Kept in sync with the CLI task store (`TaskUpdate`) and
> GitHub (`gh pr list` is the source of truth for PRs). **CEO status** = summarize SHIPPING + P0/P1.
> Update whenever a PR opens/merges or the queue changes. Task-store IDs (`#NNN`) cross-referenced.
> Last refreshed 2026-07-13 (v1.68.1 CEO WSL-dogfood drain COMPLETE: 3 fixes -> v1.69.0/.1/.2 dogfood-verified on the published wheel; campaign #142 before — 4 PRs drained: v1.67.1/v1.68.0/v1.68.1/v1.68.2, zero broken releases).

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

## ⭐ CURRENT STATE (2026-07-13) — authoritative; every section BELOW is HISTORICAL until the next full refresh

- **Live PyPI: v1.69.3.** **Proactive dogfood -> #151 shipped (2026-07-13):** running the published wheel on 3 real external repos (flask/fastapi/requests) surfaced one genuine correctness gap -- `tg importers FILE [ROOT]` (ROOT defaults to CWD) returned an empty `importer_count` with NO signal when FILE is OUTSIDE ROOT (indistinguishable from "genuinely unimported"; silent-wrong for an agent shelling `tg importers /other/repo/file.py` from a different CWD). Fix (**#566** `00e4e99`, Sonnet-TDD -> **Opus gate SHIP** 7-axis adversarial, additive-only, MCP output-shape safe): a lexical containment check in `build_file_importers_from_map` stamps `file_outside_root` + an honest `scan_remediation`. **Dogfood-verified on the published v1.69.3 wheel:** outside-root -> `file_outside_root:true` + remediation; in-root -> `false` + correct `importer_count`. fastapi/requests batteries were clean (no new defects).
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
- **Strategic (CEO steer 2026-07-11, still in force):** tool WORKS (moat = **7.5x fewer tokens than grep on definition-lookup**, benchmark-proven); finish the moat (latency — warm-daemon flip #543 is the next lever, gate-pending) + shift to gotcontext wiring vs draining the self-refilling tail; no-SaaS (gotcontext.ai is the SaaS shell, not tg).

---

## SHIPPING — open PRs (drain one-per-publish) — task #117

**0 open PRs — QUEUE CLEAR.** Post-drain proactive dogfood on 3 real repos shipped **#566** (`tg importers` outside-root honest signal -> v1.69.3, Opus-gated SHIP, dogfood-verified live). Before it, the v1.68.1 CEO WSL-dogfood 3-PR drain (#562/#563/#564 -> v1.69.0/.1/.2) fully drained one-per-publish, zero broken releases; before it, campaign #142's 4-PR queue (#554-557) drained to v1.67.1/v1.68.0/v1.68.1/v1.68.2 — zero broken releases. Post-campaign artifacts also merged: **#559** backlog-reconcile + **#560** AGENTS.md ruff-scope docs-hardening (both docs-only, no release).

## SHIPPED — live on PyPI up to **v1.69.3**

**v1.59–v1.66.1 window (merged, on PyPI):** #541 index capability-validator · #542 AstBackend tree-sitter query-API repair · #543 warm-daemon default-ON flip (#94 latency lever) · #544 `--index` front-door routing · #545 `--rank` chunk cap · #546 atomic + cross-process-locked index write · #547 backlog reconcile · #548 iterative Go AST walk (no RecursionError) · #549 `tg classify --stdin/--text` · #550 ast-grep fail-closed · #551 wedged-python help-probe deflake · #552 launcher import-defer perf · #553 Ed25519 evidence-signing (v1.67.0) · #558 release-blocker hotfix · #554-557 campaign-100 (v1.67.1→v1.68.2) · #559 backlog-reconcile (docs) · #560 AGENTS.md whole-repo ruff-scope hardening (docs) · #561 backlog-refresh v1.68.1->v1.68.2 (docs) · **#562 codemap --ignore/--deadline (v1.69.0)** · **#563 nested-import recall (v1.69.1)** · **#564 suggested_scope-on-tie + normpath ..-confinement (v1.69.2)** · **#566 importers outside-root honest signal (v1.69.3, dogfood-found on flask)**. Older detail below is HISTORICAL.

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

## CURRENT LIVE BACKLOG (campaign #142 reconciled 2026-07-12 — the real residual queue after 4 audit passes)

### IN-FLIGHT builds (actively draining/underway)
- **#543** warm session-daemon default-ON flip (#94) + version-skew guard. `feat/warm-daemon-default`,
  draft. Gate: session_daemon (Opus) — **PENDING**, not yet approved despite design-level clearance.
  `[shipping]`
- **#544** route `--index` to the Rust capability validator instead of leaking through Python-passthrough
  pre-clap-parse (#138/#140). `fix/passthrough-index-frontdoor`, draft. Gate: routing (Opus) — pending,
  `--`-sentinel caveat flagged for the reviewer. `[shipping]`
- **#545** cap the plain-`--rank` corpus rechunk at the `reranker.py` chokepoint (#128d/MED-1) —
  `TG_RANK_CORPUS_CHUNK_CAP`, never drops matches, sets `rank_fallback_reason` on trip.
  `fix/rank-total-chunk-cap`, draft. Gate: none (pure `core/`, self-declared gate-free). `[shipping]`
- **#2** index atomic+locked write — `std::fs::write` at `index.rs:856` has zero fsync/rename/locking
  (audit A4). Building now (`fix/atomic-locked-index*` worktrees), not yet pushed. Gate: index_lock
  (Opus). `[ready]`

### GATE-FREE ready (no mandatory-gate surface; buildable now, no in-flight collision)
- **#63** lang-graph crash/leak tail — ONE PR for 3 Fable-audit LOWs: Python `Annotated[int,
  validate(X)]` mislabels runtime-value call-args as `"type"` (`in_annotation` leaks into `ast.Call`
  args, `repo_map.py:4383-4390`) · registry↔language-mapping governance test missing
  (`_target_language_for_path` vs `_provider_language_for_path` drift undetected — "the
  MOST-FORGOTTEN seam") · Go `_walk` unbounded recursion + `splitlines()`-vs-tree-sitter row mismatch
  (`lang_go.py`, a crash not a degrade). Cite: `cluster-4-stale-reconcile.md` (#63). `[ready]`
- **#92** `tg classify --stdin`/`--text` literal mode — the sidecar payload protocol already carries
  literal content end-to-end (`sidecar.py:211,243-251`); only the two front doors (clap + Typer) can't
  express it yet. Serialize on `main.rs`. Cite: `cluster-3-p2-followups.md` (#92). `[ready]`
- **#130b** `sys.path.insert` import-awareness (imports/importers, beyond #504's dynamic-call recall) —
  a benchmarked P4 moat-recall gap, not polish; cluster-2 recommends un-holding it from the CEO churn
  steer. Cite: `cluster-2-p1-moat.md` (#130). `[ready]`
- **#124-Gap1/Gap2** persist checkpoint `undo_argv`/`undo_command` (in-memory-only today) + persist the
  manifest `rollback` field (write-ordering is already correct; additive field only) — both
  MCP-adjacent (`tg_checkpoint_*`); light Opus review recommended though neither is on the strict
  mandatory-gate list. Cite: `cluster-2-p1-moat.md` (#124). `[ready]`
- **#86 T7→T8** late-rerank real-model integration + latency receipt (T7), then **T8 = the golden-set
  ship/no-ship decision** (4-arm nDCG/recall/p50 vs RRF baseline; a fail means stay experimental or
  retire to PAPER.md — a quality gate, not a security one). T9 (`--rerank` registration) / T10 (NOTICE
  attribution) follow ONLY if T8 passes. Fully parallel-safe, new files only.
  Cite: `cluster-2-p1-moat.md` (#86). `[ready]`
- **Dead-code:** `sidecar.py _classify_lines` (trivial, zero callers, delete) · `backend_cpu.rs
  replace_in_place` (confirmed-dead, but `cpu_backend` IS a mandatory-gate surface → light Opus parity
  review even for a pure deletion) · **KEEP** `semantic_index.py` (unwired BM25 persistence — stamp an
  honesty docstring only; it's the designed substrate for #108, deleting now is negative-value churn).
  Cite: `cluster-3-p2-followups.md` DEAD-CODE 1-3. `[ready]`

### OPUS-GATED (mandatory or recommended adversarial review before merge)
- **#124-P2** EvidenceReceipt HMAC signing + `tg evidence verify` — mirror the shipped audit-manifest
  digest-chain+signing system 1:1, do not invent a new scheme. Discretionary Opus crypto pass.
  Cite: `cluster-2-p1-moat.md` (#124). `[ready]`
- **#98** MCP consolidation (47→~10 task-shaped dispatch tools, non-breaking, `TG_MCP_TOOL_SURFACE=lean`)
  — design fully recovered + re-verified against live code; **#124-P3** (`tg_evidence` MCP tool) rides
  immediately after on the same consolidated surface. Gate: mcp (mandatory). Cite:
  `cluster-2-p1-moat.md` (#98). `[ready]`
- **#108** daemon Tier-2 — serve orient/agent capsules via the warm daemon (PR-A orient, then PR-B
  agent). Blocked-by **#543** landing (same surface + cache region + gate). Gate: session_daemon
  (mandatory). Cite: `cluster-2-p1-moat.md` (#108). `[blocked]` (after #543)
- **#125** checkpoint `except Exception`→`except BaseException` cleanup-on-abort + create-vs-undo
  symlink consistency. MCP-reachable (`tg_checkpoint_undo`). Gate: mcp-adjacent (mandatory). Cite:
  `cluster-3-p2-followups.md` (#125). `[ready]`
- **#126** apply_policy fail-open edge — canonicalize the executable's PARENT (realpath) so the lexical
  guard-check and the spawn use the same form; closes the 8.3-shortname/junction/`\\?\` divergence on
  the RCE guard. Highest-value item in cluster-3. Gate: apply_policy (mandatory RE-gate). Cite:
  `cluster-3-p2-followups.md` (#126). `[ready]`
- **#121** native `--count-matches` no-rg degrade — the raw hard-error half already shipped (structured
  exit-2); the remaining gap is native-engine parity, gated on verifying occurrence- vs line-granularity
  in the match vector BEFORE choosing degrade-vs-refuse-reword. Gate: backends (mandatory,
  routing/selection change). Cite: `cluster-3-p2-followups.md` (#121). `[ready]`
- **#115** symlink-sweep: 3 unguarded `std::fs::write` sites (checkpoint metadata, checkpoint index,
  rollback-restore) — the `write_bytes_refuse_symlink` helper already exists with exactly one caller;
  mechanical swap to 4. Gate: light Opus diff-review (security-adjacent, not on the strict mandatory
  list). Cite: `cluster-3-p2-followups.md` (#115). `[ready]`
- **F3** GPU fail-closed capability matrix — `--gpu-device-ids` combined with `-r`/`-o`/`--max-filesize`/
  `--color`/`--no-ignore-vcs` silently drops the flag and exits 0 (Backend Fail-Closed Contract class,
  same class as the `--pcre2` precedent). **Gate-ambiguity flagged by cluster-1, read as resolved
  here:** this is Phase-0 honesty (non-CEO-gated); only the NVIDIA-packaging-matrix-removal half of
  the GPU-program line (CEO-FACING below) stays CEO-gated — reconfirm with the CEO if that reading is
  wrong before merge. Gate: backends (mandatory). Cite: `cluster-1-p0-correctness.md` (#131 F3).
  `[ready]`
- **#128c** session-daemon worker-semaphore (`TG_DAEMON_MAX_WORKERS`, gates only the expensive dispatch
  branch — `ping`/`stop` stay ungated so shutdown can't starve). Blocked-by **#543** merging first (same
  handler file); priority rises once #543 lands (becomes the default hot path). Gate: session_daemon
  (mandatory). Cite: `cluster-1-p0-correctness.md` (#128c). `[blocked]` (after #543)
- **#127** index-build `.gitignore` silently no-ops in non-git dirs — mirror the sibling `add_ignore`
  trio (`main.rs`/`native_search.rs`); do NOT use `.require_git(false)` (the original task's own
  suggested fix is backwards — it would create the opposite divergence on nested `.gitignore`s). Gate:
  none mandatory (correctness, not index_lock) but same-file blocked-by **#2** (atomic+locked index
  write) — build immediately after it merges. Cite: `cluster-3-p2-followups.md` (#127). `[blocked]`
  (after #2)

### FLIP follow-ups
- **#143** (LOW) — daemon-default-flip follow-up; scope not detailed in any of campaign #142's 4 cluster
  audits, surfaces once #543 ships. `[blocked]` (after #543)

### Blocked on a Linux box (unaudited by campaign #142 — carried forward unchanged)
- **#89** WSL `/mnt/c` path resolution · **#90** ast-grep Linux/WSL + doctor honesty · **#109** cuda
  implicit-walk ceiling.

---

## CEO-FACING / strategic (the CEO's call — not auto-fired)
- **#72** benchmark proof-point publish (tokens-per-correct-answer; tg **7.5x fewer tokens than grep**
  on definition-lookup, oracle-validated). Reinforced by the dogfood + GPU "published accuracy gate"
  enterprise-gap below.
- **#77** `tg ledger` local agent context-sharing (thinktank-reviewed conditional narrow-yes; gated
  behind semantic-search shipping first).
- **GPU program** — the GPU deep-dive recommends a ~24wk/2-engineer rebuild (Phase 0 truth → Phase 1
  resident+matcher experiments = the FUNDING GATE → sharded streaming → fixed-string matcher →
  packaging → resident service → bounded regex) toward **a local/BYOC high-throughput scanning plane
  (secret/PII/license/policy rule-packs)**. ⚠ **Directly re-opens the #99 "no-SaaS" wedge the CEO
  closed 2026-07-10** — a genuine strategic fork. Campaign #142 re-homes the old **#47** finding
  ("GPU public-proof", an NVIDIA-flavor native build) onto this same fork — one CEO decision now
  covers both. Cite: `cluster-4-stale-reconcile.md` (#47). Only the Phase-0 honesty/correctness fixes (**F3**,
  CURRENT LIVE BACKLOG above) are non-gated.
- **Enterprise gaps** (dogfood-surfaced, design-scale): **multi-root workspace primitive** (orient/
  search/blast across sibling repos, no manual fan-out) · target-selection accuracy scoreboard
  (top-k/MRR) · cross-OS managed ast-grep · LSP proof-mode (availability ≠ navigation proof).
- **Next-language expansion** (Java/C#/C++/Ruby/PHP) — explicitly multi-week + CEO-gated (re-homed
  from **#62**; cite `cluster-4-stale-reconcile.md`). The Go Stage-1 pattern (registry + fail-closed
  grammar-missing + `resolution_gaps`, `3481742`/#420) is the proven template, so the marginal
  per-language cost is now much lower than when this roadmap was first scoped.
  `_provider_language_for_path` already maps java/c/cpp/csharp/php ids for the LSP-provider layer
  today, but the graph layer does not — the same drift class **#63**'s new F22 governance test
  (CURRENT LIVE BACKLOG above) now guards against.

## References
- Cross-session resume anchor (memory): `tensor-grep-drain-resume-2026-07-09.md` (live drain/audit/dogfood/GPU state).
- Full process rules: [AGENTS.md](https://github.com/oimiragieo/tensor-grep/blob/main/AGENTS.md).
