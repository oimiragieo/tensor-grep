# Adversarial Security Audit & Verification Gate: SEC-007 (MCP Wire Error Sanitization) - Round 9

You are the Codex Sol security auditor. Perform a rigorous, adversarial security audit of the changes in branch `fix/sec-007-mcp-sanitize` against base `c7a515d`.

## Target Contracts & Round 8 Finding Resolutions:

1. **Outer Error Boundaries & Native Resolver Hardening (Resolving High Finding)**:
   - In `src/tensor_grep/cli/mcp_rewrite_tools.py`:
     - `_resolve_native_tg_binary_for_mcp`: catches `Exception as exc`, logs full trace via `_log_tool_exception("resolve_native_tg_binary", exc)`, and returns `None, f"Native binary resolution failed: {exc.__class__.__name__}"`.
     - `_build_rewrite_command` and `_build_index_search_command` accept pre-resolved `native_binary: str | None = None` to avoid double-resolution, and wrap any fallback resolution in try/except `Exception` returning `"tg"`.
     - `execute_rewrite_plan_json`: wrapped in top-level try/except `Exception as exc:`, logging to `_log_tool_exception` and returning `_rewrite_error(f"Rewrite plan failed: {exc.__class__.__name__}", code="internal_error")`.
     - In `execute_rewrite_apply_json`: wrapped `evaluate_apply_policy` in try/except `Exception as exc:`, logging full trace to `_log_tool_exception`, returning `{"code": "policy_evaluation_failed", "message": "Policy evaluation failed after rewrite application. Edits may have already been applied."}` and setting `policy_evaluation_error: f"Policy evaluation failed: {exc.__class__.__name__}"`.
   - In `src/tensor_grep/cli/mcp_audit_tools.py`:
     - Added outer try/except `Exception as exc:` boundaries with `_log_tool_exception` logging across `tg_rulesets`, `tg_ruleset_scan`, `tg_index_search`, `tg_rewrite_plan`, `tg_rewrite_apply`, and `tg_rewrite_diff`.
   - In `src/tensor_grep/cli/mcp_server.py`:
     - Added outer try/except `Exception as exc:` boundaries with `_log_tool_exception` logging across `tg_mcp_capabilities` and `tg_devices`.
   - Verified across all registered MCP tools: **100% of all 58 tools across all 4 files have top-level try/except Exception error boundaries!**

2. **`PolicyValidationError.details` Sanitization & Server-Side Logging (Resolving Medium Finding)**:
   - Defined `_ALLOWED_POLICY_FIELDS` and `_sanitize_policy_validation_details(details)` in `mcp_rewrite_tools.py`.
   - Allowlisted fields only; curates messages to safe, path-free diagnostics (e.g. `ruleset_scan.baseline path must be within the policy directory`).
   - In `execute_rewrite_apply_json`: server-side logging of raw `exc.details` (including un-sanitized paths/tokens) to `sys.stderr` via `print(f"[tensor-grep-mcp] load_apply_policy details: {json.dumps(exc.details)}", file=sys.stderr)`. Only sanitized details are returned on the MCP wire.

3. **AST Ratchet Hardening & Mutation Controls (Resolving Medium Finding)**:
   - In `tests/unit/test_mcp_error_sanitization.py`:
     - Enhanced `find_narrow_offenders` to detect:
       - Direct returns or exposures of `exc.details`, `exc.args`, `exc.message`.
       - Narrow exception handlers that do not bind `as exc` (`unbound_narrow_handler`).
       - Narrow exception handlers missing server-side stderr logging (`missing_stderr_log`).
     - Added 3 new negative mutation controls to `narrow_test_snippets` proving each detection works.
     - Added `test_all_mcp_registered_tools_have_outer_fail_closed_boundary` verifying all 58 tools across all 4 files have outer try/except `Exception` boundaries, backed by negative mutation control.
     - Added 4 direct FastMCP poison reproduction tests:
       - `test_direct_call_tool_rewrite_plan_resolver_poison_sanitized`
       - `test_direct_call_tool_rewrite_apply_policy_validation_details_poison_sanitized`
       - `test_direct_call_tool_rewrite_apply_policy_evaluation_poison_sanitized`
       - `test_direct_call_tool_devices_poison_sanitized`
     - Total tests in `test_mcp_error_sanitization.py`: 33 (all passing).
     - G3 joint fail-closed suite: 88 (all passing).
     - AST `str(exc)` sites across 4 modules: exactly 54 sites, 100% `PathConfinementError`.

4. **Synchronized Documentation & Receipts**:
   - `MAP.md`, `gates/from-map.md`, and `RECEIPTS.md` are synchronized to 54 sites across 4 modules, 33 passed in `test_mcp_error_sanitization.py`, and 88 passed in G3 joint suite.
   - Code clean under `ruff check`, `ruff format --preview --check`, and `mypy src/tensor_grep`.

## Review Instructions:
1. Inspect the git diff against base `c7a515d`.
2. Verify that all 3 Round 8 findings are fully resolved:
   - Missing outer error boundaries & resolver hardening.
   - `PolicyValidationError.details` bypass & stderr logging.
   - AST ratchet blind spots, missing handler checks, and mutation controls.
3. Try to BREAK it: search for any bypass, unintended leak, unhandled exception, or regression.
4. Output your findings and final verdict:
   - If clean: `AUDIT_CLEAR / VERIFIED: GO` or `SHIP`.
   - If broken: `FIX-FIRST` with `file:line`, repro, and minimal fix.


