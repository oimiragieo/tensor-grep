"""Diff blast radius and review risk gating (P1 diff-impact).

Computes transitive blast radius for symbols modified in a git diff (working tree,
staged changes, or arbitrary ref/commit). Identifies downstream callers, affected
test files, calculates risk tiers, and supports CI gate failure thresholds.
"""

from __future__ import annotations

import json
import re
import time
from pathlib import Path
from typing import Any

from tensor_grep.cli import lang_registry, repo_map
from tensor_grep.cli.repo_map import (
    _deadline_monotonic_from_seconds,
    _scan_did_not_finish,
    build_repo_map,
    build_symbol_blast_radius_from_map,
)
from tensor_grep.cli.subprocess_policy import (
    configured_git_timeout_seconds,
    deadline_capped_timeout_seconds,
    run_subprocess,
)

_DIFF_HUNK_RE = re.compile(
    r"^@@ -(?P<old_start>\d+)(?:,(?P<old_count>\d+))? \+(?P<new_start>\d+)(?:,(?P<new_count>\d+))? @@"
)
_DIFF_GIT_FILE_RE = re.compile(r"^diff --git a/(?P<old_path>.+) b/(?P<new_path>.+)$")
_DIFF_PLUS_FILE_RE = re.compile(r"^\+\+\+ b/(?P<new_path>.+)$")


def parse_git_diff_hunks(diff_text: str) -> dict[Path, list[tuple[int, int]]]:
    """Parse git diff hunk headers `@@ -l,s +start,count @@` into mapped 1-indexed line ranges per file.

    Returns a dict mapping relative file Path to a list of (start_line, end_line) inclusive tuples.
    Deleted files (/dev/null) are omitted.
    """
    result: dict[Path, list[tuple[int, int]]] = {}
    current_file: Path | None = None
    is_deleted = False

    for line in diff_text.splitlines():
        if line.startswith("diff --git "):
            match = _DIFF_GIT_FILE_RE.match(line)
            if match:
                current_file = Path(match.group("new_path"))
                is_deleted = False
            else:
                current_file = None
                is_deleted = False
            continue

        if line.startswith("+++ "):
            if line.startswith("+++ /dev/null"):
                is_deleted = True
                current_file = None
            else:
                match = _DIFF_PLUS_FILE_RE.match(line)
                if match:
                    current_file = Path(match.group("new_path"))
                    is_deleted = False
            continue

        if is_deleted or current_file is None:
            continue

        if line.startswith("@@ "):
            match = _DIFF_HUNK_RE.match(line)
            if match:
                start = int(match.group("new_start"))
                count_str = match.group("new_count")
                count = int(count_str) if count_str is not None else 1
                if count == 0:
                    # Pure deletion at line `start`, changed point is line start
                    line_start = max(1, start)
                    line_end = line_start
                else:
                    line_start = start
                    line_end = start + count - 1

                ranges = result.setdefault(current_file, [])
                ranges.append((line_start, line_end))

    # Normalize / merge adjacent or overlapping ranges per file
    for file_path, ranges in list(result.items()):
        if not ranges:
            continue
        ranges.sort(key=lambda r: (r[0], r[1]))
        merged: list[tuple[int, int]] = [ranges[0]]
        for r_start, r_end in ranges[1:]:
            last_start, last_end = merged[-1]
            if r_start <= last_end + 1:
                merged[-1] = (last_start, max(last_end, r_end))
            else:
                merged.append((r_start, r_end))
        result[file_path] = merged

    return result


def extract_diff_hunks_from_git(
    ref: str | None = None,
    staged: bool = False,
    root: Path = Path("."),
    deadline_monotonic: float | None = None,
) -> dict[Path, list[tuple[int, int]]]:
    """Call git diff via run_subprocess with deadline capping and parse hunk ranges.

    Returns dict mapping file Path to 1-indexed (start_line, end_line) ranges.
    """
    cmd = ["git", "diff", "-U0"]
    if staged:
        cmd.append("--cached")
    if ref:
        cmd.append(ref)

    base_timeout = configured_git_timeout_seconds()
    timeout = deadline_capped_timeout_seconds(base_timeout, deadline_monotonic=deadline_monotonic)
    if timeout is None:
        # Deadline already expired
        return {}

    try:
        proc = run_subprocess(
            cmd,
            cwd=str(root),
            stdout=-1,
            stderr=-1,
            text=True,
            timeout_seconds=timeout,
        )
    except (OSError, ValueError, TimeoutError):
        return {}

    if proc.returncode != 0:
        return {}

    return parse_git_diff_hunks(proc.stdout or "")


