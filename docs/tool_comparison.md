# Tool Comparison

`tensor-grep` should not be described as a single universal winner over every other search tool.
The honest comparison surface is workload-specific:

- `ripgrep` is the cold generic text-search baseline
- `tensor-grep --cpu` is the native CPU probe for large-file and count-heavy workloads
- `ast-grep` is the structural search and rewrite baseline
- `Semgrep` is the policy and security scanning baseline
- `Zoekt` is the indexed search-at-scale baseline

The comparison format here deliberately follows the useful part of the `ripgrep` README benchmark
style: show the workload, show the command shape, show the median, and state plainly that one benchmark is never enough.

Two comparison axes run through this document. The benchmark tables below cover the first:
engine speed and contract parity against grep-class and structural-search comparators. The second
axis — what `tg` returns to an AI agent in one call, and what its answer admits when it could not
finish — is covered in the "One Call To Edit Readiness" and "What The Answer Says When It Could Not
Finish" sections below. The speed comparators do not compete on that axis, and speed is not the
axis `tg` is built for.

## Public Comparison Snapshot

The current public comparison story is anchored to rerunnable artifacts, not one-off anecdotes.

| Workload | Comparator | Current read | Source |
| --- | --- | --- | --- |
| Cold generic text search | `ripgrep` | `rg` remains the baseline on the current release line. `tg search` keeps CLI contract parity, but the current cold-path rerun does not beat `rg` on this Windows host. | `artifacts/bench_run_benchmarks.json`, [benchmarks.md](benchmarks.md) |
| Host-local CLI comparison | `ripgrep`, `git grep --no-index` | On the current host, `rg` wins the standard-corpus row, while `tg search` is effectively tied with `rg` on the 200MB large-file row. | `artifacts/bench_tool_comparison.json` |
| Native CPU text search | `ripgrep` | With rg fallback disabled for native measurement, the current `tg --cpu` rerun wins all four native CPU rows, including count-heavy and many-file probes. | `artifacts/bench_run_native_cpu_benchmarks.json`, [benchmarks.md](benchmarks.md) |
| AST search and rewrite | `ast-grep` | `tg` is ahead on AST search (`0.116s` vs `0.151s`, `0.770x`) and the one-shot rewrite apply path remains under the `sg` gate (`0.636s` vs `0.719s`, `0.885x`). | [benchmarks.md](benchmarks.md) |
| Repeated query on unchanged corpora | cold grep-style tools | `tg` wins after warm index reuse. This is a different workload class from one-shot cold scans. | `artifacts/bench_hot_query_benchmarks.json` |
| Policy and security scanning | `Semgrep` | `Semgrep` remains the stronger ecosystem baseline today. | [benchmarks.md](benchmarks.md) |
| Indexed search at repository scale | `Zoekt` | `Zoekt` remains the search-at-scale baseline. `tg` currently publishes local repeated-query wins rather than an accepted direct Zoekt bakeoff. | [benchmarks.md](benchmarks.md) |

## Validated `rg` Contract Snapshot

The `v1.4.5` contract work adds a deterministic parity corpus plus a contract-driven benchmark artifact for the validated rg-compatible surface.

- parity suite: `tests/e2e/test_rg_parity_matrix.py`
- benchmark artifact: `artifacts/bench_run_rg_parity_benchmarks.json`
- current semantic result: all 23 validated rows match pinned `rg` on the deterministic corpus
- current deterministic edge coverage: `--files-with-matches --sort path`, `--files-without-match --sort path`, `--replace --sort path`, ignored directories after `git init`, Windows path normalization, binary exclusion by default, and match/no-match/parse-error/binary-skip exit codes
- current timing result on this Windows host: every benchmarked validated row is slower than pinned `rg`

This is the intended read:

- `rg` remains the cold text-search baseline
- `tg` now has a narrower but explicit, measured validated compatibility set for the common search rows it validates
- deterministic stdout equality is supported for the validated rows and sorted edge cases; raw unsorted root ordering is still semantic parity
- `ast-grep` remains the structural comparator for `run`, `scan`, `test`, and `new`, not the cold text-search comparator

