"""Bootstrap native ``tg search`` argv hardening (CWE-88 / MCP-276).

Kept out of ``bootstrap.py`` so the file-size ratchet is not tripped by the SEC-001
sentinel helpers. Import-time: ``bootstrap`` must already be loaded (lazy import from
``bootstrap._run_native_tg_search`` only).
"""

from __future__ import annotations

from tensor_grep.cli.bootstrap import (
    _SEARCH_ATTACHED_VALUE_SHORT_FLAGS,
    _SEARCH_PATTERN_SOURCE_FLAGS,
    _TG_ONLY_SEARCH_FLAG_PREFIXES,
    _TG_ONLY_SEARCH_FLAGS,
)
from tensor_grep.cli.bootstrap_search_guards import (
    _consumes_next_arg,
    _flags,
    _parse,
    flag_present,
    has_end_of_options,
)

# rg's own short-flag clustering, verified against ripgrep 15.1.0 by probing every letter.
# `z` (--search-zip, launches decompressor processes) is deliberately NOT a no-value flag in the
# pattern slot: a token containing it stays a pattern. `h` and `V` print help/version and exit 0.
_RG_NO_VALUE_SHORT = frozenset("0.FHILNPSUVabchilnopqsuvwx")
_RG_NUMERIC_VALUE_SHORT = frozenset("ABCMdjm")
_U64_MAX_DIGITS = "18446744073709551615"
# CWE-88: long flags that make rg EXECUTE or spawn something are never classified as flags in the
# pattern slot; they keep the `--` sentinel. EXACT option name before `=` (`--pretty` is a flag).
_RG_EXEC_LONG_FLAG_NAMES = frozenset({"pre", "pre-glob", "hostname-bin", "search-zip"})
# Long names that are FLAGS in the pattern slot: what `tg search` declares plus rg-only long flags
# (the Typer command does not declare e.g. --no-heading). Static (bootstrap must not import
# main/typer); tests/unit/test_bootstrap_search_guards_prototype.py pins both against the sources.
_TG_DECLARED_LONG_FLAG_NAMES: frozenset[str] = frozenset({
    "after-context",
    "allow-broad-generated-scan",
    "ast",
    "auto-hybrid-regex",
    "before-context",
    "binary",
    "block-buffered",
    "bm25",
    "byte-offset",
    "case-sensitive",
    "color",
    "colors",
    "column",
    "context",
    "context-separator",
    "count",
    "count-matches",
    "cpu",
    "crlf",
    "debug",
    "dfa-size-limit",
    "encoding",
    "engine",
    "enrich-ast",
    "field-context-separator",
    "field-match-separator",
    "file",
    "files",
    "files-with-matches",
    "files-without-match",
    "fixed-strings",
    "follow",
    "force-cpu",
    "format",
    "generate",
    "glob",
    "glob-case-insensitive",
    "gpu-device-ids",
    "heading",
    "hidden",
    "hostname-bin",
    "hyperlink-format",
    "iglob",
    "ignore",
    "ignore-case",
    "ignore-dot",
    "ignore-exclude",
    "ignore-file",
    "ignore-file-case-insensitive",
    "ignore-files",
    "ignore-global",
    "ignore-messages",
    "ignore-parent",
    "ignore-vcs",
    "include-zero",
    "invert-match",
    "json",
    "lang",
    "line-buffered",
    "line-number",
    "line-regexp",
    "ltl",
    "max-columns",
    "max-columns-preview",
    "max-count",
    "max-depth",
    "max-filesize",
    "maxdepth",
    "messages",
    "mmap",
    "multiline",
    "multiline-dotall",
    "ndjson",
    "no-auto-hybrid-regex",
    "no-binary",
    "no-block-buffered",
    "no-byte-offset",
    "no-column",
    "no-config",
    "no-context-separator",
    "no-crlf",
    "no-encoding",
    "no-filename",
    "no-fixed-strings",
    "no-follow",
    "no-glob-case-insensitive",
    "no-hidden",
    "no-ignore",
    "no-ignore-dot",
    "no-ignore-exclude",
    "no-ignore-file-case-insensitive",
    "no-ignore-files",
    "no-ignore-global",
    "no-ignore-messages",
    "no-ignore-parent",
    "no-ignore-vcs",
    "no-include-zero",
    "no-invert-match",
    "no-json",
    "no-line-buffered",
    "no-line-number",
    "no-max-columns-preview",
    "no-messages",
    "no-mmap",
    "no-multiline",
    "no-multiline-dotall",
    "no-one-file-system",
    "no-pcre2",
    "no-pcre2-unicode",
    "no-pre",
    "no-require-git",
    "no-search-zip",
    "no-stats",
    "no-text",
    "no-trim",
    "no-unicode",
    "null",
    "null-data",
    "one-file-system",
    "only-matching",
    "passthrough",
    "passthru",
    "path-separator",
    "pcre2",
    "pcre2-unicode",
    "pcre2-version",
    "pre",
    "pre-glob",
    "pretty",
    "quiet",
    "rank",
    "regex-size-limit",
    "regexp",
    "replace",
    "require-git",
    "search-zip",
    "semantic",
    "smart-case",
    "sort",
    "sort-files",
    "sortr",
    "stats",
    "stop-on-nonmatch",
    "text",
    "threads",
    "trace",
    "trim",
    "type",
    "type-add",
    "type-clear",
    "type-list",
    "type-not",
    "unicode",
    "unrestricted",
    "version",
    "vimgrep",
    "with-filename",
    "word-regexp",
})
_RG_ONLY_LONG_FLAG_NAMES: frozenset[str] = frozenset({
    "help",
    "no-heading",
    "no-sort-files",
    "print0",
})
_KNOWN_SEARCH_LONG_FLAG_NAMES = (
    _TG_DECLARED_LONG_FLAG_NAMES | _RG_ONLY_LONG_FLAG_NAMES
) - _RG_EXEC_LONG_FLAG_NAMES


