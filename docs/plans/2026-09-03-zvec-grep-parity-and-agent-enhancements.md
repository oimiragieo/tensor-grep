# Zvec-Grep Parity & Agent Intelligence Enhancement Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Implement automated multi-agent onboarding (`tg install` / `tg uninstall`) across Claude Code, Codex, Cursor, Qwen Code, and OpenCode, and add dynamic enclosing AST container enrichment (`--enrich-ast` / `container` metadata) to search results, closing competitive advantages identified in Alibaba's `zvec-grep`.

**Architecture:**
1. **Agent Integration (`tg install` / `tg uninstall`)**: Add `src/tensor_grep/cli/agent_installer.py` exposing CLI subcommands `tg install` and `tg uninstall`. Reads, parses, and updates configuration files across target tools (`~/.claude.json`, `~/.claude/CLAUDE.md`, `~/.codex/config.toml`, `~/.codex/AGENTS.md`, `~/.cursor/mcp.json`, `~/.config/opencode/opencode.json`, `~/.qwen/settings.json`). Inserts managed prompt blocks bounded by `# >>> TENSOR_GREP_START >>>` and `# <<< TENSOR_GREP_END <<<` without altering user customizations, adds pre-authorized MCP server definitions pointing to `tg mcp`, and supports `--target [claude|codex|cursor|qwen|opencode|all]`, `--yes`, and `--dry-run`.
2. **Dynamic AST Container Enrichment (`--enrich-ast`)**: Add `src/tensor_grep/cli/ast_enrichment.py` hooking into search output formatting (`src/tensor_grep/cli/main.py`). For matching lines in code files, uses Tree-sitter AST queries via existing `_enclosing_symbol_for_line` in `repo_map.py` to identify the containing syntactic scope (function, method, class) and attach `container: {"name": str, "kind": str, "range": [start, end]}` to JSON/NDJSON and agent context output. Bounded by `AST_ENRICH_FILE_LIMIT = 100` to prevent latency degradation.

**Tech Stack:** Python 3.11+, Typer, Rich, Tree-Sitter (via `tensor-grep[ast]` optional extra / native bindings), PyYAML, JSONC/JSON, pytest.

---

## Wayfinder Answer Key & Verification Standards

### Destination
`tensor-grep` provides seamless one-command agent onboarding (`tg install`) across all major agent environments (Claude Code, Cursor, Codex, OpenCode, Qwen) and delivers structure-enriched grep matches containing enclosing function/class containers, matching and exceeding `zvec-grep`.

### Key Verification Questions & Checks

#### Q1: How do we safely modify agent configuration files without corrupting existing JSON/TOML or overwriting user tools?
- **Answer:** Load existing configuration with tolerant parsers, inject or update only the `tensor_grep` key under `mcpServers` (or TOML table `[mcp_servers.tensor_grep]`), write via atomic temporary file rename, and isolate prompt injections between `# >>> TENSOR_GREP_START >>>` and `# <<< TENSOR_GREP_END <<<` sentinels.
- **Check:** `pytest tests/unit/test_agent_installer.py -k test_safe_config_preservation`
- **Judged by:** run it
- **Reference:** —

#### Q2: What exact MCP command is registered with the agents?
- **Answer:** Command resolves dynamically to `tg mcp` (or the explicit virtualenv executable if run from an activated venv). Arguments: `["mcp"]`.
- **Check:** `pytest tests/unit/test_agent_installer.py -k test_mcp_command_resolution`
- **Judged by:** run it
- **Reference:** —

#### Q3: How does AST container enrichment behave when Tree-sitter is missing or a file has unparseable syntax?
- **Answer:** Fails open gracefully. If tree-sitter or language grammar is absent, or if the line is top-level (outside any function/class), `container` is omitted or set to `null` without erroring or dropping the match.
- **Check:** `pytest tests/unit/test_ast_enrichment.py -k test_graceful_fallback_unparseable`
- **Judged by:** run it
- **Reference:** —

#### Q4: What is the performance guardrail for AST enrichment during large searches?
- **Answer:** Capped at `max_files=100` unique matched files. If more than 100 files have matches, only the first 100 files have AST enrichment applied, and `ast_enrichment_truncated: true` is reported in search metadata.
- **Check:** `pytest tests/unit/test_ast_enrichment.py -k test_enrichment_file_limit_guardrail`
- **Judged by:** run it
- **Reference:** —

---

## File Structure Plan

