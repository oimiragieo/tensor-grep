"""Unit tests for tg diff-impact (P1 diff blast radius and review risk gate)."""

from __future__ import annotations

import json
import subprocess
import sys
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


@pytest.mark.parametrize("ref", ["--output=pwned.txt", "-p", "--ext-diff", "a\nb", "a\x00b", ""])
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
    except Exception as exc:
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


def test_cli_diff_impact_bogus_fail_on_risk_is_usage_error(monkeypatch: Any) -> None:
    monkeypatch.setattr("tensor_grep.cli.diff_impact.extract_diff_hunks_from_git", lambda **k: {})
    res = runner.invoke(app, ["diff-impact", "--fail-on-risk", "bogus"])
    assert res.exit_code == 2
    # click 8.4.2 (uv.lock) separates streams; the error is echoed to stderr (council round 2)
    assert "fail-on-risk" in (res.stdout or "") + (res.stderr or "")


def _payload(**overrides: Any) -> dict[str, Any]:
    base: dict[str, Any] = {
        "root": ".",
        "ref": None,
        "staged": False,
        "changed_files": ["a.py"],
        "changed_symbols": [],
        "callers": [],
        "affected_files": ["a.py"],
        "affected_tests": [],
        "blast_radius_score": 0.5,
        "risk_tier": "medium",
        "partial": False,
        "downgrade_reasons": [],
        "symbol_count": 0,
        "caller_count": 0,
        "file_count": 1,
        "test_count": 0,
    }
    base.update(overrides)
    return base


def test_cli_diff_impact_exit_reason_and_strict_threshold(monkeypatch: Any) -> None:
    monkeypatch.setattr(
        "tensor_grep.cli.diff_impact.build_diff_blast_radius", lambda **k: _payload()
    )
    equal = runner.invoke(app, ["diff-impact", "--json", "--fail-threshold", "0.5"])
    assert equal.exit_code == 0  # strict '>' per help text ("exceeds")
    assert json.loads(equal.stdout)["exit_reason"] == "ok"
    breach = runner.invoke(app, ["diff-impact", "--json", "--fail-on-risk", "medium"])
    assert breach.exit_code == 2
    assert json.loads(breach.stdout)["exit_reason"] == "gate_breached"


def test_cli_diff_impact_exit_reason_incomplete(monkeypatch: Any) -> None:
    monkeypatch.setattr(
        "tensor_grep.cli.diff_impact.build_diff_blast_radius",
        lambda **k: _payload(partial=True, downgrade_reasons=["git_diff_failed"]),
    )
    res = runner.invoke(app, ["diff-impact", "--json"])
    assert res.exit_code == 2
    assert json.loads(res.stdout)["exit_reason"] == "incomplete"


def _assert_deletion_reported(payload: dict[str, Any], name: str) -> None:
    assert name in payload["deleted_files"]
    assert name in payload["changed_files"]
    assert "deleted_files_symbols_not_analyzed" in payload["downgrade_reasons"]


def test_real_repo_empty_file_deletion_is_a_change(tmp_path: Path) -> None:
    _init_repo(tmp_path)
    (tmp_path / "empty.py").write_text("", encoding="utf-8")
    (tmp_path / "keep.py").write_text("x = 1\n", encoding="utf-8")
    _git(tmp_path, "add", "--", "empty.py", "keep.py")
    _git(tmp_path, "commit", "-qm", "i")
    (tmp_path / "empty.py").unlink()
    payload = build_diff_blast_radius(root=tmp_path)
    _assert_deletion_reported(payload, "empty.py")


def test_real_repo_binary_file_deletion_is_a_change(tmp_path: Path) -> None:
    _init_repo(tmp_path)
    (tmp_path / "data.bin").write_bytes(b"\x00\x01\x02\xff\x00")
    (tmp_path / "keep.py").write_text("x = 1\n", encoding="utf-8")
    _git(tmp_path, "add", "--", "data.bin", "keep.py")
    _git(tmp_path, "commit", "-qm", "i")
    (tmp_path / "data.bin").unlink()
    payload = build_diff_blast_radius(root=tmp_path)
    _assert_deletion_reported(payload, "data.bin")


def test_cli_empty_file_deletion_is_not_no_changes(tmp_path: Path, monkeypatch: Any) -> None:
    _init_repo(tmp_path)
    (tmp_path / "empty.py").write_text("", encoding="utf-8")
    _git(tmp_path, "add", "--", "empty.py")
    _git(tmp_path, "commit", "-qm", "i")
    (tmp_path / "empty.py").unlink()
    monkeypatch.chdir(tmp_path)
    res = runner.invoke(app, ["diff-impact", "--json"])
    data = json.loads(res.stdout)
    assert data["deleted_files"] == ["empty.py"]
    assert data["exit_reason"] != "no_changes"
    assert res.exit_code != 1


