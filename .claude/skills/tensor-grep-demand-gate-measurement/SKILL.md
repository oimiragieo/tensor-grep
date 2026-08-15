---
name: tensor-grep-demand-gate-measurement
description: >-
  Use when a DEMAND_GATED board row must be measured before it may reopen: running a bounded,
  plan-frozen measurement that decides whether the row's reopen condition (its Trigger text,
  re-derived verbatim from origin/main) is satisfied. Covers frozen thresholds (client count,
  duration, request shape, and control expectation all fixed BEFORE the run, never chosen from
  the result), the positive-control law (a control arm must hit the frozen threshold VERBATIM or
  the arm is CANNOT_MEASURE - add a single-shot arm that reports the exact number instead of
  recharacterizing the frozen value as illustrative, A112), discriminated failure classes (claim
  only what the raw JSON classifies; split connect vs response timeouts so each classifies
  separately; unrecorded environment readings are observations, not data, A113), CANNOT_MEASURE
  as a first-class outcome, honest soft-severity reporting, and per-row receipt discipline with
  the board update in-body per A71. Proven on DD-006 (2026-08-14). NOT for building the fix -
  this skill only measures whether to reopen; not for demand rows without a measurable trigger.
---

# tensor-grep: demand-gate measurement

Decides ONE question: is a DEMAND_GATED board row's reopen condition satisfied? The method is the
2026-08-14 DD-006 measurement (W5B in `docs/audits/2026-08-13-demand-gated-dispositions.md:51`): a
bounded local probe whose thresholds were frozen before the run, whose control arm hit the frozen
number verbatim, whose claims were narrowed to exactly what each arm's raw JSON classifies, and
whose board update rode the row's Trigger in-body. The long worked example lives in
`references/dd006-worked-example.md`; this file is the runbook.

## When to use / when NOT to use

| Your task | Use |
|---|---|
| A DEMAND_GATED row whose Trigger names a measurable condition (load, timeouts, refusals, drop counts, a demand signal with a shape) | **this skill** |
| Deciding whether the measured evidence reopens the row (REPRODUCED vs CANNOT_MEASURE vs unmet) | **this skill** |
| Building the fix, design, or implementation once the row reopens | `tensor-grep-backlog-campaign` / `tensor-grep-codex-gated-audit-loop` |
| Rows gated on authorization, approval, or a named user appearing | record the unmet condition; no probe |

**DO NOT USE FOR:**

- **Building the fix.** This skill only measures whether to reopen. Once the trigger is satisfied
  and the row reopens, the build belongs to the normal plan/build/gate pipeline, not here.
- **Demand rows without a measurable trigger.** A trigger like "approved scoping pass" or "named
  user with a 100+-pattern workload" is a decision or an appearance, not a measurement. Do not
  fabricate a probe for it; record the condition as unmet (the W5A #255 shape) and stop.

## The workflow

### Step 0 - re-derive the Trigger verbatim from origin/main

The row's Trigger text is the ONLY authority on the reopen condition. Read it from `origin/main`,
not the dirty local tree and not memory: `git show origin/main:docs/TASK_BOARD.md` and read the
row's `Trigger:` field (DD-006's row is `docs/TASK_BOARD.md:88`). Restate the condition in the
plan in the row's own words ("measured concurrent daemon load or denial-of-service evidence"), and
make every threshold below answer THAT condition, not a friendlier paraphrase.

### Step 1 - freeze the plan thresholds before the run

Fix all four BEFORE the first probe fires; never choose any of them from the result:

1. **Client count.** DD-006 froze 20 clients.
2. **Duration.** 60 s under a hard timeout.
3. **Request shape.** One bounded daemon ping request per client, looped; all 20 started then
   held. The arrival schedule belongs to the harness, not to the server's response (the
   coordinated-omission trap - see references).
4. **Control expectation.** The exact number the control arm must report, e.g. `failures == 20`
   for 20 single-shot clients against a closed port. This number is frozen; it is not illustrative.

### Step 2 - the positive control and the verbatim-threshold law (A112)

The probe must be able to report non-zero before any zero is trusted. Two arm shapes:

- **Single-shot arm:** N clients, ONE attempt each, against a freshly-closed loopback port. This
  is the plan-frozen arm; it must report the frozen threshold EXACTLY (DD-006: 20 failures / 0
  successes, verbatim).
- **Looped arm:** N clients looping for the full window against the same closed port. It proves
  every client fails on every attempt (DD-006: 1600/0) but it CANNOT meet a frozen per-client
  number - it is recorded as supplementary, never substituted for the frozen arm.

**The law:** if a looped control reports 1600 where the plan froze `failures == 20`, add a
single-shot arm that reports exactly 20. Recharacterizing the frozen number as "illustrative" is
a plan violation, not a fix. A control arm that cannot hit the frozen threshold is
CANNOT_MEASURE, not valid.

### Step 3 - instrument identity before any number

Record what was measured before believing any number: `tg --version`, the daemon address, token
presence, `package_version`, and that the code being exercised is byte-identical between the
running tree and `origin/main` (DD-006: venv `session_daemon.py` / `session_store.py`
byte-identical to `origin/main`). A number measured against a stale or foreign tree answers a
different question.

### Step 4 - discriminated failure classes (A113)

Claim only what the raw artifact classifies. An undifferentiated `TimeoutError` token cannot be
upgraded to `connect_timeout` in prose. If the plan needs connect vs response as separate classes,
SPLIT the probe so each failure kind is recorded separately, and re-run the affected arms on the
discriminating build. DD-006 ran arms 1-4 undifferentiated, then split; the durable claim became
"5/5 arms showed timeouts at the 0.5s budget, and the one discriminated arm classified them
connect-timeout" - not "5/5 arms showed connect-timeouts".

