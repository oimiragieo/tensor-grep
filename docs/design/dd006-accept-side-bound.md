# DD-006 design: accept-side bound for the session daemon

| Field | Value |
|---|---|
| Row | **DD-006** |
| Artifact kind | Design (Phase-1 draft) |
| Revision id | **REV-DRAFT-3** (pair with requirements) |
| Status | **DRAFT** — no product code in this packet |
| Requirements | `docs/requirements/dd006-accept-side-bound.md` |
| Decisions | `docs/decisions/dd006-campaign-scope-2026-08-14.md` |
| Evidence | W5B in `docs/audits/2026-08-13-demand-gated-dispositions.md` |
| Linked sub-dispositions | **DD-006-PERF** (Option A + R7), **DD-006-HONESTY** (Option B) |

### Content-hash instruction

Hash the committed blob (or designated worktree bytes) before audit seats stamp approval:

```powershell
git hash-object docs/design/dd006-accept-side-bound.md
```

Record method + SHA beside the revision id (A46).

---

## 1. Context (what exists today — cite symbols, not “already fixed”)

Relevant production symbols (verify at build time with `tg defs` / search; do not trust
stale line numbers):

| Symbol | Role |
|---|---|
| `_ThreadedSessionDaemon` | `socketserver.ThreadingMixIn` + `TCPServer` daemon acceptor |
| `allow_reuse_address` | Set on the daemon server class (reuse binding) |
| `_DAEMON_CONNECT_TIMEOUT_SECONDS` | Client connect budget (**0.5** today) |
| `_probe_daemon` | Connect/probe helper; failure → cold-path fallback |
| `_read_bounded_request_line` | Pre-auth byte-capped readline |
| `_SessionDaemonHandler.timeout` | Per-socket pre-auth/idle timeout |

Python stdlib behavior (confirmed on this workstation):

- `socketserver.TCPServer.request_queue_size` defaults to **5**.
- `TCPServer.server_activate` calls `self.socket.listen(self.request_queue_size)`.
- `ThreadingMixIn` spawns a thread per accepted connection — **no aggregate pre-auth
  worker/admission cap** exists on `_ThreadedSessionDaemon` today.

No explicit `request_queue_size = …` override was required for W5B's hypothesis: the
default backlog is the load-bearing number.

W5B summary (soft, not hard DoS):

- Under 20 concurrent clients, connects miss the 0.5 s budget → `connect_timeout`.
- 0 refusals/drops; 2.0 s budget and single-client arms were clean.
- Control single-shot: **20 failures / 0 successes** (A112 frozen threshold).
- User-visible effect: silent warm→cold fallback via `_probe_daemon` → `None`.

---

## 2. Architecture options

### Option A — Raise `request_queue_size` to measured operating backlog N\* (+ R7) — DD-006-PERF

**Idea:** On `_ThreadedSessionDaemon` (or the concrete `TCPServer` subclass the daemon
constructs), set `request_queue_size = N` where **N > 5**, and choose **N\*** from the
requirements §11 measurement matrix rather than guessing `65535`. **Mandatory companion:**
aggregate pre-auth concurrency bound (requirements R7 / §3 below).

**What it changes:**

- Kernel accept-queue depth requested at `listen()`.
- Aggregate cap on concurrent not-yet-authenticated handler work (R7).

**What it does not change:**

- Per-request CPU cost after accept.
- Auth / token checks (unauthorized-after-accept remains **`auth_rejected`**).
- Client `_DAEMON_CONNECT_TIMEOUT_SECONDS` (unless a later authorized change says so).
- Host `net.core.somaxconn` (OS may still cap `N`).
- Residual cold-fallback **honesty** (that is Option B / DD-006-HONESTY).

**Measured operating backlog N\* (planned method — not run as part of this draft):**

1. Replay requirements §11 discriminating arm (20 clients, 0.5 s connect, 60 s) at
   candidate `N` ∈ `{5 (control), 16, 32, 64, 128}`.
2. **N\*** = smallest `N` that yields 0 connect timeouts on ≥2 independent runs with
   a valid positive control (`failures == 20` / `successes == 0` on single-shot closed
   port — A112).
