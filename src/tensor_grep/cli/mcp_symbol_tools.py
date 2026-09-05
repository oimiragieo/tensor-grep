"""MCP tool family: pure symbol-graph navigation (defs/source/impact/refs/callers,
file imports/importers, symbol blast-radius).

Split out of mcp_server.py (docs/design/2026-08-19-split-floor-escape.md, Route A) as a
pure code move: no wire-surface change. See mcp_rewrite_tools.py module docstring for
the full rationale of the _self-points-at-mcp_server pattern used here.
"""

import json
import sys
from pathlib import Path
from typing import TYPE_CHECKING

from tensor_grep.cli.repo_map import build_file_imports

if TYPE_CHECKING:
    from tensor_grep.cli import mcp_server as _self
else:
    _self = sys.modules["tensor_grep.cli.mcp_server"]

from tensor_grep.cli.mcp_server import (
    _DEFAULT_MCP_REPO_SCAN_LIMIT as _DEFAULT_MCP_REPO_SCAN_LIMIT,
)
from tensor_grep.cli.mcp_server import (
    PathConfinementError as PathConfinementError,
)
from tensor_grep.cli.mcp_server import (
    _confine_mcp_path as _confine_mcp_path,
)
from tensor_grep.cli.mcp_server import (
    _confine_read_path as _confine_read_path,
)
from tensor_grep.cli.mcp_server import (
    _envelope_base as _envelope_base,
)
from tensor_grep.cli.mcp_server import (
    _log_tool_exception as _log_tool_exception,
)
from tensor_grep.cli.mcp_server import (
    _mcp_root as _mcp_root,
)
from tensor_grep.cli.mcp_server import (
    _register_legacy_tool as _register_legacy_tool,
)
from tensor_grep.cli.mcp_server import (
    _sanitized_tool_error as _sanitized_tool_error,
)
from tensor_grep.cli.mcp_server import (
    _sanitized_tool_error_text as _sanitized_tool_error_text,
)


@_register_legacy_tool  # type: ignore
def tg_symbol_blast_radius_plan(
    symbol: str,
    path: str = ".",
    max_depth: int = 3,
    max_files: int = 3,
    max_symbols: int = 5,
    provider: str = "native",
    max_repo_files: int = _DEFAULT_MCP_REPO_SCAN_LIMIT,
) -> str:
    """
    Return a machine-readable blast-radius planning bundle without rendered source text.

    Args:
        symbol: Exact symbol name to resolve.
        path: File or directory to inventory.
        max_depth: Maximum reverse-import depth to include.
        max_files: Maximum files to include in the plan.
        max_symbols: Maximum ranked symbols to retain.
        max_repo_files: Maximum repository files to scan before resolving the symbol.
    """
    try:
        from tensor_grep.cli.repo_map import build_symbol_blast_radius_plan

        # round-8 security (audit #95 gate): confine the primary path/root param to the MCP root
        # before any scan -- see tg_repo_map for the systemic-finding rationale.
        try:
            path = str(_confine_mcp_path(path, label="path"))
        except PathConfinementError as exc:
            payload = _envelope_base(
                routing_backend="RepoMap",
                routing_reason="symbol-blast-radius-plan",
                include_schema_version=False,
            )
            payload["symbol"] = symbol
            payload["max_depth"] = max(0, int(max_depth))
            payload["path"] = "[refused]"
            payload["error"] = {"code": "invalid_input", "message": str(exc)}
            return _self._inject_mcp_contract_fields(json.dumps(payload, indent=2))

        try:
            # M14: build_symbol_blast_radius_plan returns a bare dict crossed the wire exactly;
            # the injector stamps (and re-serializes) the final JSON like every tool envelope.
            return _self._inject_mcp_contract_fields(
                json.dumps(
                    build_symbol_blast_radius_plan(
                        symbol,
                        path,
                        max_depth=max_depth,
                        max_files=max_files,
                        max_symbols=max_symbols,
                        semantic_provider=provider,
                        max_repo_files=max_repo_files,
                    ),
                    indent=2,
                )
            )
        except FileNotFoundError as exc:
            _log_tool_exception("tg_symbol_blast_radius_plan", exc)
            payload = _envelope_base(
                routing_backend="RepoMap",
                routing_reason="symbol-blast-radius-plan",
                include_schema_version=False,
            )
            payload["symbol"] = symbol
            payload["max_depth"] = max(0, int(max_depth))
            payload["path"] = str(Path(path).expanduser())
            payload["error"] = {
                "code": "invalid_input",
                "message": f"Path not found: {path}",
            }
            return _self._inject_mcp_contract_fields(json.dumps(payload, indent=2))
        except Exception as exc:  # M11: propagate as structured error, never a raw exception
            payload = _envelope_base(
                routing_backend="RepoMap",
                routing_reason="symbol-blast-radius-plan",
                include_schema_version=False,
            )
            payload["symbol"] = symbol
            payload["max_depth"] = max(0, int(max_depth))
            payload["path"] = str(Path(path).expanduser())
            payload["error"] = _sanitized_tool_error("tg_symbol_blast_radius_plan", exc)
            return _self._inject_mcp_contract_fields(json.dumps(payload, indent=2))
    except Exception as exc:
        _log_tool_exception("tg_symbol_blast_radius_plan", exc)
        return _sanitized_tool_error_text("tg_symbol_blast_radius_plan", exc)