def _exec_capable_flag_present(args: list[str]) -> bool:
    """True when rg would read an exec-capable flag in OPTION position of ``args``: ``-z`` (also
    INSIDE a cluster, ``-zebra`` is ``-z -e bra``) or ``--pre``/``--pre-glob``/``--hostname-bin``/
    ``--search-zip`` in any spelling. A pattern source elsewhere must never hide one of these from
    the sentinel policy."""
    for spelling, _ in _flags(args):
        if spelling == "-z" or (
            spelling.startswith("--") and spelling[2:] in _RG_EXEC_LONG_FLAG_NAMES
        ):
            return True
    return False


def _is_rg_unsigned_number(text: str) -> bool:
    """rg's numeric short-flag values (-A -B -C -M -d -j -m; the long forms agree): ONE optional
    leading ``+`` then one or more ASCII digits, within u64. Verified against rg 15.1.0: ``+1``,
    ``01`` and ``+0`` parse; ``-1``, ``-0``, ``++1``, ``+``, ``=``, ``+x``, ``1x``, ``1_0``,
    ``1e3``, whitespace, non-ASCII digits (``\u0661``, full-width ``+``) and values above u64
    are all ``not a valid number``."""
    digits = text[1:] if text.startswith("+") else text
    if not (digits.isascii() and digits.isdigit()):
        return False
    # NEVER int(): Python 3.11+ raises ValueError past 4300 digits, and argv is hostile input.
    # Compare as strings: strip leading zeros (rg accepts 5000 of them), then length, then lexicographic.
    significant = digits.lstrip("0") or "0"
    if len(significant) != len(_U64_MAX_DIGITS):
        return len(significant) < len(_U64_MAX_DIGITS)
    return significant <= _U64_MAX_DIGITS