def test_parse_headerless_deletions_empty_binary_and_quoted() -> None:
    diff = (
        "diff --git a/empty.py b/empty.py\ndeleted file mode 100644\nindex e69de29..0000000\n"
        "diff --git a/sp ace.bin b/sp ace.bin\ndeleted file mode 100644\nindex 1111111..0000000\n"
        "Binary files a/sp ace.bin and /dev/null differ\n"
        'diff --git "a/caf\\303\\251.bin" "b/caf\\303\\251.bin"\ndeleted file mode 100644\n'
        "index 1111111..0000000\nBinary files a/caf\\303\\251.bin and /dev/null differ\n"
    )
    assert parse_git_diff_hunks(diff) == {
        Path("empty.py"): [],
        Path("sp ace.bin"): [],
        Path("café.bin"): [],
    }


def test_parse_pure_rename_without_hunks_is_not_a_deletion() -> None:
    diff = (
        "diff --git a/old.py b/new.py\nsimilarity index 100%\nrename from old.py\n"
        "rename to new.py\n"
    )
    assert parse_git_diff_hunks(diff) == {}


@pytest.mark.parametrize("sep", ["\u2028", "\u0085", "\u2029", "\x0b", "\x0c", "\x1c"])
def test_parse_filename_with_unicode_line_separator_is_intact(sep: str) -> None:
    name = f"caf{sep}e.py"
    diff = f"diff --git a/{name} b/{name}\n--- a/{name}\n+++ b/{name}\n@@ -1 +1 @@\n-x\n+y\n"
    assert parse_git_diff_hunks(diff) == {Path(name): [(1, 1)]}


def test_parse_tolerates_crlf_line_endings() -> None:
    diff = "diff --git a/a.py b/a.py\r\n--- a/a.py\r\n+++ b/a.py\r\n@@ -1 +1 @@\r\n-x\r\n+y\r\n"
    assert parse_git_diff_hunks(diff) == {Path("a.py"): [(1, 1)]}


def test_real_repo_unicode_separator_filename(tmp_path: Path) -> None:
    _init_repo(tmp_path)
    name = "caf\u2028e.py"
    try:
        (tmp_path / name).write_text("def a():\n    return 1\n", encoding="utf-8")
    except OSError as exc:
        pytest.skip(f"filesystem refuses U+2028 in names: {exc}")
    _git(tmp_path, "add", "--", name)
    _git(tmp_path, "commit", "-qm", "i")
    (tmp_path / name).write_text("def a():\n    return 2\n", encoding="utf-8")
    payload = build_diff_blast_radius(root=tmp_path)
    assert payload["changed_files"] == [name]
    assert {s["name"] for s in payload["changed_symbols"]} == {"a"}


def test_cli_empty_ref_is_invalid_ref(tmp_path: Path, monkeypatch: Any) -> None:
    _init_repo(tmp_path)
    monkeypatch.chdir(tmp_path)
    res = runner.invoke(app, ["diff-impact", "--json", ""])
    assert res.exit_code == 2
    assert json.loads(res.stdout)["incomplete_reason"] == "invalid_ref"


@pytest.mark.parametrize(
    ("key", "value"),
    [
        ("color.ui", "always"),
        ("color.diff", "always"),
        ("diff.noprefix", "true"),
        ("diff.mnemonicPrefix", "true"),
        ("diff.srcPrefix", "SRC/"),
        ("diff.dstPrefix", "DST/"),
        ("diff.renames", "false"),
        ("diff.renames", "copies"),
        ("diff.context", "7"),
        ("diff.interHunkContext", "50"),
        ("diff.external", "false"),
        ("diff.relative", "true"),
    ],
)
def test_hostile_user_git_config_does_not_change_the_parse(
    tmp_path: Path, key: str, value: str
) -> None:
    _init_repo(tmp_path)
    _git(tmp_path, "config", key, value)
    (tmp_path / "sub").mkdir()
    body = "".join(f"line{i}\n" for i in range(1, 21))
    (tmp_path / "a.py").write_text(body, encoding="utf-8")
    (tmp_path / "sub" / "b.py").write_text(body, encoding="utf-8")
    _git(tmp_path, "add", "--", "a.py", "sub/b.py")
    _git(tmp_path, "commit", "-qm", "i")
    edited = body.replace("line3\n", "LINE3\n").replace("line9\n", "LINE9\n")
    (tmp_path / "a.py").write_text(edited, encoding="utf-8")
    (tmp_path / "sub" / "b.py").write_text(edited, encoding="utf-8")

    hunks = extract_diff_hunks_from_git(root=tmp_path)
    # exact ranges: interHunkContext must not merge the two edits into one 3..9 range
    assert hunks == {
        Path("a.py"): [(3, 3), (9, 9)],
        Path("sub/b.py"): [(3, 3), (9, 9)],
    }
    payload = build_diff_blast_radius(root=tmp_path)
    assert payload["changed_files"] == ["a.py", "sub/b.py"]
    assert not payload["partial"]


