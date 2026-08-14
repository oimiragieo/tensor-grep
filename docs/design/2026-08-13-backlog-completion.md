# Backlog-Completion Campaign - Design (2026-08-13)

> Phase 1 (requirements/design) artifact. Companion plan:
> `docs/plans/2026-08-13-backlog-completion-plan.md`. This document is the argument; the plan is
> the execution. Both are docs-only outputs of this session: no code was written, no tests run,
> no PR opened.
>
> ASCII-only by construction (A96: non-ASCII punctuation in governed docs defeats byte-exact
> edit-tool matches).

---

## 0. Executive summary, including the correction that changes the campaign shape

This campaign drives the 28-row canonical board (`docs/TASK_BOARD.md`, index `2026-08-12.1`, 17
unfinished) to an honest terminal, advanced, or premise-reverified state, and reconciles the index
to `2026-08-13.1`.

**The single most important finding of the design phase is a refuted premise in the input brief.**
The brief ranks "stranded security fixes (verified real, never shipped)" as the highest-priority
wave. Re-derived against live `origin/main` = `9738134c7772bd30e4cd51fba9aa7ebe2efcedfa`, that
claim is **FALSE for both items**: the H3 CWE-88 batch-shim fix and the H6 `CuDFBackend`
normalization are **both already on `origin/main`, with their tests**. Receipts in section 2.

Per A102 (input-brief facts are hypotheses; the builder verifies them against the tree and reports
any that fail verification rather than silently propagating them) and A75 (premise-check the
ready-to-build queue before dispatch), this is reported, not worked around. Wave W1 is therefore
retained at full scope but **re-shaped from "rebase, PR, merge two security fixes" to "publish the
verification receipt and retire the branch"**. This is not a silent scope reduction; it is the
correct disposition of work that is already done, and section 9 states explicitly what capacity it
frees and where that capacity goes.

Net effect on the campaign: two release-bearing PRs that the brief expected in W1 do not exist,
because the code is shipped. The two release-bearing PRs this campaign actually produces are W2
(the A101 probe flake) and W3 (RUST-REPLACE-SYMLINK).

---

## 1. Ground truth (verified live, 2026-08-13)

| Fact | Value | How derived |
|---|---|---|
| `origin/main` | `9738134c7772bd30e4cd51fba9aa7ebe2efcedfa` | `git rev-parse origin/main` after `git fetch origin` |
| Public product | v1.110.14 | brief; board `Live campaign snapshot` |
| Board index on main | `2026-08-12.1` | `git show origin/main:docs/TASK_BOARD.md` line 53 |
| Canonical rows | 28 | enumerated in section 3 |
| Unfinished rows | 17 | 1 READY + 6 BLOCKED + 5 CEO_GATED + 5 DEMAND_GATED |
| Open PRs | exactly 1: **#966** (draft, `test:`, MERGEABLE) | `gh pr list --state open --json number,title,isDraft,mergeable` |
| Local checkout | `audit/h6-cudf-backend`, dirty, pre-existing in-flight work | brief; preserved untouched |

**Delta from the brief worth recording:** the brief describes RUST-REPLACE-SYMLINK as needing its
reopen trigger satisfied. It is **already `READY` on `origin/main`** as of index `2026-08-12.1` -
the 2026-08-12 campaign performed that flip, carrying the CVE receipts in-body per A71. The
campaign therefore starts W3 at the design-council step, not at the disposition step.

---

## 2. Refuted premise: the "stranded security fixes" are shipped

The brief states that commits on `audit/h6-cudf-backend` are "verified real, never shipped". They
are not. Three independent receipts, each re-runnable:

### 2.1 `git cherry` - patch-id against live main

```
$ git cherry origin/main audit/h6-cudf-backend
+ f1e888c2276f78a7c5c7108157c65ae377d7f391
+ 928e9b270d2b21602a18708183eb2f748554d389
- d9e477b7a8a7b47b3357e7b732d67ef2631279ea
```

`-` means patch-id-equivalent upstream. `d9e477b` (H3) is **already on main by patch identity**.
The two `+` lines (H6) are patch-distinct, which is exactly the trap: patch-distinct is not
"absent". Section 2.3 shows the content shipped by another route.

### 2.2 H3 - the CWE-88 batch-shim removal is on main, with its tests

`git show origin/main:rust_core/src/python_sidecar.rs`:

- `fn command_for_executable(program: &OsStr) -> Command` at **`python_sidecar.rs:757`** returns
  a bare `Command::new(program)`. There is no `cmd /d /c` wrap.
- The 8-line comment at **`:758-765`** names the exact class the brief describes: "do NOT manually
  wrap a .cmd/.bat interpreter via `cmd /d /c <program> <args>` ... letting a `&`/`|`/`%` in a
  caller-supplied pattern inject an additional command ... (CWE-88 / BatBadBut CVE-2024-24576
  class)".
- Two tests exist on main: `command_for_executable_never_wraps_batch_shim_in_cmd` at
  **`:1496`** (asserts `program != "cmd"` for a `.bat` shim) and
  `command_for_executable_plain_program_untouched` at **`:1515`**.

The comment is written in the past tense of a landed audit fix ("H3 audit"), and the test is the
fix's own ratchet. There is nothing to rebase.

### 2.3 H6 - the `CuDFBackend` normalization is on main, with its test

`git show origin/main:src/tensor_grep/backends/cudf_backend.py`:

