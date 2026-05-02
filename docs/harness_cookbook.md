# Harness Cookbook

Use this guide when consuming `tensor-grep` as a search/edit substrate from another tool or agent. The goal is to stay on the public surface only:

- CLI JSON
- CLI NDJSON
- MCP tools

For field-level contracts, use [Harness API](harness_api.md). This cookbook focuses on workflow shape.

## Search JSON Flow

Use JSON when you want one complete response object.

```powershell
tg.exe search --json ERROR .\logs
```

Expected top-level fields:

- `"version"`
- `"routing_backend"`
- `"routing_reason"`
- `"sidecar_used"`
- `"query"`
- `"path"`
- `"total_matches"`
- `"matches"`

Recommended consumer behavior:

1. parse one JSON document from stdout
2. inspect `routing_backend` / `routing_reason`
3. iterate `matches[]`

## Indexed Search Flow

Use the index path when the corpus is warm and routing policy allows it.

```powershell
tg.exe search --index --json ERROR .\logs
```

Expected differences from plain search:

- `"routing_backend": "TrigramIndex"`
- `"routing_reason": "index-accelerated"`

Consumers should treat the payload shape as the same as Search JSON.

## Repo Map Flow

Use repo-map output before planning edits when the agent needs a deterministic view of files, symbols, imports, and likely related test files.

```powershell
tg.exe map --json .\src
```

Expected top-level fields:

- `"version"`
- `"routing_backend"`
- `"routing_reason"`
- `"sidecar_used"`
- `"coverage"`
- `"path"`
- `"files"`
- `"symbols"`
- `"imports"`
- `"tests"`
- `"related_paths"`

Recommended consumer behavior:

1. request the repo map before multi-file edit planning
2. choose a small set of relevant files from `files`, `symbols`, and `related_paths`
3. feed only that subset into the next search or rewrite step

Current coverage values describe the limits of this surface:

- `"language_scope": "python-js-ts-rust"`
- `"symbol_navigation": "python-ast+heuristic-js-ts-rust"`
- `"test_matching": "filename+import+graph-heuristic"`

## Context Pack Flow

Use context packs when the agent already has a task/query and wants a smaller ranked subset than the full repo map.

```powershell
tg.exe context --query "invoice payment" --json .\src
```

Expected top-level fields:

- `"version"`
- `"routing_backend"`
- `"routing_reason"`
- `"sidecar_used"`
- `"coverage"`
- `"query"`
- `"path"`
- `"files"`
- `"symbols"`
- `"imports"`
- `"tests"`
- `"related_paths"`

Recommended consumer behavior:

1. use the raw user task or issue title as the initial query
2. take the top ranked `files` and `tests` as the first edit/search context
3. use symbol `score` to decide which definitions to inspect before planning edits
4. inspect `coverage` before treating the results as cross-language semantic truth

When an agent is ready to move from discovery into editing, prefer `edit-plan` or `context-render` and read `navigation_pack.parallel_read_groups[]` first. That gives a deterministic fan-out plan: inspect the `primary` phase first, then parallelize the `related` phase, then run the `test` phase before validation. If a caller does not understand phased reads yet, `navigation_pack.follow_up_reads[]` is still the flat fallback.

Resolve exact definitions:

```powershell
tg defs --symbol create_invoice --max-repo-files 512 --json .\src
```

Fetch the exact source block:

```powershell
tg source --symbol create_invoice --max-repo-files 512 --json .\src
```

Estimate likely change impact:

```powershell
tg impact --symbol create_invoice --max-repo-files 512 --json .
```

Find reference sites:

```powershell
tg refs --symbol create_invoice --max-repo-files 512 --json .
```

Find call sites plus likely impacted tests:

```powershell
tg callers --symbol create_invoice --max-repo-files 512 --json .
```

For broad repo roots, read `scan_limit` before assuming the inventory is complete. If `no_match` is true on `defs` or `source`, treat the compact payload as a real miss and refine the symbol/query instead of scanning unrelated inventories.

## Session Reuse Flow

Use sessions when the agent will issue multiple context queries against the same repo during one edit loop.

