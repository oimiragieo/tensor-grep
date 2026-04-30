# Benchmarks

`tensor-grep` is designed as a routing-first search engine that keeps strict behavioral parity while selecting the best backend per query class.

## Benchmark Matrix

These scripts and artifact paths are the accepted benchmark surface for the current line.

| Surface | Script | Default artifact |
| --- | --- | --- |
| End-to-end CLI text search | `benchmarks/run_benchmarks.py` | `artifacts/bench_run_benchmarks.json` |
| Native CPU large-file / many-file search | `benchmarks/run_native_cpu_benchmarks.py` | `artifacts/bench_run_native_cpu_benchmarks.json` |
| Host-local CLI tool comparison | `benchmarks/run_tool_comparison_benchmarks.py` | `artifacts/bench_tool_comparison.json` |
| Repeated-query / hot-cache search | `benchmarks/run_hot_query_benchmarks.py` | `artifacts/bench_hot_query_benchmarks.json` |
| AST single-query gate | `benchmarks/run_ast_benchmarks.py` | `artifacts/bench_run_ast_benchmarks.json` |
| AST multi-language search | `benchmarks/run_ast_multilang_benchmarks.py` | `artifacts/bench_ast_multilang.json` |
| AST rewrite plan/diff/apply | `benchmarks/run_ast_rewrite_benchmarks.py` | `artifacts/bench_ast_rewrite.json` |
| AST workflow startup | `benchmarks/run_ast_workflow_benchmarks.py` | `artifacts/bench_run_ast_workflow_benchmarks.json` |
| Provider-mode hardcase navigation | `benchmarks/run_provider_navigation_bakeoff.py` | `artifacts/bench_provider_navigation_click_hardcases.json` |
| Repository planning retrieval | `benchmarks/run_repo_retrieval_benchmarks.py` | `artifacts/bench_repo_retrieval_benchmarks.json` |
| Editor-plane context render | `benchmarks/run_context_render_benchmarks.py` | `artifacts/bench_context_render.json` |
| Blast-radius render latency | `benchmarks/run_blast_radius_benchmarks.py` | `artifacts/bench_blast_radius.json` |
| Python GPU/NLP benchmark | `benchmarks/run_gpu_benchmarks.py` | `artifacts/bench_run_gpu_benchmarks.json` |
| Native GPU crossover / throughput | `benchmarks/run_gpu_native_benchmarks.py` | `artifacts/bench_run_gpu_native_benchmarks.json` |
| Harness loop | `benchmarks/run_harness_loop_benchmark.py` | `artifacts/bench_harness_loop.json` |
| Index build/query scaling | `benchmarks/run_index_scaling_benchmark.py` | `artifacts/bench_index_scaling.json` |

## Artifact Conventions

Every committed benchmark artifact should make the measurement surface machine-readable.

Required top-level fields vary by suite, but benchmark artifacts should consistently expose:

- `suite`
- `artifact`
- `environment`
- `generated_at_epoch_s`
- `rows` or equivalent summary payload

Environment blocks should at minimum record:

- `platform`
- `machine`
- `python_version` when Python orchestrates the benchmark

For `run_benchmarks.py`, the environment block should also record `tg_launcher_mode` so cold-path comparisons stay tied to an explicit entrypoint mode rather than inferred from the command line after the fact.

For `run_repo_retrieval_benchmarks.py`, the metrics block should expose retrieval-quality and context-efficiency keys explicitly:

- `recall_at_k`
- `precision_at_k`
- `mrr_at_k`
- `ndcg_at_k`
- `file_f1`
- `line_f1`
- `p50_latency_ms`
- `token_budget_mean`

## Accepted Repo-Map Lexical Retrieval Snapshot (2026-04-19)

The current accepted repo-map retrieval change is a quality win, not a speed-marketing claim.

Curated retrieval artifact line:

- clean `origin/main` baseline artifact: `artifacts/bench_repo_retrieval_lexical_base.json`
- accepted lexical feature artifact: `artifacts/bench_repo_retrieval_lexical_feature.json`
- baseline metrics: `recall_at_5 = 0.0`, `precision_at_5 = 0.0`, `mrr_at_5 = 0.0`, `ndcg_at_5 = 0.0`
- accepted feature metrics: `recall_at_5 = 1.0`, `precision_at_5 = 0.2`, `mrr_at_5 = 1.0`, `ndcg_at_5 = 1.0`, `file_f1 = 0.333333`, `line_f1 = 0.222222`

Accepted implementation boundary:

- camelCase queries can now recover snake_case definitions through symbol-aware lexical expansion
- exact snake_case symbol queries stay anchored to exact definitions instead of over-ranking partial split matches such as `build_invoice`
- source-term scanning is now a bounded fallback when parser/path evidence is weak, not the default hot path

Editor-plane guardrails were rerun on the same host before accepting the line:

- `context-render` improved against the refreshed clean-head baseline on all three fixture sizes (`small`, `medium`, `large`) in `artifacts/bench_context_render_v140.json` versus `artifacts/bench_context_render_base_refresh.json`
- blast-radius remained in the same measured band after the exact-symbol correction in `artifacts/bench_blast_radius_v140.json` versus `artifacts/bench_blast_radius_base_refresh.json`; representative rows are `medium depth=2: 0.3508s vs 0.3417s` and `large depth=2: 1.4572s vs 1.4673s`

The accepted product read is therefore narrow and explicit: lexical-first repo-map retrieval now fixes the curated planning misses without reopening the earlier cold-path or provider-default decisions.

## Bounded Full Edit-Plan Seed Snapshot (2026-04-25)

The current bounded context-render contract is a correctness and feasibility line, not a speedup claim.

