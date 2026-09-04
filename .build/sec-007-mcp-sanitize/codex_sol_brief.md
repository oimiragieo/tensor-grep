# Adversarial Security Audit & Verification Gate: SEC-007 (MCP Wire Error Sanitization) - Round 15

You are the Codex Sol security auditor. Perform a rigorous, adversarial security audit of the changes in branch `fix/sec-007-mcp-sanitize` against base `c7a515d`.

## Target Contracts & Round 14 Finding Resolutions:

1. **Strict Exact Type Identity Classification (Resolving Round 14 Finding 1 - MEDIUM)**:
   - In `src/tensor_grep/cli/mcp_server.py`:
     - Replaced heuristic `__module__` and `__name__` string checks with a strict exact type-object identity dictionary `_TRUSTED_EXCEPTION_CLASSES: dict[type, str]` mapping Python built-ins, standard library exceptions (`json.JSONDecodeError`, `subprocess.CalledProcessError`, `subprocess.SubprocessError`, `subprocess.TimeoutExpired`), and framework exceptions (`BackendExecutionError`, `ConfigurationError`, `PathConfinementError`) directly to their wire string representations.
     - `_safe_exception_class_name(exc)` performs exact type-object lookup via `_TRUSTED_EXCEPTION_CLASSES.get(type(exc), "InternalError")`.
     - Forgeable type metadata (`__module__`, `__name__`, `__class__`) is completely ignored.
     - Dynamic classes spoofing `__module__ = "tensor_grep.cli.mcp_server"` safely degrade to `"InternalError"`.
     - Synthetic classes named `"ValueError"` whose type identity does not match `builtins.ValueError` safely degrade to `"InternalError"`.
   - Added transport-level test controls in `tests/unit/test_mcp_error_sanitization.py`:
     - `test_direct_call_tool_spoofed_module_exception_type_sanitized`: verifies spoofed `__module__` results in `InternalError` on wire while logging full details to stderr.
     - `test_direct_call_tool_colliding_name_dynamic_exception_type_sanitized`: verifies synthetic class named `ValueError` results in `InternalError` on wire due to mismatched type identity.

2. **Audit Manifest Recording Error Wire Sanitization (Resolving Round 14 Finding 2 - MEDIUM)**:
   - In `src/tensor_grep/cli/mcp_server.py`:
     - In `_record_generated_audit_manifest`, replaced direct unclassified `type(exc).__name__` leak with `_safe_exception_class_name(exc)`.
   - Added test control in `tests/unit/test_mcp_error_sanitization.py`:
     - `test_direct_call_tool_rewrite_apply_audit_manifest_record_failure_sanitized`: drives public FastMCP `tg_rewrite_apply` with audit recording raising synthetic `SEC007_AUDIT_RECORD_SECRET`, confirming `record_error` on the wire degrades safely to `"InternalError"` while full details log to stderr.

3. **Scope-Wide Lexical Binding & Shadow Analysis in AST Ratchet (Resolving Round 14 Finding 3 - MEDIUM)**:
   - In `tests/unit/test_mcp_error_sanitization.py`:
     - Expanded shadow analysis in `_check_encompassing_boundary` from handler-local walk to entire `fn_node` function scope.
     - Inspects all function parameters (`posonlyargs`, `args`, `kwonlyargs`, `vararg`, `kwarg`).
     - Inspects all definitions and scopes across `fn_node`: `ast.Name` with `Store`/`Del`, `ast.FunctionDef`, `ast.AsyncFunctionDef`, `ast.ClassDef`, `ast.ExceptHandler.name`, `ast.Import`, and `ast.ImportFrom`.
     - Any parameter, variable, function, class, import, or except-target that shadows `protected_names` (`_log_tool_exception`, `_sanitized_tool_error`, `_sanitized_tool_error_text`, etc.) is immediately rejected (`return False`).
   - Added 3 hostile mutation controls (Controls 23, 24, 25; 25 total mutation controls, all verified):
     - Control 23 (`except Exception as _log_tool_exception:`): fails.
     - Control 24 (`_sanitized_tool_error_text = lambda ...` in try body): fails.
     - Control 25 (`def tool(_sanitized_tool_error_text=None):` in parameter): fails.

4. **Handler Dispositions Ledger Locatability & Synchronization (Resolving Round 14 Finding 4 - MEDIUM)**:
   - In `docs/audits/2026-08-20-handler-dispositions.json`:
     - Updated advisory line number for `_record_generated_audit_manifest` to 1202, falling squarely within its symbol span `[1191, 1205]`.
     - Resynchronized all advisory line numbers in `cli/mcp_server.py` that shifted due to formatting/imports.
   - `test_handler_dispositions.py` passes 11/11 tests completely green, with zero locatability or span mismatches.

5. **Static Analysis & Whitespace Verification**:
   - `ruff check .`: clean (all checks passed).
   - `ruff format --preview --check .`: clean.
   - `mypy src/tensor_grep`: clean (0 issues in 123 source files).
   - `git diff --check`: clean (0 whitespace errors).

## Review Instructions:
1. Inspect the git diff against base `c7a515d`.
2. Verify that all Round 14 findings are completely resolved:
   - Strict exact type-object identity dictionary classification.
   - Centralized sanitization of `record_error` in audit manifests.
   - Scope-wide shadow detection over function parameters, try body, and except targets.
   - Dispositions ledger 11/11 locatability passes.
3. Try to BREAK it: search for any bypass, unintended leak, unhandled exception, or regression.
4. Output your findings and final verdict:
   - If clean: `AUDIT_CLEAR / VERIFIED: GO` or `SHIP`.
   - If broken: `FIX-FIRST` with `file:line`, repro, and minimal fix.
