"""Up-front argument validation for the MCP search tools (bug-hunt Part K2, E-06/E-09/E-10).

Lives outside ``mcp_server.py`` because that file is under a file-size ratchet (it may only
shrink). Everything here is PURE: it returns a constant, non-leaking message (or a payload dict)
and never touches the wire itself -- ``mcp_server`` stamps the contract fields and returns it.
No import of ``mcp_server`` at module scope (circular); the only I/O is the optional rg probe in
``regex_is_invalid``.
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


def search_arg_error(
    pattern: str,
    context: int | None,
    max_count: int | None,
    type_filter: str | None,
) -> str | None:
    """Return a refusal message for an argument rg would reject (or misparse), else None."""
    if not pattern:
        return "either pattern or query is required."
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


# Python `re` messages for syntax errors that the Rust regex grammar (rg / tg native) ALSO rejects:
# an unclosed group, an unmatched ")", an unterminated class. Anything else Python rejects
# (`\p{Greek}`, possessive quantifiers, ...) can be valid Rust, so it is never pre-rejected.
_AGREED_RE_ERRORS = (
    "missing ), unterminated subpattern",
    "unbalanced parenthesis",
    "unterminated character set",
)


_REGEX_META = frozenset(chr(92) + "^$.|?*+()[]{}")


def _rg_rejects_regex(pattern: str) -> bool | None:
    """Ask rg itself (empty stdin, same default grammar). None = rg absent / cannot tell.

    Measured ~180 ms per probe on a loaded Windows box, so a pattern with no regex metacharacter
    (always valid) skips it.
    """
    import subprocess

    if _REGEX_META.isdisjoint(pattern):
        return False

    from tensor_grep.cli import runtime_paths

    binary = runtime_paths.resolve_ripgrep_binary()
    if binary is None:
        return None
    try:
        proc = subprocess.run(
            [str(binary), "--no-config", "-e", pattern],
            input=b"",
            capture_output=True,
            timeout=5,
            check=False,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    if proc.returncode in (0, 1):
        return False
    return b"regex parse error" in proc.stderr


def regex_is_invalid(pattern: str, *, fixed_strings: bool) -> bool:
    """True only when ``pattern`` is a syntax error for the engine that will run it.

    rg's own grammar decides when rg is present (never rejects an rg-valid pattern). Without rg
    only errors invalid in BOTH Python ``re`` and Rust regex are reported. A literal
    (``fixed_strings``) pattern is never a regex. Runs BEFORE the walk, so a pattern is refused
    even when the glob/type filter selects no file and no backend parser would ever see it.
    """
    if fixed_strings:
        return False
    verdict = _rg_rejects_regex(pattern)
    if verdict is not None:
        return verdict
    try:
        re.compile(pattern)
    except re.error as exc:
        return any(exc.msg.startswith(m) for m in _AGREED_RE_ERRORS)
    except (RecursionError, OverflowError):
        return False
    return False


def raise_if_regex_invalid(pattern: str, fixed_strings: bool) -> None:
    """Raise ``re.error`` when ``pattern`` is a syntax error and NO file reached a backend.

    Called only after a walk that scanned zero files: the backend parser (which normally reports
    a bad regex, mapped to ``invalid_input``) never ran, so without this the caller would get a
    complete empty success. Searches that touch files never pay the rg probe.
    """
    if regex_is_invalid(pattern, fixed_strings=fixed_strings):
        raise re.error(REGEX_INVALID_MESSAGE)
