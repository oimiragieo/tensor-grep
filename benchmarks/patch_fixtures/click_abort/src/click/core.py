from __future__ import annotations

from .exceptions import Abort


class Context:
    """Derived from click.Context.abort for patch benchmarking."""

    def abort(self) -> None:
        raise Abort()
