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
| `NativeCpuBackend` | `force_cpu`, `json_output`, `cpu-auto-size-threshold`, `gpu-auto-fallback-cpu`, `rg_unavailable` | Native Rust text search is used for structured explicit `--cpu`, JSON/NDJSON output, GPU fallback, and when `rg` is unavailable. |
| `NativeGpuBackend` | `gpu-device-ids-explicit-native`, `gpu-auto-size-threshold` | Native Rust CUDA search. Explicit `--gpu-device-ids` always targets this route first; calibrated auto-routing can also choose it. |
| `TrigramIndex` | `index-accelerated` | Explicit `--index` and warm compatible `.tg_index` auto-routing both land here. Explicit `--index` is gated the same way warm auto-routing is (see the fail-closed note below) -- it is not an unconditional override. |
| `AstBackend` | `ast-native` | Native Rust AST search/rewrite path for `tg run`. |
| `GpuSidecar` | `gpu-device-ids-explicit` | Python sidecar fallback used when an explicit GPU request cannot stay on the native GPU path (for example non-CUDA builds or unsupported GPU-native search features). |
| `RipgrepBackend` | `rg_passthrough` | Default cold-path backend for generic text search when `rg` is available and the request does not require native structured output. Also used as the final fallback after a forced native CPU route fails. |

## Unified `tg search` decision tree

The router's priority order is now explicit and shared:

1. `--index` -> `TrigramIndex`
2. `--gpu-device-ids` -> `NativeGpuBackend`
3. `--force-cpu` / `--cpu` with structured output or no usable `rg` -> `NativeCpuBackend`
4. AST command -> `AstBackend`
5. Warm non-stale compatible `.tg_index` -> `TrigramIndex`
6. Corpus `>` calibrated threshold **and** GPU available **and** calibration positive -> `NativeGpuBackend`
7. Otherwise, if `rg` is available and structured output is not required -> `RipgrepBackend`
8. Otherwise -> `NativeCpuBackend`
9. If the selected native CPU route fails and `allow_rg_fallback` is true -> `RipgrepBackend` final fallback

### Notes on the tree

- `--index` is the highest priority override for the *routing decision* (which backend gets picked). It is not an override of *search semantics* -- see the fail-closed note below for what happens once `TrigramIndex` is selected.
- `--gpu-device-ids` overrides warm-index and size-based routing.
- `--force-cpu` overrides auto GPU routing, but not an explicit `--gpu-device-ids` request.
- Plain `--cpu` / `--force-cpu` may still use `RipgrepBackend` for rg-compatible text output parity.
- Warm-index auto-routing only applies when the cache exists, is not stale, and the query is index-compatible (`pattern >= 3 bytes`, no `-v`, no `-C`, no `--max-count`, no `-w`, no `-g`).
- JSON and NDJSON output do **not** bypass a warm compatible index anymore.
- Auto GPU routing is conservative: no fresh positive calibration means stay on the CPU-side cold path.
- `rg` is again the normal cold-path choice for generic text search when available. Native CPU remains the default only for structured outputs, explicit `--cpu`, warm index, AST, and GPU fallback cases.

### `--index` fail-closed compatibility contract (audit H1, 2026-07-10)

`route_search` selects `TrigramIndex` for explicit `--index` before any compatibility check
runs (`routing.rs:234-236`), so `handle_index_search` (`main.rs`) enforces the following
itself, *after* routing, before running the query:

- **Refused (fails closed with a non-zero exit and an error naming the flag):** `-v`/`--invert-match`,
  context (`-C`/`-A`/`-B`), `-m`/`--max-count`, `-w`/`--word-regexp`, `-g`/`--glob`, and multiple
  `-e` patterns. These are the same conditions `detect_warm_index_state` already enforces for
  warm-index auto-routing (`main.rs`) -- `run_index_query` never consults any of them, so honoring
  `--index` together with one of them used to silently drop the flag instead of honoring or
  refusing it (for example, `--index -v` returned the *non-inverted* result set with exit 0).
- **Transparently handled via an internal full-scan fallback (no error, correct results):**
  fixed-string patterns shorter than the 3-byte trigram length, and non-ASCII `--ignore-case`
  fixed-string patterns. Both cases have zero or mismatched trigrams to prefilter on, so
  `TrigramIndex::search` falls back to scanning every indexed file directly instead of trusting an
  empty/mismatched trigram candidate set as "no match" (`index.rs`,
  `fixed_string_candidate_selection`).
- **`--smart-case` (`-S`) honored per pattern:** `-S` is not diverted to ripgrep in JSON/NDJSON
  output mode (`search_requires_ripgrep_passthrough` gates it behind `!json && !ndjson`), so it
  reaches the index. `run_index_query` resolves case-sensitivity per pattern
  (`args.ignore_case || (args.smart_case && smart_case_pattern_is_case_insensitive(pattern))`)
  before calling `index.search`, so an all-lowercase `-S` pattern searches case-insensitively
  (matching an uppercase occurrence) and a pattern containing an uppercase char stays
  case-sensitive -- identical to native smart-case. This is honored rather than refused because
  it is index-doable and reuses the same `ignore_case` path (and its H1b/H1c full-scan safety
  nets); before this fix `-S` was silently dropped to a case-sensitive query (a false negative,
  exit 0). Covers both explicit `--index` and warm auto-routing, since both reach
  `run_index_query`.
- **`--no-ignore` mode tracking:** the on-disk index format records the `no_ignore` mode it was
  built with (`INDEX_FORMAT_VERSION` 4). A query whose `--no-ignore` request disagrees with the
  stored build mode is treated as stale and triggers a rebuild under the query's requested mode --
  this closes both an information-disclosure gap (an index built with `--no-ignore` silently
  leaking gitignored content into a later default query) and a false-negative gap (an index built
  without `--no-ignore` silently missing gitignored files a later `--no-ignore` query asked for).
  This applies to warm auto-routing too, via the same `is_stale`/`staleness_reason` check.

## GPU-specific behavior

The smart router chooses `NativeGpuBackend` for both explicit and calibrated auto GPU paths, but execution still distinguishes two cases in `main.rs`:

- **Explicit GPU routing:** `handle_gpu_search(...)`
  - stays on native GPU when the search shape is GPU-native compatible
  - falls back to `GpuSidecar` for unsupported explicit GPU modes
- **Auto GPU routing:** `handle_auto_gpu_search(...)`
  - only attempted when the router already proved the search is GPU-native compatible
  - if CUDA is unavailable, falls back to `NativeCpuBackend` with `routing_reason = "gpu-auto-fallback-cpu"`
  - CPU fallback emits `requested_gpu_device_ids` for the user request and `routing_gpu_device_ids = []`; normal output and docs must call it CPU fallback, not GPU acceleration
  - if GPU initialization fails fatally, exits with a user-facing error instead of silently changing backends

## AST commands

`tg run` is policy-routed to `AstBackend` with `routing_reason = "ast-native"` by default.

However, **actual runtime native AST execution depends on `AstBackend().is_available()` in the environment.** If the required dependencies (like `torch-geometric` or `tree-sitter`) are not present or the environment lacks support, the router will automatically fall back to `AstGrepWrapperBackend` (the `ast-grep` CLI). Additionally, string-based metavariable queries (like `def $F($$$ARGS)`) that cannot be natively parsed as S-expressions will deliberately trigger this fallback.

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
