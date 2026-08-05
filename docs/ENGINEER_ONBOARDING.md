# Engineer Onboarding: tensor-grep

This is the front door for a new engineer. It reorganizes the law in `AGENTS.md` (2251 lines,
written rule-by-rule as each was learned) by TASK -- what you are trying to do right now -- and
keeps, for every rule, the one-line receipt that explains why it exists. In this repo a rule
without its reason is a rule the next person deletes as clutter, so the receipts are load-bearing.

`AGENTS.md` remains canonical. When this document and `AGENTS.md` disagree, `AGENTS.md` wins;
fix the drift here in the same PR you notice it.

A note on citations: this repo's own law is **cite the SYMBOL, not the line number** -- files like
`src/tensor_grep/cli/repo_map.py` (19,000+ lines) and `cli/main.py` (17,000+) grow every release,
and five consecutive maintenance passes that re-stamped line numbers by hand each shipped anchors
that were already wrong. This document therefore names files and symbols and tells you to grep.
Version numbers stamped anywhere in prose (including here) drift; the live release state is the
`release_docs_current_tag` line in `AGENTS.md`'s "Current Handoff" section.

---

## 1. What tensor-grep is

tensor-grep (`tg`) is a code-intelligence CLI and MCP server built for AI coding agents, and
useful to humans for the same reasons. It layers three tiers on one command surface:

- **Text search, any language** -- ripgrep-class, usually BY delegating to `rg` or to a native
  Rust binary. tg does not try to beat rg at cold text search; that is the parity tier.
- **Structural (AST) search and rewrite, ~26 languages** -- via a wrapped `ast-grep` binary,
  with a native tree-sitter fallback for a narrower query shape.
- **A symbol graph, 10 languages registered -- but NOT at uniform depth.** This distinction is
  load-bearing and `docs/BACKLOG.md` says so in as many words: *"do not read '10/10 languages' as
  uniform depth."*

  | tier | languages | what you actually get |
  |---|---|---|
  | **Parser-backed (9)** | C, C#, Go, Java, JavaScript, PHP, Python, Rust, TypeScript | `defs` / `refs` / `callers` / `blast-radius` resolved from a real parse |
  | **Foundational (1)** | C++ | `defs` + `imports` are real; **`refs` / `callers` / `blast-radius` fall back to a REGEX heuristic** |

  **NEVER HAND-COUNT THIS. Ask the product:**

  ```bash
  python -c "import sys;sys.path.insert(0,'src');from tensor_grep.cli import repo_map as r;print(r._symbol_navigation_descriptor())"
  # parser-backed-refs-callers:c-csharp-go-java-javascript-php-python-rust-typescript+foundational-defs-imports-only:cpp
  ```

  That instruction is not decoration -- this line has been wrong THREE times. It once said 4/10
  with Go wrongly demoted (corrected 2026-07-27). The first version of THIS table said 3/5/2 with
  a phantom "unresolved" tier, because it hand-counted the `references_and_calls` field, found
  js/ts ABSENT rather than `None`, and quoted the unconfirmed guess as a measurement. An earlier
  pass reported 8/10 by grepping for the string `references_and_calls=`, which also matches
  `references_and_calls=None`. Substring presence is not value, a field's absence is not its
  meaning, and the product already knows the answer.

  **Why a junior must know this on day one:** run `tg callers` on a Java symbol and you get a
  regex-heuristic answer. The payload DOES label its provenance
  (`provenance_when_missing="grammar-missing"`), so tg is not lying -- but if you read the label as
  decoration you will trust a heuristic as a parse. Closing the gap is tracked as task #31.

  Plus agent-facing commands (`orient`, `agent`, `prepare`, `context`, `find`, `session`, `ledger`,
  `evidence`) producing token-budgeted, machine-readable context capsules.

The moat is the **agent-native context layer**, not raw speed. The product's central honesty
invariant: tg must never hang and never silently lie -- an empty result must be distinguishable
from a failed scan, and a truncated result must never read as complete. Most of the engineering
law in this repo exists to protect that invariant.

Implementation: a Python package (`src/tensor_grep/`, Typer CLI, Python >=3.11) plus a Rust
workspace (`rust_core/`, pinned toolchain 1.96.0) that builds both a PyO3 extension module
(`tensor_grep.rust_core`) and standalone native binaries (`tg`, `tg-search-fast`) shipped as
release assets. Released to PyPI by semantic-release on every `fix:`/`feat:` merge to `main`.

---

## 2. Your first hour

### 2.1 Install and verify

```bash
git clone https://github.com/oimiragieo/tensor-grep.git
cd tensor-grep
python -m pip install uv==0.11.25    # the exact version CI pins -- not "latest"

rustup default 1.96.0                # pinned in rust_core/rust-toolchain.toml
rustup component add rustfmt clippy

uv venv --python 3.12                # any >=3.11; CI matrix is 3.11 + 3.12
uv pip install -e ".[dev,ast]"       # builds the Rust extension via maturin automatically
```

Verify the install actually produced a working extension (an install that "didn't error" can
still have silently failed to build it):

```bash
uv run tg --version
uv run python -c "import tensor_grep.rust_core; print('rust_core OK')"
uv run pytest tests/unit/test_rust_core.py -q
```

Then run the four-step local gate once, so you know your environment can pass it:

```powershell
uv run ruff check .
uv run ruff format --check --preview .
uv run mypy src/tensor_grep
uv run pytest -q
```

### 2.2 Traps you WILL hit in week one

1. **`uv run tg` is NOT your working tree.** It resolves `tg` from `.venv/Lib/site-packages`,
   not from `src/`. A dev box can carry THREE disagreeing tg's (PATH-installed release, venv
   site-packages, `src/`), and nothing in the output says which one answered. Receipt: two
   confidently wrong conclusions in one session (2026-07-28), including a phantom
   Python-vs-Rust divergence that was the same stale copy on both arms. Before believing ANY
   measured behaviour:

   ```bash
   PYTHONPATH=src uv run --no-sync python -c "import tensor_grep.cli.main as m; print(m.__file__)"
   # must print ...src/tensor_grep/cli/main.py -- if it says site-packages, every number is stale
   PYTHONPATH=src uv run --no-sync python -m tensor_grep.cli.bootstrap <args>   # not `uv run tg`
   ```

