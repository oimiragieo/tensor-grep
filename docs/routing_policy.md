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
| `NativeCpuBackend` | `force_cpu`, `json_output`, `plain-text-native`, `gpu-auto-fallback-cpu`, `rg_unavailable` | Native Rust text search is used for structured explicit `--cpu`, JSON/NDJSON output, the admitted plain-text subset (see below), GPU fallback, and when `rg` is unavailable. |
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
7. Otherwise, if `rg` is available and structured output is not required **and the request is not
   an admitted plain-text native request** -> `RipgrepBackend`
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
- `rg` is again the normal cold-path choice for generic text search when available. Native CPU remains the default only for structured outputs, explicit `--cpu`, warm index, AST, GPU fallback, and the admitted plain-text subset described below.

### Admitted plain-text native subset (perf: skip the `rg` subprocess)

Spawning `rg` costs a fixed process round trip on every plain-text search. `native_can_serve_plain_text` (`rust_core/src/routing.rs`) is the single, fail-closed predicate that decides when the in-process native CPU engine may answer instead. It is deliberately narrow, and every clause is a refusal:

- Exactly one pattern, and that pattern is not the empty string (`run_native_search` rejects an empty pattern, and the rg fallback then emits a `warning:` line `rg` never prints).
- That pattern is **native-renderable**: it contains no line terminator or NUL (literal or escaped) and it compiles with the native matcher. `rg` refuses a pattern that can match a line terminator (`needle\n`, `\n`, `[\n]`) or a NUL (`\x00`) with **exit 2 plus a diagnostic**, while the native matcher accepts it and succeeds with **zero matches (exit 1, silent)** -- an exit-code regression an agent branching on 2-vs-1 would misread as "no matches". A pattern that fails to compile (`[`, `(`, `\Qx\E`, `a{500}{500}{500}`) exits 2 either way, but only after the rg-fallback net prints a `warning: native CPU search failed...` line. The check is deliberately over-broad (it also refuses `\x`/`\u` escapes wholesale): over-refusal costs one `rg` spawn, under-refusal costs correctness.
- Exactly one PATH operand that resolves to an existing **regular file** (never a directory -- walking diverges from `rg` on binary-file messages and on emission order).
- That file passes a **full-content** probe: no `\r` byte, valid UTF-8, no NUL byte, and within a 512 KiB cap. These were originally DATA-level divergences in the shared native emitter; **task 266/263 fixed all three in the emitter itself** (`native_search.rs`'s `strip_native_line_terminator`, `native_json_text_fields`, and `emit_binary_match_warning`). This probe clause still refuses all three cases as a deliberate conservative margin -- relaxing it to reclaim the perf win on CRLF/non-UTF-8/binary content is a disclosed, not-yet-done follow-up, not a live correctness gap:
  - CRLF: the native plain sink used to strip the trailing `\r` (no CRLF line terminator is ever installed on this path) where `rg` keeps it.
  - non-UTF-8: the native plain sink used `grep_searcher::sinks::Lossy` and substituted U+FFFD where `rg` writes raw bytes.
  - NUL: `rg` spells its binary-match notice `"\0"` where the native engine used to spell it `"/0"`.
- The PATH was supplied explicitly (an implicit path makes `rg` print `name` where the native engine prints `./name`).
- The PATH is **not** `-`. That is ripgrep's stdin sentinel, but `Path::new("-").is_file()` is true whenever a file literally named `-` exists in the working directory, so the native engine would search that file while `rg` searched stdin -- exit 0, no stderr, plausible output, wrong data source. Also covers `-- -` and `-e PATTERN -`.
- The file's reported size equals the bytes actually read. This refuses pseudo-files such as `/proc/self/status`, which is a regular file with `st_size` 0 that returns ~1.4 KB of clean UTF-8 on read: its content is generated per-open, so the probe cannot describe what the search will subsequently read. (`/dev/*` and FIFOs are already refused for not being regular files; a symlink to a regular file is admitted and correct, since both engines follow an explicit symlink operand.)
- stdout is **not** a terminal (`rg` is spawned with inherited stdio, so on a terminal it renders its grouped/heading layout with color).
- `$RIPGREP_CONFIG_PATH` is **not** set to a non-empty value. This is the one clause about the process ENVIRONMENT rather than the request: `execute_ripgrep_search` never clears the environment and passes `--no-config` only when the user asks for it, so a plain-text search that reaches `rg` today applies the user's rg config, while the in-process native engine reads no rg config at all. A config containing `-i` changes which lines match, `--vimgrep` changes the entire output format, and a dangling path makes `rg` print a read-failure diagnostic the native route would omit. An empty value is ignored by `rg`, so the guard is "set AND non-empty".
- No `--json`, `--ndjson`, or `--format`.
- Every flag on the command line is in `PLAIN_TEXT_NATIVE_ALLOWED_FLAGS`: `-i`/`--ignore-case`, `-F`/`--fixed-strings`, `-w`/`--word-regexp`, `-n`/`--line-number`, `--verbose` (combined short clusters such as `-in` are accepted, matching clap).

Anything outside that subset keeps spawning `rg`, unchanged. The route reports `NativeCpuBackend` / `plain-text-native` and keeps `allow_rg_fallback = true`, so a native failure still falls back to real `rg`. `--verbose` stderr intentionally reports the new backend -- that is the flag's purpose.

The 512 KiB cap is measured, not guessed: the probe costs ~2% of the win at 200 KB, 16% at 1 MB, 43% at 4 MB and 78% at 8 MB, and on a match-dense 8 MB file the native engine is itself slower than `rg`, turning the whole route into a ~11ms regression. Files above the cap keep spawning `rg`.

The two expensive clauses (the pattern compile and the file probe) are evaluated **last**, only after `plain_text_native_cheap_checks_pass` has cleared every free refusal. That is a latency contract: an interactive terminal search, a `--json`/`--ndjson` run and an `-A`/`-B`/`-C` run all reach the clap-side adapter and must not pay for a file read on their way to `rg`. The probe is also memoized per path, because both adapters run on an admitted request.

### Admission-rate telemetry (default-OFF)

Nothing else in the repo can answer "how often is this route actually taken?" -- none of the benchmark scenarios, none of the dogfood calls, and none of the MCP surface (which always builds `--json`) are eligible for it, so the benchmark-regression gate can observe neither its benefit nor a future regression in it.

Set `TG_ROUTE_TELEMETRY=1` and `tg` appends one JSON Lines record per eligibility evaluation, carrying the stage (`frontdoor` or `clap`) and every clause. The file defaults to the OS temp directory (never the workspace) and is overridable with `TG_ROUTE_TELEMETRY_PATH`. Emission is best-effort and fail-silent: telemetry must never change what a search returns.

Any existing workload works unmodified:

```bash
TG_ROUTE_TELEMETRY=1 TG_ROUTE_TELEMETRY_PATH=/tmp/route.jsonl uv run python benchmarks/run_benchmarks.py
python scripts/summarize_route_telemetry.py /tmp/route.jsonl
```

The summary reports the admit rate per stage and a histogram of which clause refused first. The `clap` stage is the real admission surface, since an admitted request always reaches it.

### Broken-pipe exit-code change (all native routes)

A consumer that closes the pipe early (`tg ... | head -1`, `| less`, an agent reading N lines) is normal termination, not a search failure. Previously the native engine surfaced it as an error: exit **2** plus `native standard output search failed for <path>` on stderr, and -- where an rg fallback was configured -- a `warning: native CPU search failed...` line followed by a full re-run of the search into the already-closed pipe. It now exits quietly with ripgrep's code (**1** for the single-file shape this route admits).

This is a **contract change on a machine-facing surface**: `--json` and `--ndjson` consumers that closed the pipe early previously saw exit 2 and now see exit 1, with no structured error emitted (there is no longer anywhere to emit it). The change applies to every native route -- `--cpu`, `--json`/`--ndjson`, rg-unavailable, and the plain-text route -- because a broken pipe was never a search failure on any of them.

The predicate has two adapters (a raw-argv one at the pre-clap front door, a parsed-`SearchArgs` one on the clap path) and they are required to return identical verdicts for every shape the agreement test lists, because the front door is not the only path into `route_search`. The general invariant is one-directional -- the front door may be stricter, never looser -- and attached-value short spellings (`-eneedle`) are a known, deliberate asymmetry in that safe direction.

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
  built with (the `no_ignore` byte was introduced in `INDEX_FORMAT_VERSION` 4; the current format
  version is higher -- readers/writers derive it from the constant, never a literal). A query whose
  `--no-ignore` request disagrees with the
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
| Unknown top-level command refusal (A90) | Python `bootstrap._top_level_command_refusal` / native `top_level_unknown_command_refusal` + shared `python_set_members` |

## Unknown top-level commands (A90) — the dispatch contract

An unknown top-level command must **never** be swallowed into `tg search` (which would fake a
nonexistent command's existence at exit 0). The refusal surface and its boundaries:

- **Refuses (exit 2, stderr-only, `error.code=unknown_command` on the JSON path):**
  - a RESERVED (roadmap, not-yet-registered) top-level command followed by any flag
    (`tg edit-ready --json`), and
  - any unknown first arg followed by `--help`/`-h` (a nonexistent command has no help).
- **Stays search:** bare `tg PATTERN`, `tg PATTERN PATH`, unreserved pattern+flag
  (`tg hello --json`), reserved+positional with no flag (`tg edit-ready docs/`), and any
  dash-first invocation (`tg -V`, `tg --bogus --help`). The escape hatch is the explicit
  `tg search <pattern> ...` form.
- The reserved set (`RESERVED_TOP_LEVEL_COMMANDS` in `src/tensor_grep/cli/commands.py`:
  `edit-ready`, `verify-edit`, `workspace`) is roadmap-owned; a reserved name is NOT a registered
  command (`RESERVED ∩ KNOWN == ∅`, lifecycle-pinned), and is removed from reserved when the
  command is registered.
- Known/nearest membership is extracted SCOPED to each set block, quote/escape-aware, by the
  shared `python_set_members` helper (native) — never a bare quoted-literal scan. `nearest[]`
  uses Levenshtein distance <= 3, hides internal `__` names, caps at 5.

This policy is enforced by `tests/e2e/test_routing_parity.py`
(`test_unknown_command_refusal_parity_across_launchers`, 15 rows), the Python unit
`_top_level_command_refusal` tests, and the native `top_level_unknown_command_refusal` tests.
