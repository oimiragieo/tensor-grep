# Design: escaping the split floor on the three un-splittable modules

**Status:** step 1 shipped (#1036); the CONVERSION (steps 2-4) is still a proposal and still
**unreviewed** — it needs the adversarial review named in §5 before any call site changes.
**Date:** 2026-08-19.
**Decides:** how `cli/main.py`, `cli/repo_map.py` and `cli/mcp_server.py` reach the 1,500-line
limit, given that **they cannot get there by moving code**.

---

## 1. The problem, measured

`scripts/measure_split_floor.py`:

| module | total | lines LOCKED to this file | reachable by splitting? |
|---|---|---|---|
| `cli/repo_map.py` | 19,708 | **11,731** | no |
| `cli/main.py` | 17,605 | **10,172** | no |
| `cli/mcp_server.py` | 7,876 | **5,852** | no |
| *(`cli/agent_capsule.py`)* | *3,652* | *1,527* | *no — see correction* |

> **CORRECTED 2026-08-19, same day.** The first version of this table read 11,025 / 9,453 / 5,554 /
> **1,190**, from a tool that omitted the most obvious members of the locked set: **the patched
> functions themselves.** `monkeypatch.setattr(mod, "f", ...)` rebinds an attribute on that module,
> so `f` must live there — yet the tool locked only the functions that *reference* `f`. All nine of
> `agent_capsule`'s patched symbols are top-level functions in it, and none was counted.
>
> The error ran in the **dangerous direction**: a too-low floor reads as permission to split.
> `agent_capsule.py` was briefed to wave 4 as "viable" at 1,190 when the true floor was 1,527 —
> above the limit. The wave succeeded anyway, but by using the escape hatch in §3 on one function,
> not because the brief was right.
>
> Only the tool's stated *"this is a lower bound; a number under the limit is encouraging, not a
> guarantee"* kept that from being a wasted wave. **A tool honest about its direction of error stays
> useful when it is wrong.**
>
> The corrected tool now reports 10 functions / 1,527 lines for `agent_capsule`, matching what the
> wave-4 agent derived independently. The three larger modules moved further **over** the limit, so
> every "no" above strengthens.

**Why anything is locked at all.** Python resolves a bare name through the *defining* module's
globals. A test does `monkeypatch.setattr(main, "resolve_native_tg_binary", fake)`; that rebinds an
attribute on the `main` module object. Any function that calls `resolve_native_tg_binary(...)` as a
bare name and lives in `main.py` picks up the patch. Move that function to `main_helpers.py` and it
resolves against `main_helpers`'s globals instead — **the test still passes, and production runs the
original.** Silent.

So the locked set is: every function bare-referencing a patched name, plus everything that
bare-calls those, transitively. On these three modules that closure is 4–7× the limit on its own.

This was learned the expensive way in wave 3, *after* an agent had been dispatched at
`run_gpu_native_benchmarks.py` and could only get partway.

## 2. The two routes, costed

`scripts/cost_split_floor_routes.py`:

| | Route A — late binding | Route B — repoint the tests |
|---|---|---|
| what changes | bare calls inside the module become attribute lookups | every test patch site moves to the new module path |
| `main.py` | 102 | 376 |
| `repo_map.py` | 166 | 296 |
| `mcp_server.py` | 125 | 115 |
| **total edits** | **393** | **787** |
| files touched | **3** | **75 distinct test files** (82 module-file pairs; 7 shared) |
| verifiable how? | an AST query: *"is there any bare call to a patched name left?"* | by reading each edit |
| failure mode if wrong | the call breaks loudly at runtime | **a test silently patches the wrong module and passes** |

> **CORRECTED 2026-08-19 (second correction to this doc).** The first version of this table
> read 102 / 166 / **69** / **337**, measured by `scripts/cost_split_floor_routes.py` while it
> resolved its repo root from a **hardcoded absolute path** — so every run measured one
> particular checkout no matter where it was invoked, and that checkout sits on an unrelated
> branch with dozens of uncommitted files. Its sibling `scripts/measure_split_floor.py` already
> resolved the root from `__file__`; the two disagreed, and the wrong one produced these numbers.
>
> The script is fixed and the table above is re-measured against real `main`. **The
> recommendation is unaffected** — Route A still wins ~2× (393 vs 787), and it is still 3 files
> against 75. (Both the old "79" and the raw new "82" were per-module SUMS; 7 test files patch
> more than one of the three modules, so Route B's real blast radius is the union, 75.)
> **The SEQUENCING rationale in §5 was not unaffected**, and is corrected there.
>
> Same lesson as the split-floor tool one section up, from the opposite direction: that tool was
> honest about erring LOW and stayed usable when wrong. This one gave no indication it was
> reading a different tree at all, so nothing about its output invited a second look.

**Route A is 2.3× less work, lives in 3 files instead of 79, and its failure mode is loud.**
Route B's failure mode is precisely the false green this campaign exists to prevent, repeated 787
times with no gate that can see it.

**Recommendation: Route A.**

## 3. What Route A actually looks like

Inside the module, a bare call to a patched name becomes a late attribute read against the module's
own object:

```python
# before -- binds at import, moves badly
def _probe() -> str | None:
    return resolve_native_tg_binary()


# after -- binds at CALL time, moves freely
import sys

_self = sys.modules[__name__]


def _probe() -> str | None:
    return _self.resolve_native_tg_binary()
```

`_self.X` is resolved on every call, so it sees a `setattr` on the module object no matter which
file `_probe` ends up in. The function is now free to move.

**Why `sys.modules[__name__]` rather than importing the module by name:** the module is mid-import
when its own body executes, so `from tensor_grep.cli import main` inside `main.py` is circular.
`sys.modules[__name__]` is the same object, already registered, and costs one dict lookup.

The work concentrates in a handful of hot symbols, which is what makes 393 tractable:

- `repo_map.py`: `build_repo_map` ×15, `_read_source_text_cached` ×14,
  `_deadline_monotonic_from_seconds` ×11, `_iter_repo_files` ×8, `build_symbol_defs_from_map` ×7
- `main.py`: `_native_tg_version_matches` ×12, `resolve_native_tg_binary` ×7,
  `_maybe_symbol_command_via_running_daemon` ×6, `_doctor_tg_candidate_version` ×5
- `mcp_server.py`: `_inject_mcp_contract_fields` **×54**, `resolve_native_tg_binary` ×3,
  `_embedded_rewrite_available` ×3, `Pipeline` ×2, `_execute_embedded_rewrite_json` ×2

## 4. The gate that makes it safe

Route A is only trustworthy if "did we miss one?" is answerable mechanically. It is — it is the same
AST query the costing tool already runs:

> For each of the three modules: **zero** `ast.Call` nodes whose `func` is an `ast.Name` matching a
> patched symbol.

**SHIPPED 2026-08-19 as PR #1036** — `scripts/bare_call_ratchet.py`, pinned in
`scripts/bare_call_pins.json` at 102 / 125 / 166, wired into the `static-analysis` job beside
the file-size ratchet, with 17 mutation tests in `tests/unit/test_bare_call_ratchet.py`. It is
fail-closed in all four directions the file-size ratchet uses, including the unusual one: a count
BELOW its pin also fails, because a pin above the real count accepts a range, and a range is
where a later regression hides.

**The bidirectional control was run, not assumed.** One bare call injected into `mcp_server.py`:

    ARM 1  clean          125 == pin   exit 0
    ARM 2  +1 bare call   126 >  pin   exit 1, naming the file and the fix
    ARM 3  reverted       125 == pin   exit 0, file byte-identical (sha 1ff472cb…)

The exit codes were re-measured without a pipe after a first reading took `tail`'s status and
printed a false `exit=0` — the trap that once let a commit land on red in this repo.

**What a zero here will NOT mean.** No bare call left is not the same as splittable: this AST
query does not model class methods, closures, `global` rebinding, or `spec_from_file_location`.
Re-run `scripts/measure_split_floor.py` when a module reaches zero rather than inferring the
floor from it.

## 5. Sequencing, and the honest risk

1. ~~Ship the bare-call ratchet with counts pinned at today's values.~~ **DONE — PR #1036**, 2026-08-19. No behaviour change; see §4 for the perturbation receipt.
2. Convert one module — **`mcp_server.py` first**, but NOT for the reason first given here.
   It does not have the fewest edits: at 125 it sits between `main.py` (102) and `repo_map.py`
   (166). It goes first because of **blast radius and shape**, which the corrected numbers
   actually strengthen:

   - **8 test files** depend on it, against 19 for `main.py` and 55 for `repo_map.py`. If a
     conversion goes wrong, this is much the cheapest place to find out.
   - **54 of its 125 sites are a single symbol** (`_inject_mcp_contract_fields`). That is 43% of
     the module's Route A work in one mechanically uniform, uniformly reviewable pattern —
     whereas `main.py`'s heaviest symbol appears 12 times and its 102 sites are spread thin.

   A raw edit count was the wrong metric to sequence on: 102 scattered one-off edits across 19
   test files' worth of surface is not obviously safer than 125 edits of which 54 are the same
   line.
3. Only then split it. The split becomes ordinary once the floor is gone.
4. Repeat for `main.py`, then `repo_map.py` (largest, and 54 test files depend on it).

**Risks, stated plainly:**

- `_self.X` is a **behaviour change in the hot path** — one dict lookup per call. `repo_map.py`'s
  `_read_source_text_cached` is called 14× in-module and is genuinely hot. The benchmark gate must
  run on each conversion; if a hot loop regresses, that specific call site stays bare and its
  function stays in the facade. Partial escape is an acceptable outcome, exactly as it was for
  `run_gpu_native_benchmarks.py`.
- The split-floor measure is a **lower bound**. It does not model class methods, closures, or
  `global` rebinding, and it is blind to `spec_from_file_location`-loaded modules. Converting all
  393 may not drop the locked set to zero. Re-measure after each module rather than assuming.
- **This is a design project, not a cleanup wave.** It should get an adversarial review before any
  code moves — the repo's own rule for load-bearing changes — because the failure mode it is
  guarding against is invisible to a passing test suite.

## 6. What this does not decide

Whether the limit is 1,500 or 1,000 (the brief and the audit template disagree; at 1,000, eleven
more files violate), and whether the four oversized **Rust** files are tractable at all given that
this development box cannot compile Rust and every attempt is a CI round-trip.
