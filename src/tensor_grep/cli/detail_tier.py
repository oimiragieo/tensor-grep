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


def apply_detail_tier(payload: dict[str, Any], detail: str) -> dict[str, Any]:
    """Filter verbose diagnostic/telemetry keys when `detail == 'compact'`."""
    validate_detail_tier(detail)
    if detail in ("standard", "full"):
        return payload
    return {key: value for key, value in payload.items() if key not in _COMPACT_OMITTED_KEYS}


def dump_detail_json(
    payload: dict[str, Any],
    detail: str,
    *,
    ensure_ascii: bool = True,
) -> str:
    """Apply `detail` filtering and serialize `payload` to JSON."""
    filtered = apply_detail_tier(payload, detail)
    if detail == "compact":
        return json.dumps(filtered, ensure_ascii=ensure_ascii, separators=(",", ":"))
    return json.dumps(filtered, ensure_ascii=ensure_ascii, indent=2)