def test_diff_relative_config_in_subdirectory_root_keeps_repo_relative_paths(
    tmp_path: Path,
) -> None:
    _init_repo(tmp_path)
    (tmp_path / "sub").mkdir()
    (tmp_path / "a.py").write_text("x = 1\n", encoding="utf-8")
    (tmp_path / "sub" / "b.py").write_text("y = 1\n", encoding="utf-8")
    _git(tmp_path, "add", "--", "a.py", "sub/b.py")
    _git(tmp_path, "commit", "-qm", "i")
    (tmp_path / "a.py").write_text("x = 2\n", encoding="utf-8")
    (tmp_path / "sub" / "b.py").write_text("y = 2\n", encoding="utf-8")
    plain = extract_diff_hunks_from_git(root=tmp_path / "sub")
    _git(tmp_path, "config", "diff.relative", "true")
    hostile = extract_diff_hunks_from_git(root=tmp_path / "sub")
    assert hostile == plain
    assert Path("a.py") in hostile


def test_argv_pins_output_shape_overrides(monkeypatch: Any) -> None:
    seen: list[list[str]] = []

    class P:
        returncode = 0
        stdout = ""
        stderr = ""

    monkeypatch.setattr(
        "tensor_grep.cli.diff_impact.run_subprocess",
        lambda cmd, **k: seen.append(list(cmd)) or P(),
    )
    extract_diff_hunks_from_git()
    for flag in ("--no-color", "--no-relative"):
        assert flag in seen[0]
    assert "--inter-hunk-context=0" in seen[0]


def test_parse_binary_modified_and_added_lines_are_recorded() -> None:
    diff = (
        "diff --git a/data.bin b/data.bin\nindex 1111111..2222222 100644\n"
        "Binary files a/data.bin and b/data.bin differ\n"
        "diff --git a/sp ace.bin b/sp ace.bin\nnew file mode 100644\nindex 0000000..3333333\n"
        "Binary files /dev/null and b/sp ace.bin differ\n"
        'diff --git "a/caf\\303\\251.bin" "b/caf\\303\\251.bin"\nindex 1111111..2222222 100644\n'
        "Binary files a/caf\\303\\251.bin and b/caf\\303\\251.bin differ\n"
        "diff --git a/old.bin b/new.bin\nsimilarity index 90%\nrename from old.bin\n"
        "rename to new.bin\nindex 1111111..2222222 100644\n"
        "Binary files a/old.bin and b/new.bin differ\n"
        "diff --git a/gone.bin b/gone.bin\ndeleted file mode 100644\nindex 1111111..0000000\n"
        "Binary files a/gone.bin and /dev/null differ\n"
    )
    parsed = parse_git_diff_hunks(diff)
    assert parsed == {
        Path("data.bin"): [],
        Path("sp ace.bin"): [],
        Path("café.bin"): [],
        Path("new.bin"): [],
        Path("gone.bin"): [],
    }
    # the deleted binary is a deletion, not a "binary" entry
    assert getattr(parsed, "binary_files", None) == {
        Path("data.bin"),
        Path("sp ace.bin"),
        Path("café.bin"),
        Path("new.bin"),
    }


def test_parse_pure_rename_of_binary_without_content_change_is_not_a_change() -> None:
    diff = (
        "diff --git a/old.bin b/new.bin\nsimilarity index 100%\nrename from old.bin\n"
        "rename to new.bin\n"
    )
    assert parse_git_diff_hunks(diff) == {}


