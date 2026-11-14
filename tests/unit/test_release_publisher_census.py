from __future__ import annotations

import importlib
import sys
from pathlib import Path

import pytest
import yaml

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "scripts"))
release_assets = importlib.import_module("validate_release_assets")
validate_release_publisher_census = importlib.import_module(
    "_release_assets_checks.release_security"
).validate_release_publisher_census


def _workflow(step: dict[str, object]) -> str:
    return yaml.safe_dump({
        "permissions": {"contents": "read"},
        "jobs": {"publish": {"steps": [step]}},
    })


def test_publisher_census_accepts_only_authoritative_ci() -> None:
    workflows = {
        ".github/workflows/ci.yml": _workflow({"uses": "softprops/action-gh-release@sha"}),
        ".github/workflows/proof.yml": _workflow({"run": 'echo "Use gh release upload manually"'}),
    }
    assert validate_release_publisher_census(workflows=workflows) == []


def test_publisher_census_rejects_inherited_default_permissions() -> None:
    backfill = """
on: pull_request_target
jobs:
  publish:
    steps:
      - env:
          GH_TOKEN: ${{ secrets.GITHUB_TOKEN }}
        run: curl -X POST https://api.github.com/repos/o/r/releases
"""
    workflows = {
        ".github/workflows/ci.yml": _workflow({"uses": "softprops/action-gh-release@sha"}),
        ".github/workflows/backfill.yml": backfill,
    }
    errors = validate_release_publisher_census(workflows=workflows)
    assert any("requires explicit permissions" in error for error in errors)


@pytest.mark.parametrize(
    "step",
    [
        {"uses": "pypa/gh-action-pypi-publish@sha"},
        {"run": "gh release upload v1 artifacts/tg --clobber"},
        {"run": "npm publish --access public"},
        {"run": "maturin upload dist/*"},
        {"run": "uv publish dist/*"},
        {"run": "gh api --method POST repos/o/r/releases"},
        {"run": "gh release \\\n upload v1 artifacts/tg"},
        {"run": 'gh release "$operation" v1'},
        {
            "uses": "actions/github-script@sha",
            "with": {"script": "github.rest.repos.createRelease({owner, repo})"},
        },
    ],
)
def test_publisher_census_rejects_second_publisher(step: dict[str, object]) -> None:
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


@pytest.mark.parametrize(
    "job",
    [
        {"uses": "owner/repo/.github/workflows/publish.yml@sha", "secrets": "inherit"},
        {"steps": [{"uses": "./.github/actions/publish"}]},
    ],
)
def test_publisher_census_rejects_opaque_publish_boundaries(job: dict[str, object]) -> None:
    workflows = {
        ".github/workflows/ci.yml": _workflow({"uses": "softprops/action-gh-release@sha"}),
        ".github/workflows/backfill.yml": yaml.safe_dump({"jobs": {"publish": job}}),
    }
    errors = validate_release_publisher_census(workflows=workflows)
    assert any("forbids opaque" in error for error in errors)


@pytest.mark.parametrize(
    ("permissions", "step"),
    [
        ({"contents": "write"}, {"run": "curl -X POST https://api.github.com/repos/o/r/releases"}),
        ({"packages": "write"}, {"uses": "owner/alternate-release-action@sha"}),
        ({"id-token": "write"}, {"run": "bash -lc 'gh release create v1 artifact'"}),
        ({"contents": "write"}, {"run": 'gh --repo "$GITHUB_REPOSITORY" release create v1'}),
        ({"contents": "write"}, {"run": "gh.exe release upload v1 artifact"}),
        ("write-all", {"run": "echo opaque publisher"}),
    ],
)
def test_publisher_census_rejects_release_write_authority(
    permissions: object, step: dict[str, object]
) -> None:
    backfill = yaml.safe_dump({"permissions": permissions, "jobs": {"publish": {"steps": [step]}}})
    workflows = {
        ".github/workflows/ci.yml": _workflow({"uses": "softprops/action-gh-release@sha"}),
        ".github/workflows/backfill.yml": backfill,
    }
    errors = validate_release_publisher_census(workflows=workflows)
    assert any("Release write authority is only allowed" in error for error in errors)


