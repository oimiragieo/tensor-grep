# DOCS-RECONCILE Implementation Plan

> **For agentic workers:** Docs-only slice; governance pytest is the RED/GREEN gate. No product code.

**Goal:** Reconcile `docs/TASK_BOARD.md` and `docs/SESSION_HANDOFF.md` after SEC-001 (#1122) and ENV-SYNC closeout.

**Architecture:** Stamp `post-**v1.113.5**` reconcile paragraph (first-match tolerance gate), bump canonical index to `2026-08-30.1`, correct F8/MCP-SURFACE triggers to cite real files on `origin/main`.

**Tech Stack:** Markdown governance docs + pytest pins (`test_task_board_freshness`, `test_backlog_tracker_truth`, `test_public_docs_governance`).

**Status:** IMPLEMENTED @ `2fb11a5` — PR #1123  
**Release class:** `docs:` (no publish)

---

## Task 1: TASK_BOARD reconcile stamp

**Files:**
- Modify: `docs/TASK_BOARD.md`

- [x] **Step 1:** Add `post-**v1.113.5**` reconcile paragraph recording SEC-001 + ENV-SYNC
- [x] **Step 2:** Set `Canonical status index version: 2026-08-30.1`
- [x] **Step 3:** Correct F8 trigger (no `path_domain.rs`; cite `main.rs` routing)
- [x] **Step 4:** Correct MCP-SURFACE trigger (`mcp_server.py:188`)

## Task 2: SESSION_HANDOFF refresh

**Files:**
- Modify: `docs/SESSION_HANDOFF.md`

- [x] **Step 1:** Add 2026-08-30 closeout section
- [x] **Step 2:** Mirror canonical index `2026-08-30.1`

## Task 3: Verification (GREEN)

```powershell
uv run --no-sync python -m pytest tests/unit/test_task_board_freshness.py tests/unit/test_backlog_tracker_truth.py tests/unit/test_public_docs_governance.py -q
uv run --no-sync ruff format --check --preview docs/TASK_BOARD.md docs/SESSION_HANDOFF.md
git diff origin/main...HEAD --name-only
```

**Pass criteria:** 97 passed; 2 files formatted; diff is docs-only.

Answer key: `.wayfinder/docs-reconcile/MAP.md`

---

## Plan audit (Tier-0 structural — 2026-08-30)

| Gate | Verdict | Notes |
|---|---|---|
| Scope is docs-only | **PASS** | `git diff origin/main...HEAD` = 2 markdown files |
| file:line claims verifiable | **PASS** | F8/MCP triggers cite real paths; `path_domain.rs` absence verified |
| No contradictory acceptance criteria | **PASS** | Stamp v1.113.5 is intentional (tolerance gate first-match) |
| Release class honest | **PASS** | `docs:` — no product/security code |
| Size limits | **N/A** | Docs slice; no contracts/logic modules |

**Tier 3 seats:** `use-claude` / `use-codex` not on PATH — Fable/thinktank/Sol deferred ($0 spend). Tier-0 audit **APPROVED** for docs-only scope.

---

## QA audit (Tier-0 — implementation SHA `2fb11a5`)

| Check | Result |
|---|---|
| Governance pytest (97 tests) | **PASS** (local 2026-08-30) |
| Changed-file format `--preview` | **PASS** |
| Diff population (docs only) | **PASS** |
| Wayfinder MAP criteria | **PASS** |
| PR base `main` | **PASS** (#1123) |
| PR CI run `33295196600` | **PENDING** at audit time |

**AUDIT_CLEAR** (local verification complete; merge gated on CI completion + push-race check per A33/A142).
