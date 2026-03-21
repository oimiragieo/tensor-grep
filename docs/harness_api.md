# Harness API

`tg.exe` exposes a small set of machine-readable output shapes for harnesses and agents. This document describes the current v1 JSON contracts emitted by the native Rust CLI, plus the current GPU sidecar hybrid shape.

All committed examples live in [`docs/examples/`](examples/) and are valid single-document JSON files generated from real `tg.exe` commands against temporary fixtures created under `bench_data/`.

> `bench_data/*.log` is ignored by default because of the repo ignore rules, so search examples that target log files use `--no-ignore`.

## Common envelope fields

These top-level fields are shared across every JSON shape documented here.

| Field | Type | Meaning |
| --- | --- | --- |
| `version` | `integer` | Contract version. Current value: `1`. |
| `routing_backend` | `string` | Backend selected by the Rust control plane, such as `CpuBackend`, `TrigramIndex`, `AstBackend`, or `GpuSidecar`. |
| `routing_reason` | `string` | Stable reason string describing why that backend was chosen. |
| `sidecar_used` | `boolean` | `true` only when the Rust CLI delegated the request through the Python sidecar. |

## Example files

| Shape | Trigger | Example |
| --- | --- | --- |
| Search JSON | `tg.exe search --json ...` | [`examples/search.json`](examples/search.json) |
| Index search JSON | `tg.exe search --index --json ...` | [`examples/index_search.json`](examples/index_search.json) |
| Repo map JSON | `tg.exe map --json ...` | [`examples/repo_map.json`](examples/repo_map.json) |
| Context pack JSON | `tg.exe context --query ... --json ...` | [`examples/context_pack.json`](examples/context_pack.json) |
| Rewrite plan JSON | `tg.exe run --rewrite ...` | [`examples/rewrite_plan.json`](examples/rewrite_plan.json) |
| Apply + verify JSON | `tg.exe run --rewrite ... --apply --verify --json ...` | [`examples/rewrite_apply_verify.json`](examples/rewrite_apply_verify.json) |
| GPU sidecar JSON | `tg.exe search --gpu-device-ids ... --json ...` | [`examples/gpu_sidecar_search.json`](examples/gpu_sidecar_search.json) |
| Calibrate JSON | `tg.exe calibrate` | [`examples/calibrate.json`](examples/calibrate.json) |
| Search NDJSON | `tg.exe search --ndjson ...` | [`examples/search.ndjson`](examples/search.ndjson) |
| Symbol defs JSON | `tg.exe defs --symbol <name> --json ...` | [`examples/defs.json`](examples/defs.json) |
| Symbol impact JSON | `tg.exe impact --symbol <name> --json ...` | [`examples/impact.json`](examples/impact.json) |
| Symbol refs JSON | `tg.exe refs --symbol <name> --json ...` | [`examples/refs.json`](examples/refs.json) |
| Symbol callers JSON | `tg.exe callers --symbol <name> --json ...` | [`examples/callers.json`](examples/callers.json) |
| Session open JSON | `tg.exe session open ... --json` | [`examples/session_open.json`](examples/session_open.json) |
| Session context JSON | `tg.exe session context <id> --query ... --json` | [`examples/session_context.json`](examples/session_context.json) |
| MCP rewrite diff JSON | `tg_rewrite_diff(...)` | [`examples/mcp_rewrite_diff.json`](examples/mcp_rewrite_diff.json) |

## Search JSON

Emitted by native text search when `--json` is set.

Example: [`examples/search.json`](examples/search.json)

| Field | Type | Notes |
| --- | --- | --- |
| `version` | `integer` | Always `1` for the current contract. |
| `routing_backend` | `string` | `CpuBackend` for the committed example. |
| `routing_reason` | `string` | `cpu-native` for the committed example. |
| `sidecar_used` | `boolean` | `false` for native CPU search. |
| `query` | `string` | Search pattern exactly as passed on the command line. |
| `path` | `string` | Search root passed to the command. |
| `total_matches` | `integer` | Number of materialized matches in `matches`. |
| `matches` | `array<object>` | Match rows. |

