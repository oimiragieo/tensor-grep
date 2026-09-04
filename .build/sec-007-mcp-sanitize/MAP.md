# MAP — SEC-007-MCP-SANITIZE: MCP Wire Error Sanitization and Leak Prevention

## Destination
Eliminate all un-sanitized raw `str(exc)` exposures on the MCP JSON-RPC wire in `src/tensor_grep/cli/mcp_server.py`, preventing absolute filesystem path, credential, and internal trace leaks to AI agent callers while preserving all structured error envelope keys, codes, and fail-closed contracts.

## Open questions

<!-- zero open questions: all answered -->

## Answers

### Q1: What is the exact scope and classification of raw str(exc) sites on the MCP wire?
**Answer:** Across `src/tensor_grep/cli/mcp_server.py`, an initial population of 56 AST call sites was audited and reduced to exactly 30 closed-world authorized sites (26 eliminated: 16 Class A broad exception arms + 10 Class B narrow exception arms):
1. **Eliminated (26 sites):**
   - 16 Class A broad `except Exception` internal error arms routed to `_sanitized_tool_error` / constant sanitized payloads, with full traceback and path details sent exclusively to `stderr`.
   - 10 Class B narrow exception arms: 7 `SessionStaleError` sites returning safe session refresh guidance, 1 `FileNotFoundError` site in `tg_session_file_importers` returning `f"File not found: {file}"`, and 2 library `ValueError` sites in `tg_orient` and `tg_agent_capsule` returning safe parameter error messages.
2. **Authorized Closed-World Remaining (30 sites):**
   - 26 PathConfinementError sites (25 tool arms + 1 `_meta_confinement_error` helper), where `PathConfinementError` enforces a constant non-leaking message `"{label} must stay within the MCP root (refused)"` while logging resolved targets to `stderr`.
   - 2 Class C curated BackendExecutionError sites (`tg_find` and `tg_search`) preserving error code contracts.
   - 1 within-root `FileNotFoundError` site in `tg_find`.
   - 1 W1-a `tracked_file_count_error` detail key site in `tg_session_open`.
   - Exactly 0 unauthorized sites.
**Why:** Eliminates raw `str(exc)` tracebacks, local paths, and secret leaks while strictly preserving structured error envelope keys, codes, and fail-closed contracts.
**Check:** python -c "import ast; tree = ast.parse(open('src/tensor_grep/cli/mcp_server.py', encoding='utf-8').read()); calls = [n.lineno for n in ast.walk(tree) if isinstance(n, ast.Call) and isinstance(n.func, ast.Name) and n.func.id == 'str' and len(n.args) == 1 and isinstance(n.args[0], ast.Name) and n.args[0].id in ('exc', 'e')]; assert len(calls) == 30; print('30 AST str(exc) sites mapped')"
**Judged by:** run it
**Reference:** docs/plans/HANDLER-CENSUS-W2.md:64

### Q2: What is the sanitization architecture for each class?
**Answer:**
1. **Class A (16 broad arms):** Routed through `_sanitized_tool_error(tool_name, exc)` (or `_session_error_payload` with sanitized message), logging full tracebacks and paths server-side to `stderr` via `_log_tool_exception` and returning client-safe generic internal error messages identifying only the tool and exception class name.
2. **Class B (confinement & narrow arms):** Upstream path confinement in `_confine_read_path` and `_confine_write_path` raises structured `PathConfinementError(ValueError)` with constant public message `"{label} must stay within the MCP root (refused)"` and logs the absolute resolved path to `stderr`. Narrow exception handlers (`SessionStaleError`, `FileNotFoundError`, `ValueError`) return constant sanitized messages.
3. **Class C (2 backend arms):** Preserve curated backend execution error contracts in `tg_find` and `tg_search`.
**Why:** Preserves the envelope structure (`code`, `message`, `retryable`, `tool`, `routing_*`) while guaranteeing zero raw filesystem paths, environment variables, or trace strings cross the JSON-RPC wire.
**Check:** uv run --no-sync python -m pytest tests/unit/test_mcp_error_sanitization.py -q
**Judged by:** run it
**Reference:** src/tensor_grep/cli/mcp_server.py:784

### Q3: How is zero wire leakage verified deterministically without breaking preexisting gates?
**Answer:**
1. **Poison Exception Fixtures:** Inject poison exceptions containing secret paths and tokens across Class A tools and Class B narrow handlers. Verify that poison strings appear in `stderr` (logging control) but are 100% absent from wire JSON payloads.
2. **Closed-World Structural AST Ratchet:** `test_mcp_wire_str_exc_closed_world_ast_ratchet` and `test_broad_mcp_handlers_never_echo_raw_str_exc_ast_ratchet` in `test_mcp_error_sanitization.py` enforcing exactly 30 authorized sites and 0 un-allowlisted sites across `mcp_server.py`.
3. **Regression Suite:** All existing MCP suites (`test_mcp_server_path_confinement.py` [175 tests], `test_mcp_tg_find.py`, `test_w1a_mcp_silent_swallow_fixes.py`) pass 100% clean.
**Why:** Combines behavioral poison injection (proves leakage is stopped) with structural AST ratchets (prevents future regressions) and existing envelope contract tests.
**Check:** uv run --no-sync python -m pytest tests/unit/test_mcp_error_sanitization.py tests/unit/test_mcp_server_path_confinement.py -q
**Judged by:** run it
**Reference:** tests/unit/test_mcp_error_sanitization.py