## Host-Local Command Snapshot

These are the current rerunnable rows from `artifacts/bench_tool_comparison.json`.
They are medians over three timed samples after one warmup run on this Windows host.

| Scenario | Tool | Command | Line count | Median | vs `rg` |
| --- | --- | --- | --- | --- | --- |
| standard corpus | `rg` | `rg --no-ignore ERROR artifacts/bench_data` | `800001` | `0.227s` | `1.00x` |
| standard corpus | `tg search` | `tg search --no-ignore ERROR artifacts/bench_data` | `800001` | `0.288s` | `1.27x` |
| standard corpus | `tg search --cpu` | `tg search --cpu --no-ignore ERROR artifacts/bench_data` | `800001` | `0.288s` | `1.27x` |
| standard corpus | `git grep --no-index` | `git grep --no-index -n ERROR artifacts/bench_data` | `800001` | `0.278s` | `1.22x` |
| 200MB large file | `rg` | `rg --no-ignore ERROR artifacts/native_cpu_bench_data/large_file_200mb.log` | `4271` | `0.221s` | `1.00x` |
| 200MB large file | `tg search` | `tg search --no-ignore ERROR artifacts/native_cpu_bench_data/large_file_200mb.log` | `4271` | `0.220s` | `1.00x` |
| 200MB large file | `tg search --cpu` | `tg search --cpu --no-ignore ERROR artifacts/native_cpu_bench_data/large_file_200mb.log` | `4271` | `0.220s` | `1.00x` |
| 200MB large file | `git grep --no-index` | `git grep --no-index -n ERROR artifacts/native_cpu_bench_data/large_file_200mb.log` | `4271` | `0.232s` | `1.05x` |

## One Call To Edit Readiness

Every row above is speed or parity. Those rows are true, and they are not the axis `tg` is built
for. `tg`'s differentiating surface is agent-facing: `tg prepare` returns, in a single call, the
four things an agent otherwise assembles from a multi-step orient -> search -> agent -> route-test
-> callers loop:

1. a primary edit target (file/symbol) with a confidence score,
2. a callers/blast-radius floor with graph-trust provenance,
3. detected validation commands (each carrying its own detection and confidence fields, so an
   unverified suggestion is distinguishable from a detected one),
4. a machine-branchable `ask_user_before_editing` flag that hands the decision back to a human.

```bash
tg prepare src/ "task description" --out capsule.json --json
```

In the 2026-08-01 comparator survey (`docs/positioning/2026-08-01-policy-layer-moat.md`, each
competitor claim carrying a dated URL), no surveyed agent-facing code tool bundled all four in one
call: CodeGraph's `codegraph_explore` covers target + blast radius, GitNexus's `impact` covers
confidence + blast radius, and Aider's repo map returns a ranked target list only. None of the
grep-class comparators in the tables above attempt this surface at all.

## What The Answer Says When It Could Not Finish

`tg`'s primary caller is an AI agent, and an agent cannot tell a genuinely empty result from a
silently truncated one — "no callers found" acted on in good faith deletes working code. Section 0
of [docs/CONTRACTS.md](CONTRACTS.md) makes completeness disclosure a contract governing every
command, enforced by CI ratchets (`tests/unit/test_silent_loss_census_ratchet.py`,
`tests/unit/test_native_walk_error_ratchet.py`). Three properties, stated at their real width:

- **The exit code agrees with the payload.** `0` complete / `1` not found / `2` incomplete, with
  the JSON envelope carrying the matching disclosure fields. Rerunnable two-arm receipt, run on
  this repo against `tg 1.101.31`:

  | Arm | Command shape | Exit | Payload |
  | --- | --- | --- | --- |
  | complete | `tg prepare src/tensor_grep/cli/incompleteness.py "budget_remediable" --json --deadline 25` | `0` | no `partial` key; `confidence.overall = 0.9`, empty `downgrade_reasons` |
  | truncated | `tg prepare src "budget_remediable" --json --deadline 0.1` | `2` | `partial: true`, `partial_reason: "deadline"`, `confidence.overall` downgraded `0.9 -> 0.72` with three named reasons, `ask_user_before_editing.required: true` |

  A truncated scan does not merely append a flag — it lowers the confidence number the agent is
  supposed to gate on, and flips `ask_user_before_editing`.

