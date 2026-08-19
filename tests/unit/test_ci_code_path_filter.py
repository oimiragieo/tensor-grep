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


# --------------------------------------------------------------------------
# The docs filter and the job it gates.
#
# A docs-only PR correctly skips the expensive lanes -- but the doc-governance tests
# lived in test-python and skipped with them, so the checks that catch doc/product
# drift were not running on the changes most likely to cause it. `docs-governance`
# closes that; these tests keep it closed.
# --------------------------------------------------------------------------

_DOC_FILTER_LINE = re.compile(r"DOC_FILES=\$\(git diff --name-only[^)]*")

#: Paths whose change can invalidate a doc claim or dangle a skill citation.
REQUIRED_DOC_PATHS = ("docs", ".claude/skills", "mkdocs.yml", "README.md", "AGENTS.md")


def _doc_filter_line() -> str:
    source = CI_WORKFLOW.read_text(encoding="utf-8")
    match = _DOC_FILTER_LINE.search(source)
    assert match is not None, (
        "could not find the `changes` job's DOC_FILES git-diff line in ci.yml. If the "
        "docs gate was restructured, update this test -- do NOT delete it."
    )
    return match.group(0)


@pytest.mark.parametrize("path", REQUIRED_DOC_PATHS)
def test_doc_filter_watches_path(path: str) -> None:
    line = _doc_filter_line()
    assert re.search(rf"(?<![\w/.]){re.escape(path)}(?![\w/.])", line), (
        f"the docs filter does not watch {path!r}. A PR touching only that path would "
        "skip the doc-governance suite, which is exactly the suite that catches a doc "
        f"drifting from the product.\nfilter line: {line}"
    )


def test_docs_governance_job_exists_and_is_gated_on_both_signals() -> None:
    """It must run on docs changes AND on code changes.

    Code-only is not sufficient (a docs-only PR would skip it), and docs-only is not
    sufficient either -- the 2026-08-19 audit's actual finding ran the other way: the
    PRODUCT moved to 10 parser-backed languages and three docs kept claiming 6+4. A
    code change can invalidate a doc's claim without touching a single doc file.
    """
    import yaml

    workflow = yaml.safe_load(CI_WORKFLOW.read_text(encoding="utf-8"))
    jobs = workflow["jobs"]
    assert "docs-governance" in jobs, "the docs-governance job was removed"

    job = jobs["docs-governance"]
    assert "changes" in job["needs"], "docs-governance must consume the changes outputs"

    condition = " ".join(str(job["if"]).split())
    assert "outputs.docs" in condition, "must run when docs changed"
    assert "outputs.code" in condition, (
        "must ALSO run when code changed -- a code change can invalidate a doc claim, "
        "which is the direction the audit actually found broken"
    )

    assert "docs" in jobs["changes"]["outputs"], "the changes job must expose a docs output"


def test_static_analysis_also_runs_on_docs_changes() -> None:
    """`ruff format` formats Python inside markdown fences, so docs can break it.

    Gating Formatting & Linting on `code` alone let a docs-only PR merge a fence whose
    aligned trailing comments failed `ruff format --check --preview .` (PR #1023). The
    break then surfaced on the next unrelated CODE PR, attributing it to the wrong
    author and blocking work that had nothing to do with it.
    """
    import yaml

    workflow = yaml.safe_load(CI_WORKFLOW.read_text(encoding="utf-8"))
    condition = " ".join(str(workflow["jobs"]["static-analysis"]["if"]).split())
    assert "outputs.docs" in condition, (
        "Formatting & Linting must run on docs changes -- ruff formats Python blocks "
        "inside markdown, so a docs-only PR can red the formatter gate with nothing "
        "watching"
    )
    assert "outputs.code" in condition, "must still run on code changes"


def test_docs_governance_runs_the_suites_that_catch_drift() -> None:
    """Pin the suite list. A silently-shrunk list is how this gate would decay."""
    source = CI_WORKFLOW.read_text(encoding="utf-8")
    for suite in (
        "test_public_docs_governance.py",
        "test_enterprise_docs_governance.py",
        "test_skill_library_drift.py",
        "test_skill_index_sync.py",
    ):
        assert suite in source, f"docs-governance no longer runs {suite}"
