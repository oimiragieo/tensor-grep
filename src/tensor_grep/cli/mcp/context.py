"""MCP context pack, edit plan, and agent capsule tools."""

from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import TYPE_CHECKING, Any, Literal

from tensor_grep.cli import mcp_arg_validation as _av
from tensor_grep.cli import mcp_tool_contract as _tool_contract
from tensor_grep.cli.citations import (
    mcp_agent_capsule_call,
    mcp_context_render_call,
)
from tensor_grep.cli.mcp.security import (
    _confine_mcp_path,
    _envelope_base,
    _log_tool_exception,
    _meta_confinement_error,
    _meta_envelope,
    _meta_invalid_input_error,
    _meta_missing_param_error,
    _meta_unknown_action_error,
    _sanitized_tool_error,
    _sanitized_tool_error_text,
)
from tensor_grep.cli.mcp.server import (
    _DEFAULT_MCP_CONTEXT_MAX_TOKENS,
    _DEFAULT_MCP_REPO_SCAN_LIMIT,
    _register_legacy_tool,
    _register_tool,
)
from tensor_grep.cli.mcp_path_errors import PathConfinementError
from tensor_grep.core.pipeline import ConfigurationError

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

_AGENT_ROUTING_REASON = "agent-context-capsule"


def _agent_capsule_error(message: str, *, code: str, query: str, path: str) -> str:
    payload = _envelope_base(
        routing_backend="RepoMap",
        routing_reason=_AGENT_ROUTING_REASON,
        include_schema_version=False,
    )
    payload["query"] = query
    payload["path"] = str(Path(path).expanduser())
    payload["error"] = {"code": code, "message": message}
    return json.dumps(payload, indent=2)


@_register_legacy_tool  # type: ignore
def tg_context_pack(
    query: str, path: str = ".", max_tokens: int | None = _DEFAULT_MCP_CONTEXT_MAX_TOKENS
) -> str:
    """
    Return a ranked repository context pack for edit planning.

    Args:
        query: Query text used to rank relevant files, symbols, and tests.
        path: File or directory to inventory.
        max_tokens: Bound the pack for prompt injection (default ~16000; 0/None = unbounded).
    """
    # round-8 security (audit #95 gate): confine the primary path/root param to the MCP root
    # before any scan -- see tg_repo_map for the systemic-finding rationale.
    try:
        try:
            path = str(_confine_mcp_path(path, label="path"))
        except PathConfinementError as exc:
            payload = _envelope_base(
                routing_backend="RepoMap",
                routing_reason="context-pack",
                include_schema_version=False,
            )
            payload["query"] = query
            payload["path"] = "[refused]"
            payload["error"] = {"code": "invalid_input", "message": str(exc)}
            return json.dumps(payload, indent=2)

        try:
            return _self._inject_mcp_contract_fields(
                json.dumps(_self.build_context_pack(query, path, max_tokens=max_tokens), indent=2)
            )
        except FileNotFoundError as exc:
            _log_tool_exception("tg_context_pack", exc)
            payload = _envelope_base(
                routing_backend="RepoMap",
                routing_reason="context-pack",
                include_schema_version=False,
            )
            payload["query"] = query
            payload["path"] = str(Path(path).expanduser())
            payload["error"] = {
                "code": "invalid_input",
                "message": f"Path not found: {path}",
            }
            return json.dumps(payload, indent=2)
        except Exception as exc:  # M11: propagate as structured error, never a raw exception
            payload = _envelope_base(
                routing_backend="RepoMap",
                routing_reason="context-pack",
                include_schema_version=False,
            )
            payload["query"] = query
            payload["path"] = str(Path(path).expanduser())
            payload["error"] = _sanitized_tool_error("tg_context_pack", exc)
            return json.dumps(payload, indent=2)
    except Exception as exc:
        _log_tool_exception("tg_context_pack", exc)
        return _sanitized_tool_error_text("tg_context_pack", exc)


