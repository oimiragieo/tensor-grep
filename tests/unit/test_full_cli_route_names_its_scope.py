"""The full-CLI search route must also name a defaulted scope on a zero-result run.

#857 closed this for the rg-passthrough route, which is what a bare `tg search PAT` takes. But an
invocation carrying a `_requires_full_cli` flag -- `--ast`, `--rank`, `--semantic`, `--stats` --
bypasses `bootstrap.main_entry`'s passthrough entirely and lands in the Python CLI's `is_empty`
branch, which was still silent.

Reachability was TRACED, not assumed, before this test was written::

    tg search NO_MATCH_ZZZ --ast --lang python   ->   EXIT 1 at main.py:8443

That is the `is_empty` branch. The trace matters: an earlier attempt at this fix was written into a
branch the invocation never takes, produced no observable effect, and had to be reverted. Confirm
the exit line before editing a search surface.

Exit stays 1. A defaulted-scope search that ran to completion IS complete -- it answered a narrower
question than the caller may have meant. Exit 2 means INCOMPLETE and is reserved for
`partial`/`result_incomplete`, which the same line already handles.
"""

from __future__ import annotations

import pytest

# DEFERRED import, deliberately. A module-level `from tensor_grep.cli import main` perturbs the
# CLI's lazy-import state and poisons four `--help` tests in test_cli_modes.py -- they pass alone
# and fail once this file has been collected. Same trap, same fix, second occurrence this campaign.


def _cli_main():
    from tensor_grep.cli import main as cli_main

    return cli_main


def test_the_full_cli_route_has_a_defaulted_scope_note() -> None:
    """THE DEFECT: the is_empty branch exited 1 with nothing on either stream.

    Pinned by source rather than behaviour: reaching this branch needs a real repo scan through a
    `_requires_full_cli` flag, and a test that shells out would be slow and platform-fragile. The
    reachability claim itself is verified by the trace recorded in the module docstring.
    """
    import inspect

    source = inspect.getsource(_cli_main().search_command)
    marker = "if all_results.is_empty:"
    assert marker in source, "the is_empty branch moved; re-trace before updating this guard"

    branch = source.split(marker, 1)[1].split("if quiet:", 1)[0]

    assert "_write_defaulted_scope_note" in branch or "_defaulted_scope_note" in branch, (
        "the full-CLI zero-result branch does not name the defaulted scope. A --ast/--rank/"
        "--semantic/--stats search with no PATH exits 1 with nothing on either stream, so a "
        "caller cannot tell 'absent from the repository' from 'absent from the directory I "
        "happened to be in'."
    )
    assert "paths_defaulted" in branch, (
        "the note is not gated on paths_defaulted -- an explicitly scoped search would print it "
        "too, and a note that fires when the caller DID choose the scope is noise"
    )


def test_the_branch_still_exits_1_not_2() -> None:
    """CONTROL ARM: the fix must not promote a COMPLETE result to exit 2.

    Exit 2 is the incompleteness contract. Without this, 'make the zero louder' slides into
    'make the zero an error', which breaks every consumer branching on 1-vs-2 and contradicts
    AGENTS.md's closed exit-code contract.
    """
    import inspect

    source = inspect.getsource(_cli_main().search_command)
    branch = source.split("if all_results.is_empty:", 1)[1].split("if quiet:", 1)[0]

    assert "sys.exit(2 if all_results.result_incomplete else 1)" in branch, (
        "the is_empty exit no longer keys exit 2 exclusively on result_incomplete"
    )


@pytest.mark.parametrize("flag", ["--ast", "--rank", "--semantic", "--stats"])
def test_every_full_cli_flag_is_covered(flag: str) -> None:
    """All four `_requires_full_cli` flags take this route, not just --ast.

    The plan's first draft named only `--ast`. Covering one flag would have left three siblings
    silent while the item read as closed.
    """
    from tensor_grep.cli.bootstrap import _requires_full_cli

    assert _requires_full_cli([flag, "PATTERN"]), (
        f"{flag} no longer routes to the full CLI; this test's premise is stale and the fix may "
        "be guarding a route this flag never takes"
    )


def test_a_scoped_full_cli_search_is_not_covered_by_the_note() -> None:
    """CONTROL ARM on the predicate itself: an explicit PATH must not be treated as defaulted.

    Without this, a fix that hard-codes the note into the branch (ignoring `paths_defaulted`)
    passes the first test while printing on every scoped zero-result search.
    """
    from tensor_grep.cli.bootstrap import _search_args_include_explicit_path

    assert _search_args_include_explicit_path(["--ast", "PATTERN", "src/"]) is True
    assert _search_args_include_explicit_path(["--ast", "PATTERN"]) is False
