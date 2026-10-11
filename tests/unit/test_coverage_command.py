from __future__ import annotations

import hashlib
import json
from pathlib import Path

from typer.testing import CliRunner

from tensor_grep.cli.main import app


def test_coverage_cold_root_reports_files_as_unindexed(tmp_path: Path) -> None:
    project = tmp_path / "project"
    project.mkdir()
    (project / "main.py").write_text("def run():\n    return 1\n", encoding="utf-8")

    result = CliRunner().invoke(app, ["coverage", str(project), "--json"])

    assert result.exit_code == 2, result.output
    payload = json.loads(result.stdout)
    assert payload["files_total"] == 1
    assert payload["unindexed"] == ["main.py"]
    assert payload["complete"] is False


def test_coverage_added_file_is_stale_and_unindexed(tmp_path: Path) -> None:
    project = tmp_path / "project"
    project.mkdir()
    (project / "main.py").write_text("value = 1\n", encoding="utf-8")
    runner = CliRunner()
    opened = runner.invoke(app, ["session", "open", str(project), "--json"])
    assert opened.exit_code == 0, opened.output
    (project / "added.py").write_text("value = 2\n", encoding="utf-8")

    result = runner.invoke(app, ["coverage", str(project), "--json"])

    assert result.exit_code == 2, result.output
    payload = json.loads(result.stdout)
    assert {"path": "added.py", "reason": "added"} in payload["stale"]
    assert "added.py" in payload["unindexed"]


def test_coverage_exact_repo_map_limit_does_not_report_a_hit(tmp_path: Path, monkeypatch) -> None:
    from tensor_grep.cli import repo_map

    project = tmp_path / "project"
    project.mkdir()
    for name in ("first.py", "second.py"):
        (project / name).write_text("value = 1\n", encoding="utf-8")
    monkeypatch.setattr(repo_map, "DEFAULT_AGENT_REPO_MAP_LIMIT", 2)

    result = CliRunner().invoke(app, ["coverage", str(project), "--json"])

    assert result.exit_code == 2, result.output
    payload = json.loads(result.stdout)
    assert payload["files_total"] == 2
    assert payload["caps"] == []


def test_coverage_reports_repo_map_limit_hit(tmp_path: Path, monkeypatch) -> None:
    from tensor_grep.cli import repo_map

    project = tmp_path / "project"
    project.mkdir()
    for name in ("first.py", "second.py", "third.py"):
        (project / name).write_text("value = 1\n", encoding="utf-8")
    monkeypatch.setattr(repo_map, "DEFAULT_AGENT_REPO_MAP_LIMIT", 2)

    result = CliRunner().invoke(app, ["coverage", str(project), "--json"])

    assert result.exit_code == 2, result.output
    payload = json.loads(result.stdout)
    assert payload["files_total"] == 2
    assert payload["caps"] == ["max_repo_files"]
    assert payload["complete"] is False


def _tree_bytes(root: Path) -> dict[str, str]:
    return {
        item.relative_to(root).as_posix(): hashlib.sha256(item.read_bytes()).hexdigest()
        for item in root.rglob("*")
        if item.is_file()
    }


def test_coverage_after_session_is_complete_and_read_only(tmp_path: Path) -> None:
    project = tmp_path / "project"
    project.mkdir()
    (project / "main.py").write_text("def run():\n    return 1\n", encoding="utf-8")
    runner = CliRunner()
    opened = runner.invoke(app, ["session", "open", str(project), "--json"])
    assert opened.exit_code == 0, opened.output
    before = _tree_bytes(project)

    result = runner.invoke(app, ["coverage", str(project), "--json"])

    assert result.exit_code == 0, result.output
    payload = json.loads(result.stdout)
    assert payload["complete"] is True
    assert payload["files_total"] == 1
    assert payload["stale"] == []
    assert payload["unindexed"] == []
    assert payload["unparsed"] == []
    assert _tree_bytes(project) == before


def test_coverage_reports_modified_and_unparseable_files(tmp_path: Path) -> None:
    project = tmp_path / "project"
    project.mkdir()
    modified = project / "modified.py"
    malformed = project / "malformed.py"
    modified.write_text("def run():\n    return 1\n", encoding="utf-8")
    malformed.write_text("def broken():\n    return 1\n", encoding="utf-8")
    opened = CliRunner().invoke(app, ["session", "open", str(project), "--json"])
    assert opened.exit_code == 0, opened.output
    modified.write_text("def run():\n    return 2\n", encoding="utf-8")
    malformed.write_text("def broken(:\n    return 1\n", encoding="utf-8")

    result = CliRunner().invoke(app, ["coverage", str(project), "--json"])

    assert result.exit_code == 2, result.output
    payload = json.loads(result.stdout)
    assert {entry["path"] for entry in payload["stale"] if entry["reason"] == "modified"} == {
        "modified.py",
        "malformed.py",
    }
    assert {entry["path"] for entry in payload["unparsed"]} == {"malformed.py"}
    assert payload["complete"] is False
