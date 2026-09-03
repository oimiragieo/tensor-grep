# Acceptance Gates: Zvec-Grep Parity & Agent Intelligence Enhancement

- [x] GATE-1: AST Container Extraction Unit Tests Pass
  CHECK: uv run pytest tests/unit/test_ast_enrichment.py -q
  EXPECT: 0 failed (VERIFIED: 5 passed)

- [x] GATE-2: CLI Search `--enrich-ast` Option Works
  CHECK: uv run pytest tests/unit/test_search_ast_enrichment.py -q
  EXPECT: 0 failed (VERIFIED: 2 passed)

- [x] GATE-3: Agent Installer Multi-Target Unit Tests Pass
  CHECK: uv run pytest tests/unit/test_agent_installer.py -q
  EXPECT: 0 failed (VERIFIED: 12 passed)

- [x] GATE-4: CLI `tg install` and `tg uninstall` End-to-End Tests Pass
  CHECK: uv run pytest tests/unit/test_cli_installer_commands.py -q
  EXPECT: 0 failed (VERIFIED: 3 passed)

- [x] GATE-5: Full Linter Passes Across All Modified Code
  CHECK: uv run ruff check src/tensor_grep/cli/agent_installer.py src/tensor_grep/cli/ast_enrichment.py src/tensor_grep/cli/main.py src/tensor_grep/cli/bootstrap.py src/tensor_grep/cli/commands.py src/tensor_grep/core/result.py src/tensor_grep/cli/formatters/json_fmt.py tests/unit/test_agent_installer.py tests/unit/test_ast_enrichment.py tests/unit/test_search_ast_enrichment.py tests/unit/test_cli_installer_commands.py tests/e2e/test_routing_parity.py
  EXPECT: All checks passed (VERIFIED: All checks passed!)

- [x] GATE-6: Full Formatter Check Passes Across All Modified Code
  CHECK: uv run ruff format --check --preview src/tensor_grep/cli/agent_installer.py src/tensor_grep/cli/ast_enrichment.py src/tensor_grep/cli/main.py src/tensor_grep/cli/bootstrap.py src/tensor_grep/cli/commands.py src/tensor_grep/core/result.py src/tensor_grep/cli/formatters/json_fmt.py tests/unit/test_agent_installer.py tests/unit/test_ast_enrichment.py tests/unit/test_search_ast_enrichment.py tests/unit/test_cli_installer_commands.py tests/e2e/test_routing_parity.py
  EXPECT: 12 files already formatted (VERIFIED: 12 files already formatted)

- [x] GATE-7: Static Type Checking Passes Across All Modified Source
  CHECK: uv run mypy src/tensor_grep/cli/agent_installer.py src/tensor_grep/cli/ast_enrichment.py src/tensor_grep/cli/main.py src/tensor_grep/cli/bootstrap.py src/tensor_grep/cli/commands.py src/tensor_grep/core/result.py src/tensor_grep/cli/formatters/json_fmt.py
  EXPECT: Success: no issues found (VERIFIED: Success: no issues found in 7 source files)
