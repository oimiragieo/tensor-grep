"""`-o` / `--replace` output, taken from rg itself.

Split out of `cli/main.py` under the file-size ratchet. rg decides what matches AND what the
replacement text is: `RipgrepBackend` forwards `-o`/`-r` and parses rg's own `--json` fields
(`submatches[].match`, `submatches[].replacement`, a replaced line assembled from the line's
ORIGINAL bytes, and a strict decode of context/inverted lines). This module only selects among
those strings. It never compiles or evaluates the user's pattern: re-deriving rg's
captures/offsets in Python was wrong (capture alternatives), unbounded (ReDoS) and lossy
(offsets applied to re-decoded text).

Record kinds (`MatchLine.rg_kind`): `context` and `inverted` (-v) records are printed unchanged by
rg under -o/-r and pass through. An ordinary `match` record with no rg data, or any record whose
bytes cannot be represented faithfully as `str`, is refused with `BackendExecutionError` (the CLI
turns that into a structured exit 2) instead of being guessed.

Columns: each extracted token keeps ITS OWN rg submatch (original byte offset) so the formatter's
column is rg's. For `-o -r` rg reports columns in the REPLACED line's coordinates, i.e. the
original start shifted by the byte-length delta of the earlier replacements on the line.
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
        f"cannot produce {what} for {match.file}:{match.line_number}: no faithful rg-produced "
        "data (the line is not valid UTF-8, or the search did not run on rg); refusing to "
        "rebuild rg's output in Python"
    )


def _field_bytes(field: object) -> bytes | None:
    if not isinstance(field, dict):
        return None
    text = field.get("text")
    if isinstance(text, str):
        return text.encode("utf-8")
    b64 = field.get("bytes")
    if isinstance(b64, str):
        try:
            return base64.b64decode(b64)
        except (binascii.Error, ValueError):
            return None
    return None


def _field_text(field: object) -> str | None:
    """An rg text-or-bytes field as `str`; None when absent or not valid UTF-8."""
    raw = _field_bytes(field)
    if raw is None:
        return None
    try:
        return raw.decode("utf-8")
    except UnicodeDecodeError:
        return None


def _pass_through(match: MatchLine, what: str) -> MatchLine:
    if match.replaced_text is None:
        raise _refuse(match, what)
    return replace(match, text=match.replaced_text, submatches=None)


def replace_lines(matches: list[MatchLine], pattern: str, config: SearchConfig) -> list[MatchLine]:
    if config.replace_str is None:
        return matches
    out: list[MatchLine] = []
    for match in matches:
        if match.rg_kind is None or match.replaced_text is None:
            raise _refuse(match, "the --replace output")
        first = tuple(match.submatches[:1]) if match.submatches else None
        # rg prints ONE line per matching line; its column is the first match's (unshifted)
        out.append(replace(match, text=match.replaced_text, submatches=first))
    return out


def only_matching_lines(
    matches: list[MatchLine], pattern: str, config: SearchConfig
) -> list[MatchLine]:
    replacing = config.replace_str is not None
    # the CONFIGURED record delimiter: NUL under --null-data (an embedded LF is then content)
    delim = b"\0" if config.null_data else b"\n"
    out: list[MatchLine] = []
    for match in matches:
        if match.rg_kind in ("context", "inverted"):
            out.append(_pass_through(match, "the -o output"))
            continue
        record = match.rg_lines_raw
        if match.rg_kind != "match" or not match.submatches or record is None:
            raise _refuse(match, "the -o output")
        shift, shift_line = 0, -1  # -o -r: byte delta of earlier replacements on the SAME line
        for sub in match.submatches:
            start, end = sub.get("start"), sub.get("end")
            if not isinstance(start, int) or not isinstance(end, int):
                raise _refuse(match, "the -o output")
            field = sub.get("replacement" if replacing else "match")
            token = _field_text(field)
            raw = _field_bytes(field)
            if token is None or raw is None:
                raise _refuse(match, "the -o output")
            # offsets index the RECORD bytes (a -U record spans lines): locate the line/column
            line_start = record.rfind(delim, 0, start) + 1
            line_offset = record.count(delim, 0, start)
            if line_start != shift_line:
                shift, shift_line = 0, line_start
            col0 = start - line_start + shift
            if replacing:
                shift += len(raw) - (end - start)
            # an EMPTY match is still a match: rg prints an empty line (and exits 0)
            # a multi-line -o match is printed one numbered line per line; a replacement is not
            pieces = [token] if replacing else token.split(delim.decode())
            if len(pieces) > 1 and pieces[-1] == "":
                pieces.pop()  # a match ending with the delimiter has no phantom next line
            for index, piece in enumerate(pieces):
                begin = col0 if index == 0 else 0
                piece_sub = dict(sub)
                piece_sub["start"], piece_sub["end"] = begin, begin + len(piece.encode("utf-8"))
                out.append(
                    replace(
                        match,
                        text=piece,
                        line_number=match.line_number + line_offset + index,
                        submatches=(piece_sub,),
                        replaced_text=None,
                        rg_lines_raw=None,
                    )
                )
    return out


def post_process_matches(
    matches: list[MatchLine], pattern: str, config: SearchConfig, only_matching: bool
) -> list[MatchLine]:
    """Apply rg's `-o` / `-r` output to ``matches`` (a no-op when neither was requested)."""
    if only_matching:
        return only_matching_lines(matches, pattern, config)
    return replace_lines(matches, pattern, config)
