from __future__ import annotations

import json
from pathlib import Path

import pytest
from typer.testing import CliRunner

from tensor_grep.cli.main import app


@pytest.mark.parametrize(
    "query, rows",
    [
        ("SELECT x'41' AS payload", [{"payload": "b'A'"}]),
        (
            "SELECT 7 AS answer, 'ok' AS label, NULL AS absent",
            [{"answer": 7, "label": "ok", "absent": None}],
        ),
        ("SELECT 1 AS answer WHERE 0", []),
    ],
    ids=["blob", "scalar-string-null", "empty"],
)
def test_sql_json_values(tmp_path: Path, query: str, rows: list[dict[str, object]]) -> None:
    result = CliRunner().invoke(app, ["sql", str(tmp_path), query, "--json"])

    assert result.exit_code == 0, repr(result.exception)
    payload = json.loads(result.stdout)
    assert payload["rows"] == rows
    assert payload["count"] == len(rows)
    assert payload["truncated"] is False
    assert payload["result_incomplete"] is False
    assert payload["partial"] is False


def test_sql_blob_text_control(tmp_path: Path) -> None:
    result = CliRunner().invoke(app, ["sql", str(tmp_path), "SELECT x'41' AS payload"])

    assert result.exit_code == 0, repr(result.exception)
    assert "b'A'" in result.stdout
    assert "(1 row)" in result.stdout


@pytest.mark.parametrize(
    "query, error",
    [
        ("SELECT FROM", "syntax error"),
        ("SELECT x'41' AS payload, 1 AS payload", "duplicate column"),
    ],
    ids=["syntax", "duplicate-column"],
)
def test_sql_json_error_controls(tmp_path: Path, query: str, error: str) -> None:
    result = CliRunner().invoke(app, ["sql", str(tmp_path), query, "--json"])

    assert result.exit_code == 1
    payload = json.loads(result.stdout)
    assert error in payload["error"]
    assert payload["rows"] == []
    assert payload["count"] == 0
    assert payload["result_incomplete"] is True
    assert payload["incomplete_reason"] == "query_error"
