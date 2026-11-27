"""MCP meta-tool dispatchers for navigate, impact, scan, audit, checkpoint, and rewrite."""

from __future__ import annotations

import json
import sys
from typing import TYPE_CHECKING

from tensor_grep.cli import mcp_checkpoint_label_args as _checkpoint_label_args
from tensor_grep.cli import mcp_tool_contract as _tool_contract
from tensor_grep.cli.mcp.security import (
    _confine_mcp_path,
    _log_tool_exception,
    _meta_confinement_error,
    _meta_envelope,
    _meta_missing_param_error,
    _meta_unknown_action_error,
    _sanitized_tool_error,
    _sanitized_tool_error_text,
)
from tensor_grep.cli.mcp.server import (
    _DEFAULT_MCP_REPO_SCAN_LIMIT,
    _register_tool,
    mcp,
)
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


@_register_tool  # type: ignore
def tg_navigate(
    action: NavigateAction,
    symbol: str | None = None,
    file: str | None = None,
    path: str = ".",
    provider: str = "native",
    max_repo_files: int = _DEFAULT_MCP_REPO_SCAN_LIMIT,
    deadline: float | None = None,
) -> str:
    """Symbol/file navigation: defs, source, refs, callers (symbol); imports, importers (file). Confined to MCP root."""
    try:
        try:
            path = str(_confine_mcp_path(path, label="path"))
            if file is not None:
                file = str(_confine_mcp_path(file, label="file"))
        except PathConfinementError as exc:
            return _meta_confinement_error("tg_navigate", action, exc)

        try:
            if action == "defs":
                if symbol is None:
                    return _meta_missing_param_error("tg_navigate", action, "symbol")
                return _self.tg_symbol_defs(
                    symbol=symbol, path=path, provider=provider, max_repo_files=max_repo_files
                )
            if action == "source":
                if symbol is None:
                    return _meta_missing_param_error("tg_navigate", action, "symbol")
                return _self.tg_symbol_source(
                    symbol=symbol, path=path, provider=provider, max_repo_files=max_repo_files
                )
            if action == "refs":
                if symbol is None:
                    return _meta_missing_param_error("tg_navigate", action, "symbol")
                return _self.tg_symbol_refs(
                    symbol=symbol,
                    path=path,
                    provider=provider,
                    max_repo_files=max_repo_files,
                    deadline=deadline,
                )
            if action == "callers":
                if symbol is None:
                    return _meta_missing_param_error("tg_navigate", action, "symbol")
                return _self.tg_symbol_callers(
                    symbol=symbol,
                    path=path,
                    provider=provider,
                    max_repo_files=max_repo_files,
                    deadline=deadline,
                )
            if action == "imports":
                if file is None:
                    return _meta_missing_param_error("tg_navigate", action, "file")
                return _self.tg_file_imports(file=file)
            if action == "importers":
                if file is None:
                    return _meta_missing_param_error("tg_navigate", action, "file")
                return _self.tg_file_importers(
                    file=file, path=path, max_repo_files=max_repo_files, deadline=deadline
                )
            return _meta_unknown_action_error("tg_navigate", action, _TG_NAVIGATE_ACTIONS)
        except Exception as exc:  # never a raw exception across the MCP boundary
            payload = _meta_envelope(tool="tg_navigate", action=action)
            payload["error"] = _sanitized_tool_error("tg_navigate", exc)
            return json.dumps(payload, indent=2)
    except Exception as exc:
        _log_tool_exception("tg_navigate", exc)
        return _sanitized_tool_error_text("tg_navigate", exc)


@_register_tool  # type: ignore
def tg_impact(
    action: ImpactAction,
    symbol: str | None = None,
    path: str = ".",
    max_depth: int = 3,
    max_files: int = 3,
    max_symbols: int = 5,
    max_sources: int = 5,
    max_symbols_per_file: int = 6,
    max_render_chars: int | None = None,
    optimize_context: bool = False,
    render_profile: str = "full",
    profile: bool = False,
    provider: str = "native",
    max_repo_files: int = _DEFAULT_MCP_REPO_SCAN_LIMIT,
    deadline: float | None = None,
) -> str:
    """Symbol change-impact: impact, blast_radius, blast_radius_plan, blast_radius_render (symbol required)."""
    try:
        try:
            path = str(_confine_mcp_path(path, label="path"))
        except PathConfinementError as exc:
            return _meta_confinement_error("tg_impact", action, exc)

        if symbol is None:
            return _meta_missing_param_error("tg_impact", action, "symbol")

        try:
            if action == "impact":
                return _self.tg_symbol_impact(
                    symbol=symbol,
                    path=path,
                    provider=provider,
                    max_repo_files=max_repo_files,
                    deadline=deadline,
                )
            if action == "blast_radius":
                return _self.tg_symbol_blast_radius(
                    symbol=symbol,
                    path=path,
                    max_depth=max_depth,
                    provider=provider,
                    max_repo_files=max_repo_files,
                    deadline=deadline,
                )
            if action == "blast_radius_plan":
                return _self.tg_symbol_blast_radius_plan(
                    symbol=symbol,
                    path=path,
                    max_depth=max_depth,
                    max_files=max_files,
                    max_symbols=max_symbols,
                    provider=provider,
                    max_repo_files=max_repo_files,
                )
            if action == "blast_radius_render":
                return _self.tg_symbol_blast_radius_render(
                    symbol=symbol,
                    path=path,
                    max_depth=max_depth,
                    max_files=max_files,
                    max_sources=max_sources,
                    max_symbols_per_file=max_symbols_per_file,
                    max_render_chars=max_render_chars,
                    optimize_context=optimize_context,
                    render_profile=render_profile,
                    profile=profile,
                    provider=provider,
                    max_repo_files=max_repo_files,
                )
            return _meta_unknown_action_error("tg_impact", action, _TG_IMPACT_ACTIONS)
        except Exception as exc:
            payload = _meta_envelope(tool="tg_impact", action=action)
            payload["error"] = _sanitized_tool_error("tg_impact", exc)
            return json.dumps(payload, indent=2)
    except Exception as exc:
        _log_tool_exception("tg_impact", exc)
        return _sanitized_tool_error_text("tg_impact", exc)


