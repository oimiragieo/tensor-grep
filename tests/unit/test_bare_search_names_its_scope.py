"""A zero-result search the caller never scoped must name the scope it actually covered.

Reported by the live dogfood on THREE consecutive releases (1.101.7, 1.101.9, 1.101.17):

    bare `tg search P` and `tg search P --json` -- ~2s, exit 1, empty, no refuse stderr.
    Always pass explicit PATH.

The reason three rounds of disclosure work never touched it: **a bare search never reaches the
Python CLI at all.** `bootstrap.main_entry` dispatches it straight to ripgrep via
`_run_rg_passthrough` and re-raises rg's exit code, so every disclosure surface in `cli/main.py`
sits downstream of a branch this path does not take. A fix written there had no observable effect,
which is what finally located the real dispatch.

Exit 1 is KEPT. Exit 2 means INCOMPLETE, and a defaulted-scope search that ran to completion is
complete -- it just answered a narrower question than the caller may have meant. Refusing every
bare `tg search foo` would break the ordinary invocation, which works correctly.
"""

from __future__ import annotations

import pytest

from tensor_grep.cli.bootstrap import (
    _search_args_include_explicit_path,
    _write_defaulted_scope_note,
)


@pytest.mark.parametrize(
    ("args", "expected"),
    [
        (["PAT"], False),
        (["PAT", "."], True),
        (["PAT", "src/"], True),
        (["--json", "PAT"], False),
        (["--json", "PAT", "src"], True),
        # A flag VALUE is not a path. Without the value-flag skip, `-g *.py` would read as a
        # second positional and silently suppress the note on a genuinely unscoped search.
        (["-g", "*.py", "PAT"], False),
        (["-g", "*.py", "PAT", "src"], True),
        # `-e PAT` supplies the pattern via a flag, so there is still no bare positional path.
        (["-e", "PAT"], False),
    ],
)
def test_explicit_path_detection(args: list[str], expected: bool) -> None:
    """The pattern is the first bare positional; a path is any bare positional after it."""
    assert _search_args_include_explicit_path(args) is expected


def test_the_note_names_the_scope_and_the_remedy(capsys: pytest.CaptureFixture[str]) -> None:
    """THE DEFECT: exit 1 with zero bytes on both streams.

    'Nothing matches in the repository' and 'nothing matches in whatever directory I was standing
    in' were the same bytes on the wire.
    """
    _write_defaulted_scope_note()
    err = capsys.readouterr().err

    assert err, "the note produced no output at all"
    assert "no PATH was given" in err, "the note does not say the scope was defaulted"
    assert "current directory" in err, "the note does not name the scope actually searched"
    assert "tg search <pattern> <dir>" in err, "the note does not give the remedy"


def test_the_note_goes_to_stderr_not_stdout(capsys: pytest.CaptureFixture[str]) -> None:
    """CONTROL ARM: stdout is the machine surface.

    Without this, writing the note to stdout would satisfy the test above while corrupting
    `--json` output and any downstream parser -- the exact harm this whole surface exists to
    prevent.
    """
    _write_defaulted_scope_note()
    captured = capsys.readouterr()

    assert captured.out == "", "the note leaked onto stdout, where a --json consumer parses"
    assert captured.err != ""


def test_the_dispatch_site_gates_on_both_conditions() -> None:
    """PREMISE, pinned by source: the note fires only on exit 1 AND no explicit path.

    Pinned structurally because the call sits in `main_entry`'s dispatch, after a real subprocess
    -- there is no way to exercise it in-process without running ripgrep. The two guards are what
    keep it from becoming noise: without the exit-code check every successful search would print
    it, and without the path check every scoped search would.
    """
    import inspect

    from tensor_grep.cli import bootstrap

    source = inspect.getsource(bootstrap.main_entry)

    assert "_write_defaulted_scope_note()" in source, (
        "main_entry no longer emits the defaulted-scope note; the bare-search case is silent again"
    )
    assert "exit_code == 1" in source, (
        "the note is not gated on a no-match exit -- a successful search would print it too"
    )
    assert "_search_args_include_explicit_path" in source, (
        "the note is not gated on the path being defaulted -- a scoped search would print it too"
    )
