"""Single place that resolves case-sensitivity precedence for non-rg engines.

Mirrors how ``RipgrepBackend`` forwards the flags (``_pattern_semantics_flags``): ``-i`` then
``-s`` is emitted in that order and rg is last-flag-wins, so an explicit ``case_sensitive`` beats
``ignore_case``; and ``-S`` (smart case) is only emitted when neither explicit flag is set.
"""

from __future__ import annotations

from tensor_grep.core.config import SearchConfig


def effective_ignore_case(config: SearchConfig | None, pattern: str) -> bool:
    """Whether a case-INsensitive match is requested after rg's precedence rules."""
    if config is None:
        return False
    if config.case_sensitive:
        return False  # explicit -s beats both -i and -S
    if config.ignore_case:
        return True
    # smart case: insensitive only when the pattern has no uppercase letter
    return bool(config.smart_case and not any(ch.isupper() for ch in pattern))
