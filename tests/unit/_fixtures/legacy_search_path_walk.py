"""FROZEN copy of the pre-prototype search-path walker, for DIFFERENTIAL USE ONLY.

Frozen from main 094dc972ac91c49543c1a719805de5f5a28034e8 (`src/tensor_grep/cli/bootstrap.py`):
the four functions and the four flag tables they read, byte-for-byte except this header and the
`from __future__` line. It exists so tests/unit/test_bootstrap_search_guards_prototype.py can
compare the shipped walker with the one it replaced WITHOUT reading git (`origin/main` moves).
Do not edit, do not import from production code, do not "fix" it: its bare-`-` and `--files`
behaviour is the documented difference the differential asserts.
"""

from __future__ import annotations

_SEARCH_PATTERN_FLAGS = {"-e", "--regexp"}
_SEARCH_PATTERN_SOURCE_FLAGS = _SEARCH_PATTERN_FLAGS | {"-f", "--file"}
_SEARCH_FLAGS_WITH_VALUES = {
    "-A",
    "-B",
    "-C",
    "-E",
    "-M",
    "-g",
    "-j",
    "-m",
    "--after-context",
    "--before-context",
    "--color",
    "--colors",
    "--context",
    "--context-separator",
    "--dfa-size-limit",
    "--encoding",
    "--engine",
    "--field-context-separator",
    "--field-match-separator",
    "--file",
    "-f",
    "--format",
    "--generate",
    "--glob",
    "--gpu-device-ids",
    "--hostname-bin",
    "--hyperlink-format",
    "--iglob",
    "--ignore-file",
    "--lang",
    "--max-columns",
    "--max-count",
    "--max-depth",
    "--maxdepth",
    "--max-filesize",
    "--path-separator",
    "--pre",
    "--pre-glob",
    "--regex-size-limit",
    "--replace",
    "--sort",
    "--sortr",
    "--threads",
    "--type",
    "--type-add",
    "--type-clear",
    "--type-not",
    "-d",
    "-r",
    "-t",
    "-T",
}
_SEARCH_ATTACHED_VALUE_SHORT_FLAGS = (
    "-A",
    "-B",
    "-C",
    "-E",
    "-M",
    "-d",
    "-e",
    "-f",
    "-g",
    "-j",
    "-m",
    "-r",
    "-t",
    "-T",
)


def _is_short_flag_with_attached_value(arg: str) -> bool:
    if not arg.startswith("-") or arg.startswith("--"):
        return False
    return any(
        arg.startswith(flag) and len(arg) > len(flag) for flag in _SEARCH_ATTACHED_VALUE_SHORT_FLAGS
    )


def _attached_cluster_value_offset(arg: str) -> int | None:
    """For a bundled/clustered short-flag token (`-ieneedle`, `-tpy`, a plain boolean cluster
    like `-in`, ...), return the index INTO `arg` of the first ATTACHED-VALUE short-flag
    character in the cluster, or `None` if the token carries no attached-value flag at all (a
    plain boolean cluster, or not a dash-prefixed multi-char token to begin with). Shared by
    `_search_args_contains_pattern_source_flag`'s pre-pass and `_search_path_args_raw`'s
    extraction walk below so the two passes cannot silently diverge on which characters count
    (task #269 independent-gate BLOCKING-1's own root cause was two separate passes computing
    the same thing differently)."""
    if arg.startswith("--") or len(arg) <= 2 or not arg.startswith("-"):
        return None
    for offset, ch in enumerate(arg[1:], start=1):
        if f"-{ch}" in _SEARCH_ATTACHED_VALUE_SHORT_FLAGS:
            return offset
    return None


