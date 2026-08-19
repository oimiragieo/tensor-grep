"""The cost-smart CI filter must cover every directory whose code CI gates depend on.

`ci.yml`'s `changes` job decides whether the expensive lanes (test-python,
test-rust-core, Formatting & Linting, native-build-smoke, ...) run at all, by
diffing the PR against a fixed list of paths. If nothing in that list changed, it
emits `code=false` and every one of those lanes SKIPS.

The list omitted `scripts/` and `benchmarks/`.

That is not a cost optimisation, it is a hole. `scripts/` contains:

  * `validate_release_assets.py` -- gates the release
  * `check_repo_hygiene.py`      -- run by the repo-hygiene job
  * `file_size_budget.py`        -- the file-size ratchet, itself a CI gate
  * `stamp_release_assets.py`    -- release stamping

and `tests/unit/test_release_assets_validation.py` (5,258 lines) exists purely to
test the first of those. Under the old filter that suite ran when the TESTS
changed but not when their SUBJECT did -- an inverted gate.

Measured 2026-08-19 on PR #1021: a 3,500-line refactor of
`validate_release_assets.py` produced a fully green PR in which `test-python`,
`test-rust-core` and `Formatting & Linting` all had `conclusion: "skipped"`. The
green was vacuous for precisely the change that most needed testing, and it looked
identical to a real pass.

This test pins the path list so the hole cannot silently reopen.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
CI_WORKFLOW = REPO_ROOT / ".github" / "workflows" / "ci.yml"

#: Every path the filter must watch. A change under any of these can break a CI
#: gate, a release gate, or the product, so none may be silently excluded.
REQUIRED_PATHS = (
    "src",
    "rust_core",
    "tests",
    "scripts",
    "benchmarks",
    ".github/workflows",
    "pyproject.toml",
)

_FILTER_LINE = re.compile(r"CODE_FILES=\$\(git diff --name-only[^)]*")


def _filter_line() -> str:
    source = CI_WORKFLOW.read_text(encoding="utf-8")
    match = _FILTER_LINE.search(source)
    assert match is not None, (
        "could not find the `changes` job's CODE_FILES git-diff line in ci.yml. "
        "If the cost-smart gate was restructured, update this test to match -- do "
        "NOT delete it, or the skip hole reopens unnoticed."
    )
    return match.group(0)


def test_positive_control_the_filter_line_is_found_and_nonempty() -> None:
    """A regex that stopped matching would make every assertion below vacuous."""
    line = _filter_line()
    assert "git diff --name-only" in line
    assert len(line) > 60, f"suspiciously short filter line: {line!r}"


@pytest.mark.parametrize("path", REQUIRED_PATHS)
def test_filter_watches_path(path: str) -> None:
    """Each watched path, asserted individually so a failure names the missing one."""
    line = _filter_line()
    assert re.search(rf"(?<![\w/.]){re.escape(path)}(?![\w/.])", line), (
        f"the cost-smart CI filter does not watch {path!r}. A PR touching only that "
        "directory will skip test-python, test-rust-core and Formatting & Linting, "
        "and report green having tested nothing.\n"
        f"filter line: {line}"
    )


def test_detector_rejects_a_filter_missing_a_path() -> None:
    """Mutation control: prove the matcher can fail.

    Without this, a regex that matched everything would keep the suite green while
    the filter silently lost a directory.
    """
    truncated = "CODE_FILES=$(git diff --name-only base...HEAD -- src rust_core tests"
    assert re.search(r"(?<![\w/.])src(?![\w/.])", truncated)
    assert not re.search(r"(?<![\w/.])scripts(?![\w/.])", truncated), (
        "the matcher claims 'scripts' is present in a line that lacks it -- it would "
        "never catch a real omission"
    )


def test_detector_does_not_confuse_a_substring() -> None:
    """`scripts` must not be satisfied by `.github/workflows/scripts-foo`."""
    decoy = "CODE_FILES=$(git diff --name-only base...HEAD -- src scriptsfoo tests"
    assert not re.search(r"(?<![\w/.])scripts(?![\w/.])", decoy)