def _is_plausible_rg_flag_token(token: str) -> bool:
    """True when rg itself would parse ``token`` as flag(s), not a pattern: a run of no-value short
    flags, optionally ending in ONE value-taking flag that takes the rest of the token (numeric
    flags only a numeric rest, see ``_is_rg_unsigned_number``; one leading ``=`` is dropped, ``-m=1``). Exec-capable and unknown
    long flags never count (an unknown ``--x`` stays a pattern behind ``--``)."""
    if token.startswith("--"):
        return token[2:].split("=", 1)[0] in _KNOWN_SEARCH_LONG_FLAG_NAMES
    for pos, ch in enumerate(token[1:], start=1):
        if ch in _RG_NO_VALUE_SHORT:
            continue
        if f"-{ch}" in _SEARCH_ATTACHED_VALUE_SHORT_FLAGS:
            attached = token[pos + 1 :]
            if ch in _RG_NUMERIC_VALUE_SHORT and attached:
                # rg drops ONE leading `=`; `-m=` (empty after that) is a parse error, not a flag
                return _is_rg_unsigned_number(
                    attached[1:] if attached.startswith("=") else attached
                )
            return True
        return False
    return True


def _sentinel_insertion_index(search_args: list[str]) -> int | None:
    """Index to insert ``--`` before caller-influenced dash-led positionals only."""
    if has_end_of_options(search_args):  # (S) value-aware: in `-e --` the `--` is a pattern
        return None

    dash_led = _first_dash_led_pattern_index_after_tg_flags(search_args)
    if dash_led is not None:
        return dash_led

    return _first_dash_led_positional_index(search_args)


def _first_dash_led_positional_index(search_args: list[str]) -> int | None:
    """Index of the first positional (pattern or path) that starts with ``-``, read with the guards'
    rg grammar. In rg's grammar no positional before the real ``--`` can start with ``-`` (such a
    token is an option; only a bare ``-`` is a positional), so this is None for every argv the
    sentinel builder reaches: the older hand-rolled walk's final ``return index`` was unreachable
    for the same reason. Kept as a defence-in-depth probe over the one shared tokenizer."""
    for index, (kind, token) in enumerate(_parse(search_args)):
        if kind == "sentinel":
            return None
        if kind == "positional" and token.startswith("-") and token != "-":
            return index
    return None


def _first_dash_led_pattern_index_after_tg_flags(search_args: list[str]) -> int | None:
    """Pattern index when pattern is dash-led after tg-only flags."""
    index = 0
    while index < len(search_args):
        arg = search_args[index]
        if arg in _TG_ONLY_SEARCH_FLAGS or any(
            arg.startswith(prefix) for prefix in _TG_ONLY_SEARCH_FLAG_PREFIXES
        ):
            # A value-taking tg-only flag (`-g`, `--glob`, `--lang`) owns the NEXT token.
            index += 2 if _consumes_next_arg(arg) else 1
            continue
        break
    remainder = search_args[index:]
    # When `-e`/`-f`/`--regexp`/`--file` supplies the pattern there is no dash-led pattern slot:
    # every dash-led token is an option (`-e --` is pattern `--`; `-kq` after it is a cluster).
    if not remainder:
        return None
    # The early return is for the pattern-source case only, and never over an exec-capable flag:
    # `-zebra` is `-z -e bra` and `--pre=sh -efoo` carries `-e`, yet both keep the sentinel.
    if flag_present(remainder, _SEARCH_PATTERN_SOURCE_FLAGS) and not _exec_capable_flag_present(
        remainder
    ):
        return None
    if all(token.startswith("-") for token in remainder):
        return index
    if (
        len(remainder) >= 2
        and remainder[0].startswith("-")
        and len(remainder[0]) > 2
        and not remainder[1].startswith("-")
        and not _is_plausible_rg_flag_token(remainder[0])
    ):
        return index
    return None


def bootstrap_native_tg_search_argv(search_args: list[str]) -> list[str]:
    """Insert ``--`` before dash-led caller positionals for native delegation."""
    if has_end_of_options(search_args):
        return list(search_args)
    insert_at = _sentinel_insertion_index(search_args)
    if insert_at is None:
        return list(search_args)
    return [*search_args[:insert_at], "--", *search_args[insert_at:]]


def run_native_tg_search(binary_name: str, search_args: list[str]) -> int:
    from tensor_grep.cli.bootstrap import _streaming_passthrough_returncode

    return _streaming_passthrough_returncode([
        binary_name,
        "search",
        *bootstrap_native_tg_search_argv(search_args),
    ])
