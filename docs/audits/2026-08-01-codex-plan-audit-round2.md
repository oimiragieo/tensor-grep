# Codex plan audit — round 2

| Q# | answer | evidence file:line | risk |
|---|---|---|---|
| Q1 | **PASS.** There is exactly one production construction, and it always passes a generated token. | `src/tensor_grep/cli/session_daemon.py:2064-2069`; all other symbol references are annotations/cast/class definition at `:1555`, `:1733`, `:1801`, `:2022`. | None: zero tokenless production constructions. |
| Q2 | **PASS.** The 16-site direct tokenless test census is complete and correct: 11 + 3 + 2. | `tests/unit/test_session_cli.py:2461,2528,2584,2634,2708,2775,2845,2897,2968,3036,3107`; `tests/unit/test_session_serve.py:356,393,457`; `tests/unit/test_session_daemon_security.py:60,675`. | None. No missing or wrongly included call. |
| Q3 | **FAIL.** Yes: the plan indirectly instructs local execution of `tests/e2e/test_routing_parity.py` through unscoped full-suite pytest commands. | `docs/plans/2026-08-01-backlog-campaign.md:64,101,225,267,498,712`; collection root `pyproject.toml:34-52`. | **BLOCKER:** violates the shared-server ban and can invoke local Cargo through that E2E file. |
| Q4 | **FAIL.** Yes: a fifth current stale prose site says only Slice 1 canonicalizes. | `docs/multi_agent_context_plane.md:139-151`, especially `:148-151`; positive controls are the four known hits at `docs/CONTRACTS.md:240,253` and `src/tensor_grep/cli/ledger_store.py:389-391,434-438`. | **BLOCKER:** Task 1 would leave a current architecture document contradicting shipped Slice-2 behavior. |

**Q1 note.** The whole-`src/` symbol census found five references. Four are non-constructions; the sole call is `_ThreadedSessionDaemon(..., token=token)` at `:2069`, immediately after `token = secrets.token_urlsafe(32)` at `:2068`. That token-bearing call is the positive control proving the zero-tokenless search saw a real construction.

**Q2 note.** Each of the 16 call expressions was read, including the two security sites. The other direct test constructions pass a nonempty token; both `_real_daemon` helpers forward a default `token="test-token"` (`test_symbol_daemon_autostart.py:73-75`, `test_session_daemon_version_skew.py:35-38`), and their callers do not override it with an empty token.

**Q3 note.** The conflicting instruction is: “**Local test gate:** ... `uv run --no-sync pytest -q --maxfail=0` before push” (`:64`). `testpaths = ["tests"]` and the default addopts contain no E2E exclusion, so this collects the forbidden routing-parity module. The explicit prohibition at plan `:62` does not neutralize the executable commands.

**Q4 note.** Whole-repo grep found the four named current lies as positive controls and also `docs/multi_agent_context_plane.md:149`: “`list` (**Slice 1 only**) canonicalize `PATH`...”. Historical CHANGELOG/audit/test narratives describe the old state and are not additional current-contract lies; this architecture document is current and links readers to the live contract.

**Verdict: BLOCK. mustfix=2.** Scope every local full-suite pytest command so it cannot collect the forbidden E2E module, and add `docs/multi_agent_context_plane.md:148-151` to Task 1's prose fixes and sweep.
