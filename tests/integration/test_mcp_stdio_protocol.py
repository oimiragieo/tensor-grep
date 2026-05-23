from __future__ import annotations

import asyncio
import json
import os
import sys
from pathlib import Path

import pytest
from mcp import ClientSession
from mcp.client.stdio import StdioServerParameters, stdio_client

pytestmark = [pytest.mark.integration]

REPO_ROOT = Path(__file__).resolve().parents[2]
SRC_DIR = REPO_ROOT / "src"


def _mcp_env() -> dict[str, str]:
    env = os.environ.copy()
    existing_pythonpath = env.get("PYTHONPATH")
    env["PYTHONPATH"] = (
        f"{SRC_DIR}{os.pathsep}{existing_pythonpath}" if existing_pythonpath else str(SRC_DIR)
    )
    return env


async def _stdio_protocol_roundtrip() -> None:
    server = StdioServerParameters(
        command=sys.executable,
        args=["-m", "tensor_grep", "mcp"],
        cwd=REPO_ROOT,
        env=_mcp_env(),
        encoding="utf-8",
        encoding_error_handler="replace",
    )
    async with stdio_client(server) as streams:
        read_stream, write_stream = streams
        async with ClientSession(read_stream, write_stream) as session:
            initialized = await session.initialize()
            assert initialized.serverInfo.name == "tensor-grep"

            listed = await session.list_tools()
            tool_names = {tool.name for tool in listed.tools}
            assert "tg_mcp_capabilities" in tool_names
            assert "tg_rulesets" in tool_names

            capabilities = await session.call_tool("tg_mcp_capabilities", {})
            assert capabilities.isError is False
            capabilities_payload = json.loads(capabilities.content[0].text)
            assert capabilities_payload["schema_version"] == capabilities_payload["version"]
            assert capabilities_payload["routing_reason"] == "mcp-capabilities"

            rulesets = await session.call_tool("tg_rulesets", {})
            assert rulesets.isError is False
            rulesets_payload = json.loads(rulesets.content[0].text)
            assert rulesets_payload["schema_version"] == rulesets_payload["version"]
            assert {rule["name"] for rule in rulesets_payload["rulesets"]} >= {"secrets-basic"}


def test_tg_mcp_stdio_initialize_tools_list_and_call_roundtrip() -> None:
    asyncio.run(_stdio_protocol_roundtrip())
