# AST Parity Remediation Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Restore the promised `ast-grep`-compatible structural-search and rewrite surface in `tg`, without regressing the native AST line or mixing in unrelated performance work.

**Architecture:** Begin with re-validation because some AST gaps may already be closed in the current line. Convert each remaining reported gap into a narrow contract test, fix exactly one parity surface at a time, and keep payload-shape changes separate from workflow or rewrite changes. Benchmark only after correctness is locked for that specific AST surface.

**Tech Stack:** Python CLI, Python AST wrapper backend, native Rust AST backend/workflow, `pytest`, Rust integration tests, AST parity harnesses

---

### Task 1: Re-Validate the Current AST Backlog Against the Live Code

**Files:**
- Modify: `docs/PAPER.md` only if the historical parity read must be corrected after verification
- No code changes unless a contract test proves a live mismatch

- [ ] **Step 1: Run the existing AST contract cluster**

Run: `uv run pytest tests/unit/test_ast_backend.py -q`

Run: `uv run pytest tests/unit/test_ast_wrapper_backend.py -q`

Run: `uv run pytest tests/unit/test_ast_workflows.py -q`

Run: `uv run pytest tests/integration/test_cross_backend.py -q`

Run: `C:\\Users\\oimir\\.cargo\\bin\\cargo.exe test --manifest-path rust_core/Cargo.toml --test test_ast_backend -- --nocapture`

- [ ] **Step 2: Run the AST parity harness**

Run: `python benchmarks/run_ast_parity_check.py`

Record which externally reported gaps still reproduce on the current line.

- [ ] **Step 3: Convert only live failures into implementation tasks**

If an externally reported AST issue is already fixed, remove it from the execution backlog instead of “fixing” it again.

- [ ] **Step 4: Commit only if backlog tracking docs changed**

```bash
git add docs/PAPER.md
git commit -m "docs: refresh verified ast parity backlog"
```

### Task 2: Lock Rewrite Preview Diff Parity

**Files:**
- Modify: `tests/unit/test_mcp_server.py`
- Modify: `tests/integration/test_harness_adoption.py`
- Modify: `rust_core/tests/test_schema_compat.rs`
- Modify as needed: `src/tensor_grep/cli/mcp_server.py`, `rust_core/src/backend_ast.rs`, `rust_core/src/main.rs`

- [ ] **Step 1: Add or tighten the failing preview-diff test**

```python
def test_rewrite_preview_returns_unified_diff_payload(...):
    payload = ...
    assert payload["diff"]
    assert "---" in payload["diff"]
    assert "+++" in payload["diff"]
```

- [ ] **Step 2: Run the red phase**

Run: `uv run pytest tests/unit/test_mcp_server.py -k rewrite_preview -q`

Run: `uv run pytest tests/integration/test_harness_adoption.py -k rewrite_diff -q`

- [ ] **Step 3: Fix preview-only behavior**

```python
# Preserve plan/apply behavior; only ensure preview carries the diff contract through.
payload["diff"] = native_result.diff
```

Do not change apply semantics or unrelated AST JSON in this slice.

- [ ] **Step 4: Run the green phase**

Run: `uv run pytest tests/unit/test_mcp_server.py -k rewrite_preview -q`

Run: `uv run pytest tests/integration/test_harness_adoption.py -k rewrite_diff -q`

Run: `C:\\Users\\oimir\\.cargo\\bin\\cargo.exe test --manifest-path rust_core/Cargo.toml test_ast_rewrite -- --nocapture`

- [ ] **Step 5: Commit**

```bash
git add tests/unit/test_mcp_server.py tests/integration/test_harness_adoption.py rust_core/tests/test_schema_compat.rs src/tensor_grep/cli/mcp_server.py rust_core/src/backend_ast.rs
git commit -m "fix(ast): preserve rewrite preview diffs across wrappers"
```

### Task 3: Restore Richer AST JSON Parity

**Files:**
- Modify: `tests/unit/test_ast_wrapper_backend.py`
- Modify: `tests/unit/test_cli_modes.py`
- Modify: `tests/integration/test_cross_backend.py`
- Modify: `rust_core/tests/test_schema_compat.rs`
- Modify as needed: `src/tensor_grep/backends/ast_wrapper_backend.py`, `src/tensor_grep/cli/main.py`, `rust_core/src/main.rs`, `rust_core/src/backend_ast_workflow.rs`

- [ ] **Step 1: Add the missing-field test**

```python
def test_ast_wrapper_json_preserves_range_and_meta_variables(...):
    payload = ...
    assert "range" in payload["matches"][0]
    assert "metaVariables" in payload["matches"][0]
```

- [ ] **Step 2: Run the red phase**

Run: `uv run pytest tests/unit/test_ast_wrapper_backend.py -k json -q`

Run: `uv run pytest tests/integration/test_cross_backend.py -k ast -q`

- [ ] **Step 3: Thread through the missing fields without changing routing**

```python
match = {
    "text": raw["text"],
    "range": raw["range"],
    "metaVariables": raw.get("metaVariables", {}),
}
```

Avoid changing backend selection or CLI argument parsing in this slice.

- [ ] **Step 4: Run the green phase**

Run: `uv run pytest tests/unit/test_ast_wrapper_backend.py -k json -q`

Run: `uv run pytest tests/integration/test_cross_backend.py -k ast -q`

- [ ] **Step 5: Commit**

