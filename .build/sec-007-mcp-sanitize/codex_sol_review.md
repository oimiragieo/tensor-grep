# FIX-FIRST

Audited `f108cb3` against `c7a515d`. One blocking finding remains.

## [HIGH] MCP rewrite engine bypasses the three-module sanitization ratchet

The claimed 52-site closed world excludes [`mcp_rewrite_tools.py`](/C:/dev/projects/tensor-grep/src/tensor_grep/cli/mcp_rewrite_tools.py:1), even though it produces responses forwarded directly by registered MCP tools at [`mcp_audit_tools.py:518`](/C:/dev/projects/tensor-grep/src/tensor_grep/cli/mcp_audit_tools.py:518).

Multiple paths still serialize raw exception text:

- Embedded rewrite exceptions: [`mcp_rewrite_tools.py:558`](/C:/dev/projects/tensor-grep/src/tensor_grep/cli/mcp_rewrite_tools.py:558) and [`mcp_rewrite_tools.py:578`](/C:/dev/projects/tensor-grep/src/tensor_grep/cli/mcp_rewrite_tools.py:578)
- Rewrite subprocess failures: [`mcp_rewrite_tools.py:508`](/C:/dev/projects/tensor-grep/src/tensor_grep/cli/mcp_rewrite_tools.py:508)
- Diff subprocess failures: [`mcp_rewrite_tools.py:1027`](/C:/dev/projects/tensor-grep/src/tensor_grep/cli/mcp_rewrite_tools.py:1027)
- Index-search subprocess failures: [`mcp_rewrite_tools.py:1066`](/C:/dev/projects/tensor-grep/src/tensor_grep/cli/mcp_rewrite_tools.py:1066)

The ratchets explicitly inspect only three files at [`test_mcp_error_sanitization.py:242`](/C:/dev/projects/tensor-grep/tests/unit/test_mcp_error_sanitization.py:242) and [`test_mcp_error_sanitization.py:392`](/C:/dev/projects/tensor-grep/tests/unit/test_mcp_error_sanitization.py:392).

Direct FastMCP repros:

```text
tg_rewrite_plan + poisoned embedded RuntimeError:
TOKEN_IN_TEXT True
TOKEN_IN_DATA True
ERROR_MESSAGE SEC007_REWRITE_SECRET at C:\private\native_model.bin

tg_rewrite_diff + poisoned OSError:
TOKEN_IN_TEXT True
TOKEN_IN_DATA True
ERROR_MESSAGE Failed to execute rewrite diff command:
              SEC007_SUBPROCESS_SECRET at C:\private\runner.exe
```

Thus secrets and internal paths reach both FastMCP text content and structured result data.

Minimal fix:

- Include `mcp_rewrite_tools.py` in the broad, narrow, and closed-world ratchets.
- Log raw exception/subprocess diagnostics server-side and return stable messages containing at most the safe exception class and curated error code.
- Treat native stderr as untrusted diagnostic material; classify it internally rather than returning it verbatim.
- Add direct `mcp.call_tool` poison tests for `tg_rewrite_plan` and `tg_rewrite_diff`, asserting absence from both `content` and structured data.
- Recalculate the documented population and receipts.

## Round 6 verification

The requested Round 6 corrections themselves are effective:

- `tracked_file_count_error` is sanitized; poison was absent from both FastMCP outputs and present in stderr.
- All 22 newly changed narrow handlers are bound and logged. The current module contains 23 relevant handlers total, including the pre-existing logged `tg_find` handler.
- Per-case stderr assertions execute independently.
- The 52-site count is accurate for the three selected files—but is not a closed-world count of the complete MCP wire-producing surface.

Verification completed:

- Dedicated sanitization suite: **24 passed**
- Sanitization + fail-closed suites: **79 passed**
- Path-confinement suite: **175 passed**
- Ruff check and format: passed
- Mypy on all three changed production modules: passed
- `git diff --check`: passed

No files were modified. Audit followed the [codebase-audit skill](/C:/Users/oimir/.codex/skills/codebase-audit/SKILL.md).

**Final verdict: FIX-FIRST**