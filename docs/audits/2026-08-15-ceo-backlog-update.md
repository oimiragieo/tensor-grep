# CEO backlog update — 2026-08-15 (dumbed-down)

Public product: **`v1.110.16`** on PyPI and GitHub (unchanged this window; no new release).
Spend: **$0**. No CEO_GATED flip. No #169 spend. Only open PR: **#966** (Task 2A RED draft — do not merge).

Supersedes live unfinished totals in `docs/audits/2026-08-13-ceo-backlog-update.md` for this
snapshot. Keep the 2026-08-13 file as historical campaign closeout. Board index still
**`2026-08-13.1`** (row population unchanged except DD-006 trigger text notes the design packet).

---

## Plain English — what worked

1. **We finished the paperwork for the daemon “too-slow accept” bug (DD-006), not the code fix.**
   Demand was already proven (20 clients timing out at the 0.5s connect budget). This window
   landed a Sol-approved design packet: requirements + design + decisions (PR **#1015**, merge
   `0710219`). Full Claude/Fable council was quota-blocked; you said **skip Fable**; Codex Sol
   approved the exact commit; main CI went green.
2. **Earlier (since the 2026-08-13 CEO packet):** W5–W8 closeout (#1013), session capture +
   A111–A116 retention (#1014), demand-gate measurement skill, CEO packets for the five gated
   rows (recommendations only), RUST-REPLACE-SYMLINK already shipped in v1.110.16.
3. **Hard stops held:** no product daemon code; #966 stayed parked; $0; no silent gate flips.

---

## Plain English — what is still backlog (ALL rows)

Closed-world = **29 rows**. Unfinished = **17**. Buildable READY = **0**.

### BLOCKED — cannot build on this shared box / wrong sequence (6)

| ID | One-line meaning | Why blocked |
|---|---|---|
| **#89** | WSL path looks broken (`path_not_found` on real `/mnt/c`) | Needs Task 2A→2B typed-path; #966 advanced but not GREEN |
| **#90** | WSL raw-path scan finds 0 while translated path finds 6 | Same Task 2A program; doctor half already shipped |
| **F5** | Edit-ready Steps 3–5 | Needs `rust_core` + e2e → CI/cloud (shared-box cargo ban) |
| **F6** | Edit-verification remainder | **Mixed:** Python/schema slices buildable-first; native/e2e still CI/cloud |
| **F8** | Workspace program (Tasks 12–13) | Native front door + path_domain + e2e → CI/cloud |
| **MCP-SURFACE** | MCP disclosure bump | Blocked on Task 2C; live contract version still **1.7.0** |

### CEO_GATED — waiting on your decision (5)

| ID | Decision needed | Money? |
|---|---|---|
| **#48** | Native front-door startup architecture | No |
| **#72** | Fresh public benchmark claim | No |
| **#77** | Ledger-enforcement scope (#77/F9) | No |
| **#131** | Publish GPU-flavor native assets | No |
| **#169** | Physical GPU proof / spend | **Yes — only financial stop** |

Recommendations live in `docs/audits/2026-08-13-ceo-gated-packets.md` (advisory only).

### DEMAND_GATED — research / demand / design (6)

| ID | Status in plain English | Needs more research? |
|---|---|---|
| **#255** | Many-pattern dedup: LEAVE — max pack 35 anchors, no named 100+ user | No fresh chase; reopen only on real demand |
| **DD-006** | Demand **SATISFIED**; **design packet on main** (#1015); product build **not** started | Measurement done. Build needs deliberate go (PERF+HONESTY+A3). Optional: re-measure N\* on clean load |
| **AST-DSL-PARITY** | LEAVE — peers chase DSL/parity, not metavar perf; no blocked consumer | Watch only; no build |
| **MCP-LEAN-DEFAULT** | Spec-level lean defaults elsewhere, but still after Task 2C | Yes when Task 2C unblocks — compatibility evidence |
| **CONTINUOUS-REFRESH** | Warm search-index is table stakes; scoping pass still required | Yes — approved scoping/design pass (not a build) |
| **RUST-REPLACE-TOCTOU** | Residual races after symlink guard | Yes when threat/build authorized — O_NOFOLLOW / reparse / handle opens |

### Terminal — done or retired (12)

**SHIPPED (8):** #36, #37, #109, #859, F7, CPU-BACKEND, REF-CALL-REGISTRY, RUST-REPLACE-SYMLINK  
**RETIRED (4):** #22, F2, F10, DD-004

### Parked PR (not a board READY)

- **#966** — Task 2A RED scaffold. Advanced (Actions evidence). **Not GREEN. Do not merge.**

---

## What needs research (explicit list)

1. **CONTINUOUS-REFRESH** — warm search-index service scoping/design (peer patterns banked; no build).
2. **MCP-LEAN-DEFAULT** — after Task 2C: client demand + compatibility evidence for default lean surface.
3. **RUST-REPLACE-TOCTOU** — residual leaf / walk / ancestor / root swap races; platform primitives already sketched in design.
4. **DD-006 build prep (optional re-measure)** — choose measured backlog **N\*** and aggregate pre-auth cap **P** on a controlled load; box was 77–100% CPU during W5B.
5. **CEO_GATED refresh** — #48/#72/#77/#131/#169 packets are recommendations; any decision is yours (no agent flip).
6. **Do not re-research as “open demand”:** #255 and AST-DSL-PARITY (LEAVE dispositions recorded 2026-08-14).

---

## 5+ lessons since the last CEO update (2026-08-13)

Prior CEO packet: `docs/audits/2026-08-13-ceo-backlog-update.md`. New laws banked as **A111–A122**.

### From the W5–W8 / session-capture wave (A111–A116)

1. **Commit the plan you cite** (A111) — docs on main must not point at untracked plan/spec paths.
2. **Control numbers are literal** (A112) — frozen `failures == 20` needs a single-shot arm that reports 20, not a looped 1600 “close enough.”
3. **Only claim what the raw JSON can split** (A113) — undifferentiated `TimeoutError` is not “connect_timeout.”
4. **Census locations must be re-derived** (A114) — right totals with wrong line inventory = not closed.
5. **Wave receipts are per-row tables** (A115) — group sentences are claims, not evidence.
6. **Never `uv run` a bare worktree into a new `.venv`** (A116) — use the main checkout’s venv against worktree paths.

### From the DD-006 design-packet closeout (A117–A122) — this window

7. **“Skip Fable” waives that seat for that docs packet only** (A117) — not product code, not spend, not CEO_GATED flips (extends A74: substitute SHIP ≠ durable clearance; explicit waiver ≠ build license).
8. **Local `gh pr merge` failure can hide a remote success** (A118) — when another worktree owns `main`, judge `mergedAt` / use the API; don’t double-merge.
9. **Docs PR “skipped jobs” ≠ cheap main** (A119) — PR `changes` gate skips heavy jobs; **push to main always runs the full matrix.**
10. **Shell timeout must exceed probe duration** (A120) — equal timeouts are Sol REVISE; freeze grace separately.
11. **Bigger accept backlog without a pre-auth concurrency cap enlarges DoS** (A121) — `ThreadingMixIn` + raise `request_queue_size` without R7 is incomplete PERF design.
12. **Demand satisfied + design on main ≠ shipped** (A122) — parent DD-006 still needs PERF + HONESTY product code under a separate deliberate go.

---

## Artifacts / proof pointers

| Claim | Proof |
|---|---|
| Public product | `v1.110.16` PyPI + GitHub release |
| Design packet | PR #1015 → `0710219`; Sol exact-commit APPROVE on `abebd62` |
| Main CI after merge | run `31863697236` success |
| Demand evidence | `docs/audits/2026-08-13-demand-gated-dispositions.md` W5B |
| Session capture | `docs/audits/2026-08-14-session-capture.md` |
| Fable waiver | operator “skip fable approval”; recorded on #1015 comment |

**Next (if you want engineering to continue):** say deliberately to **build DD-006** (TDD + A3 security gate on the daemon acceptor). Otherwise the queue stays at **0 READY**.
