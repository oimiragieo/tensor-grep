# Backlog-Completion Campaign Implementation Plan (2026-08-13)

> **For agentic workers:** REQUIRED SUB-SKILLS: `superpowers:test-driven-development`,
> `superpowers:executing-plans`, `tensor-grep-change-control`, `verify-plan-against-code`.
> Work ONE ranked item per iteration. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Drive all 28 canonical board rows to an honest terminal, advanced, or premise-reverified
state, shipping the two genuinely buildable fixes (the A101 probe flake and RUST-REPLACE-SYMLINK),
and reconcile `docs/TASK_BOARD.md` to index `2026-08-13.1`.

**Architecture:** Eight waves. W1 publishes the receipt that two "stranded security fixes" are
already on main (premise refuted - see design section 2). W2 and W3 are the campaign's only two
release-bearing builds. W4 timeboxes the Task 2A repair loop. W5-W7 produce research dispositions
and CEO decision packets without flipping any gate. W8 reconciles the board. Every wave item begins
with Step 0, a premise re-derivation against `origin/main`.

**Tech Stack:** Python 3.11+ (`uv`, `ruff`, `mypy`, `pytest`), Rust 1.96.0 pinned
(`rust_core`, PyO3/maturin - **CI-compiled only**), GitHub Actions, `gh` CLI, `tg` 1.110.14.

**Spec:** `docs/design/2026-08-13-backlog-completion.md`

> **ASCII exception, deliberate - do not "fix" it.** This file is ASCII-only except for a fixed,
> enumerated set of non-ASCII punctuation on nine lines (census re-derived 2026-08-14 against the
> committed bytes): **7 em dashes** - the ROW_RE separator example in this note itself, the W3A
> "council amendments ... plan amendment A1" heading, W3A amendment bullets 1-3, the W8
> RUST-REPLACE-TOCTOU board row, and the "literal em dash" explanation in W8's three-property
> list - **2 section signs** in the W3A amendment-1 sentence ("5/8"), and **1 right arrow** in
> W3A amendment 4 ("FLIPS Ok->Err"). `tests/unit/test_backlog_tracker_truth.py`'s `ROW_RE`
> requires a literal em-dash-space separator after `**ID**` - `— ` - so an ASCII sweep over that
> row would ship a line the parser rejects as `malformed or multiline canonical row`. Verify with
> `LC_ALL=C grep -cP '[^\x00-\x7F]' <this file>` -> expect **10**, on exactly those nine lines. The
> design artifact is at **0**. (This note's earlier text said "exactly three em dashes / expect 3";
> that was true when written and the count grew as later council rounds folded the TOCTOU row and
> amendment bullets in - the note above is the re-derived census, not a re-stamp.)

## Detail level

**Every wave, W1 through W8, carries concrete numbered steps.** There are no open expansion markers
anywhere in this plan.

An earlier revision marked W4-W8 **EXPAND AT WAVE START**, reasoning that their content depended on
verdicts and measurements that did not exist yet. Four of eight Round-1 council seats read that
marker as a placeholder - which is the correct reading of it, because a marker that says "write the
steps later" is indistinguishable from "the steps are not written". The dependency was real; the
response to it was wrong. A step whose *content* depends on a future verdict can still be written
now as a step whose *procedure* is fixed: run this exact command, compare against this exact
expected value, and take branch A or branch B.

So the waves are expanded from state that exists today - the Sol F1-F6 ledger in
`docs/audits/2026-08-12-stale-branch-reconciliation.md`, the 2026-08-12 research receipts, and the
2026-08-06 CEO packets - and every genuinely verdict-dependent point is instead a **named wave-start
re-derivation gate** with three mandatory parts:

1. an exact command to run,
2. a pass/fail trigger stated as a concrete comparison, and
3. a re-approval rule saying what happens on FAIL (which is never "improvise": it is always either a
   named alternate branch of the step, or STOP and re-disposition).

Each gate is named (`GATE-W4-1`, `GATE-W5A-1`, ...) so a reviewer can check that every branch of
every gate is accounted for, and so a wave-start agent can report against it. The distinction that
matters: a gate says *what to do when reality disagrees with the plan*; an expansion marker says
*the plan is not finished*. Only the first is a plan.

---

## Global Constraints

Copied verbatim from the spec and AGENTS.md. Every task's requirements implicitly include these.

1. **$0 spend.** No #169 spend, no paid infrastructure, no public release decisions.
2. **No local Rust compilation, ever.** `cargo build/test/check/clippy`, `rustc`, `maturin` are
   BANNED on this shared desktop (CPU-SAFE, A12). Any item touching `rust_core/**` routes its ONLY
   compile-and-test verification through GitHub Actions CI. `rustfmt --check` is the sole permitted
   local Rust command. `tests/e2e/test_routing_parity.py` is banned locally (it runs `cargo run`) -
   and because pytest would otherwise **collect** it, constraint 5's local gate is scoped to exclude
   it by path. Constraints 2 and 5 are consistent by construction; see the note under 5.
3. **Never modify the pre-existing dirty checkout.** Branch `audit/h6-cudf-backend` carries 21
   modified + untracked entries of another session's work. Never `git add .` / `git add -A`. Stage
   explicit paths only. **`git stash` is FORBIDDEN** (worktrees share `.git` stash refs). Revert with
   `git checkout -- <file>` or a patch file.
4. **All work in fresh worktrees off `origin/main`** = `9738134c7772bd30e4cd51fba9aa7ebe2efcedfa`.
5. **Repo gates.** The three lint/type gates take whole-repo `.` (a subpath is not a substitute).
   The pytest gate is **repo-wide minus the one cargo-invoking e2e module**, in exactly this form:
   - `uv run ruff check .`
   - `uv run ruff format --check --preview .`
   - `uv run mypy src/tensor_grep`
   - `uv run pytest -q --ignore=tests/e2e/test_routing_parity.py`

   **Why the pytest gate is scoped, and why that is not a weakening.** A bare `uv run pytest -q`
   *collects* `tests/e2e/test_routing_parity.py`, which shells out to `cargo run` - so running the
   unscoped gate would violate constraint 2 in the same breath as satisfying constraint 5. Those two
   constraints contradicted each other in an earlier revision of this plan; this is the
   reconciliation, and it resolves in favour of the CPU-SAFE ban because that ban protects a shared
   machine while the local gate is only ever a fast pre-filter. `--ignore=<path>` (not `-k`, not a
   marker deselect) because `--ignore` drops the file at **collection** time, before its module-level
   imports run - and import time is exactly where a self-compiling module would spend the CPU.
   **The full matrix, `tests/e2e/**` included, runs in CI, and CI is the authoritative gate.** A
   green local gate is never merge clearance; see constraint 6.
6. **Release class:** `fix:`/`feat:`/`perf:` publish; `docs:`/`test:`/`chore:`/`ci:`/`build:` do not;
   `refactor:` passes the title gate but does NOT publish (angular default parser). Release-bearing
   PRs merge **one per publish**. The merge gate is "no runs in flight on main", queried **by run
   ID** - never `tag == PyPI`.
7. **Security-class PRs require an independent adversarial audit before merge** (A3). A build
   agent's self-gate is a hypothesis, not clearance (A18/A81).
8. **WIP cap: 3 open PRs maximum**, draft #966 included.
9. **Board rows are parser-validated**: only `Status:` / `PR:` / `Trigger:` keys under
   `## Canonical status index`. Free-form bullets there are illegal (A71).
10. **Anti-hang protocol (A6):** every test run wrapped in a shell `timeout` with per-test
    `--timeout`. Never two pytest processes concurrently on this box.
11. **ASCII-only in new doc prose** (A96). Edit existing governed lines by line index with an
    assertion, never by quoting the line.
12. **A97:** after any interrupted or ambiguous tool result, READ the target file back before
    retrying. Never re-apply blind.
13. **Step 0 is mandatory** for every item: re-derive the premise against `origin/main` and record
    the receipt, even when the premise holds.
14. **Step 0 LIVE-STATE GATE, mandatory before ANY edit to `docs/TASK_BOARD.md`** (W6, W8, and the
    `RUST-REPLACE-TOCTOU` row insertion):

    ```bash
    git show origin/main:docs/TASK_BOARD.md | grep -n "Canonical status index" -A 4
    git show origin/main:docs/TASK_BOARD.md | grep -n "RUST-REPLACE-SYMLINK" -A 3
    ```

    Record the index string and the row's `Status:` **verbatim**. Asserted premise: index
    `2026-08-12.1`; `RUST-REPLACE-SYMLINK` `Status: READY`. **On any mismatch, BLOCK the edit** and
    re-derive the campaign shape - a board edit computed against a superseded index silently reverts
    a concurrent campaign's row.
15. **The orchestrating session's local checkout is STALE** (board index `2026-08-08.1`, four days
    behind `origin/main`'s `2026-08-12.1`). Round-1 council seats reached two false conclusions from
    it - "the A-laws stop at A76" (they run to A102) and "0 READY rows / DEMAND_GATED" (there is 1
    READY row: `RUST-REPLACE-SYMLINK`). **Re-derive every premise from `origin/main`** via
    `git show origin/main:<path>` or inside a fresh worktree. Never `grep` the main working tree,
    and never trust a citation this plan inherited without re-running its command.

---

## File Structure

| File | Responsibility | Wave |
|---|---|---|
| `docs/audits/2026-08-13-stranded-work-premise-recheck.md` | CREATE. Receipt that H3/H6 are shipped | W1 |
| `scripts/agent_readiness.py` | MODIFY. `Check.retry_on_timeout`; retry loop in `run_check`; shell-probe budgets | W2 |
| `tests/unit/test_agent_readiness_script.py` | MODIFY. Retry behaviour, no-retry control, probe-budget population | W2 |
| `rust_core/src/backend_cpu.rs` | MODIFY. `symlink_metadata` guard in `replace_in_place`; unit tests | W3 |
| `rust_core/tests/test_replace.rs` | MODIFY. Integration arms for symlink refusal + directory-mode pin | W3 |
| `docs/design/2026-08-13-replace-in-place-symlink-threat-model.md` | CREATE. Council-gated threat model | W3 |
| `docs/audits/2026-08-13-demand-gated-dispositions.md` | CREATE. Five demand-row packets | W5 |
| `docs/audits/2026-08-13-ceo-gated-packets.md` | CREATE. Five CEO packets with 2026-08-13 deltas | W7 |
| `docs/TASK_BOARD.md` | MODIFY. Index -> `2026-08-13.1`; row text per wave | W8 |
| `docs/BACKLOG.md` | MODIFY. Dated entries | W8 |
| `docs/SESSION_HANDOFF.md` | MODIFY. Campaign state refresh | W8 |

---

# WAVE 1 - Premise verification and stranded-work closure

> **WARNING - the checkout you are reading this in is STALE.** Its board index is `2026-08-08.1`;
> `origin/main` is at `2026-08-12.1`. Two Round-1 council findings were pure artifacts of grepping
> this tree instead of `origin/main`. **Every premise in this wave - and in every other wave - is
> re-derived from `origin/main`** (`git show origin/main:<path>`, or a fresh worktree), never from
> the working tree. If a command in this plan reads a path without an `origin/main:` prefix and it
> is not inside a fresh worktree, that is a bug in the plan: fix it before running it.

**Rank: 1.** Runs first because every downstream wave's capacity allocation depends on its outcome.

## Item W1A: Publish the premise-recheck receipt

**Rank:** 1 of 8
**Requirement refs:** brief section A (stranded security fixes); design section 2; A75, A102
**Dependency:** none
**Release class:** `docs:` - does NOT publish

**Files:**
- Create: `docs/audits/2026-08-13-stranded-work-premise-recheck.md`

**Allowed paths:** `docs/audits/**` only.
**Protected paths:** everything else. Specifically: no `src/**`, no `rust_core/**`, no
`scripts/**`, and NOTHING in the dirty main checkout.

**Interfaces:**
- Consumes: nothing.
- Produces: the receipt path, cited by W8's board reconcile and BACKLOG entry.

**Bidirectional oracle:** This item's oracle is the pair of `git` commands themselves, and both
arms are already recorded. The RED arm - what the check would show if the premise were TRUE and the
fixes really were stranded - is: `git cherry origin/main audit/h6-cudf-backend` would print `+` for
`d9e477b`, and `git show origin/main:rust_core/src/python_sidecar.rs | grep -c "H3 audit"` would
print `0`. Both were run and both show the opposite. The receipt records both the observed value
and the value that would have supported the brief.

- [ ] **Step 1: Create the worktree**

```bash
git fetch origin
git worktree add .claude/worktrees/w1a-premise-receipt -b docs/2026-08-13-premise-recheck origin/main
cd .claude/worktrees/w1a-premise-receipt
git rev-parse HEAD   # must print 9738134c7772bd30e4cd51fba9aa7ebe2efcedfa
```

- [ ] **Step 2: Re-run the three receipts and capture raw output**

```bash
git cherry origin/main audit/h6-cudf-backend
git show origin/main:rust_core/src/python_sidecar.rs | grep -n "fn command_for_executable" -A 12
git show origin/main:rust_core/src/python_sidecar.rs | grep -n "command_for_executable_never_wraps_batch_shim_in_cmd"
git show origin/main:src/tensor_grep/backends/cudf_backend.py | grep -n "BackendExecutionError"
git show origin/main:tests/unit/test_cudf_backend.py | grep -n "H6 audit"
```

Expected: `- d9e477b...`; `command_for_executable` at `:757` returning `Command::new(program)`; the
test at `:1496`; `cudf_backend.py` normalization at `:325-336`; `test_cudf_backend.py:699`.

**If any of these disagrees with the design doc, STOP** and re-open the premise. A disagreement
means main moved, and the campaign shape changes again.

- [ ] **Step 3: Write the receipt document**

Content requirements (each is a section, none may be omitted):
1. `origin/main` SHA and the date the receipts were re-derived.
2. The `git cherry` output verbatim, with the explicit note that **`+` means patch-distinct, NOT
   absent** - a squash-merge or rebase changes patch-id for identical content.
3. Per-commit disposition table: `d9e477b` SHIPPED (patch-id equivalent), `f1e888c` + `928e9b2`
   SHIPPED-BY-CONTENT with the `file:line` receipts.
4. The RED arm: what these commands would print if the fixes were genuinely stranded.
5. Cross-reference to `docs/audits/2026-08-12-stale-branch-reconciliation.md` section 1, which
   reached the same conclusion independently **one day earlier** (2026-08-12 vs today's 2026-08-13).
   State the interval exactly. The corroboration's strength is that the two audits used *different
   methods* - that one read the branch ledger, this one reads `origin/main` content directly - not
   that time passed between them; an inflated interval would dress up weak independence as strong.
6. A "what this cost / what it saved" line: two release-bearing PR cycles were budgeted and are not
   needed; design section 9 records the reallocation.

- [ ] **Step 4: Gate**

```bash
uv run ruff format --check --preview .
```

Expected: PASS. (No Python changed; this catches markdown fence formatting, which ruff formats
repo-wide.)

- [ ] **Step 5: Commit**

```bash
git add docs/audits/2026-08-13-stranded-work-premise-recheck.md
git diff --cached --name-status   # MUST be exactly one file
git commit -F - <<'EOF'
docs: receipt that H3/H6 audit fixes are already on origin/main

The 2026-08-13 campaign brief ranked two commits on audit/h6-cudf-backend
as stranded security fixes. Both are shipped:

- d9e477b (H3, CWE-88 batch-shim) is patch-id equivalent upstream
  (git cherry prints "-"); python_sidecar.rs:757-765 plus tests at
  :1496 and :1515.
- f1e888c + 928e9b2 (H6, CuDFBackend BackendExecutionError normalization)
  are patch-distinct but the content is on main at
  cudf_backend.py:325-336, test_cudf_backend.py:699.

git cherry "+" means patch-distinct, not absent. Corroborates the
independent 2026-08-12 reconciliation audit.
EOF
```

**Acceptance criteria:**
- all five sub-receipts reproduce; no file outside `docs/audits/` staged;
- **the receipt is present on `origin/main` at campaign end** - verified by
  `git show origin/main:docs/audits/2026-08-13-stranded-work-premise-recheck.md | head -5` returning
  content, not by the local commit existing. Committed is not shipped: a receipt that lives only on
  a worktree branch is why the next session re-derives the same refutation from scratch. The landing
  mechanism is W1B Step 6's own `docs:` PR (design 8.2 chose this over cherry-picking into W8, so
  the receipt lands early rather than behind two release-bearing merges).

**Rollback:** `git worktree remove --force .claude/worktrees/w1a-premise-receipt` and delete the
branch. Nothing on main changed.

---

## Item W1B: Branch and worktree retirement - PROPOSED only

**Rank:** 2 of 8
**Requirement refs:** design section 8.1; `docs/audits/2026-08-12-stale-branch-reconciliation.md`
section 1 (cleanup PROPOSED, not executed)
**Dependency:** W1A committed
**Release class:** `docs:` - does NOT publish. **W1B opens W1's own PR** (Step 6), carrying both
W1A's receipt and this section. Design 8.2 chose this over cherry-picking into W8 so the receipt
lands early rather than behind two release-bearing merges.

**Files:**
- Modify: `docs/audits/2026-08-13-stranded-work-premise-recheck.md` (append a "Proposed cleanup"
  section)

**Allowed paths:** `docs/audits/2026-08-13-stranded-work-premise-recheck.md` only.
**Protected paths:** the entire working tree of the main checkout. **No deletion is executed by
this plan.**

**Bidirectional oracle:** the A30 ancestor proof. For each candidate branch, `git merge-base
--is-ancestor <branch> origin/main` exits 0 (landed) or 1 (not landed). The RED arm is exit 1: any
branch that is NOT an ancestor stays on the keep list with its reason. A98 applies - **each branch
gets its own command and its own recorded result; no branch's disposition is inferred from a
sibling's.**

- [ ] **Step 1: Enumerate, do not sample**

```bash
git worktree list
git branch --list | cat
```

- [ ] **Step 2: Per-branch ancestor check, one command each, with exit codes discriminated**

```bash
for b in $(git for-each-ref --format='%(refname:short)' refs/heads/); do
  git merge-base --is-ancestor "$b" origin/main
  case $? in
    0) echo "$b LANDED" ;;
    1) echo "$b NOT-LANDED" ;;
    *) echo "$b CANNOT-MEASURE (git exit $?)" ;;
  esac
done
```

Two things this form fixes, both of which an earlier revision got wrong:

- **One loop variable.** The prior draft iterated over one name and echoed `$b`, so every line
  reported whichever `$b` happened to be set - or an empty string. A branch table is worthless if the
  branch column does not track the branch tested.
- **`&&`/`||` collapses three outcomes into two.** `git merge-base --is-ancestor` exits 0 for
  ancestor and 1 for not-ancestor, but any *error* - a bad ref, a missing object, a corrupt repo -
  also exits non-zero and would be recorded as **NOT-LANDED**, i.e. as "keep this branch". That is
  the false-negative direction in a deletion-proposal table: a git failure must read as
  CANNOT-MEASURE, never as a measured verdict. The `case` on `$?` keeps all three states distinct.

- [ ] **Step 3: Record the table in the receipt doc**

Columns: branch, ancestor-of-main (yes/no), uncommitted-content-in-its-worktree (yes/no/na),
disposition (PROPOSED-DELETE / KEEP + reason / **CANNOT-MEASURE + the git exit code**). The
CANNOT-MEASURE disposition is never merged into KEEP: they look the same on the shelf and mean
opposite things about what was learned. The uncommitted column is load-bearing: the
2026-08-02 sweep nearly deleted 519 uncommitted lines behind an "ANCESTOR of main" verdict.
`git merge-base --is-ancestor` says nothing about a worktree's uncommitted files.

- [ ] **Step 4: State the non-execution explicitly**

The section must end with a literal sentence: *"No branch, worktree, or file listed above was
deleted by this campaign. Execution requires an explicit operator acknowledgement."*

