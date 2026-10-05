"""Single place that resolves case-sensitivity precedence for non-rg engines.

Mirrors how ``RipgrepBackend`` forwards the flags (``_pattern_semantics_flags``): ``-i`` then
``-s`` is emitted in that order and rg is last-flag-wins, so an explicit ``case_sensitive`` beats
``ignore_case``; and ``-S`` (smart case) is only emitted when neither explicit flag is set.

Smart case is only EXACT here for patterns whose uppercase characters are all literals: rg looks
at uppercase LITERALS only, not at escapes (``\\S``, ``\\D``, ``\\p{L}``), classes or inline
groups. For a regex that contains any of those the answer belongs to rg, so a non-rg path must
not guess: ``effective_ignore_case`` raises ``BackendExecutionError`` and the pipeline routes the
search to rg (or fails closed when rg is unavailable).
"""

from __future__ import annotations

import re

from tensor_grep.backends.base import BackendExecutionError
from tensor_grep.core.config import SearchConfig

# A backslash (escape / property name), a bracket (class) or "(?" (inline flags, named groups)
# can contribute uppercase letters that are NOT literals to rg's smart-case scan.
_SMART_AMBIGUOUS_MARKERS = ("\\", "[", "(?")


def smart_case_is_exact(config: SearchConfig | None, pattern: str) -> bool:
    """True when a Python-side smart-case decision for ``pattern`` provably equals rg's."""
    if config is not None and config.fixed_strings:
        return True
    return not any(marker in pattern for marker in _SMART_AMBIGUOUS_MARKERS)


def smart_case_needs_rg(config: SearchConfig | None) -> bool:
    """True when ``config`` asks for smart case on a regex whose case rg alone can decide."""
    if config is None or not config.smart_case:
        return False
    if config.ignore_case or config.case_sensitive:
        return False  # explicit flags win; smart case is not consulted
    patterns = [config.query_pattern or "", *(config.regexp or [])]
    return not all(smart_case_is_exact(config, p) for p in patterns)


def effective_ignore_case(
    config: SearchConfig | None, pattern: str, *, strict: bool = True
) -> bool:
    """Whether a case-INsensitive match is requested after rg's precedence rules.

    ``strict=False`` is for callers that only need *some* answer (e.g. a syntax-validation
    compile): an ambiguous smart-case regex then resolves to case-sensitive instead of raising.
    """
    if config is None:
        return False
    if config.case_sensitive:
        return False  # explicit -s beats both -i and -S
    if config.ignore_case:
        return True
    if not config.smart_case:
        return False
    if not smart_case_is_exact(config, pattern):
        if strict:
            raise BackendExecutionError(
                "smart case (-S) on a regex with escapes/classes/inline groups can only be "
                "decided by rg (it looks at uppercase literals only); refusing to guess "
                f"for pattern {pattern!r}"
            )
        return False
    # smart case: insensitive only when the pattern has no uppercase letter
    return not any(ch.isupper() for ch in pattern)


def case_regex_flags(config: SearchConfig | None, pattern: str, *, strict: bool = True) -> int:
    """``re`` flags for the Python engines: ``re.IGNORECASE`` iff ``effective_ignore_case``."""
    return re.IGNORECASE if effective_ignore_case(config, pattern, strict=strict) else 0
