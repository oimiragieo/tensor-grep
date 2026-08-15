# Decision: DD-006 campaign scope (2026-08-14)

| Field | Value |
|---|---|
| Row | **DD-006** |
| Artifact kind | Decision record |
| Revision id | **REV-DRAFT-3** (packet with requirements + design) |
| Date | 2026-08-14 |
| Status | **DRAFT decision** pending CEO authorization for any product build |
| Requirements | `docs/requirements/dd006-accept-side-bound.md` |
| Design | `docs/design/dd006-accept-side-bound.md` |
| Linked sub-dispositions | **DD-006-PERF**, **DD-006-HONESTY** |

### Content-hash instruction

```powershell
git hash-object docs/decisions/dd006-campaign-scope-2026-08-14.md
```

Record method + SHA with any audit seat verdict (A46).

---

## Decision (one paragraph)

**DD-006 remains demand-gated and CEO-gated for product code.** The next **authorized**
build path for performance is **Option A**: raise the session daemon
`request_queue_size` from the stdlib default **5** to a **measured operating backlog
N\*** (smallest matrix `N` that passes the requirements §11 envelope), **plus** a
**mandatory aggregate pre-auth concurrency bound** (fail-closed when exhausted). The
next **authorized** path for residual cold-fallback / overload honesty is **Option B**
(structured timeout / probe taxonomy so residual warm→cold is attributable). These are
tracked as linked sub-dispositions **DD-006-PERF** (A + aggregate pre-auth bound) and
**DD-006-HONESTY** (B). **Full parent DD-006 row closure requires both.** Closing only
DD-006-PERF does **not** close the parent row. Until CEO authorization, work is limited
to docs/requirements/design packets and measurement harnesses outside production
`src/`. Reject Option C (client-timeout-only) as the primary lever and Option D (async
rewrite) as out of scope.

---

## Why this decision

1. **W5B evidence** (`docs/audits/2026-08-13-demand-gated-dispositions.md`): under 20
   concurrent clients and a 0.5 s connect budget, failures classed as soft
   `connect_timeout`; 0 refusals/drops. Control single-shot: **failures == 20 /
   successes == 0** (A112). That is backlog/latency pressure, not “daemon refuse.”
2. **Code/stdlib fact:** `_ThreadedSessionDaemon` uses `ThreadingMixIn` + `TCPServer`
   without an explicit `request_queue_size` override → default **5**. Raising backlog
   without an aggregate pre-auth cap enlarges DoS admission (Sol BLOCKER-1).
3. **Industry corroboration (Exa, 2026-08-14):** FRR mgmtd raised listen backlog toward
   `SOMAXCONN` under fan-in; Drozd (2026) documents accept-queue overflow presenting as
   client timeouts rather than clean refuses — same observable family as W5B.
4. **tt_quick recommendation:** both seats preferred Option A as the performance lever
   (codex primary; agy concurred). Full Claude/Fable council was quota-blocked (A78).
5. **Sol REV-DRAFT-1 REVISE (BLOCKER-2):** residual cold-fallback must be attributable
   for full closeout → Option B is **mandatory for full parent close**, not optional
   polish. Option A alone may close only **DD-006-PERF**.

---

## Options considered

| Option | Disposition |
|---|---|
| A — Raise `request_queue_size` to measured operating backlog N\* + aggregate pre-auth bound | **Accepted primary for DD-006-PERF** (post-auth + measurement) |
| B — Honest accept-budget / timeout taxonomy | **Mandatory for DD-006-HONESTY / full parent close** |
| C — Raise client connect timeout only | **Rejected** as primary |
| D — Async server rewrite | **Out of scope** |

---

## Hard gates (do not skip)

- [ ] CEO authorization recorded before any `src/` / product test / workflow change.
- [ ] Requirements + design + this decision share the same **revision id**; seats hash
      the exact bytes (A46/A51).
