"""Shared deduplicated <=4000-byte JSON compaction for render and blast-radius CLI commands."""

from __future__ import annotations

import copy
import json
import math
from typing import Any

import typer

from tensor_grep.cli import detail_tier as _detail_tier

COMPACT_MAX_BYTES = 4000

_ENVELOPE_KEYS: frozenset[str] = frozenset({
    "version",
    "schema_version",
    "command",
    "routing_backend",
    "routing_reason",
    "sidecar_used",
    "partial",
    "partial_reason",
    "deadline_limit",
    "result_incomplete",
    "incomplete_reason",
    "not_found",
    "suggestions",
    "compact_truncated",
})

_SAFETY_AND_OMISSION_KEYS: frozenset[str] = frozenset({
    "context_consistency",
    "omissions",
    "omitted_sections",
    "rollback",
    "ask_user_before_editing",
    "confidence",
    "truncated",
})

_DIAGNOSTIC_OMIT_KEYS: frozenset[str] = frozenset({
    "coverage",
    "coverage_summary",
    "provider_agreement",
    "provider_status",
    "symbol_cache",
    "daemon_response_cache",
    "serve_cache",
    "graph_trust_summary",
    "ranking_quality",
    "raw_context_ref",
    "gpu_acceleration",
    "scan_remediation",
    "file_summaries",
    "imports",
    "related_paths",
    "file_matches",
    "test_matches",
    "import_graph_consumers",
    "rendered_caller_tree",
    "edit_plan_seed",
    "navigation_pack",
    "profile",
    "timings",
    "profiling",
    "candidate_symbols",
})

_RENDER_EXTRA_OMIT_KEYS: frozenset[str] = frozenset({
    "plan",
    "candidate_edit_targets",
    "edit_order",
})

_SECONDARY_TRIM_ORDER: tuple[str, ...] = (
    "caveat",
    "candidate_edit_targets",
    "resolution_gaps",
    "candidates",
    "alternative_targets",
    "related_call_sites",
    "dependency_grounding",
    "route_rationale",
    "ambiguity",
    "scan_limit",
    "central_files",
    "symbol_map",
    "validation_plan",
    "plan",
    "suggested_validation_commands",
    "validation_commands",
    "caller_tree",
    "affected_files",
    "callers",
    "sources",
    "source_budget",
    "symbols",
    "tests",
    "files",
)

_LIST_OMIT_ORDER: tuple[str, ...] = (
    "sources",
    "sections",
    "callers",
    "candidate_edit_targets",
    "symbols",
    "tests",
    "files",
    "definitions",
    "edit_order",
)

_BLAST_RADIUS_ROUTING_REASONS: frozenset[str] = frozenset({
    "symbol-blast-radius-render",
    "symbol-blast-radius-plan",
})

_ESSENTIAL_FALLBACK_KEYS: frozenset[str] = frozenset({
    "compact_truncated",
    "omissions",
    "omitted_sections",
    "result_incomplete",
    "incomplete_reason",
    "context_consistency",
    "rollback",
    "ask_user_before_editing",
    "confidence",
    "truncated",
    "version",
    "schema_version",
    "routing_backend",
    "routing_reason",
})

_CORE_CONSISTENCY_KEYS: frozenset[str] = frozenset({
    "primary_file",
    "primary_symbol",
    "truncated_by_compact",
    "primary_file_present",
    "primary_symbol_present",
    "confidence_downgraded",
})


def validate_compact_flags(compact: bool, *, json_output: bool, citations: bool = False) -> None:
    """Validate `--compact` flag combinations for CLI commands."""
    if compact and not json_output:
        raise typer.BadParameter("--compact requires --json.")
    if compact and citations:
        raise typer.BadParameter("--compact cannot be combined with --citations.")


def _json_bytes(payload: dict[str, Any]) -> bytes:
    return json.dumps(payload, ensure_ascii=True, separators=(",", ":")).encode("utf-8")


