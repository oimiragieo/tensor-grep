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


def test_session_refresh_unchanged_content_preserves_current_freshness(tmp_path: Path) -> None:
    """AGT-02 Task 02 versioned freshness: a prepare decision made against an unchanged
    snapshot must survive a refresh (that detects no changeset) still labeled `current`,
    with `decision_generation` equal to the session's `current_generation`."""
    f1 = tmp_path / "calc.py"
    f1.write_text("def add(a, b):\n    return a + b\n", encoding="utf-8")

    open_res = runner.invoke(app, ["session", "open", str(tmp_path), "--json"])
    session_id = json.loads(open_res.stdout)["session_id"]

    prep_res = runner.invoke(
        app, ["session", "prepare", session_id, "add numbers", str(tmp_path), "--json"]
    )
    assert prep_res.exit_code == 0

    # No content change: refresh should see an empty changeset.
    refresh_res = runner.invoke(app, ["session", "refresh", session_id, str(tmp_path), "--json"])
    assert refresh_res.exit_code == 0

    resume_res = runner.invoke(app, ["session", "resume", session_id, str(tmp_path), "--json"])
    assert resume_res.exit_code == 0
    resume_data = json.loads(resume_res.stdout)
    last_prepare = resume_data["last_prepare"]
    assert last_prepare is not None
    assert last_prepare.get("decision_freshness") == "current", (
        f"unchanged refresh must preserve current freshness, got {last_prepare!r}"
    )
    assert last_prepare.get("decision_generation") is not None
    assert last_prepare.get("decision_generation") == resume_data.get("current_generation")


def test_session_refresh_changed_content_retains_history_but_marks_historical(
    tmp_path: Path,
) -> None:
    """AGT-02 Task 02: deleting the file the selected primary_target lives in, then refreshing,
    must retain the prior decision (never silently discarded) but mark it `historical` so
    resume does not advertise a decision about a target that no longer exists as `current`."""
    f1 = tmp_path / "calc.py"
    f1.write_text("def add(a, b):\n    return a + b\n", encoding="utf-8")

    open_res = runner.invoke(app, ["session", "open", str(tmp_path), "--json"])
    session_id = json.loads(open_res.stdout)["session_id"]

    prep_res = runner.invoke(
        app, ["session", "prepare", session_id, "add numbers", str(tmp_path), "--json"]
    )
    assert prep_res.exit_code == 0
    prep_data = json.loads(prep_res.stdout)
    assert prep_data.get("primary_target", {}).get("file")

    # Delete the file the selected target lives in -- the decision's subject no longer exists.
    f1.unlink()

    refresh_res = runner.invoke(app, ["session", "refresh", session_id, str(tmp_path), "--json"])
    assert refresh_res.exit_code == 0

    resume_res = runner.invoke(app, ["session", "resume", session_id, str(tmp_path), "--json"])
    assert resume_res.exit_code == 0
    resume_data = json.loads(resume_res.stdout)
    last_prepare = resume_data["last_prepare"]
    assert last_prepare is not None, "history must be retained, never discarded"
    assert last_prepare.get("query") == "add numbers"
    assert last_prepare.get("decision_freshness") == "historical", (
        "resume must not advertise a decision about a deleted target as current: "
        f"got {last_prepare!r}"
    )


def test_session_resume_legacy_payload_without_generation_is_unknown(tmp_path: Path) -> None:
    """AGT-02 Task 02: identity must never be inferred from wall-clock timestamps alone. A
    pre-existing (legacy) `last_prepare` snapshot carrying no `decision_generation` must be
    reported `unknown`, never silently upgraded to `current`."""
    from tensor_grep.cli import session_store

    f1 = tmp_path / "calc.py"
    f1.write_text("def add(a, b):\n    return a + b\n", encoding="utf-8")

    open_res = runner.invoke(app, ["session", "open", str(tmp_path), "--json"])
    session_id = json.loads(open_res.stdout)["session_id"]

    prep_res = runner.invoke(
        app, ["session", "prepare", session_id, "add numbers", str(tmp_path), "--json"]
    )
    assert prep_res.exit_code == 0

    # Simulate a legacy payload written before this feature existed: strip identity metadata
    # directly from the on-disk payload.
    payload = session_store.get_session(session_id, str(tmp_path))
    payload.pop("current_generation", None)
    last_prepare = dict(payload["last_prepare"])
    last_prepare.pop("decision_generation", None)
    last_prepare.pop("current_generation", None)
    last_prepare.pop("decision_freshness", None)
    payload["last_prepare"] = last_prepare
    session_path = session_store._session_payload_path(
        session_store._resolve_root(Path(str(tmp_path))), session_id
    )
    session_store._write_json_atomic(session_path, payload)

    resume_res = runner.invoke(app, ["session", "resume", session_id, str(tmp_path), "--json"])
    assert resume_res.exit_code == 0
    resume_data = json.loads(resume_res.stdout)
    assert resume_data["last_prepare"].get("decision_freshness") == "unknown", (
        f"a legacy snapshot lacking identity must be unknown, got {resume_data['last_prepare']!r}"
    )