```bash
git add tests/unit/test_ast_wrapper_backend.py tests/unit/test_cli_modes.py tests/integration/test_cross_backend.py rust_core/tests/test_schema_compat.rs src/tensor_grep/backends/ast_wrapper_backend.py
git commit -m "fix(ast): preserve richer ast-grep json fields"
```

### Task 4: Add Inline Rules Support

**Files:**
- Modify: `tests/unit/test_cli_modes.py`
- Modify: `tests/unit/test_mcp_server.py`
- Modify: `tests/unit/test_apply_policy.py`
- Modify as needed: `src/tensor_grep/cli/main.py`, `src/tensor_grep/cli/mcp_server.py`, `src/tensor_grep/cli/apply_policy.py`, `rust_core/src/backend_ast_workflow.rs`

- [ ] **Step 1: Add the CLI regression**

```python
def test_scan_supports_inline_rules_text(monkeypatch, tmp_path):
    result = CliRunner().invoke(
        app,
        [
            "scan",
            "--inline-rules",
            "id: no-console\nlanguage: JavaScript\nrule:\n  pattern: console.log($A)",
            str(tmp_path),
        ],
    )
    assert result.exit_code == 0
```

- [ ] **Step 2: Add the MCP parity regression**

```python
def test_mcp_scan_supports_inline_rules(...): ...
```

- [ ] **Step 3: Run the red phase**

Run: `uv run pytest tests/unit/test_cli_modes.py -k inline_rules -q`

Run: `uv run pytest tests/unit/test_mcp_server.py -k inline_rules -q`

- [ ] **Step 4: Implement inline rule plumbing only**

```python
inline_rules: str | None = typer.Option(
    None, "--inline-rules", help="Pass ast-grep rule YAML directly."
)
```

Map the flag through the native workflow or wrapper path without widening built-in ruleset behavior.

- [ ] **Step 5: Run the green phase**

Run: `uv run pytest tests/unit/test_cli_modes.py -k inline_rules -q`

Run: `uv run pytest tests/unit/test_mcp_server.py -k inline_rules -q`

Run: `uv run pytest tests/unit/test_apply_policy.py -k inline_rules -q`

- [ ] **Step 6: Commit**

```bash
git add tests/unit/test_cli_modes.py tests/unit/test_mcp_server.py tests/unit/test_apply_policy.py src/tensor_grep/cli/main.py src/tensor_grep/cli/mcp_server.py src/tensor_grep/cli/apply_policy.py
git commit -m "feat(ast): support inline scan rules"
```

### Task 5: Close AST Test Framework Gaps

**Files:**
- Modify: `tests/unit/test_cli_modes.py`
- Modify: `src/tensor_grep/cli/main.py`
- Modify as needed: `rust_core/src/backend_ast_workflow.rs`, `rust_core/src/main.rs`
- Optional docs after acceptance: `README.md`

- [ ] **Step 1: Define the narrowest parity target**

Use the official `ast-grep test` contract as the comparator. Start with one missing flag or snapshot behavior rather than the entire test framework at once.

- [ ] **Step 2: Add one failing contract test**

```python
def test_ast_test_supports_snapshot_dir(...): ...
```

or

```python
def test_ast_test_supports_skip_snapshot_tests(...): ...
```

Pick the highest-value missing behavior first and keep it isolated.

- [ ] **Step 3: Run the red phase**

Run: `uv run pytest tests/unit/test_cli_modes.py -k "snapshot_dir or skip_snapshot_tests" -q`

- [ ] **Step 4: Implement the smallest test-command parity change**

```python
snapshot_dir: Path | None = typer.Option(None, "--snapshot-dir")
skip_snapshot_tests: bool = typer.Option(False, "--skip-snapshot-tests")
```

- [ ] **Step 5: Run the green phase**

Run: `uv run pytest tests/unit/test_cli_modes.py -k "snapshot_dir or skip_snapshot_tests" -q`

- [ ] **Step 6: Commit**

```bash
git add tests/unit/test_cli_modes.py src/tensor_grep/cli/main.py rust_core/src/backend_ast_workflow.rs
git commit -m "feat(ast): extend tg test parity with ast-grep"
```

### Task 6: Full AST Validation and AST-Specific Benchmark

**Files:**
- Modify: `docs/PAPER.md` only if the accepted parity story changed
- Modify: `README.md` only if the user-facing AST contract changed materially

- [ ] **Step 1: Run the AST suites**

Run: `uv run pytest tests/unit/test_ast_backend.py -q`

Run: `uv run pytest tests/unit/test_ast_wrapper_backend.py -q`

Run: `uv run pytest tests/unit/test_ast_workflows.py -q`

Run: `uv run pytest tests/integration/test_cross_backend.py -q`

Run: `uv run pytest tests/integration/test_harness_adoption.py -q`

Run: `C:\\Users\\oimir\\.cargo\\bin\\cargo.exe test --manifest-path rust_core/Cargo.toml`

- [ ] **Step 2: Run the AST benchmark surfaces only if correctness moved a hot path**

Run: `python benchmarks/run_ast_benchmarks.py --output artifacts/bench_run_ast_benchmarks.json`

Run: `python benchmarks/run_ast_workflow_benchmarks.py --output artifacts/bench_run_ast_workflow_benchmarks.json`

- [ ] **Step 3: Apply docs discipline**

Update `docs/PAPER.md` only for accepted or rejected parity/perf conclusions that matter to future work. Do not rewrite the product story casually.

- [ ] **Step 4: Commit any accepted docs update separately**

```bash
git add README.md docs/PAPER.md
git commit -m "docs: record restored ast parity contracts"
```
