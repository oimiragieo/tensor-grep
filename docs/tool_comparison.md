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
- current semantic result: all 22 validated rows match pinned `rg` on the deterministic corpus
- current timing result on this Windows host: every benchmarked validated row is slower than pinned `rg`

This is the intended read:

- `rg` remains the cold text-search baseline
- `tg` now has a narrower but explicit, measured compatibility claim for the common search rows it validates
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

## Where `tensor-grep` Is Stronger

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
