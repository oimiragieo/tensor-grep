"""Preserve literal checkpoint annotations before FastMCP argument validation."""

from __future__ import annotations

from typing import Any

from mcp.server.fastmcp import FastMCP
from mcp.server.fastmcp.utilities.func_metadata import FuncMetadata


class _CheckpointLabelMetadata(FuncMetadata):
    def pre_parse_json(self, data: dict[str, Any]) -> dict[str, Any]:
        # Optional str annotations otherwise cause FastMCP to parse "null" and
        # JSON-shaped text before the handler can validate the original annotation.
        parsed = super().pre_parse_json({
            key: value for key, value in data.items() if key != "label"
        })
        if "label" in data:
            parsed["label"] = data["label"]
        return parsed


def install_checkpoint_label_arguments(server: FastMCP) -> None:
    """Adapt only the two checkpoint label fields, retaining their models and outputs."""
    expected_fields = {"arg_model", "output_schema", "output_model", "wrap_output"}
    for name in ("tg_checkpoint_create", "tg_checkpoint"):
        tool = server._tool_manager.get_tool(name)
        if tool is None:
            if name == "tg_checkpoint_create":
                continue  # Legacy names can be intentionally disabled.
            raise RuntimeError("Checkpoint meta tool must be registered before label adaptation.")
        metadata = tool.fn_metadata
        if isinstance(metadata, _CheckpointLabelMetadata):
            continue
        if type(metadata) is not FuncMetadata or set(FuncMetadata.model_fields) != expected_fields:
            raise RuntimeError("Unsupported FastMCP checkpoint argument metadata.")
        label_field = metadata.arg_model.model_fields.get("label")
        if label_field is None or label_field.alias is not None or label_field.default is not None:
            raise RuntimeError("Unsupported FastMCP checkpoint label field.")
        tool.fn_metadata = _CheckpointLabelMetadata(
            arg_model=metadata.arg_model,
            output_schema=metadata.output_schema,
            output_model=metadata.output_model,
            wrap_output=metadata.wrap_output,
        )
