# Routing Policy

This document describes the unified smart router implemented in `rust_core/src/routing.rs` and consumed by both `run_positional_cli` and `handle_ripgrep_search` in `rust_core/src/main.rs`.

## Router entry point

Text-search routing now flows through a single decision function:

```rust
route_search(config, calibration_data, index_state, gpu_available) -> RoutingDecision
```

`RoutingDecision` carries:

- `selection` (`NativeCpu`, `NativeGpu`, `TrigramIndex`, `AstBackend`, `Ripgrep`, `GpuSidecar`)
- `routing_backend`
- `routing_reason`
- `sidecar_used`
- `allow_rg_fallback`

## Backend inventory

| Backend | Reason string(s) | What it means in practice |
| --- | --- | --- |
| `NativeCpuBackend` | `force_cpu`, `json_output`, `cpu-auto-size-threshold`, `gpu-auto-fallback-cpu`, `rg_unavailable` | Native Rust text search is the default CPU path and the normal destination when no higher-priority route wins. |
| `NativeGpuBackend` | `gpu-device-ids-explicit-native`, `gpu-auto-size-threshold` | Native Rust CUDA search. Explicit `--gpu-device-ids` always targets this route first; calibrated auto-routing can also choose it. |
| `TrigramIndex` | `index-accelerated` | Explicit `--index` and warm compatible `.tg_index` auto-routing both land here. |
| `AstBackend` | `ast-native` | Native Rust AST search/rewrite path for `tg run`. |
| `GpuSidecar` | `gpu-device-ids-explicit` | Python sidecar fallback used when an explicit GPU request cannot stay on the native GPU path (for example non-CUDA builds or unsupported GPU-native search features). |
| `RipgrepBackend` | `rg_passthrough` | Final fallback only after a native CPU route fails and structured output is not required. |

## Unified `tg search` decision tree

The router's priority order is now explicit and shared:

1. `--index` -> `TrigramIndex`
2. `--gpu-device-ids` -> `NativeGpuBackend`
3. `--force-cpu` / `--cpu` -> `NativeCpuBackend`
4. AST command -> `AstBackend`
5. Warm non-stale compatible `.tg_index` -> `TrigramIndex`
6. Corpus `>` calibrated threshold **and** GPU available **and** calibration positive -> `NativeGpuBackend`
7. Otherwise -> `NativeCpuBackend`
8. If the selected native CPU route fails and `allow_rg_fallback` is true -> `RipgrepBackend` final fallback

### Notes on the tree

- `--index` is the highest priority override.
- `--gpu-device-ids` overrides warm-index and size-based routing.
- `--force-cpu` overrides auto GPU routing, but not an explicit `--gpu-device-ids` request.
- Warm-index auto-routing only applies when the cache exists, is not stale, and the query is index-compatible (`pattern >= 3 bytes`, no `-v`, no `-C`, no `--max-count`, no `-w`, no `-g`).
- JSON and NDJSON output do **not** bypass a warm compatible index anymore.
- Auto GPU routing is conservative: no fresh positive calibration means CPU.
- `rg` is no longer the normal cold-path choice; it remains the final fallback and is also preferred for plain `-C/--context` searches to preserve the benchmark guard for context-line output.

## GPU-specific behavior

The smart router chooses `NativeGpuBackend` for both explicit and calibrated auto GPU paths, but execution still distinguishes two cases in `main.rs`:

- **Explicit GPU routing:** `handle_gpu_search(...)`
  - stays on native GPU when the search shape is GPU-native compatible
  - falls back to `GpuSidecar` for unsupported explicit GPU modes
- **Auto GPU routing:** `handle_auto_gpu_search(...)`
  - only attempted when the router already proved the search is GPU-native compatible
  - if CUDA is unavailable, falls back to `NativeCpuBackend` with `routing_reason = "gpu-auto-fallback-cpu"`
  - if GPU initialization fails fatally, exits with a user-facing error instead of silently changing backends

## AST commands

`tg run` is always routed to `AstBackend` with `routing_reason = "ast-native"`.

That applies to:

- AST search
- rewrite planning (`--rewrite`)
- rewrite apply (`--apply`)
- rewrite diff (`--diff`)
- batch rewrite flows

## Source cross-reference

| Source area | Current function / block |
| --- | --- |
| Unified routing data types and decision tree | `rust_core/src/routing.rs` (`BackendSelection`, `RoutingDecision`, `SearchRoutingConfig`, `route_search`) |
| Positional CLI smart-routing call site | `run_positional_cli` |
| Search subcommand smart-routing call site | `handle_ripgrep_search` |
| Warm-index detection | `detect_warm_index_state` |
| Calibration loading for routing | `load_search_routing_calibration` |
| Explicit / rebuildable index execution | `handle_index_search` + `run_index_query` |
| Explicit GPU routing execution | `handle_gpu_search` |
| Auto GPU execution + CPU fallback | `handle_auto_gpu_search` |
| Explicit GPU sidecar fallback | `handle_gpu_sidecar_search` |
| AST routing execution | `handle_ast_run` |
