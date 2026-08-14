# Demand-gated dispositions (2026-08-13 campaign, W5)

Execution date: 2026-08-14. Base: `origin/main` `a1c51ee` (v1.110.16). This wave
writes no product code; the DD-006 probe is a scratch measurement harness whose
OUTPUT is recorded here, not committed under `src/`.

## Step 0 - the five rows re-derived from `origin/main` (2026-08-14)

All five rows read `DEMAND_GATED` on `origin/main` at the time of this wave:

| Row | Status on origin/main | Reopen condition (verbatim trigger, abbreviated) |
|---|---|---|
| #255 | DEMAND_GATED | many-pattern dedup parity experiment OR approved compression/native investment |
| DD-006 | DEMAND_GATED | measured concurrent daemon load or denial-of-service evidence |
| AST-DSL-PARITY | DEMAND_GATED | demand for full structural DSL parity and a preprocessor-aware oracle |
| MCP-LEAN-DEFAULT | DEMAND_GATED | client demand + compatibility evidence for changing the default surface |
| CONTINUOUS-REFRESH | DEMAND_GATED | approved scoping/design pass for a warm search-index service (not a build) |

## W5A - #255 (many-pattern dedup over-count)

Measured on `origin/main` `src/tensor_grep/cli/rule_packs.py` (the shipped
built-in ruleset source):

| Pack | Pattern entries |
|---|---|
| auth-safe | 35 |
| crypto-safe | 7 |
| secrets-basic | 21 |
| deserialization-safe | 26 |
| subprocess-safe | 33 |
| tls-safe | 7 |
| Total (sum of six disjoint packs) | 129 |

Per-run anchor population: `resolve_rule_pack` (`rule_packs.py:1063`) resolves
exactly ONE pack per scan; the largest single pack is 35. The six packs are
never unioned (resolving the shared "security" category raises
`ValueError: ... is a security category, not a single built-in ruleset`), so no
single `tg scan` dispatch exceeds 35 anchors.

Both arms of the reopen condition, recorded rather than assumed:

- **Anchor count past ~100?** NO for any single ruleset (max 35). The 129
  figure is the sum across six disjoint, never-unionable packs.
- **Named user with a 100+-pattern workload?** NO named user exists; the
  2026-08-12 receipt's finding stands (Aho-Corasick workloads are real but
  scanner-internal; no external agent demand signal).

