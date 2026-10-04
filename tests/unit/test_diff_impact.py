"""Unit tests for tg diff-impact (P1 diff blast radius and review risk gate)."""

from __future__ import annotations

import json
import subprocess
from pathlib import Path
from typing import Any

import pytest
from typer.testing import CliRunner

import tensor_grep.cli.diff_impact as di

from tensor_grep.cli.diff_impact import (
    _calculate_risk_tier,
    _is_test_path,
    build_diff_blast_radius,
    extract_diff_hunks_from_git,
    map_changed_lines_to_symbols,
    parse_git_diff_hunks,
)
from tensor_grep.cli.main import app

runner = CliRunner()

SAMPLE_DIFF_1 = """diff --git a/src/tensor_grep/cli/demo.py b/src/tensor_grep/cli/demo.py
index 1111111..2222222 100644
--- a/src/tensor_grep/cli/demo.py
+++ b/src/tensor_grep/cli/demo.py
@@ -10,3 +10,5 @@ def foo():
+    x = 1
+    y = 2
@@ -25,0 +27,4 @@ def bar():
+    pass
+    pass
+    pass
+    pass
diff --git a/src/tensor_grep/cli/deleted.py b/dev/null
deleted file mode 100644
index 3333333..0000000 100644
--- a/src/tensor_grep/cli/deleted.py
+++ /dev/null
@@ -1,5 +0,0 @@
-def gone():
-    pass
"""

SAMPLE_DIFF_PURE_DELETION = """diff --git a/pkg/mod.py b/pkg/mod.py
index 4444444..5555555 100644
--- a/pkg/mod.py
+++ b/pkg/mod.py
@@ -15,4 +15,0 @@ def helper():
-    line1
-    line2
-    line3
-    line4
"""


def test_parse_git_diff_hunks_basic() -> None:
    parsed = parse_git_diff_hunks(SAMPLE_DIFF_1)
    demo_path = Path("src/tensor_grep/cli/demo.py")
    assert demo_path in parsed
    # Deleted file should be ignored
    deleted_path = Path("src/tensor_grep/cli/deleted.py")
    assert parsed[deleted_path] == []

    ranges = parsed[demo_path]
    # Hunk 1: @@ -10,3 +10,5 @@ -> lines 10 to 14
    # Hunk 2: @@ -25,0 +27,4 @@ -> lines 27 to 30
    assert ranges == [(10, 14), (27, 30)]


def test_parse_git_diff_hunks_pure_deletion() -> None:
    parsed = parse_git_diff_hunks(SAMPLE_DIFF_PURE_DELETION)
    mod_path = Path("pkg/mod.py")
    assert mod_path in parsed
    assert parsed[mod_path] == [(15, 15)]


def test_parse_git_diff_hunks_merges_adjacent() -> None:
    diff_text = """diff --git a/foo.py b/foo.py
--- a/foo.py
+++ b/foo.py
@@ -10,2 +10,2 @@
@@ -12,2 +12,2 @@
"""
    parsed = parse_git_diff_hunks(diff_text)
    foo_path = Path("foo.py")
    assert foo_path in parsed
    # 10..11 and 12..13 are adjacent, so merged into [(10, 13)]
    assert parsed[foo_path] == [(10, 13)]


def test_map_changed_lines_to_symbols(tmp_path: Path) -> None:
    file_path = tmp_path / "module.py"
    file_path.write_text(
        "class Worker:\n"  # Line 1
        "    def run(self):\n"  # Line 2
        "        pass\n"  # Line 3
        "\n"  # Line 4
        "def helper():\n"  # Line 5
        "    return 42\n",  # Line 6
        encoding="utf-8",
    )

    # Change on lines 2..3 touches Worker and run
    changed_map = {Path("module.py"): [(2, 3)]}
    symbols = map_changed_lines_to_symbols(changed_map, root=tmp_path)
    sym_names = [s["name"] for s in symbols]
    assert "Worker" in sym_names or "run" in sym_names

    # Change on line 5..6 touches helper
    changed_map_2 = {Path("module.py"): [(5, 6)]}
    symbols_2 = map_changed_lines_to_symbols(changed_map_2, root=tmp_path)
    sym_names_2 = [s["name"] for s in symbols_2]
    assert "helper" in sym_names_2
    assert "Worker" not in sym_names_2


