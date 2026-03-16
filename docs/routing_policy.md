# Routing Policy

This document describes the routing decision tree that is currently implemented in `rust_core/src/main.rs`.

## Backend inventory

`main.rs` defines four routing identities in `RoutingDecision`:

| Backend | Reason string | What it means in practice |
| --- | --- | --- |
| `CpuBackend` | `cpu-native` | Native CPU text search metadata. For the normal cold text-search CLI hot path, this is paired with `rg` passthrough when JSON output is not requested. |
| `AstBackend` | `ast-native` | Native Rust AST search and rewrite path used by `tg run`. |
| `TrigramIndex` | `index-accelerated` | Warm or explicit trigram-index search path. |
| `GpuSidecar` | `gpu-device-ids-explicit` | Python sidecar GPU search path. |

## `tg search` decision tree

`handle_ripgrep_search` is the main routing function for `tg search`.

1. **Explicit `--index` wins first.**
   - If `args.index` is set, `handle_ripgrep_search` immediately calls `handle_index_search`.
   - `handle_index_search` builds, reloads, or rebuilds `.tg_index` as needed, then finishes in `run_index_query`.

2. **Warm index auto-routing is the next branch.**
   - Auto-routing only runs when all of these conditions are true:
     - `--index` was **not** passed.
     - `-v/--invert-match` is absent.
     - `-C/--context` is absent.
     - `--max-count` / `-m` is absent.
     - `-w/--word-regexp` is absent.
     - `-g/--glob` was not used.
     - a `.tg_index` file exists for the search root.
     - the cached index loads successfully.
     - the cached index is not stale.
     - `pattern >= 3 bytes`.
   - When those checks pass, the request is routed to `run_index_query` with `routing_backend = "TrigramIndex"` and `routing_reason = "index-accelerated"`.
   - Compatible flags such as `-i`, `-F`, `-c`, `--json`, and `--no-ignore` do **not** block this branch in the current code.

3. **Explicit GPU routing is checked after warm-index auto-routing.**
   - `GpuSidecar` is only selected from `handle_ripgrep_search` when the user passes `--gpu-device-ids`.
   - There is no implicit GPU auto-routing in `main.rs`.
   - **Current code-order caveat:** warm index auto-routing is evaluated before the explicit `--gpu-device-ids` branch, so a compatible warm index can preempt a GPU request.

4. **JSON text search uses native CPU search.**
   - If `--json` is set and the request did not already route to index or GPU, `handle_ripgrep_search` uses `CpuBackend::search_with_paths(...)` and emits `routing_backend = "CpuBackend"` / `routing_reason = "cpu-native"`.

5. **Cold text search falls through to the default CpuBackend/rg path.**
   - For the standard human-readable `tg search PATTERN PATH` flow, cold text search uses `execute_ripgrep_search(...)`.
   - This is the repo's default text-search policy: no explicit index, no compatible warm index, no explicit GPU sidecar request, and no JSON-only CPU materialization.

## `tg run` decision tree

`handle_ast_run` always constructs `AstBackend` first, so AST work never routes through `CpuBackend`, `TrigramIndex`, or `GpuSidecar`.

- **AST search:** `tg run <pattern> <path>` always uses `AstBackend` with `routing_reason = "ast-native"`.
- **Rewrite planning:** `tg run --rewrite ...` routes to `handle_ast_rewrite`, which keeps the work on `AstBackend`.
- **Rewrite apply:** `tg run --rewrite ... --apply` routes to `handle_ast_rewrite_apply`, which also stays on `AstBackend`.
- **Rewrite diff:** `tg run --rewrite ... --diff` still stays on `AstBackend` because the diff is generated from the AST rewrite plan.

In short: **AST run -> always `AstBackend`; rewrite -> always `AstBackend`.**

## Positional CLI note

`run_positional_cli` has a separate, simpler order:

1. `--gpu-device-ids` -> `handle_gpu_sidecar_search`
2. `--json` -> `CpuBackend::search_with_paths(...)`
3. otherwise, if `--force-cpu` is not set, `--replace` is not set, and `rg` is available -> `execute_ripgrep_search(...)`
4. otherwise -> native `CpuBackend`

This positional path does **not** auto-promote to `TrigramIndex`.

## Source cross-reference

These are the `main.rs` entry points that define the current routing tree:

| Source area | Current function / block |
| --- | --- |
| Backend names and reason strings | `RoutingDecision` |
| Positional CLI routing | `run_positional_cli` |
| Search subcommand routing | `handle_ripgrep_search` |
| Explicit index build/load/rebuild path | `handle_index_search` |
| Index query execution | `run_index_query` |
| AST command routing | `handle_ast_run` |
| Rewrite planning path | `handle_ast_rewrite` |
| Rewrite apply/verify path | `handle_ast_rewrite_apply` |
| Explicit GPU sidecar path | `handle_gpu_sidecar_search` |

At the time of writing, these blocks appear in `rust_core/src/main.rs` around the following ranges: `RoutingDecision` (~204-226), `run_positional_cli` (~270-403), `handle_ripgrep_search` (~405-495), `handle_index_search` / `run_index_query` (~509-595), `handle_ast_run` / `handle_ast_rewrite` / `handle_ast_rewrite_apply` (~625-767), and `handle_gpu_sidecar_search` (~785-823).
