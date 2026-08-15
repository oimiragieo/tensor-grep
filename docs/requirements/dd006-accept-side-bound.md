# DD-006 requirements: accept-side bound for the session daemon

| Field | Value |
|---|---|
| Row | **DD-006** (daemon concurrent-load / accept-path bound) |
| Artifact kind | Requirements (Phase-1 draft) |
| Revision id | **REV-DRAFT-3** |
| Worktree / branch | `docs/dd006-accept-bound-auth-packet` |
| Base SHA (packet start) | `93078ef3ec758a02982cb5bea1d4450fb9beb28c` |
| Status | **DRAFT** — not authorized for product implementation |
| Companion docs | `docs/design/dd006-accept-side-bound.md`, `docs/decisions/dd006-campaign-scope-2026-08-14.md` |
| Evidence anchor | `docs/audits/2026-08-13-demand-gated-dispositions.md` §W5B; board row in `docs/TASK_BOARD.md` |
| Linked sub-dispositions | **DD-006-PERF** (Option A), **DD-006-HONESTY** (Option B) — see §5 R3/R4 |

### Content-hash instruction (for auditors)

Before any approval stamp, hash **this file's committed Git blob** (or the designated
canonical worktree bytes) and record the method beside the hash (A46). Do not treat a
path-only citation as clearance. Example (PowerShell):

```powershell
git hash-object docs/requirements/dd006-accept-side-bound.md
```

Stamp the resulting SHA next to `REV-DRAFT-3` when promoting to an audited revision
(e.g. `REV-DRAFT-3 @ <blob-sha>`). A later edit without a new revision id invalidates
prior seats.

---

## 1. Problem (plain language)

The warm **session daemon** accepts TCP connections on loopback. Under a burst of
concurrent clients, some connects miss the CLI's short connect budget
(`_DAEMON_CONNECT_TIMEOUT_SECONDS`, currently **0.5 s**) and the client falls back to
the **cold path** (`_probe_daemon` → `None`). That looks like "daemon was fine" from
the user's point of view — search still works — but the warm-path benefit silently
disappears under load.

The 2026-08-14 bounded probe (W5B) reproduced **soft** accept-path latency, not a hard
denial:

- 20 clients × 60 s; control single-shot arm **20 failures / 0 successes** (instrument valid;
  A112 — plan-frozen threshold met verbatim).
- 5/5 live arms at 0.5 s connect budget showed timeouts; the discriminating arm labeled
  them **`connect_timeout`**.
- **0 refusals / 0 drops** recorded; at 2.0 s connect budget and in the single-client arm,
  failure rate was **0**.
- Mechanism **hypothesis** (not yet a closed root-cause proof): Python
  `socketserver.TCPServer` default **`request_queue_size = 5`** (stdlib `listen`
  backlog) on `_ThreadedSessionDaemon` (`ThreadingMixIn` + `TCPServer`) is too small
  for the observed concurrent connect rate on a busy shared box.

Separately: `_ThreadedSessionDaemon` uses `ThreadingMixIn` with **per-socket** timeout
and byte bounds, but **no aggregate pre-auth worker/admission cap**. Raising
`request_queue_size` alone enlarges DoS admission unless a measured aggregate
pre-auth concurrency bound ships with it (see R7).

Demand condition on the board row is **SATISFIED**. Implementation still needs
**CEO authorization**. This packet only states what a fix must achieve.

---

## 2. Goals

1. **Keep warm-path connects reliable** under the documented concurrent-client envelope
   in §11 (W5B-shaped), without inventing a denial that the probe did not measure.
2. **Make overload honest** when the envelope is exceeded: clients and operators must
   be able to tell "daemon busy / accept budget exceeded" from "daemon absent" and
   from "request failed after accept".
3. **Preserve fail-closed / no-silent-degrade discipline** for anything that changes
   observable routing: cold-path fallback under load must remain intentional and
   attributable (DD-006-HONESTY / Option B).
4. **Bound aggregate pre-auth concurrency** so a larger listen backlog cannot admit
   unbounded unauthenticated worker load (R7).
