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
| #424 | H1 — `callers --provider lsp` unions native+LSP (was dropping native callers) | repo_map | 62t |
| #425 | T2 — agent confidence graph-corroborated, not render-token-budget | agent_capsule | 83t |
| #426 | H5/H6 — StringZilla `--invert-match`/`--max-count` (+1 line-model fix) | backends | 68t |
| #427 | H8/H9/M6/M8 — phantom-rollback, index-lock reclaim, fsync, retention-order | apply_policy/_index_lock/stores | 70t |
| #428 | H3/H4/M10/M11 — MCP walk-hardening (port #400) + `tg_session_context` budget | mcp_server | 175t |

---

## P0 — the #1 product gap
- `[research]` **PERF-1/2/3 — repo_map per-(path,mtime) parse-product cache.** Cold `context-render`
  100s→~20s, `callers` 62s→<10s, and unlocks raising `CALLER_SCAN_FILE_CEILING` (512). Add alias-aware
  `symbol-in-source` early-exit + fix the no-op JS/TS gate (`_file_may_import_symbol_definition` returns
  True unconditionally, repo_map.py:1196). **PROFILE first** (`tg … --profile`). Fable-designed. (tasks #52/#57/#61, #65-C1)

## P1 — HIGH bugs / moat (Fable-designed)
- `[ready]` **1A/1D — `tg agent` truncation hole.** The flagship command drops `scan_limit`/`partial` +
  never gates exit-2 (a capped scan = confident capsule @ exit 0 — H2 double-confirmed by 2 audits); and
  the seed never emits `confidence.overall` so the capsule defaults to a fabricated 0.9. agent_capsule+main. Do after #425 merges.
- `[ready]` **R1 — FFI kwargs finish.** Partial in worktree `worktree-agent-a89b405…` (commit a3e2974):
  kwargs-by-keyword done; NEEDS the mock-test rewrite (positional→keyword) + a real-extension bidirectional
  test + `maturin develop` to verify vs the REAL `.pyd`. Delicate FFI.
- `[ready]` **T1/T3 — caller-scan honesty + validation_plan population** (dogfood #65). Top-level
  `caller_scan_truncated` on refs/callers + capsule trust downgrade; populate validation_plan from
  blast-radius tests on the scoped path. repo_map+agent_capsule. After #424.
- `[ready]` **correctness MED tail** (Fable audit): M1 unify 3 blast-radius answers, M3 context/inventory/orient
  exit-2, M5 `--deadline` sibling loops (task #61), M9 routing-merge, M12 uplift eligibility, M13 cybert alignment.

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
Plus in-flight: the 5 PRs above (H1/T2/backends/reliability/MCP).

## References
- Fable fix-queue detail (findings + worktree branches): `scratchpad/fable_blitz_fixqueue_2026-07-07.md` (temp — non-durable).
- Cross-session resume anchor (memory): `tensor-grep-fable-blitz-active-work-2026-07-07.md`.
- Full process rules: [AGENTS.md](https://github.com/oimiragieo/tensor-grep/blob/main/AGENTS.md). Prose narrative log: [SESSION_HANDOFF.md](SESSION_HANDOFF.md).
