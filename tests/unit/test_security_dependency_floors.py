from __future__ import annotations

import json
import tomllib
from pathlib import Path

from packaging.version import Version

ROOT = Path(__file__).resolve().parents[2]


def test_npm_lock_should_pin_tar_above_drive_relative_symlink_fix_floor() -> None:
    package_lock = json.loads((ROOT / "npm" / "package-lock.json").read_text(encoding="utf-8"))
    tar_version = package_lock["packages"]["node_modules/tar"]["version"]

    assert Version(tar_version) >= Version("7.5.11")


def test_uv_lock_should_pin_pyjwt_above_unknown_crit_header_fix_floor() -> None:
    uv_lock = tomllib.loads((ROOT / "uv.lock").read_text(encoding="utf-8"))
    pyjwt_package = next(pkg for pkg in uv_lock["package"] if pkg["name"] == "pyjwt")

    assert Version(pyjwt_package["version"]) >= Version("2.12.0")