def _primary_keys_for_payload(payload: dict[str, Any]) -> tuple[frozenset[str], frozenset[str]]:
    reason = str(payload.get("routing_reason", ""))
    if reason == "symbol-blast-radius-plan":
        primary = frozenset({
            "symbol",
            "path",
            "definitions",
            "files",
            "tests",
            "symbols",
            "edit_order",
            "validation_commands",
            "blast_radius_score",
        })
        return primary, _DIAGNOSTIC_OMIT_KEYS
    if reason == "symbol-blast-radius-render":
        primary = frozenset({
            "symbol",
            "path",
            "definitions",
            "rendered_context",
            "sections",
            "token_estimate",
            "render_profile",
            "files",
            "tests",
            "blast_radius_score",
        })
        return primary, _DIAGNOSTIC_OMIT_KEYS | _RENDER_EXTRA_OMIT_KEYS
    # Default: context-render (including --render-profile focused)
    primary = frozenset({
        "query",
        "path",
        "rendered_context",
        "sections",
        "token_estimate",
        "render_profile",
        "source_budget",
        "files",
    })
    return primary, _DIAGNOSTIC_OMIT_KEYS | _RENDER_EXTRA_OMIT_KEYS


def _record_budget_omission(
    bounded: dict[str, Any], section: str, count: int, *, prefer_omissions: bool = False
) -> None:
    if count <= 0:
        return
    bounded["compact_truncated"] = True
    omitted_sections = bounded.get("omitted_sections")
    if not prefer_omissions and isinstance(omitted_sections, list):
        for entry in omitted_sections:
            if (
                isinstance(entry, dict)
                and entry.get("section") == section
                and entry.get("reason") == "compact_budget"
            ):
                entry["omitted_count"] = int(entry.get("omitted_count", 0)) + count
                return
        omitted_sections.append({
            "section": section,
            "omitted_count": count,
            "reason": "compact_budget",
        })
        return
    omissions = bounded.get("omissions")
    if isinstance(omissions, dict):
        compact_omitted = omissions.get("compact_omitted")
        merged = dict(compact_omitted) if isinstance(compact_omitted, dict) else {}
        is_new_section = section not in merged or int(merged.get(section, 0)) == 0
        merged[section] = int(merged.get(section, 0)) + count
        omissions["compact_omitted"] = merged
        if isinstance(omissions.get("omitted_count"), int) and not isinstance(
            omissions.get("omitted_count"), bool
        ):
            omissions["omitted_count"] += count
        else:
            omissions["omitted_count"] = sum(
                int(v) for v in merged.values() if isinstance(v, int) and not isinstance(v, bool)
            )
        if isinstance(omissions.get("omitted_section_count"), int) and not isinstance(
            omissions.get("omitted_section_count"), bool
        ):
            if is_new_section:
                omissions["omitted_section_count"] += 1
        else:
            omissions["omitted_section_count"] = len(merged)
        return
    bounded["omissions"] = {
        "compact_omitted": {section: count},
        "omitted_section_count": 1,
        "omitted_count": count,
    }


def _drop_key_with_omission(bounded: dict[str, Any], key: str) -> None:
    if key not in bounded:
        return
    val = bounded.pop(key)
    count = len(val) if isinstance(val, list) else 1
    if count > 0:
        _record_budget_omission(bounded, key, count)
    else:
        bounded["compact_truncated"] = True


def _downgrade_confidence_after_render_trim(
    bounded: dict[str, Any], *, both_missing: bool = False
) -> None:
    conf = bounded.get("confidence")
    if isinstance(conf, dict):
        conf["compact_confidence_downgraded"] = True
        overall = conf.get("overall")
        if (
            isinstance(overall, (int, float))
            and not isinstance(overall, bool)
            and math.isfinite(float(overall))
        ):
            conf["overall"] = round(min(float(overall), 0.55), 3)
        for key in ("level", "band"):
            val = conf.get(key)
            if isinstance(val, str):
                lowered = val.lower()
                if lowered == "high":
                    conf[key] = "low" if both_missing else "medium"
                elif lowered == "medium" and both_missing:
                    conf[key] = "low"
        reasons = conf.get("downgrade_reasons")
        if isinstance(reasons, list):
            reason = "primary context omitted by compact"
            if reason not in reasons:
                reasons.append(reason)
    elif isinstance(conf, str):
        lowered = conf.lower()
        if lowered == "high":
            bounded["confidence"] = "low" if both_missing else "medium"
        elif lowered == "medium" and both_missing:
            bounded["confidence"] = "low"
    elif (
        isinstance(conf, (int, float)) and not isinstance(conf, bool) and math.isfinite(float(conf))
    ):
        bounded["confidence"] = round(min(float(conf), 0.55), 3)


