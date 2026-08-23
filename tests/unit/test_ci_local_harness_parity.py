"""Pin `scripts/ci-local/` against `.github/workflows/ci.yml`.

WHY THIS EXISTS
---------------
`scripts/ci-local/` is a SECOND definition of what CI runs. A second definition drifts -- that is
what second definitions do -- and a drifted local harness is worse than none, because it reports
GREEN for a command CI no longer runs. Four of five council seats reviewing the harness asked for
exactly this gate before it landed.

WHAT IT ASSERTS
---------------
Every value the harness MIRRORS from ci.yml appears, verbatim, in both files. It does not check
that the harness is a faithful reproduction of CI (it is deliberately not: ubuntu-only, no
static-analysis lane, a python-lane ast-grep superset -- all documented in the files themselves).
It checks the narrow, mechanical thing a test can check: if someone edits the cargo invocation, the
pytest invocation, the pinned uv version, or the symlink-tests env var in ci.yml, a test that NAMES
this harness goes red, so the harness cannot silently fall behind.

WHAT IT DOES NOT CLAIM
----------------------
Passing does NOT mean the harness matches CI. It means the mirrored STRINGS still agree. The
harness's own banner enumerates the jobs it does not run; that list is prose and is not pinned
here, because a list of absences cannot be verified from the absences themselves.
"""

from __future__ import annotations

from pathlib import Path

import pytest

_REPO_ROOT = Path(__file__).resolve().parents[2]
_CI_YML = _REPO_ROOT / ".github" / "workflows" / "ci.yml"
_HARNESS_DIR = _REPO_ROOT / "scripts" / "ci-local"
_DOCKERFILE = _HARNESS_DIR / "Dockerfile"
_ENTRYPOINT = _HARNESS_DIR / "entrypoint.sh"
_RUN_SH = _HARNESS_DIR / "run.sh"

#: (label, string that must appear in ci.yml, file in scripts/ci-local that must also contain it)
MIRRORED_VALUES = (
    ("cargo test invocation", "cargo test --verbose --no-default-features", _ENTRYPOINT),
    ("pytest invocation", 'pytest tests -v --tb=short -m "not eval"', _ENTRYPOINT),
    ("pinned uv version", "uv==0.11.25", _DOCKERFILE),
    ("symlink-tests env var", "TG_REQUIRE_SYMLINK_TESTS", _DOCKERFILE),
    ("editable install extras", '-e ".[dev,ast]"', _ENTRYPOINT),
)


def _read(path: Path) -> str:
    assert path.is_file(), f"missing file: {path}"
    return path.read_text(encoding="utf-8")


def test_positive_control_both_sources_are_present_and_nonempty() -> None:
    """Without this, every assertion below would pass vacuously on an empty/missing file."""
    ci = _read(_CI_YML)
    assert len(ci) > 10_000, f"ci.yml is implausibly small ({len(ci)} bytes) -- did it move?"
    for path in (_DOCKERFILE, _ENTRYPOINT, _RUN_SH):
        body = _read(path)
        assert len(body) > 500, f"{path.name} is implausibly small ({len(body)} bytes)"


@pytest.mark.parametrize(
    ("label", "needle", "harness_file"),
    MIRRORED_VALUES,
    ids=[label for label, _, _ in MIRRORED_VALUES],
)
def test_mirrored_value_agrees_between_ci_and_harness(
    label: str, needle: str, harness_file: Path
) -> None:
    ci = _read(_CI_YML)
    harness = _read(harness_file)

    assert needle in ci, (
        f"{label}: {needle!r} is no longer in ci.yml. Either CI changed and "
        f"scripts/ci-local/{harness_file.name} must be updated to match, or this pin is stale. "
        "Do NOT delete the pin to make this pass -- update BOTH sides."
    )
    assert needle in harness, (
        f"{label}: ci.yml uses {needle!r} but scripts/ci-local/{harness_file.name} does not. "
        "The local harness has drifted from CI and would report GREEN for a command CI does not "
        "run."
    )


def test_detector_rejects_a_missing_needle() -> None:
    """The matcher must be able to FAIL -- otherwise the parametrised test proves nothing."""
    assert "cargo test --verbose --no-default-features" not in "cargo test", (
        "substring matcher claims a needle is present in text that lacks it"
    )


def test_harness_declares_it_is_not_a_ci_substitute() -> None:
    """The banner must keep saying the GitHub run is the arbiter.

    A local harness that stops saying this is one refactor away from being treated as a merge
    gate, which it is not: it runs one OS, one python version, and no static-analysis lane.
    """
    entrypoint = _read(_ENTRYPOINT)
    assert "remains the merge arbiter" in entrypoint, (
        "entrypoint.sh no longer states that the GitHub Actions run is the merge arbiter"
    )
    assert "DID NOT EXECUTE" in entrypoint, (
        "entrypoint.sh no longer enumerates the CI jobs it does not run"
    )


def test_run_sh_keeps_a_cpu_cap() -> None:
    """The CPU cap is what makes running these lanes acceptable on a SHARED box at all.

    AGENTS.md/CLAUDE.md ban local cargo because it saturates the machine. The cap does not bound
    I/O or memory bandwidth (documented in run.sh), but removing it entirely would put the harness
    straight back in breach of the ban's purpose.
    """
    run_sh = _read(_RUN_SH)
    assert "--cpus=" in run_sh, "run.sh no longer passes a --cpus cap to docker run"
    assert "TG_CI_CPUS" in run_sh, "run.sh no longer exposes the TG_CI_CPUS override"
