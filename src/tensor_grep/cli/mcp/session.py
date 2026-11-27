"""MCP session lifecycle and session-scoped context/blast-radius tools."""

from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import TYPE_CHECKING, Any, cast

from tensor_grep.cli import mcp_tool_contract as _tool_contract
from tensor_grep.cli.mcp.security import (
    _confine_mcp_path,
    _confine_read_path,
    _json_output_version,
    _log_tool_exception,
    _meta_confinement_error,
    _meta_envelope,
    _meta_missing_param_error,
    _meta_unknown_action_error,
    _sanitized_tool_error,
    _sanitized_tool_error_text,
)
from tensor_grep.cli.mcp.server import (
    _DEFAULT_MCP_CONTEXT_MAX_TOKENS,
    _DEFAULT_MCP_REPO_SCAN_LIMIT,
    _TG_MCP_SERVER_CONTRACT_VERSION,
    _register_legacy_tool,
    _register_tool,
)
from tensor_grep.cli.mcp_path_errors import PathConfinementError
from tensor_grep.cli.mcp_session_paths import (
    confine_mcp_session_path as _confine_mcp_session_path,
)
from tensor_grep.cli.repo_map import (
    _apply_context_token_budget,
)

AuditAction = _tool_contract.AuditAction
CheckpointAction = _tool_contract.CheckpointAction
ContextAction = _tool_contract.ContextAction
ExploreAction = _tool_contract.ExploreAction
ImpactAction = _tool_contract.ImpactAction
NavigateAction = _tool_contract.NavigateAction
QueryAction = _tool_contract.QueryAction
RewriteAction = _tool_contract.RewriteAction
ScanAction = _tool_contract.ScanAction
SessionAction = _tool_contract.SessionAction
_TG_AUDIT_ACTIONS = _tool_contract.TG_AUDIT_ACTIONS
_TG_CHECKPOINT_ACTIONS = _tool_contract.TG_CHECKPOINT_ACTIONS
_TG_CONTEXT_ACTIONS = _tool_contract.TG_CONTEXT_ACTIONS
_TG_EXPLORE_ACTIONS = _tool_contract.TG_EXPLORE_ACTIONS
_TG_IMPACT_ACTIONS = _tool_contract.TG_IMPACT_ACTIONS
_TG_NAVIGATE_ACTIONS = _tool_contract.TG_NAVIGATE_ACTIONS
_TG_QUERY_ACTIONS = _tool_contract.TG_QUERY_ACTIONS
_TG_REWRITE_ACTIONS = _tool_contract.TG_REWRITE_ACTIONS
_TG_SCAN_ACTIONS = _tool_contract.TG_SCAN_ACTIONS
_TG_SESSION_ACTIONS = _tool_contract.TG_SESSION_ACTIONS
register_mcp_tool = _tool_contract.register_mcp_tool

if TYPE_CHECKING:
    from tensor_grep.cli import mcp_server as _self
else:
    _self = sys.modules["tensor_grep.cli.mcp_server"]


def _effective_auto_refresh(refresh_on_stale: bool, auto_refresh: bool | None) -> bool:
    return bool(refresh_on_stale or auto_refresh)


def _session_error_payload(
    *,
    session_id: str,
    path: str,
    code: str,
    message: str,
    detail: dict[str, Any] | None = None,
    **extra: Any,
) -> str:
    payload: dict[str, Any] = {
        "version": _json_output_version(),
        "mcp_contract_version": _TG_MCP_SERVER_CONTRACT_VERSION,
        "session_id": session_id,
        "path": str(Path(path).expanduser()),
        **extra,
        "error": {
            "code": code,
            "message": message,
            "detail": detail or {},
        },
    }
    return json.dumps(payload, indent=2)


def _session_exception_payload(
    *,
    session_id: str | None = None,
    path: str,
    message: str,
    detail: dict[str, Any] | None = None,
    **extra: Any,
) -> str:
    payload: dict[str, Any] = {
        "version": _json_output_version(),
        "mcp_contract_version": _TG_MCP_SERVER_CONTRACT_VERSION,
        "path": str(Path(path).expanduser()),
        **extra,
        "error": {
            "code": "invalid_input",
            "message": message,
            "detail": detail or {},
        },
    }
    if session_id is not None:
        payload["session_id"] = session_id
    return json.dumps(payload, indent=2)