- `build_context_render(..., max_repo_files=N, include_edit_plan_seed=True)` keeps `edit_plan_seed`, `candidate_edit_targets`, and `navigation_pack` inside the capped repo-map file universe.
- Provider-backed references/callers are filtered through the same cap before they can affect the rendered edit plan.
- Latest local artifacts: `artifacts/bench_editor_profiling.json` and `artifacts/bench_context_render.json`.
- Latest `bench_context_render` medians: `small cold=0.5227s / warm=0.5122s`, `medium cold=0.7458s / warm=0.7309s`, `large cold=2.1434s / warm=2.2001s`.

Current Roadmap 1 launcher-mode read on this host:

- `python_module_launcher`: mean `tg_time_s = 0.252554`, median `tg_time_s = 0.230292`
- `explicit_binary`: mean `tg_time_s = 0.282347`, median `tg_time_s = 0.269235`

This is useful control-plane evidence, but not yet an accepted speed win. Both modes still regress against the accepted Windows baseline under `benchmarks/check_regression.py`, so the current conclusion is only that `python_module_launcher` is the better measured launcher mode of the two on this host.

For the current release line, that closes Roadmap 1 as a boundary rather than leaving it as an implied open loop: there is still no accepted cold-path win from Python-side launcher variants, so a larger native rewrite is required for material movement toward raw `rg`.

For the Rust-first native control-plane roadmap, `run_benchmarks.py` should now also record `tg_binary_source` so future launcher/control-plane experiments can distinguish repo-default binary dispatch (`default_binary_path`) from a user-supplied native binary (`explicit_arg`) before making any new speed claim.

The first Rust-first native control-plane roadmap experiment is also now recorded explicitly. Forcing the env-gated `python_module_rust_first` bootstrap mode, which hands plain text search from `python -m tensor_grep` into the native `tg` binary and lets Rust own routing/fallback decisions, produces a rejected result on this host:

- `python_module_rust_first`: mean `tg_time_s = 0.386778`, median `tg_time_s = 0.384161`

That is materially worse than the earlier `python_module_launcher` line (`0.252554` mean, `0.230292` median), and `benchmarks/check_regression.py --baseline auto` reports regressions across all 10 cold-path scenarios. The benchmark-governed conclusion is therefore narrow and explicit: this bootstrap handoff shape is a rejected experiment, not a new accepted control-plane path.

The next Rust-native probe is also measured now. `explicit_binary_early_rg` is an env-gated early ripgrep fast path inside the Rust CLI itself, benchmarked at `artifacts/bench_run_benchmarks_explicit_binary_early_rg_uv.json`:

- `explicit_binary_early_rg`: mean `tg_time_s = 0.297869`, median `tg_time_s = 0.281141`

That is a useful data point but still not an accepted win. It remains materially better than the rejected `python_module_rust_first` handoff, but even after narrowing the fast path away from the glob, word-boundary, and fixed-string cases that hurt the first probe, `benchmarks/check_regression.py --baseline auto` still reports regressions across all 10 cold-path scenarios. The repo therefore records it as another rejected control-plane probe rather than a new baseline.

The next structural launcher probe is `explicit_binary_positional`, benchmarked at `artifacts/bench_run_benchmarks_explicit_binary_positional_uv.json`:

- `explicit_binary_positional`: mean `tg_time_s = 0.286235`, median `tg_time_s = 0.26987`

This mixed launcher mode uses positional Rust CLI invocation for benchmark-safe plain search shapes and falls back to `tg search` for unsupported cases. It is a real improvement over `explicit_binary_early_rg` on aggregate, but it is still not an accepted win: `benchmarks/check_regression.py --baseline auto` reports regressions on 9 of the 10 cold-path scenarios, so the repo records it as another rejected control-plane probe rather than a new baseline.

The next structural launcher probe is `explicit_binary_positional_early_rg`, benchmarked at `artifacts/bench_run_benchmarks_explicit_binary_positional_early_rg_uv.json`:

- `explicit_binary_positional_early_rg`: mean `tg_time_s = 0.268412`, median `tg_time_s = 0.255065`

This mixed launcher mode uses a raw-args positional ripgrep fast path for benchmark-safe plain search shapes and falls back to `tg search` for unsupported cases. It is the best Roadmap 1 native-control-plane probe so far, beating the aggregate line from `explicit_binary_positional`, but it is still not an accepted win: `benchmarks/check_regression.py --baseline auto` reports regressions on 7 of the 10 cold-path scenarios. The repo therefore records it as the strongest rejected probe to date rather than a new baseline.

The first rewrite-backed native probe is `explicit_fast_binary`, benchmarked at `artifacts/bench_run_benchmarks_explicit_fast_binary_uv.json`:

- `explicit_fast_binary`: mean `tg_time_s = 0.324425`, median `tg_time_s = 0.312694`

This dedicated `tg-search-fast` binary uses a manual parser for the benchmark subset and passes matching searches directly to ripgrep. It is still not an accepted win: `benchmarks/check_regression.py --baseline auto` reports regressions on 9 of the 10 cold-path scenarios, so the repo records it as another rejected control-plane probe. The useful conclusion is narrow but important: a separate minimal launcher binary by itself is not the rewrite shape that closes the remaining cold-path gap.

The benchmark suite name and artifact file name should stay aligned with the script that produced them.

## Acceptance Rules