@_register_legacy_tool  # type: ignore
def tg_symbol_defs(
    symbol: str,
    path: str = ".",
    provider: str = "native",
    max_repo_files: int = _DEFAULT_MCP_REPO_SCAN_LIMIT,
) -> str:
    """
    Return exact definition locations for a symbol.

    Args:
        symbol: Exact symbol name to resolve.
        path: File or directory to inventory.
        max_repo_files: Maximum repository files to scan before resolving the symbol.
    """
    # round-8 security (audit #95 gate): confine the primary path/root param to the MCP root
    # before any scan -- see tg_repo_map for the systemic-finding rationale.
    try:
        try:
            path = str(_confine_mcp_path(path, label="path"))
        except PathConfinementError as exc:
            payload = _envelope_base(
                routing_backend="RepoMap",
                routing_reason="symbol-defs",
                include_schema_version=False,
            )
            payload["symbol"] = symbol
            payload["path"] = "[refused]"
            payload["error"] = {"code": "invalid_input", "message": str(exc)}
            return json.dumps(payload, indent=2)

        try:
            return _self._inject_mcp_contract_fields(
                json.dumps(
                    _self.build_symbol_defs(
                        symbol, path, semantic_provider=provider, max_repo_files=max_repo_files
                    ),
                    indent=2,
                )
            )
        except FileNotFoundError as exc:
            _log_tool_exception("tg_symbol_defs", exc)
            payload = _envelope_base(
                routing_backend="RepoMap",
                routing_reason="symbol-defs",
                include_schema_version=False,
            )
            payload["symbol"] = symbol
            payload["path"] = str(Path(path).expanduser())
            payload["error"] = {
                "code": "invalid_input",
                "message": f"Path not found: {path}",
            }
            return json.dumps(payload, indent=2)
        except Exception as exc:  # C4: propagate as structured error, never a raw exception
            payload = _envelope_base(
                routing_backend="RepoMap",
                routing_reason="symbol-defs",
                include_schema_version=False,
            )
            payload["symbol"] = symbol
            payload["path"] = str(Path(path).expanduser())
            payload["error"] = _sanitized_tool_error("tg_symbol_defs", exc)
            return json.dumps(payload, indent=2)
    except Exception as exc:
        _log_tool_exception("tg_symbol_defs", exc)
        return _sanitized_tool_error_text("tg_symbol_defs", exc)


