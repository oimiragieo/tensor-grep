"""Compatibility facade for the modular MCP server under ``tensor_grep.cli.mcp``."""

from __future__ import annotations

import json as json
import os as os
import re as re
import subprocess as subprocess
import sys
import time as time
from pathlib import Path as Path
from typing import TYPE_CHECKING

import anyio as anyio
from mcp import types as types
from mcp.server.mcpserver import MCPServer as MCPServer

from tensor_grep.backends.base import BackendExecutionError as BackendExecutionError
from tensor_grep.backends.cpu_backend import (
    compute_native_walk_deadline as compute_native_walk_deadline,
)
from tensor_grep.backends.cpu_backend import (
    native_walk_deadline_exceeded as native_walk_deadline_exceeded,
)
from tensor_grep.backends.ripgrep_backend import RipgrepBackend as RipgrepBackend
from tensor_grep.cli import mcp_serial_execution as _serial_execution
from tensor_grep.cli import mcp_tool_contract as _tool_contract
from tensor_grep.cli.checkpoint_store import CheckpointNotFoundError as CheckpointNotFoundError
from tensor_grep.cli.citations import (
    mcp_agent_capsule_call as mcp_agent_capsule_call,
)
from tensor_grep.cli.citations import (
    mcp_context_render_call as mcp_context_render_call,
)
from tensor_grep.cli.citations import (
    result_citations_json as result_citations_json,
)
from tensor_grep.cli.citations import (
    serialize_mcp_find_result as serialize_mcp_find_result,
)
from tensor_grep.cli.citations import (
    serialize_workspace_citations as serialize_workspace_citations,
)
from tensor_grep.cli.main import (
    _LARGE_ROOT_SCAN_FILE_CEILING as _LARGE_ROOT_SCAN_FILE_CEILING,
)
from tensor_grep.cli.main import (
    _apply_semantic_rerank as _apply_semantic_rerank,
)
from tensor_grep.cli.main import (
    _build_doctor_payload as _build_doctor_payload,
)
from tensor_grep.cli.main import (
    _execute_find as _execute_find,
)
from tensor_grep.cli.main import (
    _format_unbounded_large_root_scan_error as _format_unbounded_large_root_scan_error,
)
from tensor_grep.cli.main import (
    _format_unbounded_vendored_root_scan_error as _format_unbounded_vendored_root_scan_error,
)
from tensor_grep.cli.main import (
    _run_ast_scan_payload as _run_ast_scan_payload,
)
from tensor_grep.cli.main import (
    _search_with_cpu_fallback as _search_with_cpu_fallback,
)
from tensor_grep.cli.main import (
    _set_semantic_rank_fallback_reason as _set_semantic_rank_fallback_reason,
)
from tensor_grep.cli.main import (
    _should_refuse_unbounded_large_root_scan as _should_refuse_unbounded_large_root_scan,
)
from tensor_grep.cli.main import (
    _should_refuse_unbounded_vendored_root_scan as _should_refuse_unbounded_vendored_root_scan,
)
from tensor_grep.cli.mcp_batch_tools import tg_batch as tg_batch
from tensor_grep.cli.mcp_path_errors import PathConfinementError as PathConfinementError
from tensor_grep.cli.orient_capsule import (
    build_orient_capsule_json as build_orient_capsule_json,
)
from tensor_grep.cli.repo_map import (
    _apply_context_token_budget as _apply_context_token_budget,
)
from tensor_grep.cli.repo_map import (
    _deadline_monotonic_from_seconds as _deadline_monotonic_from_seconds,
)
from tensor_grep.cli.repo_map import (
    build_context_pack as build_context_pack,
)
from tensor_grep.cli.repo_map import (
    build_context_render as build_context_render,
)
from tensor_grep.cli.repo_map import (
    build_file_importers as build_file_importers,
)
from tensor_grep.cli.repo_map import (
    build_repo_map as build_repo_map,
)
from tensor_grep.cli.repo_map import (
    build_symbol_blast_radius as build_symbol_blast_radius,
)
from tensor_grep.cli.repo_map import (
    build_symbol_blast_radius_render as build_symbol_blast_radius_render,
)
from tensor_grep.cli.repo_map import (
    build_symbol_callers as build_symbol_callers,
)
from tensor_grep.cli.repo_map import (
    build_symbol_defs as build_symbol_defs,
)
from tensor_grep.cli.repo_map import (
    build_symbol_impact as build_symbol_impact,
)
from tensor_grep.cli.repo_map import (
    build_symbol_refs as build_symbol_refs,
)
from tensor_grep.cli.repo_map import (
    build_symbol_source as build_symbol_source,
)
from tensor_grep.cli.runtime_paths import (
    resolve_native_tg_binary as resolve_native_tg_binary,
)
from tensor_grep.core.config import SearchConfig as SearchConfig
from tensor_grep.core.hardware.device_inventory import (
    collect_device_inventory as collect_device_inventory,
)
from tensor_grep.core.pipeline import ConfigurationError as ConfigurationError
from tensor_grep.core.pipeline import Pipeline as Pipeline
from tensor_grep.core.result import SearchResult as SearchResult
from tensor_grep.core.result import merge_runtime_routing as merge_runtime_routing
from tensor_grep.io.directory_scanner import DirectoryScanner as DirectoryScanner

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
    _self = sys.modules[__name__]

