"""Guard for `-o` / `--replace` output, which is rg's own.

`RipgrepBackend` answers a structured `-o` / `-r` request from rg's `--json` data through the
rg-compatible printer in `backends/rg_json_render.py` (rules proven against rg's own plain output
by a differential fuzz). What is left here is the
fail-closed check: a result line that did not come from that route (a non-rg engine, which the
pipeline never routes `-o`/`-r` to) is refused with `BackendExecutionError` instead of being
rendered by guesswork. The CLI turns the error into a structured exit 2.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from tensor_grep.backends.base import BackendExecutionError

if TYPE_CHECKING:
    from tensor_grep.core.config import SearchConfig
    from tensor_grep.core.result import MatchLine


def post_process_matches(
    matches: list[MatchLine], pattern: str, config: SearchConfig, only_matching: bool
) -> list[MatchLine]:
    """Return ``matches`` unchanged, refusing any line rg did not render for -o / -r."""
    if only_matching or config.replace_str is not None:
        for match in matches:
            if match.rg_kind is None:
                raise BackendExecutionError(
                    f"cannot produce the -o/-r output for {match.file}:{match.line_number}: the "
                    "line was not rendered by rg; refusing to rebuild rg's output in Python"
                )
    return matches