- **A closed reason-class vocabulary with a remediability verdict.** `budget_remediable()` in
  `src/tensor_grep/cli/incompleteness.py` is a fail-closed allow-list answering "would a bigger
  budget fix this?": a budget cause (`scan_limit`, `deadline`, `timeout`) means raise the knob; an
  unreadable path means no knob will help; an unrecognized cause is never answered with "retry
  bigger".

- **Contract-level scope, not a per-endpoint flag.** The disclosure fields span the Python CLI and
  the Rust native core (grep `result_incomplete` across `src/` and `rust_core/src/` — the census
  ratchets above pin the counts so they can fall but never silently rise).

Stated at honest width: incompleteness disclosure exists in fragments elsewhere — GitHub's REST
Search API ships a required `incomplete_results` boolean, and LSP ships
`CompletionList.isIncomplete` on completion results. What the 2026-08-01 survey did not find
elsewhere is the combination: an exit code that agrees with the payload, a closed reason-class
vocabulary carrying a remediability verdict, and contract-level scope enforced by CI ratchets. MCP
— the protocol `tg mcp` itself rides on — has no standardized equivalent field in its current
stable spec.

## Language Coverage

Competitor language counts are large: Gortex publishes **256 or 257** languages depending on which
of its own documents you read — `docs/languages.md` says "currently indexes 256 languages" and its
per-category table totals 256, while the README, `docs/features.md`, and gortex.dev all say 257
(verified 2026-08-02). We cite the range rather than pick a side; the distinction does not change
any conclusion below, and quoting a competitor's number more precisely than the competitor does is
how a comparison loses credibility. Serena advertises 40+. `tg`
registers ten. The honest comparison needs the tier structure on both sides, because "supports
language X" spans everything from resolved call edges to a filename glob.

`tg`'s ten languages split into two tiers with different guarantees, and the split is about what an
agent can safely do with an answer:

| Tier | Languages | What an agent can do with the answer |
| --- | --- | --- |
| Parser-backed refs/callers | C, C#, C++, Go, Java, JavaScript, PHP, Python, Rust, TypeScript | `tg refs` / `tg callers` / `tg blast-radius` return AST-verified reference and call sites. A complete-scan "no callers found" here is evidence for a rename or a deletion. |
| Foundational defs/imports only | *(empty)* | No language sits in this tier any more. C (Task 10D) and C++ (Task 10E) were the last two, promoted in the final wave. |

**The gap that remains is cross-file, not per-language.** All ten are AST-verified IN-FILE: a
same-file reference or call is resolved from a real parse. Cross-file caller confirmation still
falls back to the same literal-text prefilter for **every** language, because no
package/source-root resolver (`import_update_target`) ships yet; a `resolution_gaps` entry names
that gap per language rather than letting it pass as a proven zero. C++'s confirmed band is
deliberately narrower than the rest (bare-identifier, qualified calls, and explicit `this->`, never
an arbitrary receiver -- see `lang_cpp.py`'s Task 10E docstring for the inheritance/`auto`/template
reasoning).

Do not hand-count this table. Ask the product, which derives it from the live registry:

```bash
PYTHONPATH=src python -c "from tensor_grep.cli import repo_map; print(repo_map._symbol_navigation_descriptor())"
# parser-backed-refs-callers:c-cpp-csharp-go-java-javascript-php-python-rust-typescript+foundational-defs-imports-only:
```

This split has been wrong in four separate documents at four different values (6+4, 5+5, 8+2,
9+1) while the registry moved underneath them. The one-liner above is the only source that cannot
go stale.

Each refs/callers entry in a JSON payload also carries its own per-file `provenance` field, so a
consumer can branch on how a specific answer was produced instead of memorizing this table.

