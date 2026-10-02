from __future__ import annotations

import json
import tomllib
from pathlib import Path

from packaging.version import Version

ROOT = Path(__file__).resolve().parents[2]


def test_npm_lock_should_keep_wrapper_dependency_free() -> None:
    package_lock = json.loads((ROOT / "npm" / "package-lock.json").read_text(encoding="utf-8"))

    assert set(package_lock["packages"]) == {""}


def test_uv_lock_should_pin_pyjwt_above_security_release_floor() -> None:
    uv_lock = tomllib.loads((ROOT / "uv.lock").read_text(encoding="utf-8"))
    pyjwt_package = next(pkg for pkg in uv_lock["package"] if pkg["name"] == "pyjwt")

    # 2.13.0 carries 13 open PYSEC-2026-41xx advisories (fixed in 2.14.0 / 2.15.0); audit.yml's
    # pip-audit gate failed on main for days on exactly this.
    assert Version(pyjwt_package["version"]) >= Version("2.15.0")


def test_uv_lock_should_pin_urllib3_above_security_release_floor() -> None:
    uv_lock = tomllib.loads((ROOT / "uv.lock").read_text(encoding="utf-8"))
    urllib3_package = next(pkg for pkg in uv_lock["package"] if pkg["name"] == "urllib3")

    # urllib3 is transitive only; 2.7.0 carries PYSEC-2026-4175/4176/4177 (fixed in 2.8.0).
    assert Version(urllib3_package["version"]) >= Version("2.8.0")


def test_uv_constraint_floors_match_the_locked_security_releases() -> None:
    # `[tool.uv].constraint-dependencies` governs this repo's own resolution (lock/CI audit); the
    # floors that reach PyPI users are asserted separately below. Both are required.
    pyproject = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))
    constraints = set(pyproject["tool"]["uv"]["constraint-dependencies"])

    assert {"pyjwt>=2.15.0", "urllib3>=2.8.0"} <= constraints


def test_published_metadata_carries_the_pyjwt_and_urllib3_security_floors() -> None:
    """A `[tool.uv]` constraint is lock-only and never reaches `pip install tensor-grep`, so an
    environment that already holds PyJWT 2.13.0 / urllib3 2.7.0 would keep the vulnerable build
    while every gate stayed green. PyJWT is reachable from the BASE install (mcp[crypto] ->
    pyjwt), urllib3 only through the `nlp` extra (tritonclient[http] -> geventhttpclient), so each
    floor belongs in the metadata that actually pulls it.
    """
    project = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))["project"]

    assert "pyjwt>=2.15.0" in project["dependencies"]
    assert "urllib3>=2.8.0" in project["optional-dependencies"]["nlp"]
