"""When is a Python-side case-insensitive match EXACT? Only on pure ASCII.

rg folds case with Unicode simple case folding. Python's ``str.lower()`` (StringZilla paths) and
``re.IGNORECASE`` (CPU Python loop) do not agree with it outside ASCII; measured against rg:

* ``lower()``: final sigma ``\\u03c2`` is not folded to ``\\u03c3`` (rg matches, ``lower()`` misses).
* ``re.IGNORECASE``: ``\\u0130`` / ``\\u0131`` match ``i`` in Python but not in rg.

An ASCII pattern is therefore necessary but NOT sufficient (the file can hold non-ASCII case
variants such as ``\\u017f`` for ``s`` or the Kelvin sign for ``k``), so the only provably exact
domain is: pattern ASCII AND file content ASCII. Outside it, the search is handed to rg, or
refused when rg is unavailable.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from tensor_grep.backends.base import BackendExecutionError

if TYPE_CHECKING:
    from tensor_grep.core.config import SearchConfig
    from tensor_grep.core.result import SearchResult

_CHUNK = 1 << 16


def file_is_ascii(path: str) -> bool:
    """Chunked scan; an unreadable file raises (fail closed) instead of reading as ASCII."""
    try:
        with open(path, "rb") as handle:
            while chunk := handle.read(_CHUNK):
                if not chunk.isascii():
                    return False
    except OSError as exc:
        raise BackendExecutionError(f"cannot read {path!r} to check its encoding: {exc}") from exc
    return True


def ascii_fold_exact(pattern: str, path: str) -> bool:
    """True when ``lower()``/``re.IGNORECASE`` provably equal rg's case folding for this search."""
    return pattern.isascii() and file_is_ascii(path)


def delegate_to_rg(path: str, pattern: str, config: SearchConfig | None) -> SearchResult:
    """Run the search on rg, the only engine whose Unicode case folding is authoritative."""
    from tensor_grep.backends.ripgrep_backend import RipgrepBackend

    rg = RipgrepBackend()
    if not rg.is_available():
        raise BackendExecutionError(
            "a case-insensitive search over non-ASCII text needs rg's Unicode case folding and "
            "the 'rg' backend is unavailable; refusing to return a count that may differ"
        )
    return rg.search(path, pattern, config)