@_register_legacy_tool  # type: ignore
def tg_symbol_source(
    symbol: str,
    path: str = ".",
    provider: str = "native",
    max_repo_files: int = _DEFAULT_MCP_REPO_SCAN_LIMIT,
) -> str:
    """
    Return exact source blocks for a symbol definition.

    Args:
        symbol: Exact symbol name to resolve.
        path: File or directory to inventory.
        max_repo_files: Maximum repository files to scan before resolving the symbol.
    """
    # round-8 security (audit #95 gate): confine the primary path/root param to the MCP root
    # before any scan -- see tg_repo_map for the systemic-finding rationale.
    try:
        try:
            path = str(_confine_mcp_path(path, label="path"))
        except PathConfinementError as exc:
            payload = _envelope_base(
                routing_backend="RepoMap",
                routing_reason="symbol-source",
                include_schema_version=False,
            )
            payload["symbol"] = symbol
            payload["path"] = "[refused]"
            payload["error"] = {"code": "invalid_input", "message": str(exc)}
            return json.dumps(payload, indent=2)

        try:
            return _self._inject_mcp_contract_fields(
                json.dumps(
                    _self.build_symbol_source(
                        symbol, path, semantic_provider=provider, max_repo_files=max_repo_files
                    ),
                    indent=2,
                )
            )
        except FileNotFoundError as exc:
            _log_tool_exception("tg_symbol_source", exc)
            payload = _envelope_base(
                routing_backend="RepoMap",
                routing_reason="symbol-source",
                include_schema_version=False,
            )
            payload["symbol"] = symbol
            payload["path"] = str(Path(path).expanduser())
            payload["error"] = {
                "code": "invalid_input",
                "message": f"Path not found: {path}",
            }
            return json.dumps(payload, indent=2)
        except Exception as exc:  # C4: propagate as structured error, never a raw exception
            payload = _envelope_base(
                routing_backend="RepoMap",
                routing_reason="symbol-source",
                include_schema_version=False,
            )
            payload["symbol"] = symbol
            payload["path"] = str(Path(path).expanduser())
            payload["error"] = _sanitized_tool_error("tg_symbol_source", exc)
            return json.dumps(payload, indent=2)
    except Exception as exc:
        _log_tool_exception("tg_symbol_source", exc)
        return _sanitized_tool_error_text("tg_symbol_source", exc)


@_register_legacy_tool  # type: ignore
def tg_symbol_impact(
    symbol: str, path: str = ".", provider: str = "native", deadline: float | None = None
) -> str:
    """
    Return likely impacted files and tests for a symbol change.

    Args:
        symbol: Exact symbol name to evaluate.
        path: File or directory to inventory.
        deadline: Optional wall-clock budget in seconds for the underlying repo scan. When
            exceeded, the scan stops and returns a flagged partial result instead of running
            unbounded.
    """
    # round-8 security (audit #95 gate): confine the primary path/root param to the MCP root
    # before any scan -- see tg_repo_map for the systemic-finding rationale.
    try:
        try:
            path = str(_confine_mcp_path(path, label="path"))
        except PathConfinementError as exc:
            payload = _envelope_base(
                routing_backend="RepoMap",
                routing_reason="symbol-impact",
                include_schema_version=False,
            )
            payload["symbol"] = symbol
            payload["path"] = "[refused]"
            payload["error"] = {"code": "invalid_input", "message": str(exc)}
            return json.dumps(payload, indent=2)

        try:
            return _self._inject_mcp_contract_fields(
                json.dumps(
                    _self.build_symbol_impact(
                        symbol,
                        path,
                        semantic_provider=provider,
                        max_repo_files=_DEFAULT_MCP_REPO_SCAN_LIMIT,
                        deadline_seconds=deadline,
                    ),
                    indent=2,
                )
            )
        except FileNotFoundError as exc:
            _log_tool_exception("tg_symbol_impact", exc)
            payload = _envelope_base(
                routing_backend="RepoMap",
                routing_reason="symbol-impact",
                include_schema_version=False,
            )
            payload["symbol"] = symbol
            payload["path"] = str(Path(path).expanduser())
            payload["error"] = {
                "code": "invalid_input",
                "message": f"Path not found: {path}",
            }
            return json.dumps(payload, indent=2)
        except Exception as exc:  # C4: propagate as structured error, never a raw exception
            payload = _envelope_base(
                routing_backend="RepoMap",
                routing_reason="symbol-impact",
                include_schema_version=False,
            )
            payload["symbol"] = symbol
            payload["path"] = str(Path(path).expanduser())
            payload["error"] = _sanitized_tool_error("tg_symbol_impact", exc)
            return json.dumps(payload, indent=2)
    except Exception as exc:
        _log_tool_exception("tg_symbol_impact", exc)
        return _sanitized_tool_error_text("tg_symbol_impact", exc)


