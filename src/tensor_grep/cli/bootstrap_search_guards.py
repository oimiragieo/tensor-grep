"""Search-argv guards for the Python front door, kept out of bootstrap.py (file-size ratchet).

ONE tokenizer (`_parse` / `_flags`) models how rg itself reads argv, and every public helper here
is derived from it, so no two scans can disagree about what is a flag, a flag VALUE, a positional
or the real ``--``. Facts verified against ripgrep 15.1.0 (see the prototype test file):

* ``--`` ends options, unless it is the VALUE of a value-taking flag (``-e --``, ``-g --``).
* A short cluster (``-ie``, ``-iefoo``, ``-m5``) is read letter by letter; the first value-taking
  letter takes the rest of the token as its value, or the NEXT token when nothing is attached.
  That next token is consumed even when it starts with ``-`` or is ``--``.
* An attached short value may carry one leading ``=`` which is dropped: ``-e=foo`` is pattern
  ``foo`` (rg's argv parser strips it); ``--regexp=foo`` keeps everything after the first ``=``.
* ``-`` alone is a positional (stdin as a path, or the pattern when it is the first positional).
* With ``-e``/``-f`` present no positional is the pattern: every positional is a path.
* Boolean flags are last-wins against their negation (``-F`` ... ``--no-fixed-strings``).

Import lazily from ``bootstrap`` (this module imports ``bootstrap`` for its flag tables).
Raw-argv rule: every function takes RAW argv and never reassigns it; ``option_tokens`` output is
never fed back into another argv-taking function (a second pass would consume one more value).
"""

from __future__ import annotations

import re
import warnings
from collections.abc import Iterator, Sequence
from collections.abc import Set as AbstractSet

from tensor_grep.cli import bootstrap as _b

# rg has no `--no-quiet`; `--no-messages` only hides rg's own errors (the full CLI hides the
# defaulted-scope note on quiet alone), so it is not a quiet flag.
_QUIET_NAMES = frozenset({"-q", "--quiet"})
_PATTERN_ONLY_NAMES = frozenset({"-e", "--regexp"})
# Last-wins negations. They are not in bootstrap.py, so they are pinned here by name.
_NEGATED_BY = {
    "-F": "--no-fixed-strings",
    "--fixed-strings": "--no-fixed-strings",
    "-P": "--no-pcre2",
    "--pcre2": "--no-pcre2",
    "-.": "--no-hidden",
    "--hidden": "--no-hidden",
}
_AGREED_RE_ERRORS = (
    "unbalanced parenthesis",
    "missing ), unterminated subpattern",
    "min repeat greater than max repeat",
)


def _short_value_pos(arg: str) -> int | None:
    """Index into ``arg`` of the first value-taking letter of a short cluster, else None."""
    if not arg.startswith("-") or arg.startswith("--"):
        return None
    for pos, ch in enumerate(arg[1:], start=1):
        if f"-{ch}" in _b._SEARCH_ATTACHED_VALUE_SHORT_FLAGS:
            return pos
    return None


def _consumes_next_arg(arg: str) -> bool:
    """True when the NEXT argv token is ``arg``'s value: a bare value-taking long/short flag
    (``-e``, ``--replace``) or a short cluster whose value-taking letter is its LAST character
    (``-ie``, ``-ig``). ``-iefoo`` and ``--regexp=foo`` carry their value and consume nothing."""
    if arg in _b._SEARCH_PATTERN_SOURCE_FLAGS or arg in _b._SEARCH_FLAGS_WITH_VALUES:
        return True
    return _short_value_pos(arg) == len(arg) - 1


def _parse(search_args: Sequence[str]) -> Iterator[tuple[str, str]]:
    """Yield ``(kind, token)``: option | value | positional | sentinel. The single walk."""
    skip = False
    options_open = True
    for arg in search_args:
        if skip:
            skip = False
            yield "value", arg
        elif options_open and arg == "--":
            options_open = False
            yield "sentinel", arg
        elif options_open and arg != "-" and arg.startswith("-"):
            skip = _consumes_next_arg(arg)
            yield "option", arg
        else:
            yield "positional", arg


def end_of_options_index(search_args: Sequence[str]) -> int:
    """Index of the ``--`` that really ends rg's options; ``len(search_args)`` when there is none
    (NEVER None, so callers can slice with it). ``-e --`` and ``-ie --`` pass ``--`` as a VALUE."""
    for index, (kind, _) in enumerate(_parse(search_args)):
        if kind == "sentinel":
            return index
    return len(search_args)


def has_end_of_options(search_args: Sequence[str]) -> bool:
    return end_of_options_index(search_args) < len(search_args)


def option_tokens(search_args: Sequence[str]) -> Iterator[str]:
    """Only tokens in OPTION position: never a flag value, a positional, or anything after the
    real ``--``."""
    for kind, token in _parse(search_args):
        if kind == "option":
            yield token


