# Architecture

## Overview

`tensor-grep` (`tg`) is an agent-native code-search and retrieval tool. A compiled Rust core
(`rust_core/`) owns argument parsing, ripgrep-compatible text search, and the text-search router.
A Python CLI and backend layer (`src/tensor_grep/`) implements everything else: AST-aware
code-intelligence commands, session/daemon/checkpoint state, an MCP server, and several of the
search backends. Every `tg` invocation starts in the Rust binary; text search either resolves
natively there or is handed to the Python CLI as a subprocess ("sidecar"); most non-search
commands (sessions, checkpoints, MCP, the agent capsule, repo-map/symbol navigation) are
Python-only and reach Rust purely as a passthrough target.

See `docs/CONTRACTS.md` for the stability/compatibility guarantees and `docs/routing_policy.md`
for the maintained routing decision tree; this document points to both rather than duplicating
them.

## The two front doors

Every invocation starts in `main()` (`rust_core/src/main.rs:1228`), which runs the real work on a
dedicated thread via `main_inner()` (`main.rs:1238`). Before either front door is reached,
`main_inner` runs a chain of early, argv-shape passthrough checks -- bare `--help`/`--version`,
`--pcre2-version`, `--type-list`, an "early ripgrep passthrough", a "default search frontdoor
passthrough", and a check that forwards `--rank`/`--semantic`-style search flags straight to the
Python CLI (`main.rs:1241-1340`) -- any of which can resolve the whole invocation without ever
reaching a router. This is the literal mechanism behind the documented risk that a search flag
registered on only one front door silently misroutes to ripgrep.

Once those checks fall through, `should_use_positional_cli()` (`main.rs:4293`) decides between the
two front doors for text search:

- **Positional CLI** -- a bare `tg PATTERN [PATH]` invocation parses as `PositionalCli` and runs
  through `run_positional_cli()` (`main.rs:4104`), which calls `route_search` at `main.rs:4186`.
- **Command CLI** -- `tg search ...` (and every other `tg <command>`) parses as `CommandCli` and
  runs through `run_command_cli()` (`main.rs:3827`). `Commands::Search(args) =>
  handle_ripgrep_search(args)` (`main.rs:3829`, handler at `main.rs:5160`) first checks
  `search_prefers_ripgrep_passthrough()` (`main.rs:5202`) and, only if that does not already
  resolve the request, calls the same `route_search` at `main.rs:5271`.

Both front doors converge on the identical `route_search` function (`routing.rs:222`); they just
build a `SearchRoutingConfig` slightly differently (for example the positional front door has no
`--index` flag, so it always passes `explicit_index: false`).

Every other `Commands::*` variant -- `Session`, `Checkpoint`, `Map`, `Orient`, `Mcp`, `Agent`,
`Defs`, `Refs`, `Callers`, `Impact`, `BlastRadius`, `Rulesets`, `Doctor`, and more
(`main.rs:3831-3959`) -- never touches `route_search`. It is dispatched straight to
`handle_python_passthrough(command, args)` (`main.rs:8968`), which execs the Python CLI as a
subprocess ("sidecar") via the `tensor_grep.sidecar` module (`python_sidecar.rs:15-20`;
`TG_SIDECAR_PYTHON` / `TG_SIDECAR_TIMEOUT_MS` env overrides, 30s default timeout). `tg run` (AST
search/rewrite) is its own case: `Commands::Run(args) => handle_ast_run(args)` (`main.rs:3853`)
constructs `RoutingDecision::ast()` directly (`main.rs:7550`) rather than going through either
search front door above.

## Routing

`route_search` (`routing.rs:222-285`) resolves to one of six `BackendSelection` variants --
`NativeCpu`, `NativeGpu`, `TrigramIndex`, `AstBackend`, `Ripgrep`, `GpuSidecar`
(`routing.rs:2-9`) -- via an ordered chain of guard clauses, returning the first match:

