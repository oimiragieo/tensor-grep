# Text Parity Remediation Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Restore the broken `ripgrep`-compat text-search contracts in `tg search` and lock them behind unit, e2e, and output-contract tests.

**Architecture:** Keep this workstream strictly in the text-search control plane. Each issue starts from a failing test, lands the smallest fix in the CLI or backend layer, and proves parity with either raw-byte output assertions or existing golden-contract harnesses. Do not mix benchmark work into these tasks unless a fix changes the hot path and explicitly requires measurement.

**Tech Stack:** Python CLI (`typer`), Python search backends, Rust native CLI front door where necessary, `pytest`, snapshot/golden tests

---

### Task 1: Fix `-r` Capture Group Substitution

**Files:**
- Modify: `tests/unit/test_cli_modes.py`
- Modify: `tests/e2e/test_output_golden_contract.py`
- Modify: `src/tensor_grep/cli/main.py`
- Optional docs only if contract text needs correction: `README.md`, `docs/PAPER.md`

- [ ] **Step 1: Write the failing unit test**

```python
def test_replace_mode_expands_capture_groups_in_output(monkeypatch):
    global _FAKE_WALK, _FAKE_BACKEND
    _FAKE_WALK = {".": ["a.log"]}
    _FAKE_BACKEND = _FakeBackend(
        results_by_file={
            "a.log": SearchResult(
                matches=[MatchLine(line_number=1, text="line 1", file="a.log")],
                total_files=1,
                total_matches=1,
            )
        }
    )
    _patch_cli_dependencies(monkeypatch)

    runner = CliRunner()
    result = runner.invoke(app, ["search", r"line (\\d+)", ".", "-r", "LINE=$1"])

    assert result.exit_code == 0
    assert result.stdout.strip() == "LINE=1"
```

- [ ] **Step 2: Write the failing e2e contract case**

```python
(("replace_capture_group_single_file", ["-r", "LINE=$1", r"line (\\d+)"], TEXT_FILE1_TARGET),)
```

Add fixture content containing at least one numeric capture so the expected snapshot proves `$1` expansion.

- [ ] **Step 3: Run the narrow red phase**

Run: `uv run pytest tests/unit/test_cli_modes.py -k replace_mode_expands_capture_groups_in_output -q`

Run: `uv run pytest tests/e2e/test_output_golden_contract.py -k replace_capture_group_single_file -q`

Expected: both fail because `$1` is emitted literally today.

- [ ] **Step 4: Implement the minimal translation from ripgrep replacement syntax**

```python
def _translate_replace_template(template: str) -> str:
    # Convert rg-style $0/$1/$name/$$ to Python re.sub syntax.
    ...


def _replace_lines(
    matches: list[MatchLine], pattern: str, config: "SearchConfig"
) -> list[MatchLine]:
    ...
    replacement = _translate_replace_template(config.replace_str)
    new_text = regex.sub(replacement, match.text)
```

Keep the change scoped to replacement template handling. Do not change unrelated match extraction or formatter code in the same slice.

- [ ] **Step 5: Run the narrow green phase**

Run: `uv run pytest tests/unit/test_cli_modes.py -k replace_mode_expands_capture_groups_in_output -q`

Run: `uv run pytest tests/e2e/test_output_golden_contract.py -k replace_capture_group_single_file -q`

Expected: both pass.

- [ ] **Step 6: Run the local text-parity regression cluster**

Run: `uv run pytest tests/unit/test_cli_modes.py -k "replace_mode or short_replace_mode" -q`

Run: `uv run pytest tests/e2e/test_output_golden_contract.py -k replace -q`

- [ ] **Step 7: Commit**

```bash
git add tests/unit/test_cli_modes.py tests/e2e/test_output_golden_contract.py src/tensor_grep/cli/main.py
git commit -m "fix(search): support rg-style replacement capture groups"
```

### Task 2: Fix `--files-without-match` False Positives

