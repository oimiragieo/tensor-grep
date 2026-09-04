# Adversarial Security Audit & Verification Gate: SEC-007 (MCP Wire Error Sanitization) - Round 14

You are the Codex Sol security auditor. Perform a rigorous, adversarial security audit of the changes in branch `fix/sec-007-mcp-sanitize` against base `c7a515d`.

## Target Contracts & Round 13 Finding Resolutions:

1. **AST Ratchet Bypasses Closed & Hostile Mutation Controls (Resolving Round 13 Finding 1 - MEDIUM)**:
   - In `tests/unit/test_mcp_error_sanitization.py`:
     - `_is_broad_handler` now recognizes both simple broad exceptions and tuple-form broad handlers (`except (Exception,):` or `except (..., Exception, ...):`).
     - Pre-log checks recursively inspect statements prior to `first_log_idx` for any control transfers (`ast.Return`, `ast.Raise`, `ast.Break`, `ast.Continue`), catching nested transfers such as `if True: return "unlogged"`.
     - Lexical shadow check forbids rebinding, assigning, or deleting approved logger or sanitizer sink names (`_log_tool_exception`, `_sanitized_tool_error`, `_sanitized_tool_error_text`, etc.).
     - Approved sink set includes `_safe_exception_class_name`.
     - Added hostile mutation controls 19 through 22 (22 total mutation controls, all verified failing on bypass and passing on hardened implementation):
       - Control 19: nested early return before log (`if True: return 'unlogged'`) -> rejected.
       - Control 20: shadowed sanitizer sink (`_sanitized_tool_error = lambda ...`) -> rejected.
       - Control 21: shadowed logger (`_log_tool_exception = lambda ...`) -> rejected.
       - Control 22: tuple broad handler (`except (Exception,):`) -> detected and checked.

2. **Strictly Non-Throwing Exception Class Wire Classification (Resolving Round 13 Finding 2 - MEDIUM)**:
   - Created centralized helper `_safe_exception_class_name(exc: BaseException) -> str` in `src/tensor_grep/cli/mcp_server.py`.
   - Never accesses user-controlled properties or `exc.__class__`; uses `type(exc)` to inspect the type descriptor safely at the C level.
   - Validates the type name against a strict allowlist of standard Python builtins and known framework error classes (`_TRUSTED_EXCEPTION_CLASSES`), plus modules in `tensor_grep` and standard library modules (`builtins`, `json`, `subprocess`, `os`, `pathlib`).
   - Any dynamic, synthesized, or hostile type name (e.g. `type("SEC007_DYNAMIC_TYPE_SECRET", (Exception,), {})`) degrades safely to `"InternalError"` on the wire.
   - Replaced all direct wire interpolations of `exc.__class__.__name__` across `mcp_server.py`, `mcp_audit_tools.py`, and `mcp_rewrite_tools.py` with `_safe_exception_class_name(exc)`.
   - Hardened server-side `_log_tool_exception` with non-throwing fallback handling in case `traceback.format_exception` encounters an exception with a throwing property.
   - Added transport-level FastMCP direct tool call tests in `tests/unit/test_mcp_error_sanitization.py`:
     - `test_direct_call_tool_dynamic_exception_type_sanitized`: verifies dynamic class names degrade safely to `InternalError` on the wire while logging full details to stderr.
     - `test_direct_call_tool_hostile_class_property_sanitized`: verifies hostile `@property def __class__` accessors do not escape the boundary or leak secrets on the wire.

3. **Handler Dispositions Ledger Factually Accurate (Resolving Round 13 Finding 3 - MEDIUM)**:
   - In `docs/audits/2026-08-20-handler-dispositions.json`:
     - Corrected `_confine_mcp_path` entry: handler catches `_mcp_root()` anchor resolution failure (not candidate path resolution).
     - Corrected 8 entries that were inaccurately labeled "outer" boundaries when they were nested/inner handlers (`tg_ruleset_scan`, `tg_index_search`, rewrite endpoints, `tg_devices`, and `execute_rewrite_apply_json`).
     - Synchronized advisory line numbers (`_record_generated_audit_manifest`, `stdin_reader`, `tg_mcp_capabilities`).
     - Verified all 338 ledger records against their AST symbol spans.
   - In `tests/unit/test_silent_failure_hardening.py`:
     - Updated ceiling comment at line 164 to document the actual 71 `INTENTIONAL-BOUNDARY` and 1 `LOGGED-DEGRADE` split.
   - Dispositions test suite passes 11/11 tests green.

4. **Synchronized Census & Ceiling**:
   - `TOTAL_BROAD_HANDLERS_CEILING = 338` in `tests/unit/test_silent_failure_hardening.py`.
   - All 58 FastMCP tools verified to have fail-closed outer encompassing boundaries.

5. **Static Analysis & Whitespace Verification**:
   - `ruff check .`: clean.
   - `ruff format --preview --check .`: clean.
   - `mypy src/tensor_grep`: clean (0 issues in 123 source files).
   - `git diff --check`: clean.

## Review Instructions:
1. Inspect the git diff against base `c7a515d`.
2. Verify that all Round 13 findings are fully resolved:
   - AST ratchet recognizes tuple broad handlers, rejects pre-log control transfers, rejects shadowing of sink/logger names.
   - Exception class rendering is centralized, non-throwing, never accesses `exc.__class__`, validates against trusted classes, and degrades dynamic/hostile types to `InternalError`.
   - Dispositions ledger factually accurate (inner vs outer boundaries, root vs candidate confinement, 71/1 split).
3. Try to BREAK it: search for any bypass, unintended leak, unhandled exception, or regression.
4. Output your findings and final verdict:
   - If clean: `AUDIT_CLEAR / VERIFIED: GO` or `SHIP`.
   - If broken: `FIX-FIRST` with `file:line`, repro, and minimal fix.
