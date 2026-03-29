from __future__ import annotations

from ._compat import strip_ansi


def unstyle(text: str) -> str:
    """Derived from click.unstyle for patch benchmarking."""
    return strip_ansi(text)
