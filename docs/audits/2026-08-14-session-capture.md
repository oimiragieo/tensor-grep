# Session capture (2026-08-14, W5-W8 closeout session)

Dated receipt for everything learned in the 2026-08-14 session, written so a junior analyst
can pick it up without the orchestrator's context. Sources: the session-capture ledger
(`.orchestrator/w6/retention-ledger.md`, orchestrator scratch, the verified ground truth),
the demand-gated disposition receipt (`docs/audits/2026-08-13-demand-gated-dispositions.md`,
the authority for the DD-006 numbers - all figures below were cross-checked against it), and
the merged artifact itself (`origin/main` `e1a2b61`). No product code, no release, no spend
this session; it is a docs/closeout campaign.

## 1. What shipped this session

- PR #1013 (`docs: W5-W8 closeout ...`) merged as `e1a2b61` (2026-08-14). Main CI run
  `31846488603` success. Non-releasing (`docs:` title) - no version was cut and none was
  expected.
- Board index stays `2026-08-13.1`; the closed world is 29 rows with this bucket table:

| Status       | Rows                                                                                |
|--------------|-------------------------------------------------------------------------------------|
| SHIPPED      | 8  (#36 #37 #109 #859 F7 CPU-BACKEND REF-CALL-REGISTRY RUST-REPLACE-SYMLINK)         |
| RETIRED      | 4  (#22 F2 F10 DD-004)                                                              |
| BLOCKED      | 6  (#89 #90 F5 F6 F8 MCP-SURFACE)                                                   |
| CEO_GATED    | 5  (#48 #72 #77 #131 #169)                                                          |
| DEMAND_GATED | 6  (#255 DD-006 AST-DSL-PARITY MCP-LEAN-DEFAULT CONTINUOUS-REFRESH RUST-REPLACE-TOCTOU) |

  0 READY, 0 IN_FLIGHT. The DEMAND_GATED bucket includes RUST-REPLACE-TOCTOU (the residual
  races banked when RUST-REPLACE-SYMLINK shipped in #1010); the DD-006 row now carries a
  SATISFIED demand condition with the reproduction as its trigger (section 2).
- New tracked artifacts landed by the PR:
  `docs/audits/2026-08-13-demand-gated-dispositions.md`,
  `docs/audits/2026-08-13-ceo-gated-packets.md`,
  `docs/plans/2026-08-13-backlog-completion-plan.md` (the council-approved plan; committed
  SHA-256 `BE1C85DDCB3BC598CF2A5D2DC38A6B7AD980DA97D6938A4B1695A089C60BF6EE`),
  `docs/design/2026-08-13-backlog-completion.md` (the spec).
- Post-merge verification: board parser 43 passed; governance sweep 62 passed;
  agent-readiness product checks (capsule / mixed-language / hardcases / docs-claim) PASS
  on the merged artifact.

## 2. The DD-006 measured method (W5B) - the demand-gate measurement

DD-006's reopen condition was "measured concurrent daemon load or denial-of-service
evidence". W5B satisfied it with a bounded local probe. The harness itself is scratch-only
(`.orchestrator/w5/dd006_probe.py`, not committed to `src/`); raw evidence is in
`artifacts/dd006_*.json`.

Parameters frozen before the run (plan-frozen): 20 clients, 60 s wall under a hard
timeout, one bounded daemon ping request per client looped, all 20 started then held.
Instrument identity recorded before any number was believed: `tg --version` ->
`tensor-grep 1.110.0`; `session_daemon.py` / `session_store.py` byte-identical between the
venv tree and `origin/main`.

Positive control (the probe must be able to report non-zero) - two arms:

| Arm         | Shape                                                       | Failures | Successes | Verdict                                                       |
|-------------|-------------------------------------------------------------|----------|-----------|---------------------------------------------------------------|
| looped      | 20 clients looping 60s vs a freshly-closed loopback port    | 1600     | 0         | supplementary                                                 |
| single-shot | 20 clients, ONE attempt each, vs the freshly-closed port    | 20       | 0         | the plan-frozen arm: `failures == 20` holds verbatim          |

Live arms against the warm session daemon (each 60 s, 20 clients). Arms 1-4 ran the
pre-discrimination probe (failure token was undifferentiated `TimeoutError`); arms 5-6 plus
the single-client arm ran the discriminating build:

| Run           | Connect budget | Response budget | Failures | Successes | Failure kinds (as recorded in the raw JSON) |
|---------------|----------------|-----------------|----------|-----------|---------------------------------------------|
| measure_1     | 0.5 s          | 2 s             | 54       | 4537      | 54 TimeoutError (undifferentiated)          |
| measure_2     | 0.5 s          | 2 s             | 36       | 4633      | 36 TimeoutError (undifferentiated)          |
| measure_3     | 0.5 s          | 2 s             | 37       | 4601      | 37 TimeoutError (undifferentiated)          |
| measure_4     | 0.5 s          | 60 s            | 25       | 4666      | 25 TimeoutError (undifferentiated)          |
| measure_5     | 0.5 s          | 60 s            | 64       | 4530      | 64 connect_timeout (discriminated)          |
| measure_6     | 2.0 s          | 60 s            | 0        | 4618      | none                                        |
| single_client | 0.5 s          | 60 s            | 0        | 238       | none                                        |

What the raw JSON supports, stated at its exact strength:

- Every multi-client arm at the CLI's own 0.5 s connect budget (5 of 5) reported >= 1
  timed-out client. Zero refusals, zero drops in every arm.
- The ONE discriminated arm (measure_5) classifies every one of its 64 failures as
  `connect_timeout`. Arms 1-4 cannot be split into connect vs response from their JSON; the
  durable claim is "5/5 arms showed timeouts at the 0.5 s budget, and the discriminated arm
  showed the class is connect-timeout" - never "5/5 connect-timeouts".
- "Zero response timeouts" is claimed ONLY for the arms whose raw JSON can prove it
  (measure_5, measure_6, single_client).
- Box load was 77-100% CPU during the multi-client arms (session observation via
  Get-CimInstance, not stored in the probe JSON) - recorded here as an observation about
  the measurement environment, not as data.

GATE-W5B-1 resolution: the threshold ">= 1 refused/dropped/timed-out client in at least 2
of 2 independent runs" is satisfied in 5 of 5 runs at the CLI's own connect budget, with
the control arm valid under the frozen threshold. Outcome REPRODUCED, at its true (soft)
severity: the daemon never refused or dropped a request; every classified timeout was a
slow accept (> 0.5 s) at the CLI's own `_DAEMON_CONNECT_TIMEOUT_SECONDS` budget under 20
concurrent clients on a 77-100%-loaded box; 0 failures at a 2.0 s budget and with a single
client. Mechanism hypothesis (stated as such): 20 clients x ~4 connects/s against a
`ThreadingMixIn` `TCPServer` with the stdlib default accept backlog
(`request_queue_size=5`) saturates the accept queue; a CLI client that times out on
connect falls back to the cold path by design (`_probe_daemon` -> None -> cold path), so
the user impact is a silent cold-path fallback under concurrency, not an outage.

Row consequence: DD-006's demand condition is now SATISFIED, so the row reopens per the
plan's re-approval rule. It stays OPEN with the reproduction as its trigger, awaiting
authorization for the bounded accept-side bound design (raise `request_queue_size`, or an
accept-thread budget note) - a design pass, not code, in this campaign (section 7).

## 3. Independent-gate receipts: the codex 4-round audit loop

Codex (gpt-5.6-sol) audited the docs branch in four rounds. The raw reports were scratch
files in `.claude/worktrees/w8-docs-closeout/.claude/codex_audit_w8_*.md` (that worktree
was removed after the merge); the surviving round-by-round record is the
"Codex-audit closure" section of `docs/audits/2026-08-13-demand-gated-dispositions.md`.

| Round | Verdict          | Finding class and closure                                                                                                                                                                                                                                                                                                                                                                                                                                                       |
|-------|------------------|---------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------|
| R1    | REVISE, 5 findings | C-01 control threshold (the looped control reported 1600 where the plan froze `failures == 20`; closed by ADDING the single-shot arm that reports exactly 20/0, with the looped arm recorded as supplementary); H-01 undifferentiated timeout claim (closed by narrowing the durable claim to what each arm's raw JSON can prove); C-02 missing W6 per-row receipts (closed by the six-row command/output table); C-03 missing A101 3x receipt (closed by recording the recurrence count); H-02 untracked plan (closed by committing the approved plan + spec bytes so the cited paths exist on the merged tree) |
| R2    | 2 LOW            | L-01 plan ASCII-census falsehood (fixed); L-02 trailing space (fixed). No semantic content changed                                                                                                                                                                                                                                                                                                                                                                             |
| R3    | 1 LOW            | L-03 census location inventory (fixed by correcting the census note's named lines)                                                                                                                                                                                                                                                                                                                                                                                             |
| R4    | APPROVE          | on `7b7f3c8`                                                                                                                                                                                                                                                                                                                                                                                                                                                                  |

Plan hash chain (SHA-256 over the audited plan bytes; A46 discipline): round-1 bytes
`4A4841AC8431549FA6DDEDA208C6C722563CBA954EC5F217472C540F3EC36FEE` (pre-format witness
preserved in the parent checkout); round-2 bytes
`E073670B50FBFA735CFDA46A37BA32E5E83C95BBBDA6A04EE1889A4D3EBE847E`; round-3 bytes
`3649D711D54825342787CE6D2E4778B9CE23A27F88806DFC97CD94B50717B61D`; final committed plan
bytes `BE1C85DDCB3BC598CF2A5D2DC38A6B7AD980DA97D6938A4B1695A089C60BF6EE`. Three audit
rounds spent on one census paragraph are the tell behind A114 below.

## 4. Thinktank substitution receipts

The W7 CEO-packet council (`tt_council.sh`, 8 seats):

- 7 seats verdict-bearing; 7/7 HYBRID-ACCEPTED / ADVISORY-ONLY on the packets.
- copilot TIMEOUT: recorded as a FAILED seat, not a blocker (the A10 rule: a no-verdict
  seat is failed, and the surviving verdicts carry the synthesis).
- claude seat sat `sonnet`: Fable 5 was quota-blocked at dispatch time, so that seat ran
  on sonnet instead. The substitution is recorded in the council synthesis header and in
  the CEO packet doc (`docs/audits/2026-08-13-ceo-gated-packets.md`) - it is a disclosed
  vendor substitution, never presented as a Fable verdict (A74 discipline: a quota-blocked
  seat is not durable clearance).

## 5. New A-law candidates (A111-A116) - provenance record

Drafted by the orchestrator; sibling agent A transcribes them into AGENTS.md/CLAUDE.md
with their final numbering. This doc is the provenance record for that transcription.

- **A111 - Commit the plan you cite.** Docs merged onto main must not cite plan/spec paths
  that do not exist in the merged tree; an untracked council-approved plan breaks every
  citation downstream (codex H-02). When committing a previously-untracked approved
  artifact, record the pre-format witness hash AND the committed hash (A46 extension).
- **A112 - A plan-frozen control threshold is met verbatim or the arm is
  CANNOT_MEASURE.** A looped probe whose control reports 1600 where the plan froze
  `failures == 20` needs a single-shot arm that reports exactly 20; recharacterizing the
  frozen number as "illustrative" is a plan violation, not a fix (codex C-01).
- **A113 - Claim only what the raw artifact discriminates.** 5/5 arms timed out, but only
  the ONE discriminated arm may be called connect-timeout; an undifferentiated
  `TimeoutError` cannot be upgraded to a specific class in prose, and environment readings
  (CPU%) the harness did not record are observations, not data (codex H-01).
- **A114 - A corrected census is not closed until its location inventory is mechanically
  re-derived.** Totals can be right while the named lines are wrong; a census note's own
  prose is auditable content, and three audit rounds on one paragraph is the tell (codex
  L-01/L-03). Re-derive locations with a script, never from memory of the file.
- **A115 - Wave receipts are per-row tables, not group sentences.** "Six rows, six
  commands, six recorded results" asserted as one sentence is a claim, not a receipt; each
  row gets its own command and output in a table (codex C-02; A98 applied to board waves).
- **A116 - Never let `uv run` create a venv inside a bare worktree.** `uv run pytest` in a
  worktree without `.venv` creates an empty broken venv (`No module named pytest`); run
  worktree tests from the MAIN checkout's venv targeting worktree paths
  (`uv run --no-sync python -m pytest "<worktree>/tests/..."`) and remove any
  accidentally-created worktree `.venv` immediately.

## 6. Exa deltas banked this session

- MCP lean-default is now SPEC-LEVEL: the official MCP client-best-practices codify
  progressive discovery (catalog / inspect / execute) and programmatic tool calling (up to
  85% token reduction claimed by Anthropic); AWS prescriptive guidance codifies <=8
  params/tool.
- Warm code-index daemons are table stakes: TriSeek v0.4.2 (multi-harness warm daemon +
  memo), cgh (watch-first code graph), seekr, Cursor cloud-agent warm builds.
- ast-grep's Rust tree-sitter rewrite is ~22% faster end-to-end; NL-to-DSL arXiv
  2507.02107; no metavariable-PERFORMANCE demand signal (peers reach for the DSL/parity
  axis, not the performance axis).

## 7. Known follow-ups

- **DD-006 accept-side bound design awaits authorization.** The demand condition is
  satisfied and the reproduction rides the row trigger; the bounded accept-side bound
  design (raise `request_queue_size`, or an accept-thread budget note) is a DESIGN pass
  that needs authorization before any code. The row stays OPEN until then.
- **#966 parked draft stays parked.** The Task 2A draft remains parked RED by design
  (Sol exact-byte SHIP + Windows census evidence outstanding); nothing this session
  changes that disposition.
- **Retention fan-out:** this doc is written by the docs-capture seat (D) of the
  2026-08-14 retention fan-out off `e1a2b61`. Sibling agent C is creating the new in-repo
  skill `tensor-grep-demand-gate-measurement` (the bounded demand-gate measurement method
  with the DD-006 worked example); the docs-artifact audit-loop learnings fold into the
  existing `tensor-grep-codex-gated-audit-loop` skill. Skill count goes 34 -> 35, with the
  AGENTS.md/CLAUDE.md index updated by sibling agent A (runs last).
