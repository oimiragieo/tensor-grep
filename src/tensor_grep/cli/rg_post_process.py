"""`-o` / `--replace` post-processing of search results.

Split out of `cli/main.py` under the file-size ratchet. When rg ran the search, every MatchLine
carries rg's AUTHORITATIVE submatches (0-based UTF-8 byte offsets), so extraction and
replacement slice the original line at those offsets: the user's regex is not re-evaluated and
case sensitivity is never guessed (the pipeline routes patterns whose smart-case answer only rg
knows, e.g. ``foo\\S``, to rg). Only lines WITHOUT submatches (non-rg engines) fall back to the
Python regex, whose case flags come from the shared resolver in `core/case_semantics.py`.
"""

from __future__ import annotations

import re
from dataclasses import replace
from typing import TYPE_CHECKING

from tensor_grep.backends.base import BackendExecutionError
from tensor_grep.cli.rg_replacement import expand_ripgrep_replacement
from tensor_grep.core.case_semantics import case_regex_flags

if TYPE_CHECKING:
    from tensor_grep.core.config import SearchConfig
    from tensor_grep.core.result import MatchLine


def _byte_spans(match: MatchLine) -> list[tuple[int, int]]:
    spans: list[tuple[int, int]] = []
    for sub in match.submatches or ():
        if not isinstance(sub, dict):
            continue
        start, end = sub.get("start"), sub.get("end")
        if (
            isinstance(start, int)
            and isinstance(end, int)
            and not isinstance(start, bool)
            and not isinstance(end, bool)
            and 0 <= start <= end
        ):
            spans.append((start, end))
    return spans


def _compile(pattern: str, config: SearchConfig, flags: int) -> re.Pattern[str]:
    if config.fixed_strings:
        return re.compile(re.escape(pattern), flags)
    if config.line_regexp:
        return re.compile(f"^{pattern}$", flags)
    if config.word_regexp:
        return re.compile(rf"\b{pattern}\b", flags)
    return re.compile(pattern, flags)


def _expand_at_span(
    pattern: str, config: SearchConfig, text: str, char_start: int, char_end: int, template: str
) -> str:
    """Expand ``$1``-style groups for a span rg already matched.

    rg owns WHAT matched; Python only recovers the capture groups. The regex is anchored at rg's
    start offset and must end exactly at rg's end offset, otherwise we refuse (fail closed)
    rather than substitute something rg did not match. Both case variants are tried because
    which one rg used is not recoverable from the span alone; an exact span match is the proof.
    """
    first = case_regex_flags(config, pattern, strict=False)
    for flags in dict.fromkeys((first, re.IGNORECASE, 0)):
        found = _compile(pattern, config, flags).match(text, char_start)
        if found is not None and found.end() == char_end:
            return expand_ripgrep_replacement(template, found)
    raise BackendExecutionError(
        "cannot reproduce rg's --replace capture groups for the matched span "
        f"[{char_start}:{char_end}] of pattern {pattern!r}"
    )


def _replacement_for_span(
    pattern: str, config: SearchConfig, text: str, raw: bytes, start: int, end: int
) -> str:
    """The `--replace` output for ONE rg-matched span (literal, or with `$N` groups expanded)."""
    template = config.replace_str or ""
    if "$" not in template:
        return template
    c_start = len(raw[:start].decode("utf-8", errors="replace"))
    c_end = len(raw[:end].decode("utf-8", errors="replace"))
    return _expand_at_span(pattern, config, text, c_start, c_end, template)


def replace_lines(matches: list[MatchLine], pattern: str, config: SearchConfig) -> list[MatchLine]:
    if config.replace_str is None:
        return matches
    template = config.replace_str
    regex: re.Pattern[str] | None = None
    out: list[MatchLine] = []
    for match in matches:
        spans = _byte_spans(match)
        if spans:
            raw = match.text.encode("utf-8")
            pieces: list[str] = []
            cursor = 0
            for start, end in spans:
                pieces.append(raw[cursor:start].decode("utf-8", errors="replace"))
                pieces.append(_replacement_for_span(pattern, config, match.text, raw, start, end))
                cursor = end
            pieces.append(raw[cursor:].decode("utf-8", errors="replace"))
            # rg's offsets index the ORIGINAL text; drop them so nothing re-reads stale spans
            out.append(replace(match, text="".join(pieces), submatches=None))
            continue
        if regex is None:
            regex = _compile(pattern, config, case_regex_flags(config, pattern))
        flags = regex.flags & re.IGNORECASE
        if config.fixed_strings and "$" not in template:
            if flags:
                new_text = re.sub(
                    re.escape(pattern),
                    template.replace("\\", r"\\"),
                    match.text,
                    flags=re.IGNORECASE,
                )
            else:
                new_text = match.text.replace(pattern, template)
            out.append(replace(match, text=new_text))
            continue
        new_text = regex.sub(lambda m: expand_ripgrep_replacement(template, m), match.text)
        out.append(replace(match, text=new_text))
    return out


def only_matching_lines(
    matches: list[MatchLine], pattern: str, config: SearchConfig
) -> list[MatchLine]:
    regex: re.Pattern[str] | None = None
    out: list[MatchLine] = []
    for match in matches:
        spans = _byte_spans(match)
        if spans:
            raw = match.text.encode("utf-8")
            for start, end in spans:
                if config.replace_str is not None:
                    # rg `-o -r X`: every match alone, replacement applied to THAT span; the
                    # regex is never re-run on already-replaced text. Empty results are kept.
                    token_text = _replacement_for_span(pattern, config, match.text, raw, start, end)
                    out.append(replace(match, text=token_text, submatches=None))
                    continue
                token_text = raw[start:end].decode("utf-8", errors="replace")
                if token_text:
                    out.append(replace(match, text=token_text, submatches=None))
            continue
        if config.replace_str is not None:
            # no rg offsets (non-rg engine): historical behaviour, replace then extract
            match = replace_lines([match], pattern, config)[0]
        if regex is None:
            regex = _compile(pattern, config, case_regex_flags(config, pattern))
        for token in regex.findall(match.text):
            if isinstance(token, tuple):
                token = "".join(token)
            token_text = str(token)
            if token_text:
                out.append(replace(match, text=token_text))
    return out
