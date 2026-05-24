from __future__ import annotations

import asyncio
import json
import os
import sys
from importlib.metadata import version
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
            assert initialized.serverInfo.version == version("tensor-grep")

            listed = await session.list_tools()
            tool_names = {tool.name for tool in listed.tools}
            assert "tg_mcp_capabilities" in tool_names
            assert "tg_rulesets" in tool_names

            capabilities = await session.call_tool("tg_mcp_capabilities", {})
            assert capabilities.isError is False
            capabilities_payload = json.loads(capabilities.content[0].text)
            assert capabilities_payload["schema_version"] == capabilities_payload["version"]
            assert capabilities_payload["routing_reason"] == "mcp-capabilities"
            assert capabilities_payload["cli_version"] == initialized.serverInfo.version
            assert capabilities_payload["mcp_protocol_version"]

            rulesets = await session.call_tool("tg_rulesets", {})
            assert rulesets.isError is False
            rulesets_payload = json.loads(rulesets.content[0].text)
            assert rulesets_payload["schema_version"] == rulesets_payload["version"]
            assert {rule["name"] for rule in rulesets_payload["rulesets"]} >= {"secrets-basic"}


def test_tg_mcp_stdio_initialize_tools_list_and_call_roundtrip() -> None:
    asyncio.run(_stdio_protocol_roundtrip())


async def _read_jsonrpc_line(process: asyncio.subprocess.Process) -> dict[str, object]:
    assert process.stdout is not None
    raw = await asyncio.wait_for(process.stdout.readline(), timeout=10.0)
    assert raw, "MCP server did not emit a JSON-RPC response"
    return json.loads(raw.decode("utf-8"))


async def _stdio_content_length_initialize_roundtrip() -> None:
    process = await asyncio.create_subprocess_exec(
        sys.executable,
        "-m",
        "tensor_grep",
        "mcp",
        cwd=REPO_ROOT,
        env=_mcp_env(),
        stdin=asyncio.subprocess.PIPE,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    assert process.stdin is not None
    try:
        initialize = {
            "jsonrpc": "2.0",
            "id": 1,
            "method": "initialize",
            "params": {
                "protocolVersion": "2025-06-18",
                "capabilities": {},
                "clientInfo": {"name": "raw-framed-test", "version": "1.0.0"},
            },
        }
        body = json.dumps(initialize, separators=(",", ":"))
        frame = f"Content-Length: {len(body.encode('utf-8'))}\r\n\r\n{body}"
        process.stdin.write(frame.encode("utf-8"))
        await process.stdin.drain()

        response = await _read_jsonrpc_line(process)

        assert response["id"] == 1
        result = response["result"]
        assert isinstance(result, dict)
        assert result["protocolVersion"] == "2025-06-18"
        server_info = result["serverInfo"]
        assert isinstance(server_info, dict)
        assert server_info["name"] == "tensor-grep"
        assert server_info["version"] == version("tensor-grep")
    finally:
        if process.stdin is not None:
            process.stdin.close()
            await process.stdin.wait_closed()
        try:
            await asyncio.wait_for(process.wait(), timeout=5.0)
        except TimeoutError:
            process.terminate()
            await process.wait()


def test_tg_mcp_stdio_accepts_content_length_initialize_frame() -> None:
    asyncio.run(_stdio_content_length_initialize_roundtrip())
