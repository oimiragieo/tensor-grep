from __future__ import annotations

import hashlib
import json
import sqlite3
import subprocess
import sys
from pathlib import Path

import pytest
from typer.testing import CliRunner

from tensor_grep.cli.coverage import _ast_cache_entries, check_coverage
from tensor_grep.cli.main import app


def test_coverage_cold_root_reports_files_as_unindexed(tmp_path: Path) -> None:
    project = tmp_path / "project"
    project.mkdir()
    (project / "main.py").write_text("def run():\n    return 1\n", encoding="utf-8")

    result = CliRunner().invoke(app, ["coverage", str(project), "--json"])

    assert result.exit_code == 2, result.output
    payload = json.loads(result.stdout)
    assert payload["files_total"] == 1
    assert payload["unindexed"] == ["main.py"]
    assert payload["complete"] is False


def test_coverage_added_file_is_stale_and_unindexed(tmp_path: Path) -> None:
    project = tmp_path / "project"
    project.mkdir()
    (project / "main.py").write_text("value = 1\n", encoding="utf-8")
    runner = CliRunner()
    opened = runner.invoke(app, ["session", "open", str(project), "--json"])
    assert opened.exit_code == 0, opened.output
    (project / "added.py").write_text("value = 2\n", encoding="utf-8")

    result = runner.invoke(app, ["coverage", str(project), "--json"])

    assert result.exit_code == 2, result.output
    payload = json.loads(result.stdout)
    assert {"path": "added.py", "reason": "added"} in payload["stale"]
    assert "added.py" in payload["unindexed"]


def test_coverage_exact_repo_map_limit_does_not_report_a_hit(tmp_path: Path, monkeypatch) -> None:
    from tensor_grep.cli import repo_map

    project = tmp_path / "project"
    project.mkdir()
    for name in ("first.py", "second.py"):
        (project / name).write_text("value = 1\n", encoding="utf-8")
    monkeypatch.setattr(repo_map, "DEFAULT_AGENT_REPO_MAP_LIMIT", 2)

    result = CliRunner().invoke(app, ["coverage", str(project), "--json"])

    assert result.exit_code == 2, result.output
    payload = json.loads(result.stdout)
    assert payload["files_total"] == 2
    assert payload["caps"] == []


def test_coverage_reports_repo_map_limit_hit(tmp_path: Path, monkeypatch) -> None:
    from tensor_grep.cli import repo_map

    project = tmp_path / "project"
    project.mkdir()
    for name in ("first.py", "second.py", "third.py"):
        (project / name).write_text("value = 1\n", encoding="utf-8")
    monkeypatch.setattr(repo_map, "DEFAULT_AGENT_REPO_MAP_LIMIT", 2)

    result = CliRunner().invoke(app, ["coverage", str(project), "--json"])

    assert result.exit_code == 2, result.output
    payload = json.loads(result.stdout)
    assert payload["files_total"] == 2
    assert payload["caps"] == ["max_repo_files"]
    assert payload["complete"] is False


def _tree_bytes(root: Path) -> dict[str, str]:
    return {
        item.relative_to(root).as_posix(): hashlib.sha256(item.read_bytes()).hexdigest()
        for item in root.rglob("*")
        if item.is_file()
    }


def test_coverage_after_session_is_complete_and_read_only(tmp_path: Path) -> None:
    project = tmp_path / "project"
    project.mkdir()
    (project / "main.py").write_text("def run():\n    return 1\n", encoding="utf-8")
    runner = CliRunner()
    opened = runner.invoke(app, ["session", "open", str(project), "--json"])
    assert opened.exit_code == 0, opened.output
    before = _tree_bytes(project)

    result = runner.invoke(app, ["coverage", str(project), "--json"])

    assert result.exit_code == 0, result.output
    payload = json.loads(result.stdout)
    assert payload["complete"] is True
    assert payload["files_total"] == 1
    assert payload["stale"] == []
    assert payload["unindexed"] == []
    assert payload["unparsed"] == []
    assert _tree_bytes(project) == before