- [ ] **Step 5: Commit (amend into W1A's commit - ONLY because nothing has been pushed yet)**

```bash
git log --oneline origin/docs/2026-08-13-premise-recheck 2>/dev/null   # MUST print nothing
git add docs/audits/2026-08-13-stranded-work-premise-recheck.md
git commit --amend --no-edit
```

**The condition on the amend, stated because it is easy to lose:** `--amend` rewrites history and is
safe **only while the branch has never been pushed**. The `git log` line above is the check - it must
print nothing, i.e. no remote-tracking ref exists yet. If W1A's branch has already been pushed (for
example because this step is being re-run after Step 6), **do not amend**: make a second, ordinary
commit. A force-push to a branch with an open PR discards review state and, in a repo where sibling
agents may have fetched the ref, rewrites history other work is anchored to.

- [ ] **Step 6: Push and open the receipt's own `docs:` PR**

```bash
git push -u origin docs/2026-08-13-premise-recheck
gh pr create --title "docs: receipt that H3/H6 audit fixes are already on origin/main" --body-file -
gh pr checks <PR#> --watch
```

`docs:` does not publish (angular default parser), so this PR is non-releasing and does not consume
a publish slot - but it **does** count against the WIP cap of 3. Merge it in a green gap, before W2's
release-bearing PR is ready, so the cap stays clear.

- [ ] **Step 7: Verify the receipt actually landed**

```bash
git fetch origin
git show origin/main:docs/audits/2026-08-13-stranded-work-premise-recheck.md | head -5
```

Expected: the document's header. An empty result or a `fatal: path ... does not exist` means the
receipt did **not** land, regardless of what the local log says.

**Acceptance criteria:** every branch has its own recorded command result, with CANNOT-MEASURE kept
distinct from NOT-LANDED; the non-execution sentence is present verbatim; nothing deleted; the
receipt is readable via `git show origin/main:` (Step 7), not merely committed.
**Rollback:** before push, revert the amend. After push, `git revert` the merge commit on main;
nothing outside the receipt changed either way.

---

# WAVE 2 - A101 probe flake: structural fix

## Item W2A: Bounded timeout retry plus raised shell-probe budgets

**Rank:** 3 of 8
**Requirement refs:** brief section B; AGENTS.md **A101**; design section 4
**Dependency:** W1A (capacity confirmation only, not technical)
**Release class:** **`fix:` - PUBLISHES a patch release.** Merges one-per-publish.

**Files:**
- Modify: `scripts/agent_readiness.py` (`Check` NamedTuple at `:42-49`; `run_check` at `:1102`;
  shell probe construction at `:770-812`)
- Test: `tests/unit/test_agent_readiness_script.py`

**Allowed paths:** `scripts/agent_readiness.py`, `tests/unit/test_agent_readiness_script.py`.
**Protected paths:** `src/**`, `rust_core/**`, `.github/workflows/**`, `docs/**`. The CI workflow
is NOT touched - the fix lives entirely in the probe definition and runner.

**Interfaces:**
- Consumes: `module.Check`, `module.run_check(check, *, repo_root, expected_version)`,
  `module.build_check_plan(*, repo_root, expected_version, include_shell_probes, include_wsl_probe,
  only_shell_probes=False)` - all loaded via the existing `_load_script_module()` helper at
  `tests/unit/test_agent_readiness_script.py:12`.
- Produces:
  - `Check.retry_on_timeout: int = 0` - a new NamedTuple field, **appended last** so existing
    positional construction is unaffected.
  - `run_check(...)` result dict gains `"attempts": int` (1 when no retry occurred).

**Bidirectional oracle (stated before any code is written):**
- **RED arm, pre-fix:** a probe whose first `subprocess.run` raises `subprocess.TimeoutExpired` and
  whose second call would succeed currently returns `status == "failed"` with
  `message == "timed out after 30s"`, because `run_check` calls `subprocess.run` exactly once at
  `:1126` and the `except subprocess.TimeoutExpired` handler at `:1160` sets `failed` with no retry.
  The test therefore fails with `AssertionError: assert 'failed' == 'passed'`. This is a
  **behavioral** RED with an exact pinned reason (A61) - not an ImportError, not a TypeError, not a
  setup crash.
- **GREEN arm, post-fix:** `status == "passed"`, `attempts == 2`.
- **Control arm (must stay RED-able):** a check with `retry_on_timeout=0` that times out must
  remain `failed` with `attempts == 1`. Mutation control: change the loop bound to retry
  unconditionally and this control must go RED. If it stays green, the control is not discriminating
  and the fix is unproven.

- [ ] **Step 1: Create the worktree**

```bash
git fetch origin
git worktree add .claude/worktrees/w2a-probe-retry -b fix/a101-readiness-probe-retry origin/main
cd .claude/worktrees/w2a-probe-retry
git rev-parse HEAD   # 9738134c7772bd30e4cd51fba9aa7ebe2efcedfa
```

- [ ] **Step 2: Step 0 premise check - confirm the defect is still live**

```bash
git show origin/main:scripts/agent_readiness.py | sed -n '42,50p'
git show origin/main:scripts/agent_readiness.py | sed -n '776,786p'
git show origin/main:scripts/agent_readiness.py | grep -c "retry"
```

Expected: `Check` has no retry field; `public-version-powershell` has `timeout_s=30`; the retry
grep prints `0`. **If the grep prints non-zero, STOP** - someone shipped a retry and this item's
premise is refuted; re-disposition it the way W1A dispositioned the stranded fixes.

- [ ] **Step 3: Write the failing test (retry behaviour)**

Append to `tests/unit/test_agent_readiness_script.py`:

```python
def test_run_check_retries_a_timed_out_probe_before_failing(monkeypatch, tmp_path) -> None:
    """A101: the public-version-powershell probe flaked 3x in 3 runs at a 30s timeout.

    A single transient timeout must not fail the readiness gate when a retry succeeds.
    PRE-FIX this fails with AssertionError: assert 'failed' == 'passed', because run_check
    calls subprocess.run exactly once and its TimeoutExpired handler has no retry.
    """
    module = _load_script_module()
    calls = {"n": 0}

    def fake_run(command, **kwargs):
        calls["n"] += 1
        if calls["n"] == 1:
            raise subprocess.TimeoutExpired(cmd=command, timeout=kwargs.get("timeout", 0))
        return subprocess.CompletedProcess(
            args=command, returncode=0, stdout="tensor-grep 1.110.14\n", stderr=""
        )

    monkeypatch.setattr(module, "_command_available", lambda command: True)
    monkeypatch.setattr(module.subprocess, "run", fake_run)

    check = module.Check(
        name="probe-under-test",
        command=["powershell", "-NoProfile", "-Command", "tg --version"],
        description="retry probe",
        timeout_s=90,
        retry_on_timeout=1,
    )
    result = module.run_check(check, repo_root=tmp_path, expected_version="1.110.14")

    assert result["status"] == "passed", result
    assert result["attempts"] == 2, result
    assert calls["n"] == 2
```

- [ ] **Step 4: Run it and confirm the RED reason**

```bash
timeout 120 uv run --no-sync python -m pytest \
  tests/unit/test_agent_readiness_script.py::test_run_check_retries_a_timed_out_probe_before_failing \
  -q -rA --timeout=15
```

Expected: **FAIL**. The first failure will be
`TypeError: Check.__new__() got an unexpected keyword argument 'retry_on_timeout'` because the
field does not exist yet. **That is a construction crash, not the behavioral RED.** Per A61,
proceed to Step 5 to add the field ONLY, then re-run to observe the real behavioral RED before
implementing the loop.

- [ ] **Step 5: Add the field only (no retry logic yet)**

In `scripts/agent_readiness.py`, `class Check(NamedTuple)`, append as the LAST field:

```python
class Check(NamedTuple):
    name: str
    command: list[str]
    description: str
    timeout_s: int = 60
    validator: Validator | None = None
    required: bool = True
    skip_error_patterns: tuple[str, ...] = ()
    # A101: opt-in bounded retry for a TIMED-OUT probe only. Never retries a non-zero
    # exit or a validator failure -- a wrong version must fail on the first attempt.
    # Default 0 so a blanket retry can never mask a real regression.
    retry_on_timeout: int = 0
```

- [ ] **Step 6: Re-run and confirm the BEHAVIORAL red**

```bash
timeout 120 uv run --no-sync python -m pytest \
  tests/unit/test_agent_readiness_script.py::test_run_check_retries_a_timed_out_probe_before_failing \
  -q -rA --timeout=15
```

Expected: **FAIL with `AssertionError: assert 'failed' == 'passed'`**. This is the pinned RED reason
from the oracle. If the failure message is anything else, the test is measuring the wrong thing -
fix the test before the code.

- [ ] **Step 7: Implement the bounded retry loop**

In `run_check` (`scripts/agent_readiness.py:1102`), replace the single-shot
`subprocess.run` / `except subprocess.TimeoutExpired` structure so the subprocess call sits in a
bounded loop. The retry is inside the `TimeoutExpired` handler ONLY:

```python
    stdout = ""
    stderr = ""
    returncode = 0
    attempts = 0
    max_attempts = 1 + max(0, check.retry_on_timeout)
    try:
        if check.command:
            env = dict(os.environ)
            env.setdefault("PYTHONUTF8", "1")
            last_timeout: subprocess.TimeoutExpired | None = None
            for _ in range(max_attempts):
                attempts += 1
                try:
                    completed = subprocess.run(
                        check.command,
                        cwd=repo_root,
                        env=env,
                        text=True,
                        encoding="utf-8",
                        errors="replace",
                        capture_output=True,
                        timeout=check.timeout_s,
                        check=False,
                    )
                except subprocess.TimeoutExpired as exc:
                    # A101: retry a TIMEOUT only. A non-zero exit or a validator failure
                    # falls through untouched below and fails on the first attempt.
                    last_timeout = exc
                    continue
                last_timeout = None
                break
            if last_timeout is not None:
                raise last_timeout
            stdout = completed.stdout
            ...
```

The rest of the existing body (the `returncode != 0` branch with `skip_error_patterns`, the
`ReadinessError` raise, the validator call) is unchanged. Add `"attempts": attempts` to **every**
returned dict in `run_check`. Scope note, stated to prevent a builder inventing either: the
`_command_available` early return gets the literal `"attempts": 0` - it precedes the `try` where
`attempts` is defined, so the variable is not in scope there - while the `skip_error_patterns`
early return (which follows the `try`) gets the variable.

- [ ] **Step 8: Run to GREEN**

```bash
timeout 120 uv run --no-sync python -m pytest \
  tests/unit/test_agent_readiness_script.py::test_run_check_retries_a_timed_out_probe_before_failing \
  -q -rA --timeout=15
```

Expected: **PASS**.

- [ ] **Step 9: Write the no-retry control**

```python
def test_run_check_does_not_retry_when_retry_on_timeout_is_zero(monkeypatch, tmp_path) -> None:
    """Control arm for the A101 retry: the retry must be OPT-IN and bounded.

    A blanket retry would double the wall-clock of every genuine hang and mask real
    regressions. Mutation control: make the loop retry unconditionally and this test
    must go RED. If it stays green, the retry is not actually bounded.
    """
    module = _load_script_module()
    calls = {"n": 0}

    def always_timeout(command, **kwargs):
        calls["n"] += 1
        raise subprocess.TimeoutExpired(cmd=command, timeout=kwargs.get("timeout", 0))

    monkeypatch.setattr(module, "_command_available", lambda command: True)
    monkeypatch.setattr(module.subprocess, "run", always_timeout)

    check = module.Check(
        name="no-retry-control",
        command=["powershell", "-NoProfile", "-Command", "tg --version"],
        description="no-retry control",
        timeout_s=5,
    )
    result = module.run_check(check, repo_root=tmp_path, expected_version="1.110.14")

    assert result["status"] == "failed", result
    assert result["attempts"] == 1, result
    assert calls["n"] == 1
```

- [ ] **Step 10: Run the control, then run the MUTATION control**

```bash
timeout 120 uv run --no-sync python -m pytest \
  tests/unit/test_agent_readiness_script.py::test_run_check_does_not_retry_when_retry_on_timeout_is_zero \
  -q -rA --timeout=15
```

Expected: **PASS**.

Then temporarily change `max_attempts = 1 + max(0, check.retry_on_timeout)` to
`max_attempts = 2` and re-run the same command. Expected: **FAIL** with
`AssertionError: assert 2 == 1`. **Revert the mutation immediately** with
`git checkout -- scripts/agent_readiness.py` is WRONG here (it would discard Step 7 too) - instead
re-edit the single line back to `1 + max(0, check.retry_on_timeout)` and re-run to confirm PASS.
Record both observations; a control that cannot be made to fail is not a control.

- [ ] **Step 10a: Test the `attempts: 0` early-return path (do not ship an untested claim)**

Step 7 requires `"attempts": attempts` on **every** returned dict, including the
`_command_available` early return where the subprocess is never launched and the value is `0`. That
`0` is a claim - "we never attempted this" - and an untested claim in a field whose whole purpose is
to make a retried pass distinguishable from a first-attempt pass is exactly the field that quietly
reads `1` forever and nobody notices. Either this test exists, or `attempts` is dropped from that
path and the plan stops claiming it; the first is cheap, so:

```python
def test_run_check_reports_zero_attempts_when_the_command_is_unavailable(
    monkeypatch, tmp_path
) -> None:
    """The early return runs NO subprocess, so attempts must be 0, not 1.

    attempts exists so a retried pass stays distinguishable from a first-attempt pass. A path
    that never launched the command must be distinguishable from both -- otherwise the field
    silently means "at least one attempt" and A101's recurrence signal is lost.
    """
    module = _load_script_module()

    def must_not_run(command, **kwargs):  # pragma: no cover - the assertion is that it is unreached
        raise AssertionError(f"subprocess.run must not be called, got {command!r}")

    monkeypatch.setattr(module, "_command_available", lambda command: False)
    monkeypatch.setattr(module.subprocess, "run", must_not_run)

    check = module.Check(
        name="unavailable-probe",
        command=["definitely-not-on-path", "--version"],
        description="early-return probe",
        timeout_s=5,
    )
    result = module.run_check(check, repo_root=tmp_path, expected_version="1.110.14")

    assert result["attempts"] == 0, result
    assert result["status"] != "passed", result
```

Run it:

```bash
timeout 120 uv run --no-sync python -m pytest \
  tests/unit/test_agent_readiness_script.py::test_run_check_reports_zero_attempts_when_the_command_is_unavailable \
  -q -rA --timeout=15
```

Expected **pre-Step-7**: `KeyError: 'attempts'`. Expected **post-Step-7**: PASS. The stubbed
`subprocess.run` is itself the control - if the early return ever stops being early, the test fails
with the explicit `subprocess.run must not be called` message rather than silently passing.