@_register_legacy_tool  # type: ignore
def tg_session_edit_plan(
    session_id: str,
    query: str,
    path: str = ".",
    max_files: int = 3,
    max_repo_files: int = _DEFAULT_MCP_REPO_SCAN_LIMIT,
    max_sources: int = 5,
    max_tokens: int | None = None,
    max_symbols: int = 5,
    refresh_on_stale: bool = False,
    auto_refresh: bool | None = None,
) -> str:
    """
    Return a cached-session edit-planning bundle without rendered source text.

    Args:
        session_id: Session ID to query.
        query: Query text used to rank edit targets.
        path: File or directory rooted at the session scope.
        max_files: Maximum files to include in the plan.
        max_repo_files: Maximum repository files to scan before ranking edit targets.
        max_sources: Maximum related source/span records to retain.
        max_tokens: Accepted for command-surface parity; no rendered source text is emitted.
        max_symbols: Maximum ranked symbols to retain.
    """
    try:
        from tensor_grep.cli.session_store import SessionStaleError, session_context_edit_plan

        # Confine the effective session state root before store access.
        try:
            path = str(_confine_mcp_session_path(path, session_id))
        except PathConfinementError as exc:
            return _session_error_payload(
                session_id=session_id,
                path="[refused]",
                code="invalid_input",
                message=str(exc),
                detail={"query": query, "max_files": max_files, "max_symbols": max_symbols},
                query=query,
            )

        effective_refresh = _effective_auto_refresh(refresh_on_stale, auto_refresh)
        try:
            # M14: session_store's helper dicts carry no MCP envelope -- stamp at the tool seam.
            return _self._inject_mcp_contract_fields(
                json.dumps(
                    session_context_edit_plan(
                        session_id,
                        query,
                        path,
                        max_files=max_files,
                        max_repo_files=max_repo_files,
                        max_sources=max_sources,
                        max_tokens=max_tokens,
                        max_symbols=max_symbols,
                        refresh_on_stale=effective_refresh,
                    ),
                    indent=2,
                )
            )
        except SessionStaleError as exc:
            _log_tool_exception("tg_session_edit_plan", exc)
            return _session_error_payload(
                session_id=session_id,
                path=path,
                code="invalid_input",
                message=f"Session cache is stale (cached session files changed on disk) for session '{session_id}'. Refresh session with tg_session_refresh.",
                detail={"query": query, "max_files": max_files, "max_symbols": max_symbols},
                query=query,
            )
        except FileNotFoundError as exc:
            _log_tool_exception("tg_session_edit_plan", exc)
            return _session_error_payload(
                session_id=session_id,
                path=path,
                code="invalid_input",
                message=f"Path not found: {path}",
                detail={"query": query, "max_files": max_files, "max_symbols": max_symbols},
                query=query,
            )
        except Exception as exc:  # M11: propagate as structured error, never a raw exception
            return _session_error_payload(
                session_id=session_id,
                path=path,
                code="internal_error",
                message=_sanitized_tool_error_text("tg_session_edit_plan", exc),
                detail={"query": query, "max_files": max_files, "max_symbols": max_symbols},
                query=query,
            )
    except Exception as exc:
        _log_tool_exception("tg_session_edit_plan", exc)
        return _sanitized_tool_error_text("tg_session_edit_plan", exc)


@_register_legacy_tool  # type: ignore
def tg_session_context_render(
    session_id: str,
    query: str,
    path: str = ".",
    max_files: int = 3,
    max_repo_files: int = _DEFAULT_MCP_REPO_SCAN_LIMIT,
    max_sources: int = 5,
    max_symbols_per_file: int = 6,
    max_render_chars: int | None = None,
    max_tokens: int | None = _DEFAULT_MCP_CONTEXT_MAX_TOKENS,
    model: str | None = None,
    optimize_context: bool = False,
    render_profile: str = "full",
    profile: bool = False,
    refresh_on_stale: bool = False,
    auto_refresh: bool | None = None,
) -> str:
    """
    Return a prompt-ready repository context bundle derived from a cached session.

    Args:
        session_id: Session ID to query.
        query: Query text used to rank and render repo context.
        path: File or directory rooted at the session scope.
        max_files: Maximum files to include in the render bundle.
        max_repo_files: Maximum cached repo files to score before rendering.
        max_sources: Maximum exact source blocks to include.
        max_symbols_per_file: Maximum summary symbols to include per file.
        max_render_chars: Maximum characters to emit in rendered_context.
        optimize_context: Strip blank lines and comment-only lines from rendered source blocks.
        render_profile: Render profile to use: full, compact, llm, or focused.
        max_tokens: Bundle bound (default ~16000; 0/None = unbounded).
    """
    try:
        from tensor_grep.cli.session_store import SessionStaleError, session_context_render

        # Confine the effective session state root before store access.
        try:
            path = str(_confine_mcp_session_path(path, session_id))
        except PathConfinementError as exc:
            return _session_error_payload(
                session_id=session_id,
                path="[refused]",
                code="invalid_input",
                message=str(exc),
                detail={"query": query, "render_profile": render_profile},
                query=query,
            )

        effective_refresh = _effective_auto_refresh(refresh_on_stale, auto_refresh)
        try:
            # M14: session_store's helper dicts carry no MCP envelope -- stamp at the tool seam.
            return _self._inject_mcp_contract_fields(
                json.dumps(
                    session_context_render(
                        session_id,
                        query,
                        path,
                        max_files=max_files,
                        max_repo_files=max_repo_files,
                        max_sources=max_sources,
                        max_symbols_per_file=max_symbols_per_file,
                        max_render_chars=max_render_chars,
                        max_tokens=max_tokens,
                        model=model,
                        optimize_context=optimize_context,
                        render_profile=render_profile,
                        profile=profile,
                        refresh_on_stale=effective_refresh,
                    ),
                    indent=2,
                )
            )
        except SessionStaleError as exc:
            _log_tool_exception("tg_session_context_render", exc)
            return _session_error_payload(
                session_id=session_id,
                path=path,
                code="invalid_input",
                message=f"Session cache is stale (cached session files changed on disk) for session '{session_id}'. Refresh session with tg_session_refresh.",
                detail={"query": query, "render_profile": render_profile},
                query=query,
            )
        except FileNotFoundError as exc:
            _log_tool_exception("tg_session_context_render", exc)
            return _session_error_payload(
                session_id=session_id,
                path=path,
                code="invalid_input",
                message=f"Path not found: {path}",
                detail={"query": query, "render_profile": render_profile},
                query=query,
            )
        except Exception as exc:  # M11: propagate as structured error, never a raw exception
            return _session_error_payload(
                session_id=session_id,
                path=path,
                code="internal_error",
                message=_sanitized_tool_error_text("tg_session_context_render", exc),
                detail={"query": query, "render_profile": render_profile},
                query=query,
            )
    except Exception as exc:
        _log_tool_exception("tg_session_context_render", exc)
        return _sanitized_tool_error_text("tg_session_context_render", exc)


