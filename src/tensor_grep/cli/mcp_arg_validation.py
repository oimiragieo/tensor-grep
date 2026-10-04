"""Up-front argument validation for the MCP search tools (bug-hunt Part K2, E-06/E-09/E-10).

Lives outside ``mcp_server.py`` because that file is under a file-size ratchet (it may only
shrink). Everything here is PURE: it returns a constant, non-leaking message (or a payload dict)
and never touches the wire itself -- ``mcp_server`` stamps the contract fields and returns it.
No import of ``mcp_server`` (circular), no I/O.
"""

from __future__ import annotations

import json
import re
from typing import Any

from tensor_grep.cli.incompleteness import incomplete_class_fragment

# council wave-2b r21: rg permits Unicode letters/numbers in type names
# (ignore/src/types.rs TypesBuilder::add); reject only option-shaped or separator-bearing input.
# `[^\W_]` = any Unicode letter or digit.
RG_TYPE_NAME_RE = re.compile(r"^[^\W_][\w+.-]*$")


def tg_search_invalid_argument(
    context: int | None, max_count: int | None, type_filter: str | None
) -> str | None:
    """Return a refusal message for an argument rg would reject (or misparse), else None."""
    if context is not None and context < 0:
        return "context must be >= 0."
    if max_count is not None and max_count < 0:
        return "max_count must be >= 0."
    if type_filter and not RG_TYPE_NAME_RE.fullmatch(type_filter):
        return "type_filter must be a file type name such as 'py' or 'js'."
    return None


REGEX_INVALID_MESSAGE = "pattern is not a valid regular expression."
FILE_TYPE_UNKNOWN_MESSAGE = "type_filter is not a file type rg knows (see `rg --type-list`)."


def search_error_message(exc: BaseException) -> str | None:
    """Return a constant ``invalid_input`` message when a search failure is a caller-argument
    error (bad regex, unknown rg file type), else None.

    Reads the exception text here, OFF the except arm, so the SEC-007 narrow-handler ratchet
    still sees no exception formatting on a wire-facing arm and only a constant message ever
    reaches the caller. ``re.error`` is what the CPU/Python backends raise for a bad regex
    (council wave-2b r1); rg exits 2 with ``regex parse error`` / ``unrecognized file type``.
    """
    if isinstance(exc, re.error):
        return REGEX_INVALID_MESSAGE
    text = str(exc)
    if "regex parse error" in text:
        return REGEX_INVALID_MESSAGE
    if "unrecognized file type" in text:  # council wave-2b r3: e.g. type_filter="c++"
        return FILE_TYPE_UNKNOWN_MESSAGE
    return None


def search_invalid_input_payload(pattern: str, message: str, *, path: str) -> dict[str, Any]:
    """The structured ``invalid_input`` envelope for ``tg_search`` (nothing was searched)."""
    return {
        "pattern": pattern,
        "path": path,
        "total_matches": 0,
        "total_files": 0,
        "rendered_match_count": 0,
        "rendered_file_count": 0,
        "matches": [],
        "truncated": False,
        "result_incomplete": True,
        "incomplete_reason": message,
        **incomplete_class_fragment(None),
        "error": {"code": "invalid_input", "message": message},
    }


def search_invalid_input_response(
    pattern: str, message: str, *, path: str, structured_json: bool
) -> str:
    """Plain text, or the contract-stamped ``invalid_input`` envelope, for a refused search."""
    if not structured_json:
        return f"Search failed: {message}"
    from tensor_grep.cli import mcp_server  # lazy: mcp_server imports this module

    payload = search_invalid_input_payload(pattern, message, path=path)
    return mcp_server._inject_mcp_contract_fields(json.dumps(payload, indent=2))


def tg_find_refusal(query: object, limit: int) -> str | None:
    """Return a refusal message for an empty query or non-positive limit, else None."""
    if not isinstance(query, str) or not query.strip():
        return "query must not be empty."
    if limit < 1:
        return "limit must be >= 1."
    return None


def unsupported_ast_language_message(lang: str | None) -> str | None:
    """Return a refusal message when ``lang`` is non-empty and not a supported AST language."""
    if not lang or not lang.strip():
        return None
    from tensor_grep.backends.ast_backend import get_supported_languages, normalize_ast_language

    try:
        normalize_ast_language(lang)
    except ValueError:
        return (
            f"Unsupported AST language {lang.strip()[:64]!r}. "
            f"Supported languages: {', '.join(get_supported_languages())}."
        )
    return None
