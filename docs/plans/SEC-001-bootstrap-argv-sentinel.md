# SEC-001 — Bootstrap native search `--` sentinel

**Status:** IMPLEMENTED (pending PR + A3 gate)  
**Slice:** SEC-001-SPEC  
**HEAD at authoring:** `8a879b286a470866dec12ba6aac80ff80aa61adc`

## Problem

`bootstrap._run_native_tg_search` forwarded flat `search_args` to the managed native
`tg search` subprocess without an end-of-options sentinel. Dash-leading patterns/paths
(e.g. `-i`, `-r`) are parsed as flags by the native clap front door, producing silent
wrong-scope scans (CWE-88 / MCP-276 class). `main._build_native_tg_search_command`
already emits an unconditional `--` at `main.py:895`.

## Seam claims (verified)

| Claim | Evidence |
|---|---|
| Bootstrap passthrough lacked sentinel | `bootstrap.py:_run_native_tg_search` (pre-fix) |
| Main builder has sentinel | `main.py:895` `command.append("--")` |
| Census omitted bootstrap | `test_argv_sentinel_covers_every_builder.py` population |
| Flag walk for positionals | `bootstrap.py:_search_path_args_raw` / `_search_args_contains_pattern_source_flag` |

## Fix

1. Add `_sentinel_insertion_index` — dash-led caller positionals only (bootstrap flat argv
   may carry trailing search flags like `--count-matches` after paths; unconditional insert
   would strand those flags after `--`).
2. Helpers: `_first_dash_led_pattern_index_after_tg_flags` (injection tails like
   `--json -i -r`, `--cpu -pattern src`) and `_first_dash_led_positional_index`.
3. Add `_bootstrap_native_tg_search_argv` — insert `--` at the computed index unless argv
   already contains `--`.
4. `_run_native_tg_search` uses the builder output.

## Tests

- `test_argv_sentinel_covers_every_builder.py` — new population member for bootstrap builder.
- `test_cli_bootstrap.py` — unit tests for argv shape + `_run_native_tg_search` capture.

## Out of scope

- `rust_core` rg passthrough (Rust-side guard).
- SEC-002..012.
- Handler census / format hygiene slices.

## Merge gates

- Local: ruff, ruff format --preview, mypy, targeted pytest.
- PR CI full matrix.
- A3 adversarial security gate before merge.
- Codex Sol `audit-until-clear` on PR head SHA (tier-3, pre-merge).
