"""MCP repository exploration and diagnostics tools."""

from __future__ import annotations

import inspect
import json
import sys
from pathlib import Path
from typing import TYPE_CHECKING

from tensor_grep.cli import mcp_tool_contract as _tool_contract
from tensor_grep.cli.mcp.security import (
    _confine_mcp_path,
    _confine_read_path,
    _envelope_base,
    _log_tool_exception,
    _meta_confinement_error,
    _meta_envelope,
    _meta_invalid_input_error,
    _meta_unknown_action_error,
    _safe_exception_class_name,
    _sanitized_tool_error,
    _sanitized_tool_error_text,
)
from tensor_grep.cli.mcp.server import (
    _DEFAULT_MCP_REPO_SCAN_LIMIT,
    _register_legacy_tool,
    _register_tool,
)
from tensor_grep.cli.mcp_coverage_tool import coverage_tool
from tensor_grep.cli.mcp_path_errors import PathConfinementError

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


@_register_legacy_tool  # type: ignore
def tg_repo_map(path: str = ".", max_repo_files: int | None = _DEFAULT_MCP_REPO_SCAN_LIMIT) -> str:
    """
    Return a deterministic repository inventory for agent context selection.

    Args:
        path: File or directory to inventory.
        max_repo_files: Maximum repo files to scan before returning.
    """
    # round-8 security (audit #95 gate): confine the primary path/root param to the MCP root
    # before any scan -- unconfined it is an arbitrary-directory-read primitive over the MCP
    # protocol (systemic finding: every path/root-taking tool except the file-scoped
    # tg_file_imports/tg_classify_logs lacked this).
    try:
        try:
            path = str(_confine_mcp_path(path, label="path"))
        except PathConfinementError as exc:
            payload = _envelope_base(
                routing_backend="RepoMap",
                routing_reason="repo-map",
                include_schema_version=False,
            )
            payload["path"] = "[refused]"
            payload["error"] = {"code": "invalid_input", "message": str(exc)}
            payload["incomplete"] = {"status": True, "cause": "error", "budget_remediable": False}
            return json.dumps(payload, indent=2)

        try:
            from tensor_grep.cli.repo_map import DEFAULT_AGENT_REPO_MAP_LIMIT

            effective_max_repo_files = max_repo_files or DEFAULT_AGENT_REPO_MAP_LIMIT
            return _self._inject_mcp_contract_fields(
                json.dumps(
                    _self.build_repo_map(path, max_repo_files=effective_max_repo_files),
                    indent=2,
                )
            )
        except FileNotFoundError as exc:
            _log_tool_exception("tg_repo_map", exc)
            payload = _envelope_base(
                routing_backend="RepoMap",
                routing_reason="repo-map",
                include_schema_version=False,
            )
            payload["path"] = str(Path(path).expanduser())
            payload["error"] = {
                "code": "invalid_input",
                "message": f"Path not found: {path}",
            }
            return json.dumps(payload, indent=2)
        except Exception as exc:  # M11: propagate as structured error, never a raw exception
            payload = _envelope_base(
                routing_backend="RepoMap",
                routing_reason="repo-map",
                include_schema_version=False,
            )
            payload["path"] = str(Path(path).expanduser())
            payload["error"] = _sanitized_tool_error("tg_repo_map", exc)
            return json.dumps(payload, indent=2)
    except Exception as exc:
        _log_tool_exception("tg_repo_map", exc)
        return _sanitized_tool_error_text("tg_repo_map", exc)


@_register_tool  # type: ignore
def tg_coverage(path: str = ".") -> str:
    """Report repo-map session and AST-cache coverage for PATH without writing state."""
    try:
        return coverage_tool(path)
    except Exception as exc:
        _log_tool_exception("tg_coverage", exc)
        return _sanitized_tool_error_text("tg_coverage", exc)


