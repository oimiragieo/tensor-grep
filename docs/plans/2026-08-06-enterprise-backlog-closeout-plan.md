# Enterprise Backlog Closeout Plan — 2026-08-06 (AMEND_SPINE absorbed)

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development
> (or executing-plans). Steps use checkbox (`- [ ]`) syntax. Orchestrator keeps context lean —
> workers write receipts into `docs/audits/`; do not paste large diffs into the orchestrator chat.

**Goal:** Close what *can* close for Jarvis-class enterprise public-release readiness **without**
false GREEN: land truth-stamping docs, CEO/DEMAND research packets, and only then security/CI-gated
build waves. Do **not** claim full launch readiness from docs progress alone.

**Architecture:** Disposition-first drain. Canonical truth is the 2026-08-05/06 reconcile in
`docs/BACKLOG.md` + audits — not the still-stale READY labels on `docs/TASK_BOARD.md`. Build only
the START_NOW set below. Everything else is STOP until its gate fires.

**Tech Stack:** docs/governance tests, Windows `gh.exe` + PowerShell verification, pytest TDD,
GitHub Actions (CI/cloud for rust/e2e), Sol adversarial gate for Task 2A.

**Plan authorship:** Fable Task seat usage-limited through ~2026-08-14; orchestrator-authored from
measured `gh`/tip blobs + thinktank AMEND_SPINE (agent `e0e0a11d`) + premise census (agent
`fa9c67ea`) + Task2A probe (agent `5add4114`). WebSearch substituted for Exa (MCP absent).

**Thinktank Round-1 (2026-08-06):** `VERDICT: AMEND_SPINE` — all MUST_FIX folded into this document.
Round-2 re-audit required before any BUILD beyond B0/R0/docs stamp.

