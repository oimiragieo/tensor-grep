"""Construct the root app before main registers its commands."""

import typer


def create_root_app() -> typer.Typer:
    return typer.Typer(
        help="""tensor-grep (tg) - Fast text, AST, indexed, and GPU-aware search CLI

Search code and large datasets with ripgrep-compatible text search, native AST search/rewrite,
persisted repeated-query acceleration, and optional GPU routing.

**Common usage**
- `tg PATTERN [PATH ...]`
- `tg search [OPTIONS] PATTERN [PATH ...]`
- `tg run PATTERN [PATH]`
- `tg agent PATH "change invoice tax"`
- `tg scan --config sgconfig.yml`
- `tg doctor --with-lsp`
- `tg dogfood --output artifacts/dogfood_readiness.json`
- `tg repair-launcher`
- `tg mcp`

**AI workflows**
- `tg map PATH`
- `tg context-render PATH "invoice flow"`
- `tg edit-plan PATH "add retry with tests"`
- `tg agent PATH "change behavior" --json`
- `tg blast-radius PATH create_invoice --json`  (caller graph; `blast-radius-render` = prose bundle)
- `tg session open PATH`
- `tg session daemon start PATH`

**Agent contracts**
- `tg agent` emits primary targets, alternative targets, snippets, validation_commands, rollback metadata, confidence, optional gpu_acceleration route evidence, and ask-before-editing guidance.
- `tg agent --gpu-device-ids 0,1 --json` runs an opt-in native GPU evidence scan; sidecar-routed GPU results are reported as unsupported.
- `context-render` and `edit-plan` also expose top-level validation_commands.
- Validation command templates can quote `$file` or `{file}` placeholders; the command is split into a program and arguments and spawned directly (no shell), so the file path is passed as a single argument and shell constructs (pipes, `&&`, redirects, `cmd`/`sh` builtins) are not interpreted. Applied rewrites run placeholder commands once per edited file.

**Search and safety**
- Use `--format rg --sort path` for deterministic ripgrep-shaped text output.
- The search surface is a validated common rg-compatible subset, not a full ripgrep replacement.
- Use `--format rg --json` for ripgrep JSON Lines events; plain `--json` is tensor-grep aggregate JSON.
- Direct generated-root, broad file-list, and multi-project workspace-root scans are refused unless scoped with paths, `--glob`, `--type`, `--max-depth`, or explicit `--allow-broad-generated-scan`; project-root `--no-ignore` content searches follow ripgrep.
- On Windows, PowerShell double quotes expand $NAME before `tg` receives literal patterns; use single quotes or escape `$`. In `cmd.exe`, quote or caret-escape metacharacters such as `|` and `&`.
- `--smart-case`, `--hidden`, `--max-depth`, and `--text` are honored by structured CPU and sidecar search; native GPU falls back when a requested switch changes semantics it cannot safely execute yet.
- `--gpu-device-ids` pins selected GPUs for explicit search, benchmark, and agent evidence probes; GPU remains experimental until 1GB/5GB correctness and speed beat both `rg` and `tg_cpu`.
- `classify` is local by default; set `TENSOR_GREP_CLASSIFY_PROVIDER=cybert` to opt into CyBERT/Triton.

**Notes**
- Bare patterns and option-first common search flags are treated as `tg search`, including `tg -t js PATTERN PATH` and `tg --count-matches PATTERN PATH`.
- Use `tg search --help` for the current validated rg-compatible flag subset.
- `tg run --help` for AST rewrite flags.
- Lexical repo-map retrieval bridges camelCase, snake_case, and source-term planning queries.
- Use `tg doctor --json` for system, GPU, cache, daemon, and launcher diagnostics including path_tg_first_launcher_kind and fresh_shell_path_tg_first_launcher_kind.
- Use `tg repair-launcher` to remove verified or self-identifying tensor-grep Python Scripts launchers that shadow the managed native front door; add `--allow-foreign-rename` only for a foreign `tg.exe` that you own and want tensor-grep to back up.
- Use `tg session --help` for cached edit-loop and daemon commands; daemon-routed edit-plan/context requests keep a short connect probe, a longer work response timeout, and byte-bounded response-cache stats.

**Environment overrides**
- `TG_SIDECAR_PYTHON`: Path to the Python executable used for sidecar-backed commands.
- `TG_NATIVE_TG_BINARY`: Path to the native front door used by Python-backed commands.
- `TG_FRONTDOOR_HOPS`: Native/Python front-door hand-off depth (internal; absent means 0). A door refuses with exit 2 at 4 hops to stop a routing loop.
- `TENSOR_GREP_NATIVE_FRONTDOOR_FLAVOR`: Set to `nvidia` to prefer NVIDIA release-native front-door assets, with CPU fallback.
- `TG_RG_PATH`: Path to the ripgrep executable used for text-search passthrough.
- `TG_FORCE_CPU`: Force CPU routing for search commands.
- `TG_SIDECAR_TIMEOUT_MS`: Timeout for sidecar-backed commands.
- `TG_HELP_PROBE_TIMEOUT_MS`: Timeout for the native front door's `--help` passthrough probe to this rich Python help before it falls back to the condensed native help (default 3000ms).
- `TENSOR_GREP_DEVICE_IDS`: Comma-separated GPU IDs available to tensor-grep.
- `TENSOR_GREP_CLASSIFY_PROVIDER`: Set to `cybert` to opt into CyBERT/Triton classification.
- `TENSOR_GREP_TRITON_TIMEOUT_SECONDS`: Timeout for Triton-backed NLP probes.
- `TG_MCP_ALLOW_VALIDATION_COMMANDS`: Set to `1` to let the `tg mcp` server's `tg_rewrite_apply` tool accept and shell-execute `lint_cmd` / `test_cmd`; default off (such requests are rejected with `code="unsupported_option"`).
- `TENSOR_GREP_LSP_OPERATION_BUDGET_SECONDS`: Total per-command budget for optional external LSP provider requests before native fallback.
- `TENSOR_GREP_CPU_LITERAL_INDEX_CACHE_MAX_ENTRIES`, `TENSOR_GREP_STRING_INDEX_CACHE_MAX_ENTRIES`, `TENSOR_GREP_AST_QUERY_CACHE_MAX_ENTRIES`, `TENSOR_GREP_AST_NODE_INDEX_CACHE_MAX_ENTRIES`, `TENSOR_GREP_REPO_CONTEXT_CACHE_MAX_ROOTS`: Bound long-lived in-process search and repo-context caches.
- `TENSOR_GREP_SESSION_RESPONSE_CACHE_MAX_BYTES`, `TENSOR_GREP_LSP_PROVIDER_CLIENT_CACHE_MAX_ENTRIES`, `TENSOR_GREP_LSP_PROVIDER_OPEN_DOCUMENT_MAX_ENTRIES`: Bound agent-loop response and LSP provider caches.
- `TG_SESSION_DAEMON_AUTOSTART`: Default-ON warm-daemon fast path for `defs`/`impact`/`refs`/`callers`/`blast-radius` (probes a running `tg session daemon`; auto-spawns one non-blocking on a miss, so only the first call per root pays the cold-start cost). Set to `0`/`false`/`no`/`off` to opt back out to the always-cold path; always forced off when `CI` or `GITHUB_ACTIONS` is set. Querying N distinct repo roots with this on can leave up to N resident daemons; each self-shuts-down after `TG_SESSION_DAEMON_IDLE_SECONDS` (900s default) of inactivity.""",
        no_args_is_help=True,
        add_completion=True,
        rich_markup_mode="markdown",
    )
