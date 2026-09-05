# SEC-007 Round 12 — FIX-FIRST

Audited `c7a515d794ff84d677f9426222828a0fbad5096a..ba8d3a44fae297797284238ceae3be46387563dd` read-only using the `codebase-audit` skill.

## Findings

- [MEDIUM] The outer-boundary AST ratchet remains fail-open for unrecognized taint contexts and unreachable top-level logging. In [test_mcp_error_sanitization.py](C:/dev/projects/tensor-grep/tests/unit/test_mcp_error_sanitization.py:1610), logging only needs to appear somewhere in `h.body`; it may occur after an unconditional return. At [lines 1652–1675](C:/dev/projects/tensor-grep/tests/unit/test_mcp_error_sanitization.py:1652), tainted loads are rejected only for several enumerated parent types, while every other AST shape implicitly passes.

  Running the exact committed `_check_encompassing_boundary` returned `True` for all six hostile mutations:

  ```text
  f_string True                    # return f"{exc}"
  nested_alias True                # if True: leaked = exc; return leaked
  early_return_unreachable_log True
  raise_then_return True           # raise exc; return "safe"
  attribute_sink_spoof True        # attacker._sanitized_tool_error(exc)
  walrus_return True               # return (leaked := exc)
  ```

  Minimal fix: make unknown taint contexts reject by default; propagate taint recursively through nested statements, destructuring and `NamedExpr`; accept sinks only when `Call.func` is an exact unshadowed `ast.Name`; and require the direct log before any control-transfer statement. Add the six mutations above as controls.

- [MEDIUM] Resolver failure now fails open to executable lookup through `PATH`. Both [mcp_rewrite_tools.py:523](C:/dev/projects/tensor-grep/src/tensor_grep/cli/mcp_rewrite_tools.py:523) and [mcp_rewrite_tools.py:571](C:/dev/projects/tensor-grep/src/tensor_grep/cli/mcp_rewrite_tools.py:571) catch resolution failures and substitute `binary_str = "tg"`. This behavior was not present at the base revision.

  Reproduction with `resolve_native_tg_binary()` forced to raise:

  ```text
  rewrite_argv0 tg
  index_argv0 tg
  ```

  All current production callers pass an explicitly resolved `native_binary`, which limits immediate external reachability, but the security-sensitive builders themselves are now fail-open and can select an unintended executable.

  Minimal fix: make `native_binary` required and remove both internal resolver/`"tg"` fallback branches. Propagate resolver failure into the existing sanitized error path.

- [MEDIUM] The ledger is structurally synchronized but several new disposition claims are factually incorrect. For example:

  - [ledger:1783](C:/dev/projects/tensor-grep/docs/audits/2026-08-20-handler-dispositions.json:1783) calls `_mcp_root` an outer fail-closed boundary using `_log_tool_exception` and returning a sanitized wire payload. The implementation directly prints and falls back to the current directory at [mcp_server.py:1407](C:/dev/projects/tensor-grep/src/tensor_grep/cli/mcp_server.py:1407).
  - [ledger:2453](C:/dev/projects/tensor-grep/docs/audits/2026-08-20-handler-dispositions.json:2453) and [ledger:2463](C:/dev/projects/tensor-grep/docs/audits/2026-08-20-handler-dispositions.json:2463) describe the command builders as fail-closed boundaries returning sanitized payloads. They instead continue with `binary_str = "tg"`.
  - The `_confine_write_path` and `_confine_mcp_path` records similarly claim `_log_tool_exception` and direct sanitized wire returns, while their handlers use direct stderr output and rethrow `PathConfinementError`.

  The ledger test only requires evidence and reason to be non-empty and unequal at [test_handler_dispositions.py:287](C:/dev/projects/tensor-grep/tests/unit/test_handler_dispositions.py:287), so copied but false evidence passes.

  Minimal fix: rederive those records from actual behavior; classify the `_mcp_root` fallback as `LOGGED-DEGRADE`, accurately describe confinement exception translation, and remove or correctly disposition the command-builder fallbacks.

## Verified clean portions

- `_log_tool_exception` is non-throwing through `except BaseException` at [mcp_server.py:769](C:/dev/projects/tensor-grep/src/tensor_grep/cli/mcp_server.py:769).
- Ledger structure: 131/131 four-module handlers represented, zero missing, zero extra, zero exact-line mismatches. SEC-007 contributes exactly 74 rows: 39/10/19/6 by module.
- `test_mcp_error_sanitization.py`: 39 passed.
- Joint security suite: 96 passed.
- Ledger suite: 10 passed, 1 expensive locatability test deselected; an independent exact-line reconciliation covered all 131 records more strictly.
- Focused Ruff checks, format check, MyPy over 123 source files, and `git diff --check`: clean.
- Worktree remained clean.

**Final verdict: FIX-FIRST.** Round 11 findings 1 and 2 are not fully resolved, and the new `PATH` fallback should be removed before clearance.