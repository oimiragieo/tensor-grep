"""B-03: `session open` / `checkpoint create` on a missing PATH exit 2 (error), never 1 (no match)."""

from __future__ import annotations

from pathlib import Path

from typer.testing import CliRunner

from tensor_grep.cli.main import app

runner = CliRunner()


def _tree(root: Path) -> list[str]:
    return sorted(str(p.relative_to(root)) for p in root.rglob("*"))


def test_session_open_missing_path_exits_2_and_creates_no_state(tmp_path: Path) -> None:
    # CONTROL: an existing directory opens fine.
    ok = tmp_path / "ok"
    ok.mkdir()
    (ok / "a.py").write_text("def a():\n    return 1\n", encoding="utf-8")
    assert runner.invoke(app, ["session", "open", str(ok)]).exit_code == 0

    before = _tree(tmp_path)
    missing = tmp_path / "missing"
    result = runner.invoke(app, ["session", "open", str(missing)])
    assert result.exit_code == 2, result.output
    assert "Path not found" in result.output
    assert not missing.exists()
    assert _tree(tmp_path) == before, "a refused PATH must not write any state"


def test_checkpoint_create_missing_path_exits_2_and_creates_no_state(tmp_path: Path) -> None:
    # CONTROL: an existing directory checkpoints fine.
    ok = tmp_path / "ok"
    ok.mkdir()
    (ok / "a.txt").write_text("hi\n", encoding="utf-8")
    assert runner.invoke(app, ["checkpoint", "create", str(ok)]).exit_code == 0

    before = _tree(tmp_path)
    missing = tmp_path / "missing"
    result = runner.invoke(app, ["checkpoint", "create", str(missing)])
    assert result.exit_code == 2, result.output
    assert "Path not found" in result.output
    assert not missing.exists()
    assert _tree(tmp_path) == before, "a refused PATH must not write any state"
