# CEO Update — 2026-08-19 Enterprise Audit

Plain language. Numbers are measured, not estimated.

---

## The one-paragraph version

We audited the whole codebase against your enterprise standard. **The code is in good shape; the
things that CHECK the code were not.** Security came back clean on every vector we tested. But the
file-size rule you set had **no enforcement anywhere**, so 35 files had quietly grown past it — and
worse, three separate holes in CI meant some pull requests were passing while running **zero
tests**. We fixed the checking layer, shipped two releases, and started shrinking the oversized
files: **35 down to 31**, and it can now only go down.

**Verdict: FAIL** — on one rule only, the file-size limits. Everything else passed.

---

## What worked

**1. We stopped the bleeding before cleaning up.**
The instinct was to split all 35 oversized files immediately. We asked a 7-model review panel; it
voted **7 to 0 against**. Doing a 90-module refactor right before a public release is the single
likeliest way to break the release the standard exists to protect.

Instead we shipped a **ratchet**: the 35 files are recorded at their current size, and CI now fails
if any of them grows, or if a new oversized file appears. The number can only go down. It caught
our *own* work three times.

**2. Two real bugs fixed and published.**

- **The version stamp was broken in every installed copy.** Our tool reads a version number out of
  a Rust source file — which isn't included in the package users download. So every real install
  silently fell back to a hardcoded default. Harmless today by coincidence; the day we bump the
  number, every user reports the wrong one with no error. Shipped as **v1.110.17**.
- **One of 18 places in the Rust code hardcoded the same number** instead of using the shared
  constant. Shipped as **v1.110.18**.

**3. Three CI holes closed — this is the big one.**
Our CI has a cost-saving feature that skips expensive tests when a change "doesn't touch code." It
was wrong about what counts as code, three separate ways:

| what CI ignored | what that meant |
|---|---|
| the `scripts/` folder | a 3,500-line rewrite of our release-checking script merged having run **zero tests** |
| the `docs/` folder | the tests that catch documentation contradicting the product never ran on documentation changes |
| `docs/` for the formatter | a documentation change broke a code-quality check on `main`, and it surfaced days later on someone else's unrelated work |

All three are fixed. **None of them was found by reading the CI config** — they were found by
noticing that job names in the status list still had unfilled placeholders in them, which is what a
job that never actually ran looks like. It looks identical to a pass.

**4. Documentation now matches the product.**
Three public documents advertised outdated language support. One was *understating* us against a
competitor. Fixed — and pointed at a command that regenerates the truth, instead of a number that
rots.

**5. Files actually got smaller.**

| file | before | after |
|---|---|---|
| `validate_release_assets.py` | 3,780 | **340** |
| `run_gpu_benchmarks.py` | 1,925 | **971** |
| `ast_workflows.py` | 1,996 | **1,411** |
| `checkpoint_store.py` | 1,566 | **1,431** |
| `run_gpu_native_benchmarks.py` | 3,364 | 1,919 *(still over)* |

---

## The full backlog — every remaining item

### A. Oversized files (31 after the last PR merges)

The rule: contracts ≤500, core code ≤1,500, tests ≤2,000, fixtures ≤2,000.

**The four giants — these are the real work:**

| file | lines | over by | why it's hard |
|---|---|---|---|
| `cli/repo_map.py` | 19,733 | 13× | 482 functions; 290 test hooks reach into it |
| `cli/main.py` | 17,948 | 12× | ~50 commands; 268 test hooks |
| `tests/unit/test_cli_modes.py` | 17,183 | 8.6× | split plan written, not executed |
| `rust_core/src/main.rs` | 15,094 | 10× | Rust — can only be verified through CI here |

**Everything else, largest first:**