def test_map_changed_lines_to_symbols_polyglot(tmp_path: Path) -> None:
    # 1. Go file
    go_file = tmp_path / "service.go"
    go_file.write_text(
        "package main\n\nfunc ProcessPayment(amount int) bool {\n    return amount > 0\n}\n",
        encoding="utf-8",
    )
    changed_go = {Path("service.go"): [(3, 4)]}
    symbols_go = map_changed_lines_to_symbols(changed_go, root=tmp_path)
    assert any(s["name"] == "ProcessPayment" for s in symbols_go)

    # 2. Rust file
    rs_file = tmp_path / "lib.rs"
    rs_file.write_text(
        "pub fn calculate_tax(subtotal: f64) -> f64 {\n    subtotal * 0.08\n}\n",
        encoding="utf-8",
    )
    changed_rs = {Path("lib.rs"): [(1, 2)]}
    symbols_rs = map_changed_lines_to_symbols(changed_rs, root=tmp_path)
    assert any(s["name"] == "calculate_tax" for s in symbols_rs)


def test_is_test_path() -> None:
    assert _is_test_path("tests/unit/test_app.py") is True
    assert _is_test_path("src/tests/helper.py") is True
    assert _is_test_path("src/my_test.go") is True
    assert _is_test_path("src/foo.test.ts") is True
    assert _is_test_path("src/tensor_grep/cli/main.py") is False


def test_calculate_risk_tier() -> None:
    assert _calculate_risk_tier(0.05, 1, 1) == "low"
    assert _calculate_risk_tier(0.2, 2, 2) == "medium"
    assert _calculate_risk_tier(0.1, 4, 2) == "medium"
    assert _calculate_risk_tier(0.5, 5, 5) == "high"
    assert _calculate_risk_tier(0.2, 12, 5) == "high"
    assert _calculate_risk_tier(0.8, 1, 1) == "critical"
    assert _calculate_risk_tier(0.2, 30, 2) == "critical"
    assert _calculate_risk_tier(0.2, 5, 60) == "critical"


def test_build_diff_blast_radius_clean(tmp_path: Path) -> None:
    res = build_diff_blast_radius(root=tmp_path, diff_text="")
    assert res["changed_files"] == []
    assert res["changed_symbols"] == []
    assert res["callers"] == []
    assert res["blast_radius_score"] == 0.0
    assert res["risk_tier"] == "low"
    assert res["partial"] is False
    assert res["downgrade_reasons"] == []


def test_build_diff_blast_radius_with_changes(tmp_path: Path, monkeypatch: Any) -> None:
    # Setup test workspace
    pkg = tmp_path / "pkg"
    pkg.mkdir()
    core_file = pkg / "core.py"
    core_file.write_text(
        "def compute():\n    return 100\n",
        encoding="utf-8",
    )
    user_file = pkg / "user.py"
    user_file.write_text(
        "from pkg.core import compute\n\ndef call_compute():\n    return compute()\n",
        encoding="utf-8",
    )
    test_file = tmp_path / "tests" / "test_core.py"
    test_file.parent.mkdir()
    test_file.write_text(
        "from pkg.core import compute\n\ndef test_compute():\n    assert compute() == 100\n",
        encoding="utf-8",
    )

    diff = (
        "diff --git a/pkg/core.py b/pkg/core.py\n"
        "--- a/pkg/core.py\n"
        "+++ b/pkg/core.py\n"
        "@@ -1,2 +1,2 @@\n"
        " def compute():\n"
        "-    return 100\n"
        "+    return 200\n"
    )

    payload = build_diff_blast_radius(root=tmp_path, diff_text=diff)
    assert "pkg/core.py" in payload["changed_files"]
    changed_syms = [s["name"] for s in payload["changed_symbols"]]
    assert "compute" in changed_syms

    # Downstream caller or affected files should include user.py or tests
    affected = [f.replace("\\", "/") for f in payload["affected_files"]]
    assert any("user.py" in f or "test_core.py" in f or "core.py" in f for f in affected)


