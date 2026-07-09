# tensor-grep — Project Backlog & PR Tracker

> **Canonical prioritized work list.** Kept in sync with the CLI task store (`TaskUpdate`) and
> GitHub (`gh pr list` is the source of truth for PRs). **Subagents:** read this for *what* to work
> and *how*. **CEO status** = summarize the SHIPPING + P0/P1 sections. Update this doc whenever a PR
> opens/merges or the queue changes.

**Process (the standing framework):** deep-dive → Fable audit (find + fix-idea, cite `file:line`) →
Exa recency + competitive research → plan (superpowers) → thinktank/Fable review the plan → **Sonnet
build, TDD** → verify in the REAL venv (`uv run --no-sync`; a worktree "tests pass" is a hypothesis) →
`ruff check` + `ruff format --preview` + `mypy` → codex/Fable review the PR → **PR → drain**
(one-merge-per-publish, push-race rule) → repeat until no issues. Isolate code agents with
`isolation:'worktree'`. Match model to task (haiku scan / sonnet build / opus+fable review-synthesis).
Always run the common-sense gate before pending a question to the CEO.

**Legend:** `P0` ship-blocking / #1 product gap · `P1` HIGH bug / moat · `P2` MED / coherence ·
`P3` LOW / refactor / feature. Status: `[shipping]` open PR · `[ready]` designed, buildable ·
`[research]` needs Exa/Fable/profile first · `[blocked]` gated · `[done]`.

---

## SHIPPING — open PRs (drain one-per-publish)
| PR | Fix | Files | Verified |
|----|-----|-------|----------|
| #475 | dogfood #84 — scoped-agent + edit-plan validation-plan parity (README boundary-trap + python discovery) + budget-corroborated 0.75 confidence channel | repo_map, agent_capsule | 158t real-venv + real-binary dogfood on gotcontext-saddle (validation_plan empty->5, confidence 0.55->0.9, ask_user required->False) |