- **`:325-336`** carries the normalization and its rationale: "a GPU OOM / driver / regex fault
  escaped raw, so the per-file CPU-fallback retry (`except BackendExecutionError`, cli/main.py)
  never fired ... so any real failure becomes BackendExecutionError", implemented as
  `except BackendExecutionError: raise` followed by
  `raise BackendExecutionError(f"CuDFBackend failed: {exc}") from exc` at **`:333-336`**.
- `git show origin/main:tests/unit/test_cudf_backend.py` carries the docstring
  `"""H6 audit: engine failures MUST surface as BackendExecutionError so the per-file ..."""`
  at **`:699`**.

This corroborates the 2026-08-12 reconciliation audit
(`docs/audits/2026-08-12-stale-branch-reconciliation.md` section 1), which reached the identical
conclusion independently and was itself cross-verified by a ground-truth seat and the codex Sol
seat. Two independent audits **one day apart** (2026-08-12 and 2026-08-13) agree. The brief's claim
is the outlier. The one-day gap is stated exactly because an inflated interval would overstate the
independence of the corroboration: a day-old audit and today's re-derivation read a tree that barely
moved, so the strength here comes from the two audits using *different methods* (that one read the
branch ledger; this one reads `origin/main` content directly), not from elapsed time.

### 2.4 Why this matters beyond the two commits

This is the third recorded instance in this repo of the same failure: A75 (six of six
ready-to-build items already shipped), A98 (a spot-check census generalized from one file), and now
a dispatch brief asserting unshipped status for landed code. The generalizable rule the plan
enforces at every wave start is **Step 0**: re-derive the premise against `origin/main` before
touching anything, and record the receipt even when the premise holds.

The specific instrument trap here is worth naming: **`git cherry` reporting `+` is not evidence of
absence.** A squash-merge, a rewritten commit message, or a rebase all produce a different patch-id
for identical content. `+` means "patch-distinct", and the only way to convert that into a claim
about content is to read the content on main - which is what 2.3 does.

---

## 3. Closed world: all 28 rows

Every row below is enumerated from `git show origin/main:docs/TASK_BOARD.md` (index `2026-08-12.1`).
A45 requires the closed world be stated, not sampled.

### 3.1 Finished (11 rows - no campaign action beyond leaving them alone)

| Row | Status | Note |
|---|---|---|
| #22 | RETIRED | exit-code contract settled |
| F2 | RETIRED | anonymous-agent sentinel deliberately retained |
| #36 | SHIPPED | PR #903 |
| #37 | SHIPPED | PR #908 |
| #109 | SHIPPED | PR #605 |
| #859 | SHIPPED | PR #913 / #918 / #920 |
| F7 | SHIPPED | PR #950/#952/#955/#957, closure #963 |
| CPU-BACKEND | SHIPPED | PR #923/#925 - W3 extends this row's Rust half |
| REF-CALL-REGISTRY | SHIPPED | PR #915/#940 |
| F10 | RETIRED | MaxSim decisively negative |
| DD-004 | RETIRED | not empty-success |

### 3.2 Unfinished (17 rows) and this campaign's terminal disposition

| Row | Status on main | Wave | Terminal disposition this campaign |
|---|---|---|---|
| RUST-REPLACE-SYMLINK | READY | W3 | **SHIPPED** as *static no-follow guard + fail-closed metadata handling + directory-arm pin + a bounded event-gated residual-race **characterization pin** (window currently OPEN) + a named junction disposition*; the race itself carried to `RUST-REPLACE-TOCTOU` (design 5.4a). **`IN_FLIGHT` instead, if the PR merges and the release does not publish** (plan GATE-W8-1) |
| #89 | BLOCKED | W4, W6 | BLOCKED, advanced (Task 2A repair round, honest park if still FIX-FIRST) |
| #90 | BLOCKED | W4, W6 | BLOCKED, advanced (same program) |
| F5 | BLOCKED | W6 | BLOCKED, prerequisites restated |
| F6 | BLOCKED | W6 | BLOCKED (MIXED per A41 - both halves carried) |
| F8 | BLOCKED | W6 | BLOCKED, prerequisites restated |
| MCP-SURFACE | BLOCKED | W6 | BLOCKED behind Task 2C; contract version re-verified |
| #255 | DEMAND_GATED | W5 | DEMAND_GATED or bounded fix candidate (see 6.1) |
| DD-006 | DEMAND_GATED | W5 | DEMAND_GATED with a measurement, or RETIRED with a receipt |
| AST-DSL-PARITY | DEMAND_GATED | W5 | DEMAND_GATED, evidence refreshed |
| MCP-LEAN-DEFAULT | DEMAND_GATED | W5 | DEMAND_GATED, evidence refreshed, Task 2C fence held |
| CONTINUOUS-REFRESH | DEMAND_GATED | W5 | DEMAND_GATED, scoping-only trigger held |
| #48 | CEO_GATED | W7 | **Decision packet** - remains CEO_GATED |
| #72 | CEO_GATED | W7 | **Decision packet** - remains CEO_GATED |
| #77 | CEO_GATED | W7 | **Decision packet** - remains CEO_GATED |
| #131 | CEO_GATED | W7 | **Decision packet** - remains CEO_GATED |
| #169 | CEO_GATED | W7 | **Money stop** - pointer only, no packet, no spend |

### 3.3 The three strays

| Stray | Brief's claim | Verified status | Wave |
|---|---|---|---|
| H3 batch-shim (`d9e477b`) | stranded security fix | **SHIPPED** - `python_sidecar.rs:757-765`, tests `:1496`/`:1515` | W1 (receipt only) |
| H6 CuDF normalization (`f1e888c`+`928e9b2`) | stranded security fix | **SHIPPED** - `cudf_backend.py:325-336`, test `:699` | W1 (receipt only) |
| A101 probe flake | verified flaky 3x in 3 runs | **REAL** - `scripts/agent_readiness.py:781` `timeout_s=30`, no retry | W2 (fix) |