def test_extract_diff_hunks_from_git_mocked(monkeypatch: Any) -> None:
    class DummyProc:
        returncode = 0
        stdout = SAMPLE_DIFF_1
        stderr = ""

    def dummy_run(*args: Any, **kwargs: Any) -> DummyProc:
        return DummyProc()

    monkeypatch.setattr("tensor_grep.cli.diff_impact.run_subprocess", dummy_run)
    hunks = extract_diff_hunks_from_git(ref="HEAD~1")
    assert Path("src/tensor_grep/cli/demo.py") in hunks


def test_cli_diff_impact_clean(monkeypatch: Any) -> None:
    # When diff returns empty, diff-impact exits 1 (0 matches / clean diff)
    monkeypatch.setattr(
        "tensor_grep.cli.diff_impact.extract_diff_hunks_from_git",
        lambda **kwargs: {},
    )
    result = runner.invoke(app, ["diff-impact", "--json"])
    assert result.exit_code == 1
    data = json.loads(result.stdout)
    assert data["changed_files"] == []


def test_cli_diff_impact_success_with_matches(monkeypatch: Any, tmp_path: Path) -> None:
    # Mock extract_diff_hunks_from_git to return a change
    demo_file = tmp_path / "sample.py"
    demo_file.write_text("def my_func():\n    return 1\n", encoding="utf-8")

    monkeypatch.setattr(
        "tensor_grep.cli.diff_impact.extract_diff_hunks_from_git",
        lambda **kwargs: {Path("sample.py"): [(1, 2)]},
    )

    def mock_build_diff(*args: Any, **kwargs: Any) -> dict[str, Any]:
        return {
            "root": str(tmp_path),
            "ref": None,
            "staged": False,
            "changed_files": ["sample.py"],
            "changed_symbols": [{"name": "my_func", "line": 1, "file": "sample.py"}],
            "callers": [],
            "affected_files": ["sample.py"],
            "affected_tests": [],
            "blast_radius_score": 0.05,
            "risk_tier": "low",
            "partial": False,
            "downgrade_reasons": [],
            "symbol_count": 1,
            "caller_count": 0,
            "file_count": 1,
            "test_count": 0,
        }

    monkeypatch.setattr("tensor_grep.cli.diff_impact.build_diff_blast_radius", mock_build_diff)

    result = runner.invoke(app, ["diff-impact", "--json"])
    assert result.exit_code == 0
    data = json.loads(result.stdout)
    assert data["blast_radius_score"] == 0.05
    assert data["risk_tier"] == "low"


def test_cli_diff_impact_fail_threshold_breached(monkeypatch: Any) -> None:
    def mock_build_diff(*args: Any, **kwargs: Any) -> dict[str, Any]:
        return {
            "root": ".",
            "ref": None,
            "staged": False,
            "changed_files": ["sample.py"],
            "changed_symbols": [{"name": "my_func", "line": 1, "file": "sample.py"}],
            "callers": [],
            "affected_files": ["sample.py"],
            "affected_tests": [],
            "blast_radius_score": 0.65,
            "risk_tier": "high",
            "partial": False,
            "downgrade_reasons": [],
            "symbol_count": 1,
            "caller_count": 0,
            "file_count": 1,
            "test_count": 0,
        }

    monkeypatch.setattr("tensor_grep.cli.diff_impact.build_diff_blast_radius", mock_build_diff)

    # fail-threshold=0.5 while score is 0.65 -> exit 2
    res = runner.invoke(app, ["diff-impact", "--fail-threshold", "0.5"])
    assert res.exit_code == 2

    # fail-on-risk=high while risk_tier is high -> exit 2
    res_risk = runner.invoke(app, ["diff-impact", "--fail-on-risk", "high"])
    assert res_risk.exit_code == 2

    # fail-on-risk=critical while risk_tier is high -> exit 0
    res_crit = runner.invoke(app, ["diff-impact", "--fail-on-risk", "critical"])
    assert res_crit.exit_code == 0