def positionals(search_args: Sequence[str]) -> list[str]:
    """Positional tokens in order (``-`` and everything after the real ``--`` included)."""
    return [token for kind, token in _parse(search_args) if kind == "positional"]


def _flags(search_args: Sequence[str]) -> Iterator[tuple[str, str | None]]:
    """Yield ``(spelling, value)`` for every flag in order: ``-ie foo`` yields ``-i`` then
    ``-e`` with value ``foo``. Expansion of a cluster stops at its first value-taking letter,
    which itself counts as present."""
    toks = list(_parse(search_args))
    for index, (kind, token) in enumerate(toks):
        if kind != "option":
            continue
        following = (
            toks[index + 1][1] if index + 1 < len(toks) and toks[index + 1][0] == "value" else None
        )
        if token.startswith("--"):
            name, equals, attached = token.partition("=")
            yield name, (attached if equals else following)
            continue
        for pos, ch in enumerate(token[1:], start=1):
            if f"-{ch}" not in _b._SEARCH_ATTACHED_VALUE_SHORT_FLAGS:
                yield f"-{ch}", None
                continue
            rest = token[pos + 1 :]
            yield f"-{ch}", (rest[1:] if rest.startswith("=") else rest) if rest else following
            break


def _cancelled_by(name: str) -> str | None:
    """The flag that switches ``name`` back off (rg's last-wins pairs): ``-F`` and
    ``--fixed-strings`` by ``--no-fixed-strings``, and every ``--no-X`` by ``--X`` (so
    ``--no-ignore`` by ``--ignore``, ``--no-ignore-vcs`` by ``--ignore-vcs``)."""
    if name in _NEGATED_BY:
        return _NEGATED_BY[name]
    return "--" + name[5:] if name.startswith("--no-") else None


def flag_present(search_args: Sequence[str], names: AbstractSet[str]) -> bool:
    """True when a flag spelled in ``names`` is in effect in RAW argv (this takes the caller's raw
    argument list, never an ``option_tokens`` result): in option position, not a value, not after
    the real ``--``. Short flags are found inside clusters (``-iF`` has ``-F``); a long name matches
    exactly or as ``--name=...``. Last-wins PER NAME: ``-F ... --no-fixed-strings`` is off,
    ``--no-ignore --ignore`` is off, while ``--no-ignore --ignore-dot`` leaves ``--no-ignore`` on."""
    cancels: dict[str, list[str]] = {}
    for name in names:
        off = _cancelled_by(name)
        if off is not None and off not in names:
            cancels.setdefault(off, []).append(name)
    state = dict.fromkeys(names, False)
    for spelling, _ in _flags(search_args):
        if spelling in names:
            state[spelling] = True
        for name in cancels.get(spelling, ()):
            state[name] = False
    return any(state.values())


def request_quiet(search_args: Sequence[str]) -> bool:
    return flag_present(search_args, _QUIET_NAMES)


def split_pattern_and_paths(search_args: Sequence[str]) -> tuple[list[str], list[str]]:
    """``(pattern positionals, path positionals)`` exactly as rg assigns them. The first positional
    is the pattern unless ``-e``/``-f`` (or their long forms) supplied one; under ``--files`` rg
    takes no pattern at all, so EVERY positional is a path (``rg --files foo src`` searches the
    paths ``foo`` and ``src``). ``-`` is a positional like any other; after the real ``--`` every
    token is."""
    pos = positionals(search_args)
    if flag_present(search_args, {"--files"}) or flag_present(
        search_args, _b._SEARCH_PATTERN_SOURCE_FLAGS
    ):
        return [], pos
    return pos[:1], pos[1:]


def path_args(search_args: Sequence[str]) -> list[str]:
    """Explicit PATH positionals (empty when the scope defaulted to the cwd). The one grammar
    behind ``bootstrap._search_path_args_raw``."""
    return split_pattern_and_paths(search_args)[1]


def _names_stdin_path(search_args: Sequence[str]) -> bool:
    """A bare ``-`` in a PATH position is stdin. It is the PATTERN when it is the first
    positional and no ``-e``/``-f`` supplied one, and a VALUE in ``-e -``."""
    return "-" in path_args(search_args)


def scope_note_applies(search_args: Sequence[str]) -> bool:
    return (
        _b._search_args_paths_defaulted(list(search_args))
        and not _names_stdin_path(search_args)
        and not request_quiet(search_args)
    )


def regex_patterns(search_args: Sequence[str]) -> list[str]:
    """Inline pattern text, as rg would read it. ``-e``/``--regexp`` in every spelling (separated,
    ``=``-attached, cluster-attached ``-efoo``/``-iefoo``/``-e=foo``) supply patterns; ``-f``/
    ``--file`` supply them FROM A FILE (not read here, contributes nothing). With any pattern
    source present NO positional is a pattern; otherwise the first positional is (also after
    the real ``--``)."""
    patterns: list[str] = []
    source = False
    for spelling, value in _flags(search_args):
        if spelling in _PATTERN_ONLY_NAMES:
            source = True
            if value is not None:
                patterns.append(value)
        elif spelling in ("-f", "--file"):
            source = True
    if flag_present(search_args, {"--files"}):
        return []  # rg --files reads no pattern; every positional is a path
    return patterns if source else split_pattern_and_paths(search_args)[0]


