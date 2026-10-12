"""MCP adapter for the read-only coverage report."""

from __future__ import annotations

import json
from collections.abc import Callable
from pathlib import Path


def _register_legacy_tool(fn: Callable[..., str]) -> Callable[..., str]:
    """Decorator for the 47 legacy (pre-consolidation) MCP tool functions (#98).

    Registers `fn` through the shared MCP tool wrapper only when `_legacy_tools_enabled()` is
    True at IMPORT time. Both paths return `fn` unchanged: the shared wrapper only
    registers the function, and the 10 meta-tools dispatch directly to these Python
    callables even when their legacy names are de-advertised from `list_tools()`.

    Evaluated once per decoration (import time) so registration and `_MCP_TOOL_CAPABILITIES`
    share the SAME flag read. Each registered tool's description gains a covering-meta-tool
    note after the meta tools are defined (see `_LEGACY_NOTE`).
    """
    from tensor_grep.cli import mcp_server as server

    return server._register_legacy_tool(fn)


def coverage_tool(path: str) -> str:
    """Report repo-map session and AST-cache coverage for PATH without writing state."""
    from tensor_grep.cli import mcp_server as server

    try:
        try:
            path = str(server._confine_mcp_path(path, label="path"))
        except server.PathConfinementError as exc:
            payload = server._envelope_base(
                routing_backend="Coverage",
                routing_reason="coverage-inventory",
                include_schema_version=False,
            )
            payload["path"] = "[refused]"
            payload["error"] = {"code": "invalid_input", "message": str(exc)}
            return json.dumps(payload, indent=2)
        from tensor_grep.cli.coverage import check_coverage

        payload = check_coverage(Path(path), confinement_root=server._mcp_root())
        if not payload["complete"]:
            cause = (
                "scan_limit"
                if "max_repo_files" in payload["caps"]
                else "unreadable_path"
                if "unreadable_paths" in payload["caps"]
                else "partial"
            )
            payload["incomplete"] = {
                "status": True,
                "cause": cause,
                "budget_remediable": cause == "scan_limit",
            }
        return server._inject_mcp_contract_fields(json.dumps(payload, indent=2))
    except Exception as exc:
        server._log_tool_exception("tg_coverage", exc)
        payload = server._envelope_base(
            routing_backend="Coverage",
            routing_reason="coverage-inventory",
            include_schema_version=False,
        )
        payload["path"] = str(Path(path).expanduser())
        payload["error"] = server._sanitized_tool_error("tg_coverage", exc)
        return json.dumps(payload, indent=2)