- **Control-plane changes require artifacts:** If a patch changes launcher routing, frontend dispatch, or output formatting, it MUST include updated benchmark artifacts (e.g., `artifacts/bench_run_benchmarks.json`).
- **Regression policy:** If a patch is correct but regresses accepted benchmark lines, it must be either rejected or explicitly documented in `docs/PAPER.md` as an intentional non-goal.
- Do not update benchmark docs or claims until the relevant artifact has been rerun on the accepted line.
- Compare against the current accepted baseline, not memory.
- Reject wins that only appear in microprofiles if end-to-end artifacts regress.
- Keep backend labels explicit in artifacts so routing claims are auditable.
- Freeze artifact naming once a suite becomes part of release or contract governance.

## Windows Accepted Baseline Refresh (2026-04-18)

The previous Windows accepted baseline had become stale: fresh clean `origin/main` evidence on this host no longer passed the older March line. The accepted file `benchmarks/baselines/run_benchmarks.windows.json` is therefore refreshed from clean `origin/main` evidence captured on 2026-04-18, not from the current release-candidate branch.

This is a governance refresh, not a relaxed gate. The accepted Windows line now records the provenance fields the current benchmark surface expects:

- `benchmark_host_key`
- `host_provenance`
- `environment.tg_binary_source`
- `environment.tg_launcher_mode`

`check_regression.py` policy is unchanged. The only contract repair is that the accepted Windows baseline now matches fresh clean-head evidence on the current host class instead of forcing stale-baseline failures on clean `origin/main`.

Historical roadmap sections below that say "accepted Windows baseline" should be read against the accepted line in force for that roadmap batch, not implicitly re-scored against every later baseline refresh.

## Latest Scripted Benchmark Snapshot (2026-04-29)

The numbers below are from local benchmark artifacts generated by:

```bash
uv sync --extra dev --extra ast
uv run python benchmarks/run_benchmarks.py
uv run python benchmarks/run_native_cpu_benchmarks.py
uv run python benchmarks/run_tool_comparison_benchmarks.py
uv run python benchmarks/run_ast_benchmarks.py
uv run python benchmarks/run_ast_multilang_benchmarks.py
uv run python benchmarks/run_ast_workflow_benchmarks.py
uv run python benchmarks/run_ast_rewrite_benchmarks.py
uv run python benchmarks/run_context_render_benchmarks.py
uv run python benchmarks/run_blast_radius_benchmarks.py
uv run python benchmarks/run_harness_loop_benchmark.py
uv run python benchmarks/run_index_scaling_benchmark.py
uv run python benchmarks/run_repo_retrieval_benchmarks.py
uv run python benchmarks/run_hot_query_benchmarks.py
uv run python benchmarks/run_gpu_native_benchmarks.py
```

For the optional GPU/NLP microbenchmark:

```bash
uv sync --extra dev --extra bench --extra nlp
uv run python benchmarks/run_gpu_benchmarks.py
```

Notes:
- `cyBERT` benchmarking also requires a reachable Triton inference endpoint; when Triton is unavailable, the script now records `cybert_backend` as `SKIP` instead of failing the whole benchmark run.
- When no operational GPU device is detected, `run_gpu_benchmarks.py` now records a top-level `status: "SKIP"` before generating synthetic corpora. This prevents no-GPU CI or unsupported-device hosts from creating misleading CPU-only GPU artifacts.
- On this host, the current `run_benchmarks.py` rerun preserved output parity across all 10 rows, but `benchmarks/check_regression.py --baseline auto --current artifacts/bench_run_benchmarks.json` failed against the frozen Windows baseline because the `rg` comparator drifted and the case-insensitive `tg` row regressed by 8.93%. Treat the cold-path rerun as correctness evidence, not an accepted speed baseline refresh.
- The current host-local CLI comparison artifact is `artifacts/bench_tool_comparison.json`. It is informational, not a release-gated regression suite.
- Latest host-local CLI comparison medians: standard corpus `rg 0.227s`, `tg search 0.288s`, `tg search --cpu 0.288s`, `git grep --no-index 0.278s`; 200MB large file `rg 0.221s`, `tg search 0.220s`, `tg search --cpu 0.220s`, `git grep --no-index 0.232s`.
- Latest native CPU medians with `rg` fallback disabled: `cold_standard_corpus 0.173s vs rg 0.240s`, `large_file_200mb 0.220s vs rg 0.283s`, `large_file_200mb_count 0.072s vs rg 0.417s`, and `many_file_directory 0.159s vs rg 0.236s`; all rows passed.
- Latest hot-query medians: repeated fixed-string `0.5671s -> 0.1470s`, repeated regex-prefilter `0.5476s -> 0.1662s`; both rows passed.
- Latest AST search medians: single-query Python `tg 0.116s` vs `sg 0.151s` (`0.770x`); multi-language ratios were Python `0.722x`, JavaScript `0.800x`, TypeScript `0.726x`, and Rust `0.715x`.
- Latest AST rewrite medians from the `v1.7.0` audit: plan `0.361s`, diff `0.410s`, apply `0.464s`; `sg apply 0.537s`, `tg/sg = 0.865x`, ratio gate passed.
- Latest AST workflow medians: native `run 0.0279s`, sidecar `scan 0.2670s`, sidecar `test 0.4359s`.
- Latest editor-plane medians: context-render `small cold/warm 0.449s/0.373s`, `medium 0.691s/0.647s`, `large 1.808s/1.925s`; blast-radius `medium depth=2 0.579s`, `large depth=2 1.446s`.
- Latest harness loop medians across five iterations: search `0.343s`, plan `0.136s`, apply `0.313s`, verify `0.037s`; all iterations passed.
- Latest index scaling rows passed build/query thresholds: 1,000 files `build 0.155s / query 0.161s`, 5,000 files `0.728s / 0.691s`, 10,000 files `1.413s / 1.327s`.
- Latest native GPU crossover audit still found no crossover: device `0` completed 10MB and 100MB but remained slower than `rg`, then timed out on 500MB and 1GB; device `1` (`RTX 5070`, `sm_120`) remains unsupported by the current PyTorch/CUDA sidecar stack on this host.
- `run_repo_retrieval_benchmarks.py` now has a committed default smoke dataset at `benchmarks/datasets/repo_retrieval_eval.jsonl`, so the suite is runnable without a local-only fixture. Latest default artifact: `artifacts/bench_repo_retrieval_benchmarks.json`, with `recall_at_5 = 1.0`, `precision_at_5 = 0.333333`, `mrr_at_5 = 1.0`, `ndcg_at_5 = 1.0`, `file_f1 = 0.492064`, `line_f1 = 0.492064`, `p50_latency_ms = 4.8`, and `token_budget_mean = 74.333333`. This is benchmark-harness coverage, not a replacement for the accepted 2026-04-19 repo-map lexical feature line.
- The current accepted provider hardcase artifact is `artifacts/bench_provider_navigation_click_hardcases.json`, with a companion markdown scorecard at `artifacts/bench_provider_navigation_click_hardcases.md`.
- The current accepted JS/TS provider hardcase artifact is `artifacts/bench_provider_navigation_js_ts_hardcases.json`, with a companion markdown scorecard at `artifacts/bench_provider_navigation_js_ts_hardcases.md`.
- The current accepted Rust provider hardcase artifact is `artifacts/bench_provider_navigation_rust_hardcases.json`, with a companion markdown scorecard at `artifacts/bench_provider_navigation_rust_hardcases.md`.
- The combined accepted provider hardcase summary is `artifacts/bench_provider_navigation_hardcases_combined.md`.
- The current accepted JS/TS provider hardcase artifact is `artifacts/bench_provider_navigation_js_ts_hardcases.json`, with a companion markdown scorecard at `artifacts/bench_provider_navigation_js_ts_hardcases.md`.
- For long-running patch A/B reruns, the Claude and Gemini benchmark harnesses now treat an `instance_id` as complete only when the full expected paired rows are present; interrupted partial rows are no longer considered resumable completion.
- The Gemini A/B harness now also accepts `--scenarios` and emits scored bakeoff rows plus a per-system summary in the same output artifact, so broader Gemini-enhanced reruns can be rendered directly into patch scorecards.

