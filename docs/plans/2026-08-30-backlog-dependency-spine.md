# Backlog dependency spine — 2026-08-30 SESSION CONTINUE

> **For agentic workers:** Plans only. CEO_GATED / DEMAND_GATED / env-BLOCKED rows are **not** build-authorized. Implement only leaves marked **BUILD_NOW**.

**Goal:** Order every unfinished backlog item by dependencies so SESSION CONTINUE picks one buildable leaf without re-fighting gated work.

**Architecture:** Orchestrator audit queue (`backlog.md`) sits beside canonical `TASK_BOARD.md`. Wave slices are PR-sized; security product code requires A3 before merge.

**Tech Stack:** Python ledger JSON + pytest gates; MCP sanitize later; no Rust in BUILD_NOW leaves.

---

## Dependency DAG (buildable → gated)

```text
[MERGED #1124 wave1 census]
        |
        v
 HANDLER-CENSUS-W2-a (cpu+rg ledger)  --BUILD_NOW-->
        |
        +--> W2-b (cybert/cudf/torch) --> W2-c (ast/rust/stringzilla)
        |
        +--> SEC-007 MCP sanitize (after ledger completeness for backends OR parallel once W2-a pattern proven)
        |
        x  SEC-002..006,008..012  (need per-item A3 plans; do not batch)
        x  ARCH-001/011 split-floor (A130 DI campaign — separate design)
        x  ARCH-003/004 A90 product decision (CEO taste)
        x  #89/#90 WSL (env BLOCKED)
        x  F5/F6/F8 rust/e2e (shared-box → CI)
        x  MCP-SURFACE (after Task 2C)
        x  #72/#77/#131/#169 CEO_GATED
        x  DD-006 product (CEO build go / A122)
        x  other DEMAND_GATED (LEAVE)
```

## Plan inventory

| Plan ID | Path | Status | Depends |
|---|---|---|---|
| SPINE | `docs/plans/2026-08-30-backlog-dependency-spine.md` | this file | — |
| W2 parent | `docs/plans/HANDLER-CENSUS-W2.md` | wave1 SHIPPED #1124/`ed740d0` | — |
| **W2-a** | `docs/plans/2026-08-30-handler-census-w2a-cpu-ripgrep.md` | **BUILD_NOW** | wave1 |
| W2-b | (draft after W2-a merges) | PARKED | W2-a |
| W2-c | (draft after W2-b) | PARKED | W2-b |
| SEC-007 | (draft after W2-a pattern green) | PARKED | W2-a recommended |
| SEC-004 | byte-cap checkpoint reads | PARKED | A3 plan |
| DD-006-PERF | | PARKED | CEO build go |
| BLOCKED-UNBLOCK | `docs/plans/2026-08-22-blocked-row-unblock-campaign.md` | DRAFT not CEO-approved | CEO |

## Size gates (all build plans)

| Class | Limit |
|---|---|
| Contracts / ledger batch | ≤500 LOC net |
| Logic | ≤1500 LOC |
| Tests | ≤2000 LOC |

## Explicit non-goals this continue

- Closing all backlog in one session
- Spending on Sol/Fable if A78 quota still dead (Tier-0 + CI substitute; record FAILED seats)
- Merging while main `ci.yml` `in_progress` after #1124 (wait completed before next merge)
