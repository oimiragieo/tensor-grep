"""MCP tool capability registries, discovery, and SQL tools."""

from __future__ import annotations

import json
import sys
from typing import TYPE_CHECKING, Any, cast

from mcp import types
from mcp_types.version import SUPPORTED_PROTOCOL_VERSIONS

from tensor_grep.cli import mcp_tool_contract as _tool_contract
from tensor_grep.cli.mcp.security import (
    _confine_mcp_path,
    _json_output_version,
    _log_tool_exception,
    _safe_exception_class_name,
    _sanitized_tool_error,
)
from tensor_grep.cli.mcp.server import (
    _DEFAULT_MCP_REPO_SCAN_LIMIT,
    _TG_MCP_SERVER_CONTRACT_VERSION,
    _legacy_tools_enabled,
    _mcp_server_version,
    _register_tool,
)
from tensor_grep.cli.mcp_path_errors import PathConfinementError
from tensor_grep.core import completeness as _completeness

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

_PYTHON_LOCAL_MCP_TOOLS = (
    "tg_rulesets",
    "tg_ruleset_scan",
    "tg_repo_map",
    "tg_orient",
    "tg_doctor",
    "tg_context_pack",
    "tg_edit_plan",
    "tg_context_render",
    "tg_agent_capsule",
    "tg_session_edit_plan",
    "tg_session_context_render",
    "tg_session_blast_radius",
    "tg_symbol_blast_radius_plan",
    "tg_session_blast_radius_render",
    "tg_session_blast_radius_plan",
    "tg_symbol_defs",
    "tg_symbol_source",
    "tg_symbol_impact",
    "tg_symbol_refs",
    "tg_symbol_callers",
    "tg_file_imports",
    "tg_file_importers",
    "tg_session_file_importers",
    "tg_symbol_blast_radius",
    "tg_symbol_blast_radius_render",
    "tg_search",
    "tg_ast_search",
    "tg_find",
    "tg_devices",
    "tg_audit_manifest_verify",
    "tg_audit_history",
    "tg_audit_diff",
    "tg_review_bundle_create",
    "tg_review_bundle_verify",
    "tg_checkpoint_create",
    "tg_checkpoint_list",
    "tg_checkpoint_undo",
    "tg_session_open",
    "tg_session_list",
    "tg_session_show",
    "tg_session_refresh",
    "tg_session_context",
)
_EMBEDDED_SAFE_MCP_TOOLS = ("tg_rewrite_plan", "tg_rewrite_apply")
_NATIVE_REQUIRED_MCP_TOOLS = ("tg_index_search", "tg_rewrite_diff")
# The always-on singletons (#98 build-precision must-fix #4): NEVER gated by
# `TG_MCP_LEGACY_TOOLS` -- `tg_mcp_capabilities` is the discovery entrypoint an agent needs
# even when every legacy name is de-advertised, and `tg_classify_logs` is a standalone-family
# tool with no meta-tool home (niche, no sibling family to compose into).
_SINGLETON_MCP_TOOLS = (
    "tg_mcp_capabilities",
    "tg_classify_logs",
    "tg_batch",
    "tg_coverage",
    "tg_discover_tools",
)
# The 10 additive task-shaped meta-tools (#98, Phase-1): ALWAYS registered/advertised
# regardless of `TG_MCP_LEGACY_TOOLS`, since they are the additive surface this flag exists to
# let an operator eventually rely on ALONE. Each composes several of the 47 legacy tools by an
# `action` string param; see `_META_MCP_TOOL_CAPABILITIES` below for the composition map.
_META_MCP_TOOLS = (
    "tg_navigate",
    "tg_impact",
    "tg_query",
    "tg_context",
    "tg_explore",
    "tg_session",
    "tg_scan",
    "tg_audit",
    "tg_checkpoint",
    "tg_rewrite",
)
# Per-action 3-class signal (native_required / mutation / embedded_fallback) for each meta
# tool's composed actions -- preserves the SAME signal `_MCP_TOOL_CAPABILITIES` already tracks
# per legacy tool, just keyed one level deeper since a single meta tool's actions can span
# more than one class (e.g. tg_query's "index" action is native-required while its "text"/
# "ast"/"find" siblings are not). `mutation=True` marks an action that can write to disk
# (either always, like tg_checkpoint's create/undo, or opt-in via an optional param the legacy
# tool already gates, like tg_scan's write_baseline/write_suppressions or tg_rewrite's apply).
_META_MCP_TOOL_CAPABILITIES: dict[str, dict[str, object]] = {
    "tg_navigate": {
        "mode": "meta",
        "composes": [
            "tg_symbol_defs",
            "tg_symbol_source",
            "tg_symbol_refs",
            "tg_symbol_callers",
            "tg_file_imports",
            "tg_file_importers",
        ],
        "actions": {
            "defs": {"native_required": False, "mutation": False, "embedded_fallback": False},
            "source": {"native_required": False, "mutation": False, "embedded_fallback": False},
            "refs": {"native_required": False, "mutation": False, "embedded_fallback": False},
            "callers": {"native_required": False, "mutation": False, "embedded_fallback": False},
            "imports": {"native_required": False, "mutation": False, "embedded_fallback": False},
            "importers": {
                "native_required": False,
                "mutation": False,
                "embedded_fallback": False,
            },
        },
        "notes": "Task-shaped meta-tool: symbol/file navigation (defs/source/refs/callers/imports/importers).",
    },
    "tg_impact": {
        "mode": "meta",
        "composes": [
            "tg_symbol_impact",
            "tg_symbol_blast_radius",
            "tg_symbol_blast_radius_plan",
            "tg_symbol_blast_radius_render",
        ],
        "actions": {
            "impact": {"native_required": False, "mutation": False, "embedded_fallback": False},
            "blast_radius": {
                "native_required": False,
                "mutation": False,
                "embedded_fallback": False,
            },
            "blast_radius_plan": {
                "native_required": False,
                "mutation": False,
                "embedded_fallback": False,
            },
            "blast_radius_render": {
                "native_required": False,
                "mutation": False,
                "embedded_fallback": False,
            },
        },
        "notes": "Task-shaped meta-tool: symbol change-impact analysis (impact/blast_radius/blast_radius_plan/blast_radius_render).",
    },
    "tg_query": {
        "mode": "meta",
        "composes": ["tg_search", "tg_ast_search", "tg_find", "tg_index_search"],
        "actions": {
            "text": {"native_required": False, "mutation": False, "embedded_fallback": False},
            "search": {"native_required": False, "mutation": False, "embedded_fallback": False},
            "ast": {"native_required": False, "mutation": False, "embedded_fallback": False},
            "find": {"native_required": False, "mutation": False, "embedded_fallback": False},
            "index": {"native_required": True, "mutation": False, "embedded_fallback": False},
        },
        "notes": (
            "Task-shaped meta-tool: pattern/AST/whole-repo-semantic/trigram-index search "
            "(text/search/ast/find/index). search aliases text; index requires a native tg binary "
            "and fails closed (routing_reason='native-tg-unavailable') without one. Accepts "
            "an optional workspace_roots (array of paths, each independently confined) to "
            "run the same action across multiple repo roots in one call, aggregated under "
            "results_by_root."
        ),
    },
    "tg_context": {
        "mode": "meta",
        "composes": ["tg_context_pack", "tg_edit_plan", "tg_context_render", "tg_agent_capsule"],
        "actions": {
            "pack": {"native_required": False, "mutation": False, "embedded_fallback": False},
            "edit_plan": {
                "native_required": False,
                "mutation": False,
                "embedded_fallback": False,
            },
            "render": {"native_required": False, "mutation": False, "embedded_fallback": False},
            "capsule": {"native_required": False, "mutation": False, "embedded_fallback": False},
        },
        "notes": (
            "Task-shaped meta-tool: repository context for edit planning "
            "(pack/edit_plan/render/capsule). action='capsule' accepts the same optional "
            "deadline (seconds) as `tg codemap --deadline`."
        ),
    },
    "tg_explore": {
        "mode": "meta",
        "composes": ["tg_orient", "tg_repo_map", "tg_doctor", "tg_devices"],
        "actions": {
            "orient": {"native_required": False, "mutation": False, "embedded_fallback": False},
            "repo_map": {
                "native_required": False,
                "mutation": False,
                "embedded_fallback": False,
            },
            "doctor": {"native_required": False, "mutation": False, "embedded_fallback": False},
            "devices": {"native_required": False, "mutation": False, "embedded_fallback": False},
        },
        "notes": (
            "Task-shaped meta-tool: codebase orientation and diagnostics "
            "(orient/repo_map/doctor/devices). Call action='orient' FIRST on an unfamiliar repo."
        ),
    },
    "tg_session": {
        "mode": "meta",
        "composes": [
            "tg_session_open",
            "tg_session_list",
            "tg_session_show",
            "tg_session_refresh",
            "tg_session_context",
            "tg_session_edit_plan",
            "tg_session_context_render",
            "tg_session_blast_radius",
            "tg_session_blast_radius_plan",
            "tg_session_blast_radius_render",
            "tg_session_file_importers",
        ],
        "actions": {
            "open": {"native_required": False, "mutation": True, "embedded_fallback": False},
            "list": {"native_required": False, "mutation": False, "embedded_fallback": False},
            "show": {"native_required": False, "mutation": False, "embedded_fallback": False},
            "refresh": {"native_required": False, "mutation": True, "embedded_fallback": False},
            "context": {"native_required": False, "mutation": False, "embedded_fallback": False},
            "edit_plan": {
                "native_required": False,
                "mutation": False,
                "embedded_fallback": False,
            },
            "context_render": {
                "native_required": False,
                "mutation": False,
                "embedded_fallback": False,
            },
            "blast_radius": {
                "native_required": False,
                "mutation": False,
                "embedded_fallback": False,
            },
            "blast_radius_plan": {
                "native_required": False,
                "mutation": False,
                "embedded_fallback": False,
            },
            "blast_radius_render": {
                "native_required": False,
                "mutation": False,
                "embedded_fallback": False,
            },
            "file_importers": {
                "native_required": False,
                "mutation": False,
                "embedded_fallback": False,
            },
        },
        "notes": (
            "Task-shaped meta-tool: cached repository-map session lifecycle and "
            "session-scoped queries (open/list/show/refresh/context/edit_plan/"
            "context_render/blast_radius/blast_radius_plan/blast_radius_render/"
            "file_importers). action='open'/'refresh' write the session cache."
        ),
    },
    "tg_scan": {
        "mode": "meta",
        "composes": ["tg_ruleset_scan", "tg_rulesets"],
        "actions": {
            "scan": {"native_required": False, "mutation": True, "embedded_fallback": False},
            "rulesets": {
                "native_required": False,
                "mutation": False,
                "embedded_fallback": False,
            },
        },
        "notes": (
            "Task-shaped meta-tool: built-in/inline ast-grep ruleset scanning "
            "(scan/rulesets). action='scan' is read-only by default; supplying "
            "write_baseline/write_suppressions writes a file to disk."
        ),
    },
    "tg_audit": {
        "mode": "meta",
        "composes": [
            "tg_audit_manifest_verify",
            "tg_audit_history",
            "tg_audit_diff",
            "tg_review_bundle_create",
            "tg_review_bundle_verify",
        ],
        "actions": {
            "manifest_verify": {
                "native_required": False,
                "mutation": False,
                "embedded_fallback": False,
            },
            "history": {"native_required": False, "mutation": False, "embedded_fallback": False},
            "diff": {"native_required": False, "mutation": False, "embedded_fallback": False},
            "bundle_create": {
                "native_required": False,
                "mutation": True,
                "embedded_fallback": False,
            },
            "bundle_verify": {
                "native_required": False,
                "mutation": False,
                "embedded_fallback": False,
            },
        },
        "notes": (
            "Task-shaped meta-tool: rewrite audit manifest and review bundle operations "
            "(manifest_verify/history/diff/bundle_create/bundle_verify). "
            "action='bundle_create' writes a bundle file when output_path is supplied."
        ),
    },
    "tg_checkpoint": {
        "mode": "meta",
        "composes": ["tg_checkpoint_create", "tg_checkpoint_list", "tg_checkpoint_undo"],
        "actions": {
            "create": {"native_required": False, "mutation": True, "embedded_fallback": False},
            "list": {"native_required": False, "mutation": False, "embedded_fallback": False},
            "undo": {"native_required": False, "mutation": True, "embedded_fallback": False},
        },
        "notes": "Task-shaped meta-tool: edit checkpoint lifecycle (create/list/undo). action='create'/'undo' write.",
    },
    "tg_rewrite": {
        "mode": "meta",
        "composes": ["tg_rewrite_plan", "tg_rewrite_apply", "tg_rewrite_diff"],
        "actions": {
            "plan": {"native_required": False, "mutation": False, "embedded_fallback": True},
            "apply": {"native_required": False, "mutation": True, "embedded_fallback": True},
            "diff": {"native_required": True, "mutation": False, "embedded_fallback": False},
        },
        "notes": (
            "Task-shaped meta-tool: native AST rewrite plan/apply/diff (plan/apply/diff). "
            "action='apply' is the mutation surface (writes files); action='diff' requires "
            "a standalone native tg binary. lint_cmd/test_cmd stay gated by "
            "TG_MCP_ALLOW_VALIDATION_COMMANDS exactly as on the legacy tg_rewrite_apply tool."
        ),
    },
}


