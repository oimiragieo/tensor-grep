"""Parser-backed reference evidence helpers for diff-impact."""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

from tensor_grep.cli import repo_map


def _test_reference_evidence(
    repo_map_payload: dict[str, Any],
    symbol: str,
    deadline_monotonic: float | None,
    is_test_path: Callable[[str], bool],
) -> tuple[bool, bool]:
    """Return complete-reference-data and whether any test file references the symbol."""
    try:
        payload = repo_map.build_symbol_refs_from_map(
            repo_map_payload,
            symbol,
            deadline_monotonic=deadline_monotonic,
        )
    except Exception:
        return False, False

    incomplete = (
        payload.get("partial")
        or payload.get("result_incomplete")
        or bool(payload.get("deadline_limit"))
        or bool(payload.get("scan_limit", {}).get("possibly_truncated"))
        or bool(payload.get("resolution_gaps"))
    )
    references = payload.get("references")
    if incomplete or payload.get("no_match") or not isinstance(references, list):
        return False, False
    has_test_reference = any(
        isinstance(reference, dict)
        and is_test_path(str(reference.get("file", "")).replace("\\", "/"))
        for reference in references
    )
    return True, has_test_reference
