# v1.4.3 Count-Path Bootstrap Overhead Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Reduce default `tg search -c` overhead by skipping native-launcher resolution on rg-passthrough count searches, while hardening native-launcher detection for the shapes that already use native delegation.

**Architecture:** First harden native binary resolution so Windows Python launcher shims are never treated as native binaries. Then reorder bootstrap search routing so rg-passthrough searches do not resolve native `tg` at all unless the search shape can actually use it. Keep the existing native-trigger set unchanged.

**Tech Stack:** Python CLI bootstrap, runtime path resolution, Typer tests, benchmark harness

---

## Execution Outcome

- Task 1 landed and stayed: the runtime-path resolver now rejects foreign Windows `PythonXY\Scripts\tg.exe` launchers, and the focused regression test remains in the branch.
- The editable `uv.lock` version was also corrected to match the shipped `1.4.2` package metadata so `scripts/validate_release_assets.py` passes honestly.
- Task 2 was implemented, tested, and benchmarked, then reverted after `artifacts/bench_run_benchmarks.count_path_candidate_uv.json` failed the frozen regression gate on the target `5. Count Matches` row.
- Task 3 still applies to the surviving diff: rerun repo gates, preserve the rejected attempt in `docs/PAPER.md`, and do not market this branch as a `perf:` release.

---

### Task 1: Harden Trusted Native Binary Resolution

**Files:**
- Modify: `src/tensor_grep/cli/runtime_paths.py`
- Modify: `tests/unit/test_runtime_paths.py`

- [ ] **Step 1: Write the failing test for foreign Windows launcher shims**

```python
@pytest.mark.skipif(not sys.platform.startswith("win"), reason="Windows launcher layout")
def test_resolve_native_tg_binary_ignores_foreign_python_install_scripts_launcher(
    monkeypatch, tmp_path
):
    repo_root = tmp_path / "repo"
    runtime_file = repo_root / "src" / "tensor_grep" / "cli" / "runtime_paths.py"
    runtime_file.parent.mkdir(parents=True, exist_ok=True)
    runtime_file.write_text("# stub\n", encoding="utf-8")

    current_venv_dir = tmp_path / "current-venv" / "Scripts"
    current_venv_dir.mkdir(parents=True, exist_ok=True)
    current_python = current_venv_dir / "python.exe"
    current_python.write_text("python\n", encoding="utf-8")

    foreign_python_root = tmp_path / "Python314"
    foreign_scripts_dir = foreign_python_root / "Scripts"
    foreign_scripts_dir.mkdir(parents=True, exist_ok=True)
    (foreign_python_root / "python.exe").write_text("python\n", encoding="utf-8")
    foreign_tg = foreign_scripts_dir / "tg.exe"
    foreign_tg.write_text("launcher\n", encoding="utf-8")

    monkeypatch.setattr(runtime_paths, "__file__", str(runtime_file))
    monkeypatch.setattr(runtime_paths.sys, "executable", str(current_python))
    monkeypatch.delenv("TG_NATIVE_TG_BINARY", raising=False)
    monkeypatch.delenv("TG_MCP_TG_BINARY", raising=False)
    monkeypatch.setattr(
        runtime_paths.shutil,
        "which",
        lambda name: str(foreign_tg) if name in {"tg", "tg.exe"} else None,
    )
    resolve_native_tg_binary.cache_clear()

    assert resolve_native_tg_binary() is None
```

- [ ] **Step 2: Run the focused test and verify RED**

Run: `uv run pytest tests/unit/test_runtime_paths.py -k foreign_python_install_scripts_launcher -q`

Expected: FAIL because the current resolver treats `Python314\Scripts\tg.exe` as a native binary.

- [ ] **Step 3: Implement the minimal trusted-launcher guard**

```python
def _looks_like_python_scripts_launcher(candidate: Path) -> bool:
    candidate_bin_dirs = {candidate.parent}
    try:
        candidate_bin_dirs.add(candidate.resolve().parent)
    except OSError:
        pass
    if not _current_python_bin_dirs().isdisjoint(candidate_bin_dirs):
        return True

    if sys.platform.startswith("win") and candidate.parent.name.lower() == "scripts":
        python_root = candidate.parent.parent
        return (python_root / "python.exe").is_file() or (python_root / "pythonw.exe").is_file()

    return False
```

- [ ] **Step 4: Re-run the focused test and verify GREEN**