def _search_args_contains_pattern_source_flag(search_args: list[str]) -> bool:
    """Pre-pass: does ANY pattern-source flag (`-e`/`--regexp`/`-f`/`--file`, in every accepted
    spelling -- exact, `--flag=value`, attached short (`-eVAL`), and mid-bundle attached short
    (`-ieVAL`)) appear anywhere in `search_args` BEFORE a `--` end-of-options sentinel?

    Task #269 independent-gate BLOCKING-1: rg's own grammar is ORDER-INDEPENDENT -- a pattern
    can be supplied via a flag either before OR after the positional PATH
    (`rg sub -eneedle` == `rg -eneedle sub`) -- but `_search_path_args_raw`'s extraction walk
    is a single left-to-right pass whose `regexp_pattern_seen` state used to start `False` and
    only flip `True` at the MOMENT it encountered the flag. A PATH positional occurring BEFORE
    that flag in argv (`tg search otherdir -eneedle`) was therefore silently misread as the
    bare pattern under the "first non-flag token is the pattern" rule, `_search_path_args_raw`
    returned the wrong (empty) root list, and `_run_rg_passthrough` injected the WRONG root's
    ignore file -- reproducing the #264 signature (`--json` landed on the correct engine,
    plain text did not) A FOURTH time, inside this same PR. This pre-pass supplies the correct
    STARTING value for `regexp_pattern_seen` instead, so a PATH appearing anywhere relative to
    the flag is handled identically.

    Deliberately mirrors ONLY the flag-classification portion of `_search_path_args_raw`'s
    walk (not positional extraction, which this function does not need) -- and shares
    `_attached_cluster_value_offset` with it rather than re-deriving the same cluster logic a
    second time, so the two passes cannot drift apart on which argv shapes count.
    """
    skip_next = False
    for arg in search_args:
        if skip_next:
            skip_next = False
            continue
        if arg == "--":
            break
        if arg in _SEARCH_PATTERN_SOURCE_FLAGS:
            return True
        if any(arg.startswith(f"{flag}=") for flag in _SEARCH_PATTERN_SOURCE_FLAGS):
            return True
        offset = _attached_cluster_value_offset(arg)
        if offset is not None:
            if arg[offset] in ("e", "f"):
                return True
            # Independent-gate re-gate BLOCKING-2: a non-pattern-source attached-value flag
            # (e.g. `-tpy`, `-im5`) is only self-contained when its value is ATTACHED within
            # the same token. When the value char is the token's LAST character (`-ir`, `-ig`
            # -- a bundled `-i` plus `-r`/`-g` with the value in the NEXT argv token, `-ir
            # needle`), that next token is the flag's VALUE, not a genuine positional -- it
            # must be skipped here too, exactly like the extraction walk below already does,
            # or a value that happens to look like a pattern-source flag gets misclassified.
            # The prior version of this pre-pass shared `_attached_cluster_value_offset` (the
            # OFFSET) with the extraction walk but let each pass decide `skip_next`
            # independently -- the offset was unified, the CONSUMPTION was not, and that
            # asymmetry (`-r -e needle` correctly not a pattern source; `-ir -e needle`
            # wrongly WAS, because the un-skipped "-e" got read as its own flag) is exactly
            # the "one rule, two implementations" trap this whole PR exists to close.
            if offset == len(arg) - 1:
                skip_next = True
            continue
        if arg in _SEARCH_FLAGS_WITH_VALUES:
            skip_next = True
            continue
        if any(arg.startswith(f"{flag}=") for flag in _SEARCH_FLAGS_WITH_VALUES):
            continue
        # A bare boolean flag (no value) or a genuine positional needs no further handling
        # here -- this pre-pass only answers "does a pattern-source flag appear anywhere
        # before `--`", not where positionals fall; that is the extraction walk's job below.
    return False


def _search_path_args_raw(search_args: list[str]) -> list[str]:
    """Same walk as ``_search_path_args`` but WITHOUT its ``paths or ["."]`` fallback --
    an empty return means the caller supplied no explicit PATH positional at all
    (``paths_defaulted``), which the fallback-collapsed public helper cannot
    distinguish from an explicit ``.`` (both become ``["."]`` there).
    ``_search_args_paths_defaulted`` below is the only reason this is split out; keep
    both derived from one walk so they can never drift out of sync with each other.

    Task #269 independent-gate BLOCKING-1: `regexp_pattern_seen` is seeded from
    `_search_args_contains_pattern_source_flag`'s pre-pass (a TWO-pass walk) rather than
    starting `False` and flipping mid-walk -- see that function's docstring for why a
    single-pass walk silently misread a PATH positional occurring BEFORE the pattern-source
    flag in argv as the bare pattern instead."""
    paths: list[str] = []
    bare_pattern_seen = False
    regexp_pattern_seen = _search_args_contains_pattern_source_flag(search_args)
    skip_next = False
    parse_options = True
    for index, arg in enumerate(search_args):
        if skip_next:
            skip_next = False
            continue
        if parse_options and arg == "--":
            parse_options = False
            continue
        if parse_options:
            if arg in _SEARCH_PATTERN_SOURCE_FLAGS:
                regexp_pattern_seen = True
                skip_next = index + 1 < len(search_args)
                continue
            if any(arg.startswith(f"{flag}=") for flag in _SEARCH_PATTERN_SOURCE_FLAGS):
                regexp_pattern_seen = True
                continue
            # Bundled/clustered short-flag walk (mirrors `_requires_full_cli`'s bundled scan
            # and `_search_args_request_unrestricted`'s cluster walk, both above in this same
            # module -- cited by NAME rather than a line range on purpose, per the NB-2 lesson
            # from this task's independent gate: a raw line-number citation drifted stale
            # within the SAME commit that added it): scan past leading BOOLEAN short flags
            # (e.g. `-i`, `-n`) until the first ATTACHED-VALUE short flag, which swallows the
            # remainder of the token -- or, if it is the token's LAST character, the NEXT argv
            # token -- as its own value. Shares `_attached_cluster_value_offset` with the
            # pre-pass above rather than re-deriving the same cluster logic a second time.
            offset = _attached_cluster_value_offset(arg)
            if offset is not None:
                ch = arg[offset]
                if ch in ("e", "f"):
                    regexp_pattern_seen = True
                if offset == len(arg) - 1:
                    # The attached-value flag is the LAST character of this token -- its
                    # value is the NEXT argv token, not attached (`-ie needle` / `-im 5`).
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
        if not regexp_pattern_seen and not bare_pattern_seen:
            bare_pattern_seen = True
            continue
        paths.append(arg)
    return paths
