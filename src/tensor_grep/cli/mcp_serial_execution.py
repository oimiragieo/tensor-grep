"""Run registered synchronous MCP tools without blocking the protocol loop."""

from __future__ import annotations

import asyncio
from concurrent.futures import ThreadPoolExecutor
from contextvars import copy_context
from functools import wraps
from threading import BoundedSemaphore, Event
from typing import Any

from mcp.server.fastmcp import FastMCP
from mcp.server.fastmcp.exceptions import ToolError

# A single worker preserves request order for stateful operations such as checkpoints.
# At most 64 calls can be active or queued. Cancellation marks a queued job to skip
# when its turn comes; an active job settles once before the next begins. Writes
# are never retried here.
_TOOL_WORKER = ThreadPoolExecutor(max_workers=1, thread_name_prefix="tg-mcp-tool")
_MAX_PENDING_TOOLS = 64
_TOOL_SLOTS = BoundedSemaphore(_MAX_PENDING_TOOLS)


def _consume_abandoned_result(future: asyncio.Future[Any]) -> None:
    # The caller is gone, but the active write must settle. Retrieve any error
    # so its abandoned Future does not emit an unhandled-exception warning.
    if not future.cancelled():
        future.exception()


def install_serial_tool_execution(server: FastMCP) -> None:
    """Adapt registered sync callables after FastMCP has built their wire schemas.

    The module-level Python functions remain synchronous and keep their signatures.
    FastMCP still parses and validates arguments before invoking the adapted callable.
    """
    for tool in server._tool_manager._tools.values():
        if tool.is_async:
            continue
        original = tool.fn

        @wraps(original)
        async def invoke(*args: Any, _original: Any = original, **kwargs: Any) -> Any:
            slots = _TOOL_SLOTS
            if not slots.acquire(blocking=False):
                raise ToolError("MCP tool worker is busy; retry after active requests settle.")
            loop = asyncio.get_running_loop()
            request_context = copy_context()
            cancelled = Event()

            def execute() -> Any:
                try:
                    if cancelled.is_set():
                        return None
                    return request_context.run(_original, *args, **kwargs)
                finally:
                    slots.release()

            try:
                job = loop.run_in_executor(_TOOL_WORKER, execute)
            except BaseException:
                slots.release()
                raise
            try:
                return await asyncio.shield(job)
            except asyncio.CancelledError:
                cancelled.set()
                job.add_done_callback(_consume_abandoned_result)
                raise

        tool.fn = invoke
        tool.is_async = True
