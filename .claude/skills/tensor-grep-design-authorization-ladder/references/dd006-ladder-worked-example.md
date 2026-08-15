# DD-006 ladder worked example (2026-08-14 → 2026-08-15)

Condensed authorization path for parent row **DD-006** (session-daemon accept-side bound).
Companion to `tensor-grep-design-authorization-ladder`. Demand measurement detail lives in
`tensor-grep-demand-gate-measurement/references/dd006-worked-example.md`.

## Stage map

| Stage | What happened | Artifact / law |
|---|---|---|
| 1. Demand measure | W5B: 20 clients / 60 s; soft `connect_timeout` under `_DAEMON_CONNECT_TIMEOUT_SECONDS` (0.5 s); 0 refusals/drops; control **20/0** (A112) | Trigger SATISFIED; no `src/` |
| 2. Design packet | Requirements + design + decisions share **REV-DRAFT-3** | `docs/requirements/dd006-accept-side-bound.md`, `docs/design/dd006-accept-side-bound.md`, `docs/decisions/dd006-campaign-scope-2026-08-14.md` |
| 3. Sol exact-commit | Hash packet bytes; Sol APPROVE after REVISE waves (incl. A120 timeout envelope, A121 R7) | A46/A51; Sol not “docs vibes” |
| 4. Fable waiver | Operator waived Fable for the **named docs packet only** | A117; recorded on PR |
| 5. Deliberate build | **Not started** as of 2026-08-15 CEO packet | A122 — design on main ≠ SHIPPED |
| 6. TDD + A3 | Still owed for product slices | Daemon acceptor = security-class |

Merged design PR: **#1015** / merge `0710219` (docs packet). Parent DD-006 stays open.

## Sub-dispositions (do not flatten)

| ID | Intent | Primary lever | Close condition |
|---|---|---|---|
| **DD-006-PERF** | Accept-side capacity under fan-in | Raise `request_queue_size` from stdlib default **5** to measured operating backlog N\* **plus** fail-closed aggregate pre-auth concurrency cap (**R7**) | Product code + measurement envelope green |
| **DD-006-HONESTY** | Residual warm→cold attributable | Option B: structured timeout / probe taxonomy (not client-timeout-only) | Product code; mandatory for **full parent** close |

`_ThreadedSessionDaemon` uses `ThreadingMixIn` + `TCPServer`. Larger listen backlog without R7
enlarges DoS admission (A121 / Sol BLOCKER-1). Closing PERF alone does **not** close parent DD-006.

## ADR parallel (what we mirror / what we do not)

Nygard ADR (Context → Decision → Status → Consequences; Alternatives called out) is the industry
shape. tensor-grep mirrors that content across **three** packet files under `docs/`, not
`adr.github.io` tooling or a GitHub Actions ADR workflow. Status stays **DRAFT** until a deliberate
build go; merging the packet does not flip the board to SHIPPED (A122).

## Hard stops carried forward

- Freeze **three** times for any follow-on probe: duration, grace, enclosing shell timeout with
  outer **strictly greater** than duration+grace (A120).
- Cite `request_queue_size`, `ThreadingMixIn`, `_DAEMON_CONNECT_TIMEOUT_SECONDS` by **name**.
- Skip-Fable ≠ build license (A117). Design-on-main ≠ shipped (A122).
