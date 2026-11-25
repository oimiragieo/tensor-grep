"""Compact-tier payload trimming for symbol commands (defs, refs, callers)."""

from __future__ import annotations

import json
from typing import Any

_COMPACT_MISSING_MAX_BYTES = 1400
_CORE_PROVENANCE_KEYS: frozenset[str] = frozenset({
    "routing_backend",
    "routing_reason",
    "sidecar_used",
    "incomplete_reason",
    "result_incomplete",
    "not_found",
    "schema_version",
    "command",
    "resolution_gaps_count",
})
_PROVENANCE_KEYS: frozenset[str] = _CORE_PROVENANCE_KEYS | frozenset({
    "scan_remediation",
    "lsp_evidence_status",
    "graph_completeness",
    "suggestions",
})
_VERBOSE_PROSE_KEYS: tuple[str, ...] = (
    "caveat",
    "message",
    "not_lsp_proof_reason",
    "semantic_provider",
    "lsp_proof",
    "no_match",
)


def _json_byte_len(payload: dict[str, Any]) -> int:
    return len(json.dumps(payload, separators=(",", ":")).encode("utf-8"))


def _truncate_str(value: str, max_len: int) -> str:
    return value if len(value) <= max_len else value[: max(0, max_len - 3)] + "..."


def _enforce_missing_byte_ceiling(trimmed: dict[str, Any], *, result_key: str) -> None:
    trimmed["compact_truncated"] = True

    protected = _PROVENANCE_KEYS | {
        "version",
        "symbol",
        "path",
        "compact_truncated",
        result_key,
    }
    extra_keys = sorted(
        (k for k in trimmed if k not in protected),
        key=lambda k: len(json.dumps(trimmed[k], separators=(",", ":"))),
        reverse=True,
    )
    for key in extra_keys:
        if _json_byte_len(trimmed) <= _COMPACT_MISSING_MAX_BYTES:
            return
        trimmed.pop(key, None)

    suggestions = trimmed.get("suggestions")
    if isinstance(suggestions, list) and suggestions:
        suggestions = [_truncate_str(s, 64) if isinstance(s, str) else s for s in suggestions]
        while (
            suggestions
            and _json_byte_len({**trimmed, "suggestions": suggestions}) > _COMPACT_MISSING_MAX_BYTES
        ):
            suggestions.pop()
        trimmed["suggestions"] = suggestions
    if _json_byte_len(trimmed) <= _COMPACT_MISSING_MAX_BYTES:
        return

    remediation = trimmed.get("scan_remediation")
    if isinstance(remediation, str):
        for max_len in (120, 64, 32, 16):
            if _json_byte_len(trimmed) <= _COMPACT_MISSING_MAX_BYTES:
                return
            if len(remediation) > max_len:
                remediation = _truncate_str(remediation, max_len)
                trimmed["scan_remediation"] = remediation

    if _json_byte_len(trimmed) <= _COMPACT_MISSING_MAX_BYTES:
        return

    core_keys = _CORE_PROVENANCE_KEYS | {"compact_truncated", result_key}
    non_core_keys = sorted(
        (k for k in trimmed if k not in core_keys),
        key=lambda k: len(json.dumps(trimmed[k], separators=(",", ":"))),
        reverse=True,
    )
    for key in non_core_keys:
        if _json_byte_len(trimmed) <= _COMPACT_MISSING_MAX_BYTES:
            return
        trimmed.pop(key, None)

    if _json_byte_len(trimmed) > _COMPACT_MISSING_MAX_BYTES:
        minimal: dict[str, Any] = {
            key: trimmed[key] for key in _CORE_PROVENANCE_KEYS if key in trimmed
        }
        minimal["compact_truncated"] = True
        minimal[result_key] = []
        trimmed.clear()
        trimmed.update(minimal)


def trim_symbol_compact_payload(payload: dict[str, Any], *, result_key: str = "") -> dict[str, Any]:
    """Trim verbose provider_status and resolution_gaps under `--detail compact`.

    Preserves compact provenance, ``incomplete_reason``, ``result_incomplete``, and
    ``suggestions``, and bounds missing-symbol payloads below 1.5 KB.
    """
    trimmed = dict(payload)
    trimmed.pop("provider_status", None)
    if "resolution_gaps" in trimmed:
        gaps = trimmed.pop("resolution_gaps")
        if isinstance(gaps, list) and gaps:
            trimmed["resolution_gaps_count"] = len(gaps)

    if (
        result_key
        and not trimmed.get(result_key)
        and _json_byte_len(trimmed) > _COMPACT_MISSING_MAX_BYTES
    ):
        omissions = trimmed.get("omissions")
        if isinstance(omissions, dict) and omissions.get("omitted_section_count", 0) == 0:
            trimmed.pop("omissions", None)
        token_budget = trimmed.get("token_budget")
        if isinstance(token_budget, dict) and not token_budget.get("truncated"):
            trimmed.pop("token_budget", None)
        output_limit = trimmed.get("output_limit")
        if isinstance(output_limit, dict) and not output_limit.get("tests_truncated"):
            trimmed.pop("output_limit", None)
        scan_limit = trimmed.get("scan_limit")
        if isinstance(scan_limit, dict) and not scan_limit.get("possibly_truncated"):
            trimmed.pop("scan_limit", None)
        for key in _VERBOSE_PROSE_KEYS:
            if _json_byte_len(trimmed) <= _COMPACT_MISSING_MAX_BYTES:
                break
            trimmed.pop(key, None)
        if _json_byte_len(trimmed) > _COMPACT_MISSING_MAX_BYTES:
            _enforce_missing_byte_ceiling(trimmed, result_key=result_key)

    return trimmed
