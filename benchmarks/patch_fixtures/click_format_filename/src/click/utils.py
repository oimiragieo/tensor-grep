from __future__ import annotations

import os
import sys


def format_filename(
    filename: str | bytes | os.PathLike[str] | os.PathLike[bytes],
    shorten: bool = False,
) -> str:
    """Derived from click.format_filename for patch benchmarking."""
    if shorten:
        filename = os.fspath(filename)
    else:
        filename = os.fspath(filename)

    if isinstance(filename, bytes):
        filename = filename.decode(sys.getfilesystemencoding(), "replace")
    else:
        filename = filename.encode("utf-8", "surrogateescape").decode("utf-8", "replace")

    return filename
