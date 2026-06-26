"""Integration tests for the `tg orient` CLI command (Plan 2 Task 2)."""

import json
from pathlib import Path

from typer.testing import CliRunner

from tensor_grep.cli.main import app

runner = CliRunner()


def test_orient_command_outputs_human_capsule(tmp_path: Path) -> None:
    (tmp_path / "hub.py").write_text("def hub():\n    pass\n", encoding="utf-8")
    (tmp_path / "leaf.py").write_text("import hub\n\n\ndef leaf():\n    pass\n", encoding="utf-8")

    result = runner.invoke(app, ["orient", str(tmp_path)])

    assert result.exit_code == 0, result.output
    assert "Codebase orientation" in result.output
    assert "central files" in result.output


def test_orient_command_json_is_parseable(tmp_path: Path) -> None:
    (tmp_path / "main.py").write_text("def run():\n    pass\n", encoding="utf-8")

    result = runner.invoke(app, ["orient", str(tmp_path), "--json"])

    assert result.exit_code == 0, result.output
    payload = json.loads(result.output)
    assert payload["routing_reason"] == "orient"
    assert "central_files" in payload
