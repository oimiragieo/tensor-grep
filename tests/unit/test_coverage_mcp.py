from __future__ import annotations

import asyncio
import json
from pathlib import Path

from mcp import types

from tensor_grep.cli import mcp_server


def test_coverage_contract_advances_after_mcp_actions() -> None:
    assert tuple(map(int, mcp_server._TG_MCP_SERVER_CONTRACT_VERSION.split("."))) == (1, 19, 0)


def test_tg_coverage_returns_inventory_and_contract_version(tmp_path: Path, monkeypatch) -> None:
    project = tmp_path / "project"
    project.mkdir()
    (project / "main.py").write_text("def run():\n    return 1\n", encoding="utf-8")
    monkeypatch.setenv("TG_MCP_ROOT", str(tmp_path))

    payload = json.loads(mcp_server.tg_coverage(str(project)))

    assert payload["mcp_contract_version"] == mcp_server._TG_MCP_SERVER_CONTRACT_VERSION
    assert payload["files_total"] == 1
    assert payload["unindexed"] == ["main.py"]
    assert payload["complete"] is False


def test_tg_coverage_refuses_paths_outside_mcp_root(tmp_path: Path, monkeypatch) -> None:
    anchor = tmp_path / "anchor"
    external = tmp_path / "external"
    anchor.mkdir()
    external.mkdir()
    monkeypatch.setenv("TG_MCP_ROOT", str(anchor))

    payload = json.loads(mcp_server.tg_coverage(str(external)))

    assert payload["path"] == "[refused]"
    assert payload["error"]["code"] == "invalid_input"


def test_tg_coverage_error_sets_mcp_wire_error_flag(tmp_path: Path, monkeypatch) -> None:
    anchor = tmp_path / "anchor"
    external = tmp_path / "external"
    anchor.mkdir()
    external.mkdir()
    monkeypatch.setenv("TG_MCP_ROOT", str(anchor))

    refused = asyncio.run(mcp_server.mcp.call_tool("tg_coverage", {"path": str(external)}))
    accepted = asyncio.run(mcp_server.mcp.call_tool("tg_coverage", {"path": str(anchor)}))

    if not isinstance(accepted, types.CallToolResult):
        content, structured_content = accepted
        accepted = types.CallToolResult(content=list(content), structuredContent=structured_content)
    assert refused.isError is True
    assert json.loads(refused.content[0].text)["error"]["code"] == "invalid_input"
    assert accepted.isError is False


def test_tg_coverage_does_not_load_parent_session_outside_mcp_root(
    tmp_path: Path, monkeypatch
) -> None:
    from typer.testing import CliRunner

    from tensor_grep.cli.main import app

    project = tmp_path / "project"
    allowed = project / "allowed"
    allowed.mkdir(parents=True)
    (project / "pyproject.toml").write_text("[project]\nname='fixture'\n", encoding="utf-8")
    (allowed / "safe.py").write_text("value = 1\n", encoding="utf-8")
    (project / "private_sibling_name.py").write_text("secret = 1\n", encoding="utf-8")
    assert CliRunner().invoke(app, ["session", "open", str(project), "--json"]).exit_code == 0
    monkeypatch.setenv("TG_MCP_ROOT", str(allowed))

    payload = json.loads(mcp_server.tg_coverage(str(allowed)))

    assert "private_sibling_name.py" not in json.dumps(payload)
    assert payload["unindexed"] == ["safe.py"]


def test_tg_coverage_cap_sets_incomplete_status(tmp_path: Path, monkeypatch) -> None:
    from tensor_grep.cli import repo_map

    project = tmp_path / "project"
    project.mkdir()
    for name in ("first.py", "second.py"):
        (project / name).write_text("value = 1\n", encoding="utf-8")
    monkeypatch.setenv("TG_MCP_ROOT", str(project))
    monkeypatch.setattr(repo_map, "DEFAULT_AGENT_REPO_MAP_LIMIT", 1)

    payload = json.loads(mcp_server.tg_coverage(str(project)))

    assert payload["caps"] == ["max_repo_files"]
    assert payload["complete"] is False
    assert payload["incomplete"]["status"] is True
    assert payload["incomplete"]["cause"] == "scan_limit"
    assert payload["incomplete"]["budget_remediable"] is True