from tensor_grep.cli.mcp.capabilities import (  # noqa: E402
    _EMBEDDED_SAFE_MCP_TOOLS as _EMBEDDED_SAFE_MCP_TOOLS,
)
from tensor_grep.cli.mcp.capabilities import (  # noqa: E402
    _MCP_TOOL_CAPABILITIES as _MCP_TOOL_CAPABILITIES,
)
from tensor_grep.cli.mcp.capabilities import (  # noqa: E402
    _META_MCP_TOOL_CAPABILITIES as _META_MCP_TOOL_CAPABILITIES,
)
from tensor_grep.cli.mcp.capabilities import (  # noqa: E402
    _META_MCP_TOOLS as _META_MCP_TOOLS,
)
from tensor_grep.cli.mcp.capabilities import (  # noqa: E402
    _NATIVE_REQUIRED_MCP_TOOLS as _NATIVE_REQUIRED_MCP_TOOLS,
)
from tensor_grep.cli.mcp.capabilities import (  # noqa: E402
    _PYTHON_LOCAL_MCP_TOOLS as _PYTHON_LOCAL_MCP_TOOLS,
)
from tensor_grep.cli.mcp.capabilities import (  # noqa: E402
    _SINGLETON_MCP_TOOLS as _SINGLETON_MCP_TOOLS,
)
from tensor_grep.cli.mcp.capabilities import (  # noqa: E402
    _build_mcp_tool_capabilities as _build_mcp_tool_capabilities,
)
from tensor_grep.cli.mcp.capabilities import (  # noqa: E402
    _inject_mcp_contract_fields as _inject_mcp_contract_fields,
)
from tensor_grep.cli.mcp.capabilities import (  # noqa: E402
    _mcp_capabilities_payload as _mcp_capabilities_payload,
)
from tensor_grep.cli.mcp.capabilities import (  # noqa: E402
    tg_discover_tools as tg_discover_tools,
)
from tensor_grep.cli.mcp.capabilities import (  # noqa: E402
    tg_mcp_capabilities as tg_mcp_capabilities,
)
from tensor_grep.cli.mcp.capabilities import (  # noqa: E402
    tg_sql as tg_sql,
)
from tensor_grep.cli.mcp.security import (  # noqa: E402
    _INDEX_ROUTING_BACKEND as _INDEX_ROUTING_BACKEND,
)
from tensor_grep.cli.mcp.security import (  # noqa: E402
    _INDEX_ROUTING_REASON as _INDEX_ROUTING_REASON,
)
from tensor_grep.cli.mcp.security import (  # noqa: E402
    _MAX_INLINE_RULES as _MAX_INLINE_RULES,
)
from tensor_grep.cli.mcp.security import (  # noqa: E402
    _MAX_INLINE_RULES_CHARS as _MAX_INLINE_RULES_CHARS,
)
from tensor_grep.cli.mcp.security import (  # noqa: E402
    _NATIVE_TG_REMEDIATION as _NATIVE_TG_REMEDIATION,
)
from tensor_grep.cli.mcp.security import (  # noqa: E402
    _PLAN_DIGEST_VERSION as _PLAN_DIGEST_VERSION,
)
from tensor_grep.cli.mcp.security import (  # noqa: E402
    _REWRITE_INTERNAL_ERROR_SIGNATURES as _REWRITE_INTERNAL_ERROR_SIGNATURES,
)
from tensor_grep.cli.mcp.security import (  # noqa: E402
    _REWRITE_IO_ERROR_SIGNATURES as _REWRITE_IO_ERROR_SIGNATURES,
)
from tensor_grep.cli.mcp.security import (  # noqa: E402
    _REWRITE_PATTERN_ERROR_SIGNATURES as _REWRITE_PATTERN_ERROR_SIGNATURES,
)
from tensor_grep.cli.mcp.security import (  # noqa: E402
    _REWRITE_ROUTING_BACKEND as _REWRITE_ROUTING_BACKEND,
)
from tensor_grep.cli.mcp.security import (  # noqa: E402
    _REWRITE_ROUTING_REASON as _REWRITE_ROUTING_REASON,
)
from tensor_grep.cli.mcp.security import (  # noqa: E402
    _TRUSTED_EXCEPTION_CLASSES as _TRUSTED_EXCEPTION_CLASSES,
)
from tensor_grep.cli.mcp.security import (  # noqa: E402
    _WINDOWS_VARIADIC_METAVAR_RE as _WINDOWS_VARIADIC_METAVAR_RE,
)
from tensor_grep.cli.mcp.security import (  # noqa: E402
    _broad_root_scan_refusal_result as _broad_root_scan_refusal_result,
)
from tensor_grep.cli.mcp.security import (  # noqa: E402
    _confine_mcp_path as _confine_mcp_path,
)
from tensor_grep.cli.mcp.security import (  # noqa: E402
    _confine_read_path as _confine_read_path,
)
from tensor_grep.cli.mcp.security import (  # noqa: E402
    _confine_write_path as _confine_write_path,
)
from tensor_grep.cli.mcp.security import (  # noqa: E402
    _envelope_base as _envelope_base,
)
from tensor_grep.cli.mcp.security import (  # noqa: E402
    _json_output_version as _json_output_version,
)
from tensor_grep.cli.mcp.security import (  # noqa: E402
    _log_tool_exception as _log_tool_exception,
)
from tensor_grep.cli.mcp.security import (  # noqa: E402
    _mcp_broad_root_scan_refusal as _mcp_broad_root_scan_refusal,
)
from tensor_grep.cli.mcp.security import (  # noqa: E402
    _mcp_root as _mcp_root,
)
from tensor_grep.cli.mcp.security import (  # noqa: E402
    _meta_confinement_error as _meta_confinement_error,
)
from tensor_grep.cli.mcp.security import (  # noqa: E402
    _meta_envelope as _meta_envelope,
)
from tensor_grep.cli.mcp.security import (  # noqa: E402
    _meta_invalid_input_error as _meta_invalid_input_error,
)
from tensor_grep.cli.mcp.security import (  # noqa: E402
    _meta_missing_param_error as _meta_missing_param_error,
)
from tensor_grep.cli.mcp.security import (  # noqa: E402
    _meta_unknown_action_error as _meta_unknown_action_error,
)
from tensor_grep.cli.mcp.security import (  # noqa: E402
    _meta_workspace_roots_cap_error as _meta_workspace_roots_cap_error,
)
from tensor_grep.cli.mcp.security import (  # noqa: E402
    _record_generated_audit_manifest as _record_generated_audit_manifest,
)
from tensor_grep.cli.mcp.security import (  # noqa: E402
    _repo_root as _repo_root,
)
from tensor_grep.cli.mcp.security import (  # noqa: E402
    _safe_exception_class_name as _safe_exception_class_name,
)
from tensor_grep.cli.mcp.security import (  # noqa: E402
    _sanitized_tool_error as _sanitized_tool_error,
)
from tensor_grep.cli.mcp.security import (  # noqa: E402
    _sanitized_tool_error_text as _sanitized_tool_error_text,
)
from tensor_grep.cli.mcp.server import (  # noqa: E402
    _DEFAULT_MCP_CONTEXT_MAX_TOKENS as _DEFAULT_MCP_CONTEXT_MAX_TOKENS,
)
from tensor_grep.cli.mcp.server import (  # noqa: E402
    _DEFAULT_MCP_FIND_MAX_TOKENS as _DEFAULT_MCP_FIND_MAX_TOKENS,
)
from tensor_grep.cli.mcp.server import (  # noqa: E402
    _DEFAULT_MCP_REPO_SCAN_LIMIT as _DEFAULT_MCP_REPO_SCAN_LIMIT,
)
from tensor_grep.cli.mcp.server import (  # noqa: E402
    _LEGACY_NOTE as _LEGACY_NOTE,
)
from tensor_grep.cli.mcp.server import (  # noqa: E402
    _MAX_MCP_STDIO_FIRST_LINE_BYTES as _MAX_MCP_STDIO_FIRST_LINE_BYTES,
)
from tensor_grep.cli.mcp.server import (  # noqa: E402
    _MAX_MCP_STDIO_HEADER_BYTES as _MAX_MCP_STDIO_HEADER_BYTES,
)
from tensor_grep.cli.mcp.server import (  # noqa: E402
    _MAX_MCP_STDIO_HEADER_LINES as _MAX_MCP_STDIO_HEADER_LINES,
)
from tensor_grep.cli.mcp.server import (  # noqa: E402
    _MAX_MCP_STDIO_MESSAGE_BYTES as _MAX_MCP_STDIO_MESSAGE_BYTES,
)
from tensor_grep.cli.mcp.server import (  # noqa: E402
    _TG_MCP_SERVER_CONTRACT_VERSION as _TG_MCP_SERVER_CONTRACT_VERSION,
)
from tensor_grep.cli.mcp.server import (  # noqa: E402
    _annotate_legacy_tools as _annotate_legacy_tools,
)
from tensor_grep.cli.mcp.server import (  # noqa: E402
    _apply_mcp_server_metadata as _apply_mcp_server_metadata,
)
from tensor_grep.cli.mcp.server import (  # noqa: E402
    _legacy_tools_enabled as _legacy_tools_enabled,
)
from tensor_grep.cli.mcp.server import (  # noqa: E402
    _mcp_server_version as _mcp_server_version,
)
from tensor_grep.cli.mcp.server import (  # noqa: E402
    _read_bounded_line as _read_bounded_line,
)
from tensor_grep.cli.mcp.server import (  # noqa: E402
    _read_stdio_message_payload as _read_stdio_message_payload,
)
from tensor_grep.cli.mcp.server import (  # noqa: E402
    _register_legacy_tool as _register_legacy_tool,
)
from tensor_grep.cli.mcp.server import (  # noqa: E402
    _register_tool as _register_tool,
)
from tensor_grep.cli.mcp.server import (  # noqa: E402
    _run_mcp_stdio_async as _run_mcp_stdio_async,
)
from tensor_grep.cli.mcp.server import (  # noqa: E402
    _stdio_server_accepting_content_length as _stdio_server_accepting_content_length,
)
from tensor_grep.cli.mcp.server import (  # noqa: E402
    mcp as mcp,
)
from tensor_grep.cli.mcp.server import (  # noqa: E402
    run_mcp_server as run_mcp_server,
)
from tensor_grep.cli.mcp_coverage_tool import coverage_tool as coverage_tool  # noqa: E402

