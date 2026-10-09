"""Up-front argument validation for the MCP search tools (bug-hunt Part K2, E-06/E-09/E-10).

Lives outside ``mcp_server.py`` because that file is under a file-size ratchet (it may only
shrink). Everything here is PURE: it returns a constant, non-leaking message (or a payload dict)
and never touches the wire itself -- ``mcp_server`` stamps the contract fields and returns it.
No import of ``mcp_server`` at module scope (circular). The one backend interaction is
``probe_backend`` (a zero-file search runs the backend's own call on an empty file).
"""

from __future__ import annotations

import contextlib
import json
import os
import re
import tempfile
from typing import Any

from tensor_grep.backends.base import BackendExecutionError
from tensor_grep.backends.cpu_backend import InvalidRegexError
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


def investigation_options_refusal(grounding: str, rerank: str) -> str | None:
    """Constant refusals for enum inputs; never echo untrusted input as a diagnostic."""
    if grounding not in {"off", "local", "registry"}:
        return "grounding must be off, local, or registry"
    if rerank not in {"off", "auto", "cross-encoder"}:
        return "rerank must be off, auto, or cross-encoder"
    return None


# Everything tg_search's backend arm maps through ``search_error_message`` (ValueError covers
# InvalidRegexError and other parser rejections).
SEARCH_ERRORS = (
    BackendExecutionError,
    re.error,
    ArithmeticError,
    RecursionError,
    MemoryError,
    ValueError,
)
REGEX_INVALID_MESSAGE = "pattern is not a valid regular expression."
FILE_TYPE_UNKNOWN_MESSAGE = "type_filter is not a file type rg knows (see `rg --type-list`)."


def search_error_message(exc: BaseException) -> str | None:
    """Return a constant ``invalid_input`` message when a search failure is a caller-argument
    error (bad regex, unknown rg file type), else None.

    The invalid-regex test mirrors ``cli/main.py::_is_invalid_regex_error`` and
    ``backends/rust_backend.py::_is_invalid_regex_error``: ``re.error`` / ``InvalidRegexError``
    (what the CPU and Rust backends raise) or rg's / the Rust engine's message text. The exception
    text is read here, OFF the except arm, so the SEC-007 narrow-handler ratchet sees no exception
    formatting on a wire-facing arm and only a constant message reaches the caller.
    """
    if isinstance(exc, (re.error, InvalidRegexError, ArithmeticError, RecursionError, MemoryError)):
        # Parser blow-ups on a hostile pattern ("(" * 100000, "a{99999999999999999999}"):
        # a structured refusal on BOTH the zero-file and the per-file path.
        return REGEX_INVALID_MESSAGE
    text = str(exc).lower()
    if "regex parse error" in text or "error parsing regex" in text or "invalid regex" in text:
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


def probe_backend(backend: Any, pattern: str, config: Any) -> bool:
    """Give a zero-file search the verdict a one-file search would have got.

    When the walk selected no file the backend parser never ran, so a bad regex came back as a
    complete empty success. Run the SAME call the per-file loop makes (``backend.search``, same
    config, same ``BackendExecutionError`` -> CPU-fallback step) on one empty file and let its
    exception propagate to the caller's ``except`` arm -- the zero-file verdict equals the
    one-file verdict by construction, with no regex rules of our own and NO error suppression:
    every failure goes through the same ``search_error_message`` mapping as a per-file failure.
    Always returns False (usable as ``files_scanned or probe(..)``).
    """
    from tensor_grep.cli.backend_fallback import search_with_cpu_fallback

    # An empty file in the OS temp dir (never the repo / search root); mkstemp creates it, so this
    # code writes nothing. Removed on every path, BaseException included. No cache or index entry
    # is ever made for this path: only `backend.search` (a read) is called on it.
    fd, probe = tempfile.mkstemp(prefix="tg-probe-", suffix=".txt")
    os.close(fd)
    try:
        backend.search(probe, pattern, config=config)
    except BackendExecutionError as exc:
        search_with_cpu_fallback(probe, pattern, config, exc)
    finally:
        with contextlib.suppress(OSError):
            os.remove(probe)
    return False
