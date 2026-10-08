"""The MCP event loop remains live while a serialized tool owns the worker."""

from __future__ import annotations

import asyncio
import json
import threading
from pathlib import Path
from typing import Any

import pytest
from mcp.server.fastmcp.exceptions import ToolError

from tensor_grep.cli import checkpoint_store, mcp_serial_execution, mcp_server


def _payload(result: Any) -> dict[str, Any]:
    return json.loads(result[0][0].text)


def test_blocked_create_keeps_loop_live_and_cancelled_queue_never_writes(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    project = tmp_path / "project"
    project.mkdir()
    (project / "sample.py").write_bytes(b"before\n")
    monkeypatch.setenv("TG_MCP_ROOT", str(tmp_path))

    entered = threading.Event()
    responsive = threading.Event()
    release = threading.Event()
    observed: list[bool] = []
    original = checkpoint_store.create_checkpoint
    calls = 0

    def held_create(*args: Any, **kwargs: Any) -> Any:
        nonlocal calls
        calls += 1
        entered.set()
        observed.append(responsive.wait(5.0))
        assert release.wait(10.0)
        return original(*args, **kwargs)

    monkeypatch.setattr(checkpoint_store, "create_checkpoint", held_create)
    delegate = mcp_serial_execution._TOOL_WORKER
    queued = threading.Event()

    class SubmitProbe:
        submissions = 0

        def submit(self, *args: Any, **kwargs: Any) -> Any:
            future = delegate.submit(*args, **kwargs)
            self.submissions += 1
            if self.submissions == 2:
                queued.set()
            return future

    monkeypatch.setattr(mcp_serial_execution, "_TOOL_WORKER", SubmitProbe())

    async def exercise() -> None:
        first = asyncio.create_task(
            mcp_server.mcp.call_tool(
                "tg_checkpoint_create", {"path": str(project), "label": "first"}
            )
        )
        try:
            assert await asyncio.to_thread(entered.wait, 10.0)
            responsive.set()
            second = asyncio.create_task(
                mcp_server.mcp.call_tool(
                    "tg_checkpoint_create", {"path": str(project), "label": "cancelled"}
                )
            )
            assert await asyncio.to_thread(queued.wait, 10.0)
            second.cancel()
            with pytest.raises(asyncio.CancelledError):
                await second
            first.cancel()  # the active create still settles once
            with pytest.raises(asyncio.CancelledError):
                await first
        finally:
            release.set()

        listed = _payload(
            await mcp_server.mcp.call_tool("tg_checkpoint_list", {"path": str(project)})
        )
        assert [item["label"] for item in listed["checkpoints"]] == ["first"]

    asyncio.run(exercise())
    assert observed == [True], "the event loop was blocked by a synchronous MCP handler"
    assert calls == 1


def test_worker_admission_stays_bounded_until_cancelled_jobs_drain(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    project = tmp_path / "project"
    project.mkdir()
    (project / "sample.py").write_bytes(b"before\n")
    monkeypatch.setenv("TG_MCP_ROOT", str(tmp_path))
    monkeypatch.setattr(mcp_serial_execution, "_TOOL_SLOTS", threading.BoundedSemaphore(2))

    entered = threading.Event()
    release = threading.Event()
    submitted = threading.Event()
    drained = threading.Event()
    original = checkpoint_store.create_checkpoint
    calls = 0

    def held_create(*args: Any, **kwargs: Any) -> Any:
        nonlocal calls
        calls += 1
        entered.set()
        assert release.wait(10.0)
        return original(*args, **kwargs)

    monkeypatch.setattr(checkpoint_store, "create_checkpoint", held_create)
    delegate = mcp_serial_execution._TOOL_WORKER

    class SubmitProbe:
        submissions = 0

        def submit(self, *args: Any, **kwargs: Any) -> Any:
            future = delegate.submit(*args, **kwargs)
            self.submissions += 1
            if self.submissions == 2:
                submitted.set()
            return future

    monkeypatch.setattr(mcp_serial_execution, "_TOOL_WORKER", SubmitProbe())

    async def exercise() -> None:
        first = asyncio.create_task(
            mcp_server.mcp.call_tool("tg_checkpoint_create", {"path": str(project)})
        )
        try:
            assert await asyncio.to_thread(entered.wait, 10.0)
            second = asyncio.create_task(
                mcp_server.mcp.call_tool("tg_checkpoint_create", {"path": str(project)})
            )
            assert await asyncio.to_thread(submitted.wait, 10.0)
            with pytest.raises(ToolError, match="busy"):
                await mcp_server.mcp.call_tool("tg_checkpoint_list", {"path": str(project)})
            second.cancel()
            with pytest.raises(asyncio.CancelledError):
                await second
            # A cancelled queued job still holds its slot until the worker skips it.
            with pytest.raises(ToolError, match="busy"):
                await mcp_server.mcp.call_tool("tg_checkpoint_list", {"path": str(project)})
            first.cancel()
            with pytest.raises(asyncio.CancelledError):
                await first
        finally:
            release.set()

        delegate.submit(drained.set)
        assert await asyncio.to_thread(drained.wait, 10.0)
        listed = _payload(
            await mcp_server.mcp.call_tool("tg_checkpoint_list", {"path": str(project)})
        )
        assert len(listed["checkpoints"]) == 1

    asyncio.run(exercise())
    assert calls == 1