def map_changed_lines_to_symbols(
    changed_files_with_lines: dict[Path, list[tuple[int, int]]],
    root: Path,
) -> list[dict[str, Any]]:
    """Use LANGUAGE_REGISTRY (or _imports_and_symbols_for_path fallback) to extract symbols for each file,

    checking which symbols span the modified lines.
    """
    changed_symbols: list[dict[str, Any]] = []

    for rel_path, line_ranges in changed_files_with_lines.items():
        full_path = root / rel_path
        if not full_path.is_file():
            continue

        spec = lang_registry.spec_for_path(full_path)
        symbols: list[dict[str, Any]] = []
        if spec is not None and spec.extract_imports_and_symbols is not None:
            try:
                _, symbols = spec.extract_imports_and_symbols(full_path)
            except (OSError, ValueError, SyntaxError, UnicodeDecodeError):
                symbols = []
        else:
            try:
                _, symbols = repo_map._imports_and_symbols_for_path(full_path)
            except (OSError, ValueError, SyntaxError, UnicodeDecodeError):
                symbols = []

        for sym in symbols:
            s_start = int(sym.get("start_line", sym.get("line", 1)))
            s_end = int(sym.get("end_line", s_start))

            # Check overlap between [s_start, s_end] and any [r_start, r_end]
            overlaps = any(
                max(s_start, r_start) <= min(s_end, r_end) for r_start, r_end in line_ranges
            )
            if overlaps:
                sym_copy = dict(sym)
                sym_copy["file"] = str(rel_path).replace("\\", "/")
                changed_symbols.append(sym_copy)

    # Sort deterministically
    changed_symbols.sort(
        key=lambda item: (item.get("file", ""), item.get("line", 0), item.get("name", ""))
    )
    return changed_symbols


def _is_test_path(path_str: str) -> bool:
    """Return True if path matches test conventions (tests/** or *_test.* or test_*.*)."""
    p = Path(path_str)
    parts = p.parts
    if any(part in ("tests", "test", "__tests__") for part in parts):
        return True
    name = p.name.lower()
    return name.startswith("test_") or name.endswith((
        "_test.py",
        "_test.go",
        "_test.rs",
        "_test.js",
        "_test.ts",
        ".test.js",
        ".test.ts",
        ".spec.js",
        ".spec.ts",
    ))


def _calculate_risk_tier(
    blast_radius_score: float, affected_files_count: int, callers_count: int
) -> str:
    """Calculate risk tier based on blast radius score, affected file count, and callers count.

    Tiers:
    - critical: score >= 0.7 or affected_files >= 25 or callers >= 50
    - high: score >= 0.4 or affected_files >= 10 or callers >= 20
    - medium: score >= 0.15 or affected_files >= 3 or callers >= 5
    - low: otherwise
    """
    if blast_radius_score >= 0.7 or affected_files_count >= 25 or callers_count >= 50:
        return "critical"
    if blast_radius_score >= 0.4 or affected_files_count >= 10 or callers_count >= 20:
        return "high"
    if blast_radius_score >= 0.15 or affected_files_count >= 3 or callers_count >= 5:
        return "medium"
    return "low"