def _build_mcp_tool_capabilities() -> dict[str, dict[str, object]]:
    """Build the live `_MCP_TOOL_CAPABILITIES` registry (#98 build-precision must-fix #4).

    The 47 legacy entries are included ONLY when `_legacy_tools_enabled()` -- evaluated once
    here, at IMPORT time, the SAME read `_register_legacy_tool` uses for actual registration,
    so the two can never disagree within one running process (see that decorator's docstring).
    The 3 always-on tools and the 10 meta tools are ALWAYS included, unconditionally.
    """
    capabilities: dict[str, dict[str, object]] = {}
    if _legacy_tools_enabled():
        capabilities.update({
            name: {
                "mode": "python-local",
                "native_required": False,
                "embedded_fallback": False,
                "native_required_options": [],
                "notes": "Runs without a standalone native tg binary.",
            }
            for name in _PYTHON_LOCAL_MCP_TOOLS
        })
        capabilities.update({
            name: {
                "mode": "embedded-safe",
                "native_required": False,
                "embedded_fallback": True,
                "native_required_options": (
                    [
                        "verify",
                        "audit_manifest",
                        "audit_signing_key",
                        "lint_cmd",
                        "test_cmd",
                    ]
                    if name == "tg_rewrite_apply"
                    else []
                ),
                "notes": (
                    "Uses embedded rewrite fallback for simple requests when standalone "
                    "native tg is unavailable."
                ),
            }
            for name in _EMBEDDED_SAFE_MCP_TOOLS
        })
        capabilities.update({
            name: {
                "mode": "native-required",
                "native_required": True,
                "embedded_fallback": False,
                "native_required_options": [],
                "notes": "Requires a standalone native tg binary.",
            }
            for name in _NATIVE_REQUIRED_MCP_TOOLS
        })
        capabilities["tg_agent_capsule"]["notes"] = (
            "Runs without a standalone native tg binary for normal capsules; optional "
            "gpu_device_ids run a selected GPU evidence probe and report sidecar-routed "
            "GPU as unsupported."
        )
    capabilities.update({
        name: {
            "mode": "python-local",
            "native_required": False,
            "embedded_fallback": False,
            "native_required_options": [],
            "notes": "Always-on tool: runs without a standalone native tg binary.",
        }
        for name in _SINGLETON_MCP_TOOLS
    })
    for name in _META_MCP_TOOLS:
        entry = dict(_META_MCP_TOOL_CAPABILITIES[name])
        actions = cast(dict[str, dict[str, object]], entry["actions"])
        # Aggregate top-level native_required/embedded_fallback (True if ANY action needs it)
        # so a client reading only the flat fields -- the shape every legacy tool entry
        # already has -- still gets a correct, if coarser, signal; `actions` carries the
        # precise per-action detail for a client that reads deeper.
        entry["native_required"] = any(bool(flags["native_required"]) for flags in actions.values())
        entry["embedded_fallback"] = any(
            bool(flags["embedded_fallback"]) for flags in actions.values()
        )
        entry["native_required_options"] = []
        capabilities[name] = entry
    return capabilities


