# Daemon stop recurrence: independent diagnostic PLAN SHIP

Reviewed base: `7ef470fe1880825c32af8e124104e8a90391d14d` in `C:/dev/projects/tg-dogfood-main-verify`. This approves the bounded test-only diagnostic recipe below, not a runtime fix, timeout change, further unchanged-head retry, or release clearance. No tests, CI actions, providers, or repository edits were performed for this review. Token usage unavailable.

## Evidence and unresolved boundary

Main run 37453425025, Windows Python 3.11 job 112236925422, recurs at `tests/unit/test_session_daemon_signed_version.py:124`. Its raw log shows the original cooperative assertion and only truncated `running=True/stopped=False`, then 9998 passed/721 skipped; the preceding signed-old-version positive passed. Current failure duration includes cleanup and does not identify the failed stage. The workflow executes pytest with `--tb=short`; a dictionary assertion message alone therefore cannot supply the missing result. Preserve both this failure and the earlier failed attempt/retry history under DOGFOOD-CI-DAEMON-STOP. No exact Windows runner-container proof exists locally; no runner-only or THIRD STATE classification is justified.

The fixture changes start timeout to exactly 1.0 seconds (`test_session_daemon_signed_version.py:20`), serves with poll interval 0.05 (`:45`), and closes the listener separately after serve_forever exits (`:49`). Stop ACK precedes asynchronous shutdown (`session_daemon.py:1836`). `_stop_unprobed_daemon` separately verifies proof, accepts missing signed-version fields as skew, requests stop, and then requires observed socket refusal (`session_daemon_stop.py:67-89`; `session_daemon_trust.py:1195`). `_await_endpoint_refused` uses a three-second connect timeout even with the one-second deadline, retries non-refusal OSError, and returns true only for ConnectionRefusedError (`session_daemon_trust.py:581`). This makes distinct proof-response, ACK, shutdown, and listener-close observations necessary; none is inferred from overall test duration.

Important correction: fixture metadata `pid=0` does not prevent escalation. The signed proof uses the current process PID (`session_daemon_trust.py:1254`), and the rejected-stop helper replaces metadata PID with proven_pid (`session_daemon_stop.py:120`). The real classifier rejects its own PID (`session_daemon_trust.py:174`), so no valid signal is expected.

## Exact leaf scope

Edit only the signed-version test fixture/module and a focused test-owned helper if needed. Production daemon/stop/trust modules, workflow, import/exception allowances, timeouts, sleeps, poll interval, current proof omission, assertion predicate, and native/release paths remain unchanged. Do not add a startup synchronization barrier to the naturally scheduled original test.

Collect a capped in-memory trace (for example, 128 events) using monotonic timestamps, sequence numbers, and thread identity. Record overflow explicitly; an overflow trace is incomplete evidence. Avoid printing, filesystem writes, locks that wait, or extra network requests during the observed stop. Spies forward the original arguments and returns and re-raise the original exceptions. Install instance server lifecycle spies before starting the fixture thread; retain the real serve_forever/shutdown/server_close implementations.

Observe these actual seams:

- `_daemon_request`: command, begin/end time, configured response/connect timeout, returned ok/stopping flags or exception class/errno/winerror. Record initial-probe and rejected-stop pings separately.
- `_ping_proof_fields`: server entry/exit/error and duration, including the existing missing-version wrapper; only field-presence/type booleans. This distinguishes slow proof generation/secret ACL work from refusal latency without revealing secret material.
- `_probe_daemon` result and verification/version outcomes. Wrap the actual imported `_verify_ping_reply` and `_signed_ping_version` aliases in both `session_daemon` and `session_daemon_stop` where consumed; wrapping only the trust module would miss imported aliases. Record verification boolean, version-ok boolean, missing-version boolean, and expected-version equality.
- `_await_endpoint_refused` begin/end, deadline argument, returned refusal boolean, and every actual `socket.create_connection` attempt to the fixture endpoint: stage, elapsed time, success or exception class/errno/winerror. Endpoint-filter a passthrough socket spy and identify request versus refusal stage without changing calls or socket lifetime. Preserve the exact exception instance and never synthesize refusal.
- Fixture serve entry/return, shutdown entry/return, server_close entry/return/error; distinguish cleanup from the stop observation. Snapshot returned result and thread/listener state before cleanup.
- Retain real PID classification, but replace the actual `session_daemon._terminate_identified` signal seam with a test-only deny sentinel returning false and recording that it was reached. Never call the original signal function, kill an actual PID, or log process argv. This sentinel should be unreachable for this self-PID fixture; assert that fact separately and retain its evidence if unexpectedly reached.

Never log whole requests/replies/metadata, token, nonce, HMAC proof/version_proof, per-user secret bytes, secret path, or exception text/repr. Use explicit primitive projections. The complete public stop result may be serialized as JSON after checking its known output keys; redact/refuse unexpected credential-bearing keys rather than dumping them. Keep proof verification/presence booleans without proof values. Include test node, source identity, Python/platform, configured timeouts and poll interval as trace provenance.

