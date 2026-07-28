"""`mcp` must stay upper-bounded until `cli/mcp_server.py` is ported off `mcp.server.fastmcp`.

mcp 2.0.0 removed `mcp.server.fastmcp`. `cli/mcp_server.py:20` imports it at module scope, and
`tg run --rewrite` lazily imports that module (`ast_workflows.py:64` ->
`execute_rewrite_plan_json`), so a fresh install resolving 2.x kills the rewrite path with::

    ModuleNotFoundError: No module named 'mcp.server.fastmcp'

That is a USER-FACING break -- `pip install tensor-grep` today -- not merely a CI one.

Why it hid for two releases: every in-repo consumer installs from `uv.lock`, which pins mcp
1.28.1, so the whole test suite and every dev environment is immune. The ONLY component that
resolves the dependency FRESH is the PyPI artifact smoke venv, and it is the only thing that
caught it -- release runs 30363114542 (v1.101.10) and 30375308409 (v1.101.11), both of which
blocked `publish-pypi` and left the version tagged but unpublished.

That asymmetry is the reason this guard reads `pyproject.toml` rather than importing anything: an
import-based check would pass under the lock forever and could never see the drift it exists to
catch. It is a claim about the DECLARED contract, which is what a fresh installer resolves
against.
"""

from __future__ import annotations

import tomllib
from pathlib import Path

from packaging.requirements import Requirement
from packaging.version import Version

_PYPROJECT = Path(__file__).resolve().parents[2] / "pyproject.toml"


def _mcp_requirement() -> Requirement:
    metadata = tomllib.loads(_PYPROJECT.read_text(encoding="utf-8"))
    for raw in metadata["project"]["dependencies"]:
        requirement = Requirement(raw)
        if requirement.name == "mcp":
            return requirement
    raise AssertionError("mcp is no longer a declared dependency; update or delete this guard")


def test_mcp_is_capped_below_2() -> None:
    """THE DEFECT: `mcp>=1.27.2` with no upper bound resolved to 2.0.0 on a fresh install."""
    requirement = _mcp_requirement()

    assert not requirement.specifier.contains("2.0.0"), (
        f"the declared mcp requirement ({requirement}) admits 2.0.0, which removed "
        "`mcp.server.fastmcp` -- `tg run --rewrite` dies with ModuleNotFoundError on any fresh "
        "install. Port cli/mcp_server.py off mcp.server.fastmcp before lifting the cap."
    )


def test_the_security_floor_is_not_lost_while_capping() -> None:
    """CONTROL ARM: without it, `mcp<2` alone would satisfy the test above while silently
    reopening CVE-2026-52870 (the mcp 1.26.0 advisory the floor was raised for).

    A cap and a floor are independent claims; a change that fixes one by dropping the other is
    the failure mode this pair exists to prevent.
    """
    requirement = _mcp_requirement()

    assert not requirement.specifier.contains("1.26.0"), (
        f"the declared mcp requirement ({requirement}) admits 1.26.0, which is the version "
        "CVE-2026-52870 was patched after; the >=1.27.2 floor must survive any capping change"
    )


def test_the_currently_locked_version_still_satisfies_the_declared_range() -> None:
    """PREMISE: the cap must not exclude what the lock pins.

    An upper bound that contradicts `uv.lock` is the silent-downgrade trap this repo has hit
    before (a cap unsatisfiable on a newer Python resolved the WHOLE package down with no error).
    Reading the locked version back and asserting it is admissible catches that at test time
    rather than at somebody's install time.
    """
    lock = (_PYPROJECT.parent / "uv.lock").read_text(encoding="utf-8")
    marker = '\nname = "mcp"\nversion = "'
    start = lock.find(marker)
    assert start != -1, "could not find the mcp entry in uv.lock"
    locked = lock[start + len(marker) :].split('"', 1)[0]

    # PREMISE: we really parsed a version, not an empty string or a marker fragment.
    assert Version(locked) >= Version("1.0"), f"parsed an implausible locked version: {locked!r}"

    requirement = _mcp_requirement()
    assert requirement.specifier.contains(locked), (
        f"uv.lock pins mcp {locked}, which the declared requirement ({requirement}) excludes -- "
        "a fresh resolve would silently move to a different version than the one tested"
    )