**Measured base (re-measure before each merge):**
- Public product: `tensor-grep 1.110.0` on PyPI (prior W5 dogfood #962)
- Open PR at plan write: **#963** docs CEO update (`docs:` non-releasing), head `a9bae7e`
- Task 2A RED `6367614960327b1a4e00301c8bfdb9b2e4bb453e` exists **locally only** on
  `task2a-round60-red`; **not** on `origin/main`; **no** Actions run (A68: no clearance)
- Live MCP `_TG_MCP_SERVER_CONTRACT_VERSION = "1.7.0"`

## Global Constraints

- Never build in a dirty shared checkout; use `.claude/worktrees/*` via **Windows git**
- Never point WSL `uv` at the Windows `.venv` (A60)
- `#169` FINANCIAL_HOLD — no spend
- CEO_GATED `#48/#72/#77/#131` — **recommendation packets only**; never silent reclass
- DEMAND_GATED — research/retire-with-receipt only when a measured null/false premise exists;
  never flip status by prose alone
- MCP wire-contract fence: Task 2C owns `1.7.0→1.8.0` before Task 4 / MCP-SURFACE `1.8.0→1.9.0`
- No local `cargo` / rust_core / e2e routing builds on the shared desktop (W3 → CI/cloud)
- Security-class PRs: independent adversarial `SHIP` on exact head (A3/A52); architecture SHIP ≠ security SHIP
- Merge gate: newest main run `completed` before next merge (A33); docs may batch after completed
- Public claim stays route-scoped; `world_class_readiness = not_claimed` remains honest
- Board READY is **disputed**, not a build license (A71/A76)

---

## START_NOW (only)

| ID | Work | Acceptance |
|---|---|---|
| **B0** | Land #963 after exact-head CI population terminal green | Squash-merge; tip includes A70–A76 + F7/CPU-BACKEND/REF-CALL SHIPPED stamps |
| **R0a** | CEO_GATED recommendation packets refresh (`#48/#72/#77/#131`) | Dated audit under `docs/audits/`; status remains `CEO_GATED` |
| **R0b** | DEMAND_GATED research/retire-with-receipt (`#255`, `DD-006`, `AST-DSL-PARITY`, `MCP-LEAN-DEFAULT`, `CONTINUOUS-REFRESH`, `RUST-REPLACE-SYMLINK`; F10/DD-004 already retired if stamp confirms) | Receipt file; **no** silent status flip without measured premise |
| **D1** | Docs-only board stamp: READY∩BLOCKED contradiction for MCP-SURFACE / F5 / F6 / F8 / #89 / #90 | Canonical index matches BACKLOG reconcile; freshness stamp vs PyPI; governance tests green |

---

## Explicit STOP (do not build)

| Item | Why |
|---|---|
| **MCP-SURFACE / Task 4** | Live contract is `1.7.0`; Task 4 plans `1.8.0→1.9.0`. Needs Task 2C first. Additive incomplete fields still expand the wire. |
| **F6 Tasks 6–7 remainder** | Multi-week; includes evidence signing, bounded readers, WSL path-domain, native `verify-edit` + e2e |
| **F5 Steps 3–5** | BLOCKED on `rust_core/**` + `tests/e2e/**` |
| **F8 Tasks 12–13** | BLOCKED on `rust_core` + e2e parity |
| **#89 / #90 product GREEN** | Owned by Task 2B/2C after Task 2A Sol `SHIP` + real Windows CI |
| **Task 2A “re-gate GREEN”** | RED still FIX-FIRST (10 HIGH); unpushed; no Actions. Order: repair → Sol exact-byte SHIP → push draft → Windows CI → then #89/#90 |
| **W3 rust halves** | Shared-box cargo ban; CI/cloud only |
| **#169** | FINANCIAL_HOLD |
| **Silent CEO_GATED → READY/RETIRED** | Hard STOP |
| **“Jarvis enterprise launch ready” absolute claim** | Only route-scoped Phase 0+1 CUJ non-claim allowed |

---

## Dependency order (CEO update / Task 2 program)

Correct order (do not invert):

1. **Task 2A** — repair RED `6367614…` ten HIGH blockers → Sol exact-byte `SHIP` → push draft → immutable-SHA Windows CI
2. **#89** search path-domain product fix (typed-path)
3. **Task 2B / #90** scan portability
4. **Task 2C** — MCP contract `1.7.0→1.8.0` (WSL path-domain MCP)
5. **Task 4 / MCP-SURFACE** — `1.8.0→1.9.0` + `tool_surface` lean/full disclosure

Premise: #911 merged planning/deps only — **not** Task 2A clearance.

---

## Wave B0 — Land #963

- [ ] `gh pr checks 963` — require heavy lanes **present by name/count**, not “nothing pending”
- [ ] Exact head `a9bae7e` (or successor) green across CI + Security Audit + CodeQL
- [ ] Squash-merge (`docs:` → no release)
- [ ] Fast-forward local tracking; confirm tip blob has CEO audit + A70–A76

---

## Wave R0 — Research packets (parallel subagents)

### R0a — CEO_GATED packets

For each of `#48`, `#72`, `#77`, `#131`:

- [ ] One-page recommendation in `docs/audits/2026-08-06-ceo-gate-<id>.md` (or single bundled file with sections)
- [ ] Cite live code/`file:line` or measured receipt; no invented spend
- [ ] Explicit line: `STATUS REMAINS CEO_GATED`

### R0b — DEMAND research / retire-with-receipt

- [ ] For each DEMAND row: either (a) measured reopen trigger still unmet → leave DEMAND_GATED with updated evidence date, or (b) measured null/false premise → write RETIRE receipt and a **separate** docs PR that flips status with the measurement in-body
- [ ] Never flip inside a free-form paragraph outside the canonical index (A71)

---

## Wave D1 — Board truth stamp (docs/test)

**Files:** `docs/TASK_BOARD.md`, possibly `docs/BACKLOG.md` Active list, `MEMORY.md`, `docs/SESSION_HANDOFF.md`

- [ ] Index rows for MCP-SURFACE / F5 / F6 / F8 / #89 / #90: Status must match BACKLOG reconcile (`BLOCKED` / `PARTIAL` wording allowed; do not leave false READY build licenses)
- [ ] Preserve mixed dispositions; do not flatten shipped+blocked
- [ ] Refresh reconcile stamp vs PyPI `1.110.0` (ordinal distance, not major.minor sentinel)
- [ ] Run `tests/unit/test_task_board_freshness.py` (+ related docs governance) on Windows or CI
- [ ] Title `docs:` — non-releasing; merge after B0 if needed as follow-on

---

## Wave W4 — Task 2A (deferred until START_NOW done; security order)

Only after B0+R0+D1 land and WIP allows:

1. Isolated checkout of `task2a-round60-red` / `6367614…`
2. Repair the **ten named Sol HIGH** blockers (census/PCRE2 oracle, Job heartbeat/cleanup, SDDL, CNG positive control, TxR close ownership, producer self-attest, public `-f`/`--file` pre-ledger, etc. — see `docs/TASK_BOARD.md` Task 2A gate)
3. Exact-byte Sol until `SHIP`
4. Push **draft** PR; obtain real Windows CI on immutable SHA (A68)
5. Only then #89 → Task 2B/#90 → Task 2C → MCP-SURFACE

If Sol seats are usage-limited: Opus adversarial substitute may unblock *repair design*, but **vendor Sol SHIP on exact bytes remains the gate** for claiming Task 2A clearance (A74).

---

## Wave W3 — rust_core / e2e (CI/cloud only; after W4 path or parallel if WIP allows)

- [ ] F5 Steps 3–5, F8 Tasks 12–13, any remaining F6 native registration — **only** via CI/cloud seats
- [ ] No local cargo on shared desktop
- [ ] Any Python-only security-adjacent slice of F6 (if later carved) still needs independent adversarial SHIP (A3)

---

## Wave Close — Dogfood + findings

- [ ] After any release-bearing merge: published-wheel dogfood matrix (pos+neg)
- [ ] Append new issues/bugs to `docs/BACKLOG.md`; reconcile board same turn
- [ ] Lint/format: `ruff check` + `ruff format --check --preview .` + `mypy src/tensor_grep` (Windows for product claims)

---

## Dispatch table

| task_id | agent_role | depends_on | acceptance |
|---|---|---|---|
| B0 | drain | CI green | #963 merged |
| R0a | research | — | CEO packets; status unchanged |
| R0b | research | — | DEMAND receipts; no silent flip |
| D1 | docs implementer | B0 preferred | READY∩BLOCKED cleaned; tests green |
| Thinktank-R2 | plan auditor | this file | `SHIP` or further AMEND |
| W4 | security builder + Sol | D1 + WIP | Sol SHIP + Windows CI |
| W3 | CI/cloud builders | gates | rust/e2e green |
| Close | dogfood | merges | wheel matrix + BACKLOG |

---

## Plan hash method

Canonical bytes: this file in the **enterprise-closeout-plan worktree** (or main after land).
Reviewers verify `sha256sum docs/plans/2026-08-06-enterprise-backlog-closeout-plan.md` on that path
and cite the hash in the Round-2 verdict. Approval is exact-artifact-specific (A51).
