# Enterprise Closeout Wave-2 Plan — 2026-08-06 (reconciled)

> **For agentic workers:** REQUIRED SUB-SKILL: superpowers:subagent-driven-development
> or executing-plans. Checkboxes track work. Orchestrator keeps context lean.

**Goal:** Land START_NOW remainder (D1/#964), then begin authorized W4 Task 2A FIX-FIRST repair toward Sol exact-byte `SHIP` + draft Windows CI — **without** false-GREEN claims or STOP-list product builds. Public claim stays route-scoped Phase 0+1 readiness, not absolute “Jarvis enterprise launch complete.”

**Architecture:** Disposition-first. Ratified closeout spine (`docs/plans/2026-08-06-enterprise-backlog-closeout-plan.md`) already SHIP for START_NOW. Wave-2 finishes D1 then enters W4 security order. Fable seat recommended STOP-after-D1 pending re-auth; **Codex Sol + prior thinktank AMEND_SPINE + CEO “complete backlog” mandate authorize W4 after D1**. Absolute public-launch marketing remains CEO_GATED (#72/#48 family).

**Tech Stack:** docs governance, Windows `gh`/`git`, pytest TDD, Sol adversarial gate, GitHub Actions Windows CI.

## Global Constraints

- No false GREEN on Task 2A / #89 / #90
- No silent CEO_GATED or DEMAND flips
- No #169 spend
- No MCP-SURFACE before Task 2C (`1.7.0→1.8.0`)
- No local `rust_core` cargo / F5 Steps 3–5 / F8 / F6 native on shared desktop
- Merge gate: no main runs in flight; `fix:`/`feat:` one-at-a-time with publish wait
- A60: never WSL `uv` against Windows `.venv`

## START_NOW

- [ ] **D1** — Squash-merge #964 when exact-head CI terminal green and main idle
- [ ] Confirm tip has R0a CEO packets + R0b DEMAND receipts + board READY∩BLOCKED stamp + closeout plan
- [ ] Append BACKLOG dated receipt for START_NOW close (B0/D1/R0)
- [ ] Dogfood published `1.110.0` refuse + CUJ routes (docs PR does not bump PyPI)

## Wave W4 — Task 2A (authorized after D1)

Local RED: `6367614960327b1a4e00301c8bfdb9b2e4bb453e` on `task2a-round60-red` (ahead 1 / behind ~67).

- [ ] Isolated worktree `.claude/worktrees/task2a-w4-repair`
- [ ] Rebase onto `origin/main`; pause if Phase 0+1 conflicts require re-scope
- [ ] Repair ten Sol HIGH (board Task 2A gate): immutable-SHA Windows CI expectation; runner crash≠behavioral RED; PCRE2 oracle in census; Job heartbeat/cleanup; SDDL garbage reject; CNG exportable positive control; TxR close ownership; producer no self-attest-before-start; public `-f`/`--file` ledger-before-read
- [ ] Exact-byte Sol `SHIP` on head
- [ ] Push **draft** PR; real Windows CI; only then claim clearance
- [ ] Then #89 → Task 2B/#90 → Task 2C → MCP-SURFACE (later waves)

## Explicit STOP (unchanged)

MCP-SURFACE product build; F5 3–5; F6 remainder; F8; #89/#90 product GREEN before Task 2A clearance; #169; silent CEO reclass; “world_class_readiness” absolute claim.

## Council notes (2026-08-06)

| Seat | Verdict |
|---|---|
| Prior plan Round-2 | SHIP_START_NOW then W4 |
| Codex Sol (shell) | `B_START_TASK2A_REPAIR` after #964 |
| Fable (shell) | `PROCEED_D1_THEN_STOP` (wants re-auth) — **overridden** by ratified W4 + CEO complete-backlog + Sol |
| tt_quick | FAILED (codex PROVIDER_FAILURE / agy STRUCTURED_INVALID) — not blocking; Sol direct seat used |

**ORCH_VERDICT:** `PROCEED_D1_THEN_W4`

## Dogfood + BACKLOG

- After any releasing merge: published-wheel matrix
- After D1 docs merge: no version bump expected; still re-verify `1.110.0` refuse class
- Document new findings in `docs/BACKLOG.md` same turn as discovery

## Plan hash

`sha256sum docs/plans/2026-08-06-enterprise-closeout-wave2-plan.md` after land; cite in Sol re-audit.
