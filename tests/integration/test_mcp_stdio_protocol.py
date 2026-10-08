from __future__ import annotations

import asyncio
import json
import os
import sys
import time
from importlib.metadata import version
from pathlib import Path

import pytest
from mcp import ClientSession
from mcp.client.stdio import StdioServerParameters, stdio_client

from tensor_grep.cli.mcp_server import _TG_MCP_SERVER_CONTRACT_VERSION

pytestmark = [pytest.mark.integration]

REPO_ROOT = Path(__file__).resolve().parents[2]
SRC_DIR = REPO_ROOT / "src"

# #98 (MCP consolidation Phase-1): the 10 additive meta-tools grew the `tools/list` JSON-RPC
# response past asyncio's DEFAULT `StreamReader` line-buffer limit (2**16 = 64 KiB) -- a
# single-line `tools/list` result now runs ~85 KiB, and asyncio.subprocess's default `limit`
# governs the buffer BOTH `Process.stdout.readline()` (used by the raw manual-framing tests
# below) and `Process.stdout.readuntil()` read against. The official `mcp` SDK client used by
# `_stdio_protocol_roundtrip` above chunks its own reads and is unaffected; only the two
# `asyncio.create_subprocess_exec(...)`-based raw-framing helpers below need a larger buffer.
# Sized generously (8 MiB) so continued additive tool-surface growth does not silently
# re-trip this -- a real MCP message is separately capped at `_MAX_MCP_STDIO_MESSAGE_BYTES`
# (64 MiB) server-side, so this test-only buffer is well inside that ceiling.
_SUBPROCESS_STDOUT_LIMIT_BYTES = 8 * 1024 * 1024

# The raw-framing assertions measure the server's response after it starts reading stdin. On a
# cold Windows host, importing the MCP tool surface can consume the entire response deadline.
# The child preloads that surface, then runs the same CLI entry point as `python -m tensor_grep mcp`.
# Its private ready marker is emitted at the start of the server coroutine, after CLI dispatch.
_FRAMED_SERVER_STARTUP_TIMEOUT_SECONDS = 60.0
_FRAMED_SERVER_READY = b"tg-mcp-framed-ready\n"
_FRAMED_SERVER_SCRIPT = (
    "import sys\n"
    "import tensor_grep.cli.mcp_server as mcp_server\n"
    "_original = mcp_server._run_mcp_stdio_async\n"
    "async def _ready_then_run():\n"
    f"    sys.stderr.buffer.write({_FRAMED_SERVER_READY!r})\n"
    "    sys.stderr.buffer.flush()\n"
    "    await _original()\n"
    "mcp_server._run_mcp_stdio_async = _ready_then_run\n"
    "sys.argv = ['tensor_grep', 'mcp']\n"
    "from tensor_grep.__main__ import main\n"
    "main()\n"
)


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
            # _TG_MCP_SERVER_CONTRACT_VERSION history: 1.3.0 -> 1.4.0 (#98, 10 additive
            # task-shaped meta-tools); 1.4.0 -> 1.5.0 (#283, additive scan_limit cause
            # fields on tg_search); 1.7.0 -> 1.8.0 (P3, unified `incomplete` envelope via
            # incompleteness.py); 1.8.0 -> 1.9.0 (E-04, bounded match rows); 1.9.0 -> 1.10.0 (K2); 1.10.0 -> 1.11.0 (G1, coverage_gap). Keep this comment in step with the assert below.
            assert (
                initialized.serverInfo.version == _TG_MCP_SERVER_CONTRACT_VERSION
            )  # task 336: budget_remediable on the repo_map-backed wire

            listed = await session.list_tools()
            tool_names = {tool.name for tool in listed.tools}
            assert "tg_mcp_capabilities" in tool_names
            assert "tg_rulesets" in tool_names
            # #98: the 10 additive meta-tools are registered by default (TG_MCP_LEGACY_TOOLS
            # defaults ON, so the 46 legacy names -- including tg_rulesets above -- stay too).
            assert "tg_navigate" in tool_names
            assert "tg_rewrite" in tool_names
            query_tool = next(tool for tool in listed.tools if tool.name == "tg_query")
            assert '"search"' in query_tool.description
            assert "action" in query_tool.inputSchema["properties"]
            assert "paths_defaulted" not in query_tool.inputSchema["properties"]
            search_tool = next(tool for tool in listed.tools if tool.name == "tg_search")
            assert "paths_defaulted" not in search_tool.inputSchema["properties"]

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

            search_payloads = []
            for action in ("text", "search"):
                result = await session.call_tool(
                    "tg_query",
                    {
                        "action": action,
                        "pattern": "KNOWN_COMMANDS",
                        "path": "src/tensor_grep/cli/commands.py",
                        "fixed_strings": True,
                        "max_results": 1,
                    },
                )
                assert result.isError is False
                payload = json.loads(result.content[0].text)
                assert "error" not in payload
                assert payload["total_matches"] > 0
                assert payload["mcp_contract_version"] == _TG_MCP_SERVER_CONTRACT_VERSION
                search_payloads.append(payload)
            assert search_payloads[0]["matches"] == search_payloads[1]["matches"]


def test_tg_mcp_stdio_initialize_tools_list_and_call_roundtrip() -> None:
    asyncio.run(_stdio_protocol_roundtrip())