## Comparative Benchmark v2

This is the frozen comparison surface for the current accepted line. Update it only when a new accepted artifact supersedes an older one.

### Frozen Comparator Set

- `tensor-grep`
- `claude-baseline`
- `claude-enhanced`
- `copilot`
- `gemini-cli`
- `gemini-baseline`
- `gemini-enhanced`

### Frozen Scenario Packs

- planning broad pack: the accepted external planning pack and companion broad provider bakeoff surface
- provider broad pack: the broad `click` provider bakeoff used for the keep-opt-in decision
- provider hardcases: the accepted Python, JS/TS, and Rust alias-wrapper hardcase packs
- patch same-pack 12-scenario line: the accepted patch-correctness comparator pack used by `real_patch_system_scorecard.md`
- cold-path local benchmark: the local `run_benchmarks.py` line used for `tg` vs `rg`

The purpose of this section is governance, not marketing. Comparator additions or pack substitutions should land only when the new artifact line is accepted and the paper plus top-level reports are updated in the same change.

## Comparative Benchmark v3

For the current next-line program, `Comparative Benchmark v3` intentionally reuses the same accepted comparator set and pack inventory until a new accepted artifact supersedes them. This keeps the comparison surface frozen for governance purposes instead of reopening the comparator list or scenario packs by implication.

## Comparative Benchmark v4

For the future roadmap, `Comparative Benchmark v4` starts from the same accepted comparator set and pack inventory, then updates only when a new accepted artifact line lands. This keeps the governance model stable while the Rust-first native control-plane roadmap is still in its provenance and benchmark stage.

For the current line, that roadmap item is now closed as governance rather than left open by implication: the comparator set and pack inventory remain frozen until a new accepted artifact supersedes them.

## Comparative Benchmark v5

For the next roadmap, `Comparative Benchmark v5` starts from the same governance rule: comparator additions, pack substitutions, and top-level report changes should land only when a new accepted artifact line exists and the paper plus benchmark docs are updated in the same change.

Closed on 2026-03-31 as a frozen comparison surface for the current line: comparator set and pack inventory remain frozen until a new accepted artifact supersedes them.

## Comparative Benchmark v6

For the next roadmap line, `Comparative Benchmark v6` keeps the same governance model: comparator additions, pack substitutions, and top-level report changes should land only when a new accepted artifact line exists and the paper plus benchmark docs are updated in the same change. The comparison surface should render only from frozen accepted inputs for that line.

The matching execution model is now explicitly parallel: lane-local work should run narrow tests and workload-specific benchmarks in parallel, while full repo gates run at merge points. That is a throughput decision, not a quality downgrade.

### External workload baselines

These are the external baselines for the current line. They are workload-class anchors, not marketing shorthand:

- `ripgrep`: cold plain-text search baseline
- `ast-grep`: structural search/rewrite baseline
- `Semgrep`: policy/security scan baseline
- `Zoekt`: indexed repeated-query baseline

The accepted product read for the current line is workload-specific:

- `tensor-grep` is not yet better than `ripgrep` on raw cold plain-text search
- native Rust AST search is ahead of `ast-grep`; the one-shot rewrite apply path is also back under the `sg` ratio gate on the latest same-repo control artifact
- `Semgrep` remains the stronger policy/security ecosystem benchmark
- `Zoekt` remains the indexed repeated-query/search-at-scale benchmark