def test_cli_diff_impact_partial_deadline_exit_code(monkeypatch: Any) -> None:
    def mock_build_diff(*args: Any, **kwargs: Any) -> dict[str, Any]:
        return {
            "root": ".",
            "ref": None,
            "staged": False,
            "changed_files": ["sample.py"],
            "changed_symbols": [],
            "callers": [],
            "affected_files": ["sample.py"],
            "affected_tests": [],
            "blast_radius_score": 0.0,
            "risk_tier": "low",
            "partial": True,
            "downgrade_reasons": ["deadline_exceeded"],
            "symbol_count": 0,
            "caller_count": 0,
            "file_count": 1,
            "test_count": 0,
        }

    monkeypatch.setattr("tensor_grep.cli.diff_impact.build_diff_blast_radius", mock_build_diff)

    result = runner.invoke(app, ["diff-impact", "--deadline", "0.5", "--json"])
    assert result.exit_code == 2
    data = json.loads(result.stdout)
    assert data["partial"] is True
    assert "deadline_exceeded" in data["downgrade_reasons"]


def _git(repo: Path, *args: str) -> None:
    subprocess.run(
        ["git", "-c", "user.email=t@t", "-c", "user.name=t", *args],
        cwd=repo,
        check=True,
        capture_output=True,
    )


def _init_repo(repo: Path) -> None:
    repo.mkdir(exist_ok=True)
    _git(repo, "init", "-q")
    _git(repo, "config", "core.autocrlf", "false")


@pytest.mark.parametrize("ref", ["--output=pwned.txt", "-p", "--ext-diff", "a\nb", "a\x00b"])
def test_extract_diff_hunks_rejects_option_like_ref_without_running_git(
    monkeypatch: Any, ref: str
) -> None:
    calls: list[Any] = []
    monkeypatch.setattr(
        "tensor_grep.cli.diff_impact.run_subprocess", lambda *a, **k: calls.append(a)
    )
    # Council round 5: capture with an EXISTING type first so main fails BEHAVIOURALLY (git was
    # invoked), not on a missing-attribute lookup; only then check the new exception's identity.
    try:
        di.extract_diff_hunks_from_git(ref=ref)
        raised: BaseException | None = None
    except Exception as exc:  # noqa: BLE001 - deliberate: classify after the behavioural check
        raised = exc
    assert calls == [], "an option-like ref must be refused BEFORE git is invoked"
    assert type(raised).__name__ == "DiffError" and getattr(raised, "reason", None) == "invalid_ref"


@pytest.mark.parametrize("ref", ["HEAD~1", "main..HEAD", "main...HEAD"])
def test_extract_diff_hunks_git_argv_is_hardened(monkeypatch: Any, ref: str) -> None:
    seen: list[list[str]] = []

    class P:
        returncode = 0
        stdout = ""
        stderr = ""

    monkeypatch.setattr(
        "tensor_grep.cli.diff_impact.run_subprocess",
        lambda cmd, **k: seen.append(list(cmd)) or P(),
    )
    extract_diff_hunks_from_git(ref=ref, staged=True)
    cmd = seen[0]
    assert cmd[:5] == ["git", "-c", "core.quotepath=false", "-c", "core.fsmonitor=false"]
    for flag in ("--no-ext-diff", "--no-textconv", "--cached"):
        assert flag in cmd
    assert "--no-renames" not in cmd  # council round 5: keep main's rename semantics
    assert cmd[-3:] == ["--end-of-options", ref, "--"]


def test_option_like_ref_never_writes_file_in_real_repo(tmp_path: Path) -> None:
    # Behavioural RED on main: no new symbol needed; main creates pwned.txt.
    _init_repo(tmp_path)
    try:
        extract_diff_hunks_from_git(ref="--output=pwned.txt", root=tmp_path)
    except Exception:  # post-fix: di.DiffError
        pass
    assert not (tmp_path / "pwned.txt").exists()


def test_git_failure_is_incomplete_not_no_changes(tmp_path: Path, monkeypatch: Any) -> None:
    monkeypatch.setenv("GIT_CEILING_DIRECTORIES", str(tmp_path.parent))
    payload = build_diff_blast_radius(root=tmp_path)  # not a git repo
    assert payload["partial"] is True
    assert payload["result_incomplete"] is True
    assert payload["incomplete_reason"] == "git_diff_failed"
    assert "git_diff_failed" in payload["downgrade_reasons"]
    monkeypatch.chdir(tmp_path)  # council round 2: never run git in the real repo cwd
    res = runner.invoke(app, ["diff-impact", "--json", "--", "--output=x"])
    assert not (tmp_path / "x").exists()
    assert res.exit_code == 2
    assert json.loads(res.stdout)["incomplete_reason"] == "invalid_ref"


