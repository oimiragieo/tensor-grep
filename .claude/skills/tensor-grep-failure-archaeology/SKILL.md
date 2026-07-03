---
name: tensor-grep-failure-archaeology
description: Use when about to "fix" or "optimize" something in tensor-grep that feels novel — before proposing PyO3/FFI for directory walking, re-enabling free-threading, adding a --json self-test, tightening a dependency upper-cap, blaming an IDF/ranking flip, trusting a green mock/FFI test, diagnosing a release that "didn't publish", chasing a reported latency "regression" without profiling at scale, shipping a doc-drift/precision heuristic off green fixtures alone, adding a "differs-from-default" native-delegation gate, reading a `capfd`-based CliRunner test result, or micro-optimizing a hot loop without checking who actually consumes the value. A chronicle of settled battles (symptom -> root cause -> evidence -> status) so no one re-fights them. Load it to check "has this already been tried and lost?" before spending effort. For a live NEW failure use tensor-grep-debugging-playbook; for the process gates to re-attempt one use tensor-grep-change-control.
---

# Tensor-Grep Failure Archaeology

A chronicle of **settled battles** in tensor-grep: expensive fights that already reached a
verdict. Each entry is `symptom -> root cause -> evidence -> status` so a future engineer or
model does not burn a day re-discovering the same wall.

Facts below were verified against the repo on **2026-07-02 at v1.17.25**, with a second pass on
**2026-07-03 at v1.19.3** (Battles 9-14). Re-verify anything load-bearing with the commands in
**Provenance and maintenance** before you act on it.

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
- Chasing a reported latency "regression" by guessing the hot path instead of profiling the
  **actual slow command** at scale.
- Shipping a doc-drift / precision-heuristic feature because its fixture tests are green.
- Adding a **"differs-from-default"** runtime gate to decide native-delegation refusal.
- Reading `capfd`/fd-level captured output from a `CliRunner`-invoked command.
- Micro-optimizing a hot loop (e.g. skip-unless-needed) without checking every consumer of the
  skipped value first.
- Gating a **second** sequential merge-watcher on "did the tag change since I started" instead of
  an absolute condition.

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

## Battle 9 — native delegation silently dropped `--rank`/`--sort-files` (+ the `query_pattern` landmine)