2. **cargo/rustc are off PATH on the primary dev desktop.** Use
   `C:/Users/oimir/.cargo/bin/cargo.exe` or prepend `~/.cargo/bin` to PATH. And a "hanging"
   Rust build is almost always slow LTO that finishes: `maturin develop` ~15s, a `--release`
   build takes minutes. Do not kill it.

3. **`rustfmt` is REQUIRED before pushing Rust, and it exists at `~/.cargo/bin/rustfmt.exe`.**
   **CPU-SAFE** (defined here because the term is used throughout and is repo-local, not
   industry jargon): this desktop is a SHARED box that also runs the operator's own workloads, so
   CPU-heavy jobs must go to CI or a cloud agent, never here. It forbids local
   `cargo`/`rustc`/`clippy` builds and `tests/e2e/test_routing_parity.py` (which shells out to
   `cargo run`). It does NOT forbid the whole toolchain -- check whether the REASON (CPU cost)
   applies before assuming a ban. `rustfmt --check` is cheap and is REQUIRED before pushing Rust.
   The rule forbids local `cargo`/`rustc`/`clippy` (expensive on a shared box), but
   rustfmt is not a compiler -- it parses and formats in milliseconds. Three CI cycles were
   burned (2026-07-25) hand-deriving format diffs from logs before anyone asked whether the
   rule's REASON applied to rustfmt. Run `rustfmt --check` on every Rust file you touched --
   all of them; a 3-of-4-files check shipped a red CI run.

4. **`ruff format` flags: `--preview` always on format, never on check.** CI runs
   `ruff format --check --preview .`; a bare `ruff format` (no `--preview`) actively REVERTS
   preview-style formatting on disk and reds the next CI run. The trailing `.` is load-bearing
   too: under `--preview`, ruff formats Python fences inside Markdown, so a `src tests`-scoped
   run passes locally while an unformatted `docs/**/*.md` snippet reds CI (blocked v1.67.0).

5. **Editing a CRLF file in text mode flips every line ending.** `ci.yml` and `uv.lock` are
   CRLF; a Python text-mode write turns an 11-line change into a 1443-line diff. Use binary
   read + byte-replace + binary write.

6. **`git commit -m` with backticks runs shell command substitution** and mangles the message.
   Use `git commit -F <file>` or a single-quoted heredoc for any message containing backticks,
   `$`, or `!`.

7. **`MSYS_NO_PATHCONV=1` is required for `git cat-file blob origin/main:path` in git-bash**,
   or the ref gets mangled and the failure reads as "that path does not exist on origin/main"
   (twice produced a confident wrong conclusion). Related: parse `gh --json` output with
   python, not jq.

8. **Navigate the codebase with tg itself** (`tg search`, `tg defs`, `tg callers`, `tg orient`)
   rather than generic grep -- it exercises the product's own surfaces and catches routing
   regressions early.

### 2.3 Where the work comes from

