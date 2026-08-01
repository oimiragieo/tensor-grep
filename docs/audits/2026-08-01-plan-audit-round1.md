# 2026-08-01 — plan audit round 1: UNANIMOUS BLOCK

Audit of `docs/superpowers/plans/2026-08-01-backlog-campaign.md` at `0126cb3` (`HEAD == origin/main`).

Two independent audits ran: an 8-seat thinktank council (6 returned anchored verdicts) and a
`codex gpt-5.6-sol` pass at `xhigh` reasoning, dispatched separately. **Both BLOCK.** They agree on
one defect and each found blockers the other missed — which is the argument for running both.

## Seat health

| seat | provider | result |
|---|---|---|
| claude (fable-5) | Anthropic | verdict |
| droid_kimi | Moonshot | verdict |
| droid_minimax | MiniMax | verdict |
| droid_glm | Zhipu | verdict |
| cursor | Cursor auto | verdict |
| agy | Google | verdict |
| codex (council seat) | OpenAI | **zero-byte — wedged, counted as FAILED** |
| copilot | Microsoft | 32KB, **no anchored verdict — DEGRADED** |

The three Chinese seats were DOWN at gate time (droid token expired, a single cause taking all three)
and were restored mid-session by the operator. Provider diversity was preserved for the seats that
voted. The wedged council-codex seat is separate from the standalone codex audit below, which used a
direct `codex exec` dispatch and completed normally.

## The one defect all six seats found — VERIFIED

**PR-B would reverse a documented-intentional policy while calling it a bug fix.**

`tests/unit/test_session_daemon_security.py:58`:

```python
def test_tokenless_server_stays_backward_compatible() -> None:
    # A server constructed without a token (legacy/in-test path) must not reject requests.
    server = session_daemon._ThreadedSessionDaemon(Path.cwd(), ("127.0.0.1", 0))
    assert server.is_authorized({}) is True
```

This is not an incidental call site that needs migrating. It is a test whose NAME and COMMENT pin the
current fail-open as deliberate. The plan treated the behaviour as an unexamined defect and never
mentions this test. Any fail-closed change must therefore be argued as a **policy reversal** — the
pin retired with a written reason and the human merger told — not shipped as a fix.

The plan's census was also short: `tests/unit/test_session_serve.py:356,393,457` construct tokenless
daemons and were omitted. Codex's independent count puts the real direct-call population at **16**,
against the plan's ~13, and additionally finds the two named "harness files" already use a token — so
the census was wrong in both directions. Two of the plan's named harness files
(`test_orient_agent_daemon.py`, `test_graph_completeness_oracle.py`) do not construct daemons at all.

This is the population-census failure mode this repo has hit repeatedly: members added by reasoning
rather than by being called.

## Three HIGH blockers ONLY codex found — both verified independently

### 1. The `--ltl` fix reds 16 existing tests

The plan validates the LTL grammar at the CLI boundary, before `DirectoryScanner`
(`cli/main.py:8073-8076`). Sixteen existing tests invoke `search_command` with `--ltl` and the pattern
`"ERROR"` — which is NOT valid LTL grammar (the parser requires `A -> eventually B`). Confirmed:
`grep -c '"--ltl"' tests/unit/test_cli_modes.py` → **16**, with sites at `:3838`, `:13282`, `:13322`,
`:13345` and onward. Those tests use `--ltl` incidentally while testing routing/debug/stats; the new
validation would exit 2 before their fake backends are ever reached. The plan's own full-suite gate
would fail.

### 2. The Rust red arm is observable; the e2e parity red arm is NOT

A genuine split the plan collapsed into one claim:

- **Observable.** The `SEARCH_PYTHON_PASSTHROUGH_FLAGS` unit assertion is truly RED before the
  allow-list line and GREEN after — stable CI runs `cargo test --no-default-features`
  (`.github/workflows/ci.yml:448-513`).
- **NOT observable.** The proposed `tests/e2e/test_routing_parity.py` case calls
  `_skip_if_native_binary_missing`, which `pytest.skip`s when the native binary is absent
  (`tests/e2e/test_routing_parity.py:165-167`, verified by reading the function). The `test-python`
  job never builds a release `tg` (`ci.yml:442-446`), and the job that does build one runs only
  `tests/e2e/test_native_*.py` (`ci.yml:658-660,703-726`). **So that arm SKIPS in both the pre-fix and
  post-fix trees** — a check that passes in both arms, which is precisely what this repo's oracle law
  forbids. The plan recorded it as a red-arm receipt.

### 3. PR-D skips the mandatory adversarial security gate

`AGENTS.md:48-53` makes the gate mandatory for any PR touching `apply_policy` or the native front
door. PR-D edits both. The plan waives it on the grounds that the changes are comments — but the
trigger is the surface, not the diff's shape.

## Also found (MED)

- **A fourth proposed Task-3 test is baseline GREEN, not RED.** Invalid *regex* subexpressions are
  already classified by `_is_invalid_regex_error` and routed to `_exit_invalid_regex`
  (`cli/main.py:3985-3995`, `:4902-4911`). Recording it as a red receipt would be a false control.
- **`CONTRACTS.md` has a THIRD lie the plan does not fix** — §9 still calls Slice 2 path-literal at
  `:240`, and the producer's own docstring says `_ledger_physical_root` is claims-only
  (`ledger_store.py:434-438`) while it has five call sites (`:658,797,854,1198,1335`). Fixing only
  `:253-263` leaves two contradictions live, one in the same file.
- **File collisions across PRs.** PR-C and PR-A both edit `cli/main.py`; PR-D and PR-A both edit
  `rust_core/src/main.rs`. A branch rebased only before its first push goes stale once C/D land. The
  plan also omits an explicit newest-main-run-completed gate after C/D.

## Confirmed CORRECT (do not re-litigate)

- **`BackendExecutionError` is the wrong taxonomy for a user grammar error** — the plan's call was
  right, and now has a receipt: it is a runtime engine-failure type (`backends/base.py:7-12`) and
  `search_command` retries every such error through `_search_with_cpu_fallback`
  (`cli/main.py:8279-8284`), whose presenter says "search backend failed". A user typo must go
  straight to `_exit_search_error`.
- **The dead-code deletion is safe** — held under four independent lenses (`tg callers` 0/0/0 with
  `result_incomplete=false`; positive control returned 4 callers across 3 files; exact-symbol scans of
  tracked and hidden files; no `__all__`, string registry, or `getattr` dispatch).

## Verdict

**BLOCK.** Council 6/6 BLOCK; codex BLOCK with 7 must-fix. The plan is not safe to execute as
written. It is not a bad plan — its ground truth, its error taxonomy and its dead-code analysis all
survived attack — but three of its stated red arms do not discriminate, one PR reverses a pinned
policy without saying so, and one skips a mandatory gate.

## Method note

The council and codex overlapped on only ONE finding. Six seats sharing five providers all missed the
16-test collision and the skipping e2e arm; codex alone missed nothing the council caught but found
three HIGH issues it did not. Consensus across many seats is not coverage — the seats converged on the
most legible defect (a named test with a self-describing comment) and stopped. Two structurally
different audits beat more seats of the same shape.
