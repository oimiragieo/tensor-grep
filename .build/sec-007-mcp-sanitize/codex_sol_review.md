# SEC-007 Round 11 — FIX-FIRST

Audited `f0af1253a31906c02e80d3c4d3846cdb5fff0efc` against `c7a515d794ff84d677f9426222828a0fbad5096a`. Read-only audit; no files changed.

## Findings

- [MEDIUM] The “taint-aware” boundary ratchet remains bypassable. At [test_mcp_error_sanitization.py:1625](</C:/dev/projects/tensor-grep/tests/unit/test_mcp_error_sanitization.py:1625>), only the immediate parent of each `exc` load is examined. Assignments and most attribute accesses fall through without rejection. At [test_mcp_error_sanitization.py:1610](</C:/dev/projects/tensor-grep/tests/unit/test_mcp_error_sanitization.py:1610>), any syntactic logging call satisfies the gate, including unreachable calls.

  Reproduced in memory with these mutations:

  ```python
  leaked = exc
  return leaked

  return exc.__dict__

  if False:
      _log_tool_exception("tool", exc)
  return "safe"
  ```

  The first mutation produced `FALSE-GREEN` from all four relevant AST ratchets, including `test_all_mcp_registered_tools_have_outer_fail_closed_boundary`. Direct probes of `_check_encompassing_boundary` returned `True` for all three bypass classes.

  Minimal fix: propagate taint through assignments, containers, attributes, formatting, and calls; require `_log_tool_exception` as an unconditional direct handler statement; require a direct terminal return whose exception-derived values reach only explicitly approved sanitizers. Add mutation controls for alias-return, arbitrary attributes, and unreachable/nested logging.

- [MEDIUM] The handler ceiling is synchronized, but the claimed audited disposition population is not. [test_silent_failure_hardening.py:162](</C:/dev/projects/tensor-grep/tests/unit/test_silent_failure_hardening.py:162>) raises the ceiling with an aggregate classification, while the file says the complete per-handler table lives in [2026-08-20-handler-dispositions.json](</C:/dev/projects/tensor-grep/docs/audits/2026-08-20-handler-dispositions.json:1>). That ledger is unchanged and contains only the original MCP population:

  - Ledger: `35 + 10 + 8 + 4 = 57`
  - Current source: `74 + 20 + 27 + 10 = 131`
  - Missing dispositions: exactly `74`

  Minimal fix: add the 74 current handlers to the canonical disposition ledger with enclosing-function/site evidence, then ratchet ledger/source set equality—not only `total <= 340`.

## Verified clean

- `_log_tool_exception` is non-throwing around traceback formatting and stderr output at [mcp_server.py:769](</C:/dev/projects/tensor-grep/src/tensor_grep/cli/mcp_server.py:769>).
- Broken-stderr FastMCP regression passed.
- Exact census verified: repository `266 → 340`; module deltas `+39/+10/+19/+6`.
- Exactly 58 registered tools remain; all current outer handlers have direct logging and terminal returns.
- 54 `str(exc)` sites remain; current allowed confinement behavior passed.
- `96 passed in 25.55s`; collection counts independently confirmed as 39 and 94.
- Ruff check and format check passed; mypy passed for 123 source files.
- Worktree remained clean.

Final verdict: **FIX-FIRST**. Round 10 finding 1 is resolved; finding 2 is only partially resolved; finding 3 remains unresolved.