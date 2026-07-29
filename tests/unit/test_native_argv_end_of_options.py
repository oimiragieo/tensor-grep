"""The native-delegation argv builder must terminate options before user positionals.

CWE-88 (argument injection), the MCP-276 threat class named in `AGENTS.md:1259` — which defines it
with **no CLI-vs-MCP carve-out** and explicitly lists "remaining tg sweep (tracked): the other
native-argv builders". `_build_native_tg_search_command` is one of those builders.

The comment that kept it unfixed was FALSE::

    # The native binary's `search` positionals use clap allow_hyphen_values, so it
    # already accepts dash-leading patterns/paths without an -e/-- shim

Only `-e/--regexp` carries `allow_hyphen_values` (`rust_core/src/main.rs:686`). The `pattern`
(`:690-691`) and `path` (`:693-695`) positionals do not. So a dash-leading pattern is parsed by the
native binary as a FLAG, and the intended path silently slides into pattern position — the search
then runs against a different scope than the caller asked for and reports success. Wrong scope
without an error is the confident-false-answer family this codebase keeps closing.

The two sibling builders already do this correctly: `ripgrep_backend.py:870` and
`mcp_server.py:1387`.

THE SENTINEL IS UNCONDITIONAL, DELIBERATELY. A conditional form ("only emit `--` when the pattern
starts with `-`") looks equivalent and is not: it leaves the ordinary multi-positional case exposed
to path-promotion, which is the half that fails silently rather than loudly.
"""

from __future__ import annotations

from pathlib import Path

from tensor_grep.cli.main import _build_native_tg_search_command
from tensor_grep.core.config import SearchConfig


def _argv(pattern: str, paths: list[str], **kwargs: object) -> list[str]:
    return _build_native_tg_search_command(
        Path("tg.exe"),
        pattern=pattern,
        paths=paths,
        config=SearchConfig(**kwargs),  # type: ignore[arg-type]
        ndjson=False,
    )


def test_a_sentinel_precedes_the_user_positionals() -> None:
    """THE DEFECT: pattern and paths were appended bare."""
    argv = _argv("ERROR", ["."])

    assert "--" in argv, "no end-of-options sentinel; a dash-leading pattern parses as a flag"
    sentinel = argv.index("--")
    assert argv[sentinel + 1 :] == ["ERROR", "."], (
        "the sentinel must be the LAST thing before the user positionals, or options after it "
        f"are themselves treated as positionals: {argv}"
    )


def test_a_dash_leading_pattern_stays_the_pattern() -> None:
    """The injection case. Without the sentinel `-foo` is a flag and `.` becomes the pattern."""
    argv = _argv("-foo", ["src"])

    sentinel = argv.index("--")
    assert argv[sentinel + 1] == "-foo", f"the dash-leading pattern was not protected: {argv}"
    assert argv[sentinel + 2] == "src", f"the path did not stay a path: {argv}"


def test_the_path_is_never_promoted_to_pattern_position() -> None:
    """The SILENT half, and the reason the sentinel is unconditional.

    With a dash-leading pattern eaten as a flag, the first surviving positional is the path — so
    the search runs against a scope the caller never chose and still exits 0. There is no error to
    notice.
    """
    argv = _argv("-i", ["only/this/dir"])
    sentinel = argv.index("--")

    positionals = argv[sentinel + 1 :]
    assert positionals[0] == "-i", "pattern lost its position"
    assert "only/this/dir" in positionals[1:], "path lost its position"
    assert positionals.count("only/this/dir") == 1, f"path duplicated into pattern slot: {argv}"


def test_the_sentinel_is_unconditional() -> None:
    """CONTROL ARM: an ordinary pattern must ALSO get the sentinel.

    Without this a "smart" conditional version — emit `--` only when the pattern looks dangerous —
    passes every test above while leaving the common multi-positional case exposed. That variant is
    forbidden by the design, and this is the assertion that forbids it.
    """
    for pattern in ("ERROR", "plain_word", "some.regex+"):
        argv = _argv(pattern, ["."])
        assert "--" in argv, f"sentinel missing for an ordinary pattern {pattern!r}: {argv}"


def test_flags_still_precede_the_sentinel() -> None:
    """CONTROL ARM: the fix must not push tg's own flags past the sentinel.

    A sentinel emitted too early turns every subsequent tg flag into a positional — the search
    would then treat `--json` as a path. This is the failure mode of "just append -- first".
    """
    argv = _argv("ERROR", ["."], json_mode=True)
    sentinel = argv.index("--")

    assert "--json" in argv[:sentinel], f"a tg flag landed after the sentinel: {argv}"
    assert argv[sentinel + 1 :] == ["ERROR", "."]


def test_the_false_allow_hyphen_values_comment_is_gone() -> None:
    """The comment actively argued against the fix, so it must not survive it.

    A wrong comment is worse than no comment: it is the reason this builder was skipped in the
    original CWE-88 sweep. Pinned so a future edit cannot restore the claim.

    Checks the CLAIM, not the token. The replacement comment necessarily NAMES
    ``allow_hyphen_values`` in order to explain that the old claim was false, so a bare substring
    check fires on the correction itself -- the quoting-vs-asserting trap, which has now bitten
    four separate guards in this campaign. Match the assertion instead.
    """
    import inspect
    import re

    source = inspect.getsource(_build_native_tg_search_command)

    # The false assertion, in any wrapping: positionals HAVE the attribute.
    asserted = re.search(
        r"positionals\s+(?:\S+\s+){0,3}(?:use|carry|have)\s+clap\s+`?allow_hyphen_values",
        source,
        re.IGNORECASE,
    )
    assert asserted is None, (
        "the false claim that the native positionals carry clap allow_hyphen_values is back; "
        f"only -e/--regexp does (rust_core/src/main.rs:686). Matched: {asserted!r}"
    )

    # PREMISE: the correction is still present. Without this, deleting the whole comment would
    # satisfy the assertion above while removing the reason the sentinel exists.
    assert "CWE-88" in source, "the sentinel lost its rationale comment"
