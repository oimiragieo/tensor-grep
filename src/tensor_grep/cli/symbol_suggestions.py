"""Bounded "Did you mean" suggestions for symbol commands whose target was not found.

Candidates come only from inventories already in hand (the scanned repo map, or the target
file's own AST symbols) -- never a second repository scan.
"""

from __future__ import annotations

import difflib
from collections.abc import Iterable
from pathlib import Path
from typing import Any

MAX_CANDIDATE_CHARS = 64
_CUTOFF = 0.6


def _names(items: Iterable[Any]) -> set[str]:
    out: set[str] = set()
    for item in items:
        if isinstance(item, dict) and item.get("name"):
            out.add(str(item["name"]))
        elif isinstance(item, str) and item:
            out.add(item)
    return out


def bounded_candidates(symbol: str, symbols: Iterable[Any], *, n: int = 20) -> list[str]:
    """Near-miss names for ``symbol``, pre-filtered so a payload never carries the whole inventory."""
    names = {c for c in _names(symbols) if len(c) <= MAX_CANDIDATE_CHARS and "\n" not in c}
    names.discard(symbol)
    return sorted(difflib.get_close_matches(symbol, list(names), n=n, cutoff=_CUTOFF))


def suggestions_for_payload(payload: dict[str, Any]) -> list[str]:
    """Up to 5 suggestions for a not-found symbol payload, best match first, ties alphabetical."""
    target = payload.get("symbol")
    if not isinstance(target, str) or not target:
        return []
    names = _names(payload.get("candidate_symbols", [])) | _names(payload.get("symbols", []))
    path = payload.get("path")
    if not names and path and Path(path).is_file():
        from tensor_grep.backends.base import BackendExecutionError
        from tensor_grep.cli.repo_map import _imports_and_symbols_for_path

        # Best-effort: suggestions must never change the not-found exit code, so a file we cannot
        # parse (unreadable, undecodable, fail-closed grammar) simply yields no suggestions.
        try:
            _, file_symbols = _imports_and_symbols_for_path(Path(path))
        except (OSError, ValueError, BackendExecutionError):
            file_symbols = []
        names = _names(file_symbols)
    valid = [c for c in names if c != target and len(c) <= MAX_CANDIDATE_CHARS and "\n" not in c]
    matches = difflib.get_close_matches(target, valid, n=5, cutoff=_CUTOFF)
    matcher = difflib.SequenceMatcher()
    matcher.set_seq2(target)

    def _key(match: str) -> tuple[float, str]:
        matcher.set_seq1(match)
        return (-round(matcher.ratio(), 4), match)

    return sorted(matches, key=_key)