3. Document OS cap behavior if `N` is silently truncated (Linux `somaxconn`).
4. Refuse cargo-cult `65535` without matrix evidence.

**Hard maximum / enforcement (separate from N\*):**

| Concept | Definition |
|---|---|
| **N\*** | Smallest matrix `N` that passes the §11 envelope |
| **Hard maximum / enforcement** | Any product-enforced upper clamp (env max, code constant, documented refuse-above-X). If none ships, say so explicitly and document OS truncation only |

**Pros**

- Directly tests the W5B backlog hypothesis.
- Tiny surface area for the backlog attribute; easy rollback.
- Matches industry guidance: application backlog and kernel cap must both be sane
  (FRR mgmtd raised listen backlog toward `SOMAXCONN` under fan-in; Drozd 2026
  documents accept-queue overflow presenting as client timeouts).

**Cons**

- Does not fix true accept starvation (blocked accept loop / CPU throttling).
- Larger queue without R7 enlarges pre-auth DoS admission — **forbidden without R7**.
- Alone, does not make residual cold-fallback attributable (needs Option B).

### Option B — Honest accept-budget / timeout taxonomy — DD-006-HONESTY (MANDATORY for full close)

**Idea:** Make overload and residual cold-fallback **visible and attributable**:

- Discriminate connect vs response timeouts in client/doctor surfaces (W5B probe
  already needed this split).
- Surface a structured reason when `_probe_daemon` fails under load
  (“connect budget exceeded” / `connect_timeout`) instead of an undifferentiated miss.
- Document operator-facing accept/connect budget notes in contracts / doctor text as
  needed.

**Pros**

- Improves diagnosis even when backlog is raised.
- Prevents “daemon missing” misreads.
- Required so residual overload after N\* is not a silent warm→cold lie.

**Cons**

- Alone, does not reduce connect timeouts under the W5B envelope.
- Easy to over-claim “fixed DD-006” while only renaming failures — mitigated by the
  split disposition rule (cannot close parent without both).

### Option C — Raise client connect timeout only (REJECTED as primary)

**Why rejected:** Treats the symptom in the client, leaves accept-queue depth at 5,
and trains operators to “just wait longer.” May be considered later as a **paired**
knob after Option A measurement, never as the sole closeout.

### Option D — New async server / protocol rewrite (OUT OF SCOPE)

Deferred. Wrong size for a soft local concurrency finding.

---

## 3. Aggregate pre-auth concurrency bound (BLOCKER-1 — mandatory design)

### 3.1 Gap

Today:

- Per-socket: handler `timeout` + `_read_bounded_request_line` byte cap.
- Missing: **aggregate** cap on concurrent pre-auth accepted connections / worker
  threads.

`ThreadingMixIn` will create a thread per accept. Raising `request_queue_size`
increases how many sockets can complete handshake and wait for / enter handlers
before auth — enlarging DoS admission if unbounded.

### 3.2 Contract (specify only — do not implement in this worktree)

| Element | Requirement |
|---|---|
| Cap | Finite, measured aggregate pre-auth concurrency limit `P` (name recorded at build) |
| Exhaustion | **Fail-closed**: refuse/drop/document-reject excess unauthenticated work; no unbounded thread growth |
| Layering | Does not remove per-socket timeout/byte bounds |
| Coupling | Must ship with any authorized backlog raise (Option A / DD-006-PERF) |
| Tests | Adversarial arms: many **silent** unauthenticated clients; many **slow** unauthenticated clients; assert in-flight pre-auth ≤ `P` and excess fail-closed |

Exact refuse primitive (close socket, send error line, stop accepting temporarily)
is chosen at authorized build time; “keep accepting forever” is not a valid choice.

### 3.3 Relationship to listen backlog

```
listen(N*)  -- admits waiting established connections
     |
     v
accept() + ThreadingMixIn -- must respect aggregate pre-auth cap P
     |
     v
per-socket timeout + byte-bounded readline -- then auth
     |
     +--> auth ok --> request handling
     +--> auth fail --> auth_rejected
```

