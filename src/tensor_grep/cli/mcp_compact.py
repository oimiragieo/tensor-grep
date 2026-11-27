"""MCP response compaction, capability summaries, and TSCG schema tightening (arXiv:2605.04107)."""

from __future__ import annotations

import json
import re
from typing import Any

from tensor_grep.cli.detail_tier import _COMPACT_OMITTED_KEYS, _COMPACT_TRIM_ORDER

CAPABILITIES_SUMMARY_MAX_BYTES = 3072
_COMPACT_CAPABILITIES_MAX_BYTES = CAPABILITIES_SUMMARY_MAX_BYTES
COMPACT_RESULT_MAX_BYTES = 4000

_WHITESPACE_RE = re.compile(r"\s+")
_PREFIX_OPTIONAL_RE = re.compile(r"^(?:\(optional\)\s*|optional[.:]\s+)", re.IGNORECASE)
_SUFFIX_OPTIONAL_RE = re.compile(r"\s*\(optional\)(?=\.?$)", re.IGNORECASE)

_BOILERPLATE_REPLACEMENTS: tuple[tuple[str, str], ...] = (
    ("Defaults to ", "default="),
    ("Default: ", "default="),
    ("Maximum number of ", "Max "),
    ("maximum number of ", "max "),
    ("Maximum ", "Max "),
    ("maximum ", "max "),
    ("Confined to the MCP server root", "MCP-root confined"),
    ("File or directory to inventory.", "Target path."),
    ("Query text used to ", "Query to "),
)

_ACTIONABLE_EVIDENCE_KEYS: frozenset[str] = frozenset({
    "primary_target",
    "candidate_edit_targets",
    "rollback",
    "call_site_evidence",
    "context_consistency",
})

_EVIDENCE_OMIT_ORDER: tuple[str, ...] = (
    "call_site_evidence",
    "candidate_edit_targets",
    "rollback",
    "context_consistency",
    "primary_target",
)

_MANDATORY_ENVELOPE_KEYS: frozenset[str] = frozenset({
    "mcp_contract_version",
    "version",
    "schema_version",
    "routing_backend",
    "routing_reason",
    "sidecar_used",
    "error",
    "result_incomplete",
    "incomplete_reason",
    "incomplete",
    "compact_truncated",
})

_DEFAULT_DIAGNOSTIC_KEYS: frozenset[str] = frozenset({
    "coverage",
    "coverage_summary",
    "symbol_cache",
    "daemon_response_cache",
    "serve_cache",
    "provider_agreement",
    "provider_status",
    "ranking_quality",
    "raw_context_ref",
    "gpu_acceleration",
})

