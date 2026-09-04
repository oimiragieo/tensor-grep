# FIX-FIRST

Audited `68654220` against base `c7a515d7`. Two blocking findings remain.

- **[HIGH] Broad exception text still leaks directly over FastMCP.** [`mcp_server.py:3779`](C:/dev/projects/tensor-grep/src/tensor_grep/cli/mcp_server.py:3779) serializes `str(exc)` into `tracked_file_count_error`. A direct `mcp.call_tool("tg_session_open", ...)` poison test returned:

  ```json
  "tracked_file_count_error": "SEC007_SESSION_SECRET at C:\\private\\session.db"
  ```

  The secret appeared in both FastMCP text content and structured result data. The ratchet deliberately skips this leak at [`test_mcp_error_sanitization.py:309`](C:/dev/projects/tensor-grep/tests/unit/test_mcp_error_sanitization.py:309) and authorizes it at line 828, producing a false green.

  Minimal fix: use `_sanitized_tool_error_text("get_session", exc)` or log plus a constant message; add a direct poison test for the second `get_session` stage; remove the exception from the allowlist; update the documented count from 53 to 52.

- **[MEDIUM] The narrow-handler stderr logging contract is unimplemented and untested.** Twenty-two tool-level `SessionStaleError`, `FileNotFoundError`, and `ValueError` handlers in `mcp_server.py` do not bind or log the exception, including [`mcp_server.py:1556`](C:/dev/projects/tensor-grep/src/tensor_grep/cli/mcp_server.py:1556), [`mcp_server.py:2111`](C:/dev/projects/tensor-grep/src/tensor_grep/cli/mcp_server.py:2111), and [`mcp_server.py:3948`](C:/dev/projects/tensor-grep/src/tensor_grep/cli/mcp_server.py:3948). A direct poisoned `SessionStaleError` produced a sanitized wire response but empty stderr. The AST ratchet skips unbound handlers at [`test_mcp_error_sanitization.py:459`](C:/dev/projects/tensor-grep/tests/unit/test_mcp_error_sanitization.py:459), while the dynamic test captures stderr only after several cases, allowing one logged case to mask the others.

  Minimal fix: bind each narrow exception with `as exc`, call `_log_tool_exception`, and assert stderr independently for every poisoned case.

The legacy path redaction and `_mcp_root()` cwd-failure paths verified clean. Existing checks passed—23/23 sanitization tests, 74/74 related tests, Ruff, formatting, mypy, and `git diff --check`—but the first finding demonstrates those gates are currently false-green. `MAP.md`, `from-map.md`, and `RECEIPTS.md` are numerically synchronized, not aligned with the zero-leak contract.

Audit was read-only, following [codebase-audit](C:/Users/oimir/.codex/skills/codebase-audit/SKILL.md) and bounded-test guidance from [anti-hang-test-protocol](C:/Users/oimir/.codex/skills/anti-hang-test-protocol/SKILL.md).