"""Output bounds for MCP match rendering (``tg_search`` / ``tg_ast_search``).

``max_results`` bounds the ROW COUNT, not row size: one minified-JS line is a multi-megabyte
row. These helpers bound the width of each rendered match and the total rendered bytes. They
live outside ``mcp_server.py`` (size-ratcheted) and import nothing from it, so ``mcp_server``
can import them at module level without a cycle.
"""

from __future__ import annotations

import functools
import json
from typing import Any

_MCP_MATCH_TEXT_MAX_CHARS = 400
_MCP_MATCH_WINDOW_LEAD_CHARS = 100
_MCP_MATCHES_MAX_BYTES = 256 * 1024
_MCP_OUTPUT_TRUNCATED_NOTICE = (
    f"... output truncated at {_MCP_MATCHES_MAX_BYTES} bytes; "
    "narrow the search or lower max_results"
)


def _byte_to_char(raw: str, byte_offset: int) -> int:
    return len(raw.encode("utf-8")[:byte_offset].decode("utf-8", "ignore"))


def _anchor_char(raw: str, match: Any) -> int | None:
    """Char index (in ``raw``) to centre the window on, or None to use the stripped head.

    Preference, falling through until one is usable: (1) the first submatch whose span holds a
    non-whitespace character (ripgrep reports BYTE offsets into the raw line; a regex such as
    ` +|NEEDLE` can report a whitespace-only first submatch that would blank the window); (2) the
    first non-whitespace character of the line; (3) None -> the stripped head."""
    for sub in getattr(match, "submatches", None) or ():
        try:
            start = _byte_to_char(raw, int(sub.get("start", 0)))
            end = _byte_to_char(raw, int(sub.get("end", sub.get("start", 0))))
        except (TypeError, ValueError, AttributeError):
            continue
        if raw[start:end].strip():
            return start
    first_visible = len(raw) - len(raw.lstrip())
    return first_visible if first_visible < len(raw) else None


def _clip_match_text(match: Any) -> tuple[str, int]:
    """Return ``(text, removed_chars)`` for one match, bounded to the per-match cap.

    ``removed_chars == 0`` means the text is the full stripped line. Otherwise the text is a
    window cut from the RAW line (anchor coordinates are raw) and stripped afterwards; it never
    strips to empty while the line has visible content. A wider-than-cap line with NO visible
    content renders empty, honestly, and is flagged (``removed_chars`` = the line length)."""
    raw = str(match.text)
    stripped = raw.strip()
    if not stripped:
        return "", (len(raw) if len(raw) > _MCP_MATCH_TEXT_MAX_CHARS else 0)
    if len(stripped) <= _MCP_MATCH_TEXT_MAX_CHARS:
        return stripped, 0
    anchor = _anchor_char(raw, match)
    if anchor is None:
        window = stripped[:_MCP_MATCH_TEXT_MAX_CHARS]
    else:
        lo = max(0, anchor - _MCP_MATCH_WINDOW_LEAD_CHARS)
        window = (
            raw[lo : lo + _MCP_MATCH_TEXT_MAX_CHARS].strip() or stripped[:_MCP_MATCH_TEXT_MAX_CHARS]
        )
    return window, max(0, len(stripped) - len(window))


def _bounded_match_row(filepath: str, match: Any) -> dict[str, Any]:
    """One ``matches[]`` row. A line wider than the cap is windowed around the first submatch and
    flagged additively with ``text_truncated`` / ``text_chars`` so a caller can tell the text is
    a window."""
    text, removed = _clip_match_text(match)
    row: dict[str, Any] = {"file": filepath, "line_number": match.line_number, "text": text}
    if removed:
        row["text_truncated"] = True
        row["text_chars"] = len(str(match.text))
    return row


def _plain_match_text(match: Any) -> str:
    """Per-line bound for the plain-text branches: the same window as the JSON rows, plus an ASCII
    marker whenever text was removed (plain text has no per-row flag)."""
    text, removed = _clip_match_text(match)
    return f"{text} ...[truncated {removed} chars]" if removed else text


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