Run: `uv run pytest tests/unit/test_runtime_paths.py -k foreign_python_install_scripts_launcher -q`

Expected: PASS

### Task 2: Skip Native Resolution For `-c` Bootstrap Searches

**Files:**
- Modify: `src/tensor_grep/cli/bootstrap.py`
- Modify: `tests/unit/test_cli_bootstrap.py`

- [ ] **Step 1: Write the failing bootstrap resolution-order test**

```python
def test_main_entry_should_passthrough_count_flag_without_native_resolution(monkeypatch):
    seen: dict[str, object] = {}

    monkeypatch.setattr(sys, "argv", ["tg", "search", "-c", "ERROR", "."])
    monkeypatch.setattr(
        bootstrap,
        "resolve_native_tg_binary",
        lambda: pytest.fail("native tg resolution should not run"),
    )
    monkeypatch.setattr(bootstrap, "resolve_ripgrep_binary", lambda: "rg")
    monkeypatch.setattr(
        bootstrap,
        "_run_rg_passthrough",
        lambda binary_name, search_args: (
            seen.update({"binary_name": binary_name, "search_args": list(search_args)}) or 0
        ),
    )
    monkeypatch.setattr(bootstrap, "_run_full_cli", lambda: pytest.fail("full cli should not run"))

    with pytest.raises(SystemExit) as excinfo:
        bootstrap.main_entry()

    assert excinfo.value.code == 0
    assert seen == {"binary_name": "rg", "search_args": ["-c", "ERROR", "."]}
```

- [ ] **Step 2: Run the focused bootstrap test and verify RED**

Run: `uv run pytest tests/unit/test_cli_bootstrap.py -k passthrough_count_flag_without_native_resolution -q`

Expected: FAIL because bootstrap currently resolves native `tg` before taking the rg passthrough lane.

- [ ] **Step 3: Implement the smallest routing-order change**

```python
requires_full_cli = _requires_full_cli(search_args)
should_try_native = _can_delegate_to_native_tg_search(search_args) or (
    _prefer_rust_first_search() and not requires_full_cli
)

if should_try_native:
    native_binary_path = resolve_native_tg_binary()
    native_binary = str(native_binary_path) if native_binary_path else None
    if native_binary is not None:
        raise SystemExit(_run_native_tg_search(native_binary, search_args))
```

- [ ] **Step 4: Re-run the focused bootstrap test and verify GREEN**

Run: `uv run pytest tests/unit/test_cli_bootstrap.py -k passthrough_count_flag_without_native_resolution -q`

Expected: PASS

- [ ] **Step 5: Run the full narrow suites**

Run: `uv run pytest tests/unit/test_runtime_paths.py -q`

Run: `uv run pytest tests/unit/test_cli_bootstrap.py -q`

Expected: PASS

### Task 3: Validate And Benchmark The Candidate

**Files:**
- Modify only if the candidate is accepted: `docs/PAPER.md`

- [ ] **Step 1: Run the repo validation gates**

Run: `uv run ruff check .`

Run: `uv run mypy src/tensor_grep`

Run: `uv run pytest -q`

Run: `uv run python scripts/validate_release_assets.py`

- [ ] **Step 2: Measure the cold-path benchmark**

Run: `python benchmarks/run_benchmarks.py --output artifacts/bench_run_benchmarks.count_path_candidate.json`

Run: `python benchmarks/check_regression.py --baseline auto --current artifacts/bench_run_benchmarks.count_path_candidate.json`

- [ ] **Step 3: Extract the target row**

```python
import json

rows = json.load(
    open("artifacts/bench_run_benchmarks.count_path_candidate.json", encoding="utf-8")
)["rows"]
row = next(r for r in rows if r["name"] == "5. Count Matches")
print(row)
```

- [ ] **Step 4: Accept or reject honestly**

If `5. Count Matches` improves and the regression gate passes, keep the patch and record the accepted result in `docs/PAPER.md`.

If the gate regresses or the target row does not improve, revert the code change and record the rejected attempt in `docs/PAPER.md` instead of shipping it.

- [ ] **Step 5: Commit with patch-release intent if accepted**

```bash
git add src/tensor_grep/cli/runtime_paths.py src/tensor_grep/cli/bootstrap.py tests/unit/test_runtime_paths.py tests/unit/test_cli_bootstrap.py docs/PAPER.md
git commit -m "perf(search): reduce count-mode bootstrap overhead"
```
