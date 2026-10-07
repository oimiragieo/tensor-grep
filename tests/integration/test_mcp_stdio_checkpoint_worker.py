"""Checkpoint recovery through a real MCP stdio process."""

from __future__ import annotations

import asyncio
import json
import os
import subprocess
import sys
from datetime import timedelta
from pathlib import Path
from typing import Any

import pytest
from mcp import ClientSession, types
from mcp.client.stdio import StdioServerParameters, stdio_client

pytestmark = pytest.mark.integration
SRC_DIR = Path(__file__).resolve().parents[2] / "src"
_HELD_SERVER = """
import os
import socket
from tensor_grep.cli import checkpoint_store
from tensor_grep.cli import mcp_server

original = checkpoint_store.create_checkpoint

def held_create(*args, **kwargs):
    with socket.create_connection(("127.0.0.1", int(os.environ["TG_TEST_GATE_PORT"])), timeout=10) as gate:
        gate.sendall(b"E")
        if gate.recv(1) != b"R":
            raise RuntimeError("checkpoint test gate closed")
    return original(*args, **kwargs)

checkpoint_store.create_checkpoint = held_create
if os.environ.get("TG_TEST_UNSERIALIZED") == "1":
    tool = mcp_server.mcp._tool_manager.get_tool("tg_checkpoint_create")
    tool.fn = mcp_server.tg_checkpoint_create
    tool.is_async = False
mcp_server.run_mcp_server()
"""


async def _call(
    session: ClientSession, name: str, arguments: dict[str, Any], timeout: float = 30
) -> dict[str, Any]:
    result = await asyncio.wait_for(session.call_tool(name, arguments), timeout)
    assert result.isError is False
    return json.loads(result.content[0].text)


@pytest.mark.parametrize("git_repo", [False, True])
@pytest.mark.parametrize("directory_scope", [False, True])
@pytest.mark.parametrize("consolidated", [False, True])
def test_checkpoint_stdio_create_list_undo_is_scoped_and_reusable(
    tmp_path: Path, git_repo: bool, directory_scope: bool, consolidated: bool
) -> None:
    project = tmp_path / "project"
    project.mkdir()
    target = project / "target.py"
    target.write_bytes(b"old \x00 bytes\r\n")
    neighbor = project / "neighbor.py"
    neighbor.write_bytes(b"neighbor\n")
    outside = tmp_path / "outside.py"
    outside.write_bytes(b"outside\n")
    if git_repo:
        subprocess.run(["git", "init", "-q", str(project)], check=True, timeout=10)
        subprocess.run(
            ["git", "-C", str(project), "add", "--", "target.py", "neighbor.py"],
            check=True,
            timeout=10,
        )
    scope = project if directory_scope else target

    env = os.environ.copy()
    env["PYTHONPATH"] = str(SRC_DIR)
    env["TG_MCP_ROOT"] = str(project)
    env["TG_MCP_LEGACY_TOOLS"] = "1"
    server = StdioServerParameters(
        command=sys.executable,
        args=["-m", "tensor_grep", "mcp"],
        cwd=project,
        env=env,
        encoding="utf-8",
        encoding_error_handler="replace",
    )

    async def exercise() -> None:
        async with stdio_client(server) as (read, write):
            async with ClientSession(read, write, read_timeout_seconds=timedelta(seconds=30)) as s:
                await s.initialize()
                names = {tool.name for tool in (await s.list_tools()).tools}
                assert {
                    "tg_checkpoint",
                    "tg_checkpoint_create",
                    "tg_checkpoint_list",
                    "tg_checkpoint_undo",
                } <= names

                def args_for(action: str, **extra: Any) -> tuple[str, dict[str, Any]]:
                    args: dict[str, Any] = {"path": str(scope), **extra}
                    if consolidated:
                        return "tg_checkpoint", {"action": action, **args}
                    return f"tg_checkpoint_{action}", args

                create_name, create_args = args_for("create", label="null")
                created = await _call(s, create_name, create_args)
                assert "error" not in created, created
                assert created["mode"] == (
                    "git-worktree-snapshot"
                    if git_repo and directory_scope
                    else "filesystem-snapshot"
                )
                assert created["root"] == str(project)
                assert created["file_count"] == (2 if directory_scope else 1)
                checkpoint_id = created["checkpoint_id"]
                assert created["label"] == "null"
                assert created["scoped_paths"] is None

                list_name, list_args = args_for("list")
                listed = await _call(s, list_name, list_args)
                assert [row["checkpoint_id"] for row in listed["checkpoints"]] == [checkpoint_id]

                target.write_bytes(b"changed\n")
                neighbor.write_bytes(b"neighbor changed\n")
                outside.write_bytes(b"outside changed\n")
                undo_name, undo_args = args_for("undo", checkpoint_id=checkpoint_id)
                undone = await _call(s, undo_name, undo_args)
                assert "error" not in undone, undone
                assert undone["checkpoint_id"] == checkpoint_id
                assert undone["root"] == str(project)
                assert undone["restored_files"] == (2 if directory_scope else 1)
                assert target.read_bytes() == b"old \x00 bytes\r\n"
                assert neighbor.read_bytes() == (
                    b"neighbor\n" if directory_scope else b"neighbor changed\n"
                )
                assert outside.read_bytes() == b"outside changed\n"

                refused = await _call(
                    s,
                    create_name,
                    {"path": str(outside), "label": "outside"}
                    | ({"action": "create"} if consolidated else {}),
                )
                assert refused["error"]["code"] == "invalid_input"
                assert outside.read_bytes() == b"outside changed\n"
                assert len((await _call(s, list_name, list_args))["checkpoints"]) == 1
                assert "error" not in await _call(s, "tg_mcp_capabilities", {})

    asyncio.run(exercise())
    index = project / ".tensor-grep" / "checkpoints" / "index.json"
    assert len(json.loads(index.read_text(encoding="utf-8"))) == 1


