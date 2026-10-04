"""Parse rg's own PLAIN-TEXT output into structured match records.

Structured (`--json` etc.) `-o` / `-r` requests are answered by rg's plain-text rendering, so every
line number, column, replacement coordinate and multi-line split comes from rg itself; Python does
no arithmetic on offsets and never re-derives rg's rendering.

Observed format (real rg 15.1, run with the PINNED flags below):

    <path> NUL <line> ':' <col> ':' <text> TERM     a match line (`-o` token, `-r` line, ...)
    <path> NUL <line> ':' <text> TERM               an INVERTED (-v) line: rg prints NO column
    <path> NUL <line> '-' <text> TERM               a context line (-A/-B/-C): '-' and no column
    <path> ': binary file matches (found "\\0" byte around offset N)' LF    a binary-file notice

`TERM` is LF normally and NUL under `--null-data` (an LF in the text is then content). A multi-line
`-U -o` match is printed as one such line PER physical line, each with its own `line:col:` prefix;
an `-o -r` match is reported in the REPLACED line's coordinates; a match consisting only of the
record delimiter (`-o '\\n'`) prints nothing and exits 0. `--null` ends the path, so a path
containing ':' or digits is unambiguous.

LF mode, continuation: a record starts with `<path> NUL`. Text-mode content cannot contain NUL
(rg treats a NUL byte as binary: a file that has one prints only the binary notice above, never
its lines; `-a/--text/--binary` would admit NUL into text and are refused), so an LF-mode line WITHOUT
a NUL continues the previous record's text -- e.g. `-r 'X\\nY'` prints `<path> NUL 1:1:X` then a bare
`Y` -- and is appended with the LF between them. Under `--null-data` records end in NUL and may
contain LF freely, so nothing extra is needed.

OUTPUT-FORMAT FLAGS (checked against `rg --help`; rg is last-wins, so these are appended LAST):

  pinned to a fixed value (the user's value is overridden, it has no meaning for structured
  entries): `--field-match-separator :`, `--field-context-separator -`, `--no-stats` (tg's own
  `--stats` is computed by tg from the parsed result; rg's summary would land on stdout and be
  parsed as a record), `--no-heading`, `--color never` (so `--colors`/`--pretty` colour is moot),
  `--no-byte-offset`, `--null`, `--no-context-separator` (the `--` group separator, which also
  makes a file literally named `--` unambiguous), `-n`, `--column`, `--with-filename` (overrides
  `-N`/`-I`), `--no-trim`, `--max-columns 0` + `--no-max-columns-preview` (no truncation),
  `--hyperlink-format none`.
  left alone: `--path-separator` (the parser only needs the NUL after the path), `--sort*`,
  `--threads`, `--debug`/`--trace` (stderr), `--line-buffered`/`--block-buffered`, `--mmap`.
  REFUSED with a structured `invalid_input` (`refuse_unrepresentable_flags`), because they change
  the TEXT itself or admit NUL into it and cannot be honoured faithfully: `--trim`, `-M/--max-columns`,
  `--max-columns-preview`, `--vimgrep`, `--passthru`, `-b/--byte-offset`, `-a/--text`, `--binary`.
  mode flags that cannot coexist with a rendered stream (`-c/--count*`, `-l`, `--files-without-match`,
  `--files`) never reach this route: `RipgrepBackend.search` handles them before it, as rg would.

Text is returned byte-exact; text that is not valid UTF-8 cannot be a faithful `str` and is refused
(`BackendExecutionError`) rather than mangled.
"""

from __future__ import annotations

import re

from tensor_grep.backends.base import BackendExecutionError, InvalidInputError
from tensor_grep.core.config import SearchConfig
from tensor_grep.core.result import MatchLine

PINNED_FORMAT_FLAGS: tuple[str, ...] = (
    "-n",
    "--column",
    "--with-filename",
    "--null",
    "--no-heading",
    "--no-context-separator",
    "--no-byte-offset",
    "--no-trim",
    "--max-columns",
    "0",
    "--no-max-columns-preview",
    "--field-match-separator",
    ":",
    "--field-context-separator",
    "-",
    "--no-stats",
    "--hyperlink-format",
    "none",
    "--color",
    "never",
)

# Strict: the FIRST numeric fields only; everything after them is the text, untouched.
_HEAD = re.compile(rb"(\d+)([:-])", re.DOTALL)
_COLUMN = re.compile(rb"(\d+):", re.DOTALL)
_BINARY_NOTICE = re.compile(rb'(.*): (binary file matches \(found "\\0" byte around offset \d+\))')


def refuse_unrepresentable_flags(config: SearchConfig) -> None:
    """Raise `InvalidInputError` for flags the internal record stream cannot honour (see above)."""
    refused = [
        spelling
        for present, spelling in (
            (config.trim, "--trim"),
            (config.max_columns is not None, "-M/--max-columns"),
            (config.max_columns_preview, "--max-columns-preview"),
            (config.vimgrep, "--vimgrep"),
            (config.passthru, "--passthru"),
            (config.byte_offset, "-b/--byte-offset"),
            (config.text, "-a/--text"),
            (config.binary, "--binary"),
        )
        if present
    ]
    if refused:
        raise InvalidInputError(
            f"{', '.join(refused)} cannot be combined with structured -o/-r output: it changes "
            "the text of the lines (or admits NUL into it), which the structured record stream "
            "cannot represent faithfully; drop the flag, or drop --json/-o/-r for plain rg output"
        )


def _bad(what: str) -> BackendExecutionError:
    return BackendExecutionError(f"cannot parse rg's plain-text output: {what}")


def _split_records(stdout: bytes, null_data: bool) -> list[tuple[bytes, bytes, bool]]:
    """(path, body, is_binary_notice) per record; LF continuation lines are folded in."""
    records: list[tuple[bytes, bytes, bool]] = []
    if null_data:  # <path> NUL <body> NUL ; the body may contain LF freely
        pos = 0
        while pos < len(stdout):
            nul = stdout.find(b"\0", pos)
            if nul == -1:
                raise _bad("missing the NUL that ends a path")
            end = stdout.find(b"\0", nul + 1)
            if end == -1:
                raise _bad("a record is not terminated")
            records.append((stdout[pos:nul], stdout[nul + 1 : end], False))
            pos = end + 1
        return records
    lines = stdout.split(b"\n")
    if lines and lines[-1] == b"":
        lines.pop()
    for line in lines:
        if b"\0" in line:
            path, _, body = line.partition(b"\0")
            records.append((path, body, False))
            continue
        notice = _BINARY_NOTICE.fullmatch(line)
        if notice is not None:
            records.append((notice.group(1), notice.group(2), True))
            continue
        if not records or records[-1][2]:
            raise _bad("a line has neither a NUL-terminated path nor a previous record to continue")
        path, body, _ = records[-1]
        records[-1] = (path, body + b"\n" + line, False)  # NUL-free line: continuation
    return records


def parse_rg_plain_output(
    stdout: bytes, *, null_data: bool = False, inverted: bool = False
) -> list[MatchLine]:
    """Turn rg's pinned plain output into `MatchLine`s."""
    out: list[MatchLine] = []
    for path_bytes, body, is_notice in _split_records(stdout, null_data):
        path = path_bytes.decode("utf-8", errors="replace")
        if is_notice:
            out.append(
                MatchLine(
                    line_number=1,
                    text=body.decode("ascii"),
                    file=path,
                    rg_kind="match",
                    meta_variables={"binary_notice": True},
                )
            )
            continue
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
    return out
