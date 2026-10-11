"""Empty diff-impact response payload."""

from __future__ import annotations

from pathlib import Path
from typing import Any


def _empty_payload(
    root: Path,
    ref: str | None,
    staged: bool,
    *,
    partial: bool,
    reason: str | None = None,
    error: str | None = None,
) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "root": str(root).replace("\\", "/"),
        "ref": ref,
        "staged": staged,
        "changed_files": [],
        "changed_symbols": [],
        "callers": [],
        "affected_files": [],
        "affected_tests": [],
        "blast_radius_score": 0.0,
        "risk_tier": "low",
        "risk_reasons": [],
        "risk_cap_evidence": "not_applicable",
        "partial": partial,
        "downgrade_reasons": [reason] if partial and reason else [],
        "symbol_count": 0,
        "caller_count": 0,
        "file_count": 0,
        "test_count": 0,
        "deleted_files": [],
        "not_analyzed_paths": [],
        "unparsed_changed_files": [],
        "binary_files": [],
        "mode_changed_files": [],
        "submodule_changed_files": [],
        "result_incomplete": partial,
        "incomplete_reason": reason if partial else None,
    }
    if partial:
        payload["error"] = error
    return payload
