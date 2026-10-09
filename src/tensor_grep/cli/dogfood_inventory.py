"""Bounded discovery receipts for the artifact's CLI and MCP registrations."""

from __future__ import annotations

import asyncio
import json
from typing import cast

import typer


def inventory() -> dict[str, object]:
    from tensor_grep.cli.main import app
    from tensor_grep.cli.mcp_server import _META_MCP_TOOL_CAPABILITIES, mcp

    def commands(application: typer.Typer) -> dict[str, object]:
        return {
            "commands": sorted(
                item.name or (item.callback.__name__ if item.callback else "unknown")
                for item in application.registered_commands
            ),
            "groups": {
                item.name: commands(item.typer_instance)
                for item in application.registered_groups
                if item.typer_instance is not None
            },
        }

    tools = asyncio.run(mcp.list_tools())
    return {
        "cli": commands(app),
        "mcp_tools": sorted(tool.name for tool in tools),
        "mcp_actions": {
            name: sorted(cast(dict[str, object], spec["actions"]))
            for name, spec in _META_MCP_TOOL_CAPABILITIES.items()
        },
    }


if __name__ == "__main__":
    print(json.dumps(inventory()))