# [SEC] Cap the NUMBER of workspace_roots tg_query fans out across in one call, and (below)


@_register_tool  # type: ignore
def tg_scan(
    action: ScanAction,
    ruleset: str | None = None,
    inline_rules: str | None = None,
    path: str = ".",
    language: str | None = None,
    glob: str | None = None,
    file_type: str | None = None,
    max_depth: int | None = None,
    allow_broad_generated_scan: bool = False,
    baseline_path: str | None = None,
    write_baseline: str | None = None,
    suppressions_path: str | None = None,
    write_suppressions: str | None = None,
    justification: str | None = None,
    include_evidence_snippets: bool = False,
    max_evidence_snippets_per_file: int = 1,
    max_evidence_snippet_chars: int = 120,
) -> str:
    """AST ruleset scanning: scan (ruleset or inline_rules YAML), rulesets (list built-in)."""
    try:
        try:
            path = str(_confine_mcp_path(path, label="path"))
        except PathConfinementError as exc:
            return _meta_confinement_error("tg_scan", action, exc)

        try:
            if action == "scan":
                return _self.tg_ruleset_scan(
                    ruleset=ruleset,
                    inline_rules=inline_rules,
                    path=path,
                    language=language,
                    glob=glob,
                    file_type=file_type,
                    max_depth=max_depth,
                    allow_broad_generated_scan=allow_broad_generated_scan,
                    baseline_path=baseline_path,
                    write_baseline=write_baseline,
                    suppressions_path=suppressions_path,
                    write_suppressions=write_suppressions,
                    justification=justification,
                    include_evidence_snippets=include_evidence_snippets,
                    max_evidence_snippets_per_file=max_evidence_snippets_per_file,
                    max_evidence_snippet_chars=max_evidence_snippet_chars,
                )
            if action == "rulesets":
                return _self.tg_rulesets()
            return _meta_unknown_action_error("tg_scan", action, _TG_SCAN_ACTIONS)
        except Exception as exc:
            payload = _meta_envelope(tool="tg_scan", action=action)
            payload["error"] = _sanitized_tool_error("tg_scan", exc)
            return json.dumps(payload, indent=2)
    except Exception as exc:
        _log_tool_exception("tg_scan", exc)
        return _sanitized_tool_error_text("tg_scan", exc)


