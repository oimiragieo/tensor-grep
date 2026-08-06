"""Workspace-root refuse must not share ``scan_limit`` with file-cap truncation.

An external dogfood asked for ``workspace_root_refused`` so agents do not confuse a
multi-project parent refuse with a ``--max-repo-files`` truncation. Both previously shared
``incomplete_reason_class: "scan_limit"`` (and ``error.code: "broad_scan_refused"``).

Defaults on ``_emit_broad_scan_refusal`` stay ``scan_limit`` / ``broad_scan_refused`` for
generated/vendored/large-root ceilings; only the workspace-root call site opts into the new
class/code pair.
"""

from __future__ import annotations

import json
from pathlib import Path

from typer.testing import CliRunner

from tensor_grep.cli.main import app


def _multi_project_workspace(tmp_path: Path) -> Path:
    workspace = tmp_path / "projects"
    workspace.mkdir()
    for project_name, marker_name in (
        ("alpha", "pyproject.toml"),
        ("beta", "package.json"),
        ("gamma", "Cargo.toml"),
    ):
        project = workspace / project_name
        (project / "src").mkdir(parents=True)
        (project / marker_name).write_text("", encoding="utf-8")
        (project / "src" / "app.py").write_text("needle\n", encoding="utf-8")
    return workspace


def test_workspace_root_json_refuse_uses_workspace_root_refused_class_and_code(
    tmp_path: Path,
) -> None:
    workspace = _multi_project_workspace(tmp_path)
    result = CliRunner().invoke(app, ["search", "needle", str(workspace), "--json"])

    assert result.exit_code == 2
    assert "broad workspace-root scan refused" in result.output
    # JSON goes to stdout; CliRunner merges streams into .output -- parse the JSON document.
    payload_start = result.stdout.find("{")
    assert payload_start >= 0, result.stdout
    payload = json.loads(result.stdout[payload_start:])
    assert payload["incomplete_reason_class"] == "workspace_root_refused"
    assert payload["error"]["code"] == "workspace_root_refused"
    assert payload["result_incomplete"] is True
    assert payload["total_matches"] == 0


def test_emit_defaults_remain_scan_limit_for_non_workspace_ceilings(
    capsys,
) -> None:
    """CONTROL: generated/vendored/large-root refusals keep the prior class/code pair."""
    from tensor_grep.cli.main import _emit_broad_scan_refusal

    _emit_broad_scan_refusal(
        "Error: broad root scan refused as a safety guard",
        json_output=True,
        path=".",
    )
    out = capsys.readouterr().out
    payload = json.loads(out)
    assert payload["incomplete_reason_class"] == "scan_limit"
    assert payload["error"]["code"] == "broad_scan_refused"