@_register_legacy_tool  # type: ignore
def tg_edit_plan(
    query: str,
    path: str = ".",
    max_files: int = 3,
    max_repo_files: int = _DEFAULT_MCP_REPO_SCAN_LIMIT,
    max_sources: int = 5,
    max_tokens: int | None = None,
    max_symbols: int = 5,
    provider: str = "native",
) -> str:
    """
    Return a machine-readable edit-planning bundle without rendered source text.

    Args:
        query: Query text used to rank edit targets.
        path: File or directory to inventory.
        max_files: Maximum files to include in the plan.
        max_repo_files: Maximum repository files to scan before ranking edit targets.
        max_sources: Maximum related source/span records to retain.
        max_tokens: Accepted for command-surface parity; no rendered source text is emitted.
        max_symbols: Maximum ranked symbols to retain.
        provider: Semantic provider for primary target proof: native, lsp, or hybrid.
    """
    try:
        from tensor_grep.cli.repo_map import build_context_edit_plan

        # round-8 security (audit #95 gate): confine the primary path/root param to the MCP root
        # before any scan -- see tg_repo_map for the systemic-finding rationale.
        try:
            path = str(_confine_mcp_path(path, label="path"))
        except PathConfinementError as exc:
            payload = _envelope_base(
                routing_backend="RepoMap",
                routing_reason="context-edit-plan",
                include_schema_version=False,
            )
            payload["query"] = query
            payload["path"] = "[refused]"
            payload["error"] = {"code": "invalid_input", "message": str(exc)}
            return json.dumps(payload, indent=2)

        try:
            return _self._inject_mcp_contract_fields(
                json.dumps(
                    build_context_edit_plan(
                        query,
                        path,
                        max_files=max_files,
                        max_repo_files=max_repo_files,
                        max_sources=max_sources,
                        max_tokens=max_tokens,
                        max_symbols=max_symbols,
                        semantic_provider=provider,
                    ),
                    indent=2,
                )
            )
        except FileNotFoundError as exc:
            _log_tool_exception("tg_edit_plan", exc)
            payload = _envelope_base(
                routing_backend="RepoMap",
                routing_reason="context-edit-plan",
                include_schema_version=False,
            )
            payload["query"] = query
            payload["path"] = str(Path(path).expanduser())
            payload["error"] = {
                "code": "invalid_input",
                "message": f"Path not found: {path}",
            }
            return json.dumps(payload, indent=2)
        except Exception as exc:  # M11: propagate as structured error, never a raw exception
            payload = _envelope_base(
                routing_backend="RepoMap",
                routing_reason="context-edit-plan",
                include_schema_version=False,
            )
            payload["query"] = query
            payload["path"] = str(Path(path).expanduser())
            payload["error"] = _sanitized_tool_error("tg_edit_plan", exc)
            return json.dumps(payload, indent=2)
    except Exception as exc:
        _log_tool_exception("tg_edit_plan", exc)
        return _sanitized_tool_error_text("tg_edit_plan", exc)


@_register_legacy_tool  # type: ignore
def tg_context_render(
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
    provider: str = "native",
    profile: bool = False,
    citations: bool = False,
) -> str:
    """
    Return a prompt-ready repository context bundle for edit planning.

    Args:
        query: Query text used to rank and render repo context.
        path: File or directory to inventory.
        max_repo_files: Maximum repository files to scan before ranking context.
        max_files / max_sources / max_symbols_per_file / max_render_chars: bundle size caps.
        max_tokens: Bundle bound (default ~16000; 0/None = unbounded); model: token-estimation
            model; optimize_context: strip blank/comment-only source lines.
        render_profile: full, compact, llm, or focused.
        provider: Semantic provider for primary target proof: native, lsp, or hybrid.
        profile: Include a render profiling breakdown.
    """
    # round-8 security (audit #95 gate): confine the primary path/root param to the MCP root
    # before any scan -- see tg_repo_map for the systemic-finding rationale.
    try:
        try:
            path = str(_confine_mcp_path(path, label="path"))
        except PathConfinementError as exc:
            payload = _envelope_base(
                routing_backend="RepoMap",
                routing_reason="context-render",
                include_schema_version=False,
            )
            payload["query"] = query
            payload["path"] = "[refused]"
            payload["error"] = {"code": "invalid_input", "message": str(exc)}
            return json.dumps(payload, indent=2)

        try:
            return mcp_context_render_call(_self, locals(), path, citations)
        except FileNotFoundError as exc:
            _log_tool_exception("tg_context_render", exc)
            payload = _envelope_base(
                routing_backend="RepoMap",
                routing_reason="context-render",
                include_schema_version=False,
            )
            payload["query"] = query
            payload["path"] = str(Path(path).expanduser())
            payload["error"] = {
                "code": "invalid_input",
                "message": f"Path not found: {path}",
            }
            return json.dumps(payload, indent=2)
        except Exception as exc:  # M11: propagate as structured error, never a raw exception
            payload = _envelope_base(
                routing_backend="RepoMap",
                routing_reason="context-render",
                include_schema_version=False,
            )
            payload["query"] = query
            payload["path"] = str(Path(path).expanduser())
            payload["error"] = _sanitized_tool_error("tg_context_render", exc)
            return json.dumps(payload, indent=2)
    except Exception as exc:
        _log_tool_exception("tg_context_render", exc)
        return _sanitized_tool_error_text("tg_context_render", exc)