@_register_legacy_tool  # type: ignore
def tg_session_blast_radius(
    session_id: str,
    symbol: str,
    path: str = ".",
    max_depth: int = 3,
    refresh_on_stale: bool = False,
    auto_refresh: bool | None = None,
) -> str:
    """
    Return a cached-session blast radius for a symbol.

    Args:
        session_id: Session ID to query.
        symbol: Exact symbol name to resolve.
        path: File or directory rooted at the session scope.
        max_depth: Maximum reverse-import depth to include.
    """
    try:
        from tensor_grep.cli.session_store import SessionStaleError, session_blast_radius

        # Confine the effective session state root before store access.
        try:
            path = str(_confine_mcp_session_path(path, session_id))
        except PathConfinementError as exc:
            return _session_error_payload(
                session_id=session_id,
                path="[refused]",
                code="invalid_input",
                message=str(exc),
                detail={"symbol": symbol, "max_depth": max(0, int(max_depth))},
                symbol=symbol,
                max_depth=max(0, int(max_depth)),
            )

        effective_refresh = _effective_auto_refresh(refresh_on_stale, auto_refresh)
        try:
            # M14: session_store's helper dicts carry no MCP envelope -- stamp at the tool seam.
            return _self._inject_mcp_contract_fields(
                json.dumps(
                    session_blast_radius(
                        session_id,
                        symbol,
                        path,
                        max_depth=max_depth,
                        refresh_on_stale=effective_refresh,
                    ),
                    indent=2,
                )
            )
        except SessionStaleError as exc:
            _log_tool_exception("tg_session_blast_radius", exc)
            return _session_error_payload(
                session_id=session_id,
                path=path,
                code="invalid_input",
                message=f"Session cache is stale (cached session files changed on disk) for session '{session_id}'. Refresh session with tg_session_refresh.",
                detail={"symbol": symbol, "max_depth": max(0, int(max_depth))},
                symbol=symbol,
                max_depth=max(0, int(max_depth)),
            )
        except FileNotFoundError as exc:
            _log_tool_exception("tg_session_blast_radius", exc)
            return _session_error_payload(
                session_id=session_id,
                path=path,
                code="invalid_input",
                message=f"Path not found: {path}",
                detail={"symbol": symbol, "max_depth": max(0, int(max_depth))},
                symbol=symbol,
                max_depth=max(0, int(max_depth)),
            )
        except Exception as exc:  # M11: propagate as structured error, never a raw exception
            return _session_error_payload(
                session_id=session_id,
                path=path,
                code="internal_error",
                message=_sanitized_tool_error_text("tg_session_blast_radius", exc),
                detail={"symbol": symbol, "max_depth": max(0, int(max_depth))},
                symbol=symbol,
                max_depth=max(0, int(max_depth)),
            )
    except Exception as exc:
        _log_tool_exception("tg_session_blast_radius", exc)
        return _sanitized_tool_error_text("tg_session_blast_radius", exc)


