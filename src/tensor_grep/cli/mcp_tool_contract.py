"""Shared registration contracts for the FastMCP tool surface."""

import inspect
import json
from collections.abc import Callable
from functools import wraps
from typing import Annotated, Any, TypeVar

from mcp import types
from mcp.server.fastmcp import FastMCP
from pydantic import Field

ToolFunction = TypeVar("ToolFunction", bound=Callable[..., Any])

TG_NAVIGATE_ACTIONS = ("defs", "source", "refs", "callers", "imports", "importers")
TG_IMPACT_ACTIONS = ("impact", "blast_radius", "blast_radius_plan", "blast_radius_render")
TG_QUERY_ACTIONS = ("text", "search", "ast", "find", "index")
TG_CONTEXT_ACTIONS = ("pack", "edit_plan", "render", "capsule")
TG_EXPLORE_ACTIONS = ("orient", "repo_map", "doctor", "devices")
TG_SESSION_ACTIONS = (
    "open",
    "list",
    "show",
    "refresh",
    "context",
    "edit_plan",
    "context_render",
    "blast_radius",
    "blast_radius_plan",
    "blast_radius_render",
    "file_importers",
)
TG_SCAN_ACTIONS = ("scan", "rulesets")
TG_AUDIT_ACTIONS = ("manifest_verify", "history", "diff", "bundle_create", "bundle_verify")
TG_CHECKPOINT_ACTIONS = ("create", "list", "undo")
TG_REWRITE_ACTIONS = ("plan", "apply", "diff")

NavigateAction = Annotated[str, Field(json_schema_extra={"enum": list(TG_NAVIGATE_ACTIONS)})]
ImpactAction = Annotated[str, Field(json_schema_extra={"enum": list(TG_IMPACT_ACTIONS)})]
QueryAction = Annotated[str, Field(json_schema_extra={"enum": list(TG_QUERY_ACTIONS)})]
ContextAction = Annotated[str, Field(json_schema_extra={"enum": list(TG_CONTEXT_ACTIONS)})]
ExploreAction = Annotated[str, Field(json_schema_extra={"enum": list(TG_EXPLORE_ACTIONS)})]
SessionAction = Annotated[str, Field(json_schema_extra={"enum": list(TG_SESSION_ACTIONS)})]
ScanAction = Annotated[str, Field(json_schema_extra={"enum": list(TG_SCAN_ACTIONS)})]
AuditAction = Annotated[str, Field(json_schema_extra={"enum": list(TG_AUDIT_ACTIONS)})]
CheckpointAction = Annotated[str, Field(json_schema_extra={"enum": list(TG_CHECKPOINT_ACTIONS)})]
RewriteAction = Annotated[str, Field(json_schema_extra={"enum": list(TG_REWRITE_ACTIONS)})]


def _error_result(text: str) -> types.CallToolResult | None:
    try:
        payload = json.loads(text)
    except (json.JSONDecodeError, TypeError):
        return None
    if isinstance(payload, dict) and isinstance(payload.get("error"), dict) and payload["error"]:
        return types.CallToolResult(
            content=[types.TextContent(type="text", text=text)],
            structuredContent={"result": text},
            isError=True,
        )
    return None


def register_mcp_tool(server: FastMCP) -> Callable[[ToolFunction], ToolFunction]:
    """Register a tool while mapping top-level JSON errors to MCP's error flag."""

    def decorate(fn: ToolFunction) -> ToolFunction:
        if inspect.iscoroutinefunction(fn):

            @wraps(fn)
            async def async_wrapped(*args: Any, **kwargs: Any) -> Any:
                text = await fn(*args, **kwargs)
                return _error_result(text) or text

            server.tool()(async_wrapped)
        else:

            @wraps(fn)
            def wrapped(*args: Any, **kwargs: Any) -> Any:
                text = fn(*args, **kwargs)
                return _error_result(text) or text

            server.tool()(wrapped)
        return fn

    return decorate
