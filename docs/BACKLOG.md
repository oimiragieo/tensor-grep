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
| #469 | audit #76 — anchor rewrite policy confinement to the target's parent dir for single-file targets (Opus-gate PASSED) | mcp_server | 18t real-venv |

**Deep-dive #81 audit drain (v1.51.x, 2026-07-08/09) — MERGED:** #461 ast CWE-88 · #462 backend fail-closed contract (#10/#14/#79) · #463 repo_map from-dot-import recall (#3/#4/#11) · #460 scoped file-dep primitives `tg imports`/`importers` (#74 moat) · #455 cpu ReDoS-gate bypass (#6/#16) · #464 mcp read-path confine (#1/#2/#12) · #457 index-lock ownership token (#14) · #465 rust exit-2 + pcre2 fail-closed (#7/#9) · #466 daemon token-ACL (#13) · #467 docs/architecture rewrite vs real code · #468 release-stamp anchor + de-anachronize 5 docs + fix 2 masked governance gates. **All 14 deep-dive findings (#81) shipped; every security PR passed the mandatory adversarial Opus gate; #468 needed an 11-conflict merge resolution (verified 104 governance tests + real validate_docs_claims).** Only #469 (last) draining.

---

## CURRENT LIVE BACKLOG (2026-07-09, post-#81-drain) — action after #469 drains
- `[P1, ready]` **Agent validation-plan parity** (dogfood #84, gotcontext-saddle 2026-07-09): scoped `tg agent` resolves the target @ 0.9 but ships `validation_plan: []` + `ask_user_before_editing: required` (root agent DOES detect the nearest `tests/` neighbor — give scoped agent + edit-plan the same). + root-agent confidence downgrades over-fire (0.55 despite a good target + populated plan). Feature/tuning — design + TDD.
- `[P2, ready]` **imports/importers dynamic-import awareness** (dogfood #84): `tg imports` misses `sys.path.insert` + sibling `from X import` (hook-repo idiom, shows only stdlib); `tg importers` returns 0. Known #74 limitation (skill P7) — add awareness OR emit a `resolution_gaps` honesty signal on the envelope.
- `[P2, ready]` **orient suggested_scope/suggested_ignore auto-hints** (dogfood #84): root orient without `--ignore` ranks `seo/scripts/*` central; auto-hint so agents don't manually apply `--ignore` on harness repos.
- `[P3, ready]` unscoped-search immediate-refuse (detect missing PATH/--glob before the scan, not exit-124 after); blast-radius proceed/stop rubric when confidence=moderate + parser_backed>0 + callers<5.
- `[P1, research/CEO-gated]` **#72 benchmark reconcile** — re-run bench (tokens-per-correct) now that #74 imports/importers shipped, to reconcile docs/benchmarks.md P1 numbers; needs external Sverklo harness + bidirectional-oracle setup (fresh context). CEO-gated: public benchmark publish.
- `[blocked/CEO-gated]` **tg-ledger / A2A local coordination** (#77) — document-now-build-later; go/no-go on the 2-week demand receipt from #456's instrumentation.
- `[P3]` flaky-test hardening: #83 `test_public_help_falls_back...` (windows, needs timeout-mechanism investigation, not blind-widen), #64 index_lock_concurrency, #37 test-ordering pollution.
- `[re-verify]` #78 ReDoS simple-path residual (Rust-less install still falls to Python re on unflagged patterns), #76-pt2 islice giant-line bound (batch into one gated mcp/cpu follow-up).

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
