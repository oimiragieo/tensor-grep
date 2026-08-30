"""Bootstrap native ``tg search`` argv hardening (CWE-88 / MCP-276).

Kept out of ``bootstrap.py`` so the file-size ratchet is not tripped by the SEC-001
sentinel helpers. Import-time: ``bootstrap`` must already be loaded (lazy import from
``bootstrap._run_native_tg_search`` only).
"""

from __future__ import annotations

from tensor_grep.cli.bootstrap import (
    _SEARCH_FLAGS_WITH_VALUES,
    _SEARCH_PATTERN_SOURCE_FLAGS,
    _TG_ONLY_SEARCH_FLAG_PREFIXES,
    _TG_ONLY_SEARCH_FLAGS,
    _attached_cluster_value_offset,
    _is_short_flag_with_attached_value,
    _search_args_contains_pattern_source_flag,
)


def _sentinel_insertion_index(search_args: list[str]) -> int | None:
    """Index to insert ``--`` before caller-influenced dash-led positionals only."""
    if "--" in search_args:
        return None

    dash_led = _first_dash_led_pattern_index_after_tg_flags(search_args)
    if dash_led is not None:
        return dash_led

    return _first_dash_led_positional_index(search_args)


def _first_dash_led_positional_index(search_args: list[str]) -> int | None:
    """First bare-pattern or path positional that starts with ``-``."""
    bare_pattern_seen = False
    regexp_pattern_seen = _search_args_contains_pattern_source_flag(search_args)
    skip_next = False
    parse_options = True
    for index, arg in enumerate(search_args):
        if skip_next:
            skip_next = False
            continue
        if parse_options and arg == "--":
            return None
        if parse_options:
            if arg in _SEARCH_PATTERN_SOURCE_FLAGS:
                regexp_pattern_seen = True
                skip_next = index + 1 < len(search_args)
                continue
            if any(arg.startswith(f"{flag}=") for flag in _SEARCH_PATTERN_SOURCE_FLAGS):
                regexp_pattern_seen = True
                continue
            offset = _attached_cluster_value_offset(arg)
            if offset is not None:
                ch = arg[offset]
                if ch in ("e", "f"):
                    regexp_pattern_seen = True
                if offset == len(arg) - 1:
                    skip_next = index + 1 < len(search_args)
                continue
            if arg in _SEARCH_FLAGS_WITH_VALUES:
                skip_next = index + 1 < len(search_args)
                continue
            if any(arg.startswith(f"{flag}=") for flag in _SEARCH_FLAGS_WITH_VALUES):
                continue
            if _is_short_flag_with_attached_value(arg):
                continue
            if arg.startswith("-"):
                continue
        if not arg.startswith("-"):
            if not regexp_pattern_seen and not bare_pattern_seen:
                bare_pattern_seen = True
            continue
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
            index += 1
            continue
        break
    remainder = search_args[index:]
    if not remainder:
        return None
    if all(token.startswith("-") for token in remainder):
        return index
    if (
        len(remainder) >= 2
        and remainder[0].startswith("-")
        and len(remainder[0]) > 2
        and not remainder[1].startswith("-")
    ):
        return index
    return None


def bootstrap_native_tg_search_argv(search_args: list[str]) -> list[str]:
    """Insert ``--`` before dash-led caller positionals for native delegation."""
    if "--" in search_args:
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