@_register_legacy_tool  # type: ignore
def tg_orient(
    path: str = ".",
    max_tokens: int = 3000,
    max_central_files: int = 10,
    ignore: list[str] | None = None,
) -> str:
    """
    Return a one-call orientation capsule for an unfamiliar repository.

    Mirrors `tg orient` (build_orient_capsule_json): the most central files by import-graph
    centrality, heuristically detected entry points, a symbol map, and bounded AST-boundary
    source snippets within a token budget. Pure-CPU, no API key, no GPU. Use it when you need
    "what is this codebase and where do I start", before tg_repo_map/tg_context_pack.

    Args:
        path: File or directory to orient on. Confined to the MCP server root (cwd, or
            TG_MCP_ROOT if set); a path outside it is refused.
        max_tokens: Snippet token budget for the capsule. Defaults to 3000.
        max_central_files: Number of top central files to surface. Defaults to 10.
        ignore: Glob(s) to exclude from the centrality ranking (basename or repo-relative
            path), e.g. ["seo/**", "core/skills/**"]. Excludes vendor/skill CODE trees that
            otherwise rank as "central" on a harness repo.
    """
    # round-9 security (audit #95 Part 2): confine the primary path/root param to the MCP
    # root before any scan -- see tg_repo_map for the systemic-finding rationale.
    try:
        try:
            path = str(_confine_mcp_path(path, label="path"))
        except PathConfinementError as exc:
            payload = _envelope_base(
                routing_backend="RepoMap",
                routing_reason="orient",
                include_schema_version=False,
            )
            payload["path"] = "[refused]"
            payload["error"] = {"code": "invalid_input", "message": str(exc)}
            return json.dumps(payload, indent=2)

        try:
            return _self._inject_mcp_contract_fields(
                _self.build_orient_capsule_json(
                    path,
                    max_tokens=max_tokens,
                    max_central_files=max_central_files,
                    ignore=tuple(ignore or ()),
                )
            )
        except FileNotFoundError as exc:
            _log_tool_exception("tg_orient", exc)
            payload = _envelope_base(
                routing_backend="RepoMap",
                routing_reason="orient",
                include_schema_version=False,
            )
            payload["path"] = str(Path(path).expanduser())
            payload["error"] = {"code": "invalid_input", "message": f"Path not found: {path}"}
            return json.dumps(payload, indent=2)
        except ValueError as exc:
            _log_tool_exception("tg_orient", exc)
            payload = _envelope_base(
                routing_backend="RepoMap",
                routing_reason="orient",
                include_schema_version=False,
            )
            payload["path"] = str(Path(path).expanduser())
            payload["error"] = {"code": "invalid_input", "message": "Invalid orient parameter"}
            return json.dumps(payload, indent=2)
        except Exception as exc:  # propagate as structured error, never a raw exception
            payload = _envelope_base(
                routing_backend="RepoMap",
                routing_reason="orient",
                include_schema_version=False,
            )
            payload["path"] = str(Path(path).expanduser())
            payload["error"] = _sanitized_tool_error("tg_orient", exc)
            return json.dumps(payload, indent=2)
    except Exception as exc:
        _log_tool_exception("tg_orient", exc)
        return _sanitized_tool_error_text("tg_orient", exc)


@_register_legacy_tool  # type: ignore
def tg_doctor(
    path: str = ".",
    config: str | None = "sgconfig.yml",
    with_lsp: bool = True,
    fast: bool = False,
) -> str:
    """
    Return system, GPU, cache, AST, daemon, shell-escaping, and LSP-provider diagnostics.

    Mirrors `tg doctor` (_build_doctor_payload). Provider availability
    (lsp.providers/lsp.providers_by_language) is not navigation proof -- inspect health_status/
    health_check before trusting an LSP-confirmed evidence label.

    Args:
        path: Workspace root to inspect. Confined to the MCP server root (cwd, or
            TG_MCP_ROOT if set); a path outside it is refused.
        config: Path to an ast-grep root config, resolved relative to path when not
            absolute. Confined to the (already-confined) path; a config that legitimately
            lives outside path must be copied in first (fail-closed, not a silent drop).
            Defaults to "sgconfig.yml".
        with_lsp: Include external LSP provider diagnostics. Defaults to true.
        fast: Skip slow PyPI/LSP/CPU-GPU runtime probes. Defaults to false.
    """
    # round-9 security (audit #95 Part 2): confine the primary path/root param to the MCP
    # root before any probe -- see tg_repo_map for the systemic-finding rationale.
    try:
        try:
            path = str(_confine_mcp_path(path, label="path"))
        except PathConfinementError as exc:
            payload = _envelope_base(
                routing_backend="Doctor",
                routing_reason="doctor",
                include_schema_version=False,
            )
            payload["path"] = "[refused]"
            payload["error"] = {"code": "invalid_input", "message": str(exc)}
            return json.dumps(payload, indent=2)

        # New hardening (beyond the design's literal "wrap it" ask): `config` is a SECONDARY
        # param that `_build_doctor_payload` uses to relocate its own `root` (config's resolved
        # parent directory) for every downstream diagnostic probe -- unconfined, a caller could
        # point every probe at an arbitrary directory via `config=/some/other/place/x.yml`, the
        # same "secondary anchor derived from an unconfined param" class the #95 gate's MUST-FIX
        # #3 closed for tg_session_file_importers. Confine to the already-confined `path`,
        # mirroring tg_ruleset_scan's baseline_path/suppressions_path anchor-to-scan-root
        # pattern. `if config:` (not `is not None`) matches _build_doctor_payload's OWN
        # truthiness check so an empty string is treated identically to "not provided" on both
        # sides -- confining "" would otherwise turn a no-op default into a real (and wrong)
        # root-parent relocation.
        if config:
            try:
                config = str(_confine_read_path(config, Path(path), label="config"))
            except PathConfinementError as exc:
                payload = _envelope_base(
                    routing_backend="Doctor",
                    routing_reason="doctor",
                    include_schema_version=False,
                )
                payload["path"] = path
                payload["config"] = "[refused]"
                payload["error"] = {"code": "invalid_input", "message": str(exc)}
                return json.dumps(payload, indent=2)

        try:
            builder = _self._build_doctor_payload
            sig = inspect.signature(builder)
            accepts_fast = "fast" in sig.parameters or any(
                p.kind == inspect.Parameter.VAR_KEYWORD for p in sig.parameters.values()
            )
            doctor_payload = (
                builder(path, config=config, with_lsp=with_lsp, fast=fast)
                if accepts_fast
                else builder(path, config=config, with_lsp=with_lsp)
            )
            if fast and isinstance(doctor_payload, dict) and "lsp" in doctor_payload:
                doctor_payload.pop("lsp_providers", None)
                doctor_payload.pop("lsp_provider_items", None)
            return _self._inject_mcp_contract_fields(json.dumps(doctor_payload, indent=2))
        except Exception as exc:  # propagate as structured error, never a raw exception
            payload = _envelope_base(
                routing_backend="Doctor",
                routing_reason="doctor",
                include_schema_version=False,
            )
            payload["path"] = str(Path(path).expanduser())
            payload["error"] = _sanitized_tool_error("tg_doctor", exc)
            return json.dumps(payload, indent=2)
    except Exception as exc:
        _log_tool_exception("tg_doctor", exc)
        return _sanitized_tool_error_text("tg_doctor", exc)


