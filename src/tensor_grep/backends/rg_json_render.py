"""rg-compatible `-o` / `-r` printer driven by rg's `--json` DATA (the only route).

rg's `--json` output has explicit message types and carries paths, line blocks, submatch offsets
and (with `--replace`) a per-submatch `replacement` as JSON `text` or base64 `bytes` fields, so
nothing is parsed out of a text stream and no file name, replacement or separator can forge
framing. `lines` is the sunk BLOCK (several lines under `-U`), `line_number` is its first line,
and `start`/`end` are offsets into that block (verified with rg 15.1):

    {"type":"match","data":{"lines":{"text":"foo\\n"},"line_number":1,
      "submatches":[{"match":{"text":"foo"},"replacement":{"text":"X"},"start":0,"end":3}]}}

This module re-implements how rg's PLAIN-TEXT printer lays those facts out, rule by rule. The
rules were derived from real rg output and are PROVEN (not assumed) by the seeded differential
fuzz in tests/unit/test_cli_rg_post_process.py (2400 cases), which compares this printer's
`(kind, line, column, text)` entries with rg's own plain `-o`/`-r` output on benign file names.

  * Replaced stream: with `-r`, the block is rebuilt with every submatch replaced; each match's
    column is its offset in that REPLACED buffer + 1 (a replacement that removes or inserts bytes,
    newlines included, shifts every later column). Without `-r` the buffer is the block. The
    column is block-relative, not line-relative, and the same for every piece of one match.
  * Line numbers: the counter starts at the block's first line; before each match it advances by
    the terminators in the UNMATCHED text since the previous match; after a match printed in
    "lines mode" it advances by the terminators in the text that was printed.
  * "Lines mode" (rg's multi-line printer) holds when the block spans several lines, or a match
    contains a terminator, or - for a single-line block under `-U` whose two layouts would differ
    (an empty or LF-bearing printed text, or `-r` with `--crlf`) - when rg's searcher used its
    multi-line strategy for this pattern. rg does not report that strategy, so it is OBSERVED: a
    haystack of two adjacent copies of the block yields ONE merged record under the multi-line
    strategy and two otherwise (`probe`, one extra rg run, memoised per request).
  * Verbatim (not lines mode): `-o` prints each match as ONE entry (at most one trailing
    terminator dropped), even when empty (`-o '^'` prints an empty line); a replacement
    containing LF stays inside that entry.
  * Lines mode: the printed text (the replacement, if any) is split on the terminator; each
    NON-EMPTY piece is an entry with an incrementing line number and the match's column (so
    `-o '$'`, `-o '\\n'` and `-o -r '' 'a\\nb'` print nothing and still exit 0). `-r` alone keeps
    every line of the replaced block (blank ones included) and prints nothing only when the
    whole replaced buffer is empty.
  * `-r` alone (verbatim): one entry per block, at most one trailing terminator dropped, at the
    first match's column.
  * `--crlf`: rg ends each printed line with CRLF, so an entry gets an extra `\\r` unless its text
    already ended with the terminator (or with `\\r`).
  * Context and `-v` lines are rg's own and pass through as printed. Under `-v` the "context"
    records are the lines that really match: they are rendered like matches (`N-C-text`, with the
    replacement applied, one per submatch under `-o`), labelled context, never in lines mode.
  * The terminator is LF, or NUL under `--null-data`.

NOT reproducible from the JSON data (reported, never guessed; the fuzz never combines them):
`--crlf` together with `--null-data` - rg's plain printer then works on per-record blocks (line and
column relative to the record) while its JSON merges several records into one block.

Text that is not valid UTF-8 cannot be a faithful `str`: `BackendExecutionError` (structured exit 2).
"""

from __future__ import annotations

import base64
import binascii
from collections.abc import Callable

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


def effective_crlf(config: SearchConfig) -> bool:
    """The `--crlf` rg ACTUALLY receives: `_pattern_semantics_flags` emits `--no-crlf` after
    `--crlf`, and rg is last-wins, so the negation wins whenever both are set."""
    return bool(config.crlf and not config.no_crlf)


def effective_multiline(config: SearchConfig) -> bool:
    """The `-U` rg ACTUALLY receives (`--no-multiline` is emitted after `--multiline`)."""
    return bool(config.multiline and not config.no_multiline)


