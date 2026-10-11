from __future__ import annotations

import json
from pathlib import Path

from typer.testing import CliRunner

from tensor_grep.cli.main import app


def test_session_refresh_json_missing_id_has_actionable_hint(tmp_path: Path) -> None:
    result = CliRunner().invoke(app, ["session", "refresh", ".", str(tmp_path), "--json"])

    assert result.exit_code == 1
    assert json.loads(result.stdout) == {
        "error": {
            "code": "session_not_found",
            "message": "Session not found: .",
            "hint": "pass SESSION_ID (see tg session list PATH --json)",
        }
    }
    text_result = CliRunner().invoke(app, ["session", "refresh", ".", str(tmp_path)])
    assert text_result.exit_code == 1
    assert "Hint: pass SESSION_ID (see tg session list PATH --json)" in text_result.stderr


def test_scoped_session_refresh_keeps_original_scan_scope(tmp_path: Path) -> None:
    (tmp_path / "pyproject.toml").write_text("[project]\nname = 'fixture'\n", encoding="utf-8")
    scoped = tmp_path / "scope"
    scoped.mkdir()
    (scoped / "inside.py").write_text("inside = 1\n", encoding="utf-8")
    (tmp_path / "outside.py").write_text("outside = 1\n", encoding="utf-8")
    runner = CliRunner()

    opened = runner.invoke(app, ["session", "open", str(scoped), "--json"])
    assert opened.exit_code == 0, opened.output
    opened_payload = json.loads(opened.stdout)
    assert opened_payload["file_count"] == 1

    refreshed = runner.invoke(
        app, ["session", "refresh", opened_payload["session_id"], str(scoped), "--json"]
    )
    assert refreshed.exit_code == 0, refreshed.output
    assert json.loads(refreshed.stdout)["file_count"] == 1