**If `_command_available` is not a module-level function with this exact name** (Step 2's premise
check confirms it, since Step 3's retry test already monkeypatches it), adapt the patch target - do
not delete the test.

- [ ] **Step 11: Write the failing probe-budget test**

```python
def test_shell_version_probes_carry_a101_timeout_budget_and_retry(tmp_path) -> None:
    """A101: record the structural fix on the four shell version probes.

    PRE-FIX this fails with AssertionError on public-version-powershell timeout_s=30
    (expected >= 90). The probe flaked 3x in 3 runs; the third sighting is a
    structural-fix signal, not a rerun signal.
    """
    module = _load_script_module()
    checks = module.build_check_plan(
        repo_root=tmp_path,
        expected_version="1.110.14",
        include_shell_probes=True,
        include_wsl_probe=False,
        only_shell_probes=True,
    )
    by_name = {check.name: check for check in checks}
    expected = (
        "public-version-powershell",
        "public-version-cmd",
        "public-version-pwsh-noprofile",
        "public-version-git-bash",
    )
    assert set(expected) <= set(by_name), sorted(by_name)
    for name in expected:
        check = by_name[name]
        assert check.timeout_s >= 90, f"{name} timeout_s={check.timeout_s}, expected >= 90"
        assert check.retry_on_timeout >= 1, f"{name} retry_on_timeout={check.retry_on_timeout}"
```

- [ ] **Step 12: Run it and confirm the RED reason**

```bash
timeout 120 uv run --no-sync python -m pytest \
  tests/unit/test_agent_readiness_script.py::test_shell_version_probes_carry_a101_timeout_budget_and_retry \
  -q -rA --timeout=15
```

Expected: **FAIL with
`AssertionError: public-version-powershell timeout_s=30, expected >= 90`**.

- [ ] **Step 13: Raise the four shell-probe budgets**

In `build_check_plan`, in the `checks.extend([...])` block that constructs the four version probes
(`scripts/agent_readiness.py` around `:776-812`), change `timeout_s=30` to `timeout_s=90` and add
`retry_on_timeout=1` on each of `public-version-powershell`, `public-version-cmd`,
`public-version-pwsh-noprofile`, `public-version-git-bash`. Leave `required`,
`skip_error_patterns` and `validator` exactly as they are. Add one comment above the block:

```python
# A101 (2026-08-13): public-version-powershell flaked 3x in 3 windows-agent-readiness
# runs at timeout_s=30 while the pwsh -NoProfile sibling passed in <1s -- Windows
# PowerShell 5.1 cold start on a fresh runner is the leading hypothesis, but the fix
# is deliberately mechanism-independent: raise the budget AND allow one bounded
# timeout retry. The third sighting is a structural-fix signal, not a rerun signal.
```

- [ ] **Step 14: Run to GREEN**

```bash
timeout 180 uv run --no-sync python -m pytest tests/unit/test_agent_readiness_script.py \
  -q -rA --timeout=30
```

Expected: **PASS**, whole file, no regressions in the existing ~30 tests.

- [ ] **Step 15: Full local gate**

```bash
uv run ruff check .
uv run ruff format --check --preview .
uv run mypy src/tensor_grep
timeout 1800 uv run pytest -q --ignore=tests/e2e/test_routing_parity.py
```

Expected: all four PASS. `mypy` covers `src/tensor_grep` only; `scripts/` is checked by ruff.
The `--ignore` is **mandatory, not optional** (global constraint 5): an unscoped `pytest -q` collects
`tests/e2e/test_routing_parity.py`, which shells out to `cargo run` and violates the CPU-SAFE ban on
this shared box. `tests/e2e/**` is covered by CI, which is the authoritative gate; this local run is
a pre-filter and green here is not merge clearance.

- [ ] **Step 16: Commit**

```bash
git add scripts/agent_readiness.py tests/unit/test_agent_readiness_script.py
git diff --cached --name-status   # MUST be exactly these two files
git commit -F - <<'EOF'
fix: bounded timeout retry for readiness shell probes (A101)

public-version-powershell flaked 3x in 3 windows-agent-readiness runs at
timeout_s=30 while the pwsh -NoProfile sibling passed in under 1s. A101:
the third recurrence is a structural-fix signal, not a rerun signal.

- Check gains retry_on_timeout (default 0, appended last so positional
  construction is unaffected).
- run_check retries a TIMEOUT only, bounded by 1 + retry_on_timeout; a
  non-zero exit or validator failure still fails on the first attempt.
- run_check results carry "attempts" so a retried pass stays visible and
  the next session sees the probe is marginal.
- The four shell version probes move to timeout_s=90, retry_on_timeout=1.

Bidirectional oracle: the retry test fails pre-fix with
AssertionError: assert 'failed' == 'passed'; the no-retry control goes RED
under an unconditional-retry mutation.
EOF
```

- [ ] **Step 17: Push and open the PR**

```bash
git push -u origin fix/a101-readiness-probe-retry
gh pr create --title "fix: bounded timeout retry for readiness shell probes (A101)" --body-file -
```

The PR body must state: the A101 receipt, the 3x recurrence count, the exact RED message for each
test, the mutation-control result from Step 10, and that this is `fix:` and therefore
**release-bearing**.

- [ ] **Step 18: CI, then merge in a green gap**

```bash
gh pr checks <PR#> --watch
gh run list --workflow=ci.yml --branch=main --limit 5 --json databaseId,status,conclusion
gh run view <run-id> --json status,conclusion,jobs
```

Merge only when the newest main run is `completed` and no run is in flight. Then wait for the
`chore(release)` commit and PyPI before touching W3's merge.

**Acceptance criteria:**
- **Four** new tests pass (retry, no-retry control, `attempts == 0` early return, probe budgets);
  the whole `test_agent_readiness_script.py` file passes.
- Both RED messages observed and recorded verbatim (Steps 6 and 12).
- Mutation control observed RED and reverted (Step 10).
- Four-step local gate green; PR CI green; merged; a new patch version on PyPI.

**Commit boundary:** one commit, two files.
**Rollback:** `git revert <sha>` on main. The change is additive with a zero default, so a revert
restores exact prior behaviour; no data migration, no contract version.

---

# WAVE 3 - RUST-REPLACE-SYMLINK

## Item W3A: Design council and threat model

**Rank:** 4 of 8
**Requirement refs:** board row `RUST-REPLACE-SYMLINK` (Status READY on index `2026-08-12.1`);
`docs/audits/2026-08-12-research-receipts.md` "RUST-REPLACE-SYMLINK"; design section 5
**Dependency:** none technical; runs concurrently with W2's CI wait
**Release class:** `docs:` - carried into W3B's PR

**Files:**
- Create: `docs/design/2026-08-13-replace-in-place-symlink-threat-model.md`

**Allowed paths:** `docs/design/**`.
**Protected paths:** all code. **W3A writes no Rust.**

**Bidirectional oracle:** the council's job is to falsify the design, so the oracle is the
council's own discipline: **every claim cites `file:line`, and an uncited claim is discarded**
(A11, and the "review can INVENT a symbol" receipt). The council must be given the four specific
citations below and asked to check each against `origin/main` rather than accept them.

- [ ] **Step 1: Create the worktree**

```bash
git fetch origin
git worktree add .claude/worktrees/w3a-threat-model -b docs/2026-08-13-replace-symlink-threat-model origin/main
cd .claude/worktrees/w3a-threat-model
git rev-parse HEAD   # must print 9738134c7772bd30e4cd51fba9aa7ebe2efcedfa
```

The branch name is fixed here because **W3B branches from it** (W3B Step 1), not from `origin/main`.
An earlier revision of this plan had W3A writing a document with no worktree at all and W3B cutting
from `origin/main` - which would have produced a `fix:` PR whose code comment and commit message both
cite `docs/design/2026-08-13-replace-in-place-symlink-threat-model.md`, a path present in neither the
PR's diff nor `main`. A dangling citation in a shipped security comment is worse than no citation:
it reads as though the reasoning was published when it was not.

- [ ] **Step 2: Assemble the threat model from code, not from the CVE list**

Required content, each with its citation:
- Directory mode is **already safe**: `walk_directory_entries` - fn signature at
  `rust_core/src/backend_cpu.rs:493`, with the **`WalkDir::new(path_obj)` call itself at `:507`**
  (the `#[cfg(test)]` `force_walk_failure` injection block occupies `:494-505` in between) - relies
  on walkdir's default `follow_links(false)`, and both
  `replace_directory_literal` (`:519`) and `replace_directory_regex` (`:545`) filter
  `if !entry.file_type().is_file() { continue; }`.
- The explicit-file arm **does follow**: `replace_in_place` at `:440` branches on
  `path_obj.is_file()` at `:452` (`Path::is_file()` follows), then
  `OpenOptions::new().read(true).write(true).open(path)` at `:590` and `:647` (also follows) and
  mmap-rewrites.
- **Reachability, stated honestly:** `replace_in_place` is `pub fn` with no `tg` CLI caller; the
  only in-tree callers are `rust_core/src/backend_cpu.rs` and `rust_core/tests/test_replace.rs`.
  This is library-surface hardening ahead of exposure, not a live CLI exploit.
- The CVE class from `docs/audits/2026-08-12-research-receipts.md`: sed CVE-2026-5958, uutils
  GHSA-239g-2685-54x3 / CVE-2026-35356/35359, Capgo CVE-2026-56236, rsync GHSA-4h9m-w5ff-j735.
  **Reuse these; do not re-derive.**

- [ ] **Step 3: State the compatibility decision and its alternative**

The board trigger names the fork: "no-follow-by-default or a documented boundary". The document must
choose **no-follow-by-default, fail-closed**, and must state why the alternative was rejected: an
opt-in `--follow-symlinks` flag would replicate the exact sed surface that earned CVE-2026-5958,
and there is no consumer requesting it.

- [ ] **Step 4: State the residual TOCTOU window explicitly**

`symlink_metadata` followed by `open` is racy by construction. The document must say so, name the
proper fix (`O_NOFOLLOW` on POSIX plus `FILE_FLAG_OPEN_REPARSE_POINT` on Windows at the open site,
or handle-based reopen), and record it as a follow-up board row. **Do not claim the class is
closed.** The shipped guard converts a 100%-reliable static-symlink overwrite into a race an
attacker must win - that is the honest claim.

- [ ] **Step 5: Run the design council**

Four seats minimum, each reading the real code and citing `file:line`. Required questions:
1. Is the directory arm genuinely safe, or does `WalkDir`'s default differ from the assumption?
2. Does `symlink_metadata` before the `is_file()` branch cover **every** path into
   `replace_file_literal` and `replace_file_regex`, or is there a second entry point?
3. **MUST-ANSWER (gated downstream):** on Windows, is a **directory junction** - a reparse point on
   which `Path::is_symlink()` and `symlink_metadata().file_type().is_symlink()` do *not* behave the
   way POSIX intuition predicts - also a follow vector here?
4. Does the guard break any existing test in `rust_core/tests/test_replace.rs`?
5. Does the guard fail **closed** when `symlink_metadata` itself returns `Err`, and is the resulting
   change to the not-found error message acceptable?

A no-verdict seat is a **FAILED seat, not a blocker** (A10) - record it and synthesize from the
survivors. Question 3, however, cannot be left unanswered by seat attrition: see the gate below.

- [ ] **GATE-W3A-1: the junction question resolves to exactly one of three outcomes**

**Command / evidence:** the council's answer to question 3, plus a direct check of what the guard
does on a junction (a citation to Rust's `std::fs` reparse-point behaviour, or a CI-run probe if the
council cannot settle it from documentation).

**Trigger:** the answer must be exactly one of -
- **(a) REFUSE** - the guard also refuses a junction. W3B adds a Windows junction test, and the board
  Trigger says junctions are refused.
- **(b) DOCUMENT** - junctions are explicitly out of scope. The scope limit is written in the code
  comment, the threat model, **and** the board Trigger text.
- **(c) FOLD** - junction handling moves into `RUST-REPLACE-TOCTOU`, and that row's Trigger names it.

**Re-approval rule on FAIL (no answer, or an answer that is none of the three):** W3B does **not**
start. Re-run question 3 as a single-question dispatch to a fresh seat with the reparse-point
documentation attached. If it still cannot be settled, take **(c) FOLD** as the default - deferring a
question into a named row is honest, whereas shipping SHIPPED over an unanswered follow-vector
question is not. Record which outcome was taken; W3B's acceptance criteria checks for it by name.

- [ ] **Step 6: Fold must-fixes into the document, then commit**

```bash
git add docs/design/2026-08-13-replace-in-place-symlink-threat-model.md
git commit -m "docs: threat model for replace_in_place symlink policy (RUST-REPLACE-SYMLINK)"
git log --oneline -1   # note this SHA: W3B branches from it
```

Do **not** push or open a PR for this branch. Its commit rides into `main` as the first commit of
W3B's PR (W3B Step 1).

**Acceptance criteria:** four citations verified against `origin/main`; the compatibility decision
and its rejected alternative both stated; the residual TOCTOU stated; **GATE-W3A-1 resolved to a
named outcome (a), (b) or (c)**; council must-fixes folded BEFORE any code (A11).
**Rollback:** delete the doc and the worktree; no code touched.

---

## Item W3B: TDD build - refuse to follow a symlinked file target

**Rank:** 5 of 8
**Requirement refs:** board row `RUST-REPLACE-SYMLINK`; W3A's threat model; A38/A48 (leaf resolution
and swap resistance are separate contracts - satisfied here by an event-gated characterization pin,
not a RED); A87 (CI is the only Rust compile oracle); A88 (the fixture must bite)
**Dependency:** **W3A council complete AND W2 merged and published** (one-per-publish)
**Release class:** **`fix:` - PUBLISHES a patch release.**

### W3A council amendments to W3B (folded 2026-08-13 — plan amendment A1; where they disagree with
the steps below, the amendments win)

The W3A design council (3 rounds + a bounded toolchain probe; record in
`docs/design/2026-08-13-replace-in-place-symlink-threat-model.md` §5/§8) changed W3B's scope:

1. **GATE-W3A-1 = (a) REFUSE.** Probe on the pinned Rust 1.96.0: a Windows junction reports
   `is_symlink() == true` (`is_symlink_dir` true, `is_symlink_file` false), `Path::is_dir()`
   follows it, and `OpenOptions::open` opens through it. The pre-branch `symlink_metadata` +
   `is_symlink` guard therefore refuses junction roots AND junction children as a free
   consequence — the earlier "junctions are invisible to the guard" hypothesis (FOLD) is
   disproven on this toolchain.
2. **New test — Windows junction refusal** (`#[cfg(windows)]`, skip-with-reason
   `CANNOT_MEASURE`, fixture-bites assertion): `replace_in_place` on a junction root returns
   `Err` and the target directory's files are untouched. Junctions target directories only
   (`mklink /J` refuses file targets), so there is no file-target-junction case. The decisive
   RED/GREEN stays the Linux symlink arm; the Windows junction arm is an informational pin.
3. **New test — POSIX directory-symlink root refusal:** a directory-target symlink passed as
   `path` returns `Err`, target untouched. This closes the walkdir `follow_root_links(true)`
   hole the council round 1 falsified ("directory mode already safe" was true for children
   only, never the root).
4. **Missing-path pin FLIPS Ok→Err** (fail-closed stat): the existing
   `test_rust_replace_in_place_direct_file_nonexistent_path_is_currently_a_silent_no_op` is
   renamed to the new contract and asserts `Err`; broken-symlink paths also `Err` by the same
   mechanism (named, not a surprise).
5. **`RUST-REPLACE-TOCTOU` trigger text** (W8 Step 4) names BOTH the leaf-open mechanisms
   (`O_NOFOLLOW` on POSIX, `FILE_FLAG_OPEN_REPARSE_POINT` / identity-verified handle-reopen on
   Windows) AND walk-time reparse-point directory descent. The `RUST-REPLACE-SYMLINK` SHIPPED
   trigger claims exactly: "static no-follow guard (symlink_metadata, fail-closed) covering
   symlinks and junctions + root refusal + residual-race characterization pin".

**Files:**
- Modify: `rust_core/src/backend_cpu.rs` (`replace_in_place` at `:440`; `#[cfg(test)] mod tests`
  near `:1314`)
- Modify: `rust_core/tests/test_replace.rs`

**Allowed paths:** `rust_core/src/backend_cpu.rs`, `rust_core/tests/test_replace.rs`,
`docs/design/2026-08-13-replace-in-place-symlink-threat-model.md`.
**Protected paths:** `rust_core/src/main.rs`, `rust_core/src/python_sidecar.rs`, all of `src/**`,
all of `tests/e2e/**`, `.github/workflows/**`.

**Interfaces:**
- Consumes: `CpuBackend::replace_in_place(&self, pattern: &str, replacement: &str, path: &str,
  ignore_case: bool, fixed_strings: bool) -> anyhow::Result<()>` - signature unchanged.
- Produces: no new public API. The observable change is that `replace_in_place` returns `Err` when
  `path` names a symlink.

**Bidirectional oracle:**
- **RED arm, pre-fix:** given `real.txt` containing `needle` and a symlink `link.txt -> real.txt`,
  `replace_in_place("needle", "found", link_path, false, true)` currently returns `Ok(())` **and
  rewrites `real.txt`**, because `Path::is_file()` at `:452` follows and `OpenOptions::open` at
  `:590` follows. The test therefore fails with
  `assertion failed: result.is_err()`, and the second assertion would independently fail with the
  file contents showing `found` instead of `needle`. Behavioral RED, exact reason pinned.
- **GREEN arm:** `result.is_err()`, the error message names the path, and `real.txt` still contains
  `needle` (the content assertion is the load-bearing one - an `Err` with the file already rewritten
  would be worse than the bug).
- **Positive control:** a regular (non-symlink) file must still be replaced. Without this, a guard
  that refuses everything would pass the RED arm.
- **Directory-arm pin:** a symlink inside a directory tree must still be skipped, proving the fix
  did not change directory behaviour and pinning a property that is currently incidental.
- **Fixture-bites assertion (A88):** the test asserts
  `std::fs::symlink_metadata(&link).unwrap().file_type().is_symlink()` **before** exercising the
  guard, and panics with an explicit `CANNOT_MEASURE: symlink creation did not produce a symlink`
  message otherwise. A PASS on a fixture that never applied proves nothing.

- [ ] **Step 1: Create the worktree FROM W3A's BRANCH (only after W2 has published)**

```bash
git fetch origin
git worktree add .claude/worktrees/w3b-symlink-guard \
  -b fix/replace-in-place-no-follow-symlink docs/2026-08-13-replace-symlink-threat-model
cd .claude/worktrees/w3b-symlink-guard

# Verify the carry BEFORE writing any Rust:
git log --oneline -2
ls docs/design/2026-08-13-replace-in-place-symlink-threat-model.md
```

Expected: `git log` shows W3A's `docs: threat model ...` commit as the parent, and the `ls` finds the
file. **If either fails, STOP** - the guard's code comment and commit message both cite that document
by path, and a `fix:` PR citing a file present in neither its own diff nor `main` ships a dangling
reference in a security comment. The base is **W3A's branch, not `origin/main`**; a worktree cut from
`origin/main` would not carry the threat model, which is the defect this step exists to prevent.

(The alternative carry - cherry-pick W3A's commit into a worktree cut from `origin/main` - is
equivalent and acceptable if the branch is unavailable:
`git cherry-pick <W3A SHA>` then re-run the same two verification commands. Pick one and record
which; do not assume the document is present.)

- [ ] **Step 2: Step 0 premise check**

```bash
git show origin/main:rust_core/src/backend_cpu.rs | sed -n '440,455p'
git show origin/main:rust_core/src/backend_cpu.rs | grep -n "symlink_metadata"
```

Expected: `is_file()` branch present at `:452`; `symlink_metadata` grep prints **nothing**. If it
prints a hit, the guard shipped and this item is refuted - re-disposition it.

- [ ] **Step 3: Write the failing test in `rust_core/tests/test_replace.rs`**

```rust
#[test]
fn replace_in_place_refuses_to_follow_a_symlinked_file_target() {
    // RUST-REPLACE-SYMLINK: Path::is_file() and OpenOptions::open both FOLLOW, so an
    // attacker-planted symlink redirects an in-place replace to a destination the caller
    // never named (sed CVE-2026-5958 / uutils GHSA-239g-2685-54x3 class).
    let dir = tempfile::tempdir().unwrap();
    let real = dir.path().join("real.txt");
    let link = dir.path().join("link.txt");
    std::fs::write(&real, b"needle here").unwrap();

    #[cfg(unix)]
    std::os::unix::fs::symlink(&real, &link).unwrap();
    #[cfg(windows)]
    // CANNOT_MEASURE, not RED: symlink_file needs SeCreateSymbolicLinkPrivilege (or Developer
    // Mode). An unprivileged runner would panic HERE, in the fixture, and the CI log would read
    // as a failing security test (A61: the RED reason must be the pinned behavioral assertion,
    // never a setup crash). Same shape as tests/test_ast_rewrite.rs:1778-1784.
    if let Err(err) = std::os::windows::fs::symlink_file(&real, &link) {
        eprintln!(
            "skipping replace_in_place_refuses_to_follow_a_symlinked_file_target: \
             cannot create a Windows symlink in this environment: {err}"
        );
        return;
    }

    // A88: prove the hostile fixture actually BITES before trusting the verdict.
    let meta = std::fs::symlink_metadata(&link)
        .expect("CANNOT_MEASURE: symlink was not created at all");
    assert!(
        meta.file_type().is_symlink(),
        "CANNOT_MEASURE: symlink creation did not produce a symlink; this test proves nothing"
    );

    let backend = CpuBackend::new();
    let result = backend.replace_in_place("needle", "found", link.to_str().unwrap(), false, true);

    assert!(
        result.is_err(),
        "replace_in_place must refuse a symlinked target, got Ok"
    );
    let contents = std::fs::read_to_string(&real).unwrap();
    assert_eq!(
        contents, "needle here",
        "the symlink target was rewritten through the link -- the guard did not hold"
    );
}
```

Adapt `CpuBackend::new()` and the import prelude to match the existing constructor pattern already
used in `rust_core/tests/test_replace.rs`; do not invent a constructor.

**Where the decisive verdict comes from, and what the skip line costs.** The skip branch above keeps
an unprivileged Windows runner from producing a wrong-reason RED - but a skip measures nothing, and a
run in which *every* node skipped is a run that proved nothing about a security fix. So:

- **The decisive RED/GREEN is routed to a node known to be capable**: the Linux CI nodes, where
  `std::os::unix::fs::symlink` requires no privilege and the `#[cfg(unix)]` arm always executes.
  Step 5's RED evidence and Step 8's GREEN evidence are both read from a **Linux** job's log.
- **The only acceptable raw CI failure for this test is the pinned behavioral assertion** -
  `replace_in_place must refuse a symlinked target, got Ok` (RED phase) - and nothing else. A panic
  inside the fixture, an `unwrap` on `tempdir`, or a compile error is a GATE FAIL, not a RED.
- **Before reading any verdict, grep the log for the skip line.** If
  `skipping replace_in_place_refuses_to_follow_a_symlinked_file_target` appears on the node whose
  result is being quoted, that node's verdict is CANNOT_MEASURE and must not be quoted as evidence.
  Record the Windows result separately as informational.

- [ ] **Step 4: Add the positive control and the directory pin (same file)**

```rust
#[test]
fn replace_in_place_still_rewrites_a_regular_file() {
    // Positive control for the symlink guard: a guard that refuses EVERYTHING would pass
    // the RED arm above and be indistinguishable from a correct fix.
    let dir = tempfile::tempdir().unwrap();
    let target = dir.path().join("plain.txt");
    std::fs::write(&target, b"needle here").unwrap();

    let backend = CpuBackend::new();
    backend
        .replace_in_place("needle", "found", target.to_str().unwrap(), false, true)
        .expect("a regular file must still be replaced");

    assert_eq!(std::fs::read_to_string(&target).unwrap(), "found here");
}

#[test]
fn replace_directory_mode_skips_symlinked_entries() {
    // Pins a property that is currently INCIDENTAL (walkdir's follow_links(false) default
    // plus DirEntry::file_type().is_file() being false for a symlink). Unpinned, it is one
    // refactor away from becoming the vulnerability the test above guards.
    let dir = tempfile::tempdir().unwrap();
    let outside = dir.path().join("outside.txt");
    std::fs::write(&outside, b"needle here").unwrap();

    let tree = dir.path().join("tree");
    std::fs::create_dir(&tree).unwrap();
    let link = tree.join("link.txt");
    #[cfg(unix)]
    std::os::unix::fs::symlink(&outside, &link).unwrap();
    #[cfg(windows)]
    // CANNOT_MEASURE, not RED -- see the note under Step 3.
    if let Err(err) = std::os::windows::fs::symlink_file(&outside, &link) {
        eprintln!(
            "skipping replace_directory_mode_skips_symlinked_entries: \
             cannot create a Windows symlink in this environment: {err}"
        );
        return;
    }
    assert!(
        std::fs::symlink_metadata(&link).unwrap().file_type().is_symlink(),
        "CANNOT_MEASURE: symlink creation did not produce a symlink"
    );

    let backend = CpuBackend::new();
    backend
        .replace_in_place("needle", "found", tree.to_str().unwrap(), false, true)
        .expect("directory mode must succeed while skipping the symlink");

    assert_eq!(
        std::fs::read_to_string(&outside).unwrap(),
        "needle here",
        "directory mode followed a symlink out of the tree"
    );
}
```

- [ ] **Step 4a: The fail-closed fault-injection arms (in `backend_cpu.rs`, NOT `test_replace.rs`)**

