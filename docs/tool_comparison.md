# Tool Comparison

There is no single universal winner: one benchmark is never enough.

Choose a comparator for the workload being measured. `ripgrep` is the text-search
baseline, `ast-grep` the structural-search baseline, and `git grep --no-index` a
useful additional text-search comparator. Policy scanning and repository-scale
indexed search require their own corpora, correctness definitions, and tool setup.

This page makes no current head-to-head speed or competitor language-count claim.
Use [benchmark methods](benchmarks.md) and the actual versioned artifact to assess
results on a particular system.

## Text-search compatibility

The validated flag set and intentional differences from `rg` are documented in
[Contracts](CONTRACTS.md#3-text-search-compatibility). The parity suite is
`tests/e2e/test_rg_parity_matrix.py`; its benchmark output is
`artifacts/bench_run_rg_parity_benchmarks.json`. Use sorted output for deterministic
path-order comparisons. Help output alone does not establish compatibility.

## One Call To Edit Readiness

`tg prepare` combines a primary edit target, confidence, a callers/blast-radius
floor with provenance, detected validation commands, and `ask_user_before_editing`.
These suggestions help a caller plan an edit; they do not authorize or verify it.

```bash
tg prepare src/ "task description" --out capsule.json --json
```

## What The Answer Says When It Could Not Finish

Inspect both exit status and the JSON completeness fields. For search and symbol
surfaces, `0` means a complete result, `1` a complete not-found result, and `2` an
error or incomplete result. Commands have specific exceptions documented in
[Contracts](CONTRACTS.md). An output cap, scan cap, deadline, and unreadable path
have different implications; do not treat all small results as complete.

`budget_remediable` identifies whether a larger budget may help. An unreadable
path or unknown cause must not be answered by automatically increasing the cap.

## Language Coverage

`tg` supports 10 registered languages split into two tiers with different guarantees, and the split is about what an
agent can safely do with an answer:

| Tier | Languages | What an agent can do with the answer |
| --- | --- | --- |
| Parser-backed refs/callers | C, C#, C++, Go, Java, JavaScript, PHP, Python, Rust, TypeScript | `tg refs` / `tg callers` / `tg blast-radius` return AST-verified reference and call sites. An empty result is limited by the reported scan scope and resolution gaps; it is not proof that deletion is safe. |
| Foundational defs/imports only | *(empty)* | No currently registered language is in this tier. |

**The gap that remains is cross-file, not per-language.** All ten are AST-verified IN-FILE: a
same-file reference or call is resolved from a real parse. Cross-file caller confirmation still
falls back to the same literal-text prefilter for **every** language, because no
package/source-root resolver (`import_update_target`) ships yet; a `resolution_gaps` entry names
that gap per language rather than letting it pass as a proven zero. C++'s confirmed band is
deliberately narrower than the rest (bare-identifier, qualified calls, and explicit `this->`, never
an arbitrary receiver -- see `lang_cpp.py`'s resolver contract for the inheritance/`auto`/template
reasoning).

Do not hand-count this table. Ask the product, which derives it from the live registry:

```bash
PYTHONPATH=src python -c "from tensor_grep.cli import repo_map; print(repo_map._symbol_navigation_descriptor())"
# parser-backed-refs-callers:c-cpp-csharp-go-java-javascript-php-python-rust-typescript+foundational-defs-imports-only:
```

Each refs/callers entry in a JSON payload also carries its own per-file `provenance` field, so a
consumer can branch on how a specific answer was produced instead of memorizing this table.

**Re-derive this table from the product.** Both tier lists are computed live from the language
registry and stamped into every repo-map JSON payload — this section is a transcription, and the
payload wins if they ever disagree. For example:

```bash
tg defs src/tensor_grep/cli/lang_registry.py register_language --json
```

The payload's `coverage` block, verbatim:

```json
{
  "language_scope": "c-cpp-csharp-go-java-javascript-php-python-rust-typescript",
  "symbol_navigation": "parser-backed-refs-callers:c-cpp-csharp-go-java-javascript-php-python-rust-typescript+foundational-defs-imports-only:",
  "test_matching": "filename+import+graph-heuristic"
}
```

`symbol_navigation` is the two-tier split; `language_scope` is the ten-language registry. Both are
derived from `lang_registry.LANGUAGE_REGISTRY` at call time (grep `_symbol_navigation_descriptor`
in `src/tensor_grep/cli/repo_map.py`), so a newly onboarded language lands in the correct bucket
without anyone editing this file.


## Comparator Policy

Publish a comparison only with a reproducible corpus, exact tool versions and
commands, equivalent output semantics, correctness checks, and raw timing samples.
`benchmarks/run_tool_comparison_benchmarks.py` writes
`artifacts/bench_tool_comparison.json`; inspect tool availability and every row's
actual route before interpreting the summary. Warm index reuse, cold startup,
structural search, and complete edit workflows are distinct measurement surfaces.