1. PCRE2 requested and `rg` available -> `Ripgrep` (`routing.rs:230-232`)
2. Explicit `--index` -> `TrigramIndex` (`routing.rs:234-236`)
3. Explicit `--gpu-device-ids` -> `NativeGpu` (`routing.rs:238-240`)
4. `--force-cpu` -> `Ripgrep` or `NativeCpu` depending on output shape (`routing.rs:242-247`)
5. AST command -> `AstBackend` (`routing.rs:249-251`)
6. Warm, non-stale, pattern-compatible `.tg_index` -> `TrigramIndex` (`routing.rs:253-255`)
7. Corpus over the calibrated threshold with GPU available -> `NativeGpu`, else `NativeCpu`
   fallback (`routing.rs:257-270`)
8. `rg` available and no structured output required -> `Ripgrep` (`routing.rs:272-274`)
9. Structured (`--json`/`--ndjson`) output -> `NativeCpu` (`routing.rs:276-278`)
10. Final fallback -> `NativeCpu` (`routing.rs:280-284`)

That is ten ordered checks against the code as read, not nine: `docs/routing_policy.md`'s own
numbered decision tree omits the PCRE2 override at `routing.rs:230-232`. The backend count (six)
matches what `docs/routing_policy.md` documents. See that file for the full backend-inventory
table, GPU execution notes (`handle_gpu_search`/`handle_auto_gpu_search`), and AST fallback rules
-- this section only verifies the router's shape against the current code.

## Backends and the Fail-Closed Contract

The Backend Fail-Closed Contract is defined in `src/tensor_grep/backends/base.py` (22 lines
total):

```python
class BackendExecutionError(RuntimeError):
    """A search backend failed at runtime for a reason that is NOT an invalid regex.

    Covers native panics, encoding/IO errors, version skew, and GPU/CUDA/OOM faults.
    Backends MUST raise this instead of returning an empty ``SearchResult``, so a real
    failure is never reported to the user as a clean no-match; callers may catch it to
    retry on the CPU fallback (audit B2/I1).
    """
```

(`base.py:7-14`), together with the `ComputeBackend` protocol every backend implements --
`search(file_path, pattern, config) -> SearchResult` and `is_available() -> bool` (`base.py:17-22`).
The full compatibility and output-schema guarantees live in `docs/CONTRACTS.md`; this section only
names what exists.

`src/tensor_grep/backends/` has ten modules (`backends/__init__.py:5-20` is the lazy-export map):

- `ripgrep_backend.py` -- `RipgrepBackend`, shells out to the `rg` binary and decodes its
  `--json` event stream, including base64 `.bytes` fields for non-UTF-8 content
  (`ripgrep_backend.py:13-30`).
- `cpu_backend.py` -- `CPUBackend`, the in-process Python regex engine, wall-clock deadline-bound
  via `compute_native_walk_deadline()` (`cpu_backend.py:22-30`).
- `rust_backend.py` -- `RustCoreBackend`, "a Python wrapper implementing the ComputeBackend
  interface around the PyO3 Rust extension" (`rust_backend.py:28-29`); `HAVE_RUST` reports whether
  the compiled extension is importable.
- `stringzilla_backend.py` -- `StringZillaBackend`, SIMD literal-string matching via the
  StringZilla C++ library, used for fixed-string (`-F`) searches (`stringzilla_backend.py:16-21`).
- `ast_backend.py` / `ast_wrapper_backend.py` -- the native tree-sitter AST backend and its
  `ast-grep` CLI-wrapper fallback; see "AST layer" below.
- `torch_backend.py` -- `TorchBackend`, "CUDA fallback backend for systems without cuDF. Uses
  tensor operations for literal substring matching on GPU" (`torch_backend.py:12-16`).