These two tests reach the **private** `replace_fault_injection` seam, which
`rust_core/tests/test_replace.rs` cannot see - that file links against the non-`--cfg test` build
where the field compiles away entirely (the existing comment at `backend_cpu.rs:1316-1323` says so).
They therefore live in the in-file `#[cfg(test)] mod tests` alongside the existing
`test_replace_in_place_directory_walk_failure_propagates_as_err_with_path_context`, and they extend
the existing struct rather than inventing a parallel harness.

Add one `#[cfg(test)]`-only field to `ReplaceFaultInjection` (`backend_cpu.rs:284-291`). **The FIELD
is part of the RED commit; the ARMS below are not** - they assert properties of a guard that does not
exist until Step 8, so they land with it (Step 5 states the split and its A61 reason). The field is
test-only scaffolding with no behavioural effect in a release build - the same field-first /
logic-second split W2A Steps 5-6 use.

```rust
    /// Force the symlink_metadata guard's stat call to fail, without needing a real
    /// unreadable path. Proves the guard fails CLOSED rather than falling through.
    #[allow(dead_code)] // consumed by the W3B Step 8 test arms; this allow is removed in that commit
    force_symlink_metadata_failure: bool,
```

```rust
    #[test]
    fn test_replace_in_place_fails_closed_when_symlink_metadata_errors() {
        // The guard's FIRST draft was `if let Ok(meta) = symlink_metadata(..)`, which fails
        // OPEN: on any stat error the guard silently does nothing and the follow behaviour
        // returns, with nothing observable distinguishing "checked and safe" from "could not
        // check". This is the arm that discriminates those two.
        let dir = tempfile::tempdir().unwrap();
        let target = dir.path().join("plain.txt");
        write_fixture_file(&target, "needle");

        let backend = CpuBackend::new();
        backend
            .replace_fault_injection
            .lock()
            .unwrap()
            .force_symlink_metadata_failure = true;

        let result =
            backend.replace_in_place("needle", "found", target.to_str().unwrap(), false, true);

        let err = result.expect_err(
            "a symlink_metadata failure must fail CLOSED as Err, never fall through to the rewrite",
        );
        let msg = format!("{err:#}");
        assert!(
            msg.contains(&target.display().to_string()),
            "the error must name the path it could not stat; got {msg}"
        );
        assert_eq!(
            std::fs::read_to_string(&target).unwrap(),
            "needle",
            "FAILED OPEN: the file was rewritten even though the guard could not stat it"
        );
    }

    #[test]
    fn test_replace_in_place_rewrites_normally_when_no_metadata_fault_is_injected() {
        // The other direction of the bidirectional pair. Without this, a guard that refused
        // on EVERY stat outcome would pass the test above and be indistinguishable from a
        // correct fail-closed guard. Same fixture, same call, fault flag OFF.
        let dir = tempfile::tempdir().unwrap();
        let target = dir.path().join("plain.txt");
        write_fixture_file(&target, "needle");

        let backend = CpuBackend::new();
        backend
            .replace_in_place("needle", "found", target.to_str().unwrap(), false, true)
            .expect("with no injected fault a regular file must still be rewritten");

        assert_eq!(std::fs::read_to_string(&target).unwrap(), "found");
    }
```

**The bidirectional claim these two make together:** a metadata failure can reach **neither** rewrite
path - not the literal fast path, not the regex route - because the guard sits above both (Step 6),
and the fault is injected at the guard itself. The second test proves the first is not vacuous.

**Plus the retained-contract test.** Failing closed changes one pre-existing behaviour: a path that
does not exist now surfaces the guard's stat error instead of the `is_file()` branch's error. That is
a deliberate, narrow contract change, so it is pinned rather than left to chance:

```rust
    #[test]
    fn test_replace_in_place_on_a_missing_path_still_errors_with_the_path_named() {
        // Compatibility pin for the fail-closed guard: a nonexistent path errored BEFORE the
        // guard (via the is_file() branch) and errors AFTER it (via symlink_metadata). The
        // caller-visible contract that must survive is "Err, naming the path" -- not which of
        // the two produced it. Pinning the contract rather than the message keeps this from
        // becoming a change-detector test.
        let dir = tempfile::tempdir().unwrap();
        let missing = dir.path().join("does-not-exist.txt");

        let backend = CpuBackend::new();
        let err = backend
            .replace_in_place("needle", "found", missing.to_str().unwrap(), false, true)
            .expect_err("a missing path must still be an Err");

        assert!(
            format!("{err:#}").contains(&missing.display().to_string()),
            "the error must name the missing path"
        );
    }
```

- [ ] **Step 4b: The bounded event-gated leaf-swap CHARACTERIZATION PIN (A38/A48)**

**Polarity, stated first because an earlier revision got it wrong.** This arm is **not a RED**. A RED
arm asserts the behaviour the fix will produce and fails until the fix lands. This arm asserts the
behaviour that is **still broken after** the fix lands - the residual TOCTOU window is OPEN, and this
PR does not close it. That is a **characterization pin**: it PASSES on the shipped bytes and is
expected to **FLIP to failing** when `RUST-REPLACE-TOCTOU` closes the race. Calling it a RED would
mean the PR could not go green with it present, which is the opposite of what it does.

**Why it is required and not extra.** The board Trigger frames RUST-REPLACE-SYMLINK as a *deliberate
close*, and A38/A48 state that leaf resolution and swap resistance are **separate security
contracts**. Shipping only the static guard and writing SHIPPED against that Trigger would claim more
than was built. The board Trigger's own wording requires an event-gated swap test; a Boolean flag and
a synchronous helper call is not one, because nothing in it can distinguish "the writer was inside
the window" from "the swap happened at some other time".

**The mechanism, in full.** Two **capacity-1** channels (`std::sync::mpsc::sync_channel::<()>(1)`),
chosen so that **no blocking operation in the handshake is unbounded**: each channel carries exactly
one message, so every `send` either fills the empty slot or fails immediately because the peer is
gone - it can never block. Every `recv` is `recv_timeout`-bounded by a `Duration` that panics
`CANNOT_MEASURE` on expiry, and two real actors: the writer on a spawned thread, the swapper on the
test's main thread. (Round-3 council fix: the original capacity-0 rendezvous form lets a `send`
block forever when its peer never arrives, defeating the bound.)

Replace the Step 4a-adjacent bool with a handshake pair. Add beside `force_symlink_metadata_failure`
on `ReplaceFaultInjection` (`backend_cpu.rs:284-291`), `#[cfg(test)]`-only:

```rust
    /// Event gate for the residual-TOCTOU characterization pin. The writer signals on
    /// `reached_guard` once it is PAST the no-follow guard and BEFORE the writer opens the
    /// path, then blocks on `resume` until the second actor acknowledges its swap. Both are
    /// capacity-1 channels: a `send` never blocks, a `recv` is always `recv_timeout`-bounded,
    /// so this is a handshake, not a sleep and not a hang vector.
    #[allow(dead_code)] // consumed by the W3B Step 8 pin arm; this allow is removed in that commit
    swap_gate: Option<SwapGate>,
```

```rust
#[allow(dead_code)] // consumed by the W3B Step 8 pin arm; this allow is removed in that commit
#[cfg(test)]
struct SwapGate {
    reached_guard: std::sync::mpsc::SyncSender<()>,
    resume: std::sync::mpsc::Receiver<()>,
}
```

`SwapGate` holds a `Receiver`, which is not `Clone` and must not be used while the injection mutex is
held (holding it across a blocking wait would deadlock the second actor out of the same struct). The
guard site therefore `take()`s the whole gate out under a short lock and drops the lock before it
waits - written out in Step 6.

**The bound.** One constant, used by both actors:

```rust
#[allow(dead_code)] // consumed by the W3B Step 8 pin arm; this allow is removed in that commit
#[cfg(test)]
const SWAP_GATE_TIMEOUT: std::time::Duration = std::time::Duration::from_secs(2);
```

Two seconds is a deadlock detector, not a race budget: nothing in this handshake waits on real work,
so any expiry means the peer never arrived. Every expiry panics with a message beginning
`CANNOT_MEASURE:` so a hung handshake can never be read as a verdict about the window.

**The helper the second actor calls** (`#[cfg(test)]`, a free function in the in-file `mod tests`
beside the fault struct - **not** a `CpuBackend` method, because production code never calls it):

```rust
    /// Replace the leaf `path` with a symlink pointing at `attacker_target`, exactly as an
    /// attacker who wins the TOCTOU race would. Returns false when this environment cannot
    /// create a symlink at all -- CANNOT_MEASURE, not a failed swap (A61).
    #[allow(dead_code)] // consumed by the W3B Step 8 pin arm; this allow is removed in that commit
    fn swap_leaf_to_symlink_for_test(path: &Path, attacker_target: &Path) -> bool {
        if let Err(err) = std::fs::remove_file(path) {
            eprintln!("skipping the residual-TOCTOU pin: cannot unlink the leaf: {err}");
            return false;
        }
        #[cfg(unix)]
        {
            match std::os::unix::fs::symlink(attacker_target, path) {
                Ok(()) => true,
                Err(err) => {
                    eprintln!("skipping the residual-TOCTOU pin: symlink failed: {err}");
                    false
                }
            }
        }
        #[cfg(windows)]
        {
            // Same shape as Step 3 and tests/test_ast_rewrite.rs:1778-1784: an unprivileged
            // runner lacks SeCreateSymbolicLinkPrivilege, and that is CANNOT_MEASURE.
            match std::os::windows::fs::symlink_file(attacker_target, path) {
                Ok(()) => true,
                Err(err) => {
                    eprintln!(
                        "skipping the residual-TOCTOU pin: cannot create a Windows symlink \
                         in this environment: {err}"
                    );
                    false
                }
            }
        }
    }
```

**The pin itself:**

```rust
    #[test]
    fn test_replace_in_place_leaf_swapped_between_guard_and_open_characterizes_the_residual_window()
    {
        // CHARACTERIZATION PIN, NOT A RED. A38/A48: the leaf check and a swap-resistant WRITER
        // are SEPARATE contracts, and this PR ships the first only. This test asserts the
        // CURRENT behaviour -- the window is OPEN, and a leaf swapped between the guard's stat
        // and the writer's open still redirects the write -- so the residual is an enforced
        // property of the code rather than a sentence in a doc that nothing checks.
        //
        // WHEN RUST-REPLACE-TOCTOU LANDS, THIS PIN MUST FLIP: the writer will open with
        // O_NOFOLLOW / FILE_FLAG_OPEN_REPARSE_POINT, the swap will stop redirecting, and this
        // assertion will fail. That failure is the REOPEN SIGNAL for that row -- invert the
        // assertion then, do not delete the test.
        //
        // Bounded by construction: a two-party capacity-1 handshake with a 2s cap on every
        // wait, no spin loop and no wall-clock race. An unbounded race test on a shared CI
        // runner is a flake generator, not evidence.
        let dir = tempfile::tempdir().unwrap();
        let target = dir.path().join("plain.txt");
        let attacker = dir.path().join("attacker-owned.txt");
        write_fixture_file(&target, "needle");
        write_fixture_file(&attacker, "needle");

        let (reached_tx, reached_rx) = std::sync::mpsc::sync_channel::<()>(1);
        let (resume_tx, resume_rx) = std::sync::mpsc::sync_channel::<()>(1);

        let backend = CpuBackend::new();
        backend.replace_fault_injection.lock().unwrap().swap_gate = Some(SwapGate {
            reached_guard: reached_tx,
            resume: resume_rx,
        });

        // ACTOR 1: the writer. The backend is MOVED into the thread, so this needs only
        // CpuBackend: Send (not Sync) -- the test keeps nothing but the channel endpoints.
        let writer_path = target.clone();
        let writer = std::thread::spawn(move || {
            backend.replace_in_place(
                "needle",
                "found",
                writer_path.to_str().unwrap(),
                false,
                true,
            )
        });

        // Bounded wait: the writer must reach the post-guard point, or this test measured
        // nothing at all.
        reached_rx.recv_timeout(SWAP_GATE_TIMEOUT).expect(
            "CANNOT_MEASURE: the writer never signalled the post-guard point within 2s; the \
             gate is not wired at the guard site",
        );

        // ACTOR 2: the attacker, deterministically inside the window.
        let swapped = swap_leaf_to_symlink_for_test(&target, &attacker);

        // Release the writer either way -- an un-acknowledged writer would sit until its own
        // 2s bound and panic in a thread, turning a skip into a confusing failure. The send
        // itself cannot block (capacity 1); it errors immediately only if the writer is gone.
        resume_tx
            .send(())
            .expect("CANNOT_MEASURE: the writer thread vanished before acknowledgment");
        let write_result = writer.join().expect("the writer thread panicked");

        if !swapped {
            // The skip line is already on stderr from the helper. No verdict from this node.
            return;
        }

        assert!(
            write_result.is_ok(),
            "the writer errored instead of following the swapped-in link; the window may have \
             moved -- re-derive before changing this pin"
        );
        assert_eq!(
            std::fs::read_to_string(&attacker).unwrap(),
            "found",
            "RESIDUAL TOCTOU (RUST-REPLACE-TOCTOU) -- CHARACTERIZATION PIN: a leaf swapped \
             between the guard and the open still redirects the write. If this assertion now \
             FAILS, the window was CLOSED -- invert this pin and close that board row."
        );
    }
```

**The decisive node is Linux**, per the Step 3 note: `std::os::unix::fs::symlink` needs no privilege,
so the `swapped == false` early return can only fire on an unprivileged Windows runner. A Windows
node that printed the skip line contributes no verdict about this pin.

**Scope fence:** this arm pins one window in its CURRENT open state. It is **not** a claim that the
TOCTOU class is closed - it is the enforced record that it is not - and Step 11 files
`RUST-REPLACE-TOCTOU` in the same campaign precisely so the partial fix cannot read as a complete
one.

- [ ] **Step 5: Commit the RED tests ALONE and push, so CI proves the RED**

Local `cargo` is banned, so **CI is the RED oracle**. Push the tests without the fix.

**The RED commit stages TWO files, not one.** Steps 4a and 4b add `#[cfg(test)]`-only scaffolding
that lives *inside* `rust_core/src/backend_cpu.rs`: the `force_symlink_metadata_failure` field, the
`swap_gate` field, the `SwapGate` struct, `SWAP_GATE_TIMEOUT`, and the
`swap_leaf_to_symlink_for_test` helper. Staging only `test_replace.rs` would leave the RED run to
die on a **compile error** the moment the guard commit lands mid-review, and it splits one logical
scaffolding change across two commits for no reason. The scaffolding is test-only and has zero
effect in a release build; **the guard itself does not land until Step 8.**

**Clippy / `dead_code` pin for the RED commit.** Until the Step-8 arms land, five
`#[cfg(test)]` items have no consumers - `force_symlink_metadata_failure` (Step 4a),
`swap_gate`, `SwapGate`, `SWAP_GATE_TIMEOUT`, and `swap_leaf_to_symlink_for_test` (Step 4b) -
and CI's `clippy -D warnings` fails each of them as `dead_code`: a wrong-reason RED (A61).
Each of the five therefore carries `#[allow(dead_code)]` **in its code block** with the comment
`// consumed by the W3B Step 8 test arms; this allow is removed in that commit`. Step 8 removes
the allows together with the arms. If the CI log shows `unused`/`dead_code` anyway, fix the
allow placement before reading anything else into the run.

**What is NOT in the RED commit, and why.** The in-file `#[cfg(test)] mod tests` ARMS from Steps 4a
and 4b (`..._fails_closed_when_symlink_metadata_errors`,
`..._rewrites_normally_when_no_metadata_fault_is_injected`,
`..._on_a_missing_path_still_errors_with_the_path_named`, and the residual-TOCTOU characterization
pin) land with the guard in Step 8. Every one of them asserts a property of a guard that does not
exist yet, and two of them would fail for the *wrong reason* in a RED run: with no guard site to
honour it, the swap gate is never signalled, so the pin would die on its 2s
`CANNOT_MEASURE: the writer never signalled the post-guard point` bound. A CANNOT_MEASURE panic in
the RED run is precisely the wrong-reason failure A61 forbids, and it would pollute the one raw log
line this step exists to capture. The RED phase proves exactly one thing - the follow behaviour is
real - and the fields/struct/helper are staged with it only so the two commits do not straddle a
compile unit.

```bash
git add rust_core/tests/test_replace.rs rust_core/src/backend_cpu.rs
git diff --cached --name-status
```

Expected, exactly these two lines and nothing else:

```
M       rust_core/src/backend_cpu.rs
M       rust_core/tests/test_replace.rs
```

**If any other path appears, unstage it** - the dirty parent checkout carries another session's work
and `git add` of a directory would sweep it in. Then confirm the guard is genuinely absent from what
is staged:

```bash
git diff --cached rust_core/src/backend_cpu.rs | grep -n "symlink_metadata(path_obj)"
```

Expected: **no output**. A hit means Step 6's guard leaked into the RED commit and the RED would be
vacuous.

```bash
git commit -m "test: RED arm for replace_in_place symlink follow (RUST-REPLACE-SYMLINK)"
git push -u origin fix/replace-in-place-no-follow-symlink
gh pr create --draft --title "fix: replace_in_place refuses to follow a symlinked target" --body "RED arm plus test-only fault scaffolding; guard follows in the next commit."
gh pr checks <PR#> --watch
```

Expected in the CI log: `replace_in_place_refuses_to_follow_a_symlinked_file_target` FAILS with
`assertion failed: replace_in_place must refuse a symlinked target, got Ok`, while
`replace_in_place_still_rewrites_a_regular_file` and `replace_directory_mode_skips_symlinked_entries`
**PASS**. Copy the raw log lines into the PR body. If the directory pin fails, the design's safety
claim about the directory arm is wrong and W3A must be re-opened before proceeding.

- [ ] **Step 6: Implement the guard**

In `rust_core/src/backend_cpu.rs`, at the top of `replace_in_place` (immediately after
`let path_obj = Path::new(path);` at `:448`, **before** the `fixed_strings` fast path at `:450`, so
both the literal and the regex routes are covered by one guard):

```rust
        // RUST-REPLACE-SYMLINK: refuse a symlinked target rather than writing THROUGH it.
        // Path::is_file() and OpenOptions::open both FOLLOW symlinks, so without this an
        // attacker-planted link redirects an in-place rewrite to a destination the caller
        // never named (GNU sed CVE-2026-5958, uutils GHSA-239g-2685-54x3, Capgo
        // CVE-2026-56236). symlink_metadata does NOT follow.
        //
        // RESIDUAL TOCTOU, deliberately not claimed as closed: the path can be swapped
        // between this stat and the open below. Closing it needs O_NOFOLLOW (POSIX) plus
        // FILE_FLAG_OPEN_REPARSE_POINT (Windows) at the open site. This guard turns a
        // reliable static-symlink overwrite into a race the attacker must win; see
        // docs/design/2026-08-13-replace-in-place-symlink-threat-model.md.
        //
        // Directory mode is unaffected: walk_directory_entries uses WalkDir's default
        // follow_links(false) (the call is at the WalkDir::new site inside
        // walk_directory_entries) and both directory routes skip non-is_file() entries.
        //
        // FAILS CLOSED. `if let Ok(meta) = ..` would fail OPEN: on any stat error -- a
        // permission denial, a reparse point whose filter driver refuses the query, an EIO --
        // the guard would silently do nothing and the follow behaviour would return, with
        // nothing observable separating "checked and safe" from "could not check".
        let meta = {
            #[cfg(test)]
            if self
                .replace_fault_injection
                .lock()
                .unwrap()
                .force_symlink_metadata_failure
            {
                anyhow::bail!(
                    "replace_in_place: cannot determine whether {} is a symlink: injected test fault",
                    path_obj.display()
                );
            }
            std::fs::symlink_metadata(path_obj).map_err(|err| {
                anyhow::anyhow!(
                    "replace_in_place: cannot determine whether {} is a symlink: {}",
                    path_obj.display(),
                    err
                )
            })?
        };
        if meta.file_type().is_symlink() {
            anyhow::bail!(
                "replace_in_place: refusing to follow symlink {}; pass the resolved target explicitly",
                path_obj.display()
            );
        }
        // A38/A48: the leaf check above and a swap-resistant WRITER are separate contracts.
        // This PR ships the leaf check only. The gate below is the seam that lets a test stand
        // INSIDE the residual window -- past the guard, before the open -- so the window is an
        // asserted property (the characterization pin, Step 4b) rather than a doc sentence.
        //
        // The gate is TAKEN out from under the lock and the lock DROPPED before either wait:
        // holding the injection mutex across the blocking `recv_timeout` would deadlock the
        // second actor out of the same struct. take() also makes it fire exactly once.
        #[cfg(test)]
        {
            let gate = self.replace_fault_injection.lock().unwrap().swap_gate.take();
            if let Some(gate) = gate {
                // Non-blocking: the channel has capacity 1 and this is its only message.
                gate.reached_guard.send(()).expect(
                    "CANNOT_MEASURE: swap-gate signal channel closed before the writer arrived",
                );
                gate.resume.recv_timeout(SWAP_GATE_TIMEOUT).expect(
                    "CANNOT_MEASURE: no swap acknowledgment within 2s; the handshake deadlocked",
                );
            }
        }
```

