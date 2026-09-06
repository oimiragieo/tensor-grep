from __future__ import annotations

import json
from pathlib import Path

from typer.testing import CliRunner

from tensor_grep.cli.main import app

runner = CliRunner()


def test_session_prepare_and_resume_contract(tmp_path: Path) -> None:
    # 1. Open a session
    f1 = tmp_path / "calc.py"
    f1.write_text("def add(a, b):\n    return a + b\n", encoding="utf-8")

    open_res = runner.invoke(app, ["session", "open", str(tmp_path), "--json"])
    assert open_res.exit_code == 0
    session_data = json.loads(open_res.stdout)
    session_id = session_data["session_id"]

    # 2. tg session prepare S123 "query" --json
    prep_res = runner.invoke(
        app, ["session", "prepare", session_id, "add numbers", str(tmp_path), "--json"]
    )
    assert prep_res.exit_code == 0
    prep_data = json.loads(prep_res.stdout)
    assert prep_data["session_id"] == session_id
    assert "primary_target" in prep_data

    # 3. tg session resume S123 --json
    resume_res = runner.invoke(app, ["session", "resume", session_id, str(tmp_path), "--json"])
    assert resume_res.exit_code == 0
    resume_data = json.loads(resume_res.stdout)
    assert resume_data["session_id"] == session_id
    assert resume_data["resumed"] is True
    assert "last_prepare" in resume_data


def test_session_refresh_preserves_last_prepare(tmp_path: Path) -> None:
    """Codex Sol delta-verification audit HIGH finding, confirmed by direct read:
    refresh_session (session_store.py) builds a fresh payload dict from scratch and never
    carries `last_prepare` forward from the existing payload, so a `tg session refresh`
    unconditionally discarded a prior `tg session prepare` decision -- not a race, a guaranteed
    data-loss bug on every refresh. session_resume still claims `resumed: true` afterward with
    last_prepare silently gone."""
    f1 = tmp_path / "calc.py"
    f1.write_text("def add(a, b):\n    return a + b\n", encoding="utf-8")

    open_res = runner.invoke(app, ["session", "open", str(tmp_path), "--json"])
    session_id = json.loads(open_res.stdout)["session_id"]

    prep_res = runner.invoke(
        app, ["session", "prepare", session_id, "add numbers", str(tmp_path), "--json"]
    )
    assert prep_res.exit_code == 0

    # A file change that makes the session refresh-worthy.
    f2 = tmp_path / "extra.py"
    f2.write_text("def noop():\n    pass\n", encoding="utf-8")

    refresh_res = runner.invoke(app, ["session", "refresh", session_id, str(tmp_path), "--json"])
    assert refresh_res.exit_code == 0

    resume_res = runner.invoke(app, ["session", "resume", session_id, str(tmp_path), "--json"])
    assert resume_res.exit_code == 0
    resume_data = json.loads(resume_res.stdout)
    assert resume_data["last_prepare"] is not None, (
        "tg session refresh must not discard a prior tg session prepare decision"
    )
