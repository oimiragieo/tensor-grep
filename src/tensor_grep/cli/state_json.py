"""Strict byte-bounded JSON helpers for durable state files."""

from __future__ import annotations

import json
import os
import re
import sys
from pathlib import Path
from typing import Any

from tensor_grep.io.confined import read_confined

_DEFAULT_STATE_JSON_MAX_BYTES = 64 * 1024 * 1024
_STATE_JSON_LIMIT_ENV = "TG_STATE_JSON_MAX_BYTES"


class StateJsonError(ValueError):
    """A durable JSON state file is invalid or exceeds its configured byte budget."""


def _validate_limit(limit: int) -> None:
    if isinstance(limit, bool) or not isinstance(limit, int):
        raise StateJsonError("state JSON byte limit must be an integer")
    if not 1 <= limit <= sys.maxsize - 1:
        raise StateJsonError(f"state JSON byte limit must be between 1 and {sys.maxsize - 1}")


def configured_state_json_limit() -> int:
    """Return the configured state JSON byte limit, validating it before I/O."""
    raw_limit = os.environ.get(_STATE_JSON_LIMIT_ENV)
    if raw_limit is None:
        return _DEFAULT_STATE_JSON_MAX_BYTES

    if re.fullmatch(r"[ \t\n\r\f\v]*[+-]?[0-9](?:_?[0-9])*[ \t\n\r\f\v]*", raw_limit) is None:
        raise StateJsonError(f"{_STATE_JSON_LIMIT_ENV} must be a decimal integer")

    normalized = raw_limit.strip(" \t\n\r\f\v")
    negative = normalized.startswith("-")
    digits = normalized[1:] if normalized[:1] in {"+", "-"} else normalized
    significant = digits.replace("_", "").lstrip("0") or "0"
    maximum = str(sys.maxsize - 1)
    in_range = (
        not negative
        and significant != "0"
        and (
            len(significant) < len(maximum)
            or (len(significant) == len(maximum) and significant <= maximum)
        )
    )
    limit = int(significant, 10) if in_range else 0
    _validate_limit(limit)
    return limit


def read_state_json(path: Path, *, trusted_root: Path, limit: int, kind: str) -> Any:
    """Read confined JSON; preserve initial path absence before leaf observation as FileNotFoundError.

    Callers must supply an established trusted root and a fixed, nonsensitive
    diagnostic label for ``kind``.
    """
    _validate_limit(limit)
    try:
        data = read_confined(trusted_root, path, limit)
    except FileNotFoundError:
        raise
    except (OSError, ValueError) as exc:
        raise StateJsonError(f"could not read confined {kind} JSON state") from exc
    try:
        return json.loads(data.decode("utf-8"))
    except (ValueError, RecursionError) as exc:
        raise StateJsonError(f"invalid {kind} JSON state") from exc