- [ ] Measurement plan is junior-decidable per requirements §11 (exact harness shape,
      20/60/0.5, artifact paths, frozen **20/0** control, A112, host-load validity) —
      no “reasonable local host” / “meets SLA.”
- [ ] Measured **operating backlog N\*** selected from the candidate matrix before
      shipping a magic constant; **hard maximum / enforcement** documented separately
      (or “none beyond OS cap”).
- [ ] Aggregate pre-auth concurrency bound specified and (at authorized build) tested:
      silent + slow unauthenticated client arms; fail-closed when exhausted.
- [ ] Both **DD-006-PERF** and **DD-006-HONESTY** closed before stamping parent DD-006
      SHIPPED / FULLY CLOSED.
- [ ] A3 adversarial security gate on the implementation PR (daemon acceptor /
      concurrency path).
- [ ] Board/tracker update only in the authorized implementation or closure PR(s).

---

## Non-decisions (explicit)

- Exact `N*` value (requires §11 matrix).
- Exact aggregate pre-auth cap `P` and refuse primitive on exhaustion.
- Whether any product hard maximum ships above N\*.
- Whether Option B ships in the same PR as Option A+R7 or a linked follow-up (parent
  waits for both either way).
- Any change to bind address, auth token scheme, or remote exposure.
- Closing the full DD-006 row on Option A alone.

---

## Audit-closure note — Sol REV-DRAFT-1 → REV-DRAFT-2 (2026-08-14)

Codex Sol returned **REVISE** on REV-DRAFT-1. How **REV-DRAFT-2** closes each finding
(docs-only; no product code):

| Sol finding | Closure in REV-DRAFT-2 |
|---|---|
| **BLOCKER-1** — no aggregate pre-auth worker/admission cap; raising `request_queue_size` enlarges DoS admission | Requirements **R7** + design **§3** specify mandatory measured aggregate pre-auth concurrency bound, fail-closed exhaustion, and adversarial silent/slow unauthenticated client tests. Design recommendation couples R7 with Option A. **Not implemented** — contract for future authorized build only. |
| **BLOCKER-2** — R4 vs optional Option B inconsistency | Decision + requirements split **DD-006-PERF** (Option A + R7) vs **DD-006-HONESTY** (Option B). Option B is **mandatory for full parent close**. R3/R4 and design recommendation updated; Option A alone may close only the PERF sub-disposition. |
| **MAJOR** — measurement plan not junior-decidable | Requirements **§11** replaces vague SLA language with exact harness/command shape, client count/duration/cadence (20 / 60 s / 0.5 s), artifact paths, frozen positive-control **20/0** (W5B; A112 CANNOT_MEASURE), and host-load validity criteria. Design §11 points to that normative text. |
| **NIT-1** — failure matrix “refuse class” | Observability and failure matrices use structured **`auth_rejected`** (unauthorized after accept). |
| **NIT-2** — “measured ceiling” ambiguous | Renamed to **measured operating backlog N\*** (smallest matrix `N` that passes the envelope); **hard maximum / enforcement** defined separately (product clamp or “none beyond OS”). |

**Packet status after this note:** REV-DRAFT-2 ready for Sol re-audit on the exact
worktree/committed bytes of the three packet files (requirements, design, decisions).

---

## Audit-closure note — Sol REV-DRAFT-2 → REV-DRAFT-3 (2026-08-14)

Codex Sol returned **REVISE** on REV-DRAFT-2 (prior blockers CLOSED; one new blocker):

| Sol finding | Closure in REV-DRAFT-3 |
|---|---|
| **BLOCKER** — §11 set probe `--duration 60` and enclosing shell `timeout 60` equal, so startup/sync/JSON flush can kill a valid run before the receipt | Requirements §11.1 now freezes **probe duration 60 s**, enclosing shell **`timeout 90`**, and **frozen grace 30 s** (shell must strictly exceed `--duration`). Design §11 points updated. |

**Packet status after this note:** REV-DRAFT-3 ready for Sol re-audit.
