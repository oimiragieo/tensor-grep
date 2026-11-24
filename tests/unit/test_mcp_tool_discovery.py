"""Tests for Wave 3 CL 3D-1: MCP progressive tool discovery & legacy default-off."""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path


def _list_tools_subprocess(env_overrides: dict[str, str] | None = None) -> list[str]:
    repo_root = Path(__file__).resolve().parents[2]
    env = {**os.environ, "PYTHONPATH": str(repo_root / "src")}
    env.pop("TG_MCP_LEGACY_TOOLS", None)
    if env_overrides:
        env.update(env_overrides)
    script = (
        "import asyncio, json; from tensor_grep.cli import mcp_server as m; "
        "print(json.dumps([t.name for t in asyncio.run(m.mcp.list_tools())]))"
    )
    done = subprocess.run(
        [sys.executable, "-c", script],
        env=env,
        cwd=str(repo_root),
        capture_output=True,
        text=True,
        timeout=90,
        check=True,
    )
    return list(json.loads(done.stdout))


def test_compact_mcp_list_tools_advertises_compact_surface() -> None:
    names = _list_tools_subprocess()
    assert len(names) == 15
    assert "tg_discover_tools" in names
    assert "tg_mcp_capabilities" in names
    assert "tg_navigate" in names
    assert "tg_symbol_defs" not in names
    assert "tg_search" not in names


def test_opt_in_mcp_surface_preserves_legacy_tools_and_discovery() -> None:
    names = _list_tools_subprocess({"TG_MCP_LEGACY_TOOLS": "1"})
    assert len(names) == 61
    assert "tg_discover_tools" in names
    assert "tg_symbol_defs" in names
    assert "tg_search" in names


def test_tg_discover_tools_returns_relevant_tools_within_limit() -> None:
    from tensor_grep.cli import mcp_server

    raw = mcp_server.tg_discover_tools("blast radius callers", limit=4)
    payload = json.loads(raw)

    assert payload["mcp_contract_version"] == mcp_server._TG_MCP_SERVER_CONTRACT_VERSION
    assert payload["total_catalog_tools"] == 61
    assert len(payload["tools"]) == 4

    returned_names = [tool["name"] for tool in payload["tools"]]
    assert any("blast_radius" in name or name == "tg_impact" for name in returned_names)
    assert any("callers" in name or name in {"tg_impact", "tg_navigate"} for name in returned_names)

    for tool in payload["tools"]:
        assert {"name", "summary", "parameters", "legacy_opt_in_env"} <= set(tool)
        assert isinstance(tool["summary"], str) and tool["summary"]
        assert isinstance(tool["parameters"], list)


def test_tg_discover_tools_bounds_oversized_intent_and_bool_limit() -> None:
    from tensor_grep.cli import mcp_server

    huge_intent = "search " * 5000
    payload = json.loads(mcp_server.tg_discover_tools(huge_intent, limit=True))  # type: ignore[arg-type]
    assert len(payload["intent"]) == 4096
    assert payload["limit"] == 5
    assert len(payload["tools"]) == 5

    empty_payload = json.loads(mcp_server.tg_discover_tools("   ", limit=100))
    assert empty_payload["tools"] == []
    assert empty_payload["limit"] == 20
