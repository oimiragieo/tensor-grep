# Closed-world census — 2026-08-05.2

**Purpose:** measured dispositions for Wave W2.c board reconcile. No CEO decisions invented.

**Sources:**
- `docs/BACKLOG.md` § RECONCILED 2026-08-05
- `docs/audits/2026-08-05-enterprise-launch-readiness-census.md` (drift callout)
- `gh pr list` / merged PR merge commits (2026-08-05)
- PyPI version endpoint with positive + negative controls

## PyPI / release

| Probe | Result |
|---|---|
| `GET https://pypi.org/pypi/tensor-grep/1.108.2/json` | **HTTP 200** (prior complete) |
| `GET https://pypi.org/pypi/tensor-grep/1.999.999/json` | **HTTP 404** |
| `GET https://pypi.org/pypi/tensor-grep/1.109.0/json` | **HTTP 200** (remeasured after publish; GitHub release publishedAt 2026-08-06T00:05:12Z) |
| Board stamp | `post-**v1.109.0**` (PyPI version endpoint HTTP 200) |

**Merge gate:** PyPI `1.109.0` is served (HTTP 200). Still wait if a *newer* main release publish
is in flight before merging this docs PR (push-race / publish-tail cancel).

## Canonical index flips (board `2026-08-05.1` → `2026-08-05.2`)

| ID | Was | Now | Evidence |
|---|---|---|---|
| **CPU-BACKEND** | IN_FLIGHT (#923) | **SHIPPED** | PR #923 (`0481e975…`) Python invert_match; PR #925 (`f29c9484…`) Rust replace_in_place; Closure PR #948 |
| **REF-CALL-REGISTRY** | IN_FLIGHT (#915) | **SHIPPED** | PR #915 (`3faf500f…`) registry dispatch; PR #940 (`3dbe85b1…`) Step 2 pin; Closure PR #948 |
| **F5** | READY | **IN_FLIGHT** (#943) | Step 2 `PrepareSnapshotV1` merged; Steps 3–5 rust/e2e blocked |
| **F6** | READY | **IN_FLIGHT** (#939) | Step 0 `prepare_service.py` merged; remainder multi-week |
| **F7** | READY | **IN_FLIGHT** (#950) | Task 10 shipped #927–#934; Task 11 wave 1 Java merged #950 |
| **F8** | READY | **BLOCKED** | `rust_core` + e2e routing suite |
| **MCP-SURFACE** | READY | **BLOCKED** | live MCP contract `1.7.0`; Task 4 needs Task 2C first |
| **#89** | READY | **BLOCKED** | Task 2B/2C typed-path / rust_core + WSL host |
| **#90** | READY | **BLOCKED** | same owner as #89; doctor half still PR #571 |
| **F10** | (already RETIRED on 2026-08-05.1) | RETIRED | #953 |
| **DD-004** | (already RETIRED on 2026-08-05.1) | RETIRED | #953 |

## Live tallies after flips

- Population: **28** rows (unchanged `EXPECTED_IDS`)
- Terminal (SHIPPED+RETIRED): **10**
- Unfinished: **18** = 0 READY + 3 IN_FLIGHT + 4 BLOCKED + 5 CEO_GATED + 6 DEMAND_GATED
- Start-now READY set: **EMPTY** (matches BACKLOG reconcile)

## Open PRs at census time (`gh pr list --state open`)

Derived live; re-run before merge. Observed 2026-08-05: #952, #954, #955, #956 (plus any opened after).

## Explicit non-decisions

- `#48` / `#72` / `#77` / `#131` remain **CEO_GATED** — recommendation packets only; no status change.
- `#169` remains the only **financial** stop.
- DEMAND_GATED six remain demand/research gated.
- Task 2A RED stays FIX-FIRST / unpushed — not merge-ready.