Each `matches[]` object has:

| Field | Type | Notes |
| --- | --- | --- |
| `file` | `string` | Absolute path to the matching file. |
| `line` | `integer` | 1-based line number. |
| `text` | `string` | Full matching line text. |

## Index Search JSON

Emitted by native trigram index search with `tg.exe search --index --json ...`.

Example: [`examples/index_search.json`](examples/index_search.json)

The shape matches Search JSON exactly; only the routing envelope changes.

| Field | Type | Notes |
| --- | --- | --- |
| `routing_backend` | `string` | `TrigramIndex` in the example. |
| `routing_reason` | `string` | `index-accelerated` in the example. |
| `sidecar_used` | `boolean` | Always `false` for the native index path. |
| `query` | `string` | Original literal/regex query. |
| `path` | `string` | Indexed search root. |
| `total_matches` | `integer` | Number of returned index matches. |
| `matches[].file` | `string` | Absolute file path. |
| `matches[].line` | `integer` | 1-based line number. |
| `matches[].text` | `string` | Matching line text. |

## Repo Map JSON

Emitted by `tg.exe map --json ...`.

Example: [`examples/repo_map.json`](examples/repo_map.json)

Use this shape when an agent needs a deterministic repository inventory before choosing edits.

| Field | Type | Notes |
| --- | --- | --- |
| `version` | `integer` | Contract version. |
| `routing_backend` | `string` | `RepoMap`. |
| `routing_reason` | `string` | `repo-map`. |
| `sidecar_used` | `boolean` | Always `false`. |
| `coverage` | `object` | Self-description for the current inventory/navigation coverage. |
| `path` | `string` | Absolute root path inventoried. |
| `files` | `array<string>` | Non-test files included in the inventory. |
| `symbols` | `array<object>` | Deterministic symbol inventory. |
| `imports` | `array<object>` | Per-file import inventory. |
| `tests` | `array<string>` | Test files associated with the inventory root. |
| `related_paths` | `array<string>` | Stable union of relevant source and test paths. |

Each `symbols[]` object has:

| Field | Type | Notes |
| --- | --- | --- |
| `name` | `string` | Symbol name. |
| `kind` | `string` | Current values include `class` and `function`. |
| `file` | `string` | Absolute file path containing the symbol. |
| `line` | `integer` | 1-based line number. |

Each `imports[]` object has:

| Field | Type | Notes |
| --- | --- | --- |
| `file` | `string` | Absolute file path. |
| `imports` | `array<string>` | Imported module names extracted from the file. |

Current `coverage` values:

| Field | Type | Notes |
| --- | --- | --- |
| `language_scope` | `string` | Currently `python-js-ts-rust`. |
| `symbol_navigation` | `string` | Currently `python-ast+heuristic-js-ts-rust`. |
| `test_matching` | `string` | Currently `filename+import-heuristic`. |

## Context Pack JSON

Emitted by `tg.exe context --query <text> --json ...`.

Example: [`examples/context_pack.json`](examples/context_pack.json)

Use this shape when an agent needs a query-driven subset of the repository map before choosing edits.

| Field | Type | Notes |
| --- | --- | --- |
| `version` | `integer` | Contract version. |
| `routing_backend` | `string` | `RepoMap`. |
| `routing_reason` | `string` | `context-pack`. |
| `sidecar_used` | `boolean` | Always `false`. |
| `coverage` | `object` | Same coverage contract as Repo Map JSON. |
| `query` | `string` | Query text used for ranking. |
| `path` | `string` | Absolute root path inventoried. |
| `files` | `array<string>` | Ranked source files related to the query. |
| `symbols` | `array<object>` | Ranked symbols related to the query. |
| `imports` | `array<object>` | Ranked import rows related to the query. |
| `tests` | `array<string>` | Ranked test files related to the query. |
| `related_paths` | `array<string>` | Stable merged order of the highest-value source and test paths. |

