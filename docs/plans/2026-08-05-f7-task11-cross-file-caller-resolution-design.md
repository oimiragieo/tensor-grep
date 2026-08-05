# F7 Task 11 — cross-file caller resolution (design)

Status: DESIGN APPROVED by 8-seat council 2026-08-05 (unanimous `PROCEED_B_WITH_CHANGES`).
Chairman: Opus 5. Spine seat: `claude` (fable-5). Verified deltas from `droid_glm`, `cursor`,
`codex`. Council log: `/tmp/tt_f7t11/`.

## The correction that justifies this document

My pre-council design targeted `_confirm_import_edges` (`repo_map.py:17726`) as the gate to
widen. **That is the wrong seam**, and building against it would have been the most expensive
possible mistake: five waves shipping green while the consumers observed nothing.

- `_confirm_import_edges` serves **`tg importers` only** (`repo_map.py:17995`).
- The seam `blast_radius_floor` keys on is `file_imports_symbol_from_definition` on the
  `LanguageSpec`, plus call-site-to-definition confirmation in `build_symbol_callers_from_map`
  (`repo_map.py:18128`).
- That field is `None` for all five target languages — `repo_map.py:6643`, `:6690`, `:6734`,
  `:6787`, `:6847` — and the code **already names this work**: `"Task 11A"` at `:6619-6622`.

A second, independent gap (codex, `repo_map.py:18192-18207` and `:18233-18260`): the caller loop
admits files by **literal name match without import proof**, then appends their in-file calls
**without binding them to the selected definition**. So "resolve the import" is necessary and not
sufficient — the acceptance criterion is **target-symbol binding**, not file resolution.

My stated reason for rejecting approach A was also wrong (cursor): confirm does not consume
`resolved` at all. A bare allow-list widen is still wrong, but because it **misroutes non-Python
files into `_python_module_matches_definition`** (`repo_map.py:17750-17760`).

## Scope

Give the five in-file-only languages a sound cross-file caller graph, so `blast_radius_floor`
stops under-reporting. Pure Python: `rust_core/**` and `tests/e2e/**` need cargo and the e2e
routing suite, both forbidden on this shared box — they route to CI or a cloud seat.

## Waves (collapsed from five to three, per council)

Grouping is by **resolution mechanism**, not by language, because that is where the shared code
actually is.

| wave | languages | mechanism | notes |
|---|---|---|---|
| **1** | Java | source-root / package mapping | first because it is the cleanest manifest-free mapping and it proves the seam end to end |
| **2** | PHP, C# | manifest-backed | PHP: PSR-4 from `composer.json`. **C# is NOT manifest-backed for symbols** — no manifest maps namespaces to files (spine seat), so it needs a namespace-to-symbol-index resolution and shares only the manifest *reader* plumbing, not the resolution strategy |
| **3** | C, C++ | include-path resolution | one engine, two thin adapters (codex) |

**Go is explicitly a sixth, deferred wave.** My original framing omitted it entirely; three seats
caught that. Go already has `references_and_calls` but the same `file_imports_symbol_from_definition`
gap, and `_go_import_path_to_dir` resolves to a *package directory*, not a file — a distinct
problem, deferred rather than smuggled in.

## Per-wave exit criterion — this is the load-bearing change

A wave is **not** done when its import resolver works. A wave is done when:

1. `LanguageSpec.file_imports_symbol_from_definition` is non-`None` for that language, and
2. a caller in another file appears in `blast_radius_floor` for a real fixture, and
3. that caller is **bound to the selected definition** — not merely a literal-name match
   (`repo_map.py:18192-18207`), and
4. a behaviour-specific RED arm was recorded first (an `ImportError`/`NameError` is a FALSE red),
   and
5. the two confidence bands are **mutation-proved**: collapsing confirmed to demoted turns tests
   red; reverting restores green with the source file byte-identical.

Criterion 2 is the one that would have caught the original design's fatal flaw, and it is stated
as a *product observable*, not as a test that the resolver ran.

## The measurement precondition — resolved

I proposed a Step-0 re-measure gating all waves. The council split on scope and converged: the
re-measure is **necessary but must not serially freeze the campaign**.

- The justifying number (cross-file 1.7:1, 46% of symbols) is regex ground truth on **one C++
  repo** — and C++ is the *least* representative of the five (glm), so it justifies nothing about
  the manifest-backed languages.
- Resolution: each wave carries its **own parsed resolvability measurement as that wave's test
  oracle** (spine seat), and a parsed Java + C# corpus measurement runs **in parallel with wave 1**
  to size and order waves 2-3 — not as a serial gate.

So: not delay dressed as rigour, and not a campaign-wide freeze. The measurement becomes the
oracle rather than a checkpoint.

## Highest unnamed risk (council question 5)

Named by the spine seat and independently by cursor: **five waves can each ship green while the
consumer observes zero change**, because the tests would assert the resolver's own output rather
than the floor. Mitigated by exit criterion 2 above, which is a product observable.

Secondary (codex): binding a caller to *a* definition of the right name is not binding it to *the
selected* definition — overload and same-name-across-files cases must demote, not guess, exactly
as the in-file waves did.

## Non-goals

Full DSL parity; resolving through build-system conditionals; C# assembly-level references beyond
the source tree; anything requiring `rust_core` or the e2e routing suite.
