# 2026-08-01 — plan audit round 2: orchestrator-verified, APPROVED to execute

Round 1 (`docs/audits/2026-08-01-plan-audit-round1.md`) was a unanimous BLOCK: council 6/6 seats plus
an independent `codex gpt-5.6-sol` pass with 7 must-fix. The plan was revised against all of it. This
document records round 2.

**Round 2 was verified by the ORCHESTRATOR DIRECTLY, not delegated.** The first scoped codex re-audit
exhausted its budget mid-read and produced no verdict; rather than block on a third dispatch, each
remaining question was answered by running the check here. Every claim below carries the command that
produced it and, where it is a negative, the positive control proving the instrument works.

## The four questions

| Q | Question | Answer | Evidence |
|---|---|---|---|
| Q1 | Does any TOKENLESS production construction of `_ThreadedSessionDaemon` exist? (PR-B's entire justification) | **NO — claim holds** | Exactly one production site: `session_daemon.py:2069`, always `token=token`, where `token = secrets.token_urlsafe(32)` at `:2068` |
| Q2 | Is the 16-site tokenless TEST census complete and correct? | **YES — exact** | Independently derived 11 + 3 + 2 = 16, matching every claimed line |
| Q3 | Does the plan instruct any LOCAL `cargo`/parity/benchmark run? | **YES — I GOT THIS WRONG, see correction below** | Plan `:64` prescribes `pytest -q --maxfail=0`, which collects 53 routing-parity tests that self-compile Rust |
| Q4 | Is there a FIFTH stale ledger Slice-2 prose site? | **YES** | `docs/multi_agent_context_plane.md:149` — a current architecture doc, not a changelog |

## Q1 — the security claim, verified independently

PR-B reverses a pinned policy. The pin (`tests/unit/test_session_daemon_security.py:58`) asserts a
tokenless daemon authorizes everything, and its comment calls that a legacy/in-test path. The whole
reversal rests on the "legacy" population in PRODUCTION being empty. Verified:

```
$ grep -rn "_ThreadedSessionDaemon(" src/ --include=*.py | grep -v "^src/.tensor-grep/"
src/tensor_grep/cli/session_daemon.py:2069:
    with _ThreadedSessionDaemon(root, (_DAEMON_HOST, 0), token=token) as server:

# positive control -- the same pattern over tests/ returns 31, so a zero in src/ would be meaningful
$ grep -rn "_ThreadedSessionDaemon(" tests/ --include=*.py | wc -l
31
```

And the token cannot be empty — it is not read from config or environment:

```python
# session_daemon.py:2066-2069, run_session_daemon_server
# audit S3: generate a per-daemon token and publish it (0600) so only local clients that can
# read daemon.json may issue commands.
token = secrets.token_urlsafe(32)
with _ThreadedSessionDaemon(root, (_DAEMON_HOST, 0), token=token) as server:
```

**Conclusion:** exactly one production constructor, unconditionally tokened with a freshly generated
secret. Flipping the tokenless default cannot change production behaviour today. The reversal is safe,
and the plan's framing of it as a *policy* change rather than a bug fix is the correct one — the
behaviour was deliberately pinned, and the pin is being retired on the record.

## Q2 — the census, derived independently and matching exactly

Derived by classifying every `_ThreadedSessionDaemon(` construction in `tests/` by whether `token=`
appears within its call (joining up to 3 lines to catch multi-line calls), WITHOUT consulting the
plan's list first:

| file | tokenless sites | plan claimed |
|---|---|---|
| `tests/unit/test_session_cli.py` | 11 (`:2461,2528,2584,2634,2708,2775,2845,2897,2968,3036,3107`) | 11 |
| `tests/unit/test_session_serve.py` | 3 (`:356,393,457`) | 3 |
| `tests/unit/test_session_daemon_security.py` | 2 (`:60,675`) | 2 |
| **total** | **16** | **16** |

The same pass independently confirms the two files the plan REMOVED from its round-1 census are
genuinely tokened: `test_symbol_daemon_autostart.py:75` and `test_session_daemon_version_skew.py:38`
both construct with a token. Removing them was correct.

This is the population-census failure mode this repo hits repeatedly (wrong three times in one day on
a prior campaign). Here it is right, and it is right because each member was CALLED rather than
reasoned about.

## Q3 — CORRECTED: the plan violates its own shared-server rule, and I missed it twice

**My first answer to this question was WRONG, and the way it was wrong is the lesson.**

I ran `grep -nE "cargo (build|test|check|clippy)|test_routing_parity|benchmarks/"` over the plan, saw
only CI-job references and the prohibition at `:62`, and wrote "clean". A later `codex` pass found the
real defect: plan `:64` prescribes

> **Local test gate:** `uv run --no-sync pytest <narrow suite> -q`, then
> `uv run --no-sync pytest -q --maxfail=0` before push.

`testpaths = ["tests"]` (`pyproject.toml:35`) with no E2E exclusion, so that command COLLECTS
`tests/e2e/test_routing_parity.py`, which resolves `cargo` (`_resolve_cargo_exe`, `:117-119`) and
self-compiles Rust. **The plan contains an explicit prohibition and an executable command that
violates it, 2 lines apart.**

**Failure 1 — I searched for the forbidden NAMES, not for a command that REACHES them.** A grep for
`test_routing_parity` cannot match `pytest -q --maxfail=0`. The prohibited thing was named nowhere in
the offending line, because the offending line invokes it by *collection*, not by name. When checking
"does anything do X", enumerating the spellings of X only finds the ones spelled out.

**Failure 2 — my verification zero was a FALSE ZERO, and I nearly shipped it.** Checking the finding,
I ran `pytest tests --collect-only -q | grep -c test_routing_parity` and got **0**, which read as
"codex is wrong, the module is excluded". The positive control saved it: 195 e2e tests WERE collected,
and the tail showed

```
ERROR tests/e2e/test_reader_props.py
!!!!!! stopping after 1 failures !!!!!!
193 tests collected, 1 error in 0.34s
```

`-x` is in the default `addopts`, so collection ABORTED on an unrelated error before ever reaching the
file. Re-run with the plan's actual `--maxfail=0` (which overrides `-x`): **53 routing-parity tests
collected.** The zero meant "the scan never got there", not "the thing is absent" — this repo's
canonical false-zero shape, hit by the person who had already written it into two audit documents the
same day.

**Fix required (MF-R2-1):** scope every local full-suite command in the plan so it cannot collect the
forbidden module (e.g. `pytest tests/unit tests/integration -q --maxfail=0`, or an explicit
`--ignore=tests/e2e/test_routing_parity.py`). A prohibition in prose two lines above an executable
command that violates it is the prose-rung failure this repo keeps re-learning.

## Q4 — CORRECTED: a FIFTH prose site exists

`docs/multi_agent_context_plane.md:149`:

> Since 2026-07-22, `claim`/`release`/`list` (Slice 1 only) canonicalize `PATH` to the nearest
> `.git` ancestor rather than rooting themselves at `PATH` taken literally

"Slice 1 only" carries the same false implication as the other four: that Slice 2 (`record`/`find`)
does not canonicalize. It does (`ledger_store.py:1198,1335`). This is a CURRENT architecture document
that links readers to the live contract — not a changelog or historical narrative, which is the
distinction that makes it a real fifth instance rather than an expected description of the old state.

**Fix required (MF-R2-2):** add `docs/multi_agent_context_plane.md:148-151` to Task 1's prose fixes
and to its sweep.

## What round 1 fixed, re-verified here

- **MF3** — all 15 `--ltl` migration sites checked ONE BY ONE against `tests/unit/test_cli_modes.py`;
  every line is a real `--ltl` site. `:10453` uses valid grammar
  (`"AUTH_FAIL -> eventually DB_TIMEOUT"`) and is a positive control, not a 16th target.
  15 + 1 = 16 reconciles with `grep -c`.
- **MF4 (highest risk)** — the replacement red arm IS observable, unlike the one it replaced.
  `native-build-smoke` (`ci.yml:627`) builds the release binary
  (`cargo build --release --no-default-features`), runs the `tests/e2e/test_native_*.py` glob
  (`ci.yml:718-726`), and sets `TG_REQUIRE_RG_PARITY: "1"`. The job's own comment states the point
  exactly: *"turns a missing binary from a skip into a FAILURE, so this can never masquerade as
  coverage."* The job is in `Semantic Release`'s `needs[]` (`ci.yml:1064`), so it is release-blocking.
  The both-arms defect is CLOSED, not relocated.
- **MF7** — the fourth prose lie is real: `ledger_store.py:389-391` says Slice 2 "deliberately keep
  plain `_resolve_root`, untouched", contradicted by the call sites at `:1198,1335` and by the
  already-corrected module docstring at `:48-57` in the same file.

## Verdict

**APPROVED to execute, conditional on two must-fixes** (both cheap, both in Task 1's existing scope):

- **MF-R2-1** — scope the plan's local full-suite command so it cannot collect
  `tests/e2e/test_routing_parity.py` (plan `:64`).
- **MF-R2-2** — add `docs/multi_agent_context_plane.md:148-151` as the fifth prose site in Task 1.

Q1 and Q2 — the two claims carrying real risk (the security surface and the 16-site census) — were
re-derived here independently and both hold exactly. Neither must-fix touches them.

## Method note — and the correction that outranks it

Round 1's council and codex overlapped on ONE finding out of nine. Round 2 needed no council: the
questions had become specific enough to answer with four commands. **Escalate breadth when the
question is "what is wrong with this?", and collapse to direct verification once the question is "is
this specific claim true?"**

**That framing is right and it still cost me two findings.** I answered Q3 and Q4 myself, concluded
"clean" and "deferred", and a scoped codex pass then found a real defect in each. The lesson is not
"always convene the council" — it is that *direct verification is only as good as the probe*, and both
of my probes were the wrong shape:

| my probe | why it could not find the defect |
|---|---|
| grep the plan for `cargo`/`test_routing_parity` | the violating line invokes the module by COLLECTION, not by name — the forbidden string appears nowhere in it |
| `pytest --collect-only \| grep -c` | `-x` aborted collection on an unrelated error 2 files earlier; the 0 meant "never reached", not "absent" |

Three instrument failures in one session, all mine, all the same family:

1. `awk` range pattern over `ci.yml` returned EMPTY — read as "the job does not build the binary".
   Caught by a positive control (job names grep). Would have produced a false BLOCK on the plan's
   strongest fix.
2. The `pytest --collect-only` zero above. Caught by the same discipline — reading the tail instead of
   the count.
3. Earlier the same day, a shared `/tmp` log collision led me to blame a healthy tool and kill its run.

**Every one was a false NEGATIVE, and every one was caught by a positive control rather than by
re-reading.** A probe that returns "nothing found" is making a claim about the world and needs the same
evidence as any other claim. The one time I skipped the control (Q3), the finding survived into a
committed audit document until an independent seat disproved it.
