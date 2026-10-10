"""Parser-backed reference evidence helpers for diff-impact."""

from __future__ import annotations

import time
from pathlib import Path
from typing import Any

from tensor_grep.cli import repo_map
from tensor_grep.cli.repo_map_test_paths import _is_test_file


def _test_reference_evidence(
    repo_map_payload: dict[str, Any],
    symbol: str,
    deadline_monotonic: float | None,
    max_repo_files: int | None,
    lookup_incomplete: list[str] | None = None,
) -> tuple[bool, bool]:
    """Return complete-reference-data and whether any test file references the symbol."""
    if deadline_monotonic is not None and time.monotonic() >= deadline_monotonic:
        if lookup_incomplete is not None:
            lookup_incomplete.append("deadline_exceeded")
        return False, False
    if max_repo_files is not None:
        files, tests = repo_map._repo_map_file_and_test_universe(repo_map_payload)
        if deadline_monotonic is not None and time.monotonic() >= deadline_monotonic:
            if lookup_incomplete is not None:
                lookup_incomplete.append("deadline_exceeded")
            return False, False
        if len(files) + len(tests) > max(1, int(max_repo_files)):
            if lookup_incomplete is not None:
                lookup_incomplete.append("test_reference_lookup_incomplete")
            return False, False
    try:
        payload = repo_map.build_symbol_refs_from_map(
            repo_map_payload,
            symbol,
            deadline_monotonic=deadline_monotonic,
        )
    except Exception:
        if lookup_incomplete is not None:
            lookup_incomplete.append("test_reference_lookup_incomplete")
        return False, False

    if deadline_monotonic is not None and time.monotonic() >= deadline_monotonic:
        if lookup_incomplete is not None:
            lookup_incomplete.append("deadline_exceeded")
        return False, False

    truncated = (
        payload.get("partial")
        or payload.get("result_incomplete")
        or bool(payload.get("deadline_limit"))
        or bool(payload.get("scan_limit", {}).get("possibly_truncated"))
        or bool(payload.get("caller_scan_limit", {}).get("possibly_truncated"))
    )
    references = payload.get("references")
    if truncated or not isinstance(references, list):
        if lookup_incomplete is not None:
            lookup_incomplete.append(
                "deadline_exceeded"
                if payload.get("deadline_limit")
                else "test_reference_lookup_incomplete"
            )
        return False, False
    if payload.get("no_match"):
        return False, False
    if payload.get("resolution_gaps"):
        return False, False
    has_test_reference = any(
        isinstance(reference, dict) and _is_test_file(Path(str(reference.get("file", ""))))
        for reference in references
    )
    return True, has_test_reference
