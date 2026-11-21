from __future__ import annotations

import csv
import io
import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import pytest
from typer.testing import CliRunner

from tensor_grep.cli import diff_impact as diff_impact_mod
from tensor_grep.cli.formatters.csv_fmt import CsvFormatter
from tensor_grep.cli.main import app
from tensor_grep.core.result import MatchLine, SearchResult

runner = CliRunner()


@dataclass
class _DummyProc:
    returncode: int
    stdout: bytes = b""
    stderr: bytes = b""


def test_diff_impact_decodes_bytes_stderr_and_hints_on_directory_ref(
    tmp_path: Path, monkeypatch: Any
) -> None:
    target_dir = tmp_path / "subdir"
    target_dir.mkdir()

    def fake_run_subprocess(cmd: list[str], **kwargs: Any) -> _DummyProc:
        if "rev-parse" in cmd:
            return _DummyProc(returncode=0, stdout=str(tmp_path).encode("utf-8") + b"\n")
        return _DummyProc(returncode=128, stderr=b"fatal: bad revision\n")

    monkeypatch.setattr(diff_impact_mod, "run_subprocess", fake_run_subprocess)

    result = runner.invoke(app, ["diff-impact", str(target_dir), "--json"])
    assert result.exit_code == 2
    payload = json.loads(result.stdout)
    assert payload.get("partial") is True
    error = str(payload.get("error") or "")
    assert error
    assert not error.startswith(("b'", 'b"'))
    lowered = error.lower()
    assert any(token in lowered for token in ("revision", "commit", "ref"))
    assert any(token in lowered for token in ("directory", "path"))

    with pytest.raises(diff_impact_mod.DiffError) as exc_info:
        diff_impact_mod.extract_diff_hunks_from_git(ref="nonexistent-rev", root=tmp_path)
    msg = str(exc_info.value)
    assert not msg.startswith(("b'", 'b"'))
    assert "fatal: bad revision" in msg


def test_scan_json_emits_structured_error_on_unknown_ruleset_and_missing_sgconfig(
    tmp_path: Path, monkeypatch: Any
) -> None:
    monkeypatch.chdir(tmp_path)

    res_ruleset = runner.invoke(
        app, ["scan", str(tmp_path), "--ruleset", "nonexistent-pack", "--json"]
    )
    assert res_ruleset.exit_code != 0
    payload_ruleset = json.loads(res_ruleset.stdout)
    assert payload_ruleset.get("error")

    res_missing = runner.invoke(app, ["scan", str(tmp_path), "--json"])
    assert res_missing.exit_code != 0
    payload_missing = json.loads(res_missing.stdout)
    assert payload_missing.get("error")


def test_file_api_extracts_ts_export_interface_type_enum(tmp_path: Path) -> None:
    ts_file = tmp_path / "models.ts"
    ts_file.write_text(
        "\n".join([
            "export interface Invoice {",
            "  id: string;",
            "}",
            "",
            'export type Status = "open" | "paid";',
            "",
            "export enum Role {",
            "  Admin = 1,",
            "}",
            "",
        ]),
        encoding="utf-8",
    )

    result = runner.invoke(app, ["file-api", str(ts_file), "--json"])
    assert result.exit_code == 0
    payload = json.loads(result.stdout)
    names = {sym["name"] for sym in payload["symbols"]}
    assert {"Invoice", "Status", "Role"}.issubset(names)


def test_prepare_does_not_suggest_uv_run_without_uv_lock(tmp_path: Path) -> None:
    (tmp_path / "app.py").write_text(
        "def compute(x: int) -> int:\n    return x + 1\n",
        encoding="utf-8",
    )
    (tmp_path / "test_app.py").write_text(
        "from app import compute\n\ndef test_compute():\n    assert compute(1) == 2\n",
        encoding="utf-8",
    )

    result = runner.invoke(app, ["prepare", str(tmp_path), "compute", "--json"])
    assert result.exit_code == 0
    payload = json.loads(result.stdout)
    plan_commands = [step["command"] for step in payload["validation_plan"]]
    assert all("uv run" not in cmd for cmd in plan_commands)
    assert all("uv run" not in cmd for cmd in payload["validation_commands"])
    assert "pytest -q" in plan_commands


def test_search_csv_strips_trailing_newline_from_text_cell() -> None:
    result = SearchResult(
        matches=[MatchLine(file="a.py", line_number=1, text="def foo():\n")],
        total_files=1,
        total_matches=1,
    )
    output = CsvFormatter().format(result)
    rows = list(csv.reader(io.StringIO(output)))
    assert rows[0] == ["file", "line_number", "text"]
    assert rows[1] == ["a.py", "1", "def foo():"]
