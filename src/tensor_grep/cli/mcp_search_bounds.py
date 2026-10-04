"""Output bounds for MCP match rendering (``tg_search`` / ``tg_ast_search``).

``max_results`` bounds the ROW COUNT, not row size: one minified-JS line is a multi-megabyte
row. These helpers bound the width of each rendered match and the total rendered bytes. They
live outside ``mcp_server.py`` (size-ratcheted) and import nothing from it, so ``mcp_server``
can import them at module level without a cycle.
"""

from __future__ import annotations

import json
from typing import Any

_MCP_MATCH_TEXT_MAX_CHARS = 400
_MCP_MATCH_WINDOW_LEAD_CHARS = 100
_MCP_MATCHES_MAX_BYTES = 256 * 1024
_MCP_OUTPUT_TRUNCATED_NOTICE = (
    f"... output truncated at {_MCP_MATCHES_MAX_BYTES} bytes; "
    "narrow the search or lower max_results"
)


def _bounded_match_row(filepath: str, match: Any) -> dict[str, Any]:
    """One ``matches[]`` row. A line wider than the cap is windowed around the first submatch
    (ripgrep reports BYTE offsets, converted to a char index) and flagged additively with
    ``text_truncated`` / ``text_chars`` so a caller can tell the text is a window."""
    raw = match.text
    stripped = raw.strip()
    row: dict[str, Any] = {"file": filepath, "line_number": match.line_number, "text": stripped}
    if len(stripped) <= _MCP_MATCH_TEXT_MAX_CHARS:
        return row
    start_char = 0
    subs = getattr(match, "submatches", None)
    if subs:
        try:
            byte_start = int(subs[0].get("start", 0))
            start_char = len(raw.encode("utf-8")[:byte_start].decode("utf-8", "ignore"))
        except (TypeError, ValueError, AttributeError, IndexError, KeyError):
            start_char = 0
    lo = max(0, start_char - _MCP_MATCH_WINDOW_LEAD_CHARS)
    row["text"] = raw[lo : lo + _MCP_MATCH_TEXT_MAX_CHARS].strip()
    row["text_truncated"] = True
    row["text_chars"] = len(raw)
    return row


def _plain_match_text(match: Any) -> str:
    """Per-line bound for the plain-text branches."""
    return str(match.text).strip()[:_MCP_MATCH_TEXT_MAX_CHARS]


def _rendered_row_bytes(row: dict[str, Any]) -> int:
    """UTF-8 bytes this row occupies in the tool's FINAL output (measured as rendered, not
    compact). The tools serialize with ``json.dumps(payload, indent=2)`` and the rows sit at
    nesting depth 2 (payload -> "matches" -> row), so every line of the row's own indent=2
    rendering gains 4 leading spaces, plus a ",\\n" separator."""
    rendered = json.dumps(row, indent=2)
    return len(rendered.encode("utf-8")) + 4 * (rendered.count("\n") + 1) + 2


def _cap_match_rows(rows: list[dict[str, Any]]) -> tuple[list[dict[str, Any]], bool]:
    kept: list[dict[str, Any]] = []
    used = 0
    for row in rows:
        used += _rendered_row_bytes(row)
        if used > _MCP_MATCHES_MAX_BYTES:
            return kept, True
        kept.append(row)
    return kept, False


def _cap_rows(
    rows: list[dict[str, Any]], all_results: Any
) -> tuple[list[dict[str, Any]], tuple[int, int, int] | None]:
    """Cap ``rows`` by rendered bytes. Returns ``(rows, None)`` when under the cap, else
    ``(kept_rows, (omitted_matches, omitted_files, rendered_file_count))`` so the caller can
    keep its omission counters consistent with what is actually rendered."""
    kept, capped = _cap_match_rows(rows)
    if not capped:
        return rows, None
    rendered_files = len({row["file"] for row in kept})
    return kept, (
        max(0, all_results.total_matches - len(kept)),
        max(0, all_results.total_files - rendered_files),
        rendered_files,
    )


def _cap_output_lines(lines: list[str]) -> list[str]:
    """Cumulative byte budget for the plain-text branches (UTF-8 bytes + 1 per newline); the
    first line that would cross it is dropped together with everything after, and an ASCII
    notice is appended."""
    used = 0
    for index, line in enumerate(lines):
        used += len(line.encode("utf-8")) + 1
        if used > _MCP_MATCHES_MAX_BYTES:
            return [*lines[:index], _MCP_OUTPUT_TRUNCATED_NOTICE]
    return lines