5. **Ship only after authorization + RED→GREEN + adversarial gate** — this
   requirements doc does not authorize code.

---

## 3. Explicit non-goals

| Non-goal | Why |
|---|---|
| Hard DoS / unauthenticated remote exploit hardening as the primary story | W5B showed soft loopback connect timeouts under concurrency, not remote refuse/drop |
| Raising client connect timeout as the *only* fix | Masks backlog pressure; hides the accept-side bound |
| Kernel sysctl campaigns (`somaxconn`, etc.) as a product dependency | Operators may tune hosts; tg must behave correctly at application `listen` backlog first |
| Rewriting the daemon to asyncio / a new protocol | Out of campaign scope; see decisions doc |
| Auth-packet / token redesign | Separate concern; do not couple |
| Claiming production code already ships this bound | **Zero implementation claims** in this packet |
| Closing DD-006 on docs alone | Board stays open until an authorized implementation lands and is verified |
| Closing the **full** DD-006 row on Option A alone | Full closeout requires DD-006-PERF **and** DD-006-HONESTY (R3/R4) |

---

## 4. Actors

| Actor | Role |
|---|---|
| **CLI / sidecar client** | Connects with `_DAEMON_CONNECT_TIMEOUT_SECONDS`; on connect failure uses cold path via `_probe_daemon` |
| **Session daemon** (`_ThreadedSessionDaemon`) | Loopback TCP acceptor; threads requests after accept |
| **Operator / agent orchestrator** | Starts daemon, reads doctor/diagnostics, interprets warm vs cold routing |
| **CI / dogfood harness** | Replays the §11 concurrency probe after any authorized change |
| **CEO / campaign owner** | Authorizes (or refuses) moving from this draft into build |

---

## 5. Functional requirements

### R1 — Documented concurrent-connect envelope (junior-decidable)

**Requirement:** The accept-side bound design MUST name the concrete concurrent-client
envelope in §11 (client count, connect budget, duration, cadence, host-load validity,
artifact paths, frozen positive-control thresholds). Vague phrases such as "reasonable
local host" or "meets SLA" are **not** acceptance criteria.

**Acceptance criteria:**

- Envelope matches §11 exactly (W5B shape: 20 concurrent clients, 0.5 s connect budget,
  60 s duration under enclosing shell timeout **90 s**) **or** an explicit, justified smaller envelope
  is approved with CEO sign-off and a new frozen control threshold.
- A post-change probe can report PASS/FAIL against that envelope with a positive
  control (A112: frozen thresholds met verbatim or `CANNOT_MEASURE`).
- Host-load validity criteria in §11 are checked before quoting a PASS.

### R2 — Primary lever: raise accept backlog (`request_queue_size`) to measured operating backlog N*

**Requirement:** The preferred product change for **DD-006-PERF** (Option A — see design
+ decisions) is to set an explicit `request_queue_size` on the daemon's `TCPServer`
subclass above the stdlib default of **5**, choosing a **measured operating backlog N\***
(not an arbitrary large constant).

**Definitions (mandatory naming):**

| Term | Meaning |
|---|---|
| **Measured operating backlog N\*** | The **smallest** candidate `N` from the measurement matrix that passes the §11 envelope (0 connect timeouts on ≥2 independent valid runs with a valid positive control). |
| **Hard maximum / enforcement** | Any upper clamp the product enforces (env override max, code constant cap, or OS-documented effective listen cap). Separate from N\*. If no product hard max ships, the design MUST say so and document OS capping behavior. |

**Acceptance criteria:**

- Default backlog of 5 is no longer the undocumented production binding once
  authorized code ships.
- Chosen **N\*** is justified by the §11 matrix; N\* and any hard maximum are recorded
  in the design/decision artifacts under those exact names.
- No claim that raising backlog alone fixes CPU starvation, application-level request
  latency after accept, or residual cold-fallback honesty.
- Raising N\* MUST be paired with R7 (aggregate pre-auth concurrency bound); otherwise
  backlog raise enlarges unauthenticated admission.

### R3 — DD-006-PERF (Option A) — performance disposition

