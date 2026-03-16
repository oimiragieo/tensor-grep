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
| Rewrite plan JSON | `tg.exe run --rewrite ...` | [`examples/rewrite_plan.json`](examples/rewrite_plan.json) |
| Apply + verify JSON | `tg.exe run --rewrite ... --apply --verify --json ...` | [`examples/rewrite_apply_verify.json`](examples/rewrite_apply_verify.json) |
| GPU sidecar JSON | `tg.exe search --gpu-device-ids ... --json ...` | [`examples/gpu_sidecar_search.json`](examples/gpu_sidecar_search.json) |

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

## Rewrite Plan JSON

Emitted by `tg.exe run --rewrite <replacement> <pattern> <path>` when `--diff` and `--apply` are not set.

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

## Apply + Verify JSON

Emitted by `tg.exe run --rewrite ... --apply --verify --json ...`.

Example: [`examples/rewrite_apply_verify.json`](examples/rewrite_apply_verify.json)

| Field | Type | Notes |
| --- | --- | --- |
| `version` | `integer` | Contract version. |
| `routing_backend` | `string` | `AstBackend`. |
| `routing_reason` | `string` | `ast-native`. |
| `sidecar_used` | `boolean` | `false`. |
| `plan` | `object` | Full rewrite plan object, using the same shape as Rewrite Plan JSON. |
| `verification` | `object \| null` | Present when `--verify` is requested; otherwise `null`. |

`verification` currently contains:

| Field | Type | Notes |
| --- | --- | --- |
| `total_edits` | `integer` | Total planned edits checked after apply. |
| `verified` | `integer` | Edits whose replacement bytes matched exactly. |
| `mismatches` | `array<object>` | Empty on success. |

Each `mismatches[]` object contains `edit_id`, `file`, `line`, `expected`, and `actual`.

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
tg.exe run --lang python --rewrite 'lambda $$$ARGS: $EXPR' --apply --verify --json 'def $F($$$ARGS): return $EXPR' bench_data\<temp-rewrite-file>
tg.exe search --gpu-device-ids 0 --json ERROR bench_data\<temp-gpu-dir>
tg.exe run --lang python --rewrite 'lambda $$$ARGS: $EXPR' --diff 'def $F($$$ARGS): return $EXPR' bench_data\<temp-rewrite-file>
```