@_register_legacy_tool  # type: ignore
def tg_session_file_importers(
    session_id: str,
    file: str,
    path: str = ".",
    refresh_on_stale: bool = False,
    auto_refresh: bool | None = None,
) -> str:
    """
    Return a cached-session (zero-reparse) list of the files that import FILE.

    Args:
        session_id: Session ID to query.
        file: File to find importers of. Confined to the session root (``path``); a file
            that legitimately lives outside it must be copied in first (fail-closed, not a
            silent drop).
        path: File or directory rooted at the session scope.
    """
    try:
        from tensor_grep.cli.session_store import SessionStaleError, session_file_importers

        # round-8 security (audit #95 gate must-fix #3, LIVE VULN): confine path to the MCP root
        # BEFORE session_root below derives from it. Previously session_root = Path(path).resolve()
        # used the RAW caller-supplied path with NO confinement at all, so path="/etc" made
        # session_root="/etc" and the "confine file to session_root" check just below then let
        # file="/etc/passwd" straight through -- an arbitrary-directory-read primitive reachable
        # from any MCP client today. Confining path here (rather than re-anchoring file's
        # confinement below to cwd) is deliberate: it keeps a legitimate relative `file` working
        # when session_root != cwd -- see the comment on the file confinement immediately below.
        try:
            path = str(_confine_mcp_session_path(path, session_id))
        except PathConfinementError as exc:
            return _session_error_payload(
                session_id=session_id,
                path="[refused]",
                code="invalid_input",
                message=str(exc),
                detail={"file": "[refused]"},
                file="[refused]",
            )

        # round-7 security : confine file to the session root
        # (path) before any read, same class/rationale as tg_file_imports/tg_file_importers above.
        # Anchored to the session root rather than cwd because that is what session_file_importers
        # itself resolves a relative `file` against (build_file_importers_from_map joins it onto
        # the session's own repo_map root, not the MCP server process cwd) -- confining to cwd
        # here would refuse a legitimate relative `file` whenever the session root differs from cwd.
        session_root = Path(path).expanduser().resolve()
        if not session_root.is_dir():
            session_root = session_root.parent
        try:
            file = str(_confine_read_path(file, session_root, label="file"))
        except PathConfinementError as exc:
            return _session_error_payload(
                session_id=session_id,
                path=path,
                code="invalid_input",
                message=str(exc),
                detail={"file": "[refused]"},
                file="[refused]",
            )

        effective_refresh = _effective_auto_refresh(refresh_on_stale, auto_refresh)
        try:
            # M14: session_store's helper dicts carry no MCP envelope -- stamp at the tool seam.
            return _self._inject_mcp_contract_fields(
                json.dumps(
                    session_file_importers(
                        session_id,
                        file,
                        path,
                        refresh_on_stale=effective_refresh,
                    ),
                    indent=2,
                )
            )
        except SessionStaleError as exc:
            _log_tool_exception("tg_session_file_importers", exc)
            return _session_error_payload(
                session_id=session_id,
                path=path,
                code="invalid_input",
                message=f"Session cache is stale (cached session files changed on disk) for session '{session_id}'. Refresh session with tg_session_refresh.",
                detail={"file": file},
                file=file,
            )
        except FileNotFoundError as exc:
            _log_tool_exception("tg_session_file_importers", exc)
            return _session_error_payload(
                session_id=session_id,
                path=path,
                code="invalid_input",
                message=f"File not found: {file}",
                detail={"file": file},
                file=file,
            )
        except Exception as exc:  # propagate as structured error, never a raw exception
            return _session_error_payload(
                session_id=session_id,
                path=path,
                code="internal_error",
                message=_sanitized_tool_error_text("tg_session_file_importers", exc),
                detail={"file": file},
                file=file,
            )
    except Exception as exc:
        _log_tool_exception("tg_session_file_importers", exc)
        return _sanitized_tool_error_text("tg_session_file_importers", exc)


@_register_legacy_tool  # type: ignore
def tg_session_blast_radius_render(
    session_id: str,
    symbol: str,
    path: str = ".",
    max_depth: int = 3,
    max_files: int = 3,
    max_sources: int = 5,
    max_symbols_per_file: int = 6,
    max_render_chars: int | None = None,
    optimize_context: bool = False,
    render_profile: str = "full",
    refresh_on_stale: bool = False,
    auto_refresh: bool | None = None,
) -> str:
    """
    Return a prompt-ready cached-session blast radius bundle for a symbol.

    Args:
        session_id: Session ID to query.
        symbol: Exact symbol name to resolve.
        path: File or directory rooted at the session scope.
        max_depth: Maximum reverse-import depth to include.
        max_files: Maximum files to include in the render bundle.
        max_sources: Maximum exact source blocks to include.
        max_symbols_per_file: Maximum summary symbols to include per file.
        max_render_chars: Maximum characters to emit in rendered_context.
        optimize_context: Strip blank lines and comment-only lines from rendered source blocks.
        render_profile: Render profile to use: full, compact, llm, or focused.
    """
    try:
        from tensor_grep.cli.session_store import (
            SessionStaleError,
            session_blast_radius_render,
        )

        # Confine the effective session state root before store access.
        try:
            path = str(_confine_mcp_session_path(path, session_id))
        except PathConfinementError as exc:
            return _session_error_payload(
                session_id=session_id,
                path="[refused]",
                code="invalid_input",
                message=str(exc),
                detail={
                    "symbol": symbol,
                    "max_depth": max(0, int(max_depth)),
                    "render_profile": render_profile,
                },
                symbol=symbol,
                max_depth=max(0, int(max_depth)),
            )

        effective_refresh = _effective_auto_refresh(refresh_on_stale, auto_refresh)
        try:
            # M14: session_store's helper dicts carry no MCP envelope -- stamp at the tool seam.
            return _self._inject_mcp_contract_fields(
                json.dumps(
                    session_blast_radius_render(
                        session_id,
                        symbol,
                        path,
                        max_depth=max_depth,
                        max_files=max_files,
                        max_sources=max_sources,
                        max_symbols_per_file=max_symbols_per_file,
                        max_render_chars=max_render_chars,
                        optimize_context=optimize_context,
                        render_profile=render_profile,
                        refresh_on_stale=effective_refresh,
                    ),
                    indent=2,
                )
            )
        except SessionStaleError as exc:
            _log_tool_exception("tg_session_blast_radius_render", exc)
            return _session_error_payload(
                session_id=session_id,
                path=path,
                code="invalid_input",
                message=f"Session cache is stale (cached session files changed on disk) for session '{session_id}'. Refresh session with tg_session_refresh.",
                detail={
                    "symbol": symbol,
                    "max_depth": max(0, int(max_depth)),
                    "render_profile": render_profile,
                },
                symbol=symbol,
                max_depth=max(0, int(max_depth)),
            )
        except FileNotFoundError as exc:
            _log_tool_exception("tg_session_blast_radius_render", exc)
            return _session_error_payload(
                session_id=session_id,
                path=path,
                code="invalid_input",
                message=f"Path not found: {path}",
                detail={
                    "symbol": symbol,
                    "max_depth": max(0, int(max_depth)),
                    "render_profile": render_profile,
                },
                symbol=symbol,
                max_depth=max(0, int(max_depth)),
            )
        except Exception as exc:  # M11: propagate as structured error, never a raw exception
            return _session_error_payload(
                session_id=session_id,
                path=path,
                code="internal_error",
                message=_sanitized_tool_error_text("tg_session_blast_radius_render", exc),
                detail={
                    "symbol": symbol,
                    "max_depth": max(0, int(max_depth)),
                    "render_profile": render_profile,
                },
                symbol=symbol,
                max_depth=max(0, int(max_depth)),
            )
    except Exception as exc:
        _log_tool_exception("tg_session_blast_radius_render", exc)
        return _sanitized_tool_error_text("tg_session_blast_radius_render", exc)


