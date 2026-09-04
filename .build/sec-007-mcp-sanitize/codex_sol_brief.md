# Adversarial Security Audit & Verification Gate: SEC-007 (MCP Wire Error Sanitization) - Round 7

You are the Codex Sol security auditor. Perform a rigorous, adversarial security audit of the changes in branch `fix/sec-007-mcp-sanitize` against base `c7a515d`.

## Goal & Target Contracts:
1. **Zero Secret / Path / Traceback Leaks to JSON-RPC Wire Across All Tool Modules (Resolving Round 6 High Finding)**:
   - Sanitized `tracked_file_count_error` in `tg_session_open` (`src/tensor_grep/cli/mcp_server.py:3780`) using `_sanitized_tool_error_text("get_session", exc)`: logs full traceback server-side to `stderr` via `_log_tool_exception` and returns safe class-name disclosure `f"get_session failed: {exc.__class__.__name__}"` on the wire without echoing raw `str(exc)` or internal secrets.
   - Added direct FastMCP poison test `test_direct_call_tool_session_open_get_session_poison_sanitized`: proves secrets do not leak into `tracked_file_count_error` or text content on the wire, while confirming the secret is properly captured in `stderr`.
   - Removed W1-a pin from broad AST ratchet and removed `("tg_session_open", "Exception"): 1` from the closed-world allowlist.
   - Closed-world population across all 3 modules (`mcp_server.py`: 26, `mcp_symbol_tools.py`: 11, `mcp_audit_tools.py`: 15) is now exactly **52 sites**, and **100% of the 52 sites are `PathConfinementError`** (zero broad or narrow unauthorized exception formatting leaks anywhere).

2. **Narrow Handler Server-Side Logging Contract Enforced and Tested (Resolving Round 6 Medium Finding)**:
   - Bound `as exc:` and added `_log_tool_exception` to all 22 tool-level narrow exception handlers (`SessionStaleError`, `FileNotFoundError`, `ValueError`) across `src/tensor_grep/cli/mcp_server.py`.
   - Updated `test_class_b_narrow_handlers_do_not_leak_poison_trace_or_path` to assert `captured = capsys.readouterr(); assert poison in captured.err` independently after *each* individual test case (1. `SessionStaleError`, 2. `FileNotFoundError` in session store, 3. `ValueError` in `tg_orient`, 4. `ValueError` in `tg_agent_capsule`, 5. `FileNotFoundError` in `tg_find`).

3. **Multi-Module Ratchet & FastMCP Coverage**:
   - Broad and narrow AST ratchets inspect all 3 modules (`mcp_server.py`, `mcp_symbol_tools.py`, `mcp_audit_tools.py`), with zero broad or narrow unauthorized exception formatting and 6 negative controls each.
   - 5 direct FastMCP `mcp.call_tool` tests cover broad exceptions, narrow exceptions, cwd resolution failure, external path redaction (`[refused]`), and session-open degradation.
   - All 39 path confinement entrypoints redact candidate paths (`payload["path"] = "[refused]"`, `payload["file"] = "[refused]"`).

4. **Synchronized Documentation & Receipts**:
   - `MAP.md`, `gates/from-map.md`, and `RECEIPTS.md` are synchronized to exactly 52 sites and 24 passed tests in `test_mcp_error_sanitization.py`.

## Review Instructions:
1. Inspect the git diff against `c7a515d`.
2. Evaluate resolutions to Round 6 findings:
   - `tracked_file_count_error` sanitization and direct FastMCP poison test.
   - All 22 narrow handlers bound and logged to stderr, with per-case independent stderr assertions.
   - Closed-world count reduction to exactly 52 sites (100% `PathConfinementError`).
3. Try to BREAK it: search for any bypass, unintended leak, unhandled exception, or regression.
4. Output your findings and final verdict:
   - If clean: `AUDIT_CLEAR / VERIFIED: GO` or `SHIP`.
   - If broken: `FIX-FIRST` with `file:line`, repro, and minimal fix.
