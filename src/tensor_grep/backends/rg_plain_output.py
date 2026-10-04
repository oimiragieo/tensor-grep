"""Parse rg's own PLAIN-TEXT output into structured match records.

Structured (`--json` etc.) `-o` / `-r` requests are answered by rg's plain-text rendering, so every
line number, column, replacement coordinate and multi-line split comes from rg itself; Python does
no arithmetic on offsets and never re-derives rg's rendering.

Observed format (real rg, run with `-n --column --with-filename --null --no-heading`, checked in
tests against the installed rg):

    <path> NUL <line> ':' <col> ':' <text> TERM     a match line (`-o` token, `-r` line, ...)
    <path> NUL <line> ':' <text> TERM               an INVERTED (-v) line: rg prints NO column
    <path> NUL <line> '-' <text> TERM               a context line (-A/-B/-C): '-' and no column

The request passes `--no-context-separator`, so rg never prints the `--` group separator and the
parser has no separator case at all: a file literally named `--` is just a path (verified with rg
15.1: with the flag the stream is the same minus the `--` lines, in LF and NUL modes).

`TERM` is LF normally and NUL under `--null-data` (an LF in the text is then content). A multi-line
`-U -o` match is printed as one such line PER physical line, each with its own `line:col:` prefix;
an `-o -r` match is reported in the REPLACED line's coordinates; a match consisting only of the
record delimiter (`-o '\\n'`) prints nothing and exits 0. `--null` ends the path with NUL, so a
path containing ':' or digits is unambiguous. Text is returned byte-exact; text that is not valid
UTF-8 cannot be a faithful `str` and is refused (`BackendExecutionError`) rather than mangled.
"""

from __future__ import annotations

import re

from tensor_grep.backends.base import BackendExecutionError
from tensor_grep.core.result import MatchLine

# Strict: the FIRST numeric fields only; everything after them is the text, untouched.
_HEAD = re.compile(rb"(\d+)([:-])", re.DOTALL)
_COLUMN = re.compile(rb"(\d+):", re.DOTALL)


def _bad(what: str) -> BackendExecutionError:
    return BackendExecutionError(f"cannot parse rg's plain-text output: {what}")


def parse_rg_plain_output(
    stdout: bytes, *, null_data: bool = False, inverted: bool = False
) -> list[MatchLine]:
    """Turn rg's `-n --column --with-filename --null` plain output into `MatchLine`s."""
    term = b"\0" if null_data else b"\n"
    out: list[MatchLine] = []
    pos, size = 0, len(stdout)
    while pos < size:
        nul = stdout.find(b"\0", pos)
        if nul == -1:
            raise _bad("missing the NUL that ends a path")
        path = stdout[pos:nul].decode("utf-8", errors="replace")
        body_end = stdout.find(term, nul + 1)
        if body_end == -1:
            raise _bad("a record is not terminated")
        body = stdout[nul + 1 : body_end]
        head = _HEAD.match(body)
        if head is None:
            raise _bad("a record does not start with '<line>:' or '<line>-'")
        line_number, sep, rest = int(head.group(1)), head.group(2), body[head.end() :]
        kind = "context" if sep == b"-" else ("inverted" if inverted else "match")
        column = 1
        if kind == "match":
            col = _COLUMN.match(rest)
            if col is None:
                raise _bad("a match record has no '<column>:' field")
            column, rest = int(col.group(1)), rest[col.end() :]
        try:
            text = rest.decode("utf-8")
        except UnicodeDecodeError as exc:
            raise BackendExecutionError(
                f"rg's output for {path}:{line_number} is not valid UTF-8 and cannot be "
                "represented faithfully in structured output"
            ) from exc
        submatches = None
        if kind == "match":
            submatches = (
                {"match": {"text": text}, "start": column - 1, "end": column - 1 + len(rest)},
            )
        out.append(
            MatchLine(
                line_number=line_number,
                text=text,
                file=path,
                rg_kind=kind,
                submatches=submatches,
            )
        )
        pos = body_end + 1
    return out