**Files:**
- Modify: `tests/unit/test_cli_modes.py`
- Modify: `src/tensor_grep/cli/main.py`
- Optional if backend file accounting proves wrong: `src/tensor_grep/backends/ripgrep_backend.py`

- [ ] **Step 1: Strengthen the failing test**

```python
def test_files_without_match_excludes_files_with_actual_matches(monkeypatch):
    global _FAKE_WALK, _FAKE_BACKEND
    _FAKE_WALK = {".": ["large.txt", "empty.txt"]}
    _FAKE_BACKEND = _FakeBackend(
        results_by_file={
            "large.txt": SearchResult(
                matches=[MatchLine(line_number=1, text="NEEDLE", file="large.txt")],
                matched_file_paths=["large.txt"],
                total_files=1,
                total_matches=100,
            ),
            "empty.txt": SearchResult(matches=[], total_files=0, total_matches=0),
        }
    )
    _patch_cli_dependencies(monkeypatch)

    result = CliRunner().invoke(app, ["search", "NEEDLE", ".", "--files-without-match"])

    assert result.exit_code == 0
    assert result.stdout.strip().splitlines() == ["empty.txt"]
```

- [ ] **Step 2: Run the red phase**

Run: `uv run pytest tests/unit/test_cli_modes.py -k files_without_match -q`

Expected: fail if the matched file still appears in the output.

- [ ] **Step 3: Fix matched-file accounting only**

```python
matched_files = set(matched_file_paths)
matched_files.update(m.file for m in all_results.matches)
all_results.matched_file_paths = sorted(matched_files)
```

If the bug comes from `total_files > 0` bookkeeping, fix that bookkeeping instead of widening the output branch.

- [ ] **Step 4: Run the green phase**

Run: `uv run pytest tests/unit/test_cli_modes.py -k files_without_match -q`

- [ ] **Step 5: Run adjacent listing-mode regressions**

Run: `uv run pytest tests/unit/test_cli_modes.py -k "files_with_matches or files_without_match" -q`

- [ ] **Step 6: Commit**

```bash
git add tests/unit/test_cli_modes.py src/tensor_grep/cli/main.py
git commit -m "fix(search): correct files-without-match accounting"
```

### Task 3: Fix `-0` / `--null` to Emit NUL Bytes

**Files:**
- Modify: `tests/e2e/test_output_golden_contract.py`
- Modify: `tests/unit/test_cli_modes.py`
- Modify: `src/tensor_grep/cli/main.py`
- Optional native follow-up slice only if Python and native share the same broken contract: `rust_core/src/main.rs`

- [ ] **Step 1: Add a raw-bytes regression test**

```python
def test_search_null_outputs_nul_separator(tmp_path):
    target = tmp_path / "sample.txt"
    target.write_text("hello\\n", encoding="utf-8")

    result = subprocess.run(
        [sys.executable, "-m", "tensor_grep", "search", "-l", "-0", "hello", str(target)],
        capture_output=True,
        check=False,
    )

    assert result.returncode == 0
    assert result.stdout.endswith(b"\\x00")
    assert b"\\r\\n" not in result.stdout
```

- [ ] **Step 2: Run the red phase**

Run: `uv run pytest tests/e2e/test_output_golden_contract.py -k null -q`

Expected: fail because text output still uses newline-delimited printing.

- [ ] **Step 3: Implement byte-accurate separator emission**

```python
def _write_path_list(paths: list[str], use_nul: bool) -> None:
    separator = b"\\x00" if use_nul else os.linesep.encode()
    payload = separator.join(path.encode("utf-8") for path in paths)
    if use_nul:
        payload += b"\\x00"
    sys.stdout.buffer.write(payload)
```

Route `--files-with-matches`, `--files-without-match`, and any file-path-only output through one helper so the contract cannot drift again.

- [ ] **Step 4: Run the green phase**

Run: `uv run pytest tests/e2e/test_output_golden_contract.py -k null -q`

- [ ] **Step 5: Run adjacent file-list output tests**

Run: `uv run pytest tests/unit/test_cli_modes.py -k "files_with_matches or files_without_match or files_mode" -q`