def test_publisher_census_rejects_unsealed_ci_write_job() -> None:
    ci = yaml.safe_load(_workflow({"uses": "softprops/action-gh-release@sha"}))
    ci["jobs"]["alternate"] = {
        "permissions": {"contents": "write"},
        "steps": [{"run": 'gh --repo "$GITHUB_REPOSITORY" release create v999 artifact'}],
    }
    errors = validate_release_publisher_census(
        workflows={".github/workflows/ci.yml": yaml.safe_dump(ci)}
    )
    assert any(
        "CI release write authority is not sealed for alternate" in error for error in errors
    )


def test_publisher_census_rejects_unapproved_ci_secret() -> None:
    ci = yaml.safe_load(_workflow({"uses": "softprops/action-gh-release@sha"}))
    ci["jobs"]["alternate"] = {
        "permissions": {"contents": "read"},
        "steps": [{"env": {"TOKEN": "${{ SeCrEtS.RELEASE_PAT }}"}, "run": "echo guarded"}],
    }
    errors = validate_release_publisher_census(
        workflows={".github/workflows/ci.yml": yaml.safe_dump(ci)}
    )
    assert any("unapproved secret context" in error for error in errors)


@pytest.mark.parametrize(
    "expression",
    [
        "${{ secrets.NPM_TOKEN }}",
        "${{ format('{0}', secrets.RELEASE_PAT) }}",
        "${{ secrets ['RELEASE_PAT'] }}",
        "${{ format('{1}', '}}', secrets.RELEASE_PAT) }}",
        "${{ toJSON(secrets) }}",
        "${{ fromJSON(toJSON(secrets)).RELEASE_PAT }}",
        "${{ secrets.GITHUB_TOKEN }}:${{ toJSON(secrets) }}",
        "${{ SeCrEtS.NPM_TOKEN }}",
    ],
)
def test_publisher_census_rejects_non_github_secret(expression: str) -> None:
    workflows = {
        ".github/workflows/ci.yml": _workflow({"uses": "softprops/action-gh-release@sha"}),
        ".github/workflows/backfill.yml": _workflow({
            "run": "echo opaque publisher",
            "env": {"TOKEN": expression},
        }),
    }
    errors = validate_release_publisher_census(workflows=workflows)
    assert any("unapproved secret context" in error for error in errors)


def test_dependabot_write_authority_exception_is_content_bound() -> None:
    content = (ROOT / ".github" / "workflows" / "dependabot-automation.yml").read_text(
        encoding="utf-8"
    )
    altered = content.replace("name: Dependabot Automation", "name: Altered Dependabot Automation")
    workflows = {
        ".github/workflows/ci.yml": _workflow({"uses": "softprops/action-gh-release@sha"}),
        ".github/workflows/dependabot-automation.yml": altered,
    }
    errors = validate_release_publisher_census(workflows=workflows)
    assert any("Release write authority is only allowed" in error for error in errors)


def test_workflow_sources_discovers_yaml_publishers(tmp_path: Path) -> None:
    workflow_dir = tmp_path / ".github" / "workflows"
    workflow_dir.mkdir(parents=True)
    (workflow_dir / "ci.yml").write_text(
        _workflow({"uses": "softprops/action-gh-release@sha"}), encoding="utf-8"
    )
    (workflow_dir / "backfill.yaml").write_text(
        _workflow({"run": "uv publish dist/*"}), encoding="utf-8"
    )
    workflows = release_assets._workflow_sources(workflow_dir, root=tmp_path)
    assert any(
        "backfill.yaml:publish" in error
        for error in validate_release_publisher_census(workflows=workflows)
    )


def test_repository_release_publisher_census_is_clean() -> None:
    workflows = release_assets._workflow_sources(ROOT / ".github" / "workflows", root=ROOT)
    assert validate_release_publisher_census(workflows=workflows) == []
