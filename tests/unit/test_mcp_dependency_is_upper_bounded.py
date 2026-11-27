"""`mcp` requires `>=2.0.0` (no `<2` cap) now that `cli/mcp_server.py` uses `mcp.server.mcpserver`.

This guard reads `pyproject.toml` and `uv.lock` directly so any accidental downgrade or
reintroduction of a `<2` upper bound is caught immediately against the declared contract.
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


def test_mcp_allows_v2_without_upper_bound() -> None:
    """`cli/mcp` is ported to `mcp.server.mcpserver`, so `mcp>=2.0.0` must be admitted."""
    requirement = _mcp_requirement()

    assert requirement.specifier.contains("2.0.0"), (
        f"the declared mcp requirement ({requirement}) must admit 2.0.0+"
    )
    assert requirement.specifier.contains("2.3.0"), (
        f"the declared mcp requirement ({requirement}) must not cap below 2.3.0"
    )


def test_the_security_floor_is_preserved() -> None:
    """CVE-2026-52870 (1.26.0) and CVE-2026-59951 (1.29.1) must remain excluded."""
    requirement = _mcp_requirement()

    assert not requirement.specifier.contains("1.26.0"), (
        f"the declared mcp requirement ({requirement}) admits 1.26.0 (CVE-2026-52870)"
    )
    assert not requirement.specifier.contains("1.29.1"), (
        f"the declared mcp requirement ({requirement}) admits 1.29.1 (CVE-2026-59951)"
    )


def test_the_currently_locked_version_still_satisfies_the_declared_range() -> None:
    """`uv.lock` must pin `mcp>=2.0.0` and satisfy the declared requirement."""
    lock = (_PYPROJECT.parent / "uv.lock").read_text(encoding="utf-8")
    marker = '\nname = "mcp"\nversion = "'
    start = lock.find(marker)
    assert start != -1, "could not find the mcp entry in uv.lock"
    locked = lock[start + len(marker) :].split('"', 1)[0]

    assert Version(locked) >= Version("2.0.0"), f"expected mcp>=2.0.0 in uv.lock, got {locked!r}"

    requirement = _mcp_requirement()
    assert requirement.specifier.contains(locked), (
        f"uv.lock pins mcp {locked}, which the declared requirement ({requirement}) excludes"
    )
