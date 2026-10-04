"""Dependency-neutral MCP path-confinement error.

Lives in its own module so ``mcp_artifact_guard`` can subclass it without importing
``mcp_server`` (which imports ``mcp_audit_tools``, which imports the guard): that cycle made a cold
``from tensor_grep.cli.mcp_artifact_guard import ...`` fail. ``mcp_server`` re-exports the class,
so every existing ``mcp_server.PathConfinementError`` reference keeps working.
"""

from __future__ import annotations


class PathConfinementError(ValueError):
    """Raised when a path escapes the allowed MCP root anchor."""

    def __init__(self, label: str):
        super().__init__(f"{label} must stay within the MCP root (refused)")