# Action-specific (primary_deliverable_keys, omitted_diagnostic_keys) profiles.
_ACTION_COMPACT_PROFILES: dict[str, tuple[frozenset[str], frozenset[str]]] = {
    "capsule": (
        frozenset({
            "primary_target",
            "context_consistency",
            "omissions",
            "rollback",
            "call_site_evidence",
        }),
        _COMPACT_OMITTED_KEYS,
    ),
    "pack": (
        frozenset({
            "navigation_pack",
            "file_matches",
            "files",
            "test_matches",
            "tests",
            "token_budget",
        }),
        _DEFAULT_DIAGNOSTIC_KEYS
        | frozenset({
            "scan_remediation",
            "file_summaries",
            "imports",
            "symbols",
            "related_paths",
        }),
    ),
    "render": (
        frozenset({
            "rendered_context",
            "sections",
            "token_estimate",
            "truncated",
            "omitted_sections",
            "context_consistency",
            "source_budget",
        }),
        _DEFAULT_DIAGNOSTIC_KEYS
        | frozenset({
            "graph_trust_summary",
            "scan_remediation",
            "file_summaries",
            "imports",
            "symbols",
            "related_paths",
            "file_matches",
            "test_matches",
            "files",
            "sources",
            "navigation_pack",
            "candidate_edit_targets",
            "plan",
            "edit_plan_seed",
        }),
    ),
    "edit_plan": (
        frozenset({
            "plan",
            "candidate_edit_targets",
            "primary_target",
            "edit_order",
            "confidence",
            "ask_user_before_editing",
        }),
        _DEFAULT_DIAGNOSTIC_KEYS
        | frozenset({
            "graph_trust_summary",
            "scan_remediation",
            "file_summaries",
            "imports",
            "symbols",
            "related_paths",
            "file_matches",
            "test_matches",
            "files",
            "tests",
            "navigation_pack",
            "edit_plan_seed",
        }),
    ),
    "orient": (
        frozenset({
            "path",
            "central_files",
            "entry_points",
            "symbol_map",
            "token_estimate",
            "token_budget_label",
            "truncated",
        }),
        frozenset({
            "symbol_cache",
            "deweighted_trees",
            "auto_deweight",
            "suggested_ignore",
            "suggested_scope",
        }),
    ),
    "repo_map": (
        frozenset({
            "path",
            "files",
            "scan_limit",
        }),
        frozenset({
            "coverage",
            "coverage_summary",
            "symbol_cache",
            "scan_remediation",
            "imports",
            "related_paths",
        }),
    ),
    "doctor": (
        frozenset({
            "root",
            "platform",
            "python_version",
            "installation_health",
            "native_tg_binary_exists",
            "rust_core_extension_available",
            "search_acceleration_backend",
            "rust_binary_version_status",
            "python_package_version_status",
            "gpu",
            "ast_grep",
            "lsp",
        }),
        frozenset({
            "skipped_native_tg_binaries",
            "path_tg_candidates",
            "fresh_shell_path_tg_candidates",
            "shell_escaping_guidance",
            "env",
            "checkout_environment",
            "shadow_launchers",
            "lsp_provider_items",
            "lsp_providers",
            "ast_cache",
            "resident_worker",
            "session_daemon",
            "dense_model",
        }),
    ),
    "devices": (
        frozenset({
            "platform",
            "has_gpu",
            "device_count",
            "routable_device_ids",
            "devices",
        }),
        frozenset(),
    ),
}


def _infer_action_profile(
    payload: dict[str, Any], action: str | None
) -> tuple[frozenset[str], frozenset[str]]:
    if action and action in _ACTION_COMPACT_PROFILES:
        return _ACTION_COMPACT_PROFILES[action]
    if "rendered_context" in payload:
        return _ACTION_COMPACT_PROFILES["render"]
    if "plan" in payload or "candidate_edit_targets" in payload:
        return _ACTION_COMPACT_PROFILES["edit_plan"]
    if "navigation_pack" in payload or "file_matches" in payload:
        return _ACTION_COMPACT_PROFILES["pack"]
    if "capsule_kind" in payload or "primary_target" in payload:
        return _ACTION_COMPACT_PROFILES["capsule"]
    if "central_files" in payload and "entry_points" in payload:
        return _ACTION_COMPACT_PROFILES["orient"]
    if "doctor_schema_version" in payload or "installation_health" in payload:
        return _ACTION_COMPACT_PROFILES["doctor"]
    if "routable_device_ids" in payload and "devices" in payload:
        return _ACTION_COMPACT_PROFILES["devices"]
    if "files" in payload and "scan_limit" in payload:
        return _ACTION_COMPACT_PROFILES["repo_map"]
    return frozenset(), _DEFAULT_DIAGNOSTIC_KEYS


def _dump_compact_bytes(payload: dict[str, Any]) -> bytes:
    return json.dumps(payload, ensure_ascii=True, separators=(",", ":")).encode("utf-8")