Environment readings the harness did not record are observations, not data. DD-006's box load
(77-100% CPU, `Get-CimInstance`) was recorded in the doc as an observation about the measurement
environment, explicitly not as a probe field.

### Step 5 - CANNOT_MEASURE is a first-class outcome

Instrument-error, control-invalid, and undiscriminated arms all land in CANNOT_MEASURE. It is
never silently folded into a null result and never into a finding. If the control cannot hit the
frozen threshold (Step 2), if the fixture fails to bite, or if the raw JSON cannot classify the
claim the plan needs, report CANNOT_MEASURE with the reason - and fix the instrument and re-run
(the DD-006 instrument was corrected mid-wave; the corrected arms are what decided the row).

### Step 6 - honest severity

State the reproduced failure at its true severity. Reproduced-but-soft (latency degradation at
the client's own budget, zero refusals, zero drops) is reported as SOFT, with the mechanism
stated as a hypothesis, never inflated to a denial. DD-006: the daemon never refused or dropped
a request; every classified timeout was a slow accept (>0.5s) at the CLI's own
`_DAEMON_CONNECT_TIMEOUT_SECONDS` budget under 20 clients on a loaded box; the mechanism
hypothesis is default `request_queue_size=5` accept backlog. A soft result can still satisfy a
demand trigger - say so, at soft severity.

### Step 7 - receipt discipline and the board update

- **Raw JSON artifacts named.** `artifacts/dd006_control.json`,
  `artifacts/dd006_control_singleshot.json`, `artifacts/dd006_measure_1.json` through
  `artifacts/dd006_measure_6_connect2s.json`, `artifacts/dd006_single_client.json`. The probe
  harness itself is scratch-only (`.orchestrator/w5/dd006_probe.py`), never committed under
  `src/`.
- **Per-row receipt tables.** One command + one recorded output per row; no group sentences
  ("six rows, six commands, six results" is a claim - A115).
- **The board update rides the Trigger in-body (A71).** The reproduction is written INTO the
  row's `Trigger:` field text (e.g. "DEMAND CONDITION NOW SATISFIED by ... see ... W5B"), and the
  new reopen gate goes in the same field. No free-form bullets under the canonical-status
  heading. The Status field changes only where the disposition actually flips; DD-006 stayed
  DEMAND_GATED with the reproduction in the Trigger.

## Worked example: DD-006 (2026-08-14, condensed)

Frozen: 20 clients x 60s, one ping per client, control `failures == 20`. Control: single-shot
20/0 verbatim (the frozen arm); looped 1600/0 (supplementary). Live, 5/5 multi-client arms timed
out at the CLI's own 0.5s connect budget; the one discriminated arm classified all 64 failures
`connect_timeout`; 0 refusals/drops everywhere; 0 failures at a 2.0s budget and with a single
client. Verdict: REPRODUCED at SOFT severity (accept-path latency under concurrency, mechanism
hypothesis `request_queue_size=5`). Board: demand condition SATISFIED, reproduction rides the
Trigger, row stays open awaiting accept-side bound design authorization. Full run matrix,
control table, gate resolution, and the codex closure in
`references/dd006-worked-example.md`.

## References

External anchors fetched 2026-08-14 (full receipts in the session's Exa research file; the
supporting literature, NOT the source of the rules - no external source states the
verbatim-frozen-threshold rule in these words):

- TCP accept-queue mechanics (why the DD-006 symptom is exactly client-side timeouts with zero
  refusals): `https://veithen.io/2014/01/01/how-tcp-backlog-works-in-linux.html` (2014-01-01);
  `https://blog.cloudflare.com/syn-packet-handling-in-the-wild/` (2018-01-15);
  `https://oneuptime.com/blog/post/2026-03-20-configure-tcp-backlog-queue/view` (2026-03-20);
  `https://www.netdata.cloud/guides/nginx/nginx-listen-queue-overflow/` (fetched 2026-08-14);
  `https://docs.python.org/3/library/socketserver.html` (Python 3.14.7 docs - the
  `request_queue_size` authority); `https://bugs.python.org/issue36003` (2019-02-15 - the
  "default 5 is arbitrary" upstream discussion).
- Measurement honesty: Grafana k6 baseline loop
  `https://grafana.com/docs/learning-hub/k6-performance-testing/03-establishing-a-baseline/15-what-is-a-baseline/`
  (fetched 2026-08-14); `https://semicolony.dev/codex/performance/methods/load-testing/`
  (2026-05-17); coordinated omission `https://k6.wiki/k6-coordinated-omission-open-vs-closed-workloads`
  (2026-05-24); open-loop generator `https://github.com/giltene/wrk2`; chaos-engineering
  controlled scenarios `https://principlesofchaos.org/` (2019-03) and
  `https://docs.aws.amazon.com/prescriptive-guidance/latest/chaos-engineering-on-aws/lifecycle.html`.
- Demand-gating as practiced: `https://www.senseandrespond.co/blog/instrumentation-as-feature`
  (2026-03-12); `https://www.koji.so/docs/fake-door-testing-guide`;
  `https://dowhatmatter.com/guides/fake-door-test` (2026-03-27);
  `https://www.rocket.new/blog/fake-door-testing-validate-demand-before-you-build` (2026-07-29);
  `https://www.reforge.com/guides/prepare-instrumentation-for-new-product-launch` (2024-02-16);
  `https://mixpanel.com/blog/feature-flag-mistakes/` (2026-06-09);
  `https://www.datadoghq.com/knowledge-center/feature-flags/best-practices-ai-teams/` (2026-07-22).
