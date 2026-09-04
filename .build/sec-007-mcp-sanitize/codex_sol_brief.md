# Adversarial Security Audit & Verification Gate: SEC-007 (MCP Wire Error Sanitization) - Round 13

You are the Codex Sol security auditor. Perform a rigorous, adversarial security audit of the changes in branch `fix/sec-007-mcp-sanitize` against base `c7a515d`.

## Target Contracts & Round 12 Finding Resolutions:

1. **AST Ratchet Taint Fail-Closed & Hostile Mutation Controls (Resolving Round 12 Finding 1 - MEDIUM)**:
   - In `tests/unit/test_mcp_error_sanitization.py`, `_check_encompassing_boundary` has been updated with fail-closed semantics:
     - Direct logging requirement: `_log_tool_exception` call must appear before ANY control-transfer statements (`Return`, `Raise`, `Break`, `Continue`). Logs appearing after early returns are strictly rejected.
     - Terminal return requirement: handler must terminate with an `ast.Return`.
     - Explicitly forbids any `ast.Raise` in the handler body.
     - Fixed-point taint propagation: recursively tracks taint through `ast.NamedExpr` (walrus `:=`), `ast.Assign` (with destructuring), and `ast.AnnAssign`.
     - Fail-closed / reject-by-default for tainted variables: any load of a tainted variable that is NOT in an explicitly whitelisted safe context is immediately rejected. Allowed contexts are strictly:
       - safe attribute: `exc.__class__` (for `exc.__class__.__name__`).
       - direct call sink: direct argument to an unshadowed `ast.Name` call in `allowed_sinks`.
     - All other parent types (e.g. `FormattedValue`/`JoinedStr` f-strings, `Return`, `BinOp`, `Dict`, `List`, `Tuple`, kwargs, arbitrary attribute accesses like `exc.__dict__`, etc.) are REJECTED.
     - Validated against 18 comprehensive mutation controls (including all 6 hostile mutations highlighted by Codex Sol):
       - Control 13: f-string formatting leak `return f"{exc}"` -> rejected.
       - Control 14: nested alias assignment `if True: leaked = exc; return leaked` -> rejected.
       - Control 15: unreachable log after early return -> rejected.
       - Control 16: `raise exc; return "safe"` -> rejected.
       - Control 17: attribute sink spoof `attacker._sanitized_tool_error(exc)` -> rejected.
       - Control 18: walrus return `return (leaked := exc)` -> rejected.

2. **Removal of Fail-Open PATH Fallbacks in Command Builders (Resolving Round 12 Finding 2 - MEDIUM)**:
   - In `src/tensor_grep/cli/mcp_rewrite_tools.py`, removed internal resolver calls and `"tg"` PATH fallbacks from `_build_rewrite_command` and `_build_index_search_command`.
   - `native_binary: str | Path` is now a mandatory keyword parameter.
   - Callers (`tg_index_search`, `tg_rewrite_plan`, `tg_rewrite_diff`, `execute_rewrite_apply_json`) resolve `native_tg` up-front via `_resolve_native_tg_binary_for_mcp()`; if resolution fails, they return `_native_unavailable_error` on the sanitized error path without ever attempting to invoke an unverified binary from `PATH`.

3. **Accurate Classification and Evidence in Dispositions Ledger (Resolving Round 12 Finding 3 - MEDIUM)**:
   - All 72 net additions to `docs/audits/2026-08-20-handler-dispositions.json` have been re-derived from actual code behavior:
     - `_mcp_root` line 1407 is correctly classified as `LOGGED-DEGRADE` (fallback to current working directory with diagnostic log to `sys.stderr`).
     - `_mcp_root` line 1415 is classified as `INTENTIONAL-BOUNDARY` translating current working directory resolution failure to `PathConfinementError("root")`.
     - `_confine_write_path` and `_confine_mcp_path` accurately describe path resolution failure logging to `sys.stderr` and translation into `PathConfinementError` to prevent wire disclosure.
     - `_resolve_native_tg_binary_for_mcp` accurately describes logging and returning `(None, sanitized_error)` tuple.
     - Tool endpoints are accurately documented as `INTENTIONAL-BOUNDARY` logging via `_log_tool_exception` and returning sanitized error payloads.
     - Ledger test suite passes 11/11 tests green in `test_handler_dispositions.py`.

4. **Synchronized Census & Ceiling**:
   - `TOTAL_BROAD_HANDLERS_CEILING = 338` in `tests/unit/test_silent_failure_hardening.py` (+72 delta: +39 mcp_server, +10 mcp_symbol_tools, +19 mcp_audit_tools, +4 mcp_rewrite_tools).
   - Broad handler census passes 2/2 green.

5. **Static Analysis & Whitespace Verification**:
   - `ruff check .`: clean.
   - `ruff format --preview --check .`: clean.
   - `mypy src/tensor_grep`: clean (0 issues in 123 source files).
   - `git diff --check`: clean (0 whitespace warnings/errors).

## Review Instructions:
1. Inspect the git diff against base `c7a515d`.
2. Verify that all Round 12 findings are fully resolved:
   - Fail-closed AST ratchet rejecting unknown taint contexts, walrus, nested alias, attribute spofs, f-strings, early return logging.
   - Strict `native_binary` requirement in command builders with zero fallback to `"tg"` or `PATH`.
   - Factually accurate evidence, reasons, and categories (`LOGGED-DEGRADE`, `INTENTIONAL-BOUNDARY`) in `docs/audits/2026-08-20-handler-dispositions.json`.
3. Try to BREAK it: search for any bypass, unintended leak, unhandled exception, or regression.
4. Output your findings and final verdict:
   - If clean: `AUDIT_CLEAR / VERIFIED: GO` or `SHIP`.
   - If broken: `FIX-FIRST` with `file:line`, repro, and minimal fix.