Top-level claims should stay aligned with those workload classes instead of collapsing them into one generic “better search tool” statement.

### Native control-plane rewrite v2 (`run_benchmarks.py`)

Measured on the `explicit_binary default front door` path after promoting the fastest supported `tg search` subset into the real default front door, then refreshed after `v1.6.5`:

- artifact: `artifacts/bench_run_benchmarks_v165_control_plane_current.json`
- mean `tg_time_s`: `0.266167`
- median `tg_time_s`: `0.260132`
- parity: PASS on all 10 rows
- regression gate: `benchmarks/check_regression.py --baseline auto --current artifacts/bench_run_benchmarks_v165_control_plane_current.json` passed with no benchmark regressions

This supersedes the older rejected read for the current host artifact. The accepted status is now narrower and cleaner: the explicit native-binary front door is regression-gate clean on this Windows host, but raw `rg` remains the baseline for generic cold text search because it still wins several individual rows.

The next widening probe is explicitly rejected and preserved as history rather than code. `artifacts/bench_run_benchmarks_explicit_binary_default_frontdoor_v2_uv.json` broadened the default front door to accept the already-supported `--glob`, `-w`, and `-F` subset. That preserved parity, but it made the default `explicit_binary` line slower than the prior default-front-door artifact and still failed the frozen Windows baseline on 5 scenarios (`Case-Insensitive`, `Regex`, `File Glob Filtering`, `Word Boundary`, and `Fixed Strings`). The accepted read is narrower than “more native is always better”: widening the default front door to more ripgrep-equivalent flags was not the next win for this line.

The 2026-04-28 cold-path attribution refresh keeps that boundary intact. Current research on fast
CLI design points toward reducing eager command construction and dependency/process overhead, but
the repo evidence still says to avoid broad default-front-door widening. The measured attribution
artifact `artifacts/bench_cold_path_attribution_v166_word_boundary.json` separates explicit native
binary, discovered CLI, and Python module launcher shapes:

- `explicit_binary`: mean `0.270281s`, median `0.234517s`
- `discovered_cli_binary`: mean `0.415460s`, median `0.387956s`
- `python_module_launcher`: mean `0.377861s`, median `0.349126s`

The operational read is simple: benchmark claims should keep using the explicit repo native binary.
On this host, the shell-discovered `tg` command resolved to an older user install, so it is useful
as an environment-drift warning rather than a release-quality comparator.

The narrow follow-up accepted in this slice is only benchmark-helper coverage for the existing
positional word-boundary path. `artifacts/bench_run_benchmarks_word_boundary_positional_candidate.json`
uses `explicit_binary_positional_early_rg` for `-w/--word-regexp`, passes parity on all 10 rows, and
passes `benchmarks/check_regression.py --baseline auto`. Its aggregate is mean `0.259608s`, median
`0.243768s`; the word-boundary row measured `rg 0.241s` versus `tg 0.256s`. This is useful
attribution and a safe experimental-lane fix, not a default launcher promotion.

### Provider-mode hardcase navigation (`run_provider_navigation_bakeoff.py`)

Measured on the accepted Click-style Python alias-wrapper hardcase pack:

```bash
python benchmarks/run_provider_navigation_bakeoff.py --scenarios benchmarks/external_eval/click_provider_hardcases.json --providers native,hybrid --output artifacts/bench_provider_navigation_click_hardcases.json
python benchmarks/render_provider_navigation_scorecard.py --inputs artifacts/bench_provider_navigation_click_hardcases.json --output artifacts/bench_provider_navigation_click_hardcases.md
```

| Provider | Caller hit rate | Caller precision | Test hit rate | Result |
| --- | --- | --- | --- | --- |
| `native` | 0.0 | 0.0 | 1.0 | misses alias wrapper callers |
| `hybrid` | 1.0 | 1.0 | 1.0 | accepted hardcase win |

### Provider-mode hardcase navigation (`run_provider_navigation_bakeoff.py`) for JS/TS alias wrappers

Measured on the accepted JS/TS import-alias wrapper hardcase pack:

```bash
python benchmarks/run_provider_navigation_bakeoff.py --scenarios benchmarks/external_eval/js_ts_provider_hardcases.json --providers native,hybrid --output artifacts/bench_provider_navigation_js_ts_hardcases.json
python benchmarks/render_provider_navigation_scorecard.py --inputs artifacts/bench_provider_navigation_js_ts_hardcases.json --output artifacts/bench_provider_navigation_js_ts_hardcases.md
```

| Provider | Caller hit rate | Caller precision | Test hit rate | Result |
| --- | --- | --- | --- | --- |
| `native` | 0.0 | 0.0 | 1.0 | misses import-alias wrapper callers |
| `hybrid` | 1.0 | 1.0 | 1.0 | accepted JS/TS hardcase win |

### JS/TS provider-mode hardcase navigation (`run_provider_navigation_bakeoff.py`)

Measured on the accepted JS/TS alias-wrapper hardcase pack:

```bash
python benchmarks/run_provider_navigation_bakeoff.py --scenarios benchmarks/external_eval/js_ts_provider_hardcases.json --providers native,hybrid --output artifacts/bench_provider_navigation_js_ts_hardcases.json
python benchmarks/render_provider_navigation_scorecard.py --inputs artifacts/bench_provider_navigation_js_ts_hardcases.json --output artifacts/bench_provider_navigation_js_ts_hardcases.md
```

| Provider | Caller hit rate | Caller precision | Test hit rate | Result |
| --- | --- | --- | --- | --- |
| `native` | 0.0 | 0.0 | 1.0 | misses imported-alias wrapper callers |
| `hybrid` | 1.0 | 1.0 | 1.0 | accepted JS/TS hardcase win |