def build_diff_blast_radius(
    ref: str | None = None,
    staged: bool = False,
    root: Path = Path("."),
    max_depth: int = 3,
    deadline_seconds: float | None = None,
    diff_text: str | None = None,
    max_repo_files: int | None = None,
) -> dict[str, Any]:
    """Compute transitive blast radius and review risk for git diff changes.

    Collects changed symbols, runs blast radius for each symbol from repo_map,
    unions callers and downstream dependents, identifies affected test files,
    and returns Section 0 completeness payload.
    """
    deadline_monotonic = _deadline_monotonic_from_seconds(deadline_seconds)
    root = root.resolve()

    downgrade_reasons: list[str] = []
    partial = False

    if diff_text is not None:
        changed_files_with_lines = parse_git_diff_hunks(diff_text)
    else:
        changed_files_with_lines = extract_diff_hunks_from_git(
            ref=ref,
            staged=staged,
            root=root,
            deadline_monotonic=deadline_monotonic,
        )

    changed_files = sorted([str(p).replace("\\", "/") for p in changed_files_with_lines.keys()])
    changed_symbols = map_changed_lines_to_symbols(changed_files_with_lines, root)

    # If no files or symbols changed
    if not changed_files:
        return {
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
            "partial": False,
            "downgrade_reasons": [],
            "symbol_count": 0,
            "caller_count": 0,
            "file_count": 0,
            "test_count": 0,
        }

    # Build repo map
    repo_m = build_repo_map(
        root,
        max_repo_files=max_repo_files,
        deadline_monotonic=deadline_monotonic,
    )

    if _scan_did_not_finish(repo_m):
        partial = True
        downgrade_reasons.append("repo_map_scan_incomplete")

    union_callers: list[dict[str, Any]] = []
    seen_callers: set[tuple[str, str, int]] = set()

    union_affected_files: set[str] = set(changed_files)
    union_tests: set[str] = set()
    per_symbol_scores: list[float] = []

    for sym in changed_symbols:
        symbol_name = str(sym.get("name", ""))
        if not symbol_name:
            continue

        # Check deadline before building symbol blast radius
        if deadline_monotonic is not None and time.monotonic() >= deadline_monotonic:
            partial = True
            if "deadline_exceeded" not in downgrade_reasons:
                downgrade_reasons.append("deadline_exceeded")
            break

        sym_radius = build_symbol_blast_radius_from_map(
            repo_m,
            symbol_name,
            max_depth=max_depth,
            deadline_monotonic=deadline_monotonic,
        )

        if sym_radius.get("partial"):
            partial = True
            if "symbol_blast_radius_partial" not in downgrade_reasons:
                downgrade_reasons.append("symbol_blast_radius_partial")

        # Collect callers
        for c in sym_radius.get("callers", []):
            c_file = str(c.get("file", "")).replace("\\", "/")
            c_sym = str(c.get("caller", c.get("name", "")))
            c_line = int(c.get("line", 0))
            key = (c_file, c_sym, c_line)
            if key not in seen_callers:
                seen_callers.add(key)
                caller_dict = dict(c)
                caller_dict["file"] = c_file
                union_callers.append(caller_dict)

        # Collect affected files
        for f in sym_radius.get("affected_files", []):
            union_affected_files.add(str(f).replace("\\", "/"))

        # Collect tests
        for t in sym_radius.get("tests", []):
            union_tests.add(str(t).replace("\\", "/"))

        score = float(sym_radius.get("blast_radius_score", 0.0))
        per_symbol_scores.append(score)

    # Also detect any tests in union_affected_files
    for f in union_affected_files:
        if _is_test_path(f):
            union_tests.add(f)

    # Sort results
    sorted_affected_files = sorted(union_affected_files)
    sorted_tests = sorted(union_tests)
    union_callers.sort(
        key=lambda c: (str(c.get("file", "")), int(c.get("line", 0)), str(c.get("caller", "")))
    )

    # Compute overall blast radius score
    if per_symbol_scores:
        overall_score = round(max(per_symbol_scores), 3)
    else:
        # If files changed without recognized symbols (e.g. config or docs), estimate from files
        overall_score = round(min(1.0, len(changed_files) * 0.05), 3)

    risk_tier = _calculate_risk_tier(overall_score, len(sorted_affected_files), len(union_callers))

    return {
        "root": str(root).replace("\\", "/"),
        "ref": ref,
        "staged": staged,
        "changed_files": changed_files,
        "changed_symbols": changed_symbols,
        "callers": union_callers,
        "affected_files": sorted_affected_files,
        "affected_tests": sorted_tests,
        "blast_radius_score": overall_score,
        "risk_tier": risk_tier,
        "partial": partial,
        "downgrade_reasons": downgrade_reasons,
        "symbol_count": len(changed_symbols),
        "caller_count": len(union_callers),
        "file_count": len(sorted_affected_files),
        "test_count": len(sorted_tests),
    }


def diff_impact_command(
    *,
    ref: str | None = None,
    staged: bool = False,
    deadline: float | None = None,
    json_output: bool = False,
    fail_threshold: float | None = None,
    fail_on_risk: str | None = None,
) -> None:
    """CLI implementation for diff-impact command."""
    import typer

    payload = build_diff_blast_radius(
        ref=ref,
        staged=staged,
        deadline_seconds=deadline,
    )

    if json_output:
        typer.echo(json.dumps(payload, indent=2))
    else:
        from tensor_grep.cli import main as cli_main

        cli_main._emit_scan_incompleteness_banner(payload)
        typer.echo(
            f"Diff impact: changed_files={payload['file_count']} changed_symbols={payload['symbol_count']} "
            f"callers={payload['caller_count']} affected_files={len(payload['affected_files'])} "
            f"affected_tests={payload['test_count']} score={payload['blast_radius_score']} risk={payload['risk_tier']}"
        )

    breached = False
    if (
        fail_threshold is not None
        and float(payload.get("blast_radius_score", 0.0)) > fail_threshold
    ):
        breached = True
    if fail_on_risk is not None:
        risk_rank = {"low": 1, "medium": 2, "high": 3, "critical": 4}
        current_rank = risk_rank.get(str(payload.get("risk_tier", "low")).lower(), 1)
        target_rank = risk_rank.get(fail_on_risk.lower(), 1)
        if current_rank >= target_rank:
            breached = True

    if payload.get("partial") or repo_map._scan_did_not_finish(payload) or breached:
        raise typer.Exit(2)

    if not payload.get("changed_files"):
        raise typer.Exit(1)

    raise typer.Exit(0)


__all__ = [
    "build_diff_blast_radius",
    "diff_impact_command",
    "extract_diff_hunks_from_git",
    "map_changed_lines_to_symbols",
    "parse_git_diff_hunks",
]