One of three strays is real. That ratio is itself the argument for Step 0.

---

## 4. W2 design: the A101 probe flake is a missing structural property, not a slow probe

### 4.1 The measured facts

`scripts/agent_readiness.py` on `origin/main`:

- `class Check(NamedTuple)` at **`:42-49`** has fields `name, command, description, timeout_s=60,
  validator, required=True, skip_error_patterns=()`. **There is no retry field.**
- The `public-version-powershell` check is constructed at **`:778-783`** with
  `command = ["powershell", "-NoProfile", "-Command", "tg --version"]` (built at `:770-774`) and
  `timeout_s=30`.
- `run_check` at **`:1102`** calls `subprocess.run(..., timeout=check.timeout_s)` **exactly once**
  at **`:1126-1136`**; `except subprocess.TimeoutExpired` at **`:1160`** sets
  `status = "failed"`, `message = f"timed out after {check.timeout_s}s"`. There is no loop, no
  attempt counter, and no retry.
- The job is `windows-agent-readiness` (`.github/workflows/ci.yml:178-182`, `runs-on:
  windows-latest`), which runs the shell probes; the Linux `agent-readiness` job at `:139` runs
  with `--no-shell-probes` and is unaffected.

### 4.2 Why the timeout is the wrong single lever, and what the contrast tells us

The flaking probe already passes `-NoProfile`. The contrast that A101 records - 30s timeout versus
`-NoProfile` passing in under 1s - is between **two different interpreters**:
`public-version-powershell` invokes `powershell` (Windows PowerShell 5.1), while the sibling
`public-version-pwsh-noprofile` at `:791-797` invokes `pwsh` (PowerShell 7). Windows PowerShell
5.1 cold-start on a fresh `windows-latest` runner is the plausible mechanism.

Per A102 and the "briefing a MECHANISM asserts a HYPOTHESIS" law, **the plan does not require this
mechanism to be true.** The fix is designed to be correct under either mechanism:

1. **Raise the budget** where a cold interpreter start can plausibly exceed it (30s -> 90s). This
   addresses a genuinely slow start.
2. **Add a bounded retry on timeout only** (`retry_on_timeout`, default 0, set to 1 for the shell
   version probes). This addresses a transient stall of any cause.

Neither lever alone is sufficient, and the second is the structural one. A101's own wording is
"fix the probe (raise the timeout / make it tolerant)"; the plan does both and pins both.

### 4.3 Two design rules this fix must not violate

- **`retry_on_timeout` defaults to 0 and is opted into per check.** A blanket retry would convert
  every genuine hang into a doubled wall-clock failure and would mask real regressions. The
  bidirectional oracle for this is an explicit no-retry control test (W2A step 7), plus a mutation
  control: changing the loop to retry unconditionally must turn that control RED.
- **Only timeouts are retried, never non-zero exits or validator failures.** A probe that returns
  the wrong version must fail on the first attempt. The plan's implementation retries strictly
  inside `except subprocess.TimeoutExpired`.

### 4.4 Recurrence recording

A101's second half - "record the recurrence count beside the flake so the next session sees 3x
instead of treating it as a fresh one-off" - is satisfied by the `attempts` field the fix adds to
`run_check`'s result dict, plus a BACKLOG dated entry naming the 3x recurrence. Without the
`attempts` field a retried pass is indistinguishable from a first-attempt pass, and the next
session loses the signal that the probe is marginal.

---

## 5. W3 design: RUST-REPLACE-SYMLINK, scoped to the arm that actually follows

### 5.1 Threat model, derived from the code rather than from the CVE list

The 2026-08-12 receipts establish the **class** (GNU sed `-i --follow-symlinks` TOCTOU
CVE-2026-5958; uutils coreutils GHSA-239g-2685-54x3 / CVE-2026-35356/35359; Capgo CLI
CVE-2026-56236; rsync GHSA-4h9m-w5ff-j735). They do not establish which of tg's arms is exposed.
That was derived directly:

**Directory mode is already safe.** `walk_directory_entries` (fn signature at `backend_cpu.rs:493`;
the `WalkDir::new(path_obj)` call itself at **`:507`**, after the `#[cfg(test)]`
`force_walk_failure` injection block at `:494-505`) relies on walkdir's default
`follow_links(false)`, and both
`replace_directory_literal` (**`:519-543`**) and `replace_directory_regex` (**`:545-569`**) filter
with `if !entry.file_type().is_file() { continue; }`. For a symlink entry under `follow_links(false)`,
`file_type().is_file()` is false, so symlinked entries are skipped. This is a property worth
**pinning**, not changing - it is currently incidental (it depends on a walkdir default and on
`DirEntry::file_type` semantics), and an unpinned incidental safety property is one refactor from
becoming a vulnerability.

**The explicit-file argument arm follows symlinks.** `replace_in_place` at **`:440-447`** branches
on `path_obj.is_file()` at **`:452`**. `Path::is_file()` **follows** symlinks. It then calls
`replace_file_literal` / `replace_file_regex`, each of which opens with
`OpenOptions::new().read(true).write(true).open(path)` at **`:590`** and **`:647`** - which also
follows - and then mmaps and rewrites. So
`replace_in_place(pat, rep, "<attacker-planted-symlink>", ...)` writes **through** the link to a
destination the caller never named.

### 5.2 Honest reachability scoping