Each ranked `symbols[]` object extends the Repo Map JSON symbol shape with:

| Field | Type | Notes |
| --- | --- | --- |
| `score` | `integer` | Deterministic query relevance score. |

Each ranked `imports[]` object extends the Repo Map JSON import shape with:

| Field | Type | Notes |
| --- | --- | --- |
| `score` | `integer` | Deterministic query relevance score. |

## Rewrite Plan JSON

Emitted by `tg.exe run --rewrite <replacement> <pattern> <path>` when `--diff` and `--apply` are not set.

Optional edit selection flags:

- `--apply-edit-ids <id1,id2,...>` keeps only the listed planned edit IDs
- `--reject-edit-ids <id1,id2,...>` drops the listed planned edit IDs

These flags filter `edits[]` before diff/apply/verify execution and fail closed on unknown or duplicate IDs.

`tg.exe run --batch-rewrite <config.json> <path>` emits the same common rewrite-plan envelope, but replaces the single `pattern` / `replacement` / `lang` fields with a `rewrites` array copied from the config file.

Example: [`examples/rewrite_plan.json`](examples/rewrite_plan.json)

| Field | Type | Notes |
| --- | --- | --- |
| `version` | `integer` | Contract version. |
| `routing_backend` | `string` | `AstBackend`. |
| `routing_reason` | `string` | `ast-native`. |
| `sidecar_used` | `boolean` | `false`. |
| `pattern` | `string` | Structural search pattern. |
| `replacement` | `string` | Rewrite template. |
| `lang` | `string` | Tree-sitter language passed with `--lang`. |
| `total_files_scanned` | `integer` | Files walked during planning. |
| `total_edits` | `integer` | Accepted edits in `edits`. |
| `edits` | `array<object>` | Concrete edit plan. |
| `rejected_overlaps` | `array<object>` | Optional; only present when overlapping edits are rejected. |

Each `edits[]` object has:

| Field | Type | Notes |
| --- | --- | --- |
| `id` | `string` | Stable edit identifier. |
| `file` | `string` | File being rewritten. |
| `line` | `integer` | 1-based source line for the match. |
| `byte_range.start` | `integer` | Inclusive byte start in the original file. |
| `byte_range.end` | `integer` | Exclusive byte end in the original file. |
| `original_text` | `string` | Original matched text. |
| `replacement_text` | `string` | Final replacement text to write. |
| `metavar_env` | `object<string,string>` | Bound metavariables captured from the match. |

If `rejected_overlaps` is present, each object contains `file`, `edit_a`, `edit_b`, and `reason`.

## Batch Rewrite Config

Batch rewrite is configured with `tg.exe run --batch-rewrite <config.json> <path>`.

Config schema:

```json
{
  "rewrites": [
    {
      "pattern": "def $F($$$ARGS): return $EXPR",
      "replacement": "lambda $$$ARGS: $EXPR",
      "lang": "python"
    }
  ],
  "verify": true
}
```

Rules:

- `rewrites` is required and must be a non-empty array.
- Each rewrite object must include string `pattern`, `replacement`, and `lang` fields.
- `verify` is optional; if present it must be a boolean and enables post-apply byte-level verification for batch apply.
- Invalid configs fail with field-specific errors such as `rewrites[0].replacement`.

Batch planning/apply behavior:

- all configured patterns are planned against the original file contents before any write occurs
- `rejected_overlaps` reports cross-pattern conflicts, and conflicted files are left unchanged
- batch apply reuses the same atomic-write, BOM/CRLF preservation, binary-skip, and stale-file protections as single rewrites

## Apply + Verify JSON

Emitted by `tg.exe run --rewrite ... --apply --verify --json ...`.

