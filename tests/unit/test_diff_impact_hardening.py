"""Hardening tests for tg diff-impact: hunk bodies, path confinement, accounting invariant.

Split out of test_diff_impact.py to keep both files under the test file-size limit.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Any

import pytest
from typer.testing import CliRunner

import tensor_grep.cli.diff_impact as di
import tensor_grep.cli.diff_impact_git as dig
from tensor_grep.cli.diff_impact import (
    build_diff_blast_radius,
    map_changed_lines_to_symbols,
    parse_git_diff_hunks,
)
from tensor_grep.cli.main import app

runner = CliRunner()


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


def _text_record(hunk_header: str, body: str, path: str = "a.py") -> str:
    return (
        f"diff --git a/{path} b/{path}\nindex 1111111..2222222 100644\n"
        f"--- a/{path}\n+++ b/{path}\n{hunk_header}\n{body}"
    )


@pytest.mark.parametrize(
    ("header", "body"),
    [
        ("@@ -1 +1 @@", "-x\n+y\n+def omitted(): pass\n"),  # surplus new line
        ("@@ -1,2 +1,2 @@", "-x\n+y\n+z\n"),  # missing old line
        ("@@ -1,3 +1,3 @@", "-x\n-y\n+a\n+b\n"),  # truncated: old 2 of 3
        ("@@ -1 +1,2 @@", "-x\n+y\n"),  # truncated new side
        ("@@ -1,0 +2,1 @@", ""),  # zero-count hunk that lost its body
    ],
)
def test_hunk_body_counts_must_match_the_header(header: str, body: str) -> None:
    with pytest.raises(di.DiffError) as exc_info:
        di._parse_checked(_text_record(header, body))
    assert exc_info.value.reason == "unparsed_git_output"
    assert "a.py" in str(exc_info.value)
    assert header.split(" @@")[0] in str(exc_info.value)


def test_multi_hunk_file_with_one_short_hunk_is_rejected() -> None:
    diff = _text_record("@@ -1 +1 @@", "-x\n+y\n") + "@@ -5 +5,2 @@\n-a\n+b\n"
    with pytest.raises(di.DiffError) as exc_info:
        di._parse_checked(diff)
    assert exc_info.value.reason == "unparsed_git_output"
    assert "@@ -5 +5,2 @@" in str(exc_info.value)


@pytest.mark.parametrize(
    ("diff", "expected"),
    [
        (
            _text_record("@@ -1 +1 @@", "-x\n+y\n") + "@@ -5,2 +5,3 @@\n-a\n-b\n+c\n+d\n+e\n",
            {Path("a.py"): [(1, 1), (5, 7)]},
        ),
        (
            _text_record(
                "@@ -1 +1 @@",
                "-x\n\\ No newline at end of file\n+y\n\\ No newline at end of file\n",
            ),
            {Path("a.py"): [(1, 1)]},
        ),
        (
            "diff --git a/n.py b/n.py\nnew file mode 100644\nindex 0000000..1111111\n"
            "--- /dev/null\n+++ b/n.py\n@@ -0,0 +1,2 @@\n+a\n+b\n",
            {Path("n.py"): [(1, 2)]},
        ),
        (
            _text_record("@@ -3,2 +3,0 @@", "-a\n-b\n"),
            {Path("a.py"): [(3, 3)]},
        ),
        (
            _text_record("@@ -2,3 +2,3 @@", " keep\n-old\n+new\n keep2\n"),
            {Path("a.py"): [(2, 4)]},
        ),
    ],
)
def test_well_formed_hunk_bodies_still_validate(
    diff: str, expected: dict[Path, list[tuple[int, int]]]
) -> None:
    assert di._parse_checked(diff) == expected


def test_cross_check_compares_ranges_not_just_paths(monkeypatch: Any) -> None:
    real_parse = di.parse_git_diff_hunks

    def drop_one_range(text: str) -> Any:
        parsed = real_parse(text)
        parsed[Path("a.py")] = parsed[Path("a.py")][:1]  # path survives, one range vanishes
        return parsed

    monkeypatch.setattr(di, "parse_git_diff_hunks", drop_one_range)
    diff = _text_record("@@ -1 +1 @@", "-x\n+y\n") + "@@ -9 +9 @@\n-a\n+b\n"
    with pytest.raises(di.DiffError) as exc_info:
        di._parse_checked(diff)
    assert exc_info.value.reason == "unparsed_git_output"
    assert "a.py" in str(exc_info.value)


@pytest.mark.parametrize(
    "path",
    ["../../outside.py", "/etc/passwd", "C:/Windows/x.py", "//server/share/x.py", "a/../../x.py"],
)
def test_diff_paths_that_escape_the_repo_are_refused(path: str) -> None:
    diff = (
        f"diff --git a/{path} b/{path}\nindex 1111111..2222222 100644\n"
        f"--- a/{path}\n+++ b/{path}\n@@ -1 +1 @@\n-x\n+y\n"
    )
    with pytest.raises(di.DiffError) as exc_info:
        di._parse_checked(diff)
    assert exc_info.value.reason == "unsafe_path"


def test_plus_plus_plus_escape_alone_is_refused() -> None:
    diff = (
        "diff --git a/a.py b/a.py\nindex 1111111..2222222 100644\n"
        "--- a/a.py\n+++ b/../../outside.py\n@@ -1 +1 @@\n-x\n+y\n"
    )
    with pytest.raises(di.DiffError) as exc_info:
        di._parse_checked(diff)
    assert exc_info.value.reason == "unsafe_path"


def test_file_header_operands_must_match_the_diff_git_header() -> None:
    diff = (
        "diff --git a/a.py b/a.py\nindex 1111111..2222222 100644\n"
        "--- a/a.py\n+++ b/other.py\n@@ -1 +1 @@\n-x\n+y\n"
    )
    with pytest.raises(di.DiffError) as exc_info:
        di._parse_checked(diff)
    assert exc_info.value.reason == "unparsed_git_output"
    assert "operands" in str(exc_info.value)


def test_map_changed_lines_refuses_dotdot_paths_without_opening_anything(
    tmp_path: Path, monkeypatch: Any
) -> None:
    root = tmp_path / "repo"
    root.mkdir()
    (tmp_path / "outside.py").write_text("def leaked():\n    return 1\n", encoding="utf-8")
    opened: list[Any] = []
    monkeypatch.setattr(di.lang_registry, "spec_for_path", lambda path: opened.append(path) or None)
    monkeypatch.setattr(
        di.repo_map,
        "_imports_and_symbols_for_path",
        lambda path: opened.append(path) or ([], []),
    )
    assert map_changed_lines_to_symbols({Path("../outside.py"): [(1, 2)]}, root) == []
    assert opened == []


def test_map_changed_lines_refuses_symlink_escape_without_opening_anything(
    tmp_path: Path, monkeypatch: Any
) -> None:
    root = tmp_path / "repo"
    root.mkdir()
    outside = tmp_path / "outside.py"
    outside.write_text("def leaked():\n    return 1\n", encoding="utf-8")
    try:
        (root / "link.py").symlink_to(outside)
    except (OSError, NotImplementedError) as exc:
        pytest.skip(f"cannot create symlinks here: {exc}")
    opened: list[Any] = []
    monkeypatch.setattr(di.lang_registry, "spec_for_path", lambda path: opened.append(path) or None)
    monkeypatch.setattr(
        di.repo_map,
        "_imports_and_symbols_for_path",
        lambda path: opened.append(path) or ([], []),
    )
    assert map_changed_lines_to_symbols({Path("link.py"): [(1, 2)]}, root) == []
    assert opened == []


HUGE = "9" * 5000


@pytest.mark.parametrize(
    "header",
    [f"@@ -1 +{HUGE} @@", f"@@ -{HUGE} +1 @@", f"@@ -1,{HUGE} +1 @@", f"@@ -1 +1,{HUGE} @@"],
)
def test_huge_hunk_number_is_a_diff_error_not_a_value_error(header: str) -> None:
    diff = _text_record(header, "-x\n+y\n")
    with pytest.raises(di.DiffError) as exc_info:
        di._parse_checked(diff)
    assert exc_info.value.reason == "unparsed_git_output"
    payload = build_diff_blast_radius(diff_text=diff)
    assert payload["partial"] is True
    assert payload["incomplete_reason"] == "unparsed_git_output"


def test_huge_hunk_number_does_not_raise_from_the_bare_parser() -> None:
    parse_git_diff_hunks(_text_record(f"@@ -1 +{HUGE} @@", "-x\n+y\n"))  # must not raise


def test_cli_huge_hunk_number_exits_2(monkeypatch: Any) -> None:
    diff = _text_record(f"@@ -1 +{HUGE} @@", "-x\n+y\n")

    class P:
        returncode = 0
        stdout = diff
        stderr = ""

    class Top(P):
        stdout = "C:/repo\n"

    monkeypatch.setattr(
        "tensor_grep.cli.diff_impact.run_subprocess",
        lambda cmd, **k: Top() if "rev-parse" in cmd else P(),
    )
    res = runner.invoke(app, ["diff-impact", "--json"])
    assert res.exit_code == 2
    assert json.loads(res.stdout)["incomplete_reason"] == "unparsed_git_output"


def _escaping_symlink_repo(tmp_path: Path) -> tuple[Path, Path]:
    """A committed in-repo symlink whose target is then re-pointed OUTSIDE the repository."""
    repo = tmp_path / "repo"
    _init_repo(repo)
    (repo / "inside.py").write_text("def inside():\n    return 1\n", encoding="utf-8")
    outside = tmp_path / "outside.py"
    outside.write_text("def leaked():\n    return 1\n", encoding="utf-8")
    try:
        (repo / "link.py").symlink_to(repo / "inside.py")
    except (OSError, NotImplementedError) as exc:
        pytest.skip(f"cannot create symlinks here: {exc}")
    _git(repo, "-c", "core.symlinks=true", "add", "--all")
    _git(repo, "commit", "-qm", "i")
    (repo / "link.py").unlink()
    (repo / "link.py").symlink_to(outside)
    return repo, outside


def test_symlink_escape_fails_closed_through_the_builder(tmp_path: Path, monkeypatch: Any) -> None:
    repo, outside = _escaping_symlink_repo(tmp_path)
    opened: list[Any] = []
    monkeypatch.setattr(di.lang_registry, "spec_for_path", lambda path: opened.append(path) or None)
    monkeypatch.setattr(
        di.repo_map,
        "_imports_and_symbols_for_path",
        lambda path: opened.append(path) or ([], []),
    )
    payload = build_diff_blast_radius(root=repo)
    assert payload["partial"] is True
    assert payload["result_incomplete"] is True
    assert payload["incomplete_reason"] == "path_escapes_root"
    assert "path_escapes_root" in payload["downgrade_reasons"]
    assert payload["not_analyzed_paths"] == [{"path": "link.py", "reason": "path_escapes_root"}]
    assert payload["changed_files"] == ["link.py"]
    assert payload["changed_symbols"] == []
    # the repo-map scan legitimately opens in-repo files; the escaping target must never be opened
    assert all(Path(str(o)).resolve() != outside.resolve() for o in opened)
    assert all(Path(str(o)).resolve().is_relative_to(repo.resolve()) for o in opened)


def test_symlink_escape_exits_2_through_the_cli(tmp_path: Path, monkeypatch: Any) -> None:
    repo, _ = _escaping_symlink_repo(tmp_path)
    monkeypatch.chdir(repo)
    res = runner.invoke(app, ["diff-impact", "--json"])
    assert res.exit_code == 2
    data = json.loads(res.stdout)
    assert data["exit_reason"] == "incomplete"
    assert data["not_analyzed_paths"] == [{"path": "link.py", "reason": "path_escapes_root"}]


def test_symlink_escape_exits_2_in_a_real_subprocess(tmp_path: Path) -> None:
    repo, _ = _escaping_symlink_repo(tmp_path)
    src = Path(di.__file__).resolve().parents[2]
    env = {**os.environ, "PYTHONPATH": str(src)}
    proc = subprocess.run(
        [sys.executable, "-m", "tensor_grep", "diff-impact", "--json"],
        cwd=repo,
        env=env,
        capture_output=True,
        text=True,
        timeout=240,
        check=False,
    )
    assert proc.returncode == 2, proc.stderr[-600:]
    data = json.loads(proc.stdout)
    assert data["not_analyzed_paths"] == [{"path": "link.py", "reason": "path_escapes_root"}]
    assert data["changed_symbols"] == []


def test_in_repo_symlink_pointing_inside_the_root_is_still_analyzed(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    _init_repo(repo)
    (repo / "one.py").write_text("def one():\n    return 1\n", encoding="utf-8")
    (repo / "two.py").write_text("def two():\n    return 2\n", encoding="utf-8")
    try:
        (repo / "link.py").symlink_to(repo / "one.py")
    except (OSError, NotImplementedError) as exc:
        pytest.skip(f"cannot create symlinks here: {exc}")
    _git(repo, "-c", "core.symlinks=true", "add", "--all")
    _git(repo, "commit", "-qm", "i")
    (repo / "link.py").unlink()
    (repo / "link.py").symlink_to(repo / "two.py")
    payload = build_diff_blast_radius(root=repo)
    assert payload["changed_files"] == ["link.py"]
    assert payload["not_analyzed_paths"] == []
    assert payload["partial"] is False
    assert "path_escapes_root" not in payload["downgrade_reasons"]


def test_in_repo_symlink_pointing_inside_the_root_exits_0(tmp_path: Path, monkeypatch: Any) -> None:
    repo = tmp_path / "repo"
    _init_repo(repo)
    (repo / "one.py").write_text("def one():\n    return 1\n", encoding="utf-8")
    (repo / "two.py").write_text("def two():\n    return 2\n", encoding="utf-8")
    try:
        (repo / "link.py").symlink_to(repo / "one.py")
    except (OSError, NotImplementedError) as exc:
        pytest.skip(f"cannot create symlinks here: {exc}")
    _git(repo, "-c", "core.symlinks=true", "add", "--all")
    _git(repo, "commit", "-qm", "i")
    (repo / "link.py").unlink()
    (repo / "link.py").symlink_to(repo / "two.py")
    monkeypatch.chdir(repo)
    res = runner.invoke(app, ["diff-impact", "--json"])
    assert res.exit_code == 0, res.stdout
    assert json.loads(res.stdout)["exit_reason"] == "ok"


def _swap_race_repo(tmp_path: Path) -> tuple[Path, Path]:
    """link.py -> two.py is the changed, in-root file; the test swaps it outside mid-analysis."""
    repo = tmp_path / "repo"
    _init_repo(repo)
    (repo / "one.py").write_text("def one():\n    return 1\n", encoding="utf-8")
    (repo / "two.py").write_text("def two():\n    return 2\n", encoding="utf-8")
    outside = tmp_path / "outside.py"
    outside.write_text("def leaked():\n    return 1\n", encoding="utf-8")
    try:
        (repo / "link.py").symlink_to(repo / "one.py")
    except (OSError, NotImplementedError) as exc:
        pytest.skip(f"cannot create symlinks here: {exc}")
    _git(repo, "-c", "core.symlinks=true", "add", "--all")
    _git(repo, "commit", "-qm", "i")
    (repo / "link.py").unlink()
    (repo / "link.py").symlink_to(repo / "two.py")
    return repo, outside


def _install_swap_on_first_extraction(monkeypatch: Any, repo: Path, outside: Path) -> list[str]:
    """After the containment check passed, swap link.py to point OUTSIDE, then delegate."""
    events: list[str] = []
    real = di.lang_registry.spec_for_path

    def swapping(path: Any) -> Any:
        if Path(str(path)).name == "link.py" and not events:
            (repo / "link.py").unlink()
            (repo / "link.py").symlink_to(outside)
            events.append("swapped")
        return real(path)

    monkeypatch.setattr(di.lang_registry, "spec_for_path", swapping)
    return events


def test_swap_during_extraction_is_detected_and_discarded(tmp_path: Path, monkeypatch: Any) -> None:
    repo, outside = _swap_race_repo(tmp_path)
    events = _install_swap_on_first_extraction(monkeypatch, repo, outside)
    payload = build_diff_blast_radius(root=repo)
    assert events == ["swapped"]
    assert "leaked" not in {s["name"] for s in payload["changed_symbols"]}
    assert {"path": "link.py", "reason": "path_changed_during_analysis"} in payload[
        "not_analyzed_paths"
    ]
    assert payload["partial"] is True
    assert payload["incomplete_reason"] == "path_changed_during_analysis"


def test_swap_during_extraction_exits_2_through_the_cli(tmp_path: Path, monkeypatch: Any) -> None:
    repo, outside = _swap_race_repo(tmp_path)
    _install_swap_on_first_extraction(monkeypatch, repo, outside)
    monkeypatch.chdir(repo)
    res = runner.invoke(app, ["diff-impact", "--json"])
    assert res.exit_code == 2
    data = json.loads(res.stdout)
    assert "leaked" not in {s["name"] for s in data["changed_symbols"]}
    assert data["exit_reason"] == "incomplete"


def test_no_swap_control_reports_the_symbol_and_exits_0(tmp_path: Path, monkeypatch: Any) -> None:
    repo, _ = _swap_race_repo(tmp_path)
    payload = build_diff_blast_radius(root=repo)
    assert {s["name"] for s in payload["changed_symbols"]} == {"two"}
    assert payload["not_analyzed_paths"] == []
    assert payload["partial"] is False
    monkeypatch.chdir(repo)
    res = runner.invoke(app, ["diff-impact", "--json"])
    assert res.exit_code == 0, res.stdout


def _simple_repo_with_changed_file(tmp_path: Path) -> Path:
    repo = tmp_path / "repo"
    _init_repo(repo)
    (repo / "app.py").write_text("def run():\n    return 1\n", encoding="utf-8")
    _git(repo, "add", "--all")
    _git(repo, "commit", "-qm", "i")
    (repo / "app.py").write_text("def run():\n    return 2\n", encoding="utf-8")
    return repo


def test_mapper_records_a_containment_failure_instead_of_dropping_it(tmp_path: Path) -> None:
    # Finding 1: the mapper's own containment branch used to `continue` silently.
    root = tmp_path / "repo"
    root.mkdir()
    outside = tmp_path / "outside.py"
    outside.write_text("def leaked():\n    return 1\n", encoding="utf-8")
    try:
        (root / "link.py").symlink_to(outside)
    except (OSError, NotImplementedError) as exc:
        pytest.skip(f"cannot create symlinks here: {exc}")
    out: list[dict[str, str]] = []
    assert map_changed_lines_to_symbols({Path("link.py"): [(1, 2)]}, root, out) == []
    assert out == [{"path": "link.py", "reason": "path_escapes_root"}]


def test_containment_swap_before_the_check_fails_closed_through_the_builder(
    tmp_path: Path, monkeypatch: Any
) -> None:
    repo, outside = _swap_race_repo(tmp_path)
    real = di.repo_map._path_is_relative_to
    calls: list[str] = []

    def swapping(path: Path, parent: Path) -> bool:
        if Path(str(path)).name == "link.py" and not calls:
            calls.append("swapped")
            (repo / "link.py").unlink()
            (repo / "link.py").symlink_to(outside)
        return real(path, parent)

    monkeypatch.setattr(di.repo_map, "_path_is_relative_to", swapping)
    payload = build_diff_blast_radius(root=repo)
    assert calls == ["swapped"]
    assert payload["partial"] is True
    assert {"path": "link.py", "reason": "path_escapes_root"} in payload["not_analyzed_paths"]
    assert "leaked" not in {s["name"] for s in payload["changed_symbols"]}


class _Spec:
    def __init__(self, fn: Any) -> None:
        self.extract_imports_and_symbols = fn


def _raiser(exc: BaseException) -> Any:
    def fail(*_a: Any, **_k: Any) -> Any:
        raise exc

    return fail


@pytest.mark.parametrize("branch", ["spec", "fallback"])
@pytest.mark.parametrize(
    "exc",
    [
        PermissionError("denied"),
        OSError("io"),
        ValueError("v"),
        SyntaxError("s"),
        UnicodeDecodeError("utf-8", b"\xff", 0, 1, "bad"),
        RecursionError("deep"),
    ],
)
def test_extractor_exception_is_recorded_not_swallowed(
    tmp_path: Path, monkeypatch: Any, branch: str, exc: BaseException
) -> None:
    root = tmp_path / "repo"
    root.mkdir()
    (root / "app.py").write_text("def run():\n    return 1\n", encoding="utf-8")
    if branch == "spec":
        monkeypatch.setattr(di.lang_registry, "spec_for_path", lambda path: _Spec(_raiser(exc)))
    else:
        monkeypatch.setattr(di.lang_registry, "spec_for_path", lambda path: None)
        monkeypatch.setattr(di.repo_map, "_imports_and_symbols_for_path", _raiser(exc))
    out: list[dict[str, str]] = []
    assert map_changed_lines_to_symbols({Path("app.py"): [(1, 2)]}, root, out) == []
    assert out == [{"path": "app.py", "reason": f"extraction_failed: {type(exc).__name__}"}]


def test_backend_execution_error_is_recorded(tmp_path: Path, monkeypatch: Any) -> None:
    from tensor_grep.backends.base import BackendExecutionError

    root = tmp_path / "repo"
    root.mkdir()
    (root / "app.py").write_text("def run():\n    return 1\n", encoding="utf-8")
    monkeypatch.setattr(
        di.lang_registry,
        "spec_for_path",
        lambda path: _Spec(_raiser(BackendExecutionError("native panic"))),
    )
    out: list[dict[str, str]] = []
    map_changed_lines_to_symbols({Path("app.py"): [(1, 2)]}, root, out)
    assert out == [{"path": "app.py", "reason": "extraction_failed: BackendExecutionError"}]


def test_unexpected_extractor_exception_is_not_swallowed(tmp_path: Path, monkeypatch: Any) -> None:
    root = tmp_path / "repo"
    root.mkdir()
    (root / "app.py").write_text("def run():\n    return 1\n", encoding="utf-8")
    monkeypatch.setattr(
        di.lang_registry, "spec_for_path", lambda path: _Spec(_raiser(KeyError("bug")))
    )
    with pytest.raises(KeyError):
        map_changed_lines_to_symbols({Path("app.py"): [(1, 2)]}, root, [])


def test_extractor_permission_error_exits_2_through_the_cli(
    tmp_path: Path, monkeypatch: Any
) -> None:
    repo = _simple_repo_with_changed_file(tmp_path)
    fired: list[str] = []
    real = di.lang_registry.spec_for_path

    def one_shot(path: Any) -> Any:
        if not fired:  # the mapper runs before the repo-map scan; fail only its extraction
            fired.append("x")
            return _Spec(_raiser(PermissionError("denied")))
        return real(path)

    monkeypatch.setattr(di.lang_registry, "spec_for_path", one_shot)
    monkeypatch.chdir(repo)
    res = runner.invoke(app, ["diff-impact", "--json"])
    assert res.exit_code == 2, res.stdout
    data = json.loads(res.stdout)
    assert data["not_analyzed_paths"] == [
        {"path": "app.py", "reason": "extraction_failed: PermissionError"}
    ]
    assert data["incomplete_reason"] == "extraction_failed"


def test_accounting_invariant_catches_a_branch_that_forgets_a_path(
    tmp_path: Path, monkeypatch: Any
) -> None:
    root = tmp_path / "repo"
    root.mkdir()
    (root / "a.py").write_text("x = 1\n", encoding="utf-8")
    (root / "b.py").write_text("y = 1\n", encoding="utf-8")
    real_one = di._map_one_path

    def forgetful(rel_path: Path, *args: Any, **kwargs: Any) -> Any:
        if rel_path.name == "b.py":
            return "forgotten", None, []  # a branch that records nothing
        return real_one(rel_path, *args, **kwargs)

    monkeypatch.setattr(di, "_map_one_path", forgetful)
    with pytest.raises(di.DiffError) as exc_info:
        map_changed_lines_to_symbols({Path("a.py"): [(1, 1)], Path("b.py"): [(1, 1)]}, root, [])
    assert exc_info.value.reason == "internal_accounting_error"
    assert "b.py" in str(exc_info.value)


def test_accounting_error_exits_2_through_the_cli(tmp_path: Path, monkeypatch: Any) -> None:
    repo = _simple_repo_with_changed_file(tmp_path)
    monkeypatch.setattr(di, "_map_one_path", lambda *a, **k: ("forgotten", None, []))
    monkeypatch.chdir(repo)
    res = runner.invoke(app, ["diff-impact", "--json"])
    assert res.exit_code == 2
    assert json.loads(res.stdout)["incomplete_reason"] == "internal_accounting_error"


def test_controls_ordinary_file_is_analyzed_and_deleted_file_keeps_its_handling(
    tmp_path: Path,
) -> None:
    repo = _simple_repo_with_changed_file(tmp_path)
    (repo / "gone.py").write_text("def g():\n    return 1\n", encoding="utf-8")
    _git(repo, "add", "--", "gone.py")
    _git(repo, "commit", "-qm", "g")
    (repo / "gone.py").unlink()
    payload = build_diff_blast_radius(root=repo)
    assert {s["name"] for s in payload["changed_symbols"]} == {"run"}
    assert payload["deleted_files"] == ["gone.py"]
    assert payload["not_analyzed_paths"] == []
    assert payload["partial"] is False


def _py_repo(tmp_path: Path, before: bytes, after: bytes) -> Path:
    repo = tmp_path / "repo"
    _init_repo(repo)
    (repo / "app.py").write_bytes(before)
    _git(repo, "add", "--all")
    _git(repo, "commit", "-qm", "i")
    (repo / "app.py").write_bytes(after)
    return repo


def test_real_extractor_swallowed_syntax_error_is_not_analyzed(
    tmp_path: Path, monkeypatch: Any
) -> None:
    # The REAL Python extractor catches SyntaxError itself and returns ([], []); with no raising
    # fake involved this used to be reported `analyzed`, losing `changed`, exit 0.
    repo = _py_repo(
        tmp_path,
        b"def changed():\n    return 1\n",
        b"def changed():\n    return 2\ndef broken(:\n",
    )
    monkeypatch.chdir(repo)
    res = runner.invoke(app, ["diff-impact", "--json"])
    assert res.exit_code == 2, res.stdout
    data = json.loads(res.stdout)
    assert data["not_analyzed_paths"] == [
        {"path": "app.py", "reason": "extraction_failed: SyntaxError"}
    ]
    assert data["incomplete_reason"] == "extraction_failed"
    assert data["exit_reason"] == "incomplete"


def test_real_extractor_swallowed_decode_error_is_not_analyzed(
    tmp_path: Path, monkeypatch: Any
) -> None:
    repo = _py_repo(
        tmp_path,
        b"def changed():\n    return 1\n",
        b"def changed():\n    return 2\n# bad byte \xff\n",
    )
    monkeypatch.chdir(repo)
    res = runner.invoke(app, ["diff-impact", "--json"])
    assert res.exit_code == 2, res.stdout
    data = json.loads(res.stdout)
    assert data["not_analyzed_paths"] == [
        {"path": "app.py", "reason": "extraction_failed: UnicodeDecodeError"}
    ]


def test_valid_symbol_free_python_file_stays_analyzed(tmp_path: Path, monkeypatch: Any) -> None:
    repo = _py_repo(tmp_path, b"x = 1\n", b"x = 2\n")
    monkeypatch.chdir(repo)
    res = runner.invoke(app, ["diff-impact", "--json"])
    assert res.exit_code == 0, res.stdout
    data = json.loads(res.stdout)
    assert data["not_analyzed_paths"] == []
    assert data["changed_files"] == ["app.py"]
    assert data["partial"] is False


def test_valid_python_file_with_symbols_is_unchanged(tmp_path: Path, monkeypatch: Any) -> None:
    repo = _py_repo(tmp_path, b"def changed():\n    return 1\n", b"def changed():\n    return 2\n")
    monkeypatch.chdir(repo)
    res = runner.invoke(app, ["diff-impact", "--json"])
    assert res.exit_code == 0, res.stdout
    assert [s["name"] for s in json.loads(res.stdout)["changed_symbols"]] == ["changed"]


def _outside_symlink_repo(tmp_path: Path) -> Path:
    repo = tmp_path / "repo"
    _init_repo(repo)
    (tmp_path / "outside.py").write_text("def leaked():\n    return 1\n", encoding="utf-8")
    try:
        (repo / "link.py").symlink_to(Path("..") / "outside.py")
    except (OSError, NotImplementedError) as exc:
        pytest.skip(f"cannot create symlinks here: {exc}")
    (repo / "keep.py").write_text("x = 1\n", encoding="utf-8")
    _git(repo, "-c", "core.symlinks=true", "add", "--all")
    _git(repo, "commit", "-qm", "i")
    return repo


def test_staged_deletion_of_an_outside_pointing_symlink_is_a_deletion(
    tmp_path: Path, monkeypatch: Any
) -> None:
    repo = _outside_symlink_repo(tmp_path)
    _git(repo, "rm", "-q", "--cached", "--", "link.py")  # the symlink survives in the work tree
    assert (repo / "link.py").is_symlink()
    monkeypatch.chdir(repo)
    res = runner.invoke(app, ["diff-impact", "--json", "--staged"])
    assert res.exit_code == 0, res.stdout
    data = json.loads(res.stdout)
    assert data["deleted_files"] == ["link.py"]
    assert data["not_analyzed_paths"] == []
    assert data["partial"] is False


def test_staged_deletion_of_an_ordinary_file_is_unchanged(tmp_path: Path, monkeypatch: Any) -> None:
    repo = _outside_symlink_repo(tmp_path)
    _git(repo, "rm", "-q", "--", "keep.py")
    monkeypatch.chdir(repo)
    res = runner.invoke(app, ["diff-impact", "--json", "--staged"])
    assert res.exit_code == 0, res.stdout
    assert json.loads(res.stdout)["deleted_files"] == ["keep.py"]


def test_changed_not_deleted_outside_symlink_still_escapes(
    tmp_path: Path, monkeypatch: Any
) -> None:
    repo = _outside_symlink_repo(tmp_path)
    (tmp_path / "outside2.py").write_text("def other():\n    return 1\n", encoding="utf-8")
    (repo / "link.py").unlink()
    (repo / "link.py").symlink_to(Path("..") / "outside2.py")
    monkeypatch.chdir(repo)
    res = runner.invoke(app, ["diff-impact", "--json"])
    assert res.exit_code == 2
    assert json.loads(res.stdout)["not_analyzed_paths"] == [
        {"path": "link.py", "reason": "path_escapes_root"}
    ]


def test_deletion_exemption_never_suppresses_analysis_of_a_surviving_source_file(
    tmp_path: Path,
) -> None:
    # A path can be both "deleted" and re-added (type change) and then has content ranges.
    root = tmp_path / "repo"
    root.mkdir()
    (root / "app.py").write_text("def changed():\n    return 2\n", encoding="utf-8")
    hunks = di.DiffHunks()
    hunks[Path("app.py")] = [(1, 2)]
    hunks.deleted_paths.add(Path("app.py"))
    out: list[dict[str, str]] = []
    symbols = map_changed_lines_to_symbols(hunks, root, out)
    assert [s["name"] for s in symbols] == ["changed"]
    assert out == []


@pytest.mark.parametrize("name", ["app.PY", "app.Py", "app.pY"])
@pytest.mark.parametrize(
    ("after", "reason"),
    [
        (b"def changed():\n    return 2\ndef broken(:\n", "extraction_failed: SyntaxError"),
        (
            b"def changed():\n    return 2\n# bad byte \xff\n",
            "extraction_failed: UnicodeDecodeError",
        ),
    ],
)
def test_uppercase_python_suffix_cannot_bypass_the_parse_check(
    tmp_path: Path, monkeypatch: Any, name: str, after: bytes, reason: str
) -> None:
    repo = tmp_path / "repo"
    _init_repo(repo)
    (repo / name).write_bytes(b"def changed():\n    return 1\n")
    _git(repo, "add", "--all")
    _git(repo, "commit", "-qm", "i")
    (repo / name).write_bytes(after)
    monkeypatch.chdir(repo)
    res = runner.invoke(app, ["diff-impact", "--json"])
    assert res.exit_code == 2, res.stdout
    data = json.loads(res.stdout)
    assert data["not_analyzed_paths"] == [{"path": name, "reason": reason}]
    assert data["incomplete_reason"] == "extraction_failed"


def test_uppercase_suffix_symbol_free_valid_file_stays_analyzed(
    tmp_path: Path, monkeypatch: Any
) -> None:
    repo = tmp_path / "repo"
    _init_repo(repo)
    (repo / "app.PY").write_bytes(b"x = 1\n")
    _git(repo, "add", "--all")
    _git(repo, "commit", "-qm", "i")
    (repo / "app.PY").write_bytes(b"x = 2\n")
    monkeypatch.chdir(repo)
    res = runner.invoke(app, ["diff-impact", "--json"])
    assert res.exit_code == 0, res.stdout
    assert json.loads(res.stdout)["not_analyzed_paths"] == []


@pytest.mark.parametrize("name", ["app.PY", "app.Py", "app.pY"])
def test_valid_uppercase_python_file_with_symbols_reports_them(
    tmp_path: Path, monkeypatch: Any, name: str
) -> None:
    # The Python extractor now agrees with the registry (which lowercases suffixes), so a valid
    # `app.PY` is analysed like `app.py`: its changed symbol is reported and the run is clean.
    repo = tmp_path / "repo"
    _init_repo(repo)
    (repo / name).write_bytes(b"def changed():\n    return 1\n")
    _git(repo, "add", "--all")
    _git(repo, "commit", "-qm", "i")
    (repo / name).write_bytes(b"def changed():\n    return 2\n")
    monkeypatch.chdir(repo)
    res = runner.invoke(app, ["diff-impact", "--json"])
    assert res.exit_code == 0, res.stdout
    data = json.loads(res.stdout)
    assert [s["name"] for s in data["changed_symbols"]] == ["changed"]
    assert data["not_analyzed_paths"] == []


def _commit_app(repo: Path, body: bytes, name: str = "app.py") -> None:
    (repo / name).write_bytes(body)
    _git(repo, "add", "--", name)
    _git(repo, "commit", "-qm", f"c-{name}")


def _staged_function_repo(tmp_path: Path) -> Path:
    repo = tmp_path / "repo"
    _init_repo(repo)
    _commit_app(repo, b"def old():\n    return 1\n")
    (repo / "app.py").write_bytes(
        b"def old():\n    return 1\n\n\ndef staged_function():\n    return 2\n"
    )
    _git(repo, "add", "--", "app.py")
    (repo / "app.py").write_bytes(b"x = 1\n")  # unrelated, symbol-free working-tree content
    return repo


def test_staged_diff_is_analysed_from_the_index_blob_not_the_working_tree(
    tmp_path: Path, monkeypatch: Any
) -> None:
    repo = _staged_function_repo(tmp_path)
    monkeypatch.chdir(repo)
    res = runner.invoke(app, ["diff-impact", "--json", "--staged"])
    assert res.exit_code == 0, res.stdout
    data = json.loads(res.stdout)
    assert [s["name"] for s in data["changed_symbols"]] == ["staged_function"]
    assert data["changed_symbols"][0]["file"] == "app.py"  # the ORIGINAL repo-relative path
    assert data["not_analyzed_paths"] == []


def test_revision_range_is_analysed_from_the_destination_blob(
    tmp_path: Path, monkeypatch: Any
) -> None:
    repo = tmp_path / "repo"
    _init_repo(repo)
    _commit_app(repo, b"def a():\n    return 1\n")
    _commit_app(repo, b"def a():\n    return 1\n\n\ndef b_in_range():\n    return 2\n")
    (repo / "app.py").write_bytes(b"x = 1\n")  # the working tree has since diverged
    monkeypatch.chdir(repo)
    res = runner.invoke(app, ["diff-impact", "--json", "HEAD~1..HEAD"])
    assert res.exit_code == 0, res.stdout
    data = json.loads(res.stdout)
    assert [s["name"] for s in data["changed_symbols"]] == ["b_in_range"]


def test_unstaged_control_still_uses_and_verifies_the_working_tree(
    tmp_path: Path, monkeypatch: Any
) -> None:
    repo = tmp_path / "repo"
    _init_repo(repo)
    _commit_app(repo, b"def a():\n    return 1\n")
    (repo / "app.py").write_bytes(b"def a():\n    return 2\n")
    monkeypatch.chdir(repo)
    res = runner.invoke(app, ["diff-impact", "--json"])
    assert res.exit_code == 0, res.stdout
    assert [s["name"] for s in json.loads(res.stdout)["changed_symbols"]] == ["a"]


def test_unstaged_crlf_working_tree_with_autocrlf_is_not_a_hash_mismatch(
    tmp_path: Path, monkeypatch: Any
) -> None:
    repo = tmp_path / "repo"
    _init_repo(repo)
    _git(repo, "config", "core.autocrlf", "true")
    _commit_app(repo, b"def a():\r\n    return 1\r\n")
    (repo / "app.py").write_bytes(b"def a():\r\n    return 2\r\n")
    monkeypatch.chdir(repo)
    res = runner.invoke(app, ["diff-impact", "--json"])
    assert res.exit_code == 0, res.stdout
    assert [s["name"] for s in json.loads(res.stdout)["changed_symbols"]] == ["a"]


def test_working_tree_that_changed_after_the_diff_was_taken_fails_closed(
    tmp_path: Path, monkeypatch: Any
) -> None:
    repo = tmp_path / "repo"
    _init_repo(repo)
    _commit_app(repo, b"def a():\n    return 1\n")
    (repo / "app.py").write_bytes(b"def a():\n    return 2\n")
    monkeypatch.setattr(di, "_worktree_hash", lambda root, rel: "0" * 40)
    payload = build_diff_blast_radius(root=repo)
    assert payload["partial"] is True
    assert payload["not_analyzed_paths"] == [
        {"path": "app.py", "reason": "path_changed_during_analysis"}
    ]


def test_blob_read_failure_is_blob_unavailable_and_exits_2(
    tmp_path: Path, monkeypatch: Any
) -> None:
    repo = _staged_function_repo(tmp_path)

    def broken(root: Path, oid: str, max_bytes: int) -> bytes:
        raise di._BlobUnavailable("simulated read failure")

    monkeypatch.setattr(di, "_read_blob", broken)
    monkeypatch.chdir(repo)
    res = runner.invoke(app, ["diff-impact", "--json", "--staged"])
    assert res.exit_code == 2, res.stdout
    data = json.loads(res.stdout)
    assert data["not_analyzed_paths"] == [{"path": "app.py", "reason": "blob_unavailable"}]
    assert data["incomplete_reason"] == "blob_unavailable"


def test_blob_bytes_that_do_not_match_the_diff_oid_are_blob_unavailable(
    tmp_path: Path, monkeypatch: Any
) -> None:
    repo = _staged_function_repo(tmp_path)
    monkeypatch.setattr(di, "_read_blob", lambda root, oid, max_bytes: b"def forged():\n    pass\n")
    payload = build_diff_blast_radius(root=repo, staged=True)
    assert payload["partial"] is True
    assert payload["not_analyzed_paths"] == [{"path": "app.py", "reason": "blob_unavailable"}]
    assert "forged" not in {s["name"] for s in payload["changed_symbols"]}


def test_over_cap_blob_is_not_analyzed(tmp_path: Path, monkeypatch: Any) -> None:
    repo = _staged_function_repo(tmp_path)
    monkeypatch.setenv("TENSOR_GREP_MAX_PARSE_BYTES", "10")
    payload = build_diff_blast_radius(root=repo, staged=True)
    assert payload["partial"] is True
    assert payload["not_analyzed_paths"] == [{"path": "app.py", "reason": "over_cap"}]


def test_temp_file_is_deleted_on_success_and_when_extraction_raises(
    tmp_path: Path, monkeypatch: Any
) -> None:
    repo = _staged_function_repo(tmp_path)
    made: list[Path] = []
    real_mkstemp = tempfile.mkstemp

    def tracking(*a: Any, **k: Any) -> Any:
        fd, name = real_mkstemp(*a, **k)
        made.append(Path(name))
        return fd, name

    monkeypatch.setattr(di.tempfile, "mkstemp", tracking)
    payload = build_diff_blast_radius(root=repo, staged=True)
    assert payload["partial"] is False
    assert len(made) == 1 and made[0].suffix == ".py"
    assert not made[0].exists()
    assert not str(made[0]).startswith(str(repo))  # never inside the repository

    made.clear()
    real_spec = di.lang_registry.spec_for_path
    fired: list[str] = []

    def one_shot(path: Any) -> Any:  # fail only the mapper's extraction, not the repo-map scan
        if not fired:
            fired.append("x")
            return _Spec(_raiser(PermissionError("denied")))
        return real_spec(path)

    monkeypatch.setattr(di.lang_registry, "spec_for_path", one_shot)
    payload = build_diff_blast_radius(root=repo, staged=True)
    assert payload["not_analyzed_paths"] == [
        {"path": "app.py", "reason": "extraction_failed: PermissionError"}
    ]
    assert len(made) == 1 and not made[0].exists()


def test_staged_add_whose_working_tree_file_is_gone_is_analysed_from_the_index(
    tmp_path: Path, monkeypatch: Any
) -> None:
    repo = tmp_path / "repo"
    _init_repo(repo)
    _commit_app(repo, b"x = 1\n", "keep.py")
    (repo / "new.py").write_bytes(b"def added():\n    return 1\n")
    _git(repo, "add", "--", "new.py")
    (repo / "new.py").unlink()
    monkeypatch.chdir(repo)
    res = runner.invoke(app, ["diff-impact", "--json", "--staged"])
    assert res.exit_code == 0, res.stdout
    assert [s["name"] for s in json.loads(res.stdout)["changed_symbols"]] == ["added"]


def test_staged_syntax_error_in_the_blob_is_extraction_failed(
    tmp_path: Path, monkeypatch: Any
) -> None:
    repo = tmp_path / "repo"
    _init_repo(repo)
    _commit_app(repo, b"def a():\n    return 1\n")
    (repo / "app.py").write_bytes(b"def a():\n    return 2\ndef broken(:\n")
    _git(repo, "add", "--", "app.py")
    (repo / "app.py").write_bytes(b"def a():\n    return 1\n")  # clean working tree, bad index
    monkeypatch.chdir(repo)
    res = runner.invoke(app, ["diff-impact", "--json", "--staged"])
    assert res.exit_code == 2, res.stdout
    assert json.loads(res.stdout)["not_analyzed_paths"] == [
        {"path": "app.py", "reason": "extraction_failed: SyntaxError"}
    ]


def test_staged_deleted_binary_and_submodule_paths_keep_their_handling(
    tmp_path: Path, monkeypatch: Any
) -> None:
    repo = tmp_path / "repo"
    _init_repo(repo)
    _commit_app(repo, b"def gone():\n    return 1\n", "gone.py")
    (repo / "data.bin").write_bytes(b"\x00\x01\x02")
    _git(repo, "add", "--", "data.bin")
    _git(repo, "update-index", "--add", "--cacheinfo", f"160000,{'1' * 40},vendor/lib")
    _git(repo, "commit", "-qm", "base")
    _git(repo, "rm", "-q", "--", "gone.py")
    (repo / "data.bin").write_bytes(b"\x00\x09\x08")
    _git(repo, "add", "--", "data.bin")
    _git(repo, "update-index", "--add", "--cacheinfo", f"160000,{'2' * 40},vendor/lib")
    monkeypatch.chdir(repo)
    res = runner.invoke(app, ["diff-impact", "--json", "--staged"])
    assert res.exit_code == 0, res.stdout
    data = json.loads(res.stdout)
    assert data["deleted_files"] == ["gone.py"]
    assert data["binary_files"] == ["data.bin"]
    assert data["submodule_changed_files"] == ["vendor/lib"]
    assert data["not_analyzed_paths"] == []


def _many_files_repo(tmp_path: Path, count: int = 50) -> Path:
    repo = tmp_path / "repo"
    _init_repo(repo)
    for i in range(count):
        (repo / f"mod_{i:02d}.py").write_text(f"def f_{i}():\n    return 1\n", encoding="utf-8")
    _git(repo, "add", "--all")
    _git(repo, "commit", "-qm", "base")
    for i in range(count):
        (repo / f"mod_{i:02d}.py").write_text(f"def f_{i}():\n    return 2\n", encoding="utf-8")
    return repo


def _count_content_spawns(monkeypatch: Any) -> list[list[str]]:
    """Every git process spawned to read blobs or hash files, via either spawn seam."""
    spawned: list[list[str]] = []
    real_run = di.run_subprocess

    def run_spy(cmd: Any, **kwargs: Any) -> Any:
        if any(a in ("cat-file", "hash-object") for a in cmd):
            spawned.append(list(cmd))
        return real_run(cmd, **kwargs)

    monkeypatch.setattr(di, "run_subprocess", run_spy)
    real_spawn = getattr(dig, "spawn_git", None)
    if real_spawn is not None:

        def spawn_spy(root: Path, args: list[str]) -> Any:
            spawned.append(list(args))
            return real_spawn(root, args)

        monkeypatch.setattr(dig, "spawn_git", spawn_spy)
    return spawned


def test_staged_fifty_file_diff_uses_one_batch_process_not_per_file_spawns(
    tmp_path: Path, monkeypatch: Any
) -> None:
    repo = _many_files_repo(tmp_path)
    _git(repo, "add", "--all")
    spawned = _count_content_spawns(monkeypatch)
    payload = build_diff_blast_radius(root=repo, staged=True)
    assert len(payload["changed_symbols"]) == 50
    assert payload["not_analyzed_paths"] == []
    assert len(spawned) <= 2, spawned[:3]  # PINNED: one `cat-file --batch`, never ~100
    assert any("--batch" in a for a in spawned)


def test_unstaged_fifty_file_diff_uses_one_hash_process_not_per_file_spawns(
    tmp_path: Path, monkeypatch: Any
) -> None:
    repo = _many_files_repo(tmp_path)
    spawned = _count_content_spawns(monkeypatch)
    payload = build_diff_blast_radius(root=repo)
    assert len(payload["changed_symbols"]) == 50
    assert payload["not_analyzed_paths"] == []
    assert len(spawned) <= 2, spawned[:3]  # PINNED: one `hash-object --stdin-paths`, never ~50
    assert any("--stdin-paths" in a for a in spawned)


def test_batch_session_is_closed_after_the_run(tmp_path: Path, monkeypatch: Any) -> None:
    repo = _many_files_repo(tmp_path, count=3)
    _git(repo, "add", "--all")
    procs: list[Any] = []
    real_spawn = dig.spawn_git

    def tracking(root: Path, args: list[str]) -> Any:
        proc = real_spawn(root, args)
        procs.append(proc)
        return proc

    monkeypatch.setattr(dig, "spawn_git", tracking)
    build_diff_blast_radius(root=repo, staged=True)
    assert len(procs) == 1
    assert procs[0].poll() is not None  # terminated, not left running


def test_over_cap_blob_is_skipped_without_poisoning_the_batch(
    tmp_path: Path, monkeypatch: Any
) -> None:
    repo = tmp_path / "repo"
    _init_repo(repo)
    _commit_app(repo, b"def small():\n    return 1\n", "small.py")
    _commit_app(repo, b"def big():\n    return 1\n" + b"# pad\n" * 50, "big.py")
    (repo / "small.py").write_bytes(b"def small():\n    return 2\n")
    (repo / "big.py").write_bytes(b"def big():\n    return 2\n" + b"# pad\n" * 50)
    _git(repo, "add", "--all")
    monkeypatch.setenv("TENSOR_GREP_MAX_PARSE_BYTES", "100")
    payload = build_diff_blast_radius(root=repo, staged=True)
    assert payload["not_analyzed_paths"] == [{"path": "big.py", "reason": "over_cap"}]
    assert [s["name"] for s in payload["changed_symbols"]] == ["small"]  # still served after it


def test_cat_file_batch_reply_shapes_map_to_blob_unavailable(monkeypatch: Any) -> None:
    import io

    cases = [
        b"",  # process closed its output
        b"abc1234 missing\n",
        b"abc1234 ambiguous\n",
        b"garbage header\n",
        b"abc1234 tree 4\nxxxx\n",  # not a blob
        b"abc1234 blob 10\nshort\n",  # truncated content
    ]
    for reply in cases:
        with pytest.raises(di._BlobUnavailable):
            di._parse_cat_file_reply(io.BytesIO(reply), 1000)
    with pytest.raises(di._BlobOverCap):
        di._parse_cat_file_reply(io.BytesIO(b"abc1234 blob 5000\n" + b"x" * 5000 + b"\n"), 1000)
    assert di._parse_cat_file_reply(io.BytesIO(b"abc1234 blob 3\nabc\n"), 1000) == b"abc"