Open a cached session:

```powershell
tg session open . --json
```

Inspect cached session metadata:

```powershell
tg session list . --json
tg session show session-20260320071200-rewrite . --json
tg session refresh session-20260320071200-rewrite . --json
```

Reuse the cached repo map for another query:

```powershell
tg session context session-20260320071200-rewrite . --query "invoice payment" --json
tg session serve session-20260320071200-rewrite . < requests.jsonl
tg session serve session-20260320071200-rewrite . --refresh-on-stale < requests.jsonl
```

Recommended consumer behavior:

1. open one session at the start of a multi-step edit task
2. reuse `session context` for follow-up queries instead of rebuilding repo inventory
3. keep the returned `session_id` with the task state until the edit loop finishes
4. run `session refresh` after accepted file mutations or use `--refresh-on-stale` for automated recovery
5. use `session serve` for repeated repo-map, context, defs, refs, callers, and impact requests
6. keep honoring the same `coverage` contract as `tg map` / `tg context`

Example `requests.jsonl`:

```json
{"command":"repo_map"}
{"command":"context","query":"invoice payment"}
{"command":"callers","symbol":"create_invoice"}
```

## End-to-End CLI Flow

Use this as the canonical low-noise CLI chain for one edit task:

1. inventory the repo
2. narrow context
3. inspect exact symbols if needed
4. plan rewrites or edits
5. apply and verify
6. score the result

Minimal command chain:

```powershell
tg.exe map --json .\src
tg.exe context --query "invoice payment" --json .\src
tg defs --symbol create_invoice --json .\src
tg callers --symbol create_invoice --json .\src
tg.exe run --lang python --rewrite "lambda $$$ARGS: $EXPR" --json "def $F($$$ARGS): return $EXPR" .\src\sample.py
tg.exe run --lang python --rewrite "lambda $$$ARGS: $EXPR" --apply --verify --checkpoint --lint-cmd "ruff check ." --test-cmd "pytest -q" --json "def $F($$$ARGS): return $EXPR" .\src\sample.py
python benchmarks/run_patch_bakeoff.py --scenarios benchmarks/patch_eval/real_patch_bakeoff_scenarios.json --predictions artifacts/patch_eval_demo/gemini_skill_ab_limit12_bakeoff.json --output artifacts/patch_eval_demo/gemini_skill_ab_limit12_bakeoff_scored.json
python benchmarks/render_patch_scorecard.py --inputs artifacts/patch_eval_demo/gemini_skill_ab_limit12_bakeoff_scored.json --output artifacts/patch_eval_demo/gemini_skill_ab_limit12_scorecard.md
```

Recommended consumer behavior:

1. treat `map` / `context` as context acquisition, not final truth
2. use `defs`, `source`, `refs`, `impact`, or `callers` only when the ranked context still leaves ambiguity
3. prefer `navigation_pack.primary_target` plus `navigation_pack.follow_up_reads[]` as the first read set for planner/executor loops
4. apply only after a plan or diff is acceptable
5. use the patch bakeoff artifact as the machine-readable final score

## End-to-End MCP Flow

Use this as the canonical MCP chain for the same task shape:

1. `tg_mcp_capabilities`
2. `tg_repo_map`
3. `tg_context_pack`
4. `tg_symbol_defs` or `tg_symbol_callers`
5. `tg_edit_plan`
6. `tg_rewrite_diff` when `native_tg.available = true`
7. `tg_rewrite_apply`
8. `tg_audit_manifest_verify`

Minimal MCP chain:

```text
tg_mcp_capabilities()
tg_repo_map(path=".")
tg_context_pack(query="invoice payment", path=".")
tg_symbol_defs(symbol="create_invoice", path=".")
tg_symbol_callers(symbol="create_invoice", path=".")
tg_edit_plan(query="invoice payment", path=".")
tg_rewrite_diff(pattern="def $F($$$ARGS): return $EXPR", replacement="lambda $$$ARGS: $EXPR", lang="python", path=".")
tg_rewrite_apply(pattern="def $F($$$ARGS): return $EXPR", replacement="lambda $$$ARGS: $EXPR", lang="python", path=".", verify=True, checkpoint=True)
tg_audit_manifest_verify(manifest_path="rewrite-audit.json")
```