Optional edit selection flags:

- `--apply-edit-ids <id1,id2,...>`
- `--reject-edit-ids <id1,id2,...>`
- `--lint-cmd <command>`
- `--test-cmd <command>`

When edit selection flags are present, the emitted `plan` object reflects the filtered subset that was actually applied and verified.
When validation flags are present, the emitted payload also includes a structured `validation` object describing each post-apply command.

Example: [`examples/rewrite_apply_verify.json`](examples/rewrite_apply_verify.json)

| Field | Type | Notes |
| --- | --- | --- |
| `version` | `integer` | Contract version. |
| `routing_backend` | `string` | `AstBackend`. |
| `routing_reason` | `string` | `ast-native`. |
| `sidecar_used` | `boolean` | `false`. |
| `checkpoint` | `object \| null` | Present when `--checkpoint` is requested before apply; otherwise `null` or omitted. |
| `plan` | `object` | Full rewrite plan object, using the same shape as Rewrite Plan JSON. |
| `validation` | `object \| null` | Present when `--lint-cmd` and/or `--test-cmd` is requested; otherwise `null` or omitted. |
| `verification` | `object \| null` | Present when `--verify` is requested; otherwise `null`. |

`verification` currently contains:

| Field | Type | Notes |
| --- | --- | --- |
| `total_edits` | `integer` | Total planned edits checked after apply. |
| `verified` | `integer` | Edits whose replacement bytes matched exactly. |
| `mismatches` | `array<object>` | Empty on success. |

Each `mismatches[]` object contains `edit_id`, `file`, `line`, `expected`, and `actual`.

`validation` currently contains:

| Field | Type | Notes |
| --- | --- | --- |
| `success` | `boolean` | `true` only when all requested post-apply commands succeeded. |
| `commands` | `array<object>` | Ordered list of executed validation commands. |

Each `commands[]` object contains `kind`, `command`, `success`, `exit_code`, `stdout`, and `stderr`.

## GPU Sidecar JSON

Emitted by `tg.exe search --gpu-device-ids <ids> --json ...`.

Example: [`examples/gpu_sidecar_search.json`](examples/gpu_sidecar_search.json)

This is a hybrid contract:

- the Rust control plane injects the unified envelope (`version`, `routing_backend`, `routing_reason`, `sidecar_used`)
- the nested search payload comes from Python sidecar JSON

| Field | Type | Notes |
| --- | --- | --- |
| `version` | `integer` | Added by Rust. |
| `routing_backend` | `string` | `GpuSidecar`, added by Rust. |
| `routing_reason` | `string` | `gpu-device-ids-explicit`, added by Rust. |
| `sidecar_used` | `boolean` | `true`, added by Rust. |
| `total_matches` | `integer` | Preserved from sidecar payload. |
| `total_files` | `integer` | Preserved from sidecar payload. |
| `routing_gpu_device_ids` | `array<integer>` | Device IDs reported by the sidecar payload. |
| `matches` | `array<object>` | Sidecar match rows. |

Each GPU sidecar `matches[]` object has:

| Field | Type | Notes |
| --- | --- | --- |
| `file` | `string` | Absolute file path. |
| `line_number` | `integer` | 1-based line number from the Python sidecar. |
| `text` | `string` | Matching line text. |

On this worker host the real GPU Python backends were unavailable, so the committed example was produced by running the real native `tg.exe` command against `bench_data/` with `TG_SIDECAR_SCRIPT` set to a deterministic mock. That still exercises the Rust sidecar transport and envelope normalization path.

## Calibrate JSON

Emitted by `tg.exe calibrate`.

Example: [`examples/calibrate.json`](examples/calibrate.json)

This shape is the persisted routing calibration contract consumed by the native Rust router.