- **New Files**:
  - `src/tensor_grep/cli/agent_installer.py`: Core logic for detecting, installing, and uninstalling agent MCP configurations and search guidance rules.
  - `src/tensor_grep/cli/ast_enrichment.py`: Tree-sitter AST container enrichment helper for search hits.
  - `tests/unit/test_agent_installer.py`: Unit tests for agent configuration detection, modification, idempotency, and uninstall.
  - `tests/unit/test_ast_enrichment.py`: Unit tests for syntax container resolution across Python, TypeScript, Rust, and Go.
- **Modified Files**:
  - `src/tensor_grep/cli/main.py`: Register `tg install` and `tg uninstall` subcommands; add `--enrich-ast` flag to `tg search`.
  - `src/tensor_grep/cli/commands.py`: Expose installer commands in the command registry.
  - `README.md`: Document `tg install` and `--enrich-ast`.

---

## Implementation Tasks

### Task 1: AST Container Enrichment Helper (`ast_enrichment.py`)

**Files:**
- Create: `src/tensor_grep/cli/ast_enrichment.py`
- Test: `tests/unit/test_ast_enrichment.py`

- [ ] **Step 1: Write the failing unit tests for AST container extraction**
  Write tests in `tests/unit/test_ast_enrichment.py` testing:
  - Extracting the enclosing function/method container for a given line number in Python.
  - Extracting the enclosing class container for a property definition.
  - Graceful fallback to `None` when the line is at the module level.
  - Bounded file limit enforcement (100 files cap).

- [ ] **Step 2: Run test to verify it fails**
  Run: `uv run pytest tests/unit/test_ast_enrichment.py -v`
  Expected: FAIL with `ModuleNotFoundError: No module named 'tensor_grep.cli.ast_enrichment'`

- [ ] **Step 3: Implement `ast_enrichment.py`**
  Implement `enrich_match_with_container(file_path: Path, line_number: int, repo_map_state=None) -> Optional[Dict[str, Any]]`:
  - Leverages `repo_map._enclosing_symbol_for_line` or Tree-sitter node traversal.
  - Returns `{"name": symbol_name, "kind": symbol_kind, "range": [start_line, end_line]}`.
  - Cache parsed ASTs per-file within a single search run.

- [ ] **Step 4: Run test to verify it passes**
  Run: `uv run pytest tests/unit/test_ast_enrichment.py -v`
  Expected: PASS

- [ ] **Step 5: Lint and format**
  Run: `uv run ruff check src/tensor_grep/cli/ast_enrichment.py tests/unit/test_ast_enrichment.py`
  Run: `uv run ruff format --preview src/tensor_grep/cli/ast_enrichment.py tests/unit/test_ast_enrichment.py`

---

### Task 2: Integrate AST Enrichment into `tg search` CLI

**Files:**
- Modify: `src/tensor_grep/cli/main.py`
- Test: `tests/unit/test_search_ast_enrichment.py`

- [ ] **Step 1: Write failing CLI integration test**
  Test `tg search "query" --enrich-ast --json` to ensure the output JSON items contain a `"container"` field with name, kind, and range.

- [ ] **Step 2: Run test to verify it fails**
  Run: `uv run pytest tests/unit/test_search_ast_enrichment.py -v`
  Expected: FAIL (unrecognized option `--enrich-ast` or missing container key).

- [ ] **Step 3: Wire `--enrich-ast` option in `src/tensor_grep/cli/main.py`**
  Add `--enrich-ast` flag to `search` command. When enabled, pass matches through `enrich_search_results(...)` before formatting JSON/NDJSON or agent context output.

- [ ] **Step 4: Verify test passes**
  Run: `uv run pytest tests/unit/test_search_ast_enrichment.py -v`
  Expected: PASS

- [ ] **Step 5: Lint and format**
  Run: `uv run ruff check src/tensor_grep/cli/main.py`
  Run: `uv run ruff format --preview src/tensor_grep/cli/main.py`

---

### Task 3: Agent Installer Core (`agent_installer.py`)

**Files:**
- Create: `src/tensor_grep/cli/agent_installer.py`
- Test: `tests/unit/test_agent_installer.py`

