---
name: tensor-grep-large-repo-scale-campaign
description: >
  Use when tg hangs, stalls, or runs for minutes on a large/unscoped repo; when
  `--deadline` "seems ignored" and a symbol query still overruns its budget; when
  working task #52 (end-to-end deadline ineffective on a ~1800-file TS repo), the
  #390 daemon-path deadline gap, caller-scan / build_repo_map latency, the
  unscoped-`tg search` hang, or the exit-2 partial-result semantics. The
  decision-gated campaign to finish agent-native SCALE HONESTY: tg must never hang
  and never silently lie (return an empty/partial result as if it were complete) on
  a customer-scale repo. Gives the reproduce -> phase-instrument (cProfile) -> ranked
  solution menu -> fail-closed build -> change-control promotion runbook with exact
  commands, expected numbers, and branch-on-mismatch forks. Verified against v1.40.2
  + merged-unreleased #400 on 2026-07-05.
---

# tensor-grep — Large-Repo Scale-Honesty Campaign

A decision-gated runbook for the project's hardest **live** problem: making tg
**bounded and honest at customer scale.** Two failure shapes, one contract:

1. **Never hang.** Every scan path has a wall-clock bound; when the bound trips it
   returns a **partial** result, never spins forever.
2. **Never silently lie.** A truncated/partial result is **flagged** (`result_incomplete` /
   `partial` in JSON, an exit-2 signal for symbol commands, a stderr warning) so an
   agent can tell "complete zero" from "gave up early." A quietly-empty result that
   reads as "no matches / no callers / dead code" is the one bug a context tool cannot
   ship.

This skill is the campaign map: what already shipped, what is still open, the exact
commands + expected numbers at each gate, the wrong paths that are fenced off, and how
promotion routes through change-control. **You ship nothing user-visible from this
skill without beating a measurable gate and a conscious flag-flip** (Phase 4).

---

## Why this is the live frontier (receipts)

