"""Explicit missing AST paths fail closed over both MCP tool names."""

from __future__ import annotations

import asyncio
import json
import os
import sys
from datetime import timedelta
from pathlib import Path

import pytest
from mcp import ClientSession
from mcp.client.stdio import StdioServerParameters, stdio_client

pytestmark = pytest.mark.integration
SRC_DIR = Path(__file__).resolve().parents[2] / "src"


def test_stdio_ast_missing_path_is_refused_and_session_recovers(tmp_path: Path) -> None:
    missing = tmp_path / "missing.py"
    env = os.environ.copy()
    env["PYTHONPATH"] = str(SRC_DIR)
    env["TG_MCP_ROOT"] = str(tmp_path)
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
                for name, arguments in (
                    ("tg_ast_search", {}),
                    ("tg_query", {"action": "ast"}),
                ):
                    result = await asyncio.wait_for(
                        session.call_tool(
                            name,
                            {
                                "pattern": "hub_fn($VALUE)",
                                "lang": "python",
                                "path": str(missing),
                                **arguments,
                            },
                        ),
                        30,
                    )
                    assert result.isError is False
                    payload = json.loads(result.content[0].text)
                    assert payload["error"]["code"] == "invalid_input"
                    assert "not found" in payload["error"]["message"].lower()
                    assert "matches" not in payload
                await asyncio.wait_for(session.send_ping(), 5)

    asyncio.run(exercise())
