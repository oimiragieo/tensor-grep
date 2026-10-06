"""Checkpoint annotations retain literal text through FastMCP argument validation."""

from __future__ import annotations

import asyncio
import json
from pathlib import Path
from typing import Any, cast
from unittest.mock import Mock

import pytest
from mcp.server.fastmcp import FastMCP
from mcp.server.fastmcp.exceptions import ToolError
from mcp.server.fastmcp.utilities.func_metadata import FuncMetadata

from tensor_grep.cli import checkpoint_store, mcp_server
from tensor_grep.cli.mcp_checkpoint_label_args import install_checkpoint_label_arguments


@pytest.fixture
def project(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    root = tmp_path / "project"
    root.mkdir()
    (root / "sample.py").write_text("value = 'before'\n", encoding="utf-8")
    monkeypatch.setenv("TG_MCP_ROOT", str(tmp_path))
    return root


def _call(tool: str, arguments: dict[str, object]) -> dict[str, Any]:
    content, _metadata = asyncio.run(mcp_server.mcp.call_tool(tool, arguments))
    return cast(dict[str, Any], json.loads(cast(Any, content)[0].text))


@pytest.mark.parametrize("tool", ["tg_checkpoint_create", "tg_checkpoint"])
@pytest.mark.parametrize(
    "label", ["null", "123", "true", '"quoted"', '["x"]', '{"a":1}', "再確認 🧭"]
)
def test_real_mcp_create_preserves_printable_label_strings(
    project: Path, tool: str, label: str
) -> None:
    arguments: dict[str, object] = {"path": str(project), "label": label}
    if tool == "tg_checkpoint":
        arguments["action"] = "create"
    payload = _call(tool, arguments)
    assert payload["label"] == label
    assert payload["mcp_contract_version"] == mcp_server._TG_MCP_SERVER_CONTRACT_VERSION
    records = checkpoint_store.list_checkpoints(str(project))
    assert len(records) == 1
    assert records[0].label == label
    index = json.loads(
        (project / ".tensor-grep/checkpoints/index.json").read_text(encoding="utf-8")
    )
    assert index[0]["label"] == label
    assert (
        checkpoint_store.load_checkpoint_metadata(records[0].checkpoint_id, str(project))["label"]
        == label
    )


@pytest.mark.parametrize("tool", ["tg_checkpoint_create", "tg_checkpoint"])
@pytest.mark.parametrize("label", [123, True, [], {}])
def test_real_mcp_nontext_labels_are_protocol_refusals_without_writes(
    project: Path,
    monkeypatch: pytest.MonkeyPatch,
    tool: str,
    label: object,
) -> None:
    checkpoint_store.create_checkpoint(str(project), label="baseline")
    index = project / ".tensor-grep/checkpoints/index.json"
    before = index.read_bytes()
    create = Mock(side_effect=AssertionError("protocol refusal must precede checkpoint creation"))
    monkeypatch.setattr(checkpoint_store, "create_checkpoint", create)
    arguments: dict[str, object] = {"path": str(project), "label": label}
    if tool == "tg_checkpoint":
        arguments["action"] = "create"
    with pytest.raises(ToolError, match="label"):
        asyncio.run(mcp_server.mcp.call_tool(tool, arguments))
    create.assert_not_called()
    assert index.read_bytes() == before


def test_real_legacy_invalid_label_never_echoes_unconfined_path(
    project: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    create = Mock(side_effect=AssertionError("invalid label must precede checkpoint creation"))
    monkeypatch.setattr(checkpoint_store, "create_checkpoint", create)
    payload = _call("tg_checkpoint_create", {"path": "~", "label": ""})
    assert payload["error"]["code"] == "invalid_input"
    assert payload["path"] == "[refused]"
    create.assert_not_called()
    assert not (project / ".tensor-grep").exists()


@pytest.mark.parametrize("action", ["list", "undo"])
@pytest.mark.parametrize("label", ["null", '["x"]', "123"])
def test_real_meta_labels_are_refused_before_list_or_undo_dispatch(
    project: Path,
    monkeypatch: pytest.MonkeyPatch,
    action: str,
    label: str,
) -> None:
    created = checkpoint_store.create_checkpoint(str(project), label="baseline")
    index = project / ".tensor-grep/checkpoints/index.json"
    before = index.read_bytes()
    dispatch = Mock(side_effect=AssertionError("labels must be rejected before action dispatch"))
    monkeypatch.setattr(mcp_server, f"tg_checkpoint_{action}", dispatch)
    payload = _call(
        "tg_checkpoint",
        {
            "action": action,
            "path": str(project),
            "checkpoint_id": created.checkpoint_id,
            "label": label,
        },
    )
    assert payload["error"]["code"] == "invalid_input"
    dispatch.assert_not_called()
    assert index.read_bytes() == before


def test_checkpoint_label_schema_remains_optional_nullable_with_default_none() -> None:
    tools = {tool.name: tool for tool in asyncio.run(mcp_server.mcp.list_tools())}
    for name in ("tg_checkpoint_create", "tg_checkpoint"):
        schema = tools[name].inputSchema
        assert {item["type"] for item in schema["properties"]["label"]["anyOf"]} == {
            "string",
            "null",
        }
        assert schema["properties"]["label"]["default"] is None
        assert "label" not in schema.get("required", [])


def test_label_adapter_leaves_other_argument_and_tool_parsing_unchanged() -> None:
    tool = mcp_server.mcp._tool_manager.get_tool("tg_checkpoint")
    assert tool is not None
    assert tool.fn_metadata.pre_parse_json({"label": "null", "checkpoint_id": "null"}) == {
        "label": "null",
        "checkpoint_id": None,
    }
    unrelated = mcp_server.mcp._tool_manager.get_tool("tg_rewrite")
    assert unrelated is not None
    assert type(unrelated.fn_metadata) is FuncMetadata


def test_adapter_supports_absent_legacy_tool_and_preserves_models_and_output_conversion() -> None:
    server = FastMCP("checkpoint-label-fixture")

    @server.tool()
    def tg_checkpoint(label: str | None = None) -> str:
        return json.dumps({"label": label})

    tool = server._tool_manager.get_tool("tg_checkpoint")
    assert tool is not None
    before = tool.fn_metadata
    content_before = asyncio.run(server.call_tool("tg_checkpoint", {"label": "literal"}))
    install_checkpoint_label_arguments(server)
    after = tool.fn_metadata
    assert after.arg_model is before.arg_model
    assert after.output_model is before.output_model
    assert after.output_schema == before.output_schema
    assert after.wrap_output == before.wrap_output
    assert asyncio.run(server.call_tool("tg_checkpoint", {"label": "literal"})) == content_before
    content, _metadata = asyncio.run(server.call_tool("tg_checkpoint", {"label": "null"}))
    assert json.loads(cast(Any, content)[0].text)["label"] == "null"
    install_checkpoint_label_arguments(server)
    assert tool.fn_metadata is after


def test_adapter_fails_closed_for_missing_meta_registration() -> None:
    with pytest.raises(RuntimeError, match="meta tool must be registered"):
        install_checkpoint_label_arguments(FastMCP("missing-checkpoint-fixture"))


def test_adapter_fails_closed_for_unknown_metadata_shape() -> None:
    class UnsupportedMetadata(FuncMetadata):
        unsupported_field: int = 1

    server = FastMCP("unsupported-checkpoint-fixture")

    @server.tool()
    def tg_checkpoint(label: str | None = None) -> str:
        return json.dumps({"label": label})

    tool = server._tool_manager.get_tool("tg_checkpoint")
    assert tool is not None
    tool.fn_metadata = UnsupportedMetadata(
        arg_model=tool.fn_metadata.arg_model,
        output_schema=tool.fn_metadata.output_schema,
        output_model=tool.fn_metadata.output_model,
        wrap_output=tool.fn_metadata.wrap_output,
    )
    with pytest.raises(RuntimeError, match="Unsupported FastMCP checkpoint argument metadata"):
        install_checkpoint_label_arguments(server)