@_register_legacy_tool  # type: ignore
def tg_session_blast_radius_plan(
    session_id: str,
    symbol: str,
    path: str = ".",
    max_depth: int = 3,
    max_files: int = 3,
    max_symbols: int = 5,
    refresh_on_stale: bool = False,
    auto_refresh: bool | None = None,
) -> str:
    """
    Return a cached-session blast-radius planning bundle without rendered source text.

    Args:
        session_id: Session ID to query.
        symbol: Exact symbol name to resolve.
        path: File or directory rooted at the session scope.
        max_depth: Maximum reverse-import depth to include.
        max_files: Maximum files to include in the plan.
        max_symbols: Maximum ranked symbols to retain.
    """
    try:
        from tensor_grep.cli.session_store import SessionStaleError, session_blast_radius_plan

        # Confine the effective session state root before store access.
        try:
            path = str(_confine_mcp_session_path(path, session_id))
        except PathConfinementError as exc:
            return _session_error_payload(
                session_id=session_id,
                path="[refused]",
                code="invalid_input",
                message=str(exc),
                detail={
                    "symbol": symbol,
                    "max_depth": max(0, int(max_depth)),
                    "max_files": max_files,
                    "max_symbols": max_symbols,
                },
                symbol=symbol,
                max_depth=max(0, int(max_depth)),
            )

        effective_refresh = _effective_auto_refresh(refresh_on_stale, auto_refresh)
        try:
            # M14: session_store's helper dicts carry no MCP envelope -- stamp at the tool seam.
            return _self._inject_mcp_contract_fields(
                json.dumps(
                    session_blast_radius_plan(
                        session_id,
                        symbol,
                        path,
                        max_depth=max_depth,
                        max_files=max_files,
                        max_symbols=max_symbols,
                        refresh_on_stale=effective_refresh,
                    ),
                    indent=2,
                )
            )
        except SessionStaleError as exc:
            _log_tool_exception("tg_session_blast_radius_plan", exc)
            return _session_error_payload(
                session_id=session_id,
                path=path,
                code="invalid_input",
                message=f"Session cache is stale (cached session files changed on disk) for session '{session_id}'. Refresh session with tg_session_refresh.",
                detail={
                    "symbol": symbol,
                    "max_depth": max(0, int(max_depth)),
                    "max_files": max_files,
                    "max_symbols": max_symbols,
                },
                symbol=symbol,
                max_depth=max(0, int(max_depth)),
            )
        except FileNotFoundError as exc:
            _log_tool_exception("tg_session_blast_radius_plan", exc)
            return _session_error_payload(
                session_id=session_id,
                path=path,
                code="invalid_input",
                message=f"Path not found: {path}",
                detail={
                    "symbol": symbol,
                    "max_depth": max(0, int(max_depth)),
                    "max_files": max_files,
                    "max_symbols": max_symbols,
                },
                symbol=symbol,
                max_depth=max(0, int(max_depth)),
            )
        except Exception as exc:  # M11: propagate as structured error, never a raw exception
            return _session_error_payload(
                session_id=session_id,
                path=path,
                code="internal_error",
                message=_sanitized_tool_error_text("tg_session_blast_radius_plan", exc),
                detail={
                    "symbol": symbol,
                    "max_depth": max(0, int(max_depth)),
                    "max_files": max_files,
                    "max_symbols": max_symbols,
                },
                symbol=symbol,
                max_depth=max(0, int(max_depth)),
            )
    except Exception as exc:
        _log_tool_exception("tg_session_blast_radius_plan", exc)
        return _sanitized_tool_error_text("tg_session_blast_radius_plan", exc)


