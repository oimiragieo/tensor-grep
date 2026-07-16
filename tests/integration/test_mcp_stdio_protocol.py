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
            # Wave 2d (#189): _TG_MCP_SERVER_CONTRACT_VERSION bumped 1.2.0 -> 1.3.0 (tg_find added).
            assert initialized.serverInfo.version == "1.3.0"

            listed = await session.list_tools()
            tool_names = {tool.name for tool in listed.tools}
            assert "tg_mcp_capabilities" in tool_names
            assert "tg_rulesets" in tool_names

            capabilities = await session.call_tool("tg_mcp_capabilities", {})
            assert capabilities.isError is False
            capabilities_payload = json.loads(capabilities.content[0].text)
            assert capabilities_payload["schema_version"] == capabilities_payload["version"]
            assert capabilities_payload["routing_reason"] == "mcp-capabilities"
            assert capabilities_payload["cli_version"] == version("tensor-grep")
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
        # Wave 2d (#189): _TG_MCP_SERVER_CONTRACT_VERSION bumped 1.2.0 -> 1.3.0 (tg_find added).
        assert server_info["version"] == "1.3.0"
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


def _frame(payload: dict[str, object]) -> bytes:
    body = json.dumps(payload, separators=(",", ":"), ensure_ascii=False)
    encoded = body.encode("utf-8")
    return f"Content-Length: {len(encoded)}\r\n\r\n".encode("ascii") + encoded


async def _stdio_content_length_multibyte_utf8_does_not_desync_next_message() -> None:
    """Audit #49: a Content-Length-framed message with a multi-byte UTF-8 body must not desync the
    framed stream -- the FOLLOWING pipelined message must still parse and execute correctly. Uses a
    real subprocess and real OS pipes (not an in-memory buffer) for maximum fidelity."""
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
        # "e" with an acute accent (\u00e9) and "i" with a circumflex (\u00ee) are each 2 UTF-8
        # bytes but 1 character -- escape sequences keep this source file ASCII-only (house rule).
        initialize = {
            "jsonrpc": "2.0",
            "id": 1,
            "method": "initialize",
            "params": {
                "protocolVersion": "2025-06-18",
                "capabilities": {},
                "clientInfo": {
                    "name": "t\u00e9st-cl\u00eent-\u00e9\u00e9\u00e9",
                    "version": "1.0.0",
                },
            },
        }
        process.stdin.write(_frame(initialize))
        await process.stdin.drain()
        initialize_response = await _read_jsonrpc_line(process)
        assert initialize_response["id"] == 1
        assert "result" in initialize_response, initialize_response

        # Pipeline a notification (no response expected) then a real follow-up request, all via
        # the same Content-Length-framed path. If the first (multi-byte) body were read as
        # characters instead of bytes, the stream would already be desynced here.
        process.stdin.write(_frame({"jsonrpc": "2.0", "method": "notifications/initialized"}))
        await process.stdin.drain()
        process.stdin.write(
            _frame({"jsonrpc": "2.0", "id": 2, "method": "tools/list", "params": {}})
        )
        await process.stdin.drain()

        tools_response = await _read_jsonrpc_line(process)
        assert tools_response["id"] == 2, tools_response
        result = tools_response["result"]
        assert isinstance(result, dict)
        tools = result["tools"]
        assert isinstance(tools, list)
        tool_names = {tool["name"] for tool in tools}
        assert "tg_mcp_capabilities" in tool_names
    finally:
        if process.stdin is not None:
            process.stdin.close()
            await process.stdin.wait_closed()
        try:
            await asyncio.wait_for(process.wait(), timeout=5.0)
        except TimeoutError:
            process.terminate()
            await process.wait()


def test_tg_mcp_stdio_multibyte_utf8_body_does_not_desync_next_message() -> None:
    asyncio.run(_stdio_content_length_multibyte_utf8_does_not_desync_next_message())