**Requirement:** Option A (raise `request_queue_size` to N\* + R7 aggregate pre-auth
bound) owns the **performance** sub-disposition **DD-006-PERF**.

**Acceptance criteria:**

- Under the §11 envelope, post-change probe reports **0** `connect_timeout` on ≥2
  independent valid runs (or `CANNOT_MEASURE` with reason — never recharacterize the
  frozen control).
- R7 adversarial arms for many silent/slow unauthenticated clients are specified and
  later pass in the authorized build.
- Closing **DD-006-PERF alone** does **not** close the parent DD-006 row.

### R4 — DD-006-HONESTY (Option B) — mandatory for full row closeout

**Requirement:** Option B (honest accept-budget / timeout taxonomy that attributes
residual cold-fallback / overload) owns the **honesty** sub-disposition
**DD-006-HONESTY**. Option B is **mandatory for full DD-006 closeout**. It is not an
optional nice-to-have once Option A lands.

**Full closeout rule:**

| Disposition | What closes it | Closes parent DD-006? |
|---|---|---|
| **DD-006-PERF** | Option A + R7 measured and verified | No |
| **DD-006-HONESTY** | Option B taxonomy live so residual cold-fallback/overload is attributable | No |
| **DD-006 (parent)** | **Both** DD-006-PERF and DD-006-HONESTY | Yes |

Option A alone may close only the separately named **DD-006-PERF** disposition. A
board stamp that says "DD-006 SHIPPED" without DD-006-HONESTY is a disposition error.

**Acceptance criteria:**

- Taxonomy maps to discriminated failure classes the probe already used
  (`connect_timeout` vs `response_timeout` vs `connection_refused` vs
  `auth_rejected`).
- Client-visible or doctor-visible signal does not call a connect-timeout a
  "daemon missing" when the daemon is up.
- If fallback still occurs under overload, diagnostics attribute
  "connect budget exceeded under concurrency" (or equivalent structured reason)
  rather than only "probe returned None".
- Option B alone does **not** close DD-006-PERF (does not reduce connect timeouts
  under the W5B envelope).

### R5 — Compatibility with existing daemon auth and request bounds

**Requirement:** Accept-queue sizing MUST NOT weaken pre-auth read bounds, token
checks, or per-request timeouts already present on the daemon.

**Acceptance criteria:**

- Pre-auth bounded read + timeout discipline remains intact (Security Hardening
  Patterns: `_read_bounded_request_line`, handler `timeout`).
- Backlog increase is not mistaken for a session/request concurrency SLA.
- Aggregate pre-auth cap (R7) sits **in addition to**, not instead of, per-socket bounds.

### R6 — Rollback path

**Requirement:** The authorized change MUST be revertible by restoring the prior
backlog binding, prior aggregate pre-auth cap, and/or prior taxonomy-only behavior
without data-format migration.

**Acceptance criteria:**

- Design names the configuration / attributes to revert.
- No on-disk schema migration is required for Option A / R7.
- Option B additive diagnostic fields, if introduced, document a compatible rollback
  (field absence = pre-honesty behavior).

### R7 — Mandatory aggregate pre-auth concurrency bound

**Requirement:** Before or together with any authorized raise of `request_queue_size`,
the product MUST define and enforce a **measured aggregate pre-auth concurrency
bound**: a hard cap on how many unauthenticated (or not-yet-authenticated) accepted
connections / worker threads may be in flight concurrently.

**Context (current code — contract gap, not a shipped fix):**
`_ThreadedSessionDaemon` uses `ThreadingMixIn` + per-socket timeout/byte bounds, but
has **no** aggregate pre-auth worker/admission cap. Raising `request_queue_size`
enlarges how many completed handshakes can wait for `accept()` and how many
handler threads `ThreadingMixIn` can spawn for silent/slow clients — enlarging
DoS admission unless this bound ships.

**Acceptance criteria (contract for a future authorized build — do not implement here):**

1. **Measured cap value:** choose the aggregate pre-auth concurrency cap from a
   bounded measurement (document candidates and the selected value in the design
   record at build time). Cap MUST be finite and fail-closed.