Recommended consumer behavior:

1. call `tg_mcp_capabilities` before picking rewrite/index tools in PyPI or sandboxed installs
2. preserve machine-readable envelopes from each tool instead of scraping prose
3. treat `native-required` tools as unavailable unless `native_tg.available = true`
4. inspect `native_required_options` before calling `tg_rewrite_apply`; `verify`, `checkpoint`, `audit_manifest`, `audit_signing_key`, `lint_cmd`, and `test_cmd` require standalone native `tg`
5. use simple `tg_rewrite_plan` / `tg_rewrite_apply` as the embedded-safe fallback when native `tg` is unavailable
6. treat `tg_edit_plan` as the plan contract and `tg_rewrite_apply` as the patch-attempt plus validation contract
7. use `tg_audit_manifest_verify` when the workflow needs trust or replay validation
8. if final outcome needs benchmark-style scoring, hand the produced patch artifact to `run_patch_bakeoff.py`

## Rewrite Planning Flow

Plan first. Do not mutate files until the plan is accepted.

```powershell
tg.exe run --lang python --rewrite "lambda $$$ARGS: $EXPR" --json "def $F($$$ARGS): return $EXPR" .\src\sample.py
```

Important plan fields:

- `"routing_backend"`
- `"routing_reason"`
- `"sidecar_used"`
- `"total_edits"`
- `"edits"`

Each `edits[]` object includes stable provenance:

- `id`
- `file`
- `line`
- `byte_range`
- `original_text`
- `replacement_text`
- `metavar_env`

## Diff Review Flow

Review diffs before apply when a human or agent policy requires it.

```powershell
tg.exe run --lang python --rewrite "lambda $$$ARGS: $EXPR" --diff "def $F($$$ARGS): return $EXPR" .\src\sample.py
```

This emits a unified diff, not JSON. Expect:

- `---`
- `+++`
- `@@`

Use this when your agent needs a review artifact before mutation.

If only part of the plan is acceptable, pass stable edit IDs back into the next command:

```powershell
tg.exe run --lang python --rewrite "lambda $$$ARGS: $EXPR" --apply-edit-ids "e0000:sample.py:0-27" --diff "def $F($$$ARGS): return $EXPR" .\src\sample.py
```

## Apply + Verify Flow

Apply only after planning succeeds. Prefer `--verify` for harness use.

```powershell
tg.exe run --lang python --rewrite "lambda $$$ARGS: $EXPR" --apply --verify --checkpoint --lint-cmd "ruff check ." --test-cmd "pytest -q" --json "def $F($$$ARGS): return $EXPR" .\src\sample.py
```

Selection flags are supported here too:

- `--apply-edit-ids <id1,id2,...>`
- `--reject-edit-ids <id1,id2,...>`

Use them when the agent or reviewer accepts only part of the proposed edit set.

The response is a single JSON document with:

- `"version"`
- `"routing_backend"`
- `"routing_reason"`
- `"sidecar_used"`
- `"checkpoint"` when pre-apply rollback capture is requested
- `"plan"`
- `"validation"` when post-apply commands are requested
- `"verification"`

## Multi-Attempt Replay Flow

Use this flow when a task may take more than one patch attempt and the controller needs a replayable, auditable chain instead of ad hoc retry notes.

Canonical machine-readable artifact:

- [`examples/attempt_ledger.json`](examples/attempt_ledger.json)
- [`examples/multi_session_attempt_ledger.json`](examples/multi_session_attempt_ledger.json) for a multi-session replay chain
- [`examples/multi_task_attempt_ledger.json`](examples/multi_task_attempt_ledger.json) for a multi-task replay chain

Producer command:

```powershell
python benchmarks/build_attempt_ledger.py --input artifacts/agent_loop/attempts.json --output artifacts/agent_loop/attempt_ledger.json
```

Integrated producer path:

