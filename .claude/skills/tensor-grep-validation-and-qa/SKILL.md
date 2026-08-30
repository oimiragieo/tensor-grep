---
name: tensor-grep-validation-and-qa
description: Use when deciding what counts as proof that a tensor-grep (tg) change works — before trusting a subagent's "tests pass", writing a new test, claiming a routing/docs/release fix is done, shipping a doc-drift/ranking/classification heuristic off green fixture tests, proving a red-green baseline on a reverted/pre-fix commit, reviewing a payload-byte/ratio governance test, de-flaking a timing-sensitive test, or running the pre-push gate. Covers TDD-first discipline, the CliRunner-vs-real-binary trap, the fixture-green-vs-real-corpus-dogfood trap for precision/heuristic features, the `capfd`-vs-`result.stdout` capture-surface trap on routing/delegation changes (needs `tests/integration/` run with the native `tg` binary rebuilt, not just `tests/unit/`), the `tests/conftest.py` `sys.path.insert`-outranks-`PYTHONPATH` trap that can falsify a red-green baseline even with `tensor_grep.__file__` verified, a shared-envelope field growth breaking a payload-ratio governance test plus its tmp-path-length platform sensitivity, the self-gate-suite-subset-is-not-full-CI-matrix trap, a new test that proves nothing until seen fail on the pre-fix baseline, a `max(baseline*N, floor)` timing ratio degenerating to its floor below clock resolution plus the profile-before-attributing-a-flake discipline, preferring a structural order-based assertion over any wall-clock form, the certified/golden inventory (routing parity, docs governance, release-asset validation), agent-readiness/`tg dogfood`, benchmark-gated speed claims, acceptance thresholds, and which suite/marker/fixture to use for a new test, plus the `--preview` / `--no-sync` / `-x` gotchas.
---

# tensor-grep validation and QA

This is the **evidence-bar runbook**: what is allowed to count as proof that a change to `tensor-grep`
(the `tg` CLI) works, and how to add a test that actually enforces it. `tensor-grep` describes itself
as a "benchmark-governed, contract-heavy codebase" (`CONTRIBUTING.md:3`) — many behaviors are pinned by
tests that fail on drift, and speed claims are gated by measured numbers, not review opinion.

## Who this is for

Two readers, written to the **lower bound** of each:

- A **Sonnet-class AI** in a cheap autonomous session: copy-pasteable commands and hard gates so you
  cannot silently skip validation.
- A **mid-level human engineer**: the *why* behind each gate, so you extend it correctly to new cases.

## When to use this skill vs a sibling