2. **Fail-closed when exhausted:** when the aggregate pre-auth cap is exhausted, new
   unauthenticated work MUST be refused or dropped in a documented way (no unbounded
   thread growth; no silent infinite queue of pre-auth handlers). Exact refuse
   mechanism is design-time; silence/"keep accepting forever" is forbidden.
3. **Adversarial tests required** (authorized build):
   - Many concurrent **silent** unauthenticated clients (connect, send no line /
     no newline within handler timeout).
   - Many concurrent **slow** unauthenticated clients (drip bytes under the byte
     cap but stall auth).
   - Assert: in-flight pre-auth work never exceeds the cap; excess is fail-closed;
     authenticated traffic under the §11 envelope still meets DD-006-PERF criteria
     when the hostile arm is not the subject under test.
4. **Does not replace** per-socket timeout / byte bounds — layers with them.
5. **Not claimed shipped** by this docs packet.

---

## 6. Non-functional requirements (NFRs)

| ID | NFR | Notes |
|---|---|---|
| NFR-1 | **Measurability** | Every claim about "fixed under concurrency" needs the §11 probe with positive control and discriminated timeout classes |
| NFR-2 | **Local-first** | Fix targets application `listen` backlog + app-level aggregate pre-auth cap; host sysctls are optional operator guidance only |
| NFR-3 | **CPU-safe verification** | Heavy fan-out probes stay bounded per §11; do not saturate the shared desktop beyond the authorized harness |
| NFR-4 | **Cross-platform honesty** | Windows + POSIX loopback behavior both considered; backlog semantics differ by OS but the attribute is portable |
| NFR-5 | **Docs parity** | Board trigger, SESSION_HANDOFF, and this packet stay consistent; no "shipped" wording until merge + dogfood; parent DD-006 requires both sub-dispositions |
| NFR-6 | **Junior-decidable gates** | PASS/FAIL/CANNOT_MEASURE decided from frozen numbers and artifact paths — no judgmental "reasonable" / "SLA" language |

---

## 7. Security

- **Trust boundary:** loopback daemon + local token remains the trust model; raising
  backlog does not expand the network exposure surface by itself.
- **Do not** treat backlog raise as auth. Auth and accept admission are separate.
- **Pre-auth DoS:** larger backlog holds more completed-but-unaccepted connections in
  the kernel accept queue; R7 MUST keep aggregate pre-auth concurrency finite so a
  larger queue cannot become unbounded worker work before auth. Per-socket byte/time
  bounds remain mandatory.
- **Threat to avoid:** documenting "we fixed DoS" when W5B only showed soft
  connect-timeout under load (A113 — claim only what the artifact discriminates).
- **Auth failure after accept:** structured class is **`auth_rejected`** (unauthorized
  after accept), not a vague "refuse class".

---

## 8. Observability

Minimum signals a complete solution should enable or preserve:

1. Ability to distinguish **`connect_timeout`** vs **`response_timeout`** vs
   **`connection_refused`** vs **`auth_rejected`**.
2. Doctor or daemon diagnostic fields (or structured logs) that can show warm-path
   probe outcome reasons under concurrency (exact field names are design-time).
3. Replayable probe artifacts at the §11 paths (`artifacts/dd006_*.json` style) for
   before/after comparison.
4. For DD-006-HONESTY: residual cold-fallback under overload MUST carry an attributable
   reason (not only `_probe_daemon` → `None`).

---

## 9. Rollback

| Step | Action |
|---|---|
| 1 | Revert the authorized PR (or restore prior `request_queue_size`, aggregate pre-auth cap, and/or taxonomy fields) |
| 2 | Re-run the §11 W5B-shaped probe; expect soft timeouts at 0.5 s under 20 clients again if backlog returns to 5 |
| 3 | Update board / handoff dispositions (DD-006-PERF / DD-006-HONESTY / parent); do not leave "SHIPPED" on a reverted change |

No data migration. No PyPI yank expected for a docs-first packet; product rollback
applies only after an authorized implementation ships.

---

## 10. Open assumptions and defaults

