"""`tg sql`'s `imports` table (SLICE 4, resilient-cooking-cupcake plan).

Adds `imports(file, module, line, resolved_file NULL)` to the in-memory SQLite sandbox that
`tg sql` already exposes for `symbols`. Rows come from `repo_map._imports_with_lines_for_path`
(line-aware raw extraction, 10 supported languages) resolved via
`repo_map._resolve_raw_import_entry` (module-string -> target-file, honestly unresolved when the
resolver has no manifest to resolve against). The shared repo-map payload gains no new keys
(Sol R3 finding 2): `sql_query.py` owns this pass itself, over `repo_map["files"]`, under the
SAME `--scan-deadline` the symbols pass already respects (Sol R4/R5).
"""

from __future__ import annotations

import json
import time
from pathlib import Path

import pytest
from typer.testing import CliRunner

from tensor_grep.cli.main import app

runner = CliRunner()


def _write(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def test_sql_imports_join_symbols(tmp_path: Path) -> None:
    """A JOIN of symbols x imports on file finds the importer's own defined symbol."""
    proj = tmp_path / "proj"
    _write(
        proj / "pkg" / "__init__.py",
        "",
    )
    _write(proj / "pkg" / "util.py", "def helper():\n    pass\n")
    _write(
        proj / "pkg" / "main.py",
        "from pkg import util\n\n\ndef entrypoint():\n    return util.helper()\n",
    )

    result = runner.invoke(
        app,
        [
            "sql",
            str(proj),
            "SELECT s.symbol, i.module FROM symbols s JOIN imports i ON s.file = i.file "
            "WHERE s.symbol = 'entrypoint'",
            "--json",
        ],
    )
    assert result.exit_code == 0, result.output
    payload = json.loads(result.stdout)
    assert payload["result_incomplete"] is False
    assert payload["count"] == 1
    assert payload["rows"][0]["symbol"] == "entrypoint"
    assert payload["rows"][0]["module"] == "pkg"


def test_sql_imports_unresolved_row_kept_with_null(tmp_path: Path) -> None:
    """An import of a genuinely external module is kept, with `resolved_file` NULL."""
    proj = tmp_path / "proj"
    _write(proj / "main.py", "import totally_external_package_xyz\n")

    result = runner.invoke(
        app,
        [
            "sql",
            str(proj),
            "SELECT module, resolved_file FROM imports WHERE module = "
            "'totally_external_package_xyz'",
            "--json",
        ],
    )
    assert result.exit_code == 0, result.output
    payload = json.loads(result.stdout)
    assert payload["count"] == 1
    assert payload["rows"][0]["module"] == "totally_external_package_xyz"
    assert payload["rows"][0]["resolved_file"] is None


def test_sql_imports_no_imports_negative_control(tmp_path: Path) -> None:
    """A repo with source files but zero import statements yields zero import rows, complete."""
    proj = tmp_path / "proj"
    _write(proj / "lonely.py", "def standalone():\n    return 1\n")

    result = runner.invoke(
        app,
        ["sql", str(proj), "SELECT * FROM imports", "--json"],
    )
    assert result.exit_code == 0, result.output
    payload = json.loads(result.stdout)
    assert payload["count"] == 0
    assert payload["result_incomplete"] is False


def test_sql_imports_unsupported_language_file_flagged(tmp_path: Path) -> None:
    """A file with no registered language spec (e.g. Kotlin) is honestly disclosed, not silently
    treated as "this file has zero imports"."""
    proj = tmp_path / "proj"
    _write(proj / "app.py", "def f():\n    pass\n")
    _write(proj / "Main.kt", 'fun main() { println("hi") }\n')

    json_result = runner.invoke(app, ["sql", str(proj), "SELECT * FROM imports", "--json"])
    assert json_result.exit_code == 2, json_result.output
    payload = json.loads(json_result.stdout)
    assert payload["result_incomplete"] is True
    assert payload.get("scan_incomplete") is True

    text_result = runner.invoke(app, ["sql", str(proj), "SELECT * FROM imports"])
    assert text_result.exit_code == 2
    assert "INCOMPLETE" in text_result.stdout


def test_sql_imports_deadline_hit_mid_import_pass_is_incomplete(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A fake clock that expires AFTER the repo-map build but mid-import-pass must still surface
    as INCOMPLETE (banner on text, flag on JSON, exit 2 on both) -- Sol R4."""
    proj = tmp_path / "proj"
    _write(proj / "a.py", "import os\n")
    _write(proj / "b.py", "import sys\n")

    import tensor_grep.cli.repo_map as repo_map_module

    real_monotonic = time.monotonic
    state = {"map_built": False}
    # First N calls (repo-map build + the scan-deadline setup) behave normally; once the map is
    # built, the clock jumps past scan_deadline so the import pass sees it as already expired.
    real_build_repo_map = repo_map_module.build_repo_map

    def fake_build_repo_map(*args, **kwargs):
        result = real_build_repo_map(*args, **kwargs)
        state["map_built"] = True
        return result

    monkeypatch.setattr(repo_map_module, "build_repo_map", fake_build_repo_map)

    def fake_monotonic() -> float:
        if state["map_built"]:
            return real_monotonic() + 10_000.0
        return real_monotonic()

    monkeypatch.setattr(time, "monotonic", fake_monotonic)

    json_result = runner.invoke(
        app,
        ["sql", str(proj), "SELECT * FROM imports", "--json", "--scan-deadline", "30"],
    )
    assert json_result.exit_code == 2, json_result.output
    payload = json.loads(json_result.stdout)
    assert payload["result_incomplete"] is True
    assert payload.get("scan_incomplete") is True

    state["map_built"] = False
    text_result = runner.invoke(
        app, ["sql", str(proj), "SELECT * FROM imports", "--scan-deadline", "30"]
    )
    assert text_result.exit_code == 2
    assert "INCOMPLETE" in text_result.stdout


def test_sql_imports_deadline_hit_within_a_single_file(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A cutoff can land WITHIN one file's own import list (between two
    `_resolve_raw_import_entry` calls for the SAME file), not only between files (Sol R5)."""
    proj = tmp_path / "proj"
    lines = "\n".join(f"import mod_{i}" for i in range(40))
    _write(proj / "many_imports.py", lines + "\n")

    import tensor_grep.cli.repo_map as repo_map_module

    real_resolve = repo_map_module._resolve_raw_import_entry

    def slow_resolve(*args, **kwargs):
        # Real wall-clock delay per resolved entry, so a 1s real `--scan-deadline` expires
        # partway through this ONE file's 40 import statements -- not between files.
        time.sleep(0.05)
        return real_resolve(*args, **kwargs)

    monkeypatch.setattr(repo_map_module, "_resolve_raw_import_entry", slow_resolve)

    json_result = runner.invoke(
        app,
        ["sql", str(proj), "SELECT * FROM imports", "--json", "--scan-deadline", "1"],
    )
    assert json_result.exit_code == 2, json_result.output
    payload = json.loads(json_result.stdout)
    assert payload["result_incomplete"] is True
    # Because the cutoff landed mid-file, fewer than all 40 import rows can have been resolved.
    assert payload["count"] < 40


def test_sql_write_to_imports_refused(tmp_path: Path) -> None:
    """The read-only authorizer covers `imports` exactly like `symbols`: no INSERT/DDL."""
    proj = tmp_path / "proj"
    _write(proj / "a.py", "import os\n")

    result = runner.invoke(
        app,
        [
            "sql",
            str(proj),
            "INSERT INTO imports VALUES ('x.py', 'os', 1, NULL)",
            "--json",
        ],
    )
    assert result.exit_code == 1, result.output
    payload = json.loads(result.stdout)
    assert "error" in payload
