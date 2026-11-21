"""Order-preserving Typer options for ripgrep's follow policy."""

from __future__ import annotations

from typing import Any

import typer


def follow_option() -> Any:
    return typer.Option(
        None,
        "-L",
        "--follow/--no-follow",
        help="Follow symbolic links; --no-follow is useful for config overrides.",
    )


def resolve_flags(follow: bool | None) -> tuple[bool, bool]:
    return follow is True, follow is False