**Push-race (2026-07-09):** v1.55.0 mid-publish (from #474). #475 waits for the `chore(release): v1.55.0` stamp + PyPI before it can drain.

**Deep-dive #81 audit drain (v1.51.x, 2026-07-08/09) — 100% MERGED + LIVE:** #461 ast CWE-88 · #462 backend fail-closed (#10/#14/#79) · #463 from-dot-import recall (#3/#4/#11) · #460 scoped file-dep primitives `tg imports`/`importers` (#74 moat) · #455 cpu ReDoS-gate bypass (#6/#16) · #464 mcp read-path confine (#1/#2/#12) · #457 index-lock ownership token (#14) · #465 rust exit-2 + pcre2 fail-closed (#7/#9) · #466 daemon token-ACL (#13) · #467 docs/architecture rewrite · #468 release-stamp anchor + 2 masked governance gates · #469 policy-anchor confinement (#76) · #470 BACKLOG refresh. **All 14 findings shipped; every security PR passed the adversarial Opus gate.**

**Late-rerank feature (task #86, the #1 ColGrep competitive response) — T0-T6 MERGED (ships default-OFF behind `TG_LATE_RERANK`):** #471 (T0-T2 extra + maxsim + LateReranker) · #472 (T3-T4 ONNX encoder + checksum-pinned LateOn-Code-edge fetch, Opus-gate PASS) · #473 (T5-T6 rerank_hybrid seam + fail-closed) · #474 (#87 fetch total-deadline nit). **T7-T10 remaining** (see backlog).

---

## CURRENT LIVE BACKLOG (2026-07-09, updated) — action after #475 drains

### 1.54.0 WSL2 workspace dogfood (2026-07-09) — new P0/P1 signal
- `[P0]` **#52 --deadline is not a hard wall-clock bound** (CONFIRMED 3x now, #1 recurring): `tg callers ... --deadline 15` = 354s (23x); `--deadline 5` = 104s; `inventory --deadline 30` = 76s. JSON flags `result_incomplete` + exit 2 but the WALL-CLOCK isn't bounded -> agents hang 6 min. Root: "each stage bounded != pipeline bounded" (#61 = the 2 remaining unbounded caller-scan loops). Needs a DESIGN pass (thread a monotonic deadline INSIDE the hot loops). Sibling to #88.
- `[P1]` **#89 WSL /mnt/c/ absolute-path resolution** fails in native backend (path_not_found on a valid bind-mount; relative path from cwd works). Cross-platform path normalization — WSL/Windows/Docker must resolve the same path. Deployment blocker for Linux agents.
- `[P1]` **#90 tg scan ast-grep portability on Linux/WSL** + doctor false-"available": scan exit 127 (execs the Windows npm shim from Linux); doctor reports "available" (checks presence, not executability). Fix doctor to probe-run + resolve a platform-native/managed ast-grep.
- `[P2]` **#91 `tg search --type <lang> --json` reports `total_matches:0`** while plain search finds hits (--type filter vs JSON aggregate disagree — a false 0 is worse than a slow answer).
- `[P3]` **#92 tg classify --stdin/--text** literal mode (log-streaming agents; keep file-path default, bounded+fail-closed read).
- `[strategic/CEO-gated]` enterprise gaps the report surfaced (mostly already tracked): workspace-scale indexing / raise the 512 cap (#57 P0), agent target-selection accuracy gate + published metrics (#72-adjacent), observability/run-receipt export, multi-root `tg orient --roots`, semantic-search graduation (= rerank #86). GPU promotion (#project-gpu, paused).

- `[P1, SHIPPING #475]` **Agent validation-plan parity** (dogfood #84) — DONE, draining. (moved to SHIPPING table above.) NOTE: the 1.54.0 report also flags agent TARGET-SELECTION accuracy (picked a wrong-language .js @0.65 for an "authentication flow" query) — distinct from #84's validation-plan fix; folds into the #72 accuracy-gate strategic item.
- `[P1, ready]` **Late-rerank T7-T10** (task #86, fresh-session — heavy + ship-gated): T7 real-model latency receipt (cold+warm, `scripts/dogfood/`), **T8 golden-set ship/no-ship gate** (`eval_late_rerank_quality.py` + ~40 vocab-mismatch queries, 4-arm BM25/dense/RRF/RRF+MaxSim, nDCG@5 >=+0.03 & no recall regression & p50<=2000ms), T9 `--rerank` registration (all 8 sites, ONLY after T8 evidence), T10 docs + NOTICE (Apache-2.0). Design: `docs/plans/design-tensor-grep-late-rerank-2026-07-09.md`. **T8 is the ship decision** — if it fails its own gate, keep env-gated experimental / honest no-ship in PAPER.md.
- `[P2, ready]` **#88 — `tg search --glob` alone times out** on large/harness repos (1.54.0 dogfood): a bare `--glob` (no PATH positional) triggers an unbounded whole-repo walk -> exit-124. Fix = auto-scope/bound the glob walk. Needs verify-plan-against-code on the search-routing seam FIRST (the 8-site / 2-front-door misroute hazard). **DESIGN dispatched this tick (Sonnet, background).**
- `[P2, ready]` **imports/importers dynamic-import awareness** (dogfood #84): `tg imports` misses `sys.path.insert` + sibling `from X import`; `tg importers` returns 0. Known #74 limit — add awareness OR emit a `resolution_gaps` honesty signal.
- `[P2, ready]` **orient suggested_scope/suggested_ignore auto-hints** (dogfood #84): root orient without `--ignore` ranks `seo/scripts/*` central; auto-hint so agents don't hand-apply `--ignore`.
- `[P3, ready]` unscoped-search immediate-refuse (detect missing PATH/--glob before the scan, not exit-124 after); blast-radius proceed/stop rubric when confidence=moderate + parser_backed>0 + callers<5.
- `[P1, research/CEO-gated]` **#72 benchmark reconcile** — re-run bench (tokens-per-correct) now that #74 imports/importers shipped; needs external Sverklo harness + bidirectional-oracle (fresh context). CEO-gated: public benchmark publish.
- `[blocked/CEO-gated]` **tg-ledger / A2A local coordination** (#77) — document-now-build-later; go/no-go on the 2-week demand receipt from #456's instrumentation.
- `[P3]` flaky-test hardening: #83 `test_public_help_falls_back...` (windows, needs timeout-mechanism investigation, not blind-widen), #64 index_lock_concurrency, #37 test-ordering pollution.
- `[re-verify]` #78 ReDoS simple-path residual (Rust-less install falls to Python re on unflagged patterns), #76-pt2 islice giant-line bound (batch into one gated mcp/cpu follow-up). **#76 read-path exfil = DONE (shipped #464/#469, verified on main).**
- `[stale-WIP, revive-or-retire]` `tensor-grep-deweight` worktree (`feat/auto-deweight-vendored-trees`, based on v1.40.3): auto-deweight vendored/harness trees in orient centrality; needs rebase onto current + Fable's file-subgraph exclusion. Left in place (holds real WIP) — decide revive vs retire next campaign.

> **NOTE (2026-07-09):** the P0/P1 sections below are from the 2026-07-07 v1.45.x campaign — MANY releases have shipped since (now v1.51.9). Re-verify each against current code before actioning; several (PERF parse-cache #432, the repo_map cluster) likely shipped in the v1.46-1.51 line. `gh pr list --state merged` + `git log` are authoritative.

---

## P0 — the #1 product gap
- `[SHIPPED — PR #432, draining]` **PERF — repo_map per-(path,mtime) parse-product cache.** One tree-sitter
  parse per (path,mtime) shared across symbol/ref/caller extractors, golden-parity-locked (172 oracle tests
  byte-identical). Measured ~-25% cold render / -45% parse on this repo, larger on TS. **Unblocks raising
  `CALLER_SCAN_FILE_CEILING` (512) — the next repo_map item after #432 merges.** (tasks #52/#57/#61, #65-C1)

## P1 — HIGH bugs / moat (Fable-designed)
**The gated repo_map cluster — all touch `repo_map.py`, so they serialize behind #432 (PERF) merging; build in this order:**
- `[gated on #432, design done]` **Caller-cap raise** — raise `CALLER_SCAN_FILE_CEILING` (512), now safe because
  the parse-cache (#432) makes the re-parse cheap. The dogfood #65-C1 "callers unusable on TypeScript" fix. Build FIRST.
- `[gated on #432, design done — 1D SHIPPED #433]` **1A — evidence-derived seed confidence.** The edit-plan seed
  never emits `confidence.overall`, so the capsule defaults to a fabricated 0.9; emit a real `overall`
  (`min(file,symbol)` = route-test's derivation) + a 0.55 capsule fallback. Spec: `scratchpad/build_spec_1D_pr1.md` (§1A). Build after the cap raise.
- `[gated on #432, design done]` **C-EDGE-1 — caller precision (competitive, dogfood-confirmed).** Name-based
  `tg callers` over-attributes a same-named LOCAL function's calls to the queried symbol (codegraph #67 / mycelium
  Bug 3 class). Fix = 3-tier resolution confidence (import-resolved 0.95 / name-only 0.75 / shadowed-local 0.5) reusing
  `import_graph_consumer_files` + shadow-def set — **recall-preserving** (drops nothing; tg's recall beats resolved-edge
  rivals). Spec: `scratchpad/build_spec_c_edge_1.md`. Build LAST (largest diff). Memory: `tensor-grep-competitive-caller-edgecases-2026-07-07`.
- `[gated — repo_map/agent_capsule]` **T1/T3 — caller-scan honesty + validation_plan population** (dogfood #65).
- `[SHIPPED]` R1 FFI kwargs (#430) · M9 routing-merge (#434) · L7 rg-timeout partial (#435).
- `[correctness MED tail]` M1 unify 3 blast-radius answers, M3 context/inventory/orient exit-2, M5 `--deadline` sibling
  loops (#61), M12 uplift eligibility (repo_map/agent_capsule — gated). **M13 cybert alignment: REJECTED** — a mis-diagnosis;
  `classify()` drop-filtering is the intended, tested contract (no caller zips at nonzero threshold). Do NOT re-attempt.

## P2 — coherence / MED
- `[ready]` **W2/W3/W4** (E2E audit): blast-radius-plan `files`-vs-`affected_files` contradiction (zero-risk);
  session doc-edit absorb (don't invalidate a code session on CLAUDE.md edits); repo-level scope config (`.tensor-grep.toml`) + `--ignore` parity on edit-plan.
- `[research]` **M2 — cold-render latency**: profile to confirm it's the 2000-cap cost, not a regression.
- `[ready]` **R2/R3 — finish the language registry** so the next language (Java) is a pure `lang_*.py` + one
  `register_language()` (no core edits). R6 — Go validation/test-discovery gaps.

## P3 — refactors / LOW / features
- `[ready]` **R4/R5 — safe repo_map split** (strangler-fig behind a facade): extract `validation_commands.py`,
  `trust.py`; `lang_common.py` leaf module to de-dup the 3× byte-identical helpers.
- `[ready]` **correctness LOW tail** L1-L12 + **audit LOWs** (#63: F15/F19/F22/F23/F26).
- `[ready]` **features** (dogfood #65/#35): `tg sweep`, `tg spine`, `tg doc-gap`, progress-NDJSON heartbeat,
  `docs-coverage --suggest-doc`, unified blast-radius mode, agent auto-scale tokens, suggested_scope/ignore.
- `[blocked/CEO-gated]` **strategic** (#62): languages beyond Go (multi-week); semantic-search promotion (evidence-gated).
- Older live tasks still relevant: #17 SafeBackendMixin CI gate, #18 `tg registration-check` CLI, #43 Q5 ReDoS ProcessPool, #45 LSP graph routing, #58 promote `tg route-test`.

---

## SHIPPED — this campaign (v1.45.x, 2026-07-07)
Path A T1 (typed ref_kind) · Stage 0 (lang registry) · Go language · Path B (local hybrid semantic search) ·
Cluster A (MCP unbounded scans) · Cluster B (exit-2 daemon) · F1–F8 dogfood fixes · crossbeam CVE · flaky-test hardening.
Plus the v1.45.x correctness+trust blitz — **MERGED:** #424 H1 · #425 T2 · #426 backends · #427 reliability ·
#428 MCP · #429 backlog-tracker · #430 R1 FFI. **DRAINING (#431–#436):** F15/F23 · PERF parse-cache · 1D truncation ·
M9 mixed-routing · L7 rg-timeout · SESSION_HANDOFF. All verified in the real venv; M13 correctly rejected as a mis-diagnosis.

## References
- Fable fix-queue detail (findings + worktree branches): `scratchpad/fable_blitz_fixqueue_2026-07-07.md` (temp — non-durable).
- Cross-session resume anchor (memory): `tensor-grep-fable-blitz-active-work-2026-07-07.md`.
- Full process rules: [AGENTS.md](https://github.com/oimiragieo/tensor-grep/blob/main/AGENTS.md). Prose narrative log: [SESSION_HANDOFF.md](SESSION_HANDOFF.md).