@_register_legacy_tool  # type: ignore
def tg_session_open(
    path: str = ".", max_repo_files: int | None = _DEFAULT_MCP_REPO_SCAN_LIMIT
) -> str:
    """
    Create a cached repository-map session for repeated edit loops.

    Args:
        path: File or directory rooted at the session scope.
        max_repo_files: Optional cap for files scanned into the initial session repo map.
            Defaults to 2000 for agent-safe cold opens.
    """
    # round-8 security (audit #95 gate): confine the primary path/root param to the MCP root
    # before opening a session rooted there -- see tg_repo_map for the systemic-finding
    # rationale. A session persists a cached repo-map keyed to `path`, so unconfined this was
    # also an arbitrary-directory-read primitive (the cached repo_map content is later
    # returned verbatim by tg_session_show/tg_session_context/etc.).
    try:
        try:
            path = str(_confine_mcp_session_path(path))
        except PathConfinementError as exc:
            return _session_exception_payload(path="[refused]", message=str(exc), detail={})

        from tensor_grep.cli.session_store import get_session, open_session

        try:
            result = open_session(path, max_repo_files=max_repo_files)
        except Exception as exc:
            return _session_exception_payload(
                path=path, message=_sanitized_tool_error_text("open_session", exc), detail={}
            )

        degraded: dict[str, object]  # M13: tracked_file_count counts source + TEST files
        try:
            session_payload = get_session(result.session_id, path)
            repo_map = session_payload.get("repo_map") or {}
            related_paths = repo_map.get("related_paths") or []
            tracked_file_count, degraded = len(related_paths), {}
        except Exception as exc:  # W1-a: was a SILENT-SWALLOW (see the disposition ledger)
            tracked_file_count, degraded = (
                result.file_count,
                {"tracked_file_count_error": _sanitized_tool_error_text("get_session", exc)},
            )

        return _self._inject_mcp_contract_fields(
            json.dumps(
                {
                    "version": _json_output_version(),
                    "mcp_contract_version": _TG_MCP_SERVER_CONTRACT_VERSION,
                    "schema_version": _json_output_version(),
                    **result.__dict__,
                    "tracked_file_count": tracked_file_count,
                    **degraded,
                },
                indent=2,
            )
        )
    except Exception as exc:
        _log_tool_exception("tg_session_open", exc)
        return _sanitized_tool_error_text("tg_session_open", exc)


@_register_legacy_tool  # type: ignore
def tg_session_list(path: str = ".") -> str:
    """
    List cached sessions for the current root.

    Args:
        path: File or directory rooted at the session scope.
    """
    # round-8 security (audit #95 gate): confine the primary path/root param to the MCP root
    # before any read -- see tg_repo_map for the systemic-finding rationale.
    try:
        try:
            path = str(_confine_mcp_session_path(path))
        except PathConfinementError as exc:
            return _session_exception_payload(path="[refused]", message=str(exc), detail={})

        from tensor_grep.cli.session_store import list_sessions

        try:
            sessions = [record.__dict__ for record in list_sessions(path)]
        except Exception as exc:
            return _session_exception_payload(
                path=path, message=_sanitized_tool_error_text("list_sessions", exc), detail={}
            )

        return _self._inject_mcp_contract_fields(
            json.dumps(
                {
                    "version": _json_output_version(),
                    "mcp_contract_version": _TG_MCP_SERVER_CONTRACT_VERSION,
                    "sessions": sessions,
                },
                indent=2,
            )
        )
    except Exception as exc:
        _log_tool_exception("tg_session_list", exc)
        return _sanitized_tool_error_text("tg_session_list", exc)


@_register_legacy_tool  # type: ignore
def tg_session_show(session_id: str, path: str = ".") -> str:
    """
    Return the cached repository-map payload for a session.

    Args:
        session_id: Session ID to inspect.
        path: File or directory rooted at the session scope.
    """
    # round-8 security (audit #95 gate): confine the primary path/root param to the MCP root
    # before any read -- see tg_repo_map for the systemic-finding rationale.
    try:
        try:
            path = str(_confine_mcp_session_path(path, session_id))
        except PathConfinementError as exc:
            return _session_exception_payload(
                session_id=session_id,
                path="[refused]",
                message=str(exc),
                detail={},
            )

        from tensor_grep.cli.session_store import get_session

        try:
            payload = get_session(session_id, path)
        except Exception as exc:
            return _session_exception_payload(
                session_id=session_id,
                path=path,
                message=_sanitized_tool_error_text("get_session", exc),
                detail={},
            )

        return _self._inject_mcp_contract_fields(json.dumps(payload, indent=2))
    except Exception as exc:
        _log_tool_exception("tg_session_show", exc)
        return _sanitized_tool_error_text("tg_session_show", exc)