- [ ] **Step 6: Commit**

```bash
git add tests/e2e/test_output_golden_contract.py tests/unit/test_cli_modes.py src/tensor_grep/cli/main.py
git commit -m "fix(search): emit nul separators for file path output"
```

### Task 4: Support `--files` Without Requiring a Pattern

**Files:**
- Modify: `tests/unit/test_cli_modes.py`
- Modify: `tests/unit/test_cli_bootstrap.py`
- Modify: `src/tensor_grep/cli/main.py`
- Optional native-front-door follow-up if required: `rust_core/src/main.rs`

- [ ] **Step 1: Add the contract tests**

```python
def test_files_mode_lists_candidates_without_pattern(monkeypatch):
    global _FAKE_WALK
    _FAKE_WALK = {".": ["a.py", "b.py"]}
    _patch_cli_dependencies(monkeypatch)

    result = CliRunner().invoke(app, ["search", "--files", "."])

    assert result.exit_code == 0
    assert result.stdout.strip().splitlines() == ["a.py", "b.py"]
```

```python
def test_bootstrap_preserves_files_mode_without_pattern(monkeypatch):
    monkeypatch.setattr(sys, "argv", ["tg", "search", "--files", "."])
    ...
```

- [ ] **Step 2: Run the red phase**

Run: `uv run pytest tests/unit/test_cli_modes.py -k files_mode_lists_candidates_without_pattern -q`

Run: `uv run pytest tests/unit/test_cli_bootstrap.py -k files_mode_without_pattern -q`

- [ ] **Step 3: Relax parsing only for `--files`**

```python
pattern: str | None = typer.Argument(None)
...
if files:
    list_files(...)
    raise typer.Exit(0)
if not pattern:
    raise typer.BadParameter("PATTERN is required unless --files is set.")
```

Do not make the pattern optional for normal search paths.

- [ ] **Step 4: Run the green phase**

Run: `uv run pytest tests/unit/test_cli_modes.py -k files_mode_lists_candidates_without_pattern -q`

Run: `uv run pytest tests/unit/test_cli_bootstrap.py -k files_mode_without_pattern -q`

- [ ] **Step 5: Run adjacent files-mode regressions**

Run: `uv run pytest tests/unit/test_cli_modes.py -k files_mode -q`

- [ ] **Step 6: Commit**

```bash
git add tests/unit/test_cli_modes.py tests/unit/test_cli_bootstrap.py src/tensor_grep/cli/main.py
git commit -m "fix(search): allow files mode without a pattern"
```

### Task 5: Fix `--glob-case-insensitive`

**Files:**
- Modify: `tests/unit/test_directory_scanner.py`
- Modify: `tests/unit/test_cli_bootstrap.py`
- Modify: `src/tensor_grep/io/directory_scanner.py`
- Optional if forwarding is broken too: `src/tensor_grep/backends/ripgrep_backend.py`, `rust_core/src/main.rs`

- [ ] **Step 1: Add a semantic regression test**

```python
def test_should_include_file_honors_glob_case_insensitive():
    assert _should_include_file(
        "SRC/MAIN.PY",
        globs=["*.py"],
        glob_case_insensitive=True,
    )
```

- [ ] **Step 2: Add a CLI/bootstrap regression**

```python
def test_main_entry_routes_glob_case_insensitive_without_crashing(monkeypatch):
    monkeypatch.setattr(
        sys, "argv", ["tg", "search", "hello", ".", "-g", "*.py", "--glob-case-insensitive"]
    )
    ...
```

- [ ] **Step 3: Run the red phase**

Run: `uv run pytest tests/unit/test_directory_scanner.py -k glob_case_insensitive -q`

Run: `uv run pytest tests/unit/test_cli_bootstrap.py -k glob_case_insensitive -q`

- [ ] **Step 4: Implement case-insensitive glob matching**

```python
candidate = relative_path.casefold() if glob_case_insensitive else relative_path
pattern = glob.casefold() if glob_case_insensitive else glob
matched = fnmatch.fnmatch(candidate, pattern)
```