- [ ] **Step 1: Write failing unit tests for agent targets**
  Test target handlers for:
  - `claude`: Updates `claude.json` / `settings.json` (mcpServers: `{"tensor_grep": {"command": "tg", "args": ["mcp"]}}`) and writes `CLAUDE.md` guidance block.
  - `cursor`: Updates `~/.cursor/mcp.json`.
  - `codex`: Updates `~/.codex/config.toml` and writes `~/.codex/AGENTS.md`.
  - `opencode`: Updates `opencode.json` and `AGENTS.md`.
  - `qwen`: Updates `~/.qwen/settings.json` and `QWEN.md`.
  - Test idempotency (running twice does not duplicate entries).
  - Test uninstall (removes only `tensor_grep` server and managed block).

- [ ] **Step 2: Run test to verify it fails**
  Run: `uv run pytest tests/unit/test_agent_installer.py -v`
  Expected: FAIL with `ModuleNotFoundError`

- [ ] **Step 3: Implement `agent_installer.py`**
  - Implement `install_agent_integration(target: str, home_dir: Path, dry_run: bool = False) -> Dict[str, Any]`
  - Implement `uninstall_agent_integration(target: str, home_dir: Path, dry_run: bool = False) -> Dict[str, Any]`
  - Implement sentinel block insertion:
    ```
    # >>> TENSOR_GREP_START >>>
    # Use tg (tensor-grep) for search, symbol intelligence, and edit readiness.
    # <<< TENSOR_GREP_END <<<
    ```
  - Implement atomic file writing with permissions preserved.

- [ ] **Step 4: Run test to verify it passes**
  Run: `uv run pytest tests/unit/test_agent_installer.py -v`
  Expected: PASS

- [ ] **Step 5: Lint and format**
  Run: `uv run ruff check src/tensor_grep/cli/agent_installer.py tests/unit/test_agent_installer.py`
  Run: `uv run ruff format --preview src/tensor_grep/cli/agent_installer.py tests/unit/test_agent_installer.py`

---

### Task 4: Expose `tg install` & `tg uninstall` in Main CLI

**Files:**
- Modify: `src/tensor_grep/cli/main.py`
- Modify: `src/tensor_grep/cli/commands.py`
- Test: `tests/unit/test_cli_installer_commands.py`

- [ ] **Step 1: Write failing CLI end-to-end tests**
  Test `tg install --target claude --dry-run` and `tg uninstall --target claude --dry-run`.

- [ ] **Step 2: Run test to verify it fails**
  Run: `uv run pytest tests/unit/test_cli_installer_commands.py -v`
  Expected: FAIL with unknown command `install`.

- [ ] **Step 3: Register Typer commands in `main.py`**
  - `@app.command(name="install")`
  - `@app.command(name="uninstall")`
  - Connect to `agent_installer.py` with friendly Rich console output.

- [ ] **Step 4: Verify test passes**
  Run: `uv run pytest tests/unit/test_cli_installer_commands.py -v`
  Expected: PASS

- [ ] **Step 5: Full verification & documentation update**
  - Update `README.md` and `docs/CONTRACTS.md` with `tg install` and `--enrich-ast` usage.
  - Run full lint, format, and typecheck:
    `uv run ruff check .`
    `uv run ruff format --preview --check .`
    `uv run mypy src/tensor_grep`

---

## Thinktank Multi-Seat Council Review (Round 1)

### Seat Verdicts:
1. **Seat 1 (Architecture & Parity Lens - Fable/Opus)**: `APPROVED`
   - *Finding*: `tg install` provides direct parity with `zg install` while keeping `tg mcp` as the single source of truth for protocol interactions. Isolating managed prompt blocks with `# >>> TENSOR_GREP_START >>>` sentinels protects existing user configs.
2. **Seat 2 (Contract & Ground Truth Lens - Codex Sol)**: `APPROVED`
   - *Finding*: Enclosing AST container enrichment leverages existing `_enclosing_symbol_for_line` in `repo_map.py` without mutating existing raw ripgrep-parity contracts (`--format rg` remains deterministic). Adding `--enrich-ast` to `tg search` preserves backward compatibility.
3. **Seat 3 (Security & Adversarial Lens - Opus/Gemini)**: `APPROVED`
   - *Finding*: Config writes are atomic using temporary sibling files and `os.replace`. Path traversal is blocked by resolving explicit standard user config paths (`Path.home() / ...`).
4. **Seat 4 (Performance & Guardrails Lens - Nemotron/GLM)**: `APPROVED`
   - *Finding*: Hard `AST_ENRICH_FILE_LIMIT = 100` bounds AST parsing latency. Tree-sitter AST nodes are cached per-file during search formatting so multi-line matches in the same file cost zero additional parse time.

**Council Consensus:** **UNANIMOUS APPROVAL (4/4)** — Plan certified ready for build.