def _update_context_consistency_after_render_trim(bounded: dict[str, Any]) -> None:
    cc = bounded.get("context_consistency")
    if not isinstance(cc, dict):
        return
    cc["truncated_by_compact"] = True
    rendered = bounded.get("rendered_context")
    rendered_str = rendered if isinstance(rendered, str) else ""
    primary_file = cc.get("primary_file")
    if isinstance(primary_file, str) and primary_file:
        file_present = primary_file in rendered_str
        cc["primary_file_present"] = file_present
        if "rendered_context_includes_primary" in cc:
            cc["rendered_context_includes_primary"] = file_present
    primary_symbol = cc.get("primary_symbol")
    if isinstance(primary_symbol, str) and primary_symbol:
        symbol_present = primary_symbol in rendered_str
        cc["primary_symbol_present"] = symbol_present
        if "rendered_context_includes_primary_symbol" in cc:
            cc["rendered_context_includes_primary_symbol"] = symbol_present
    if cc.get("primary_file_present") is False or cc.get("primary_symbol_present") is False:
        cc["confidence_downgraded"] = True
        _downgrade_confidence_after_render_trim(
            bounded,
            both_missing=(
                cc.get("primary_file_present") is False
                and cc.get("primary_symbol_present") is False
            ),
        )


def _shrink_rendered_context(bounded: dict[str, Any], max_bytes: int) -> bool:
    val = bounded.get("rendered_context")
    if not isinstance(val, str) or not val:
        return False
    target_str = val
    sections = val.split("\n\n")
    if len(sections) > 1:
        for keep in range(len(sections) - 1, 0, -1):
            bounded["rendered_context"] = "\n\n".join(sections[:keep]) + "\n\n..."
            _update_context_consistency_after_render_trim(bounded)
            if len(_json_bytes(bounded)) <= max_bytes:
                return True
        target_str = sections[0]
    lo, hi = 0, len(target_str)
    best = ""
    while lo <= hi:
        mid = (lo + hi) // 2
        candidate = target_str[:mid] + ("..." if mid < len(target_str) else "")
        bounded["rendered_context"] = candidate
        _update_context_consistency_after_render_trim(bounded)
        if len(_json_bytes(bounded)) <= max_bytes:
            best = candidate
            lo = mid + 1
        else:
            hi = mid - 1
    bounded["rendered_context"] = best
    _update_context_consistency_after_render_trim(bounded)
    return len(_json_bytes(bounded)) <= max_bytes


def _truncate_str_field(obj: dict[str, Any], key: str, max_bytes: int) -> None:
    val = obj.get(key)
    if not isinstance(val, str) or not val:
        return
    lo, hi = 0, len(val)
    best = ""
    while lo <= hi:
        mid = (lo + hi) // 2
        obj[key] = val[:mid] + ("..." if mid < len(val) else "")
        if len(_json_bytes(obj)) <= max_bytes:
            best = obj[key]
            lo = mid + 1
        else:
            hi = mid - 1
    obj[key] = best