def test_coverage_reports_modified_and_unparseable_files(tmp_path: Path) -> None:
    project = tmp_path / "project"
    project.mkdir()
    modified = project / "modified.py"
    malformed = project / "malformed.py"
    modified.write_text("def run():\n    return 1\n", encoding="utf-8")
    malformed.write_text("def broken():\n    return 1\n", encoding="utf-8")
    opened = CliRunner().invoke(app, ["session", "open", str(project), "--json"])
    assert opened.exit_code == 0, opened.output
    modified.write_text("def run():\n    return 2\n", encoding="utf-8")
    malformed.write_text("def broken(:\n    return 1\n", encoding="utf-8")

    result = CliRunner().invoke(app, ["coverage", str(project), "--json"])

    assert result.exit_code == 2, result.output
    payload = json.loads(result.stdout)
    assert {entry["path"] for entry in payload["stale"] if entry["reason"] == "modified"} == {
        "modified.py",
        "malformed.py",
    }
    assert {entry["path"] for entry in payload["unparsed"]} == {"malformed.py"}
    assert payload["complete"] is False


def test_coverage_merges_older_session_file_receipts(tmp_path: Path) -> None:
    project = tmp_path / "project"
    project.mkdir()
    (project / "first.py").write_text("value = 1\n", encoding="utf-8")
    (project / "second.py").write_text("value = 2\n", encoding="utf-8")
    runner = CliRunner()
    assert runner.invoke(app, ["session", "open", str(project), "--json"]).exit_code == 0
    assert (
        runner.invoke(
            app, ["session", "open", str(project), "--max-repo-files", "1", "--json"]
        ).exit_code
        == 0
    )

    payload = check_coverage(project)

    assert payload["unindexed"] == []
    assert payload["stale"] == []


def test_coverage_capped_files_are_not_deleted(tmp_path: Path, monkeypatch) -> None:
    from tensor_grep.cli import repo_map

    project = tmp_path / "project"
    project.mkdir()
    for name in ("first.py", "second.py", "third.py"):
        (project / name).write_text("value = 1\n", encoding="utf-8")
    assert CliRunner().invoke(app, ["session", "open", str(project), "--json"]).exit_code == 0
    monkeypatch.setattr(repo_map, "DEFAULT_AGENT_REPO_MAP_LIMIT", 1)

    payload = check_coverage(project)

    assert payload["caps"] == ["max_repo_files"]
    assert not any(entry["reason"] == "deleted" for entry in payload["stale"])


def test_coverage_oversize_change_is_modified(tmp_path: Path, monkeypatch) -> None:
    from tensor_grep.cli import repo_map

    project = tmp_path / "project"
    project.mkdir()
    source = project / "main.py"
    source.write_text("value = 1\n", encoding="utf-8")
    assert CliRunner().invoke(app, ["session", "open", str(project), "--json"]).exit_code == 0
    monkeypatch.setattr(repo_map, "_max_parse_bytes", lambda: 16)
    source.write_text("value = 2\n" * 5, encoding="utf-8")

    payload = check_coverage(project)

    assert {"path": "main.py", "reason": "modified"} in payload["stale"]
    assert {"path": "main.py", "reason": "oversize"} in payload["unparsed"]


def test_coverage_js_parser_errors_are_unparsed(tmp_path: Path) -> None:
    from tensor_grep.cli import lang_registry

    project = tmp_path / "project"
    project.mkdir()
    source = project / "broken.js"
    source.write_text("function broken( {\n", encoding="utf-8")
    spec = lang_registry.spec_for_path(source)
    assert spec is not None and spec.parser_for_path is not None
    parser = spec.parser_for_path(source)
    assert parser is not None
    assert parser.parse(source.read_bytes()).root_node.has_error
    assert CliRunner().invoke(app, ["session", "open", str(project), "--json"]).exit_code == 0

    payload = check_coverage(project)

    assert {"path": "broken.js", "reason": "parse_error"} in payload["unparsed"]
    assert payload["complete"] is False