Always emit one clearly prefixed JSON diagnostic line from the main test thread, using `capsys.disabled()` after the original assertion has been evaluated and cleanup attempted. Include the complete pre-cleanup public result and final trace/cleanup state. Nested finally must emit on assertion and cleanup failure, without replacing the original test failure. Successful hosted execution must also leave a visible trace; relying only on pytest failure report_sections would lose that population. No new repository output artifact or workflow edit is needed.

## Bounded local and causal controls

Run the instrumented real subject once locally in the canonical Windows venv under external timeout. This checks trace wiring and secret-free positive capture. A green result does not identify the recurring hosted cause; do not repeat the unchanged scenario seeking a failure or green classification.

Add one separate deterministic fixture control for the ACK-versus-close distinction: retain real signed proof, real stop ACK, real refusal polling and the one-second deadline, but hold only fixture server_close on a test-owned Event. In this control only, the returned stop-ACK observer waits boundedly for close-hold entry before returning ACK to the caller, so refusal polling starts while the listener is deliberately held open. Require real verification/ACK evidence, no observed refusal, unconfirmed stopped=false, and the trace's close-hold event. Release the Event in finally before fixture close/join, with a bounded emergency wait and outer process timeout. Any emergency auto-release or missing handshake invalidates this causal control; it cannot be scored as the intended refusal. The original cooperative test keeps its natural scheduling and unchanged assertion. This control proves the diagnostic distinguishes a valid ACK from observed endpoint refusal; it does not prove CI had this cause.

Also check the diagnostic serializer never emits token/nonce/HMAC/secret sentinels, preserves the complete stop reason, and records an overflow marker when capped. Controls must not accept import/setup crashes as behavioral evidence.

The existing full PR CI on a fresh branch from exact 7ef is the hosted observation surface. Preserve raw job logs and full test order, runner image/interpreter/plugins/env, both signed-version populations and sibling outcomes. A captured recurrence determines the next narrow fix plan; a hosted green remains a capture pass, not proof of cause. Other work remains parked while main is red. Mandatory independent final/security review remains separate for any eventual runtime amendment.

## Raw SHA-256 identities

Method: SHA-256 of current raw worktree/evidence file bytes.

- signed-version test: `c81d12d7324b8f79c4cc158636f69829f8ca2179b126976085fc31a24c531b28`
- session_daemon.py: `99cb3dbac29cf821489c7b4492e07319f5094522afae8895bd6d1a943d082d5e`
- session_daemon_stop.py: `815491e5f6762835dc1baf19ba89c60b265b1e92c8ecee2f7aae50c2c970c2f0`
- session_daemon_trust.py: `c243a9c7669f91262918e1fdc5e4e32e4805cc0720198659bbe2c675a3afbf67`
- main-labels-daemon-recurrence-20261006T114429907057.json: `f88bebc44bed4c9c55f37e8aa14d1d39db58e2c0c42bc63b4c362b8490ee97b8`
- main-labels-daemon-112236925422.log: `fe6e99fc6e5d916f03a36c88c1c9a233d9bb16f263888c9447a9cdb352106f9b`


## Superseding amendment: shutdown-entry hold (PLAN SHIP)

The CLOSE-HOLD causal-control premise is disproved and its earlier approval is superseded. Preserve `daemon-diagnostic-focused-1.log` and `daemon-diagnostic-control-2.log` as FAILED evidence, not successful controls. Their real Windows socket outcomes show that, once the accept loop stops, queued connections can be followed by ConnectionRefusedError while server_close is still held. Increasing the backlog did not repair the control. This does not identify the hosted natural failure's cause.

Approved narrow amendment: replace ONLY the separate control's close hold with a hold at fixture `shutdown` entry, BEFORE calling the real shutdown method. The live serve_forever loop continues accepting sockets throughout the observed refusal poll. Remove the close hold and the `socket.listen(128)` mutation. Retain normal backlog, production calls, real proof/version validation, genuine stop ACK, original timeout/poll values, and the self-PID classification/signal-deny safeguard.

In this separate control only, the client stop-request observer waits boundedly for the shutdown-held Event after the actual ok/stopping ACK and before returning that ACK to the stop caller. Set the Event immediately before waiting for release in the fixture shutdown wrapper. Do not call real shutdown until released. Log hold entry, real-shutdown-call boundary, release/expiry, and ordinary lifecycle events. The original cooperative subject never uses this hold or handshake and keeps natural scheduling; do not rerun that unchanged subject for this correction.

Control proof must require all of: successful real proof verification; accepted missing signed-version fields; genuine ok/stopping ACK; successful shutdown-hold handshake; at least one real successful socket connection in the refusal stage; refusal_poll_end with refused=false; stopped=false with an explicit unconfirmed reason; and no real shutdown call, serve loop exit, or server_close entry before explicit control release. Require no signal-seam invocation and no trace overflow. A handshake timeout or emergency hold expiry invalidates the intended control and cannot be counted as PASS, even if some outcome coincidentally matches.

