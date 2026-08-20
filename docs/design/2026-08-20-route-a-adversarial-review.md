# Adversarial review: is the Route A rewrite mechanically safe?

**Date:** 2026-08-20. **Reviews:** `docs/design/2026-08-19-split-floor-escape.md` §3–§5.
**Verdict:** the mechanical rewrite is **cleared** for all three modules. The **performance**
question is **not** cleared and must be measured per module, as the design already says.

**Scope, stated up front so it is not over-read.** This is a STATIC review — an AST census of
every call site plus three precondition checks. It is not a multi-seat council, and it does not
clear the runtime cost. It answers exactly one question: *can `NAME(...)` → `_self.NAME(...)`
change behaviour anywhere in these files?*

---

## 1. The hazard that would have made this unsafe

`_self = sys.modules[__name__]` is safe inside a function body — the design's example — because
the attribute is read when the function RUNS, by which time the module is fully imported.

It is not automatically safe elsewhere. A bare call can also appear where the expression is
evaluated **at import time**: module level, a class body, a decorator, or a default argument. At
those points the module object is still being populated, so `_self.NAME` can raise
`AttributeError` where bare `NAME` resolved fine. The design's cost table counts all sites
equally and does not distinguish them.

So the falsifiable claim under test is: **"all 393 sites are mechanically convertible."**

## 2. Census of every site, by evaluation context

`scripts/` has no committed tool for this; the classification walks each `ast.Call` up its parent
chain to the nearest scope-defining construct.

| module | FUNC-BODY | LAMBDA-BODY | import-time | total |
|---|---:|---:|---:|---:|
| `mcp_server.py` | 125 | 0 | **0** | 125 |
| `main.py` | 102 | 0 | **0** | 102 |
| `repo_map.py` | 162 | 4 | **0** | 166 |
| | | | | **393** |

**Every one of the 393 sites is in a deferred context.** The import-time hazard is not mitigated
here — it is **absent**. That is a stronger result than the design assumed, and it is the main
reason this review clears the rewrite.

The four `LAMBDA-BODY` sites in `repo_map.py` are language-registry entries of the form
`parser_for_path=lambda path: _rust_parser()` (around `repo_map.py:6550`, `:6559`, `:6569`,
`:6633` — locate them with
`grep -n 'parser_for_path=lambda' src/tensor_grep/cli/repo_map.py`). The lambda is CREATED at
import; its body runs when a parser is requested. Deferred, therefore safe.

### The first version of this census was wrong, in the dangerous direction

It reported those same four sites as **MODULE-LEVEL import-time hazards**, because the parent
walk treated `ast.Lambda` as transparent and fell through to module scope. That would have sent
someone hunting an ordering bug that does not exist, or added a needless constraint to the plan.

It was caught by opening `repo_map.py:6550` and reading it, not by re-running the probe. Same
shape as the costing-script defect this design has already been corrected for once: **the
instrument was wrong, not the subject.** A lambda's defaults ARE evaluated at def time, so the
corrected classifier distinguishes `LAMBDA-BODY` from `DEFAULT-ARG` rather than collapsing both.

## 3. Preconditions the rewrite needs

| precondition | `mcp_server` | `main` | `repo_map` |
|---|---|---|---|
| `_self` unused (no collision) | ✅ 0 refs | ✅ 0 refs | ✅ 0 refs |
| `import sys` already present | ✅ | ✅ | ✅ |

So the preamble is two lines with no new import and no rename risk in any of the three.

## 4. What this review does NOT clear

- **Runtime cost.** `_self.X` is a dict lookup per call. `repo_map.py`'s
  `_read_source_text_cached` is called 14× in-module and is genuinely hot. The design already
  requires the benchmark gate per conversion; nothing here substitutes for it. If a hot loop
  regresses, that call site stays bare and its function stays in the facade — partial escape is
  an acceptable outcome.
- **That zero bare calls means splittable.** `scripts/bare_call_ratchet.py` counts one
  mechanism. Class methods, closures, `global` rebinding and `spec_from_file_location` are not
  modelled. Re-run `scripts/measure_split_floor.py` after a module reaches zero.
- **Behavioural equivalence of the surrounding refactor.** This clears the *rewrite*. It says
  nothing about the *split* that follows it.

## 5. Verdict

**Proceed with `mcp_server.py`** (design §5 step 2), on the sequencing the corrected numbers
support: 8 dependent test files against 19 and 55, and 54 of its 125 sites a single symbol.

The gate from step 1 (`scripts/bare_call_ratchet.py`, PR #1036) makes the conversion verifiable:
the pin moves 125 → 0 in provable decrements, and cannot be half-done and believed complete.

**One caveat on this review's own authority:** it is a static census by a single reviewer, and
this repo's rule for load-bearing changes is an independent adversarial seat. The census is
reproducible — the table above is a count anyone can re-derive — but the judgement that "deferred
⇒ safe" has not been independently challenged. Treat §5's clearance as covering the MECHANICAL
question only.