_MCP_TOOL_CAPABILITIES: dict[str, dict[str, object]] = _build_mcp_tool_capabilities()


def _mcp_capabilities_payload() -> dict[str, Any]:
    native_tg, native_error = _self._resolve_native_tg_binary_for_mcp()
    native_path: str | None = None
    if native_tg is not None:
        native_path = str(native_tg)
        if native_path.startswith("\\\\?\\UNC\\"):
            native_path = "\\\\" + native_path[len("\\\\?\\UNC\\") :]
        elif native_path.startswith("\\\\?\\"):
            native_path = native_path[len("\\\\?\\") :]
    native_tg_payload: dict[str, Any] = {
        "available": native_tg is not None,
        "path": native_path,
    }
    if native_error is not None:
        native_tg_payload["error"] = native_error
    return {
        "version": _json_output_version(),
        "mcp_contract_version": _TG_MCP_SERVER_CONTRACT_VERSION,
        "schema_version": _json_output_version(),
        "routing_backend": "MCPRuntime",
        "routing_reason": "mcp-capabilities",
        "sidecar_used": False,
        "mcp_protocol_version": types.LATEST_PROTOCOL_VERSION,
        "mcp_supported_protocol_versions": list(SUPPORTED_PROTOCOL_VERSIONS),
        "cli_version": _mcp_server_version(),
        "native_tg": native_tg_payload,
        "embedded_rewrite": {
            "available": _self._embedded_rewrite_available(),
        },
        "tools": [
            {"name": name, **capability}
            for name, capability in sorted(_MCP_TOOL_CAPABILITIES.items())
        ],
    }