`replace_in_place` is `pub fn` on the CPU backend. A repo-wide search for callers outside its own
module found matches only in `rust_core/src/backend_cpu.rs` and `rust_core/tests/test_replace.rs`.
**It is not wired to any `tg` CLI command today.** This is stated plainly because it changes the
severity honestly: this is **library-surface hardening ahead of exposure**, not a live exploitable
CLI path. Shipping it as anything else would be the "a public number can be wrong in the direction
that costs you" failure in reverse.

That scoping is also the argument for doing it now rather than later: the board's own trigger text
frames this as "a deliberate close", and closing a no-follow default before a consumer exists costs
one PR. Closing it after a consumer exists costs a compatibility decision plus a CVE.

### 5.3 The compatibility decision, made explicitly

The board's trigger names the fork: "no-follow-by-default or a documented boundary". The design
chooses **no-follow-by-default, fail-closed, with the resolved-target escape stated in the error**:

- `replace_in_place` calls `std::fs::symlink_metadata(path_obj)` (which does **not** follow) before
  the `is_file()` branch. If `file_type().is_symlink()`, it returns `Err` naming the path and
  telling the caller to pass the resolved target explicitly.
- **The metadata call itself fails CLOSED.** If `symlink_metadata` returns `Err`, the guard returns
  a contextual `Err` naming the path and the stat error - it does **not** fall through to the
  rewrite. An `if let Ok(meta) = ...` shape (the obvious first draft, and what an earlier revision
  of this design specified) fails **open**: on any stat error - a permission denial, a
  `ERROR_CANT_ACCESS_FILE` on a reparse point whose filter driver refuses the query, an EIO - the
  guard silently does nothing and the follow behaviour returns. A guard whose failure mode is
  "behave exactly as if the guard were absent" is the false-green shape this repo catalogues:
  nothing observable distinguishes "checked and safe" from "could not check". The one behaviour
  this trades away is `replace_in_place` on a path that does not exist now returning the guard's
  stat error rather than the pre-existing `is_file()`-branch error; that contract is kept explicit
  by a dedicated compatibility test rather than left to chance.
- No new public flag. Adding a `--follow-symlinks` opt-in would replicate exactly the sed surface
  that earned CVE-2026-5958, and there is no consumer asking for it (section 5.2). YAGNI, and the
  research receipts argue against it directly.
- Backward-compatibility impact has **no in-repo consumer** by measurement (5.2). Per **A40** that
  is not the same as "no consumer": `CpuBackend` is exported in an `rlib`, and an out-of-tree caller
  is invisible to an in-repository census. The signature is therefore retained unchanged and only
  the behaviour on a symlinked argument narrows. It is still a `fix:` PR, and the CHANGELOG must say
  the behavior changed.

### 5.4 What this design deliberately does NOT do

- **It does not attempt to close the TOCTOU window.** A `symlink_metadata` check followed by an
  `open` is inherently racy: the path can be swapped between the two calls. Closing that properly
  requires `O_NOFOLLOW` (POSIX) plus `FILE_FLAG_OPEN_REPARSE_POINT` (Windows) at the open site, or
  handle-based reopen. The plan therefore ships the guard **and** records the residual TOCTOU
  window as a follow-up board row, in the same PR that ships the guard, rather than claiming the
  class is closed. Claiming a TOCTOU fix that is itself TOCTOU-racy would be a false green of
  exactly the kind AGENTS.md catalogues. The bounded first step is still worth shipping: it turns
  a 100%-reliable static-symlink overwrite into a race an attacker must win.
- **It does not touch the directory arm's behavior** - only pins it.

### 5.4a The board Trigger requires more than the static guard: swap-gating and junctions

The board row's own Trigger text frames this row as a *deliberate close*, and A38/A48 say plainly
that a leaf check and a swap-resistant writer are **separate security contracts**. A PR that shipped
only the static `symlink_metadata` guard and then wrote SHIPPED against that Trigger would be
claiming more than it built. Two additions close that gap honestly:

1. **A bounded event-gated leaf-swap CHARACTERIZATION PIN.** Not a RED - the distinction is
   load-bearing and an earlier revision of this document got it backwards. A RED arm asserts the
   behaviour the fix produces and fails until the fix lands. This arm asserts the behaviour that
   **survives** the fix: the window between the guard's `symlink_metadata` and the writer's
   `OpenOptions::open` is still OPEN, and a leaf swapped inside it still redirects the write. It
   therefore PASSES on the shipped bytes, and is expected to **FLIP to failing** when
   `RUST-REPLACE-TOCTOU` closes the race - at which point the plan's instruction is to invert it, not
   delete it. That flip is the reopen signal for that row, and the plan records it as such.

   `backend_cpu.rs` already carries the seam this needs: a `#[cfg(test)]` `ReplaceFaultInjection`
   struct at **`:284-291`** (`force_walk_failure`, `force_literal_child_failure`,
   `force_regex_child_failure`) reached through the PUBLIC `replace_in_place` by in-file unit tests
   at **`:1316-1345`**. The pin extends that seam rather than inventing a parallel harness, and it is
   genuinely event-gated: two capacity-1 channels form a handshake between the writer
   (on a spawned thread, signalling once it is past the guard and blocking for acknowledgment) and
   the second actor (the test's main thread, which unlinks the leaf and links it to an
   attacker-owned target). A `send` on a capacity-1 channel never blocks (each channel carries one
   message), and every blocking `recv` carries a 2-second `Duration` bound that panics
   `CANNOT_MEASURE` on expiry - so no operation in the handshake is unbounded and a deadlocked
   handshake can never be read as a verdict about the window. **A Boolean flag plus a synchronous
   helper call is not an event gate** - nothing in that shape can distinguish "the swap landed
   inside the window" from "the swap landed at all", which is precisely what the pin exists to
   assert; it is bounded by that handshake rather than by a timing loop, because an unbounded race
   test on a shared CI box is a flake generator, not evidence.