def _shrink_extreme_fallback(minimal: dict[str, Any], max_bytes: int) -> dict[str, Any]:
    if len(_json_bytes(minimal)) <= max_bytes:
        return minimal

    for key in sorted(
        (k for k in minimal if k not in _ESSENTIAL_FALLBACK_KEYS),
        key=lambda k: len(json.dumps(minimal[k], ensure_ascii=True)),
        reverse=True,
    ):
        minimal.pop(key, None)
        if len(_json_bytes(minimal)) <= max_bytes:
            return minimal

    if isinstance(minimal.get("omitted_sections"), list):
        omitted_list = minimal["omitted_sections"]
        popped_sections = 0
        popped_items = 0
        while omitted_list and len(_json_bytes(minimal)) > max_bytes:
            removed_entry = omitted_list.pop()
            popped_sections += 1
            if isinstance(removed_entry, dict):
                raw_cnt = removed_entry.get("omitted_count", 1)
                popped_items += (
                    raw_cnt if isinstance(raw_cnt, int) and not isinstance(raw_cnt, bool) else 1
                )
            else:
                popped_items += 1
        if popped_sections > 0:
            existing_omissions = minimal.get("omissions")
            if isinstance(existing_omissions, dict):
                raw_count = existing_omissions.get("omitted_count")
                existing_count = (
                    raw_count
                    if isinstance(raw_count, int) and not isinstance(raw_count, bool)
                    else 0
                )
                raw_section_count = existing_omissions.get("omitted_section_count")
                existing_section_count = (
                    raw_section_count
                    if isinstance(raw_section_count, int)
                    and not isinstance(raw_section_count, bool)
                    else 0
                )
                existing_omissions["omitted_count"] = max(existing_count, popped_items)
                existing_omissions["omitted_section_count"] = max(
                    existing_section_count, popped_sections
                )
            elif "omissions" not in minimal:
                minimal["omissions"] = {
                    "omitted_section_count": popped_sections,
                    "omitted_count": popped_items,
                }
    if len(_json_bytes(minimal)) <= max_bytes:
        return minimal

    ask = minimal.get("ask_user_before_editing")
    if isinstance(ask, dict):
        compact_ask: dict[str, Any] = {"required": bool(ask.get("required"))}
        raw_reason = ask.get("reason")
        if isinstance(raw_reason, str) and raw_reason.strip():
            compact_ask["reason"] = raw_reason.strip()[:160]
        else:
            raw_reasons = ask.get("reasons")
            if (
                isinstance(raw_reasons, list)
                and raw_reasons
                and isinstance(raw_reasons[0], str)
                and raw_reasons[0].strip()
            ):
                compact_ask["reason"] = raw_reasons[0].strip()[:160]
        minimal["ask_user_before_editing"] = compact_ask
        if len(_json_bytes(minimal)) <= max_bytes:
            return minimal

    conf = minimal.get("confidence")
    if isinstance(conf, dict):
        for subkey in sorted(
            (k for k in conf if k not in {"overall", "level", "compact_confidence_downgraded"}),
            key=lambda k: len(json.dumps(conf[k], ensure_ascii=True)),
            reverse=True,
        ):
            conf.pop(subkey, None)
            if len(_json_bytes(minimal)) <= max_bytes:
                return minimal

    omissions = minimal.get("omissions")
    if isinstance(omissions, dict):
        compact_omitted = omissions.get("compact_omitted")
        if isinstance(compact_omitted, dict):
            for subkey in sorted(
                compact_omitted,
                key=lambda k: len(json.dumps(compact_omitted[k], ensure_ascii=True)),
                reverse=True,
            ):
                compact_omitted.pop(subkey, None)
                if len(_json_bytes(minimal)) <= max_bytes:
                    return minimal
        for subkey in sorted(
            (k for k in omissions if k not in {"omitted_count", "omitted_section_count"}),
            key=lambda k: len(json.dumps(omissions[k], ensure_ascii=True)),
            reverse=True,
        ):
            omissions.pop(subkey, None)
            if len(_json_bytes(minimal)) <= max_bytes:
                return minimal

    cc = minimal.get("context_consistency")
    if isinstance(cc, dict):
        for subkey in sorted(
            (k for k in cc if k not in _CORE_CONSISTENCY_KEYS),
            key=lambda k: len(json.dumps(cc[k], ensure_ascii=True)),
            reverse=True,
        ):
            cc.pop(subkey, None)
            if len(_json_bytes(minimal)) <= max_bytes:
                return minimal

    rollback = minimal.get("rollback")
    if isinstance(rollback, dict):
        for subkey in sorted(
            rollback,
            key=lambda k: len(json.dumps(rollback[k], ensure_ascii=True)),
            reverse=True,
        ):
            rollback.pop(subkey, None)
            if len(_json_bytes(minimal)) <= max_bytes:
                return minimal
    elif isinstance(rollback, str):
        _truncate_str_field(minimal, "rollback", max_bytes)
        if len(_json_bytes(minimal)) <= max_bytes:
            return minimal

    for key in (
        "incomplete_reason",
        "routing_reason",
        "routing_backend",
        "version",
        "schema_version",
    ):
        if len(_json_bytes(minimal)) <= max_bytes:
            return minimal
        _truncate_str_field(minimal, key, max_bytes)

    if isinstance(minimal.get("ask_user_before_editing"), dict):
        minimal["ask_user_before_editing"].pop("reason", None)
        if len(_json_bytes(minimal)) <= max_bytes:
            return minimal

    protected_last = {"compact_truncated", "ask_user_before_editing", "truncated", "omissions"}
    for key in sorted(
        (k for k in minimal if k not in protected_last),
        key=lambda k: len(json.dumps(minimal[k], ensure_ascii=True)),
        reverse=True,
    ):
        if len(_json_bytes(minimal)) <= max_bytes:
            return minimal
        minimal.pop(key, None)

    for key in sorted(
        (k for k in minimal if k != "compact_truncated"),
        key=lambda k: len(json.dumps(minimal[k], ensure_ascii=True)),
        reverse=True,
    ):
        if len(_json_bytes(minimal)) <= max_bytes:
            return minimal
        minimal.pop(key, None)
    return minimal