The two `expect` messages both begin `CANNOT_MEASURE:` deliberately: a hung handshake must be
unmistakable in a CI log as an instrument failure, never readable as a verdict about the window.

**Two consequences to state rather than discover:**

- A path that does not exist now returns the guard's `cannot determine whether ... is a symlink`
  error instead of the `is_file()` branch's error. Step 4a's compatibility test pins the surviving
  contract ("Err, naming the path") rather than the exact message, so this does not become a
  change-detector test.
- The `#[cfg(test)]` blocks compile away entirely in a release build; they are reachable only from
  the in-file unit tests, exactly like the existing `force_walk_failure` seam.

- [ ] **Step 7: Format check (the ONLY permitted local Rust command)**

```bash
rustfmt --check rust_core/src/backend_cpu.rs rust_core/tests/test_replace.rs
```

Expected: no output (clean). **Do not run `cargo` in any form.**

- [ ] **Step 8: Commit the fix and push - CI is the GREEN oracle**

```bash
git add rust_core/src/backend_cpu.rs
git diff --cached --name-status      # expect exactly: M rust_core/src/backend_cpu.rs
git commit -F - <<'EOF'
fix: replace_in_place refuses to follow a symlinked target

Path::is_file() and OpenOptions::open both follow symlinks, so
replace_in_place(pat, rep, "<planted-symlink>") wrote THROUGH the link to a
destination the caller never named -- the class actively earning 2026 CVEs in
peer tools (GNU sed CVE-2026-5958, uutils GHSA-239g-2685-54x3, Capgo
CVE-2026-56236).

Guard: symlink_metadata (which does not follow) before the is_file() branch,
covering both the literal fast path and the regex route. Directory mode is
unchanged and now pinned -- WalkDir's follow_links(false) default plus the
is_file() filter already skipped symlinked entries, but that safety was
incidental and unpinned.

BEHAVIOR CHANGE: a symlinked path argument now returns Err. Measured
reachability: replace_in_place is pub but has no tg CLI caller; the only
in-tree callers are backend_cpu.rs and tests/test_replace.rs.

Residual TOCTOU is NOT closed and is not claimed to be; see
docs/design/2026-08-13-replace-in-place-symlink-threat-model.md.
EOF
git push
gh pr ready <PR#>
gh pr checks <PR#> --watch
```

Expected on CI, full matrix green:

- the three `test_replace.rs` arms PASS (the symlink refusal now flips from RED to GREEN; the
  positive control and the directory pin were already green in Step 5);
- the four in-file arms landing in this commit PASS - the fail-closed pair, the missing-path
  compatibility pin, and the **residual-TOCTOU characterization pin**, which passes because the
  window is still open and is *supposed* to be. A failure there means either the gate is not wired at
  the guard site (its message will say `CANNOT_MEASURE`) or the window closed by accident, which is a
  finding either way, not a nit.

**A87: expect the first real CI run to find compile errors that static review missed** - two prior
Rust PRs did exactly this. Budget two CI rounds before escalating.

- [ ] **Step 9: Mandatory adversarial security audit (A3)**

Dispatch an independent adversarial audit on the **exact pushed bytes, after CI is green**. Brief:
"try to BREAK this guard; cite `file:line`; default FIX-FIRST if uncertain." Required attack
surfaces to check by name:
- a second entry point into `replace_file_literal` / `replace_file_regex` that bypasses the guard;
- Windows **directory junctions**, on which `Path::is_symlink()` and
  `symlink_metadata().file_type().is_symlink()` behave differently from POSIX symlinks. Give the
  auditor GATE-W3A-1's recorded outcome ((a) refuse / (b) document / (c) fold) and ask whether the
  shipped bytes actually match it - a row that says "documented" while the comment says nothing is
  the failure mode here;
- whether the guard can be reached in a state where it fails **open**. The guard is written to fail
  closed (`symlink_metadata(..).map_err(..)?`), and Step 4a proves it in both directions; the
  auditor's job is to find a path that reaches `replace_file_literal` / `replace_file_regex` without
  passing the guard at all - a second entry point, or a route added later;
- whether the `#[cfg(test)]` swap seam can be reached in a release build (it must not be);
- whether the error message leaks a path the caller should not learn.

Verdict shape: `SHIP` or `FIX-FIRST(+file:line + repro + minimal fix)`. **A81: the implementer's own
receipts are not a SHIP verdict.** A no-verdict seat is FAILED, not a blocker (A10).

- [ ] **Step 10: Fold safety findings, bank cosmetic ones (A19), then merge**

Any finding that changes observable behaviour folds into THIS PR before merge. Purely cosmetic
findings are banked as a follow-up. Then:

```bash
gh run list --workflow=ci.yml --branch=main --limit 5 --json databaseId,status,conclusion
gh run view <run-id> --json status,conclusion
```

Merge only with no run in flight on main and W2's release fully published.

- [ ] **Step 11: Hand the residual-TOCTOU row to W8 with its exact text**

The row is filed by W8 (a docs change, not a code change). W3B's job is to hand over the **exact
parser-legal text**, so W8 is not left composing a security row from memory. See W8 Step 4 for the
insertion and the governance-test run.

- [ ] **Step 12: Update the board Trigger text to claim exactly what shipped**

Hand W8 the `RUST-REPLACE-SYMLINK` SHIPPED text, which must name what was actually built and nothing
more: a **static no-follow guard + fail-closed metadata handling + a directory-arm pin + a residual
race characterization pin (window currently OPEN)**, with the race itself pointed at
`RUST-REPLACE-TOCTOU` along with the GATE-W3A-1 junction outcome. The phrase "window currently OPEN"
is load-bearing and must survive into the row: it is the difference between "we tested the race" and
"we closed the race". **A SHIPPED row that implies the TOCTOU class is closed is the exact false
claim design 5.4a exists to prevent.**

**Acceptance criteria:**
- CI log shows the RED (Step 5) and the GREEN (Step 8) on the same PR, with raw lines in the PR body,
  **read from a Linux job** (the Windows arms may legitimately be CANNOT_MEASURE).
- Positive control and directory pin PASS in **both** arms.
- Fixture-bites assertion present in both symlink tests; both Windows fixtures use the
  skip-with-reason shape, and no quoted verdict comes from a node whose log carries the skip line.
- The RED commit's staged set is exactly `rust_core/tests/test_replace.rs` +
  `rust_core/src/backend_cpu.rs`, proven by the `git diff --cached --name-status` output in Step 5,
  with the guard absent from it.
- The fail-closed pair (Step 4a) both PASS, and the missing-path compatibility test PASSES.
- The residual-TOCTOU **characterization pin** (Step 4b) PASSES on a Linux node with its "invert, do
  not delete" comment intact - and is described as a characterization pin, never as a RED, in the PR
  body, the board row, and the commit message.
- **GATE-W3A-1's outcome ((a)/(b)/(c)) is named in the PR body, and the shipped bytes match it** -
  (a) implies a junction test exists; (b) implies the scope limit appears in the code comment, the
  threat model, and the board Trigger; (c) implies `RUST-REPLACE-TOCTOU`'s Trigger names junctions.
- Independent adversarial audit returns SHIP (or FIX-FIRST findings folded and re-audited).
- Merged; a new patch version published.
- `RUST-REPLACE-TOCTOU` row text and the `RUST-REPLACE-SYMLINK` SHIPPED text handed to W8.

**Commit boundary:** exactly two commits, so the CI RED is preserved in history.
1. **RED** - stages **both** `rust_core/tests/test_replace.rs` (the three external arms) **and**
   `rust_core/src/backend_cpu.rs` (test-only fault fields, `SwapGate`, `SWAP_GATE_TIMEOUT`, the
   `swap_leaf_to_symlink_for_test` helper). **No guard.**
2. **GREEN** - stages `rust_core/src/backend_cpu.rs` only: the guard, the swap-gate honouring site,
   and the four in-file `#[cfg(test)]` arms that assert guard behaviour.
**Rollback:** `git revert` both commits. The change is a pure refusal with no state; reverting
restores the follow behaviour exactly.

---

# WAVE 4 - Task 2A repair round (timeboxed)

**Rank:** 6 of 8. The repair *content* comes from the Sol round-2 verdict, which does not exist yet;
the repair *procedure* does not depend on it and is written out in full below. GATE-W4-1 is the one
point where the verdict enters, and it has a defined branch for every outcome including "no verdict".

**Requirement refs:** brief section D; board rows #89 / #90;
`docs/audits/2026-08-12-stale-branch-reconciliation.md` sections 3-5 (F1-F6, the 10-HIGH set, the
R0-R1 round ledger)
**Dependency:** none technical (draft #966 already MERGEABLE); counts against the WIP cap
**Release class:** `test:` on draft PR #966 - does NOT publish, MUST NOT merge

**Files:** confined to the `task2a-round60-red` branch in the existing worktree
`.claude/worktrees/task2a-w4-repair`. Re-verify the worktree is clean and at the expected head
immediately before use (A23/A26).

**Allowed paths:** the Task 2A suites, `.github/workflows/ci.yml` Task 2A steps,
`tests/fixtures/task2a_windows_node_manifest.json`.
**Protected paths:** `origin/main`; **never merge #966**; never edit a worktree a live agent owns
(A-law from 2026-08-04).

**Scope, stated as an exclusion:** repair the remaining Python and CI-wiring findings **only**.
**F5 (parent-handle anchoring for atomic `write_receipt`) stays RED-by-design program work** and is
explicitly NOT in scope. F2 (one valid node clearing the whole manifest) is the workflow-level
receipt-aggregation item; include it only if it is Python/CI-wiring shaped.

**Per-round gates (binding, every round):**
1. A live CI run on **that round's own head** (A68/M5': a pre-repair run cannot confirm anything
   about repaired bytes). Record run ID, head SHA, base SHA, merge-ref checkout SHA, and executed
   node population against the **169-node manifest census** (157 python + 12 rust; test-python 103 /
   native-build-smoke 66).
2. Per-node outcome diff against the pre-merge baseline; **any collection/import/setup error is a
   GATE FAIL, never an acceptable RED** (A61).
3. Sol exact-byte re-audit on the pushed head.
4. Whole-repo `uv run ruff format --check --preview .`; `mypy src/tensor_grep` if imports changed.
5. **No cargo locally** - CI is the Rust oracle.

**Timebox:** **2 repair rounds maximum.** If still FIX-FIRST after round 2, **park honestly**:
append the round receipts to `docs/audits/2026-08-12-stale-branch-reconciliation.md` section 4, and
W8 leaves #89 and #90 **BLOCKED**.

### Steps

- [ ] **Step 1: Step 0 premise check - is #966 still the live vehicle?**

```bash
git fetch origin
gh pr view 966 --json number,state,isDraft,mergeable,headRefName,headRefOid
gh pr list --state open --json number,title,isDraft   # enumerate, never a baked list
```

Expected: #966 OPEN, draft, `MERGEABLE`, head ref `task2a-round60-red`. **If #966 is closed, merged,
or CONFLICTING, STOP** and re-disposition: a repair round against a dead vehicle produces receipts
that reference nothing. The `gh pr list` line is deliberate - a hardcoded PR list is stale by
definition, and a later-opened PR would otherwise be invisible to this wave.

- [ ] **Step 1a: Ensure the worktree EXISTS before the step that assumes it**

This wave's Files section names `.claude/worktrees/task2a-w4-repair` as if it were guaranteed. It is
not - it is a worktree a previous session created, and a plan that opens with
`git -C <path> status` against a path that may not exist fails with a git error whose text reads
nothing like "provision the worktree".

```bash
git worktree list | grep -F "task2a-w4-repair"
```

**If it is listed** (verified present at authoring time, 2026-08-13, at `8181762` on
`task2a-round60-red`, matching #966's `headRefOid` from Step 1): nothing to create. Go to Step 2,
which re-verifies both properties live rather than trusting this parenthesis.

**If it is absent**, create it from **#966's head**, never from `origin/main` - a worktree cut from
main carries none of the round-60 RED work this wave repairs:

```bash
git fetch origin
git worktree add .claude/worktrees/task2a-w4-repair task2a-round60-red

# If the local branch ref is missing or stale, take the PR head directly instead:
#   gh pr checkout 966 --branch task2a-round60-red   (in the main checkout, then worktree add)
# or, detached and unambiguous, straight from the OID Step 1 printed:
#   git worktree add --detach .claude/worktrees/task2a-w4-repair <headRefOid>
```

Then re-run Step 1's `gh pr view` and confirm the new worktree's `rev-parse HEAD` equals
`headRefOid`. **A worktree whose head does not match the PR is not this wave's vehicle** - repairing
bytes that are not what CI will run is the "sufficient is not operative" failure.

- [ ] **Step 2: Re-verify the worktree before touching it (A23/A26)**

```bash
git -C .claude/worktrees/task2a-w4-repair status --porcelain   # expect empty
git -C .claude/worktrees/task2a-w4-repair rev-parse HEAD       # must equal #966's headRefOid
```

**If the worktree is dirty or its head differs, do NOT edit it.** A live agent may own it (the
2026-08-04 receipt: a seat reported "running now", the notification said COMPLETED, and the worktree
was being written). Probe for liveness before assuming abandonment.

- [ ] **Step 3: Re-derive the open finding set from the ledger, per finding**

```bash
git show origin/main:docs/audits/2026-08-12-stale-branch-reconciliation.md | grep -nE "^\| F[1-6] " -B 2 -A 1
```

**The pattern matters and an earlier revision had it wrong.** The findings live in **table rows**
(`| F1 | HIGH | ... |`), not under `###` headings - a `^### F[1-6]` grep returns **nothing** on this
file, and an empty result here is indistinguishable from "no findings remain", which is the exact
false-zero this campaign is written against. Confirm the instrument before reading the result: the
command above must print **at least six** `| F<n> |` lines (the round-1 F1-F6 verdict table), and the
`-B 2` context must show which table each row belongs to - the file carries *two* F1-F6 sequences (a
BLOCKED-premise recheck table and the round-1 findings table) and they are different populations.
A run that prints fewer than six is CANNOT_MEASURE, not a clean ledger.

Build a table: finding ID, one-line claim, current status in the ledger (OPEN / repaired / refuted),
and shape (Python / CI-wiring / native). **One command's output per finding, recorded separately**
(A98) - no finding's status is inferred from a sibling's. F5 is excluded by the scope fence above
regardless of what the ledger says.

- [ ] **GATE-W4-1: the Sol round-2 verdict determines the repair list**

**Command:** dispatch the Sol exact-byte re-audit on #966's pushed head, then read the verdict block.

**Trigger:**
- Verdict `SHIP` -> no repair round is needed. Skip to Step 6 and record the receipt; #89/#90 still
  do **not** become GREEN (see acceptance criteria - that needs Sol SHIP **plus** real Windows CI
  evidence naming all three SHAs).
- Verdict `FIX-FIRST` with findings -> the repair list is exactly those findings, filtered by the
  scope fence (Python + CI-wiring only; F5 excluded). Proceed to Step 4.
- **No verdict** (a seat that returned nothing, or returned a verdict beside a
  `CANNOT_READ_REQUIRED_FILE` line) -> that seat is **FAILED, not a blocker** (A10), and a verdict
  printed beside a read failure is **discarded**, not honoured. Re-dispatch **once** to a fresh seat.

**Re-approval rule on FAIL (second dispatch also returns no verdict):** do not improvise a repair
list from the round-1 ledger. Park the wave at Step 6 with `SOL_ROUND2 = CANNOT_MEASURE` recorded,
and W8 leaves #89/#90 BLOCKED with that as the stated prerequisite. An unaudited repair round is
worse than no round: it consumes the timebox and produces a receipt nobody can rely on.

- [ ] **Step 4: Repair round N (N in {1, 2}) - one commit, scoped**

For each in-scope finding: write the failing arm first, confirm it fails for the **pinned reason**
(a collection/import/setup error is a GATE FAIL, never an acceptable RED - A61), then fix. Stage
explicit paths; `git diff --cached --name-status` before committing.

- [ ] **Step 5: Round gates - all five, in order, on THAT round's own head**

```bash
git push                                     # fast-forward onto the draft branch
gh pr checks 966 --watch
gh run view <run-id> --json status,conclusion,jobs,headSha
```

1. Live CI on the round's own head (A68/M5': a pre-repair run confirms nothing about repaired bytes).
   Record run ID, head SHA, base SHA, merge-ref checkout SHA, and the executed node population
   against the **169-node manifest census** (157 python + 12 rust; test-python 103 /
   native-build-smoke 66).
2. Per-node outcome diff vs the pre-merge baseline; any collection/import/setup error is a GATE FAIL.
3. Sol exact-byte re-audit on the pushed head.
4. `uv run ruff format --check --preview .`; `mypy src/tensor_grep` if imports changed.
5. No cargo locally - CI is the Rust oracle.

- [ ] **GATE-W4-2: node-population census, not a spot check**

**Command:** the per-node outcome list from Step 5's `gh run view --json jobs`, counted.

**Trigger:** the executed node count must reconcile to the 169-node manifest census. **A count below
the census is CANNOT_MEASURE, not a pass** - a run that executed 40 nodes and reported no failures
proves nothing about the other 129, and a `cancelled` node silently drops out of a naive success
count (the 2026-08-05 receipt: one evicted lane marked a whole main run cancelled).

**Re-approval rule on FAIL:** re-run the failed/cancelled lanes by run ID
(`gh run rerun <id> --failed`), then re-census. If the population still cannot be reconciled after
one re-run, record `NODE_CENSUS = CANNOT_MEASURE` and treat the round as **not gated** - it does not
count toward the timebox as a completed round, and it cannot support any status claim.

- [ ] **Step 6: Park or advance, honestly**

Append to `docs/audits/2026-08-12-stale-branch-reconciliation.md` section 4: the round number, the
run ID, all three SHAs, the node census, the Sol verdict verbatim (or `CANNOT_MEASURE`), and the
per-finding disposition. Hand W6 the resulting trigger text for #89/#90.

**Acceptance criteria:** #966 stays draft and non-conflicting; each round has a real Actions run
recorded and a Sol verdict recorded; **no GREEN claim for #89/#90 regardless of outcome** - that
requires Sol exact-byte SHIP plus real Windows CI evidence naming all three SHAs.
**Commit boundary:** one commit per repair round, pushed as a fast-forward.
**Rollback:** each round is a separate commit on a draft branch; revert the round's commit. Nothing
on main is at risk.

---

# WAVE 5 - Demand-gated rows: research and disposition

**Rank:** 7 of 8. The measurements have not been taken, but the procedure for taking them and the
decision rule applied to each result are both fixed below. GATE-W5A-1, GATE-W5B-1 and GATE-W5C-1 are
the three points where a result changes the branch taken.

**Requirement refs:** brief section E; board rows #255, DD-006, AST-DSL-PARITY, MCP-LEAN-DEFAULT,
CONTINUOUS-REFRESH; `docs/audits/2026-08-12-research-receipts.md` Part B; design section 6
**Dependency:** none
**Release class:** `docs:` - batched into W8's PR

**Files:**
- Create: `docs/audits/2026-08-13-demand-gated-dispositions.md`

**Allowed paths:** `docs/audits/**`. **Protected paths:** all code. W5 writes no code; a bounded
fix candidate for #255 is a *design*, not an implementation.

### Per-item gates

