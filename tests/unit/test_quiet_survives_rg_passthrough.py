"""`--quiet` must survive the rg-passthrough route, not be silently dropped.

THE DEFECT. `tg search PAT PATH --quiet` is PASSTHROUGH-ELIGIBLE -- verified by calling the gate
directly, `_can_passthrough_rg(config=SearchConfig(quiet=True), format_type="rg", ...)` returns
True, so `quiet` is not among the flags that refuse the route (unlike `ast`, `ltl`, `force_cpu`,
`rank_bm25`, `semantic_rank`, `gpu_device_ids`). But `RipgrepBackend._build_cmd` never emitted
`-q`, and the module contained ZERO occurrences of "quiet". So on that route the caller's explicit
`--quiet` reached an engine that had never heard of it, and rg printed every match.

This is the silent-downgrade class, not a cosmetic one: the flag the caller passed is dropped, the
engine does something else, and the exit code still says success. tg's `--quiet` and rg's `-q` mean
the same thing (suppress output; exit 0 when a match exists), so the fix is to thread it.

WHY A UNIT TEST ON THE ARGV RATHER THAN AN END-TO-END RUN. Reaching the passthrough route needs rg
resolvable by the backend's own lookup AND the native-delegation gate to decline first. Dogfooding
`--quiet` on a machine where either is false returns EMPTY STDOUT and looks correct -- which is
exactly what happened while investigating this, on a box with rg on PATH: the run took
`NativeCpuBackend` and never touched the code under test. A behavioural probe that cannot reach the
mechanism reports a pass that means nothing. The argv is where the defect lives, so the argv is what
this asserts.
"""

from __future__ import annotations

import pytest

from tensor_grep.backends.ripgrep_backend import RipgrepBackend
from tensor_grep.core.config import SearchConfig


def _passthrough_argv(**config_kwargs: object) -> list[str]:
    """The argv `search_passthrough` actually sends -- the STREAMING consumer.

    Captured at `run_subprocess` rather than built by hand, because the flag under test is appended
    AFTER `_build_cmd` returns and a `_build_cmd`-only probe would not see it. That is not a
    hypothetical: the first version of this file tested `_build_cmd`, which is exactly where the
    flag was wrongly placed -- so the test and the bug agreed with each other.
    """
    from tensor_grep.backends import ripgrep_backend as mod

    captured: list[list[str]] = []

    class _Result:
        returncode = 0
        stdout = ""
        stderr = ""

    monkeypatch = pytest.MonkeyPatch()
    try:
        monkeypatch.setattr(RipgrepBackend, "_get_binary_name", lambda self: "rg")
        monkeypatch.setattr(
            mod, "run_subprocess", lambda cmd, **kw: (captured.append(list(cmd)), _Result())[1]
        )
        RipgrepBackend().search_passthrough("somewhere", "needle", SearchConfig(**config_kwargs))  # type: ignore[arg-type]
    finally:
        monkeypatch.undo()

    assert captured, "search_passthrough never reached run_subprocess; this capture is inert"
    return captured[0]


def _parsing_argv(**config_kwargs: object) -> list[str]:
    """The argv a PARSING consumer builds -- `_build_cmd` directly."""
    backend = RipgrepBackend()
    backend._get_binary_name = lambda: "rg"  # type: ignore[method-assign]
    return backend._build_cmd(
        file_path="somewhere",
        pattern="needle",
        config=SearchConfig(**config_kwargs),  # type: ignore[arg-type]
        json_mode=False,
    )


def test_quiet_reaches_the_rg_argv() -> None:
    """THE FIX. Without `-q`, rg prints every match despite the caller asking for silence."""
    argv = _passthrough_argv(quiet=True)

    assert "-q" in argv or "--quiet" in argv, (
        "config.quiet never reached the ripgrep argv, so the passthrough route prints matches for "
        f"a caller that explicitly asked for none: {argv}"
    )


def test_a_non_quiet_search_does_not_suppress_output() -> None:
    """CONTROL ARM: the flag must be conditional, not unconditional.

    Without this, hardcoding `-q` would satisfy the test above while silencing every ordinary
    search -- turning a dropped flag into a far worse always-on one.
    """
    argv = _passthrough_argv()

    assert "-q" not in argv and "--quiet" not in argv, (
        f"a search with quiet unset must not suppress rg's output: {argv}"
    )


def test_quiet_is_passthrough_eligible_so_this_route_is_reachable() -> None:
    """THE PREMISE, pinned. If `quiet` ever joins the refuse-list, this whole file goes moot.

    A test whose subject has become unreachable still passes, and silently stops meaning anything.
    Asserting the gate keeps that from happening quietly: if a future change makes `--quiet` refuse
    the passthrough route, this fails and tells the reader to retire the file rather than leaving a
    green test guarding a dead path.
    """
    from tensor_grep.cli.main import _can_passthrough_rg

    assert _can_passthrough_rg(
        config=SearchConfig(quiet=True),  # type: ignore[arg-type]
        format_type="rg",
        explicit_rg_format=False,
        json_mode=False,
        ndjson_mode=False,
        files_mode=False,
        files_with_matches=False,
        files_without_match=False,
        only_matching=False,
        stats_mode=False,
    ), (
        "--quiet no longer reaches the rg-passthrough route. If that is deliberate, this file's "
        "premise is gone -- retire it rather than leaving a green test over a dead path."
    )


def test_the_parsing_consumers_never_receive_quiet() -> None:
    """THE ARM THAT WOULD HAVE CAUGHT THE REGRESSION I SHIPPED.

    `-q` makes rg print NOTHING. Three of `_build_cmd`'s four consumers PARSE that output --
    `search` (--json), `_search_files_with_matches` (-l), `_search_counts` (--count) -- so giving
    them `-q` turns a matching file into a reported ZERO. Measured on the real binary:

        rg --count-matches needle f.txt   -> "2"       with -q -> ""
        rg -l needle f.txt                -> "f.txt"   with -q -> ""
        rg --json needle f.txt            -> 5 lines   with -q -> 1

    A false no-match, and an exit-code contract violation (1 where 0 is correct). Suppressing
    OUTPUT and suppressing the ANSWER are not the same thing.

    The earlier version of this file asserted the OPPOSITE -- that `-q` SHOULD appear alongside
    `--count`/`-o`/`-l` -- on the reasoning that "rg accepts it and suppression wins on stdout".
    True of rg, and irrelevant to tg, which consumes that stdout. The test and the bug agreed with
    each other, which is why neither caught it.
    """
    for flag in ("count", "only_matching", "files_with_matches", "json_mode"):
        argv = _parsing_argv(quiet=True, **{flag: True})
        assert "-q" not in argv and "--quiet" not in argv, (
            f"a PARSING consumer received -q alongside {flag}: rg will emit nothing and tg will "
            f"report a false zero. {argv}"
        )


@pytest.mark.parametrize("flag", ["count", "only_matching", "files_with_matches"])
def test_quiet_still_reaches_the_streaming_consumer_alongside_other_flags(flag: str) -> None:
    """CONTROL ARM: moving `-q` off the shared builder must not drop it from the route that needs it.

    Without this, deleting the flag entirely would satisfy the parsing-consumer test above while
    re-introducing the original bug -- a passthrough search printing every match for a caller who
    asked for silence.
    """
    argv = _passthrough_argv(quiet=True, **{flag: True})

    assert "-q" in argv, f"quiet dropped from the streaming route when combined with {flag}: {argv}"
    assert len(argv) >= 4, f"argv collapsed when combining quiet with {flag}: {argv}"