def test_coverage_rejects_cache_rows_without_ast_products(tmp_path: Path, monkeypatch) -> None:
    from tensor_grep.cli import session_daemon_trust, symbols_cache

    project = tmp_path / "project"
    project.mkdir()
    source = project / "main.py"
    source.write_text("value = 1\n", encoding="utf-8")
    cache = project / ".tg_cache" / "symbols_v1" / "metadata.sqlite3"
    cache.parent.mkdir(parents=True)
    secret = b"coverage-test-authentication-key"
    monkeypatch.setattr(session_daemon_trust, "_read_user_secret", lambda _path: secret)
    product = json.dumps({"imports": [], "symbols": []})
    receipt = symbols_cache._receipt(secret, "valid", product)
    digest = hashlib.sha256(source.read_bytes()).hexdigest()
    with sqlite3.connect(cache) as connection:
        connection.executescript("""
            CREATE TABLE generations(id INTEGER PRIMARY KEY, scope TEXT, merkle TEXT,
                complete INTEGER, created REAL);
            CREATE TABLE files(scope TEXT, path TEXT, content TEXT, entry_key TEXT,
                generation INTEGER, PRIMARY KEY(scope,path));
            CREATE TABLE entries(key TEXT PRIMARY KEY, payload TEXT, receipt TEXT,
                generation INTEGER);
            PRAGMA user_version=1;
        """)
        connection.execute("INSERT INTO generations VALUES(1, ?, '', 1, 1)", (str(project),))
        connection.execute(
            "INSERT INTO files VALUES(?, ?, ?, 'valid', 1)",
            (str(project), str(source), digest),
        )
        connection.execute("INSERT INTO entries VALUES('valid', ?, ?, 1)", (product, receipt))

    assert _ast_cache_entries(project)[0] == {str(source): digest}
    with sqlite3.connect(cache) as connection:
        connection.execute("DELETE FROM entries")

    payload = check_coverage(project)

    assert payload["complete"] is False
    assert payload["unindexed"] == ["main.py"]


def test_coverage_unknown_option_exits_one(tmp_path: Path) -> None:
    result = CliRunner().invoke(app, ["coverage", str(tmp_path), "--unknown"])

    assert result.exit_code == 1


def test_coverage_denied_directory_is_incomplete(tmp_path: Path, monkeypatch) -> None:
    from tensor_grep.cli import repo_map

    project = tmp_path / "project"
    denied = project / "denied"
    denied.mkdir(parents=True)
    (denied / "hidden.py").write_text("value = 1\n", encoding="utf-8")
    original = repo_map.os.scandir

    def denied_scandir(path):
        if Path(path) == denied:
            raise PermissionError(13, "Access denied", str(denied))
        return original(path)

    monkeypatch.setattr(repo_map.os, "scandir", denied_scandir)

    payload = check_coverage(project)

    assert payload["complete"] is False
    assert "unreadable_paths" in payload["caps"]


def test_coverage_recursive_sqlite_view_is_bounded(tmp_path: Path) -> None:
    project = tmp_path / "project"
    project.mkdir()
    cache = project / ".tg_cache" / "symbols_v1" / "metadata.sqlite3"
    cache.parent.mkdir(parents=True)
    scope_literal = str(project).replace("'", "''")
    with sqlite3.connect(cache) as connection:
        connection.executescript(f"""
            CREATE VIEW generations AS WITH RECURSIVE x(id) AS
                (SELECT 1 UNION ALL SELECT id + 1 FROM x)
                SELECT id, id AS created, '{scope_literal}' AS scope FROM x;
            CREATE TABLE files(scope TEXT, path TEXT, content TEXT, generation INTEGER);
        """)

    try:
        result = subprocess.run(
            [
                sys.executable,
                "-c",
                "from pathlib import Path; from tensor_grep.cli.coverage "
                "import _ast_cache_entries; _ast_cache_entries(Path(__import__('sys').argv[1]))",
                str(project),
            ],
            capture_output=True,
            text=True,
            timeout=5,
            check=False,
        )
    except subprocess.TimeoutExpired:
        pytest.fail("coverage query exceeded five seconds on a recursive SQLite view")

    assert result.returncode != 0
    assert "DatabaseError" in result.stderr or "OperationalError" in result.stderr
