# Benchmarks

`tensor-grep` is designed as a routing-first search engine that keeps strict behavioral parity while selecting the best backend per query class.

## Benchmark Matrix

These scripts and artifact paths are the accepted benchmark surface for the current line.

| Surface | Script | Default artifact |
| --- | --- | --- |
| End-to-end CLI text search | `benchmarks/run_benchmarks.py` | `artifacts/bench_run_benchmarks.json` |
| Native CPU large-file / many-file search | `benchmarks/run_native_cpu_benchmarks.py` | `artifacts/bench_run_native_cpu_benchmarks.json` |
| Repeated-query / hot-cache search | `benchmarks/run_hot_query_benchmarks.py` | `artifacts/bench_hot_query_benchmarks.json` |
| AST single-query gate | `benchmarks/run_ast_benchmarks.py` | `artifacts/bench_run_ast_benchmarks.json` |
| AST multi-language search | `benchmarks/run_ast_multilang_benchmarks.py` | `artifacts/bench_ast_multilang.json` |
| AST rewrite plan/diff/apply | `benchmarks/run_ast_rewrite_benchmarks.py` | `artifacts/bench_ast_rewrite.json` |
| AST workflow startup | `benchmarks/run_ast_workflow_benchmarks.py` | `artifacts/bench_run_ast_workflow_benchmarks.json` |
| Provider-mode hardcase navigation | `benchmarks/run_provider_navigation_bakeoff.py` | `artifacts/bench_provider_navigation_click_hardcases.json` |
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

- Do not update benchmark docs or claims until the relevant artifact has been rerun on the accepted line.
- Compare against the current accepted baseline, not memory.
- Reject wins that only appear in microprofiles if end-to-end artifacts regress.
- Keep backend labels explicit in artifacts so routing claims are auditable.
- Freeze artifact naming once a suite becomes part of release or contract governance.

## Latest Scripted Benchmark Snapshot (2026-03-18)

The numbers below are from local benchmark artifacts generated by:

