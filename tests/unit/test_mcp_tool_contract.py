"""MCP action schema and error signaling contracts."""

import asyncio
import json
from pathlib import Path

import pytest
from mcp import types

from tensor_grep.cli import mcp_server
from tensor_grep.cli.mcp_tool_contract import _error_result


def test_meta_action_schemas_match_runtime_action_tuples() -> None:
    action_tuples = {
        "tg_navigate": mcp_server._TG_NAVIGATE_ACTIONS,
        "tg_query": mcp_server._TG_QUERY_ACTIONS,
        "tg_impact": mcp_server._TG_IMPACT_ACTIONS,
        "tg_context": mcp_server._TG_CONTEXT_ACTIONS,
        "tg_explore": mcp_server._TG_EXPLORE_ACTIONS,
        "tg_session": mcp_server._TG_SESSION_ACTIONS,
        "tg_scan": mcp_server._TG_SCAN_ACTIONS,
        "tg_audit": mcp_server._TG_AUDIT_ACTIONS,
        "tg_checkpoint": mcp_server._TG_CHECKPOINT_ACTIONS,
        "tg_rewrite": mcp_server._TG_REWRITE_ACTIONS,
    }
    tools = {tool.name: tool for tool in asyncio.run(mcp_server.mcp.list_tools())}

    for name, valid_actions in action_tuples.items():
        schema = tools[name].inputSchema["properties"]["action"]
        assert schema["enum"] == list(valid_actions)


def _call_tool(name: str, arguments: dict[str, object]) -> types.CallToolResult:
    result = asyncio.run(mcp_server.mcp.call_tool(name, arguments))
    if isinstance(result, types.CallToolResult):
        return result
    content, structured_content = result
    return types.CallToolResult(content=list(content), structuredContent=structured_content)


def test_empty_search_pattern_sets_is_error_and_preserves_body() -> None:
    result = _call_tool("tg_search", {"pattern": "", "path": "."})
    expected = {
        "pattern": "",
        "path": str(Path.cwd().resolve()),
        "total_matches": 0,
        "total_files": 0,
        "rendered_match_count": 0,
        "rendered_file_count": 0,
        "matches": [],
        "truncated": False,
        "result_incomplete": True,
        "incomplete_reason": "either pattern or query is required.",
        "error": {
            "code": "invalid_input",
            "message": "either pattern or query is required.",
        },
        "mcp_contract_version": mcp_server._TG_MCP_SERVER_CONTRACT_VERSION,
        "schema_version": 1,
        "incomplete": {
            "status": True,
            "cause": "either pattern or query is required.",
            "budget_remediable": False,
        },
    }

    assert result.isError is True
    assert result.content[0].text == json.dumps(expected, indent=2)


def test_outside_root_search_sets_is_error(tmp_path: Path) -> None:
    result = _call_tool("tg_search", {"pattern": "needle", "path": str(tmp_path)})

    assert result.isError is True
    payload = json.loads(result.content[0].text)
    assert payload["error"]["code"] == "invalid_input"


def test_unknown_meta_action_sets_is_error_and_keeps_envelope() -> None:
    result = _call_tool("tg_navigate", {"action": "bogus", "symbol": "Foo"})

    assert result.isError is True
    assert result.content[0].text == json.dumps(
        {
            "version": 1,
            "mcp_contract_version": mcp_server._TG_MCP_SERVER_CONTRACT_VERSION,
            "routing_backend": "MCPMeta",
            "routing_reason": "tg_navigate:bogus",
            "sidecar_used": False,
            "tool": "tg_navigate",
            "action": "bogus",
            "error": {
                "code": "invalid_input",
                "message": (
                    "Unknown action 'bogus' for tg_navigate. "
                    "Valid actions: defs, source, refs, callers, imports, importers."
                ),
            },
        },
        indent=2,
    )


def test_successful_search_stays_non_error() -> None:
    result = _call_tool("tg_search", {"pattern": "KNOWN_COMMANDS", "path": "."})

    assert result.isError is False
    assert "error" not in json.loads(result.content[0].text)


def test_incomplete_response_without_error_stays_non_error(monkeypatch) -> None:
    monkeypatch.setattr(
        mcp_server,
        "_mcp_capabilities_payload",
        lambda: {"result_incomplete": True},
    )

    result = _call_tool("tg_mcp_capabilities", {})
    payload = json.loads(result.content[0].text)

    assert payload["result_incomplete"] is True
    assert "error" not in payload
    assert result.isError is False


@pytest.mark.parametrize(
    "response",
    [
        pytest.param('{"error": null}', id="null-error"),
        pytest.param('{"error": {}}', id="empty-error"),
        pytest.param('{"error": "failure"}', id="string-error"),
        pytest.param('{"error": ["failure"]}', id="list-error"),
        pytest.param('{"message": "success"}', id="missing-error"),
        pytest.param("not JSON", id="non-json"),
    ],
)
def test_error_result_requires_a_nonempty_top_level_error_dict(response: str) -> None:
    assert _error_result(response) is None


def test_error_result_marks_a_nonempty_top_level_error_dict() -> None:
    error_result = _error_result('{"error": {"code": "invalid_input"}}')
    assert error_result is not None
    assert error_result.isError is True
