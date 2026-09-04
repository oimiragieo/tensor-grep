# MAP — SEC-007-MCP-SANITIZE: MCP Wire Error Sanitization and Leak Prevention

## Destination
Eliminate all un-sanitized raw `str(exc)` exposures on the MCP JSON-RPC wire in `src/tensor_grep/cli/mcp_server.py`, preventing absolute filesystem path, credential, and internal trace leaks to AI agent callers while preserving all structured error envelope keys, codes, and fail-closed contracts.

## Open questions

<!-- zero open questions: all answered -->

## Answers

### Q1: What is the exact scope and classification of raw str(exc) sites on the MCP wire?
**Answer:** Across all three registered MCP tool modules (`src/tensor_grep/cli/mcp_server.py`, `src/tensor_grep/cli/mcp_symbol_tools.py`, `src/tensor_grep/cli/mcp_audit_tools.py`), an audited population was reduced to exactly 53 closed-world authorized sites:
1. **Eliminated across 3 modules:**
   - 32 Class A broad `except Exception` internal error arms routed to `_sanitized_tool_error` / constant sanitized payloads, with full traceback and path details sent exclusively to `stderr`.
   - All narrow exception arms (`SessionStaleError`, `FileNotFoundError`, `ValueError`, `ConfigurationError`) sanitized to return safe client messages while logging full details to `stderr`.
2. **Authorized Closed-World Remaining (53 sites):**
   - `mcp_server.py`: 27 sites (26 `PathConfinementError` + 1 W1-a `tracked_file_count_error` detail key in `tg_session_open`).
   - `mcp_symbol_tools.py`: 11 sites (all `PathConfinementError`).
   - `mcp_audit_tools.py`: 15 sites (all `PathConfinementError`).
   - Total = 53 sites (52 `PathConfinementError` + 1 W1-a detail key).
   - Exactly 0 unauthorized sites across all 3 modules.
**Why:** Eliminates raw `str(exc)` tracebacks, local paths, and secret leaks while strictly preserving structured error envelope keys, codes, and fail-closed contracts.
**Check:** python -c "import ast; from pathlib import Path; d = Path('src/tensor_grep/cli'); mods = ['mcp_server.py', 'mcp_symbol_tools.py', 'mcp_audit_tools.py']; total = sum(len([n for n in ast.walk(ast.parse((d/m).read_text(encoding='utf-8'))) if isinstance(n, ast.Call) and isinstance(n.func, ast.Name) and n.func.id == 'str' and len(n.args) == 1 and isinstance(n.args[0], ast.Name) and n.args[0].id in ('exc', 'e')]) for m in mods); assert total == 53; print('53 AST str(exc) sites mapped across 3 modules')"
**Judged by:** run it
**Reference:** docs/plans/HANDLER-CENSUS-W2.md:64

### Q2: What is the sanitization architecture for each class?
**Answer:**
1. **Class A (32 broad arms across 3 modules):** Routed through `_sanitized_tool_error(tool_name, exc)` (or sanitized envelope helper), logging full tracebacks and paths server-side to `stderr` via `_log_tool_exception` and returning client-safe generic internal error messages identifying only the tool and exception class name.
2. **Class B (confinement & narrow arms):** Upstream path confinement in `_confine_read_path` and `_confine_write_path` raises structured `PathConfinementError(ValueError)` with constant public message `"{label} must stay within the MCP root (refused)"` and logs the absolute resolved path to `stderr`. All 39 path confinement entrypoints redact candidate path fields (`path: "[refused]"`, `file: "[refused]"`). Fallback `Path.cwd()` in `_mcp_root()` is enclosed and translates unexpected failures to `PathConfinementError("root")`. Narrow exception handlers (`SessionStaleError`, `FileNotFoundError`, `ValueError`, `ConfigurationError`) return constant sanitized messages.
3. **Class C (2 backend arms):** Preserve curated backend execution error contracts in `tg_find` and `tg_search` using `_sanitized_tool_error`.
**Why:** Preserves the envelope structure (`code`, `message`, `retryable`, `tool`, `routing_*`) while guaranteeing zero raw filesystem paths, environment variables, or trace strings cross the JSON-RPC wire.
**Check:** uv run --no-sync python -m pytest tests/unit/test_mcp_error_sanitization.py -q
**Judged by:** run it
**Reference:** src/tensor_grep/cli/mcp_server.py:784

### Q3: How is zero wire leakage verified deterministically without breaking preexisting gates?
**Answer:**
1. **Poison Exception Fixtures & Direct FastMCP Tests:** Inject poison exceptions containing secret paths and tokens across broad and narrow handlers, cwd resolution failure, and external candidate path refusals. Direct `mcp.call_tool` tests verify that poison strings appear in `stderr` (logging control) but are 100% absent from wire JSON payloads.
2. **Closed-World Structural AST Ratchet:** `test_broad_mcp_handlers_never_echo_raw_str_exc_ast_ratchet` and `test_narrow_mcp_handlers_never_echo_raw_exception_formatting_ast_ratchet` scan `mcp_server.py`, `mcp_symbol_tools.py`, and `mcp_audit_tools.py`; `test_mcp_wire_str_exc_closed_world_ast_ratchet` enforces exactly 53 authorized sites and 0 un-allowlisted sites across all 3 modules.
3. **Regression Suite:** All existing MCP suites (`test_mcp_server_path_confinement.py` [175 tests], `test_w1a_mcp_handler_fail_closed.py` [55 tests]) pass 100% clean.
**Why:** Combines behavioral poison injection and direct FastMCP invocation with multi-module structural AST ratchets and existing envelope contract tests.
**Check:** uv run --no-sync python -m pytest tests/unit/test_mcp_error_sanitization.py tests/unit/test_w1a_mcp_handler_fail_closed.py -q
**Judged by:** run it
**Reference:** tests/unit/test_mcp_error_sanitization.py