def compact_payload_for_cli(payload: dict[str, Any]) -> dict[str, Any]:
    """Return a deduplicated, <=4000-byte JSON-serializable copy of `payload`."""
    primary_keys, omit_keys = _primary_keys_for_payload(payload)
    protected = _ENVELOPE_KEYS | _SAFETY_AND_OMISSION_KEYS | primary_keys
    effective_omit = omit_keys - protected

    bounded: dict[str, Any] = {}
    omitted_diagnostics: list[tuple[str, int]] = []
    trimmed = False
    for key, value in payload.items():
        if key in effective_omit:
            trimmed = True
            count = len(value) if isinstance(value, list) else 1
            if count > 0:
                omitted_diagnostics.append((key, count))
            continue
        bounded[key] = copy.deepcopy(value)

    for key, count in omitted_diagnostics:
        _record_budget_omission(bounded, key, count, prefer_omissions=True)

    gaps = bounded.get("resolution_gaps")
    if isinstance(gaps, list) and len(gaps) > 10:
        bounded["resolution_gaps_truncated_count"] = len(gaps) - 10
        bounded["resolution_gaps"] = gaps[:10]
        trimmed = True

    if trimmed:
        bounded["compact_truncated"] = True

    if len(_json_bytes(bounded)) <= COMPACT_MAX_BYTES:
        return bounded

    # 1. Drop secondary keys before touching primary deliverables.
    for key in _SECONDARY_TRIM_ORDER:
        if key in bounded and key not in (_ENVELOPE_KEYS | _SAFETY_AND_OMISSION_KEYS):
            if key in primary_keys:
                continue
            _drop_key_with_omission(bounded, key)
            if len(_json_bytes(bounded)) <= COMPACT_MAX_BYTES:
                return bounded

    # 2. Drop any remaining non-protected keys largest-first.
    for key in sorted(
        (k for k in bounded if k not in protected),
        key=lambda k: len(json.dumps(bounded[k], ensure_ascii=True)),
        reverse=True,
    ):
        _drop_key_with_omission(bounded, key)
        if len(_json_bytes(bounded)) <= COMPACT_MAX_BYTES:
            return bounded

    # 3. Ensure non-rendered_context fields leave room for rendered_context when present.
    has_rendered_context = isinstance(bounded.get("rendered_context"), str) and bool(
        bounded.get("rendered_context")
    )
    reserved_render_bytes = 512 if has_rendered_context else 0

    def _over_budget() -> bool:
        if has_rendered_context:
            probe = dict(bounded)
            probe["rendered_context"] = "..."
            return len(_json_bytes(probe)) + reserved_render_bytes > COMPACT_MAX_BYTES
        return len(_json_bytes(bounded)) > COMPACT_MAX_BYTES

    # 4. Whole-entry omission on list-valued deliverables (never slice inside objects).
    # Keep at least 1 definition/file/test entry while trimming larger lists first.
    for key in _LIST_OMIT_ORDER:
        val = bounded.get(key)
        if isinstance(val, list) and val:
            min_keep = 1 if key in {"definitions", "files", "tests", "edit_order"} else 0
            removed = 0
            while len(val) > min_keep and _over_budget():
                val.pop()
                removed += 1
            if removed:
                _record_budget_omission(bounded, key, removed)
            if len(_json_bytes(bounded)) <= COMPACT_MAX_BYTES:
                return bounded

    # 5. Shrink rendered_context string if present, preserving structured fields.
    bounded["compact_truncated"] = True
    if has_rendered_context and _shrink_rendered_context(bounded, COMPACT_MAX_BYTES):
        return bounded

    # 6. Drop whole primary structured objects (excluding envelope/safety/omissions) if needed.
    # Shed `edit_order` last so blast-radius-plan preserves the ordered edit plan whenever possible.
    _drop_order = (
        *[k for k in _LIST_OMIT_ORDER if k != "edit_order"],
        "plan",
        "source_budget",
        "edit_order",
    )
    for key in _drop_order:
        if key in bounded and key not in (_ENVELOPE_KEYS | _SAFETY_AND_OMISSION_KEYS):
            _drop_key_with_omission(bounded, key)
            if len(_json_bytes(bounded)) <= COMPACT_MAX_BYTES:
                return bounded

    # 7. Extreme fallback: preserve mandatory envelope, safety, and bounded omission summary.
    minimal: dict[str, Any] = {
        k: bounded[k] for k in (_ENVELOPE_KEYS | _SAFETY_AND_OMISSION_KEYS) if k in bounded
    }
    minimal["compact_truncated"] = True
    if has_rendered_context:
        _update_context_consistency_after_render_trim(minimal)
    return _shrink_extreme_fallback(minimal, COMPACT_MAX_BYTES)