# --- FINAL response budget (the whole serialized envelope, not just matches[]) -----------------
_MCP_ECHO_MAX_CHARS = 1024
_MCP_RESPONSE_MAX_BYTES = _MCP_MATCHES_MAX_BYTES + 8192  # + envelope allowance (counts, notice)
_MCP_LINE_MAX_CHARS = 2048


def _json_size(doc: dict[str, Any]) -> int:
    return len(json.dumps(doc, indent=2).encode("utf-8"))


def _clip_strings(node: dict[str, Any], *, skip: frozenset[str] = frozenset()) -> bool:
    """Truncate every over-long string value (echoed ``pattern`` / ``path`` / ``query``, long
    error messages, ...) in ``node`` and its nested dicts, flagging each ``<key>_truncated``."""
    changed = False
    for key, value in list(node.items()):
        if key in skip:
            continue
        if isinstance(value, str) and len(value) > _MCP_ECHO_MAX_CHARS:
            node[key] = value[:_MCP_ECHO_MAX_CHARS]
            node[f"{key}_truncated"] = True
            changed = True
        elif isinstance(value, dict):
            changed = _clip_strings(value) or changed
    return changed


def _set_rows(doc: dict[str, Any], rows: list[Any], total_rows: int) -> None:
    """Install ``rows`` and recompute every counter that describes them from the retained rows and
    the ORIGINAL totals (``total_matches`` / ``total_files`` are never changed), so a trim here
    cannot leave a count from an earlier, larger render behind."""
    doc["matches"] = rows
    files = {row.get("file") for row in rows if isinstance(row, dict)}
    if isinstance(doc.get("rendered_match_count"), int):
        doc["rendered_match_count"] = len(rows)
    if isinstance(doc.get("rendered_file_count"), int):
        doc["rendered_file_count"] = len(files)
    if isinstance(doc.get("total_matches"), int) and isinstance(doc.get("omitted_matches"), int):
        doc["omitted_matches"] = max(0, doc["total_matches"] - len(rows))
    if isinstance(doc.get("total_files"), int) and isinstance(doc.get("omitted_files"), int):
        doc["omitted_files"] = max(0, doc["total_files"] - len(files))
    if len(rows) < total_rows:
        doc["truncated"] = True


def _bound_json_envelope(doc: dict[str, Any]) -> dict[str, Any] | None:
    """Bound ``doc`` in place when it exceeds the response budget; None when it already fits."""
    if _json_size(doc) <= _MCP_RESPONSE_MAX_BYTES:
        return None
    _clip_strings(doc, skip=frozenset({"matches"}))
    doc["output_truncated"] = True  # present while measuring: it is part of the final size
    rows = doc.get("matches")
    if _json_size(doc) > _MCP_RESPONSE_MAX_BYTES and isinstance(rows, list) and rows:
        all_rows = rows
        lo, hi = 0, len(all_rows)  # largest k whose FULL envelope fits (monotonic in k)
        while lo < hi:
            mid = (lo + hi + 1) // 2
            _set_rows(doc, all_rows[:mid], len(all_rows))
            if _json_size(doc) <= _MCP_RESPONSE_MAX_BYTES:
                lo = mid
            else:
                hi = mid - 1
        _set_rows(doc, all_rows[:lo], len(all_rows))
    return doc


def bound_response(text: str) -> str:
    """Final-budget guard for a tool's serialized response (JSON or plain text)."""
    if len(text) <= 65_000 or len(text.encode("utf-8")) <= _MCP_RESPONSE_MAX_BYTES:
        return text
    if text.lstrip().startswith("{"):
        try:
            doc = json.loads(text)
        except ValueError:
            doc = None
        if isinstance(doc, dict):
            bounded = _bound_json_envelope(doc)
            return text if bounded is None else json.dumps(bounded, indent=2)
    lines = [
        line if len(line) <= _MCP_LINE_MAX_CHARS else line[:_MCP_ECHO_MAX_CHARS] + "... [truncated]"
        for line in text.split("\n")
    ]
    return "\n".join(_cap_output_lines(lines))


def bounded_response(fn: Any) -> Any:
    """Decorator: apply :func:`bound_response` to a tool function's string result."""

    @functools.wraps(fn)
    def wrapper(*args: Any, **kwargs: Any) -> Any:
        out = fn(*args, **kwargs)
        return bound_response(out) if isinstance(out, str) else out

    return wrapper
