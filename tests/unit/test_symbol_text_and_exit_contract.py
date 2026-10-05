"""B-01 / B-05 / B-09: symbol-command text output and the 0/1/2 exit contract."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from typer.testing import CliRunner

from tensor_grep.cli.main import app

runner = CliRunner()


def _project(tmp_path: Path) -> Path:
    project = tmp_path / "project"
    (project / "src").mkdir(parents=True)
    (project / "src" / "payments.py").write_text(
        "def create_invoice(total, tax):\n"
        "    subtotal = total + tax\n"
        "    return subtotal\n"
        "\n"
        "def caller():\n"
        "    return create_invoice(1, 2)\n"
        "\n"
        "def lonely_helper():\n"
        "    return 2\n",
        encoding="utf-8",
    )
    return project


def test_source_text_mode_prints_the_source_block(tmp_path: Path) -> None:
    project = _project(tmp_path)
    # CONTROL: the --json arm already carries the body, so the premise (the body is reachable)
    # holds and the text-mode miss is the emitter, not the resolver.
    as_json = runner.invoke(app, ["source", str(project), "create_invoice", "--json"])
    assert as_json.exit_code == 0, as_json.output
    assert "subtotal = total + tax" in json.loads(as_json.stdout)["sources"][0]["source"]

    result = runner.invoke(app, ["source", str(project), "create_invoice"])
    assert result.exit_code == 0, result.output
    assert "subtotal = total + tax" in result.stdout
    assert "payments.py:1-3" in result.stdout.replace("\\", "/")


@pytest.mark.parametrize("command", ["blast-radius-plan", "blast-radius-render"])
@pytest.mark.parametrize("json_flag", [[], ["--json"]])
def test_blast_radius_siblings_exit_1_on_unknown_symbol(
    tmp_path: Path, command: str, json_flag: list[str]
) -> None:
    project = _project(tmp_path)
    # CONTROL: a real symbol exits 0 through the same command and flags.
    known = runner.invoke(app, [command, str(project), "create_invoice", *json_flag])
    assert known.exit_code == 0, known.output

    unknown = runner.invoke(app, [command, str(project), "no_such_symbol_zz", *json_flag])
    assert unknown.exit_code == 1, unknown.output
    if json_flag:
        assert json.loads(unknown.stdout)["not_found"] is True


@pytest.mark.parametrize("command", ["blast-radius-plan", "blast-radius-render"])
def test_blast_radius_siblings_still_exit_2_when_scan_truncated(
    tmp_path: Path, command: str
) -> None:
    project = tmp_path / "project"
    (project / "src").mkdir(parents=True)
    for index in range(4):
        (project / "src" / f"m{index}.py").write_text(
            f"def helper_{index}():\n    return {index}\n", encoding="utf-8"
        )
    result = runner.invoke(
        app, [command, str(project), "helper_0", "--max-repo-files", "1", "--json"]
    )
    assert result.exit_code == 2, result.output
    assert json.loads(result.stdout)["result_incomplete"] is True


@pytest.mark.parametrize("command", ["refs", "callers", "blast-radius"])
def test_defined_but_unreferenced_symbol_is_not_reported_not_found(
    tmp_path: Path, command: str
) -> None:
    project = _project(tmp_path)
    result = runner.invoke(app, [command, str(project), "lonely_helper", "--json"])
    payload = json.loads(result.stdout)
    assert payload["definitions"], "premise: the symbol IS defined"
    assert payload["not_found"] is False
    assert result.exit_code == 0, result.output


@pytest.mark.parametrize("command", ["refs", "callers", "blast-radius"])
def test_undefined_symbol_still_reports_not_found(tmp_path: Path, command: str) -> None:
    # POSITIVE CONTROL for the test above: a genuinely absent symbol keeps exit 1 / not_found.
    project = _project(tmp_path)
    result = runner.invoke(app, [command, str(project), "no_such_symbol_zz", "--json"])
    assert result.exit_code == 1, result.output
    assert json.loads(result.stdout)["not_found"] is True
