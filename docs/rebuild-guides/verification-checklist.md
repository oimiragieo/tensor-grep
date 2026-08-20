# Rebuild verification checklist

How to prove a rebuilt (or reconstructed-from-a-guide) feature is actually correct, not just
"looks like the original". This is the generic checklist referenced by every guide under
`docs/rebuild-guides/`; `docs/rebuild-guides/tg-checkpoint.md` §7 is a worked instance of it.

This checklist is deliberately about **evidence**, not vibes. Every item below answers a specific
question: *what would this check show if the rebuild were subtly wrong?* If the answer is "the
same as if it were right," that item is not verification — see `AGENTS.md`'s verification-oracle
family and the global `detect-the-false-green` skill for the general form of this rule; this
document is its rebuild-specific application.

## 1. Locate and run the feature's own test files first

Every non-trivial feature in this repo already has tests that exist *because* a naive
implementation shipped and broke once. Find them before writing any new ones:

```
find tests -iname "*<feature-keyword>*"
```

Read the test **names**, not just whether they pass — a name like
`test_undo_commit_failure_restores_a_removed_file` or
`test_rust_created_out_of_root_symlink_checkpoint_fails_closed_on_undo` is closer to a spec than a
test, and tells you which trap in the rebuild guide's §6-equivalent that test exists to catch.
Run the file-scoped subset relevant to the feature:

```
python -m pytest tests/unit/test_<feature>_*.py -q
```

then, once that is green, the two governance gates every docs-only change in this campaign must
also pass (see §5):

```
python -m pytest tests/unit/test_public_docs_governance.py tests/unit/test_skill_library_drift.py \
    tests/unit/test_skill_index_sync.py -q
```

**A rebuild is not verified by "the tests I wrote pass."** It is verified by "the tests that were
already written for this exact failure mode still pass, unmodified" — per this repo's TDD-first
discipline (`.claude/skills/tensor-grep-change-control/SKILL.md`), a new test proves nothing until
it has been seen to *fail* on the pre-fix baseline. If you are rebuilding a feature that was
deleted for the exercise, that means: revert your rebuild, confirm the existing tests fail (RED),
restore the rebuild, confirm they pass (GREEN). A test that was never seen red is not evidence.

## 2. Dogfood the real, shipped surface — not just the internal function

Per `docs/rebuild-guides/`'s house rule (mirrored from
`.claude/skills/tensor-grep-validation-and-qa/SKILL.md`'s CliRunner-vs-real-binary trap): calling
`create_checkpoint(...)` directly from a Python REPL, or driving it through Typer's `CliRunner`,
bypasses the real front door (`bootstrap.py`'s intercept, the native-vs-Python routing gate, argv
parsing quirks). Verify through the **actual installed or built `tg` binary**:

```
tg <command> ... --json
```

and read the real JSON it prints, not a mocked or hand-constructed shape. Every JSON example in a
rebuild guide should be output that was actually captured this way — see
`docs/rebuild-guides/tg-checkpoint.md` §4-5 for the pattern (real command, real output, absolute
paths trimmed for readability but nothing else altered).

## 3. Trace a full round trip on a throwaway scratch directory

Never validate a stateful feature (anything that reads and writes its own persisted state — a
checkpoint store, a ledger, an index, a cache) against a real project checkout. Build a minimal
throwaway directory, exercise the feature's full lifecycle, and inspect the on-disk artifact it
produced directly:

```
mkdir -p "$SCRATCH" && cd "$SCRATCH"
# ... create the preconditions the feature needs ...
tg <command> create ... --json      # capture the real output
find .tensor-grep -maxdepth 5        # or whatever this feature's state directory is
cat .tensor-grep/.../metadata.json   # read the actual persisted shape, don't assume it
# ... mutate the scratch dir to simulate real usage ...
tg <command> undo/restore/apply ... --json
# ... assert the mutation was actually reverted, by re-reading the files, not by trusting the JSON alone
```

This is how `docs/rebuild-guides/tg-checkpoint.md` §4-5 was produced, and it is what caught (while
writing this checklist) that `undo`'s `diverged_paths` field only appears when non-empty — reading
the *doc comment* claiming that behavior would not have proven it; running the round trip did.

## 4. Walk the "traps" list against the real code, not the guide's prose

A rebuild guide's "naive implementation gets this wrong" section is itself a claim that needs
checking, not a checklist to trust blindly. For each trap named in a feature's rebuild guide:

1. Find the guarding code by the cited symbol (`grep -n 'def <symbol>' <file>` — prefer this over
   trusting a bare line number, which rots as the file is edited).
2. Read the guard's own comment. Does it name the real audit/task/bug this guards against? A
   comment that cannot answer "what would happen without me" is decoration, not documentation.
3. If time permits, find or write a minimal test that removes the guard (comment it out, or patch
   around it) and confirms the failure mode actually reproduces, then restores the guard and
   confirms it is gone again. This is the perturbation-proof pattern from AGENTS.md's evidence
   laws (inject the defect -> red; revert -> green, file byte-identical) — the strongest form of
   "this trap is real," stronger than a docstring asserting it.

## 5. Run the governance gates that apply to ANY change in this repo

Regardless of what was rebuilt, before calling the work done:

- **Docs-only changes** (adding or editing a guide, a design doc, this checklist):
  ```
  python -m pytest tests/unit/test_public_docs_governance.py tests/unit/test_skill_library_drift.py \
      tests/unit/test_skill_index_sync.py -q
  python -m ruff format --preview <changed .md files>
  ```
  (`ruff format --preview` formats Python code blocks fenced inside Markdown — a malformed fence
  can fail CI even though the file "looks" like plain prose.)
- **Code changes**: the full relevant unit-test file(s), `ruff check` and `ruff format --preview`
  over changed files, and — for anything touching a registered CLI command, MCP tool, or search
  flag — the registration-site checklist in `AGENTS.md`'s "Adding a Command or Flag" section. A
  feature that works when called directly but was not wired into all of its front doors is not
  actually rebuilt; it is a private function that happens to match the original's behavior.
- **Never** run `cargo` anything, `tests/e2e/test_routing_parity.py`, or a benchmark script on a
  shared/personal dev box — those are CI's job (see `AGENTS.md` / this repo's CPU-safe discipline).
  If a rebuilt feature touches Rust, describe what CI will check and let CI check it.

## 6. State what you verified, distinctly from what you read

When reporting a rebuild's verification status, separate three tiers explicitly — collapsing them
is the single most common way a "verified" claim turns out to be a guess:

1. **Ran and observed** — a command was executed, its real output was captured, and that output is
   what's quoted. (Example: the JSON blocks in `docs/rebuild-guides/tg-checkpoint.md` §4-5.)
2. **Read and cited** — a `file:line`/symbol was opened and the claim is a direct paraphrase of
   what's there, but nothing was executed to confirm the code path actually behaves as read.
   (Example: most of `docs/rebuild-guides/tg-checkpoint.md` §6's trap descriptions — the guard
   code was read, not independently exercised with a crafted attack input.)
3. **Unverified / out of scope** — named explicitly as such (a "what this guide does not cover"
   section), never silently omitted. An honest gap is worth more than a confident guess — see
   `AGENTS.md`'s dated-instrument laws for why a blocked or unrun check must never be reported the
   same way as a definitive negative.

A rebuild guide or verification report that does not make this distinction reads as fully verified
even where it is not, which is the exact failure mode this whole checklist exists to prevent.