@_register_legacy_tool  # type: ignore
def tg_symbol_refs(
    symbol: str,
    path: str = ".",
    provider: str = "native",
    max_repo_files: int = _DEFAULT_MCP_REPO_SCAN_LIMIT,
    deadline: float | None = None,
) -> str:
    """
    Return Python-first symbol references across the inventory root.

    Args:
        symbol: Exact symbol name to resolve.
        path: File or directory to inventory.
        max_repo_files: Maximum repository files to scan before resolving the symbol.
        deadline: Optional wall-clock budget in seconds for the underlying repo scan. When
            exceeded, the scan stops and returns a flagged partial result instead of running
            unbounded.
    """
    # round-8 security (audit #95 gate): confine the primary path/root param to the MCP root
    # before any scan -- see tg_repo_map for the systemic-finding rationale.
    try:
        try:
            path = str(_confine_mcp_path(path, label="path"))
        except PathConfinementError as exc:
            payload = _envelope_base(
                routing_backend="RepoMap",
                routing_reason="symbol-refs",
                include_schema_version=False,
            )
            payload["symbol"] = symbol
            payload["path"] = "[refused]"
            payload["error"] = {"code": "invalid_input", "message": str(exc)}
            return json.dumps(payload, indent=2)

        try:
            return _self._inject_mcp_contract_fields(
                json.dumps(
                    _self.build_symbol_refs(
                        symbol,
                        path,
                        semantic_provider=provider,
                        max_repo_files=max_repo_files,
                        deadline_seconds=deadline,
                    ),
                    indent=2,
                )
            )
        except FileNotFoundError as exc:
            _log_tool_exception("tg_symbol_refs", exc)
            payload = _envelope_base(
                routing_backend="RepoMap",
                routing_reason="symbol-refs",
                include_schema_version=False,
            )
            payload["symbol"] = symbol
            payload["path"] = str(Path(path).expanduser())
            payload["error"] = {
                "code": "invalid_input",
                "message": f"Path not found: {path}",
            }
            return json.dumps(payload, indent=2)
        except Exception as exc:  # C4: propagate as structured error, never a raw exception
            payload = _envelope_base(
                routing_backend="RepoMap",
                routing_reason="symbol-refs",
                include_schema_version=False,
            )
            payload["symbol"] = symbol
            payload["path"] = str(Path(path).expanduser())
            payload["error"] = _sanitized_tool_error("tg_symbol_refs", exc)
            return json.dumps(payload, indent=2)
    except Exception as exc:
        _log_tool_exception("tg_symbol_refs", exc)
        return _sanitized_tool_error_text("tg_symbol_refs", exc)


@_register_legacy_tool  # type: ignore
def tg_symbol_callers(
    symbol: str,
    path: str = ".",
    provider: str = "native",
    max_repo_files: int = _DEFAULT_MCP_REPO_SCAN_LIMIT,
    deadline: float | None = None,
) -> str:
    """
    Return Python-first symbol call sites and likely impacted tests.

    Args:
        symbol: Exact symbol name to resolve.
        path: File or directory to inventory.
        max_repo_files: Maximum repository files to scan before resolving the symbol.
        deadline: Optional wall-clock budget in seconds for the underlying repo scan. When
            exceeded, the scan stops and returns a flagged partial result instead of running
            unbounded.
    """
    # round-8 security (audit #95 gate): confine the primary path/root param to the MCP root
    # before any scan -- see tg_repo_map for the systemic-finding rationale.
    try:
        try:
            path = str(_confine_mcp_path(path, label="path"))
        except PathConfinementError as exc:
            payload = _envelope_base(
                routing_backend="RepoMap",
                routing_reason="symbol-callers",
                include_schema_version=False,
            )
            payload["symbol"] = symbol
            payload["path"] = "[refused]"
            payload["error"] = {"code": "invalid_input", "message": str(exc)}
            return json.dumps(payload, indent=2)

        try:
            return _self._inject_mcp_contract_fields(
                json.dumps(
                    _self.build_symbol_callers(
                        symbol,
                        path,
                        semantic_provider=provider,
                        max_repo_files=max_repo_files,
                        deadline_seconds=deadline,
                    ),
                    indent=2,
                )
            )
        except FileNotFoundError as exc:
            _log_tool_exception("tg_symbol_callers", exc)
            payload = _envelope_base(
                routing_backend="RepoMap",
                routing_reason="symbol-callers",
                include_schema_version=False,
            )
            payload["symbol"] = symbol
            payload["path"] = str(Path(path).expanduser())
            payload["error"] = {
                "code": "invalid_input",
                "message": f"Path not found: {path}",
            }
            return json.dumps(payload, indent=2)
        except Exception as exc:  # C4: propagate as structured error, never a raw exception
            payload = _envelope_base(
                routing_backend="RepoMap",
                routing_reason="symbol-callers",
                include_schema_version=False,
            )
            payload["symbol"] = symbol
            payload["path"] = str(Path(path).expanduser())
            payload["error"] = _sanitized_tool_error("tg_symbol_callers", exc)
            return json.dumps(payload, indent=2)
    except Exception as exc:
        _log_tool_exception("tg_symbol_callers", exc)
        return _sanitized_tool_error_text("tg_symbol_callers", exc)


