---
name: tensor-grep-failure-archaeology
description: Use when about to "fix" or "optimize" something in tensor-grep that feels novel — before proposing PyO3/FFI for directory walking, re-enabling free-threading, adding a --json self-test, tightening a dependency upper-cap, blaming an IDF/ranking flip, trusting a green mock/FFI test, or diagnosing a release that "didn't publish". A chronicle of settled battles (symptom -> root cause -> evidence -> status) so no one re-fights them. Load it to check "has this already been tried and lost?" before spending effort. For a live NEW failure use tensor-grep-debugging-playbook; for the process gates to re-attempt one use tensor-grep-change-control.
---

# Tensor-Grep Failure Archaeology

A chronicle of **settled battles** in tensor-grep: expensive fights that already reached a
verdict. Each entry is `symptom -> root cause -> evidence -> status` so a future engineer or
model does not burn a day re-discovering the same wall.

Facts below were verified against the repo on **2026-07-02 at v1.17.25**. Re-verify anything
load-bearing with the commands in **Provenance and maintenance** before you act on it.

## When to use this skill

Load this **before** you spend effort on any of these, because each has already been fought:

- Proposing to move directory traversal / file walking into Rust via a PyO3 extension "for speed".
- Re-enabling PyO3 free-threading (`gil_used = false`, `#[pyclass(frozen)]`).
- Adding a self-test or health check that runs `tg search … --json …`.
- Tightening a dependency **upper** cap (`typer<X`, `click<X`, `pydantic<X`, …).
- Diagnosing a release that "didn't publish" or a green-CI-then-no-PyPI gap.
- Blaming a ranking / capsule / semantic-search behavior change on IDF, or "fixing" it by
  widening a candidate pool.
- Trusting a **green mock or FFI test** as proof that a bridge works.
- Reacting to a wall of red governance tests after a README / docs change.

## When NOT to use this skill (use a sibling instead)

| Situation | Use instead |
|---|---|
| A **new**, live bug / test failure / crash you are actively debugging | `tensor-grep-debugging-playbook` |
| You want to *re-attempt* a settled battle and need the gates (council, dry-run, flag-flip) | `tensor-grep-change-control` |
| You need the invariants that these battles hardened (front door, registration, fail-closed backend) | `tensor-grep-architecture-contract` |
| You are running the release pipeline / semantic-release mechanics | `tensor-grep-release-and-positioning` |
| Rust/uv/maturin build or env problems (off-PATH cargo, slow LTO) | `tensor-grep-build-and-env` |
| The eval/oracle/grader version of the "green test lied" trap (pnpm exit-127, bidirectional oracle) | `trustworthy-cuj-scoring` |
| Search-flag / config-axis defaults and semantics | `tensor-grep-config-and-flags` |

**This skill is read-only history. It does NOT authorize a change.** Reopening any settled
battle still goes through `tensor-grep-change-control` (council-verify -> dry-run -> conscious
flag-flip). Nothing here routes around that.

## How to read an entry

Every entry has four fields. Trust the **Evidence** column over the prose; if the evidence no
longer matches the code, the battle may have moved — flag it, do not silently act.

- **Symptom** — what you would observe that tempts the re-fight.
- **Root cause** — the mechanism, stated precisely (the naive guess is usually wrong).
- **Evidence** — the commit / PR / file:symbol that proves it.
- **Status** — SETTLED (do not re-fight without change-control) or OPEN (known debt).

---

## Battle 1 — PyO3 for directory walking (FFI boundary loses to `os.walk`)

