# Daemon stop CI recurrence — 2026-10-06

DOGFOOD-CI-DAEMON-STOP is IN_FLIGHT in PR #1218 (PR history: #1218), owned by the Runtime/CI maintainer. It blocks publication
of checkpoint labels, which merged as `7ef470fe1880825c32af8e124104e8a90391d14d` in #1216.
No labels release or root cause is claimed. The four requested product implementations are
complete; this necessary main-CI diagnosis remains separate from the 44 strategic rows.

| Artifact | Actual outcome |
|---|---|
| Labels head `e6f180fe5e5e226979ecbedf281837e8ebc95bdc`, run `37443145599` attempt 1 | Windows Python 3.11 job `112203552092` failed the no-signed-version cooperative stop assertion; same-head Python 3.12 passed all seven module cases |
| Same labels head, attempt 2 | Exactly one failed-job-only diagnostic retry, job `112219288470`, passed; whole run completed 38 jobs with no failing/unfinished jobs |
| Actual labels merge `7ef470fe`, main run `37453425025` | Completed, failure, 38 terminal jobs; Windows Python 3.11 job `112236925422` repeated the same assertion failure; no release ran |
| Same actual merge, Windows Python 3.12 job `112236925517` | Passed all seven signed-version cases; complete lane succeeded |

The failing node is
`tests/unit/test_session_daemon_signed_version.py::test_a_verified_reply_with_no_signed_version_is_treated_as_skewed`.
The latest failed lane reports 1 failed, 9998 passed, 721 skipped, 3 deselected and 2 xfailed.
Its assertion exposes `running=true, stopped=false` but truncates the reason. The immediately
preceding doctored-metadata and different-signed-version cases pass. Both failures used
Python 3.11.15 on Windows Server 2025 image `windows-2025-vs2026` version `20260925.250.1`.
The same-main successful sibling used Python 3.12.13. Raw environment/cache/order observations
and both lane populations are retained in the [recurrence evidence](evidence/2026-10-05-dogfood/daemon-recurrence.json).
Missing cache/env observations do not establish their absence. The exact hosted image is
unavailable locally, so this is not a proved runner-only failure.

The fixture uses a one-second stop-confirmation deadline and a 50 ms serving poll interval.
Production verifies the ping, accepts an absent signed version as old, requests a stop, and
requires actual connection refusal before claiming success. The stop ACK, asynchronous shutdown,
and listener close are distinct events. A delayed proof response, missing ACK, or late refusal
are hypotheses until measured. Fixture metadata PID zero does not itself prevent escalation:
the signed reply contains the current process PID, which the real classifier refuses to signal.

The approved diagnostic plan adds bounded test-owned in-memory timing and primitive outcome
projections, emitted after cleanup on both pass and failure. It preserves the original assertion,
deadlines, polling, natural scheduling, real authentication, and socket refusal calls. It logs no
tokens, nonces, proof bytes, secret paths, whole request/reply payloads or exception messages.
A separate Event-controlled hold before shutdown leaves the real serving loop active and
validates that a real ACK is not refusal proof. The initial listener-close hold was rejected:
after its serving loop exited, real Windows connections were refused despite the held close.
Both failed controls are retained in the [amended plan](plans/2026-10-06-daemon-stop-diagnostic.md).
This is diagnostic validation, not evidence that CI had the same cause. No additional unchanged-head
retry or runtime amendment is authorized by this plan.

The trigger for the next decision is a complete hosted protocol/lifecycle observation. Keep
other work parked while main is red. A runtime/security change needs a separately reviewed plan
and exact-artifact adversarial gate; successful publication and published Windows replay remain
required before labels closes. The prior failed attempts are never overwritten by a later pass.

Python's documented server lifecycle confirms that `shutdown()` waits for the serving loop
from another thread, while `server_close()` closes server resources. This supports separating
the diagnostic events, not a timing fix. [Python 3.11 socketserver documentation](https://docs.python.org/3.11/library/socketserver.html).

Decoding and filename selection already shipped in v1.123.23 from `47700556`, release commit
`a179430a0788bf67654081eac06642c1c64a97d3`. Its release run `37437549841` completed 44 jobs;
33 published Windows cases passed and installed wheel bytes matched the downloaded artifact.
The [dependency publication evidence](evidence/2026-10-05-dogfood/dependencies-publication.json)
preserves that proof separately from the failed labels main run.

## Instrumentation review corrections

Independent review of `01a50960` returned FIX-FIRST on two reproduced diagnostic defects:
concurrent writers could duplicate sequence numbers and exceed the cap without marking overflow;
an injected snapshot error could replace the original assertion and bypass cleanup. The separate
Opus security review cleared that head, but did not supersede those demonstrated findings.

Builder `6ca158d7` corrects both under the amended plan: nonwaiting unique reservations into
128 fixed slots, explicit outstanding/incomplete evidence, and unconditional release/close with
behavioral failure taking precedence over cleanup and observation failures. Bounded controls
cover interleaved reservations and snapshot/payload/emission failures. These correct the
instrument, not the unproved hosted daemon cause. Fresh integrated checks, independent reviews
and exact-head CI remain required; prior verdicts and CI do not clear the new artifact.

A second independent review of `77276eb0` reproduced a completion-boundary defect:
the pre-cleanup assertion can reject a transient pending record, and joining the serving
thread does not finish the asynchronous shutdown observer. Builder `90d253c9` removes the
early completeness assertions and registers completion Events before entered shutdown
wrappers first observe anything. After release and fixture close/join, one aggregate
five-second cleanup budget waits for those entered wrappers before final completeness is
evaluated. It does not change the one-second behavioral stop deadline. A future callback
that has not entered on an already-failed stop is outside that ownership claim.

Bounded controls prove delayed completion passes, completion timeout remains incomplete,
and a completed callback with a permanently unfilled record still fails. The provider's
Opus refresh on `77276eb0` returned HTTP 529 before review and grants no clearance. Both
demonstrated failures and prior verdicts remain evidence. Fresh exact-head reviews and
hosted observation are required for this amended diagnostic; the historical cause remains
unproved.