| # | Assumption / default | Status |
|---|---|---|
| A1 | Stdlib `TCPServer.request_queue_size` defaults to **5** and is what `_ThreadedSessionDaemon` inherits today | Confirmed on this workstation's Python stdlib; re-verify at build time via symbol inspection |
| A2 | W5B mechanism hypothesis (backlog saturation) is the leading explanation | **Hypothesis** — Option A is the primary test of that hypothesis |
| A3 | Primary performance lever is **Option A** (raise backlog to N\*) | Recorded in decisions (`tt_quick` unanimous); full Claude/Fable council blocked by quota |
| A4 | Implementation awaits **CEO authorization** | Hard gate |
| A5 | N\* will be chosen at build time from §11 matrix candidates `{5, 16, 32, 64, 128}` against somaxconn-aware guidance | Open until authorized measurement |
| A6 | Quiet cold-path fallback under connect timeout is acceptable only if attributable | **Resolved for full close:** DD-006-HONESTY / Option B is mandatory |
| A7 | Aggregate pre-auth concurrency bound is mandatory with any backlog raise | **Resolved in REV-DRAFT-2** (R7 / BLOCKER-1) |

---

## 11. Measurement plan (junior-decidable — future authorized build)

This section replaces vague "reasonable local host" / "meets SLA" language. A junior
engineer must be able to decide PASS / FAIL / `CANNOT_MEASURE` from these numbers alone.

### 11.1 Harness identity

| Item | Value |
|---|---|
| Harness | Scratch / wave probe historically at `.orchestrator/w5/dd006_probe.py` (not product `src/`). Authorized build may promote an equivalent harness under `scripts/` **only if** CEO-authorized; until then treat W5B command shape as normative. |
| Command shape (normative) | `python <probe> --clients 20 --duration 60 --connect-timeout 0.5 --target <closed\|live> [--single-shot] --json` |
| Probe internal duration | **60 s** (`--duration 60`) — measurement window only |
| Shell bound (external) | Hard enclosing timeout **strictly greater than** probe duration: **`timeout 90`** (or platform equivalent) around the probe process |
| Frozen grace period | **30 s** (= shell 90 − duration 60) reserved for process startup, client sync, JSON serialization, and artifact flush. Do **not** set shell timeout equal to `--duration`; Sol REV-DRAFT-2 finding: equal bounds can kill a valid run before the receipt is written |
| Instrument identity (before any number) | `tg --version`; `python -c "import tensor_grep; print(tensor_grep.__file__)"` — record both in the run receipt |

### 11.2 Envelope parameters (frozen)

| Parameter | Frozen value | Notes |
|---|---|---|
| Concurrent clients | **20** | All started, then held (no staggered ramp) |
| Wall duration | **60 s** | Probe `--duration` only; enclose with shell **90 s** (+30 s frozen grace) |
| Connect budget | **0.5 s** (`_DAEMON_CONNECT_TIMEOUT_SECONDS`) | Discriminating live budget |
| Cadence | One bounded daemon ping / connect attempt per client, **looped** for the full 60 s (except control single-shot arm) | Matches W5B |
| Target | `closed` (control) or `live` warm session daemon (measure) | Same client count both arms |

### 11.3 Artifact paths (write these exact names, or record the rename in the receipt)

| Arm | Path |
|---|---|
| Control (looped, closed port) | `artifacts/dd006_control.json` |
| Control (single-shot, closed port) | `artifacts/dd006_control_singleshot.json` |
| Live measure runs | `artifacts/dd006_measure_<n>.json` (n ≥ 2 independent runs) |
| Optional 2.0 s connect contrast | `artifacts/dd006_measure_connect2s.json` |
| Optional single-client contrast | `artifacts/dd006_single_client.json` |
| Post-change GREEN matrix for N\* | `artifacts/dd006_post_N<value>_run<k>.json` |

### 11.4 Frozen positive-control thresholds (A112)

Cite W5B control arm and A112: a plan-frozen control threshold is met **verbatim** or
the arm is **`CANNOT_MEASURE`**. Do **not** recharacterize the frozen number.