| Field | Detail |
|---|---|
| **Symptom** | "Python's `os.walk` is slow; wrap Andrew Gallant's Rust `ignore` crate in a PyO3 class and directory traversal will be much faster." |
| **Root cause** | The bottleneck is the **PyO3 FFI boundary, not the walk**. A Rust iterator that yields paths back to Python must allocate and serialize tens of thousands of Rust `String` into `PyString` on the Python heap, acquiring and releasing the **GIL** (Global Interpreter Lock — CPython's per-interpreter mutex) on every yield. CPython's `os.walk` runs in C *inside* the interpreter and never crosses a language boundary until it yields native objects. |
| **Evidence** | Benchmarked on the `C:\dev` monorepo: **Rust PyO3 `ignore` extension = 48.818 s** vs **pure Python `os.walk` = 39.892 s**. Documented in `docs/PAPER.md` §3.7 ("Why Pure Python Traversals Sometimes Win"); the Rust `scanner.rs` PyO3 path was deleted in commit `b2f3fdd` ("Document PyO3 FFI overhead limits and revert to native CPython directory scanning"). |
| **Status** | **SETTLED.** Directory traversal stays pure-Python stdlib. |

**Rule:** FFI is the speed path only when it **avoids the per-item boundary entirely** — i.e. the
embedded `tg.exe` route that maps files with `memmap2` + `rayon` and never yields per-path into
Python (`docs/PAPER.md` §3.6). A PyO3 extension that returns a large iterator of small Python
objects will lose to CPython C code. Do not re-propose "just wrap `ignore` in PyO3" for the walk.

## Battle 2 — PyO3 free-threading (`gil_used=false`) broke Linux, blocked all releases

| Field | Detail |
|---|---|
| **Symptom** | "Free-threading (drop the GIL, mark `#[pyclass(frozen)]`) should speed up the extension — turn it on." |
| **Root cause** | PR #266 flipped `gil_used = false` + `#[pyclass(frozen)]`. The extension then **failed to import on Linux** (`agent-readiness` red), while Windows and local import stayed green — so the break was invisible where it was developed. Worse: #266 merged with its CI **cancelled by a force-push**, so no green run ever gated it. The release job `needs` `agent-readiness`, so this **blocked every release from v1.13.43 onward**. |
| **Evidence** | Revert commit `a90595f` ("revert #266 free-threading (gil_used=false + frozen) — broke Linux agent-readiness"); reverted to the known-green #265 config (`gil_used=true`, no `frozen`). |
| **Status** | **SETTLED / re-enable only behind full green CI.** Free-threading is marginal-value here; it may be re-attempted, but only via `tensor-grep-change-control` with a **complete green multi-OS CI run** — never merged with cancelled CI. |

**Rule:** A merge whose CI was cancelled by a force-push is **not** verified. Two independent
traps compounded here (Linux-only import break + ungated merge). Do not re-flip free-threading
casually, and never let a force-push swallow the gating CI run.

## Battle 3 — the `--json` fork-bomb (mutual native<->Python delegation)

| Field | Detail |
|---|---|
| **Symptom** | A `tg` invocation with `--json` plus a passthrough flag (`--json -b`, `--json --debug`, `--stats`, …) **hangs, then spawns processes without bound** — historically it **disabled both native `tg.exe` binaries** (audit item "C3"). |
| **Root cause** | The native front door delegates `--json`+passthrough combos to the Python sidecar. If the sidecar's launcher lacks the guard (a stale/guard-less Python), the Python launcher re-invokes the **native** binary, which delegates back to Python — a self-invocation loop that deadlocks and fork-bombs. A self-test that ran `tg search … --json --debug` against a guard-less Python would re-trigger it. |
| **Evidence** | Guard is a **re-exec marker** enforced across three files: `src/tensor_grep/cli/bootstrap.py` (search "fork-bomb"), `rust_core/src/main.rs`, `rust_core/src/python_sidecar.rs`; behavioral test `tests/unit/test_launcher_no_respawn.py`. See CHANGELOG entries for the C3 close ("close the C3 fork-bomb for ALL --json + passthrough-flag combos (re-exec marker)"). |
| **Status** | **SETTLED / guarded.** The immediate mitigation disabled the native binaries; the durable fix is the re-exec marker guard. Current native-binary availability is nuanced — check `tg doctor --json` (`skipped_native_tg_binaries`) and `tensor-grep-run-and-operate` for live status. |

**Rule:** Never add a health check / self-test that runs `tg search … --json …` — that is the
exact shape that reopens C3. If you touch the native<->Python delegation path, run
`tests/unit/test_launcher_no_respawn.py` and dogfood the **real** binary, not `CliRunner`.

## Battle 4 — README rewrite: 14 red governance tests + 4 wasted CI cycles

| Field | Detail |
|---|---|
| **Symptom** | A force-pushed marketing README turned **main red on ~11–14 governance tests** and also tripped a release-blocker gate. The team then burned **~4 CI cycles** trying fixes that did not address the failing check. |
| **Root cause** | Two layers. (1) The README rewrite **dropped enterprise-doc links + pinned content** that `test_public_docs_governance` / `test_enterprise_docs_governance` / `validate_readme_contract` assert. (2) The **4-cycle waste** came from *theorizing from tracebacks* — a free-threading revert and `uv run --no-sync` were tried first — instead of **decoding the structured failing check**. The real release-gate failer was `validate_docs_claims` (a docs-claim staleness check), not the theories. |
| **Evidence** | PR #269 / v1.13.44 ("green main after the README rewrite — restore enterprise-doc links + relax redundant README governance"); commits `fc1f4b9`, and the `validate_readme_contract` / `test_public_docs_governance` relaxations in CHANGELOG v1.13.44. |
| **Status** | **SETTLED.** README stays governed by `validate_docs_claims` for **version-staleness**, but is exempted from **technical-fragment pins**; substance moved to dedicated docs (SKILL.md / AGENTS.md / CONTRACTS.md). |

**Rule (the load-bearing lesson):** **Decode the structured failing check FIRST.** Run
`gh run view <id> --log-failed` (or read the specific failing test's assertion) and fix *that*
check. Do not theorize a root cause from a traceback and start reverting unrelated things — that
is what cost 4 cycles. This same "decode first" rule is why Battle 6's push-race is diagnosable in
one step.

## Battle 5 — dependency upper-cap silently downgraded the whole install on Python 3.14

| Field | Detail |
|---|---|
| **Symptom** | `uv tool install tensor-grep` (or `pipx`/`uvx`) on **Python 3.14** silently installed **stale 1.13.35** instead of the latest — with **no error**. |
| **Root cause** | The pin was `typer>=0.12,<0.25`. On py3.14 there was **no py3.14-compatible `click`** inside the `typer<0.25` range, so the resolver could not satisfy the newest tensor-grep's constraints and **silently resolved the WHOLE package DOWN** to the last version that fit. `requires-python >= X` has **no upper bound**, so nothing surfaced the mismatch as an error — a downgrade reads as success. |
| **Evidence** | PR #310 / v1.17.16 ("Allow typer 0.25 to unblock Python 3.14 installs"); commit `20d22c8`. Current pin in `pyproject.toml` is **`typer>=0.12,<0.26`**. The `<0.25` floor existed because typer 0.26 dropped `CliRunner.isolated_filesystem()` that **49** CLI tests use; typer **0.25.1** threads the needle (keeps that API AND supports py3.14). |
| **Status** | **SETTLED** at `typer>=0.12,<0.26`. |

**Rule:** When a **new** Python version yields a **stale** install, suspect a transitive
dependency **upper cap** (typer / click / pydantic), **not** `requires-python`. An upper cap that
is unsatisfiable on a newer Python degrades silently — always validate an unpinned install on the
newest supported Python end-to-end before assuming a cap is safe. Tightening any `<X` cap is a
change-control event (`tensor-grep-change-control`).

## Battle 6 — release "didn't publish": the push-race (non-fast-forward)

| Field | Detail |
|---|---|
| **Symptom** | PR CI is green, the `fix:`/`feat:` code is on `main`, but **no `chore(release): vX` commit appears and PyPI does not update** — the version "didn't publish". |
| **Root cause** | The real publish is the **`Semantic Release` job inside `.github/workflows/ci.yml`** (gated `github.ref=='refs/heads/main' && github.event_name=='push'`). It **compiles native assets first, so it runs ~6 min** — a race window. If **any** merge lands on `main` during that window — *including a no-release `docs:`/`chore:` PR* — `main` advances and the in-flight release's final `git push origin main` (the version-bump commit) is **rejected non-fast-forward** (`! [rejected]  main -> main`). The CI concurrency group serializes **runs**, not the human/agent act of clicking merge. |
| **Evidence** | AGENTS.md "Release publish is not instant — the push-race (hard-won, re-confirmed 2026-07-02)". Receipt: **v1.17.23 (#318, a security batch) failed to publish** because the GPU-pause `docs:` PR **#319** was merged while #318's release job was still compiling assets. `release.yml` is `workflow_dispatch`-only (a manual tag cannot bypass semantic-release). |
| **Status** | **SETTLED discipline = one-merge-per-tick.** Merge ONE release-bearing PR -> wait for its `chore(release): vX` commit on `main` AND the new PyPI version -> then merge the next. Applies even to no-release docs/chore PRs. |

**Recovery — do NOT panic-rerun.** The failure **self-heals**: the next push-to-`main` re-runs
`Semantic Release`, and because the version is **derived from git tags** (not the failed run's
state), it recomputes the correct next version and covers the orphaned commit. Just confirm that
next run's `Semantic Release` job succeeds. **Diagnose** with the structured result first:
`gh run view <id> --json jobs` -> find `Semantic Release` -> `gh run view <id> --log-failed`. A
`! [rejected]  main -> main` line is the push-race signature; anything else is a different problem.

## Battle 7 — the IDF-ranking-fragility flip (the IDF root-cause guess was DISPROVEN)

| Field | Detail |
|---|---|
| **Symptom** | A change that added **zero ranking terms** (a GPU-code diff) silently flipped the agent capsule's bridge-binary choice from **"tie, ask the user"** to **"confidently pick a marker no-op"** — i.e. a ranking surface (`tg search --rank`, agent capsule, semantic) silently changed a **safety** behavior on corpus change, and the blast radius was **invisible to the call graph**. |
| **Root cause** | The **first guess — "missing IDF weighting" — was DISPROVEN** by a thinktank, and the minimal "just widen the candidate pool" fix was **proven insufficient**. The real cause is a stack: a **flat, no-IDF scorer** + a **hard top-5 candidate cap** + a `file_score` flip + an **alphabetical path tie-break**. (IDF = inverse document frequency, the term-rarity weighting BM25 uses; its absence makes common and rare terms score alike.) |
| **Evidence** | PR #302 shipped a **degrade-to-ask safety floor** (force `ask_user` when the post-swap primary is still an *unrequested* marker), de-fragilized the self-referential live-test into a safety-floor assertion, and moved the exact-identity contract to a deterministic fixture. See CHANGELOG #302 and AGENTS.md ranking notes. |
| **Status** | **PARTIALLY SETTLED — OPEN DEBT.** The **safety floor** shipped. The underlying **flat no-IDF scorer still exists** and remains fragile; a proper fix is deferred to a separate **benchmarked `repo_map`** PR. |

**Rule:** Do **not** ship your first root-cause guess for a ranking flip (IDF was the wrong
guess here). Ranking surfaces can **silently degrade a safety behavior** on corpus change — treat
any capsule/`--rank`/semantic change as safety-relevant, keep the degrade-to-ask floor, and gate
scorer changes on a **measured** benchmark (`tensor-grep-benchmark-and-proof-toolkit`), never a
clean-looking diff. No speed/quality claim without numbers vs the accepted baseline.

## Battle 8 — the broken-oracle trap: green mock/FFI tests while the real bridge was DEAD

| Field | Detail |
|---|---|
| **Symptom** | Mock-based FFI tests were **green**, yet the real PyO3 passthrough bridge **dropped every forwarded flag** — the passthrough was effectively **dead** and users got wrong/unaccelerated results. |
| **Root cause** | The bridge passed `None` for `glob`/`file_type` into an rg `Vec<String>` -> `TypeError` -> a broad `except` swallowed it into a **silent fallback** that dropped all flags. The **mocks never exercised the real extension**, so the tests validated nothing about the live boundary — a **broken oracle** (a test that passes regardless of whether the thing works). |
| **Evidence** | PR #309 / v1.17.15 ("Forward dropped rg flags through PyO3 bridge + revive the passthrough (audit #3)"); commit `fd30e6d`. The fix forwards the hardcoded flags and guards `(… or [])`. |
| **Status** | **SETTLED.** |

**Rule:** **Verify FFI/bridge changes against the REAL extension** (a live `maturin develop`
runtime call), never mocks alone. A green mock is a hypothesis, not proof (see the unwritten rule
"never trust a self-report"). For the eval/grader version of this trap — a whole lane failing
uniformly, a grader that passes an empty answer, `pnpm.cmd` exit-127 on Windows — load
**`trustworthy-cuj-scoring`**, which enforces **bidirectional** oracle validation (a correct
answer must PASS *and* a wrong/empty answer must FAIL before any batch is trusted).

---

## Cross-cutting lessons (the meta-patterns behind the battles)

These recur across the chronicle; internalize them and you avoid the next re-fight too.

1. **Decode the structured failure FIRST, never theorize from a traceback.** (Battle 4's 4 wasted
   cycles; Battle 6's one-step diagnosis.) `gh run view <id> --log-failed`, read the failing
   assertion, fix *that*.
2. **Green ≠ working when the test doesn't touch the real boundary.** Mocks, cancelled CI, and
   Windows-only local runs all produce false green. (Battles 2, 3, 8.) Dogfood the **real**
   published binary — `CliRunner` bypasses the `bootstrap` front door.
3. **Silent success is the dangerous failure.** A dependency downgrade (5), a swallowed FFI
   `except` (8), and a ranking flip (7) all *look* like success. Fail **closed** and make legit
   fallbacks **visible** (`fallback_reason`) — see `tensor-grep-architecture-contract`.
4. **The naive optimization/root-cause guess is usually wrong here.** FFI for the walk (1), IDF
   for the ranking flip (7). Measure before you commit to a mechanism.
5. **Merge/release is a serialized human act, not just a CI gate.** One-merge-per-tick (6);
   never merge on cancelled CI (2).

## Provenance and maintenance

Re-verify these before treating any claim above as current (drift-prone facts are date-stamped
**as of 2026-07-02, v1.17.25**):

```bash
# Current version + latest settled entries
grep -E '^version *=' pyproject.toml            # expect 1.17.25 (or newer)
head -20 CHANGELOG.md

# Battle 1 (PyO3 dir-walk revert) + Battle 2 (free-threading revert)
git show b2f3fdd --stat
git log --oneline --all | grep -iE "free-thread|revert #266|gil_used"

# Battle 3 (fork-bomb guard still present across the 3 files)
git grep -n "fork-bomb" src/tensor_grep/cli/bootstrap.py rust_core/src/main.rs rust_core/src/python_sidecar.rs

# Battle 5 (dependency upper-cap that bit py3.14)
grep -nE "typer" pyproject.toml                 # expect typer>=0.12,<0.26

# Battle 6 (push-race discipline, authoritative)
grep -n "push-race" AGENTS.md

# Battle 7 (IDF-ranking safety floor) + Battle 8 (dead-bridge revive)
grep -nE "#302|#309" CHANGELOG.md
```

If any command's output no longer matches the entry (e.g. the typer cap moved, a guard file was
renamed, a battle reopened), **update this skill in the same PR that changed the fact** and note
it under the affected battle's Status. Do not let this chronicle drift — a stale failure record is
how a settled battle gets re-fought. Reopening any SETTLED entry goes through
`tensor-grep-change-control`.
