"""Parser-backed reference evidence helpers for diff-impact."""

from __future__ import annotations

import ast
import time
from pathlib import Path
from typing import Any

from tensor_grep.cli import repo_map
from tensor_grep.cli.repo_map_test_paths import _is_test_file


def _python_import_reference(
    repo_map_payload: dict[str, Any], symbol: str, deadline_monotonic: float | None
) -> tuple[bool, bool]:
    files, tests = repo_map._repo_map_file_and_test_universe(repo_map_payload)
    root = repo_map._repo_map_root_dir(repo_map_payload)
    max_bytes = repo_map._max_parse_bytes()
    for path in [*files, *tests]:
        if path.suffix.lower() != ".py":
            continue
        if deadline_monotonic is not None and time.monotonic() >= deadline_monotonic:
            return False, False
        if not repo_map._path_is_relative_to(path, root):
            return False, False
        try:
            with path.open("rb") as source_file:
                raw = source_file.read(max_bytes + 1)
            if len(raw) > max_bytes:
                return False, False
            tree = ast.parse(raw.decode("utf-8-sig", errors="strict"))
        except (OSError, UnicodeError, SyntaxError):
            return False, False
        if any(
            isinstance(node, ast.ImportFrom) and any(alias.name == symbol for alias in node.names)
            for node in ast.walk(tree)
        ):
            return True, True
    return True, False


def _test_reference_evidence(
    repo_map_payload: dict[str, Any],
    symbol: str,
    deadline_monotonic: float | None,
    max_repo_files: int | None,
    lookup_incomplete: list[str] | None = None,
) -> tuple[bool, bool]:
    """Return complete-reference-data and whether a test reference or Python import exists."""
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
    reference_groups = [
        value for key, value in payload.items() if key == "references" or key.endswith("_refs")
    ]
    if (
        truncated
        or not isinstance(payload.get("references"), list)
        or not isinstance(payload.get("string_refs"), list)
        or not all(isinstance(group, list) for group in reference_groups)
    ):
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
        for group in reference_groups
        for reference in group
    )
    if has_test_reference:
        return True, True
    imports_complete, has_import_reference = _python_import_reference(
        repo_map_payload, symbol, deadline_monotonic
    )
    if not imports_complete and lookup_incomplete is not None:
        lookup_incomplete.append("test_reference_lookup_incomplete")
    return imports_complete, has_import_reference
