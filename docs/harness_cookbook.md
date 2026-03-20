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
- `"test_matching": "filename+import-heuristic"`

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

Resolve exact definitions:

```powershell
tg defs --symbol create_invoice --json .\src
```

Estimate likely change impact:

```powershell
tg impact --symbol create_invoice --json .
```

Find reference sites:

```powershell
tg refs --symbol create_invoice --json .
```

Find call sites plus likely impacted tests:

```powershell
tg callers --symbol create_invoice --json .
```

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
```

Reuse the cached repo map for another query:

```powershell
tg session context session-20260320071200-rewrite . --query "invoice payment" --json
```

Recommended consumer behavior:

1. open one session at the start of a multi-step edit task
2. reuse `session context` for follow-up queries instead of rebuilding repo inventory
3. keep the returned `session_id` with the task state until the edit loop finishes
4. keep honoring the same `coverage` contract as `tg map` / `tg context`

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

Verification is byte-level exact-text verification. Consumers should fail closed if `verification.mismatches` is non-empty.
Validation is command-level repo health verification. Consumers should fail closed if `validation.success` is `false`.

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
tg.exe search --ndjson ERROR .\logs
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

- `tg_repo_map`
- `tg_context_pack`
- `tg_symbol_defs`
- `tg_symbol_impact`
- `tg_symbol_refs`
- `tg_symbol_callers`
- `tg_session_open`
- `tg_session_list`
- `tg_session_show`
- `tg_session_context`
- `tg_checkpoint_create`
- `tg_checkpoint_list`
- `tg_checkpoint_undo`
- `tg_index_search`
- `tg_rewrite_plan`
- `tg_rewrite_apply`
- `tg_rewrite_diff`

Example flow:

1. call `tg_index_search("ERROR", path=".")`
2. call `tg_rewrite_plan(...)`
3. call `tg_rewrite_diff(...)`
4. call `tg_checkpoint_create(path=".")` if rollback is required
5. call `tg_rewrite_apply(..., verify=True, checkpoint=True)`

The MCP tool payloads mirror the CLI contract envelopes. Consumers should still inspect:

- `"routing_backend"`
- `"routing_reason"`
- `"sidecar_used"`

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