def _truncate_value_for_budget(
    value: Any, max_str: int = 120, max_list: int = 3, max_dict: int = 6
) -> Any:
    if isinstance(value, str):
        if len(value) <= max_str:
            return value
        return value[:max_str] + "..."
    if isinstance(value, list):
        return [
            _truncate_value_for_budget(v, max_str, max_list, max_dict) for v in value[:max_list]
        ]
    if isinstance(value, dict):
        items = list(value.items())[:max_dict]
        return {k: _truncate_value_for_budget(v, max_str, max_list, max_dict) for k, v in items}
    return value


def _shrink_string_to_fit(
    payload: dict[str, Any],
    key: str,
    max_bytes: int,
    *,
    measure_fn: Any = None,
) -> bool:
    val = payload.get(key)
    if not isinstance(val, str) or not val:
        return False
    size_of = measure_fn or (lambda p: len(_dump_compact_bytes(p)))
    lo, hi = 0, len(val)
    best = ""
    while lo <= hi:
        mid = (lo + hi) // 2
        candidate = val[:mid] + ("..." if mid < len(val) else "")
        payload[key] = candidate
        if size_of(payload) <= max_bytes:
            best = candidate
            lo = mid + 1
        else:
            hi = mid - 1
    payload[key] = best
    return size_of(payload) <= max_bytes


def _append_or_increment_omission_list(entries: list[Any], omitted_counts: dict[str, int]) -> None:
    by_section = {
        entry.get("section"): entry
        for entry in entries
        if isinstance(entry, dict) and entry.get("reason") == "compact_budget"
    }
    for section, count in omitted_counts.items():
        existing_entry = by_section.get(section)
        if existing_entry is not None:
            existing_entry["omitted_count"] = int(existing_entry.get("omitted_count", 0)) + count
        else:
            new_entry = {"section": section, "omitted_count": count, "reason": "compact_budget"}
            entries.append(new_entry)
            by_section[section] = new_entry


def _record_omissions(bounded: dict[str, Any], omitted_counts: dict[str, int]) -> None:
    if not omitted_counts:
        return
    bounded["compact_truncated"] = True
    if isinstance(bounded.get("omitted_sections"), list):
        _append_or_increment_omission_list(bounded["omitted_sections"], omitted_counts)
        return
    existing = bounded.get("omissions")
    if isinstance(existing, dict):
        compact_omitted = existing.get("compact_omitted")
        merged = dict(compact_omitted) if isinstance(compact_omitted, dict) else {}
        for section, count in omitted_counts.items():
            merged[section] = int(merged.get(section, 0)) + count
        existing["compact_omitted"] = merged
        if isinstance(existing.get("omitted_section_count"), int):
            existing["omitted_section_count"] += sum(omitted_counts.values())
        return
    if isinstance(existing, list):
        _append_or_increment_omission_list(existing, omitted_counts)
        return
    bounded["omissions"] = {
        "compact_omitted": dict(omitted_counts),
        "omitted_count": sum(omitted_counts.values()),
    }


def _bound_non_dict_payload(wrapper: dict[str, Any], *, max_bytes: int) -> str:
    for max_str, max_list, max_dict in (
        (1500, 12, 12),
        (500, 5, 6),
        (120, 3, 4),
        (40, 1, 2),
        (8, 1, 1),
    ):
        candidate = {
            "compact_truncated": True,
            **{
                k: _truncate_value_for_budget(v, max_str, max_list, max_dict)
                for k, v in wrapper.items()
            },
        }
        raw = _dump_compact_bytes(candidate)
        if len(raw) <= max_bytes:
            return raw.decode("utf-8")
    return json.dumps({"compact_truncated": True}, ensure_ascii=True, separators=(",", ":"))