def _inject_mcp_contract_fields(result_json: str) -> str:
    """H9: inject mcp_contract_version and schema_version into every tool JSON envelope.

    Operates on the final serialized string so it works uniformly across all tool
    code-paths regardless of which builder produced the underlying dict.  No-ops for
    non-dict JSON (arrays, primitives) to stay safe.

    M14 (retired behavior): ``mcp_contract_version`` is a HARD assignment -- the
    central const (``_TG_MCP_SERVER_CONTRACT_VERSION``) ALWAYS wins. The retired form
    was ``setdefault``, which let a tool payload's OWN top-level ``mcp_contract_version``
    literal (a stale hardcode, or a forked value copied from an older contract) WIN and
    skirt the central const -- a forked literal defeats the point of a single source of
    truth. ``schema_version`` deliberately stays ``setdefault``: it is the JSON-output
    version only by default, and a tool with a DOMAIN meaning for that exact key keeps
    it (``tg_doctor`` documents top-level ``schema_version: 2`` as the doctor JSON
    schema version, distinct from the MCP output version; clobbering it would break
    consumers -- see harness_api.md).
    """
    try:
        payload = json.loads(result_json)
    except (json.JSONDecodeError, ValueError):
        return result_json
    if not isinstance(payload, dict):
        return result_json
    payload["mcp_contract_version"] = _self._TG_MCP_SERVER_CONTRACT_VERSION
    payload.setdefault("schema_version", _json_output_version())
    # AGT-07 Task 09: construct the typed CompletenessEvidence record internally and project
    # back out via to_legacy_dict() for this tool's existing external `incomplete` shape --
    # behavior-preserving (byte-identical to the prior direct unified_incomplete_envelope call).
    payload.setdefault("incomplete", _completeness.project(payload).to_legacy_dict())
    return json.dumps(payload, indent=2)