def test_stdio_ping_and_cancellation_while_checkpoint_worker_is_held(tmp_path: Path) -> None:
    project = tmp_path / "project"
    project.mkdir()
    (project / "target.py").write_bytes(b"before\n")

    async def exercise() -> None:
        loop = asyncio.get_running_loop()
        entered: asyncio.Future[asyncio.StreamWriter] = loop.create_future()

        async def gate(reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
            assert await reader.readexactly(1) == b"E"
            entered.set_result(writer)
            await reader.read()
            writer.close()

        listener = await asyncio.start_server(gate, "127.0.0.1", 0)
        async with listener:
            env = os.environ.copy()
            env["PYTHONPATH"] = str(SRC_DIR)
            env["TG_MCP_ROOT"] = str(project)
            env["TG_TEST_GATE_PORT"] = str(listener.sockets[0].getsockname()[1])
            server = StdioServerParameters(
                command=sys.executable,
                args=["-c", _HELD_SERVER],
                cwd=project,
                env=env,
                encoding="utf-8",
                encoding_error_handler="replace",
            )
            async with stdio_client(server) as (read, write):
                async with ClientSession(
                    read, write, read_timeout_seconds=timedelta(seconds=10)
                ) as session:
                    await session.initialize()
                    sent_calls: asyncio.Queue[int] = asyncio.Queue()

                    class RequestProbe:
                        def __init__(self, delegate: Any) -> None:
                            self.delegate = delegate

                        async def send(self, message: Any) -> None:
                            await self.delegate.send(message)
                            request = message.message.root
                            if (
                                isinstance(request, types.JSONRPCRequest)
                                and request.method == "tools/call"
                            ):
                                sent_calls.put_nowait(request.id)

                        def __getattr__(self, name: str) -> Any:
                            return getattr(self.delegate, name)

                    session._write_stream = RequestProbe(session._write_stream)
                    first = asyncio.create_task(
                        session.call_tool(
                            "tg_checkpoint_create", {"path": str(project), "label": "active"}
                        )
                    )
                    first_id = await asyncio.wait_for(sent_calls.get(), 10)
                    writer = await asyncio.wait_for(entered, 10)
                    ping = asyncio.create_task(session.send_ping())
                    ping_responsive = False
                    try:
                        done, _pending = await asyncio.wait({ping}, timeout=5)
                        ping_responsive = bool(done)
                        if ping_responsive:
                            second = asyncio.create_task(
                                session.call_tool(
                                    "tg_checkpoint_create",
                                    {"path": str(project), "label": "queued"},
                                )
                            )
                            second_id = await asyncio.wait_for(sent_calls.get(), 10)
                            await asyncio.wait_for(session.send_ping(), 5)
                            await session.send_notification(
                                types.ClientNotification(
                                    types.CancelledNotification(
                                        params=types.CancelledNotificationParams(
                                            requestId=second_id
                                        )
                                    )
                                )
                            )
                            second.cancel()
                            with pytest.raises(asyncio.CancelledError):
                                await second
                            await session.send_notification(
                                types.ClientNotification(
                                    types.CancelledNotification(
                                        params=types.CancelledNotificationParams(requestId=first_id)
                                    )
                                )
                            )
                            first.cancel()
                            with pytest.raises(asyncio.CancelledError):
                                await first
                            await asyncio.wait_for(session.send_ping(), 5)
                    finally:
                        writer.write(b"R")
                        await writer.drain()
                    await asyncio.wait_for(ping, 10)
                    if not ping_responsive:
                        await asyncio.wait_for(first, 10)
                    assert ping_responsive, "ping waited behind the synchronous checkpoint handler"
                    listed = await _call(session, "tg_checkpoint_list", {"path": str(project)})
                    assert [row["label"] for row in listed["checkpoints"]] == ["active"]
                    await asyncio.wait_for(session.send_ping(), 5)

    asyncio.run(exercise())


@pytest.mark.parametrize("offline", [False, True])
def test_stdio_doctor_returns_after_worker_dispatch(tmp_path: Path, offline: bool) -> None:
    env = os.environ.copy()
    env["PYTHONPATH"] = str(SRC_DIR)
    env["TG_MCP_ROOT"] = str(tmp_path)
    if offline:
        env["TG_DOCTOR_OFFLINE"] = "1"
    else:
        env.pop("TG_DOCTOR_OFFLINE", None)
    server = StdioServerParameters(
        command=sys.executable,
        args=["-m", "tensor_grep", "mcp"],
        cwd=tmp_path,
        env=env,
        encoding="utf-8",
        encoding_error_handler="replace",
    )

    async def exercise() -> None:
        async with stdio_client(server) as (read, write):
            async with ClientSession(
                read, write, read_timeout_seconds=timedelta(seconds=30)
            ) as session:
                await session.initialize()
                doctor = await _call(
                    session,
                    "tg_explore",
                    {"action": "doctor", "path": str(tmp_path), "config": None, "with_lsp": False},
                    timeout=30,
                )
                assert "error" not in doctor, doctor
                assert doctor["doctor_schema_version"]
                assert doctor["root"] == str(tmp_path)
                if offline:
                    assert doctor["pypi_latest"] is None
                await asyncio.wait_for(session.send_ping(), 2)

    asyncio.run(exercise())