| file | lines |
|---|---|
| `tests/unit/test_benchmark_scripts.py` | 10,689 |
| `tests/unit/test_mcp_server.py` | 9,710 |
| `cli/mcp_server.py` | 7,963 |
| `tests/unit/test_release_assets_validation.py` | 5,258 |
| `rust_core/src/gpu_native.rs` | 4,952 |
| `rust_core/tests/test_schema_compat.rs` | 4,412 |
| `cli/agent_capsule.py` | 3,652 |
| `rust_core/src/native_search.rs` | 3,563 |
| `tests/unit/test_session_cli.py` | 3,337 |
| `rust_core/src/index.rs` | 3,092 |
| `rust_core/tests/test_routing.rs` | 2,995 |
| `tests/unit/test_cli_bootstrap.py` | 2,987 |
| `tests/unit/test_file_deps.py` | 2,901 |
| `rust_core/src/backend_ast.rs` | 2,553 |
| `rust_core/tests/test_ast_rewrite.rs` | 2,509 |
| `tests/unit/test_apply_policy.py` | 2,375 |
| `rust_core/tests/test_index.rs` | 2,356 |
| `rust_core/tests/test_public_native_cli_parity.rs` | 2,318 |
| `tests/unit/test_semantic_provider_navigation.py` | 2,251 |
| `tests/unit/test_gpu_benchmark_scale_contracts.py` | 2,244 |
| `cli/session_daemon.py` | 2,139 |
| `rust_core/src/backend_ast_workflow.rs` | 2,109 |
| `cli/ast_workflows.py` | 1,996 |
| `benchmarks/run_gpu_native_benchmarks.py` | 1,919 |
| `cli/session_store.py` | 1,828 |
| `rust_core/src/backend_cpu.rs` | 1,817 |
| `cli/bootstrap.py` | 1,696 |
| `cli/checkpoint_store.py` | 1,566 |
| `rust_core/src/python_sidecar.rs` | 1,519 |

**Honest warning on the estimate.** We proved that some of these **cannot be fixed by splitting at
all**. `run_gpu_native_benchmarks.py` has 1,752 lines welded to its main file by the way ~150 tests
hook into it — splitting it further would break those tests silently. `main.py` and `repo_map.py`
will hit the same wall. Those need a deeper architectural change, not file surgery. **Budget
multiple sessions, not one.**

### B. Documentation gaps (from the audit's own §9)

| item | state |
|---|---|
| Step-by-step "rebuild from scratch" guide | **missing entirely** |
| A design-doc convention (decisions live only in commits/code) | missing — logged as DOC-003 |
| A checklist proving a rebuild behaves the same | partial |
| Cache/session format compatibility across releases | not documented |

### C. Verification debt

| item | state |
|---|---|
| ~30 subprocess calls in `main.py` | spot-checked, not exhaustively reviewed |
| 117 broad `except Exception:` blocks | sampled, not individually judged |
| Path-confinement across ~50 MCP tools | verified at 2 sites, not all |
| 11 "always true" range assertions in tests | present; not confirmed harmless |
| ~8 loose wall-clock timing assertions | lower flake risk; not individually reviewed |

### D. Repository hygiene

