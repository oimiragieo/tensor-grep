"""Structured `-o` / `-r` entries read from rg's `--json` DATA (the unforgeable route).

rg's `--json` output carries explicit message types and the line / submatch / replacement values
as JSON `text` or base64 `bytes` fields, so nothing here parses a text stream and no framing can be
forged by file content, a replacement, or a path separator. With `--replace`, every submatch has a
`replacement` field and `lines` keeps the ORIGINAL text (verified with rg 15.1):

    {"type":"match","data":{"lines":{"text":"foo\\n"},"line_number":1,
      "submatches":[{"match":{"text":"foo"},"replacement":{"text":"X"},"start":0,"end":3}]}}

This route serves requests the plain-text route cannot carry unambiguously: `-a/--text/--binary`
(NUL inside text), a replacement containing LF/NUL, and any plain-text framing anomaly. The record
rules (all derived from rg's own fields; offsets index the ORIGINAL line bytes):

  * `-o`           one entry per submatch: text = the `match` field.
  * `-o -r`        one entry per submatch: text = the `replacement` field; columns are reported in
                   the REPLACED line's coordinates (original start shifted by the byte-length delta
                   of the earlier replacements on the same line), as rg prints them.
  * `-r`           one entry per matching line: the line rebuilt from its original bytes with each
                   `replacement` substituted at its offsets; the column is the first match's.
  * context and `-v` lines are rg's own and pass through unchanged (strictly decoded).
  * a multi-line (`-U`) token stays ONE entry whose text contains the delimiter (faithful to the
    data; the plain-text route is the one that reproduces rg's per-physical-line printing).

Text that is not valid UTF-8 cannot be a faithful `str`: `BackendExecutionError` (structured exit 2).
"""

from __future__ import annotations

import base64
import binascii

from tensor_grep.backends.base import BackendExecutionError
from tensor_grep.core.config import SearchConfig
from tensor_grep.core.result import MatchLine


def field_bytes(field: object) -> bytes | None:
    """The raw bytes of an rg ``--json`` text-or-bytes field (None when absent/malformed)."""
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


def strip_record_terminator(raw: bytes, delim: bytes = b"\n") -> bytes:
    """Strip AT MOST ONE trailing ``delim`` (LF normally, NUL under ``--null-data``)."""
    return raw[: -len(delim)] if delim and raw.endswith(delim) else raw


def _strict(raw: bytes, path: str, line_number: int) -> str:
    try:
        return raw.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise BackendExecutionError(
            f"rg's output for {path}:{line_number} is not valid UTF-8 and cannot be represented "
            "faithfully in structured output"
        ) from exc


def render_json_record(
    data: dict[str, object], kind: str, config: SearchConfig, path: str, delim: bytes
) -> list[MatchLine]:
    """Entries for one rg `match` / `context` record under ``config`` (-o and/or -r)."""
    line_number = data.get("line_number")
    line_number = line_number if isinstance(line_number, int) else 0
    record = field_bytes(data.get("lines"))
    if record is None:
        raise BackendExecutionError(f"rg gave no line data for {path}:{line_number}")
    inverted = bool(config.invert_match and not config.no_invert_match)
    raw_subs = data.get("submatches")
    subs = [s for s in raw_subs if isinstance(s, dict)] if isinstance(raw_subs, list) else []
    if kind == "context" or inverted or not subs:
        text = _strict(strip_record_terminator(record, delim), path, line_number)
        label = "context" if kind == "context" else ("inverted" if inverted else "match")
        return [MatchLine(line_number=line_number, text=text, file=path, rg_kind=label)]

    replacing = config.replace_str is not None
    spans: list[tuple[int, int, bytes, bytes, dict[str, object]]] = []
    for sub in subs:
        start, end = sub.get("start"), sub.get("end")
        match_raw = field_bytes(sub.get("match"))
        repl_raw = field_bytes(sub.get("replacement")) if replacing else match_raw
        if not isinstance(start, int) or not isinstance(end, int) or match_raw is None:
            raise BackendExecutionError(
                f"rg gave incomplete submatch data for {path}:{line_number}"
            )
        if repl_raw is None:
            raise BackendExecutionError(f"rg gave no replacement for {path}:{line_number}")
        spans.append((start, end, match_raw, repl_raw, sub))

    def entry(text: str, row: int, col0: int, width: int, token: str) -> MatchLine:
        sub = {"match": {"text": token}, "start": col0, "end": col0 + width}
        return MatchLine(line_number=row, text=text, file=path, rg_kind="match", submatches=(sub,))

    if config.only_matching:
        out: list[MatchLine] = []
        shift, shift_line = 0, -1  # -o -r: byte delta of earlier replacements on the SAME line
        for start, end, match_raw, repl_raw, _sub in spans:
            token_raw = repl_raw if replacing else match_raw
            line_start = record.rfind(delim, 0, start) + 1
            if line_start != shift_line:
                shift, shift_line = 0, line_start
            col0 = start - line_start + shift
            if replacing:
                shift += len(repl_raw) - (end - start)
            token = _strict(token_raw, path, line_number)
            row = line_number + record.count(delim, 0, start)
            out.append(entry(token, row, col0, len(token_raw), token))
        return out

    # -r only: the replaced line, rebuilt from the original bytes
    pieces: list[bytes] = []
    cursor = 0
    for start, end, _match_raw, repl_raw, _sub in spans:
        pieces.extend((record[cursor:start], repl_raw))
        cursor = end
    pieces.append(record[cursor:])
    text = _strict(strip_record_terminator(b"".join(pieces), delim), path, line_number)
    first_start, _e, _m, first_repl, _s = spans[0]
    line_start = record.rfind(delim, 0, first_start) + 1
    token = _strict(first_repl, path, line_number)
    return [entry(text, line_number, first_start - line_start, len(first_repl), token)]