async def _read_jsonrpc_line(process: asyncio.subprocess.Process) -> dict[str, object]:
    assert process.stdout is not None
    raw = await asyncio.wait_for(process.stdout.readline(), timeout=10.0)
    assert raw, "MCP server did not emit a JSON-RPC response"
    return json.loads(raw.decode("utf-8"))


async def _close_framed_server(process: asyncio.subprocess.Process) -> None:
    if process.stdin is not None:
        process.stdin.close()
        try:
            await asyncio.wait_for(process.stdin.wait_closed(), timeout=2.0)
        except (TimeoutError, BrokenPipeError, ConnectionResetError):
            pass
    try:
        await asyncio.wait_for(process.wait(), timeout=5.0)
        return
    except TimeoutError:
        pass

    if sys.platform == "win32":
        # The venv launcher can own a second CPython process. Stop the owned tree so a
        # startup failure cannot leave that reader or its inherited stdio pipes alive.
        killer = await asyncio.create_subprocess_exec(
            "taskkill",
            "/PID",
            str(process.pid),
            "/T",
            "/F",
            stdin=asyncio.subprocess.DEVNULL,
            stdout=asyncio.subprocess.DEVNULL,
            stderr=asyncio.subprocess.DEVNULL,
        )
        try:
            await asyncio.wait_for(killer.wait(), timeout=5.0)
        except TimeoutError:
            killer.kill()
            await asyncio.wait_for(killer.wait(), timeout=5.0)
    elif process.returncode is None:
        process.kill()
    await asyncio.wait_for(process.wait(), timeout=5.0)


async def _start_framed_server() -> asyncio.subprocess.Process:
    started = time.monotonic()
    process = await asyncio.create_subprocess_exec(
        sys.executable,
        "-c",
        _FRAMED_SERVER_SCRIPT,
        cwd=REPO_ROOT,
        env=_mcp_env(),
        stdin=asyncio.subprocess.PIPE,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
        limit=_SUBPROCESS_STDOUT_LIMIT_BYTES,
    )
    assert process.stderr is not None
    try:
        ready = await asyncio.wait_for(
            process.stderr.readline(), timeout=_FRAMED_SERVER_STARTUP_TIMEOUT_SECONDS
        )
        assert ready == _FRAMED_SERVER_READY, (
            f"MCP framed server failed during startup: {ready[:500]!r}; "
            f"returncode={process.returncode}"
        )
    except BaseException as exc:
        startup_elapsed = time.monotonic() - started
        stderr_tail = bytes(process.stderr._buffer)[-2000:]
        returncode_at_failure = process.returncode
        try:
            await _close_framed_server(process)
        except Exception as cleanup_exc:
            exc.add_note(f"MCP child cleanup failed: {cleanup_exc!r}")
        if isinstance(exc, TimeoutError):
            raise TimeoutError(
                f"MCP framed server did not start after {startup_elapsed:.1f}s; "
                f"returncode={returncode_at_failure}, stderr_tail={stderr_tail!r}"
            ) from exc
        raise
    return process


async def _stdio_content_length_initialize_roundtrip() -> None:
    process = await _start_framed_server()
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

        response_started = time.monotonic()
        try:
            response = await _read_jsonrpc_line(process)
        except TimeoutError as exc:
            assert process.stdout is not None
            assert process.stderr is not None
            waited = time.monotonic() - response_started
            stdout_buffered = len(process.stdout._buffer)
            stderr_buffered = bytes(process.stderr._buffer)[-8000:]
            returncode_at_timeout = process.returncode
            process.stdin.close()
            try:
                raw_after_close = await asyncio.wait_for(process.stdout.readline(), timeout=5.0)
            except TimeoutError:
                raw_after_close = None
            raise TimeoutError(
                f"MCP framed initialize had no response after {waited:.1f}s; "
                f"returncode={returncode_at_timeout}, stdout_buffered={stdout_buffered}, "
                f"stderr_tail={stderr_buffered!r}, "
                f"stdout_after_stdin_close={raw_after_close[:500] if raw_after_close is not None else None!r}, "
                f"returncode_after_close={process.returncode}"
            ) from exc

        assert response["id"] == 1
        result = response["result"]
        assert isinstance(result, dict)
        assert result["protocolVersion"] == "2025-06-18"
        server_info = result["serverInfo"]
        assert isinstance(server_info, dict)
        assert server_info["name"] == "tensor-grep"
        # _TG_MCP_SERVER_CONTRACT_VERSION history: 1.3.0 -> 1.4.0 (#98, 10 additive
        # task-shaped meta-tools); 1.4.0 -> 1.5.0 (#283, additive scan_limit cause
        # fields on tg_search); 1.7.0 -> 1.8.0 (P3, unified `incomplete` envelope via
        # incompleteness.py); 1.8.0 -> 1.9.0 (E-04, bounded match rows); 1.9.0 -> 1.10.0 (K2); 1.10.0 -> 1.11.0 (G1, coverage_gap). Keep this comment in step with the assert below.
        assert (
            server_info["version"] == _TG_MCP_SERVER_CONTRACT_VERSION
        )  # task 336: budget_remediable on the repo_map-backed wire
    finally:
        await _close_framed_server(process)


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
    process = await _start_framed_server()
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
        await _close_framed_server(process)


def test_tg_mcp_stdio_multibyte_utf8_body_does_not_desync_next_message() -> None:
    asyncio.run(_stdio_content_length_multibyte_utf8_does_not_desync_next_message())
