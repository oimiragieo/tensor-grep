from __future__ import annotations

import codecs
from collections.abc import Iterator
from typing import Any

_LSP_STDERR_READ_CHUNK_BYTES = 4096
_LSP_STDERR_MAX_LINES = 50
_LSP_STDERR_MAX_TEXT_BYTES = 64 * 1024
_LSP_STDERR_MAX_LINE_BYTES = 8 * 1024
_LSP_STDERR_TRUNCATION_MARKER = "...[truncated]"


def _truncate_lsp_stderr_line(text: str) -> str:
    if len(text.encode("utf-8", errors="replace")) <= _LSP_STDERR_MAX_LINE_BYTES:
        return text
    marker = _LSP_STDERR_TRUNCATION_MARKER
    prefix_budget = _LSP_STDERR_MAX_LINE_BYTES - len(marker.encode("utf-8"))
    prefix: list[str] = []
    prefix_bytes = 0
    for char in text:
        char_bytes = len(char.encode("utf-8", errors="replace"))
        if prefix_bytes + char_bytes > prefix_budget:
            break
        prefix.append(char)
        prefix_bytes += char_bytes
    return "".join(prefix) + marker


def _iter_lsp_stderr_lines(stream: Any) -> Iterator[str]:
    """Drain stderr in fixed-size reads and yield only bounded decoded lines."""
    decoder = codecs.getincrementaldecoder("utf-8")(errors="replace")
    read_chunk = getattr(stream, "read1", None) or stream.read
    line_parts: list[str] = []
    line_bytes = 0
    line_truncated = False
    saw_bytes = False

    def consume(text: str) -> Iterator[str]:
        nonlocal line_parts, line_bytes, line_truncated
        while True:
            segment, separator, remainder = text.partition("\n")
            if segment and not line_truncated:
                segment_bytes = len(segment.encode("utf-8", errors="replace"))
                if line_bytes + segment_bytes > _LSP_STDERR_MAX_LINE_BYTES:
                    line = "".join(line_parts) + segment
                    line_parts = [_truncate_lsp_stderr_line(line)]
                    line_bytes = len(line_parts[0].encode("utf-8", errors="replace"))
                    line_truncated = True
                else:
                    line_parts.append(segment)
                    line_bytes += segment_bytes
            if not separator:
                return
            line = "".join(line_parts).rstrip("\r")
            if line:
                yield line
            line_parts = []
            line_bytes = 0
            line_truncated = False
            text = remainder

    while True:
        chunk = read_chunk(_LSP_STDERR_READ_CHUNK_BYTES)
        if not chunk:
            break
        if isinstance(chunk, bytes):
            saw_bytes = True
            text = decoder.decode(chunk)
        else:
            text = chunk
        yield from consume(text)
    if saw_bytes:
        yield from consume(decoder.decode(b"", final=True))
    line = "".join(line_parts).rstrip("\r")
    if line:
        yield line