| Your task | Use |
|---|---|
| "Is this proof good enough to claim done?" / adding or picking a test | **this skill** |
| The non-negotiable gates (draft-PR-only, registration sites, fail-closed contract, push-race) | `tensor-grep-change-control` |
| Picking/reading a `benchmarks/*.py` script, the noise-floor rule for sub-10ms rows | `tensor-grep-benchmark-and-proof-toolkit` |
| Interpreting a `tg doctor --json` / `tg dogfood` field — what it does and does NOT prove | `tensor-grep-diagnostics-and-tooling` |
| A live bug/red-CI to triage | `tensor-grep-debugging-playbook` |
| "Has this already been tried and lost?" | `tensor-grep-failure-archaeology` |
| Internals/why the front door is shaped this way | `tensor-grep-architecture-contract` |
| Env var / flag reference | `tensor-grep-config-and-flags` |
| Day-to-day CLI invocation syntax | `tensor-grep-run-and-operate` |
| Writing docs of record (AGENTS.md, README, docs/*.md) | `tensor-grep-docs-and-writing` |
| Release mechanics / positioning | `tensor-grep-release-and-positioning` |
| Using `tg` to navigate a codebase | `tensor-grep` (usage skill) / `code-search-and-retrieval-reference` |

**No skill routes around change-control.** This skill tells you what evidence a gate needs; it does not
relax any gate in `tensor-grep-change-control`.

---

## Part 0 — THE ORACLE FAMILY: when your verification isn't (read this first)

**The single most repeated failure mode in this repo.** TEN distinct forms, most in ONE session
(2026-07-25; forms 7 + 8 added 2026-07-26, form 9 2026-07-27, form 10 2026-07-28). Every form
shares one shape: *something that looks like verification isn't.*

(The count read NINE while ten forms were present, for three days. A header that miscounts the
thing below it is the smallest possible instance of this Part's own subject -- and it was found by
an audit that COUNTED the forms rather than reading the sentence. Re-derive the number when you add
one; do not trust the header, including this one.

**Both halves of that two-file edit were still wrong on 2026-08-01, in OPPOSITE directions.**
`AGENTS.md`'s header kept saying "nine forms" for four more days -- the fix landed here and never
crossed over -- while THIS file, which had the count right, misdated forms 8-9 to 2026-07-27 when
Form 8's own text reads 2026-07-26. Each doc was half right, and reading either one alone
confirmed it. The dates and the count are now derived from the `**Form N —**` headings
themselves, which are the only authority, and `tests/unit/test_skill_library_drift.py` now fails
if the stated count and the enumerated forms disagree in EITHER file -- because a rule that says
"re-derive the number" had already been read, agreed with, and half-applied.)

**The one question that catches all ten — before trusting any green signal, ask:
"what would this check show if the thing it verifies were BROKEN?"
If the answer is "the same", it is not verification.**

**Forms 1-5 assume the setup worked and the comparison was wrong. Form 6 inverts it: the assertion is
fine and the SETUP silently no-opped, so the hostile arm was never hostile.** Ask both questions.

**Two GLOBAL skills carry the general form of this Part — load them, they are not tensor-grep-specific
and this file is not a substitute for either** (added 2026-08-02; a census found them referenced in
ZERO tracked files here while `verify-plan-against-code` had 23, so nothing routed to them):

- **`detect-the-false-green`** (`~/.claude/skills/`) — when a green result is about to LICENSE a
  claim ("fixed", "clean", "covered", "safe to delete"), or when the check has never been observed
  to fail. Covers the shapes this Part's forms are instances of: an early return, a skip nobody
  reads, a platform-gated test, a confident zero, and an audit that re-implements the gate it audits.
- **`author-a-probe-that-cannot-lie`** (`~/.claude/skills/`) — read it BEFORE writing any probe or
  benchmark whose number you will act on. Positive control, blind-vs-busy empty results, arm
  interleaving, max-not-mean, and shared-resource pollution windows — the last one binds here,
  because the dev box is a shared server.

Adding a Form to the family stays a two-file edit (`AGENTS.md` + this file). Adding a POINTER, like
the two above, does not — those skills own their own content and must not be copied in.

| Form | What it looks like | Direction of harm | Receipt |
|---|---|---|---|
| **1. Normalize-both-sides** | A comparator applies the same lossy transform to BOTH arms | **Masks** real defects — silent | #262 (CRLF/encoding-blind rg-parity oracles); surviving accepted limit at `tests/helpers/rg_parity.py:560`, now *proven* lossy and pinned by PR #748 |
| **2. Harness-corrupts-output** | Post-processing mangles a byte-correct result before comparison | **Manufactures** false failures | `test_output_golden_contract.py::run_tg` did `line.replace("\\","/")` on the WHOLE line, turning a binary notice's `\0` into `/0`; fixed in #746 |
| **3. Test-never-executes** | The file exists, looks like proof, and SKIPS | **Fakes** coverage | `test_native_json_byte_fidelity.py` skipped in every CI job; fixed #746, class-fixed #749 |
| **4. Gate-diagnosis-wrong** | A gate's *conclusion* is right, its *root cause* is false | Sends the fix at the wrong target | The gate that found form 3 claimed `TG_REQUIRE_RG_PARITY` was in "zero workflows" — it is at `grep -n TG_REQUIRE_RG_PARITY .github/workflows/ci.yml`. **No line number here on purpose.** This anchor has now been re-stamped twice (`:706` -> `:764`) and was wrong again by 2026-08-23 (real hits: 907/918/925; `:764` had drifted onto unrelated `cargo test --lib` commentary). Re-stamping is the failure mode this very table warns about -- run the grep |
| **5. Repro topology deletes the mechanism** | Every fixture shares one structural property, and that property is the one that matters | Proves a **strict subset** of the real defect — defeats even an honest RED | PR #750: repro + all 4 tests used non-git `tempdir()`, the one topology where the fix's mechanism suffices; **inside a git repo the fix is a no-op** |
| **6. The FIXTURE never applied** | The hostile condition silently failed to take effect, so the "bad" arm is really the good arm | Declares a real defect **ABSENT** — the most flattering direction | #281: `icacls` failed to apply a deny ACE twice (`"No mapping between account names and security IDs was done"`), which would have made an unreadable-directory probe run against a perfectly readable directory and conclude "no defect" |
| **7. The MEASUREMENT cannot discriminate** | A scored column where every arm ties — usually at the floor | Reads as a **finding** when it measured nothing | #302: the trust benchmark's `vanished-file` column scores 0 for all six tools on both platforms. Six zeros read as "they're all bad at this"; in fact the fixture almost certainly deletes the file *before* the search starts, so the race it claims to measure never opens |

**Form 7 applies to benchmarks and scorecards, not just tests.** Same question, unchanged: *what
would this column show if a tool were GOOD at it?* A tied-at-floor column is worse than no column,
because it looks like data. Every scored dimension needs at least one run where arms differ, or it
gets deleted with the reason written down.

**Form 8 — the SPLIT ORACLE (2026-07-26).** *A precondition proved in a DIFFERENT run is not THIS
run's precondition.* `tests/unit/test_trust_benchmark_premise.py` pins "rg cannot signal an
incomplete scan inside its JSON stream" with two arms: ARM 1 runs `rg --json` over a tree with an
unreadable directory and asserts no incompleteness marker; ARM 2 asserts rg exits 2. **ARM 1 never
asserted its OWN run exited 2** — so on a tree where the directory is actually readable, rg exits 0,
completes, correctly emits no marker, and ARM 1 passes, reporting "rg hides incompleteness" on the
evidence of a scan that was never incomplete. What made it feel safe is the shape to learn: a helper
DID verify the directory was unreadable *to the test process*, and ARM 2 DID assert exit 2 — both
true, neither load-bearing for ARM 1. Move the premise assertion INTO the run that draws the
conclusion. (Canonical text: `AGENTS.md` "The Verification-Oracle Family", Form 8.)

**Form 9 — the REVIEWER'S expected number is the broken half (2026-07-27).** Forms 1-8 all assume
the checker is wrong about the CODE. This one inverts the subject: a census mismatch is a
**two-sided hypothesis**, and the side that is wrong is often the expectation you brought to it. In
one session this fired twice in opposite directions — an envelope seam expected at 2 sites was
really 3, and four comments suspected of claiming "observed no walk" turned out to be individually
correct. Both would have been filed as product defects on the strength of a number that felt wrong.
So: **read the breakdown before filing the finding.** A count that disagrees with your expectation
is a prompt to enumerate the members and look at each, not evidence of a bug. The same applies to
an audit handed to you by another agent: the 2026-07-27 skill audit's own corrected line numbers
were stale, because they were computed against a worktree 28 commits behind `origin/main` — the
finding was real, the expected value was not. Re-derive before you act on someone else's number.

**Form 10 — the oracle's UNIT is the BRANCH, and the defect lives in the MERGE (2026-07-28).** Every
form above assumes the check is looking at the right CODE. This one is about the right TREE. PRs
#835 and #836 were each fully green (48 checks apiece, bidirectional controls, an independent
adversarial gate on one) and **main went red the moment both were on it**: #835 asserted exactly ONE
line of `--mermaid` output mentions `INCOMPLETE RESULT`, #836 deliberately added a second. Git merged
them with NO textual conflict — the collision is semantic and exists only in the union, which CI
never evaluated because each PR's checks ran against its own base. Cost: main red and a release lost
(`Semantic Release` skipped, so v1.101.8 was never produced). It recurred immediately: #837, rebased
onto the still-broken main, returned the identical `assert 2 == 1`.

**Before pushing, rebase onto the REAL target and run the union.** A branch green against a stale
base has verified a tree nobody will ship. Merge is semantically live even when git is silent when
(a) two PRs touch the same OUTPUT SHAPE — different files and different functions still collide, or
(b) one PR adds to a rendering that another PR COUNTS. Grep the whole suite for assertions about the
shape you are changing: **the file you are editing is not the boundary of the blast radius.** (This
family is MIRRORED — see `AGENTS.md`; adding a form is a two-file edit.)


### Ambient keys and tracker prose are instruments too (2026-08-06)

- A `--sign` no-key RED that only pops `TG_EVIDENCE_SIGNING_KEY` is **split from the default key
  path** (AGENTS.md **A70**) — same family as Form 8 / ambient-fixture pollution.
  **Merge/poller twin (A77):** a `gh pr checks` pipe into a stdin-eating heredoc can empty the checklist
  and read as ALL_TERMINAL while jobs are still pending — write checks to a file and require heavy
  lanes present by name. **Quota twin (A78):** usage-limit seat errors are FAILED seats, not pending
  Sol SHIP. **Status-stamp twin (A79):** READY→BLOCKED stamps must retarget tracker pins in the same PR.
- A TASK_BOARD “campaign note” under the canonical index is not documentation; it is a **malformed
  row** to the tracker parser (**A71**).
- Bare-wheel dogfood without semantic extras cannot grade `tg find` (**A73**).


### Running the probe: the LOCATION trap (2026-07-26)

A perturbation proves nothing if the thing you perturbed survives elsewhere. Verifying the
`truncation_cause` doc ratchet, the first probe removed ONE occurrence of `unreadable-path` from
`docs/CONTRACTS.md` and the test still passed — which reads as "the ratchet is toothless". It was
not: the string appears **twice**, and the check is a substring scan over the whole file. Removing
EVERY occurrence failed the test correctly.

**Before concluding a guard is broken, confirm your perturbation actually removed the property it
guards** — `grep -c` the string first. This is the setup-not-assertion failure wearing a third face:
what looked like "passes in both arms" was really a probe that never created a second arm.

### Writing a hostile fixture (Form 6 defence)

Any fixture that simulates something BAD — permission denied, network partition, disk full, killed
process, corrupted file, missing binary — is **a claim about the world, and claims get verified.**
Assert the fixture BITES before the probe runs, and abort loudly if it doesn't:

```python
try:
    os.listdir(denied_dir)
    print("entries listed -> STILL VACUOUS")  # abort: the fixture did nothing
    raise SystemExit(1)
except PermissionError:
    pass  # good, the fixture is real
```

Windows ACL specifics learned the hard way (#281):

- **To APPLY a deny ACE, use PowerShell with the SID**, not an `icacls` account string.
  `[System.Security.Principal.WindowsIdentity]::GetCurrent().User` → `FileSystemAccessRule` with
  `Deny`, plus `SetAccessRuleProtection($true, $false)`. `icacls` account-name forms
  (`%USERNAME%`, `MACHINE\user`) failed to map and processed **0 files** while looking almost like
  success.
- **To REMOVE it, `icacls <dir> /reset` — it takes no account name** and works unelevated on a
  directory your own user locked. `Get-Acl`/`Set-Acl` can fail with `SeSecurityPrivilege` because
  `Get-Acl` pulls a section you cannot write back.
- **A mapping failure is not a privilege failure.** If `/reset` ALSO fails, the DACL belongs to a
  different SID and genuinely needs elevation — that distinction is what proved #268 operator-gated
  rather than a tooling quirk.
- **Always restore the ACL and delete the fixture** when the probe finishes.

### The rules that fall out

- **SKIPPED IS NOT PASSED.** Read the skip count, every time. A green suite can report proof that
  never ran. If a test needs an env gate or a built binary, grep whether a job actually provides
  BOTH — `tests/unit/test_native_e2e_ci_coverage_contract.py` now asserts this as an invariant.
- **A golden diff is evidence about the harness+product PAIR**, never the product alone, whenever the
  harness post-processes before comparing. Reading one as a product defect sent an agent hunting an
  emitter that did not exist.
- **A gate's clearance is a hypothesis — and so is its ROOT-CAUSE STORY.** Verify the diagnosis, not
  just the finding. Relaying an unverified root cause nearly produced CI plumbing that already existed.
- **Isolation-level evidence is not outcome-level evidence, and this binds PROSE.** Measuring a helper
  in isolation and writing it up as a user-visible failure is the both-arms trap in claim form. In #747
  an isolated `workspace_root_guard=False` became "the guard never fires"; the control arm through real
  `main_entry()` showed it fires IDENTICALLY in both arms — the defect was latent. **A confidently-wrong
  comment is worse than none.**
- **When you cannot observe RED, say so.** CPU-SAFE forbids compiling, so a Rust fix often cannot watch
  its own test fail pre-fix. The correct move is a STRUCTURAL argument from pinned source, *stated
  plainly as an argument* — never dressed as an observation. Gates judge whether the chain closes; the
  disclosure is expected behaviour, not a defect.
- **Prefer an invariant to an enumeration.** An enumeration is correct when written and silently
  incomplete on the next addition. Three fixes this session replaced one: `_TG_ONLY_SEARCH_FLAG_PREFIXES`
  parity (#272), the CI-coverage invariant (#749), and the rg-grammar differential model (#745).
- **A modelled gate must be proven able to FAIL.** #745's fuzz gate was validated by reverting one line
  (72 shapes, exit 1), mutation-killing 6/6, and checking its oracle against real `rg --debug`
  (301/301). A green gate that cannot fail is worse than no gate.
- **A control that reproduces the failure is SUFFICIENT, not proven OPERATIVE.** A two-arm control
  matched CI's failure byte-for-byte (same exit code, same stdout) and was called "confirmed"; a fix
  was dispatched on that basis. `.github/workflows/ci.yml:688` then showed the failing job never
  builds the binary the control had forced into the mix -- the reproduction was real, the mechanism
  was not the one CI runs there, and the agent had to be recalled mid-flight. Say "sufficient" until
  you have traced that the SAME mechanism fires in the real failing job; a control earns authority
  only by REPRODUCING the failure in one arm -- "the unmodified control still passes" rules out
  nothing on its own.
- **A source-scanning census can be satisfied by a COMMENT.** A census asserting the literal `"--"`
  sentinel appears in each argv builder's body was checked with a substring scan over the whole
  function body -- prose included. Three of five members could have their real `append("--")` call
  DELETED and stay green, because the comment explaining the sentinel still contained the string. The
  better a guard is documented, the less it is actually checked -- the false-NEGATIVE mirror of the
  quoting-vs-asserting trap (there prose causes a false positive, here a false negative). Match AST
  nodes or assert BEHAVIOUR; never a bare substring scan over a region that also contains prose.
- **Presence is a proxy; position is the property.** The same argv fix shipped a sentinel sitting
  BETWEEN two positionals -- present in the argv, and useless, because the first value was still
  parsed as a flag ahead of it. A presence-only check ("does `--` appear anywhere in argv") cannot
  distinguish that from a correctly-placed one. A proxy that cannot tell the fix from the bug is not a
  check -- assert WHERE the marker sits relative to the values it must protect, not just that it
  exists.
- **A census member that names a branch and cannot fail on it is worse than no member.** An entry
  declared a `--stdin` configuration "covered" while asserting only "no dangling trailing `--`" --
  injecting a caller-supplied path into exactly that branch left the suite green, because nothing in
  the assertion could ever fire there; it reported the branch as checked when the check was
  structurally incapable of touching it. Derive the property from the artifact instead of
  hand-writing per-entry expectations ("if a caller-supplied value appears in argv at all, it must
  follow `--`" holds for every configuration without deciding in advance which ones have
  positionals) -- a guard whose placement is config-conditional has as many members as
  configurations.
- **A count is blind to an order swap.** Two members asserted `len(tail) == 2` on the theory that
  both positionals were generated values and therefore safely un-pinnable by content. Swapping a
  probe's pattern and path kept the count and passed, while in production it now searches a
  directory NAMED like the pattern for a pattern that is really the temp path. The justification was
  also half wrong -- both positionals were hardcoded literals the whole time and could have been
  pinned by VALUE from the start. A length assertion proves membership, never order; when order
  carries meaning, assert the values at their positions.
- **A mutation that does not apply is a control arm that never ran.** A red-arm attempt silently
  no-op'd because the reversion string it tried to substitute did not match the file on disk -- the
  "failing" run was really the unchanged test passing, caught only because the EXPECTED failure
  message was absent from the output. Assert the mutation actually applied (diff the file, or grep
  for the string you just removed) before trusting any red/green arm it produces -- this is Form 6's
  fixture-never-applied trap on the code side of the boundary instead of the environment side.
- **A control arm that states what the CALLEE accepts is a control the defect agrees with.** The `-q`
  regression (#876, fixed #880) shipped with its own test requiring `-q` to appear ALONGSIDE
  `--count`/`-l`, on the reasoning "rg accepts the combination and suppression wins on stdout" --
  true of rg, irrelevant to tg, which PARSES that stdout. Measured on the real binary:
  `rg --count-matches needle f.txt` returns `"2"`; with `-q` it returns `""` -- so
  `tg search -q --count` on a MATCHING file reported `total_matches=0`, exit 1: a false no-match and
  an exit-contract violation. The test and the defect encoded the same wrong theory, so neither could
  catch the other. When writing a control arm, state what the CONSUMER does with the value, not what
  the callee permits -- "the tool accepts X" is not "our use of X is correct".
- **A probe built at the seam where the defect was INTRODUCED cannot see it -- build it at the seam
  the value CROSSES.** The same regression's test built its argv via `RipgrepBackend._build_cmd`,
  precisely the shared builder where `-q` had been wrongly placed (it holds ~30 flags and has FOUR
  consumers, only ONE of which streams; the other three parse rg's stdout, which `-q` empties), so
  the test was structurally incapable of showing the difference. The fix retargeted the capture to
  `run_subprocess`, where the argv actually leaves the process, with an `assert captured` arm so an
  inert capture FAILS rather than returning an empty value that passes everything. This is Form 5's
  topology trap on the test-construction side: convenience picked the seam, and the seam was the bug.

- **A BLOCKED instrument and a definitive negative are the same shape on screen.** The false-zero
  bullets above cover a probe that RAN and measured nothing; this is a probe that could not run YET
  and answered anyway. Four instances in the 2026-08-01 campaign: an `awk` range that never matched
  (read as "the CI job does not build the binary"); `pytest --collect-only | grep -c` returning 0
  because `-x` aborted collection two files before the target; `gh run view --log` returning 0 lines
  because the RUN was `in_progress` even though the JOB had already concluded failure; and
  `uvx --from <pkg>==<ver>` reporting the version does not exist when it was a stale uv index cache
  and the wheels were on PyPI. Before believing a negative, prove the instrument can return non-zero
  ON THIS INPUT, NOW. For a package index, `--refresh` is not always enough (`uv cache clean <pkg>`
  is), and the discriminator between "release failed" and "index stale" is the PyPI files endpoint
  `/pypi/<pkg>/<ver>/json` -> `urls[]`. For CI, query the job's `conclusion` and failing STEP name,
  which are available while the run is still going.
- **Separate ROUTING from EVALUATION before asserting end-to-end.** A `--ltl` e2e test asserted
  `returncode == 0` through the native binary and failed on all four OSes -- permanently, because
  `native-build-smoke` runs `cargo build --bin tg` and never builds the PyO3 extension the LTL engine
  needs. Two separable properties had been fused: the front door FORWARDING the flag (needs only the
  binary) and the sidecar ANSWERING it (needs the extension). Assert the one the change actually
  delivers unconditionally, and gate the other on the capability being present -- but keep the
  degraded arm DISCRIMINATING (here, a fail-closed "search backend failed" is textually impossible
  for a clap rejection to produce, so the routing-only arm is still evidence). And measure against
  the SHIPPED artifact before relaxing anything: relaxing an assertion without confirming real users
  are unaffected is how a genuine defect gets defined away.
- **Check that some CI job can execute BOTH sides of a boundary you are testing.** `test-python` has
  the Python deps but never builds the release binary; `native-build-smoke` builds the binary but
  installed only `pytest`. So no job could test native->sidecar delegation end to end -- the surface
  was untestable and the only possible symptom was a test nobody had written. It surfaced only
  because an audit forced the new test into the job where a skip becomes a hard FAILURE; in its
  original home it would have skipped silently and reported green.
- **A test can assert a PROXY that cannot distinguish the fix from the bug.** The CWE-88 tests
  asserted `not token.startswith("-")` and never path IDENTITY. Two mutations passed undetected:
  prefixing only the exact fixture value (leaving `--evil.ini` and bare `-` injectable), and
  returning a wholly WRONG filename so the validation command checked a different file. Parameterize
  the shapes AND assert the value resolves to the intended target; the same mutation then killed 45
  tests. Ask of every security assertion: what is the cheapest wrong implementation that still
  passes this?

- **After a fix, a grep hit is often the fix's OWN DOCUMENTATION -- and this fires while you are
  verifying SUCCESS.** The census-satisfied-by-a-comment trap has a mirror on the far side of the
  repair. `grep -c "cast(ComputeBackend,"` returned 1 after the NameError fix (the docstring
  explaining the trap); `grep -c "requires_ast_grep_wrapper"` returned 1 after a shim collapse (the
  docstring explaining the drift). Both read "still broken"; both were fixed. **Self-demonstration:
  one turn after writing this rule, a dogfood probe of the published wheel used
  `'requires_ast_grep_wrapper' in ast.unparse(fn)` -- `ast.unparse` INCLUDES the docstring -- and
  printed `VERDICT: REGRESSION` on a correct artifact.** Count AST NODES, never string containment
  over a region that also holds prose: `[n for n in ast.walk(fn) if (isinstance(n, ast.Name) and
  n.id == TARGET) or (isinstance(n, ast.Attribute) and n.attr == TARGET)]`. A verification probe is
  code and deserves the same scrutiny as its subject -- MORE, when it is about to certify a ship.
- **A list written at DISPATCH time is stale by definition; derive the set at USE time.** Three
  instances in one session, the third authored AFTER the first two were documented: a monitoring cron
  hardcoding two PR numbers (orphaning a third opened later), an eligibility filter reading only the
  FIRST LINE of multi-line board items (marking a CEO-gated entry ELIGIBLE because the gate sat on
  line 3), and a merge drain hardcoding three PRs (orphaning two opened after). Any loop that can
  enumerate must enumerate (`gh pr list --state open` per pass), and multi-line records must be
  ACCUMULATED before matching -- a line-based filter silently truncates the thing it is judging.
  Writing the law does not immunise you; only the structural fix does.
- **`git stash` is unsafe for a red-arm revert once parallel worktrees exist.** Worktrees SHARE
  `.git`'s stash refs, so N agents in N worktrees reach into one drawer -- an agent's `stash pop`
  took a different agent's stash and conflicted a file it had never touched. Use `git checkout --
  <file>` against a known commit, or a patch file. To rescue an orphaned stash non-destructively:
  `git branch <rescue-name> stash@{0}` (creates a permanent ref, does not check out or pop).
- **A subagent's "committed locally, not pushed, per instructions" is an OBLIGATION, not a
  completion.** A 27 KB investigation sat unpushed in a worktree while its findings were read,
  reported and acted upon -- the artifact reachable by nobody. Land it in the same turn you consume
  its conclusions. Same class: reconcile the board AT completion, never "next cycle" -- staleness
  accrues exactly one deferral at a time.

### 2026-08-12 campaign bullets (dated)

- **RED-by-design is valid ONLY for the EXPECTED failure-reason class (A61).** A planned-RED test
  earns its red only when it fails with the expected AssertionError/reason class. A collection
  error, import failure, fixture/setup crash, or any die-before-the-contract exit is a GATE
  FAILURE, not a behavioral RED — it proves the harness broke, not that the contract was
  exercised. Pin the exact expected refusal/reason in the assertion and reject any arm that never
  reaches it.
- **Workflow receipt verification must aggregate EVERY per-node receipt and require union ==
  expected manifest population.** One valid node receipt cannot clear a job: the verifier must
  collect all per-node receipts, take their UNION, and assert that union equals the expected
  manifest's full node population — a subset match (or a single node's success) reading as
  "workflow verified" is the false-zero family applied to receipts.
- **Live CI evidence binds to the EXACT tested tree and is recorded OUTSIDE it.** A CI verdict is
  evidence about specific SHAs — record head + base + merge-ref explicitly — and the evidence
  itself lives OUTSIDE the tested tree (a PR comment, a docs PR), never committed to the tested
  branch as self-covering proof of itself. A receipt that only exists inside the tree it vouches
  for is circular.
- **Shared-filesystem timing wobble must reproduce in ISOLATION before widening any bound.** On a
  shared dev/CI box, a one-off timing failure is a hypothesis about contention, not about the
  bound: reproduce the wobble in an isolated run first; if it does not reproduce, the correct
  response is suspect-instrument (the shared filesystem), never a looser assertion — widening a
  bound on an unreproduced wobble converts a shared-resource artifact into a permanent weakened
  contract.

### A89 — the REAL-ARTIFACT parity arm (2026-08-08/09, M17 wave)

A parity test whose "real" arm is FAKE-BACKED (a stub producer standing in for the shipped binary,
a `range.byteOffset` field never emitted by any real backend) makes three arms agree on the bug
itself: the schema test, the fake-backer, and the product all "confirm" the shape that the real
artifact would have broken. The M16/M17 receipts (A89, 2026-08-08) are this family applied to
oracle INPUTS: when the real producer is cheap to invoke, ADD A REAL `ast-grep --json` subprocess
arm (or equivalent real-binary arm) — a fake-backed arm is a hypothesis about what the producer
does, not a measurement. Rule: every parity claim names WHICH producer backed each arm
(`fake-backed` vs `real-artifact`); a suite whose arms are all fake-backed proves only that its
fakes agree with each other.

### A87 — static review is not a typecheck (2026-08-08/09, receipts #987/#988)

Two Rust PRs passed codex static audits and failed the FIRST real compile — #988 survived three
audit rounds then hit E0599/E0308/E0382; #987's regression surfaced only on the full matrix (its
author's self-gate never ran `tests/unit/test_backend_bug_fixes.py`). Rules: for Rust, "static SHIP"
is `SHIP-PROVISIONAL` until the first CI compile of the head SHA completes (the first compile IS the
typecheck gate); a self-gate's suite selection is a hypothesis until the matrix runs — state which
suites ran and which were skipped alongside any self-verified result.

- **A CHECK-RUN LIST WITH UNEXPANDED MATRIX PLACEHOLDERS IS A SKIPPED JOB, AND IT LOOKS LIKE A
  PASS (2026-08-19).** A green PR whose checks read `test-python (${{ matrix.os }}, py${{
  matrix.python-version }})` did not run those lanes -- the job was never instantiated, so the
  matrix was never expanded. Any summary that counts `failures == 0` reports this as success.
  Measured three times in one campaign: a 3,500-line refactor merged having run ZERO tests; the
  doc/skill governance suites never ran on doc changes; and the FORMATTER never ran on doc
  changes, so a docs PR reddened `main` and surfaced days later on an unrelated code PR. The
  root cause is the same each time -- `ci.yml`'s cost-smart `changes` filter is a HAND-WRITTEN
  PATH LIST, and it watched a narrower set than the jobs behind it depend on. **Before calling a
  PR green, require EXPANDED lane names** (`awk -F'\t' '$1 ~ /test-python \(/'` and assert a
  count), not merely zero failures. None of the three was found by reading `ci.yml`.

- **A CI JOB'S NAME IS NOT ITS FAILURE (2026-08-19).** `Formatting & Linting` runs ruff AND mypy;
  it failed on mypy while a cycle was spent reproducing ruff. Read the failing STEP
  (`gh api .../jobs/<id> --jq '.steps[] | select(.conclusion=="failure") | .name'`) before
  reproducing anything.

- **A SPLIT HAS A HARD FLOOR SET BY TEST-PATCH TOPOLOGY, AND mypy ADDS A SPLIT-ONLY FAILURE CLASS
  (2026-08-19).** Python resolves bare names through the DEFINING module's globals, so every
  function referencing a monkeypatched name by bare identifier must stay co-located with wherever
  the test's `setattr` lands. Measured on `benchmarks/run_gpu_native_benchmarks.py`: that
  call-graph closure is 1,752 lines / 17 functions, so the file CANNOT reach a 1,500-line limit by
  splitting. Derive the closure before scoping a wave -- a line count is not a split plan, and
  lowering a ratchet pin is the honest outcome where the floor binds. Separately, after any split,
  `from .impl import X` in the facade is a PRIVATE binding under mypy's `implicit_reexport =
  false` (runtime resolves it; mypy fails `attr-defined`). It cannot appear pre-split, because the
  symbol was locally defined and nothing was re-exported. Use `from .impl import X as X`, then
  re-check that `ruff --fix` did not merge the import blocks back and drop names -- its organiser
  silently dropped six in this same campaign.

Related global skill: `measure-what-it-claims` (same family, generalised beyond this repo).

## Part 1 — What counts as evidence here (in order of trust)

Ranked by how hard each is to fake, cheapest-to-check first:

1. **A failing test written before the fix** (TDD-first). `CONTRIBUTING.md` "Performance Discipline":
   *"Start with a failing test when behavior changes."* Repeated in `AGENTS.md` Operating Rules #1
   (`AGENTS.md:389`). If you cannot point to the test that failed before your diff, the fix is
   unverified — see `superpowers:test-driven-development`.
2. **A contract test**, not just a behavior test. This repo names them `test_*_contract*.py` /
   `test_*_contracts.py` (e.g. `tests/e2e/test_backend_contracts.py`,
   `tests/e2e/test_io_contracts.py`, `tests/unit/test_main_cli_contracts.py`,
   `tests/unit/test_rg_contract.py`). A contract test asserts an invariant that must hold for *every*
   implementation of a protocol (every `ComputeBackend` must expose `.matches` /
   `.total_matches` / `.is_empty` — `tests/e2e/test_backend_contracts.py:8-12`), not one code path's
   happy case.
3. **Dogfood on the real binary, not `CliRunner`.** `tests/unit/` uses Typer's `CliRunner` 400+ times
   (`grep -rc CliRunner tests/unit/*.py`) — `CliRunner` calls the Typer `app` object directly and
   **skips `tensor_grep.cli.bootstrap:main_entry` entirely**, so a routing bug in the bootstrap front
   door (the layer that intercepts plain-text searches and forwards them to `rg` *before* Typer ever
   sees `argv`) is invisible to it. This is not hypothetical: the `tg search --rank` flag shipped
   broken to real users while every `CliRunner` test stayed green, because the flag was missing from
   one of the two search-flag front doors (`CONTRIBUTING.md:73`, `AGENTS.md:411-418`). After any
   command/flag/routing change, run the real binary: `python scripts/dogfood/dogfood_features.py`
   (installed `tg` on PATH) or the clean-room Docker path in `scripts/dogfood/README.md`. See
   `dogfood-the-shipped-artifact` (global skill) and `tensor-grep-change-control` Part 5.

   **Cold-path caveat — dogfood proves routing correctness, not a performance claim (the single most
   load-bearing gap in this discipline).** A dogfood/`tg orient` run mostly exercises a WARM, cached
   path — repo-map/AST-parse state already populated from a prior call — so it can misjudge a change
   whose effect is COLD-path-only. Receipt: a warm end-to-end `tg orient` dogfood read the
   `_python_imports_and_symbols` walk-merge (`src/tensor_grep/cli/repo_map.py:2166` — re-derive with: grep -n '_python_imports_and_symbols' src/tensor_grep/cli/repo_map.py) as **−36% slower**;
   an isolated cold microbench of the same function (fresh process, single pass over distinct inputs)
   showed it is actually **~54% faster** (961ms→446ms) — the warm run never exercised the changed code
   path. To validate a cold-path optimization, microbench the target function directly or clear the
   cache between reps; never trust a single warm end-to-end dogfood run as the sole evidence for a
   performance change (pair with `tensor-grep-benchmark-and-proof-toolkit`; see Part 1 point 15 below
   for the same warm/cold discipline applied to a whole-campaign verdict pass).
4. **Fixture-green is not sufficient for a precision/heuristic feature — dogfood the real corpus, not
   just the fixtures you wrote alongside it.** A test suite authored together with a detection
   heuristic tends to only contain the cases the author already thought of; the failure mode that
   actually matters (flooding false positives) never shows up until the heuristic meets a real, larger
   corpus. Receipt (2026-07-03): the `tg diff-docs` MVP (round-4 design-council build, commit
   `90b7042` "wip: tg diff-docs foundation (DEFERRED — precision inadequate, see task)" on
   `wip/diff-docs-precision`, **not merged to `main`**) shipped with 17 green tests in
   `tests/unit/test_diff_docs.py` (`grep -c "def test_"` on that commit) — every fixture passed — but a
   dogfood run against this repo's real `docs/` vs `src/` corpus produced on the order of 20,000
   findings, the large majority flagging language/stdlib types (`String`, `Option`, `Vec`) as
   "unresolved symbols" because nothing in the design gated on a *positive* in-repo reference signal.
   `diff_docs.py`'s own module docstring names the mechanism up front: naive code-doc drift detection
   is independently measured at 0.62 precision / 98% flag-rate (DocPrism, arXiv 2511.00215) — this is
   the expected failure mode of the whole naive-heuristic class, not a one-off implementation bug. The
   correct call was to **defer, not ship**: 20k false positives trains the agent to ignore the tool,
   which is worse than not shipping it. Before shipping any precision/heuristic feature (doc-drift,
   ranking, classification, dedup), run it on this repo's own real corpus and eyeball the finding count
   and the top hits — a green fixture suite alone cannot catch a flooding failure mode.

   **2nd receipt (2026-07-16, `tg find` campaign #189, commit `173e093`/#630) — same trap, a different
   FAILURE SHAPE.** The 1st receipt above is a **volume** failure (thousands of extra findings); this
   one is a **shape** failure (systematic misclassification with a normal-looking finding count). The
   `TG_FIND_DENSE_WEIGHT` query classifier that scopes the adaptive dense-weight boost to genuinely
   multi-word queries was built and fixture-tested against `benchmarks/datasets/literal_golden.jsonl` —
   green. A real-corpus dogfood against tensor-grep's own `src/` then found the classifier mis-boosting
   **5 of 6** literal-identifier queries (`_confine_mcp_path`, `getUserName`, `BackendExecutionError`,
   `reciprocal_rank_fusion` — all multi-morpheme under `split_terms()`, the classifier's original gate)
   — the fixture set happened to be built from queries the morpheme-count heuristic classified
   correctly by chance, so green fixtures hid a systematic bug in the classifier's core logic, not an
   edge case it forgot to cover. Fixed by switching to a whitespace word-count gate
   (`len(query.split()) <= 1` -> literal). **Both receipts share the same root lesson (fixture-green is
   not real-corpus-safe) but manifest oppositely** — check both a flooding COUNT and a systematic
   MISCLASSIFICATION PATTERN when dogfooding a precision/heuristic feature, not just one.
5. **A routing/delegation change is only proven by `tests/integration/`, run with the native `tg`
   binary built — not `tests/unit/` alone.**
   `tests/integration/test_bm25_search_flag.py::test_search_rank_reorders_by_bm25` read `capfd` (the
   OS-fd-level stream) because, before commit `5e6f780` (#342), `--rank` silently delegated to the
   native subprocess, which is what actually wrote to that fd. Fixing the delegation gate to refuse
   `--rank` (so the BM25 rerank runs in-process) moved the JSON emission to a different capture channel
   — `typer.echo` -> `CliRunner`'s captured `result.stdout`, not the fd — and broke `main`'s release
   the same day. Commit `ab717a1`'s own message: *"#342 ... merged but its release failed:
   `test_search_rank_reorders_by_bm25` read fd-level `capfd`, which only captured output when `--rank`
   *wrongly* delegated to the native subprocess. ... Only surfaces on main/release CI, which builds the
   native binary; PR CI skips it."* (fixed by reading `result.stdout` instead of
   `capfd.readouterr().out`). Two takeaways: (a) `capfd` in a `CliRunner` test is an implicit assertion
   that a **real subprocess** wrote to the OS stdout fd — any change to whether a code path delegates
   natively or runs in-process can silently break that assertion in either direction, and a green
   `tests/unit/` run will not catch it because the native binary isn't built there either; (b) before
   trusting a routing/delegation change, rebuild the native binary locally
   (`cargo build --manifest-path rust_core/Cargo.toml --bin tg`, or add `--release` — see
   `tensor-grep-build-and-env`) and run `uv run pytest tests/integration -q` — PR review alone builds
   neither, so a `--rank`/`--sort-files`-class bug is invisible until it reaches `main`.
6. **A benchmark line vs the accepted baseline**, for any hot-path/speed claim. Never trust a
   microprofile or memory of "it felt faster." Full decision table and noise-floor rules live in
   `tensor-grep-benchmark-and-proof-toolkit` — do not duplicate that table here; use it.
7. **The agent-readiness gate** (`scripts/agent_readiness.py`, wrapped by `tg dogfood`) — a CI-blocking
   fast dogfood of agent-critical surfaces (Part 4 below).
8. **Live extension call for FFI/PyO3 changes**, never a mock alone. A mock-based bridge test passed
   green while the real PyO3 extension silently dropped every forwarded flag and fell back to the
   Python engine — the mock could not see it. Prove an FFI change by calling the *built* extension at
   runtime and checking the flag actually reached `rg`.
9. **A subagent's "tests pass" is a hypothesis, not evidence**, until re-run against external state —
   an exit code you observed, a `file:line` that resolves, or a real dogfood run. This applies doubly
   to worktree-fanout branches: a worktree has no `.venv`, so a subagent's claim is *literally
   un-runnable in its own tree* until re-run in the real environment.

   **Byte-identical-optimization proof technique.** When a change claims to MERGE or SKIP work (not
   just refactor), "tests pass" alone is not enough — prove the output is byte-identical two ways: (a)
   **enumerate every producer/branch** and argue exhaustiveness (e.g. AST node types are mutually
   exclusive; a token is always a substring of its own string; candidate names are a subset of the
   file's text, so a term absent from the text cannot be a candidate); (b) **differential fuzz** — run
   OLD-vs-NEW over N real files and assert 0 mismatches (a 386-file / 26-case sweep is the shipped
   precedent). Treat a build agent's own byte-identical claim the same as its "tests pass" claim above
   — a hypothesis until an INDEPENDENT reviewer re-runs the fuzz pass; that independent gate, not the
   build agent's self-verify, is the proof-of-record.

   **Corollary — a clean git rebase is not proof of correctness.** When several branches in a drain
   each edit the SAME shared file (e.g. a language registry test's assertion set, a pyproject extras
   list, `uv.lock`) and are rebased onto each other sequentially, a rebase that lands with **no
   conflict markers** is not evidence the result is correct — git's line-level merge can silently drop
   an import or fail to union two branches' assertions without ever raising a conflict. Always re-run
   the affected test suite after every rebase in a multi-branch drain, not only when a conflict marker
   forced a manual look; a dropped import surfaces as an `ImportError` the rebase itself will never
   flag.

   **Amendment (2026-07-24) -- PYTHONPATH alone does not prove a RED-GREEN baseline; it only proves
   which tree a normal run imported.** The standing stale-venv fix above ("pin PYTHONPATH to the
   worktree src, verify `tensor_grep.__file__` resolves into the worktree") is necessary but NOT
   sufficient the moment the goal shifts from "run tests against the right tree" to "prove this test
   genuinely fails on a BASELINE commit" (pre-fix origin/main, a tag, a reverted file) -- the two are
   different claims and need different verification. `tests/conftest.py:10` runs
   `sys.path.insert(0, str(SRC_DIR))`, where SRC_DIR is derived from `conftest.py`'s own `__file__`
   location, and `sys.path.insert(0, ...)` takes precedence over whatever PYTHONPATH points at. An
   independent gate that reverted one source file to its pre-fix state and re-ran with PYTHONPATH
   pointed at that reverted tree got a FALSE "passed on main" result, because the worktree's OWN
   `conftest.py` silently re-pointed imports back at the worktree's unmodified src regardless of
   PYTHONPATH -- and checking `tensor_grep.__file__` did not catch it either, since it correctly
   reported the worktree's file, which was exactly the wrong tree for a baseline check. Fix: to prove
   a test fails on a baseline commit, use a FULLY ISOLATED TREE COPY with the target file reverted
   INSIDE that copy -- never a PYTHONPATH swap layered on top of the current working tree's own
   `conftest.py`. This matters most for exactly the readers of this skill's Part 1 -- an independent
   gate proving a fix's red-phase test is real, not a build agent's self-report of it.
10. **A security-touching change is not "done" on green tests alone — it needs a mandatory adversarial
    review before merge.** Any PR touching `apply_policy`, `mcp_server`, `cpu_backend`/native-argv
    construction, `index_lock`, auth, money, a migration, or **native asset / installer / doctor-probe
    construction** gets a dedicated "try to BREAK this, cite `file:line`, default to FIX-FIRST if
    uncertain" pass — not a rubric checklist, an actual attempted exploit. This is not theoretical: this
    exact gate caught a real symlink-follow RCE bypass (`.resolve()` following the symlink before the
    containment check) and a lock-release TOCTOU that a green test suite missed on both. The
    native-asset/installer/doctor-probe addition is the v1.75.2/v1.75.3 GPU Phase-0 precedent -- PR #596
    (P0-5, loud nvidia-to-cpu installer downgrade) was held in draft with an explicit "Opus gate pending
    before merge" per its council-reviewed plan, because a silent wrong-flavor install or a misleading
    `doctor` probe status is a security-relevant integrity failure, not a UX nit. Route security-review
    model selection through `feedback-fable5-cyber-classifier-audit-on-opus` (global memory) — run
    vuln-hunting turns on Opus/Sonnet, not Fable (its cyber classifier silently falls back mid-turn).
    Verdict is binary: `SHIP` or `FIX-FIRST(file:line + repro + fix)`, never a rubber stamp.
11. **A test that exercises a hang-class bug (ReDoS, deadlock, lock-race, unbounded subprocess/loop)
    must itself be unhangable, or it just relocates the hang into your test run.** Wrap it in an outer
    shell timeout with a kill-after grace period AND the test framework's own per-test timeout (a
    `signal`-based timeout is a no-op on Windows/inside a GIL-held C extension — use a thread-based
    timeout mechanism instead); treat an observed exit `124`/`137` as the failure signal, not a hang to
    debug further. Write the fix **before** the red-phase adversarial test where possible, or run the
    red test already wrapped — an unwrapped catastrophic-backtracking regex test against un-fixed code
    can look indistinguishable from a genuinely stuck build/agent. Never write an unbounded
    loop/spawn/backtrack-prone pattern into a test without an explicit bound. Full protocol: the global
    skill `anti-hang-test-protocol`.
12. **An oracle must assert non-empty GOLD-LABELS, not just non-empty predictions — a vacuous-truth
    oracle scores an empty label set as a perfect result.** `retrieval_scoring.py`'s `recall_at_k`/
    `ndcg_at_k` return a vacuous `1.0` for ANY ranking when the `relevant` (gold-label) set is empty —
    a query with a broken/missing golden answer would silently "pass" with a perfect score instead of
    failing loud. `benchmarks/eval_late_rerank_quality.py` (the `tg find` golden-set gate, #189) is the
    positive counter-example worth copying: `load_golden_queries` asserts every query has a NON-EMPTY
    `relevant` set at LOAD time (a loud `GoldenSetError`, not a silent perfect score), and a separate
    `validate_oracle` function proves the METRIC itself behaves correctly — a "gold" ranking (every
    relevant file first) must score `ndcg@k == 1.0` exactly, and a "reversed"/"empty" ranking must
    score AT OR BELOW a computed achievable ceiling, not an arbitrary hardcoded number. Before trusting
    any new golden/oracle-graded query, confirm it has (a) a genuinely non-empty gold-label set and (b)
    a metric that demonstrably fails on a deliberately-wrong answer, not just passes on a correct one —
    see `tests/unit/test_eval_late_rerank_quality.py::test_empty_gold_label_is_loud`.
13. **A capability-regression gate is a DISTINCT evidence tier from a contract test — a per-task-pinned
    accuracy gate, not a floor.** `tests/eval/test_agent_accuracy.py` (`test_agent_accuracy_gate`, #690/
    #696/#693) runs the golden agent-capsule task set and asserts `not misses` — ANY single golden task
    regressing reds the gate, not an aggregate-score floor that could silently absorb one task's
    regression inside a rising average elsewhere. This is the **loop-4 hill-climbing instrument**: the
    gate itself surfaced a real primary-target ranking bug (#250 — a thin CLI-dispatcher wrapper
    outranking its real implementation), which #693 fixed, lifting the golden set from 15/16 to 16/16.
    Treat a new "`tg prepare`/`tg agent` misrouted in the wild" finding as a signal to ADD a new
    permanently-pinned task here (generalize the finding), not just patch the code and move on — #250
    is the template for this discipline.
14. **A concurrency test must assert the CONTRACT via Event handshakes, never wall-clock overlap (C-concurrency).**
    `tests/unit/test_index_lock_concurrency.py::test_index_lock_is_per_root_not_global` (#701) is the
    worked example: it proves independence (root-B acquires with a bounded timeout while root-A is
    held) AND the converse mutual-exclusion control (root-A's own re-acquire attempt must time out) via
    `threading.Event` handshakes and bounded `acquire()` calls — never by asserting two threads
    overlapped in wall-clock time, which is exactly the assertion shape that flaked for two releases
    (v1.81.1, and again on the first v1.92.2 attempt) on a loaded/scheduler-starved CI runner. A starved
    runner can legitimately serialize two threads that are contractually independent; the test must not
    mistake that for a broken lock.
15. **Published-wheel verdict-table dogfood is its own methodology, distinct from the release-tag-smoke
    CI gate (C-wheel).** After a campaign drains, verify EVERY fixed item individually against the
    PUBLISHED wheel in a clean environment (`uvx --from tensor-grep@<version> tg ...`, never the local
    editable checkout), producing one PASS/FAIL row per item with the raw JSON receipt attached — not a
    single aggregate "dogfood passed" claim. Pre-build any fixture the probes need before the loop
    starts (not ad hoc per-probe). Read the RAW JSON at least once before trusting an automated
    pass/fail verdict — a probe-shape misread reads as a clean pass or fail either way and is easy to
    miss without eyeballing the payload. Watch for pipe exit-code masking: `cmd | tail` or `cmd |
    python -c ...` reports the LAST command's exit code, not `cmd`'s — a real failure upstream of the
    pipe can silently read as success. **Receipt (2026-07-22):** a 7-item closing dogfood against the
    published v1.93.0 wheel ran 7/7 PASS this way, each with its own raw-JSON row, catching what an
    aggregate "looks fine" claim would have hidden. Cross-reference, don't duplicate: the pipe-exit-mask
    and raw-JSON-first traps also live in `tensor-grep-debugging-playbook` (§13/§14) as debugging
    fix-pointers; this item is the QA-tier methodology framing of the same two traps.
16. **A payload-byte-RATIO governance test is fragile to two things a code-review pass rarely
    checks: a SHARED envelope field growing, and tmp-path LENGTH across platforms (#733/#734,
    2026-07-24).** `test_importers_payload_is_far_smaller_than_map` (`tests/unit/test_file_deps.py`)
    compares total serialized bytes of `tg importers` (deliberately tiny) against `tg map`
    (deliberately large) as a proxy for "importers carries far less data." That proxy breaks the
    moment a field in the SHARED `_envelope()` helper (`repo_map.py`, stamped byte-identically onto
    both payloads) grows: an honest field-honesty fix in #733 made `coverage.language_scope`/
    `symbol_navigation` dynamic (~28 -> ~116 chars) — a small fraction of the large `map` payload
    but a large fraction of the tiny `importers` one, tripping the `< 0.1 * map` threshold with
    **zero** change to either payload's actual data volume. Separately, the SAME test PASSED on
    Windows and FAILED on Linux CI: Windows' longer `AppData\Local\Temp` paths inflate both
    payloads and dilute the fixed-envelope fraction back under threshold; Linux's short `/tmp`
    paths do not, so a Windows-local green run does not prove a Linux-CI green run. **Fix (#734):**
    strip the shared `_envelope()` keys SYMMETRICALLY from both payloads before comparing bytes,
    deriving the excluded-key set LIVE (`set(repo_map._envelope(project))`) rather than a
    hand-copied literal — this makes the assertion robust to any future envelope growth instead of
    re-breaking on the next honesty fix. Before adding or reviewing any payload-byte or
    payload-ratio assertion: (a) check whether either side's payload includes a shared, non-data
    envelope/header the assertion doesn't intend to measure, and (b) reproduce with a SHORT
    `pytest --basetemp` locally before trusting a Windows-local pass as proof of a Linux-CI pass.
    (c) **Measure bytes with a BINARY read, never `Path.read_text()`.** `read_text()` applies
    universal newlines, so every CRLF in the file collapses to one LF and the count comes back
    SHORT on Windows — a budget check written that way silently passes a file that is already over.
    Caught on a 17.1 KB doc budget that `read_text()` reported as comfortably under while `wc -c`
    showed it over. Use `len(path.read_bytes())` (or `wc -c`) for any size/ratio assertion; keep
    `read_text()` for content matching, where the normalisation is what you want.
17. **A self-gate's declared test SUBSET is not the full CI matrix — state what ran, not just that
    "tests passed" (#733/#734, 2026-07-24).** The build agent's own pre-merge gate on #733 ran a
    real, substantial suite (`test_harness_api_docs.py`, `test_session_cli.py`,
    `test_mcp_server_*.py`, the full `lang_registry`/`test_lang_*.py` sweep) but never named
    `tests/unit/test_file_deps.py` — the exact file whose invariant #733 broke, deterministically,
    on every platform. This is an instance of Part 1 point 9 ("never trust a self-report") applied
    to test **scope**, not just test **result**: a subagent that reports "N tests passed" without
    naming which suites it ran and which it did NOT is not proof of a clean merge, even when every
    number it reports is true. When reviewing or writing a self-gate report, require an explicit
    ran/skipped suite list, and treat the CI run itself — not the self-gate's own suite selection —
    as the actual merge arbiter.
18. **A test proves nothing until you have SEEN it fail on the pre-fix baseline — "I added a test,
    it's green" is not coverage (#737, 2026-07-24).** An independent Opus gate on the C++
    function-pointer-variable fix found the new shape-9 test pinned only the IN-CLASS member-fn-ptr
    shape (`class C { void (C::*mp)(int); };`) — which tree-sitter already excluded on pre-fix
    `origin/main`, through an unrelated code path (`['ERROR', 'pointer_declarator']` triggers a
    `len(named_children) != 1` early return that never reaches the fix's new logic). The shape the
    fix actually repaired — file-scope `void (C::*mp)(int);`, wrapping a `qualified_identifier` — had
    NO test guarding it. The fix itself was correct; the first test written for it was a
    no-regression pin, not a bug guard, and would have passed unmodified even without the fix.
    Before trusting any new test as coverage, confirm it goes RED against the pre-fix code (the same
    discipline as Part 1 point 1, restated for the specific failure mode of a test that happens to
    exercise an already-excluded shape).
19. **A ratio/relative timing assertion degenerates to its floor the moment the baseline collapses
    below clock resolution — and the fix requires profiling, not just re-attribution (#739,
    2026-07-24).** A de-flake of a checkpoint-hot-path test stubbed a `git rev-parse` subprocess call
    to make `elapsed < max(baseline * 6.0, 8.0)` "cancel load." With the subprocess stubbed, the
    baseline measured EXACTLY 0.0 across 8 runs. Windows' `time.get_clock_info('monotonic').resolution`
    is **0.015625s** (one 64Hz tick); the stubbed baseline (a `.resolve()` call, an immediate raise, and
    a 1-file `os.walk`) completed within a single tick, so `time.monotonic()` read the same value before
    and after and `elapsed` measured exactly `0.0` (min == max == 0.0000 across all 8 runs). With
    `baseline == 0.0`, `baseline * 6.0` is also `0.0`, so the assertion silently collapsed into a pure
    `elapsed < 2.0` bound (the floor was also lowered), 4x TIGHTER than the 8.0s that had just flaked;
    on the exact cited CI failure (`8.75 < 8.0`) it would still have gone red, by a wider margin. The
    root-cause attribution was also magnitude-wrong: cProfile showed the git spawn was only 6-12% of
    elapsed, while `_prime_bounded_discovery_caches_for_root`'s fsync-heavy discovery-cache I/O was
    ~93% — fsync on a contended CI disk explains a multi-second spike; a process spawn does not.
    **Rule:** before trusting a `max(baseline * N, floor)` assertion as genuinely relative, confirm the
    baseline is measurably non-trivial (check with `time.get_clock_info('monotonic').resolution`);
    before "fixing" a timing flake, profile the actual cost breakdown rather than fixing the first
    plausible-sounding noise source.
20. **Prefer a STRUCTURAL, order-based assertion over ANY wall-clock form — even a ratio one — where
    the invariant allows (same #739, extends point 14 above).** The eventual fix for the same
    checkpoint-hot-path flake wraps `index_lock` plus the suspect expensive calls to emit ordered
    ENTER/EXIT markers into a shared list, and asserts no expensive-work marker falls between the
    lock's acquire and release markers. Marker ORDER is fixed by single-threaded sequential execution
    — a loaded runner delays every marker uniformly without ever reordering them — so the assertion is
    unflakeable BY CONSTRUCTION rather than by a wider tolerance. Verified green on windows-latest
    py3.11 AND py3.12 (run `30130861182`), the exact platform/version combination that had flaked
    twice. Point 14's Event-handshake concurrency pattern and this ENTER/EXIT marker-order pattern are
    the same underlying principle (assert the CONTRACT, never wall-clock timing) applied to two
    different invariant shapes — independence/mutual-exclusion there, ordering here.

---

## Part 2 — Required local validation (run before push)

From `CONTRIBUTING.md:5-14` and `AGENTS.md:654-698`:

```powershell
uv run ruff check .
uv run ruff format --check --preview .
uv run mypy src/tensor_grep
uv run pytest -q
```

For release/workflow/package-manager changes, also:

```powershell
uv run python scripts/validate_release_assets.py
```

Gotchas that each cost a real CI cycle when missed:

- **`ruff format` needs `--preview`; `ruff check` must NOT get it.** CI runs
  `ruff format --check --preview .` but `ruff check .` with no `--preview`. Running
  `ruff format` **without** `--preview` locally is an *active revert* — it rewrites preview-style
  lines back to non-preview style on disk, so the next CI `ruff format --check --preview` fails on
  lines you never touched. Passing `--preview` to `ruff check` produces false failures instead
  (preview lint rules like RUF056 don't match the CI lint gate). (`CONTRIBUTING.md:22`)
- **Windows CRLF false-alarms a bare `ruff format --check`.** `.gitattributes` pins `*.py`/`*.rs` to
  `eol=lf`; run `ruff format --preview <files>` (which normalizes) before trusting a local check.
  Audit real on-disk endings with `git ls-files --eol` — `git show`/`git cat-file -p` smudge output
  and can report false CR. (`CONTRIBUTING.md:24`)
- **Markdown `ruff format --check --preview` on Windows disk is not a blob defect.** `*.md` is not in `.gitattributes` `eol=lf`. A working-tree FAIL with `core.autocrlf=true` can be CRLF-only; pipe the git blob (`git show origin/main:path | ruff format --check --preview --stdin-filename path -`) before opening a format PR. HYGIENE-FORMAT 2026-08-30 retired on this: 15/15 blobs passed, disk failed. (`detect-the-false-green`)
- **`mypy` runs in `strict = true` mode** targeting `python_version = "3.11"` syntax even though the
  repo's CI-tested floor is 3.11-3.12 (`pyproject.toml:559`, `requires-python = ">=3.11"`) — new functions need full type
  annotations (`disallow_untyped_defs = true`); do not rely on inference alone.
- **`uv run` alone re-syncs the environment to default deps and silently drops optional extras**
  (e.g. `[dev]`'s tree-sitter). If a prior step installed extras deliberately, use `uv run --no-sync`
  to keep them — this is exactly what CI's `agent-readiness` job does before running the readiness
  gate (`.github/workflows/ci.yml:150-153`). Forgetting `--no-sync` after an extras install is how a
  "clean" local run diverges from what CI actually validated.
- **A raw `uv lock` churns ~280 unrelated lines — hand-splice a new dependency instead.** Running
  `uv lock` after adding a package reformats GPU/CUDA marker expressions across the whole file (a
  local-vs-CI `uv` version mismatch), burying the real change in noise. For a new dependency,
  hand-splice only its `[[package]]` block (alphabetical position) plus its `requires-dist`/
  optional-dependency references, then verify with
  `uv export --format requirements.txt --all-extras --no-emit-project --locked` (must exit 0) — the
  exact check the `Dependency & License Audit` gate runs (`.github/workflows/audit.yml:12,51`), which
  reds every new-dependency PR that skips it.
- **`pytest` addopts include `-x`** (stop at first failure) — `pyproject.toml:47-52`. Useful for fast
  local iteration, but it means one early failure hides every later one in the same run. For a
  full-suite pass with no early exit, override on the command line:
  `uv run pytest -q --maxfail=0` (the last `--maxfail` value wins over the `-x` baked into `addopts`;
  verified empirically 2026-07-02).
- **The full suite is slow on Windows.** `uv run pytest -q` can exceed 70-90s when the full
  JS/TS/e2e surface is hot; budget at least 120s for narrow suites and much more for the full run
  under automation (`AGENTS.md:667`). Run a narrow suite first for a focused change, e.g.:
  ```powershell
  uv run pytest tests/unit/test_cli_bootstrap.py -q
  uv run pytest tests/unit/test_cpu_backend.py -q
  uv run pytest tests/unit/test_release_assets_validation_*.py -q
  ```
- **Decode the structured CI failure before theorizing.** When a CI check goes red, open its
  structured JSON output (`gh run view <id> --json jobs`, then `--log-failed` on the named job)
  before reading prose tracebacks — it names the exact gate/file/line. A June-2026 README rewrite
  cost 4 wasted CI round-trips because the team theorized from tracebacks instead of decoding the
  failing check first (`CONTRIBUTING.md:26`).
- **A local full-suite `pytest` pass without the native binary built does not prove a
  routing/delegation change.** `resolve_native_tg_binary()`
  (`src/tensor_grep/cli/runtime_paths.py:278`) looks for
  `rust_core/target/{release,debug}/tg(.exe)` first; if neither exists, every `native`-launcher test
  in `tests/e2e/test_routing_parity.py` and the fd-vs-in-process split in
  `tests/integration/test_bm25_search_flag.py` silently **skip** (`pytest.skip(...)`) instead of
  failing — a skip reads as a green summary line, not as "unverified." Rebuild before trusting the
  run: `cargo build --manifest-path rust_core/Cargo.toml --bin tg` (add `--release` to match CI's
  `native-build-smoke` profile). Receipt and full mechanism: Part 1 point 5 (#342/#343).

---

## Part 3 — The certified/golden inventory

Three test surfaces are explicitly named in `CONTRIBUTING.md` "Important surfaces" (`:75-79`) as the
ones that must stay in sync with any workflow/docs/release-asset change. A fourth (routing parity) is
the load-bearing contract behind the "Adding a Command or Flag" rule. Treat all four as CI-blocking
certified truth, not advisory tests.

### 1. Routing parity — Python launchers + native golden output

- `tests/e2e/test_routing_parity.py` runs the **same argv** through three launchers —
  `python -m tensor_grep`, the compiled native `tg` binary, and `bootstrap.py` — and asserts matching
  exit code / stdout / stderr (`run_command`, `LAUNCHERS = ["python-m", "native", "bootstrap"]`,
  `test_routing_parity.py:146-160,163,404-489`). It also pins `PUBLIC_TOP_LEVEL_COMMANDS`
  (`test_routing_parity.py:18-69`) against both Python's and native's visible `--help` command lists
  (`test_top_level_help_visible_commands_match_public_contract`, `:554-564`) and pins
  `PUBLIC_SEARCH_HELP_FLAGS` (from `src/tensor_grep/cli/rg_contract.py:388`) against both
  `search --help` outputs (`:525-537`).
- `rust_core/tests/test_search_golden.rs` is a **Windows-only** (`#![cfg(windows)]`) Rust integration
  test that runs the built native `tg` binary against fixture data in `tests/golden/fixture_data/` and
  diffs the output against committed golden files (`tests/golden/*.txt`, e.g.
  `simple_string_match.txt`, `case_insensitive_match.txt`, `regex_match.txt`).
- CI wires this as the **`search-golden-parity` (windows-latest)** job, which runs
  `cargo test --test test_search_golden` (`.github/workflows/ci.yml:522-547`), and separately the
  cross-platform `test-python` matrix job runs the full `tests/` tree including
  `tests/e2e/test_routing_parity.py` (`uv run pytest tests -v --tb=short -m "not eval"`,
  `.github/workflows/ci.yml:406-413`). Both are required by the `Semantic Release` job
  (`needs: [..., search-golden-parity, ...]`, `.github/workflows/ci.yml:942-943`) — a routing-parity
  regression blocks the release, not just the PR.
- This is the concrete enforcement mechanism behind the "4 registration sites for a command / 2 front
  doors for a search flag" rule in `tensor-grep-change-control` Part 3 — when you add a site, add it
  here too, or the CI registration-completeness gate (blocking since v1.17.1, #282) fails the run.

### 2. Docs governance — content-pinned assertions on docs of record

Several `tests/unit/test_*_docs_governance.py` / `test_*_docs.py` files assert that specific strings
still appear in specific docs, so a docs edit that silently drops a load-bearing claim fails CI instead
of drifting unnoticed:

- `tests/unit/test_public_docs_governance.py` — pins README pointers to canonical docs
  (`docs/benchmarks.md`, `docs/tool_comparison.md`, `docs/gpu_crossover.md`, `docs/routing_policy.md`,
  `docs/harness_api.md`, `docs/harness_cookbook.md`), capability phrases (`"tg calibrate"`, `"tg mcp"`,
  `"native CPU engine"`, `"benchmark-governed"`), and per-release verified-commit/tag markers
  (`test_public_docs_governance.py:1-56`).
- `tests/unit/test_enterprise_docs_governance.py` — pins README links to `docs/CI_PIPELINE.md`,
  `docs/SUPPORT_MATRIX.md`, `docs/CONTRACTS.md`, `docs/HOTFIX_PROCEDURE.md`, `docs/EXPERIMENTAL.md`,
  a `## Future Work` heading, the CI-tested-vs-best-effort Python version matrix, and that
  `docs/CONTRACTS.md` explicitly excludes experimental surfaces (`tg worker`, `TG_RESIDENT_AST`) from
  stability guarantees.
- Sibling governance files worth knowing exist: `test_benchmark_docs.py`, `test_benchmark_governance.py`,
  `test_harness_api_docs.py`, `test_issue_intake_governance.py`, `test_routing_policy_docs.py`,
  `test_stamp_release_assets.py`.
- Full authoring rules (which doc owns which contract, the two governance layers) live in
  `tensor-grep-docs-and-writing` — use that skill when *editing* a governed doc; use this skill to know
  the check exists and is CI-blocking.

### 3. Release-asset validation

- `scripts/validate_release_assets.py` — a standalone validator (`validate_all()`, CLI entry
  `main()`; locate both with
  `grep -n '^def validate_all\|^def main' scripts/validate_release_assets.py`). Since the
  2026-08-19 size-campaign split it is a thin FACADE over `scripts/_release_assets_checks/`
  (ci_workflow, release_workflow, workflow_checks, docs_and_manifest_checks); the primitives the
  tests patch — `_read`, `_version_from_*` — deliberately stayed in the facade, because a test
  patching a module attribute that production no longer reads passes while the code under it is
  unchanged. Invoke and import it exactly as before. It checks
  release/package-manager asset consistency: README canonical-doc links and release markers, `uv.lock`
  editable version parity with `pyproject.toml`/`rust_core/Cargo.toml`/`npm/package.json`, and more.
  Run it directly: `uv run python scripts/validate_release_assets.py` — exit 0 and
  `"Release/package assets validation passed."` on success, exit 1 with one `ERROR:` line per failure
  otherwise.
- `tests/unit/test_release_assets_validation_*.py` (themed siblings split from the former monolith;
  still one of the largest release-governance suites) exercises
  `validate_release_assets.py` module functions directly via
  `importlib.util` rather than shelling out, including
  `test_should_validate_release_and_package_assets_consistency` which just calls `validate_all()` and
  asserts `errors == []` against the *real* repo state — i.e. it fails the instant any of the other
  release-asset invariants regress.
- Related validators worth knowing exist for release *proof* (not just static asset shape):
  `scripts/verify_github_release_assets.py`(→`test_verify_github_release_assets.py`),
  `scripts/validate_pypi_artifacts.py`, `scripts/validate_release_binary_artifacts.py`,
  `scripts/validate_release_version_parity.py`, `scripts/validate_pr_title_semver.py`,
  `scripts/stamp_release_assets.py`.
- CI enforces this via the `release-readiness` job (a strict docs build plus workflow/package-manager
  validator checks, `docs/CI_PIPELINE.md:16`) — also a `needs:` dependency of `Semantic Release`.
  Deep release-mechanics coverage (push-race, PR-title→bump schema) lives in
  `tensor-grep-release-and-positioning`; this skill only anchors it as a certified test surface.

### Golden/snapshot output tests (a fourth, smaller certified surface)

- `tests/e2e/test_output_golden_contract.py` — **21** `GOLDEN_CASES` (derived 2026-08-12; this
  spot was stamped "20" — recount the `GOLDEN_CASES = [` list entries, which live at `:56-89`
  now, was cited `:28-60`; locate with `grep -n "GOLDEN_CASES = \[" tests/e2e/test_output_golden_contract.py`).
  default/`--cpu`/`-o`/`-c`/`-r`/`-n`/binary/`--json`/`--ndjson` combinations run through both
  `python-m` and `native` launchers and compared for output parity.
- `tests/e2e/test_output_snapshots.py` uses the `pytest-snapshot` plugin's `snapshot.assert_match`
  fixture (`pyproject.toml:637`, dev dependency) to pin exact JSON-formatter output, with file-path
  normalization to `<FILE>` so the snapshot stays host-independent
  (`test_output_snapshots.py:5-46`). Marker: `pytest.mark.snapshot` (registered in
  `pyproject.toml:43`).

### Per-task-pinned agent-accuracy gate (a fifth certified surface, `tests/eval/`, new directory)

`tests/eval/test_agent_accuracy.py` is its own top-level test directory, distinct from `unit`/`e2e`/
`integration` — a **capability-regression** gate, not a code-contract test. `test_agent_accuracy_gate`
asserts `not misses` over a golden set of agent-capsule tasks (`#690`/`#696`/`#693`): any single task
regressing fails the gate, with no aggregate-score floor to absorb it. This is the loop-4
hill-climbing instrument for this repo (see Part 1 point 13 above for the full discipline and the
#250 receipt). All 16 golden tasks live inside `src/tensor_grep` itself, which is a known
self-referential-corpus risk (a visible answer key, a Goodhart/contamination surface) — the standing
mitigation is that every real `tg prepare`/`tg agent` misroute found in the wild becomes a NEW
permanent pinned task rather than a one-off patch, generalizing the fix instead of just closing the
symptom.

---

## Part 4 — Agent-readiness / `tg dogfood`

`scripts/agent_readiness.py` is a fast (3-5 minute) CI-blocking dogfood gate for agent-critical
surfaces — separate from, and complementary to, the full local-validation gate (`AGENTS.md:684`).
`tg dogfood` (`dogfood()` in `src/tensor_grep/cli/main.py`, deliberately with NO line number — the one
that used to sit here drifted past the end of the file when main.py was split on 2026-08-20; find it
with `grep -n "^def dogfood" src/tensor_grep/cli/main.py`)
wraps the same check plan with a one-page verdict and an optional `--timeout-s` (default `170.0`) around
the nested readiness process.

Run it directly:

```powershell
python scripts/agent_readiness.py --output artifacts/agent_readiness.json
tg dogfood --output artifacts/dogfood_readiness.json
```

Useful flags on `scripts/agent_readiness.py` (`main()`, `:1258` (re-derive with: grep -n '^def main' scripts/agent_readiness.py)): `--json` (machine-readable
report to stdout), `--no-shell-probes` (skip public shell version probes — used by CI's Linux
`agent-readiness` job), `--only-shell-probes` (Windows-only shell probes, mutually exclusive with
`--no-shell-probes` — used by CI's `windows-agent-readiness` job), `--no-wsl-probe`.

**Acceptance semantics:** the script's exit code is `1 if report["summary"]["failed"] else 0`
(`:1336` — re-derive with: grep -n 'summary.*failed' scripts/agent_readiness.py) — any failed check fails the whole gate; there is no partial-credit threshold. CI wires two
blocking jobs off it — `agent-readiness` (Ubuntu, `--no-shell-probes --no-wsl-probe`,
`.github/workflows/ci.yml:121-157`) and `windows-agent-readiness` (Windows,
`--only-shell-probes`, `:159-193`) — and both are `needs:` of `Semantic Release`
(`release:` job at `ci.yml:1121`, `needs:` at `:1123` — re-derive with: grep -n '^  release:' .github/workflows/ci.yml), so a readiness regression blocks the release the same as a routing-parity regression.

Checks currently in the plan (`build_check_plan`, names verified at
`scripts/agent_readiness.py:698-1009`): `public-version-{powershell,cmd,pwsh-noprofile,git-bash,wsl,
python-subprocess}`, `public-doctor-{cmd,pwsh-noprofile}`, `public-windows-launcher-quoted-patterns`,
`public-search-advertised-flag-sweep`, `repo-cli-build-warmup`, `repo-doctor`,
`context-render-trust` (the `context_consistency` agent-trust check — `AGENTS.md:821,848`),
`rg-parity-edges`, `broad-generated-scan-guard`, `ast-info-json`, `ast-run-smoke`,
`mcp-context-render-smoke`, `mcp-stdio-protocol-smoke`, `agent-capsule`,
`agent-capsule-mixed-language`, `agent-capsule-hardcases`, `docs-claim-check`. This list drifts with
each release — re-verify with the grep in Provenance below rather than trusting this snapshot.

For what a `tg doctor --json` field actually proves (vs merely install evidence), see
`tensor-grep-diagnostics-and-tooling` — this skill only covers the readiness gate as a **pass/fail
CI evidence surface**, not field-by-field diagnostic interpretation.

---

## Part 5 — Benchmark-gated speed claims (summary; depth lives in the sibling)

Never claim a speedup without a measured line vs the accepted baseline (`AGENTS.md:702`,
`CONTRIBUTING.md:37-42`). The **which-script decision table**, the fair-baseline rule, and the
launcher-attribution/stale-binary-refusal rules live in `tensor-grep-benchmark-and-proof-toolkit` —
load that skill before running or reviewing a benchmark. This skill records only the acceptance
**thresholds**, which are QA-gate facts, not benchmark methodology:

| Gate | Default threshold | Where |
|---|---|---|
| `benchmarks/check_regression.py` CLI | `--max-regression-pct` default **5.0%** slowdown fails | `check_regression.py:64,66` (CLI arg) |
| `perf_guard.check_regressions()` (library default, used when no CLI override) | `max_regression_pct` **10.0%** | `src/tensor_grep/perf_guard.py:48-53` |
| Noise-floor filter | rows with `baseline_time_s < min_baseline_time_s` (CLI default **0.1s**, library default 0.2s) are skipped entirely — avoids false regressions from scheduler jitter on tiny durations | `check_regression.py:70,72`, `perf_guard.py:52,76-77` |
| Sub-10ms hot-query rows | use an **absolute** jitter tolerance in addition to the ratio check (a 5% ratio on a 2ms row is noise) | `AGENTS.md:731` |
| CI blocking gate | `benchmark-regression` job runs a same-runner base-vs-head comparison on every PR and every push to `main`, and is a blocking gate before `Semantic Release`, not advisory | `docs/CI_PIPELINE.md:23,42-43` |

If a candidate is correct but slower: **revert it and record the attempt** in `docs/PAPER.md` so no
future agent (human or model) retries the losing idea — see `tensor-grep-research-methodology`.

---

## Part 6 — How to add a test

### Step 1 — pick the directory (what each one means here)

| Directory | What lives there | Run cost |
|---|---|---|
| `tests/unit/` (**re-run `ls tests/unit/*.py | wc -l`** -- 291 on 2026-07-27; do not cite the stamp) | Fast, isolated; heavy `CliRunner` usage (400+ call sites) — good for flag-parsing/formatter/validator logic, **not sufficient alone for routing changes** (Part 1 point 3) | seconds each |
| `tests/e2e/` (**derive: `ls tests/e2e/test_*.py \| wc -l`** — was stamped 16, then 21, derived **22** at v1.110.14 on 2026-08-12; do not re-stamp the number here) | Cross-launcher parity (`python-m`/`native`/`bootstrap`), golden/snapshot output, backend/IO contracts, rg characterization, hypothesis property tests, throughput floors | seconds-minutes; some spawn real subprocesses |
| `tests/integration/` (16 files as of 2026-07-22, up from 11) | Needs real external state — GPU/cuDF, MCP stdio protocol, cross-backend runs, the harness-adoption smoke, `tg orient`/pipeline end-to-end, the `tg prepare` one-shot CUJ (`test_prepare_oneshot_cuj.py`) | slow, sometimes GPU-gated |
| `tests/eval/` (2 files as of 2026-07-24 — `test_agent_accuracy.py`, `test_retrieval_quality_regression.py`) | The per-task-pinned capability-regression gate (Part 1 point 13) — a distinct evidence tier from a contract test, opt-in via its own marker (`-m eval`), not run by a bare `pytest tests` collection the same way as `unit`/`e2e`/`integration` | seconds-minutes; requires a built repo-map over real fixtures |
| `tests/golden/` | Committed golden-output fixtures consumed by `rust_core/tests/test_search_golden.rs`, not itself a pytest dir | n/a |
| `tests/fixtures/`, `tests/schemas/`, `tests/helpers/` | Shared fixture data (`ast_smoke`, `retrieval`), `tg_output.schema.json`, `rg_parity.py` helper (ripgrep binary resolution + `RGContractRow`) | n/a |

`pyproject.toml:34-46` registers `testpaths = ["tests"]` and these markers (apply with
`@pytest.mark.<name>` or a module-level `pytestmark = pytest.mark.<name>`, `--strict-markers` is on so
an unregistered marker is a collection error):

`gpu`, `slow`, `integration`, `acceptance`, `property` (hypothesis-based, see
`tests/e2e/test_reader_props.py`), `characterization` (rg-output parity, see
`tests/e2e/test_ripgrep_parity.py`), `snapshot` (`pytest-snapshot` fixture, see
`tests/e2e/test_output_snapshots.py`), `performance` (see `tests/e2e/test_throughput.py`, which also
stacks `slow` and defines an OS-aware throughput floor that returns `None`/skip on Windows), `eval`
(the agent-accuracy/capsule-ranking golden-set gate — `tests/eval/`, opt-in via `-m eval`, deliberately
excluded from the plain `pytest tests` collection).

### Step 2 — pick the shape

- **Registration/contract change** (new command, new flag, new backend): write the failing test in
  `tests/e2e/test_routing_parity.py` (add the command to `PUBLIC_TOP_LEVEL_COMMANDS` or the flag to
  the relevant sweep) **and** confirm `tests/unit/test_cli_bootstrap.py`'s
  `test_bootstrap_commands_match_source_of_truth` / `test_typer_app_commands_match_source_of_truth` /
  `test_rust_core_uses_source_of_truth` still hold — these three are the existing enforcement of the
  4-site registration rule (`tensor-grep-change-control` Part 3). Do not invent a parallel check; add
  to these first. If the change affects whether a `SearchConfig` field is forwarded to native `argv`,
  refused, or gate-handled, also extend
  `tests/unit/test_native_delegation_field_coverage.py`'s `TestFieldCoverageRatchet` class (AST-derives
  the forwarded set — do not hand-maintain a second list) **and** run `uv run pytest tests/integration -q`
  with the native binary built (Part 1 point 5) — a `tests/unit`-only pass cannot exercise the
  fd-vs-in-process split that a delegation-routing change moves.
- **New language/grammar addition** (extending the symbol-graph tier to another tree-sitter-backed
  language): extend `tests/unit/test_lang_registry.py`'s parity assertions (e.g.
  `test_spec_for_path_resolves_every_registered_suffix`) to cover the new suffix/language — this is
  the enforcement for the `lang_registry.register_language(LanguageSpec(...))` + a self-contained
  `lang_<x>.py` module (mirror `lang_go.py`, `src/tensor_grep/cli/lang_go.py`; not the older inline
  `_rust_*` style). Add a parity-suite case per critical seam the new module must wire:
  `_imports_and_symbols_for_path`, `_imports_with_lines_for_path`, `build_symbol_source_from_map`,
  **`_target_language_for_path`** (most-forgotten — feeds the `tg agent` capsule confidence gate; miss
  it and a target in the new language won't downgrade a mismatched validation-command suggestion), and
  `_SUPPORTED_FILE_DEPENDENCY_LANGUAGES` (all in `src/tensor_grep/cli/repo_map.py`) — a registry entry
  alone does not prove all five are wired. Assert the grammar-missing path fails closed to a labeled
  gap (`provenance_when_missing="grammar-missing"`), never a silent regex fallback. If several branches
  touch this same shared registry test in a drain, re-run the full suite after every rebase, not just
  when a conflict marker appears (Part 1 point 9's clean-rebase corollary).
- **Precision/heuristic change** (doc-drift, ranking, classification, dedup, or any "flag when X looks
  wrong" feature): a green fixture suite alone is not sufficient evidence (Part 1 point 4). Add fixture
  tests as usual, but before claiming done, run the feature against this repo's own real corpus
  (`docs/` + `src/` for doc-drift, the full repo for ranking/classification) and record the finding
  count and a sample of the top hits — if the count floods (thousands of findings on a repo this size)
  or the top hits are dominated by one noisy category, that is a **defer** signal, not a "tune the
  threshold later" signal.
- **Backend behavior change**: extend `tests/e2e/test_backend_contracts.py`'s `_check_contract` shape
  or add a new `test_*_contract.py` — assert the fail-closed invariant (raises
  `BackendExecutionError`, never returns a clean empty result) per `src/tensor_grep/backends/base.py:7`.
- **Output-format change**: add a case to `tests/e2e/test_output_golden_contract.py`'s
  `GOLDEN_CASES`/`EXACT_OUTPUT_CASES`, or a new `tests/e2e/test_output_snapshots.py` snapshot (normalize
  absolute paths to a placeholder before `snapshot.assert_match` — see the existing path-scrubbing
  logic for why naive string replace breaks on Windows JSON escaping).
- **rg-compatibility claim**: add a case to `tests/e2e/test_ripgrep_parity.py` /
  `tests/e2e/test_rg_parity_edges.py` / `tests/e2e/test_rg_parity_matrix.py` — these call the real
  installed `tg` and `rg` binaries via subprocess (`rg_path`/`sample_log_file` fixtures,
  `tests/conftest.py:38,51`) and diff sorted output lines; this is a **dogfood-shaped** test, not a
  `CliRunner` test, precisely because rg-parity claims must survive the real front door.
- **Docs claim**: add or extend an assertion in the matching `test_*_docs_governance.py` /
  `test_*_docs.py` file (Part 3.2) — do not just edit the doc; the assertion is the enforcement.
  Route through `tensor-grep-docs-and-writing` for which doc owns which contract.
- **Release/workflow/package-manager change**: add or extend a case in
  `tests/unit/test_release_assets_validation_*.py` calling the relevant `validate_release_assets.py`
  function directly (via `importlib.util`, see the existing pattern in
  `test_release_assets_validation_docs_and_version_locks.py`) — do not only shell out to the script.

### Step 3 — verify the new test actually enforces something

Run it once against the **pre-fix** code and confirm it fails (TDD-first, Part 1 point 1). A test that
was never observed to fail cannot be trusted to catch a regression.

---

## Part 7 — Pre-claim checklist

- [ ] Behavior change has a test that was **observed failing** before the fix.
- [ ] If it touches routing/commands/flags: the real binary was **dogfooded**, not just `CliRunner`.
- [ ] If it touches native delegation/routing: `tests/integration/` was run **with the native `tg`
      binary rebuilt**, not just `tests/unit/` (Part 1 point 5) — a skip is not a pass.
- [ ] If it is a precision/heuristic feature (doc-drift, ranking, classification, dedup): it was run
      against this repo's **real corpus**, not just its fixture suite, and the finding count/top hits
      were eyeballed before claiming done (Part 1 point 4).
- [ ] If it touches a backend/router: the **fail-closed** contract holds (raises, doesn't return empty).
- [ ] If it touches a hot path: a **benchmark line vs the accepted baseline** exists, run through the
      right script (`tensor-grep-benchmark-and-proof-toolkit`), and did not silently trip the CLI's
      5% regression gate.
- [ ] If it touches docs/release/CI contracts: the matching **governance/validator test** was updated,
      not just the doc.
- [ ] `ruff check .` + `ruff format --check --preview .` + `mypy src/tensor_grep` + `pytest -q` (or the
      narrower targeted suite) are green **in the real venv**, not a subagent's self-report.
- [ ] `scripts/agent_readiness.py` / `tg dogfood` run clean if the change touches an agent-critical
      surface (routing, capsule, MCP, docs-claim strings).
- [ ] For release/workflow/package-manager changes: `uv run python scripts/validate_release_assets.py`
      exits 0.
- [ ] If it touches `apply_policy`/`mcp_server`/native-argv/`index_lock`/auth/money/a migration/native
      asset-installer-doctor-probe construction: an
      adversarial "try to break it" security pass ran and returned `SHIP` (Part 1 point 10) — not just
      green functional tests.
- [ ] If the test itself exercises a hang-class bug (ReDoS/deadlock/lock-race): the test run is wrapped
      in both an outer shell timeout and an inner thread-based per-test timeout (Part 1 point 11).
- [ ] If it touches a scorer/graph/ranking surface: a **pin test** locked the pre-change ranked output
      first (Part 1 point 13's sibling in `tensor-grep-change-control` Part 1 Rule 6, C-pin).
- [ ] If it touches a concurrency/lock surface: the test asserts the **contract via Event handshakes**,
      never wall-clock thread overlap (Part 1 point 14, C-concurrency).
- [ ] A campaign/release drain closed → every fixed item verified against the **published wheel**, one
      PASS/FAIL row + raw JSON each, not one aggregate claim (Part 1 point 15, C-wheel).
- [ ] If it grows a field inside a SHARED envelope/header (`_envelope()` or similar): every test that
      compares two payloads' byte SIZE was audited, and any byte-ratio assertion was reproduced with a
      short `pytest --basetemp` (not just a Windows-local run) before trusting it (Part 1 point 16).
- [ ] A self-gate/subagent report names the SUITES it ran and the suites it skipped, not just a pass
      count — the CI run, not the self-gate's suite selection, is the merge arbiter (Part 1 point 17).
- [ ] A claimed red-green baseline (proving a test fails on a pre-fix/reverted commit) used a fully
      isolated tree copy, not a `PYTHONPATH` swap — `tests/conftest.py`'s `sys.path.insert` outranks
      `PYTHONPATH` and can silently re-point imports at the current worktree regardless (Part 1 point 9
      amendment).
- [ ] A NEW test was observed to go RED against the pre-fix code — a green test alone does not prove
      it guards the shape the fix actually repairs, not an already-excluded shape (Part 1 point 18).
- [ ] Any `max(baseline * N, floor)` timing assertion has a baseline confirmed measurably non-trivial
      (not silently collapsed below clock resolution into the floor alone), and any timing-flake fix
      was profiled before being attributed to a specific cause (Part 1 point 19).
- [ ] Where the invariant allows, a timing-sensitive test uses a STRUCTURAL/order-based assertion
      rather than any wall-clock form, including a ratio (Part 1 point 20).
- [ ] A claimed "docs-only"/"comment-only" follow-up commit was PROVEN behavior-neutral (e.g. an
      `ast.dump()` comparison of both revisions), not just eyeballed from the diff — see
      `tensor-grep-change-control` Part 6 for the technique.

---

## Retention folds (2026-08-21)

### A file split must reproduce its baseline PASS *and* SKIP counts

Capture BOTH numbers before touching anything:

```bash
uv run python -m pytest <the file> -q -o addopts=      # record "N passed, M skipped"
```

A 2026-08-21 split of `test_mcp_server.py` reported **"484 passed, 5 skipped"** and looked green.
The pre-split baseline was **489 passed, 0 skipped**. It had invented three
`pytest.skip("embedded native rewrite unavailable in this environment")` guards, which would have
permanently disabled three tests that pass in CI. Collected-node-count parity (489 → 489) did NOT
catch it — the tests were still collected, just no longer run.

- **Never silence a post-split failure with an environment probe.** A failure after a split is a
  finding to report, not to guard around.
- **A bare git worktree has no compiled native extension**, so native/embedded arms fail there and
  pass in CI. That is an environment artifact — report it, let CI adjudicate, and do not add a
  skip. (Confirmed: the three guarded tests failed in the worktree even in isolation, and the PR
  went green at 49 checks in CI once the guards were removed.)
- **Watch for the ratchet interaction:** removing a guard can leave an import unused (`F401`), and
  lint is often the only thing that notices a stated intention was deleted.

Law: **A126**.

### An acceptance test must run against the PUBLISHED artifact, in a clean container

A maintainer's machine is the wrong population. `tg scan --ruleset` works on a dev box that has a
separately-installed native `tg` binary, and **fails on a stock `pip install tensor-grep`** — exit
1, `ast-grep wrapper backend … not available` — because `ast_grep_py` is in no dependency and no
extra and the wheel bundles no native binary. `tg rulesets` advertises six security rulesets with
rule counts and no availability caveat.

Every earlier check of that feature ran on a machine that happened to have the capability, so the
measurement was taken from the wrong population until the same commands ran here:

```bash
docker build --build-arg TG_VERSION=<published> -f scripts/dogfood/Dockerfile -t tg-dog scripts/dogfood
docker run --rm tg-dog                      # published-wheel battery (the customer path)

docker build -f scripts/dogfood/Dockerfile.source -t tg-dog-src .   # WORKING TREE (beta path)
docker run --rm tg-dog-src
```

**Read the build's exit code UNPIPED and verify the artifact.** `docker build … | tail` reports
*tail's* status: a failing build read through a pipe looked like `exit 0` while producing **no
image at all**. Use `docker build … > build.log 2>&1; echo $?` and confirm with
`docker images <tag>` — the one claim a misread pipe cannot fake. (A127)

Laws: **A125**, **A127**.

### Resolve a caller's module namespace by LEAF name, not a dotted prefix

`tests/` has no `__init__.py`, so pytest's prepend import mode names modules by **basename**.
Measured with a `pytest_runtest_setup` probe: `test_cli_modes_blast_radius`, **not**
`tests.unit.test_cli_modes_blast_radius`. A helper matching
`startswith("tests.unit.test_cli_modes")` therefore matched nothing, its stack walk fell through to
`return globals()`, and shared fakes read a stale copy — **the exact failure the shim existed to
prevent, silently**, because falling back to a real namespace looks like success.

If you write anything that resolves a caller's namespace by name, match the final dotted component
and prove it with a probe rather than assuming the import path. Law: **A129**.

### A replacement assertion must be PROBE-VERIFIED to discriminate (2026-08-21)

When you retire a flaky assertion, the replacement is a NEW instrument and inherits none of the old
one's credibility. Prove it can fail before trusting it.

**Worked example, including the wrong turn.** A guard test asserted
`elapsed < 1.0, "probe is not bounded"`. It was flaky (windows-latest failed at 1.175s) and it
measured the wrong thing entirely: the guard runs no probe of its own —
`_should_refuse_unbounded_large_root_scan` is "checked using the candidate count the real search
ALREADY collected (never a second walk)" — so `elapsed` was just the time to walk 2,000 stub files.

The first replacement asserted the ABSENCE of `partial` / `result_incomplete` in the output. It
looked principled and was **vacuous**: a probe of a real deadline-truncated PLAIN-TEXT run showed
neither string EVER appears on that surface. It would have passed in both arms and proven nothing —
the exact failure class this file exists to prevent, reintroduced while fixing a different one.

**The probe is what found the real discriminator.** A deadline-BURNING run prints matches
(observed: `f98.py:# TODO item 98`); a REFUSAL prints none. And crucially **both exit 2**, so the
exit code alone cannot separate them. The final assertion — no match output — is therefore
*stronger* than the timer it replaced: it proves the search stopped BEFORE emitting results, on any
machine at any load.

**The procedure:**

1. Before writing the replacement, PROBE the failing condition on the exact surface the test uses
   (text vs `--json` behave differently — that is what made the first attempt vacuous).
2. Write the assertion against what the probe actually showed, not what you expect it to show.
3. **Perturb it**: invert or break the assertion and confirm it FAILS. Measured here: inverted →
   1 failed / 103 passed; reverted → 104 passed with the file byte-identical.
4. Expect the lint fallout — removing the timing code left `time` unused (`F401`). Lint is often the
   only witness that a stated intention stopped executing.

Law **A138**. Related: the wall-clock guidance above, and A123/A135 on absent gates reading as
passes.

## Provenance and maintenance

Volatile facts re-verified **2026-07-08, release `v1.49.3`**; the 2nd fixture-blind-spot receipt
(Part 1 pt 4), the vacuous-truth-oracle checklist item (Part 1 pt 12), and the test-file counts were
re-verified **2026-07-16, release `v1.78.1`**. A further pass **2026-07-22, release `v1.93.2`**
re-verified test-file counts (unit 263 / e2e 16 / integration 16, up from 239/16/11), added the new
`tests/eval/` directory (Part 3 + Part 6), and added Part 1 points 13-15 (per-task-pinned accuracy gate,
scheduler-independent concurrency tests, published-wheel verdict-table dogfood). A further pass
**2026-07-24, release `v1.96.0`** re-verified and corrected every `file:line` citation in this skill
against `origin/main` (CONTRIBUTING.md/AGENTS.md/`.github/workflows/ci.yml`/`test_routing_parity.py`/
`scripts/agent_readiness.py`/`scripts/validate_release_assets.py`/`pyproject.toml` had all drifted
since the prior pass), refreshed the test-file counts, then DE-STAMPED them on 2026-07-27 after the number was wrong in three consecutive passes (267 -> a mid-flight 282 -> the real 291) — the row now carries only the command (historical values unit 267 / e2e 16 / integration 16 / eval 2 —
the new `test_retrieval_quality_regression.py` and the registered `eval` pytest marker), added the
cold-path dogfood caveat to Part 1 point 3 and the byte-identical-optimization-proof technique plus the
clean-rebase corollary to Part 1 point 9, added the `uv.lock` hand-splice gotcha to Part 2, and added
the new-language/grammar test shape to Part 6 (tracking the Java/C#/PHP symbol-graph expansion,
#724/#725/#726). A same-day second pass **2026-07-24, release `v1.98.2`** added Part 1 points 16-17
(the shared-envelope payload-ratio governance-test trap plus its tmp-path-length platform sensitivity,
and the self-gate-suite-subset-is-not-full-CI-matrix trap, both from #733/#734) and the PYTHONPATH/
`conftest.py` red-green-baseline amendment to Part 1 point 9 (an independent gate's false "passed on
main" result, caught the same day). A third same-day pass **2026-07-24, release `v1.98.3`** added
Part 1 points 18-20 (a test proving nothing until seen fail on the pre-fix baseline, #737; a ratio
timing assertion degenerating to its floor below clock resolution plus the profile-before-fixing
discipline, #739; preferring a structural order-based assertion over any wall-clock form, #739) and
their checklist items. A coordinator review of that same pass added the concrete clock-resolution
number (`time.get_clock_info('monotonic').resolution` = 0.015625s on Windows) to point 19's mechanism.
A further pass **2026-07-31, release `v1.101.24`** appended six oracle-adjacent bullets to Part 0's
"rules that fall out" list -- a control that reproduces a failure without being proven the operative
one; a source-scanning census satisfied by a comment; presence vs position of a placement guard; a
census member that names a branch it cannot structurally fail on; a count blind to an order swap; and
an unapplied mutation reading as a passing control arm -- deliberately as unnumbered bullets, not new
Forms (the Forms table is mirrored in `AGENTS.md`; adding a Form there is a two-file edit owned
separately).
A same-day follow-up pass **2026-07-31** appended two more unnumbered bullets to the same list from
the `-q` shared-builder regression (#876, fixed #880): a control arm stating what the callee accepts
rather than what the consumer does with the value, and a probe built at the seam the defect was
introduced (`_build_cmd`) instead of the seam the value crosses (`run_subprocess`). It also corrected
"catches all nine" to "all ten" in Part 0's opening -- the header's own miscount recurring one
sentence below the parenthetical warning about it; re-derive the count whenever a form is added.
Re-verify before relying on them:

| Claim | Re-verify command |
|---|---|
| Total collected tests | `uv run pytest tests --collect-only -q` (tail line; re-run to check — grows every release, do not trust a stale snapshot number here) |
| Test file counts — **COMMAND ONLY, never a stamped number** (291/21/16 on 2026-07-27, shown so a reader can date this line, not to be quoted) | `Get-ChildItem tests/unit,tests/e2e,tests/integration,tests/eval -Filter test_*.py -Recurse \| Measure-Object` (PowerShell) or `find tests/unit tests/e2e tests/integration tests/eval -name 'test_*.py' \| wc -l` |
| `tg find` classifier receipt + vacuous-truth oracle guard | `grep -n "test_empty_gold_label_is_loud" tests/unit/test_eval_late_rerank_quality.py`; `grep -n "GoldenSetError\|vacuous" benchmarks/eval_late_rerank_quality.py` |
| `dogfood()` CLI entry point (symbol anchor, not a line number) | `grep -n "^def dogfood" src/tensor_grep/cli/main.py` |
| `CliRunner` usage count in unit tests | `grep -rc CliRunner tests/unit/*.py \| awk -F: '{s+=$2} END{print s}'` |
| pytest markers registered | `grep -n "markers = \[" -A 10 pyproject.toml` |
| `-x` in pytest addopts (and the `--maxfail=0` override) | `grep -n "addopts" -A5 pyproject.toml`; empirically confirm with a scratch `pytest.ini` + two dummy tests |
| Routing-parity contract file/lines | `grep -n "PUBLIC_TOP_LEVEL_COMMANDS\|def test_top_level_help_visible" tests/e2e/test_routing_parity.py` |
| `search-golden-parity` CI job | `grep -n "search-golden-parity" -A25 .github/workflows/ci.yml` |
| Agent-readiness check names | `grep -n 'name="' scripts/agent_readiness.py` |
| Agent-readiness CI jobs | `grep -n "agent-readiness:\|windows-agent-readiness:" -A40 .github/workflows/ci.yml` |
| `validate_release_assets.py` entry points | `grep -n "^def validate_all\|^def main" scripts/validate_release_assets.py` |
| Release-asset validator test size | `wc -l tests/unit/test_release_assets_validation_*.py` |
| Benchmark regression thresholds | `grep -n "max-regression-pct\|min-baseline-time-s" -A3 benchmarks/check_regression.py`; `grep -n "max_regression_pct\|min_baseline_time_s" src/tensor_grep/perf_guard.py` |
| mypy strict-mode config | `grep -n "\[tool.mypy\]" -A6 pyproject.toml` |
| `--no-sync` rationale | `grep -n "no-sync" -B2 -A2 .github/workflows/ci.yml` |
| Current release tag | `grep -n "^version" pyproject.toml` |
| `tg diff-docs` still deferred/unmerged (2026-07-03) | `git log --oneline --all -- src/tensor_grep/cli/diff_docs.py` (should show only the `wip/diff-docs-precision` commit `90b7042`, nothing on `main`) |
| Native-binary discovery order for parity/integration tests (2026-07-03) | `grep -n "_in_tree_native_tg_candidates\|def resolve_native_tg_binary" -A5 src/tensor_grep/cli/runtime_paths.py` |
| Native-delegation field-coverage ratchet test still present (2026-07-03) | `grep -n "class Test" tests/unit/test_native_delegation_field_coverage.py` |
| `--rank`/`capfd` capture-surface receipt (2026-07-03) | `git show ab717a1 -s --format=%B` (contains both the `#342` refuse-delegation fix and the `#342 follow-up` capture fix in one squashed message) |
| Language-registry 5-seam checklist + `test_lang_registry.py` parity assertion (2026-07-24) | `grep -n "_imports_and_symbols_for_path\|_imports_with_lines_for_path\|_target_language_for_path\|_SUPPORTED_FILE_DEPENDENCY_LANGUAGES" src/tensor_grep/cli/repo_map.py`; `grep -n "test_spec_for_path_resolves_every_registered_suffix" tests/unit/test_lang_registry.py` |
| Cold-path dogfood receipt (`_python_imports_and_symbols` walk-merge, 2026-07-24) | `grep -n "^def _python_imports_and_symbols" src/tensor_grep/cli/repo_map.py` |
| `uv.lock` hand-splice check / `Dependency & License Audit` gate (2026-07-24) | `grep -n "Dependency & License Audit\|uv export" .github/workflows/audit.yml` |
| Shape-9/9a/9b member-fn-ptr test split (Part 1 point 18, #737) | `grep -n "shape9a_filescope_member_fn_ptr_variable\|shape9b_inclass_member_fn_ptr_variable" tests/unit/test_lang_cpp.py` |
| Structural ENTER/EXIT marker-order assertion (Part 1 point 20, #739) | `grep -n "def test_create_checkpoint_lock_does_not_wrap_expensive_work" tests/unit/test_index_lock_concurrency.py` |
| Windows clock-resolution / degenerate-ratio incident (Part 1 point 19, #739) | `gh pr view 739 --json body -q .body` (search for "baseline_elapsed measured" and "cProfile") |

## Retention folds (2026-08-13)

- **A99 — a verifier must be bound to the artifact it audits.** Record audited root + HEAD SHA + a
  path/blob manifest, and require EXACT set equality between the expected population and reported
  coverage; a truthy response that omits members is PARTIAL, a null lane is CANNOT_VERIFY, and a
  CLEAN verdict needs non-zero sampled evidence (never clean-on-empty). The pre-hardening
  `tg-skill-audit.js` could audit the wrong checkout and still report 6/6 covered.
- **A100 — advertised capability must be executed.** An unconsumed schema or un-run phase is a false
  advertisement of capability (`tg-audit-fix-loop.js` advertised five phases with zero execution
  statements). Wire structure to execution, or label the stub, before anything depends on it.

If any command above no longer matches, update this skill in the same change — a wrong runbook is
worse than none.

### A gate bounds ONE failure mode, not the family it belongs to (2026-08-19)

Not a new Form — the ten Forms are about a check that cannot discriminate. This is the
neighbouring problem: a check that discriminates **correctly**, on a narrower property than
its name suggests, so its silence is read as covering the whole family.

Canonical write-up with the full table: `AGENTS.md`, "A Green Gate Bounds One Failure Mode,
Never The Family It Belongs To".

The three that bite hardest here:

- **`test_skill_library_drift` fails a citation past END-OF-FILE. It cannot see a citation
  that still resolves and now points at the wrong code.** After wave 4 shrank
  `agent_capsule.py` 3,652 → 926, CI failed six citations and stayed silent on a seventh at
  `:294` — inside the file, and no longer describing the symbol it named. Grep every citation
  into a file you shrink; the gate's silence covers exactly the ones it cannot judge. A split
  also moves symbols between FILES, so grep the SYMBOL across `src/`.
- **A retry loop reports success on exhaustion.** `for i in 1 2 3; do … done` exits 0 on its
  last iteration whatever the body did. A retry added to fix a hang therefore reintroduces the
  silent-skip it was protecting against, unless you assert the POSTCONDITION rather than the
  loop's status.
- **A ratchet that gets re-pinned in the same commit reports clean.** Re-pinning is sometimes
  correct, but it is the weakest outcome available; write down the residual and why it was not
  avoidable, or the gate degrades into a comment.

**Before trusting a gate's silence, say out loud which property it actually asserts, then ask
what the NEIGHBOURING failure looks like.** If the neighbour is indistinguishable from silence,
you need a second probe, not more confidence in the first.
