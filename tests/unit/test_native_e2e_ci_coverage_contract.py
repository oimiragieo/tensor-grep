"""Invariant: every native-binary-dependent e2e suite is actually RUN by CI (task #275).

Background — the defect this models away, from task #266's independent gate:

`tests/e2e/test_native_json_byte_fidelity.py` was written specifically to prove the #266
emitter fix. Its own header named CI as the oracle that would confirm it. It SKIPPED in
every CI job, because the one job that builds the native binary (`native-build-smoke`)
invoked pytest on a single HARDCODED filename, and the new file was not that filename.
A green suite reported proof that never executed. **SKIPPED IS NOT PASSED.**

#746 fixed the instance by changing that invocation to a glob. This test fixes the CLASS.

The glob keys on a NAMING CONVENTION (`test_native_*.py`), not on the semantic property it
stands for ("this suite needs the real compiled `tg` binary"). It is exact today. It silently
re-opens the identical hole the moment someone adds a binary-dependent suite named anything
else -- `test_json_bytes_e2e.py`, `test_emitter_parity.py`. That is the same enumeration-rots
shape as the hardcoded filename it replaced, one level up.

So rather than enumerate today's files, assert the PROPERTY: every test file that needs the
native binary must be matched by whatever pattern CI actually runs. The marker for "needs the
native binary" is `TG_REQUIRE_RG_PARITY` -- the env var `native-build-smoke` sets to turn a
missing binary from a silent skip into a hard failure. A suite that references it is, by
construction, one that must run in the job that provides that binary.

This is the #745 lesson applied: when the same class of gap keeps recurring, model the class
instead of enumerating its instances.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest
import yaml

REPO_ROOT = Path(__file__).resolve().parents[2]
CI_WORKFLOW = REPO_ROOT / ".github" / "workflows" / "ci.yml"
NATIVE_BINARY_MARKER = "TG_REQUIRE_RG_PARITY"
NATIVE_BUILD_JOB = "native-build-smoke"


def _pytest_target_patterns() -> list[str]:
    """The path arguments CI actually hands to pytest in the native-binary job.

    Parsed from the workflow rather than hardcoded here -- a copy of the pattern in this
    file would rot in exactly the way the test exists to prevent.
    """
    workflow = yaml.safe_load(CI_WORKFLOW.read_text(encoding="utf-8"))
    job = workflow["jobs"][NATIVE_BUILD_JOB]

    patterns: list[str] = []
    for step in job["steps"]:
        run = str(step.get("run", ""))
        if "pytest" not in run:
            continue
        # Only the step that sets the marker env is the binary-dependent one; a future
        # unrelated pytest step in this job must not silently satisfy the invariant.
        if NATIVE_BINARY_MARKER not in str(step.get("env", {})):
            continue
        for line in run.splitlines():
            if "pytest" not in line:
                continue
            for token in line.split():
                if token.startswith("tests/") and token.endswith(".py"):
                    patterns.append(token)
    return patterns


def _suites_requiring_the_native_binary() -> set[Path]:
    """Test files that reference the marker, i.e. that need the real compiled binary.

    THIS FILE is excluded by path. It names the marker as DATA -- it is the contract test
    about the marker, not a consumer of it, and it needs no binary. Caught on this test's
    very first run, when it flagged itself as uncovered. The exclusion is by `__file__`
    rather than a name pattern so it cannot drift if the file is renamed, and it is the only
    exemption: any OTHER file naming the marker really is claiming to need the binary.
    """
    self_path = Path(__file__).resolve()
    found: set[Path] = set()
    for path in (REPO_ROOT / "tests").rglob("test_*.py"):
        if path.resolve() == self_path:
            continue
        if NATIVE_BINARY_MARKER in path.read_text(encoding="utf-8", errors="replace"):
            found.add(path.relative_to(REPO_ROOT))
    return found


def test_ci_runs_a_pattern_not_a_hardcoded_filename() -> None:
    """Guards the #746 fix itself: a bare filename silently drops the next suite added."""
    patterns = _pytest_target_patterns()

    assert patterns, (
        f"No pytest invocation with {NATIVE_BINARY_MARKER} found in the "
        f"'{NATIVE_BUILD_JOB}' job of {CI_WORKFLOW.name}. Either the job was renamed or the "
        "binary-dependent pytest step was removed -- both silently un-cover every suite below."
    )
    assert any("*" in pattern for pattern in patterns), (
        f"CI runs literal filenames {patterns} instead of a pattern. That is the exact defect "
        "task #275 closes: it covers today's files and silently skips the next one added. "
        "Use a glob (or a pytest marker) so new suites are picked up automatically."
    )


def test_every_native_binary_dependent_suite_is_covered_by_ci() -> None:
    """THE INVARIANT. A suite needing the binary but unmatched by CI's pattern is inert."""
    required = _suites_requiring_the_native_binary()
    assert required, (
        f"No test file references {NATIVE_BINARY_MARKER}. Either the marker was renamed "
        "(update this test with it) or the native-parity suites were deleted."
    )

    covered: set[Path] = set()
    for pattern in _pytest_target_patterns():
        covered.update(p.relative_to(REPO_ROOT) for p in REPO_ROOT.glob(pattern))

    uncovered = sorted(str(p).replace("\\", "/") for p in required - covered)
    assert not uncovered, (
        "These suites need the real native binary but CI's pytest pattern does not match "
        f"them, so they SKIP silently and prove nothing: {uncovered}. Either rename them to "
        f"match the pattern in {CI_WORKFLOW.name}'s '{NATIVE_BUILD_JOB}' job, or widen that "
        "pattern. A suite that cannot run is not coverage."
    )


@pytest.mark.parametrize(
    ("patterns", "required", "expected_uncovered"),
    [
        (["tests/e2e/test_native_*.py"], {"tests/e2e/test_native_a.py"}, []),
        (
            ["tests/e2e/test_native_*.py"],
            {"tests/e2e/test_other_shape.py"},
            ["tests/e2e/test_other_shape.py"],
        ),
        (
            ["tests/e2e/test_native_plain.py"],
            {"tests/e2e/test_native_new.py"},
            ["tests/e2e/test_native_new.py"],
        ),
    ],
    ids=["glob-covers", "glob-misses-differently-named", "hardcoded-filename-misses-sibling"],
)
def test_the_coverage_comparison_itself_discriminates(
    patterns: list[str], required: set[str], expected_uncovered: list[str]
) -> None:
    """Proves the set-difference above can FAIL, using synthetic inputs.

    Without this, `test_every_native_binary_dependent_suite_is_covered_by_ci` passing tells
    you nothing -- a comparison that always yields the empty set would pass identically. The
    middle case is the real regression scenario: a binary-dependent suite whose name does not
    match the naming convention the glob encodes.
    """
    covered = {r for r in required if any(re.fullmatch(p.replace("*", ".*"), r) for p in patterns)}
    assert sorted(required - covered) == expected_uncovered
