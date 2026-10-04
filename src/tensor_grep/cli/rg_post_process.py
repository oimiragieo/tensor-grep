"""Guard / fallback for `-o` / `--replace` output.

With rg available, `RipgrepBackend` answers a structured `-o` / `-r` request from rg's `--json`
data through the rg-compatible printer in `backends/rg_json_render.py` (rules proven against rg's
own plain output by a differential fuzz); those lines carry `rg_kind`.

Without rg the pipeline keeps serving `-o` / `-r` from the engine it selected, exactly as main
did: such lines have no `rg_kind`, and this module applies main's own post-processing to them
(`_replace_lines` / `_only_matching_lines`, moved here verbatim from `cli/main.py`). The rg printer
must never turn a request main served into an error.
"""

from __future__ import annotations

import re
from dataclasses import replace
from typing import TYPE_CHECKING

from tensor_grep.cli import rg_replacement as _rg_replacement
from tensor_grep.core.case_semantics import case_regex_flags

if TYPE_CHECKING:
    from tensor_grep.core.config import SearchConfig
    from tensor_grep.core.result import MatchLine


def _regex(pattern: str, config: SearchConfig) -> re.Pattern[str]:
    flags = case_regex_flags(config, pattern, strict=False)
    if config.fixed_strings:
        return re.compile(re.escape(pattern), flags)
    if config.line_regexp:
        return re.compile(f"^{pattern}$", flags)
    if config.word_regexp:
        return re.compile(rf"\b{pattern}\b", flags)
    return re.compile(pattern, flags)


def _replace_line(match: MatchLine, pattern: str, config: SearchConfig) -> MatchLine:
    replacement = config.replace_str or ""
    regex = _regex(pattern, config)
    if config.fixed_strings and "$" not in replacement:
        if regex.flags & re.IGNORECASE:
            new_text = re.sub(
                re.escape(pattern),
                replacement.replace("\\", r"\\"),
                match.text,
                flags=re.IGNORECASE,
            )
        else:
            new_text = match.text.replace(pattern, replacement)
        return replace(match, text=new_text)

    def _expand(current: re.Match[str]) -> str:
        return _rg_replacement.expand_ripgrep_replacement(replacement, current)

    return replace(match, text=regex.sub(_expand, match.text))


def _only_matching_line(match: MatchLine, pattern: str, config: SearchConfig) -> list[MatchLine]:
    extracted: list[MatchLine] = []
    for token in _regex(pattern, config).findall(match.text):
        if isinstance(token, tuple):
            token = "".join(token)
        token_text = str(token)
        if token_text:
            extracted.append(replace(match, text=token_text))
    return extracted


def post_process_matches(
    matches: list[MatchLine], pattern: str, config: SearchConfig, only_matching: bool
) -> list[MatchLine]:
    """Lines rg rendered (``rg_kind`` set) pass through; engine lines get main's processing."""
    if not (only_matching or config.replace_str is not None):
        return matches
    out: list[MatchLine] = []
    for match in matches:
        if match.rg_kind is not None:
            out.append(match)
            continue
        current = match
        if config.replace_str is not None:
            current = _replace_line(current, pattern, config)
        if only_matching:
            out.extend(_only_matching_line(current, pattern, config))
        else:
            out.append(current)
    return out
