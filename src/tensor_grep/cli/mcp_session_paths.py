"""MCP confinement for state roots discovered from session paths."""

from pathlib import Path

from tensor_grep.cli.mcp_path_errors import PathConfinementError


def confine_mcp_session_path(path: str) -> str:
    """Confine both the requested path and its project-anchored session state root."""
    from tensor_grep.cli.mcp_server import _confine_mcp_path, _mcp_root
    from tensor_grep.cli.session_root import _resolve_root

    confined = _confine_mcp_path(path, label="path")
    state_root = _resolve_root(Path(confined))
    if not state_root.is_relative_to(_mcp_root().expanduser().resolve()):
        raise PathConfinementError("Discovered session state root must stay within the MCP root")
    return str(confined)
