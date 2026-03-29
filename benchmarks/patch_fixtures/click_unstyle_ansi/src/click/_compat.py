from __future__ import annotations

import re

_ansi_re = re.compile(r"\x1b\[[0-9;]*m")


def strip_ansi(value: str) -> str:
    return _ansi_re.sub("", value)