def dump_compact_json(payload: dict[str, Any]) -> str:
    """Compact `payload` and serialize as single-line UTF-8-bounded JSON."""
    return _json_bytes(compact_payload_for_cli(payload)).decode("utf-8")


def dump_render(payload: dict[str, Any], compact: bool = False) -> str:
    """Serialize `context-render` JSON, compacting to <=4000 bytes when `compact=True`."""
    return dump_context_render_json(payload, compact=compact)


def dump_cmd(payload: dict[str, Any], detail: str, result_key: str) -> str:
    """Serialize symbol or blast-radius-plan/render JSON honoring `--detail` / `--compact`."""
    return dump_symbol_or_command_json(payload, detail=detail, result_key=result_key)


def dump_context_render_json(payload: dict[str, Any], *, compact: bool = False) -> str:
    """Serialize `context-render` JSON, compacting to <=4000 bytes when `compact=True`."""
    if compact:
        return dump_compact_json(payload)
    if payload.get("render_profile") == "llm":
        return json.dumps(payload, separators=(",", ":"))
    return json.dumps(payload, indent=2)


def dump_symbol_or_command_json(payload: dict[str, Any], *, detail: str, result_key: str) -> str:
    """Serialize symbol or blast-radius-plan/render JSON honoring `--detail` / `--compact`."""
    if detail == "compact":
        if payload.get("routing_reason") in _BLAST_RADIUS_ROUTING_REASONS:
            return dump_compact_json(payload)
        from tensor_grep.cli.symbol_compact import trim_symbol_compact_payload

        payload = trim_symbol_compact_payload(payload, result_key=result_key)
    return _detail_tier.dump_detail_json(payload, detail)
