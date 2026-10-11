"""Wire-level MCP error signaling for capabilities responses."""

import ast
import asyncio
import inspect
import json
import textwrap

import pytest
from mcp import types

from tensor_grep.cli import mcp_server


@pytest.mark.parametrize(
    ("body", "expected_error"),
    [
        pytest.param({"error": None}, False, id="null-error"),
        pytest.param({"error": {}}, False, id="empty-error"),
        pytest.param({"error": "failure"}, False, id="string-error"),
        pytest.param({"error": ["failure"]}, False, id="list-error"),
        pytest.param({"message": "success"}, False, id="missing-error"),
        pytest.param({"error": {"code": "invalid_input"}}, True, id="nonempty-error"),
    ],
)
def test_capabilities_error_shape_sets_wire_flag(monkeypatch, body, expected_error: bool) -> None:
    monkeypatch.setattr(mcp_server, "_mcp_capabilities_payload", lambda: body)

    result = asyncio.run(mcp_server.mcp.call_tool("tg_mcp_capabilities", {}))
    if not isinstance(result, types.CallToolResult):
        content, structured_content = result
        result = types.CallToolResult(content=list(content), structuredContent=structured_content)

    assert result.isError is expected_error
    payload = json.loads(result.content[0].text)
    if "error" in body:
        assert payload["error"] == body["error"]
    else:
        assert "error" not in payload


def test_capabilities_payload_patch_target_is_late_bound() -> None:
    tree = ast.parse(textwrap.dedent(inspect.getsource(mcp_server.tg_mcp_capabilities)))
    bare_calls = [
        node
        for node in ast.walk(tree)
        if isinstance(node, ast.Call)
        and isinstance(node.func, ast.Name)
        and node.func.id == "_mcp_capabilities_payload"
    ]

    assert bare_calls == []