**W5A - #255 (many-pattern dedup over-count).** The board's own reopen condition is the eligibility
test: `tg scan` ruleset growth past ~100 anchors, **or** a named user with a 100+-pattern workload.
Gate: derive the current ruleset anchor count from the shipped rulesets and record it; record
whether a named user exists. **If neither is satisfied, the honest outcome is DEMAND_GATED with the
demand claim recorded and unmet** - the brief's "user demand now exists" is a claim about the world
and must be substantiated, not assumed. If satisfied: produce a bounded parity-experiment design
(dedup-correct vs current, on a real corpus, with the expected number and noise band predicted
BEFORE the run) - still no code this campaign.
Bidirectional oracle for the eligibility check: the RED arm is an anchor count at or above 100 or a
named user; the GREEN-to-leave arm is both absent. Both must be recorded, not just the one observed.

**W5B - DD-006 (daemon DoS).** Bounded local concurrency measurement only - the box is shared
(A12), so bound the client count and duration and state both. Binary outcome: reproducible bound
failure -> a board row with the reproduction; no failure -> **RETIRED with a receipt**. The
measurement must carry a positive control proving it can produce a non-zero result at all, or its
zero is unresolved rather than clean.

**W5C/D/E - AST-DSL-PARITY, MCP-LEAN-DEFAULT, CONTINUOUS-REFRESH.** Exa **delta** pass only: fresh
2026-08 arXiv findings on top of the 2026-08-12 receipts, which are **reused, not re-derived**. Each
ends in a build/don't-build packet. **MCP-LEAN-DEFAULT stays fenced behind Task 2C regardless of
evidence strength** - re-verify `_TG_MCP_SERVER_CONTRACT_VERSION` is still `1.7.0` on `origin/main`
before any row text asserts it.

### Steps

- [ ] **Step 1: Step 0 - re-derive all five rows' current Status and Trigger from `origin/main`**

```bash
for row in "#255" "DD-006" "AST-DSL-PARITY" "MCP-LEAN-DEFAULT" "CONTINUOUS-REFRESH"; do
  echo "=== $row ==="
  git show origin/main:docs/TASK_BOARD.md | grep -n -- "$row" -A 3
done
```

Record each row's `Status:` and `Trigger:` verbatim. Expected: all five `DEMAND_GATED`. **Any row
that is not DEMAND_GATED is re-dispositioned before the wave proceeds** - a packet written against a
row that already moved is a packet about a fiction.

- [ ] **Step 2: W5A - derive #255's eligibility, both arms recorded**

```bash
git show origin/main:docs/TASK_BOARD.md | grep -n "#255" -A 3          # the reopen condition
# count the shipped ruleset anchors (adapt the glob to the real ruleset location, verified first):
git ls-tree -r --name-only origin/main | grep -i ruleset
```

Count the anchors. Record the number **and** the answer to "is there a named user with a
100+-pattern workload?" - both arms, not just the one that decides.

- [ ] **GATE-W5A-1: the reopen condition, tested rather than assumed**

**Command:** the anchor count from Step 2 plus the named-user answer.

**Trigger:** reopen requires **either** anchor count >= ~100 **or** a named user with a 100+-pattern
workload. The brief asserts "user demand now exists" - that is a claim about the world, not the tree,
and it is substantiated by naming the user or it is not substantiated.

**Re-approval rule:**
- **Neither satisfied** -> outcome is `DEMAND_GATED`, with the demand claim recorded **as made and
  unmet**. This is the honest outcome and it is not a failure of the wave.
- **Either satisfied** -> produce a bounded parity-experiment design (dedup-correct vs current, on a
  real corpus, with the expected number **and its noise band predicted before the run**). Still **no
  code this campaign** - a design is the deliverable, and building it needs its own change-control
  pass.
- **Cannot determine the anchor count** (the ruleset location is not where this plan guessed) ->
  `CANNOT_MEASURE`; record it as such and leave the row DEMAND_GATED. A zero anchor count reached by
  a glob that matched nothing is not evidence of absence.

- [ ] **Step 3: W5B - DD-006's bounded local measurement, with its numbers pinned here**

Bounded because the box is shared (A12). "Bounded" with no numbers is not a measurement plan - it is
an intention, and the next session re-derives the bound and gets a different one. The parameters are
therefore fixed **before** the run, not chosen from the result:

| Parameter | Value | Why this value |
|---|---|---|
| Concurrent clients | **20** | Above the daemon's expected accept backlog, small enough that 20 sockets on a shared box is a rounding error next to one pytest run |
| Wall duration | **60 s**, hard `timeout 60` | One minute is long enough for a bound failure to recur; a hang cannot outlive the shell timeout |
| Request shape | one bounded `tg session`/daemon request per client, looped | Exercises the pre-auth read + socket-timeout posture DD-006 names |
| Ramp | all 20 started, then held | A staggered ramp cannot reproduce a concurrency bound |

**The probe (adapt the client invocation to the real daemon entry point, verified first - do not
assume this plan guessed it):**

```bash
# 0) Instrument identity, before any number is believed.
tg --version
python -c "import tensor_grep, sys; print(tensor_grep.__file__)"

# 1) POSITIVE CONTROL -- prove the probe can report a non-zero failure count at all.
#    Point the same 20 clients at a socket/port that is CLOSED. Expected: 20 failures.
timeout 60 python scripts/dd006_probe.py --clients 20 --duration 60 --target closed --json \
  > artifacts/dd006_control.json

# 2) THE MEASUREMENT -- same 20 clients, same 60s, against the live daemon.
timeout 60 python scripts/dd006_probe.py --clients 20 --duration 60 --target live --json \
  > artifacts/dd006_measure.json
```

The probe script is written by this wave if it does not exist; it is a **local measurement harness,
not product code**, and it is not committed to `src/` (W5's allowed paths are `docs/audits/**` - the
probe lives in the wave's scratch and its OUTPUT is what lands in the disposition doc).

**Evidence thresholds, fixed before the run:**

- **Control valid** iff `dd006_control.json` reports **failures == 20** (every client failed against
  a closed target). Any other control value - including a partial count - means the probe cannot
  distinguish success from failure and **no measurement number may be quoted from that run**.
- **Bound failure REPRODUCED** iff the live run reports **>= 1 refused/dropped/timed-out client in
  at least 2 of 2 independent runs**. One occurrence is a flake candidate, not a finding.
- **Null** iff control == 20 **and** both live runs report **0** failures across all 20 clients for
  the full 60 s.
- Anything else - control invalid, runs disagreeing, the probe erroring - is **CANNOT_MEASURE**, and
  GATE-W5B-1's third branch applies.

Both runs' raw JSON, both counts, and the control's count go into
`docs/audits/2026-08-13-demand-gated-dispositions.md` verbatim. **A zero is reported only beside its
control's non-zero**; a bare zero in that doc is exactly the artifact this repo has been burned by
most.

- [ ] **GATE-W5B-1: a zero is only evidence if the control fired**

**Command:** Step 3's two invocations - the closed-target positive control and the live measurement -
with Step 3's fixed thresholds (control failures == 20; a finding needs >= 1 failure in 2 of 2 live
runs; a null needs 0 failures across 20 clients for the full 60 s in both).

**Trigger:** control produced its expected non-zero (20) AND the measurement produced zero -> the
null is real. Control produced anything else -> the instrument is the finding, not the subject.

**Re-approval rule:**
- Reproducible bound failure (2 of 2 runs) -> file a board row with the reproduction; DD-006 stays
  open with that row as its trigger.
- Control fired, no failure -> **RETIRED with a receipt** (board rule 4: a documented retirement is
  worth as much as a fix).
- Control did not fire -> `CANNOT_MEASURE`; DD-006 stays `DEMAND_GATED` and the receipt says the
  measurement never ran. **Do not report the zero.**

- [ ] **Step 4: W5C/D/E - the Exa delta pass**

Fresh 2026-08 findings **on top of** the 2026-08-12 receipts in
`docs/audits/2026-08-12-research-receipts.md` Part B, which are **reused, not re-derived**. Each row
ends in a build/don't-build packet. If the Exa tools are not loaded, search the deferred-tool list
before declaring them unavailable.

- [ ] **GATE-W5C-1: MCP-LEAN-DEFAULT's fence is a sequencing constraint, not an evidence question**

**Command:**

```bash
git show origin/main:src/tensor_grep/cli/mcp_server.py | grep -n "_TG_MCP_SERVER_CONTRACT_VERSION"
```

**Trigger:** the value must read `"1.7.0"` before any row text asserts it.

**Re-approval rule:** if it reads anything else, the contract moved - **do not** write `1.7.0` into
the packet from this plan's memory. Record the observed value and re-check whether the Task 2C fence
still holds. **No strength of industry-direction evidence unfences this row**; the fence is the
MCP-SURFACE ladder, and evidence does not resequence a ladder.

**Acceptance criteria:** five packets, each with the reopen condition restated and the evidence
checked against it - **including when the evidence fails to satisfy it**, and including any
`CANNOT_MEASURE` outcome recorded as such rather than collapsed into a null. Status changes only
where the packet justifies one, and any change is carried in-body per A71.
**Commit boundary:** one commit, one file, folded into W8's PR.
**Rollback:** delete the doc; no board row changes until W8.

---

# WAVE 6 - Blocked rows: status only, no fake progress

**Rank:** 8 of 8 (ordering); executed alongside W8. The step content **is** the per-row command table
below - six rows, six commands, six recorded results - plus GATE-W6-1 for the case where a
prerequisite turns out to be satisfied.

**Requirement refs:** brief section F; board rows #89, #90, F5, F6, F8, MCP-SURFACE;
`docs/audits/2026-08-12-stale-branch-reconciliation.md` section 2
**Dependency:** W4 (for #89/#90's advancement text)
**Release class:** `docs:` - part of W8's PR

**Files:** `docs/TASK_BOARD.md` row text only.
**Allowed paths:** `docs/TASK_BOARD.md`. **Protected paths:** all code.

**A98 is the binding constraint: one command per row, one recorded result per row. No row's
disposition may be inferred from a sibling's.**

| Row | Prerequisite to re-derive | Expected |
|---|---|---|
| #89 | Task 2A program state after W4 | BLOCKED; trigger cites W4's round receipts |
| #90 | same program (scan half); doctor half shipped #571 | BLOCKED |
| F5 | Task 8 Steps 3-5 touch `rust_core/**` + `tests/e2e/**` | BLOCKED (shared-box ban -> CI/cloud) |
| F6 | **MIXED per A41** - Python/schema/evidence-signing slices buildable-first; native verify-edit + e2e halves CI/cloud-routed | BLOCKED, **both halves carried in the trigger text** |
| F8 | Tasks 12-13 on `main.rs` + `path_domain.rs` + e2e routing parity | BLOCKED |
| MCP-SURFACE | `git show origin/main:src/tensor_grep/cli/mcp_server.py \| grep _TG_MCP_SERVER_CONTRACT_VERSION` | `"1.7.0"`; BLOCKED behind Task 2C |

**Bidirectional oracle:** for each row, the RED arm is the prerequisite being satisfied - e.g.
MCP-SURFACE's contract version reading anything other than `1.7.0` would mean the fence moved and
the row must be re-dispositioned. Record the observed value, not just "still blocked".

### Steps

- [ ] **Step 1: Global constraint 14's live-state gate** (index + each row's `Status:` from
  `origin/main`, recorded verbatim) before any board edit.

- [ ] **Step 2: Run the six commands from the table above, one per row, recording each output**

Six separate invocations, six separate recorded results. **A98 is binding**: the 2026-08-01 census
was wrong four times in a row, every time by reasoning "A transitively covers B". #89 and #90 share a
program and still get their own command and their own result.

- [ ] **Step 3: Write each row's trigger text from its own recorded output**

Every trigger names the exact prerequisite and the exact next condition. #89/#90's text comes from
W4 Step 6's receipt (round number, run ID, three SHAs, node census, Sol verdict). F6 carries **both**
halves per A41 - do not flatten `buildable-first + CI-routed` into one word.

- [ ] **GATE-W6-1: a prerequisite that turns out to be SATISFIED**

**Command:** each row's prerequisite command from the table.

**Trigger:** the observed value matching the "Expected" column means BLOCKED stands. Any other value
means the prerequisite may have been satisfied while nobody was looking - the A75 pattern, where six
of six ready-to-build items were already shipped.

**Re-approval rule on FAIL:** do **not** flip the Status inside this wave. Record the observed value,
open the re-disposition explicitly (what changed, what the row would become), and route it through
W8 with the receipt attached. A status flip computed inside a "status only, no fake progress" wave is
exactly the silent change this wave exists to prevent.

**Acceptance criteria:** six rows, six commands, six recorded results; every trigger names the exact
prerequisite and the next-trigger text; **zero status flips** (a justified re-disposition goes
through W8 with its receipt, never inline here).
**Rollback:** `git checkout -- docs/TASK_BOARD.md` in the W8 worktree.

---

# WAVE 7 - CEO decision packets (no flips)

**Rank:** with W8. #48's and #77's packets need thinktank output that does not exist yet; the packet
*structure*, the delta each row gets, and the terminator check are all fixed below, and GATE-W7-1
handles the case where the council does not return.

