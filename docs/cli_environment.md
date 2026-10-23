# CLI environment settings

`tg --help` stays focused on common commands. This page preserves the environment settings that
used to be listed in root help. The setting takes effect only for the entry point or feature named.

| Setting | Behavior |
|---|---|
| `TG_SIDECAR_PYTHON` | Python executable used for sidecar-backed commands. |
| `TG_NATIVE_TG_BINARY` | Native front door used by Python-backed commands. |
| `TG_FRONTDOOR_HOPS` | Internal native/Python hand-off depth; absent means 0. A door refuses delegation with exit 2 at four hops or for malformed values, preventing a routing loop. |
| `TENSOR_GREP_NATIVE_FRONTDOOR_FLAVOR` | Set to `nvidia` to prefer NVIDIA release-native assets, with CPU fallback. |
| `TG_RG_PATH` | Ripgrep executable used for text-search passthrough. |
| `TG_FORCE_CPU` | Forces CPU routing for search commands. |
| `TG_SIDECAR_TIMEOUT_MS` | Sidecar timeout in milliseconds; the native default is 30,000 ms. |
| `TG_HELP_PROBE_TIMEOUT_MS` | Timeout in milliseconds for the native front door's `--help` probe. The default is 3,000 ms; if the probe times out, the native front door renders its condensed fallback help. |
| `TG_PASSTHROUGH_TIMEOUT_MS` | Timeout for one-shot Python passthrough commands; defaults to 600,000 ms. It does not apply to `mcp`, `session serve`, or `lsp server` launches. |
| `TG_LSP_PROVIDER` | Overrides the semantic LSP provider mode; defaults to `native` and accepts experimental modes such as `hybrid`. Provider availability alone does not prove LSP behavior. |
| `TENSOR_GREP_DEVICE_IDS` | Comma-separated GPU IDs available to tensor-grep. |
| `TENSOR_GREP_CLASSIFY_PROVIDER` | Set to `cybert` to opt into CyBERT/Triton classification. Classification otherwise uses local heuristics by default. |
| `TENSOR_GREP_TRITON_TIMEOUT_SECONDS` | Timeout for Triton-backed NLP probes. |
| `TG_MCP_ALLOW_VALIDATION_COMMANDS` | Defaults off. Set to `1` in the MCP server environment to allow `tg_rewrite_apply` to accept and shell-execute `lint_cmd` and `test_cmd`; otherwise these requests are rejected with `code="unsupported_option"`. |
| `TENSOR_GREP_LSP_OPERATION_BUDGET_SECONDS` | Total per-command time budget for optional external LSP provider requests before native fallback. |
| `TENSOR_GREP_CPU_LITERAL_INDEX_CACHE_MAX_ENTRIES`, `TENSOR_GREP_STRING_INDEX_CACHE_MAX_ENTRIES`, `TENSOR_GREP_AST_QUERY_CACHE_MAX_ENTRIES`, `TENSOR_GREP_AST_NODE_INDEX_CACHE_MAX_ENTRIES`, `TENSOR_GREP_REPO_CONTEXT_CACHE_MAX_ROOTS` | Bound long-lived in-process search and repository-context caches. |
| `TENSOR_GREP_SESSION_RESPONSE_CACHE_MAX_BYTES`, `TENSOR_GREP_LSP_PROVIDER_CLIENT_CACHE_MAX_ENTRIES`, `TENSOR_GREP_LSP_PROVIDER_OPEN_DOCUMENT_MAX_ENTRIES` | Bound agent-loop response and LSP provider caches. |
| `TG_SESSION_DAEMON_AUTOSTART` | The warm-daemon path is on by default for `defs`, `impact`, `refs`, `callers`, and `blast-radius`. It probes for a running `tg session daemon`, then starts one without blocking on a miss. Set `0`, `false`, `no`, or `off` for the cold path. It is always off when `CI` or `GITHUB_ACTIONS` is set. Each daemon shuts down after `TG_SESSION_DAEMON_IDLE_SECONDS` of inactivity (900 seconds by default). |
| `TG_SESSION_DAEMON_IDLE_SECONDS` | Idle lifetime in seconds before a session daemon shuts down; defaults to 900 seconds. |

For routing and installation context, see [installation](installation.md), [routing policy](routing_policy.md),
[GPU troubleshooting](runbooks/gpu-troubleshooting.md), and [contracts](CONTRACTS.md).