54+ working directories and ~140 branches, most far behind `main`. Measured: merging them would
**delete** 345–1,003 lines each — they are stale, not pending. Four "audit" branches are already
merged. One draft PR (#966) is titled *"not GREEN, do not merge"* and stays that way.

---

## What needs research (not just doing)

**1. ~~How to split a file whose tests hook into it~~ — MEASURED, and the news is bad.**

This was the open question. It now has a number. `scripts/measure_split_floor.py` computes the
lower bound of what **must** stay in a file because tests hook into it:

| file | total | lines LOCKED to the file | can splitting reach 1,500? |
|---|---|---|---|
| `cli/repo_map.py` | 19,708 | **11,731** | **No** |
| `cli/main.py` | 17,605 | **10,172** | **No** |
| `cli/mcp_server.py` | 7,876 | **5,852** | **No** |
| `cli/agent_capsule.py` | 3,652 | 1,527 | **No** — split anyway, see below |

*These numbers were corrected the same day. The first version of the tool forgot to count the
patched functions **themselves** — it only counted the functions that call them — and reported
`agent_capsule` as 1,190 ("splittable") when the real floor was 1,527. We split it successfully
anyway, but by using an escape hatch on one function, not because the estimate was right. The three
big files moved further out of reach, so the "No" verdicts got stronger, not weaker.*

**Three of the four biggest Python files cannot reach the limit by splitting at all.** Not "it's
hard" — the code that must stay behind is already 4–7× the limit on its own. Wave 3 discovered this
the expensive way on a smaller file; this tool now answers it before an agent is dispatched.

*Cross-checked:* the patched-symbol counts (48 / 66 / 9) match the independent binding auditor
exactly. The measure is a lower bound — a number over the limit is decisive, a number under it is
encouraging rather than a guarantee.

**What this means for the plan.** The remaining Python work splits three ways:

- **`agent_capsule.py`** — splittable now. That is the next wave.
- **Test files** (`test_cli_modes.py` and friends) — no facade problem at all; tests are the ones
  *doing* the patching. Splittable, and the largest single win available.
- **`main.py`, `repo_map.py`, `mcp_server.py`** — need dependency injection or coordinated edits
  across 48–66 patch symbols each. **This is a design project, not a cleanup task**, and it should
  be scoped and reviewed on its own before anyone starts.

The still-open half: *which* of those two approaches, and what it costs. That has not been designed.

**2. Whether the test-hook count is the right metric at all.**
We now have a tool that measures it, but it was blind to a whole category twice. It is currently
honest about what it *cannot* see. Whether a fully precise measurement is worth building is an open
question.

**3. Should the limit be 1,500 or 1,000?**
Your brief said 1,500; the audit template said 1,000. We gated at 1,500. At 1,000, **11 more files**
violate (46 total). This is a one-line change whenever you want it — but it should be a decision,
not a drift.

**4. Are the big Rust files splittable at all on our setup?**
We cannot compile Rust on this machine (shared box). Every Rust change costs a full CI round-trip.
Four Rust files are over the limit. Someone should establish whether that workflow is viable before
committing to it.

---

## Lessons learned since the last update

**1. The measuring tools fail more often than the code.**
Twelve times we got a wrong answer from a check, versus about five real bugs in the product. **Five
of those twelve were our own tools.** Every one was caught by running a control — never by
re-reading the code, because the code was fine and the measurement was not.

**2. A rule with no enforcement is a wish.**
The size standard existed on paper and was violated 35 times. Nothing was checking. The fix wasn't
a memo, it was a gate that fails the build.

**3. Writing down a limitation does not mean you'll respect it.**
We documented one tool's blind spot in that tool's own submission — and wrote a task brief that
walked straight into it two hours later. Only turning it into an automatic warning stopped it.

**4. A green checkmark can mean "nothing ran."**
Three times, a pull request passed while its tests were skipped entirely. The tell was subtle: job
names still containing unfilled template text. Any summary that just counts failures reports these
as success.

**5. Hand-carried numbers rot between reading and use.**
Every count we wrote into a task brief was wrong — the violation count three times (19 → 33 → 35),
a file count (34 vs 5), a test-hook count (0 vs ~150), a projected total. The fix isn't care, it's
making the tool derive numbers at the moment it uses them.

**6. Some work is genuinely not doable the obvious way, and saying so is the right answer.**
One file could not reach the limit by splitting. We lowered its recorded size and explained why,
rather than forcing the number down by changing behaviour to satisfy a gate.

**7. Tell your helpers your instructions are probably wrong.**
Four consecutive task briefs contained a false premise. Every time, the assistant caught it —
because it was told to verify rather than trust. That instruction was worth more than the briefs.

---

## Where it stands

- **13 pull requests merged.** Two releases published and verified against the real downloaded
  package, not just the source.
- **Oversized files: 35 → 31**, and structurally unable to increase.
- **Security: no unresolved findings.** Eight standard attack categories tested; all were already
  defended.
- **One thing we broke, and fixed:** a documentation change of ours briefly broke a code-quality
  check on `main`. Caught, repaired, and the hole that let it through is now closed.

**We are not done.** The size standard is not met and will not be for several more sessions. The
accurate statement is *"31 recorded, ceiling locked, decreasing"* — never *"compliant."*