`docs/BACKLOG.md` is the canonical prioritized work list; GitHub (`gh pr list`) is the source of
truth for PRs. Before building anything from a filed task description, confirm the defect still
exists on `origin/main` (`git cat-file blob origin/main:<path>`) -- task text is a snapshot of
what someone believed when filing it, and tasks have sat `pending` after their fix already
shipped in the published wheel (task #328).

---

## 3. The architecture in one page

The single most important fact about this codebase: **a search can take several different
dispatch routes, and a change proven on one route proves nothing about the others.** Most of the
worst shipped bugs were route bugs, invisible to tests that only exercised one door.

### 3.1 The front door: intercept before Typer

The published entry point is `tensor_grep.cli.bootstrap:main_entry` (`pyproject.toml`), NOT the
Typer app. `bootstrap` parses argv itself and, for a plain text search, forwards to the native
`tg` binary or to ripgrep BEFORE Typer ever runs. The Typer app (`cli/main.py`) is reached only
for tg-only flags (`bootstrap._TG_ONLY_SEARCH_FLAGS`), help, or commands that require the full
CLI (`_requires_full_cli`).

Consequence: **`CliRunner` tests bypass the front door entirely.** Any bug in bootstrap routing
-- a flag that leaks to rg, a wrong native/Python choice, a delegation loop -- is invisible to
CliRunner and green in CI while broken for every real user. This is exactly how the `--rank`
plain-text crash shipped. Routing coverage requires the real binary (section 5.4).

### 3.2 The three rg-passthrough doors

There are THREE independent places a search can be handed to ripgrep, and a symptom can be N
different bugs across them.

> **Do not fuse this with the four-releases story -- they are different lists that both happen to
> be small.** The "bare search is silent on zero results" report took four releases because its
> routes span rg-passthrough AND native-delegation AND the Python `is_empty` branch:
> bare text -> bootstrap rg passthrough (#857); `--ast`/`--rank`/`--semantic` -> the Python CLI
> `is_empty` branch (#862); `--json` -> bootstrap NATIVE DELEGATION (#862). Only the first is an
> rg door. An earlier draft of this doc attached that receipt here on the coincidence of "three",
> which would have taught a wrong route inventory and lost the actual lesson -- **the route space
> is wider than any one dispatch family.**

The three rg doors themselves:

1. **`bootstrap._run_rg_passthrough`** -- the pre-Typer front door, for plain text searches.
2. **`rust_core/src/routing.rs::route_search`** -- the shared native decision tree (below).
3. **`can_passthrough_rg` inside `cli/main.py::search_command`** -- a third door INSIDE the
   Typer app, for invocations a tg-only flag forced past the bootstrap door. It is gated on
   `rg` availability (a pure environment probe), not on any platform flag -- which produced a
   real Windows-vs-Linux CI divergence for `tg search --stats` purely because one CI leg had
   `rg` resolvable and the other did not.

### 3.3 The routing decision tree

`route_search` (documented in `docs/routing_policy.md`) returns a `RoutingDecision` carrying
`routing_backend`, `routing_reason`, `sidecar_used`, `allow_rg_fallback`. Priority order:

1. `--index` -> `TrigramIndex`
2. `--gpu-device-ids` -> `NativeGpuBackend` (must fail loud if unhonorable)
3. `--force-cpu`/`--cpu` with structured output or no usable rg -> `NativeCpuBackend`
4. AST command -> `AstBackend`
5. warm, non-stale, compatible `.tg_index` -> `TrigramIndex`
6. large corpus + GPU available + positive calibration -> `NativeGpuBackend`
7. rg available, no structured output, **and the request is not an admitted plain-text native
   request** -> `RipgrepBackend`. That last clause is not decoration: `rust_core/src/routing.rs`
   comments it as *"the ONLY thing standing between a plain-text search and the `rg` subprocess"*.
   Drop it from your mental model and you will misread which engine served a query.
8. else -> `NativeCpuBackend`
9. native CPU fails and `allow_rg_fallback` -> `RipgrepBackend` final fallback

Load-bearing consequences: rg is the normal cold-path backend when installed; auto-GPU is
effectively dormant (no crossover has ever been proven -- see section 10); and
`NativeCpuBackend` is TWO distinct engines behind one label (`native_search.rs`, deliberately
serial with a first-match latency contract, vs `backend_cpu.rs`, the PyO3/FFI fallback where
rayon parallelism lives) -- a benchmark on one is not evidence about the other.

### 3.4 Native delegation: forward-or-refuse

`_can_delegate_to_native_tg_search` (`cli/main.py`) gates whether a Python-side search hands the
ENTIRE search to the native binary and exits on its result. The invariant: delegation only when
native output is byte-equivalent to the Python path. Every `SearchConfig` field must be
classified as forwarded / refused / gate-handled / KNOWN_GAP, and the ratchet test
`tests/unit/test_native_delegation_field_coverage.py` AST-derives the forwarded set from the
real builder source, so adding a field without classifying it goes red immediately. Receipt for
why: `--rank --cpu` once silently delegated to a native binary that has no BM25, returning
unranked output that looked correct. Landmine, do not re-propose: "refuse if ANY field differs
from default" kills the fast path entirely, because `query_pattern` differs on every search.

### 3.5 The AST two-engine divergence

`tg run`/`tg scan`/MCP `tg_ast_search` can be served by `AstGrepWrapperBackend` (shells out to
`ast-grep`; full pattern DSL incl. `$NAME`/`$$$ARGS` metavariables) or `AstBackend` (in-process
tree-sitter; bare identifiers and s-expressions only, no metavariables). A metavariable pattern
with the wrapper absent raises `ConfigurationError` at three verified sites -- it must never
silently mis-route to the native engine. The reverse fallback (native-shaped pattern, ast-grep
absent -> tree-sitter) is DELIBERATE, so a CPU-only box still gets some AST capability; do not
"fix" it into a refusal. Full DSL parity is task #141 and stays demand-gated.

### 3.6 The Backend Fail-Closed Contract

The single most important correctness invariant, and the most repeatedly violated
(`backends/base.py`): every `ComputeBackend` MUST raise `BackendExecutionError` on real failure
-- never return a clean empty `SearchResult`, never silently swap engines for a contract flag.
A swallowed failure reaches a coding agent as a trustworthy "no matches", and the agent then
edits on the belief the symbol does not exist. That is the one lie a context tool cannot afford.

- Fail closed for any flag the fallback cannot preserve (`--pcre2` through a non-PCRE2 engine
  produced WRONG results, not slow ones).
- A legitimate degraded fallback must be VISIBLE: set `fallback_reason` and a distinct
  `routing_reason`. Never label heuristic output as model output.
- The recurring anti-pattern is a bare `except Exception:` returning empty or swapping engines.
  Fixed instances: the Rust/PCRE2 bridge, the ast-grep OOM mask (killed subprocess read as a
  clean 0-match), the tree-sitter query swallow, CyBERT classify. The structural fix
  (`SafeBackendMixin` + fault-injection CI gate) is planned, NOT shipped -- the discipline is
  still per-file.
- A new command does NOT inherit a sibling's boundary catch: `tg find` shared `--semantic`'s
  dense-embedding core but shipped its first wave without its own `DenseUnavailableError`
  catch -- it would have crashed instead of degrading to BM25 -- caught by the adversarial gate,
  not by green unit tests.

Companion invariant: **suppression != absence.** `SearchResult.result_incomplete` +
`incomplete_reason` mark "this engine ran but part of the output was suppressed" (e.g. rg exit
2 with one unreadable path among many). Any new path that can drop some results due to a soft
failure must set these fields -- neither raise-and-lose-the-good-results nor silently return
the good ones as if complete.

---

## 4. How to make a change safely

### 4.1 The Operating Rules (condensed)

1. Start with a failing test when behavior changes.
2. Make the smallest defensible change.
3. Keep local gates scoped on the shared desktop; PR/main CI runs the heavy matrices.
4. Benchmark every hot-path change (section 4.6).
5. Reject regressions even if the code is otherwise clean.
6. Do not change workflow/release/docs contracts without updating the validator-backed tests.
7. Never kill unrelated processes, restart WSL, or reboot as "cleanup" without explicit user
   approval -- other agents share this machine.
8. On ANY red CI check, decode the structured job result FIRST
   (`gh run view <id> --json jobs`, then the failing job's `--log-failed`), before theorizing.

### 4.2 TDD and the RED-ARM requirement

**A passing test proves nothing until you have seen it FAIL on the pre-fix revision.** Receipt
(#737): a new test for a C++ function-pointer fix pinned a shape tree-sitter ALREADY excluded
via an unrelated path -- it would have passed unmodified with the fix deleted, while the shape
the fix actually repaired had no test at all. The mechanical check:

```bash
git diff origin/main -- <file> > /tmp/fix.patch
git apply -R /tmp/fix.patch && pytest <new-tests>   # MUST fail
git apply    /tmp/fix.patch && pytest <new-tests>   # MUST pass
```

Two traps in running that check on this repo:

- **`tests/conftest.py` does `sys.path.insert(0, SRC_DIR)` from its own `__file__`, which
  OUTRANKS `PYTHONPATH`.** A baseline proven by pointing `PYTHONPATH` at a reverted tree gives
  a FALSE red-green -- conftest silently re-points imports at the worktree's own `src`. To
  prove a test fails on a baseline commit, use a fully isolated tree copy with the revert made
  INSIDE that copy.
- **Hang-class bugs (ReDoS, deadlock): write the fix BEFORE the red-phase test** -- a
  deadlock red-test run against unfixed code IS the hang it is testing. Wrap every test run in
  a shell timeout and distinguish slow-from-hung by exit code (124/137), not elapsed time.

Prefer STRUCTURAL assertions over wall-clock ones wherever the invariant allows: a
`max(baseline * N, floor)` timing assertion silently degenerates into the bare floor when the
baseline is below Windows' 0.015625s clock resolution (#739), and wall-clock thread-overlap
assertions false-fail on a starved CI runner -- assert the contract with `threading.Event`
handshakes or ordered ENTER/EXIT markers instead (A17, #701).

### 4.3 Registration sites: the "miss one, fail quietly" class

Adding a top-level `tg COMMAND` requires FOUR sites, or it silently misroutes:

| # | Site | File |
|---|---|---|
| 1 | `KNOWN_COMMANDS` set | `src/tensor_grep/cli/commands.py` |
| 2 | `Commands::X` variant + dispatch arm | `rust_core/src/main.rs` |
| 3 | `PUBLIC_TOP_LEVEL_COMMANDS` | `tests/e2e/test_routing_parity.py` |
| 4 | `@app.command` function | `src/tensor_grep/cli/main.py` |

Adding a search flag requires TWO front doors, or it leaks to ripgrep and crashes with
`rg: unrecognized flag` for anyone on the published binary (how `--rank` shipped broken while
CliRunner tests passed):

| # | Site | File |
|---|---|---|
| 1 | `SEARCH_PYTHON_PASSTHROUGH_FLAGS` | `rust_core/src/main.rs` |
| 2 | `bootstrap._TG_ONLY_SEARCH_FLAGS` | `src/tensor_grep/cli/bootstrap.py` |

A new MCP tool is a FIFTH registration site: bump `_TG_MCP_SERVER_CONTRACT_VERSION` in
`cli/mcp_server.py` whenever a tool's request/response shape changes (the `tg_find` MCP PR
shipped without the bump; only the adversarial gate caught it).

Before claiming any registration change done, **enumerate all N sites mechanically**.
`tg callers <fn>` finds callable registrations but is BLIND to set/list/decorator registrations
(`_TG_ONLY_SEARCH_FLAGS` is a set; `@app.command` is a decorator; the Rust dispatch is a match
arm) -- grep those. The CI registration-completeness gate has been BLOCKING since v1.17.1.

Adding a LANGUAGE to the symbol graph is the same class: `lang_registry.register_language` +
a self-contained `lang_<x>.py` module mirroring `lang_go.py` (NOT the stale inline `_rust_*`
style), hitting five seams -- the most-forgotten is `_target_language_for_path`, which feeds
the agent-capsule confidence gate. Grammar missing -> a labeled gap, never a regex fallback.
Full checklist: the `tensor-grep-add-language` skill.

### 4.4 Verify plans (yours and AI-drafted) against the real code

Every factual claim in a plan needs a `file:line`-style citation checked against real source
before building; an uncited claim is a hypothesis. AI plans reliably name plausible edit
locations that do not match the actual code (dead paths, renamed symbols, already-fixed lines)
-- a citation-enforced review caught 5 blockers in two plans in one session. This applies to
your own banked notes too: a carried-forward "the fix is obviously X" hypothesis was falsified
against the live AST before a line was written (task #736).

**Your own plan is the least-audited artifact you produce.** A plan with citations, a security
class, and control arms still contained five errors, including a fix that would silently
re-break a bug shipped fixed hours earlier. The mechanical tell: take the plan's acceptance
criteria PAIRWISE and ask whether any state satisfies both. And never downgrade a severity
class in your own plan -- the repo's taxonomy wins, or a mandatory gate gets skipped.

### 4.5 Scope, slicing, and honesty markers

- Scope a PR's diff to what its TITLE promises; split unrelated-but-correct work into its own PR.
- CPU-SAFE forbids local compiling, so **CI is the only oracle for Rust** -- ship the portion
  provable now, defer the rest as its own slice, and leave the gap AT THE CODE SITE (a comment
  naming exactly what is missing and why), not only in the tracker.
- Found a defect while working? **Fix it this turn**, regardless of authorship, CI visibility,
  or scope. "Do not commit this file" is not "do not fix this file". The only legitimate stop
  is a hard blocker, and that gets a tracked task with a concrete acceptance test.

### 4.6 Benchmarks and optimization

Never claim a speedup without measured numbers. Pick the script for the area you changed
(`AGENTS.md` "Benchmark Rules" has the full table): `benchmarks/run_benchmarks.py` +
`check_regression.py` (end-to-end CLI text search), `run_hot_query_benchmarks.py` (index/hot
cache), `run_ast_benchmarks.py` / `run_ast_workflow_benchmarks.py`,
`run_agent_workflow_benchmarks.py` (capsule/edit loop), `eval_late_rerank_quality.py`
(`tg find` ranking QUALITY, not speed).

Optimization discipline: measure-first (cProfile the PUBLISHED wheel via
`uvx --from tensor-grep==<ver>`); when merging/skipping work, prove output byte-identical two
ways (enumerate every producer branch AND differential-fuzz old-vs-new over real files); and
know that a WARM end-to-end run hides a cold-path win (a fn measured -36% warm was actually
~54% faster cold -- microbench the function directly or clear the cache between reps).

---

## 5. How to know your change is correct

### 5.1 The governing question

**What would this check show if the thing it verifies were BROKEN? If the answer is "the
same", it is not verification.** This is the single most repeated failure mode in the project's
history. Ask it of every green signal -- your test, your grep, your benchmark column, your CI
probe, your reviewer's claim.

### 5.2 The Verification-Oracle Family (10 forms)

Canonical text: `AGENTS.md` "The Verification-Oracle Family" (mirrored in
`tensor-grep-validation-and-qa` Part 0; adding a form is always a TWO-FILE edit). The short
index, each with what it catches:

| Form | Failure shape | One-line receipt |
|---|---|---|
| 1 | Normalize-both-sides: a comparator applies the same lossy transform to both arms, so real divergence reads as parity | rg-parity oracles were CRLF-blind (#262) |
| 1b | A new guard/ratchet green on the FIXED file only | a ratchet showed 0 violations on the very code that caused the incident (#848) -- run every new guard against the pre-fix revision and require non-zero |
| 2 | Harness corrupts output, manufacturing false failures | a test's own `replace("\\","/")` turned a literal `\0` into `/0`; the product was right, the harness lied |
| 3 | Test never executes -- SKIPPED IS NOT PASSED | the #266 proof test skipped in every CI job because the pytest step named one hardcoded file; read the SKIP count |
| 4 | The gate's ROOT-CAUSE story is wrong even when its verdict is right | verify the diagnosis, not only the finding |
| 5 | The repro's TOPOLOGY deletes the mechanism | every fixture for a `--no-ignore-vcs` fix was a NON-git dir -- the one topology where the fix works; vary topology, not just flags |
| 6 | The hostile FIXTURE silently no-opped | `icacls` failed to apply the deny ACE; assert the fixture BITES (require the `PermissionError`) before the probe runs |
| 7 | A measurement that cannot discriminate | a benchmark column scoring 0 for every tool separates nothing; every probe carries a positive control -- a zero without one means "measured nothing" or "never measured", indistinguishably |
| 8 | The SPLIT oracle: a precondition proved in a DIFFERENT run | ARM 1 never asserted its own run exited 2; a control in another process controls nothing |
| 9 | The reviewer's EXPECTED NUMBER is the broken half | a census mismatch is two-sided; enumerate the members before filing -- and re-derive any number handed to you by another agent |
| 10 | The oracle's unit is the BRANCH; the defect lives in the MERGE | #835 + #836, each fully green alone, reddened main together with no textual conflict; rebase onto the REAL target and run the union before pushing |

### 5.3 The most-used corollaries

- **A control arm that survives the revert is not a control arm.** An external audit found 3 of
  4 of one author's control arms still passed with the fix reverted.
- **A control that moves an ADJACENT variable falsely exonerates the right hypothesis** (#868:
  `rust_core` the extension vs the native `tg` binary -- two artifacts, adjacent names). Name
  the arm as the SYMBOL the code branches on, not the capability you believe it stands for.
  And a mechanism that reproduces the output byte-for-byte is SUFFICIENT, not proven OPERATIVE.
- **Trace the signal path.** A negative result means "absent" or "the signal never reached the
  probe", and nothing in the output separates them. Name every hop the phenomenon must travel;
  six wrong conclusions in one session (2026-07-28) were all instruments that silently lacked
  reach.
- **A probe that discards the child's error is worse than no probe.** Never pair
  `capture_output=True` with `check=True` in a diagnostic -- print argv, exit status, stdout
  AND stderr (three hypotheses were "tested" against a CI log that never contained the cause).
- **Consensus is not verification.** 2 of 3 independent lenses agreed on the WRONG answer for a
  design fork; only re-deriving from source settled it. Promote claims because their citation
  checked, never because several reviewers said it.
- **Review layers are ORTHOGONAL.** Plan audit / external code audit / CI / live dogfood found
  12 defects on one feature with zero overlap. Skipping a layer costs a CATEGORY, not a
  fraction.
- **Comments and docs get the same rigor as code.** A wrong assertion eventually reds a run; a
  wrong comment misleads readers indefinitely (#739 justified a degenerate ratio in a comment
  CI can never fail on). To certify a commit "docs-only", compare `ast.dump()` of both
  revisions -- strictly stronger than eyeballing the diff.
- **Fail-closed prose is an allow-list.** "Confirm it is NOT X" fails open the moment an
  unanticipated value appears; enumerate the SAFE cases and reject everything else.

### 5.4 Dogfood the real binary

After changing any search flag, command, or routing, verify against the INSTALLED published
binary -- `scripts/dogfood/` (Dockerfile + `dogfood_features.py`) installs the real PyPI wheel
and runs every public command shape. For precision/heuristic features (classifiers, ranking
weights), dogfood a REAL, LARGE corpus, not fixtures: the `tg find` whitespace classifier
passed a synthetic golden slice and mis-boosted 5/6 real identifiers.

Fast pre-push gate on agent-critical surfaces (~3-5 min):

```powershell
python scripts/agent_readiness.py --output artifacts/agent_readiness.json
tg dogfood --output artifacts/dogfood_readiness.json
```

---

## 6. How to ship it

### 6.1 PR title = release intent

Semantic-release reads the squash-merged commit subject on `main`:

| Title prefix | Effect |
|---|---|
| `feat:` | minor release |
| `fix:` / `perf:` | patch release |
| `feat!:` / `fix!:` | major release |
| `docs:` / `test:` / `chore:` / `ci:` / `build:` | no release |

Release-bearing PRs must use Squash and merge. Never create release tags manually.

### 6.2 The push-race (the rule that has cost the most releases)

The real publish is the `Semantic Release` job inside `.github/workflows/ci.yml`, and it runs
~6 minutes (it compiles native assets first). If ANY merge lands on `main` during that window
-- including a no-release `docs:` PR -- the in-flight release's final `git push` is rejected
non-fast-forward and **that version never publishes**. Receipt: v1.17.23 (a security batch)
lost its publish to a docs PR merged mid-window.

- Merge ONE release-bearing PR per publish cycle; wait for the `chore(release)` commit on
  `main` AND PyPI serving the new version before the next release-bearing merge.
- **The only safe merge gate is "the newest ci.yml run on main has reached COMPLETED".**
  `tag == PyPI` is NOT a gate -- it cannot distinguish "released" from "not started" from
  "died", and reading it as "gate open" cost a release (2026-07-28). `release-intent` being
  skipped also proves nothing: it is a PR-title validator, always skipped on a push (A33).
- Non-releasing PRs (`docs:`/`test:`/`chore:`) can be batched once no release is in flight --
  their gate is just "newest main run completed" (~6 min), and landing them first drains the
  queue much faster (A31). Even after `Semantic Release` succeeds, wait for PyPI: a new push
  cancels the publish TAIL (wheels, `publish-pypi`) via the concurrency group.
- A failed release SELF-HEALS on the next push (versions are tag-derived). Do not panic-rerun.
- "Newest main run COMPLETED", not "completed GREEN": when main is red, the hotfix must still
  be mergeable -- requiring green before merging the thing that makes it green is a deadlock.
  Everything ELSE stays parked while red (A32).

### 6.3 Query a run BY ID, never a windowed list

To watch a run, use `gh run view <run-id>` (or `gh run watch <run-id> --exit-status`). A
windowed list plus a filter gives a false "all terminal": six cron workflows firing on the same
SHA once pushed the real run out of a `--limit 6` view, and a monitor declared ALL TERMINAL
while the release was mid-flight. Related probe trap: `gh`'s check API returns
`conclusion: ""` (empty string, not null) for in-progress CheckRuns, so `.conclusion //
"PENDING"` never fires and a merge gate reports 0 pending while jobs run -- branch on
`.status == "COMPLETED"`, guard the TOTAL check count (jobs register progressively), and give
the probe a control against a run you KNOW is in flight.

### 6.4 Real failure vs flake

- **A red run with NO failing step is an interrupted run.** A genuine failure records
  `Run Pytest: failure`; empty conclusions on every step after a successful one mean the job
  was killed -- nothing was measured about the code. Print EVERY step with its conclusion.
- **Discharge a suspected flake by measurement, never plausibility.** Cheapest control:
  the same job on the commit that SUPERSEDES it (superset tree, same job, passes => the
  earlier red carried no information). Second control: a SIBLING job in the same run on the
  same infrastructure -- an environmental claim that predicts nothing about the neighbours is
  a shrug, not a diagnosis.
- **`cancelled` != failure**: rapid multi-merge makes each push's concurrency group cancel the
  prior push's run -- benign iff the NEWEST main run is green.
- **A red `test-python` with `-x` may have aborted before your units ran** -- decode WHICH test
  failed before assuming your PR broke it.
- Never "fix" a flake by widening a time budget (tried, 4x wasted wall-clock, reverted same
  day). A deadline-shaped duration spike (everything latching at exactly the configured bound)
  means the work is not completing, not that the budget is small.

### 6.5 After the merge

Verify the fix on the MERGED artifact, not only pre-merge -- a squash can drop a hunk (A29).
For release-bearing work, the final report names the PR, merge commit, main CI run, released
tag, and PyPI status; check the `release-tag-smoke` JOB's own conclusion inside the release run
(it sat red for 4 releases while PyPI kept publishing, masking a real regression). After
publish: `git fetch origin main --tags`, fast-forward local main, and close the loop with a
published-wheel dogfood (`uvx --refresh-package tensor-grep --from tensor-grep==<ver> tg
--version`, then the feature probes).

Do not casually edit `.github/workflows/ci.yml`, `.github/workflows/release.yml`, or
`scripts/validate_release_assets.py`; read `docs/CI_PIPELINE.md` first -- workflow, release,
and docs contracts are pinned by validator tests that must change in the same PR.

---

## 7. The exit-code contract (the honesty invariant, in code)

There is no single 0/1/2 table -- the contract is layered by command family. The authoritative
text is `docs/CONTRACTS.md` (symbol-command section) and the `tensor-grep-run-and-operate`
skill, section 11. Get this wrong and an agent scripting around tg will silently trust a
truncated answer as exhaustive -- the exact lie the product exists to prevent.

### 7.1 Symbol commands (`defs`/`refs`/`callers`/`impact`/`blast-radius`/`source`, also `tg prepare`)

| Exit | Meaning | What a consumer may conclude |
|---|---|---|
| 0 | Complete result | Trust it as the full answer |
| 1 | Genuine not-found on a COMPLETE scan | Safe to treat the symbol as absent |
| 2 | INCOMPLETE -- the scan was truncated (`--deadline` -> `partial:true`, or a `--max-repo-files` cap -> `result_incomplete:true`) | Never treat the list (found OR empty) as complete; retry with a bigger budget or a narrower PATH |

**Truncation trumps found.** A found-but-truncated result exits 2. This was litigated: #398
shipped exit-2-on-any-truncation, #399 briefly narrowed it to truncated-AND-empty, and a
unanimous design council reverted #399 (#401) -- a truncated caller set silently trusted as
exhaustive is a wrong-blast-radius refactor risk. The friction ("every big-repo query exits 2")
was a default-cap miscalibration, since fixed by raising the agent-family default to 2000
files, not a reason to fork the contract.

**Why a complete-but-narrow answer is never 2:** `blast-radius` distinguishes an OUTPUT cap
(`--max-callers`/`--max-files` trims a COMPLETE analysis for display -> exit 0, flagged
`callers_truncated`/`files_truncated`) from a SCAN cap (the analysis itself could not cover the
repo -> exit 2). Exit 2 means "I do not know the full answer", never "I chose to show you
less of an answer I fully computed".

### 7.2 Search family (`tg search` / `tg run`)

Mirrors ripgrep: 0 = match, 1 = clean no-match, 2 = usage/argument error or unhandled error.
Exit 1 is NOT a failure in a script. Scan truncation surfaces as `result_incomplete` in the
JSON payload (and reds the exit to 2 on the incomplete branches -- see
`SearchResult.result_incomplete` in section 3.6).

### 7.3 Others

`tg find`: `BackendExecutionError` -> exit 2 with a structured error envelope (never a
traceback); empty + incomplete -> 2, empty + complete -> 1; found + incomplete -> prints the
ranked partial results THEN exits 2 (symbol-command rule, not search-family).
`tg docs-coverage --check` exits 1 on doc drift (a CI gate). `tg orient` deliberately has no
exit-2 contract -- a truncated orient is informational. Consumer rule of thumb: branch on the
exit code first, then parse `result_incomplete`/`partial`/`deadline_limit` when completeness
matters.

---

## 8. Where to look things up

### 8.1 The in-repo skill library (`.claude/skills/`), indexed by intent

These are the onboarding handbook proper -- each is a runbook detailed enough to act on.

> **NOTHING KEEPS THIS TABLE IN SYNC. Re-derive it before trusting it.**
> An earlier version of this section claimed `tests/unit/test_skill_index_sync.py` guarded it. It
> does not: that test reads exactly two files, `AGENTS.md` and `CLAUDE.md`
> (`test_skill_index_sync.py:22-23`), and has never heard of this document. The claim shipped
> alongside a table that was already **five skills short of the 28 on disk** -- including
> `tensor-grep` itself, the one `CLAUDE.md` names first. A guard is scoped to its consumers, and
> this doc was not one of them. To check:
>
> ```bash
> ls -d .claude/skills/*/ | wc -l      # compare against the rows below
> ```


| You are trying to... | Load |
|---|---|
| Change, review, merge, or release ANY code (the gates) | `tensor-grep-change-control` |
| Debug a failure, hang, wrong/empty result, red CI (symptom -> triage table) | `tensor-grep-debugging-playbook` |
| Propose something that feels novel (was it already tried and killed?) | `tensor-grep-failure-archaeology` |
| Decide what counts as PROOF a change works | `tensor-grep-validation-and-qa` |
| Understand bootstrap/routing/backends before touching them | `tensor-grep-architecture-contract` |
| Use `tg` itself to navigate a codebase (search/defs/callers/orient) | `tensor-grep` |
| Stress-test tg across a multi-project workspace | `tensor-grep-workspace-dogfood` |
| Search across several repos with a scoped root | `tensor-grep-multi-project-search` |
| Assess enterprise readiness gaps / agent hard-stops | `tensor-grep-enterprise-agent` |
| Create or verify a review bundle | `tensor-grep-enterprise-review-bundle` |
| Domain theory (BM25, RRF, trigram indexing, retrieval) | `code-search-and-retrieval-reference` |
| Env vars, CLI flags, provider modes, the delegation ratchet | `tensor-grep-config-and-flags` |
| Set up / rebuild the dev environment | `tensor-grep-build-and-env` |
| Run the CLI day-to-day (exact syntax, exit codes, --deadline) | `tensor-grep-run-and-operate` |
| Measure health (`tg doctor`, `tg dogfood`, readiness) | `tensor-grep-diagnostics-and-tooling` |
| Write or edit a doc of record (which doc owns which contract) | `tensor-grep-docs-and-writing` |
| Merge release PRs, make public speed claims | `tensor-grep-release-and-positioning` |
| Claim/review/dispute a speedup or regression | `tensor-grep-benchmark-and-proof-toolkit` |
| tg hangs or runs minutes on a large repo | `tensor-grep-large-repo-scale-campaign` |
| Add a language to the symbol graph | `tensor-grep-add-language` |
| Run a multi-PR drain+build campaign | `tensor-grep-backlog-campaign` |
| One-call edit readiness / multi-agent coordination / NL search | `tensor-grep-prepare` / `tensor-grep-ledger` / `tensor-grep-find-and-route` |
| SOTA research: pitch, method, or semantic-search work | `tensor-grep-research-frontier` / `tensor-grep-research-methodology` / `tensor-grep-semantic-search-campaign` |
| GPU experimental paths | `tensor-grep-gpu` |

### 8.2 Docs of record (`docs/`)

| Question | Doc |
|---|---|
| What should I work on? | `BACKLOG.md` (canonical), `TASK_BOARD.md` (live queue) |
| What are the API/JSON/exit-code contracts? | `CONTRACTS.md`, `harness_api.md` |
| How is a search routed? | `routing_policy.md`, `architecture.md` |
| How does CI/release behave, contractually? | `CI_PIPELINE.md`, `RELEASE_CHECKLIST.md`, `package_manager_publish.md` |
| What was tried and rejected (optimization history)? | `PAPER.md` -- preserves failed attempts so they are not retried |
| Benchmark story and honest comparisons | `benchmarks.md`, `benchmarks_ast.md`, `tool_comparison.md` |
| What is experimental/hidden? | `EXPERIMENTAL.md` |
| GPU status | `gpu_crossover.md` |
| Platform support tiers | `SUPPORT_MATRIX.md` |
| Consuming tg from a harness/agent | `harness_cookbook.md`, `multi_agent_context_plane.md` |
| Emergency fix procedure | `HOTFIX_PROCEDURE.md` |
| Session state / recent history | `SESSION_HANDOFF.md`, `AGENTS.md` "Current Handoff" |

---

## 8.5 Security patterns you must sweep for (AGENTS.md's four targets)

Read this before touching subprocess construction, file writes, network reads, or directory walks.
These are not hypotheticals -- each has a receipt, and the fourth shipped a LIVE hole as recently
as 2026-07-31.

| pattern | the rule | why |
|---|---|---|
| **Native-argv flag injection** (CWE-88 / the MCP-276 CVE family) | Put a `--` end-of-options sentinel BEFORE every caller-influenced positional | A list-argv `subprocess` call stops SHELL injection and does NOTHING about FLAG injection: a value starting with `-` is parsed by the CHILD's option parser. A directory named `-i` becomes `--ignore-case`; the search silently runs against the wrong scope and still exits 0 |
| **Symlink-follow disclosure** | No `followlinks` in a walk | a walk that follows a symlink reads outside the scope the caller granted |
| **Pre-auth unbounded read** | Bound the read AND set a timeout BEFORE authenticating | an unauthenticated caller can otherwise pin memory |
| **Atomic-write permission window** | `os.open(O_CREAT\|O_EXCL, mode)`, never write-then-`chmod` | between the write and the chmod the file is world-readable |

**The argv sentinel is the one to internalise, and here is why it earns a whole subsection.** It
was tracked in prose in `AGENTS.md` as "the remaining tg sweep" -- and prose did not hold it. One
builder got fixed, the class was recorded closed, and a **caller-supplied path was still being
appended bare** in `cli/agent_capsule.py::_agent_gpu_evidence` months later. Three adversarial
review rounds then found four MORE holes in the fix itself, including a sentinel placed *between*
two positionals (present, and protecting nothing).

The population now lives in a test, not a sentence:

```bash
uv run pytest tests/unit/test_argv_sentinel_covers_every_builder.py -q
```

It enumerates 13 builders, CALLS each one, and asserts `--` precedes every caller-supplied value.
Three rules fell out of getting it wrong four times:

1. **Do not add a member by reasoning it is covered -- call it.** Every miss was the same judgement
   ("builder A transitively covers B"), and every one was disproved by deleting B's guard and
   watching the suite stay green.
2. **A guard whose placement is config-conditional has as many members as configurations.**
3. **Position is the property; presence is only a proxy.** A count, or a grep for `"--"`, cannot
   tell a working sentinel from a decorative one.

One member is out of reach of that test and is named rather than assumed:
`rust_core/src/rg_passthrough.rs::ripgrep_operand_args` is Rust-side, so no Python census sees it.

Full detail: `AGENTS.md` "Security Hardening Patterns", and the global `supply-chain-hardening`
skill.

---

## 9. Mistakes this codebase has already paid for

The short list a newcomer is most likely to repeat. Each entry is (rule) -- (receipt).

1. **Trusting CliRunner for routing** -- the `--rank` crash shipped to every published-binary
   user while unit tests were green; CliRunner never touches the bootstrap front door.
2. **Merging anything while a release is in flight** -- v1.17.23's security batch never
   published because a docs PR merged during the ~6-min publish window.
3. **Silent engine swap on a contract flag** -- `--pcre2` once ran through the Python regex
   engine via a bare `except`, returning WRONG results labeled as normal ones.
4. **A green test never seen red** -- the #737 test would have passed with the fix deleted;
   3 of 4 control arms in another PR survived the revert. Red-arm everything.
5. **Two green PRs, one red main** -- #835 + #836 collided semantically with no textual
   conflict; CI never evaluated the union, and the lost release followed. Rebase onto the real
   target and run the union.
6. **The lockfile blindfold** -- `mcp` 2.0.0 removed a submodule tg imports; every dev env and
   CI leg was immune via `uv.lock`, so the ONE fresh-resolving component (the PyPI smoke venv)
   failed on two consecutive releases while being treated as the problem. Cap majors on
   anything imported by submodule path; when only the fresh-resolve canary fails, suspect the
   declared constraint.
7. **A dependency UPPER-cap silently downgrading everything** -- an upper bound with no release
   for a new Python resolves the whole package DOWN with no error. A fresh install yielding a
   stale tg means a transitive cap (typer/click/pydantic), not `requires-python`.
8. **`uv lock` churn** -- a raw `uv lock` rewrites ~280 unrelated lines; hand-splice the new
   `[[package]]` block and verify with `uv export ... --locked`.
9. **Re-stamping line numbers** -- five maintenance passes re-stamped skill anchors by hand and
   every one shipped wrong numbers; a purpose-built checker found 92 stale anchors where the
   human audit found ~15. Cite symbols; make the number regenerable.
10. **Believing a zero without a positive control** -- a language-registry probe read
    "5 registered, 0 foundational" against a 2-commit-stale checkout (truth: 10/5); an expired
    CI log grepped to empty read as "no failures". Every probe must be shown able to return
    non-zero.
11. **Fixing the instance, not the class** -- one doc's false "still broken" claim was
    corrected while a grep found the identical claim in three more skills (and beat a 12-agent
    parallel audit doing it). When a dogfood falsifies one claim, grep everything for it.
12. **A checker that cannot run, or cries wolf** -- the skill-anchor auditor first CRASHED on a
    dangling symlink (silence read as clean), then drowned 3 real findings in 762 false ones.
    Confirm a detector RUNS and DISCRIMINATES before trusting its silence.
13. **Wall-clock assertions in CI** -- a 2-release flaky died only when the test asserted the
    concurrency CONTRACT (Event handshakes) instead of thread overlap; a stubbed baseline
    measured exactly 0.0 (below Windows clock resolution) and silently turned a "relative"
    ratio into a hard 2-second bound.
14. **Reproducing a WSL-reported hang in WSL** -- a "whole-repo hang" was WSL /mnt/c 9p
    amplification, not a deadlock. Reproduce natively first.
15. **Killing a slow build or "stale" agent** -- a working build agent was killed twice on an
    mtime heuristic; releases legitimately queue 30-60 min under runner scarcity. Probe
    liveness properly; do not kill on staleness.
16. **Trusting a subagent's "tests pass"** -- worktree agents have no `.venv`; their green is a
    hypothesis until re-run in the real venv. Same bar for their TDD claims: no quoted failure
    message, no red arm.
17. **Regenerating a generated artifact as a "fix"** -- a rejected lockfile was regenerated
    (new failure, four different packages) when the real cause was a consumer missing the
    config the artifact was produced under. Reproduce the rejection as a controlled pair first.

---

## 10. What is honestly unsettled

Do not present these as solved, and do not silently re-litigate them either (check
`tensor-grep-failure-archaeology` and `docs/PAPER.md` before re-proposing):

- **GPU**: no crossover proven at any scale (historical worst ~30-35x slower at 5GB); the
  shipped kernel is a brute-force byte-compare, NOT PFAC; public CUDA-asset publishing is on a
  deliberate CEO hold (#169). Phase 0 (correctness, gated OFF) is shipped; promotion requires
  the pinned proof gate in `docs/CONTRACTS.md`.
- **LSP** remains experimental, not production-proven.
- **AST DSL parity** (native metavariable support, task #141) is demand-gated, not planned work.
- **`SafeBackendMixin` + fault-injection conformance gate** is the planned structural fix for
  the fail-closed contract's recurring violations; until it ships, the discipline is per-file.
- **The cold-text-search gap to raw rg** is accepted as parity-tier; the next lever, if ever,
  is a more native launcher/control plane, not more Python micro-tuning.
- **Known blast radius, not yet fixed at the time of writing**: `--stats --quiet` on an
  rg-passthrough route still prints rg's stats block (untested combination; reproduces
  wherever rg is installed). Verify current state before citing it.
- Several CLI emitters still TRAIL or omit their incompleteness disclosure (the rule is
  disclosure ABOVE the payload; three emitters are wired). See `AGENTS.md` "A Disclosure Must
  Precede The Data It Qualifies" for the measured list before assuming any command discloses.

---

## Appendix: the pre-push checklist (copy this)

```
[ ] Red arm seen: new tests FAIL on pre-fix code (isolated tree copy, not a PYTHONPATH swap)
[ ] All registration sites enumerated mechanically (4 command / 2 flag / 5th MCP, grep the sets)
[ ] uv run ruff check .
[ ] uv run ruff format --check --preview .          (whole repo, --preview mandatory)
[ ] uv run mypy src/tensor_grep
[ ] uv run pytest -q                                 (read the SKIP count, not just the pass count)
[ ] rustfmt --check on EVERY touched .rs file        (~/.cargo/bin/rustfmt.exe)
[ ] Hot path touched? -> the matching benchmarks/ script, vs the accepted baseline
[ ] Routing/flag/command touched? -> real-binary dogfood, not CliRunner
[ ] User-facing string changed? -> grep every test AND doc for the old string
[ ] Grep the WHOLE suite for assertions about any output SHAPE you changed (Form 10)
[ ] Rebased onto current origin/main and the union re-run
[ ] PR title matches release intent AND the diff matches the title
[ ] Merge gate: newest main ci.yml run COMPLETED, and any in-flight publish fully on PyPI
```
