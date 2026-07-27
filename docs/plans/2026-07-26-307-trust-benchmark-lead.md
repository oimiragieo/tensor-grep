# Task 307: Make the Trust Benchmark Measure Where tg Actually Leads

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this
> plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

> **Citation convention.** Local task-tracker IDs are written `task NNN` with no `#`, because
> GitHub auto-links bare `#NNN` and this repo's two numbering spaces overlap. Only genuine
> GitHub issues/PRs keep the `#`.

**Goal:** Resolve task 307 -- "tg ties rg/GNU grep 2/2/0 on the trust benchmark; find the
disclosure behaviors that would make it genuinely lead" -- by first establishing whether the tie
reflects tg's behaviour or the benchmark's blind spot, and then closing whichever is real.

**Status: RESEARCH COMPLETE, DESIGN PROPOSED, NOT YET REVIEWED.**

## What the research established

Exa research on ripgrep's machine-readable contract and on the SARIF standard, then verified
against the real code in this repo.

**Finding 1 -- rg structurally cannot signal incompleteness in its JSON stream.**
`rg --json` emits exactly five message types: `begin`, `end`, `match`, `context`, `summary`
(ripgrep `crates/printer/src/json.rs`, and the rg(1) man page). The terminal `summary` message
carries `elapsed_total` and `stats` and **no incompleteness field**. rg's soft errors -- "unable
to read a file" -- reach the user only through **stderr plus exit status 2**
(`doc/rg.1.txt.tpl`: *"`2` exit status occurs when an error occurred. This is true for both
catastrophic errors ... and for soft errors (e.g., unable to read a file)"*). A consumer parsing
only rg's JSON stream cannot distinguish "no matches" from "could not finish looking." This is
not a defect anyone forgot; it is the shape of the format.

**Finding 2 -- a standard already models this, and it is not tg-invented.**
SARIF v2.1.0 (OASIS Standard, 27 March 2020) defines `invocations[].executionSuccessful`
(section 3.20.14) and `invocations[].toolExecutionNotifications` (section 3.20.21), and carries
an entire **"Appendix I. (Informative) Detecting incomplete result sets."** SARIF's own glossary
defines a *notification* as "reporting item that describes a condition encountered by a tool
during its execution" -- exactly an unreadable directory.

**Finding 3 -- tg ALREADY conforms, on a branch that has not merged.**
Verified against `origin/feat-310-sarif` (PR #796), not assumed:
- `src/tensor_grep/cli/sarif.py:208` -- `invocation: dict[str, Any] = {"executionSuccessful": not is_partial}`
- `src/tensor_grep/cli/sarif.py:243` -- `invocation["toolExecutionNotifications"] = notifications`
- `src/tensor_grep/cli/sarif.py:11` -- the module docstring already states the intent:
  *"Incompleteness is rendered, not dropped."*
- `src/tensor_grep/cli/main.py:14181` -- maps `unreadable_paths` onto `executionSuccessful`
- `tests/unit/test_sarif_output.py:119-136` -- asserts BOTH arms: `executionSuccessful is False`
  on a partial scan and `is True` on a complete one, with the control explicitly labelled

## The reframe this forces

Task 307 was filed as "find the disclosure behaviors that would make tg lead." The research says
**tg already has one rg structurally lacks.** So the 2/2/0 tie is not a statement about tg's
behaviour -- it is a statement about what the benchmark looks at.

This is verification-oracle **Form 7**, already canonical in `AGENTS.md`: *a benchmark column
tied at the FLOOR for every tool measures nothing.* The same session already hit this once, on
PR #804, where the payload channel scored **0 for every tool including tg**. A column every
contestant fails is not evidence of parity; it is evidence the column is not wired to anything.

**So the honest resolution of task 307 is not "add disclosure behaviour to tg." It is "the
benchmark does not measure the channel where tg leads, and the tie is therefore uninformative."**

## The integrity constraint -- read before Task 2

Adding a column that only tg can score is trivially self-serving, and a benchmark I control
which concludes I win is worth nothing. Two rules keep this honest, and they are the reason
Task 2 is gated on review rather than written now:

1. **Name the column for the capability, not the winner.** "Standards-conformant machine-readable
   incompleteness disclosure (SARIF v2.1.0 Appendix I)" is a capability a consumer can want and
   any tool could implement. "Disclosure honesty" is not -- tg would be scoring itself on its own
   feature list under a general-sounding name. The existing 2/2/0 column measured stderr + exit
   code, a channel all three share; it stays, unchanged, and tg's tie on it stays reported.
2. **Report the tie and the lead side by side, never merged into one headline.** The CEO-facing
   sentence must remain "tg ties rg and GNU grep on the shared stderr/exit-code channel, and is
   the only one of the three that emits a standards-conformant machine-readable incompleteness
   signal." Collapsing that into "tg leads the trust benchmark" would repeat the exact error this
   task exists to correct -- the earlier "leads 5/5" claim that reproducibility falsified.

## Dependency reality

- SARIF conformance lives on `origin/feat-310-sarif` (PR #796). **PR #796 is currently RED**
  on the task 303 large-stdout flake, which PR #802 fixes. Order: #802 -> #796 -> this work.
- The benchmark harness is `scripts/trust_benchmark.py`, extended on PR #804
  (`fix-307-benchmark-measures-tg`), which is where the payload-channel columns already live.
- Nothing here needs Rust compiled locally. `scripts/trust_benchmark.py` is Python; SARIF
  rendering is Python.

---

### Task 1: pin the "rg cannot do this" premise as an executable check -- do FIRST

Before adding any column, prove the premise the whole reframe rests on. If rg *can* signal
incompleteness in a machine-readable stream and I simply missed the flag, every task below is
wrong and must be abandoned.

**Files:**
- Test: `tests/unit/test_trust_benchmark_premise.py` (create)

- [ ] **Step 1: Write the failing test.** Assert that rg's JSON summary event carries no
      incompleteness field when a directory is unreadable, and that the same run exits 2.

```python
def test_rg_json_summary_has_no_incompleteness_field(tmp_path: pathlib.Path) -> None:
    """PREMISE for task 307. If this ever fails, the whole reframe is void -- rg gained a
    machine-readable incompleteness channel and tg no longer leads on it.

    Skips (never silently passes) when rg is absent or the OS cannot make a directory
    unreadable -- a probe that cannot create the condition proves nothing.
    """
```

- [ ] **Step 2: Run it and confirm it FAILS before the helper exists.**
      Run: `python -m pytest tests/unit/test_trust_benchmark_premise.py -v`
      Expected: FAIL (module/helper not defined).

- [ ] **Step 3: Implement the probe.** Build an unreadable directory, run
      `rg --json PATTERN <dir>`, parse the JSON Lines, locate the `summary` message, assert no
      key in it means "incomplete". Assert exit status is 2 -- that is the ONLY channel rg has,
      and asserting it is what makes the test a statement about rg's *design* rather than a
      complaint about a missing feature.

- [ ] **Step 4: Assert the PREMISE of the premise.** The unreadable directory must actually be
      unreadable to the test process. On Windows and when running as root this often silently
      fails; `pytest.skip` with the observed mode rather than passing vacuously. Receipt: this
      session already had an escape probe read 4/6 instead of 6/6 because two canary paths were
      absent, and a UID probe under `/root` that reported DENIED for everything and proved
      nothing.

- [ ] **Step 5: Run to green, then commit.**

```bash
git add tests/unit/test_trust_benchmark_premise.py
git commit -m "test(bench): pin the premise that rg has no machine-readable incompleteness channel"
```

---

### Task 2: add the SARIF disclosure column to the trust benchmark -- GATED ON REVIEW

**Do not start this task until the thinktank review below has returned and the integrity
constraint above has been explicitly ratified or amended.** The failure mode here is not a bug;
it is publishing a benchmark that flatters its author, which is worse than the tie it replaces
and is the specific thing the CEO statement corrected me on once already.

**Files:**
- Modify: `scripts/trust_benchmark.py` (the payload-channel scoring added on PR #804)
- Test: `tests/unit/test_trust_benchmark_premise.py` (extend)

Sketch, deliberately not finalised pending review: score each tool on whether an unreadable
directory produces a machine-readable artifact in which a consumer can detect an incomplete
result set without parsing prose. tg via `--sarif`. rg and GNU grep score 0 because no such
output mode exists -- which the column must state as a *capability absence*, not a failure.

- [ ] **Step 1:** Await review verdict. Record it inline in this document, including any
      dissent, the way the task 276 plan records its own falsified tasks.

---

### Task 3: correct the CEO-facing sentence -- do LAST

**Files:**
- Modify: `docs/BACKLOG.md`, and whichever enterprise gap table PR #790 refreshed.

- [ ] **Step 1:** Replace "tg ties rg and GNU grep, it doesn't lead" with the two-clause
      sentence from the integrity constraint -- tie on the shared channel, sole conformance on
      the machine-readable one -- **only if** Task 2 shipped and its column is reproducible on
      both Windows and Linux, the same bar that falsified the original "leads 5/5" claim.
- [ ] **Step 2:** If Task 2 is rejected on review, write the negative instead: the tie stands,
      the benchmark's blind spot is documented, and task 307 closes as "measurement was the
      issue, and the honest read is unchanged." A closed negative is a real outcome here.

---

## Self-review

**Spec coverage.** Task 307 asks for "the disclosure behaviors that would make it genuinely
lead." Tasks 1-3 answer: the behaviour already exists (SARIF, PR #796); the benchmark cannot
see it; fixing the measurement is the work. Covered.

**Placeholder scan.** Task 2 is deliberately unfinished and says so, with the reason. That is a
gate, not a placeholder -- the distinction being that a gate names what unblocks it.

**Type consistency.** No new types. `executionSuccessful` is a bool per SARIF 3.20.14 and
`sarif.py:208` already emits it as one.

**The thing most likely to be wrong.** That SARIF is the right channel to benchmark at all. A
reviewer should attack this: SARIF is a *static-analysis* interchange format, and `tg search` is
not a static analysis tool. If the reviewer concludes SARIF conformance is not a fair proxy for
"trustworthy machine-readable search output," Task 2 dies and Task 3 takes its negative branch.