def test_real_repo_modified_binary_file_is_reported(tmp_path: Path) -> None:
    _init_repo(tmp_path)
    (tmp_path / "data.bin").write_bytes(b"\x00\x01\x02\xff\x00")
    (tmp_path / "keep.py").write_text("x = 1\n", encoding="utf-8")
    _git(tmp_path, "add", "--", "data.bin", "keep.py")
    _git(tmp_path, "commit", "-qm", "i")
    (tmp_path / "data.bin").write_bytes(b"\x00\x09\x08\xfe\x00\x00")
    payload = build_diff_blast_radius(root=tmp_path)
    assert payload["changed_files"] == ["data.bin"]
    assert payload["binary_files"] == ["data.bin"]
    assert payload["deleted_files"] == []
    assert "binary_files_not_analyzed" in payload["downgrade_reasons"]
    assert payload["partial"] is False


def test_real_repo_added_binary_file_is_reported(tmp_path: Path) -> None:
    _init_repo(tmp_path)
    (tmp_path / "keep.py").write_text("x = 1\n", encoding="utf-8")
    _git(tmp_path, "add", "--", "keep.py")
    _git(tmp_path, "commit", "-qm", "i")
    (tmp_path / "new.bin").write_bytes(b"\x00\x01\x02\xff")
    _git(tmp_path, "add", "--", "new.bin")
    payload = build_diff_blast_radius(root=tmp_path, staged=True)
    assert payload["changed_files"] == ["new.bin"]
    assert payload["binary_files"] == ["new.bin"]
    assert "binary_files_not_analyzed" in payload["downgrade_reasons"]


def test_cli_modified_binary_file_is_not_no_changes(tmp_path: Path, monkeypatch: Any) -> None:
    _init_repo(tmp_path)
    (tmp_path / "data.bin").write_bytes(b"\x00\x01\x02")
    _git(tmp_path, "add", "--", "data.bin")
    _git(tmp_path, "commit", "-qm", "i")
    (tmp_path / "data.bin").write_bytes(b"\x00\x05\x06\x07")
    monkeypatch.chdir(tmp_path)
    res = runner.invoke(app, ["diff-impact", "--json"])
    data = json.loads(res.stdout)
    assert data["binary_files"] == ["data.bin"]
    assert data["exit_reason"] == "ok"
    assert res.exit_code == 0


def test_payloads_always_carry_binary_files_key() -> None:
    assert build_diff_blast_radius(diff_text="")["binary_files"] == []


def test_parse_edited_binary_copy_uses_the_destination_path() -> None:
    diff = (
        "diff --git a/old.bin b/new.bin\nsimilarity index 90%\ncopy from old.bin\n"
        "copy to new.bin\nindex 1111111..2222222 100644\n"
        "Binary files a/old.bin and b/new.bin differ\n"
        'diff --git "a/o\\303\\251.bin" "b/n\\303\\251.bin"\nsimilarity index 90%\n'
        'copy from "o\\303\\251.bin"\ncopy to "n\\303\\251.bin"\nindex 1111111..2222222 100644\n'
        'Binary files "a/o\\303\\251.bin" and "b/n\\303\\251.bin" differ\n'
    )
    parsed = parse_git_diff_hunks(diff)
    assert parsed == {Path("new.bin"): [], Path("né.bin"): []}
    assert parsed.binary_files == {Path("new.bin"), Path("né.bin")}


def test_parse_edited_text_copy_uses_the_destination_path() -> None:
    diff = (
        "diff --git a/old.py b/new.py\nsimilarity index 90%\ncopy from old.py\ncopy to new.py\n"
        "--- a/old.py\n+++ b/new.py\n@@ -2,0 +3,1 @@\n+x\n"
    )
    assert parse_git_diff_hunks(diff) == {Path("new.py"): [(3, 3)]}


def test_parse_pure_copy_without_content_change_is_not_a_change() -> None:
    diff = (
        "diff --git a/old.bin b/new.bin\nsimilarity index 100%\ncopy from old.bin\n"
        "copy to new.bin\n"
    )
    assert parse_git_diff_hunks(diff) == {}


def test_parse_binary_filename_ending_in_dev_null_is_modified_not_deleted() -> None:
    name = "foo and /dev/null"
    diff = (
        f"diff --git a/{name} b/{name}\nindex 1111111..2222222 100644\n"
        f"Binary files a/{name} and b/{name} differ\n"
    )
    parsed = parse_git_diff_hunks(diff)
    assert parsed == {Path(name): []}
    assert parsed.binary_files == {Path(name)}


def test_parse_deleted_binary_is_decided_by_header_state_not_line_text() -> None:
    name = "foo and /dev/null"
    diff = (
        f"diff --git a/{name} b/{name}\ndeleted file mode 100644\nindex 1111111..0000000\n"
        f"Binary files a/{name} and /dev/null differ\n"
    )
    parsed = parse_git_diff_hunks(diff)
    assert parsed == {Path(name): []}
    assert parsed.binary_files == set()


