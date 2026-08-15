---
name: tensor-grep-design-authorization-ladder
description: >-
  Use when demand is SATISFIED but product code is not yet authorized: writing or auditing
  docs/requirements+design+decisions packets, recording skip-Fable waivers, splitting PERF vs
  HONESTY sub-dispositions, or resisting "design on main = shipped" (A117/A122). Covers the
  authorization ladder (measure → design packet → Sol exact-commit → optional Fable waiver →
  deliberate build go → TDD+A3), measurement-envelope timeouts (A120), backlog+R7 pairing when
  raising request_queue_size under ThreadingMixIn (A121), and Sol audits that hash exact commit
  bytes. NOT for demand measurement itself (tensor-grep-demand-gate-measurement) or unconstrained
  product builds without a recorded go.
---

# tensor-grep: design authorization ladder

Closes the gap between **demand proven** and **product code licensed**. Industry parallel (Exa /
Nygard 2011 ADR): significant choices land as short records with **Context / Decision /
Alternatives / Consequences** *before* implementation. We do **not** use the GitHub `adr` workflow
or `doc/arch` numbering — we use in-repo **`docs/requirements/` + `docs/design/` +
`docs/decisions/`** packets with a shared revision id and content-hash (A46/A51). Proven on
DD-006 (2026-08-14/15). Full worked example:
`references/dd006-ladder-worked-example.md`.

## When to use / NOT

| Your task | Use |
|---|---|
| Demand trigger SATISFIED; next work is a design/requirements/decisions packet | **this skill** |
| Operator says “skip Fable” / Fable quota-blocked on a **named docs packet** | **this skill** (A117) |
| Board row has mixed dispositions (demand ok / design landed / build not started) | **this skill** (A122) |
| PERF vs HONESTY (or similar) sub-dispositions must stay separate for full close | **this skill** |
| Sol/Codex exact-commit audit of packet bytes before merge | **this skill** |
| Measuring whether a DEMAND_GATED Trigger is satisfied | `tensor-grep-demand-gate-measurement` |
| Implementing `src/` after a deliberate build go | `tensor-grep-backlog-campaign` + `tensor-grep-change-control` |
| Adversarial security gate on daemon/acceptor/auth code | A3 via change-control / enterprise-agent |

**DO NOT USE FOR:**

- Treating a merged design PR as SHIPPED or READY for CUJ close.
- Treating a Fable waiver as license to touch `src/`, spend, or CEO_GATED flips.
- Building Option A (`request_queue_size`) without the R7 aggregate pre-auth cap (A121).

## The ladder (numbered stages)

1. **Demand measure** — bounded, plan-frozen probe; Trigger re-derived from `origin/main`. Skill:
   `tensor-grep-demand-gate-measurement`. Outcome: SATISFIED / unmet / CANNOT_MEASURE.
2. **Design packet** — same revision id across `docs/requirements/`, `docs/design/`,
   `docs/decisions/`. ADR-shaped sections (Context, Decision, Alternatives, Consequences) without
   claiming GitHub ADR tooling. Cite production symbols by **name** (e.g. `request_queue_size`,
   `ThreadingMixIn`, `_DAEMON_CONNECT_TIMEOUT_SECONDS`), never stale lines.
3. **Sol exact-commit audit** — hash designated packet bytes (`git hash-object`); seat stamps the
   exact SHA/revision. APPROVE / REVISE / BLOCKER. No product code yet.
4. **Optional Fable waiver (A117)** — operator may waive the Fable design-audit seat for the
   **named docs packet only** after Sol APPROVE. Record on the PR. Does **not** clear product
   build, spend, or CEO_GATED (extends A74).
5. **Deliberate build go** — explicit authorization before any `src/` / product-test / workflow
   change. Split sub-dispositions if full close needs more than one shippable slice (e.g.
   DD-006-PERF vs DD-006-HONESTY).
6. **TDD + A3** — failing tests first; security-class surfaces (daemon acceptor, pre-auth paths,
   concurrency caps) get the mandatory adversarial gate before merge.

## Fable waiver vs build license (A117)

| Cleared by “skip Fable” | Not cleared |
|---|---|
| Named docs/design/decisions packet after Sol exact-commit APPROVE | Product/`src/` changes |
| Merging that docs PR | Spend or CEO_GATED flips |
| Recording the waiver on the PR | Treating a quota substitute as durable clearance (A74) |

A117: waiver is seat-scoped and artifact-named. Build still needs stage 5 + 6.

## Design ≠ SHIPPED (A122)

Demand SATISFIED + design packet on `main` is **not** SHIPPED. Keep the parent row open with
honest mixed dispositions (e.g. demand done / design landed / PERF+HONESTY product not started).
Do not flatten to READY or close on docs alone. Full parent close needs every linked
sub-disposition’s product code under a deliberate go.

## Measurement envelope (A120) + backlog+R7 (A121)

**A120 — three time numbers.** Freeze probe **duration**, frozen **grace**, and enclosing shell
**timeout**. Outer timeout must **strictly exceed** duration + grace. Equal timeouts are Sol
REVISE (the probe cannot finish cleanly).

**A121 — backlog without R7 is incomplete.** Raising `request_queue_size` (listen backlog) without
a finite fail-closed **aggregate pre-auth concurrency cap** enlarges DoS admission.
`ThreadingMixIn` spawns a thread per accept; a larger backlog alone is Sol BLOCKER-class for
DD-006-PERF. Pair backlog change with R7 (or equivalent named cap) in the design **before** build.

Client connect budget lives in `_DAEMON_CONNECT_TIMEOUT_SECONDS` — cite by name when discussing
warm→cold fallback honesty.

## Sol exact-commit audit

1. Shared revision id on requirements + design + decisions.
2. `git hash-object` (or designated worktree bytes) — record method + digest (A46).
3. Seat reads that exact artifact; verdict binds that SHA only (A51). Newer worktree bytes lose
   clearance.
4. Fold BLOCKER/REVISE into the packet before merge; do not “fix in the build.”

## Worked example pointer

See `references/dd006-ladder-worked-example.md` for the DD-006 ladder from W5B demand measure
through design packet #1015, Sol, Fable waiver, and the still-open PERF+HONESTY build gate.

## Related skills

- `tensor-grep-demand-gate-measurement` — Stage 1 only (reopen condition).
- `tensor-grep-backlog-campaign` — drain/build orchestration after deliberate go.
- `tensor-grep-change-control` — gates, A3, registration completeness.
- `tensor-grep-codex-gated-audit-loop` — Sol/Codex exact-commit review loops.
- `tensor-grep-enterprise-agent` — enterprise hard-stops; design ≠ CUJ-complete.