def compact_mcp_json_result(
    raw_json: str, *, action: str | None = None, max_bytes: int | None = None
) -> str:
    """Apply action-aware, bounded <=4000-byte compaction to a serialized MCP JSON tool response."""
    limit = max(128, int(COMPACT_RESULT_MAX_BYTES if max_bytes is None else max_bytes))
    try:
        payload = json.loads(raw_json)
    except (json.JSONDecodeError, ValueError):
        if len(raw_json.encode("utf-8")) <= limit:
            return raw_json
        return _bound_non_dict_payload({"raw_preview": raw_json}, max_bytes=limit)
    if not isinstance(payload, dict):
        if len(raw_json.encode("utf-8")) <= limit:
            return raw_json
        return _bound_non_dict_payload({"value": payload}, max_bytes=limit)

    primary_keys, omitted_keys = _infer_action_profile(payload, action)
    protected_keys = _MANDATORY_ENVELOPE_KEYS | primary_keys
    effective_omit = omitted_keys - protected_keys

    bounded: dict[str, Any] = {}
    trimmed = False
    initial_omitted_evidence: dict[str, int] = {}
    for key, value in payload.items():
        if key in effective_omit:
            trimmed = True
            if key in _ACTIONABLE_EVIDENCE_KEYS:
                initial_omitted_evidence[key] = (
                    len(value) if isinstance(value, list) and value else 1
                )
            continue
        bounded[key] = value
    if initial_omitted_evidence:
        _record_omissions(bounded, initial_omitted_evidence)

    gaps = bounded.get("resolution_gaps")
    if isinstance(gaps, list) and len(gaps) > 10:
        bounded["resolution_gaps_truncated_count"] = len(gaps) - 10
        bounded["resolution_gaps"] = gaps[:10]
        trimmed = True

    if trimmed:
        bounded["compact_truncated"] = True

    serialized = _dump_compact_bytes(bounded)
    if len(serialized) <= limit:
        return serialized.decode("utf-8")

    # Trim secondary keys first, preserving mandatory envelope, primary deliverables, and evidence.
    secondary_protected = (
        protected_keys | _ACTIONABLE_EVIDENCE_KEYS | {"omissions", "omitted_sections"}
    )
    for trim_key in _COMPACT_TRIM_ORDER:
        if trim_key in bounded and trim_key not in secondary_protected:
            bounded.pop(trim_key, None)
            bounded["compact_truncated"] = True
            serialized = _dump_compact_bytes(bounded)
            if len(serialized) <= limit:
                return serialized.decode("utf-8")

    for key in sorted(
        (k for k in bounded if k not in secondary_protected),
        key=lambda k: len(json.dumps(bounded[k], ensure_ascii=True)),
        reverse=True,
    ):
        bounded.pop(key, None)
        bounded["compact_truncated"] = True
        serialized = _dump_compact_bytes(bounded)
        if len(serialized) <= limit:
            return serialized.decode("utf-8")

    # Next, trim primary deliverables while keeping mandatory envelope fields intact.
    bounded["compact_truncated"] = True
    if isinstance(bounded.get("rendered_context"), str) and _shrink_string_to_fit(
        bounded, "rendered_context", limit
    ):
        return _dump_compact_bytes(bounded).decode("utf-8")

    # Whole-entry omission for list-valued actionable evidence (never slice inside structured entries).
    omitted_counts: dict[str, int] = {}
    for key in _EVIDENCE_OMIT_ORDER:
        val = bounded.get(key)
        if isinstance(val, list):
            while val and len(_dump_compact_bytes(bounded)) > limit:
                val.pop()
                omitted_counts[key] = omitted_counts.get(key, 0) + 1
    if omitted_counts:
        _record_omissions(bounded, omitted_counts)
        for key in _EVIDENCE_OMIT_ORDER:
            val = bounded.get(key)
            if isinstance(val, list):
                while val and len(_dump_compact_bytes(bounded)) > limit:
                    val.pop()
                    _record_omissions(bounded, {key: 1})
        if len(_dump_compact_bytes(bounded)) <= limit:
            return _dump_compact_bytes(bounded).decode("utf-8")

    no_slice_keys = (
        _MANDATORY_ENVELOPE_KEYS
        | _ACTIONABLE_EVIDENCE_KEYS
        | {
            "omissions",
            "omitted_sections",
        }
    )
    for max_str, max_list, max_dict in (
        (500, 8, 10),
        (240, 4, 6),
        (120, 3, 4),
        (60, 2, 3),
        (24, 1, 2),
        (8, 1, 1),
    ):
        shrunk: dict[str, Any] = {}
        for k, v in bounded.items():
            if k in no_slice_keys:
                shrunk[k] = v
            else:
                shrunk[k] = _truncate_value_for_budget(v, max_str, max_list, max_dict)
        shrunk["compact_truncated"] = True
        serialized = _dump_compact_bytes(shrunk)
        if len(serialized) <= limit:
            return serialized.decode("utf-8")

    # Whole-entry omission of entire actionable evidence objects rather than slicing inside them.
    for key in _EVIDENCE_OMIT_ORDER:
        if key in bounded:
            bounded.pop(key, None)
            _record_omissions(bounded, {key: 1})
            serialized = _dump_compact_bytes(bounded)
            if len(serialized) <= limit:
                return serialized.decode("utf-8")

    # Last resort: retain ALL mandatory envelope fields (truncating oversized values if any)
    # alongside a bounded omission summary when omissions were recorded.
    minimal: dict[str, Any] = {k: bounded[k] for k in _MANDATORY_ENVELOPE_KEYS if k in bounded}
    if isinstance(bounded.get("omissions"), dict) and bounded["omissions"]:
        minimal["omissions"] = _truncate_value_for_budget(
            bounded["omissions"], max_str=64, max_list=5, max_dict=6
        )
    if isinstance(bounded.get("omitted_sections"), list) and bounded["omitted_sections"]:
        prioritized = [
            e
            for e in bounded["omitted_sections"]
            if isinstance(e, dict) and e.get("reason") == "compact_budget"
        ] + [
            e
            for e in bounded["omitted_sections"]
            if not (isinstance(e, dict) and e.get("reason") == "compact_budget")
        ]
        minimal["omitted_sections"] = _truncate_value_for_budget(
            prioritized, max_str=64, max_list=5, max_dict=6
        )
    minimal["compact_truncated"] = True
    serialized = _dump_compact_bytes(minimal)
    if len(serialized) <= limit:
        return serialized.decode("utf-8")

    shrunk_min: dict[str, Any] = minimal
    for max_str, max_list, max_dict in (
        (500, 2, 3),
        (120, 2, 3),
        (32, 1, 2),
        (8, 1, 1),
        (0, 0, 0),
    ):
        shrunk_min = {}
        for k, v in minimal.items():
            if k == "compact_truncated":
                shrunk_min[k] = True
            elif k in ("omissions", "omitted_sections"):
                if max_dict > 0:
                    shrunk_min[k] = _truncate_value_for_budget(
                        v, max(max_str, 32), max(max_list, 2), max(max_dict, 3)
                    )
                elif isinstance(v, list):
                    budget_entries = [
                        e for e in v if isinstance(e, dict) and e.get("reason") == "compact_budget"
                    ]
                    if budget_entries:
                        shrunk_min[k] = budget_entries[:2]
                elif isinstance(v, dict) and "omitted_count" in v:
                    shrunk_min[k] = {"omitted_count": v["omitted_count"]}
            else:
                shrunk_min[k] = _truncate_value_for_budget(v, max_str, max_list, max_dict)
        serialized = _dump_compact_bytes(shrunk_min)
        if len(serialized) <= limit:
            return serialized.decode("utf-8")

    return _dump_compact_bytes(shrunk_min).decode("utf-8")