Outcome: **DEMAND_GATED stands.** The demand claim ("ruleset growth past ~100
anchors or a named user") is recorded as made and unmet on both arms.

## W5B - DD-006 (daemon DoS) - bounded local concurrency measurement

Parameters fixed before the run (plan-frozen): 20 clients, 60 s wall under a
hard timeout, one bounded daemon ping request per client looped, all 20
started then held. Instrument identity recorded before any number was
believed: `tg --version` -> `tensor-grep 1.110.0` (venv; `session_daemon.py` /
`session_store.py` byte-identical between the venv tree and `origin/main`),
daemon `127.0.0.1:61335`, token present, `package_version 1.110.0`.

### Positive control (the probe must be able to report non-zero)

Two control arms, recorded separately (the second was added during the codex
audit of this wave - see the finding-closure note at the end):

| Arm | Shape | Failures | Successes | Verdict |
|---|---|---|---|---|
| looped | 20 clients looping for the full 60s window against a freshly-closed loopback port | 1600 | 0 | supplementary |
| **single-shot** | 20 clients, ONE attempt each, against a freshly-closed loopback port (`--single-shot`) | **20** | 0 | **the plan-frozen arm: `failures == 20` holds verbatim** |

Control VALID under the frozen threshold: the single-shot arm reports exactly
20 failures / 0 successes, and the looped arm confirms every client failed on
every attempt. Raw evidence: `artifacts/dd006_control_singleshot.json` and
`artifacts/dd006_control.json`.

### Live arms against the warm session daemon

Run matrix (each 60 s, 20 clients, recorded raw in `artifacts/dd006_*.json`).
Classification note: arms 1-4 ran the pre-discrimination probe, whose only
failure token was undifferentiated `TimeoutError`; the probe was then split so
connect and response timeouts classify separately, and arms 5-6 plus the
single-client arm ran the discriminating build:

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

- Every multi-client arm (5 of 5) reported >= 1 timed-out client at the CLI's
  own 0.5 s connect budget. Zero refusals, zero drops in every arm.
- The ONE discriminated arm (measure_5) classifies every one of its 64
  failures as `connect_timeout`. The other four arms cannot be split into
  connect vs response from their JSON - the claim "5/5 arms showed
  connect-timeouts" is therefore NOT made; the durable claim is "5/5 arms
  showed timeouts at the 0.5s budget, and the discriminated arm showed the
  class is connect-timeout."
- "Zero response timeouts" is claimed ONLY for the arms whose raw JSON can
  prove it (measure_5, measure_6, single_client). For arms 1-4 the split is
  unrecorded and is stated as such.
- Box load was 77-100% CPU during the multi-client arms (session observation
  via Get-CimInstance, not stored in the probe JSON) - recorded here as an
  observation about the measurement environment, not as data.

### GATE-W5B-1 resolution

Threshold check: ">= 1 refused/dropped/timed-out client in at least 2 of 2
independent runs" -> satisfied in 5 of 5 runs at the CLI's own connect budget,
with the control arm valid under the frozen threshold (single-shot: failures
== 20).

**Outcome: REPRODUCED** (per the plan-frozen thresholds, control-valid) - the
failure is **soft**, and the evidence is recorded at its true severity, not
inflated:

- The daemon never refused or dropped a request. Every classified timeout was
  a slow accept (>0.5 s) at the CLI's own `_DAEMON_CONNECT_TIMEOUT_SECONDS`
  budget, under 20 concurrent clients on a 77-100%-loaded box.
- With a 2.0 s connect budget the failure rate is 0; with a single client it
  is 0. The degradation is load-dependent accept-path latency (default
  `request_queue_size=5` backlog + a busy shared box), not a denial.
- Mechanism hypothesis (stated as such): 20 clients x ~4 connects/s against a
  `ThreadingMixIn` `TCPServer` with the stdlib default accept backlog of 5
  saturates the accept queue; connects that cannot grab a backlog slot within
  0.5 s time out. A CLI client that times out on connect falls back to the
  cold path by design (`_probe_daemon` -> None -> cold path), so the user
  impact is a silent cold-path fallback under concurrency, not an outage.

Per the plan's re-approval rule, the reproduction is filed on the DD-006 row
itself: its demand condition ("measured concurrent daemon load ... evidence")
is now SATISFIED by this measurement, so the row reopens. Candidate
accept-side bounds for the reopen trigger (design, not code, this campaign):
raise `request_queue_size`, or an accept-thread budget note. The
re-disposition travels through W8 with this receipt (W6's no-inline-flips
discipline).

## W5C - AST-DSL-PARITY (native-speed metavariables) - Exa delta

Fresh findings on top of the 2026-08-12 receipts (reused, not re-derived):

- ast-grep's Rust tree-sitter rewrite is now public with end-to-end numbers:
  ~22% less user CPU end-to-end, ~30% parser-only (ast-grep blog, "How
  ast-grep Rewrote Tree-sitter in Rust and Made It 30% Faster").
- New NL-to-DSL structural-search work (arXiv 2507.02107) builds LLM-to-DSL
  translation with a 400-query benchmark; its motivation is DSL difficulty,
  not metavariable performance.
- OpenLore's 2026 proposal document selects an in-tree minimal matcher over
  `@ast-grep/napi` for honesty reasons - relevant precedent for "native-speed
  metavars" remaining a nice-to-have: peers reach for the DSL/parity axis,
  not the performance axis.

Honest null, unchanged: zero evidence of a consumer blocked on metavariable
PERFORMANCE. The discourse remains capability/adoption. **LEAVE** - no
perf-blocked consumer named; #141's existing gate stands.

## W5D - MCP-LEAN-DEFAULT - Exa delta + GATE-W5C-1

GATE-W5C-1 (fence check, executed): `_TG_MCP_SERVER_CONTRACT_VERSION` on
`origin/main` reads `"1.7.0"` - exactly the value the MCP-SURFACE row asserts.
Fence holds; no strength of industry-direction evidence unfences this row.

Fresh findings on top of the 2026-08-12 receipts:

- The pattern is now SPEC-LEVEL, not just industry practice: the official MCP
  client-best-practices doc codifies progressive discovery (catalog / inspect
  / execute layers, thresholds at 1-5% of context) and programmatic tool
  calling; Anthropic reports up to 85% token reduction, one write-up claims a
  98.7% drop for code-mediated invocation.
- AWS Prescriptive Guidance (2026-07-09) codifies <=8 parameters per tool,
  enums, defaults, and lazy discovery as the standard advice.

**PROPOSED_REOPEN stands, still fenced behind Task 2C** - lean-by-default is
now the official direction; the fence is the MCP-SURFACE ladder and evidence
does not resequence a ladder.

## W5E - CONTINUOUS-REFRESH (warm persistent index serving) - Exa delta

Fresh findings on top of the 2026-08-12 receipts:

- TriSeek (2026) ships a warm local code-search daemon with an MCP server for
  Claude Code/Codex/OpenCode/Pi, session snapshots, and a memo layer; its
  claimed numbers (16.9x on a 20-query agent session vs cold rg) are the
  exact workload class tg's banked "big-refactor" note names.
- cgh (2026) ships a watch-first local code graph (DuckDB + FTS) with
  incremental reindex; seekr (2026) ships watch-daemon + HNSW local semantic
  search; Cursor (2026-08-13) extended warm-state culture to whole cloud
  agent environments ("builds", 3x faster starts).

Warm persistent serving is now demonstrably table stakes, not a hypothesis.
**PROPOSED_REOPEN (scoping pass only) strengthened** - the row still reopens
for a design/scoping pass, not a build, and the daemon side-load pattern
("one global daemon serving many roots") is the concrete shape peers adopted.

## Acceptance criteria vs outcome

- Five packets, each with the reopen condition restated and the evidence
  checked against it - INCLUDING the case where the evidence fails to satisfy
  it (#255) and the CANNOT_MEASURE-adjacent case (none this wave; the DD-006
  instrument was corrected mid-wave and the corrected arms are what decide).
- Status changes only where the packet justifies one: exactly one - DD-006's
  demand condition is measurement-satisfied, and its re-disposition travels
  through W8 in-body per A71.
- Raw evidence: `artifacts/dd006_control.json`,
  `artifacts/dd006_control_singleshot.json`, `artifacts/dd006_measure_1.json`
  through `artifacts/dd006_measure_6_connect2s.json`,
  `artifacts/dd006_single_client.json` (probe harness itself is scratch-only,
  `.orchestrator/w5/dd006_probe.py`, not committed to `src/`).

## W6 receipts - six blocked rows, six commands, six recorded results (A98)

One command per row, one recorded output per row; no row's disposition is
inferred from a sibling's. All executed 2026-08-14 against `origin/main`
`a1c51ee`:

| Row | Command executed | Recorded output |
|---|---|---|
| #89 | `gh pr view 966 --json number,state,isDraft,mergeable,headRefOid` | OPEN, draft, MERGEABLE, head `1210d8ef1d8d5799c6b9035eada379352e2e2141` (W4-parked RED scaffold) -> BLOCKED stands |
| #90 | `git log --oneline origin/main -3 -- src/tensor_grep/cli/session_daemon.py` | recent commits `b062989`/`22ec8b7`/`221d4a3`; doctor half shipped #571; scan half still in the parked Task 2A program -> BLOCKED stands |
| F5 | `git show origin/main:docs/TASK_BOARD.md \| grep 'shared-box'` | campaign notes still carry "W3 rust/e2e shared-box ban" and the F5 row names `rust_core/** + tests/e2e/**` -> BLOCKED stands (Step 2 shipped #943) |
| F6 | `git show origin/main:docs/TASK_BOARD.md \| grep 'F6'` | row carries the MIXED disposition (A41): Python/schema/evidence-signing buildable-first, native verify-edit + e2e halves CI/cloud-routed -> BLOCKED stands |
| F8 | `git ls-tree -r --name-only origin/main \| grep path_domain.rs` | empty (Tasks 12-13 files not on main) -> BLOCKED stands |
| MCP-SURFACE | `git show origin/main:src/tensor_grep/cli/mcp_server.py \| grep '_TG_MCP_SERVER_CONTRACT_VERSION ='` | `"1.7.0"` -> the Task 2C fence holds -> BLOCKED stands |

Zero status flips - all six rows stay BLOCKED with their prerequisites unmet.

## Codex-audit closure (findings against commit `48180fc`)

The independent codex audit returned REVISE with five findings; each was
verified against the raw evidence and closed as follows:

- **C-01 (control threshold):** the plan froze `failures == 20` for the
  control; the looped control arm reported 1600. CLOSED by adding the
  single-shot control arm, which reports exactly 20/0 against the same closed
  target - the frozen threshold now holds verbatim on the arm recorded as
  such, and the looped arm is recorded as supplementary rather than
  recharacterized.
- **H-01 (undifferentiated timeouts):** CLOSED by narrowing the durable claim
  to what each arm's raw JSON can prove (5/5 arms showed timeouts at 0.5s;
  the discriminated arm classified them connect-timeout; response-timeout
  absence claimed only where recorded).
- **C-02 (W6 receipts):** CLOSED by the W6 table above.
- **C-03 (A101 3x):** CLOSED by recording the recurrence count in the
  2026-08-14 BACKLOG entry.
- **H-02 (untracked plan):** CLOSED by committing the approved plan and spec
  bytes into this branch so the cited paths exist on the merged tree. Format
  note, recorded for hash discipline (A46): the plan was ruff-preview-
  normalized at commit time and its ASCII-census note and one trailing space
  corrected after the second codex round (L-01/L-02) - no semantic content
  changed anywhere. Hash chain: audited round-1 bytes SHA-256
  `4A4841AC8431549FA6DDEDA208C6C722563CBA954EC5F217472C540F3EC36FEE`
  (pre-format witness preserved in the parent checkout); round-2 bytes
  `E073670B50FBFA735CFDA46A37BA32E5E83C95BBBDA6A04EE1889A4D3EBE847E`;
  round-3 bytes (this revision)
  `3649D711D54825342787CE6D2E4778B9CE23A27F88806DFC97CD94B50717B61D`
  (census-note correction + trailing-space removal only). Round 4: the census
  note's location inventory was corrected again after the third codex round
  (L-03) - current plan SHA-256
  `BE1C85DDCB3BC598CF2A5D2DC38A6B7AD980DA97D6938A4B1695A089C60BF6EE`.
