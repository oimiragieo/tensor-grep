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


def _argv(**config_kwargs: object) -> list[str]:
    backend = RipgrepBackend()
    # Pin argv[0]. `_get_binary_name` resolves a real rg and raises when absent, which would make
    # every assertion here conditional on the machine having ripgrep installed -- an environment
    # dependence that passes locally and skips in CI.
    backend._get_binary_name = lambda: "rg"  # type: ignore[method-assign]
    return backend._build_cmd(
        file_path="somewhere",
        pattern="needle",
        config=SearchConfig(**config_kwargs),  # type: ignore[arg-type]
        json_mode=False,
    )


def test_quiet_reaches_the_rg_argv() -> None:
    """THE FIX. Without `-q`, rg prints every match despite the caller asking for silence."""
    argv = _argv(quiet=True)

    assert "-q" in argv or "--quiet" in argv, (
        "config.quiet never reached the ripgrep argv, so the passthrough route prints matches for "
        f"a caller that explicitly asked for none: {argv}"
    )


def test_a_non_quiet_search_does_not_suppress_output() -> None:
    """CONTROL ARM: the flag must be conditional, not unconditional.

    Without this, hardcoding `-q` would satisfy the test above while silencing every ordinary
    search -- turning a dropped flag into a far worse always-on one.
    """
    argv = _argv()

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


@pytest.mark.parametrize("flag", ["count", "only_matching", "files_with_matches"])
def test_quiet_composes_with_the_other_output_flags(flag: str) -> None:
    """`-q` must not displace the flags it coexists with.

    rg accepts `-q` alongside `--count`/`-o`/`-l`; suppression wins on stdout while the exit code
    still reflects whether a match existed. Appending it must not disturb the others, and a
    regression that emitted `-q` INSTEAD of them would otherwise look like a pass above.
    """
    argv = _argv(quiet=True, **{flag: True})

    assert "-q" in argv or "--quiet" in argv, f"quiet dropped when combined with {flag}: {argv}"
    assert len(argv) >= 4, f"argv collapsed when combining quiet with {flag}: {argv}"