def _bound_capabilities_summary_dict(
    payload: dict[str, Any],
    *,
    include_incomplete_overhead: bool,
) -> dict[str, Any]:
    def _wire_bytes(candidate: dict[str, Any]) -> int:
        # Account for _inject_mcp_contract_fields adding `incomplete` and indent=2 formatting.
        if include_incomplete_overhead and "incomplete" not in candidate:
            wired = dict(candidate)
            wired["incomplete"] = {"status": False, "cause": None, "budget_remediable": False}
            return len(json.dumps(wired, indent=2).encode("utf-8"))
        return len(json.dumps(candidate, indent=2).encode("utf-8"))

    if _wire_bytes(payload) <= CAPABILITIES_SUMMARY_MAX_BYTES:
        return payload

    payload["compact_truncated"] = True
    for list_key in ("singleton_tools", "meta_tools"):
        items = payload.get(list_key)
        if isinstance(items, list):
            while items and _wire_bytes(payload) > CAPABILITIES_SUMMARY_MAX_BYTES:
                items.pop()

    shrink_keys = ("cli_version", "routing_reason", "routing_backend", "mcp_contract_version")
    drop_keys = (
        "cli_version",
        "routing_reason",
        "routing_backend",
        "singleton_tools",
        "meta_tools",
        "embedded_rewrite_available",
        "legacy_tools_enabled",
        "native_available",
        "total_tools",
        "sidecar_used",
        "schema_version",
    )
    while _wire_bytes(payload) > CAPABILITIES_SUMMARY_MAX_BYTES:
        progressed = False
        for key in shrink_keys:
            val = payload.get(key)
            if isinstance(val, str) and val:
                _shrink_string_to_fit(
                    payload, key, CAPABILITIES_SUMMARY_MAX_BYTES, measure_fn=_wire_bytes
                )
                progressed = True
                if _wire_bytes(payload) <= CAPABILITIES_SUMMARY_MAX_BYTES:
                    break
        if _wire_bytes(payload) <= CAPABILITIES_SUMMARY_MAX_BYTES:
            break
        if not progressed:
            dropped = False
            for key in drop_keys:
                if key in payload:
                    payload.pop(key, None)
                    dropped = True
                    break
            if not dropped:
                break
    return payload