2. **Windows junctions are a MUST-ANSWER, not an open question.** A directory junction is a reparse
   point for which `Path::is_symlink()` and `symlink_metadata().file_type().is_symlink()` do **not**
   behave the way POSIX intuition predicts. W3A's council must return one of exactly three outcomes,
   and W3B's acceptance criteria is gated on which one: **(a) REFUSE** - the guard also refuses a
   junction, with a test; **(b) DOCUMENT** - junctions are out of scope, stated in the code comment,
   the threat model, and the board Trigger; or **(c) FOLD** - junction handling moves into
   `RUST-REPLACE-TOCTOU` and that row's Trigger names it. An unanswered question is not one of the
   three.

**Consequence for the board text.** The row ships with a Trigger that claims exactly what was built:
a **static no-follow guard + fail-closed metadata handling + a directory-arm pin + a residual-race
characterization pin (window currently OPEN)**, with the race itself and the junction disposition
both named and carried into `RUST-REPLACE-TOCTOU`. The parenthetical "window currently OPEN" is not
decoration - it is the difference between "we tested the race" and "we closed the race", and it is
the sentence a future reader will use to decide whether `RUST-REPLACE-TOCTOU` is still real work.
Section 3.2's disposition line is worded to match.

**And the status token itself is conditional.** The row reads `SHIPPED` only once the `fix:` PR has
merged **and** PyPI serves the version. If the merge lands and the release does not publish, the row
reads `IN_FLIGHT` with the merged SHA and run ID in its Trigger - never `SHIPPED` with a
"release pending" parenthetical, which is `SHIPPED` to every tracker, grep and bucket count that
keys on the token. The plan's GATE-W8-1 carries the failure-class diagnosis (push-race, which
self-heals and must not be rerun, versus a cancelled publish tail, which is rerun by run ID) and the
PyPI membership check that licenses the flip.

### 5.5 Verification constraints

- `rust_core/**` means **no local `cargo` of any kind** (CPU-SAFE / W3 shared-box ban, A12). The
  only compile-and-test oracle is GitHub Actions CI (A87: static review cannot typecheck; two prior
  Rust PRs passed multiple static reviews and then failed the first real CI run on genuine compile
  errors). `rustfmt --check` is the sole locally permitted Rust command.
- Security class -> **mandatory adversarial audit before merge** (A3), on the exact pushed bytes
  after CI is green (A81: implementer receipts are not a SHIP verdict).
- The hostile fixture must be proven to **bite** (A88): the test asserts the symlink actually
  exists as a symlink before exercising the guard. A dogfood or test PASS on a fixture that never
  applied proves nothing.
- **But an unprivileged Windows runner cannot create a symlink at all, and that is CANNOT_MEASURE,
  not RED.** `std::os::windows::fs::symlink_file(...).unwrap()` on a runner without
  `SeCreateSymbolicLinkPrivilege` (or Developer Mode) panics inside the *fixture*, producing a
  failure whose reason is "the environment refused to create a link" while the CI log reads as a
  failing security test. That is precisely the wrong-RED-reason class A61 forbids. The repo has
  already settled this shape: `rust_core/tests/test_ast_rewrite.rs:1778-1784` (and its batch twin at
  `:1910-1916`) handle `symlink_file` returning `Err` by printing
  `"skipping <test name>: cannot create a Windows symlink in this environment: {err}"` and
  returning. **W3B follows that established pattern rather than inventing a third convention**, with
  one addition the existing sites do not need: because this is the decisive arm of a security fix,
  the CANNOT_MEASURE outcome must not be allowed to stand in for a verdict. The decisive RED/GREEN
  is routed to a CI node known to be capable of creating symlinks (Linux, where
  `std::os::unix::fs::symlink` needs no privilege), and the **only** acceptable raw CI failure for
  this test is the pinned behavioral assertion message. A run in which every node printed the
  skip line is a run that measured nothing, and the risk table records that explicitly.

---

## 6. W5 design: demand-gated rows

### 6.1 #255 - the one demand-gated row with a live defect underneath

#255 is different in kind from the other four: the board records a **live many-pattern dedup
over-count bug in `rust_core`, guarded rather than fixed**. The gate is on the *investment*
(a parity experiment plus possible native/compression work), not on whether a defect exists.

The brief states user demand now exists. That is a claim about the world, not about the tree, so
the plan treats it as the reopen trigger being **argued**, and requires the wave to produce the
bounded parity experiment design before any code. The 2026-08-12 receipt's own reopen condition is
concrete and should be re-checked as the eligibility test: "tg `scan` ruleset growth past ~100
anchors or a named user with a 100+-pattern workload". If neither is satisfied, the honest outcome
is DEMAND_GATED with the demand claim recorded and unmet - not a build.

### 6.2 DD-006 - a bounded local measurement, or a retirement

The receipt's honest null is that no dev-tool-daemon-specific demand signal exists, and tg's
bounded-pre-auth-read plus socket-timeout posture already matches the mitigation pattern. The wave
runs a **bounded** local concurrency measurement (bounded because the box is shared - A12), and the
outcome is binary: evidence of a real bound failure -> a board row with a reproduction; no evidence
-> **RETIRED with a receipt**. Board rule 4 makes a documented retirement worth as much as a fix.

