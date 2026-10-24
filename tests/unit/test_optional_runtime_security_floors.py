from __future__ import annotations

import tomllib
from pathlib import Path

import pytest
from packaging.requirements import Requirement
from packaging.version import Version

ROOT = Path(__file__).resolve().parents[2]


def _locked_versions(lock: dict, name: str) -> list[str]:
    return [package["version"] for package in lock["package"] if package["name"] == name]


def _meets_floor(versions: list[str], floor: str) -> bool:
    return bool(versions) and all(Version(version) >= Version(floor) for version in versions)


def _dependency_names(dependencies: list[str]) -> set[str]:
    return {Requirement(value).name for value in dependencies}


@pytest.mark.parametrize(
    ("extra", "requirement"),
    [
        ("gpu", "filelock>=4.2.1"),
        ("gpu-win", "filelock>=4.2.1"),
        ("bench", "filelock>=4.2.1"),
        ("nlp", "filelock>=4.2.1"),
        ("semantic", "filelock>=4.2.1"),
        ("gpu", "setuptools>=84.1.0; python_version >= '3.12'"),
        ("gpu-win", "setuptools>=84.1.0; python_version >= '3.12'"),
        ("bench", "setuptools>=84.1.0; python_version >= '3.12'"),
    ],
)
def test_Metadata_AffectedExtra_PublishesPatchedFloor(extra: str, requirement: str) -> None:
    metadata = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))

    requirements = metadata["project"]["optional-dependencies"][extra]

    assert requirement in requirements


@pytest.mark.parametrize(("name", "floor"), [("filelock", "4.2.1"), ("setuptools", "84.1.0")])
def test_Lock_OptionalRuntimeAdvisory_ExcludesAffectedRelease(name: str, floor: str) -> None:
    lock = tomllib.loads((ROOT / "uv.lock").read_text(encoding="utf-8"))

    versions = _locked_versions(lock, name)

    assert _meets_floor(versions, floor)


def test_Constraints_OptionalRuntimeAdvisories_RequirePatchedReleases() -> None:
    metadata = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))

    constraints = metadata["tool"]["uv"]["constraint-dependencies"]

    assert "filelock>=4.2.1" in constraints
    assert "setuptools>=84.1.0" in constraints


def test_Metadata_CoreInstall_DoesNotAddOptionalRuntimePackages() -> None:
    metadata = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))

    names = _dependency_names(metadata["project"]["dependencies"])

    assert "filelock" not in names
    assert "setuptools" not in names
