from __future__ import annotations

import json
from pathlib import Path

from typer.testing import CliRunner

from tensor_grep.cli.main import app


def test_sql_json_no_such_column_includes_available_tables(tmp_path: Path) -> None:
    (tmp_path / "sample.py").write_text("def hello() -> int:\n    return 1\n", encoding="utf-8")
    runner = CliRunner()

    res = runner.invoke(app, ["sql", "SELECT name FROM symbols", str(tmp_path), "--json"])
    assert res.exit_code == 1, res.output

    payload = json.loads(res.stdout)
    assert "no such column" in payload["error"].lower()
    assert payload["incomplete_reason"] == "query_error"
    assert payload["available_tables"] == {
        "imports": ["file", "module", "line", "resolved_file"],
        "symbols": ["file", "symbol", "kind", "line", "end_line", "language", "signature"],
    }


def test_sql_json_no_such_table_includes_available_tables(tmp_path: Path) -> None:
    (tmp_path / "sample.py").write_text("def hello() -> int:\n    return 1\n", encoding="utf-8")
    runner = CliRunner()

    res = runner.invoke(app, ["sql", "SELECT * FROM functions", str(tmp_path), "--json"])
    assert res.exit_code == 1, res.output

    payload = json.loads(res.stdout)
    assert "no such table" in payload["error"].lower()
    assert "symbols" in payload["available_tables"]
    assert "imports" in payload["available_tables"]
    assert payload["available_tables"]["symbols"] == [
        "file",
        "symbol",
        "kind",
        "line",
        "end_line",
        "language",
        "signature",
    ]
