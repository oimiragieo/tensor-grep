# Benchmarks

`tensor-grep` is designed as a routing-first search engine that keeps strict behavioral parity while selecting the best backend per query class.

## Benchmark Matrix

These scripts and artifact paths are the accepted benchmark surface for the current line.

| Surface | Script | Default artifact |
| --- | --- | --- |
| End-to-end CLI text search | `benchmarks/run_benchmarks.py` | `artifacts/bench_run_benchmarks.json` |
| Cold-path startup/control-plane attribution | `benchmarks/run_cold_path_attribution.py` | `artifacts/bench_cold_path_attribution.json` |
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
| Agent capsule + edit loop workflow | `benchmarks/run_agent_workflow_benchmarks.py` | `artifacts/bench_agent_workflow.json` |
| Agent end-to-end success harness | `benchmarks/run_agent_success_harness.py` | `artifacts/bench_agent_success_harness.json` |
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

For `run_benchmarks.py`, the environment block should also record `tg_launcher_mode` and `tg_launcher_command_kind` so cold-path comparisons stay tied to both the configured entrypoint experiment and the concrete command kind being timed. This prevents native-exe, `.cmd` shim, `uv`, or Python-module overhead from being combined into one search-speed claim. Benchmark artifacts should also record `tg_binary_kind`, `tg_binary_version`, `tg_binary_expected_version`, and `tg_binary_version_status` so stale in-tree native binaries are visible. A top-level `warnings` array is required when the timed `tg` entrypoint is a shim/interpreter route. A stale in-tree native tg binary blocks claim-quality benchmark scripts by default; pass `--allow-claim-unsafe-launcher` only for exploratory timing.

For `run_cold_path_attribution.py`, every row should record the requested `launcher_mode`, `resolved_launcher_mode`, and `tg_launcher_command_kind`, plus row-local `warnings` when the timed command includes a shim/interpreter route. The top-level `environment.tg_launcher_command_kinds` map should summarize the concrete command kind for each resolved launcher mode, and the top-level `warnings` array should aggregate binary and launcher warnings. Stale in-tree native binaries block claim-quality cold-path attribution by default; pass `--allow-claim-unsafe-launcher` only when the output is explicitly exploratory.

For `run_repo_retrieval_benchmarks.py`, the metrics block should expose retrieval-quality and context-efficiency keys explicitly:

- `recall_at_k`
- `precision_at_k`
- `mrr_at_k`
- `ndcg_at_k`
- `file_f1`
- `line_f1`
- `p50_latency_ms`
- `token_budget_mean`

For `run_agent_workflow_benchmarks.py`, the artifact is the workflow measurement surface, not a raw grep-speed comparison. It must include the literal positioning string `agent-native workflow benchmark; not a cold exact-text speed claim`, the top-level `workflow_surfaces` list, and separate `agent_capsule` and `edit_loop` sections. The capsule section should report confidence, alternatives, validation alignment, snippets, rollback, and edit order signals; the edit-loop section should report search/plan/apply/verify medians and pass/fail state.

For `run_agent_success_harness.py`, the artifact is an end-to-end agent success proof, not a raw grep-speed comparison. It must include the literal positioning string `agent-native end-to-end success harness; not a raw search speed claim`, the top-level `workflow_surfaces` list, top-level `warnings`, `environment.tg_binary_version_status`, and scenario rows that cover query intent, rendered context, edit seed, apply, verify, and rollback. Like the raw benchmark scripts, it refuses stale in-tree native `tg` binaries by default unless `--allow-claim-unsafe-launcher` explicitly marks the run as exploratory.

## Agent Workflow Benchmark

`benchmarks/run_agent_workflow_benchmarks.py` measures agent capsule routing plus safe edit-loop execution. It intentionally measures an agent-native workflow benchmark; not a cold exact-text speed claim.

The default artifact is `artifacts/bench_agent_workflow.json` and exposes two surfaces:

- `agent_capsule`: runs ambiguous and explicit invoice-edit tasks through `tg agent --json`, then records confidence, alternatives, validation alignment, snippets, rollback, and edit order contract metrics.
- `edit_loop`: reuses the AST search -> rewrite plan -> apply -> verify harness loop and records phase medians for `search_s`, `plan_s`, `apply_s`, and `verify_s`.

Use this artifact when evaluating improvements to confidence honesty, alternative-target surfacing, validation-command filtering, rollback visibility, edit-order guidance, or whole-loop edit latency. Do not use it to claim that `tg` beats `rg` for cold exact-text search.

`benchmarks/run_agent_success_harness.py` is the end-to-end workflow harness. It runs a bounded scenario from query intent through `tg agent`, `context-render`, `edit-plan`, checkpoint creation, rewrite apply, verification, and checkpoint rollback. The default artifact is `artifacts/bench_agent_success_harness.json` and exposes these `workflow_surfaces`: `intent`, `context`, `edit_seed`, `apply`, `verify`, and `rollback`. It now applies the same stale in-tree binary refusal used by claim-quality benchmark scripts, with `--allow-claim-unsafe-launcher` reserved for exploratory runs. Use it to prove an agent can identify the target, receive context, seed a safe edit, mutate only under checkpoint, verify the mutation, and restore the corpus. It is not a raw search speed claim.

## Acceptance Rules

- **Control-plane changes require artifacts:** If a patch changes launcher routing, frontend dispatch, or output formatting, it MUST include updated benchmark artifacts (e.g., `artifacts/bench_run_benchmarks.json`).
- **Regression policy:** If a patch is correct but regresses accepted benchmark lines, it must be either rejected or explicitly justified in the benchmark result and change description.
- Do not update benchmark docs or claims until the relevant artifact has been rerun on the accepted line.
- Compare against the current accepted baseline, not memory.
- Reject wins that only appear in microprofiles if end-to-end artifacts regress.
- Keep backend labels explicit in artifacts so routing claims are auditable.
- Freeze artifact naming once a suite becomes part of release or contract governance.


## Running a benchmark

Install the dependencies required by the selected suite:

```bash
uv sync --extra dev --extra ast
uv sync --extra dev --extra bench --extra nlp
uv run python benchmarks/run_benchmarks.py --output artifacts/bench_run_benchmarks.json
uv run python benchmarks/run_tool_comparison_benchmarks.py --output artifacts/bench_tool_comparison.json
```

Run resource-intensive suites on a dedicated benchmark machine. Record the source
revision, executable version, dependency versions, corpus generator and seed,
flags, sample count, warmup policy, and whether the cache is cold or reused.
Retain raw samples alongside summaries. A result on one machine is not a general
speed guarantee for another machine or workload.

AST search uses `ast-grep` as its structural comparator. Report
`max_ratio_tg_vs_sg` and the configured acceptance threshold (Gate (`<= 1.1`))
separately from correctness. See [AST benchmarks](benchmarks_ast.md).

GPU search requires separate route, correctness, and speed evidence. The fair
many-pattern comparator is one `rg -F -e ... -e ...` invocation, rather than a
loop of independent invocations. Sidecar or CPU fallback rows do not establish
native CUDA acceleration. Artifacts must distinguish `native_gpu` execution from a sidecar, expose
`not_gpu_proof_reason` for unsupported routes, and require
`public_managed_promotion_ready = true` for public managed-release promotion.
See [GPU benchmark requirements](gpu_crossover.md).

NLP benchmarks may require cyBERT model files and optional Triton dependencies.
Record unavailable models or runtimes as unsupported; do not silently substitute
a different workload or treat a skipped row as a successful measurement.