def test_parse_handles_spaces_quotes_and_deleted_files() -> None:
    diff = (
        "diff --git a/sp ace.py b/sp ace.py\n--- a/sp ace.py\t\n+++ b/sp ace.py\t\n"
        "@@ -1 +1 @@\n-x\n+--- y\n"
        'diff --git "a/caf\\303\\251.py" "b/caf\\303\\251.py"\n'
        '--- "a/caf\\303\\251.py"\n+++ "b/caf\\303\\251.py"\n@@ -2,0 +3,2 @@\n+a\n+b\n'
        "diff --git a/lib.py b/lib.py\ndeleted file mode 100644\n"
        "--- a/lib.py\n+++ /dev/null\n@@ -1,2 +0,0 @@\n-a\n-b\n"
    )
    parsed = parse_git_diff_hunks(diff)
    assert parsed[Path("sp ace.py")] == [(1, 1)]
    assert parsed[Path("café.py")] == [(3, 4)]
    assert parsed[Path("lib.py")] == []


def test_deleted_file_hunk_body_lines_are_never_parsed_as_headers() -> None:
    # Council round 5: removed lines whose CONTENT starts with "-- " / "++ " render as
    # "--- ..." / "+++ ..." inside a deleted file's hunk body; they must not become paths.
    diff = (
        "diff --git a/old.py b/old.py\ndeleted file mode 100644\n"
        "--- a/old.py\n+++ /dev/null\n@@ -1,2 +0,0 @@\n--- x\n-++ y\n"
        "diff --git a/keep.py b/keep.py\n--- a/keep.py\n+++ b/keep.py\n@@ -3,0 +4,1 @@\n+z\n"
    )
    parsed = parse_git_diff_hunks(diff)
    assert parsed == {Path("old.py"): [], Path("keep.py"): [(4, 4)]}


def test_rename_with_edit_maps_to_the_new_path() -> None:
    diff = (
        "diff --git a/old.py b/new.py\nsimilarity index 90%\nrename from old.py\nrename to new.py\n"
        "--- a/old.py\n+++ b/new.py\n@@ -2,0 +3,1 @@\n+x\n"
    )
    assert parse_git_diff_hunks(diff) == {Path("new.py"): [(3, 3)]}


def test_git_header_path_unquotes_escaped_quote() -> None:
    assert di._git_header_path('"a/q\\"x.py"') == Path('q"x.py')
    assert di._git_header_path("/dev/null") is None


def test_real_repo_spaces_nonascii_and_deleted(tmp_path: Path) -> None:
    _init_repo(tmp_path)
    (tmp_path / "lib.py").write_text("def helper():\n    return 1\n", encoding="utf-8")
    (tmp_path / "café.py").write_text("def a():\n    return 1\n", encoding="utf-8")
    (tmp_path / "sp ace.py").write_text("def b():\n    return 1\n", encoding="utf-8")
    (tmp_path / "app.py").write_text("from lib import helper\nhelper()\n", encoding="utf-8")
    _git(tmp_path, "add", "--", "lib.py", "café.py", "sp ace.py", "app.py")
    _git(tmp_path, "commit", "-qm", "i")
    (tmp_path / "café.py").write_text("def a():\n    return 2\n", encoding="utf-8")
    (tmp_path / "sp ace.py").write_text("def b():\n    return 2\n", encoding="utf-8")
    (tmp_path / "lib.py").unlink()
    payload = build_diff_blast_radius(root=tmp_path)
    assert payload["changed_files"] == ["café.py", "lib.py", "sp ace.py"]
    assert {s["name"] for s in payload["changed_symbols"]} == {"a", "b"}
    assert payload["deleted_files"] == ["lib.py"]
    assert "deleted_files_symbols_not_analyzed" in payload["downgrade_reasons"]
    assert payload["partial"] is False  # orchestrator decision: deletions do not force exit 2
