"""MCP confinement for state roots discovered from session paths."""

from pathlib import Path

from tensor_grep.cli.mcp_path_errors import PathConfinementError


def confine_mcp_session_path(path: str, session_id: str | None = None) -> str:
    """Confine the requested path and the state root the session store will select."""
    from tensor_grep.cli.mcp_server import _confine_mcp_path, _mcp_root
    from tensor_grep.cli.session_root import _resolve_root, _session_root_for_payload

    confined = _confine_mcp_path(path, label="path")
    state_root = (
        _session_root_for_payload(session_id, str(confined))
        if session_id is not None
        else _resolve_root(Path(confined))
    )
    # The guard and the later store lookup are separate filesystem observations.
    if not state_root.is_relative_to(_mcp_root().expanduser().resolve()):
        raise PathConfinementError("Discovered session state root must stay within the MCP root")
    return str(confined)