| Field | Type | Notes |
| --- | --- | --- |
| `version` | `integer` | Contract version. Current value: `1`. |
| `routing_backend` | `string` | `Calibration` for the committed example. |
| `routing_reason` | `string` | `manual-calibrate` for the committed example. |
| `sidecar_used` | `boolean` | Always `false` for the native calibrate command. |
| `corpus_size_breakpoint_bytes` | `integer` | Smallest corpus size where GPU became the recommended route in the calibrated run. |
| `cpu_median_ms` | `number` | Representative CPU median at the chosen breakpoint. |
| `gpu_median_ms` | `number` | Representative GPU median at the chosen breakpoint. |
| `recommendation` | `string` | Stable routing recommendation such as `gpu_above_100mb` or `cpu_always`. |
| `calibration_timestamp` | `integer` | Unix timestamp written with the accepted calibration result. |
| `device_name` | `string` | Device name associated with the calibration run. |
| `measurements` | `array<object>` | Calibration points used to derive the recommendation. |

Each `measurements[]` object has:

| Field | Type | Notes |
| --- | --- | --- |
| `size_bytes` | `integer` | Corpus size benchmarked at this point. |
| `cpu_median_ms` | `number` | CPU median for the point. |
| `gpu_median_ms` | `number` | GPU median for the point. |
| `cpu_samples_ms` | `array<number>` | Raw CPU timing samples retained for auditability. |
| `gpu_samples_ms` | `array<number>` | Raw GPU timing samples retained for auditability. |

## Search NDJSON

Emitted by `tg.exe search --ndjson ...`.

Example: [`examples/search.ndjson`](examples/search.ndjson)

This is the streaming variant of Search JSON. Each line is a standalone JSON object with the common envelope plus a single match row.

| Field | Type | Notes |
| --- | --- | --- |
| `version` | `integer` | Contract version. |
| `routing_backend` | `string` | Backend selected by the Rust router. |
| `routing_reason` | `string` | Stable reason for the route. |
| `sidecar_used` | `boolean` | `false` for the committed native example. |
| `query` | `string` | Search pattern. |
| `path` | `string` | Search root. |
| `file` | `string` | Absolute path of the matched file for this row. |
| `line` | `integer` | 1-based line number for this row. |
| `text` | `string` | Matching line text. |
| `pattern_id` | `integer \| null` | Present for multi-pattern routes. |
| `pattern_text` | `string \| null` | Present when `pattern_id` is present. |

## Symbol Defs JSON

Emitted by `tg.exe defs --symbol <name> --json ...`.

Example: [`examples/defs.json`](examples/defs.json)

| Field | Type | Notes |
| --- | --- | --- |
| `version` | `integer` | Contract version. |
| `routing_backend` | `string` | `RepoMap`. |
| `routing_reason` | `string` | `symbol-defs`. |
| `sidecar_used` | `boolean` | `false`. |
| `coverage` | `object` | Same coverage contract as Repo Map JSON. |
| `path` | `string` | Inventory root. |
| `symbol` | `string` | Exact symbol name requested. |
| `definitions` | `array<object>` | Exact symbol definitions. |
| `files` | `array<string>` | Files containing exact definitions. |
| `tests` | `array<string>` | Test files in the inventory root. |
| `related_paths` | `array<string>` | Stable union of definition files and tests. |

Each `definitions[]` object contains `name`, `kind`, `file`, and `line`.

## Symbol Impact JSON

Emitted by `tg.exe impact --symbol <name> --json ...`.

Example: [`examples/impact.json`](examples/impact.json)

| Field | Type | Notes |
| --- | --- | --- |
| `version` | `integer` | Contract version. |
| `routing_backend` | `string` | `RepoMap`. |
| `routing_reason` | `string` | `symbol-impact`. |
| `sidecar_used` | `boolean` | `false`. |
| `coverage` | `object` | Same coverage contract as Repo Map JSON. |
| `path` | `string` | Inventory root. |
| `symbol` | `string` | Exact symbol name evaluated. |
| `definitions` | `array<object>` | Exact symbol definitions. |
| `files` | `array<string>` | Likely impacted source files, definition file first. |
| `tests` | `array<string>` | Likely impacted tests. |
| `imports` | `array<object>` | Ranked import entries from the context pack path. |
| `symbols` | `array<object>` | Ranked related symbols, including `score`. |
| `related_paths` | `array<string>` | Stable union of impacted files and tests. |