def build_capabilities_summary_payload(
    *,
    version: int,
    mcp_contract_version: str,
    cli_version: str,
    native_available: bool,
    embedded_rewrite_available: bool,
    legacy_tools_enabled: bool,
    total_tools: int,
    meta_tools: list[str],
    singleton_tools: list[str],
) -> dict[str, Any]:
    """Build a <=3072-byte compact summary of MCP server capabilities."""
    payload: dict[str, Any] = {
        "version": version,
        "mcp_contract_version": mcp_contract_version,
        "schema_version": version,
        "routing_backend": "MCPRuntime",
        "routing_reason": "mcp-capabilities-summary",
        "sidecar_used": False,
        "cli_version": cli_version,
        "native_available": native_available,
        "embedded_rewrite_available": embedded_rewrite_available,
        "legacy_tools_enabled": legacy_tools_enabled,
        "total_tools": total_tools,
        "meta_tools": list(meta_tools),
        "singleton_tools": list(singleton_tools),
    }
    return _bound_capabilities_summary_dict(payload, include_incomplete_overhead=True)


def finalize_capabilities_summary(raw_json: str) -> str:
    """Enforce CAPABILITIES_SUMMARY_MAX_BYTES after contract-field injection."""
    if len(raw_json.encode("utf-8")) <= CAPABILITIES_SUMMARY_MAX_BYTES:
        return raw_json
    try:
        payload = json.loads(raw_json)
    except (json.JSONDecodeError, ValueError):
        return _bound_non_dict_payload(
            {"raw_preview": raw_json}, max_bytes=CAPABILITIES_SUMMARY_MAX_BYTES
        )
    if not isinstance(payload, dict):
        return _bound_non_dict_payload({"value": payload}, max_bytes=CAPABILITIES_SUMMARY_MAX_BYTES)
    bounded = _bound_capabilities_summary_dict(payload, include_incomplete_overhead=False)
    return json.dumps(bounded, indent=2)