@pytest.mark.skipif(sys.platform == "win32", reason="a directory named 'foo and ' is not legal")
def test_real_repo_binary_named_like_the_deletion_suffix(tmp_path: Path) -> None:
    _init_repo(tmp_path)
    target = tmp_path / "foo and " / "dev" / "null"
    target.parent.mkdir(parents=True)
    target.write_bytes(b"\x00\x01\x02")
    _git(tmp_path, "add", "--all")
    _git(tmp_path, "commit", "-qm", "i")
    target.write_bytes(b"\x00\x07\x08\x09")
    payload = build_diff_blast_radius(root=tmp_path)
    assert payload["binary_files"] == ["foo and /dev/null"]
    assert payload["deleted_files"] == []


def test_real_repo_edited_binary_copy_is_reported(tmp_path: Path) -> None:
    _init_repo(tmp_path)
    _git(tmp_path, "config", "diff.renames", "copies")
    base = bytes((i * 37 + 11) % 251 for i in range(6000)) + b"\x00"
    (tmp_path / "old.bin").write_bytes(base)
    _git(tmp_path, "add", "--", "old.bin")
    _git(tmp_path, "commit", "-qm", "i")
    (tmp_path / "old.bin").write_bytes(base + b"\x00edited-source")
    (tmp_path / "new.bin").write_bytes(base + b"\x00edited-copy")
    _git(tmp_path, "add", "--", "old.bin", "new.bin")
    raw = subprocess.run(
        ["git", "-c", "diff.renames=copies", "diff", "--cached", "-U0", "--no-color"],
        cwd=tmp_path,
        check=True,
        capture_output=True,
        text=True,
    ).stdout
    assert "copy to new.bin" in raw, f"fixture did not make git emit a copy:\n{raw}"
    payload = build_diff_blast_radius(root=tmp_path, staged=True)
    assert "new.bin" in payload["binary_files"], raw
    assert "new.bin" in payload["changed_files"]


def test_parse_header_only_added_file_is_recorded_and_not_deleted() -> None:
    diff = (
        "diff --git a/empty.py b/empty.py\nnew file mode 100644\nindex 0000000..e69de29\n"
        'diff --git "a/caf\\303\\251.py" "b/caf\\303\\251.py"\nnew file mode 100644\n'
        "index 0000000..e69de29\n"
        "diff --git a/sp ace.py b/sp ace.py\nnew file mode 100644\nindex 0000000..e69de29\n"
    )
    parsed = parse_git_diff_hunks(diff)
    assert parsed == {Path("empty.py"): [], Path("café.py"): [], Path("sp ace.py"): []}
    assert parsed.deleted_paths == set()
    assert parsed.binary_files == set()


def test_parse_added_file_with_content_keeps_its_ranges() -> None:
    diff = (
        "diff --git a/n.py b/n.py\nnew file mode 100644\nindex 0000000..1111111\n"
        "--- /dev/null\n+++ b/n.py\n@@ -0,0 +1,2 @@\n+a\n+b\n"
    )
    parsed = parse_git_diff_hunks(diff)
    assert parsed == {Path("n.py"): [(1, 2)]}
    assert parsed.deleted_paths == set()


def test_parse_deletion_identity_comes_only_from_deleted_file_mode() -> None:
    diff = (
        "diff --git a/gone.py b/gone.py\ndeleted file mode 100644\nindex 1111111..0000000\n"
        "--- a/gone.py\n+++ /dev/null\n@@ -1 +0,0 @@\n-x\n"
        "diff --git a/empty.py b/empty.py\nnew file mode 100644\nindex 0000000..e69de29\n"
    )
    parsed = parse_git_diff_hunks(diff)
    assert parsed == {Path("gone.py"): [], Path("empty.py"): []}
    assert parsed.deleted_paths == {Path("gone.py")}


def test_parse_mode_only_change_is_recorded_and_disclosed() -> None:
    diff = (
        "diff --git a/run.sh b/run.sh\nold mode 100644\nnew mode 100755\n"
        "diff --git a/sp ace.sh b/sp ace.sh\nold mode 100644\nnew mode 100755\n"
        "diff --git a/old.sh b/new.sh\nold mode 100644\nnew mode 100755\nsimilarity index 100%\n"
        "rename from old.sh\nrename to new.sh\n"
    )
    parsed = parse_git_diff_hunks(diff)
    assert parsed == {Path("run.sh"): [], Path("sp ace.sh"): [], Path("new.sh"): []}
    assert parsed.mode_changed_files == {Path("run.sh"), Path("sp ace.sh"), Path("new.sh")}
    assert parsed.deleted_paths == set()