- `cudf_backend.py` / `cybert_backend.py` -- experimental, hardware/dependency-gated backends
  selected only by the Python `Pipeline` class (`src/tensor_grep/core/pipeline.py`), never by the
  Rust router. `Pipeline` raises its own `ConfigurationError` with the literal message "GPU
  acceleration is experimental" whenever an explicit `--gpu-device-ids` request cannot get a
  working GPU backend (`pipeline.py:32-39`), and its default path is `rg_default_fast_path` --
  "always delegate to native rg for best end-to-end CLI speed" (`pipeline.py:356-359`).
  `CybertBackend` is selected only when the small keyword-matching `QueryAnalyzer`
  (`src/tensor_grep/core/query_analyzer.py:4-23`, no NLP model of its own -- it substring-matches
  against `["classify", "detect", "extract entities", "anomaly"]`) classifies the query as
  `QueryType.NLP` (`pipeline.py:241-256`), or via the explicit opt-in
  `TENSOR_GREP_CLASSIFY_PROVIDER=cybert` on `tg classify` (`docs/CONTRACTS.md`, section 3).
  `Pipeline` is real, reachable code -- from `sidecar.py:450`, `mcp_server.py:3215`/`3486`,
  `cli/main.py:5603-5617`/`6578`, and `cli/ast_workflows.py:990-992` -- it is simply not the
  default path and not wired into the Rust router.

`docs/EXPERIMENTAL.md` separately catalogs the hidden/opt-in surfaces (`tg worker`,
`TG_RESIDENT_AST`, and the `TG_FORCE_CPU` / `TG_RUST_FIRST_SEARCH` / etc. backend-override env
vars) that sit outside the stability contract.

## AST layer

AST parsing is split by language:

- Python files are parsed with the standard-library `ast` module (`cli/repo_map.py:3`), not
  tree-sitter.
- JavaScript, TypeScript, and Rust are parsed with real tree-sitter grammars --
  `tree_sitter_javascript`, `tree_sitter_typescript`, `tree_sitter_rust` -- loaded through
  `tree_sitter.Language` / `tree_sitter.Parser` (`cli/repo_map.py:1607-1642`). Additional
  languages are handled by `cli/lang_registry.py` and `cli/lang_go.py`.
- `backends/ast_backend.py` is the native structural-search/rewrite backend that `tg run` targets
  by policy (`RoutingDecision::ast()`, reason `ast-native`, `routing.rs:181-183`). Per
  `docs/routing_policy.md`, when `AstBackend().is_available()` is false (missing tree-sitter /
  torch-geometric deps) or the pattern needs string-based metavariable matching, it falls back to
  `backends/ast_wrapper_backend.py`, which wraps the external `ast-grep` CLI as a subprocess
  (`ast_wrapper_backend.py:4` imports `subprocess`) and treats per-path I/O warnings during a scan
  as non-fatal rather than aborting the whole run (`ast_wrapper_backend.py:16-29`).

This layer is the shared engine behind `tg run` (structural search/rewrite) and, via
`cli/repo_map.py`'s `build_symbol_defs` / `build_symbol_refs` / `build_symbol_callers` /
`build_symbol_impact` / `build_symbol_blast_radius*` functions (imported by both
`session_store.py:18-37` and `mcp_server.py:39-53`), the `tg defs` / `tg refs` / `tg callers` /
`tg impact` / `tg blast-radius` code-intelligence commands.

## Session, daemon, and checkpoint layer

Three cooperating stores live under a project's `.tensor-grep/` directory:

- `cli/session_store.py` persists repo-map-derived sessions under `.tensor-grep/sessions/`
  (`session_store.py:42-43`) and wraps the `repo_map` builders -- context-render, edit-plan,
  blast-radius, defs/refs/callers/impact (`session_store.py:18-37`) -- so repeat agent-loop calls
  reuse a warm scan instead of rescanning. Retention is bounded: at most `TG_SESSION_MAX` (default
  64) sessions per root (`session_store.py:53-55`).
- `cli/session_daemon.py` is a local `socketserver`-based daemon bound to `127.0.0.1`
  (`session_daemon.py:50`) that serves session requests from a response cache, authenticated with
  HMAC (`hashlib`/`hmac`/`secrets` imports, `session_daemon.py:5-9`). It is what `--daemon`-routed
  session/context-render/edit-plan calls hit for the warm path described in `docs/CONTRACTS.md`.
- `cli/checkpoint_store.py` snapshots a scope into `.tensor-grep/checkpoints/` before a risky edit
  and supports undo (`checkpoint_store.py:20-21`); it raises `CheckpointCorruptError` when a
  snapshot blob is missing or unreadable rather than silently no-op'ing an undo
  (`checkpoint_store.py:54-60`). Retention is bounded by `TG_CHECKPOINT_MAX` (default 64,
  `checkpoint_store.py:32-33`).