# audit A1: plan-bound apply / TOCTOU. tg_rewrite_plan emits a stable plan_digest
# derived from the normalized request plus the sorted pre-image content hashes of
# every site it would touch. tg_rewrite_apply can be passed expected_plan_digest /
# expected_match_count; when supplied they are recomputed against the *current*
# tree before any edit is written and the apply is refused with code="plan_drift"
# if reality diverged from what was previewed. Enforced only when supplied so the
# default plan -> apply flow stays fully back-compatible.
_PLAN_DIGEST_VERSION = "tg-plan-digest-v1"


@_register_tool  # type: ignore
def tg_mcp_capabilities(summary: bool = False) -> str:
    """Report MCP tool availability for the current runtime."""
    try:
        if summary:
            from tensor_grep.cli.mcp_compact import (
                build_capabilities_summary_payload,
                finalize_capabilities_summary,
            )

            native_tg, _native_error = _self._resolve_native_tg_binary_for_mcp()
            summary_payload = build_capabilities_summary_payload(
                version=_json_output_version(),
                mcp_contract_version=_self._TG_MCP_SERVER_CONTRACT_VERSION,
                cli_version=_mcp_server_version(),
                native_available=native_tg is not None,
                embedded_rewrite_available=bool(_self._embedded_rewrite_available()),
                legacy_tools_enabled=bool(_legacy_tools_enabled()),
                total_tools=len(_MCP_TOOL_CAPABILITIES),
                meta_tools=sorted(_META_MCP_TOOLS),
                singleton_tools=sorted(_SINGLETON_MCP_TOOLS),
            )
            injected = _self._inject_mcp_contract_fields(json.dumps(summary_payload, indent=2))
            return finalize_capabilities_summary(injected)
        return _self._inject_mcp_contract_fields(
            json.dumps(_self._mcp_capabilities_payload(), indent=2)
        )
    except Exception as exc:
        _log_tool_exception("tg_mcp_capabilities", exc)
        return json.dumps(
            {
                "version": _json_output_version(),
                "error": {
                    "code": "internal_error",
                    "message": f"Capabilities failed: {_safe_exception_class_name(exc)}",
                },
            },
            indent=2,
        )