def test_parse_mode_change_with_content_edit_keeps_ranges_and_is_disclosed() -> None:
    diff = (
        "diff --git a/run.sh b/run.sh\nold mode 100644\nnew mode 100755\nindex 1111111..2222222\n"
        "--- a/run.sh\n+++ b/run.sh\n@@ -2,0 +3,1 @@\n+x\n"
    )
    parsed = parse_git_diff_hunks(diff)
    assert parsed == {Path("run.sh"): [(3, 3)]}
    assert parsed.mode_changed_files == {Path("run.sh")}


def test_real_repo_empty_staged_addition_is_a_change(tmp_path: Path) -> None:
    _init_repo(tmp_path)
    (tmp_path / "keep.py").write_text("x = 1\n", encoding="utf-8")
    _git(tmp_path, "add", "--", "keep.py")
    _git(tmp_path, "commit", "-qm", "i")
    (tmp_path / "empty.py").write_text("", encoding="utf-8")
    _git(tmp_path, "add", "--", "empty.py")
    payload = build_diff_blast_radius(root=tmp_path, staged=True)
    assert payload["changed_files"] == ["empty.py"]
    assert payload["deleted_files"] == []
    assert "deleted_files_symbols_not_analyzed" not in payload["downgrade_reasons"]


def test_cli_empty_staged_addition_is_not_no_changes(tmp_path: Path, monkeypatch: Any) -> None:
    _init_repo(tmp_path)
    (tmp_path / "keep.py").write_text("x = 1\n", encoding="utf-8")
    _git(tmp_path, "add", "--", "keep.py")
    _git(tmp_path, "commit", "-qm", "i")
    (tmp_path / "empty.py").write_text("", encoding="utf-8")
    _git(tmp_path, "add", "--", "empty.py")
    monkeypatch.chdir(tmp_path)
    res = runner.invoke(app, ["diff-impact", "--json", "--staged"])
    data = json.loads(res.stdout)
    assert data["changed_files"] == ["empty.py"]
    assert data["exit_reason"] == "ok"
    assert res.exit_code == 0


def test_real_repo_mode_only_change_is_reported(tmp_path: Path) -> None:
    _init_repo(tmp_path)
    _git(tmp_path, "config", "core.fileMode", "true")
    (tmp_path / "run.sh").write_text("echo hi\n", encoding="utf-8")
    _git(tmp_path, "add", "--", "run.sh")
    _git(tmp_path, "commit", "-qm", "i")
    _git(tmp_path, "update-index", "--chmod=+x", "--", "run.sh")
    payload = build_diff_blast_radius(root=tmp_path, staged=True)
    assert payload["changed_files"] == ["run.sh"]
    assert payload["mode_changed_files"] == ["run.sh"]
    assert payload["deleted_files"] == []


def _stage_gitlink(repo: Path, sha: str, path: str = "vendor/lib") -> None:
    _git(repo, "update-index", "--add", "--cacheinfo", f"160000,{sha},{path}")


@pytest.mark.parametrize(
    ("key", "value"),
    [("diff.submodule", "log"), ("diff.submodule", "diff"), ("diff.ignoreSubmodules", "all")],
)
def test_changed_gitlink_is_disclosed_under_hostile_submodule_config(
    tmp_path: Path, key: str, value: str
) -> None:
    _init_repo(tmp_path)
    _git(tmp_path, "config", key, value)
    (tmp_path / "keep.py").write_text("x = 1\n", encoding="utf-8")
    _git(tmp_path, "add", "--", "keep.py")
    _stage_gitlink(tmp_path, "1" * 40)
    _git(tmp_path, "commit", "-qm", "i")
    _stage_gitlink(tmp_path, "2" * 40)
    payload = build_diff_blast_radius(root=tmp_path, staged=True)
    assert payload["changed_files"] == ["vendor/lib"]
    assert payload["submodule_changed_files"] == ["vendor/lib"]
    assert "submodule_changes_not_analyzed" in payload["downgrade_reasons"]
    assert payload["deleted_files"] == []
    assert payload["partial"] is False