def fixed_strings_requested(search_args: Sequence[str]) -> bool:
    return flag_present(search_args, _b._SEARCH_LITERAL_FLAGS)


def engine_selects_pcre2(search_args: Sequence[str]) -> bool:
    """Last-wins regex engine: ``-P``/``--pcre2``/``--engine pcre2|auto``/``--auto-hybrid-regex``
    select PCRE2 (possibly as a fallback); ``--no-pcre2``/``--no-auto-hybrid-regex``/``--engine
    default`` reset it. A value (``-e -P``) or anything after the real ``--`` selects nothing."""
    engine = "default"
    for spelling, value in _flags(search_args):
        if spelling in ("-P", "--pcre2"):
            engine = "pcre2"
        elif spelling in ("--no-pcre2", "--no-auto-hybrid-regex"):
            engine = "default"
        elif spelling == "--auto-hybrid-regex":
            engine = "auto"
        elif spelling == "--engine" and value is not None:
            engine = value
    return engine in ("pcre2", "auto")


def pattern_invalid_in_both_engines(pattern: str) -> bool:
    """Python ``re`` rejects some rg-valid syntax (``\\p{..}``, ``(?<n>..)``, ``(?-i)``), so only
    errors Rust regex also raises count. 'nothing to repeat' counts only at the start or after
    ``(`` / ``|`` (rg accepts ``^*``). A pattern containing ``[`` is NEVER pre-rejected: Python and
    Rust parse classes differently (``[[:alpha:](]`` is a valid Rust class), so it goes to the
    real engine, which reports its own error (exit 2)."""
    if "[" in pattern:
        return False
    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        try:
            re.compile(pattern)
        except (RecursionError, OverflowError, MemoryError, ValueError):
            # Hostile patterns ("(" * 100000, "a{99999999999999999999}") crash Python's parser with
            # something that is not re.error. We cannot tell whether rg accepts them, so do NOT
            # pre-reject: the real engine reports its own error (exit 2).
            return False
        except re.error as exc:
            if exc.msg in _AGREED_RE_ERRORS:
                return True
            pos = exc.pos or 0
            return exc.msg == "nothing to repeat" and (pos == 0 or pattern[pos - 1] in "(|")
    return False


def obviously_invalid_regex(search_args: Sequence[str]) -> bool:
    if fixed_strings_requested(search_args) or engine_selects_pcre2(search_args):
        return False
    return any(p and pattern_invalid_in_both_engines(p) for p in regex_patterns(search_args))


def explicit_rg_format(search_args: Sequence[str]) -> bool:
    """The FIRST ``--format`` in option position says ``rg`` (``--format rg`` / ``--format=rg``)."""
    for spelling, value in _flags(search_args):
        if spelling == "--format":
            return value == "rg"
    return False


def strip_noop_rg_format(search_args: Sequence[str]) -> list[str] | None:
    """``search_args`` without option-position ``--format rg`` / ``--format=rg`` tokens, or None
    when any ``--format`` names something else (or has no value). A ``--format`` that is another
    flag's VALUE or comes after the real ``--`` is not a flag and is kept as it is."""
    toks = list(_parse(search_args))
    out: list[str] = []
    skip = False
    for index, (kind, token) in enumerate(toks):
        if skip:
            skip = False
            continue
        if kind == "option" and (token == "--format" or token.startswith("--format=")):
            if token == "--format":
                nxt = toks[index + 1] if index + 1 < len(toks) else None
                if nxt is None or nxt[0] != "value" or nxt[1] != "rg":
                    return None
                skip = True
            elif token.partition("=")[2] != "rg":
                return None
            continue
        out.append(token)
    return out


def has_any_option(search_args: Sequence[str]) -> bool:
    """Is there ANY flag in option position (``-e -x`` has one; the ``-x`` is its value)?"""
    return next(option_tokens(search_args), None) is not None


def resolve_native_or_exit() -> str | None:
    """``resolve_native_tg_binary()`` as a string, or a clean ASCII exit 2 when an explicit
    ``TG_NATIVE_TG_BINARY`` / ``TG_MCP_TG_BINARY`` override names a file that does not exist (by
    design a hard error, but never a traceback: exit 1 would read as "no match")."""
    try:
        path = _b.resolve_native_tg_binary()
    except FileNotFoundError as exc:
        import sys

        detail = str(exc).encode("ascii", "backslashreplace").decode("ascii")
        sys.stderr.write(f"error: {detail} Fix or unset TG_NATIVE_TG_BINARY / TG_MCP_TG_BINARY.\n")
        raise SystemExit(2) from exc
    return str(path) if path else None
