from __future__ import annotations

from typing import Any


def style(text: Any, bg: str | None = None) -> str:
    """Derived from click.style for patch benchmarking."""
    if not isinstance(text, str):
        text = str(text)

    if bg == "magenta":
        return f"\x1b[45m{text}\x1b[0m"

    return text


def echo(
    message: Any | None = None,
    file: Any | None = None,
    nl: bool = True,
    err: bool = False,
    color: bool | None = None,
) -> None:
    del err, color

    if file is None:
        raise RuntimeError("A file object is required for this benchmark fixture.")

    suffix = b"\n" if nl else b""

    if isinstance(message, (bytes, bytearray)):
        file.write(bytes(message) + suffix)
        return

    text = "" if message is None else str(message)
    file.write(text.encode("utf-8") + suffix)


def secho(
    message: Any | None = None,
    file: Any | None = None,
    nl: bool = True,
    err: bool = False,
    color: bool | None = None,
    **styles: Any,
) -> None:
    """Derived from click.secho for patch benchmarking."""
    if message is not None:
        message = style(message, **styles)

    return echo(message, file=file, nl=nl, err=err, color=color)