| Field | Detail |
|---|---|
| **Symptom** | `tg search --rank --cpu` (also `--rank --json`/`--ndjson`, `--sort-files --cpu`) silently returned **unranked / unsorted** results — no error, output just looked plausible and wrong. |
| **Root cause** | Native-tg delegation `sys.exit()`s **before** the Python-side BM25 rerank and the in-backend sort ever run. `rank_bm25` and `sort_files` were `SearchConfig` fields that were neither forwarded into the native argv nor listed in the refuse-tuple (`_NATIVE_TG_DELEGATION_DEFAULT_REQUIRED_FIELDS`), so the gate happily delegated and the flags evaporated. A tempting alternative fix — a generic **"differs-from-default" runtime gate** that refuses delegation whenever any field is non-default — was explicitly rejected: `query_pattern` is auto-set on **every** search, so a blanket differs-from-default check would trip on it on literally every invocation and kill the native fast path entirely (a re-confirmation of a 2026-06-30 failure mode; see `tensor-grep-config-and-flags`). |
| **Evidence** | Commit `5e6f780` (#342, "fix: refuse native delegation for --rank/--sort-files (silent wrong-output) + coverage ratchet") adds `sort_files`/`rank_bm25` to `_NATIVE_TG_DELEGATION_DEFAULT_REQUIRED_FIELDS` in `src/tensor_grep/cli/main.py`. New governance ratchet `tests/unit/test_native_delegation_field_coverage.py` **AST-derives** the forwarded-field set from `_build_native_tg_search_command` and asserts every `SearchConfig` field is forwarded \| refused \| gate-handled \| in the documented `_NATIVE_TG_DELEGATION_KNOWN_GAP_FIELDS` frozenset (which explicitly names `query_pattern` with the differs-from-default caveat in its comment). |
| **Status** | **SETTLED** for `rank_bm25`/`sort_files`. **OPEN debt**: the `KNOWN_GAP` frozenset (`ast_prefer_native`, `ast_selector`, `ast_stdin`, `nlp_threshold`, `use_jit`, `case_sensitive`, `ignore_dot`, …) documents fields that were *already* silently dropped before #342 — acknowledged, not fixed. |

**Rule:** Same bug class as #336's `-u`/`-uu` no-op — a `SearchConfig` field that affects output is
a silent-drop landmine unless it is forwarded to the native argv, added to the refuse-tuple, or
explicitly gated. Adding a new `SearchConfig` field? Load `tensor-grep-config-and-flags` and run
the coverage ratchet. **Never** propose a generic "differs-from-default" gate for delegation
refusal — `query_pattern` makes it degenerate to "always refuse."

## Battle 10 — the `capfd` capture-surface trap: a delegation-routing fix broke fd-level test assertions

| Field | Detail |
|---|---|
| **Symptom** | Right after Battle 9's fix made `--rank` correctly refuse native delegation, `test_search_rank_reorders_by_bm25` started raising `JSONDecodeError` — every `test-python` job on `main` went red, even though the BM25 rerank behavior itself was correct. |
| **Root cause** | Before the fix, `--rank` wrongly delegated to a **native subprocess**, whose stdout landed on the real OS file descriptor — exactly what pytest's `capfd` fixture (fd-level capture) reads. After the fix, the rerank runs **in-process**, so the JSON is emitted via `typer.echo()` inside the `CliRunner`-invoked call and lands in `CliRunner`'s captured `result.stdout` (a Python-level buffer), never touching the OS fd. The test still read `capfd.readouterr().out` → empty string → `json.loads("")` failed. |
| **Evidence** | Commit `ab717a1` (#343, "test: fix --rank bm25 capture for the non-delegation path (#342 follow-up)"), diff in `tests/integration/test_bm25_search_flag.py`: dropped the `capfd` fixture parameter, switched the assertion to `json.loads(result.stdout)`. Comment left in the test explains the fd-vs-in-process split "only surfaces when the native binary is built, which PR CI skips but main/release CI builds." |
| **Status** | **SETTLED.** |

**Rule:** A test that reads `capfd` against a `CliRunner.invoke()` result is **implicitly asserting
that the command delegated to a real subprocess** (whose output crosses the OS fd boundary) rather
than running in-process. Any change to native-delegation routing — what gets forwarded vs. refused
— can silently flip which capture surface carries the real output. If you touch
`_can_delegate_to_native_tg_search` or the refuse-tuple, run `tests/integration/` locally, not just
`tests/unit/`. This is the sibling of Battle 3's "dogfood the real binary, not `CliRunner`": here
`CliRunner` is the right tool, but you must know **which stream** it captures for the path you just
changed.

## Battle 11 — `MatchLine` hashability regression, and a "free" micro-opt that wasn't

| Field | Detail |
|---|---|
| **Symptom** | (a) A `MatchLine` with a populated `submatches` field raised `TypeError` on `hash()` — a frozen dataclass silently lost its hashability contract. (b) A follow-on "obvious" optimization (only stash `submatches` when `--vimgrep`/`--column` is requested) broke an existing test in the same PR. |
| **Root cause** | (a) `submatches` (added in #340) is typed `tuple[dict[str, object], ...] \| None` — a tuple of dicts is **unhashable** — so any future caller that hashes/dedupes a `MatchLine` would crash the instant a real rg run populated it. (b) The gating micro-opt (`want_submatches = config.vimgrep or config.column`) looked like free wasted-work removal — a profiler had flagged the per-match stash as a small cost during the Battle 12 blast-radius hunt — but `tests/unit/test_submatches_output_shaping.py::test_backend_stashes_submatches_without_inflating_count` calls `RipgrepBackend().search(..., SearchConfig())` with a **default** config (`vimgrep=False`, `column=False`) and asserts `result.matches[0].submatches is not None`. The gate made that default-config case return `None`, failing the test — the "wasted work" was not actually dead: a consumer expects it populated regardless of output format. |
| **Evidence** | Commit `80de0b4` (#344). The **final merged diff** touches only `src/tensor_grep/core/result.py` (`submatches: tuple[...] \| None = field(default=None, compare=False)`) and adds `tests/unit/test_matchline_submatches_hashable.py` — it does **not** touch `src/tensor_grep/backends/ripgrep_backend.py`. The two squashed sub-commits show why: `65022bc` added the `want_submatches` gate to `ripgrep_backend.py` (`+3 -1` there), and `4c34516` **reverted exactly that gate** (`ripgrep_backend.py \| 5 +----`), keeping only the hashability fix. |
| **Status** | **SETTLED** for (a) — `compare=False` is the shipped fix. (b) is **SETTLED as "already tried and reverted"** — do not re-propose the vimgrep/column gate without first checking `test_submatches_output_shaping.py`'s consumer contract. |

**Rule:** `field(default=None, compare=False)` is the fix shape for "add a field to a frozen
dataclass that must stay hashable but holds an unhashable value type" (the offsets are a pure
function of text+line, so excluding it from `==` is correct too). Before gating a per-match stash
"because only formatter X consumes it," grep the actual consumers first — a test that breaks the
instant you ship a profiler-motivated micro-opt is not an obstacle to route around; it is the
consumer contract telling you the guess was wrong.

## Battle 12 — the reported +33% blast-radius "regression" was noise; the council's own hot-path guess was also wrong

| Field | Detail |
|---|---|
| **Symptom** | An AI-user report claimed `tg blast-radius` (depth-2) regressed **+33%** (188s → 250s) between two tagged versions. |
| **Root cause** | Two separate wrong guesses were made and disproven before the real fix. **(1)** A 3-lens+opus review council correctly ruled out a noise-regression theory on the reported deltas, but then **guessed** the hot path was AST parsing — live profiling later showed `compile()` was only **3.6%** of runtime, and a `Counter`-monkeypatch of `ripgrep_backend` calls showed **zero** frames on the actual `tg callers` path the council had profiled (they profiled the wrong file/path — 0 calls on-path). **(2)** A follow-on regression-hunt workflow's own synthesis guessed "cache the AST parse" as the fix; profiling proved that would have saved only **~3%**. The ACTUAL dominant cost, found only by a **profile-at-scale** of the real `tg blast-radius --depth 2` invocation on a high-fan-in symbol (290 callers), was `_module_aliases_for_path`: called **1,431,341 times** for only **~1,000 unique path inputs** inside the reverse-import-graph / PageRank loops (6.1s self / 38s cumulative of a 62s run) — a pure function of the path string, rebuilt on every call instead of cached. Separately, the reported cross-version **+33% was itself confirmed to be environmental noise**: the plain `tg callers` path was byte-identical between v1.17.31 and HEAD. |
| **Evidence** | Commit `bb5dc59` (#345, "perf: memoize _module_aliases_for_path — blast-radius depth-2 ~4.8x faster (62s->13s)"); adds `@lru_cache(maxsize=16384)` + a `frozenset` return type to `_module_aliases_for_path` in `src/tensor_grep/cli/repo_map.py`, plus `tests/unit/test_module_aliases_cache.py`. Result: **61.7s → 12.8s (4.8x)**, byte-identical output (affected=62; 231 blast-radius/callers/pagerank parity tests unchanged). Commit message states explicitly: "corrects the regression-hunt synthesis, which guessed AST-parse caching (would have saved ~3%)." |
| **Status** | **SETTLED** — real fix shipped and dogfooded. The "+33% regression" claim itself is **SETTLED as noise**, not a code defect. |

**Rule:** For a **latency** claim, the profiler is the oracle — not a diff-review council, and not
even a first-pass profiler read: this hunt needed a **second** profiling pass (profile the real
command at scale, with a call-count monkeypatch) before the true hotspot appeared, because the
first profile ran on the wrong command entirely. Before accepting any "X% regression" report,
reproduce it on the byte-identical code path across both versions before designing a fix — it may
be noise (see also `tensor-grep-benchmark-and-proof-toolkit`). A pure, no-I/O helper function
called repeatedly inside a graph/loop algorithm is a near-free `@lru_cache` candidate; check
`dict[str, set]` → `dict[str, frozenset]` downstream type hints when the cached value is
shared/iterated but never mutated in place.

## Battle 13 — `tg diff-docs`: 17 green fixture tests, 20k+ false positives on the real corpus (deliberately deferred)

| Field | Detail |
|---|---|
| **Symptom** | A doc-drift-detection prototype (`tg diff-docs`: flag a doc code-span identifier that no longer resolves to a repo symbol) shipped a fully-passing fixture suite — **17/17** unit tests green — but running it on the **real** repo corpus (`docs/` vs `src/`) produced an unusable flood of findings dominated by stdlib/language type names. |
| **Root cause** | Fixture tests only exercise the hand-picked cases the author thought of; they cannot reveal a heuristic's real-world false-positive rate. The design's own module docstring already cited the risk — "DocPrism: naive code-doc drift detection is 0.62 precision / 98% flag-rate" (arXiv 2511.00215) — but shipped anyway because the fixtures were green. Dogfooding on the real corpus confirmed the DocPrism trap: "unresolved symbol in a doc code-span" flags common language/stdlib type names (e.g. `String`, `Option`, `Vec`) that were never going to resolve in `repo_map`'s symbol table because they are not project symbols — the heuristic has no **positive** repo-reference signal, only a "not found locally" absence signal, so anything foreign reads as "drifted." |
| **Evidence** | Commit `90b7042` on branch `wip/diff-docs-precision` ("wip: tg diff-docs foundation (DEFERRED — precision inadequate, see task)"), adding `src/tensor_grep/cli/diff_docs.py` (303 lines) and `tests/unit/test_diff_docs.py` (17 `def test_...` functions, direct-counted). `docs/SESSION_HANDOFF.md` records: "`tg diff-docs` was prototyped and deliberately deferred pending a precision rebuild — naive doc-drift detection floods false positives (documented follow-up)." The feature was **never merged to `main`**; it lives only on the `wip/` branch. (The exact real-corpus finding counts were an ad hoc dogfood measurement during the deferring session, not persisted as a committed benchmark artifact — treat the shape, not the digits, as load-bearing.) |
| **Status** | **OPEN / DEFERRED BY DESIGN.** Not a bug to fix — a feature intentionally **not** shipped. A rebuild needs (1) a positive repo-reference signal (e.g. a qualified `repo_module.symbol` span where the module resolves in-repo but the symbol doesn't — not a bare identifier), and (2) a measured real-corpus precision gate **before** merging, not just green fixtures. |

**Rule:** **Fixture-green is not sufficient for a precision/heuristic feature.** Before shipping
anything that classifies or flags ("is this drifted", "is this dead code", "is this a bug"),
dogfood it on the **real** project corpus and eyeball the finding **count** and the **top hits**,
not just whether the unit tests pass. Prefer deferring (park on a `wip/` branch) over shipping a
feature that trains the agent/user to ignore its own output — thousands of false positives is
worse than no feature. See also Battle 8's broken-oracle framing: a fixture suite that only tests
hand-picked true/false cases is a weaker oracle than a real-corpus run.

## Battle 14 — sequential release-watcher deadlock: gate on absolute state, never "changed since I launched"

| Field | Detail |
|---|---|
| **Symptom** | A sequential merge-watcher (Battle 6's one-merge-per-tick discipline, automated) that gates the next merge on "has the release tag changed **since I started watching**" can deadlock permanently if a release was already in flight — or already published — at the moment the watcher launched, because the tag never "changes" relative to a baseline that was itself already stale. |
| **Root cause** | A relative baseline ("did X change since t0") is only correct if t0 is guaranteed to precede the event being waited for. A watcher that launches **after** a publish already started (or completed) cannot distinguish "still waiting" from "already done" if it only tracks deltas from its own launch time — it must instead check **absolute** state: the prior PR's `state == MERGED`, and the specific `main` CI run (identified by run ID / commit SHA, not "newer than my start time") has actually completed. The full pipeline is also longer than the ~6-minute pre-publish compile window documented in Battle 6 — a real measured run (PR #346, `gh run` id `28667549718`, 2026-07-03) took CI-verification-start (15:01) to `publish-pypi` success (15:41) — **~40 minutes** — and to the post-publish `release-tag-smoke` check completing (15:45) — **~45 minutes end-to-end**. `build-release-native-assets (macos-15-intel, cpu)` and `native-build-smoke (macos-15-intel)` were consistently the **longest individual jobs** (~12 min and ~11.7 min respectively) at both the CI-verification and release-asset-build stages — a watcher with a short or relative-only wait window will misdiagnose an in-flight release as "stuck" or "already done." |
| **Evidence** | Measured via `gh run view 28667549718 --json jobs`: `repo-hygiene`/`smoke` start `15:01:06-15:01:19`; `Semantic Release` `15:21:43-15:25:49`; `build-release-native-assets (macos-15-intel, cpu)` `15:25:53-15:37:58` (the longest release-asset job); `publish-pypi` `15:41:16-15:41:57`; `release-tag-smoke` completes `15:45:47`. AGENTS.md's "Release publish is not instant — the push-race" section (Battle 6) documents the shorter ~6-minute pre-publish compile window that creates the push-race; the **full** pipeline through PyPI + tag-smoke is longer still, as measured above. |
| **Status** | **SETTLED discipline** (a corollary to Battle 6, applied to automated watchers rather than human merge timing): gate any sequential watcher on **absolute** conditions — prior PR `state == MERGED` AND the specific `main` CI run (by ID) has `status == completed` — never on "did the tag change since my launch." Size any watcher wait window for the full measured pipeline (~40-45 min), not the ~6-minute compile window alone. |

**Rule:** This is Battle 6's push-race discipline applied to **automation**, not just human merge
timing. A polling/watcher loop that checks "did state change since I started" is race-prone
whenever the state could have already changed **before** the loop started (a pre-launch publish).
Always identify a specific, absolute target to wait FOR (a run ID, a PR merged-state, a version
string) — never a relative delta from an arbitrary observation point. Re-measure the pipeline
duration periodically (`gh run view <id> --json jobs`) — CI job mix and runner speed drift over
time, and a stale duration assumption is how a watcher's timeout gets tuned wrong.

---

## Cross-cutting lessons (the meta-patterns behind the battles)

These recur across the chronicle; internalize them and you avoid the next re-fight too.

1. **Decode the structured failure FIRST, never theorize from a traceback.** (Battle 4's 4 wasted
   cycles; Battle 6's one-step diagnosis.) `gh run view <id> --log-failed`, read the failing
   assertion, fix *that*.
2. **Green ≠ working when the test doesn't touch the real boundary.** Mocks, cancelled CI, and
   Windows-only local runs all produce false green. (Battles 2, 3, 8.) Dogfood the **real**
   published binary — `CliRunner` bypasses the `bootstrap` front door. Fixture-green is also not
   enough for a **precision/heuristic** feature (Battle 13) — dogfood the real corpus too.
3. **Silent success is the dangerous failure.** A dependency downgrade (5), a swallowed FFI
   `except` (8), a ranking flip (7), and a dropped `--rank`/`--sort-files` (9) all *look* like
   success. Fail **closed** and make legit fallbacks **visible** (`fallback_reason`) — see
   `tensor-grep-architecture-contract`.
4. **The naive optimization/root-cause guess is usually wrong here.** FFI for the walk (1), IDF
   for the ranking flip (7), AST-parse caching for the blast-radius "regression" (12 — the
   council's own guess, not just the first author's). Measure before you commit to a mechanism,
   and be ready to profile a **second** time if the first profile targeted the wrong command.
5. **Merge/release is a serialized human act, not just a CI gate.** One-merge-per-tick (6); never
   merge on cancelled CI (2); the same discipline applies to **automated watchers**, which must
   gate on absolute state, not "changed since I launched" (14).
6. **A latency/regression report needs reproduction on the byte-identical path before you design
   a fix.** Battle 12's "+33%" was noise, not a defect — chasing it with a code change first would
   have shipped a pointless "fix" for nothing.
7. **A routing/capture-surface change can silently redirect where output goes.** Battle 9's
   delegation fix moved `--rank`'s JSON from an fd-level stream to `CliRunner`'s buffer, breaking
   a `capfd`-based test (10) — any change to what gets forwarded/refused/run-in-process needs the
   **integration** tests run locally, not just unit tests.
8. **A test that breaks under your "obvious" cleanup is telling you something.** Battle 11's
   vimgrep/column stash-gate looked free until a consumer-contract test caught it — grep the real
   consumers before trusting a profiler-motivated guess that a value is unused off one path.

## Provenance and maintenance

Re-verify these before treating any claim above as current (drift-prone facts are date-stamped
**as of 2026-07-02, v1.17.25**, with Battles 9-14 verified **2026-07-03, v1.19.3**):

```bash
# Current version + latest settled entries
grep -E '^version *=' pyproject.toml            # expect 1.19.3 (or newer)
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

# Battle 9 (rank/sort-files delegation refuse-tuple + query_pattern KNOWN_GAP)
git show 5e6f780 --stat
grep -n "sort_files\|rank_bm25\|query_pattern" src/tensor_grep/cli/main.py tests/unit/test_native_delegation_field_coverage.py

# Battle 10 (capfd -> result.stdout capture-surface fix)
git show ab717a1 -- tests/integration/test_bm25_search_flag.py

# Battle 11 (MatchLine hashability fix + reverted stash-gate micro-opt)
git show 80de0b4 --stat                         # expect ONLY core/result.py + new test, NOT ripgrep_backend.py
git show 65022bc --stat; git show 4c34516 --stat  # the add-then-revert pair

# Battle 12 (blast-radius memoization, corrects the regression-hunt's own AST-cache guess)
git show bb5dc59 --stat
grep -n "lru_cache" src/tensor_grep/cli/repo_map.py

# Battle 13 (tg diff-docs deferral — check it is STILL unmerged before citing as current)
git branch -a | grep diff-docs
git log -1 --format=%s 90b7042
grep -n "diff-docs" docs/SESSION_HANDOFF.md

# Battle 14 (release pipeline duration — re-measure, this drifts with CI/runner changes)
gh run list --workflow=ci.yml --limit 5 --json databaseId,displayTitle,conclusion
gh run view <latest-release-run-id> --json jobs -q '.jobs[] | "\(.name)\t\(.startedAt)\t\(.completedAt)"'
```

If any command's output no longer matches the entry (e.g. the typer cap moved, a guard file was
renamed, a battle reopened, `tg diff-docs` merged to `main`, the release pipeline got faster or
slower), **update this skill in the same PR that changed the fact** and note it under the affected
battle's Status. Do not let this chronicle drift — a stale failure record is how a settled battle
gets re-fought. Reopening any SETTLED entry goes through `tensor-grep-change-control`.
