import json
import shutil
import subprocess
from pathlib import Path

import pytest
from typer.testing import CliRunner

from tensor_grep.cli.main import app


def test_checkpoint_create_list_and_undo_restores_non_git_tree(tmp_path: Path) -> None:
    project = tmp_path / "project"
    project.mkdir()
    source_file = project / "sample.py"
    source_file.write_text("print('before')\n", encoding="utf-8")

    runner = CliRunner()

    create_result = runner.invoke(app, ["checkpoint", "create", str(project), "--json"])
    assert create_result.exit_code == 0
    payload = json.loads(create_result.stdout)
    assert payload["mode"] == "filesystem-snapshot"
    assert payload["undo_argv"] == [
        "tg",
        "checkpoint",
        "undo",
        payload["checkpoint_id"],
        str(project.resolve()),
    ]
    checkpoint_id = payload["checkpoint_id"]

    list_result = runner.invoke(app, ["checkpoint", "list", str(project), "--json"])
    assert list_result.exit_code == 0
    listed = json.loads(list_result.stdout)
    assert listed["root"] == str(project.resolve())
    assert listed["checkpoint_count"] == 1
    assert listed["checkpoints"][0]["checkpoint_id"] == checkpoint_id

    source_file.write_text("print('after')\n", encoding="utf-8")
    extra_file = project / "generated.py"
    extra_file.write_text("print('new')\n", encoding="utf-8")

    undo_result = runner.invoke(
        app,
        ["checkpoint", "undo", checkpoint_id, str(project), "--json"],
    )
    assert undo_result.exit_code == 0
    restored = json.loads(undo_result.stdout)
    assert restored["checkpoint_id"] == checkpoint_id
    assert restored["mode"] == "filesystem-snapshot"
    assert source_file.read_text(encoding="utf-8") == "print('before')\n"
    assert not extra_file.exists()


def test_checkpoint_list_explains_empty_scope(tmp_path: Path) -> None:
    runner = CliRunner()
    result = runner.invoke(app, ["checkpoint", "list", str(tmp_path)])

    assert result.exit_code == 0
    assert f"Checkpoint root: {tmp_path.resolve()}" in result.stdout
    assert "No checkpoints found under this scope." in result.stdout
    assert "Use `tg checkpoint list PATH --discover`" in result.stdout


def test_checkpoint_list_discover_finds_child_checkpoint_scope(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    project = workspace / "project"
    project.mkdir(parents=True)
    (project / "sample.py").write_text("print('before')\n", encoding="utf-8")

    runner = CliRunner()
    create_result = runner.invoke(app, ["checkpoint", "create", str(project), "--json"])
    assert create_result.exit_code == 0
    checkpoint_id = json.loads(create_result.stdout)["checkpoint_id"]

    discover_result = runner.invoke(
        app,
        ["checkpoint", "list", str(workspace), "--discover", "--json"],
    )

    assert discover_result.exit_code == 0
    payload = json.loads(discover_result.stdout)
    assert payload["checkpoint_count"] == 1
    assert payload["discovered_scopes"][0]["root"] == str(project.resolve())
    assert payload["discovered_scopes"][0]["checkpoints"][0]["checkpoint_id"] == checkpoint_id


@pytest.mark.skipif(shutil.which("git") is None, reason="git is required for git checkpoint tests")
def test_checkpoint_create_and_undo_reports_git_mode(tmp_path: Path) -> None:
    project = tmp_path / "repo"
    project.mkdir()
    source_file = project / "sample.py"
    source_file.write_text("print('before')\n", encoding="utf-8")

    subprocess.run(["git", "init"], cwd=project, check=True, capture_output=True, text=True)
    subprocess.run(
        ["git", "config", "user.email", "tg@example.com"],
        cwd=project,
        check=True,
        capture_output=True,
        text=True,
    )
    subprocess.run(
        ["git", "config", "user.name", "tensor-grep"],
        cwd=project,
        check=True,
        capture_output=True,
        text=True,
    )
    subprocess.run(["git", "add", "."], cwd=project, check=True, capture_output=True, text=True)
    subprocess.run(
        ["git", "commit", "-m", "init"],
        cwd=project,
        check=True,
        capture_output=True,
        text=True,
    )

    runner = CliRunner()
    create_result = runner.invoke(app, ["checkpoint", "create", str(project), "--json"])
    assert create_result.exit_code == 0
    payload = json.loads(create_result.stdout)
    assert payload["mode"] == "git-worktree-snapshot"
    checkpoint_id = payload["checkpoint_id"]

    source_file.write_text("print('changed')\n", encoding="utf-8")
    untracked = project / "notes.txt"
    untracked.write_text("scratch\n", encoding="utf-8")

    undo_result = runner.invoke(
        app,
        ["checkpoint", "undo", checkpoint_id, str(project), "--json"],
    )
    assert undo_result.exit_code == 0
    restored = json.loads(undo_result.stdout)
    assert restored["mode"] == "git-worktree-snapshot"
    assert source_file.read_text(encoding="utf-8") == "print('before')\n"
    assert not untracked.exists()