### Rust provider-mode hardcase navigation (`run_provider_navigation_bakeoff.py`)

Measured on the accepted Rust use/re-export alias-wrapper hardcase pack:

```bash
python benchmarks/run_provider_navigation_bakeoff.py --scenarios benchmarks/external_eval/rust_provider_hardcases.json --providers native,hybrid --output artifacts/bench_provider_navigation_rust_hardcases.json
python benchmarks/render_provider_navigation_scorecard.py --inputs artifacts/bench_provider_navigation_rust_hardcases.json --output artifacts/bench_provider_navigation_rust_hardcases.md
```

| Provider | Caller hit rate | Caller precision | Test hit rate | Result |
| --- | --- | --- | --- | --- |
| `native` | 0.0 | 0.0 | 1.0 | misses use-chain and re-export alias wrapper callers |
| `hybrid` | 1.0 | 1.0 | 1.0 | accepted Rust hardcase win |

This is intentionally a narrow proof surface. It does not override the broader provider decision on the larger `click` planning pack, where provider-backed modes are still slower and not yet better enough to become the default.

### Gemini baseline-vs-enhanced patch A/B (`run_gemini_skill_ab.py`)

Use the Gemini A/B harness when you want the same task run twice against the same repo copy shape:

```bash
python benchmarks/run_gemini_skill_ab.py \
  --input artifacts/patch_eval_demo/real_patch_driver.json \
  --scenarios benchmarks/patch_eval/real_patch_bakeoff_scenarios.json \
  --output artifacts/patch_eval_demo/gemini_skill_ab_limit12_bakeoff.json \
  --timeout-seconds 60 \
  --resume

python benchmarks/render_patch_scorecard.py \
  --inputs artifacts/patch_eval_demo/gemini_skill_ab_limit12_bakeoff.json \
  --output artifacts/patch_eval_demo/gemini_skill_ab_limit12_scorecard.md
```

When `--scenarios` is provided, the output artifact keeps the raw A/B `records` and also includes scored patch-bakeoff `rows`, aggregate `summary`, and per-system score summaries for direct comparison against other patch benchmark artifacts.

The current broader Gemini A/B artifact is `artifacts/patch_eval_demo/gemini_skill_ab_limit12_bakeoff.json`, with companion scorecard `artifacts/patch_eval_demo/gemini_skill_ab_limit12_scorecard.md`. On the accepted 12-scenario hard pack, both `gemini-baseline` and `gemini-enhanced` remain at `0.0 / 0.0`.

### ripgrep vs tensor-grep (`run_benchmarks.py`)

| Scenario | ripgrep | tensor-grep | Result |
| --- | --- | --- | --- |
| Simple String Match | 0.265s | 0.251s | Parity PASS / comparator drift |
| Case-Insensitive Match | 0.284s | 0.302s | Parity PASS / `tg` regression gate FAIL |
| Regex Match | 0.284s | 0.315s | Parity PASS / comparator drift |
| Invert Match | 0.319s | 0.402s | Parity PASS / comparator drift |
| Count Matches | 0.173s | 0.185s | Parity PASS / comparator drift |
| Context Lines (`-C2`) | 0.369s | 0.436s | Parity PASS / comparator drift |
| Max Count (`-m 5`) | 0.124s | 0.142s | Parity PASS / comparator drift |
| File Glob Filtering | 0.200s | 0.221s | Parity PASS / comparator drift |
| Word Boundary | 0.248s | 0.236s | Parity PASS / comparator drift |
| Fixed Strings (`-F`) | 0.217s | 0.217s | Parity PASS / comparator drift |

The current accepted cold-path read is narrower than "tensor-grep beats ripgrep." `rg` remains the
baseline for generic cold text search on the current release line. The latest default explicit
native `tg search` rerun preserves parity but does not pass the frozen Windows regression gate:
`benchmarks/check_regression.py --baseline auto --current artifacts/bench_run_benchmarks.json`
reports comparator drift and an 8.93% `tg` regression on the case-insensitive row. Treat this as
correctness evidence, not a new speed baseline. Native CPU, AST, rewrite, repeated-query, and
harness workflows are the stronger differentiated surfaces.

### Host-local CLI tool comparison (`run_tool_comparison_benchmarks.py`)

The comparison below mirrors the useful part of the `ripgrep` benchmark README style: exact
commands, host-local medians, and an explicit caveat that a single benchmark is never enough. This
table is informational, not release-gated. It is useful for explaining workload classes on the
current host.

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

Current host-local read:

- `rg` still owns the generic cold standard-corpus row
- default `tg search` and `tg search --cpu` were effectively tied in this host-local run
- `tg search` is effectively tied with `rg` on the 200MB row, but that is an informational host-local row, not a general cold-search win
- `ag`, `ack`, `ugrep`, and GNU `grep` are not installed on this host, so they are intentionally absent from the accepted local comparator pack

### Historical cold-path startup refresh

| Scenario | Baseline `tg` | Refresh `tg` | Delta |
| --- | --- | --- | --- |
| Count Matches | 0.691659s | 0.573465s | -17.09% |
| Max Count (`-m 5`) | 0.720478s | 0.386605s | -46.34% |
| File Glob Filtering | 0.830538s | 0.794537s | -4.33% |
| Word Boundary | 0.971472s | 0.835348s | -14.01% |

This is an accepted startup reduction, not a new “beats `rg`” claim. The next two explicit targets
remain the positional early-rg extension for `-m/-w/--glob`, then the remaining default `-c`
count-path overhead.