def render_json_record(
    data: dict[str, object],
    kind: str,
    config: SearchConfig,
    path: str,
    delim: bytes,
    probe: Callable[[bytes], bool | None] | None = None,
) -> list[MatchLine]:
    """Entries for one rg `match` / `context` record under ``config`` (-o and/or -r)."""
    line_number = data.get("line_number")
    line_number = line_number if isinstance(line_number, int) else 0
    block = field_bytes(data.get("lines"))
    if block is None:
        raise BackendExecutionError(f"rg gave no line data for {path}:{line_number}")
    inverted = bool(config.invert_match and not config.no_invert_match)
    raw_subs = data.get("submatches")
    subs = [s for s in raw_subs if isinstance(s, dict)] if isinstance(raw_subs, list) else []
    if not subs:  # context lines / -v lines (and a match rg reports without offsets): as printed
        tail = b"\r" if effective_crlf(config) and not block.endswith(delim) else b""
        text = _strict(strip_record_terminator(block, delim) + tail, path, line_number)
        label = "context" if kind == "context" else ("inverted" if inverted else "match")
        return [MatchLine(line_number=line_number, text=text, file=path, rg_kind=label)]
    # Under -v the "context" records are the lines that really match: rg prints them like matches
    # (`N-C-text`, replacement applied, one per submatch with -o), with the context separator.
    label = "context" if kind == "context" else "match"

    replacing = config.replace_str is not None
    spans: list[tuple[int, int, bytes, bytes]] = []
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
        spans.append((start, end, match_raw, repl_raw))

    # the replaced buffer and each match's offset in it (identity when not replacing)
    pieces: list[bytes] = []
    new_starts: list[int] = []
    cursor = emitted = 0
    for start, end, _match_raw, repl_raw in spans:
        gap = block[cursor:start]
        pieces.append(gap)
        emitted += len(gap)
        new_starts.append(emitted)
        pieces.append(repl_raw)
        emitted += len(repl_raw)
        cursor = end
    pieces.append(block[cursor:])
    replaced = b"".join(pieces)

    # "lines mode": the printer splits what it prints on the terminator. It is keyed on the BLOCK
    # being multi-line or on a match containing a terminator (the -U searcher strategy).
    lines_mode = kind != "context" and (
        delim in strip_record_terminator(block, delim)
        or any(delim in match_raw for _s, _e, match_raw, _r in spans)
    )
    if not lines_mode and kind != "context" and effective_multiline(config) and probe is not None:
        # A single-line block with no terminator in any match is ambiguous under -U: the printer is
        # in lines mode iff the searcher used its multi-line strategy (the regex can match a
        # terminator), which rg does not report. It only matters when the two modes would print
        # differently (an empty match, or a replacement containing the terminator).
        printed_items = [
            repl_raw if replacing else match_raw for _s, _e, match_raw, repl_raw in spans
        ]
        differs = (
            any(not item or delim in item for item in printed_items)  # empty / multi-line text
            or (
                effective_crlf(config) and not config.only_matching
            )  # -r alone: CRLF tail per piece
        )
        if differs and probe(block):
            lines_mode = True

    def entry(
        text_raw: bytes, row: int, col0: int, *, ended: bool = False, split: bool = False
    ) -> MatchLine:
        # under --crlf rg ends every printed line with CRLF -- unless the text already ended with
        # the terminator it would have added
        tail = (
            b"\r"
            if effective_crlf(config) and not ended and not (split and text_raw.endswith(b"\r"))
            else b""
        )
        token = _strict(text_raw + tail, path, row)
        sub = {"match": {"text": token}, "start": col0, "end": col0 + len(text_raw)}
        return MatchLine(line_number=row, text=token, file=path, rg_kind=label, submatches=(sub,))

    if not config.only_matching:  # -r alone: the replaced block, at the first match's column
        ended = replaced.endswith(delim)
        printed_block = strip_record_terminator(replaced, delim)
        if not lines_mode:
            return [entry(printed_block, line_number, new_starts[0], ended=ended)]
        if not replaced:  # an entirely empty replaced buffer prints nothing in lines mode
            return []
        pieces_out = printed_block.split(delim)
        return [
            entry(
                piece,
                line_number + offset,
                new_starts[0],
                split=True,
            )
            for offset, piece in enumerate(pieces_out)
        ]

    out: list[MatchLine] = []
    counter = line_number
    previous_end = 0
    for (start, end, match_raw, repl_raw), col0 in zip(spans, new_starts, strict=True):
        counter += block.count(delim, previous_end, start)  # terminators in the unmatched gap
        previous_end = end
        printed = repl_raw if replacing else match_raw
        if lines_mode:  # one entry per non-empty piece
            pieces_out = printed.split(delim)
            for offset, piece in enumerate(pieces_out):
                if piece:  # each piece is a line the -o printer terminates itself
                    out.append(entry(piece, counter + offset, col0, split=True))
            counter += printed.count(delim)
        else:  # verbatim: one entry, at most one trailing terminator dropped
            out.append(
                entry(
                    strip_record_terminator(printed, delim),
                    counter,
                    col0,
                    ended=printed.endswith(delim),
                )
            )
    return out