N\* without P is incomplete DD-006-PERF design.

---

## 4. Recommendation

**DD-006-PERF primary build path:** Option A (raise `request_queue_size` to **N\***)
**plus** §3 aggregate pre-auth bound (R7).

**DD-006-HONESTY (mandatory for full parent closeout):** Option B honesty taxonomy for
residual cold-fallback / overload attribution.

**Full DD-006 row close requires both sub-dispositions.** Option A alone may close only
**DD-006-PERF**. Option B alone may close only **DD-006-HONESTY**. Neither alone closes
the parent row.

Rationale:

1. W5B's discriminating evidence is **connect_timeout under concurrency** with
   **zero refusals** — the shape of accept-queue pressure, not application refuse.
2. Stdlib default backlog **5** is far below the 20-client fan-in used in the probe.
3. `tt_quick` (codex + agy) unanimously selected Option A as the performance lever
   (see decisions doc).
4. Sol REV-DRAFT-1 review (BLOCKER-2): residual cold-fallback must be attributable for
   full close — Option B is mandatory, not optional.
5. Sol BLOCKER-1: backlog raise without aggregate pre-auth cap enlarges DoS admission.
6. Full Claude/Fable council was quota-blocked; this draft does not invent a second
   unanimous full-council stamp.

**Hard gate:** no implementation until CEO authorization.

---

## 5. Trust boundaries

```
[CLI / agent process]
        |  loopback TCP + token
        v
[Kernel SYN queue] --> [Kernel accept queue (listen backlog N*)] --> [accept()]
        |                                                      |
        |                                                      v
        |                              [aggregate pre-auth cap P — fail-closed]
        |                                                      |
        |                                                      v
        |                              [_ThreadedSessionDaemon worker thread]
        |                                                      |
        |                                                      v
        |                              [pre-auth bounded read + auth + request]
        |                                                      |
        |                              auth fail --> auth_rejected
        v
  connect timeout at client
  -> _probe_daemon returns None
  -> cold path (must become attributable under DD-006-HONESTY)
```

| Boundary | Rule |
|---|---|
| Loopback only | Do not expand bind address as part of DD-006 |
| Token auth | Unchanged; backlog ≠ authorization |
| Pre-auth read | Keep byte cap + timeout before auth (existing hardening) |
| Aggregate pre-auth | Cap `P` fail-closed when exhausted (§3) |
| Client budget | `_DAEMON_CONNECT_TIMEOUT_SECONDS` remains a client admission deadline |

---

## 6. Failure matrix (desired honesty)

| Condition | Today (observed / hypothesized) | Desired after authorized A+R7+B |
|---|---|---|
| Daemon down | Connect fails → cold path | Unchanged |
| Light load, daemon up | Warm path | Unchanged |
| 20-client burst @ 0.5 s, backlog=5 | Soft `connect_timeout` → cold path | **Should clear** if measured `N*` is adequate (DD-006-PERF) |
| Burst beyond measured envelope | Likely timeouts / cold path | Timeouts OK if **labeled** (`connect_timeout` / budget-exceeded); no silent “all good, actually cold” lie (DD-006-HONESTY) |
| Auth failure after accept | Existing unauthorized path | Structured **`auth_rejected`** (not “refuse class”) |
| Aggregate pre-auth cap exhausted | N/A (no cap today) | Fail-closed; documented class; no unbounded threads |
| Slow handler after accept | Response latency / response timeout | Not solved by backlog; do not claim otherwise |
| Host caps backlog below `N*` | Possible silent OS cap | Design/docs call out verification via `ss` / platform tools where available |

**No silent degrade:** do not add a path that reports warm-daemon success while
executing cold semantics. Do not widen flags that swap engines without a visible
reason field.

---

## 7. Compatibility

| Surface | Impact of Option A + R7 |
|---|---|
| CLI warm/cold routing | Fewer false cold fallbacks under concurrency; semantics of fallback unchanged until Option B attributes them |
| MCP / other clients using the same daemon | Same benefit if they share the acceptor |
| On-disk session state | None |
| Wire protocol / JSON schemas | None for Option A alone |
| Option B taxonomy fields | Additive diagnostics; bump any contract version that embeds new fields |

