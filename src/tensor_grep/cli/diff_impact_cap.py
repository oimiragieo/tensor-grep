"""Evidence requirements for the diff-impact uncalled-symbol risk cap."""

from __future__ import annotations

import time
from typing import Any

from tensor_grep.cli import repo_map


def risk_cap_evidence_status(changed_symbols: list[dict[str, Any]], lookup_complete: bool) -> str:
    if not changed_symbols:
        return "not_applicable"
    return "complete" if lookup_complete else "incomplete"


def apply_uncalled_symbol_cap(
    risk_tier: str,
    risk_reasons: list[str],
    *,
    symbol_evidence_complete: bool,
    all_changed_symbols_uncalled: bool,
    test_reference_data_complete: bool,
    partial: bool,
    not_analyzed_paths: list[dict[str, str]],
    repo_map_payload: dict[str, Any],
    deadline_monotonic: float | None,
) -> tuple[str, list[str]]:
    if (
        symbol_evidence_complete
        and all_changed_symbols_uncalled
        and test_reference_data_complete
        and not partial
        and not not_analyzed_paths
        and not any(
            entry.get("reason", "").startswith(("coverage_gap", "extraction_failed"))
            for entry in not_analyzed_paths
        )
        and not repo_map._scan_did_not_finish(repo_map_payload)
        and not (deadline_monotonic is not None and time.monotonic() >= deadline_monotonic)
        and risk_tier in {"high", "critical"}
    ):
        return "medium", sorted({*risk_reasons, "capped_uncalled_symbols"})
    return risk_tier, risk_reasons
