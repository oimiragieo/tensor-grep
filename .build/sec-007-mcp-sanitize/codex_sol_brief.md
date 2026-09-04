# Adversarial Security Audit & Verification Gate: SEC-007 (MCP Wire Error Sanitization) - Round 6

You are the Codex Sol security auditor. Perform a rigorous, adversarial security audit of the changes in branch `fix/sec-007-mcp-sanitize` against base `c7a515d`.

## Goal & Target Contracts:
1. **Zero Secret / Path / Traceback Leaks to JSON-RPC Wire Across All Tool Modules (Resolving Round 5 High Finding)**:
   - All 32 broad `except Exception` internal error handlers across `src/tensor_grep/cli/mcp_server.py`, `src/tensor_grep/cli/mcp_symbol_tools.py`, and `src/tensor_grep/cli/mcp_audit_tools.py` return sanitized error envelopes (via `_sanitized_tool_error` or constant sanitized payloads), logging full details and tracebacks strictly to `stderr` and never echoing raw exception formatting on the wire.
   - All narrow handlers across all three modules (`SessionStaleError`, `FileNotFoundError`, `ValueError`, `ConfigurationError`) do not echo raw un-sanitized exception messages to the client and log full details to `stderr`.
   - Direct FastMCP `mcp.call_tool` poison tests verify broad exception sanitization (`test_direct_call_tool_broad_poison_sanitized`) and narrow exception sanitization (`test_direct_call_tool_narrow_poison_sanitized`).
   - Broad AST ratchet (`test_broad_mcp_handlers_never_echo_raw_str_exc_ast_ratchet`) and narrow AST ratchet (`test_narrow_mcp_handlers_never_echo_raw_exception_formatting_ast_ratchet`) now inspect all three modules (`mcp_server.py`, `mcp_symbol_tools.py`, and `mcp_audit_tools.py`), with zero offenders found and 6 negative controls each.
2. **Path Confinement Security & Envelope Redaction (Resolving Round 5 Medium Finding 1)**:
   - Direct legacy tools `tg_file_imports` and `tg_file_importers` in `mcp_symbol_tools.py` now catch `PathConfinementError` and redact candidate path fields (`payload["file"] = "[refused]"` and `payload["path"] = "[refused]"`), preventing external candidate paths from leaking on the wire.
   - Enrolled `tg_file_imports`, `tg_file_importers` (file refusal, path refusal, and both refusal) in the confinement behavior matrix in `test_confinement_refusal_envelope_never_contains_external_path`, bringing total verified entrypoints to 39.
   - Direct FastMCP `mcp.call_tool` test (`test_direct_call_tool_external_path_redacted`) proves external paths are redacted to `"[refused]"`.
3. **Safe MCP Root Resolution (Resolving Round 5 Medium Finding 2)**:
   - `_mcp_root()` in `mcp_server.py` encloses its default `Path.cwd()` resolution in a try-except block that catches unexpected errors, logs full details to `stderr`, and raises `PathConfinementError("root") from exc`.
   - Direct consumers evaluate `_confine_read_path` / `_confine_mcp_path` which handle `PathConfinementError` cleanly, returning `"[refused]"` envelopes rather than escaping to FastMCP as unhandled `ToolError`.
   - Direct FastMCP `mcp.call_tool` test (`test_direct_call_tool_cwd_failure_sanitized`) proves cwd resolution failure returns sanitized invalid_input `"[refused]"` envelope without leaking exception text or escaping.
4. **Exact Closed-World Ratchet & Verification Alignment (Resolving Round 5 Low Finding)**:
   - `test_mcp_wire_str_exc_closed_world_ast_ratchet` inspects all 3 modules (`mcp_server.py`: 27, `mcp_symbol_tools.py`: 11, `mcp_audit_tools.py`: 15) and enforces exactly 53 authorized `str(exc)` sites (52 `PathConfinementError` + 1 W1-a `tracked_file_count_error` detail key) and zero un-allowlisted sites.
   - `MAP.md`, `gates/from-map.md`, and `RECEIPTS.md` are synchronized to the exact 53-site count and 23-test suite in `test_mcp_error_sanitization.py`.

## Review Instructions:
1. Inspect the git diff against `c7a515d`.
2. Evaluate resolutions to Round 5 findings (split modules coverage, direct legacy tools candidate path redaction, `_mcp_root` cwd enclosure, and artifact synchronization).
3. Try to BREAK it: search for any bypass, unintended leak, unhandled exception, or regression.
4. Output your findings and final verdict:
   - If clean: `AUDIT_CLEAR / VERIFIED: GO` or `SHIP`.
   - If broken: `FIX-FIRST` with `file:line`, repro, and minimal fix.
