# Adversarial Security Audit & Verification Gate: SEC-007 (MCP Wire Error Sanitization) - Round 8

You are the Codex Sol security auditor. Perform a rigorous, adversarial security audit of the changes in branch `fix/sec-007-mcp-sanitize` against base `c7a515d`.

## Goal & Target Contracts:
1. **Sanitization and AST Ratchet Enrollment of `mcp_rewrite_tools.py` (Resolving Round 7 Finding)**:
   - Identified that `mcp_audit_tools.py` delegates `tg_rewrite_plan`, `tg_rewrite_apply`, `tg_rewrite_diff`, and `tg_index_search` to `src/tensor_grep/cli/mcp_rewrite_tools.py`.
   - In `mcp_rewrite_tools.py`:
     - Imported `PathConfinementError` and `_log_tool_exception` from `mcp_server`.
     - Subprocess native stderr is classified into safe curated error strings via `_extract_rewrite_error_message(stderr, code=...)`; raw stderr is emitted server-side to `sys.stderr.write` and never leaks to the client wire.
     - `_classify_native_rewrite_failure` inspects `stderr: str | BaseException` and maps to safe categories (`pattern_error`, `io_error`, `native_internal_error`, `invalid_input`) without stringifying arbitrary exception text on wire.
     - `_execute_rewrite_json_command`: catches `FileNotFoundError` and `OSError`, logs to `_log_tool_exception`, and returns safe class-name message.
     - `_execute_embedded_rewrite_json`: catches `ImportError`, `RuntimeError`, `Exception`, logs to `_log_tool_exception`, and returns `f"Embedded rewrite {mode} failed: {exc.__class__.__name__}"`.
     - `_execute_rewrite_diff_command`: catches `FileNotFoundError` and `OSError`, logs to `_log_tool_exception`, and returns safe messages.
     - `_execute_index_search_command`: catches `FileNotFoundError` and `OSError`, logs to `_log_tool_exception`, and returns safe messages.
     - `execute_rewrite_apply_json`: catches `PathConfinementError` for `audit_manifest` and `policy`, logs `ValueError` and checkpoint exceptions to `_log_tool_exception`.
   - In `mcp_audit_tools.py`:
     - Redacted candidate paths in `tg_index_search` and `tg_audit_history` `ValueError` handlers (`path="[refused]"`).
   - In `tests/unit/test_mcp_error_sanitization.py`:
     - Added `"mcp_rewrite_tools.py"` to broad and narrow AST ratchets (now 4 modules total).
     - Added `("execute_rewrite_apply_json", "PathConfinementError"): 2` to the closed-world allowlist.
     - Closed-world population across all 4 modules (`mcp_server.py`: 26, `mcp_symbol_tools.py`: 11, `mcp_audit_tools.py`: 15, `mcp_rewrite_tools.py`: 2) is now exactly **54 sites**, and **100% of the 54 sites are `PathConfinementError`**.
     - Added 4 direct FastMCP tests covering rewrite diff subprocess failure, embedded rewrite failure, native stderr classified diagnostic isolation, and index search subprocess failure.
     - Total tests in `test_mcp_error_sanitization.py`: 28 (all passing).

2. **Narrow Handler Server-Side Logging Contract Enforced and Tested**:
   - All 22 narrow exception handlers across `src/tensor_grep/cli/mcp_server.py` bind `as exc:` and log to `_log_tool_exception`.
   - Dynamic tests in `test_class_b_narrow_handlers_do_not_leak_poison_trace_or_path` assert `captured.err` independently for every case.

3. **Complete Confinement & Redaction**:
   - All 39 path confinement entrypoints redact candidate paths (`payload["path"] = "[refused]"`, `payload["file"] = "[refused]"`).
   - `_mcp_root()` fallback to `Path.cwd()` raises `PathConfinementError("root")`.

4. **Synchronized Documentation & Receipts**:
   - `MAP.md`, `gates/from-map.md`, and `RECEIPTS.md` are synchronized to exactly 54 sites across 4 modules, 28 passed tests in `test_mcp_error_sanitization.py`, and 83 passed tests in the joint fail-closed suite.

## Review Instructions:
1. Inspect the git diff against `c7a515d`.
2. Evaluate resolutions to Round 7 findings:
   - `mcp_rewrite_tools.py` inclusion in AST ratchets and sanitization of subprocess/embedded execution.
   - Native stderr diagnostic classification and server-side logging.
   - 4 direct FastMCP poison tests for rewrite engine operations.
   - Closed-world verification: exactly 54 sites across 4 modules, 100% `PathConfinementError`.
3. Try to BREAK it: search for any bypass, unintended leak, unhandled exception, or regression.
4. Output your findings and final verdict:
   - If clean: `AUDIT_CLEAR / VERIFIED: GO` or `SHIP`.
   - If broken: `FIX-FIRST` with `file:line`, repro, and minimal fix.

