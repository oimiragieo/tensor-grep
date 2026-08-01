"""Regression: an invalid --ltl query must exit 2 with a one-line clean error, never a traceback.

Pre-fix baseline: CPUBackend._compile_ltl's ValueError escapes the search command uncaught
(main.py's per-file loop handles only BackendExecutionError / invalid-regex), so the CLI
prints a raw Python traceback and exits 1. Convention control: every other expected CLI
error (path_not_found, invalid_regex, configuration_error) routes through
_exit_search_error and exits 2 with a single `Error: ...` line.
"""

from __future__ import annotations

import json as jsonlib
from pathlib import Path

from typer.testing import CliRunner

from tensor_grep.cli.main import app

runner = CliRunner()


def _target(tmp_path: Path) -> Path:
    target = tmp_path / "sample.py"
    target.write_text("def alpha():\n    return 1\n", encoding="utf-8")
    return target


def test_invalid_ltl_query_exits_2_with_one_line_clean_error(tmp_path: Path) -> None:
    # RED ARM 1. Pre-fix: ValueError traceback, exit 1.
    result = runner.invoke(app, ["search", "def ", "--ltl", str(_target(tmp_path))])
    assert result.exit_code == 2
    lines = [line for line in result.output.splitlines() if line.strip()]
    # "one-line" is ASSERTED, not narrated: the presenter emits exactly one stderr line
    # (typer.echo(f"Error: ...", err=True) in _exit_search_error) and nothing else precedes
    # the exit on this path.
    assert len(lines) == 1
    assert lines[0].startswith("Error:")
    assert "A -> eventually B" in lines[0]
    assert "Traceback" not in result.output


def test_invalid_ltl_query_json_mode_emits_error_envelope(tmp_path: Path) -> None:
    # RED ARM 2. Pre-fix: traceback, exit 1, no envelope.
    result = runner.invoke(app, ["search", "def ", "--ltl", "--json", str(_target(tmp_path))])
    assert result.exit_code == 2
    payload = jsonlib.loads(result.output.strip())
    # Full _search_error_payload presenter shape (version, schema_version, ok, error, detail)
    # -- parsed, not substring-matched.
    assert payload["ok"] is False
    assert payload["error"] == "invalid_ltl_query"
    assert "A -> eventually B" in payload["detail"]
    assert "version" in payload and "schema_version" in payload


def test_valid_ltl_query_still_works(tmp_path: Path) -> None:
    # GREEN control (not a red arm -- it survives the revert by design;
    # arms 1-2 above are what must go RED on pre-fix code).
    result = runner.invoke(
        app, ["search", "def -> eventually return", "--ltl", str(_target(tmp_path))]
    )
    assert result.exit_code == 0


def test_ltl_with_invalid_subexpression_regex_stays_on_invalid_regex_convention(
    tmp_path: Path,
) -> None:
    # REGRESSION GUARD, baseline GREEN -- NOT a red arm (round-1 MF5). Pre-fix, the
    # re.error from compiling "(" already routes through _is_invalid_regex_error ->
    # _exit_invalid_regex and exits 2. This test pins that the NEW boundary preserves
    # that convention (post-fix the same observable is produced at the boundary instead
    # of inside the per-file loop). It passes in both arms BY DESIGN and is never cited
    # as a red receipt.
    result = runner.invoke(app, ["search", "( -> eventually X", "--ltl", str(_target(tmp_path))])
    assert result.exit_code == 2
    assert "Traceback" not in result.output