**Requirement refs:** brief section G; board rows #48, #72, #77, #131, #169;
`docs/audits/2026-08-06-ceo-gated-recommendation-packets.md`; design section 7
**Dependency:** W5 (for #72's evidence delta)
**Release class:** `docs:` - part of W8's PR

**Files:**
- Create: `docs/audits/2026-08-13-ceo-gated-packets.md`

**Allowed paths:** `docs/audits/**`. **Protected paths:** all code; `docs/TASK_BOARD.md` Status
values for these five rows (they stay `CEO_GATED`).

| Row | Deliverable | Hard constraint |
|---|---|---|
| #48 | Thinktank-recommended option + **reversible** implementation proposal for the architectural remainder | Marked "requires CEO decision"; no rewrite authorized |
| #72 | Packet with evidence + recommendation | The 2026-08-12 competitor token-reduction receipts strengthen the case, **not** the gate. HOLD any public multiplier (7.5x / 6.4x conflict unresolved) |
| #77 | Thinktank-recommended option + reversible implementation proposal for ledger enforcement scope | Standing recommendation is local opt-in advisory only; no auth/CI blocking gate |
| #131 | Packet with evidence + recommendation | Still downstream of #169; no asset publish |
| #169 | **Pointer only** | **No packet, no recommendation, no spend.** The only money stop. A recommendation on a money stop reads as a nudge |

**Bidirectional oracle:** the packet gate is textual and mechanically checkable. Every one of the
five sections must end with the literal string `STATUS REMAINS CEO_GATED`, and the board Status for
all five must still read `CEO_GATED` after W8. The RED arm is any section missing that string or any
row whose Status changed - either is a silent flip and blocks the campaign.

### Steps

- [ ] **Step 1: Step 0 - re-read the five standing packets and the five board rows**

```bash
git show origin/main:docs/audits/2026-08-06-ceo-gated-recommendation-packets.md | grep -n "^## "
for row in "#48" "#72" "#77" "#131" "#169"; do
  echo "=== $row ==="; git show origin/main:docs/TASK_BOARD.md | grep -n -- "$row" -A 3
done
```

Expected: five standing packets present, five rows `CEO_GATED`. The 2026-08-06 packets are the base;
this campaign writes a **delta**, not a rewrite.

- [ ] **Step 2: Draft the four delta packets** (#48, #72, #77, #131) per the table above, each ending
  with the literal string `STATUS REMAINS CEO_GATED`.

- [ ] **Step 3: Write #169 as a pointer only** - no packet body, no recommendation, no spend option
  presented. A recommendation on a money stop reads as a nudge, which is the one thing a money stop
  must not do.

- [ ] **GATE-W7-1: a thinktank seat that does not return**

**Command:** the council dispatch for #48's and #77's recommended-option sections.

**Trigger:** a seat returns a verdict, or it does not. A verdict printed **beside** a
`CANNOT_READ_REQUIRED_FILE` (or equivalent read failure) is **discarded**, not counted as a dissent -
the 2026-08-01 receipt where a dead seat's `RECOMMENDED: REJECT` was nearly read as a real rejection.

**Re-approval rule on FAIL:** a no-verdict seat is a **FAILED seat, not a blocker** (A10). Synthesize
from the surviving seats and record how many seats returned. If **zero** seats return, the packet
ships with the 2026-08-06 standing recommendation carried forward **unchanged**, explicitly labelled
"no 2026-08-13 council input; standing recommendation carried" - which is honest, and is not the same
as inventing a fresh recommendation with no council behind it.

- [ ] **Step 4: Mechanical terminator check before commit**

```bash
grep -c "STATUS REMAINS CEO_GATED" docs/audits/2026-08-13-ceo-gated-packets.md   # expect 5
```

Expected: `5`. **This check has a false-green mode worth naming:** `grep -c` counts *lines*, so five
occurrences inside one section would also print 5. Confirm the five hits fall in five different
sections (`grep -n` and read the line numbers against the section headers) rather than trusting the
count alone.

**Acceptance criteria:** five sections; the literal terminator on each, verified per-section and not
by count alone; #169 carries no recommendation; zero Status changes on the board for these rows.
**Commit boundary:** one commit, one file, folded into W8's PR.
**Rollback:** delete the doc; no board change.

---

# WAVE 8 - Docs closeout and board reconcile

**Rank:** last. The row *text* comes from W2-W7's outcomes; the *edits*, their order, the exact new
row's format, and the gates that must pass are all fixed below. GATE-W8-1 and GATE-W8-2 are where
outcomes enter.

**Requirement refs:** brief section H; A71 (parser-legal rows), A76 (freshness is ordinal CHANGELOG
distance), A79 (status-stamp PRs must retarget governance pins)
**Dependency:** **W2 and W3 both merged, and each either published or parked at `IN_FLIGHT`
under GATE-W8-1**; W4 parked or advanced; W5-W7 docs written
**Release class:** `docs:` - does NOT publish. Opens only after both release-bearing PRs have
merged (WIP cap).

**Files:**
- Modify: `docs/TASK_BOARD.md` (index -> `2026-08-13.1`; campaign note; row text; the new
  `RUST-REPLACE-TOCTOU` row)
- Modify: `docs/BACKLOG.md` (dated entries: the refuted premise, A101, RUST-REPLACE-SYMLINK, each
  demand disposition)
- Modify: `docs/SESSION_HANDOFF.md`
- Modify: `tests/unit/test_backlog_tracker_truth.py` (**the only non-`docs/` file this wave touches**)
  - `EXPECTED_IDS` gains `RUST-REPLACE-TOCTOU`; `DEMAND_IDS` gains it too; `DEMAND_IDS` loses
    `DD-006` only if W5B retired it. The closed world is held as literal sets in this file, so a new
    board row that is not added here fails the parser test with `closed-world population drift`.
    Step 4a carries the re-derivation and the measured current values.
- Add: the W5 and W7 docs (W1's receipt lands via its own PR in W1B Step 6 and must already be on
  `origin/main` by now - verify with `git show origin/main:docs/audits/2026-08-13-stranded-work-premise-recheck.md | head -3`
  rather than re-adding it)

**`AGENTS.md` is NOT edited by this wave, or by this campaign.** The A-laws cited throughout these
two artifacts - through **A102** - already exist on `origin/main` (enumerated, not grep-counted).
Design section 8.5 states this; it is repeated here because W8 is the wave that would otherwise be
the natural place to "just add a law", and a law minted by a campaign's own author at the end of that
campaign has survived no second session. The single permitted exception: if this wave's reconcile
turns up an `AGENTS.md` line whose `file:line` anchor no longer resolves, correct **that one line**
and name the correction in the PR body. Adding a new law is out of scope.

**Allowed paths:** `docs/**` **plus exactly one test file**,
`tests/unit/test_backlog_tracker_truth.py` (closed-world constants only - the `EXPECTED_IDS` /
`DEMAND_IDS` sets, no assertion logic). **Protected paths:** all of `src/**`, all of `rust_core/**`,
every other file under `tests/`, `.github/workflows/**`.

**Release class is unchanged by that widening:** the PR title stays `docs:`, which publishes nothing.
A `docs:`-titled PR touching a test file is legal here - the title governs the release intent, not
the diff's file extensions - and the diff is one constant set in a governance test that exists to
mirror the board. Anything beyond those two constants belongs in a different PR.

**Board mechanics, binding:**
- Rows under `## Canonical status index` accept **only** `Status:` / `PR:` / `Trigger:` keys.
  Free-form bullets there are illegal (A71) and the parser test will fail.
- Governance tests must be re-targeted, not worked around (A79). There are **two distinct** ones and
  they are easy to conflate: `tests/unit/test_backlog_tracker_truth.py` is the **closed-world row
  parser** (`ROW_RE`, `EXPECTED_IDS`, `DEMAND_IDS`, `CEO_IDS`, `LIFECYCLE_IDS`) and
  `tests/unit/test_task_board_freshness.py` is the **CHANGELOG-distance freshness** check. A new row
  is the first one's business; the release stamps are the second's. Plus the skill/index sync tests.
- Freshness is **ordinal CHANGELOG distance**, not patch subtraction (A76) - after W2 and W3 publish,
  main is two releases ahead of `v1.110.14`, and the stamps must reflect that.
- The closed-world section must enumerate **all 28 rows plus `RUST-REPLACE-TOCTOU` = 29** (A45).

**Expected terminal state after this campaign:**

| Bucket | Before (index `2026-08-12.1`) | After (index `2026-08-13.1`) |
|---|---|---|
| SHIPPED | 7 | 8 (RUST-REPLACE-SYMLINK joins) - or 7 when that row is the `IN_FLIGHT` one below (GATE-W8-1) |
| RETIRED | 4 | 4, or 5 if DD-006 retires |
| READY | 1 | 0 |
| IN_FLIGHT | 0 | 0 - or 1, holding RUST-REPLACE-SYMLINK, only if its release merged but has not published (GATE-W8-1) |
| BLOCKED | 6 | 6 |
| CEO_GATED | 5 | 5 (unchanged - packets, not flips) |
| DEMAND_GATED | 5 | 5, or 4 if DD-006 retires; **+1 for `RUST-REPLACE-TOCTOU`** |
| **Total** | **28** | **29** |

### Steps

- [ ] **Step 1: Global constraint 14's live-state gate, run immediately before the first edit**

```bash
git fetch origin
git show origin/main:docs/TASK_BOARD.md | grep -n "Canonical status index" -A 4
git show origin/main:docs/TASK_BOARD.md | grep -n "RUST-REPLACE-SYMLINK" -A 3
```

Record the index and the row Status verbatim. Asserted premise: index `2026-08-12.1`,
`RUST-REPLACE-SYMLINK` `Status: READY`. **On mismatch, BLOCK** and re-derive - a concurrent campaign
may have moved the board, and an edit computed against a superseded index silently reverts it.

- [ ] **Step 2: Create the worktree off current `origin/main`** (which by now includes W1's, W2's and
  W3's merges - so re-fetch first, and do not reuse an earlier wave's worktree).

```bash
git worktree add .claude/worktrees/w8-docs-closeout -b docs/2026-08-13-closeout origin/main
git -C .claude/worktrees/w8-docs-closeout rev-parse --abbrev-ref HEAD   # expect: docs/2026-08-13-closeout
```

If the path already exists from a prior session, do NOT remove it blindly - a live agent may own
it (A23/A24). Check first, then reuse or replace:

```bash
git -C .claude/worktrees/w8-docs-closeout status --porcelain   # empty => clean
git -C .claude/worktrees/w8-docs-closeout rev-parse --abbrev-ref HEAD   # expect docs/2026-08-13-closeout
```

Reuse only when BOTH hold (clean AND on `docs/2026-08-13-closeout`) after a
`git -C .claude/worktrees/w8-docs-closeout fetch origin` + rebase onto `origin/main`. Only a
clean, stale worktree of that same branch may be `git worktree remove --force`d before recreating
it (the remove-before-checkout rule from the worktree-fanout verification gate). On any dirty
tree or mismatched branch, STOP, record the observation, and create
`.claude/worktrees/w8-docs-closeout-2` instead - never delete another session's files.

- [ ] **GATE-W8-1: only claim SHIPPED for what actually published**

**Command:**

```bash
gh pr view <W2 PR#> --json state,mergedAt,mergeCommit
gh pr view <W3 PR#> --json state,mergedAt,mergeCommit
gh run list --workflow=ci.yml --branch=main --limit 10 --json databaseId,status,conclusion,headSha
pip index versions tensor-grep 2>/dev/null | head -3    # or the equivalent PyPI check
```

**Trigger:** a row reads SHIPPED only when the PR is merged **and** the release published. Merged is
not released; a `cancelled` main run can silently eat a release (2026-08-05: one evicted lane, a
`fix:` PR that merged and never published).

**Re-approval rule on FAIL:** if the merge landed but nothing published, **do not write SHIPPED - in
any form.** An earlier revision of this plan prescribed
`SHIPPED (merged <sha>; release pending <run-id>)`, which is a false-SHIPPED state: every tracker,
grep, and per-bucket count keys on the status token, and `SHIPPED` with a parenthetical is still
`SHIPPED` to all of them. The board has a status for exactly this - **`IN_FLIGHT`** - and the row
takes it:

```
Status: IN_FLIGHT; PR: PR #<n>; Trigger: merged <sha>; run <id>; not on PyPI as of <UTC timestamp>. <the diagnosed failure class and the recovery applied>. Reopen on: PyPI serving the version, then flip to SHIPPED with the closure receipt.
```

Two parser facts to honour rather than discover (`tests/unit/test_backlog_tracker_truth.py`,
`ROW_RE` + `PR_STATUSES` + `TERMINAL`): `IN_FLIGHT` is in `PR_STATUSES`, so the `PR:` field **must**
read a literal `PR #<n>` (not `none`); and `IN_FLIGHT` is **not** in `TERMINAL`, so the checkbox
stays **`- [ ]`** unchecked. A checked box with a non-terminal status is a hard parser failure
(`checkbox/status disagreement`).

**Diagnose the failure class before touching anything** - the two look identical from
"merged, not published" and have opposite recoveries:

```bash
gh run view <run-id> --json jobs \
  --jq '.jobs[]|select(.name|test("Semantic Release|publish-pypi|publish-success"))|"\(.name): \(.conclusion)"'
gh run view <run-id> --log-failed | grep -nE "rejected|main -> main|already been released"
```

- **`! [rejected]  main -> main`** -> the A33 semantic-release **push-race**: another merge landed
  inside the ~6-minute native-asset build window. **Do NOT rerun it.** It self-heals - the next
  push to `main` recomputes the version from tags and folds the orphaned commit in. The recovery is
  to merge nothing else and wait for the next push, or make one. A blind `gh run rerun` here re-runs
  a job whose premise (its base ref) has already moved.
- **a `cancelled` lane / a publish tail that never ran** -> the 2026-08-05 class (one evicted
  `test-python` lane marked a whole main run cancelled and ate a `fix:` release). Recovery **is**
  `gh run rerun <id> --failed`, then re-check.
- **anything else** -> read the failing job's log before acting; an undiagnosed rerun is a coin flip.

Transition to `SHIPPED` **only** after PyPI serves the version, verified by membership on the exact
version rather than by `info.version` (which is CDN-cached and has read stale immediately after a
publish):

```bash
python - <<'PY'
import json, urllib.request
req = urllib.request.Request("https://pypi.org/pypi/tensor-grep/json",
                             headers={"Cache-Control": "no-cache"})
data = json.load(urllib.request.urlopen(req))
print("info.version:", data["info"]["version"])
print("<X.Y.Z> present:", "<X.Y.Z>" in data["releases"])   # ask for YOUR version by name
PY
```

If the campaign ends with the row still `IN_FLIGHT`, that is an honest terminal state for this
campaign and W8 says so. Rounding it up to SHIPPED is the failure this rule exists to prevent.

- [ ] **Step 3: Update the index, the campaign note, and each row's text** from W2-W7's recorded
  outcomes. ASCII-only in new prose (A96); edit existing governed lines by line index with an
  assertion, never by quoting a line that may carry an em dash.

- [ ] **Step 4: Insert the `RUST-REPLACE-TOCTOU` row, parser-legal**

Rows under `## Canonical status index` accept **only** `Status:` / `PR:` / `Trigger:` keys; a
free-form bullet there is illegal (A71) and the parser test fails. The **whole line** - checkbox,
bolded ID, separator, three keys - is the unit `ROW_RE` matches, and an earlier revision of this plan
quoted only the key half, which would have failed as `malformed or multiline canonical row`:

```
- [ ] **RUST-REPLACE-TOCTOU** — Status: DEMAND_GATED; PR: none; Trigger: close the residual TOCTOU left open by RUST-REPLACE-SYMLINK -- the leaf-swap window between symlink_metadata and the writer's open, pinned OPEN by the characterization pin in rust_core/src/backend_cpu.rs. Needs O_NOFOLLOW (POSIX) plus FILE_FLAG_OPEN_REPARSE_POINT (Windows) at the open site, or handle-based reopen. Carries the GATE-W3A-1 Windows-junction outcome. Reopen on: a tg CLI command wiring replace_in_place to user input, or a reported exploit attempt. See docs/design/2026-08-13-replace-in-place-symlink-threat-model.md.
```

**Three mechanical properties of that line, each one a hard parser failure if missed** (source:
`tests/unit/test_backlog_tracker_truth.py`, `ROW_RE` and the checks under it):

1. The separator after `**ID**` is a literal **em dash** `—`, not a hyphen. This is the one place in
   this campaign where A96's ASCII-only rule does **not** apply, because the parser regex requires
   the non-ASCII character. Copy the separator byte-for-byte from a neighbouring row; do not retype
   it.
2. `DEMAND_GATED` is not in `PR_STATUSES`, so the field **must** read exactly `PR: none`. It is not
   in `TERMINAL` either, so the checkbox **must** be `- [ ]`.
3. The trigger is one line and must be non-empty and not the literal `none`.

**Before writing it, re-read a neighbouring row and match its shape exactly** - the file on `main` is
the authority over this plan's transcription.

- [ ] **Step 4a: Update the closed-world constants the parser test enforces**

`tests/unit/test_backlog_tracker_truth.py` holds the closed world as literal sets, so a new row is a
**two-file edit**: adding it to the board alone fails with `closed-world population drift:
extra=['RUST-REPLACE-TOCTOU']`. Re-derive the current sets rather than trusting this plan
(the working checkout is stale and its copy of this file already disagrees with `origin/main`):

```bash
git show origin/main:tests/unit/test_backlog_tracker_truth.py | grep -n "EXPECTED_IDS = {" -A 32
git show origin/main:tests/unit/test_backlog_tracker_truth.py | grep -n "^DEMAND_IDS = {" -A 8
```

Measured on `origin/main` at authoring time (2026-08-13) - **verify, do not assume**:

| Constant | Observed on `origin/main` | Edit required by this campaign |
|---|---|---|
| `EXPECTED_IDS` | 28 ids, **includes** `RUST-REPLACE-SYMLINK`, **excludes** `RUST-REPLACE-TOCTOU` | **ADD `"RUST-REPLACE-TOCTOU"`** -> 29 |
| `DEMAND_IDS` | 5 ids: `#255`, `DD-006`, `AST-DSL-PARITY`, `MCP-LEAN-DEFAULT`, `CONTINUOUS-REFRESH` | **ADD `"RUST-REPLACE-TOCTOU"`** (it ships DEMAND_GATED, and `_assert_status_ownership` asserts set EQUALITY, so an omission fails) |
| `CEO_IDS` | 5 ids | unchanged - W7 flips nothing |
| `LIFECYCLE_IDS` | `PROGRAM_OWNERS` keys + `#89`/`#90`/`#859` | unchanged - neither `RUST-REPLACE-*` row is a lifecycle id, so neither needs `Implementation PRs:` / `Closure PR:` / `Merged SHA:` in its trigger |

**The `RUST-REPLACE-SYMLINK` READY -> SHIPPED transition needs NO constant change**, and this is
stated explicitly because the round-2 finding assumed it did: the id is **already** in
`EXPECTED_IDS` (membership is status-independent) and is **absent** from `DEMAND_IDS` on
`origin/main`, so flipping its status touches neither set. Re-run the two greps above before relying
on that - the local working tree's copy of this file *does* carry `RUST-REPLACE-SYMLINK` in
`DEMAND_IDS`, which is exactly why the check reads `origin/main` and not the checkout.

**If DD-006 retires** (W5B's null branch), it also comes **out** of `DEMAND_IDS` - a `RETIRED` row is
terminal and `_assert_status_ownership` would otherwise fail on the leftover member. That is a
conditional edit gated on W5B's outcome, not a default.

Adding this file to the wave's diff widens W8 beyond `docs/**`; the Files and Allowed-paths sections
above are written to permit exactly this one test file and nothing else in `tests/`.

- [ ] **Step 4b: Prove parser legality rather than assuming it**

```bash
timeout 300 uv run --no-sync python -m pytest tests/unit/test_backlog_tracker_truth.py -q -rA --timeout=60
timeout 300 uv run --no-sync python -m pytest tests/unit/ -k "backlog_tracker or task_board" -q -rA --timeout=60
```

**The first command is the one that matters, and an earlier revision ran the wrong test.** `-k
task_board` matches `tests/unit/test_task_board_freshness.py` - a **freshness** test about
CHANGELOG distance - and matches the parser test not at all, because that file is named
`test_backlog_tracker_truth.py`. A green `-k task_board` run therefore says nothing whatever about
row legality or the closed world: it is a pass from a test that never looked. The parser test is
named by **path**; the `-k` line is a sibling sweep on top, not a substitute.

Expected: PASS, with a **non-zero** collected count on both - a `-k` filter that matches nothing
exits 0 and reads exactly like a pass. If the path-named command reports "no tests ran", the module
moved: find it (`git ls-files 'tests/unit/*backlog_tracker*' 'tests/unit/*task_board*'`) and run the
real path. **A zero-test pass is not a pass.**

- [ ] **GATE-W8-2: the closed world is enumerated, not sampled**

**Command:** count the rows under `## Canonical status index` in the edited file and reconcile
against the bucket table below.

**Trigger:** 28 pre-existing rows + `RUST-REPLACE-TOCTOU` = **29**, and the per-bucket counts sum to
29. A45 requires the closed world be stated, not sampled.

**Re-approval rule on FAIL:** a count that disagrees is re-derived by listing every row ID and
diffing against design section 3's enumeration - **not** by adjusting the total to match the count.
This repo's row/skill/language counts have been wrong in a published doc four separate times, every
time by trusting a remembered number over a re-derivation.

- [ ] **Step 5: Full gate, PR, merge in a green gap**

```bash
uv run ruff check .
uv run ruff format --check --preview .
uv run mypy src/tensor_grep
timeout 1800 uv run pytest -q --ignore=tests/e2e/test_routing_parity.py
```

Then open the `docs:` PR (non-releasing), watch CI, and merge only with no run in flight on main
queried **by run ID**.

**Gates:** four-step local gate (pytest scoped per global constraint 5);
`tests/unit/test_backlog_tracker_truth.py` run **by path** with a non-zero collected count; PR CI
green; merge in a green gap.
**Acceptance criteria:** index reads `2026-08-13.1`; all 29 rows enumerated; the
`RUST-REPLACE-TOCTOU` row is parser-legal (em-dash separator, `PR: none`, unchecked box) and proven
so by a **path-named** `test_backlog_tracker_truth.py` run that collected more than zero tests;
`EXPECTED_IDS` and `DEMAND_IDS` both carry the new row and the sets were re-derived from
`origin/main` rather than from this plan; every unfinished row's trigger names its exact next
condition; the `RUST-REPLACE-SYMLINK` row claims exactly what W3B built and no more - **and reads
`IN_FLIGHT` rather than `SHIPPED` if its release has not published** (GATE-W8-1); no CEO_GATED Status
changed; the A101 recurrence count (3x) recorded in BACKLOG.
**Commit boundary:** one `docs:` commit.
**Rollback:** `git revert` the docs commit. No code or contract is affected.

---

## Traceability matrix

| Board row / requirement | Wave item | Test | Implementation file |
|---|---|---|---|
| Brief A: H3 stranded (REFUTED) | W1A | `git cherry` + `python_sidecar.rs:757,1496` receipts | `docs/audits/2026-08-13-stranded-work-premise-recheck.md` |
| Brief A: H6 stranded (REFUTED) | W1A | `cudf_backend.py:325-336`, `test_cudf_backend.py:699` | same |
| Branch/worktree cleanup | W1B | `git merge-base --is-ancestor` per branch | same (PROPOSED section) |
| A101 probe flake | W2A | `test_run_check_retries_a_timed_out_probe_before_failing` | `scripts/agent_readiness.py` (`Check`, `run_check`) |
| A101 retry must be bounded | W2A | `test_run_check_does_not_retry_when_retry_on_timeout_is_zero` | `scripts/agent_readiness.py::run_check` |
| A101 `attempts` on the early-return path | W2A step 10a | `test_run_check_reports_zero_attempts_when_the_command_is_unavailable` | `scripts/agent_readiness.py::run_check` |
| A101 probe budgets | W2A | `test_shell_version_probes_carry_a101_timeout_budget_and_retry` | `scripts/agent_readiness.py::build_check_plan` |
| RUST-REPLACE-SYMLINK (threat model) | W3A | council citation check vs `origin/main` | `docs/design/2026-08-13-replace-in-place-symlink-threat-model.md` |
| RUST-REPLACE-SYMLINK (guard) | W3B | `replace_in_place_refuses_to_follow_a_symlinked_file_target` | `rust_core/src/backend_cpu.rs::replace_in_place` |
| RUST-REPLACE-SYMLINK (control) | W3B | `replace_in_place_still_rewrites_a_regular_file` | same |
| RUST-REPLACE-SYMLINK (dir pin) | W3B | `replace_directory_mode_skips_symlinked_entries` | `walk_directory_entries` (pinned, unchanged) |
| RUST-REPLACE-SYMLINK (fail-closed stat) | W3B step 4a | `test_replace_in_place_fails_closed_when_symlink_metadata_errors` + `..._rewrites_normally_when_no_metadata_fault_is_injected` | `backend_cpu.rs::replace_in_place` guard + `ReplaceFaultInjection` |
| RUST-REPLACE-SYMLINK (missing-path contract) | W3B step 4a | `test_replace_in_place_on_a_missing_path_still_errors_with_the_path_named` | same |
| RUST-REPLACE-SYMLINK (A38/A48 residual-race **characterization pin**, not a RED) | W3B step 4b | `test_replace_in_place_leaf_swapped_between_guard_and_open_characterizes_the_residual_window` | `backend_cpu.rs` `#[cfg(test)]` `SwapGate` handshake seam |
| Windows junction disposition | W3A GATE-W3A-1 / W3B acceptance | council answer + shipped-bytes match | threat model + code comment + board Trigger |
| RUST-REPLACE-TOCTOU (new row) | W3B step 11 / W8 steps 4, 4a, 4b | `tests/unit/test_backlog_tracker_truth.py` run **by path**: row parser legality + closed-world set equality, non-zero collected count | `docs/TASK_BOARD.md` + `tests/unit/test_backlog_tracker_truth.py` (`EXPECTED_IDS`, `DEMAND_IDS`) |
| #89 | W4, W6 | Task 2A per-node census vs 169-node manifest | draft PR #966 branch |
| #90 | W4, W6 | same | same |
| #255 | W5A | ruleset anchor count + named-user check | `docs/audits/2026-08-13-demand-gated-dispositions.md` |
| DD-006 | W5B | bounded concurrency measurement + positive control | same |
| AST-DSL-PARITY | W5C | Exa delta | same |
| MCP-LEAN-DEFAULT | W5D | `_TG_MCP_SERVER_CONTRACT_VERSION` == `1.7.0` | same |
| CONTINUOUS-REFRESH | W5E | Exa delta | same |
| F5 / F6 / F8 / MCP-SURFACE | W6 | one prerequisite command per row | `docs/TASK_BOARD.md` |
| #48 / #72 / #77 / #131 | W7 | `STATUS REMAINS CEO_GATED` terminator present | `docs/audits/2026-08-13-ceo-gated-packets.md` |
| #169 | W7 | pointer only; no recommendation text | same |
| Board reconcile | W8 | `tests/unit/test_backlog_tracker_truth.py` (A71 row parser + closed world) **and** `tests/unit/test_task_board_freshness.py` (A76 CHANGELOG distance) - two different tests, both required | `docs/TASK_BOARD.md`, `docs/BACKLOG.md`, `docs/SESSION_HANDOFF.md`, `tests/unit/test_backlog_tracker_truth.py` |
| Release actually published before any SHIPPED claim | W8 GATE-W8-1 | `gh run view --json jobs` failure-class diagnosis + PyPI `releases` membership on the exact version | `docs/TASK_BOARD.md` (`IN_FLIGHT` until PyPI serves it) |

---

## What completion means

Completion is **not** "17 rows closed". It is: all 29 rows carry an honest, receipted terminal
state, and the index reads `2026-08-13.1`.

- **SHIPPED** = PR number **and** merged SHA **and**, for `fix:`/`feat:`/`perf:`, a published
  artifact. Committed is not shipped; merged is not released.
- **BLOCKED** = the exact prerequisite plus next-trigger text. No fake progress. #89/#90 stay
  BLOCKED whatever W4 finds, absent Sol exact-byte SHIP plus real Windows CI evidence.
- **DEMAND_GATED** = the reopen condition restated with the evidence checked against it, including
  when the evidence fails to satisfy it.
- **RETIRED** = the reason, in full. A documented retirement is worth as much as a fix.
- **CEO_GATED rows complete as decision packets, not flips.** #48, #72, #77, #131 each get a
  recommendation and stay `CEO_GATED`. #169 gets a pointer with **no** recommendation - it is the
  only money stop, and a recommendation on a money stop reads as a nudge.
- **Refuted premises complete as receipts.** W1 is the worked example: the brief's highest-priority
  build work completes as a published verification receipt, because the code is already shipped and
  that is what honest completion looks like.

---

## Risks

| Risk | Mitigation |
|---|---|
| **CI-only Rust verification latency** (W3): no local compile; A87 says the first real CI run commonly finds compile errors static review missed | Budget 2 CI rounds before escalating; `rustfmt --check` locally; never treat a static SHIP as clearance; the RED arm is pushed separately so CI proves both arms |
| **Push-race**: `Semantic Release` runs ~6 min; any merge in that window rejects the in-flight push | Merge W2, wait for the `chore(release)` commit **and** PyPI, then merge W3, then W8's docs PR. Query by run ID, never `tag == PyPI` |
| **WIP cap**: 2 release-bearing PRs + draft #966 + a docs PR | Hard cap 3 open; W8's PR opens only after W2 and W3 have merged |
| **The dirty checkout**: 21 modified + untracked entries of another session's work | Explicit-path staging; `git diff --cached --name-status` before every commit; **no `git stash`**; all work in fresh worktrees; W1B's cleanup is PROPOSED, never executed |
| **Task 2A timebox**: the repair loop has consumed whole campaigns before | Hard 2 rounds, live CI per round; park honestly with receipts; board stays BLOCKED |
| **A97**: an interrupted edit may have already applied - this campaign edits `docs/TASK_BOARD.md`, which has been duplicated by blind retries before | Read the file back after any interrupted or ambiguous tool result. Never re-apply blind |
| **A98**: W6 restates six BLOCKED rows | One command per row, one recorded result per row. No inference from a sibling |
| **A96**: governed docs carry em dashes; byte-exact edits fail while text looks identical | New prose ASCII-only; edit existing lines by line index with an assertion |
| **Further refuted premises**: section 2 found one; A75 found six in one pass | Step 0 on every item. A refuted item is re-dispositioned and reported, never silently skipped |
| **W3 behavior change with zero measured consumers**: reachability was measured, but a measurement can be wrong | The commit message states the behavior change and the measured reachability; the adversarial audit is asked to find a second entry point by name |

---

## Self-review (run against the spec)

**Spec coverage.** Design sections 2-7 each map to a wave: section 2 -> W1; 4 -> W2; 5 -> W3;
6 -> W5; 7 -> W7; section 3.2's per-row dispositions -> W6 and W8; section 8's constraints ->
Global Constraints; sections 9-11 -> the capacity note, Risks, and What completion means. No spec
section is unimplemented.

**Placeholder scan.** No "TBD", no "add validation", no "similar to Task N", and - as of this
revision - **no open expansion markers anywhere**. Every wave W1-W8 carries numbered steps. The ten
points that genuinely depend on a future verdict or measurement are named gates (GATE-W3A-1,
GATE-W4-1, GATE-W4-2, GATE-W5A-1, GATE-W5B-1, GATE-W5C-1, GATE-W6-1, GATE-W7-1, GATE-W8-1,
GATE-W8-2 - ten, counted), each with a command, a pass/fail trigger, and a re-approval rule covering
the FAIL branch **including the "no verdict / cannot measure" branch**. Every code step in W1-W3
carries the actual code.

**Polarity audit (added in the Round-2 revision).** Every test arm in this plan is now labelled by
what it asserts, not by where it sits in the TDD sequence:

| Arm | Polarity | Passes when |
|---|---|---|
| `replace_in_place_refuses_to_follow_a_symlinked_file_target` | **behavioral RED** -> GREEN | after the guard lands |
| `replace_in_place_still_rewrites_a_regular_file` | positive control | always (both commits) |
| `replace_directory_mode_skips_symlinked_entries` | pin of existing behaviour | always (both commits) |
| `..._fails_closed_when_symlink_metadata_errors` + `..._rewrites_normally_when_no_metadata_fault_is_injected` | bidirectional pair | after the guard lands |
| `..._on_a_missing_path_still_errors_with_the_path_named` | compatibility pin | always |
| `..._leaf_swapped_between_guard_and_open_characterizes_the_residual_window` | **characterization pin - NOT a RED** | after the guard lands, and **flips to failing** when `RUST-REPLACE-TOCTOU` closes the race |

The last row is the one that was mislabelled. A RED asserts the post-fix behaviour and fails until
the fix lands; this arm asserts the *unfixed* behaviour and is expected to survive the fix. Calling
it a RED would mean the PR could not go green with it present.

**Type consistency.** `Check.retry_on_timeout` is spelled identically in the field definition
(Step 5), the retry test (Step 3), the control test (Step 9), the budget test (Step 11), and the
loop bound (Step 7). `attempts` is spelled identically in Steps 3, 7, 9, 10a. `replace_in_place`'s
signature is quoted once in Interfaces and used unchanged in every test.
`force_symlink_metadata_failure` is spelled identically in its struct field (Step 4a), its test
bodies, and the guard site (Step 6). The swap seam is now a handshake rather than a bool:
`swap_gate`, `SwapGate` (fields `reached_guard` / `resume`), `SWAP_GATE_TIMEOUT`, and the free
function `swap_leaf_to_symlink_for_test(path: &Path, attacker_target: &Path) -> bool` are each
defined once in Step 4b and spelled identically in the pin's body and at the guard site (Step 6).
The prior name `swap_leaf_to_symlink_after_guard` no longer appears anywhere in either artifact.
`RUST-REPLACE-TOCTOU` is spelled identically in the design, W3B Steps 11-12, W8 Step 4, the
traceability matrix, and the completion table.

**Gaps found and fixed during self-review.**
1. W2A originally jumped straight to the retry loop; the first RED would have been a `TypeError`
   from the missing kwarg - a construction crash, not a behavioral RED (A61). Split into Step 5
   (field only) then Step 6 (observe the real `assert 'failed' == 'passed'`).
2. W2A's no-retry control passes trivially once the default is 0. Added the explicit mutation
   control (Step 10) with its revert, so the control is proven discriminating.
3. W3B's guard was originally placed after the `fixed_strings` fast path, which would have left the
   literal route unguarded. Moved to immediately after `let path_obj = ...`, before the fast path.
4. W3B lacked a positive control - a guard refusing everything would have passed the RED arm. Added
   `replace_in_place_still_rewrites_a_regular_file`.
5. W3's directory-arm safety was asserted from `WalkDir`'s default rather than pinned. Added
   `replace_directory_mode_skips_symlinked_entries` and W3A council question 1.
6. The residual TOCTOU was initially implicit, which would have shipped a partial fix reading as a
   complete one. Now stated in the design, the code comment, the commit message, and filed as
   `RUST-REPLACE-TOCTOU`.
7. The completion table originally totalled 28 rows while W3B files a 29th. Corrected to 29.

**Gaps found and fixed in the Round-1 council revision (2026-08-13).**

8. **The plan contained its own violation.** Global constraint 5 mandated whole-repo `uv run pytest
   -q`, which collects `tests/e2e/test_routing_parity.py` and shells out to `cargo run` - forbidden
   by constraint 2 three items above it. Reconciled in favour of the CPU-SAFE ban: the gate is
   `uv run pytest -q --ignore=tests/e2e/test_routing_parity.py` everywhere it appears (constraint 5,
   W2A Step 15, W8 Step 5), with `--ignore` chosen over a deselect because it drops the module before
   its imports run.
9. **`EXPAND AT WAVE START` was read as a placeholder by half the council** - correctly, because a
   marker saying "write the steps later" is indistinguishable from unfinished. W4-W8 are now expanded
   from state that exists today, with ten named re-derivation gates carrying the genuinely
   verdict-dependent parts.
10. **The W3B guard failed OPEN.** `if let Ok(meta) = symlink_metadata(..)` restores the exact follow
    behaviour on any stat error, with nothing observable distinguishing "checked and safe" from
    "could not check". Now `map_err(..)?`, with a bidirectional fault-injection pair through the
    **existing** `#[cfg(test)] ReplaceFaultInjection` seam, plus a compatibility test pinning the
    missing-path contract that the change touches.
11. **The Windows fixture would have produced a wrong-reason RED.** `symlink_file(..).unwrap()`
    panics on an unprivileged runner, and the log reads as a failing security test. Now follows the
    repo's established `test_ast_rewrite.rs:1778-1784` skip-with-reason pattern, with the decisive
    arm routed to Linux and a rule that no verdict may be quoted from a node whose log carries the
    skip line.
12. **W3A had no worktree and W3B cut from `origin/main`,** so W3B's PR would have cited a threat
    model present in neither its diff nor `main`. W3A now creates
    `.claude/worktrees/w3a-threat-model` off `origin/main`; W3B branches **from W3A's branch** and
    verifies the carry with `git log --oneline` before writing Rust.
13. **W3 would have claimed more than it built.** The board Trigger requires an Event-gated swap test
    (A38/A48) and says nothing about junctions. Added a bounded Event-gated leaf-swap arm (using the
    same private seam, one deterministic swap, no timing loop) and made the junction question
    GATE-W3A-1, a MUST-ANSWER with three permitted outcomes and a default. The SHIPPED text is scoped
    to the four things actually built.
    **Superseded in revision 3 by items 24 and 26** - that "Event-gated" arm had no event (a Boolean
    plus a synchronous call), and treating the swap arm as a behavioral RED was a polarity error. Left here as the
    Round-1 record; build from Step 4b, not from this line.
14. **W1A's receipt had no landing mechanism.** "Committed" was the acceptance criterion for a
    document whose entire purpose is to stop the next session re-deriving the refutation. W1B now
    opens W1's own non-releasing `docs:` PR and verifies with `git show origin/main:`.
15. **"Seven days apart" was wrong** - the corroborating audit is dated 2026-08-12, one day before.
    Fixed in both artifacts, with a note that the corroboration's strength is method-independence
    rather than elapsed time.
16. **W1B's shell loop reported the wrong branch and mislabelled git errors.** One loop variable now,
    and `case $?` discriminates 0 / 1 / other so a git failure records CANNOT-MEASURE instead of
    NOT-LANDED (which reads as a measured "keep").
17. **`attempts: 0` on the `_command_available` early return was an untested claim.** Added a test
    whose stubbed `subprocess.run` raises if reached, so the early return is proven early.
18. **Design section 10 said the campaign edits `AGENTS.md`; nothing scheduled it.** The laws through
    A102 already exist on `origin/main`, so `AGENTS.md` is explicitly NOT edited (design 8.5, W8
    files note), with a stale-citation sweep as the single exception.
19. **`RUST-REPLACE-TOCTOU` was "filed" with no format and no proof.** W8 Step 4 now carries the exact
    parser-legal `Status:` / `PR:` / `Trigger:` line and runs the board governance tests - with a
    check that the run collected a non-zero number of tests, because a `-k` filter matching nothing
    exits 0 and reads like a pass.
20. **`git commit --amend` was unconditional.** It is safe only pre-push; W1B Step 5 now checks for a
    remote-tracking ref first and falls back to an ordinary second commit.
21. **The `WalkDir` citation was off by 14 lines.** `walk_directory_entries`'s signature is at
    `backend_cpu.rs:493`; the `WalkDir::new` call is at `:507`, with the `#[cfg(test)]`
    `force_walk_failure` block between them. Fixed in both artifacts - by naming the symbol and the
    call site rather than re-stamping a bare number, since a re-stamped anchor was wrong again the
    next day the last time this repo tried it.
22. **No live-state gate before a board edit.** Global constraint 14 now requires reading the index
    and the target row's `Status:` from `origin/main` and blocking on mismatch, and W1 opens with a
    warning that the orchestrator's checkout is at index `2026-08-08.1` and produced two false
    council findings.
23. **A40 honesty on reachability.** "Zero-consumer by measurement" is now "no in-repo consumer":
    `CpuBackend` ships in an `rlib`, so an out-of-tree caller is invisible to an in-repository
    census. The signature is retained; only the symlinked-argument behaviour narrows.

**Gaps found and fixed in the Round-2 council revision (2026-08-13, revision 3).**

24. **The "Event-gated" swap arm had no event** (BLOCKER). Step 4b was a Boolean plus a synchronous
    helper call - no `Event`, no thread, no handshake, no bounded acquire, so nothing in it could
    distinguish "the swap happened inside the window" from "the swap happened at all". Now a real
    two-actor mechanism: two `sync_channel::<()>(1)` capacity-1 channels, the writer on a spawned
    thread signalling `reached_guard` after the guard and blocking on `resume`, the test's main
    thread performing `remove_file` + `symlink` and acknowledging, and a single
    `SWAP_GATE_TIMEOUT = 2s` bounding **every** wait with a `CANNOT_MEASURE:`-prefixed panic. The
    gate is `take()`n out from under the injection mutex before either wait, because holding that
    lock across a blocking `recv_timeout` deadlocks the second actor out of the same struct.
    **Superseded in revision 4 (round-3 fix): the capacity-0 rendezvous form first used here lets
    a `send` block forever when its peer never arrives; the mechanism is now capacity-1 with
    non-blocking sends.**
25. **The RED commit staged one file where two were required** (BLOCKER). Step 4a/4b scaffolding
    lives inside `backend_cpu.rs`, so the RED commit now stages **both**
    `rust_core/tests/test_replace.rs` and `rust_core/src/backend_cpu.rs`, proven by a
    `git diff --cached --name-status` check whose expected output is written out, plus a
    `git diff --cached | grep symlink_metadata(path_obj)` control proving the guard did **not** leak
    in. The commit-boundary sentence now names both commits' exact staged sets. A consequence found
    while writing it: the in-file test ARMS cannot be in the RED commit either, because with no guard
    site to honour the gate the pin would die on its own 2s bound - a `CANNOT_MEASURE` panic, i.e.
    the wrong-reason failure A61 forbids. Fields and helper ship RED; arms ship GREEN.
26. **Polarity mislabel: the swap arm is a characterization pin, not a RED** (MAJOR). It asserts the
    window is **OPEN** - the behaviour that survives this PR - and is expected to FLIP to failing
    when `RUST-REPLACE-TOCTOU` closes the race. Renamed in the test name, Step 4b, Step 8's
    expectations, Step 12's SHIPPED text, the acceptance criteria, the traceability matrix, and
    design 3.2 / 5.4a. A new polarity-audit table in this self-review labels all seven W3B arms, so
    the next reader does not have to infer polarity from position in the sequence. The SHIPPED text
    now reads "static no-follow guard + fail-closed metadata handling + residual-race
    characterization pin (window currently OPEN)" and points the race at `RUST-REPLACE-TOCTOU`.
27. **`swap_leaf_to_symlink_for_test` was invoked and never defined** (MAJOR). Now fully specified:
    signature `fn swap_leaf_to_symlink_for_test(path: &Path, attacker_target: &Path) -> bool` (the
    attacker target is passed explicitly rather than inferred), full body, `bool` return
    distinguishing CANNOT_MEASURE from a failed swap, and placement as a **free function** in the
    in-file `#[cfg(test)] mod tests` beside the fault struct - not a `CpuBackend` method, since the
    guard site no longer calls it at all (the second actor does).
28. **W8 ran the wrong parser test** (MAJOR). `-k "task_board"` matches
    `test_task_board_freshness.py` - a CHANGELOG-distance test - and does **not** match the row
    parser, which is `tests/unit/test_backlog_tracker_truth.py`. A green run of the former said
    nothing about row legality. The parser test is now run **by path**, with `-k "backlog_tracker or
    task_board"` as a sibling sweep on top. New Step 4a covers the closed-world constants:
    `EXPECTED_IDS` and `DEMAND_IDS` both gain `RUST-REPLACE-TOCTOU`, with `DD-006` removed from
    `DEMAND_IDS` only if W5B retires it. **Correction to the finding as filed:** the
    `RUST-REPLACE-SYMLINK` READY -> SHIPPED transition needs **no** constant change - measured on
    `origin/main`, that id is already in `EXPECTED_IDS` (membership is status-independent) and absent
    from `DEMAND_IDS`. The step says so and still re-derives it live, because the stale local
    checkout's copy of that file disagrees. Widening W8 to touch one test file is now reflected in
    its Files and Allowed-paths sections; the `docs:` release class is unaffected.
29. **GATE-W8-1's FAIL rule wrote a false-SHIPPED state** (MAJOR). `SHIPPED (merged <sha>; release
    pending <run-id>)` is still `SHIPPED` to every tracker and bucket count that keys on the status
    token. The row now takes the board's existing **`IN_FLIGHT`** status (which requires a literal
    `PR #<n>` and an unchecked box - both parser facts, both stated), and the rule now **diagnoses
    the failure class first**: a `! [rejected] main -> main` push-race self-heals on the next push
    and must **not** be rerun, while a cancelled publish tail is recovered with
    `gh run rerun <id> --failed`. SHIPPED only after PyPI membership on the exact version, queried
    with `Cache-Control: no-cache` against the `releases` map rather than the CDN-cached
    `info.version`.
30. **W4's finding-set grep could not match** (MINOR). The ledger's findings are table **rows**
    (`| F1 | HIGH | ... |`), not `###` headings, so `^### F[1-6]` returned nothing - a false zero
    reading as "no findings remain". Now `grep -nE "^\| F[1-6] " -B 2 -A 1`, with a stated positive
    control (at least six matched rows) and a warning that the file carries **two** F1-F6 sequences
    which are different populations.
31. **W4 assumed its worktree existed** (MINOR). New Step 1a checks
    `git worktree list | grep -F task2a-w4-repair` and gives the exact creation form from #966's head
    (never from `origin/main`) when absent. Verified present at authoring time at `8181762` on
    `task2a-round60-red`, matching #966's live `headRefOid`.
32. **W5B's "bounded measurement" had no numbers** (MINOR). Client count (20), duration (60 s under a
    hard `timeout`), request shape, and ramp are now fixed in a table *before* the run, alongside the
    exact positive-control invocation (same 20 clients against a **closed** target, expected
    failures == 20) and four explicit evidence thresholds - valid control, reproduced (2 of 2 runs),
    null, and CANNOT_MEASURE. GATE-W5B-1 now cites those thresholds instead of restating them
    loosely.
33. **A parser fact caught while transcribing the new row.** `ROW_RE` matches the **whole line**,
    including a literal **em dash** separator after `**ID**`; the previous Step 4 quoted only the
    `Status:/PR:/Trigger:` half, which would have failed as `malformed or multiline canonical row`.
    The full line is now written out, with the em-dash requirement called out as the one place in
    this campaign where A96's ASCII-only rule does not apply (copy the byte from a neighbouring row,
    do not retype it), plus the `PR: none` and unchecked-box requirements for a `DEMAND_GATED` row.