def test_parse_gitlink_shapes_are_disclosed_with_empty_ranges() -> None:
    diff = (
        "diff --git a/vendor/lib b/vendor/lib\nindex 1111111..2222222 160000\n"
        "--- a/vendor/lib\n+++ b/vendor/lib\n@@ -1 +1 @@\n"
        "-Subproject commit 1111111\n+Subproject commit 2222222\n"
        "diff --git a/new/mod b/new/mod\nnew file mode 160000\nindex 0000000..3333333\n"
        "--- /dev/null\n+++ b/new/mod\n@@ -0,0 +1 @@\n+Subproject commit 3333333\n"
        "diff --git a/old/mod b/old/mod\ndeleted file mode 160000\nindex 4444444..0000000\n"
        "--- a/old/mod\n+++ /dev/null\n@@ -1 +0,0 @@\n-Subproject commit 4444444\n"
    )
    parsed = parse_git_diff_hunks(diff)
    assert parsed == {Path("vendor/lib"): [], Path("new/mod"): [], Path("old/mod"): []}
    assert parsed.submodule_changed_files == {
        Path("vendor/lib"),
        Path("new/mod"),
        Path("old/mod"),
    }
    assert parsed.deleted_paths == {Path("old/mod")}


def test_argv_pins_submodule_and_rename_shape(monkeypatch: Any) -> None:
    seen: list[list[str]] = []

    class P:
        returncode = 0
        stdout = ""
        stderr = ""

    monkeypatch.setattr(
        "tensor_grep.cli.diff_impact.run_subprocess",
        lambda cmd, **k: seen.append(list(cmd)) or P(),
    )
    extract_diff_hunks_from_git()
    for flag in ("--submodule=short", "--ignore-submodules=none", "--find-renames"):
        assert flag in seen[0]


def _make_subdir_repo(repo: Path) -> None:
    _init_repo(repo)
    (repo / "sub").mkdir()
    (repo / "sub" / "lib.py").write_text("def helper():\n    return 1\n", encoding="utf-8")
    (repo / "sub" / "app.py").write_text(
        "from lib import helper\n\n\ndef run():\n    return helper()\n", encoding="utf-8"
    )
    _git(repo, "add", "--all")
    _git(repo, "commit", "-qm", "i")
    (repo / "sub" / "lib.py").write_text("def helper():\n    return 2\n", encoding="utf-8")


def test_running_from_a_subdirectory_matches_running_from_the_top_level(
    tmp_path: Path,
) -> None:
    _make_subdir_repo(tmp_path)
    top = build_diff_blast_radius(root=tmp_path)
    sub = build_diff_blast_radius(root=tmp_path / "sub")
    assert {s["name"] for s in top["changed_symbols"]} == {"helper"}
    assert {s["name"] for s in sub["changed_symbols"]} == {"helper"}
    assert sub["changed_files"] == top["changed_files"] == ["sub/lib.py"]
    assert sub["callers"] == top["callers"]
    assert sub["affected_files"] == top["affected_files"]
    assert sub["root"] == top["root"]


def test_cli_from_a_subdirectory_reports_changed_symbols(tmp_path: Path, monkeypatch: Any) -> None:
    _make_subdir_repo(tmp_path)
    monkeypatch.chdir(tmp_path / "sub")
    res = runner.invoke(app, ["diff-impact", "--json"])
    data = json.loads(res.stdout)
    assert [s["name"] for s in data["changed_symbols"]] == ["helper"]
    assert data["partial"] is False


def test_staged_pure_rename_is_not_a_change_even_with_renames_disabled(
    tmp_path: Path,
) -> None:
    _init_repo(tmp_path)
    _git(tmp_path, "config", "diff.renames", "false")
    (tmp_path / "old.py").write_text("def f():\n    return 1\n", encoding="utf-8")
    _git(tmp_path, "add", "--", "old.py")
    _git(tmp_path, "commit", "-qm", "i")
    _git(tmp_path, "mv", "old.py", "new.py")
    payload = build_diff_blast_radius(root=tmp_path, staged=True)
    assert payload["changed_files"] == []
    assert payload["deleted_files"] == []


def _two_function_repo(repo: Path) -> None:
    _init_repo(repo)
    body = "".join(f"line{i}\n" for i in range(1, 31))
    (repo / "mod.py").write_text(body, encoding="utf-8")
    _git(repo, "add", "--", "mod.py")
    _git(repo, "commit", "-qm", "i")
    (repo / "mod.py").write_text(body.replace("line10\n", "LINE10\n"), encoding="utf-8")


def test_positive_control_normal_env_gives_exact_ranges(tmp_path: Path) -> None:
    _two_function_repo(tmp_path)
    assert extract_diff_hunks_from_git(root=tmp_path) == {Path("mod.py"): [(10, 10)]}