@_register_tool  # type: ignore
def tg_audit(
    action: AuditAction,
    manifest_path: str | None = None,
    signing_key: str | None = None,
    previous_manifest: str | None = None,
    current_manifest: str | None = None,
    path: str = ".",
    scan_path: str | None = None,
    checkpoint_id: str | None = None,
    output_path: str | None = None,
    bundle_path: str | None = None,
) -> str:
    """Rewrite audit & review bundles: manifest_verify (manifest_path), history, diff (previous_manifest, current_manifest), bundle_create (manifest_path), bundle_verify (bundle_path). signing_key requires TG_MCP_ALLOW_AUDIT_SIGNING_KEY_READ."""
    try:
        try:
            path = str(_confine_mcp_path(path, label="path"))
            if manifest_path is not None:
                manifest_path = str(_confine_mcp_path(manifest_path, label="manifest_path"))
            if previous_manifest is not None:
                previous_manifest = str(
                    _confine_mcp_path(previous_manifest, label="previous_manifest")
                )
            if current_manifest is not None:
                current_manifest = str(
                    _confine_mcp_path(current_manifest, label="current_manifest")
                )
            if scan_path is not None:
                scan_path = str(_confine_mcp_path(scan_path, label="scan_path"))
            if output_path is not None:
                output_path = str(_confine_mcp_path(output_path, label="output_path"))
            if bundle_path is not None:
                bundle_path = str(_confine_mcp_path(bundle_path, label="bundle_path"))
        except PathConfinementError as exc:
            return _meta_confinement_error("tg_audit", action, exc)

        try:
            if action == "manifest_verify":
                if manifest_path is None:
                    return _meta_missing_param_error("tg_audit", action, "manifest_path")
                return _self.tg_audit_manifest_verify(
                    manifest_path=manifest_path,
                    signing_key=signing_key,
                    previous_manifest=previous_manifest,
                )
            if action == "history":
                return _self.tg_audit_history(path=path)
            if action == "diff":
                if previous_manifest is None or current_manifest is None:
                    return _meta_missing_param_error(
                        "tg_audit", action, "previous_manifest and current_manifest"
                    )
                return _self.tg_audit_diff(
                    previous_manifest=previous_manifest, current_manifest=current_manifest
                )
            if action == "bundle_create":
                if manifest_path is None:
                    return _meta_missing_param_error("tg_audit", action, "manifest_path")
                return _self.tg_review_bundle_create(
                    manifest_path=manifest_path,
                    scan_path=scan_path,
                    checkpoint_id=checkpoint_id,
                    previous_manifest=previous_manifest,
                    output_path=output_path,
                )
            if action == "bundle_verify":
                if bundle_path is None:
                    return _meta_missing_param_error("tg_audit", action, "bundle_path")
                return _self.tg_review_bundle_verify(bundle_path=bundle_path)
            return _meta_unknown_action_error("tg_audit", action, _TG_AUDIT_ACTIONS)
        except Exception as exc:
            payload = _meta_envelope(tool="tg_audit", action=action)
            payload["error"] = _sanitized_tool_error("tg_audit", exc)
            return json.dumps(payload, indent=2)
    except Exception as exc:
        _log_tool_exception("tg_audit", exc)
        return _sanitized_tool_error_text("tg_audit", exc)


@_register_tool  # type: ignore
def tg_checkpoint(
    action: CheckpointAction,
    checkpoint_id: str | None = None,
    path: str = ".",
    label: str | None = None,
) -> str:
    """Workspace edit checkpoints: create (optional label), list, undo (checkpoint_id)."""
    try:
        return _self._checkpoint_meta(
            action=action,
            checkpoint_id=checkpoint_id,
            path=path,
            label=label,
        )
    except Exception as exc:
        _log_tool_exception("tg_checkpoint", exc)
        return _sanitized_tool_error_text("tg_checkpoint", exc)


_checkpoint_label_args.install_checkpoint_label_arguments(mcp)


@_register_tool  # type: ignore
def tg_rewrite(
    action: RewriteAction,
    pattern: str | None = None,
    replacement: str | None = None,
    lang: str | None = None,
    path: str = ".",
    verify: bool = False,
    checkpoint: bool = False,
    audit_manifest: str | None = None,
    audit_signing_key: str | None = None,
    lint_cmd: str | None = None,
    test_cmd: str | None = None,
    policy: str | None = None,
    expected_plan_digest: str | None = None,
    expected_match_count: int | None = None,
) -> str:
    """Native AST rewrites (pattern, replacement, lang required): plan, apply (mutates; checkpoint/verify/audit_manifest; audit_signing_key requires TG_MCP_ALLOW_AUDIT_SIGNING_KEY_READ; lint_cmd/test_cmd require TG_MCP_ALLOW_VALIDATION_COMMANDS=1), diff."""
    try:
        try:
            path = str(_confine_mcp_path(path, label="path"))
        except PathConfinementError as exc:
            return _meta_confinement_error("tg_rewrite", action, exc)

        if pattern is None or replacement is None or lang is None:
            return _meta_missing_param_error("tg_rewrite", action, "pattern, replacement, and lang")

        try:
            if action == "plan":
                return _self.tg_rewrite_plan(
                    pattern=pattern, replacement=replacement, lang=lang, path=path
                )
            if action == "apply":
                return _self.tg_rewrite_apply(
                    pattern=pattern,
                    replacement=replacement,
                    lang=lang,
                    path=path,
                    verify=verify,
                    checkpoint=checkpoint,
                    audit_manifest=audit_manifest,
                    audit_signing_key=audit_signing_key,
                    lint_cmd=lint_cmd,
                    test_cmd=test_cmd,
                    policy=policy,
                    expected_plan_digest=expected_plan_digest,
                    expected_match_count=expected_match_count,
                )
            if action == "diff":
                return _self.tg_rewrite_diff(
                    pattern=pattern, replacement=replacement, lang=lang, path=path
                )
            return _meta_unknown_action_error("tg_rewrite", action, _TG_REWRITE_ACTIONS)
        except Exception as exc:
            payload = _meta_envelope(tool="tg_rewrite", action=action)
            payload["error"] = _sanitized_tool_error("tg_rewrite", exc)
            return json.dumps(payload, indent=2)
    except Exception as exc:
        _log_tool_exception("tg_rewrite", exc)
        return _sanitized_tool_error_text("tg_rewrite", exc)