### 6.3 The three research rows

AST-DSL-PARITY, MCP-LEAN-DEFAULT, CONTINUOUS-REFRESH already carry 2026-08-12 evidence in their
trigger text. The wave adds a **delta** pass only: fresh 2026-08 arXiv findings on top of the
existing receipts, per the brief's instruction to reuse rather than re-derive. Each ends in a
build/don't-build packet. MCP-LEAN-DEFAULT stays fenced behind Task 2C regardless of how strong the
industry-direction evidence gets - the MCP-SURFACE ladder is a sequencing constraint, not an
evidence question, and `_TG_MCP_SERVER_CONTRACT_VERSION` must be re-verified as `1.7.0` before any
row text asserts it.

---

## 7. W7 design: CEO-gated rows complete as packets, never as flips

The five CEO-gated rows have standing packets in
`docs/audits/2026-08-06-ceo-gated-recommendation-packets.md`. Each ends "STATUS REMAINS
CEO_GATED". This campaign's contribution is a **delta**, not a rewrite:

| Row | Standing recommendation (2026-08-06) | 2026-08-13 delta |
|---|---|---|
| #48 | Accept shipped hybrid front door; no rewrite without a measured P0 + named consumer | Reversible implementation proposal for the architectural remainder; still a CEO scoping call |
| #72 | HOLD any public multiplier (7.5x / 6.4x conflict); zero-spend pinned-harness benchmark only | 2026-08-12 receipts show competitors publishing token-reduction numbers; strengthens the *case*, changes nothing about the *gate* |
| #77 | Keep ledger local opt-in advisory; no auth/CI blocking gate | Thinktank-recommended option + reversible implementation proposal |
| #131 | Optional experimental NVIDIA asset, CPU default, no speed claim; proof/spend under #169 | Unchanged; still downstream of #169 |
| #169 | FINANCIAL_HOLD - pointer only, mandatory money stop | **No packet, no recommendation, no spend.** The only money stop. |

**The binding rule:** a CEO-gated row is "complete" for this campaign when its packet is written
and surfaced. It is never completed by a status flip. #169 is not even given a recommendation -
it is a pointer, because a recommendation on a money stop reads as a nudge.

---

## 8. Architecture of the campaign itself

### 8.1 Isolation

- The dirty `audit/h6-cudf-backend` checkout is **preserved untouched**. Never `git add .` or
  `git add -A`; stage explicit paths only. `git stash` is forbidden - worktrees share `.git`'s
  stash refs and a 2026-08-02 revert took a different agent's stash.
- All campaign work happens in fresh worktrees off `origin/main`, one per wave item - **except where
  one item's output is an input to the next**, which is a real dependency and must be carried
  explicitly rather than assumed.
- **The W3A -> W3B carry, stated as a mechanism.** W3A produces
  `docs/design/2026-08-13-replace-in-place-symlink-threat-model.md` and commits it on its own branch.
  W3B's PR must contain that document, because the code comment and the commit message both cite it
  by path and a `fix:` PR that references a file absent from its own diff (and, at that moment,
  absent from `main`) ships a dangling citation. A worktree cut from `origin/main` would **not**
  carry it. W3B therefore branches from **W3A's branch**, not from `origin/main`, and the plan
  verifies the carry with `git log --oneline` before writing any Rust. That also means W3A does not
  need its own PR - its commit rides into main as the first commit of W3B's PR.
- **The stale-checkout warning.** The orchestrating session's local checkout is at board index
  `2026-08-08.1` while `origin/main` is at `2026-08-12.1`. Several Round-1 council seats reached
  false conclusions ("laws stop at A76", "0 READY rows") by grepping that stale tree. Every premise
  in this campaign is re-derived from `origin/main` via `git show origin/main:<path>` or a fresh
  worktree - **never** from the working tree, and never from a `git grep` in the main checkout.

### 8.2 Release class, decided per PR before it is opened

Per `[tool.semantic_release]` in `pyproject.toml` (angular default parser: patch tags are `fix` and
`perf` only):