@_register_legacy_tool  # type: ignore
def tg_file_imports(file: str) -> str:
    """
    Return what a single FILE imports, resolved to target files where possible.

    The scoped forward file-dependency primitive (#74): O(1) -- parses exactly one file, no
    repo scan. Far cheaper than a whole-repo `tg_map` for a single file's dependency edges.

    Args:
        file: File to inspect for its own imports. Confined to the project root (cwd); a
            file that legitimately lives outside the project must be copied in first
            (fail-closed, not a silent drop).
    """
    # round-7 security (audit #81 Opus gate #2 follow-up): confine file to the project root
    # (cwd) before any read -- unconfined it is a file-existence + import-string read-oracle
    # over any path reachable from any MCP client (build_file_imports below stats the file and
    # echoes its resolved path / import list back in the JSON result), same class as
    # tg_classify_logs.file_path above. Forward the RESOLVED path so build_file_imports sees
    # the same anchor-validated location this check validated.
    try:
        try:
            file = str(_confine_read_path(file, _mcp_root(), label="file"))
        except PathConfinementError as exc:
            payload = _envelope_base(
                routing_backend="RepoMap",
                routing_reason="file-imports",
                include_schema_version=False,
            )
            payload["file"] = "[refused]"
            payload["error"] = {"code": "invalid_input", "message": str(exc)}
            return json.dumps(payload, indent=2)
        try:
            return _self._inject_mcp_contract_fields(json.dumps(build_file_imports(file), indent=2))
        except FileNotFoundError as exc:
            _log_tool_exception("tg_file_imports", exc)
            payload = _envelope_base(
                routing_backend="RepoMap",
                routing_reason="file-imports",
                include_schema_version=False,
            )
            payload["file"] = str(Path(file).expanduser())
            payload["error"] = {
                "code": "invalid_input",
                "message": f"File not found: {file}",
            }
            return json.dumps(payload, indent=2)
        except Exception as exc:  # propagate as structured error, never a raw exception
            payload = _envelope_base(
                routing_backend="RepoMap",
                routing_reason="file-imports",
                include_schema_version=False,
            )
            payload["file"] = str(Path(file).expanduser())
            payload["error"] = _sanitized_tool_error("tg_file_imports", exc)
            return json.dumps(payload, indent=2)
    except Exception as exc:
        _log_tool_exception("tg_file_imports", exc)
        return _sanitized_tool_error_text("tg_file_imports", exc)


