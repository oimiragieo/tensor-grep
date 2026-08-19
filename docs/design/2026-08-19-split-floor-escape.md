# Design: escaping the split floor on the three un-splittable modules

**Status:** proposal, unreviewed. **Date:** 2026-08-19.
**Decides:** how `cli/main.py`, `cli/repo_map.py` and `cli/mcp_server.py` reach the 1,500-line
limit, given that **they cannot get there by moving code**.

---

## 1. The problem, measured

`scripts/measure_split_floor.py`:

| module | total | lines LOCKED to this file | reachable by splitting? |
|---|---|---|---|
| `cli/repo_map.py` | 19,708 | **11,025** | no |
| `cli/main.py` | 17,605 | **9,453** | no |
| `cli/mcp_server.py` | 7,876 | **5,554** | no |
| *(`cli/agent_capsule.py`)* | *3,652* | *1,190* | *yes — wave 4* |

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
| `main.py` | 102 | 378 |
| `repo_map.py` | 166 | 295 |
| `mcp_server.py` | 69 | 105 |
| **total edits** | **337** | **778** |
| files touched | **3** | **79 test files** |
| verifiable how? | an AST query: *"is there any bare call to a patched name left?"* | by reading each edit |
| failure mode if wrong | the call breaks loudly at runtime | **a test silently patches the wrong module and passes** |

**Route A is 2.3× less work, lives in 3 files instead of 79, and its failure mode is loud.**
Route B's failure mode is precisely the false green this campaign exists to prevent, repeated 778
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

The work concentrates in a handful of hot symbols, which is what makes 337 tractable:

- `repo_map.py`: `build_repo_map` ×15, `_read_source_text_cached` ×14,
  `_deadline_monotonic_from_seconds` ×11, `_iter_repo_files` ×8, `build_symbol_defs_from_map` ×7
- `main.py`: `_native_tg_version_matches` ×12, `resolve_native_tg_binary` ×7,
  `_maybe_symbol_command_via_running_daemon` ×6, `_doctor_tg_candidate_version` ×5
- `mcp_server.py`: `resolve_native_tg_binary` ×3, `_embedded_rewrite_available` ×3

## 4. The gate that makes it safe

Route A is only trustworthy if "did we miss one?" is answerable mechanically. It is — it is the same
AST query the costing tool already runs:

> For each of the three modules: **zero** `ast.Call` nodes whose `func` is an `ast.Name` matching a
> patched symbol.

Ship that as a test **before** any conversion, with the count pinned at its current value (102 /
166 / 69) and ratcheting to zero — exactly the pattern `scripts/file_size_budget.py` already uses.
Then the conversion cannot be partially done and believed complete, and a future edit cannot
reintroduce a bare call.

**Bidirectional control required:** the test must be seen to FAIL on a deliberately reintroduced
bare call before it is trusted, per the repo's standing rule that a gate never observed firing is a
comment.

## 5. Sequencing, and the honest risk

1. Ship the bare-call ratchet with counts pinned at today's values. No behaviour change.
2. Convert one module — **`mcp_server.py` first**: fewest edits (69), fewest test files (7), and the
   smallest blast radius of the three.
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
  337 may not drop the locked set to zero. Re-measure after each module rather than assuming.
- **This is a design project, not a cleanup wave.** It should get an adversarial review before any
  code moves — the repo's own rule for load-bearing changes — because the failure mode it is
  guarding against is invisible to a passing test suite.

## 6. What this does not decide

Whether the limit is 1,500 or 1,000 (the brief and the audit template disagree; at 1,000, eleven
more files violate), and whether the four oversized **Rust** files are tractable at all given that
this development box cannot compile Rust and every attempt is a CI round-trip.