def tg_sql(
    query: str,
    path: str = ".",
    limit: int = 100,
    max_repo_files: int = _DEFAULT_MCP_REPO_SCAN_LIMIT,
    deadline: float = 2.0,
    scan_deadline: float = 30.0,
) -> str:
    """Execute a read-only SQL query over AST symbols and imports (for tg_batch kind='sql')."""
    import contextlib
    import io

    import typer

    from tensor_grep.cli.sql_query import sql_command

    try:
        confined_path = str(_confine_mcp_path(path, label="path"))
    except PathConfinementError as exc:
        return json.dumps(
            {
                "version": _json_output_version(),
                "error": {"code": "invalid_input", "message": str(exc)},
            },
            indent=2,
        )
    buf = io.StringIO()
    try:
        with contextlib.redirect_stdout(buf):
            sql_command(
                arg1=confined_path,
                arg2=query,
                query_file=None,
                limit=limit,
                max_repo_files=max_repo_files,
                deadline=deadline,
                scan_deadline=scan_deadline,
                json_output=True,
            )
    except (typer.Exit, SystemExit):
        pass
    raw = buf.getvalue().strip()
    try:
        parsed = json.loads(raw) if raw else {}
    except json.JSONDecodeError:
        parsed = {
            "error": {"code": "internal_error", "message": "sql_command returned invalid JSON"}
        }
    if isinstance(parsed, dict) and isinstance(parsed.get("error"), str):
        parsed["error"] = {"code": "invalid_input", "message": parsed["error"]}
    return _self._inject_mcp_contract_fields(json.dumps(parsed, indent=2))


@_register_tool  # type: ignore
def tg_discover_tools(intent: str, limit: int = 5) -> str:
    """Search the full MCP tool catalog by intent and return top matching tools."""
    try:
        from tensor_grep.cli.mcp_tool_discovery import discover_mcp_tools

        return _self._inject_mcp_contract_fields(discover_mcp_tools(intent, limit=limit))
    except Exception as exc:
        _log_tool_exception("tg_discover_tools", exc)
        return json.dumps(_sanitized_tool_error("tg_discover_tools", exc), indent=2)
