from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest
from typer.testing import CliRunner

from tensor_grep.cli.main import app
from tensor_grep.cli.sql_query import (
    MAX_SQL_QUERY_FILE_BYTES,
    _has_multiple_sql_statements,
    read_query_file,
)


@pytest.mark.parametrize("data", [b"SELECT 1 AS answer", b"\xef\xbb\xbfSELECT 1 AS answer"])
def test_query_file_default_path_utf8_and_bom(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, data: bytes
) -> None:
    query = tmp_path / "query.sql"
    query.write_bytes(data)
    monkeypatch.chdir(tmp_path)
    result = CliRunner().invoke(app, ["sql", "--query-file", str(query), "--json"])
    assert result.exit_code == 0, result.output
    assert json.loads(result.stdout)["rows"] == [{"answer": 1}]


@pytest.mark.parametrize(
    "data, message",
    [(b"\xff", "UTF-8"), (b" \r\n", "empty"), (b"x" * (MAX_SQL_QUERY_FILE_BYTES + 1), "1 MiB")],
    ids=["encoding", "empty", "oversize"],
)
def test_query_file_invalid_inputs_refused_before_scan(
    tmp_path: Path, data: bytes, message: str
) -> None:
    query = tmp_path / "query.sql"
    query.write_bytes(data)
    result = CliRunner().invoke(
        app, ["sql", str(tmp_path / "nonexistent-repo"), "--query-file", str(query)]
    )
    assert result.exit_code == 2
    assert message in result.output
    assert "Path not found" not in result.output


def test_query_file_size_boundary(tmp_path: Path) -> None:
    query = tmp_path / "query.sql"
    query.write_bytes(b"SELECT 1" + b" " * (MAX_SQL_QUERY_FILE_BYTES - 8))
    assert len(read_query_file(query)) == MAX_SQL_QUERY_FILE_BYTES
    result = CliRunner().invoke(app, ["sql", str(tmp_path), "--query-file", str(query), "--json"])
    assert result.exit_code == 0, result.output


@pytest.mark.parametrize(
    "inline",
    [
        ["SELECT 1"],
        ["CREATE TABLE x(y)"],
        ["-- comment\nDELETE FROM symbols"],
        [".", "SELECT 1"],
        ["SELECT 1", "."],
    ],
)
def test_conflicting_inline_refused_before_open(tmp_path: Path, inline: list[str]) -> None:
    result = CliRunner().invoke(
        app, ["sql", "--query-file", str(tmp_path / "missing.sql"), "--", *inline]
    )
    assert result.exit_code == 2
    assert "cannot be combined" in result.output
    assert "unreadable" not in result.output


def test_query_file_missing_and_write_sandbox(tmp_path: Path) -> None:
    result = CliRunner().invoke(app, ["sql", "--query-file", str(tmp_path / "missing.sql")])
    assert result.exit_code == 2
    assert "unreadable" in result.output
    query = tmp_path / "query.sql"
    query.write_text("CREATE TABLE forbidden(value)", encoding="utf-8")
    result = CliRunner().invoke(app, ["sql", str(tmp_path), "--query-file", str(query), "--json"])
    assert result.exit_code == 1
    assert "not authorized" in result.output


@pytest.mark.parametrize("reverse", [False, True])
def test_inline_positional_forms_preserved(tmp_path: Path, reverse: bool) -> None:
    args = [str(tmp_path), "SELECT 7 AS answer"]
    if reverse:
        args.reverse()
    result = CliRunner().invoke(app, ["sql", *args, "--json"])
    assert result.exit_code == 0, result.output
    assert json.loads(result.stdout)["rows"] == [{"answer": 7}]


def test_query_file_directory_refused_before_scan(tmp_path: Path) -> None:
    result = CliRunner().invoke(
        app, ["sql", str(tmp_path / "missing-repo"), "--query-file", str(tmp_path)]
    )
    assert result.exit_code == 2
    assert "non-regular" in result.output
    assert "Path not found" not in result.output


@pytest.mark.skipif(not hasattr(os, "mkfifo"), reason="POSIX special-file control")
def test_query_file_fifo_refused_before_open(tmp_path: Path) -> None:
    query = tmp_path / "query.sql"
    os.mkfifo(query)  # type: ignore[attr-defined]
    with pytest.raises(ValueError, match="non-regular"):
        read_query_file(query)


@pytest.mark.parametrize(
    "second_statement", [False, True], ids=["single-statement", "multiple-statements"]
)
def test_one_mib_semicolon_heavy_query_file_is_bounded(
    tmp_path: Path, second_statement: bool
) -> None:
    prefix = "SELECT 1 AS answer;" if second_statement else ""
    suffix = "SELECT 2" if second_statement else ""
    query = tmp_path / "query.sql"
    query.write_text(
        prefix + ";" * (MAX_SQL_QUERY_FILE_BYTES - len(prefix) - len(suffix)) + suffix,
        encoding="utf-8",
    )
    # A generous external deadline bounds the actual CLI path, including file reading,
    # validation and SQLite. The old suffix rescans never finish this positive control.
    command = [
        sys.executable,
        "-c",
        "import json,sys; from typer.testing import CliRunner; from tensor_grep.cli.main import app; result=CliRunner().invoke(app,['sql',sys.argv[1],'--query-file',sys.argv[2],'--json']); payload=json.loads(result.stdout); print(json.dumps({'exit':result.exit_code,'rows':payload.get('rows'),'error':payload.get('error')}))",
        str(tmp_path),
        str(query),
    ]
    result = subprocess.run(
        command, capture_output=True, text=True, encoding="utf-8", timeout=60, check=False
    )
    assert result.returncode == 0, result.stderr[-1000:]
    payload = json.loads(result.stdout)
    if second_statement:
        assert payload["exit"] == 1
        assert "Multiple statements are not permitted" in payload["error"]
    else:
        assert payload["exit"] == 0, payload
        assert payload["rows"] == []


@pytest.mark.parametrize(
    "query, multiple",
    [
        (";;; SELECT 1;;; -- trailing ;\n/* ; */", False),
        ("SELECT 'a;''b', \"semi;colon\", `tick``;name`, [bracket;name]; -- ;\n", False),
        ("SELECT 1; /* comment ; */ SELECT 2", True),
        ("SELECT 1; -- ;\n 'other;value'", True),
    ],
)
def test_statement_lexer_preserves_quotes_comments_and_empty_statements(
    query: str, multiple: bool
) -> None:
    assert _has_multiple_sql_statements(query) is multiple


def test_statement_validation_uses_linear_character_accesses() -> None:
    # Count inspected/copied characters rather than making CPU/scheduler assumptions.
    # Returning the same subclass for slices also catches the old suffix-rescan loop.
    length = 16 * 1024
    accesses = [0]

    class BoundedAccessQuery(str):
        def __getitem__(self, key: int | slice) -> str:
            accesses[0] += len(range(*key.indices(len(self)))) if isinstance(key, slice) else 1
            assert accesses[0] <= 8 * length, "SQL validator exceeded a linear character budget"
            value = super().__getitem__(key)
            return BoundedAccessQuery(value) if isinstance(key, slice) else value

    assert not _has_multiple_sql_statements(BoundedAccessQuery(";" * length))
