"""Append a bounded literal-code hint to failed direct rg passthroughs."""

import os
import sys


def emit_literal_pattern_hint(search_args: list[str], exit_code: int) -> None:
    if exit_code != 2:
        return
    from tensor_grep.cli.bootstrap_search_guards import (
        engine_selects_pcre2,
        fixed_strings_requested,
        flag_present,
        regex_patterns,
    )

    if fixed_strings_requested(search_args) or engine_selects_pcre2(search_args):
        return
    if "RIPGREP_CONFIG_PATH" in os.environ and not flag_present(search_args, {"--no-config"}):
        return  # The config may select an engine with different syntax.
    try:
        from tensor_grep.rust_core import _literal_pattern_hint
    except ImportError:
        return  # An older/unavailable extension must not replace the original rg failure.
    for pattern in regex_patterns(search_args)[:64]:
        try:
            hint = _literal_pattern_hint(pattern)
        except (UnicodeError, ValueError, RuntimeError):
            continue  # A diagnostic cannot turn the original engine failure into a traceback.
        if hint:
            sys.stderr.write(hint + "\n")
            return
