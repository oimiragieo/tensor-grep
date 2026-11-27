# Upgrading from v1.x to v2.0

This guide walks you through upgrading `tensor-grep` (`tg`) from the `v1.x` line to `v2.0.0` (incorporating Waves 5–8). Every step is written so you can verify your environment before and after the change.

## What changed

1. **Progressive 12-command core workflow (`tg --help` vs `tg --help-all`)**
   - Running `tg --help` now shows the **12 core workflow commands** (`search`, `find`, `run`, `scan`, `orient`, `defs`, `callers`, `blast-radius`, `prepare`, `diff-impact`, `checkpoint`, `mcp`) so new users and AI agents can find the right command immediately.
   - All other commands remain available—run `tg --help-all` to list the full command catalog.

2. **Human-first plain-text defaults (opt-in `--json`)**
   - Planning and orientation commands such as `tg prepare`, `tg orient`, and `tg edit-plan` now emit concise human-readable text when `--json` is omitted.
   - Scripts and AI harnesses should pass `--json` (or `--json --compact` for a bounded `<= 4,000`-byte JSON payload).

3. **Compact 15-tool MCP surface (`mcp>=2.0.0`)**
   - `tg mcp` now runs on `mcp>=2` (`MCPServer`) and exposes **15 compact tools** (`< 12 KB` tool catalog) by default, including `tg_batch` step pipelines (`$prev.<field>`, `$steps[i].<field>`, `return_final_only=True`).
   - Legacy clients that expect all 61 individual tool names can set `TG_MCP_LEGACY_TOOLS=1`.

4. **Unified `.tg_cache/` and automatic `.gitignore`**
   - Search indexes (`.tg_cache/index_v1/*.tg_index`), AST caches (`.tg_cache/ast/`), symbol caches (`.tg_cache/symbols_v1/`), and pre-change test graphs (`.tg_cache/graph_v1/tdad_tests.sqlite3`) live under `.tg_cache/` with an auto-created `.tg_cache/.gitignore` so clean repositories stay untracked.
   - Durable sessions and rollback checkpoints remain under `.tensor-grep/sessions/` and `.tensor-grep/checkpoints/`.

5. **Modular CLI and Rust internals (`typer>=0.26` compatible)**
   - Python handlers are organized into `src/tensor_grep/cli/routers/`, `navigation/`, `agent/`, `mcp/`, and `state/`.
   - Native Rust CLI and search modules are organized under `rust_core/src/cli_app/` and `rust_core/src/native_search/`.

## Step-by-step upgrade

### 1. Check your current installation

Before upgrading, see which `tg` binary or Python environment is active:

```bash
tg --version
tg doctor --fast --json
```

### 2. Upgrade `tg`

- **Managed install (recommended):**
  - Linux / macOS:
    ```bash
    curl -LsSf https://raw.githubusercontent.com/oimiragieo/tensor-grep/main/scripts/install.sh | bash
    ```
  - Windows PowerShell:
    ```powershell
    irm https://raw.githubusercontent.com/oimiragieo/tensor-grep/main/scripts/install.ps1 | iex
    ```
- **Python package (`pip` or `uv`):**
  ```bash
  python -m pip install --upgrade tensor-grep
  ```
  If you use structural rulesets or AST navigation, include the extras:
  ```bash
  python -m pip install --upgrade "tensor-grep[ast,scan]"
  ```

### 3. Verify the upgraded environment

Open a new terminal and confirm the version, core help, and local health:

```bash
tg --version
tg --help
tg doctor --fast --json
```

If you have existing scripts that call `tg prepare`, `tg orient`, or `tg edit-plan` and parse stdout as JSON, add `--json` (or `--json --compact`) to those invocations.

If you connect an MCP client that requires the legacy 61-tool list, add `TG_MCP_LEGACY_TOOLS=1` to its server environment configuration.