def test_git_diff_opts_env_cannot_widen_the_hunks(tmp_path: Path, monkeypatch: Any) -> None:
    _two_function_repo(tmp_path)
    monkeypatch.setenv("GIT_DIFF_OPTS", "--unified=100")
    assert extract_diff_hunks_from_git(root=tmp_path) == {Path("mod.py"): [(10, 10)]}
    payload = build_diff_blast_radius(root=tmp_path)
    assert payload["changed_files"] == ["mod.py"]


def test_env_injected_git_config_cannot_hide_changes(tmp_path: Path, monkeypatch: Any) -> None:
    _two_function_repo(tmp_path)
    monkeypatch.setenv("GIT_CONFIG_COUNT", "3")
    for i, (key, value) in enumerate([
        ("color.ui", "always"),
        ("diff.context", "50"),
        ("diff.ignoreSubmodules", "all"),
    ]):
        monkeypatch.setenv(f"GIT_CONFIG_KEY_{i}", key)
        monkeypatch.setenv(f"GIT_CONFIG_VALUE_{i}", value)
    monkeypatch.setenv("GIT_CONFIG_PARAMETERS", "'color.ui=always' 'diff.context=50'")
    assert extract_diff_hunks_from_git(root=tmp_path) == {Path("mod.py"): [(10, 10)]}


def test_git_dir_and_index_file_env_cannot_redirect_which_repo_is_read(
    tmp_path: Path, monkeypatch: Any
) -> None:
    repo = tmp_path / "real"
    other = tmp_path / "other"
    _two_function_repo(repo)
    _init_repo(other)
    monkeypatch.setenv("GIT_DIR", str(other / ".git"))
    monkeypatch.setenv("GIT_WORK_TREE", str(other))
    monkeypatch.setenv("GIT_INDEX_FILE", str(other / ".git" / "index"))
    payload = build_diff_blast_radius(root=repo)
    assert payload["changed_files"] == ["mod.py"]


def test_git_env_strips_diff_and_repo_redirecting_variables(monkeypatch: Any) -> None:
    for name in (
        "GIT_DIFF_OPTS",
        "GIT_EXTERNAL_DIFF",
        "GIT_PAGER",
        "PAGER",
        "GIT_CONFIG_PARAMETERS",
        "GIT_CONFIG_COUNT",
        "GIT_CONFIG_KEY_0",
        "GIT_CONFIG_VALUE_0",
        "GIT_CONFIG_KEY_17",
        "GIT_DIFF_PATH_COUNTER",
        "GIT_DIFF_PATH_TOTAL",
        "GIT_DIR",
        "GIT_WORK_TREE",
        "GIT_INDEX_FILE",
    ):
        monkeypatch.setenv(name, "x")
    monkeypatch.setenv("LC_ALL", "de_DE.UTF-8")
    monkeypatch.setenv("KEEP_ME", "1")
    env = di._git_env()
    for name in (
        "GIT_DIFF_OPTS",
        "GIT_EXTERNAL_DIFF",
        "GIT_PAGER",
        "PAGER",
        "GIT_CONFIG_PARAMETERS",
        "GIT_CONFIG_COUNT",
        "GIT_CONFIG_KEY_0",
        "GIT_CONFIG_VALUE_0",
        "GIT_CONFIG_KEY_17",
        "GIT_DIFF_PATH_COUNTER",
        "GIT_DIFF_PATH_TOTAL",
        "GIT_DIR",
        "GIT_WORK_TREE",
        "GIT_INDEX_FILE",
    ):
        assert name not in env, name
    assert env["LC_ALL"] == "C"
    assert env["GIT_TERMINAL_PROMPT"] == "0"
    assert env["KEEP_ME"] == "1"


def test_every_git_subprocess_receives_the_sanitised_env(monkeypatch: Any) -> None:
    calls: list[dict[str, Any]] = []

    class P:
        returncode = 0
        stdout = "C:/repo\n"
        stderr = ""

    monkeypatch.setenv("GIT_DIFF_OPTS", "--unified=100")
    monkeypatch.setattr(
        "tensor_grep.cli.diff_impact.run_subprocess",
        lambda cmd, **k: calls.append({"cmd": list(cmd), **k}) or P(),
    )
    di._git_toplevel(Path("."))
    extract_diff_hunks_from_git()
    assert len(calls) == 2
    for call in calls:
        env = call.get("env")
        assert env is not None, call["cmd"]
        assert "GIT_DIFF_OPTS" not in env