@_register_legacy_tool  # type: ignore
def tg_session_refresh(session_id: str, path: str = ".") -> str:
    """
    Refresh a cached repository-map session after file changes.

    Args:
        session_id: Session ID to refresh.
        path: File or directory rooted at the session scope.
    """
    # round-8 security (audit #95 gate): confine the primary path/root param to the MCP root
    # before any read -- see tg_repo_map for the systemic-finding rationale.
    try:
        try:
            path = str(_confine_mcp_session_path(path, session_id))
        except PathConfinementError as exc:
            return _session_exception_payload(
                session_id=session_id,
                path="[refused]",
                message=str(exc),
                detail={},
            )

        from tensor_grep.cli.session_store import refresh_session

        try:
            payload = refresh_session(session_id, path)
        except Exception as exc:
            return _session_exception_payload(
                session_id=session_id,
                path=path,
                message=_sanitized_tool_error_text("refresh_session", exc),
                detail={},
            )

        # M14: the refreshed-session payload crossed the wire un-stamped.
        return _self._inject_mcp_contract_fields(json.dumps(payload.__dict__, indent=2))
    except Exception as exc:
        _log_tool_exception("tg_session_refresh", exc)
        return _sanitized_tool_error_text("tg_session_refresh", exc)


@_register_legacy_tool  # type: ignore
def tg_session_context(
    session_id: str,
    query: str,
    path: str = ".",
    refresh_on_stale: bool = False,
    auto_refresh: bool | None = None,
    max_tokens: int | None = _DEFAULT_MCP_CONTEXT_MAX_TOKENS,
) -> str:
    """
    Return a context pack derived from a cached session.

    Args:
        session_id: Session ID to query.
        query: Query text used to rank relevant repo context.
        path: File or directory rooted at the session scope.
        max_tokens: Bound the pack for prompt injection (default ~16000; 0/None = unbounded).
    """
    # round-8 security (audit #95 gate): confine the primary path/root param to the MCP root
    # before any read -- see tg_repo_map for the systemic-finding rationale.
    try:
        try:
            path = str(_confine_mcp_session_path(path, session_id))
        except PathConfinementError as exc:
            return _session_error_payload(
                session_id=session_id,
                path="[refused]",
                code="invalid_input",
                message=str(exc),
                detail={"query": query},
                query=query,
            )

        from tensor_grep.cli.session_store import SessionStaleError, session_context

        effective_refresh = _effective_auto_refresh(refresh_on_stale, auto_refresh)
        try:
            payload = session_context(session_id, query, path, refresh_on_stale=effective_refresh)
            # H4 : every sibling context tool (`tg_context_pack`,
            # `tg_context_render`, `tg_agent_capsule`, the session render/edit-plan family) bounds
            # its output by `max_tokens`; this tool called `session_context` ->
            # `build_context_pack_from_map` with NO bound at all (dogfood 1.27.0: unbounded at
            # ~557KB/384 files -- the exact regression `session context --daemon` hit on the CLI,
            # main.py's `_apply_context_token_budget` call). Port the same post-processing step
            # here (imported from repo_map.py, not reimplemented) since `session_store.py` is out
            # of scope for this fix.
            payload = _apply_context_token_budget(payload, max_tokens)
        except SessionStaleError as exc:
            _log_tool_exception("tg_session_context", exc)
            return _session_error_payload(
                session_id=session_id,
                path=path,
                code="invalid_input",
                message=f"Session cache is stale (cached session files changed on disk) for session '{session_id}'. Refresh session with tg_session_refresh.",
                detail={"query": query},
                query=query,
            )
        except FileNotFoundError as exc:
            _log_tool_exception("tg_session_context", exc)
            return _session_error_payload(
                session_id=session_id,
                path=path,
                code="invalid_input",
                message=f"Path not found: {path}",
                detail={"query": query},
                query=query,
            )
        except Exception as exc:
            return _session_exception_payload(
                session_id=session_id,
                path=path,
                message=_sanitized_tool_error_text("session_context", exc),
                detail={"query": query},
                query=query,
            )

        return _self._inject_mcp_contract_fields(json.dumps(payload, indent=2))
    except Exception as exc:
        _log_tool_exception("tg_session_context", exc)
        return _sanitized_tool_error_text("tg_session_context", exc)


_TG_SESSION_ACTIONS_NEEDING_ID = frozenset(_TG_SESSION_ACTIONS) - {"open", "list"}


