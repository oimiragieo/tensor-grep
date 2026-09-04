# Gates: sec-007-mcp-sanitize — seeded from MAP.md checks

Scope: every answer-key check, as a gate

- [x] G1: Q1: What is the exact scope and classification of raw str(exc) sites on the MCP wire? — python -c "import ast; tree = ast.parse(open('src/tensor_grep/cli/mcp_server.py', encoding='utf-8').read()); calls = [n.lineno for n in ast.walk(tree) if isinstance(n, ast.Call) and isinstance(n.func, ast.Name) and n.func.id == 'str' and len(n.args) == 1 and isinstance(n.args[0], ast.Name) and n.args[0].id in ('exc', 'e')]; assert len(calls) == 30; print('30 AST str(exc) sites mapped')"
  CHECK: python -c "import ast; tree = ast.parse(open('src/tensor_grep/cli/mcp_server.py', encoding='utf-8').read()); calls = [n.lineno for n in ast.walk(tree) if isinstance(n, ast.Call) and isinstance(n.func, ast.Name) and n.func.id == 'str' and len(n.args) == 1 and isinstance(n.args[0], ast.Name) and n.args[0].id in ('exc', 'e')]; assert len(calls) == 30; print('30 AST str(exc) sites mapped')"
  EXPECT: 30 AST str(exc) sites mapped
  EVIDENCE: 30 AST str(exc) sites mapped

- [x] G2: Q2: What is the sanitization architecture for each class? — uv run --no-sync python -m pytest tests/unit/test_mcp_error_sanitization.py -q
  CHECK: uv run --no-sync python -m pytest tests/unit/test_mcp_error_sanitization.py -q
  EXPECT: passed
  EVIDENCE: ...............                                                         [100%] | 15 passed

- [x] G3: Q3: How is zero wire leakage verified deterministically without breaking preexisting gates? — uv run --no-sync python -m pytest tests/unit/test_mcp_server_path_confinement.py -q
  CHECK: uv run --no-sync python -m pytest tests/unit/test_mcp_server_path_confinement.py -q
  EXPECT: passed
  EVIDENCE: ...............................                                          [100%] | 175 passed

<!-- seeded by build_state.py --seed-gates; edit CHECK/EXPECT, never delete a G<n> — ABANDON it -->
