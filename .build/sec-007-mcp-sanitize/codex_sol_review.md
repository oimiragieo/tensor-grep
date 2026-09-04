## FIX-FIRST

The Round 4 `tg_ast_search` finding is fixed, but SEC-007 is not closed across the actual MCP wire surface.

### Findings

- [HIGH] The “closed-world” ratchets exclude independently registered MCP tools in the split modules. `mcp_server.py` explicitly imports and re-exports these tools ([mcp_server.py:1454](/C:/dev/projects/tensor-grep/src/tensor_grep/cli/mcp_server.py:1454)), while all three ratchets inspect only `mcp_server.py` ([test_mcp_error_sanitization.py:243](/C:/dev/projects/tensor-grep/tests/unit/test_mcp_error_sanitization.py:243), [line 382](/C:/dev/projects/tensor-grep/tests/unit/test_mcp_error_sanitization.py:382), [line 783](/C:/dev/projects/tensor-grep/tests/unit/test_mcp_error_sanitization.py:783)).

  An independent AST census found 16 broad `except Exception` handlers in registered sibling tools that still return `str(exc)`: eight in `mcp_symbol_tools.py` and eight in `mcp_audit_tools.py`. Examples include [mcp_symbol_tools.py:183](/C:/dev/projects/tensor-grep/src/tensor_grep/cli/mcp_symbol_tools.py:183) and [mcp_audit_tools.py:642](/C:/dev/projects/tensor-grep/src/tensor_grep/cli/mcp_audit_tools.py:642).

  Repro: patching `build_symbol_defs` to raise `RuntimeError("SEC007_BROAD_SECRET C:\\private\\trace.py")`, then calling `await mcp.call_tool("tg_symbol_defs", ...)`, returned the complete poison string in the FastMCP `TextContent` wire payload.

  Narrow handlers are also outside the ratchet. [mcp_symbol_tools.py:608](/C:/dev/projects/tensor-grep/src/tensor_grep/cli/mcp_symbol_tools.py:608) returns raw `str(exc)` for `FileNotFoundError`; the same FastMCP probe returned `SEC007_NARROW_SECRET C:\\private\\missing.py`.

  Minimal fix: expand the source population to every module contributing registered tools—at least `mcp_server.py`, `mcp_symbol_tools.py`, `mcp_audit_tools.py`, and the reachable rewrite helpers—then sanitize every broad handler and non-curated narrow handler. Add direct `mcp.call_tool` poison tests, rather than testing only meta wrappers.

- [MEDIUM] Two direct legacy tools still echo refused external candidate paths. [mcp_symbol_tools.py:510](/C:/dev/projects/tensor-grep/src/tensor_grep/cli/mcp_symbol_tools.py:510) and [line 571](/C:/dev/projects/tensor-grep/src/tensor_grep/cli/mcp_symbol_tools.py:571) catch `ValueError` and retain the raw `file` field instead of `"[refused]"`.

  Repro: invoking `tg_file_imports` and `tg_file_importers` through `mcp.call_tool` with an outside-root `SEC007_WIRE_SECRET.py` returned that absolute path in `payload["file"]`.

  Minimal fix: catch `PathConfinementError`, emit `file: "[refused]"`, and enroll both direct tools in the confinement behavior matrix. The current “all 35” matrix starts at [test_mcp_error_sanitization.py:1055](/C:/dev/projects/tensor-grep/tests/unit/test_mcp_error_sanitization.py:1055) but omits these public tools.

- [MEDIUM] `_mcp_root()` does not enclose its default `Path.cwd()` call ([mcp_server.py:1378](/C:/dev/projects/tensor-grep/src/tensor_grep/cli/mcp_server.py:1378)), and direct consumers evaluate `_mcp_root()` before entering `_confine_read_path` ([mcp_server.py:3601](/C:/dev/projects/tensor-grep/src/tensor_grep/cli/mcp_server.py:3601)). A cwd-resolution failure therefore bypasses `PathConfinementError`.

  Repro: forcing `Path.cwd()` to raise `RuntimeError("SEC007_CWD_SECRET")` made `mcp.call_tool("tg_classify_logs", ...)` raise `ToolError("Error executing tool tg_classify_logs: SEC007_CWD_SECRET")`.

  Minimal fix: enclose every `_mcp_root()` branch, including fallback `Path.cwd()`, and ensure all direct-root consumers translate failures to `PathConfinementError` before FastMCP sees them.

- [LOW] The committed verification artifacts contradict the exact commit. [MAP.md:13](/C:/dev/projects/tensor-grep/.build/sec-007-mcp-sanitize/MAP.md:13) and [from-map.md:5](/C:/dev/projects/tensor-grep/.build/sec-007-mcp-sanitize/gates/from-map.md:5) claim/check 30 sites, but the source and ratchet contain 27; that recorded check now fails. [RECEIPTS.md:5](/C:/dev/projects/tensor-grep/.build/sec-007-mcp-sanitize/RECEIPTS.md:5) records 15 tests, while the committed file runs 19.

### Confirmed working

Round 4 Finding 1 is resolved: `tg_ast_search` logs `ConfigurationError` to stderr and returns constant `"unavailable"` messages in both modes ([mcp_server.py:3285](/C:/dev/projects/tensor-grep/src/tensor_grep/cli/mcp_server.py:3285)). The in-file Class A/Class B/Class C behavior, 27-site ratchet, W1-a exception, meta tools, and 35 enumerated confinement cases pass.

Verification performed:

- SEC-007 suite: `19 passed`
- Confinement/search/find/W1-a suites: `231 passed`
- Ruff lint and preview-format: passed
- Mypy on `mcp_server.py`: passed
- Diff whitespace check: passed

Read-only `codebase-audit` workflow used; no files changed.