"""Symbol commands: an ERROR (missing path, invalid input) exits 2, never 1; 1 stays "not found".

Driven through the real Typer app (CliRunner), not by replaying handlers.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from typer.testing import CliRunner

from tensor_grep.cli.main import app

runner = CliRunner()
_COMMANDS = [
    "defs",
    "source",
    "impact",
    "refs",
    "callers",
    "blast-radius",
    "blast-radius-render",
    "blast-radius-plan",
]


def _project(tmp_path: Path) -> Path:
    project = tmp_path / "project"
    project.mkdir()
    (project / "m.py").write_text(
        "def known():\n    return 1\n\ndef user():\n    return known()\n", encoding="utf-8"
    )
    return project


@pytest.mark.parametrize("command", _COMMANDS)
def test_missing_path_is_an_error_exit_2(tmp_path: Path, command: str) -> None:
    project = _project(tmp_path)
    # CONTROL: the same command on the existing project resolves the symbol (exit 0).
    assert runner.invoke(app, [command, str(project), "known"]).exit_code == 0

    missing = tmp_path / "does" / "not" / "exist"
    result = runner.invoke(app, [command, str(missing), "known"])
    assert result.exit_code == 2, result.output
    assert not missing.exists()


@pytest.mark.parametrize("command", _COMMANDS)
def test_invalid_input_is_an_error_exit_2(tmp_path: Path, command: str) -> None:
    project = _project(tmp_path)
    # "Missing symbol" / "positional SYMBOL and --symbol together" are ValueErrors in the handler.
    no_symbol = runner.invoke(app, [command, str(project)])
    assert no_symbol.exit_code == 2, no_symbol.output
    both = runner.invoke(app, [command, str(project), "known", "--symbol", "known"])
    assert both.exit_code == 2, both.output


@pytest.mark.parametrize("command", _COMMANDS)
def test_genuinely_not_found_still_exits_1(tmp_path: Path, command: str) -> None:
    project = _project(tmp_path)
    result = runner.invoke(app, [command, str(project), "no_such_symbol_zz"])
    assert result.exit_code == 1, result.output