Only add forwarding if the CLI test proves the flag is dropped before scanning.

- [ ] **Step 5: Run the green phase**

Run: `uv run pytest tests/unit/test_directory_scanner.py -k glob_case_insensitive -q`

Run: `uv run pytest tests/unit/test_cli_bootstrap.py -k glob_case_insensitive -q`

- [ ] **Step 6: Commit**

```bash
git add tests/unit/test_directory_scanner.py tests/unit/test_cli_bootstrap.py src/tensor_grep/io/directory_scanner.py
git commit -m "fix(search): honor glob-case-insensitive matching"
```

### Task 6: Close Output-Contract Gaps for `--json`, `--stats`, and `--debug`

**Files:**
- Modify: `tests/e2e/test_output_golden_contract.py`
- Modify: `tests/e2e/test_routing_parity.py`
- Modify: `tests/unit/test_cli_modes.py`
- Modify as needed: `src/tensor_grep/cli/main.py`, `src/tensor_grep/backends/ripgrep_backend.py`, `src/tensor_grep/cli/bootstrap.py`, `rust_core/src/main.rs`

- [ ] **Step 1: Capture the real contract from the repo-owned comparator**

Run: `python -c "import shutil; print(shutil.which('rg'))"`

Run: `rg --help`

Use the repo-owned or explicit comparator binary when available. Record the exact supported combinations before changing code.

- [ ] **Step 2: Add one failing parity test per output surface**

```python
def test_search_json_contract_matches_expected_envelope(...): ...
def test_search_stats_contract_uses_stderr_only(...): ...
def test_search_debug_contract_does_not_corrupt_stdout(...): ...
```

Do not mix all three fixes into one test.

- [ ] **Step 3: Run the red phase**

Run: `uv run pytest tests/unit/test_cli_modes.py -k "json_contract or stats_contract or debug_contract" -q`

Run: `uv run pytest tests/e2e/test_routing_parity.py -k "json or stats or debug" -q`

- [ ] **Step 4: Fix one output surface at a time**

```python
# Example shape, not one mega-change:
if stats:
    typer.echo(stats_line, err=True)
```

Keep stdout/stderr separation, JSON structure, and routing metadata behavior isolated per failing contract.

- [ ] **Step 5: Run the green phase**

Run: `uv run pytest tests/unit/test_cli_modes.py -k "json_contract or stats_contract or debug_contract" -q`

Run: `uv run pytest tests/e2e/test_output_golden_contract.py -k "json or stats or debug" -q`

- [ ] **Step 6: Run the full text-parity cluster**

Run: `uv run pytest tests/unit/test_cli_modes.py -q`

Run: `uv run pytest tests/unit/test_cli_bootstrap.py -q`

Run: `uv run pytest tests/unit/test_directory_scanner.py -q`

Run: `uv run pytest tests/e2e/test_output_golden_contract.py -q`

Run: `uv run pytest tests/e2e/test_routing_parity.py -q`

- [ ] **Step 7: Commit**

```bash
git add tests/e2e/test_output_golden_contract.py tests/e2e/test_routing_parity.py tests/unit/test_cli_modes.py src/tensor_grep/cli/main.py
git commit -m "fix(search): align output contracts with ripgrep-compatible modes"
```

### Task 7: Full Validation for the Text Workstream

**Files:**
- Modify only if accepted contract text changed: `README.md`
- Modify only if remediation history matters: `docs/PAPER.md`

- [ ] **Step 1: Run the repo gates**

Run: `uv run ruff check .`

Run: `uv run mypy src/tensor_grep`

Run: `uv run pytest -q`

- [ ] **Step 2: Decide whether docs move**

If a previously promised text-search contract is now restored and should be stated clearly, update the smallest truthful doc surface only.

- [ ] **Step 3: Commit any accepted docs update separately**

```bash
git add README.md docs/PAPER.md
git commit -m "docs: record restored text parity contracts"
```