## Symbol Refs JSON

Emitted by `tg.exe refs --symbol <name> --json ...`.

Example: [`examples/refs.json`](examples/refs.json)

This is currently a Python-first symbol navigation contract. It finds exact name/attribute references from Python ASTs and does not claim full cross-language semantic resolution.

| Field | Type | Notes |
| --- | --- | --- |
| `version` | `integer` | Contract version. |
| `routing_backend` | `string` | `RepoMap`. |
| `routing_reason` | `string` | `symbol-refs`. |
| `sidecar_used` | `boolean` | `false`. |
| `coverage` | `object` | Same coverage contract as Repo Map JSON. |
| `path` | `string` | Inventory root. |
| `symbol` | `string` | Exact symbol name evaluated. |
| `definitions` | `array<object>` | Exact symbol definitions. |
| `references` | `array<object>` | Python-first reference rows. |
| `files` | `array<string>` | Files containing reference rows. |
| `related_paths` | `array<string>` | Stable union of definition files, reference files, and tests. |

Each `references[]` object contains `name`, `kind`, `file`, `line`, and `text`.

## Symbol Callers JSON

Emitted by `tg.exe callers --symbol <name> --json ...`.

Example: [`examples/callers.json`](examples/callers.json)

This is currently a Python-first symbol navigation contract. It finds exact Python call sites by name/attribute match and combines them with likely impacted tests.

| Field | Type | Notes |
| --- | --- | --- |
| `version` | `integer` | Contract version. |
| `routing_backend` | `string` | `RepoMap`. |
| `routing_reason` | `string` | `symbol-callers`. |
| `sidecar_used` | `boolean` | `false`. |
| `coverage` | `object` | Same coverage contract as Repo Map JSON. |
| `path` | `string` | Inventory root. |
| `symbol` | `string` | Exact symbol name evaluated. |
| `definitions` | `array<object>` | Exact symbol definitions. |
| `callers` | `array<object>` | Python-first call rows. |
| `files` | `array<string>` | Files containing call sites. |
| `tests` | `array<string>` | Likely impacted tests. |
| `related_paths` | `array<string>` | Stable union of definition files, caller files, and tests. |

## Session Open JSON

Emitted by `tg.exe session open ... --json`.

Example: [`examples/session_open.json`](examples/session_open.json)

| Field | Type | Notes |
| --- | --- | --- |
| `session_id` | `string` | Stable identifier for later session queries. |
| `root` | `string` | Session root. |
| `created_at` | `string` | ISO-8601 timestamp for the cached repo map. |
| `file_count` | `integer` | Number of source files captured in the cached repo map. |
| `symbol_count` | `integer` | Number of symbols captured in the cached repo map. |

## Session Refresh JSON

Emitted by `tg session refresh <id> ... --json`.

| Field | Type | Notes |
| --- | --- | --- |
| `session_id` | `string` | Session identifier refreshed in place. |
| `root` | `string` | Session root. |
| `refreshed_at` | `string` | ISO-8601 refresh timestamp. |
| `file_count` | `integer` | Number of source files captured after refresh. |
| `symbol_count` | `integer` | Number of symbols captured after refresh. |

## Session Context JSON

Emitted by `tg.exe session context <id> --query ... --json`.

Example: [`examples/session_context.json`](examples/session_context.json)

This reuses a cached repo map instead of rebuilding inventory for every query.

