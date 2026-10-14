from pathlib import Path

import pytest
from typer.testing import CliRunner

from tensor_grep.cli.main import app


def _create_dangling_symlink(path: Path, target: Path) -> None:
    try:
        path.symlink_to(target)
    except OSError as exc:
        pytest.skip(f"cannot create symlink on this host: {exc}")


def test_new_rule_scaffold_refuses_dangling_symlink_destination(tmp_path: Path) -> None:
    target_dir = tmp_path / "rules"
    target_dir.mkdir()
    target = tmp_path / "outside-missing-rule.yml"
    destination = target_dir / "sample.yml"
    _create_dangling_symlink(destination, target)

    result = CliRunner().invoke(
        app, ["new", "rule", "sample", "--lang", "python", "--base-dir", str(tmp_path)]
    )

    assert result.exit_code == 1, result.output
    assert destination.is_symlink()
    assert not target.exists()


def test_new_test_scaffold_refuses_dangling_symlink_destination(tmp_path: Path) -> None:
    target_dir = tmp_path / "tests"
    target_dir.mkdir()
    target = tmp_path / "outside-missing-test.yml"
    destination = target_dir / "sample.yml"
    _create_dangling_symlink(destination, target)

    result = CliRunner().invoke(app, ["new", "test", "sample", "--base-dir", str(tmp_path)])

    assert result.exit_code == 1, result.output
    assert destination.is_symlink()
    assert not target.exists()


def test_new_project_scaffold_refuses_dangling_sgconfig_symlink(tmp_path: Path) -> None:
    project_dir = tmp_path / "demo"
    project_dir.mkdir()
    target = tmp_path / "outside-missing-sgconfig.yml"
    destination = project_dir / "sgconfig.yml"
    _create_dangling_symlink(destination, target)

    result = CliRunner().invoke(app, ["new", "project", "demo", "--base-dir", str(tmp_path)])

    assert result.exit_code == 1, result.output
    assert destination.is_symlink()
    assert not target.exists()