The first accepted positional follow-up is narrower than a new global launcher recommendation.
Using the clean `origin/main` benchmark script plus the same current binary as the baseline lane,
`artifacts/bench_run_benchmarks_positional_m_baseline_lane.json` versus
`artifacts/bench_run_benchmarks_positional_m_candidate.json` shows that enabling positional
`-m/--max-count` support improved the experimental `explicit_binary_positional_early_rg` `Max Count
Limit` row from `0.163646s` to `0.158791s` (`-2.97%`). That same batch also fixed the product
contract so positional `tg -m <n> PATTERN PATH` now preserves `max_count` through the positional
ripgrep args and native routing config instead of dropping it. The accepted read is still narrow:
this is a positional capability win with a small measured benefit on the experimental lane, not a
new accepted global cold-path mode.

The accepted `--max-count=N` follow-up is a parser/routing parity fix on the existing default
`tg search` max-count lane, not a renewed front-door widening experiment. The local
`artifacts/bench_run_benchmarks.json` gate passed with all parity rows clean and no tensor-grep
regression detected; the measured `Max Count Limit` row was `rg 0.097s` versus `tg 0.132s`.
That keeps the product contract honest: `-m N`, `--max-count N`, and `--max-count=N` now reach
the same max-count passthrough behavior, while the earlier broad count/max-count widening remains
rejected.

The next positional follow-up was measured and rejected rather than left as a placeholder.
`artifacts/bench_run_benchmarks_positional_glob_baseline_lane.json` versus
`artifacts/bench_run_benchmarks_positional_glob_candidate.json` shows that widening the
experimental `explicit_binary_positional_early_rg` lane to accept positional `--glob` moved `File
Glob Filtering` from `0.149999s` with parity `PASS` to `0.285383s` with parity `FAIL`. The failure
mode is product-significant rather than cosmetic: the candidate positional `tg --glob=*.log
PATTERN PATH` path returned zero matches on the benchmark corpus while the baseline lane still
preserved the expected glob-filtered contract. The accepted read is therefore narrow and final for
this attempt: do not land positional `--glob` on this line until the routing contract is fixed and
remeasured.

### Native CPU large-file / many-file (`run_native_cpu_benchmarks.py`)

| Scenario | ripgrep | tensor-grep native CPU | Ratio | Result |
| --- | --- | --- | --- | --- |
| cold_standard_corpus | 0.240s | 0.173s | 0.722x | PASS |
| large_file_200mb | 0.283s | 0.220s | 0.775x | PASS |
| large_file_200mb_count | 0.417s | 0.072s | 0.174x | PASS |
| many_file_directory | 0.236s | 0.159s | 0.674x | PASS |

The native CPU benchmark now disables rg fallback for `tg --cpu` measurements (`TG_DISABLE_RG=1`)
so local bundled `rg` discovery cannot pollute native rows. On the current accepted artifact,
native CPU wins the large-file count probe and the standard large-file/many-file probes in this
native-only benchmark. This does not change the default cold-path claim: generic `tg search`
still keeps `rg` as the default baseline path where routing selects it.

### ast-grep vs tensor-grep AST mode (`run_ast_benchmarks.py`)

| Metric | Value |
| --- | --- |
| `ast-grep` median | 0.151s |
| `tensor-grep` median | 0.116s |
| Ratio (`tg/sg`) | 0.770x |
| Gate (`<= 1.1`) | PASS |

### AST multi-language snapshot (`run_ast_multilang_benchmarks.py`)

| Language | `tg/sg` ratio | Result |
| --- | --- | --- |
| Python | 0.722x | faster than `sg` |
| JavaScript | 0.800x | faster than `sg` |
| TypeScript | 0.726x | faster than `sg` |
| Rust | 0.715x | faster than `sg` |

### tensor-grep AST workflow startup (`run_ast_workflow_benchmarks.py`)

| Scenario | tensor-grep | Backend | Result |
| --- | --- | --- | --- |
| `tg run "def $FUNC():\n    $$$BODY" .` synthetic AST workflow | 0.028s | native | PASS |
| `python -m tensor_grep.cli.bootstrap scan --config sgconfig.yml` synthetic AST workflow | 0.267s | sidecar | PASS |
| `python -m tensor_grep.cli.bootstrap test --config sgconfig.yml` synthetic AST workflow | 0.436s | sidecar | PASS |

### AST rewrite plan/diff/apply (`run_ast_rewrite_benchmarks.py`)

| Scenario | tensor-grep | Result |
| --- | --- | --- |
| plan median | 0.361s | PASS |
| diff median | 0.410s | PASS |
| apply median | 0.464s | Gate PASS |
| `sg` apply median | 0.537s | comparison |
| total rewrites | 50,000 | completed |
| `tg/sg` apply ratio | 0.865x | faster than `sg` |

The rewrite benchmark artifact records `thresholds.max_ratio_tg_vs_sg` and fails when `tg` is more than 10% slower than `sg` on the apply phase. The current `v1.7.0` audit artifact is `artifacts/bench_ast_rewrite_post_v170_audit.json`; it passes the apply gate with `ratio_tg_vs_sg = 0.865`. The broader contract remains narrow: JSON, diff, checkpoint, audit, validation, verify, selector, and batch rewrite stay on the plan-first path.

### Editor-plane context render (`run_context_render_benchmarks.py`)

| Corpus | Cold median | Warm-session median |
| --- | ---: | ---: |
| small | 0.449s | 0.373s |
| medium | 0.691s | 0.647s |
| large | 1.808s | 1.925s |

### Blast-radius render latency (`run_blast_radius_benchmarks.py`)

| Corpus | Depth | Median |
| --- | ---: | ---: |
| medium | 1 | 0.449s |
| medium | 2 | 0.579s |
| medium | 3 | 0.456s |
| large | 1 | 1.516s |
| large | 2 | 1.446s |
| large | 3 | 1.488s |

