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

## Apply + Verify Flow

Apply only after planning succeeds. Prefer `--verify` for harness use.

```powershell
tg.exe run --lang python --rewrite "lambda $$$ARGS: $EXPR" --apply --verify --json "def $F($$$ARGS): return $EXPR" .\src\sample.py
```

The response is a single JSON document with:

- `"version"`
- `"routing_backend"`
- `"routing_reason"`
- `"sidecar_used"`
- `"plan"`
- `"verification"`

Verification is byte-level exact-text verification. Consumers should fail closed if `verification.mismatches` is non-empty.

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

- `tg_index_search`
- `tg_rewrite_plan`
- `tg_rewrite_apply`
- `tg_rewrite_diff`

Example flow:

1. call `tg_index_search("ERROR", path=".")`
2. call `tg_rewrite_plan(...)`
3. call `tg_rewrite_diff(...)`
4. call `tg_rewrite_apply(..., verify=True)`

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
