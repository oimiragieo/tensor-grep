from __future__ import annotations

from typing import Any


def style(
    text: Any,
    fg: str | None = None,
    reset: bool = True,
) -> str:
    """Derived from click.style for patch benchmarking."""
    bits: list[str] = []

    if fg == "red":
        bits.append("\x1b[31m")

    bits.append(text)

    if reset:
        bits.append("\x1b[0m")

    return "".join(bits)