### Harness loop (`run_harness_loop_benchmark.py`)

| Phase | Median |
| --- | --- |
| search | 0.343s |
| plan | 0.136s |
| apply | 0.313s |
| verify | 0.037s |

### Index build/query scaling (`run_index_scaling_benchmark.py`)

| Corpus size | Build | Query median | Index size | Result |
| --- | --- | --- | --- | --- |
| 1,000 files | 0.155s | 0.161s | 1,789,471 B | PASS |
| 5,000 files | 0.728s | 0.691s | 8,935,187 B | PASS |
| 10,000 files | 1.413s | 1.327s | 17,867,529 B | PASS |

### Native GPU crossover / throughput (`run_gpu_native_benchmarks.py`)

| Corpus size | `rg` median | `tg --cpu` median | `tg --gpu-device-ids 0` median | GPU/rg ratio | Result |
| --- | ---: | ---: | ---: | ---: | --- |
| 10MB | 0.104s | 0.113s | 0.409s | 3.950x slower | no crossover |
| 100MB | 0.110s | 0.116s | 1.033s | 9.416x slower | no crossover |
| 500MB | 0.126s | 0.131s | timeout | n/a | FAIL |
| 1GB | 0.144s | 0.150s | timeout | n/a | FAIL |

The current native GPU benchmark reports `passed = false`, `crossover.exists = false`, and no winning GPU rows on this Windows host. Keep explicit GPU search manual-only until the end-to-end artifact shows both correctness and a real crossover.

### Python GPU/NLP sidecar benchmark (`run_gpu_benchmarks.py`)

| Corpus size | `rg` | `tg --cpu` | GPU 0 (`RTX 4070`) | GPU 1 (`RTX 5070`) |
| --- | ---: | ---: | ---: | --- |
| 1MB | 0.353s | 0.114s | 2.923s PASS | UNSUPPORTED |
| 10MB | 0.113s | 0.117s | 3.614s PASS | UNSUPPORTED |
| 100MB | 0.111s | 0.112s | 12.306s PASS | UNSUPPORTED |
| 1GB | 0.179s | 0.183s | timeout FAIL | UNSUPPORTED |

The Python sidecar artifact reports PyTorch `2.6.0+cu124`: RTX 4070 is operational but does not beat `rg`, while RTX 5070 / `sm_120` is dependency-bound with `no kernel image`. If the host has no operational CUDA device, this artifact should contain `status: "SKIP"`, `skipped: true`, and empty timing rows.

### Repeated Fixed-String Microbenchmark

The current line also adds a REI-style trigram line index to `StringZillaBackend` for repeated fixed-string workloads. This is not intended to beat `rg` on cold one-shot scans; it is aimed at hot corpora where the same file is searched repeatedly with different literals.

Measured on the local development host with a synthetic single-file corpus:

- first indexed literal query build: **0.4061s**
- second cached literal query on the same file: **0.0090s**

That speedup is exactly the kind of workload-specific win the REI paper suggests: pay a small indexing cost once, then reuse it across repeated searches.

### Repeated Regex Prefilter Microbenchmark

The current line also adds a safe literal-core prefilter to `CPUBackend` for repeated regex workloads that fall back to Python `re`. This does not try to solve general regex indexing. It only activates when a conservative parser can prove the regex contains a required literal fragment and when context/invert semantics are not in play. The prefilter cache now persists across backend instances and fresh CLI invocations.

Measured on the local development host with a synthetic single-file corpus and forced Python fallback:

- first indexed regex query: **0.6136s**
- second cached regex query on the same file: **0.2571s**

This is a narrower win than direct `rg` passthrough, but it matters in the exact cases where `tg` cannot stay on the native `rg`/Rust path and would otherwise rescan every line with Python `re`.

### Scripted Hot-Query Snapshot (`run_hot_query_benchmarks.py`)

The scripted hot-query benchmark now measures both the fixed-string and regex-prefilter rows via fresh subprocess probes and writes a JSON artifact at `artifacts/bench_hot_query_benchmarks.json`. On the current local Windows host, the refreshed rerun measured:

| Scenario | First | Second | Result |
| --- | --- | --- | --- |
| repeated_fixed_string | 0.5671s | 0.1470s | cache win |
| repeated_regex_prefilter | 0.5476s | 0.1662s | cache win |

This is a more honest benchmark than the older mixed-process snapshot because both rows now include fresh-process overhead. The literal path is still the strongest repeated-query line, but the claim is narrower than "warm queries are free": on this host the cached second literal query is about **74.1%** faster than the first, and the cached second regex-prefilter query is about **69.7%** faster than the first.

Operational note: the fixed-string row depends on the benchmark extras (`stringzilla`). The CI `benchmark-regression` job still installs `.[bench,dev]` and runs `run_hot_query_benchmarks.py`, while local one-off runs can now use the smaller `.[bench]` contract directly (`uv pip install -e ".[bench]"` or `uv run --extra bench python benchmarks/run_hot_query_benchmarks.py`). When that dependency is absent, the benchmark records an explicit `SKIP` row with an install hint instead of crashing.

For the cold-path roadmap, the next targets stay narrow and evidence-first:

- keep max-count work limited to parser/routing parity or a cleaner benchmark design, not broad count/max-count front-door widening
- keep word-boundary work limited to the positional early-rg attribution lane unless a future artifact proves a default-path win
- investigate the remaining raw-`rg` deltas per row without retrying the already-rejected broader default-front-door widening
- prioritize attribution before implementation: separate native-binary launcher cost, `rg` subprocess cost, and any row-specific parser/routing overhead before changing the front door again