```powershell
python benchmarks/run_tensor_grep_patch_driver.py --scenarios benchmarks/patch_eval/real_patch_driver_scenarios.json --output artifacts/tensor_grep_patch_driver.json --attempt-ledger-output artifacts/tensor_grep_patch_driver_attempt_ledger.json
```

The patch-driver artifact preserves `navigation_pack` alongside `edit_plan_seed`, so downstream agent loops can use the compact next-read / next-edit bundle directly instead of rebuilding it from the larger planning payload.

Scored producer path:

```powershell
python benchmarks/run_patch_bakeoff.py --scenarios benchmarks/patch_eval/real_patch_bakeoff_scenarios.json --predictions artifacts/patch_eval_demo/claude_skill_ab_limit12_current_claude_md_bakeoff.json --output artifacts/patch_eval_demo/claude_skill_ab_limit12_scored.json --attempt-ledger-dir artifacts/patch_eval_demo/attempt_ledgers
```

Claude A/B producer path:

```powershell
python benchmarks/run_claude_skill_ab.py --input artifacts/tensor_grep_patch_driver.json --output artifacts/patch_eval_demo/claude_skill_ab.json --attempt-ledger-dir artifacts/patch_eval_demo/claude_ab_attempt_ledgers
```

Gemini A/B producer path:

```powershell
python benchmarks/run_gemini_skill_ab.py --input artifacts/tensor_grep_patch_driver.json --output artifacts/patch_eval_demo/gemini_skill_ab.json --attempt-ledger-dir artifacts/patch_eval_demo/gemini_ab_attempt_ledgers
```

Competitor prediction producer paths:

```powershell
python benchmarks/run_claude_patch_predictions.py --input artifacts/tensor_grep_patch_driver.json --output artifacts/patch_eval_demo/claude_patch_predictions.json --attempt-ledger-dir artifacts/patch_eval_demo/claude_prediction_attempt_ledgers
python benchmarks/run_copilot_patch_predictions.py --input artifacts/tensor_grep_patch_driver.json --output artifacts/patch_eval_demo/copilot_patch_predictions.json --attempt-ledger-dir artifacts/patch_eval_demo/copilot_prediction_attempt_ledgers
python benchmarks/run_gemini_patch_predictions.py --input artifacts/tensor_grep_patch_driver.json --output artifacts/patch_eval_demo/gemini_patch_predictions.json --attempt-ledger-dir artifacts/patch_eval_demo/gemini_prediction_attempt_ledgers
```

External agent comparison path:

```powershell
python benchmarks/build_external_agent_patch_driver_comparison.py --summary gemini=artifacts/external_validation/gemini_patch_driver_validation_summary.json --summary claude=artifacts/external_validation/claude_patch_driver_validation_summary.json --summary codex=artifacts/external_validation/codex_patch_driver_validation_summary.json --output artifacts/external_validation/external_agent_patch_driver_comparison.json
```

External agent scorecard path:

```powershell
python benchmarks/build_external_agent_patch_driver_scorecard.py --input artifacts/external_validation/external_agent_patch_driver_comparison.json --output artifacts/external_validation/external_agent_patch_driver_scorecard.json
```

The comparison artifact now also preserves `parallel_read_groups` from the live patch-driver output when available, so the scorecard can measure serial read-step reduction instead of only compactness and validation-fit.

Producer:

```powershell
python benchmarks/build_attempt_ledger.py --input artifacts/attempt_ledger_input.json --output artifacts/attempt_ledger.json
```

Recommended ledger sequence:

1. keep one `task_id` stable across retries
2. append one `attempts[]` row per materialized attempt
3. record `retry_stage` as the narrowest safe replay boundary
4. carry forward checkpoint and audit-manifest links instead of rewriting history
5. update `final_outcome` only when one attempt is accepted or the chain is terminally rejected

Minimal control flow:

1. produce `tg_edit_plan` or `tg_rewrite_diff`
2. apply with `tg_rewrite_apply` or `tg.exe run --rewrite ... --apply --verify --json`
3. record validation success or failure for that attempt
4. if retrying, append a new attempt row with `parent_attempt_id`
5. verify the trust chain with `tg_audit_manifest_verify`
6. score the accepted or terminal output with the patch bakeoff only after the attempt chain is complete