| Arm | Frozen expected result | If not met |
|---|---|---|
| **Control single-shot** (plan-frozen) | **`failures == 20`**, **`successes == 0`** against closed port | Entire run set is **`CANNOT_MEASURE`** — quote no live PASS/FAIL |
| Control looped (supplementary) | Every client failed on every attempt (W5B recorded 1600/0); used to corroborate, not to replace the single-shot freeze | If single-shot is valid and looped disagrees wildly, investigate instrument; do not invent a new freeze without CEO amend |

### 11.5 Live PASS / FAIL / CANNOT_MEASURE (post-change DD-006-PERF)

| Verdict | Rule |
|---|---|
| **CANNOT_MEASURE** | Control single-shot ≠ 20/0; probe errors; host-load validity failed (§11.6); undifferentiated timeout class when `connect_timeout` discrimination is required |
| **FAIL (RED)** | Control valid AND ≥1 `connect_timeout` (or undifferentiated timeout at 0.5 s when discrimination unavailable) in a live 20-client / 60 s / 0.5 s arm — expected on pre-change backlog=5 |
| **PASS (GREEN for DD-006-PERF)** | Control valid AND **0** `connect_timeout` on **≥2 independent** live runs at the frozen envelope, with discrimination present in the JSON |

Parent DD-006 still requires DD-006-HONESTY separately.

### 11.6 Host-load validity criteria (replace "reasonable local host")

Before quoting PASS:

1. Record `tg --version` + `tensor_grep.__file__` in the receipt.
2. Record whether other heavy jobs (full pytest, cargo, dense model load) were running.
   If yes → mark run **`CANNOT_MEASURE` (shared-box pollution)** unless the authorized
   plan explicitly accepts polluted runs (default: **do not accept**).
3. Optional observation (not probe JSON): CPU% via host tools. W5B noted 77–100% as
   environment observation only (A113 — not probe data). High CPU alone does not flip
   PASS→FAIL if control is valid and connect timeouts are zero; it **does** block
   claiming "idle-host" performance.
4. Daemon must be the intended warm session daemon (loopback address + token present).

### 11.7 N\* selection matrix

Candidates: `{5 (control), 16, 32, 64, 128}`.

1. Run §11 live envelope at each candidate `N` (≥2 runs each) with valid control.
2. **N\*** = smallest `N` with PASS under §11.5.
3. Record OS effective listen cap if truncated.
4. Refuse cargo-cult `65535` without matrix evidence.
5. Record any **hard maximum / enforcement** separately from N\*.

### 11.8 Test strategy checklist (authorized build — not executed by this packet)

1. **RED first:** replay W5B discriminating arm on pre-change tree; require
   `connect_timeout` under 20 clients @ 0.5 s (or `CANNOT_MEASURE` with reason).
2. **Positive control:** single-shot against closed port must report exactly **20/0**.
3. **GREEN (DD-006-PERF):** same envelope against post-change daemon reports 0 connect
   timeouts on ≥2 runs at N\*.
4. **Form-1 on the guard:** intentionally tiny backlog still RED-fails.
5. **R7 adversarial:** many silent + many slow unauthenticated clients; cap enforced
   fail-closed.
6. **DD-006-HONESTY:** residual overload / cold fallback emits attributable structured
   reason (`connect_timeout` / budget-exceeded class), not only `None`.
7. **Auth/regression:** existing daemon token + bounded-read tests remain green;
   unauthorized-after-accept surfaces as **`auth_rejected`**.
8. **No CliRunner-only clearance** for real-socket routing claims; prefer subprocess /
   real loopback like W5B.

This requirements revision does **not** run those tests and does **not** claim they pass.

---

## 12. Traceability

| Source | What it contributes |
|---|---|
| `docs/TASK_BOARD.md` DD-006 row | Status DEMAND_GATED; demand SATISFIED; reopen needs authorization |
| W5B disposition | Soft severity, connect_timeout discrimination, backlog hypothesis; control 20/0 |
| Design doc | Option A vs B, R7 aggregate pre-auth bound, failure matrix, N\* vs hard max |
| Decisions doc | `tt_quick` Option A; Exa backlog research; CEO auth gate; REV-DRAFT-2 Sol closure |
| A112 | Frozen control thresholds verbatim or CANNOT_MEASURE |