- **`--deadline` is threaded but not yet end-to-end effective (task #52, OPEN).**
  Receipt (2026-07-05, on a real ~1884-file TypeScript repo, **pre-#396**):
  `tg callers QueryEngine --deadline 10` took **~25 s** (not ~10 s), and a direct
  `deadline_seconds=8` call took **>90 s**. Root cause: `build_symbol_callers_from_map`
  (the caller-scan) **re-reads/re-parses** candidate files in an `any()`-loop per
  definition, so "each stage is bounded" did **not** make the pipeline bounded.
- **#396 (v1.39.1) shipped a caller-scan re-parse cache + `Path.resolve()` memoization**
  — the code comment measures **~18 s of ~22 s** wall time was redundant `resolve()`
  churn (`src/tensor_grep/cli/repo_map.py:74-76`), claims **7.9x on central symbols.**
  So the pre-#396 receipt numbers are STALE — **Phase 0 re-measures at HEAD.**
- **The unscoped-`tg search` hang** is documented in `AGENTS.md:167`: `tg search PATTERN`
  with no path (or `--glob X -l` without a scoped path) "hangs ~600 s then errors" on
  this repo because tg's own index dirs + a vendored tree were not auto-excluded. The
  in-flight fix is **#400** (see below), merged but not yet released.

---

## 0. When to use this skill — and when to use a sibling instead

Use this skill when the task is **bounding/honesty at scale**: a hang, a `--deadline`
overrun, task #52, the #390 daemon gap, the exit-code partial contract, or a
latency profile of a graph command on a big repo.

| If you actually need to… | Use this sibling instead |
| --- | --- |
| A hang/slowness whose CAUSE is unknown — systematic bisection first | `tensor-grep-debugging-playbook` (then return here) |
| Single-file / small-repo micro-latency, or make a number claim-quality | `tensor-grep-benchmark-and-proof-toolkit` |
| The front door / routing / registration / backend contract | `tensor-grep-architecture-contract` |
| Register a new flag/command (2 front doors, 4 sites) | `tensor-grep-config-and-flags` |
| Merge/release/experimental-flag gates + the incidents behind them | `tensor-grep-change-control` |
| Learn a settled battle (FFI reverts, dep caps, mock-vs-real, golden-sensitivity) | `tensor-grep-failure-archaeology` |
| Build/run the toolchain (uv, maturin, cargo) | `tensor-grep-build-and-env` |
| Update README/AGENTS/docs after shipping | `tensor-grep-docs-and-writing` |
| Position externally (never "faster grep") | `tensor-grep-release-and-positioning` |

**This skill never routes around change-control.** It produces the *evidence*; the
flip is a `tensor-grep-change-control` decision (Phase 4).

---

## 1. What already shipped (verify each before you build on it)

The **P0-6 "moat" deadline program** (#384-#399) + the in-flight unscoped-hang fix.
Every anchor below is at HEAD on 2026-07-05; re-verify line numbers, they drift.

| Ship | What it does | Verify |
| --- | --- | --- |
| **#384-#388** deadline threading | `deadline_seconds` -> `_deadline_monotonic_from_seconds` -> `build_repo_map(deadline_monotonic=...)`; converted once to an absolute `time.monotonic()` stamp so the scan can self-bound and return partial. | `grep -n deadline_seconds src/tensor_grep/cli/repo_map.py` (builders at ~11728/12024/12263/12646) |
| **#389/#393** graph-command CLI `--deadline` | `tg callers / refs / impact / blast-radius` gained `--deadline FLOAT`; #393 bounds the **caller-scan traversal** itself. | `tg callers --help` shows `--deadline FLOAT RANGE`; source `main.py:7990/8086/8150/8289` |
| **#394** payload `result_incomplete` | Truncation stamped at the payload layer so MCP/`_json` consumers see it, not just the CLI. | `grep -n result_incomplete src/tensor_grep/cli/repo_map.py` |
| **#395** `tg inventory --deadline` | inventory walk wall-clock bounded. | `main.py:6819` (`--deadline`), `build_inventory(..., deadline_seconds=...)` |
| **#396** caller-scan cache | `_mtime_aware_cache` (mtime+size in key) + `_resolved_path_str` `lru_cache(8192)` + `_module_aliases_for_path` `lru_cache(16384)`->`frozenset` (PR #345). **7.9x on central symbols.** | `repo_map.py:87/95/5384` |
| **#398/#399** exit semantics | #398 exit 2 on ANY truncated partial; #399 **walked it back**: exit 2 only when the partial is **also EMPTY**; a found-but-scan-capped result exits **0** with the truncation flagged. | `main.py:7710-7720`; `docs/CONTRACTS.md:108` |
| **#400** unscoped-hang fix (e7f18b7, **MERGED, UNRELEASED** as of 2026-07-05) | (A) `_SKIP_DIR_NAMES` now excludes `_tg_refs` / `.tg_semantic_index` / `external_repos` (`repo_map.py:150-152`); (B) **native per-file search walk** got a wall-clock bound — `compute_native_walk_deadline` / `native_walk_deadline_exceeded` (`cpu_backend.py:22-37`), checked per file, breaks to a partial with `result_incomplete`+stderr warning (`main.py:6467-6482`); (C) `_should_refuse_unbounded_vendored_root_scan` (`main.py:3770`, exit 2) refuses a root with `node_modules`/`vendor`/`external_repos`/`third_party` at top level, **duplicated** into `bootstrap.py:576` because that front door fast-paths native/rg past `main.py` (the recurring "two front doors" class). Single source of dir names: `io/directory_scanner.py:36`. | `git log --oneline origin/main \| head` (is there a `chore(release)` ABOVE e7f18b7 yet?) |

**Merge/release state to stamp every session:** #400 sits **above** the `v1.40.2`
release commit (`8829441`) with **no release bump above it yet** — so its behaviors
(native-walk bound, vendored-root refusal) are **NOT in the installed `tg 1.40.2`
binary.** To dogfood them today, run source: `uv run --no-sync python -m tensor_grep ...`.
Re-check merge/release with `git log --oneline origin/main | head`.

---

## 2. Still open (the campaign's actual work)

- **#52 — end-to-end deadline ineffective on a large TS repo.** The caller-scan
  re-parse dominates; "each stage bounded != pipeline bounded." Re-measure at HEAD
  (post-#396) — the campaign is CLOSE-able if the numbers now hold, and NOT if they don't.
- **#390 — daemon-path deadline gap.** The `_from_map` builders that operate on a
  cached session `repo_map` are inconsistently bounded. VERIFY per command:
  `build_symbol_callers_from_map` (`repo_map.py:12284`) **does** take `deadline_monotonic`;
  `build_symbol_impact_from_map` (`repo_map.py:11752`) **does not**. Daemon-served
  queries that skip `build_repo_map` (the map is cached) are not bounded by the scan
  deadline for the part they still compute.
- **Default budget.** The native-walk bound reuses `configured_ripgrep_timeout_seconds()`,
  which now defaults to **60 s** (`subprocess_policy.py:44`, was 600 s). `AGENTS.md:167`
  still narrates "600 s" — that doc lags; the resolver is the source of truth.

---

## 3. The phased runbook (decision-gated)

Run in order. Each gate states the expected observation and where to **branch**.
PowerShell is the dev-box shell; `uv run` is cross-platform. Use `uv run --no-sync`
so a bare `uv run` does not re-sync away the `[dev]` tree (`tensor-grep-build-and-env`).

### Phase 0 — Reproduce the baseline on a REAL large repo (NEVER skip)

You cannot claim "bounded" without the unbounded number, and you cannot claim a fix
without the pre-fix number. Use a **public** large TS repo so this is reproducible
(never a private customer path).

```powershell
# thousands of .ts files, deep import graph — a public customer-scale proxy
# (any large TS repo works; substitute one with >~1500 .ts files if you prefer)
git clone --depth 1 https://github.com/microsoft/TypeScript C:\tmp\ts-ref
$repo = "C:\tmp\ts-ref\src"

# Wall-clock the bounded command against its own budget (source build = HEAD, has #400):
Measure-Command { uv run --no-sync python -m tensor_grep callers $repo Node --deadline 10 --json | Out-Null }
```

**Expected + gate:**
- **GATE 0 (the #52 test):** total elapsed should be **<= deadline + ~10%** (i.e.
  ~11 s for `--deadline 10`). Read the JSON: `partial`/`result_incomplete` must be
  `true` **iff** the scan was actually truncated, and a truncated result must carry a
  non-empty `incomplete_reason`/`caveat`.
- **If elapsed >> deadline (e.g. 25 s for a 10 s budget)** -> **#52 is still confirmed
  open at HEAD.** Proceed to Phase 1 to find where the budget leaks.
- **If elapsed is bounded AND truncation is honestly flagged** -> the P0-6 program may
  be **CLOSE-able.** Do NOT just declare victory: re-run on a *second* large repo and a
  *central* symbol (highest fan-in), confirm the exit code matches CONTRACTS.md:108,
  then route promotion of the "closed" claim through Phase 4 / change-control.
- **If it HANGS (no return, no error) on an unscoped `tg search`** on a root with a
  vendored dir -> confirm you are on source (`python -m tensor_grep`, which has #400),
  not the released `tg 1.40.2`; #400's refusal (`exit 2`) + native-walk bound should
  make this impossible on source. A hang on source is a NEW bug -> `tensor-grep-debugging-playbook`.

### Phase 1 — Phase-instrument the ACTUAL slow command (do NOT guess)

Profile the command Phase 0 flagged, on the same repo. cProfile is the oracle here
(the graph commands do **not** all expose `--profile`; only `context-render` and
`blast-radius-render` do — verified against `tg --help`). This invocation is verified
to run and print stats:

```powershell
# tottime = internal (self) time -> finds the true hot function.
# tg's own output prints first; the profile table is APPENDED at the end -> tail it.
uv run --no-sync python -m cProfile -s tottime -m tensor_grep callers $repo Node --deadline 30 2>&1 | Select-Object -Last 30
```

**Expected + gate (verified shape on a real repo):** the top `tottime` rows are the
**per-file parse** — on Python targets `{built-in method builtins.compile}`, `ast.walk`,
and `repo_map.py:1182(_python_imports_and_symbols)`; on TS targets the analog is the
tree-sitter parse via `repo_map.py:1244(_typescript_parser)` — invoked **many times**,
because `build_symbol_callers_from_map` re-parses candidate files in its `any()`-loop
(`repo_map.py:2593-2594` documents the "N definitions -> N re-reads/re-parses" hazard).

- **GATE 1a — caller-scan re-parse dominates** (many parse calls, high `ncalls` on the
  parse/`resolve` functions): the leak is the **re-parse loop**, not the one-shot map
  build -> **Solution menu candidate (a) or (b)** in Phase 2.
- **GATE 1b — a single `build_repo_map` pass dominates** (parse called ~once per file,
  not per definition): the map build itself is the cost -> the fix is bounding/caching
  `build_repo_map`, which #384-#388 already partly did via `deadline_monotonic`;
  re-measure whether the deadline is honored INSIDE that pass -> different branch,
  likely the #390 gap, not #52.
- **GATE 1c — `resolve()` / path work dominates** despite #396: the cache is being
  defeated (e.g. an uncached `resolve()` before the cache lookup — `repo_map.py:1679-1680`
  warns about exactly this) -> candidate (b), fix the cache ordering.

> **Redundancy measurement before you design a cache (candidate b obligation):**
> monkeypatch the parse function with a `collections.Counter` keyed by path and count
> how many times each file is re-parsed for one `callers` call. This is the technique
> that overturned a code-review guess in PR #345 (see fenced paths). Numbers first,
> then design the key.

### Phase 2 — Solution menu (RANKED), each with a proof obligation

Pick top-down. Each candidate carries an obligation you must **verify/measure**, not
assume, before building.

#### Candidate (a) — bound the caller-scan re-parse loop (preferred)
Apply the **same per-file deadline check** #400 used for the native search walk to the
caller-scan `any()`-loop: check `native_walk_deadline_exceeded` (or an equivalent
`deadline_monotonic`) once per file, and on expiry break to a partial.
- **Obligation:** the partial must be **fail-closed and flagged** — set
  `result_incomplete = True` + a concrete `incomplete_reason`, and let
  `_emit_symbol_command_result` (`main.py:7678`) apply the exit contract (empty partial
  -> exit 2; non-empty partial -> exit 0 with the flag). Mirror #400's shape
  (`main.py:6467-6482`): break, never return a clean empty.
- **Why preferred:** it directly closes #52 with a mechanism already proven in-tree; low
  blast radius; deterministic.

#### Candidate (b) — extend the #396 caching to the residual re-parse
If Phase 1 shows re-parses that #396's caches miss, widen coverage.
- **Obligation — cache-key correctness:** a repo-map cache MUST key on file
  **mtime+size** (use the existing `_mtime_aware_cache`, `repo_map.py:95`), not a plain
  `lru_cache` keyed on path alone — a plain cache returns **stale** results in the
  long-lived daemon (the code comments at `repo_map.py:29` + `82-92` document this trap
  and the `_MTIME_CACHE_CLEAR_REGISTRY` sweep). Prove the redundancy with the
  Counter-monkeypatch (Phase 1) BEFORE adding the cache, and prove correctness with a
  mutate-file-then-re-query test.
- Caching bounds the *common* case but does **not** bound a pathological single file —
  ship it WITH candidate (a), not instead of it.

#### Candidate (c) — close the #390 daemon-path deadline gap
Thread `deadline_monotonic` through the `_from_map` builders that lack it (start with
`build_symbol_impact_from_map`, `repo_map.py:11752`) so a daemon-served query on a
cached map is still bounded.
- **Obligation:** the daemon serves from a **cached** `repo_map`, so `build_repo_map`'s
  deadline never fires; the bound must live in the per-symbol traversal. Verify the
  daemon path actually reaches the bounded code (dogfood via the running daemon, not
  just the in-process function). Separate gate from #52 — a distinct PR.

#### Candidate (d) — replace the regex/slow TS parse with tree-sitter (HIGHEST RISK — fenced)
Only if (a)+(b)+(c) leave the parse itself as the irreducible hotspot.
- **Obligation (mandatory, before any swap):** **golden parity across the parser corpus.**
  A parser change alters symbol/import extraction for every downstream command; prove
  byte-identical (or explicitly-diffed-and-accepted) output on the AST parity corpus
  first (`benchmarks/run_ast_parity_check.py`). Do this in a fenced branch; do not mix
  it with a bounding fix.

### Phase 3 — Build behind the fail-closed contract + measure the gate

- Write the failing test first (`tests/unit/test_repo_map_targets.py`,
  `tests/unit/test_cli_modes.py` are the patterns #400 used). TDD, then the smallest fix.
- Honor the **Backend Fail-Closed / partial contract** (§4): a bound that trips
  produces a **flagged partial**, never a silent empty, never a raw crash.
- **Re-run Phase 0 + Phase 1** on the reference repo after the fix.

**Promotion gate (all must hold, measured on the reference repo, same symbol):**

| Metric | Requirement |
| --- | --- |
| Wall-clock vs budget | elapsed **<= `--deadline` + ~10%** (the #52 bar) |
| Truncation honesty | `partial`/`result_incomplete` set **iff** truncated; non-empty `incomplete_reason` |
| Exit code | matches `docs/CONTRACTS.md:108` (0 complete / 1 complete-not-found / 2 incomplete-empty) |
| No regression | benchmark-regression CI gate green (`benchmarks/check_regression.py`); correctness identical on the AST parity corpus |
| Real binary | dogfood via `scripts/dogfood/` on the REAL artifact, not CliRunner |

- **GATE 3:** if the wall-clock still overruns the budget OR a partial isn't flagged,
  **do not ship** — the honesty contract is the whole point. Iterate or record the
  negative result.

### Phase 4 — Promote through change-control (never here)

This skill produces evidence; **`tensor-grep-change-control` owns the flip.**
1. One **release-bearing PR per tick** (respect the push-race / one-merge-per-tick rule,
   `tensor-grep-release-and-positioning`).
2. Attach Phase 3 evidence (before/after wall-clock table + exit-code proof + parity).
3. **Re-dogfood on the REAL large repo before declaring the contract done** — this is
   the #399 lesson: #398 shipped an exit-code rule that had to be walked back one release
   later because the real-repo behavior (every large-repo query exiting 2) was wrong.
4. Update `docs/CONTRACTS.md` / `AGENTS.md` / `SESSION_HANDOFF.md` if the contract
   changed (`tensor-grep-docs-and-writing`).

---

## 4. Fenced-off wrong paths (do NOT do these)

| Forbidden | Why | Do instead |
| --- | --- | --- |
| **Raise the default timeout** to "fix" a hang | Masks, doesn't bound. A bigger number still hangs on a bigger repo; it just moves the wall. | Add a real per-file wall-clock bound that returns a flagged partial (#400 pattern). |
| **Return a silent empty / clean 0-result** on timeout | Violates the Backend Fail-Closed Contract (`AGENTS.md:216-218`). A partial that reads as "no matches / no callers / dead code" is the exact bug this campaign exists to kill. | `result_incomplete = True` + `incomplete_reason` + stderr warning + the exit-2-if-empty contract. |
| **Assume "each stage bounded => pipeline bounded"** | The #52 lesson: `build_repo_map` and caller-scan were each bounded in isolation, yet the end-to-end command overran because the caller-scan re-parses ~all files. | Measure the WHOLE command wall-clock (Phase 0), then profile it (Phase 1). |
| **Guess the hotspot from code review** | PR #345 receipt: a review council guessed a 3.6%-of-runtime path; the live profile found the real one (`_module_aliases_for_path` called ~1.4M times) -> `lru_cache`+`frozenset` cut the run **61.7 s -> 12.8 s (4.8x)**. | Profile the ACTUAL slow command at scale (Phase 1); the profiler is the oracle. |
| **"Fix" a golden to match your dev box** | #363 receipt: a "stale" golden was edited to include rg submatches, then reverted — the golden is backend-sensitive and CI has no rg. CI is the oracle. | Read the failing CI job's -/+ diff first; force a deterministic backend in the test. `tensor-grep-failure-archaeology`. |
| **A plain `lru_cache` keyed on path in the daemon** | Returns stale results across a long-lived session when a file changes (`repo_map.py:29`, `82-92`). | `_mtime_aware_cache` (mtime+size in key) + register its `cache_clear` in the sweep registry. |
| **Ship the parser swap (candidate d) with a bounding fix** | Conflates a correctness-risky change with a latency fix; a parity regression would hide behind the perf win. | Fence the parser swap in its own branch, gated on AST parity FIRST. |
| **Route around change-control / auto-merge** | Non-negotiable. Autonomy is draft-PR-only. | Produce evidence here; let change-control gate the flip. |

---

## 5. The contract you are defending (exit codes + fail-closed)

**Symbol-command exit codes are a 3-state agent contract** (`docs/CONTRACTS.md:108`,
enforced at `main.py:7710-7720`):

| Exit | Meaning | Agent action |
| --- | --- | --- |
| `0` | complete result (or a non-empty result whose scan was capped, with the truncation flagged in JSON) | trust the findings; if `result_incomplete`, raise the budget for *more* |
| `1` | genuine not-found on a **complete** scan | the symbol truly is absent |
| `2` | **INCOMPLETE and empty** — truncated by `--deadline` or `--max-repo-files`, and nothing found | do NOT treat as complete; retry with a larger budget or narrower scope |

The **native-walk bound** (#400) is the search-side analog: on expiry it sets
`all_results.result_incomplete = True` + `incomplete_reason`, writes a stderr warning,
and **breaks** (`main.py:6467-6482`) — a flagged partial, never a silent empty. Any new
bound you add MUST follow this shape (see `tensor-grep-architecture-contract` for the
full `BackendExecutionError` contract).

---

## 6. When NOT to use this skill

- **A hang/slowness whose cause you don't yet know** — do the systematic bisection in
  `tensor-grep-debugging-playbook` first; come back here once it's a *scale/bounding*
  problem.
- **Single-file or small-repo micro-latency**, or turning one measurement into a
  claim-quality number -> `tensor-grep-benchmark-and-proof-toolkit` (noise-floor,
  fair-baseline rules).
- **Registering the flag/command mechanics** for a new bound -> `tensor-grep-config-and-flags`
  (2 front doors, 4 sites) + `tensor-grep-architecture-contract`.
- **The merge/release flip itself** -> `tensor-grep-change-control`.

---

## Operator practice (dated, CEO-enforced 2026-07)

- **Profile-at-scale, don't council-guess a hotspot** (PR #345). For a latency fix the
  profiler is the oracle; a diff-review council correctly *killed* a wrong theory but
  then *guessed* the wrong hot path.
- **Dogfood the REAL large repo before declaring a contract done** (#399 walked back
  #398 one release later on real-repo behavior). Fixture/self-repo tests give false green
  for scale honesty.
- **Model tiering for any fan-out:** set the model explicitly per seat — haiku for
  scans/discovery, sonnet for the bulk (profiling readers, fix implementers, verifiers),
  opus for planning/synthesis/hard debugging. State the split before the fan-out runs.
- **Windows FS reality:** worktree "tests pass" is a hypothesis — re-run in a real venv;
  hammer concurrency/timeout tests 15-20x (Linux-reasoning agents miss Windows FS
  semantics; delete-pending `PermissionError`, `os.replace` `WinError5`).
- **Git hygiene:** relocate uncommitted work with `git checkout -b X origin/main` (carries
  it), never a bare `git stash pop`; never broad `git checkout -- .` with edits you need.

---

## Provenance and maintenance

Every claim above is verifiable from the repo at HEAD on **2026-07-05** (released
**v1.40.2** = `8829441`; the unscoped-hang fix **#400** = `e7f18b7`, **merged above
v1.40.2 but not yet released**). Re-run these when a claim may have drifted; date-stamp
any change.

- **Is #400 released yet?** `git log --oneline origin/main | head` — look for a
  `chore(release)` commit ABOVE `e7f18b7`. Until then, dogfood #400 via source
  (`uv run --no-sync python -m tensor_grep ...`); the installed `tg 1.40.2` lacks it.
- **Deadline threading:** `grep -n deadline_seconds src/tensor_grep/cli/repo_map.py | head`
  and `tg callers --help | grep -i deadline`.
- **#52 still open?** Re-run Phase 0 on a large repo; compare elapsed vs `--deadline`.
- **#390 daemon gap:** `grep -n "def build_symbol_.*_from_map" src/tensor_grep/cli/repo_map.py`
  then check which take `deadline_monotonic` (callers: yes; impact: no, as of 2026-07-05).
- **Native-walk bound + default budget:** `grep -n "native_walk_deadline\|compute_native_walk_deadline" src/tensor_grep/backends/cpu_backend.py`;
  `grep -n TG_RG_TIMEOUT_SECONDS src/tensor_grep/cli/subprocess_policy.py` (default 60 s, not the 600 s `AGENTS.md:167` still narrates).
- **Vendored-root refusal (two front doors):** `grep -n "_should_refuse_unbounded_vendored_root_scan\|_search_paths_include_vendored_root" src/tensor_grep/cli/main.py src/tensor_grep/cli/bootstrap.py`.
- **Exit contract:** `sed -n '108p' docs/CONTRACTS.md` and `sed -n '7710,7720p' src/tensor_grep/cli/main.py`.
- **#396 caches:** `grep -n "_mtime_aware_cache\|_resolved_path_str\|_module_aliases_for_path" src/tensor_grep/cli/repo_map.py`.
- **Profiling / parity harness:** `ls benchmarks/run_ast_parity_check.py benchmarks/check_regression.py`; `_ProfileCollector` at `repo_map.py:376`.

**Open / candidate (not settled — do not present as done):**
- #52 (end-to-end deadline) and #390 (daemon gap) are **OPEN**; whether #396's 7.9x
  already closes #52 at HEAD is **unknown until Phase 0 re-measures.**
- The caller-scan re-parse bound (candidate a) and the parser swap (candidate d) are
  **unbuilt candidates**; the parser swap is gated on AST parity and may never be worth it.
- Doc-of-record narrative lags reality: `AGENTS.md:167` (600 s), `SESSION_HANDOFF.md`
  (stalls at v1.19.x) — trust the code + `docs/CONTRACTS.md`, note the doc lags.