Both `tg session ...` and `tg checkpoint ...` are Python-only CLI verbs -- the Rust front door
dispatches them straight to the Python passthrough (`Commands::Session`, `main.rs:3929`;
`Commands::Checkpoint`, `main.rs:3932`) rather than through `route_search`.

## MCP server

`cli/mcp_server.py` runs a `FastMCP("tensor-grep")` server (`mcp_server.py:82`) at a pinned
contract version (`_TG_MCP_SERVER_CONTRACT_VERSION = "1.0.0"`, `mcp_server.py:74`) exposing 45
`@mcp.tool()`-decorated functions. They group into:

- repo-map/context: `tg_repo_map` (`mcp_server.py:1819`), `tg_context_pack` (`1865`),
  `tg_edit_plan` (`1910`), `tg_context_render` (`1981`), `tg_agent_capsule` (`2056`)
- symbol navigation: `tg_symbol_defs` (`2664`), `tg_symbol_source` (`2717`), `tg_symbol_impact`
  (`2770`), `tg_symbol_refs` (`2820`), `tg_symbol_callers` (`2873`), `tg_symbol_blast_radius`
  (`3011`)
- session-scoped equivalents: `tg_session_edit_plan` (`2135`), `tg_session_context_render`
  (`2208`), `tg_session_open` (`4361`), `tg_session_list` (`4400`)
- search: `tg_search` (`3153`), `tg_ast_search` (`3464`), `tg_index_search` (`3851`)
- rewrite: `tg_rewrite_plan` (`3880`), `tg_rewrite_apply` (`3904`), `tg_rewrite_diff` (`4535`)
- rulesets/scan: `tg_rulesets` (`1665`), `tg_ruleset_scan` (`1697`)
- audit/review: `tg_audit_manifest_verify` (`3985`), `tg_audit_history` (`4036`),
  `tg_review_bundle_create` (`4109`)
- checkpoints: `tg_checkpoint_create` (`4258`), `tg_checkpoint_list` (`4292`),
  `tg_checkpoint_undo` (`4325`)
- plus `tg_classify_logs` (`3733`), `tg_devices` (`3826`), and `tg_mcp_capabilities` (`1459`)

`tg mcp` (`Commands::Mcp`, `main.rs:3834`) launches this server through the same Python
passthrough as `session`/`checkpoint`.

## Agent Capsule, orient, and repo-map

`cli/repo_map.py` (about 15,800 lines) is the shared retrieval engine underneath nearly everything
above: it builds the import/symbol graph (`build_repo_map`) and exposes the `build_symbol_*` /
`build_context_*` / `build_file_importers` functions that `session_store.py`, `mcp_server.py`, and
`orient_capsule.py` all import.

Two token-budgeted, agent-facing surfaces sit on top of it:

- **`tg orient`** (`cli/orient_capsule.py`) is a one-call codebase-orientation capsule, "Pure-CPU,
  no API key, no GPU" (`orient_capsule.py:1-6`). `build_orient_capsule()`
  (`orient_capsule.py:199-298`) ranks files by a composite import-graph centrality score (in-degree
  capped, plus fan-out and symbol density -- `orient_capsule.py:102-120`), heuristically detects
  entry points by filename (`orient_capsule.py:47-66`, `141-147`), and returns AST-boundary
  snippets within a `max_tokens` budget.
- **`tg agent`** (`cli/agent_capsule.py`, about 2,300 lines) builds the "Actionable Context
  Capsule" (`capsule_version = 1`) documented in `docs/CONTRACTS.md` (capsule contract paragraph,
  `CONTRACTS.md:99`): primary file/function, alternative targets, route rationale, bounded source
  snippets, validation-command evidence, risk/edit order, checkpoint/rollback metadata, and an
  explicit `ask_user_before_editing` flag when confidence is low. Both commands reach Rust via the
  same Python-passthrough mechanism as the rest of the non-search commands (`Commands::Orient` /
  `Commands::Agent`, `main.rs:3926`/`3947`).

This bounded, ranked, AST-aware context layer -- not a GPU/NLP pipeline -- is tensor-grep's actual
token-economy pitch to agent callers.
