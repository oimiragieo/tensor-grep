"""Output detail-tier option and JSON payload filtering for CLI agent/symbol commands."""

from __future__ import annotations

import json
from typing import Any

import typer

VALID_DETAIL_TIERS: tuple[str, ...] = ("compact", "standard", "full")

_COMPACT_OMITTED_KEYS: frozenset[str] = frozenset({
    "coverage",
    "coverage_summary",
    "provider_agreement",
    "provider_status",
    "symbol_cache",
    "daemon_response_cache",
    "serve_cache",
    "rendered_caller_tree",
    "rendered_context",
    "graph_trust_summary",
    "file_summaries",
    "imports",
    "symbols",
    "related_paths",
    "file_matches",
    "test_matches",
    "import_graph_consumers",
    "ranking_quality",
    "edit_plan_seed",
    "navigation_pack",
    "candidate_edit_targets",
    "plan",
    "raw_context_ref",
    "gpu_acceleration",
})
_COMPACT_MAX_BYTES = 4000
_COMPACT_TRIM_ORDER: tuple[str, ...] = (
    "snippets",
    "candidates",
    "tests",
    "resolution_gaps",
    "alternative_targets",
    "related_call_sites",
    "validation_commands",
    "suggested_validation_commands",
    "dependency_grounding",
    "route_rationale",
    "edit_order",
    "ambiguity",
    "scan_limit",
    "central_files",
    "symbol_map",
    "validation_plan",
)
_CAPSULE_PROTECTED_KEYS: frozenset[str] = frozenset({
    "context_consistency",
    "omissions",
    "rollback",
    "call_site_evidence",
    "primary_target",
    "compact_truncated",
})


def validate_detail_tier(detail: Any) -> str:
    """Validate `--detail` value and raise `typer.BadParameter` on unknown tiers."""
    if isinstance(detail, typer.models.OptionInfo):
        detail = detail.default
    if detail not in VALID_DETAIL_TIERS:
        allowed = ", ".join(VALID_DETAIL_TIERS)
        raise typer.BadParameter(f"Invalid --detail '{detail}'. Choose one of: {allowed}.")
    return str(detail)


def detail_option() -> Any:
    """Shared `--detail` Typer option with eager validation."""
    return typer.Option(
        "standard",
        "--detail",
        callback=validate_detail_tier,
        help="JSON output detail level: compact, standard, or full (JSON mode only).",
    )


def compact_option() -> Any:
    """Shared `--compact` Typer option for deduplicated compact JSON output."""
    return typer.Option(
        False,
        "--compact",
        help="Emit compact, deduplicated JSON output (JSON mode only).",
    )


def resolve_compact_detail(
    compact: bool, detail: str, *, json_output: bool, citations: bool = False
) -> str:
    """Resolve `--compact` / `--detail` precedence and reject invalid combinations."""
    if compact and not json_output:
        raise typer.BadParameter("--compact requires --json.")
    if compact and citations:
        raise typer.BadParameter("--compact cannot be combined with --citations.")
    if compact and detail not in {"standard", "compact"}:
        raise typer.BadParameter(f"--compact cannot be combined with --detail {detail}.")
    return "compact" if compact else detail


def compact_payload(payload: dict[str, Any]) -> dict[str, Any]:
    """Deduplicate overlapping target/plan blocks and omit verbose diagnostic keys."""
    filtered = {key: value for key, value in payload.items() if key not in _COMPACT_OMITTED_KEYS}
    gaps = filtered.get("resolution_gaps")
    if isinstance(gaps, list) and len(gaps) > 10:
        filtered["resolution_gaps_truncated_count"] = len(gaps) - 10
        filtered["resolution_gaps"] = gaps[:10]
    return filtered


def _truncate_value_for_budget(
    value: Any, max_str: int = 120, max_list: int = 3, max_dict: int = 6
) -> Any:
    if isinstance(value, str):
        return value if len(value) <= max_str else value[:max_str] + "..."
    if isinstance(value, list):
        return [
            _truncate_value_for_budget(v, max_str, max_list, max_dict) for v in value[:max_list]
        ]
    if isinstance(value, dict):
        items = list(value.items())[:max_dict]
        return {k: _truncate_value_for_budget(v, max_str, max_list, max_dict) for k, v in items}
    return value


def _bound_compact_json(filtered: dict[str, Any], *, ensure_ascii: bool) -> str:
    serialized = json.dumps(filtered, ensure_ascii=ensure_ascii, separators=(",", ":"))
    if len(serialized.encode("utf-8")) <= _COMPACT_MAX_BYTES:
        return serialized
    bounded = dict(filtered)
    for trim_key in _COMPACT_TRIM_ORDER:
        if trim_key in bounded:
            bounded.pop(trim_key, None)
            bounded["compact_truncated"] = True
            serialized = json.dumps(bounded, ensure_ascii=ensure_ascii, separators=(",", ":"))
            if len(serialized.encode("utf-8")) <= _COMPACT_MAX_BYTES:
                return serialized
    for key in sorted(bounded.keys(), key=lambda k: len(json.dumps(bounded[k])), reverse=True):
        if key not in _CAPSULE_PROTECTED_KEYS:
            bounded.pop(key, None)
            bounded["compact_truncated"] = True
            serialized = json.dumps(bounded, ensure_ascii=ensure_ascii, separators=(",", ":"))
            if len(serialized.encode("utf-8")) <= _COMPACT_MAX_BYTES:
                return serialized
    for max_str, max_list, max_dict in ((120, 3, 6), (60, 2, 4), (24, 1, 2), (8, 1, 1)):
        shrunk = {
            k: (
                _truncate_value_for_budget(v, max_str, max_list, max_dict)
                if k != "compact_truncated"
                else True
            )
            for k, v in bounded.items()
        }
        shrunk["compact_truncated"] = True
        serialized = json.dumps(shrunk, ensure_ascii=ensure_ascii, separators=(",", ":"))
        if len(serialized.encode("utf-8")) <= _COMPACT_MAX_BYTES:
            return serialized
    minimal: dict[str, Any] = {"compact_truncated": True}
    for k in (
        "primary_target",
        "context_consistency",
        "omissions",
        "rollback",
        "call_site_evidence",
    ):
        if k in bounded:
            minimal[k] = {"truncated": True}
    return json.dumps(minimal, ensure_ascii=ensure_ascii, separators=(",", ":"))


def apply_detail_tier(payload: dict[str, Any], detail: str) -> dict[str, Any]:
    """Filter verbose diagnostic/telemetry keys when `detail == 'compact'`."""
    validate_detail_tier(detail)
    if detail in ("standard", "full"):
        return payload
    return compact_payload(payload)


def dump_detail_json(
    payload: dict[str, Any],
    detail: str,
    *,
    ensure_ascii: bool = True,
) -> str:
    """Apply `detail` filtering and serialize `payload` to JSON."""
    filtered = apply_detail_tier(payload, detail)
    if detail == "compact":
        return _bound_compact_json(filtered, ensure_ascii=ensure_ascii)
    return json.dumps(filtered, ensure_ascii=ensure_ascii, indent=2)