Recommended consumer behavior:

1. treat the attempt ledger as the source of truth for multi-attempt provenance
2. preserve the full replay chain instead of overwriting failed attempts
3. use `partial_retry_ledger` as the machine-readable partial retry ledger and resume from the narrowest safe stage
4. keep `audit_chain` entries stable so later replay or trust verification can re-check the exact accepted path
5. only treat an attempt as terminal when `final_outcome.status` is set or retry policy says to stop

When the controller crosses cached sessions, keep a multi-session replay chain instead of flattening the handoff into prose. Preserve both `session_id` values, record the handoff reason, and keep the accepted attempt linked back to the prior session state through the ledger.

When the controller advances from one bounded task to the next, keep a multi-task replay chain instead of treating the second task as a fresh unrelated run. Preserve the ordered `tasks[]` inventory, keep the accepted attempt for each task, and record the cross-task chain in `replay.task_chain` so later audit and replay can reconstruct why the later task depended on the earlier accepted result.

## Patch Score Flow

Use the patch bakeoff when the agent already has prediction artifacts and needs a machine-readable final score.

```powershell
python benchmarks/run_patch_bakeoff.py --scenarios benchmarks/patch_eval/real_patch_bakeoff_scenarios.json --predictions artifacts/patch_eval_demo/gemini_skill_ab_limit12_bakeoff.json --output artifacts/patch_eval_demo/gemini_skill_ab_limit12_bakeoff_scored.json
python benchmarks/render_patch_scorecard.py --inputs artifacts/patch_eval_demo/gemini_skill_ab_limit12_bakeoff_scored.json --output artifacts/patch_eval_demo/gemini_skill_ab_limit12_scorecard.md
```

Expected machine-readable fields:

- `"artifact"`
- `"suite"`
- `"summary"`
- `"rows"`
- `"missing_predictions"`

Recommended consumer behavior:

1. treat `rows[]` as the per-scenario final score surface
2. use `summary` for aggregate decisions only after `missing_predictions` is empty
3. rerun the producer with `--resume` if `"missing_predictions"` is non-empty
4. retry the producer, not the scorer, when a model row is absent
5. rerun only the scorer when the prediction artifact exists but the markdown scorecard is stale

Common failure signals and retries:

- `reason="no patch emitted"`: keep the artifact as a scored failure; do not treat it as a harness crash
- `reason` containing `timeout after`: treat it as a model/runtime failure line, not a scoring failure
- non-empty `"missing_predictions"`: the generation run is incomplete; resume the generator first
- corrupt or partial producer artifact: discard incomplete rows only if the generator supports safe resume semantics, then rerun

Verification is byte-level exact-text verification. Consumers should fail closed if `verification.mismatches` is non-empty.
Validation is command-level repo health verification. Consumers should fail closed if `validation.success` is `false`.

## Failure Mode Examples

Use these public examples when wiring retries or stop conditions into an external agent loop:

- [`examples/session_invalid_request_stale.json`](examples/session_invalid_request_stale.json): stale session request; refresh the session or enable `--refresh-on-stale`
- [`examples/patch_bakeoff_incomplete.json`](examples/patch_bakeoff_incomplete.json): incomplete producer run; resume the generator before reading `summary`
- [`examples/patch_bakeoff_no_patch.json`](examples/patch_bakeoff_no_patch.json): producer finished but emitted no usable patch; keep the scored failure and move on
- [`examples/defs_provider_disagreement.json`](examples/defs_provider_disagreement.json): provider disagreement metadata; treat provider state as diagnostic context, not proof that the native answer is wrong
- [`examples/provider_status_unavailable.json`](examples/provider_status_unavailable.json): provider unavailable snapshot; fall back to native behavior or surface the limitation to the caller
- [`examples/provider_status_unavailable.json`](examples/provider_status_unavailable.json): provider unavailable; read `provider_status.last_error`, stay on native routing, and avoid blind provider retries
- [`examples/rewrite_apply_verify_validation_failed.json`](examples/rewrite_apply_verify_validation_failed.json): patch applied and verified, but repo validation failed; rollback or repair before continuing