_register_tool(tg_batch)

# Imported AFTER _confine_mcp_path / _mcp_root / _register_legacy_tool / mcp are bound above.
from tensor_grep.cli.mcp_rewrite_tools import (  # noqa: E402, I001
    _audit_diff_error as _audit_diff_error,
    _audit_history_error as _audit_history_error,
    _audit_manifest_error as _audit_manifest_error,
    _build_index_search_command as _build_index_search_command,
    _build_rewrite_command as _build_rewrite_command,
    _classify_native_rewrite_failure as _classify_native_rewrite_failure,
    _compute_plan_digest as _compute_plan_digest,
    _embedded_rewrite_available as _embedded_rewrite_available,
    _execute_embedded_rewrite_json as _execute_embedded_rewrite_json,
    _execute_rewrite_diff_command as _execute_rewrite_diff_command,
    _execute_rewrite_json_command as _execute_rewrite_json_command,
    _index_search_error as _index_search_error,
    _native_unavailable_error as _native_unavailable_error,
    _normalize_apply_result_payload as _normalize_apply_result_payload,
    _normalize_rewrite_json_payload as _normalize_rewrite_json_payload,
    _produce_rewrite_plan_json as _produce_rewrite_plan_json,
    _resolve_native_tg_binary_for_mcp as _resolve_native_tg_binary_for_mcp,
    _review_bundle_error as _review_bundle_error,
    _rewrite_envelope as _rewrite_envelope,
    _rewrite_error as _rewrite_error,
    _ruleset_scan_error as _ruleset_scan_error,
    _run_rewrite_subprocess as _run_rewrite_subprocess,
    _validate_rewrite_inputs as _validate_rewrite_inputs,
    execute_rewrite_apply_json as execute_rewrite_apply_json,
    execute_rewrite_plan_json as execute_rewrite_plan_json,
)
from tensor_grep.cli.mcp_audit_tools import (  # noqa: E402
    _checkpoint_meta as _checkpoint_meta,
    tg_audit_diff as tg_audit_diff,
    tg_audit_history as tg_audit_history,
    tg_audit_manifest_verify as tg_audit_manifest_verify,
    tg_checkpoint_create as tg_checkpoint_create,
    tg_checkpoint_list as tg_checkpoint_list,
    tg_checkpoint_undo as tg_checkpoint_undo,
    tg_index_search as tg_index_search,
    tg_review_bundle_create as tg_review_bundle_create,
    tg_review_bundle_verify as tg_review_bundle_verify,
    tg_rewrite_apply as tg_rewrite_apply,
    tg_rewrite_diff as tg_rewrite_diff,
    tg_rewrite_plan as tg_rewrite_plan,
    tg_ruleset_scan as tg_ruleset_scan,
    tg_rulesets as tg_rulesets,
)
from tensor_grep.cli.mcp_symbol_tools import (  # noqa: E402
    tg_file_importers as tg_file_importers,
    tg_file_imports as tg_file_imports,
    tg_symbol_blast_radius as tg_symbol_blast_radius,
    tg_symbol_blast_radius_plan as tg_symbol_blast_radius_plan,
    tg_symbol_blast_radius_render as tg_symbol_blast_radius_render,
    tg_symbol_callers as tg_symbol_callers,
    tg_symbol_defs as tg_symbol_defs,
    tg_symbol_impact as tg_symbol_impact,
    tg_symbol_refs as tg_symbol_refs,
    tg_symbol_source as tg_symbol_source,
)

