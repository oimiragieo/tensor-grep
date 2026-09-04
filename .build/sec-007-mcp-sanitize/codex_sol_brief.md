# Adversarial Security Audit & Verification Gate: SEC-007 (MCP Wire Error Sanitization) - Round 11

You are the Codex Sol security auditor. Perform a rigorous, adversarial security audit of the changes in branch `fix/sec-007-mcp-sanitize` against base `c7a515d`.

## Target Contracts & Round 10 Finding Resolutions:

1. **Non-Throwing Server-Side Exception Logging (Resolving Round 10 Finding 1 - HIGH)**:
   - `_log_tool_exception` in `src/tensor_grep/cli/mcp_server.py` is now strictly non-throwing: wraps traceback formatting and `print(..., file=sys.stderr)` in `try ... except BaseException: pass`.
   - Even if `sys.stderr.write` or `sys.stderr.flush` raises (e.g. broken pipe or custom erroring stream), `_log_tool_exception` will never throw or let exceptions escape to the FastMCP transport layer.
   - Verified via direct FastMCP test: `test_direct_call_tool_broken_stderr_poison_sanitized` where `sys.stderr` raises `OSError(SEC007_STDERR_FAILURE_SECRET)` simultaneously with tool failure; FastMCP tool call completes cleanly with zero wire leak.

2. **Broad-Handler Census & Population Ceiling Synchronized (Resolving Round 10 Finding 2 - HIGH)**:
   - In `tests/unit/test_silent_failure_hardening.py`, `TOTAL_BROAD_HANDLERS_CEILING` is updated from 266 to 340 with exact audited arithmetic:
     - Exact delta across 4 audited modules is +74 (+39 in `mcp_server.py`, +10 in `mcp_symbol_tools.py`, +19 in `mcp_audit_tools.py`, +6 in `mcp_rewrite_tools.py`).
     - All 74 additions are classified as `INTENTIONAL-BOUNDARY` providing whole-body fail-closed error containment for all 58 registered MCP tools and engine helpers.
     - `test_broad_exception_handler_population_does_not_regress` is completely GREEN (passes in 1.86s).

3. **AST Ratchet Taint Analysis & Terminal Sanitized Return (Resolving Round 10 Finding 3 - MEDIUM)**:
   - Hardened `_check_encompassing_boundary` in `tests/unit/test_mcp_error_sanitization.py`:
     - Requires every broad handler of the encompassing outer `try` to invoke `_log_tool_exception`.
     - Requires a return statement in the handler body.
     - Performs taint analysis on the bound exception variable: rejects any direct return of `exc`, rejection of passing `exc` into data structures (dicts, tuples, lists, kwargs like `json.dumps({'error': exc})`), and allows only approved sanitization sinks (`_log_tool_exception`, `_sanitized_tool_error`, `_sanitized_tool_error_text`, `_ruleset_scan_error`, etc.) or safe attribute access (`exc.__class__.__name__`).
     - Backed by 8 comprehensive mutation controls (including missing log, `return exc`, and `json.dumps({'error': exc})`).

4. **Test & Static Analysis Verification**:
   - Total tests in `test_mcp_error_sanitization.py`: 39 (all passing).
   - G3 joint fail-closed suite: 94 (all passing).
   - Combined test suite (sanitization + silent failure + w1a fail closed): 96 passed in 25.7s.
   - AST `str(exc)` sites across 4 modules: exactly 54 sites, 100% `PathConfinementError`.
   - `ruff check`: clean.
   - `ruff format --preview --check`: clean.
   - `mypy src/tensor_grep`: clean (0 issues in 123 source files).

5. **Synchronized Documentation & Receipts**:
   - `gates/from-map.md` and `RECEIPTS.md` synchronized to 54 AST sites, 39 unit tests, and 94 joint tests.

## Review Instructions:
1. Inspect the git diff against base `c7a515d`.
2. Verify that all Round 10 findings are fully resolved:
   - Non-throwing `_log_tool_exception` under broken stderr.
   - Synchronized broad handler ceiling and audited dispositions.
   - Taint-aware AST ratchet enforcing sanitized terminal returns and server logging.
3. Try to BREAK it: search for any bypass, unintended leak, unhandled exception, or regression.
4. Output your findings and final verdict:
   - If clean: `AUDIT_CLEAR / VERIFIED: GO` or `SHIP`.
   - If broken: `FIX-FIRST` with `file:line`, repro, and minimal fix.
