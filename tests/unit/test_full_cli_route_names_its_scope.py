"""The full-CLI search route must name a defaulted scope on a zero-result run — and only then.

#857 closed this for the rg-passthrough route (a bare `tg search PAT`). A `_requires_full_cli` flag
(`--ast`, `--rank`, `--semantic`, `--stats`) bypasses that passthrough and lands in the Python CLI's
`is_empty` branch, which was still silent.

Reachability was TRACED before a line was written::

    tg search NO_MATCH_ZZZ --ast --lang python   ->   EXIT 1 at main.py's is_empty branch

An earlier attempt at this fix went into a branch the invocation never takes, produced no
observable effect, and had to be reverted.

THESE TESTS ARE BEHAVIOURAL, DELIBERATELY. The first cut asserted on `inspect.getsource(...)`, and
an external audit found the flaw: three of its four "control arms" still PASSED with the fix
reverted, because they tested pre-existing helpers (`_requires_full_cli`,
`_search_args_include_explicit_path`) rather than the new behaviour. A control arm that survives the
revert is not a control arm. Every test below runs the real CLI in a subprocess and asserts on
stderr + exit code.

THE THREE GATES, each earned by a defect the audit found in the first cut:
* `paths_defaulted` — necessary but NOT sufficient; it means only "no positional PATH".
* not scope-filtered — `--glob`/`--iglob`/`--type`/`--max-depth` ARE a chosen scope, so the note
  would be a false positive claiming the search covered the whole current directory.
* not `quiet` — `--quiet` promises no incidental output; emitting a note there is a silent contract
  change on a flag whose entire purpose is silence.
"""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

import pytest

_REPO = Path(__file__).resolve().parents[2]
_NOTE = "no PATH was given"


@pytest.fixture(scope="module")
def corpus(tmp_path_factory: pytest.TempPathFactory) -> Path:
    """A small tree with one findable symbol, so 'no match' is a real answer, not an empty scan."""
    root = tmp_path_factory.mktemp("fullcli")
    (root / "a.py").write_text("def findable_marker():\n    return 1\n", encoding="utf-8")
    (root / "b.py").write_text("x = findable_marker()\n", encoding="utf-8")
    return root


def _run(corpus: Path, *args: str) -> subprocess.CompletedProcess[str]:
    env = dict(os.environ)
    env["PYTHONPATH"] = str(_REPO / "src")
    env.setdefault("TG_RG_TIMEOUT_SECONDS", "60")
    return subprocess.run(
        [sys.executable, "-m", "tensor_grep.cli.bootstrap", "search", *args],
        cwd=corpus,
        env=env,
        capture_output=True,
        text=True,
        timeout=180,
    )


@pytest.mark.parametrize(
    "flag",
    [
        "--ast",
        "--rank",
        "--semantic",
        pytest.param(
            "--stats",
            marks=pytest.mark.xfail(
                sys.platform == "win32",
                strict=True,
                reason=(
                    "PLATFORM-DIVERGENT, and the strict xfail is what proved it. On Windows "
                    "--stats emits its own stats block on stdout and returns WITHOUT reaching the "
                    "is_empty branch, so the scope note never fires. On Linux CI the same "
                    "invocation DOES reach it and the note fires -- the marker XPASSed there. "
                    "My original 'TRACED, not assumed' note was traced on Windows only and then "
                    "generalised to every platform, which is the exact over-generalisation this "
                    "file's other tests exist to catch. Kept strict so the day Windows starts "
                    "routing --stats through is_empty this converts to a hard failure rather than "
                    "passing quietly. The divergence itself is an OPEN finding in docs/BACKLOG.md."
                ),
            ),
        ),
    ],
)
def test_a_defaulted_zero_result_names_its_scope(corpus: Path, flag: str) -> None:
    """THE DEFECT: exit 1 with nothing on either stream, on all four full-CLI routes.

    Parametrized over all four because the plan's first draft named only `--ast` — covering one
    would have left three siblings silent while the item read as closed.
    """
    result = _run(corpus, "NO_MATCH_ZZZ", flag, *(["--lang", "python"] if flag == "--ast" else []))

    assert _NOTE in result.stderr, (
        f"{flag}: a zero-result search with no PATH said nothing. A caller cannot tell 'absent "
        f"from the repository' from 'absent from the directory I was in'. stderr={result.stderr!r}"
    )
    assert result.returncode == 1, (
        f"{flag}: expected exit 1 (complete, no match); got {result.returncode}. Exit 2 is the "
        "INCOMPLETE contract and a defaulted-scope search that ran to completion is complete."
    )


def test_a_matching_search_stays_silent(corpus: Path) -> None:
    """CONTROL ARM: with matches, no note and exit 0.

    Without this, printing the note unconditionally passes every test above while training callers
    to ignore it.
    """
    result = _run(corpus, "findable_marker", "--ast", "--lang", "python")

    assert _NOTE not in result.stderr, f"note fired on a successful search: {result.stderr!r}"
    assert result.returncode == 0


def test_an_explicitly_scoped_search_stays_silent(corpus: Path) -> None:
    """CONTROL ARM: the caller chose the scope, so there is nothing to disclose."""
    result = _run(corpus, "NO_MATCH_ZZZ", ".", "--ast", "--lang", "python")

    assert _NOTE not in result.stderr, f"note fired on an explicit PATH: {result.stderr!r}"
    assert result.returncode == 1


@pytest.mark.parametrize("scope_flag", [["--glob", "*.py"], ["--max-depth", "1"]])
def test_a_filter_scoped_search_stays_silent(corpus: Path, scope_flag: list[str]) -> None:
    """AUDIT FINDING (MEDIUM): `paths_defaulted` is not "the caller chose no scope".

    `--glob`/`--iglob`/`--type`/`--max-depth` ARE a chosen scope. The first cut printed
    "no PATH was given, so the search defaulted to the current directory" for these — a false
    positive that misdescribes what actually ran.
    """
    result = _run(corpus, "NO_MATCH_ZZZ", "--ast", "--lang", "python", *scope_flag)

    assert _NOTE not in result.stderr, (
        f"note fired on a filter-scoped search ({scope_flag}); the caller DID bound the scope: "
        f"{result.stderr!r}"
    )


def test_quiet_suppresses_the_note(corpus: Path) -> None:
    """AUDIT FINDING (LOW): `--quiet` promises no incidental output.

    The first cut emitted the note before the `quiet` branch, so a `--quiet` zero-result search
    started writing stderr where it had been silent — an unmentioned contract change on the one
    flag whose entire purpose is silence.
    """
    result = _run(corpus, "NO_MATCH_ZZZ", "--ast", "--lang", "python", "--quiet")

    assert _NOTE not in result.stderr, f"--quiet emitted an informational note: {result.stderr!r}"
