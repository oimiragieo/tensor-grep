# DD-006 worked example - the bounded demand-gate measurement (2026-08-14)

The complete worked example behind `tensor-grep-demand-gate-measurement`. Source of truth:
`docs/audits/2026-08-13-demand-gated-dispositions.md` W5B (`docs/audits/2026-08-13-demand-gated-dispositions.md:51`)
and the DD-006 board row (`docs/TASK_BOARD.md:88`). Base: `origin/main` `a1c51ee` (v1.110.16).
This wave wrote no product code; the probe is a scratch measurement harness whose OUTPUT is
recorded, never committed under `src/`.

## The row and its reopen condition

Row DD-006, Status DEMAND_GATED, no PR. Trigger (verbatim from origin/main): "measured concurrent
daemon load or denial-of-service evidence". The reopen condition is therefore: show, with a real
measurement, that the session daemon degrades under concurrent load. Everything below answers
that trigger and nothing else.

## Parameters frozen before the run (Step 1)

- 20 clients, 60 s wall under a hard timeout, one bounded daemon ping request per client looped,
  all 20 started then held.
- Control expectation frozen at `failures == 20` (the number that later became the codex C-01
  finding's subject - see closure below).
- Instrument identity recorded before any number was believed: `tg --version` ->
  `tensor-grep 1.110.0` (venv); `session_daemon.py` / `session_store.py` byte-identical between
  the venv tree and `origin/main`; daemon `127.0.0.1:61335`; token present;
  `package_version 1.110.0`.

## Positive control (Step 2, A112)

Two arms, recorded separately - the single-shot arm was ADDED during the codex audit of the wave
(the looped arm reported 1600 where the plan froze 20):

| Arm | Shape | Failures | Successes | Verdict |
|---|---|---|---|---|
| looped | 20 clients looping the full 60s against a freshly-closed loopback port | 1600 | 0 | supplementary |
| **single-shot** | 20 clients, ONE attempt each, against a freshly-closed loopback port (`--single-shot`) | **20** | 0 | **the plan-frozen arm: `failures == 20` holds verbatim** |

Control VALID under the frozen threshold. Raw evidence: `artifacts/dd006_control_singleshot.json`
and `artifacts/dd006_control.json`.

## Live arms against the warm session daemon (Step 4, A113)

Run matrix (each 60 s, 20 clients, recorded raw in `artifacts/dd006_*.json`). Classification
note: arms 1-4 ran the pre-discrimination probe, whose only failure token was undifferentiated
`TimeoutError`; the probe was then split so connect and response timeouts classify separately,
and arms 5-6 plus the single-client arm ran the discriminating build:

| Run | Connect budget | Response budget | Failures | Successes | Failure kinds (as recorded in the raw JSON) |
|---|---|---|---|---|---|
| measure_1 | 0.5 s | 2 s | 54 | 4537 | 54 `TimeoutError` (undifferentiated) |
| measure_2 | 0.5 s | 2 s | 36 | 4633 | 36 `TimeoutError` (undifferentiated) |
| measure_3 | 0.5 s | 2 s | 37 | 4601 | 37 `TimeoutError` (undifferentiated) |
| measure_4 | 0.5 s | 60 s | 25 | 4666 | 25 `TimeoutError` (undifferentiated) |
| measure_5 | 0.5 s | 60 s | 64 | 4530 | 64 `connect_timeout` (discriminated) |
| measure_6 | 2.0 s | 60 s | **0** | 4618 | none |
| single_client | 0.5 s | 60 s | **0** | 238 | none |

What the raw JSON supports, stated at its exact strength:

- Every multi-client arm (5 of 5) reported >=1 timed-out client at the CLI's own 0.5s connect
  budget. Zero refusals, zero drops in every arm.
- The ONE discriminated arm (measure_5) classifies every one of its 64 failures as
  `connect_timeout`. The other four arms cannot be split into connect vs response from their
  JSON - so the claim "5/5 arms showed connect-timeouts" is NOT made; the durable claim is
  "5/5 arms showed timeouts at the 0.5s budget, and the discriminated arm showed the class is
  connect-timeout."
- "Zero response timeouts" is claimed ONLY for the arms whose raw JSON can prove it
  (measure_5, measure_6, single_client). For arms 1-4 the split is unrecorded and is stated
  as such.
- Box load was 77-100% CPU during the multi-client arms (session observation via
  Get-CimInstance, not stored in the probe JSON) - recorded in the doc as an observation about
  the measurement environment, not as data.

## GATE-W5B-1 resolution (Steps 5-6)

Threshold check: ">=1 refused/dropped/timed-out client in at least 2 of 2 independent runs" ->
satisfied in 5 of 5 runs at the CLI's own connect budget, with the control arm valid under the
frozen threshold (single-shot: failures == 20).

**Outcome: REPRODUCED** (per the plan-frozen thresholds, control-valid) - the failure is SOFT,
and the evidence is recorded at its true severity, not inflated:

- The daemon never refused or dropped a request. Every classified timeout was a slow accept
  (>0.5 s) at the CLI's own `_DAEMON_CONNECT_TIMEOUT_SECONDS` budget, under 20 concurrent
  clients on a 77-100%-loaded box.
- With a 2.0 s connect budget the failure rate is 0; with a single client it is 0. The
  degradation is load-dependent accept-path latency (default `request_queue_size=5` backlog +
  a busy shared box), not a denial.
- Mechanism hypothesis (stated as such): 20 clients x ~4 connects/s against a `ThreadingMixIn`
  `TCPServer` with the stdlib default accept backlog of 5 saturates the accept queue; connects
  that cannot grab a backlog slot within 0.5 s time out. A CLI client that times out on connect
  falls back to the cold path by design (`_probe_daemon` -> None -> cold path), so the user
  impact is a silent cold-path fallback under concurrency, not an outage.

## The board update (Step 7, A71)

Per the plan's re-approval rule, the reproduction is filed on the DD-006 row itself: its demand
condition ("measured concurrent daemon load ... evidence") is now SATISFIED by this measurement,
so the row reopens. The reproduction and the new reopen gate (authorization for a bounded
accept-side bound design - queue-size raise or accept-budget note) are written INTO the row's
`Trigger:` field text. The Status field stays DEMAND_GATED; no free-form bullets were added
under the canonical-status heading. Raw artifacts named in the wave:
`artifacts/dd006_control.json`, `artifacts/dd006_control_singleshot.json`,
`artifacts/dd006_measure_1.json` through `artifacts/dd006_measure_6_connect2s.json`,
`artifacts/dd006_single_client.json` (probe harness scratch-only:
`.orchestrator/w5/dd006_probe.py`).

## Codex-audit closure against this wave (why the workflow has these steps)

The independent codex audit returned REVISE with five findings; each was verified against the
raw evidence and closed as follows (`docs/audits/2026-08-13-demand-gated-dispositions.md:231`):

- **C-01 (control threshold):** the plan froze `failures == 20` for the control; the looped
  control arm reported 1600. CLOSED by adding the single-shot control arm, which reports exactly
  20/0 against the same closed target - the frozen threshold now holds verbatim on the arm
  recorded as such, and the looped arm is recorded as supplementary rather than recharacterized.
  This is the A112 receipt.
- **H-01 (undifferentiated timeouts):** CLOSED by narrowing the durable claim to what each arm's
  raw JSON can prove (5/5 arms showed timeouts at 0.5s; the discriminated arm classified them
  connect-timeout; response-timeout absence claimed only where recorded). This is the A113
  receipt.
- **C-02 (W6 receipts):** CLOSED by the per-row W6 table - one command and one recorded output
  per row (A115).
- **C-03 (A101 3x):** CLOSED by recording the recurrence count in the 2026-08-14 BACKLOG entry.
- **H-02 (untracked plan):** CLOSED by committing the approved plan and spec bytes into this
  branch so the cited paths exist on the merged tree. Format note, recorded for hash discipline
  (A46): the plan was ruff-preview-normalized at commit time and its ASCII-census note and one
  trailing space corrected after the second codex round (L-01/L-02) - no semantic content
  changed anywhere. Hash chain: audited round-1 bytes SHA-256
  `4A4841AC8431549FA6DDEDA208C6C722563CBA954EC5F217472C540F3EC36FEE` (pre-format witness
  preserved in the parent checkout); round-2 bytes
  `E073670B50FBFA735CFDA46A37BA32E5E83C95BBBDA6A04EE1889A4D3EBE847E`; round-3 bytes
  `3649D711D54825342787CE6D2E4778B9CE23A27F88806DFC97CD94B50717B61D` (census-note correction +
  trailing-space removal only); round-4 bytes (after the census location inventory was
  corrected again, L-03) `BE1C85DDCB3BC598CF2A5D2DC38A6B7AD980DA97D6938A4B1695A089C60BF6EE`.

## The DD-006 takeaways, one line each

1. The Trigger text is the reopen condition; every threshold answers it verbatim.
2. Freeze the control expectation; a looped arm that overshoots it is supplementary, and a
   single-shot arm that hits it exactly is the valid control.
3. Split the probe so connect and response timeouts classify separately; the raw JSON is the
   only authority on what a claim may say.
4. Unrecorded environment readings are observations, not data.
5. Soft is a valid severity; report it soft, with the mechanism as a hypothesis.
6. The board update rides the Trigger in-body; the Status field flips only where the
   disposition flips.