Use a bounded Event wait for emergency cleanup. Always set release in finally BEFORE daemon.close()/thread.join, including assertion/setup/exception paths. Keep the final visible diagnostic line on success and failure and preserve the original failure if cleanup also fails. This is a fixture scheduling control; it does not widen the real one-second stop deadline or mock socket/refusal outcomes. It demonstrates ACK before shutdown initiation is insufficient for confirmed stop. It makes no claim that the hosted recurrence has that cause. Run only the amended control plus focused serializer/observer controls under an external timeout, then review the immutable implementation before hosted capture. No runtime source change or additional unchanged-head CI retry is approved.

Failed control raw SHA-256 (current evidence bytes):

- daemon-diagnostic-focused-1.log: `0226b08e5ef88a0f8e6b819cdb7dede7ade90affeb46445f4d0e61f463c8ce13`
- daemon-diagnostic-control-2.log: `ce458ef8ecd61e5e66b5ee861d52bbc1b3a651c04bbc250d61f606eb323a6d28`

## Independent implementation review amendment: recorder and cleanup

The independent exact-01a5 reviewer reproduced two diagnostic defects without network activity.
`sol-daemon-review-race-01a5.log` forces interleaving at the actual recorder: two writers can
emit duplicate sequence numbers, and a 127-entry trace can grow to 129 with overflow=false.
`sol-daemon-review-finally-01a5.log` confirms ordinary cleanup errors preserve the assertion,
but an injected snapshot error hides the original assertion and skips cleanup (and the control's
release). These are diagnostic defects, not the cause of the hosted natural stop failure.

Approved correction: preallocate exactly 128 event slots and a `queue.SimpleQueue` containing
their unique IDs. Each writer reserves via `get_nowait`; Empty records overflow. Use the reserved
local ID for both the sequence and its exclusive slot, with no shared increment or len/append
race and no waiting lock. Sequence means reservation order. Snapshot only materialized records;
flag outstanding/unfilled reservations as incomplete rather than complete. Retain the same
primitive projections, event cap, actual timings, forwarding and natural subject schedule.

Put the control's Event release and fixture close in unconditional nested cleanup, even when
snapshotting fails. Independently catch observer snapshot/payload/emission errors, preserve the
original subject failure first, cleanup failure second, diagnostic failure last, and retain a
secondary exception's type in the trace where possible. Observer failures must not skip release
or close, hide a behavioral failure, or become a passing test. Add bounded deterministic controls
for the demonstrated race, pending reservations, and snapshot/payload/emission failure order.
No production, workflow, timeout, polling or original cooperative-predicate change is authorized.
Both original failed review controls remain raw evidence; new exact-artifact review is required.

## Superseding completion boundary amendment

Independent review of exact `77276eb0` proved that a pending reservation is transient until its
writer completes: pre-cleanup assertions can fail even though cleanup produces a complete
trace (`sol-daemon-observer-transient-7727.log`). A second bounded no-network control using
the actual lifecycle wrapper and fixture close proves the same gap at the final gate: close
joins the serve thread but leaves the actual asynchronous shutdown callback unjoined
(`sol-daemon-observer-final-gate-7727.log`). Both controls retain the real recorder and exact
exception/outcome checks; neither identifies the hosted product failure's cause.

Remove both pre-cleanup pending-reservation assertions. Register a completion Event before
each actual shutdown wrapper's first observation, and set it in an outer finally after its
return/error observation. After functional assertion, unconditional control release, and
the existing fixture close/join, wait for only those owned shutdown completions with one
aggregate five-second cleanup budget. No arbitrary sleep, startup barrier, per-event timeout
reset, original stop deadline/poll change, or production change is allowed. Proof observations
finish before ping replies, client spies are synchronous, and serve/close are already joined;
the asynchronous shutdown callback is the specific missing owner. Capture cleanup observation
errors by type and preserve original failure > cleanup failure > observer failure.

Only after owned callback completion evaluate final pending slots and trace completeness.
A delayed callback that completes during bounded cleanup must pass. A completion timeout must
remain incomplete and fail, and a completed callback with a permanently unfilled ticket must
still fail. Pin all three with bounded Event controls; preserve both failed review receipts.
This correction is cleanup of test-owned diagnostic resources, not a relaxation of the real
one-second behavioral stop assertion. Fresh exact-head review and hosted evidence remain required.

The registry owns shutdown wrappers that have entered, with registration preceding their first
observation. Natural successful listener closure and the control's hold handshake establish the
relevant entry. It does not prove that a created but not-yet-entered callback is joined on a failed
natural stop; retain that original failure regardless of secondary late observation. A registered
completion not yet consumed by the cleanup wait makes a snapshot conservatively incomplete.