Recommended control-flow policy:

1. retry stale-session and missing-prediction failures
2. do not retry scored no-patch failures inside the scorer
3. fail closed on post-apply validation failure
4. treat provider disagreement as a planning-quality warning, not an automatic retry trigger
5. treat provider unavailability as transport health; prefer native answers until `provider_status.last_error` clears

## Checkpoint Flow

Use checkpoints when an agent needs an explicit rollback point before or after an edit session.

Create a checkpoint:

```powershell
tg checkpoint create . --json
```

List checkpoints:

```powershell
tg checkpoint list . --json
```

Undo a checkpoint:

```powershell
tg checkpoint undo ckpt-20260320120000-deadbeef . --json
```

Current behavior:

- inside a Git repo, `mode` is `git-worktree-snapshot`
- outside Git, `mode` is `filesystem-snapshot`
- undo restores files captured at checkpoint creation and removes paths created afterward inside the checkpoint scope

## NDJSON Streaming Flow

Use NDJSON when you want incremental consumption for large result sets.

```powershell
tg.exe search --ndjson ERROR .\src .\tests .\docs
```

Each line is a standalone JSON object with:

- `"version"`
- `"routing_backend"`
- `"routing_reason"`
- `"sidecar_used"`
- `"query"`
- `"path"`
- `"file"`
- `"line"`
- `"text"`

Read stdout line-by-line and parse each row independently.

## MCP Workflow Flow

Use MCP when the consumer speaks tool calls instead of shelling out directly.

Available workflow tools:

- `tg_mcp_capabilities`
- `tg_repo_map`
- `tg_context_pack`
- `tg_symbol_defs`
- `tg_symbol_source`
- `tg_symbol_impact`
- `tg_symbol_refs`
- `tg_symbol_callers`
- `tg_session_open`
- `tg_session_list`
- `tg_session_show`
- `tg_session_refresh`
- `tg_session_context`
- `tg_checkpoint_create`
- `tg_checkpoint_list`
- `tg_checkpoint_undo`
- `tg_index_search`
- `tg_rewrite_plan`
- `tg_rewrite_apply`
- `tg_rewrite_diff`

Example flow:

1. call `tg_mcp_capabilities()`
2. call `tg_index_search("ERROR", path=".")` only if it is `native-required` and native `tg` is available
3. call `tg_rewrite_plan(...)`
4. call `tg_rewrite_diff(...)` only when native `tg` is available
5. call `tg_checkpoint_create(path=".")` if rollback is required
6. call `tg_rewrite_apply(..., verify=True, checkpoint=True)` only when native `tg` is available; otherwise use simple embedded-safe apply without verify/checkpoint

The MCP tool payloads mirror the CLI contract envelopes. Consumers should still inspect:

- `"routing_backend"`
- `"routing_reason"`
- `"sidecar_used"`
- `"error.code" == "unavailable"` with `"native-tg-unavailable"` routing before retrying native-required tools

## Calibrate and Routing Flow

Use calibration to teach the native router where CPU and GPU cross over on the local machine.

```powershell
tg.exe calibrate
```

Expected top-level fields:

- `"version"`
- `"routing_backend"`
- `"routing_reason"`
- `"sidecar_used"`
- `"corpus_size_breakpoint_bytes"`
- `"recommendation"`
- `"measurements"`

Interpretation:

- `cpu_always`: stay on CPU for the measured hardware/workload
- `gpu_above_*`: GPU is worthwhile only above the measured breakpoint

Do not force GPU just because it exists. Follow the measured crossover unless you are running a benchmark experiment.

## Large Corpus Guidance

Use these rules:

1. plain cold search:
   - use `tg.exe search --json ...`
2. repeated text search on unchanged corpora:
   - use `tg.exe search --index --json ...`
3. large result streams:
   - use `tg.exe search --ndjson ...`
4. structural edits:
   - use plan -> diff -> apply+verify
5. GPU:
   - use only when routing or explicit calibration says it wins

For stable field semantics, see [Harness API](harness_api.md).

