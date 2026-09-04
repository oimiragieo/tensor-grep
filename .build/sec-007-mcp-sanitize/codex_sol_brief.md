# Adversarial Security Audit & Verification Gate: SEC-007 (MCP Wire Error Sanitization) - Round 10

You are the Codex Sol security auditor. Perform a rigorous, adversarial security audit of the changes in branch `fix/sec-007-mcp-sanitize` against base `c7a515d`.

## Target Contracts & Round 9 Finding Resolutions:

1. **Whole-Body Encompassing Outer Error Boundaries (Resolving Round 9 High Finding)**:
   - Round 9 identified that the outer error boundary ratchet allowed broad `try/except` anywhere inside tool functions, which permitted executable code before the `try` block (e.g. `_confine_mcp_path`) in 5 audit tools (`tg_rewrite_plan`, `tg_rewrite_apply`, `tg_rewrite_diff`, `tg_index_search`, `tg_ruleset_scan`), letting exceptions escape to `ToolError` as raw strings.
   - In Round 10, **all 58 registered MCP tools across all modules** have been verified and wrapped in single encompassing `try/except Exception as exc:` blocks encompassing their entire post-docstring body:
     - `src/tensor_grep/cli/mcp_server.py` (34 tools)
     - `src/tensor_grep/cli/mcp_audit_tools.py` (14 tools)
     - `src/tensor_grep/cli/mcp_symbol_tools.py` (10 tools)
   - Every single registered tool handles broad exceptions cleanly, logs via `_log_tool_exception` to server stderr, and returns structured sanitized JSON error diagnostics (`_sanitized_tool_error` / `_sanitized_tool_error_text`).

2. **AST Ratchet Hardening & Mutation Controls**:
   - In `tests/unit/test_mcp_error_sanitization.py`:
     - Hardened `test_all_mcp_registered_tools_have_outer_fail_closed_boundary` with `_check_encompassing_boundary(node)` requiring:
       - Exactly 1 top-level statement after any docstring (`len(remaining_stmts) == 1`).
       - That statement must be an `ast.Try`.
       - That `ast.Try` must catch broad `Exception` or `BaseException` and log to stderr.
     - Mutation controls verify detection of:
       - No try block at all.
       - Code preceding the try block.
       - Code following the try block.
       - Narrow handler without broad fallback.
       - Async tool without encompassing boundary.
     - Added 5 new direct FastMCP confinement poison tests targeting `tg_rewrite_plan`, `tg_rewrite_apply`, `tg_rewrite_diff`, `tg_index_search`, and `tg_ruleset_scan`, verifying zero raw wire leak when `_confine_mcp_path` raises.

3. **Test & Static Analysis Verification**:
   - Total tests in `test_mcp_error_sanitization.py`: 38 (all passing).
   - G3 joint fail-closed suite: 93 (all passing).
   - AST `str(exc)` sites across 4 modules: exactly 54 sites, 100% `PathConfinementError`.
   - `ruff check`: clean.
   - `ruff format --preview --check`: clean (1017 files formatted, including docs/BACKLOG.md).
   - `mypy src/tensor_grep`: clean (0 issues in 123 source files).

4. **Synchronized Documentation & Receipts**:
   - `gates/from-map.md` and `RECEIPTS.md` synchronized to 54 AST sites, 38 unit tests, and 93 joint tests.

## Review Instructions:
1. Inspect the git diff against base `c7a515d`.
2. Verify that all Round 9 findings are fully resolved:
   - Whole-body encompassing try/except boundaries across all 58 tools.
   - Zero wire leak under confinement or resolver poisons.
   - Clean formatting and ratchet test coverage.
3. Try to BREAK it: search for any bypass, unintended leak, unhandled exception, or regression.
4. Output your findings and final verdict:
   - If clean: `AUDIT_CLEAR / VERIFIED: GO` or `SHIP`.
   - If broken: `FIX-FIRST` with `file:line`, repro, and minimal fix.