@_register_legacy_tool  # type: ignore
def tg_devices(json_output: bool = True) -> str:
    """
    Return routable GPU inventory for scheduling and diagnostics.

    Args:
        json_output: Emit machine-readable JSON output (default true). Set to false for
            plain-text output.
    """
    try:
        import json

        try:
            inventory = _self.collect_device_inventory()
            payload = inventory.to_dict()
            if json_output:
                # M14: tg_devices' JSON arrived un-stamped (compact dumps, no envelope); route it
                # through the injector like every other tool envelope.
                return _self._inject_mcp_contract_fields(json.dumps(payload))

            if not inventory.devices:
                return "No routable GPUs detected."

            lines = [f"Detected {inventory.device_count} routable GPU(s):"]
            for device in inventory.devices:
                lines.append(f"- gpu:{device.device_id} vram_mb={device.vram_capacity_mb}")
            return "\n".join(lines)
        except Exception as exc:
            _log_tool_exception("tg_devices", exc)
            if json_output:
                return _sanitized_tool_error_text("tg_devices", exc)
            return f"Error collecting device inventory: {_safe_exception_class_name(exc)}"
    except Exception as exc:
        _log_tool_exception("tg_devices", exc)
        return _sanitized_tool_error_text("tg_devices", exc)


@_register_tool  # type: ignore
def tg_explore(
    action: ExploreAction,
    path: str = ".",
    max_tokens: int = 3000,
    max_central_files: int = 10,
    ignore: list[str] | None = None,
    max_repo_files: int | None = _DEFAULT_MCP_REPO_SCAN_LIMIT,
    config: str | None = "sgconfig.yml",
    with_lsp: bool = True,
    fast: bool = True,
    json_output: bool = True,
    compact: bool = False,
) -> str:
    """Repo orient/repo_map/doctor/devices. compact=True bounds JSON <=4000B (requires json_output=True)."""
    try:
        try:
            path = str(_confine_mcp_path(path, label="path"))
            if config:
                config = str(_confine_read_path(config, Path(path), label="config"))
        except PathConfinementError as exc:
            return _meta_confinement_error("tg_explore", action, exc)

        if compact and not json_output:
            return _meta_invalid_input_error(
                "tg_explore", action, "compact=True cannot be combined with json_output=False."
            )

        try:
            result_json: str | None = None
            if action == "orient":
                result_json = _self.tg_orient(
                    path=path,
                    max_tokens=max_tokens,
                    max_central_files=max_central_files,
                    ignore=ignore,
                )
            elif action == "repo_map":
                result_json = _self.tg_repo_map(path=path, max_repo_files=max_repo_files)
            elif action == "doctor":
                result_json = _self.tg_doctor(
                    path=path, config=config, with_lsp=with_lsp, fast=fast
                )
            elif action == "devices":
                result_json = _self.tg_devices(json_output=json_output)
            else:
                return _meta_unknown_action_error("tg_explore", action, _TG_EXPLORE_ACTIONS)
            if compact:
                from tensor_grep.cli.mcp_compact import compact_mcp_json_result

                return compact_mcp_json_result(result_json, action=action)
            return result_json
        except Exception as exc:
            payload = _meta_envelope(tool="tg_explore", action=action)
            payload["error"] = _sanitized_tool_error("tg_explore", exc)
            return json.dumps(payload, indent=2)
    except Exception as exc:
        _log_tool_exception("tg_explore", exc)
        return _sanitized_tool_error_text("tg_explore", exc)
