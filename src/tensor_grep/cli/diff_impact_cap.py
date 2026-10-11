"""Evidence requirements for the diff-impact uncalled-symbol risk cap."""

from __future__ import annotations

import time
from collections.abc import Callable
from typing import Any

from tensor_grep.cli import repo_map


def risk_cap_evidence_status(changed_symbols: list[dict[str, Any]], lookup_complete: bool) -> str:
    if not changed_symbols:
        return "not_applicable"
    return "complete" if lookup_complete else "incomplete"


def collect_test_reference_evidence(
    changed_symbols: list[dict[str, Any]],
    repo_map_payload: dict[str, Any],
    reference_lookup: Callable[[dict[str, Any], str, float | None, int | None], tuple[bool, bool]],
    *,
    deadline_monotonic: float | None,
    max_repo_files: int | None,
    analysis_complete: bool,
    initial_complete: bool,
) -> tuple[bool, bool]:
    complete = initial_complete and analysis_complete and bool(changed_symbols)
    has_test_reference = False
    if not analysis_complete or not changed_symbols:
        return False, False
    evidence_by_name: dict[str, tuple[bool, bool]] = {}
    for symbol in changed_symbols:
        name = str(symbol.get("name", ""))
        if not name:
            complete = False
            continue
        if deadline_monotonic is not None and time.monotonic() >= deadline_monotonic:
            complete = False
            break
        if name not in evidence_by_name:
            evidence_by_name[name] = reference_lookup(
                repo_map_payload, name, deadline_monotonic, max_repo_files
            )
        refs_complete, referenced = evidence_by_name[name]
        if not refs_complete:
            complete = False
        if referenced:
            has_test_reference = True
    if deadline_monotonic is not None and time.monotonic() >= deadline_monotonic:
        complete = False
    return complete, has_test_reference


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
