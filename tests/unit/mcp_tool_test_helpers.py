"""Small compatibility helpers for in-process FastMCP response assertions."""

import asyncio
from typing import Any

from mcp import types


def call_mcp_tool_parts(awaitable: Any) -> tuple[Any, Any]:
    result = asyncio.run(awaitable)
    if isinstance(result, types.CallToolResult):
        assert result.is_error is True
        return result.content, result.structured_content
    content, data = result
    return content, data