@_register_tool  # type: ignore
def tg_session(
    action: SessionAction,
    session_id: str | None = None,
    query: str | None = None,
    symbol: str | None = None,
    file: str | None = None,
    path: str = ".",
    max_repo_files: int = _DEFAULT_MCP_REPO_SCAN_LIMIT,
    max_files: int = 3,
    max_sources: int = 5,
    max_symbols: int = 5,
    max_symbols_per_file: int = 6,
    max_render_chars: int | None = None,
    max_tokens: int | None = None,
    model: str | None = None,
    optimize_context: bool = False,
    render_profile: str = "full",
    profile: bool = False,
    max_depth: int = 3,
    refresh_on_stale: bool = False,
    auto_refresh: bool | None = None,
) -> str:
    """Cached repo-map sessions: open, list; or with session_id: show, refresh, context, edit_plan, context_render, blast_radius*, file_importers."""
    try:
        try:
            path = str(_confine_mcp_session_path(path))
            if file is not None:
                file = str(_confine_mcp_path(file, label="file"))
        except PathConfinementError as exc:
            return _meta_confinement_error("tg_session", action, exc)

        if action in _TG_SESSION_ACTIONS_NEEDING_ID and session_id is None:
            return _meta_missing_param_error("tg_session", action, "session_id")

        try:
            if action == "open":
                return _self.tg_session_open(path=path, max_repo_files=max_repo_files)
            if action == "list":
                return _self.tg_session_list(path=path)
            if action == "show":
                return _self.tg_session_show(session_id=cast(str, session_id), path=path)
            if action == "refresh":
                return _self.tg_session_refresh(session_id=cast(str, session_id), path=path)
            if action == "context":
                if query is None:
                    return _meta_missing_param_error("tg_session", action, "query")
                max_tokens_kwargs: dict[str, Any] = (
                    {} if max_tokens is None else {"max_tokens": max_tokens}
                )
                return _self.tg_session_context(
                    session_id=cast(str, session_id),
                    query=query,
                    path=path,
                    refresh_on_stale=refresh_on_stale,
                    auto_refresh=auto_refresh,
                    **max_tokens_kwargs,
                )
            if action == "edit_plan":
                if query is None:
                    return _meta_missing_param_error("tg_session", action, "query")
                return _self.tg_session_edit_plan(
                    session_id=cast(str, session_id),
                    query=query,
                    path=path,
                    max_files=max_files,
                    max_repo_files=max_repo_files,
                    max_sources=max_sources,
                    max_tokens=max_tokens,
                    max_symbols=max_symbols,
                    refresh_on_stale=refresh_on_stale,
                    auto_refresh=auto_refresh,
                )
            if action == "context_render":
                if query is None:
                    return _meta_missing_param_error("tg_session", action, "query")
                max_tokens_kwargs = {} if max_tokens is None else {"max_tokens": max_tokens}
                return _self.tg_session_context_render(
                    session_id=cast(str, session_id),
                    query=query,
                    path=path,
                    max_files=max_files,
                    max_repo_files=max_repo_files,
                    max_sources=max_sources,
                    max_symbols_per_file=max_symbols_per_file,
                    max_render_chars=max_render_chars,
                    model=model,
                    optimize_context=optimize_context,
                    render_profile=render_profile,
                    profile=profile,
                    refresh_on_stale=refresh_on_stale,
                    auto_refresh=auto_refresh,
                    **max_tokens_kwargs,
                )
            if action == "blast_radius":
                if symbol is None:
                    return _meta_missing_param_error("tg_session", action, "symbol")
                return _self.tg_session_blast_radius(
                    session_id=cast(str, session_id),
                    symbol=symbol,
                    path=path,
                    max_depth=max_depth,
                    refresh_on_stale=refresh_on_stale,
                    auto_refresh=auto_refresh,
                )
            if action == "blast_radius_plan":
                if symbol is None:
                    return _meta_missing_param_error("tg_session", action, "symbol")
                return _self.tg_session_blast_radius_plan(
                    session_id=cast(str, session_id),
                    symbol=symbol,
                    path=path,
                    max_depth=max_depth,
                    max_files=max_files,
                    max_symbols=max_symbols,
                    refresh_on_stale=refresh_on_stale,
                    auto_refresh=auto_refresh,
                )
            if action == "blast_radius_render":
                if symbol is None:
                    return _meta_missing_param_error("tg_session", action, "symbol")
                return _self.tg_session_blast_radius_render(
                    session_id=cast(str, session_id),
                    symbol=symbol,
                    path=path,
                    max_depth=max_depth,
                    max_files=max_files,
                    max_sources=max_sources,
                    max_symbols_per_file=max_symbols_per_file,
                    max_render_chars=max_render_chars,
                    optimize_context=optimize_context,
                    render_profile=render_profile,
                    refresh_on_stale=refresh_on_stale,
                    auto_refresh=auto_refresh,
                )
            if action == "file_importers":
                if file is None:
                    return _meta_missing_param_error("tg_session", action, "file")
                return _self.tg_session_file_importers(
                    session_id=cast(str, session_id),
                    file=file,
                    path=path,
                    refresh_on_stale=refresh_on_stale,
                    auto_refresh=auto_refresh,
                )
            return _meta_unknown_action_error("tg_session", action, _TG_SESSION_ACTIONS)
        except Exception as exc:
            payload = _meta_envelope(tool="tg_session", action=action)
            payload["error"] = _sanitized_tool_error("tg_session", exc)
            return json.dumps(payload, indent=2)
    except Exception as exc:
        _log_tool_exception("tg_session", exc)
        return _sanitized_tool_error_text("tg_session", exc)
