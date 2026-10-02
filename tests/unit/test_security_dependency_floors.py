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
    # The repo's security floors are `[tool.uv].constraint-dependencies` entries (neither package
    # is a direct dependency), so the floor must be stated there as well as satisfied by the lock.
    pyproject = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))
    constraints = set(pyproject["tool"]["uv"]["constraint-dependencies"])

    assert {"pyjwt>=2.15.0", "urllib3>=2.8.0"} <= constraints