| Field | Type | Notes |
| --- | --- | --- |
| `version` | `integer` | Contract version. |
| `routing_backend` | `string` | `RepoMap`. |
| `routing_reason` | `string` | `session-context`. |
| `sidecar_used` | `boolean` | `false`. |
| `coverage` | `object` | Same coverage contract as Repo Map JSON. |
| `path` | `string` | Session root. |
| `query` | `string` | Query text used to rank context. |
| `session_id` | `string` | Session identifier used for the cached lookup. |
| `files` | `array<string>` | Ranked source files derived from the cached repo map. |
| `symbols` | `array<object>` | Ranked symbols, including `score`. |
| `tests` | `array<string>` | Ranked tests derived from the cached repo map. |
| `related_paths` | `array<string>` | Stable union of ranked files and tests. |

## Session Serve JSONL

Emitted by `tg session serve <id> [PATH]`.

This is the long-lived session loop for repeated edit-tooling requests. It reads newline-delimited
JSON requests from stdin and emits one JSON response per line to stdout.

Request shape:

```json
{"command":"context","query":"invoice payment"}
```

Supported commands:

- `ping`
- `show`
- `repo_map`
- `context`
- `defs`
- `impact`
- `refs`
- `callers`

Responses reuse the same public payload shapes as the one-shot session and repo-map-derived
commands, with an added `session_id` field.

Use `--refresh-on-stale` to refresh the cached session once and retry the request when file
changes are detected.

Invalid requests return:

```json
{"version":1,"session_id":"session-...","error":{"code":"invalid_request","message":"..."}}
```

## MCP Tool Responses

The MCP server exposes stable tool contracts layered on top of the native CLI outputs.

Current tool set:

- `tg_repo_map(path=".")`
- `tg_context_pack(query, path=".")`
- `tg_symbol_defs(symbol, path=".")`
- `tg_symbol_impact(symbol, path=".")`
- `tg_symbol_refs(symbol, path=".")`
- `tg_symbol_callers(symbol, path=".")`
- `tg_checkpoint_create(path=".")`
- `tg_checkpoint_list(path=".")`
- `tg_checkpoint_undo(checkpoint_id, path=".")`
- `tg_session_open(path=".")`
- `tg_session_list(path=".")`
- `tg_session_show(session_id, path=".")`
- `tg_session_context(session_id, query, path=".")`
- `tg_index_search(pattern, path=".")`
- `tg_rewrite_plan(pattern, replacement, lang, path=".")`
- `tg_rewrite_apply(pattern, replacement, lang, path=".", verify=False, checkpoint=False, lint_cmd=None, test_cmd=None)`
- `tg_rewrite_diff(pattern, replacement, lang, path=".")`

Response mapping:

- `tg_index_search(...)` returns the same v1 envelope and payload shape as [`examples/index_search.json`](examples/index_search.json)
- `tg_symbol_defs(...)` returns the same v1 envelope and payload shape as [`examples/defs.json`](examples/defs.json)
- `tg_symbol_impact(...)` returns the same v1 envelope and payload shape as [`examples/impact.json`](examples/impact.json)
- `tg_symbol_refs(...)` returns the same v1 envelope and payload shape as [`examples/refs.json`](examples/refs.json)
- `tg_symbol_callers(...)` returns the same v1 envelope and payload shape as [`examples/callers.json`](examples/callers.json)
- `tg_session_open(...)` returns the same payload shape as [`examples/session_open.json`](examples/session_open.json)
- `tg_session_context(...)` returns the same payload shape as [`examples/session_context.json`](examples/session_context.json)
- `tg_rewrite_plan(...)` returns the same v1 envelope and payload shape as [`examples/rewrite_plan.json`](examples/rewrite_plan.json)
- `tg_rewrite_apply(..., verify=True, checkpoint=True, lint_cmd=..., test_cmd=...)` returns the same v1 envelope and payload shape as [`examples/rewrite_apply_verify.json`](examples/rewrite_apply_verify.json)
- `tg_rewrite_diff(...)` returns a diff wrapper JSON object instead of raw diff text