@_register_legacy_tool  # type: ignore
def tg_agent_capsule(
    query: str,
    path: str = ".",
    max_files: int = 3,
    max_sources: int = 5,
    max_tokens: int | None = 1200,
    max_repo_files: int = _DEFAULT_MCP_REPO_SCAN_LIMIT,
    model: str | None = None,
    provider: str = "native",
    gpu_device_ids: list[int] | None = None,
    gpu_timeout_s: float = 5.0,
    deadline: float | None = None,
    plan_hops: bool = False,
    grounding: Literal["off", "local", "registry"] = "local",
    rerank: Literal["off", "auto", "cross-encoder"] = "off",
    citations: bool = False,
) -> str:
    """
    Return an Actionable Context Capsule for agent edit planning.

    Args:
        query: Natural-language task or symbol query.
        path: File or directory to inventory.
        max_files: Maximum ranked files to consider.
        max_sources: Maximum source snippets to include.
        max_tokens: Token budget for bounded capsule output.
        max_repo_files: Maximum repository files to scan.
        model: Optional model name used for token estimation.
        provider: Semantic provider for primary target proof: native, lsp, or hybrid.
        gpu_device_ids: Optional selected GPU IDs for native route evidence.
        gpu_timeout_s: Maximum seconds for each opt-in GPU evidence command.
        plan_hops: Build at most three stages from existing declaration/caller/test evidence.
        grounding: Dependency metadata mode: off, local (default), or explicit registry requests.
        rerank: Advisory native snippet ordering: off (default), auto, or cross-encoder.
        deadline: Optional wall-clock budget in seconds for the underlying repo-map build
            and capsule render/ranking pass (mirrors `tg agent --deadline` /
            `tg codemap --deadline`). When exceeded, the scan stops and returns a flagged
            partial result instead of running unbounded.
    """
    # round-8 security (audit #95 gate): confine the primary path/root param to the MCP root
    # before any scan -- see tg_repo_map for the systemic-finding rationale.
    try:
        try:
            path = str(_confine_mcp_path(path, label="path"))
        except PathConfinementError as exc:
            return _agent_capsule_error(
                str(exc), code="invalid_input", query=query, path="[refused]"
            )

        refusal = _av.investigation_options_refusal(grounding, rerank)
        if refusal is not None:
            return _agent_capsule_error(refusal, code="invalid_input", query=query, path=path)
        if not Path(path).expanduser().exists():
            return _agent_capsule_error(
                f"Path not found: {path}",
                code="invalid_input",
                query=query,
                path=path,
            )

        try:
            from tensor_grep.cli.agent_capsule import build_agent_capsule

            return mcp_agent_capsule_call(build_agent_capsule, _self, locals(), path, citations)
        except FileNotFoundError as exc:
            _log_tool_exception("tg_agent_capsule", exc)
            return _agent_capsule_error(
                f"Path not found: {path}",
                code="invalid_input",
                query=query,
                path=path,
            )
        except (ValueError, ConfigurationError) as exc:
            _log_tool_exception("tg_agent_capsule", exc)
            return _agent_capsule_error(
                "Invalid input parameter for tg_agent_capsule",
                code="invalid_input",
                query=query,
                path=path,
            )
        except Exception as exc:  # M11: propagate as structured error, never a raw exception
            return _agent_capsule_error(
                _sanitized_tool_error("tg_agent_capsule", exc)["message"],
                code="internal_error",
                query=query,
                path=path,
            )
    except Exception as exc:
        _log_tool_exception("tg_agent_capsule", exc)
        return _sanitized_tool_error_text("tg_agent_capsule", exc)


