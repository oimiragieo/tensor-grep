---
name: tensor-grep-debugging-playbook
description: Use when a tensor-grep (tg) run fails, hangs, returns wrong/empty/silently-degraded results, a CI check goes red, a release doesn't publish, a worktree agent's PR reports "No commits between main and <branch>" after a reported commit, or a wall-clock/timing-ratio test flakes on a loaded CI runner. Symptom-to-triage table, each row giving a discriminating experiment and a fix pointer, for CI red, release not published (push-race), search hangs/slow, silent-empty result (fail-closed contract), argv/flag injection, mock-green-but-real-dead FFI, dependency-cap silent downgrade, ranking flip, a `CliRunner`/`capfd` test that goes green-on-PR-red-on-main after a delegation/routing change, a latency fix/regression report that needs profiling-at-scale instead of a code-reading guess, a detached-HEAD worktree push that pushes a stale branch ref instead of the real commit, a timing-ratio test flake caused by a degenerate baseline below clock resolution, a diagnostic control that checked the wrong symbol or only proved a mechanism sufficient rather than operative, a windowed `gh run list`+filter query that misses an in-flight run, and a Windows-binary-from-Git-Bash path-domain false negative. Load BEFORE theorizing from a traceback or re-running a failing gate blind.
---

# tensor-grep Debugging Playbook

A symptom-first runbook for the recurring ways `tg` (or its CI/release pipeline) breaks. Every
row below was a real, previously-diagnosed failure in this repo — not a hypothetical. The single
biggest time-waster on record is **theorizing from a stack trace instead of reading the structured
failure first**: a README rewrite once cost 4 CI cycles because the team guessed at causes from
tracebacks instead of decoding which CI check actually failed (`AGENTS.md`). Do not repeat that.

## When NOT to use this skill

This is a *triage* skill (symptom → cause → experiment → fix pointer), not a how-to or a history
book. Reach for a sibling instead when:

| You need... | Use instead |
|---|---|
| The 4 registration sites for a new command/flag, PR-title→release-intent rules, what you may not edit | `tensor-grep-change-control` |
| The full postmortem of a *settled* battle (PyO3 FFI revert, README-rewrite gate break, fork-bomb binary disable) | `tensor-grep-failure-archaeology` |
| The architecture of the `ComputeBackend` contract / registration system itself, not just "how do I diagnose a violation" | `tensor-grep-architecture-contract` |
| How to *extend* the native-delegation field-coverage ratchet for a new `SearchConfig` field (forward / refuse / KNOWN_GAP), not "why is this test red" | `tensor-grep-config-and-flags` |
| Env var reference (`TG_RG_TIMEOUT_SECONDS`, `TG_SESSION_MAX`, …) beyond the ones a failure mode below needs | `tensor-grep-config-and-flags` |
| Toolchain/build setup (cargo off `PATH`, `maturin develop`, Windows gotchas) unrelated to a live failure | `tensor-grep-build-and-env` |
| `tg doctor` / `tg dogfood` field-by-field reference | `tensor-grep-diagnostics-and-tooling` |
| Local validation gate command reference (ruff/mypy/pytest) as a checklist, not a debug session | `tensor-grep-validation-and-qa` |
| Full release-and-positioning procedure, not "why didn't THIS release publish" | `tensor-grep-release-and-positioning` |
| Writing a NEW regression test for a hang-class bug (ReDoS/deadlock/lock-race/unbounded subprocess), or deciding whether a long-silent test/agent run is genuinely hung vs. slow-but-working | global skill `anti-hang-test-protocol` |
| The mandatory adversarial security-gate review before merging a money/auth/security/migration diff (verdict shape, Opus-as-codex-substitute) | `tensor-grep-backlog-campaign` Hard Rule 11 (cross-referenced from `tensor-grep-change-control`) |

If your symptom isn't in the table below, it's probably not covered here — check
`tensor-grep-failure-archaeology` for a prior occurrence before assuming it's novel.

## Jargon, defined once

- **Front door** — the entry point argv must pass through to be routed correctly. `tg`'s Python
  front door is `tensor_grep.cli.bootstrap:main_entry`; it intercepts plain-text searches and
  forwards them to `rg` *before* the Typer app sees argv. `CliRunner` in tests calls the Typer app
  directly and **bypasses this front door**, so a routing bug can be invisible to green unit tests.
- **Fail-closed** — on a real failure, raise/error instead of silently returning a clean-looking
  empty result or swapping to an engine that can't honor the requested semantics.
- **Push-race** — two `main`-bound merges overlapping so the second `git push origin main` from an
  in-flight semantic-release job is rejected non-fast-forward.
- **Registration site** — one of several places a new command/flag/route must be added; missing
  one makes it silently misroute instead of erroring loudly.
- **argv/flag injection (CWE-88)** — a user- or LLM-controlled value that begins with `-` gets
  parsed by a subprocess's *own* argument parser as a flag instead of as data, even when the
  parent process used list-argv (`shell=False`), which only stops *shell* injection.
- **Capture surface** — the mechanism a test reads a command's output through. `CliRunner`'s
  `result.stdout`/`result.output` captures only **in-process** writes (`typer.echo`/`click.echo`
  during `.invoke()`); pytest's `capfd` captures at the **OS file-descriptor level**, which is the
  only way to see output written by a real exec'd subprocess. They are not interchangeable, and
  using the wrong one doesn't error — it silently reads back empty. See §9.

## Triage table