**Where other tools are ahead, stated plainly.** Gortex's own docs publish the same kind of tiered
breakdown — ~30 bespoke tree-sitter languages with resolved call edges, ~60 regex, ~165
signature-only — and its deep tier covers all ten of `tg`'s languages plus roughly twenty more
(2026-08-01 survey, `docs/positioning/2026-08-01-policy-layer-moat.md`, each claim carrying a dated
URL). On the apples-to-apples deep tier the count is roughly 10 vs 30, in Gortex's favor — and
`tg`'s ten are in-file resolved, with cross-file confirmation still on the text prefilter, so the
gap in resolved-edge terms is wider than 10-vs-30 alone suggests. Serena's
40+ comes from wrapping LSP servers. Tiered language disclosure itself is normal industry practice
(Semgrep's maturity levels, Sourcegraph's precise/syntactic/search-based navigation, Zed's
LSP-vs-highlighting split, nvim-treesitter's per-grammar tiers), so this table is table stakes, not
a differentiator. No shared-corpus benchmark comparing resolved-edge quality across these tools
existed as of that survey, so any depth claim in either direction is an architectural statement,
not a measured one.

**Why publish the deep tier at all, then.** Because the tier decides which failure mode a caller is
exposed to. The job `tg` is built for — an agent editing code it did not write — is exactly the job
where a text-heuristic "no callers" acted on in good faith deletes working code. For eight languages
`tg` gives the verified answer (for three of those eight, Java/C#/PHP, only within a single file so
far -- see the table caveat); for the other two it still answers, but it labels the mechanism,
and this document says so before a competitor does.

**Re-derive this table; do not trust it.** Both tier lists are computed live from the language
registry and stamped into every repo-map JSON payload — this section is a transcription, and the
payload wins if they ever disagree. Rerunnable receipt, from this repo against `tg 1.101.31`:

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
without anyone editing this file. A hardcoded ancestor of that field once under-reported coverage
for an entire language-onboarding campaign; the derivation exists because prose counts rot.

## Where `tensor-grep` Is Stronger

- One-call edit readiness (`tg prepare`) and the contract-level incompleteness disclosure surface described above
- Native AST search and benchmark-recovered one-shot AST rewrite apply workflows
- Warm repeated-query search on unchanged corpora
- Machine-readable CLI, NDJSON, session, and MCP surfaces for agent workflows
- Output-side replacement for text search plus real AST-backed rewrite application
- Count-heavy native CPU probes and workload-specific large-file CPU paths
- Managed enterprise surface: CI contracts, release validation, supply-chain automation, and operational docs

## Where Other Tools Still Lead

- `ripgrep` still owns the cold generic text-search baseline on the current release line
- `Semgrep` still has the stronger policy and security scanning ecosystem
- `Zoekt` is still the external baseline for indexed search at repository scale
- Gortex leads on language coverage at every tier: ~30 deep-tier languages with resolved call edges against `tg`'s 10 (P2, all foundational languages upgraded to parser-backed — re-derive via `tg defs src/tensor_grep/cli/lang_registry.py register_language --json`), and 256-257 total against 10 (see "Language Coverage" above)
- Minimal standalone footprint still favors pure single-purpose tools such as `rg`
- Default cold text search on the current Windows host still favors `rg`; the latest large-file row is effectively tied between `rg` and default `tg search`, not a general cold-search win.

## Comparator Policy

Do not add public head-to-head claims unless the comparison is reproducible and checked into the accepted benchmark surface.

At the moment:

- `rg`, `git grep --no-index`, and `ast-grep` have concrete published comparison rows or accepted benchmark sections
- `Semgrep` and `Zoekt` are explicitly documented as workload anchors, not marketing props
- `ag`, `ack`, `ugrep`, and GNU `grep` are not yet part of the accepted comparator pack on this host, so the project should not publish hard claims about them beyond the local availability note in `artifacts/bench_tool_comparison.json`

The next comparator expansion should be a reproducible pack for `ag`, `ack`, `ugrep`, and GNU `grep` with locked flags, documented fixture setup, and committed artifact output.
