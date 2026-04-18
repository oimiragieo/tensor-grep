# Platform Compatibility Remediation Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Eliminate platform-boundary failures that block parity on Windows and shell environments without mixing in unrelated search-logic or benchmark work.

**Architecture:** Keep this workstream at the transport boundary: output encoding, terminal-safe emission, and shell completion surfaces. Each fix starts from an environment-simulating regression test and is scoped so that search semantics stay unchanged.

**Tech Stack:** Python CLI, Rust native CLI, Windows encoding behavior, shell completion generation, `pytest`

---

### Task 1: Fix Windows `cp1252` AST Output Crashes

**Files:**
- Modify: `tests/unit/test_ast_workflows.py`
- Modify: `tests/unit/test_cli_modes.py`
- Modify as needed: `src/tensor_grep/cli/ast_workflows.py`, `src/tensor_grep/cli/main.py`, `rust_core/src/main.rs`

- [ ] **Step 1: Add a targeted encoding regression**

```python
def test_ast_output_falls_back_when_stdout_encoding_cannot_encode(monkeypatch, capsys):
    class _Cp1252Stream:
        encoding = "cp1252"

        def write(self, text: str) -> int:
            text.encode("cp1252")
            return len(text)

    ...
```

Make the sample AST match contain non-ASCII text that `cp1252` cannot encode.

- [ ] **Step 2: Run the red phase**

Run: `uv run pytest tests/unit/test_ast_workflows.py -k encoding -q`

- [ ] **Step 3: Implement encoding-safe output**

```python
def _safe_echo(text: str) -> None:
    try:
        typer.echo(text)
    except UnicodeEncodeError:
        sys.stdout.buffer.write(text.encode("utf-8", errors="replace"))
```

Scope the helper to AST emission only in this slice.

- [ ] **Step 4: Run the green phase**

Run: `uv run pytest tests/unit/test_ast_workflows.py -k encoding -q`

Run: `uv run pytest tests/unit/test_cli_modes.py -k ast -q`

- [ ] **Step 5: Commit**

```bash
git add tests/unit/test_ast_workflows.py tests/unit/test_cli_modes.py src/tensor_grep/cli/ast_workflows.py
git commit -m "fix(platform): avoid cp1252 ast output crashes"
```

### Task 2: Audit and Lock Shell Completion Generation

**Files:**
- Modify: `tests/unit/test_cli_modes.py`
- Modify: `src/tensor_grep/cli/main.py`
- Optional docs after acceptance: `README.md`, `docs/installation.md`

- [ ] **Step 1: Add contract tests for every supported generator**

```python
def test_search_generate_supports_bash_completion(): ...
def test_search_generate_supports_zsh_completion(): ...
def test_search_generate_supports_fish_completion(): ...
def test_search_generate_supports_powershell_completion(): ...
```

If a mode is intentionally unsupported, prove that with a clear error contract instead of silent absence.

- [ ] **Step 2: Run the red phase**

Run: `uv run pytest tests/unit/test_cli_modes.py -k generate -q`

- [ ] **Step 3: Implement or repair the completion surface**

```python
if generate == "complete-bash":
    ...
elif generate == "complete-powershell":
    ...
else:
    raise typer.BadParameter("unsupported generator")
```

Keep the slice limited to generation and error contracts. Do not refactor unrelated CLI help output.

- [ ] **Step 4: Run the green phase**

Run: `uv run pytest tests/unit/test_cli_modes.py -k generate -q`

- [ ] **Step 5: Commit**

```bash
git add tests/unit/test_cli_modes.py src/tensor_grep/cli/main.py
git commit -m "feat(platform): complete shell completion generation surface"
```

### Task 3: Update Installation and Front-Door Docs for Platform Parity

**Files:**
- Modify only if Tasks 1-2 are accepted: `README.md`, `docs/installation.md`

- [ ] **Step 1: Verify that the platform surface is truly restored**

Run: `uv run pytest tests/unit/test_ast_workflows.py -q`

Run: `uv run pytest tests/unit/test_cli_modes.py -k generate -q`

- [ ] **Step 2: Update the smallest truthful doc surfaces**

Document supported completion generators and any Windows encoding guarantees that are now actually tested. Do not claim universal terminal parity beyond what the tests prove.

- [ ] **Step 3: Commit docs separately**

```bash
git add README.md docs/installation.md
git commit -m "docs: record platform compatibility guarantees"
```

### Task 4: Full Platform Validation

**Files:**
- No file changes expected

- [ ] **Step 1: Run the repo gates**

Run: `uv run ruff check .`

Run: `uv run mypy src/tensor_grep`

Run: `uv run pytest -q`

- [ ] **Step 2: Record any residual platform-specific skips**

If a platform-specific test still has to skip, document the real reason in the test itself instead of hiding it in release notes.
