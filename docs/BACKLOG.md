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
| #431 | F15/F23 — reranker RRF-tie docstring + Go `go.work use (` header/comment parse | reranker/lang_go | verified |
| #432 | PERF — parse-product cache (one parse per path,mtime); ~-25% cold / -45% parse, byte-identical | repo_map | 172t golden |
| #433 | 1D — `tg agent` honors exit-2-on-scan-truncation + a truncated scan disqualifies the T2 uplift | agent_capsule/main | 188+116t |
| #434 | M9 — `merge_runtime_routing` surfaces mixed-backend routing (was last-write-wins) | result | 52t |
| #435 | L7 — `rg --count`/`-l` recover partial results on timeout (was hard-crash) | ripgrep_backend | 7+202t |
| #436 | docs — SESSION_HANDOFF v1.45.x milestone + the competitive/caller-precision finding | docs | mkdocs |

**Merged this drain (v1.45.x):** #424 H1 · #425 T2 · #426 backends · #427 reliability · #428 MCP · #429 backlog-tracker · #430 R1 FFI.

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