@_register_legacy_tool  # type: ignore
def tg_file_importers(
    file: str,
    path: str = ".",
    max_repo_files: int = _DEFAULT_MCP_REPO_SCAN_LIMIT,
    deadline: float | None = None,
) -> str:
    """
    Return the files that import a single FILE (the reverse #74 file-dependency primitive).

    Prefilters candidate importers via the repo's import-alias graph, then re-parses and
    CONFIRMS each candidate against FILE before reporting it as an edge.

    Args:
        file: File to find importers of. Confined to the project root (cwd); a file that
            legitimately lives outside the project must be copied in first (fail-closed,
            not a silent drop).
        path: Root to scan for importers.
        max_repo_files: Maximum repository files to scan before resolving importers.
        deadline: Optional wall-clock budget in seconds for the underlying repo scan. When
            exceeded, the scan stops and returns a flagged partial result instead of running
            unbounded.
    """
    # round-8 security (audit #95 gate): confine the secondary root param to the MCP root too
    # -- unconfined it is an arbitrary-directory-read primitive over the MCP protocol (the
    # design's proven example: `path` here resolved raw).
    try:
        try:
            path = str(_confine_mcp_path(path, label="path"))
        except PathConfinementError as exc:
            payload = _envelope_base(
                routing_backend="RepoMap",
                routing_reason="file-importers",
                include_schema_version=False,
            )
            payload["file"] = "[refused]"
            payload["path"] = "[refused]"
            payload["error"] = {"code": "invalid_input", "message": str(exc)}
            return json.dumps(payload, indent=2)

        # round-7 security (audit #81 Opus gate #2 follow-up): confine file to the project root
        # (cwd) before any read, same class/rationale as tg_file_imports above.
        try:
            file = str(_confine_read_path(file, _mcp_root(), label="file"))
        except PathConfinementError as exc:
            payload = _envelope_base(
                routing_backend="RepoMap",
                routing_reason="file-importers",
                include_schema_version=False,
            )
            payload["file"] = "[refused]"
            payload["path"] = path
            payload["error"] = {"code": "invalid_input", "message": str(exc)}
            return json.dumps(payload, indent=2)

        try:
            return _self._inject_mcp_contract_fields(
                json.dumps(
                    _self.build_file_importers(
                        file, path, max_repo_files=max_repo_files, deadline_seconds=deadline
                    ),
                    indent=2,
                )
            )
        except FileNotFoundError as exc:
            _log_tool_exception("tg_file_importers", exc)
            payload = _envelope_base(
                routing_backend="RepoMap",
                routing_reason="file-importers",
                include_schema_version=False,
            )
            payload["file"] = str(Path(file).expanduser())
            payload["path"] = str(Path(path).expanduser())
            payload["error"] = {
                "code": "invalid_input",
                "message": f"File not found: {file}",
            }
            return json.dumps(payload, indent=2)
        except Exception as exc:  # propagate as structured error, never a raw exception
            payload = _envelope_base(
                routing_backend="RepoMap",
                routing_reason="file-importers",
                include_schema_version=False,
            )
            payload["file"] = str(Path(file).expanduser())
            payload["path"] = str(Path(path).expanduser())
            payload["error"] = _sanitized_tool_error("tg_file_importers", exc)
            return json.dumps(payload, indent=2)
    except Exception as exc:
        _log_tool_exception("tg_file_importers", exc)
        return _sanitized_tool_error_text("tg_file_importers", exc)


@_register_legacy_tool  # type: ignore
def tg_symbol_blast_radius(
    symbol: str,
    path: str = ".",
    max_depth: int = 3,
    provider: str = "native",
    max_repo_files: int = _DEFAULT_MCP_REPO_SCAN_LIMIT,
    deadline: float | None = None,
) -> str:
    """
    Return exact callers plus a transitive file/test blast radius for a symbol.

    Args:
        symbol: Exact symbol name to resolve.
        path: File or directory to inventory.
        max_depth: Maximum reverse-import depth to include.
        max_repo_files: Maximum repository files to scan before resolving the symbol.
        deadline: Optional wall-clock budget in seconds for the underlying graph traversal.
            When exceeded, the scan stops and returns a flagged partial result instead of
            running unbounded.
    """
    # round-8 security (audit #95 gate): confine the primary path/root param to the MCP root
    # before any scan -- see tg_repo_map for the systemic-finding rationale.
    try:
        try:
            path = str(_confine_mcp_path(path, label="path"))
        except PathConfinementError as exc:
            payload = _envelope_base(
                routing_backend="RepoMap",
                routing_reason="symbol-blast-radius",
                include_schema_version=False,
            )
            payload["symbol"] = symbol
            payload["max_depth"] = max(0, int(max_depth))
            payload["path"] = "[refused]"
            payload["error"] = {"code": "invalid_input", "message": str(exc)}
            return json.dumps(payload, indent=2)

        try:
            return _self._inject_mcp_contract_fields(
                json.dumps(
                    _self.build_symbol_blast_radius(
                        symbol,
                        path,
                        max_depth=max_depth,
                        semantic_provider=provider,
                        max_repo_files=max_repo_files,
                        deadline_seconds=deadline,
                    ),
                    indent=2,
                )
            )
        except FileNotFoundError as exc:
            _log_tool_exception("tg_symbol_blast_radius", exc)
            payload = _envelope_base(
                routing_backend="RepoMap",
                routing_reason="symbol-blast-radius",
                include_schema_version=False,
            )
            payload["symbol"] = symbol
            payload["max_depth"] = max(0, int(max_depth))
            payload["path"] = str(Path(path).expanduser())
            payload["error"] = {
                "code": "invalid_input",
                "message": f"Path not found: {path}",
            }
            return json.dumps(payload, indent=2)
        except Exception as exc:  # M11: propagate as structured error, never a raw exception
            payload = _envelope_base(
                routing_backend="RepoMap",
                routing_reason="symbol-blast-radius",
                include_schema_version=False,
            )
            payload["symbol"] = symbol
            payload["max_depth"] = max(0, int(max_depth))
            payload["path"] = str(Path(path).expanduser())
            payload["error"] = _sanitized_tool_error("tg_symbol_blast_radius", exc)
            return json.dumps(payload, indent=2)
    except Exception as exc:
        _log_tool_exception("tg_symbol_blast_radius", exc)
        return _sanitized_tool_error_text("tg_symbol_blast_radius", exc)