---

## 8. Migration and rollback

**Migration:** none for backlog + aggregate cap. Attribute / guard changes on server
class / constructor / accept path.

**Rollback:**

1. Revert the PR or restore prior `request_queue_size` (implicit 5), remove aggregate
   pre-auth cap, and/or drop taxonomy fields.
2. Re-run requirements §11 probe; soft timeouts at 0.5 s under 20 clients are the
   expected regression signal if backlog returns to 5.
3. Leave board language honest (not SHIPPED if reverted); clear DD-006-PERF /
   DD-006-HONESTY independently if only one half reverts.

**Feature flag:** optional env override for backlog size / cap `P` is allowed in a
future build plan if it helps dogfood, but must default to measured production values,
must not exceed the documented hard maximum, and must not fail open to unbounded work
before auth.

---

## 9. Security notes (design-time)

- Larger accept queues increase the number of completed handshakes waiting for
  `accept()`. Pair with R7 aggregate pre-auth cap **and** existing per-socket pre-auth
  bounds so queued sockets cannot force unbounded work.
- Do not advertise DD-006 as “remote DoS closed”; W5B was local soft latency.
- Raising backlog is **not** a substitute for rate limits if a future threat model
  expands beyond loopback.
- Unauthorized-after-accept → **`auth_rejected`**.

---

## 10. Observability sketch (planned, not shipped)

Minimum useful additions for Option B / DD-006-HONESTY:

- Probe/doctor reason enum including `connect_timeout`, `response_timeout`,
  `connection_refused`, `auth_rejected`.
- Structured reason when warm probe falls back under load (budget exceeded), never
  only undifferentiated `None` for residual cases.
- Optional counter or log line when accept-queue wait is suspected (platform-dependent;
  may be best-effort).
- Optional signal when aggregate pre-auth cap `P` rejects excess clients.

Option A + R7 closes **DD-006-PERF** if the matrix shows zero connect timeouts under
the written envelope. Option B closes **DD-006-HONESTY**. Parent DD-006 needs both.

---

## 11. Measurement plan (pointer — normative text in requirements §11)

Do not use “reasonable local host” or “meets SLA.” Use requirements §11:

- Harness/command shape, 20 clients / 60 s probe duration / 0.5 s connect, **shell timeout 90 s** with **frozen 30 s grace** (shell must strictly exceed `--duration`), artifact paths
- Frozen control: single-shot **20/0** (W5B; A112)
- Host-load validity criteria
- N\* matrix and hard-maximum separation
- R7 adversarial silent/slow unauthenticated client arms

---

## 12. Implementation sketch (FORBIDDEN in this worktree — for later authorized PR)

Planned shape only (do **not** implement here):

1. Set `request_queue_size = N*` on `_ThreadedSessionDaemon` (or equivalent) with
   comment citing W5B + measured operating backlog N\* (+ hard max if any).
2. Implement aggregate pre-auth concurrency cap `P` with fail-closed exhaustion.
3. Add / extend concurrency probe + R7 adversarial tests (RED on backlog=5; GREEN on
   N\*; silent/slow unauth arms).
4. Ship Option B taxonomy / attribution fields (DD-006-HONESTY) — same PR or explicitly
   linked follow-up; parent row stays open until both land.
5. Docs: update board trigger disposition only in the implementation/closure PR(s).
6. Adversarial gate (A3) before merge — security-touching daemon acceptor.

This design draft stops before step 1.

---

## 13. Open design questions (resolved only after CEO auth + measurement)

1. Exact `N*` from the candidate matrix.
2. Exact aggregate pre-auth cap `P` and refuse primitive on exhaustion.
3. Whether any product **hard maximum** ships above N\*, or OS cap only.
4. Whether to pair a small client-timeout bump after backlog raise (secondary).
5. Whether Option B doctor fields ship in the same PR as Option A+R7 or a linked
   follow-up (parent close waits for both either way).
6. Whether Windows listen backlog semantics need a separate envelope number.