from tensor_grep.cli.mcp.explore import (  # noqa: E402
    tg_coverage as tg_coverage,
)
from tensor_grep.cli.mcp.explore import (  # noqa: E402
    tg_devices as tg_devices,
)
from tensor_grep.cli.mcp.explore import (  # noqa: E402
    tg_doctor as tg_doctor,
)
from tensor_grep.cli.mcp.explore import (  # noqa: E402
    tg_explore as tg_explore,
)
from tensor_grep.cli.mcp.explore import (  # noqa: E402
    tg_orient as tg_orient,
)
from tensor_grep.cli.mcp.explore import (  # noqa: E402
    tg_repo_map as tg_repo_map,
)
from tensor_grep.cli.mcp.context import (  # noqa: E402
    _AGENT_ROUTING_REASON as _AGENT_ROUTING_REASON,
)
from tensor_grep.cli.mcp.context import (  # noqa: E402
    _agent_capsule_error as _agent_capsule_error,
)
from tensor_grep.cli.mcp.context import (  # noqa: E402
    tg_agent_capsule as tg_agent_capsule,
)
from tensor_grep.cli.mcp.context import (  # noqa: E402
    tg_context as tg_context,
)
from tensor_grep.cli.mcp.context import (  # noqa: E402
    tg_context_pack as tg_context_pack,
)
from tensor_grep.cli.mcp.context import (  # noqa: E402
    tg_context_render as tg_context_render,
)
from tensor_grep.cli.mcp.context import (  # noqa: E402
    tg_edit_plan as tg_edit_plan,
)
from tensor_grep.cli.mcp.session import (  # noqa: E402
    _effective_auto_refresh as _effective_auto_refresh,
)
from tensor_grep.cli.mcp.session import (  # noqa: E402
    _session_error_payload as _session_error_payload,
)
from tensor_grep.cli.mcp.session import (  # noqa: E402
    _session_exception_payload as _session_exception_payload,
)
from tensor_grep.cli.mcp.session import (  # noqa: E402
    tg_session as tg_session,
)
from tensor_grep.cli.mcp.session import (  # noqa: E402
    tg_session_blast_radius as tg_session_blast_radius,
)
from tensor_grep.cli.mcp.session import (  # noqa: E402
    tg_session_blast_radius_plan as tg_session_blast_radius_plan,
)
from tensor_grep.cli.mcp.session import (  # noqa: E402
    tg_session_blast_radius_render as tg_session_blast_radius_render,
)
from tensor_grep.cli.mcp.session import (  # noqa: E402
    tg_session_context as tg_session_context,
)
from tensor_grep.cli.mcp.session import (  # noqa: E402
    tg_session_context_render as tg_session_context_render,
)
from tensor_grep.cli.mcp.session import (  # noqa: E402
    tg_session_edit_plan as tg_session_edit_plan,
)
from tensor_grep.cli.mcp.session import (  # noqa: E402
    tg_session_file_importers as tg_session_file_importers,
)
from tensor_grep.cli.mcp.session import (  # noqa: E402
    tg_session_list as tg_session_list,
)
from tensor_grep.cli.mcp.session import (  # noqa: E402
    tg_session_open as tg_session_open,
)
from tensor_grep.cli.mcp.session import (  # noqa: E402
    tg_session_refresh as tg_session_refresh,
)
from tensor_grep.cli.mcp.session import (  # noqa: E402
    tg_session_show as tg_session_show,
)
from tensor_grep.cli.incompleteness import (  # noqa: E402, F401
    incomplete_class_fragment as _incomplete_class_fragment,
)
from tensor_grep.cli.mcp.query import (  # noqa: E402
    _FIND_ROUTING_BACKEND as _FIND_ROUTING_BACKEND,
)
from tensor_grep.cli.mcp.query import (  # noqa: E402
    _MAX_WORKSPACE_ROOTS as _MAX_WORKSPACE_ROOTS,
)
from tensor_grep.cli.mcp.query import (  # noqa: E402
    _FIND_ROUTING_REASON as _FIND_ROUTING_REASON,
)
from tensor_grep.cli.mcp.query import (  # noqa: E402
    _apply_selected_gpu_defaults as _apply_selected_gpu_defaults,
)
from tensor_grep.cli.mcp.query import (  # noqa: E402
    _finalize_aggregate_result as _finalize_aggregate_result,
)
from tensor_grep.cli.mcp.query import (  # noqa: E402
    _find_invalid_input as _find_invalid_input,
)
from tensor_grep.cli.mcp.query import (  # noqa: E402
    _merge_count_metadata as _merge_count_metadata,
)
from tensor_grep.cli.mcp.query import (  # noqa: E402
    _merge_runtime_routing as _merge_runtime_routing,
)
from tensor_grep.cli.mcp.query import (  # noqa: E402
    _routing_payload as _routing_payload,
)
from tensor_grep.cli.mcp.query import (  # noqa: E402
    _routing_summary as _routing_summary,
)
from tensor_grep.cli.mcp.query import (  # noqa: E402
    _selected_gpu_execution_defaults as _selected_gpu_execution_defaults,
)
from tensor_grep.cli.mcp.query import (  # noqa: E402
    _tg_query_dispatch as _tg_query_dispatch,
)
from tensor_grep.cli.mcp.query import (  # noqa: E402
    tg_find as tg_find,
)
from tensor_grep.cli.mcp.query import (  # noqa: E402
    tg_query as tg_query,
)
from tensor_grep.cli.mcp.query import (  # noqa: E402
    tg_search as tg_search,
)
from tensor_grep.cli.mcp.ast_query import (  # noqa: E402
    _ast_error_result as _ast_error_result,
)
from tensor_grep.cli.mcp.ast_query import (  # noqa: E402
    tg_ast_search as tg_ast_search,
)
from tensor_grep.cli.mcp.ast_query import (  # noqa: E402
    tg_classify_logs as tg_classify_logs,
)
from tensor_grep.cli.mcp.dispatch import (  # noqa: E402
    tg_audit as tg_audit,
)
from tensor_grep.cli.mcp.dispatch import (  # noqa: E402
    tg_checkpoint as tg_checkpoint,
)
from tensor_grep.cli.mcp.dispatch import (  # noqa: E402
    tg_impact as tg_impact,
)
from tensor_grep.cli.mcp.dispatch import (  # noqa: E402
    tg_navigate as tg_navigate,
)
from tensor_grep.cli.mcp.dispatch import (  # noqa: E402
    tg_rewrite as tg_rewrite,
)
from tensor_grep.cli.mcp.dispatch import (  # noqa: E402
    tg_scan as tg_scan,
)

_annotate_legacy_tools()
_serial_execution.install_serial_tool_execution(mcp)