@_register_legacy_tool  # type: ignore
def tg_symbol_blast_radius_render(
    symbol: str,
    path: str = ".",
    max_depth: int = 3,
    max_files: int = 3,
    max_sources: int = 5,
    max_symbols_per_file: int = 6,
    max_render_chars: int | None = None,
    optimize_context: bool = False,
    render_profile: str = "full",
    profile: bool = False,
    provider: str = "native",
    max_repo_files: int = _DEFAULT_MCP_REPO_SCAN_LIMIT,
) -> str:
    """
    Return a prompt-ready blast-radius bundle for a symbol.

    Args:
        symbol: Exact symbol name to resolve.
        path: File or directory to inventory.
        max_depth: Maximum reverse-import depth to include.
        max_files: Maximum files to include in the render bundle.
        max_sources: Maximum exact source blocks to include.
        max_symbols_per_file: Maximum summary symbols to include per file.
        max_render_chars: Maximum characters to emit in rendered_context.
        optimize_context: Strip blank lines and comment-only lines from rendered source blocks.
        render_profile: Render profile to use: full, compact, or llm.
        max_repo_files: Maximum repository files to scan before resolving the symbol.
    """
    # round-8 security (audit #95 gate): confine the primary path/root param to the MCP root
    # before any scan -- see tg_repo_map for the systemic-finding rationale.
    try:
        try:
            path = str(_confine_mcp_path(path, label="path"))
        except PathConfinementError as exc:
            payload = _envelope_base(
                routing_backend="RepoMap",
                routing_reason="symbol-blast-radius-render",
                include_schema_version=False,
            )
            payload["symbol"] = symbol
            payload["max_depth"] = max(0, int(max_depth))
            payload["path"] = "[refused]"
            payload["error"] = {"code": "invalid_input", "message": str(exc)}
            return json.dumps(payload, indent=2)

        try:
            return _self._inject_mcp_contract_fields(
                json.dumps(
                    _self.build_symbol_blast_radius_render(
                        symbol,
                        path,
                        max_depth=max_depth,
                        max_files=max_files,
                        max_sources=max_sources,
                        max_symbols_per_file=max_symbols_per_file,
                        max_render_chars=max_render_chars,
                        optimize_context=optimize_context,
                        render_profile=render_profile,
                        profile=profile,
                        semantic_provider=provider,
                        max_repo_files=max_repo_files,
                    ),
                    indent=2,
                )
            )
        except FileNotFoundError as exc:
            _log_tool_exception("tg_symbol_blast_radius_render", exc)
            payload = _envelope_base(
                routing_backend="RepoMap",
                routing_reason="symbol-blast-radius-render",
                include_schema_version=False,
            )
            payload["symbol"] = symbol
            payload["max_depth"] = max(0, int(max_depth))
            payload["path"] = str(Path(path).expanduser())
            payload["error"] = {
                "code": "invalid_input",
                "message": f"Path not found: {path}",
            }
            return json.dumps(payload, indent=2)
        except Exception as exc:  # M11: propagate as structured error, never a raw exception
            payload = _envelope_base(
                routing_backend="RepoMap",
                routing_reason="symbol-blast-radius-render",
                include_schema_version=False,
            )
            payload["symbol"] = symbol
            payload["max_depth"] = max(0, int(max_depth))
            payload["path"] = str(Path(path).expanduser())
            payload["error"] = _sanitized_tool_error("tg_symbol_blast_radius_render", exc)
            return json.dumps(payload, indent=2)
    except Exception as exc:
        _log_tool_exception("tg_symbol_blast_radius_render", exc)
        return _sanitized_tool_error_text("tg_symbol_blast_radius_render", exc)
