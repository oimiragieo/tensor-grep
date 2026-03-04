from pathlib import Path


def test_release_workflow_should_not_use_removed_skip_pypi_flag() -> None:
    workflow = Path(".github/workflows/release.yml").read_text(encoding="utf-8")
    assert "--skip-pypi" not in workflow


def test_release_workflow_should_call_parity_validator_with_expected_tag() -> None:
    workflow = Path(".github/workflows/release.yml").read_text(encoding="utf-8")
    assert "python scripts/validate_release_version_parity.py" in workflow
    assert '--expected-tag "${GITHUB_REF#refs/tags/}"' in workflow


def test_ci_publish_parity_gate_should_validate_package_manager_versions() -> None:
    workflow = Path(".github/workflows/ci.yml").read_text(encoding="utf-8")
    assert "python scripts/validate_release_version_parity.py" in workflow
    assert "--skip-package-managers" not in workflow