| Wave item | PR title prefix | Publishes? |
|---|---|---|
| W1 receipt | `docs:` - **its own PR, opened by W1B** | no |
| W2 probe fix | `fix:` | **yes - patch** |
| W3 symlink guard | `fix:` | **yes - patch** |
| W4 Task 2A rounds | `test:` (draft #966) | no |
| W5-W7 packets | `docs:` | no |
| W8 board reconcile | `docs:` | no |

Two release-bearing PRs total. They merge **one per publish**, with the merge gate being "no runs
in flight on main" queried by run ID - never `tag == PyPI`, which cannot distinguish released from
not-started from died.

**Every row in that table needs a landing mechanism, and W1's was missing.** A `docs:` release class
answers "does this publish?"; it does not answer "does this reach `main`?" - and this repo's own law
is that committed is not shipped. W1's receipt is the campaign's evidence that its highest-priority
input premise was false; a receipt that exists only on an abandoned worktree branch is exactly the
artifact whose absence causes the next session to re-derive the same refutation from scratch.
**W1 therefore opens its own non-releasing `docs:` PR** (rather than the alternative of
cherry-picking W1A's commit into W8's worktree), for three reasons: it lands early instead of behind
two release-bearing merges; a `docs:`-only PR is cheap CI; and the campaign's WIP cap has room while
W2 is still in TDD. W1A's acceptance criteria is correspondingly "the receipt is present on
`origin/main` at campaign end", not "the receipt is committed".

### 8.3 Verification routing

| Change surface | Local | CI |
|---|---|---|
| Python (`src/`, `scripts/`, `tests/unit/`) | targeted `uv run pytest <path>` then the repo-wide-minus-e2e gate below | full matrix is authoritative |
| Rust (`rust_core/**`) | `rustfmt --check` **only** | **the sole compile+test oracle** (A87) |
| `tests/e2e/**` | forbidden (self-compiles Rust) | CI only |

**The local pytest gate is scoped, and this is a correctness requirement rather than a
convenience.** A bare `uv run pytest -q` collects `tests/e2e/test_routing_parity.py`, which shells
out to `cargo run` - so the "whole-repo gate" would itself violate the no-local-cargo ban that sits
two rows above it in the same table. An earlier revision of these artifacts stated both rules and
they contradicted each other; a plan that contains its own violation gets resolved at 2am by
whichever rule the runner remembers. The gate is therefore defined once, in exactly this form:

```
uv run pytest -q --ignore=tests/e2e/test_routing_parity.py
```

The full matrix including `tests/e2e/**` runs **in CI only**, and CI is the authoritative gate for
it. `--ignore` is used rather than a marker deselect because it excludes the file at *collection*
time: a deselect still imports the module, and import-time work is exactly where a self-compiling
test would spend the CPU this ban exists to protect.

### 8.4 Concurrency and WIP

Max 3 open PRs at any time (A1 caps build dispatch at >5 undrained; this campaign self-imposes 3
because two of its PRs are release-bearing and serialize on publish). Draft #966 counts toward the
cap. Never two pytest processes concurrently on the shared box.

### 8.5 `AGENTS.md` is NOT edited by this campaign

Stated explicitly because the omission is load-bearing and an earlier revision of this document
implied the opposite. The A-laws this campaign leans on - through **A102** - **already exist on
`origin/main`**; verified by enumeration, not by grep-count (`git show origin/main:AGENTS.md`
enumerates A1 through A102). Every citation in these two artifacts is a citation to a law that is
already published, so there is nothing to add.

The one permitted exception is a **stale-citation sweep**: if W8's board reconcile turns up an
`AGENTS.md` line whose `file:line` anchor no longer resolves, that single line is corrected in W8's
`docs:` PR with the correction named in the PR body. Adding a *new* law is out of scope - a law
minted at the end of a campaign by the campaign's own author has not survived a second session, and
this repo's laws are receipts rather than resolutions.

This also disposes of a Round-1 council claim that "the laws stop at A76". That reading came from
the stale local checkout (8.1); A79-A102 are real and are cited here deliberately.

### 8.6 Step 0 is a live-state gate, not a reading exercise

Every item's Step 0 re-derives its premise from `origin/main`. For any item that will **edit the
board**, Step 0 additionally runs:

```
git show origin/main:docs/TASK_BOARD.md | grep -n "Canonical status index" -A 4
git show origin/main:docs/TASK_BOARD.md | grep -n "<the row being edited>" -A 3
```

and records the index string and the row's `Status:` verbatim. The asserted premise for this
campaign is index `2026-08-12.1` with `RUST-REPLACE-SYMLINK` at `Status: READY` (READY count = 1).
**If either disagrees, the item blocks** and the campaign shape is re-derived before any edit -
because a board edit computed against a superseded index is how a concurrent campaign's row gets
silently reverted. This is the cheapest possible check and it is the one that was skipped the last
two times a board edit went wrong.

---

## 9. Capacity freed by the refuted premise, and where it goes

The brief budgeted W1 for two rebase-verify-PR-CI-audit-merge cycles on release-bearing security
fixes: realistically two full publish cycles plus two adversarial audits. That work does not exist.

Explicitly reallocated, so nothing is quietly dropped:

1. **W3 gets the freed adversarial-audit budget.** RUST-REPLACE-SYMLINK is the campaign's only
   genuine security-class build, it is CI-only verified, and A87's receipt says Rust PRs commonly
   fail their first real CI run. It absorbs a design council (which W1's already-shipped fixes did
   not need) plus the mandatory A3 gate.
2. **W5's #255 gets a real eligibility check** rather than a rubber-stamp reopen, since there is
   time to derive whether the reopen trigger is actually satisfied.
3. **The freed publish slots go unused.** Two release-bearing PRs (W2, W3) is the whole releasing
   surface. The campaign does not invent work to fill a budget.

---

## 10. Risks

| Risk | Why it bites here | Mitigation |
|---|---|---|
| **CI-only Rust verification latency** | W3 cannot be compiled locally; each repair round costs a full CI cycle, and A87 says the first CI run commonly finds real compile errors | Budget 2 CI rounds for W3 before escalating; `rustfmt --check` locally to remove the cheapest class; do not treat a static SHIP as clearance |
| **Push-race** | The `Semantic Release` job runs ~6 min; merging anything onto main during that window rejects the in-flight push | Merge W2, wait for the `chore(release)` commit + PyPI, then merge W3. Never merge during a run in flight, including `docs:` PRs |
| **WIP cap** | Two release-bearing PRs plus draft #966 plus a docs PR exceeds a comfortable drain rate | Hard cap of 3 open PRs; W8's docs PR opens only after W2 and W3 have merged |
| **The dirty checkout** | 21 modified + untracked entries of another session's in-flight work; a single `git add -A` destroys it | Explicit-path staging only; no `git stash`; all work in fresh worktrees; W1B's cleanup is PROPOSED, never executed |
| **Task 2A timebox** | The repair loop has previously consumed whole campaigns without reaching SHIP | Hard 2 rounds, live CI per round (A68); if still FIX-FIRST, park with receipts and leave the board BLOCKED |
| **A97 - an interrupted edit may have already applied** | This campaign edits `docs/TASK_BOARD.md`, which has been duplicated by blind retries before (see 8.5: `AGENTS.md` is deliberately NOT edited) | After any interrupted or ambiguous tool result, read the file back before retrying. Never re-apply blind |
| **A Windows runner that cannot create symlinks turns W3B's decisive arm into CANNOT_MEASURE** | `symlink_file` needs `SeCreateSymbolicLinkPrivilege`; an unprivileged runner makes the fixture panic, and the log reads as a failing security test rather than an unmeasurable one (5.5) | Follow the `test_ast_rewrite.rs:1778-1784` skip-with-reason pattern; route the decisive RED/GREEN to a known-capable node; treat an all-skip run as measuring nothing; the ONLY acceptable raw CI failure is the pinned behavioral assertion |
| **Windows junctions silently out of scope** | `is_symlink()` is false on a junction, so a guard that looks complete leaves a reparse-point follow vector open while the board reads SHIPPED (5.4a) | W3A's council question 3 is a MUST-ANSWER with exactly three permitted outcomes (refuse / document / fold into `RUST-REPLACE-TOCTOU`); W3B's acceptance criteria is gated on which one was returned |
| **The guard fails OPEN on a stat error** | An `if let Ok(meta)` shape restores the exact follow behaviour whenever `symlink_metadata` errors, and nothing observable distinguishes that from "checked and safe" (5.3) | Fail closed with a contextual `Err`; prove it with fault injection through the existing `#[cfg(test)] ReplaceFaultInjection` seam, in both directions |
| **A98 - spot-check census** | W6 restates six BLOCKED rows; generalizing one row's prerequisite to the others is the exact prior failure | Per-row command, per-row result. No row's disposition is inferred from a sibling's |
| **A96 - non-ASCII punctuation** | Board and AGENTS.md prose carries em dashes; byte-exact edits fail while the text looks identical | New prose is ASCII-only; edits to existing lines locate by line index with an assertion, not by quoting the line |
| **A merged-but-unpublished release is rounded up to SHIPPED** | The status token is what every tracker, grep and bucket count reads; a `SHIPPED (release pending ...)` row is SHIPPED to all of them, and the 2026-08-05 receipt is a `fix:` PR that merged and never published | The row takes `IN_FLIGHT` (parser-legal: literal `PR #<n>`, unchecked box) until PyPI membership on the exact version confirms the publish. Diagnose the failure class first - a push-race self-heals and must NOT be rerun; a cancelled publish tail IS rerun by run ID |
| **The new board row passes review but fails the parser** | `ROW_RE` matches the whole line including a literal em dash after `**ID**`, and the closed world is a literal set in a test file - a row added to the board alone fails with `closed-world population drift` | W8 Steps 4/4a/4b: the full line written out, the em-dash exception to A96 named, and `EXPECTED_IDS`/`DEMAND_IDS` re-derived from `origin/main` (not from the stale checkout, whose copy already disagrees) and edited in the same PR |
| **A green governance run from a test that never looked** | `-k task_board` matches the CHANGELOG-freshness test, not the row parser (`test_backlog_tracker_truth.py`) - two different files with adjacent-sounding names | Run the parser test **by path**, require a non-zero collected count, and treat the `-k` sweep as an addition rather than the check |
| **The premise-check finds more refutations mid-campaign** | Section 2 found one already; A75 found six in one pass | Every wave item starts with Step 0. A refuted item is re-dispositioned in the plan and reported, never silently skipped or silently narrowed |

---

## 11. What "completion" means for this campaign

Completion is **not** "17 rows closed". It is: every one of the 28 rows has an honest, receipted
terminal state, and the board index reads `2026-08-13.1`.

- **SHIPPED** requires a PR number **and** a merged SHA **and**, for release-bearing PRs, a
  published artifact. Committed is not shipped; merged is not released. A merged-but-unpublished row
  takes the board's **`IN_FLIGHT`** status - not `SHIPPED` with a qualifying parenthetical, because
  every consumer of that field keys on the token and reads the qualifier as prose.
- **BLOCKED** requires the exact prerequisite and the next-trigger text. No fake progress.
- **DEMAND_GATED** requires the reopen condition restated with the evidence that was checked
  against it - including when the evidence fails to satisfy it.
- **RETIRED** requires the reason. A documented retirement is worth as much as a fix.
- **CEO_GATED** completes as a **decision packet with a recommendation**. It never completes as a
  flip. #169 completes as a pointer with no recommendation at all.
- **Refuted premises** complete as receipts. W1 is the worked example: two items the brief called
  the highest-priority build work complete as a published verification receipt, because that is
  what honest completion looks like when the code is already shipped.

---

## 12. References

- `docs/TASK_BOARD.md` (canonical index `2026-08-12.1`, 28 rows)
- `docs/audits/2026-08-12-stale-branch-reconciliation.md` (sections 1-5: reconciliation, BLOCKED
  premise recheck, Task 2A rounds R0-R1, Sol round-1 F1-F6)
- `docs/audits/2026-08-12-research-receipts.md` (47 sources; Part A frontier, Part B per-row)
- `docs/audits/2026-08-06-ceo-gated-recommendation-packets.md` (the five standing packets)
- `docs/plans/2026-08-12-backlog-closeout-campaign.md` (rev 6; superseded by this campaign, its
  dispositions reused)
- `AGENTS.md` (A1-A20 campaign disciplines; A61-A69 RED/CI evidence laws; A70-A102 session laws;
  Backend Fail-Closed Contract; the ten verification-oracle forms)
