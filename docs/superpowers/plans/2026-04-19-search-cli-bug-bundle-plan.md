# Search CLI Bug Bundle Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Fix the reproducible `tg search` correctness bugs around binary fixed-string reads, empty patterns, and missing paths, while adding a non-hanging structured-output regression contract.

**Architecture:** Keep the change set small. Add one shared text-loading helper in `StringZillaBackend`, add early argument/path validation in `search_command()`, and cover each behavior with focused tests before changing production code.

**Tech Stack:** Typer CLI, Python backends, pytest, subprocess-based CLI regression tests.

---

### Task 1: Add the failing backend safety tests

**Files:**
- Modify: `tests/unit/test_stringzilla_backend.py`
- Modify: `src/tensor_grep/backends/stringzilla_backend.py`

- [ ] **Step 1: Write the failing binary-safety tests**

Add tests covering:
- a fixed-string search over a binary/invalid-UTF8 file returns an empty result instead of raising
- `--text` / binary-opt-in behavior still allows the backend to search replacement-decoded content

- [ ] **Step 2: Run the focused test file to verify the new test fails**

Run: `uv run pytest tests/unit/test_stringzilla_backend.py -q`

- [ ] **Step 3: Implement the minimal shared text-loading helper**

Use one helper in `StringZillaBackend` for both `_search_with_index()` and `search()` so strict UTF-8 decode failures and obvious binary files return an empty `SearchResult` unless binary/text mode is explicitly enabled.

- [ ] **Step 4: Re-run the focused backend tests**

Run: `uv run pytest tests/unit/test_stringzilla_backend.py -q`

- [ ] **Step 5: Keep the helper scoped**

Do not redesign encoding support beyond the documented skip-or-replace behavior.

### Task 2: Add the failing CLI argument-validation tests

**Files:**
- Modify: `tests/unit/test_cli_modes.py`
- Modify: `src/tensor_grep/cli/main.py`

- [ ] **Step 1: Write the failing empty-pattern and missing-path tests**

Add tests covering:
- `tg search "" file.py` exits `2` and prints a usage-style error
- `tg search foo missing-path` exits `2` and prints a clear missing-path error

- [ ] **Step 2: Run only the new CLI tests to verify they fail**

Run the specific pytest node ids for the new tests.

- [ ] **Step 3: Add early validation in `search_command()`**

Validate:
- empty `pattern`
- nonexistent `paths_to_search`

Return clear errors before scanner/pipeline setup.

- [ ] **Step 4: Re-run the focused CLI tests**

Run the same node ids and verify green.

### Task 3: Add the structured-output non-hang regression contract

**Files:**
- Modify: `tests/e2e/test_routing_parity.py` or `tests/unit/test_cli_modes.py`

- [ ] **Step 1: Add a subprocess contract test for `--json`**

The test should run `tg search ... --json` with a short timeout and assert:
- the process exits before timeout
- `stdout` is non-empty structured output

- [ ] **Step 2: Run that focused test**

Run only the new node id and verify it passes on current behavior.

- [ ] **Step 3: Avoid speculative production changes**

If the regression contract passes, do not edit serializer/flush code in this task bundle.

### Task 4: Full validation and benchmarks

**Files:**
- No new code expected unless validation exposes a real issue

- [ ] **Step 1: Run lint**

Run: `uv run ruff check .`

- [ ] **Step 2: Run typing**

Run: `uv run mypy src/tensor_grep`

- [ ] **Step 3: Run the full suite**

Run: `uv run pytest -q`

- [ ] **Step 4: Run the CLI benchmark**

Run: `python benchmarks/run_benchmarks.py --output artifacts/bench_run_benchmarks.search_cli_bug_bundle.json`

- [ ] **Step 5: Check benchmark regression**

Run: `python benchmarks/check_regression.py --baseline auto --current artifacts/bench_run_benchmarks.search_cli_bug_bundle.json`

- [ ] **Step 6: Update docs only if user-facing behavior changed materially**

If the CLI error behavior warrants it, update the relevant docs/help text. Do not add performance claims.
