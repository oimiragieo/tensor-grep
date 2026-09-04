# Gates: sec-007-mcp-sanitize — seeded from MAP.md checks

Scope: every answer-key check, as a gate

- [x] G1: Q1: What is the exact scope and classification of raw str(exc) sites on the MCP wire? — python -c "import ast; from pathlib import Path; d = Path('src/tensor_grep/cli'); mods = ['mcp_server.py', 'mcp_symbol_tools.py', 'mcp_audit_tools.py', 'mcp_rewrite_tools.py']; total = sum(len([n for n in ast.walk(ast.parse((d/m).read_text(encoding='utf-8'))) if isinstance(n, ast.Call) and isinstance(n.func, ast.Name) and n.func.id == 'str' and len(n.args) == 1 and isinstance(n.args[0], ast.Name) and n.args[0].id in ('exc', 'e')]) for m in mods); assert total == 54; print('54 AST str(exc) sites mapped across 4 modules')"
  CHECK: python -c "import ast; from pathlib import Path; d = Path('src/tensor_grep/cli'); mods = ['mcp_server.py', 'mcp_symbol_tools.py', 'mcp_audit_tools.py', 'mcp_rewrite_tools.py']; total = sum(len([n for n in ast.walk(ast.parse((d/m).read_text(encoding='utf-8'))) if isinstance(n, ast.Call) and isinstance(n.func, ast.Name) and n.func.id == 'str' and len(n.args) == 1 and isinstance(n.args[0], ast.Name) and n.args[0].id in ('exc', 'e')]) for m in mods); assert total == 54; print('54 AST str(exc) sites mapped across 4 modules')"
  EXPECT: 54 AST str(exc) sites mapped across 4 modules
  EVIDENCE: 54 AST str(exc) sites mapped across 4 modules

- [x] G2: Q2: What is the sanitization architecture for each class? — uv run --no-sync python -m pytest tests/unit/test_mcp_error_sanitization.py -q
  CHECK: uv run --no-sync python -m pytest tests/unit/test_mcp_error_sanitization.py -q
  EXPECT: passed
  EVIDENCE: ......................................                                                   [100%] | 38 passed

- [x] G3: Q3: How is zero wire leakage verified deterministically without breaking preexisting gates? — uv run --no-sync python -m pytest tests/unit/test_mcp_error_sanitization.py tests/unit/test_w1a_mcp_handler_fail_closed.py -q
  CHECK: uv run --no-sync python -m pytest tests/unit/test_mcp_error_sanitization.py tests/unit/test_w1a_mcp_handler_fail_closed.py -q
  EXPECT: passed
  EVIDENCE: ............................................................................................. [100%] | 93 passed

<!-- seeded by build_state.py --seed-gates; edit CHECK/EXPECT, never delete a G<n> — ABANDON it -->