```bash
uv sync --extra dev --extra ast
uv run python benchmarks/run_benchmarks.py
uv run python benchmarks/run_native_cpu_benchmarks.py
uv run python benchmarks/run_ast_benchmarks.py
uv run python benchmarks/run_ast_workflow_benchmarks.py
uv run python benchmarks/run_ast_rewrite_benchmarks.py
uv run python benchmarks/run_harness_loop_benchmark.py
uv run python benchmarks/run_index_scaling_benchmark.py
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
- On this host, the current Windows local CLI run passed `benchmarks/check_regression.py --baseline auto` against `benchmarks/baselines/run_benchmarks.windows.json` after restoring `rg` as the default cold-path backend for generic text search.
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
- the native Rust AST/rewrite path is the relevant comparison surface against `ast-grep`
- `Semgrep` remains the stronger policy/security ecosystem benchmark
- `Zoekt` remains the indexed repeated-query/search-at-scale benchmark

Top-level claims should stay aligned with those workload classes instead of collapsing them into one generic “better search tool” statement.

### Native control-plane rewrite v2 (`run_benchmarks.py`)

Measured on the `explicit_binary default front door` path after promoting the fastest supported `tg search` subset into the real default front door:

- artifact: `artifacts/bench_run_benchmarks_explicit_binary_default_frontdoor_uv.json`
- mean `tg_time_s`: `0.261513`
- median `tg_time_s`: `0.247376`

This improves the older `explicit_binary` line (`0.282347` / `0.271463`), which means the default front door change was real, not benchmark noise. It is still not an accepted cold-path win: `check_regression.py --baseline auto` still reports regressions on 5 of 10 scenarios against the accepted Windows baseline.

The next widening probe is explicitly rejected and preserved as history rather than code. `artifacts/bench_run_benchmarks_explicit_binary_default_frontdoor_v2_uv.json` broadened the default front door to accept the already-supported `--glob`, `-w`, and `-F` subset. That preserved parity, but it made the default `explicit_binary` line slower than the prior default-front-door artifact and still failed the frozen Windows baseline on 5 scenarios (`Case-Insensitive`, `Regex`, `File Glob Filtering`, `Word Boundary`, and `Fixed Strings`). The accepted read is narrower than “more native is always better”: widening the default front door to more ripgrep-equivalent flags was not the next win for this line.

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
| Simple String Match | 0.222s | 0.234s | Regression check PASS |
| Case-Insensitive Match | 0.235s | 0.232s | Regression check PASS |
| Regex Match | 0.234s | 0.245s | Regression check PASS |
| Invert Match | 0.334s | 0.373s | Regression check PASS |
| Count Matches | 0.163s | 0.199s | Regression check PASS |
| Context Lines (`-C2`) | 0.421s | 0.473s | Regression check PASS |
| Max Count (`-m 5`) | 0.114s | 0.132s | Regression check PASS |
| File Glob Filtering | 0.202s | 0.201s | Regression check PASS |
| Word Boundary | 0.269s | 0.228s | Regression check PASS |
| Fixed Strings (`-F`) | 0.206s | 0.219s | Regression check PASS |

The next accepted cold-path step is narrower than another front-door widening. A same-host
back-to-back startup refresh at
`artifacts/bench_run_benchmarks_passthrough_startup_refresh.json` kept the default `explicit_binary`
launcher mode but cached ripgrep binary resolution inside the Rust passthrough layer. Relative to
the immediately preceding local baseline artifact, that refresh improved the worst small-search
rows by:

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
new accepted global cold-path mode. The next two targets therefore tighten to positional `--glob`
first, then the separate default `-c` count-path overhead.

### Native CPU large-file / many-file (`run_native_cpu_benchmarks.py`)

| Scenario | ripgrep | tensor-grep native CPU | Ratio | Result |
| --- | --- | --- | --- | --- |
| cold_standard_corpus | 0.201s | 0.131s | 0.654x | PASS |
| large_file_200mb | 0.231s | 0.117s | 0.509x | PASS |
| large_file_200mb_count | 0.239s | 0.058s | 0.242x | PASS |
| many_file_directory | 0.210s | 0.055s | 0.264x | PASS |

### ast-grep vs tensor-grep AST mode (`run_ast_benchmarks.py`)

| Metric | Value |
| --- | --- |
| `ast-grep` median | 0.209s |
| `tensor-grep` median | 0.177s |
| Ratio (`tg/sg`) | 0.846x |
| Gate (`<= 1.1`) | PASS |

### AST multi-language snapshot (`run_ast_multilang_benchmarks.py`)

| Language | `tg/sg` ratio | Result |
| --- | --- | --- |
| Python | 0.850x | PASS |
| JavaScript | 0.795x | faster than `sg` |
| TypeScript | 0.743x | faster than `sg` |
| Rust | 0.852x | faster than `sg` |

### tensor-grep AST workflow startup (`run_ast_workflow_benchmarks.py`)

| Scenario | tensor-grep | Backend | Result |
| --- | --- | --- | --- |
| `tg run "def $FUNC():\n    $$$BODY" .` synthetic AST workflow | 0.051s | native | PASS |
| `python -m tensor_grep.cli.bootstrap scan --config sgconfig.yml` synthetic AST workflow | 0.409s | sidecar | PASS |
| `python -m tensor_grep.cli.bootstrap test --config sgconfig.yml` synthetic AST workflow | 0.481s | sidecar | PASS |

### AST rewrite plan/diff/apply (`run_ast_rewrite_benchmarks.py`)

| Scenario | tensor-grep | Result |
| --- | --- | --- |
| plan median (1K files) | 0.456s | PASS |
| diff median (1K files) | 0.681s | PASS |
| apply median (1K files) | 0.517s | Gate PASS |
| `sg` apply median (1K files) | 0.681s | comparison |
| `tg/sg` apply ratio (1K files) | 0.760x | faster than `sg` |
| plan median (5K files) | 1.236s | PASS |
| diff median (5K files) | 1.450s | PASS |
| apply median (5K files) | 4.016s | Gate PASS |
| `sg` apply median (5K files) | 5.138s | comparison |
| `tg/sg` apply ratio (5K files) | 0.782x | faster than `sg` |

The rewrite benchmark artifact now records `thresholds.max_ratio_tg_vs_sg` and fails when `tg` is more than 10% slower than `sg` on the apply phase.

### Harness loop (`run_harness_loop_benchmark.py`)

| Phase | Median |
| --- | --- |
| search | 0.119s |
| plan | 0.136s |
| apply | 0.510s |
| verify | 0.043s |

### Index build/query scaling (`run_index_scaling_benchmark.py`)

| Corpus size | Build | Query median | Index size | Result |
| --- | --- | --- | --- | --- |
| 1,000 files | 0.557s | 0.547s | 1,789,471 B | PASS |
| 5,000 files | 1.084s | 0.927s | 8,935,187 B | PASS |
| 10,000 files | 1.994s | 1.755s | 17,867,529 B | PASS |

### Native GPU crossover / throughput (`run_gpu_native_benchmarks.py`)

| Corpus size | `rg` median | `tg --cpu` median | `tg --gpu-device-ids 0` median | GPU/rg ratio | Result |
| --- | ---: | ---: | ---: | ---: | --- |
| 10MB | 0.125s | 0.317s | 0.869s | 6.933x slower | no crossover |
| 100MB | 0.120s | 0.300s | 1.004s | 8.368x slower | no crossover |
| 500MB | 0.202s | 0.364s | 1.384s | 6.843x slower | no crossover |
| 1GB | 0.202s | 0.455s | 2.045s | 10.122x slower | no crossover |

The current native GPU benchmark reports `gpu_auto_recommendation.should_add_flag = false` and `crossover.exists = false` on this Windows host.

### GPU/NLP Microbenchmark (`run_gpu_benchmarks.py`)

| Backend | Time | Result |
| --- | --- | --- |
| AST backend | 0.114s | 4 matches |
| cyBERT backend | 0.182s | 2 classes |
| Torch backend | 0.333s | 2,000 matches |

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

The scripted hot-query benchmark now measures both the fixed-string and regex-prefilter rows via fresh subprocess probes and writes a JSON artifact at `artifacts/bench_hot_query_benchmarks.json`. On the current local Windows host, the refreshed accepted rerun at `artifacts/bench_hot_query_benchmarks_post_bench_extra_refresh.json` measured:

| Scenario | First | Second | Result |
| --- | --- | --- | --- |
| repeated_fixed_string | 0.6271s | 0.2164s | cache win |
| repeated_regex_prefilter | 0.8776s | 0.2263s | cache win |

This is a more honest benchmark than the older mixed-process snapshot because both rows now include fresh-process overhead. The literal path is still the strongest repeated-query line, but the claim is narrower than “warm queries are free”: on this host the cached second literal query is about **66.6%** faster than the first, and the cached second regex-prefilter query is about **72.9%** faster than the first.

Operational note: the fixed-string row depends on the benchmark extras (`stringzilla`). The CI `benchmark-regression` job still installs `.[bench,dev]` and runs `run_hot_query_benchmarks.py`, while local one-off runs can now use the smaller `.[bench]` contract directly (`uv pip install -e ".[bench]"` or `uv run --extra bench python benchmarks/run_hot_query_benchmarks.py`). When that dependency is absent, the benchmark records an explicit `SKIP` row with an install hint instead of crashing.

For the cold-path roadmap, the next two targets stay narrow and evidence-first:

- first, reduce `--max-count` startup overhead without reopening the broader launcher rewrite
- second, investigate the remaining `Case-Insensitive`, `Regex`, `File Glob Filtering`, and `Word Boundary` cold-path gaps without retrying the already-rejected broader default-front-door widening
