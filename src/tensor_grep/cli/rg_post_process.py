"""`-o` / `--replace` output, taken from rg itself.

Split out of `cli/main.py` under the file-size ratchet. rg decides what matches AND what the
replacement text is: `RipgrepBackend` forwards `-o`/`-r` and parses rg's own `--json` fields
(`submatches[].match`, `submatches[].replacement`, and a replaced line assembled from the line's
ORIGINAL bytes). This module only selects among those strings. It never compiles or evaluates
the user's pattern: re-deriving rg's captures/offsets in Python was wrong (capture alternatives),
unbounded (ReDoS) and lossy (offsets applied to re-decoded text).

A match line that carries no rg data (a non-rg engine, which the pipeline never routes `-o`/`-r`
to) is refused with `BackendExecutionError` instead of guessed.
"""

from __future__ import annotations

import base64
import binascii
from dataclasses import replace
from typing import TYPE_CHECKING

from tensor_grep.backends.base import BackendExecutionError

if TYPE_CHECKING:
    from tensor_grep.core.config import SearchConfig
    from tensor_grep.core.result import MatchLine


def _refuse(match: MatchLine, what: str) -> BackendExecutionError:
    return BackendExecutionError(
        f"cannot produce {what} for {match.file}:{match.line_number}: no rg-produced data "
        "(the line is not valid UTF-8, or the search did not run on rg); refusing to rebuild "
        "rg's output in Python"
    )


def _field_text(field: object) -> str | None:
    """An rg text-or-bytes field as `str`; None when absent or not valid UTF-8."""
    if not isinstance(field, dict):
        return None
    text = field.get("text")
    if isinstance(text, str):
        return text
    b64 = field.get("bytes")
    if isinstance(b64, str):
        try:
            return base64.b64decode(b64).decode("utf-8")
        except (binascii.Error, ValueError):
            return None
    return None


def replace_lines(matches: list[MatchLine], pattern: str, config: SearchConfig) -> list[MatchLine]:
    if config.replace_str is None:
        return matches
    out: list[MatchLine] = []
    for match in matches:
        if match.replaced_text is None:
            raise _refuse(match, "the --replace output")
        out.append(replace(match, text=match.replaced_text, submatches=None))
    return out


def only_matching_lines(
    matches: list[MatchLine], pattern: str, config: SearchConfig
) -> list[MatchLine]:
    out: list[MatchLine] = []
    for match in matches:
        if not match.submatches:
            raise _refuse(match, "the -o output")
        for sub in match.submatches:
            field = sub.get("replacement" if config.replace_str is not None else "match")
            token = _field_text(field)
            if token is None:
                raise _refuse(match, "the -o output")
            # rg -o prints nothing for an empty match, but does print an empty replacement
            if token or config.replace_str is not None:
                out.append(replace(match, text=token, submatches=None, replaced_text=None))
    return out
