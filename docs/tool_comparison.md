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
- Minimal standalone footprint still favors pure single-purpose tools such as `rg`
- Default cold text search on the current Windows host still favors `rg`; the latest large-file row is effectively tied between `rg` and default `tg search`, not a general cold-search win.

## Comparator Policy

Do not add public head-to-head claims unless the comparison is reproducible and checked into the accepted benchmark surface.

At the moment:

- `rg`, `git grep --no-index`, and `ast-grep` have concrete published comparison rows or accepted benchmark sections
- `Semgrep` and `Zoekt` are explicitly documented as workload anchors, not marketing props
- `ag`, `ack`, `ugrep`, and GNU `grep` are not yet part of the accepted comparator pack on this host, so the project should not publish hard claims about them beyond the local availability note in `artifacts/bench_tool_comparison.json`

The next comparator expansion should be a reproducible pack for `ag`, `ack`, `ugrep`, and GNU `grep` with locked flags, documented fixture setup, and committed artifact output.
