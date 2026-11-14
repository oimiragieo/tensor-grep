from __future__ import annotations

import importlib
import sys
from pathlib import Path

import pytest
import yaml

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "scripts"))
validate_release_publisher_census = importlib.import_module(
    "_release_assets_checks.release_security"
).validate_release_publisher_census


def _workflow(step: dict[str, str]) -> str:
    return yaml.safe_dump({"jobs": {"publish": {"steps": [step]}}})


def test_publisher_census_accepts_only_authoritative_ci() -> None:
    workflows = {
        ".github/workflows/ci.yml": _workflow({"uses": "softprops/action-gh-release@sha"}),
        ".github/workflows/proof.yml": _workflow({"uses": "actions/upload-artifact@sha"}),
    }
    assert validate_release_publisher_census(workflows=workflows) == []


@pytest.mark.parametrize(
    "step",
    [
        {"uses": "pypa/gh-action-pypi-publish@sha"},
        {"run": "gh release upload v1 artifacts/tg --clobber"},
        {"run": "npm publish --access public"},
        {"run": "maturin upload dist/*"},
    ],
)
def test_publisher_census_rejects_second_publisher(step: dict[str, str]) -> None:
    workflows = {
        ".github/workflows/ci.yml": _workflow({
            "uses": "python-semantic-release/python-semantic-release@sha"
        }),
        ".github/workflows/backfill.yml": _workflow(step),
    }
    errors = validate_release_publisher_census(workflows=workflows)
    assert errors == [
        "Release publishing is only allowed in .github/workflows/ci.yml; "
        "found .github/workflows/backfill.yml:publish"
    ]


def test_publisher_census_requires_authoritative_publisher() -> None:
    workflows = {".github/workflows/proof.yml": _workflow({"uses": "actions/upload-artifact@sha"})}
    assert validate_release_publisher_census(workflows=workflows) == [
        "Workflow publisher census did not find the authoritative CI publisher"
    ]


def test_publisher_census_reports_invalid_yaml() -> None:
    errors = validate_release_publisher_census(workflows={".github/workflows/ci.yml": "jobs: ["})
    assert any("cannot parse .github/workflows/ci.yml" in error for error in errors)


def test_publisher_census_reports_malformed_steps() -> None:
    content = yaml.safe_dump({"jobs": {"publish": {"steps": None}}})
    errors = validate_release_publisher_census(workflows={".github/workflows/ci.yml": content})
    assert any("ci.yml:publish steps to be a list" in error for error in errors)


def test_repository_release_publisher_census_is_clean() -> None:
    workflows = {
        path.relative_to(ROOT).as_posix(): path.read_text(encoding="utf-8")
        for path in sorted((ROOT / ".github" / "workflows").glob("*.yml"))
    }
    assert validate_release_publisher_census(workflows=workflows) == []