| Symptom | Likely cause | Discriminating experiment | Fix pointer |
|---|---|---|---|
| CI check is red, unclear why | Wrong assumption from the traceback instead of the actual failing check (e.g. registration-completeness gate, not the code you touched) | `gh pr checks <PR>` → find the *named* failing job, then `gh run view <run-id> --json jobs` → `gh run view <run-id> --log-failed` | [§1](#1-ci-red-decode-the-structured-check-first) |
| PR merged, `main` CI green, but the version never showed up on PyPI / no `chore(release)` commit | EITHER a push-race (another merge landed mid-flight) OR a `needs:`-job flake (`Semantic Release` itself `skipped`) — these need DIFFERENT recovery, don't assume push-race by default | `gh run view <run-id> --json jobs` on the `Semantic Release` job: `! [rejected] main -> main` in its log = push-race (self-heals, don't rerun); a bare `skipped` conclusion with no rejection line = flaky upstream job (`gh run rerun --failed`) | [§2](#2-release-did-not-publish-push-race) |
| Local `gh pr merge` fails, but GitHub may have accepted the merge | The local checkout/worktree cannot update `main`; the remote merge request can still have succeeded | `gh pr view <PR> --json mergedAt` — a non-null `mergedAt` is the remote truth | Do not retry or double-merge; refresh local refs |
| `tg search` hangs, or errors after a long wait | Whole-repo / unscoped search hit one of THREE route-dependent bounds: the Python bootstrap 60s timeout, the native implicit-walk ceiling, or the native route's UNBOUNDED spawned-rg wait (often because `.tensor-grep/`, `_tg_refs/`, or a vendored `external_repos/` dir got walked) | Check the exit code — `124` = Python bootstrap timeout, `2` + "broad root scan refused" = native ceiling, no exit at all = the unbounded native arm | [§3](#3-search-hangsslow) |
| `tg` returns 0 matches / empty result but you expect matches | A backend swallowed a real failure (native panic, PCRE2 semantics mismatch, OOM'd subprocess) and returned a clean empty `SearchResult` instead of raising | Re-run with `--format rg` or check `routing_reason` / `fallback_reason` in `--json` output; compare against `rg` directly on the same pattern/path | [§4](#4-silent-empty-result-fail-closed-contract) |
| A pattern/path argument starting with `-` is silently interpreted as a flag by `rg`/`tg`/`git` (wrong output, not a crash) | A subprocess argv builder appended a user-controlled value as a bare positional with no `--` end-of-options sentinel | `tg search -- --weird-pattern PATH` vs `tg search --weird-pattern PATH` (should error) — same probe against any MCP tool call path | [§5](#5-argvflag-injection) |
| A test suite is green but the real binary/extension does the wrong thing (dropped flags, dead code path) | Test mocked the boundary (a monkeypatched function, a stubbed PyO3 class) instead of exercising the compiled extension or the published binary | Run the same call through the *installed* `tg` (not `CliRunner`, not a mocked backend) and check `tg doctor --json` / `HAVE_RUST` | [§6](#6-mock-green-real-dead) |
| A fresh Python install resolves `tensor-grep` to an old version with no error | An upper-bound dependency pin (e.g. `typer<0.26`) has no release compatible with the new Python, so the resolver silently downgrades the *whole package* | `pip index versions tensor-grep` vs what actually installed; check `pyproject.toml` for `<` pins on `typer`/`click`/`pydantic` | [§7](#7-dependency-cap-silent-downgrade) |
| Agent-capsule primary target flipped after an unrelated change (wrong file promoted to top) | The agent capsule's flat, no-IDF candidate scorer is corpus-fragile — a small corpus change can flip which candidate wins a tie. (`tg search --rank` and semantic search use a different, IDF-weighted BM25 scorer and are not known to share this bug.) | Re-run `tg agent PATH QUERY --json` before/after the change and diff `primary_target` + `ambiguity`/`ask_reasons` fields | [§8](#8-ranking-flip) |
| A `CliRunner` test reading `capfd` starts returning empty output / `JSONDecodeError` right after a delegation, routing-gate, or `--rank`/`--sort-files`-style flag change — often only on `main`/release CI, green on the PR | The code path moved from a **delegated subprocess** (needs fd-level `capfd`) to **in-process** `typer.echo` (needs `result.stdout`), or vice versa — the test's capture fixture didn't move with it. At the time of the incident PR CI did not build the native binary, so the mismatch never surfaced there (DATED — see §19's IN DISPUTE note). | Grep the refuse-tuple for the field you touched (`_NATIVE_TG_DELEGATION_DEFAULT_REQUIRED_FIELDS`, `src/tensor_grep/cli/main.py:1980` — re-derive with: grep -n '_NATIVE_TG_DELEGATION_DEFAULT_REQUIRED_FIELDS' src/tensor_grep/cli/main.py) — did it just start refusing (or allowing) native delegation? | [§9](#9-capture-surface-trap-capfd-vs-resultstdout) |
| A latency "fix" doesn't move the needle, or a reported regression can't be reproduced / doesn't match the diff | The hot path was inferred by reading code (a review/design pass) instead of measured — the real bottleneck is often a pure helper called redundantly in a hot loop, invisible from reading the "expensive-looking" function alone | Profile the **actual** slow command at realistic scale (not a toy input) and check top cumulative-time frames; Counter-wrap a suspect function to see call-count-vs-unique-input redundancy before designing a cache | [§10](#10-profile-at-scale-discipline-latency-claims) |
| PyPI/`chore(release)` published fine, "latest `main` run green" -- but a real regression shipped anyway | The workflow run's *aggregate* status hides one late-stage job's own red conclusion -- specifically the NEEDS-gated `release-tag-smoke` job (re-runs `scripts/agent_readiness.py` against an EDITABLE install of the release tag's source — not the PyPI wheel), which can stay red for releases at a time while `publish-pypi`/`publish-success-gate` keep going green; later non-release runs never re-run it | `gh run view <run-id> --json jobs` on the release run -> find the job named **`release-tag-smoke`** specifically -> read its own `conclusion`, don't infer from the run's overall status | [S11](#11-release-published-but-release-tag-smoke-stayed-red-masked-regression) |
| `Dependency & License Audit` job is red, but your diff doesn't touch any dependency file, and it reds EVERY open PR at once | A newly-disclosed CVE/RUSTSEC advisory against an already-pinned, unmodified dependency -- the strict-on-fixable `pip-audit`/`cargo-audit` gate fails for everyone until the floor moves, not just your branch | `gh run view <run-id> --log-failed` on the `Dependency & License Audit` job -- decode pip-audit's/cargo-audit's OWN structured output for the exact package + advisory ID + fixed-version | [S12](#12-dependency--license-audit-red-on-an-untouched-dependency-newly-disclosed-cve) |
| A shell one-liner that pipes `tg`/a probe script into `tail`/`grep`/`python -c ...` reports success (`exit 0`) even though the FIRST command in the pipe actually failed | Pipe exit-code masking: a shell pipeline's exit code is the LAST command's, not the first's | Re-run the first command alone and check its own `$?`/`$LASTEXITCODE`; or use `${PIPESTATUS[0]}` (bash) / split into two statements | [S13](#13-pipe-exit-code-masking) |
| An automated dogfood/verdict script says PASS or FAIL, but the underlying behavior looks wrong when you inspect it directly | The scoring logic misread the JSON shape (a renamed field, a nested-vs-top-level key) — a shape misread can silently read as either a clean pass or a clean fail | Read the RAW `--json` output at least once by eye before trusting the automated verdict for a new/changed probe | [S14](#14-raw-json-before-scoring) |
| A macOS-only CI job with a `Setup Rust` step (e.g. `test-rust-core`) fails with a network/timeout error during Rust toolchain setup — not a compile error, not something your diff touched | `rust_core/rust-toolchain.toml` pins an exact Rust version; the first `cargo` invocation with `rust_core/` as its working directory triggers an on-demand rustup fetch for that pin, and unlike the `rustup-init` bootstrap curl (`--retry 10`), rustup's own pinned-toolchain download had no retry | `gh run view <run-id> --log-failed` on the `Setup Rust` step specifically — a transient network/timeout message on the pinned-toolchain fetch, not a `rustc`/`cargo` compile error | [S15](#15-macos-rustup-pinned-toolchain-fetch-timeout-network-flake-already-mitigated) |
| `gh pr create` (or the GitHub UI) rejects a branch push with **"No commits between main and `<branch>`"** even though a worktree agent reported it committed real work | The agent committed on a DETACHED `HEAD` (not the named branch), then `git push origin <branchname>` pushed the branch REF — still sitting at `main`'s tip — instead of the commit; the work is not lost, just not reachable from the ref that was pushed | `git -C <worktree> rev-parse HEAD` vs `git -C <worktree> rev-parse <branchname>` — if they differ, the commit is real but the branch ref never moved | [S16](#16-no-commits-between-main-and-branch-detached-head-push) |
| Your fix to a **search** behaviour has NO observable effect, and tracing shows your new code IS being called | You are editing a code path the invocation never takes. A bare `tg search PAT` (and any invocation without a `_requires_full_cli` flag) is dispatched by `bootstrap.main_entry` straight to ripgrep via `_run_rg_passthrough`, which `raise SystemExit(...)`s with rg's exit code — **Typer never runs**, so every emitter in `cli/main.py` is downstream of a branch that never executes. A trace calling `main.app()` directly WILL show your code running, which is what makes this so convincing and so wrong. | Trace `sys.exit` and print the LINE it fires from: `m.sys.exit = lambda c: (print(traceback.extract_stack()[-2].lineno), orig(c))`. If it exits inside `bootstrap.py`, your edit in `main.py` is unreachable for that invocation. Then re-read `tensor-grep-architecture-contract` §"The front door: intercept before Typer" — this is documented, and not loading it cost a multi-hour detour on 2026-07-29. | [`tensor-grep-architecture-contract`](../tensor-grep-architecture-contract/SKILL.md) |
| A wall-clock/timing-ratio test flakes on a loaded Windows CI runner, and each attempt to widen its tolerance either doesn't fix it or makes it worse | A `max(baseline * N, floor)` assertion silently degenerates to the floor alone once the baseline collapses below the platform's clock resolution (or the "fix" attributed the flake to the wrong noise source without profiling) | `gh run view <run-id> --log-failed` for the exact overshoot numbers, then re-measure the baseline in isolation (does it read as a real, non-zero number across several runs?) and cProfile the real command before touching the assertion | [§17](#17-timing-testflake-de-flaking-a-ratiowall-clock-assertion) |
| A diagnostic control reports a capability "present" and rules out an otherwise-live hypothesis for a red gate, which then sits "cause unknown" | The control checked a symbol adjacent to, but different from, the one the code actually branches on (e.g. the importable `rust_core` extension module vs. the resolved native-binary path `resolve_native_tg_binary()`) | Grep the real branch point for the exact symbol it reads, then restate the control as "I set `<that symbol>` to `<value>`" rather than the capability you believe it proves | [§18](#18-a-control-that-names-the-wrong-symbol-falsely-exonerates-the-right-hypothesis) |
| A control reproduces a CI failure byte-for-byte and gets treated as "confirmed" before a fix is designed and dispatched around it | The control's forced mechanism is *sufficient* to reproduce the symptom, but nobody checked whether the REAL failing job's own config can even reach that mechanism | Read the real failing job's own config/log for the step in question -- does it execute the code path your control forced? | [§19](#19-a-reproduced-failure-is-not-proof-of-the-operative-mechanism) |
| A merge-gate or release-monitor check runs `gh run list --branch ... --limit N` (optionally filtered by SHA) and reports "0 in flight" / "all terminal" while a real run is still mid-publish | The limited window filled with unrelated rows sharing the same filter (other workflows on the branch, or cron-scheduled runs that happen to fire on the same commit SHA), pushing the real run out of view | Query the ONE run by its unique ID (`gh run view <run-id>`), never a list plus a filter; if you must list first, read every row's workflow name, not just whether the filter matched | [§20](#20-a-windowed-list-plus-filter-query-gives-a-false-complete) |
| A dogfood/verification run through a Windows-built `tg` binary invoked from Git Bash reports zero files found against a fix that actually works | The invocation handed the binary a POSIX-style path (e.g. `/tmp/...`) it cannot resolve — NOT the default Git-Bash mode (which converts the cwd), but a path-conversion-disabled or BRIDGED invocation (a shim/env-var/argument carrying the shell's untranslated POSIX string), so the binary walks an empty/nonexistent directory in its own path domain | Re-run the identical command with an explicit Windows-form path (`C:\...`) instead of the defaulted/bridged path, and compare | [§21](#21-the-setup-lies-git-bash-cwd-vs-windows-binary-path-domain) |
| A stray untracked `nul` file appears in `git status` on Windows (and `Remove-Item`/`Test-Path` can't touch it), OR a WSL-side test run misbehaves and you suspect the wrong interpreter/venv is executing | `2>nul` redirect artifact (reserved device name blocks PowerShell removal), or a broken system WSL stdlib / a WSL `uv` pointed at the Windows `.venv` (A60) | `rm -f ./nul` via Git Bash; probe WSL interpreter provenance with a bare `import shutil` and confirm a WSL-local managed venv | [§22](#22-environment-artifacts-2026-08-12-session-lessons) |
| A skill/draft "where's the file?" shell probe hangs ~1–2 min then exits `-1` / `4294967295` with empty output, even though the skill already exists in the worktree | `Get-ChildItem -Recurse -Force $env:TEMP` (or similar whole-TEMP walk) hits locked/inaccessible Windows temp trees and never finishes usefully; HTML-escaped redirects like `2&gt;$null` can also mangle the command | `Test-Path .claude/skills/<name>/SKILL.md`; `git status --porcelain -- .claude/skills/<name>`; never recurse all of `$TEMP` — top-level `$TEMP` filter only if needed | Look in the worktree skill folder / PR branch first; the hang is the instrument, not a missing skill |

---

## 1. CI red — decode the structured check first

**Do not read the traceback and start theorizing.** Identify which *named* job/check failed, then
read only that job's failed-step log.

```bash
gh pr checks <PR-number>              # which named check(s) actually failed
gh run view <run-id> --json jobs      # confirm the job name, e.g. "Semantic Release", "test-python"
gh run view <run-id> --log-failed     # only the failed step's log — not the whole 20-minute run
```

Why this matters here specifically: this repo's CI enforces far more than tests — formatting,
typing, cross-platform behavior, release-workflow contracts, package-manager contracts, and
artifact/version parity all block the same pipeline (`docs/CI_PIPELINE.md`). A registration
mismatch (new command/flag missing one of its sites) fails the **blocking registration-completeness
gate**, which is a *different* job than `test-python`, and reading a Python traceback from the
wrong job wastes a cycle. Registration sites and rules live in `tensor-grep-change-control`; the
checker itself is `src/tensor_grep/core/registration_check.py` (`check_group_smart`,
`extract_members`), exercised by `tests/unit/test_registration_check.py`.

Known real incident: a README rewrite broke ~14 governance tests **and** a separate
`agent-readiness` release-blocker gate; 4 CI cycles were wasted because the team theorized from
tracebacks instead of reading which check failed first (root cause was two unrelated layers: a
missing `ast-grep` CLI dependency, and `uv run` re-syncing away the `[dev]` tree-sitter extra).
Decode the check name before touching code.

A second, more subtle version of the same trap: a red `test-python` job whose failure signature
(`JSONDecodeError` from an empty captured string) *looks* like a routing regression but is actually
a stale test fixture — the test was reading the wrong capture stream after a delegation-routing
change moved the command from a subprocess path to an in-process one. Reading the traceback alone
sends you looking for a routing bug that doesn't exist; the fix pointer is §9, not a backend change.

If the failing check is the `Semantic Release` job specifically, go to §2, not here.

## 2. Release did not publish (push-race)

The real publish step is the **`Semantic Release` job inside `.github/workflows/ci.yml`**, which
compiles native assets before publishing (~6 minutes) — that whole window is a race window where a
second merge to `main` can knock out the first run's final push.

**Discriminating experiment:**

```bash
gh run view <run-id> --json jobs                 # find the "Semantic Release" job's run/conclusion
gh run view <run-id> --log-failed                 # read its failed step only
```

A line reading `! [rejected]  main -> main` is the push-race signature. **Do not panic-rerun** — the
failure self-heals on the next push-to-`main` (version is derived from git tags, not the failed
run's state). Full mechanism, the `v1.17.23`/#318/#319 receipt, and the one-merge-per-tick
discipline to prevent recurrence: `tensor-grep-release-and-positioning` §1.5 /
`tensor-grep-failure-archaeology` Battle 6.

**A SECOND, different release-failure branch does NOT self-heal — read the job conclusion before
picking a recovery, don't assume every "release didn't publish" is a push-race:**

| Branch | Signature | Recovery |
|---|---|---|
| **Push-race** (this section) | `! [rejected] main -> main` in the `Semantic Release` job's own log | Self-heals on the next push. Do NOT rerun. |
| **`needs:`-job flake (C-release-flake)** | `Semantic Release` shows `skipped` (not `failure`), no rejection line — a flaky upstream job in its `needs:` list failed | Does NOT self-heal — the flaky job's cause doesn't change between pushes. Run `gh run rerun --failed` on the SAME run (re-executes only the failed job). Receipts: v1.76.9/#612-613 (a timing-flaky heartbeat test); v1.92.2/#701 (the index-lock concurrency test rewritten after 2 releases of flaking). |

**Rapid-window batch-merge is a third, benign shape — don't misdiagnose it as either of the above.**
Several independently-green PRs merging ~15-20s apart can show an intermediate `cancelled` or
rejected-push run that looks alarming in isolation, but is fine IF the LAST run in the sequence
completes and publishes (receipt: v1.93.0/#703-706, runs `29890576036` rejected-only / `29890612228`
published). See `tensor-grep-change-control` Part 7 (C-batch) before treating a mid-sequence
`cancelled` conclusion as a failure needing recovery at all.

## 3. Search hangs/slow

`tg search` does NOT have one timeout contract — it has **three distinct outcomes depending on
which route executes the search** (verified 2026-08-12 against `bootstrap.py` +
`rust_core/src/rg_passthrough.rs`; SUPERSEDES this section's earlier wording that claimed BOTH
routes fail fast at 60s/exit 124):

| Route | Bound | Outcome on a pathological walk |
|---|---|---|
| Python bootstrap rg-forwarding (`bootstrap.main_entry` plain-text passthrough) | `TG_RG_TIMEOUT_SECONDS` wall timeout, default **60s** (`configured_ripgrep_timeout_seconds()`, `src/tensor_grep/cli/subprocess_policy.py`) | child killed, process exits **124** with a scope-the-search stderr hint |
| Native route, IMPLICIT (no user path) walk (`execute_ripgrep_search`, `rust_core/src/rg_passthrough.rs`) | `IMPLICIT_SEARCH_WALK_FILE_CEILING` (= 1500) bounded walk probe BEFORE any rg spawn (`check_implicit_walk_ceiling`, the function's first statement) | refusal to stderr ("broad root scan refused as a safety guard") + exit **2**, fail-fast, rg never spawned |
| Native route, spawned rg (a scoped search, or an implicit walk under the ceiling) | **NONE** — the spawned rg is waited on via `Command::status()` with NO wall timeout | rg itself walks unbounded; the native route does not kill it, so a genuinely hung native-route search has no exit at all |

The 60s default was lowered from 600s specifically because ripgrep does GB/s and a >60s search
means something pathological is being scanned (an unexcluded huge/index directory), not a
legitimately slow query. On the PYTHON route's timeout, the child is killed and the process exits
**124** with a stderr hint to scope the search or raise the timeout (`src/tensor_grep/cli/bootstrap.py`,
backward-compat shim path and the primary `Popen`/`_terminate_child` path both `return 124` —
re-verify with `grep -n "return 124" src/tensor_grep/cli/bootstrap.py`; was `:1020`/`:1063-1071`,
then `:1269`/`:1320`, now `:1353`/`:1404` — line numbers drift every release). The native route's
ceiling applies ONLY when the walk is implicit (`path_was_implicit`); a user-scoped native search
skips the ceiling and spawns rg directly into the unbounded-wait arm.

**Discriminating experiment:** check the exit code — it names the route. `124` = the PYTHON
bootstrap timeout fired (not a crash). `2` with the "broad root scan refused" marker = the native
implicit-walk ceiling fired BEFORE any rg spawn. NO exit at all (a real hang) = the unbounded
spawned-rg arm — `Ctrl-C` is legitimate there, and scoping the search is the fix. Compare a scoped
vs. unscoped run:

```bash
tg search PATTERN                 # unscoped over a large/whole repo — can hit the 60s wall
tg search PATTERN src/            # scoped — typically <1s
```

**Root cause when it fires on a legitimately-sized repo:** `tg`'s own index/state directories
(`.tensor-grep/`, `_tg_refs/`, `.tg_semantic_index/`) and vendored corpora (e.g.
`benchmarks/external_repos/`) are not excluded from an unscoped walk, so searching from the repo
root walks tg's own indices too.

**Fix / workaround:** always scope searches to a path, glob, or file type. Raise
`TG_RG_TIMEOUT_SECONDS` (or `TG_SUBPROCESS_TIMEOUT_SECONDS` for non-search subprocess calls) only
for a genuinely huge monorepo — do not raise it to paper over an unscoped-walk problem. A
trigram-hybrid index is the tracked structural fix; own-dir excludes alone were tried and did not
fully resolve full-tree speed. Full env-var reference: `tensor-grep-config-and-flags`.

**Related, known limitation — `tg inventory --deadline` on a pathological workspace-union tree:**
`tg inventory --deadline` is a *different* command from `tg search` but shares the same root-cause
class as the hang above (an unbounded directory read), and normally bounds cleanly per project
(truncates at N files, stamps `truncation_cause = "deadline"` — `build_inventory` has since moved out
of `main.py` into its own module; re-verify with
`grep -n 'truncation_cause = "deadline"' src/tensor_grep/cli/inventory.py`; was in `main.py` -- the `:8404`/`:8420` pins pointed INSIDE a `--deadline` option block deleted
by the 2026-08-23 de-duplication, so they have no successor; now `inventory.py:318`). On a PATHOLOGICAL **workspace-union** tree — many
unrelated repos flattened under one huge root, not a single normal project — it can still blow its
deadline: the shared walker `_iter_repo_files` (re-verify with `grep -n "def _iter_repo_files"
src/tensor_grep/cli/repo_map.py`; was `:1143`, now `:1144`) reads an entire huge directory's entries
in one non-lazy `list(os.scandir(normalized_root))` call inside that same function (re-verify with
`grep -n "list(os.scandir(normalized_root))" src/tensor_grep/cli/repo_map.py`; was `:1009`, now
`:1172` — the `def` itself barely moved but this internal call drifted much further as the
function's docstring grew) before its own per-file deadline check gets a chance to run, so one
abnormally large subdirectory can exceed the deadline before the mid-walk check fires even once. This is a KNOWN, accepted, low-priority edge (rare shape; verified against a real
300k+-file multi-project workspace) — not worth a load-bearing lazy-`scandir` rewrite. Don't
re-diagnose it as a new bug; if the SAME deadline-blown symptom shows up on a normal single-project
repo (not a workspace union), that IS a regression and should be treated as a new incident, not
this one.

## 4. Silent-empty result (fail-closed contract)

Every `ComputeBackend` must raise `BackendExecutionError` on a real failure — never return a clean
`0-match SearchResult`, and never silently swap to an engine that cannot preserve the requested
semantics (`src/tensor_grep/backends/base.py:6-14`). This has been violated repeatedly; the
recurring anti-pattern is a bare `except Exception:` that returns empty or falls through to a
different engine. A context tool reporting a trustworthy-looking "no matches" when the real
answer is "the backend crashed" is the one failure this repo treats as unacceptable
(`AGENTS.md`, "Backend Fail-Closed Contract").

**Discriminating experiment:** run the same pattern/path directly through `rg` and compare. If `rg`
finds matches but `tg` reports zero, suspect a swallowed backend error, not a real no-match. Then
inspect `--json` output for `routing_reason` / `fallback_reason` — a populated `fallback_reason`
means a *visible*, legitimate degraded path (e.g. CyBERT provider unavailable); an *absent* one on
a result you believe is wrong means look for a silent swap. **Current, still-live example of the
correct visible-degrade shape (v1.77.0, #189):** `tg find`'s JSON carries `rank_fallback_reason` when
the dense leg degrades to BM25-only (the `semantic` extra or model is unavailable) — a legitimate,
fully-supported result, distinguishable from a real backend failure (which instead raises
`BackendExecutionError` -> exit 2, per `tensor-grep-run-and-operate` §11c). If you see a `tg find`
result with NEITHER `rank_fallback_reason` set NOR a nonzero exit on a run you expected the dense leg
to participate in, that is the silent-swap bug this section targets, not a normal degrade.

**Ground-truth example of the correct pattern** (`src/tensor_grep/backends/rust_backend.py:260-278`):
a PCRE2 search that fails inside the native ripgrep bridge raises `BackendExecutionError` and
explicitly refuses to fall back to an engine that doesn't implement PCRE2 semantics — it does NOT
silently re-run the pattern through the Python-regex engine (which would return wrong matches,
not zero matches, but the principle is the same: don't swap engines invisibly for a
semantics-changing flag). Contrast with a legitimate degraded fallback (limit/sort flags the
Python fallback can't honor), which instead sets a visible `bridge_fallback_reason` on the result.

**Fix pointer:** if you find a bare `except Exception: return SearchResult(...)` (or similar) in a
backend, that is the bug class. Fail closed for any flag/contract the fallback cannot preserve
(raise, don't swap); if a degraded fallback is legitimate, set `fallback_reason` +
`routing_reason` so JSON/CLI consumers can tell degraded output from real output. Deep architecture
of this contract: `tensor-grep-architecture-contract`.

## 5. Argv/flag injection

A list-argv subprocess call (`shell=False`) stops *shell* injection but not *flag* injection: a
value beginning with `-` is parsed by the **child's own** option parser as a flag. This is CWE-88 —
the same class behind live MCP-server CVEs (CVE-2026-5058 aws-mcp-server, CVE-2026-23744,
CVE-2026-30623 Anthropic MCP SDK) — and it matters here because MCP tool handlers forward
LLM-controlled parameter values straight into `tg`/`rg`/`git` subprocess argv.

**Discriminating experiment:**

```bash
tg search -- --looks-like-a-flag PATH     # with -- sentinel: treated as pattern data
tg search --looks-like-a-flag PATH        # without: rg/tg's own parser errors on the "flag"
```

Run the same probe through any code path that builds subprocess argv from a
pattern/path/replacement value (MCP tool handlers, rewrite commands) — a value beginning with `-`
should error or be treated as data, never silently change tg's own behavior.

**Fixed reference implementation** (`src/tensor_grep/cli/mcp_server.py`, `_build_rewrite_command` /
`_build_index_search_command` — re-verify with
`grep -n "def _build_rewrite_command\|def _build_index_search_command" src/tensor_grep/cli/mcp_server.py`;
was `:1259`/`:1310`, now `:1328`/`:1379`, +69 each):
a `--` end-of-options sentinel is inserted before the user-controlled `pattern`/`path` positionals,
with an inline comment explaining why.

**Round-4 native-passthrough gap — RESOLVED, do not reopen (verified current at v1.49.3).**
`rust_core/src/rg_passthrough.rs` appending `paths` directly with no `--` sentinel (a directory
literally named `-l` parsed by `rg` as the `-l`/files-with-matches flag instead of a path) was fixed
in `#326` (v1.17.26), silently regressed by a later refactor, then restored in `#370` (v1.28.1) as
the extracted, unit-tested `ripgrep_operand_args` helper (`rust_core/src/rg_passthrough.rs` — see the
grep below; was `:581-600`, now `:584-603`)
— the sentinel is now pushed unconditionally before the path loop whenever `!args.paths.is_empty()`.
Patterns going through `-e` were never affected (`-e` consumes the next token as its value regardless
of a leading `-`); only bare path positionals were ever at risk, and that risk is now closed. Verify
with `grep -n "fn ripgrep_operand_args" -A 20 rust_core/src/rg_passthrough.rs` before relying on this
— do not trust the naive `grep -n "for path in &args.paths"` re-check below, it still matches (the
loop still exists, just now *after* the unconditional sentinel push) and would misread as "still
open" if you stop at the grep hit without reading the surrounding function.

**Caveats worth knowing before you conclude a builder is safe:** `--` protects only what comes
*after* it — a positional placed *before* `--` is still injectable; it does not gate
`--flag=VALUE` forms; and not every binary honors `--` the same way, so **dogfood the real binary**
rather than trusting the argv list alone. None of {validate the value, list-argv, `--` sentinel}
alone is complete — they layer.

## 6. Mock-green-real-dead

A test can pass because it mocked the exact boundary that was actually broken — a monkeypatched
function, or a Python-side stub standing in for the compiled PyO3 extension. This has happened for
real: mock-based FFI tests were green while the real Rust bridge was dead (it dropped every
forwarded flag and silently fell back to the Python engine) — the dead-passthrough bug and the
missing-flag bug compounded, because the bridge call itself never got exercised
(`AGENTS.md`, "Local Dev Gotchas").

**Discriminating experiment:** does the test import/patch `tensor_grep.rust_core` (or its Python
wrapper `RustCoreBackend`, the `try: from tensor_grep.rust_core import RustBackend as
NativeRustBackend` / `HAVE_RUST` block in `src/tensor_grep/backends/rust_backend.py` — re-verify with
`grep -n "HAVE_RUST" src/tensor_grep/backends/rust_backend.py`; was `:28-33`, now `:9-14`), or does it
patch something *around* that boundary? If a test replaces `bootstrap.run_subprocess` or stubs
`RustCoreBackend.inner`, it is validating call shape, not that the real extension does the right
thing.

```bash
uv run python -c "from tensor_grep.backends.rust_backend import HAVE_RUST; print(HAVE_RUST)"
# then, separately, exercise the REAL installed binary end to end (not CliRunner):
tg search --pcre2 'foo(bar)?' src/            # confirm the flag actually reaches rg with real semantics
```

Same principle one layer up: `CliRunner` invokes the Typer app directly and bypasses the
`tensor_grep.cli.bootstrap:main_entry` front door entirely, so a routing bug in the bootstrap layer
is invisible to `CliRunner`-based tests no matter how many pass. After any change to a search flag,
a command, or the FFI boundary, dogfood the **installed published binary** with the harness at
`scripts/dogfood/` (`Dockerfile` + `dogfood_features.py`) rather than trusting unit tests alone
(`AGENTS.md`, "Dogfood the Real Binary, Not CliRunner"). See `dogfood-the-shipped-artifact` (global
skill) for the full post-release procedure.

## 7. Dependency-cap silent downgrade

An upper-bound pin (e.g. `typer<0.26`) can silently downgrade the **entire package** on a newer
Python if no release in that range is compatible with it — `pip`/`uv` resolve the whole install
down to a stale version with **no error**, because `requires-python>=X` has no upper bound to catch
the mismatch. Receipt: on Python 3.14, `uv tool install tensor-grep` with an unsatisfiable
`typer<0.25` range resolved to a stale `1.13.35` instead of erroring. Current pin, chosen to thread
both constraints (`pyproject.toml:560-566`):

```
typer>=0.12,<0.26
```

The comment there (`pyproject.toml:560-565`) explains why the cap can't simply be dropped: typer
0.26 removed `click.testing.CliRunner` inheritance, breaking `CliRunner.isolated_filesystem()`
which ~49 tests rely on.

**Discriminating experiment:**

```bash
pip index versions tensor-grep                 # what SHOULD be installable
uvx --refresh-package tensor-grep --from tensor-grep==<expected-version> tg --version
```

If a fresh install on a new Python resolves to an old `tg --version`, do not assume
`requires-python` is wrong — grep `pyproject.toml` for `<` upper bounds on `typer`, `click`,
`pydantic`, or other transitive deps first; that is the class of bug this was.

## 8. Ranking flip

The agent capsule's **primary-target candidate selection** relies on three scoring helpers in
`repo_map.py` — `_score_symbol`, `_score_import_entry`, `_score_file_source_terms` — plus
`score_term_overlap` (`src/tensor_grep/core/retrieval_lexical.py:15`), which
`_score_file_source_terms` calls. Re-verify their positions before citing one; they do NOT move
together, only relative to each other:

```bash
grep -n "def _score_symbol\|def _score_import_entry\|def _score_file_source_terms" src/tensor_grep/cli/repo_map.py
grep -n "score_term_overlap(" src/tensor_grep/cli/repo_map.py
```

`_score_symbol` used to sit **after** the other two (`:8211` vs `:7725`/`:7732`, with the call site
at `:7737`) and now sits **before** them (`:8194` vs `:8221`/`:8228`, call site now `:8233`) — a
relative reordering, not a uniform shift, so don't assume a fixed offset holds between any two of
these four line numbers. Together they implement a **flat, no-IDF** set-membership scorer plus a
hard top-N candidate cap — an acknowledged, not-yet-fixed weak point. A small, unrelated corpus change can flip which
candidate wins a near-tie, and that flip is invisible to the call graph (nothing "broke" in the
traditional sense — the ranking function just picked a different winner). This produced a real
incident: an unrelated GPU-code change flipped the agent capsule's top pick from "tied, ask the
user" to "confidently pick the wrong marker/no-op function" with zero call-graph signal.

Note: `tg search --rank` (`rerank_by_bm25()`, `src/tensor_grep/core/reranker.py`) and local semantic
search (`src/tensor_grep/core/semantic_index.py`) both route through `Bm25Index`
(`src/tensor_grep/core/retrieval_bm25.py`) — a real Okapi BM25 scorer **with IDF**, term-frequency
saturation, and length normalization. They are a different, IDF-weighted scorer and are not known to
share this specific flat-scorer fragility; don't assume a `--rank` reorder flip has the same root
cause as an agent-capsule primary-target flip.

**Discriminating experiment:** these are two different code paths — run whichever one matches the
surface you're chasing, not both interchangeably:

```bash
# BM25-reranked search order (src/tensor_grep/core/reranker.py) — top-match ordering only,
# no ambiguity/candidate concept:
tg search PATTERN PATH --rank --json > before.json   # on the pre-change commit
tg search PATTERN PATH --rank --json > after.json     # on the post-change commit

# Agent capsule (src/tensor_grep/cli/agent_capsule.py) — primary-target selection, the surface
# that actually emits ambiguity/ask metadata:
tg agent PATH QUERY --json > before.json              # on the pre-change commit
tg agent PATH QUERY --json > after.json                # on the post-change commit
```

For the agent capsule specifically, check the `ambiguity` / `ask_reasons` fields
(`src/tensor_grep/cli/agent_capsule.py`) rather than only the `primary_target` — a **degrade-to-ask
safety floor** (the `# Degrade-to-ask safety floor:` comment in `agent_capsule.py` — re-verify
with `grep -n "Degrade-to-ask safety floor" src/tensor_grep/cli/agent_capsule.py`; was `:3224`, now
`:3244`, line numbers drift every release) forces `ask_user`-style output whenever ranking
buried the real implementation behind an unrequested marker/no-op helper, so a correctly-behaving
flip should surface as `ambiguity`/`ask_user_before_editing` metadata, not a silent wrong answer.
If you see a confident wrong `primary_target` with no ambiguity signal, that is a regression in the
safety floor itself, not just scorer fragility — treat it as higher severity. `tg search --rank`
has no equivalent safety floor to check; it only reorders matches.

**What this is NOT:** a fix for the underlying flat scorer. The safety floor added in response to
the incident above only prevents *silent* wrong picks; it does not make the ranking itself
IDF-aware or less corpus-fragile. That remains a tracked, separate, benchmarked `repo_map`
follow-up. Do not claim ranking is "fixed" — only that a floor exists under it.

## 9. Capture-surface trap (`capfd` vs `result.stdout`)

A `CliRunner`-based test can read a command's output two different ways, and they are **not
interchangeable** (see "Capture surface" in Jargon above):

- `result.stdout` / `result.output` — `CliRunner` redirects stdout **in-process** during
  `.invoke()`; correct when the exercised path writes via `typer.echo`/`click.echo` without ever
  leaving the Python process.
- `capfd.readouterr().out` — pytest's **file-descriptor-level** capture; required when the
  exercised path execs a real OS subprocess (e.g. a native-tg delegation), because that
  subprocess's writes never pass through `CliRunner`'s in-process redirect.

Using the wrong one does not error — it silently returns an **empty string**, which then fails
downstream (`json.loads("")` → `JSONDecodeError`) in a way that reads as "the command produced no
output," not "you're reading the wrong stream." It is the same shape as the fail-closed-vs-
silent-empty trap in §4, one layer up in the test harness instead of the backend.

**Real incident (round-4, commit `ab717a1`, #343 as a follow-up to #342, v1.19.0):** #342 added
`rank_bm25`/`sort_files` to `_NATIVE_TG_DELEGATION_DEFAULT_REQUIRED_FIELDS` (now at
`src/tensor_grep/cli/main.py:1987-1974`, inside the tuple starting `:1966`) so `tg search --rank` correctly **refuses** native (re-derive with: grep -n '_NATIVE_TG_DELEGATION_DEFAULT_REQUIRED_FIELDS' src/tensor_grep/cli/main.py)
delegation and the BM25 rerank runs in-process instead of via a delegated subprocess.
`test_search_rank_reorders_by_bm25` (`tests/integration/test_bm25_search_flag.py`) had been written
against the *old* delegated behavior and read `capfd.readouterr().out`, which had only ever
captured real output while `--rank` wrongly delegated. Once `--rank` started refusing delegation,
the JSON began going through `typer.echo` → `CliRunner`'s captured `result.stdout` instead, and
`capfd` read back empty → `JSONDecodeError` on every `main`/release `test-python` job. It stayed
green on the PR because PR CI did not build the native binary AT THE TIME OF THE INCIDENT
(DATED/HISTORICAL for v1.19.0 — the `ab717a1` commit message states this explicitly; do NOT read
this as a settled present-tense claim about PR CI: whether `test-python` ever reaches a real
native binary is marked IN DISPUTE in `ci.yml` itself and re-measured by the matrix-wide
non-gating "Task 22 diagnostic" step — see §19) — a second trap
layered on the first: the same test can be green on a PR and red on `main` for a reason that has
nothing to do with whether the PR's diff is correct.

**Fix:** read `result.stdout` (`tests/integration/test_bm25_search_flag.py`, current version)
instead of `capfd.readouterr().out`; the now-unused `pytest.CaptureFixture` import was dropped.

**Discriminating experiment:** if a `CliRunner` test that reads `capfd` starts failing right after a
delegation/routing/gating change, first ask "does this flag/config still delegate to a real
subprocess after my change?" — grep the refuse-tuple
(`_NATIVE_TG_DELEGATION_DEFAULT_REQUIRED_FIELDS`, `src/tensor_grep/cli/main.py:1980` — re-derive with: grep -n '_NATIVE_TG_DELEGATION_DEFAULT_REQUIRED_FIELDS' src/tensor_grep/cli/main.py) for the field
you touched. If it now refuses delegation (or newly allows it), the correct capture fixture flips
too.

**Rule going forward:** `capfd` in a `CliRunner` test is an **implicit assertion** that the
exercised path execs a real subprocess. Any change to native-delegation gating
(`_build_native_tg_search_command`, `_NATIVE_TG_DELEGATION_DEFAULT_REQUIRED_FIELDS`, or the
refuse-tuple governed by `tests/unit/test_native_delegation_field_coverage.py` — see
`tensor-grep-config-and-flags` for how to extend it) **must run `tests/integration/` locally**, not
just `tests/unit/`, before pushing: the fd-vs-in-process split is an integration-level concern, and
it only reproduces when the native binary is actually built, which most local dev loops skip
(the "and so does PR CI" half of this sentence is DATED/HISTORICAL — see §19's IN DISPUTE note and
the non-gating matrix-wide "Task 22 diagnostic" step in `ci.yml`; re-measure rather than assume).

```bash
uv run pytest tests/integration/ -v      # run before pushing any delegation/routing-gate change
```

## 10. Profile-at-scale discipline (latency claims)

**For a latency question, the profiler is the oracle — not a code-review pass, and not a function
that "looks expensive" by inspection.** This repo has a receipt of a review process guessing the
wrong hot path, and a receipt of a reported regression that turned out to be measurement noise;
both were only resolved by profiling the actual command at realistic scale.

**Incident 1 — a guessed cache target was mostly wrong (commit `bb5dc59`, PR #345, v1.19.2):** a
latency-diagnosis pass on `tg blast-radius` identified AST parsing (`compile()`) as the likely hot
path by reading the code, and reasoned toward caching it. Profiling the *actual* `tg blast-radius`
call at depth 2 on this repo showed `compile()` was only **3.6% of runtime** — caching it would
have saved roughly 3%. The real hotspot, invisible from code review, was `_module_aliases_for_path`
(`src/tensor_grep/cli/repo_map.py` — re-verify with
`grep -n "def _module_aliases_for_path" src/tensor_grep/cli/repo_map.py`; was `:8197`, now `:8693`),
called **1,431,341 times** for ~1,000 unique path inputs
from the reverse-import-graph / PageRank loops — 6.1s self / 38s cumulative of a 62s run. The commit
message states this directly: "this corrects the regression-hunt synthesis, which guessed AST-parse
caching (would have saved ~3%) — the real hotspot only showed under measurement."

**Incident 2 — a reported regression was environmental noise, not a real one (same commit
message):** an AI-user-reported "+33% slower" (188s→250s) on the callers/blast-radius path was
separately investigated by profiling `tg callers` directly: the code path was byte-identical across
the two versions being compared (`v1.17.31`→`HEAD`), and a live `cProfile` capture had **zero
`ripgrep_backend` frames** on the call path the regression theory required. Conclusion: noise, not a
regression. Don't design a fix for a slowdown you have not reproduced under a profiler.

**Incident 3 — a warm end-to-end dogfood run hid a real ~54% win (commit `9a2a01c`, PR #719,
v1.93.9):** `_python_imports_and_symbols` (`src/tensor_grep/cli/repo_map.py:2166` — re-derive with: grep -n '_python_imports_and_symbols' src/tensor_grep/cli/repo_map.py) was merged from
three separate `ast.walk(tree)` passes (imports, symbols, dynamic-imports) into one
dispatch-by-node-type pass — the same general family of redundant-work-elimination fix as the
"Technique" below (there it's redundant *calls*; here it's redundant *tree walks* over the same
parsed data), proved byte-identical because the AST node subclasses it dispatches on are
mutually-exclusive (every node still lands in exactly one branch) and output order is unchanged —
the trailing `sorted(...)`/`.sort(...)` calls already normalize it regardless of append order.
Measuring the fix through a **warm** `tg orient` dogfood run (repeat calls against files whose parse
result was already cached) read as **-36%** — apparently a regression, not a win. The warm run never exercised
the change: on a cache hit the merged-vs-unmerged `ast.walk` code doesn't run at all, so timing the
optimization end-to-end through a warm path measures the cache, not the fix. Isolating the function
directly — a fresh process (cold cache), a single pass over distinct real files, old-vs-new — showed
it is genuinely **~54% faster** (961ms→446ms on the probe corpus), verified byte-identical by a
monkeypatched-`ast.walk`-call-count assertion plus an old-vs-new diff over a
static/nested/relative-imports/classes/sync+async/dynamic-import corpus. The companion
validation-scan optimization (`_framework_test_pattern_bonus` in `src/tensor_grep/cli/repo_map.py` —
re-verify with `grep -n "def _framework_test_pattern_bonus" src/tensor_grep/cli/repo_map.py`; was
`:10616`, now `:11112` — commit `d2c1266`, PR #723, v1.93.10 — a textual pre-check
that skips an expensive per-candidate AST parse when nothing in `expanded_terms` could possibly
score) shows the identical shape: **~68% faster** (3657ms→1172ms) in isolation, invisible from a
warm end-to-end read.

**Rule (microbench-on-the-shipped-wheel):** never validate — or reject — a cold-path optimization
by timing a warm end-to-end dogfood command; a warm run's cache hits can make a real win invisible
or a real regression look like a wash. Instead, microbenchmark the target function directly,
isolated, in a **fresh process** (cold cache) against the **published wheel**
(`uvx --from tensor-grep==<ver>`), a single pass over **distinct** inputs so no run benefits from an
earlier run's warm cache, old-vs-new, and assert output-identity (not just wall-time). SUPERSEDED
(2026-08-12): this rule previously sold `total == total` (an aggregate count equal on both sides)
as proof the change is byte-identical — it is NOT: two different outputs can share a total, so a
count equality is at best a smoke precondition. The actual proof is DIRECT output equality (diff
the full serialized outputs old-vs-new and require zero difference) or a field-by-field
differential over every emitted field. The ~54%/~68% receipts above were proven by the stronger
forms (a monkeypatched call-count assertion PLUS an old-vs-new diff over the probe corpus), not by
a bare count equality. Re-verify with
`grep -n "def _python_imports_and_symbols\|def _framework_test_pattern_bonus" src/tensor_grep/cli/repo_map.py`
before trusting these line numbers on a later version.

**Technique that found the real hotspot:** before designing a cache or optimization for a suspect
function, wrap or monkeypatch it with a call counter keyed by its argument(s)
(`collections.Counter`) around the *actual* slow command — not a microbenchmark, not a toy input —
and look for a function called far more times than there are distinct outputs. A function called
1.4M times for ~1,000 unique inputs is pure redundant work; a plain `@lru_cache` (no invalidation
key needed for a pure function) collapses it to one build per distinct input. This is the
discriminating step a code-only review cannot substitute for: `compile()` genuinely runs once per
file, so it "looks" proportional to input size and isn't obviously wasteful from reading the code
alone; the redundant calls to `_module_aliases_for_path` only became visible under a call-count
instrument.

**Caching correctness check — don't cache blind:** before adding `@lru_cache` to a suspect
function, confirm it is a **pure function of its arguments** (no file I/O, no external state). This
repo already documents the opposite pattern in the same file: `_mtime_aware_cache`
(`src/tensor_grep/cli/repo_map.py:99-107`) exists specifically because a plain `@lru_cache` on a
path-keyed function that reads *file content* returns **stale results** in the long-lived daemon
after the file is edited. `_module_aliases_for_path` is safe with a plain `@lru_cache` only because
it is a pure string transform of the path itself — it never touches the filesystem. If the function
you're about to cache reads file content, use `_mtime_aware_cache`, not `@lru_cache`. Also return an
immutable type (`frozenset`, not `set`) from a cached function whose result callers might be tempted
to mutate — the fix updated `_module_aliases_for_path`'s return type and every downstream type hint
(`dict[str, set[str]]` → `dict[str, frozenset[str]]`) for exactly this reason
(`tests/unit/test_module_aliases_cache.py`).

**Discriminating experiment:**

```bash
# Profile the ACTUAL slow command at realistic scale, not a synthetic benchmark or toy input.
uv run python -m cProfile -s cumulative -m tensor_grep.cli.main blast-radius SYMBOL --depth 2

# Before promoting a code-reading guess to a fix, verify call-count redundancy on the specific
# suspect function: wrap it with a Counter keyed by its argument(s) around the real command, then
# check hits-per-unique-key. High call count + low unique-key count = a free @lru_cache candidate.
```

**Rule:** do not ship a perf fix — or accept a reported regression as real — on the strength of a
code-reading guess alone. Reproduce the slowness under a profiler on the real command at realistic
scale first; only then pick the fix target. Verify any cache/memoization "fix" against a parity
check (identical output on the same input) before trusting the speedup — a cache is only safe if it
doesn't change results.

---

## 11. Release published but `release-tag-smoke` stayed red (masked regression)

**"Latest `main` CI run is green" is not the same claim as "releases are healthy."** The post-publish
`release-tag-smoke` job (`.github/workflows/ci.yml`, `needs: [release, publish-success-gate]`,
`if: needs.release.outputs.released == 'true'`) checks out the just-published release TAG and
re-runs `scripts/validate_release_assets.py` + `scripts/agent_readiness.py` against it. Two facts
about what it actually installs and when it runs (verified 2026-08-12; SUPERSEDED — this section
previously said the job validates "the actually-published wheel"):

- It installs an **EDITABLE** copy of the tag checkout (`uv pip install -e ".[dev]"` in the job's
  install step — re-derive with `grep -n 'uv pip install' .github/workflows/ci.yml` inside the
  `release-tag-smoke` job), NOT the PyPI wheel artifact. It tests the tag's SOURCE, not what PyPI
  serves.
- It has **NO `continue-on-error`**, so its failure turns the whole run red — but because it
  `needs:` `publish-success-gate`, PyPI publication can already be COMPLETE by the time it fails:
  expect "published AND run red", not "publication blocked".

**Why a later green does not clear it:** the job exists ONLY on release runs (`if: released ==
'true'`) — a later NON-release run on `main` simply does not contain a `release-tag-smoke` job, so
a green non-release run neither re-runs nor clears a red `release-tag-smoke` from the release run.
It is a separate JOB inside the same workflow run as `Semantic Release`/`publish-pypi`, so a run's
aggregate "success" summary does not surface this one job's own red `conclusion` unless you look at
it specifically.

**Known incident:** this job stayed red **since v1.64.4** across 4 releases while `publish-pypi` and
`publish-success-gate` kept publishing fine -- masking PR #542's real `AstBackend` DSL-divergence
regression (`tg run --pattern <ast-grep-syntax>` on an environment without `ast-grep` installed).
Nobody was checking `release-tag-smoke`'s own conclusion, only "did the latest run on `main` succeed
overall." Fixed by hotfix `#144`. Full incident: `tensor-grep-failure-archaeology` Battle 15.

**Discriminating experiment:**

```bash
gh run list --workflow ci.yml --branch main --limit 5     # recent release runs, not just the latest
gh run view <run-id> --json jobs                          # find the job named "release-tag-smoke"
gh run view <run-id> --log-failed                         # if its conclusion is "failure"
```

**Rule:** after any release, check `release-tag-smoke`'s own conclusion inside that specific run by
name -- do not infer release health from "latest main run green." See
`tensor-grep-release-and-positioning` S1.9 for the release-mechanics checklist item that encodes this.

---

## 12. `Dependency & License Audit` red on an untouched dependency (newly-disclosed CVE)

**Symptom:** the `Dependency & License Audit` CI job goes red on a PR that touches nothing in
`pyproject.toml`/`uv.lock`/`Cargo.toml`/`Cargo.lock` -- and the same job is red on every OTHER open
PR simultaneously, not just yours.

**Root cause:** unlike a code defect you introduced, this is a newly-disclosed security advisory
against a dependency your `pyproject.toml`/`Cargo.toml` floor already resolves to -- the strict-on-
fixable `pip-audit` (Python) / `cargo-audit` (Rust) gate fails the instant the advisory database picks
up the CVE/RUSTSEC entry, independent of any diff. **Known incident (2026-07-16, #632, `b796be3`):**
`mcp` 1.26.0 (satisfied by the then-current floor `mcp>=1.2.0`) had a newly-disclosed advisory
(CVE-2026-52870, fixed in `mcp` 1.27.2) that failed `pip-audit` on every open branch at once.

**Discriminating experiment:**

```bash
gh run view <run-id> --json jobs                          # find "Dependency & License Audit"
gh run view <run-id> --log-failed                          # read pip-audit's/cargo-audit's OWN
                                                             # structured output: package + advisory
                                                             # ID + fixed-version, not a generic error
```

**Fix:** bump the dependency **FLOOR** in `pyproject.toml`/`Cargo.toml` to the fixed version (e.g.
`mcp>=1.2.0` -> `mcp>=1.27.2`), not just a bare `uv lock`/`cargo update` relock -- a floor-only relock
lets a future bare resolve (no `--upgrade-package`) silently settle back below the patched version.
Regenerate the lockfile, then re-run the FULL dependent test surface UNMODIFIED (for the `mcp` case:
`tests/unit/test_mcp_server_*.py`, `tests/unit/test_mcp_tg_find.py`,
`tests/integration/test_mcp_stdio_protocol.py`, `tests/unit/test_harness_api_docs.py`) — a passing
dependency bump with zero code changes is the expected GOOD outcome, not a reason to skip
verification; if the bump needs a code change too, that is itself a signal to read the changelog
between the two versions before assuming the fix is a one-line bump. See `AGENTS.md` CI/Release Rules
item (g) and the global skill `supply-chain-hardening` for the broader pattern.

**Rule:** do not treat a `Dependency & License Audit` failure as "must be something in my diff" —
check whether EVERY open PR is also red before spending time bisecting your own change; a
simultaneous cross-PR failure on this specific job is the tell.

---

## 13. Pipe exit-code masking

**Symptom:** a one-liner like `tg some-probe ... | tail -5` or `some_script.py | python -c "..."`
reports success (`$?`/`$LASTEXITCODE == 0`) even though the FIRST command in the pipe genuinely
failed — the failure is invisible because nothing downstream noticed it crashed.

**Root cause:** a shell pipeline's exit code (in bash, without `pipefail`; always in a naive
PowerShell pipe) is the LAST command's exit code, not the first's. `tail`/`grep`/`python -c` almost
always exit 0 regardless of what the upstream command produced (even empty input), so a crashed or
error-exiting first command is silently swallowed by whatever reads its output next.

**Discriminating experiment:** run the first command alone and check its own exit code before
trusting any pipeline built on top of it:

```bash
tg some-probe ...            # run alone first, check $?/$LASTEXITCODE directly
tg some-probe ... | tail -5  # only trust this AFTER the line above confirms exit 0
```

**Fix:** in bash, `set -o pipefail` (or check `${PIPESTATUS[0]}` for the first command's own exit
code specifically) before trusting a piped one-liner's exit code; in PowerShell, don't chain `|`
into a text-processing cmdlet when you need the upstream command's own exit code — capture it to a
variable first. Or simplest: split into two statements instead of one pipe when you need the exit
code AND the trimmed output.

**Rule:** never write a diagnostic/verification one-liner as `real-command | text-filter` when the
real command's own exit code is part of what you're checking — this is exactly how a genuinely
failing probe can read as a clean pass during a closing-dogfood pass (2026-07-22 closing-dogfood
receipt: caught mid-pass, before it produced a false PASS in the final verdict table).

## 14. Raw-JSON-before-scoring

**Symptom:** an automated dogfood/verdict script reports PASS or FAIL for a check, but the
underlying behavior — read by eye — doesn't match what the script concluded.

**Root cause:** the scoring logic read the wrong shape out of the JSON payload (a field that moved
from top-level to nested, a renamed key, a list where a dict was expected) — a shape mismatch can
silently produce EITHER a false PASS (the check reads a default/None value that happens to satisfy
a lenient assertion) or a false FAIL (a real, correct field the checker looked for under the wrong
name). Neither failure mode looks different from a genuine result without inspecting the payload.

**Discriminating experiment:** before trusting a new or changed probe's automated verdict, read the
RAW `--json` output at least once:

```bash
tg some-command ... --json | python -m json.tool   # eyeball the actual shape once
```

Confirm the field the scorer reads actually exists at the path the scorer expects, on a REAL (not
synthetic) run, before trusting a batch of automated PASS/FAIL rows built on the same scoring logic.

**Rule:** treat a first-time or freshly-changed automated verdict as unverified until you've read at
least one raw JSON payload behind it by eye — this is the same discipline as `trustworthy-cuj-scoring`'s
bidirectional-oracle rule (a correct answer must PASS and a wrong one must FAIL), applied to ad hoc
dogfood/verdict scripts rather than a formal eval harness. 2026-07-22 closing-dogfood receipt: this
step is what turned a suspicious-looking automated result into either a confirmed PASS or a real,
actionable finding, rather than a guess either way.

**Same family, a shell one-liner instead of a script (2026-07-24).** A `grep -ciE
"DEFERRED\|deferred"` spot-check on a sibling PR returned zero hits for a caveat that was present
verbatim — in `grep -E` (extended regex), `\|` matches a LITERAL pipe character, not alternation
(extended-regex alternation is a bare `|`; the backslash form is basic-regex/`sed` syntax). The
0-hit result briefly read as "the report is wrong" when the instrument was wrong. If a grep/check
result contradicts what you can see by eye in the file, re-test the check against known-present
content before trusting the negative — this generalizes point 14's raw-JSON rule to any ad hoc
verification command, not just JSON scoring scripts. See `tensor-grep-change-control` Part 6 and
AGENTS.md's "Verify AI-Drafted Plans" for the fuller writeup.

---

## 15. macOS rustup pinned-toolchain fetch timeout (network flake, already mitigated)

**Symptom:** the `test-rust-core` matrix job (or any job whose `Setup Rust` step installs the
`rust_core/rust-toolchain.toml`-pinned toolchain) fails on **macOS specifically** with a
network/timeout error during Rust toolchain setup — not a compile error, and not something your
diff touched.

**Root cause:** `rust_core/rust-toolchain.toml` pins an exact Rust version (currently `1.96.0`), so
the first `cargo`/`rustc` invocation with `rust_core/` as its working directory triggers an
**on-demand** rustup toolchain fetch for that pin. The `rustup-init` bootstrap curl already retries
(`--proto '=https' --tlsv1.2 --retry 10 --retry-connrefused ...`), but rustup's own pinned-toolchain
download did not — a transient macOS-runner network timeout on that fetch red-failed CI on two
consecutive PRs (#720, #721).

**Fix (already shipped, #722):** the `test-rust-core` matrix job's `Setup Rust` step now pre-fetches
the pin inside a 3x retry loop (`cd rust_core && for attempt in 1 2 3; do cargo --version && break;
...; sleep 15; done`) so the later `cargo test` step never hits the un-retried path (re-verify with
`grep -n "pinned-toolchain fetch" .github/workflows/ci.yml`; was `.github/workflows/ci.yml:449-459`,
now `:482-492`).

**Discriminating experiment:**

```bash
gh run view <run-id> --json jobs             # find "test-rust-core (macos-latest, ...)"
gh run view <run-id> --log-failed            # a network/timeout message inside the "Setup Rust"
                                              # step specifically, not a rustc/cargo compile error
```

**Rule:** if you see this signature on a run predating #722, or on a *different* job/step that also
runs `cargo` against the same pinned toolchain without going through this retry loop, extend the
same pre-fetch-with-retry pattern rather than re-diagnosing it as a new class of flake.

---

## 16. "No commits between main and `<branch>`" (detached-HEAD push)

**Symptom:** a worktree agent reports it committed real work, but opening a PR (or `gh pr create`)
fails with GitHub's "No commits between main and `<branch>`," or the diff shows nothing changed. This
reads exactly like the work vanished.

**Root cause:** the agent's commit landed on a **detached `HEAD`** inside its worktree, not on the
named branch it was supposed to be working on (common after certain worktree-setup or checkout
sequences). `git push origin <branchname>` then pushes the BRANCH REF — which never moved off `main`'s
tip, because the commit isn't reachable from it — instead of the commit itself. The commit is real and
sitting at the worktree's `HEAD`; it is simply not on the ref that got pushed.

**Discriminating experiment:**

```bash
git -C <worktree> rev-parse HEAD          # the real commit, if one exists
git -C <worktree> rev-parse <branchname>  # what actually got pushed
git -C <worktree> log --oneline -3        # confirm the commit's content/message
```

If `HEAD` and `<branchname>` resolve to different SHAs, the work is intact — do not re-dispatch the
agent to redo it.

**Fix:** push the SHA explicitly instead of trusting the branch name:

```bash
git -C <worktree> push origin <sha>:refs/heads/<branchname>
```

Then open the PR against `<branchname>` as usual — it will now show the real diff.

**Rule:** before opening a PR from a worktree agent's branch, compare `git rev-parse HEAD` against
`git rev-parse <branch>` rather than assuming a plain `git push origin <branch>` moved the ref you
expect. See `tensor-grep-backlog-campaign`'s harvest pattern for the broader worktree-to-PR sequence,
and AGENTS.md's Campaign Orchestration Disciplines (A24) for the incident this section is drawn from.

**Sibling trap (2026-08-12) — judging whether a branch's work is UNSHIPPED:** `git merge-base
--is-ancestor <branch> main` reads every squash-merged branch as unmerged (A30), and the mirror
error is reading a patch-DISTINCT commit as unshipped work. `git cherry <upstream> <branch>`
discriminates by PATCH-ID: a `- <sha>` prefix means the commit's patch is already equivalent on
upstream (shipped — do not redo it); a `+ <sha>` means patch-distinct — but patch-distinct is NOT
proof of unshipped either, because a squash merge changes the patch-id even when the CONTENT
landed. Receipt (2026-08-12 stale-branch reconciliation,
`docs/audits/2026-08-12-stale-branch-reconciliation.md`): `git cherry origin/main <branch>` marked
one commit `- d9e477b` (patch-id equivalent on main = shipped) and two commits `+` (patch-distinct)
whose content was verifiably SHIPPED on `origin/main` anyway (confirmed by `git grep` for the
normalized code and the test marker). Rule: `-` = shipped; `+` = verify the CONTENT with
`git grep <distinctive symbol> origin/main -- <paths>` / pickaxe before calling the work unshipped.

---

## 17. Timing-test flake — de-flaking a ratio/wall-clock assertion

**Symptom:** a test asserting `elapsed < max(baseline * N, floor)` (or any bare `elapsed < floor`) flakes
on a loaded CI runner — usually Windows — and successive attempts to widen the tolerance either don't
converge or make the flake worse.

**Root cause #1 — the ratio silently degenerates to the floor.** If whatever the test stubs to make the
baseline measurement "cheap" (a subprocess call, an I/O op) collapses the baseline below the platform's
`time.monotonic()` clock resolution, `baseline * N` rounds to effectively zero and
`max(ratio, floor)` always selects the floor — the assertion is no longer relative to anything, it is a
bare absolute bound wearing a ratio's clothes. (The "~15.6ms on Windows" figure the original receipt
cited is HISTORICAL / INTERPRETER-SPECIFIC, not a universal constant: it is the 0.015625s resolution
of CPython's `GetTickCount64()`-backed `time.monotonic()` measured on this Windows host under py3.12,
and the implementation AND resolution vary by interpreter, platform, and system timer state. Probe the
ACTUAL value before sizing any floor off it:
`python -c "import time; print(time.get_clock_info('monotonic'))"`.) Receipt (#739, 2026-07-24): stubbing a `git rev-parse`
subprocess call made the baseline measure exactly `0.0` across 8 runs; `elapsed < max(baseline*6, 2.0)`
had silently become `elapsed < 2.0` — 4x TIGHTER than the 8.0s floor that had just flaked.

**Root cause #2 — the attributed cause is structurally plausible but magnitude-wrong.** A code-reading
guess at "what's slow" ("a subprocess spawn is noisy") can be true in KIND while being wrong in SIZE.
Same receipt: cProfile showed the git spawn was only 6-12% of elapsed; the real ~93% was fsync-heavy
discovery-cache I/O in an unrelated helper. Removing the wrong 6-12% of cost while tightening the bound
made the next flake worse, not better.

**Discriminating experiment:**

```bash
# 1. Measure the baseline in ISOLATION, several times, with nothing stubbed — is it a real,
#    non-trivial number, or does it read as ~0.0 / suspiciously flat?
uv run --no-sync python -c "
import time
for _ in range(8):
    t0 = time.monotonic()
    <call the function the test uses as its baseline>
    print(time.monotonic() - t0)
"

# 2. Profile the ACTUAL flaking command, not the function you assume is slow.
uv run --no-sync python -m cProfile -s cumulative <script invoking the real code path> \
  | head -30
```

If the baseline reads as ~0.0 (or a fixed value at the clock-tick granularity) across all 8 runs, the
ratio arm has degenerated to the floor — fix the STUBBING, not the multiplier. If the profile's top
cumulative-time frame is not the function your fix already targets, you are about to fix the wrong 6-12%
again.

**Fix, in order of preference:**

1. **Best — a structural, order-based assertion**, if the invariant allows one. Wrap the critical
   section's boundary plus the suspect expensive call(s) to emit ordered ENTER/EXIT markers into a
   shared list, and assert on marker ORDER, never on elapsed time. A slow/loaded runner delays every
   marker uniformly without reordering them, so this cannot flake under load. Worked example:
   `tests/unit/test_index_lock_concurrency.py::test_create_checkpoint_lock_does_not_wrap_expensive_work`
   (#739) — proved green on windows-latest py3.11 AND py3.12 (run `30130861182`), the exact
   platform/version pair that had flaked twice.
2. **If a wall-clock form is unavoidable**, confirm the baseline is measurably non-trivial (well above
   clock resolution) before trusting `max(baseline * N, floor)` as relative; otherwise treat it as the
   absolute floor it actually is and size the floor off the PROFILED real cost, not a guess.

**Rule:** never widen a timing tolerance as the first move — measure the baseline in isolation and
profile the real cost first; only then decide whether the fix is "unstub something," "convert to a
structural assertion," or "the floor genuinely needs to move." Full mechanism, numbers, and the
degenerate-`max()` comment-trap that followed it: `tensor-grep-validation-and-qa` Part 1 points 18-20,
`tensor-grep-change-control` Part 6, and AGENTS.md's CI/Release Rules.

**Batch wobble (2026-08-12):** when a timing-bound test exceeds its bound ONCE inside a large
batch/suite run, re-run the EXACT node IN ISOLATION before diagnosing: a wobble that reproduces
GREEN solo is load jitter, not a defect. Receipt (2026-08-12 Task 2A union-merge per-node oracle,
`docs/audits/2026-08-12-stale-branch-reconciliation.md`): 158 nodes across 6 suites with 0 outcome
deltas except one wobble (a 14.18s > 12.0s collect bound) that reproduced GREEN solo at 13.59s —
closed as /mnt/c load jitter, correctly not treated as a defect.

---

## 18. A control that names the wrong symbol falsely exonerates the right hypothesis

**Symptom:** A live, plausible hypothesis for a red gate gets ruled out by a diagnostic control, and
the failure sits "cause unknown" for days -- even though the original hypothesis was correct all
along.

**Root cause:** the control checked whether `rust_core` (the importable Python **extension module**)
was present, and read "present" as proof native dispatch was live. The actual gate `tg` branches on
for native dispatch is `resolve_native_tg_binary()` (`src/tensor_grep/cli/runtime_paths.py:278`) -- a
resolver for the compiled standalone **binary**, a different artifact with an adjacent, easy-to-
conflate name. Receipt: #868 sat RED for days on exactly this mix-up before the correct hypothesis
was re-examined.

**Discriminating experiment:** before trusting any control's present/absent verdict, grep the real
branch point for the exact symbol it reads:

```bash
grep -n "resolve_native_tg_binary\|HAVE_RUST" src/tensor_grep/cli/main.py src/tensor_grep/cli/bootstrap.py
```

Then restate the control as "I set `<that exact symbol>` to `<value>`" -- not "I verified native
support is available." If the restated sentence names a different symbol than the one the real
branch reads, the control proves nothing about that gate.

**Rule:** name a control arm by the SYMBOL the code branches on, never by the capability you believe
it stands for. Two adjacent-sounding artifacts (an importable extension module vs. a resolved binary
path) can gate two completely different code paths, and a control that verifies the wrong one will
falsely exonerate a correct hypothesis. Sibling of §6 one layer up: there a *test* mocks the wrong
*boundary*; here a human-run *control* checks the wrong *symbol* -- same family, different failure
point.

## 19. A reproduced failure is not proof of the operative mechanism

**Symptom:** A two-arm control reproduces CI's failure byte-for-byte -- same exit code, same stdout --
and gets called "confirmed." A fix is designed and dispatched around that mechanism. It later turns
out the real failing job doesn't even exercise the mechanism the control forced, and the dispatched
agent has to be recalled mid-flight.

**Root cause:** "sufficient to reproduce the symptom" and "the actual cause in the real failing job"
are different claims, and a control that succeeds only proves the first. Receipt: a control that
forced the native binary to build reproduced the CI failure exactly -- but an in-line comment on the
`test-python` job's `Run Pytest` step (the job that was actually red) stated `test-python` **never
builds** `rust_core/target/release/tg` at all (maturin there only builds the `pyo3/extension-module`
cdylib; the release binary only exists in a different job) -- re-verify with
`grep -n "never builds" .github/workflows/ci.yml`; was `.github/workflows/ci.yml:688`, now `:704-705`
(that comment moved, and the file has since grown a second, near-identical copy at `:442-443`). The
reproduced failure and the real failure shared symptoms, not cause.

**Update, since re-verified (task 22 / PR #868): `ci.yml` now marks this exact claim "IN DISPUTE."**
A later investigation questioned whether `test-python` genuinely never reaches a real native binary
on every job/OS leg, and the file has since grown a NON-GATING diagnostic step ("Task 22 diagnostic:
which route does an explicit --gpu-device-ids search take", `continue-on-error: true`) specifically
to re-measure this on a live job instead of trusting the comment as settled
(`grep -n "Task 22 diagnostic" .github/workflows/ci.yml`). Treat the underlying "never builds the
release binary" claim as under active re-verification, not as closed, until that diagnostic's finding
is folded back into this section -- the *methodology* lesson below (reproduction is not proof of the
operative mechanism) still holds regardless of how that dispute resolves.

**Discriminating experiment:** after any control reproduces a failure, read the real failing job's
own config for the step in question and confirm it can reach the mechanism you forced:

```bash
gh run view <run-id> --json jobs                                # confirm which job actually failed
grep -n "build\|maturin\|cargo build" .github/workflows/ci.yml  # does THAT job's config run this step?
```

**Rule:** say "sufficient" the moment a control reproduces a failure; only say "operative" once
you've confirmed the real failing job's own config actually exercises that mechanism. A reproduction
that never checks against the real job's steps is a coincidence wearing a confirmation's clothes.
Companion to §1/§2 ("decode the structured check first"): those tell you WHICH job failed; this
section is about verifying your control actually explains THAT job, not a different one that happens
to fail the same way.

## 20. A windowed list-plus-filter query gives a false "complete"

**Symptom:** A merge-gate or release-monitor check runs `gh run list --branch main --limit N`
(optionally filtered by commit SHA) and reports "0 runs in flight" / "everything terminal" while a
real run is still mid-publish. Merging on that reading risks rejecting the in-flight release's own
push (§2).

**Root cause:** the limited window fills up with unrelated rows that satisfy the same filter --
other workflows on the same branch, or even **cron-scheduled workflows that happen to fire on the
same commit SHA** -- pushing the real run out of a small `--limit` view. Checking `total > 0` doesn't
catch this either: the population is full of the wrong rows, not empty.

**Discriminating experiment:** query the ONE object you care about by its unique ID, never a list
plus a filter:

```bash
gh run view <run-id>                    # the specific run, not a list
gh run view <run-id> --json jobs        # then check the JOB's own conclusion, not the run's
```

If you must list first to find the ID, widen the limit and read every row's workflow **name**, not
just whether the branch/SHA filter matched -- a cron job matching your filter is not the run you're
looking for.

**Rule:** a list-plus-filter query is a hypothesis about which rows matter, not a fact about what's
running. Query one object by its unique ID, and check the JOB's own conclusion, not the run's
aggregate status -- the same discipline as §11's "aggregate green hides one job's red," applied to
the *selection* step instead of the *reading* step.

## 21. The setup lies: Git Bash cwd vs Windows binary path domain

**Symptom:** A dogfood/verification run through a Windows-built `tg` binary, invoked from a Git Bash
shell, reports zero files found against a fix that demonstrably works.

**Root cause (NARROWED 2026-08-12 — the original blanket claim is false):** the DEFAULT
Git-Bash-to-Windows-child invocation does NOT leak a POSIX cwd. Verified on this host: `cmd /c cd`
from a Git Bash shell whose cwd is `/tmp` reports the CONVERTED Windows path
(`C:\Users\oimir\AppData\Local\Temp`), and `MSYS_NO_PATHCONV=1` does not change that (it governs
ARGV conversion, not cwd). SUPERSEDED: this section previously said "Git Bash defaults a spawned
Windows process's working directory to a POSIX-style path" — it does not, on this host, in the
default mode. The POSIX-cwd leak is specific to invocations where MSYS path conversion is
DISABLED or BRIDGED — a launcher/shim that passes the shell's POSIX path string untranslated to
the Windows binary (a captured `pwd`, an env var, an argument), or a bridged spawn that skips the
conversion layer. In those shapes the Windows binary resolves the POSIX string in its OWN path
domain, where it refers to nothing (or an empty directory), and walks zero files. The reported
"wrong" result is not the fix failing; it's the invocation handing the binary a path it cannot
interpret.

**Discriminating experiment:** re-run the identical command with an explicit Windows-form path
instead of relying on the shell's defaulted cwd:

```bash
tg search PATTERN /tmp/some/dir        # Git Bash cwd/path form -- may silently see nothing
tg search PATTERN 'C:\Users\...\dir'   # explicit Windows-form path -- the discriminator
```

If the Windows-form path finds the file immediately, the fix was never broken -- the first reading
was an artifact of the path domain, not the code.

**Rule:** on Windows, a native binary invoked through a path-conversion-disabled or BRIDGED
Git-Bash path (a shim/env-var/argument carrying the shell's POSIX string) can disagree with the
shell about what a bare/defaulted path even means — the DEFAULT direct invocation converts the cwd
and is not suspect. Before filing a regression off a Windows-binary-from-Git-Bash
run, re-run with an explicit native-form path -- stopping at the first reading risks filing a phantom
regression against a real fix. Sibling of §6's dogfood-the-real-binary rule: this is the same
discipline one step earlier, applied to the INVOCATION environment rather than the binary itself.

---

## 22. Environment artifacts (2026-08-12 session lessons)

Two environment-level artifacts that read as mysterious failures but are neither — both from the
2026-08-12 stale-branch reconciliation (`docs/audits/2026-08-12-stale-branch-reconciliation.md`).

**22.1 A stray untracked `nul` file on Windows.** A 0-byte file literally named `nul` appearing as
untracked in `git status` is a `2>nul` redirect artifact — a command whose stderr redirect ran
somewhere that treats `nul` as an ordinary filename (a Git-Bash redirect) instead of the Windows
NUL device. It is harmless litter, not a product output. The trap is REMOVING it: `nul` is a
Windows RESERVED device name, so `Test-Path ./nul` / plain `Remove-Item ./nul` cannot address the
file (they resolve to the device, not the file entry). Remove it via Git Bash: `rm -f ./nul`
(AGENTS.md-sanctioned). Prevent recurrence with `2>$null` (PowerShell) or `2>/dev/null` (bash)
instead of `2>nul`.

**22.2 WSL interpreter provenance probe.** Before trusting a WSL-side test run, probe WHICH
interpreter/stdlib is actually executing: the system WSL python3.13 stdlib was found BROKEN on
this host (`/usr/lib/python3.13/shutil.py` absent — a bare `import shutil` is the cheap probe that
exposes it). Run WSL-side work from a WSL-local MANAGED venv (rebuild with
`uv venv --python-preference only-managed` when the system interpreter is broken), and NEVER point
WSL `uv` at the Windows checkout's `.venv` (AGENTS.md A60: a WSL `uv run --no-sync --project
/mnt/c/...` probe treats the Windows venv as incompatible, DELETES it, and creates an empty Linux
venv in its place — a dependency check becomes shared-environment mutation).

---

## Provenance and maintenance

Facts here were originally verified **2026-07-02, tensor-grep v1.17.25** for §1–§8, and
**2026-07-03, v1.19.3** for §9–§10; drift-checked and re-anchored **2026-07-08 against v1.49.3**
(`pyproject.toml:430`) for the §5 rg-passthrough-sentinel status, §8 `score_term_overlap`/degrade-
to-ask citations, §3 exit-124 citations, and the §9 `_NATIVE_TG_DELEGATION_DEFAULT_REQUIRED_FIELDS`
line number; **2026-07-16 against v1.78.1** added §12 (dependency-CVE-audit triage) and the §4
`tg find rank_fallback_reason` example; **2026-07-22 against v1.93.2** extended §2 with the
push-race-vs-`needs:`-flake-vs-batch-merge triage (three distinct shapes, three different recovery
paths), refreshed the §9 line citation to `main.py:1897`, and added §13 (pipe exit-code masking) and
§14 (raw-JSON-before-scoring), both from the 2026-07-22 closing-dogfood pass; **2026-07-23 against
v1.95.0** re-verified and re-anchored every hardcoded file:line citation in §3/§5/§7/§8/§9/§10 (most
had drifted 200-3,000 lines — `repo_map.py`'s citations moved the most, after the Java/C#/PHP
language-support campaign landed a large amount of new code near its scoring/caching helpers),
added the §3 `tg inventory --deadline` pathological-workspace-union-tree scandir edge (known,
low-priority, not a regression — `repo_map.py:987`/`:1009`), added §10 Incident 3 (a warm
`tg orient` dogfood run hid PR #719's real ~54% win, plus the microbench-on-the-shipped-wheel
discipline that catches it), and added §15 (macOS rustup pinned-toolchain fetch timeout, already
mitigated by #722); **2026-07-24 against v1.98.2** added §16 ("No commits between main and
`<branch>`" — a detached-HEAD worktree push, cross-referenced to AGENTS.md's Campaign Orchestration
A24). A further same-day pass, **against v1.98.3**, added §17 (a timing-ratio test flake caused by a
degenerate baseline below clock resolution, plus the profile-before-attributing-a-cause discipline and
the structural ENTER/EXIT marker-order fix — receipts #737/#739, cross-referenced to
`tensor-grep-validation-and-qa` Part 1 points 18-20 and `tensor-grep-change-control` Part 6). A
coordinator review of that same pass added the §14 addendum (a malformed `grep -E \|` alternation
returning a false-negative spot-check on a sibling PR) and the concrete clock-resolution figure to §17.
**2026-07-31 against v1.101.24** added four diagnosis-process lessons from a single debugging
session: §18 (a control that verified the wrong symbol -- `rust_core` the importable extension vs.
`resolve_native_tg_binary()` the compiled-binary resolver -- falsely exonerated a correct hypothesis on
#868), §19 (a control reproduced CI's failure byte-for-byte but the mechanism it forced was never
exercised by the real failing job, per an in-line comment in `.github/workflows/ci.yml`; the
dispatched fix had to be recalled mid-flight), §20 (a `gh run list --branch ... --limit N` merge-gate
query reported "0 in flight" while a real release run was mid-publish, because unrelated
cron-triggered rows sharing the same commit SHA filled the limited window), and §21 (a Windows-built
`tg` binary invoked from Git Bash defaulted to a POSIX-style `/tmp/...` cwd it could not resolve,
reading as a phantom regression until re-run with an explicit Windows-form path).
**2026-08-01 citation-repair pass:** every hardcoded `file:line` citation in §3/§5/§6/§8/§10/§15/§19
had drifted again since the 2026-07-23 pass (`repo_map.py`'s moved the most -- `_score_symbol` /
`_score_import_entry` / `_score_file_source_terms` REORDERED relative to each other, not just shifted
together; `_module_aliases_for_path` and `_framework_test_pattern_bonus` each drifted ~495 lines; the
`tg inventory --deadline` truncation-cause stamp moved out of `main.py` into its own `inventory.py`
module entirely). Each was converted from a bare line number to a `grep`-verifiable symbol/phrase,
with a `was -> now` pair kept beside it as a drift-rate receipt, not a number to trust on the next
read (per AGENTS.md's "cite the SYMBOL, not the line" law -- re-stamping a citation with today's
correct number just ships the next wrong anchor on a slower clock). §19 additionally picked up a
substantive update, not just a line move: `ci.yml` itself now marks the "`test-python` never builds
the release binary" claim "IN DISPUTE" pending a task-22 diagnostic step, so that section now flags
the claim as under active re-verification rather than settled.
**2026-08-12 retention pass (branch `docs/retention-2026-08-12`, base `568065a`):** §3 rewritten
from a single "both routes 60s/124" contract to THREE route-dependent outcomes (Python bootstrap
60s/exit 124; native implicit-walk ceiling exit 2 before any rg spawn; native spawned-rg arm
unbounded via `Command::status()` — verified against `rust_core/src/rg_passthrough.rs`
`check_implicit_walk_ceiling` / `IMPLICIT_SEARCH_WALK_FILE_CEILING` / `command.status()`, and the
§3 `return 124` receipt re-anchored `:1269`/`:1320` → `:1353`/`:1404`); §11 corrected —
`release-tag-smoke` installs an EDITABLE copy of the tag checkout (`-e ".[dev]"`), not the PyPI
wheel, has no `continue-on-error`, and is never re-run by later non-release runs; §10's
`total == total` byte-identity overclaim replaced with direct-output-equality / field-by-field
differential; §9's present-tense "PR CI never builds the native binary" claims marked
DATED/HISTORICAL (contradicted by §19's IN DISPUTE + the matrix-wide non-gating Task 22
diagnostic); §21's Git Bash cwd claim NARROWED to path-conversion-disabled/bridged invocations
(default mode converts the cwd — verified live on this host); §17's "~15.6ms on Windows" relabelled
historical/interpreter-specific with a `time.get_clock_info('monotonic')` probe; and four dated
2026-08-12 lessons folded — §16 (`git cherry` patch-id: `-` = shipped, `+` = verify content, not
unshipped-by-default), §17 (batch wobble → re-run the exact node in isolation first), §22.1 (the
untracked `nul` Windows redirect artifact and its reserved-name removal), §22.2 (WSL interpreter
provenance probe + A60 WSL-venv rule).
Re-verify anything below before trusting it on a later version — this table
drifts whenever the cited line numbers, defaults, or contracts change.

Re-verification commands:

```bash
# Version this playbook was verified against
grep -n '^version' pyproject.toml

# Timeout default + env var name (§3)
grep -n "TG_RG_TIMEOUT_SECONDS\|60.0" src/tensor_grep/cli/subprocess_policy.py

# Fail-closed contract text still matches (§4)
grep -n "BackendExecutionError" src/tensor_grep/backends/base.py

# -- sentinel fix still present at both cited sites (§5)
grep -n '"--",' src/tensor_grep/cli/mcp_server.py
# CAUTION: `grep -n "for path in &args.paths"` alone is a FALSE-NEGATIVE-PRONE check -- that loop
# still exists post-fix (just after an unconditional sentinel push), so a bare grep hit does NOT
# mean the gap reopened. Read the function instead:
grep -n "fn ripgrep_operand_args" -A 20 rust_core/src/rg_passthrough.rs   # expect an unconditional operands.push("--".to_string()) before the path loop

# typer dependency cap still <0.26 with the same rationale (§7)
grep -n "typer>=" pyproject.toml

# degrade-to-ask safety floor still present (§8)
grep -n "Degrade-to-ask safety floor" src/tensor_grep/cli/agent_capsule.py

# score_symbol / score_import_entry / score_file_source_terms still current (§8)
grep -n "def _score_symbol\|def _score_import_entry\|def _score_file_source_terms" src/tensor_grep/cli/repo_map.py

# registration-completeness checker location unchanged
grep -n "def check_group_smart" src/tensor_grep/core/registration_check.py

# push-race + --log-failed guidance still current in AGENTS.md
grep -n "log-failed\|push-race\|rejected  main -> main" AGENTS.md

# capfd -> result.stdout fix still present (§9)
grep -n "result.stdout\|capfd" tests/integration/test_bm25_search_flag.py

# rank_bm25/sort_files still in the native-delegation refuse-tuple (§9)
grep -n '"sort_files"\|"rank_bm25"' src/tensor_grep/cli/main.py

# _module_aliases_for_path still memoized + frozenset-returning (§10)
grep -n "^@lru_cache(maxsize=16384)" src/tensor_grep/cli/repo_map.py
grep -n "^def _module_aliases_for_path" src/tensor_grep/cli/repo_map.py

# Incident-3 optimization functions still present (§10)
grep -n "def _python_imports_and_symbols\|def _framework_test_pattern_bonus" src/tensor_grep/cli/repo_map.py

# tg inventory --deadline scandir edge still at the same shared walker (§3)
grep -n "def _iter_repo_files" src/tensor_grep/cli/repo_map.py

# rustup pinned-toolchain retry loop still present (§15)
grep -n "pinned-toolchain fetch" .github/workflows/ci.yml

# structural ENTER/EXIT marker-order de-flake still present (§17)
grep -n "def test_create_checkpoint_lock_does_not_wrap_expensive_work" tests/unit/test_index_lock_concurrency.py

# three-route timeout contract still current (§3)
grep -n "fn check_implicit_walk_ceiling\|IMPLICIT_SEARCH_WALK_FILE_CEILING" rust_core/src/rg_passthrough.rs
grep -n "\.status()" rust_core/src/rg_passthrough.rs        # the unbounded spawned-rg wait
grep -n "def configured_ripgrep_timeout_seconds" src/tensor_grep/cli/subprocess_policy.py

# release-tag-smoke still editable-from-tag with no continue-on-error (§11)
grep -n -A8 "release-tag-smoke:" .github/workflows/ci.yml

# clock resolution probe (§17) — run, don't grep:
# python -c "import time; print(time.get_clock_info('monotonic'))"
```

## Retention fold (2026-08-13)

- **A101 — third recurrence of a flake = structural-fix signal, not rerun signal.** A rerun
  self-heals ONCE; the third sighting of the same flake (e.g. `windows-agent-readiness`
  `public-version-powershell` 30s timeout, 3× in 3 runs) means fix the probe (raise the timeout /
  make it tolerant), not keep rerunning. Record the recurrence count beside the flake. **FIXED: PR #1009 → v1.110.15 — scripts/agent_readiness.py `Check.retry_on_timeout` (opt-in, clamped at `_MAX_TIMEOUT_RETRIES = 3`) + the four shell probes timeout_s=90 + retry_on_timeout=1; `attempts` in every run_check result.**

If any of these greps come back empty or materially different, the corresponding row above is
stale — update it before relying on it, and check whether the fix pointer's target skill
(`tensor-grep-architecture-contract`, `tensor-grep-change-control`, etc.) needs the same update.

### A hung CI job, and the four `gh` readings that lie about it (2026-08-19)

`native-build-smoke (ubuntu-latest)` sat **2h21m**, then 29m, then 39m on one step, blocking two
PRs. Diagnosing it was entirely a matter of not believing four plausible readings.

**1. Find a sibling before blaming the runner.** The same run's other legs finished normally:

| leg | duration |
|---|---|
| macos-latest | 9m51s |
| macos-15-intel | 11m35s |
| windows-latest | 11m39s |
| **ubuntu-latest** | **hung** |

A degraded box moves its siblings too. They never moved, so the step is the subject. (The same
run is the strongest control available — same moment, same pool, same commit.)

**2. Ask which STEP, not which job.** `gh api repos/<o>/<r>/actions/jobs/<id> --jq '.steps[]'`
gives per-step status. Here: step 9, *"Ensure ripgrep is available"* — `sudo apt-get update &&
install ripgrep`, unbounded, so a stalled mirror runs to the 6h job limit while emitting nothing.
**An unbounded step is indistinguishable from a slow one from the outside.**

**3. `gh run view --log-failed` returns EMPTY while the RUN is in progress** — even when the JOB
has completed and failed. That empty read looks exactly like "no failures". Go at the job
directly: `gh api repos/<o>/<r>/actions/jobs/<id>/logs`.

**4. Two greps that lie in the log itself.** `grep -iE 'error'` matches `--error-format=json` in
every rustc invocation — hundreds of lines of noise that look like findings. Strip the timestamp
prefix first (`sed 's/^[0-9T:.Z-]* //'`) and anchor the pattern. And a check name containing
`${{ matrix.os }}` unexpanded means the job **never instantiated** — so a wall of red including
`publish-pypi` and `Semantic Release` can be nothing but a cancellation you performed yourself.

**Then it passed in 6 minutes on the next attempt.** Intermittency is the argument FOR a bound,
not for reruns: `timeout-minutes` converts an invisible multi-hour stall into a fast, legible red.

## A release run looks "stuck" — pending, 0 jobs (2026-08-21)

**Do not conclude "stuck" from a windowed query.** Three distinct states get confused here, and the
discriminating commands are cheap.

| symptom | what it means | how to confirm |
|---|---|---|
| `status: queued` | waiting for a **runner** | check queue depth: `gh run list --limit 20 --json status` |
| `status: pending`, **0 jobs** | held by the **concurrency group** — an earlier run in the same group is still active | list every non-completed run repo-wide (below) and find the holder |
| no run at all for the head SHA | `ci.yml` never dispatched | `gh run list --commit <sha>`; compare against a sibling PR as a control |

```bash
# What actually holds the group? (the single most useful command here)
gh api "repos/oimiragieo/tensor-grep/actions/runs?per_page=30" \
  -q '.workflow_runs[]|select(.status!="completed")|"\(.created_at[11:19]) \(.name) \(.status) \(.head_branch)"'

# Then watch the run you care about BY ID -- never by a windowed list.
gh run view <id> --json status,conclusion,jobs
```

**The failure this encodes.** A release was reported as "pending with 0 jobs, possibly stuck" for
tens of minutes. It was not stuck: run `32544510005` had been **in_progress since 01:48 with 31
jobs, 27 succeeded**, while a NEWER run sat pending behind it. The monitor used
`gh run list --limit 1`, which returns the newest run and **structurally hides the executing one**.
With `cancel-in-progress: false` on `main`, that pending state is the system working correctly.

**Corollaries:**

- **Two main merges can produce TWO releases**, one per run — not one combined. Check which commits
  each run carries (`git log --format='%s' <last-tag>..<sha>`) before claiming what shipped.
- A merge while a run is **pending** SUPERSEDES it (cancel-in-progress does not protect a queued or
  pending run — A133). That is usually what you want: the replacement run is cumulative from the
  last tag, so batch the merges and let one run publish everything.
- `--limit 1` is a window. The thing you care about can be outside it. Same trap as the
  `gh run list --commit` + `--limit` case already recorded — it recurred inside a monitor written
  by the session that had just documented it.

Laws **A139**, **A140**. See also A133/A134 (queued runs are unprotected; push churn starves CI)
and A135 (assert checks by NAME, not count).

## A test command that dies before collection reports success (2026-08-21)

`pytest tests/unit -q --timeout=300` failed at ARGUMENT PARSING (`unrecognized arguments`, because
`pytest-timeout` is not installed here) and the background wrapper reported **`[exited with code
0]`**. The suite never ran. Trusting that status would have produced a claimed full-suite pass over
zero executed tests.

**This is the false-green that looks most like a real one**, because there is no failure text to
notice — just a short, clean-looking log. Always read the tail of the output and confirm a test
COUNT (`N passed`), never the exit status alone. Kin: A127 (read exit codes unpiped — `cmd | tail`
reports tail's status) and A141.