Example diff wrapper: [`examples/mcp_rewrite_diff.json`](examples/mcp_rewrite_diff.json)

`tg_rewrite_diff(...)` response fields:

| Field | Type | Notes |
| --- | --- | --- |
| `version` | `integer` | Contract version. |
| `routing_backend` | `string` | `AstBackend`. |
| `routing_reason` | `string` | `ast-native`. |
| `sidecar_used` | `boolean` | `false`. |
| `diff` | `string` | Unified diff preview generated by the native CLI. |

## Rust vs Python field differences

The current codebase still exposes a few shape differences between native Rust JSON and Python-originated JSON:

| Area | Native Rust output | Python-originated output |
| --- | --- | --- |
| Match line field | `line` | `line_number` |
| Search metadata | `query`, `path`, `total_matches` | Python CLI/search sidecar payloads may also include `total_files`, `matched_file_paths`, `match_counts_by_file`, and GPU worker metadata |
| GPU search envelope | Rust adds `version`, `routing_backend`, `routing_reason`, `sidecar_used` | Python provides the nested match payload that Rust augments rather than reshaping |

In practice:

- `search.json` and `index_search.json` are fully native Rust shapes.
- `rewrite_plan.json` and `rewrite_apply_verify.json` are fully native Rust shapes.
- `gpu_sidecar_search.json` is the mixed Rust/Python shape, so its match rows use `line_number`, not `line`.

## Diff Output

`tg.exe run --rewrite ... --diff ...` does **not** emit JSON. It prints a unified diff preview and does not modify the file on disk.

Expected structure:

- `--- a/<path>` original file header
- `+++ b/<path>` rewritten file header
- `@@ -old,+new @@` hunk header
- removed lines prefixed with `-`
- added lines prefixed with `+`

Example excerpt from a real run:

```diff
--- a/C:\dev\projects\tensor-grep\bench_data\harness_api_doc_inputs\rewrite\rewrite_fixture.py
+++ b/C:\dev\projects\tensor-grep\bench_data\harness_api_doc_inputs\rewrite\rewrite_fixture.py
@@ -1,2 +1,2 @@
-def add(x, y): return x + y
-def mul(a, b): return a * b
+lambda x, y: x + y
+lambda a, b: a * b
```

## Command summary

The committed examples were generated with commands equivalent to:

```powershell
tg.exe search --no-ignore --json ERROR bench_data\<temp-search-dir>
tg.exe search --index --no-ignore --fixed-strings --json ERROR bench_data\<temp-index-dir>
tg.exe run --lang python --rewrite 'lambda $$$ARGS: $EXPR' 'def $F($$$ARGS): return $EXPR' bench_data\<temp-rewrite-file>
tg.exe run --lang python --rewrite 'lambda $$$ARGS: $EXPR' --apply --verify --lint-cmd "ruff check ." --test-cmd "pytest -q" --json 'def $F($$$ARGS): return $EXPR' bench_data\<temp-rewrite-file>
tg.exe run --batch-rewrite batch-rewrite.json --apply --json bench_data\<temp-rewrite-dir>
tg.exe search --gpu-device-ids 0 --json ERROR bench_data\<temp-gpu-dir>
tg.exe calibrate
tg.exe search --no-ignore --ndjson ERROR bench_data\<temp-search-dir>
tg.exe run --lang python --rewrite 'lambda $$$ARGS: $EXPR' --diff 'def $F($$$ARGS): return $EXPR' bench_data\<temp-rewrite-file>
```

## Compatibility Policy

The harness API is a versioned public contract.

Rules:

- additive field changes are allowed within the same major contract version when existing required fields and meanings stay intact
- breaking changes require a version bump
- field renames, type changes, removing required fields, or changing single-document output into a different transport shape are breaking changes
- new example artifacts and schema tests must land with any contract expansion
- docs, example artifacts, and schema tests must stay in sync