@_register_tool  # type: ignore
def tg_context(
    action: ContextAction,
    query: str | None = None,
    path: str = ".",
    max_files: int = 3,
    max_repo_files: int = _DEFAULT_MCP_REPO_SCAN_LIMIT,
    max_sources: int = 5,
    max_symbols: int = 5,
    max_symbols_per_file: int = 6,
    max_render_chars: int | None = None,
    max_tokens: int | None = None,
    model: str | None = None,
    optimize_context: bool = False,
    render_profile: str = "full",
    provider: str = "native",
    profile: bool = False,
    gpu_device_ids: list[int] | None = None,
    gpu_timeout_s: float = 5.0,
    deadline: float | None = None,
    plan_hops: bool = False,
    grounding: Literal["off", "local", "registry"] = "local",
    rerank: Literal["off", "auto", "cross-encoder"] = "off",
    citations: bool = False,
    compact: bool = False,
) -> str:
    """Edit-planning context (query required): capsule (1200 tok), edit_plan (targets), pack (~16k), render (pack+source)."""
    try:
        try:
            path = str(_confine_mcp_path(path, label="path"))
        except PathConfinementError as exc:
            return _meta_confinement_error("tg_context", action, exc)

        refusal = _av.investigation_options_refusal(grounding, rerank)
        if refusal is not None:
            return _meta_invalid_input_error("tg_context", action, refusal)
        if compact and citations:
            return _meta_invalid_input_error(
                "tg_context", action, "compact=True cannot be combined with citations=True."
            )
        if query is None:
            return _meta_missing_param_error("tg_context", action, "query")

        try:
            from tensor_grep.cli.mcp_compact import compact_mcp_json_result

            max_tokens_kwargs: dict[str, Any] = (
                {} if max_tokens is None else {"max_tokens": max_tokens}
            )
            result_json: str | None = None
            if action == "pack":
                result_json = _self.tg_context_pack(query=query, path=path, **max_tokens_kwargs)
            elif action == "edit_plan":
                result_json = _self.tg_edit_plan(
                    query=query,
                    path=path,
                    max_files=max_files,
                    max_repo_files=max_repo_files,
                    max_sources=max_sources,
                    max_tokens=max_tokens,
                    max_symbols=max_symbols,
                    provider=provider,
                )
            elif action == "render":
                result_json = _self.tg_context_render(
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
                    provider=provider,
                    profile=profile,
                    citations=citations,
                    **max_tokens_kwargs,
                )
            elif action == "capsule":
                result_json = _self.tg_agent_capsule(
                    query=query,
                    path=path,
                    max_files=max_files,
                    max_sources=max_sources,
                    max_repo_files=max_repo_files,
                    model=model,
                    provider=provider,
                    gpu_device_ids=gpu_device_ids,
                    gpu_timeout_s=gpu_timeout_s,
                    deadline=deadline,
                    plan_hops=plan_hops,
                    grounding=grounding,
                    rerank=rerank,
                    citations=citations,
                    **max_tokens_kwargs,
                )
            else:
                return _meta_unknown_action_error("tg_context", action, _TG_CONTEXT_ACTIONS)
            return compact_mcp_json_result(result_json, action=action) if compact else result_json
        except Exception as exc:
            payload = _meta_envelope(tool="tg_context", action=action)
            payload["error"] = _sanitized_tool_error("tg_context", exc)
            return json.dumps(payload, indent=2)
    except Exception as exc:
        _log_tool_exception("tg_context", exc)
        return _sanitized_tool_error_text("tg_context", exc)