def _tighten_description_text(text: str) -> str:
    compact = _WHITESPACE_RE.sub(" ", text).strip()
    compact = _PREFIX_OPTIONAL_RE.sub("", compact)
    compact = _SUFFIX_OPTIONAL_RE.sub("", compact).strip()
    for src, dst in _BOILERPLATE_REPLACEMENTS:
        compact = compact.replace(src, dst)
    return compact


def _collapse_nullable_anyof(node: Any) -> None:
    if isinstance(node, dict):
        any_of = node.get("anyOf")
        if isinstance(any_of, list) and len(any_of) == 2:
            non_null = [item for item in any_of if item != {"type": "null"}]
            if (
                len(non_null) == 1
                and isinstance(non_null[0], dict)
                and set(non_null[0].keys()) <= {"type", "items"}
                and isinstance(non_null[0].get("type"), str)
            ):
                node.pop("anyOf")
                node["type"] = [non_null[0]["type"], "null"]
                if "items" in non_null[0]:
                    node["items"] = non_null[0]["items"]
        desc = node.get("description")
        if isinstance(desc, str):
            node["description"] = _tighten_description_text(desc)
        for value in node.values():
            _collapse_nullable_anyof(value)
    elif isinstance(node, list):
        for item in node:
            _collapse_nullable_anyof(item)


_COMPACT_TOOL_DESCRIPTIONS: dict[str, str] = {
    "tg_navigate": "Symbol nav.",
    "tg_impact": "Impact/blast radius.",
    "tg_query": "Code search.",
    "tg_context": "Context pack/capsule.",
    "tg_explore": "Repo map/orient/doctor.",
    "tg_session": "Sessions (mutates).",
    "tg_scan": "Security scans.",
    "tg_audit": "Audit/signing gates.",
    "tg_checkpoint": "Checkpoints (mutates).",
    "tg_rewrite": "Plan/apply AST rewrites; apply mutates (verify diff).",
    "tg_mcp_capabilities": "Capabilities.",
    "tg_classify_logs": "Log anomalies.",
    "tg_batch": "Batch <=16 $prev/$steps[i]; return_final_only; 120s max.",
    "tg_coverage": "Cache coverage.",
    "tg_discover_tools": "Discover tools.",
}


def _strip_property_defaults(node: Any, *, _in_properties_map: bool = False) -> None:
    if isinstance(node, dict):
        if _in_properties_map:
            for prop_schema in node.values():
                if isinstance(prop_schema, dict):
                    default_val = prop_schema.get("default", 0)
                    if default_val is None or default_val is False:
                        prop_schema.pop("default", None)
                _strip_property_defaults(prop_schema, _in_properties_map=False)
            return
        node.pop("title", None)
        for key, value in node.items():
            _strip_property_defaults(value, _in_properties_map=(key == "properties"))
    elif isinstance(node, list):
        for item in node:
            _strip_property_defaults(item, _in_properties_map=False)


def tighten_mcp_tool_schemas(mcp_server_obj: Any) -> None:
    """Apply semantics-preserving TSCG tightening to registered compact MCP tool schemas."""
    tool_manager = getattr(mcp_server_obj, "_tool_manager", None)
    list_tools = getattr(tool_manager, "list_tools", None)
    if not callable(list_tools):
        return
    for tool in list_tools():
        tool_name = getattr(tool, "name", None)
        if isinstance(tool_name, str) and tool_name in _COMPACT_TOOL_DESCRIPTIONS:
            tool.description = _COMPACT_TOOL_DESCRIPTIONS[tool_name]
        elif isinstance(getattr(tool, "description", None), str):
            tool.description = _tighten_description_text(tool.description)
        if isinstance(getattr(tool, "parameters", None), dict):
            _collapse_nullable_anyof(tool.parameters)
            _strip_property_defaults(tool.parameters)
        if isinstance(getattr(tool, "output_schema", None), dict):
            _collapse_nullable_anyof(tool.output_schema)
            _strip_property_defaults(tool.output_schema)
