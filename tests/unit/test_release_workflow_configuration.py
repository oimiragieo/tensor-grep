from pathlib import Path


def test_release_workflow_should_not_use_removed_skip_pypi_flag() -> None:
    workflow = Path(".github/workflows/release.yml").read_text(encoding="utf-8")
    assert "--skip-pypi" not in workflow


def test_release_workflow_should_call_parity_validator_with_expected_tag() -> None:
    workflow = Path(".github/workflows/release.yml").read_text(encoding="utf-8")
    assert "python scripts/validate_release_version_parity.py" in workflow
    assert '--expected-tag "${GITHUB_REF#refs/tags/}"' in workflow


def test_release_workflow_should_verify_npm_registry_parity_after_publish() -> None:
    workflow = Path(".github/workflows/release.yml").read_text(encoding="utf-8")
    assert "Verify npm registry parity for release version" in workflow
    assert "--check-npm" in workflow


def test_release_success_gate_should_recheck_npm_parity_before_success() -> None:
    workflow = Path(".github/workflows/release.yml").read_text(encoding="utf-8")
    assert "Verify final npm parity before release success gate" in workflow
    assert '--expected-version "${GITHUB_REF#refs/tags/v}"' in workflow
    assert '--expected-tag "${GITHUB_REF#refs/tags/}"' in workflow
    assert "--check-npm" in workflow


def test_release_success_gate_should_recheck_pypi_parity_before_success() -> None:
    workflow = Path(".github/workflows/release.yml").read_text(encoding="utf-8")
    assert "Verify final PyPI parity before release success gate" in workflow
    assert '--expected-version "${GITHUB_REF#refs/tags/v}"' in workflow
    assert '--expected-tag "${GITHUB_REF#refs/tags/}"' in workflow
    assert "--check-pypi" in workflow


def test_ci_publish_parity_gate_should_validate_package_manager_versions() -> None:
    workflow = Path(".github/workflows/ci.yml").read_text(encoding="utf-8")
    assert "python scripts/validate_release_version_parity.py" in workflow
    assert "--skip-package-managers" not in workflow


def test_ci_workflow_should_keep_dependency_install_retry_guards() -> None:
    workflow = Path(".github/workflows/ci.yml").read_text(encoding="utf-8")
    assert "Install Dependencies (Unix with retry)" in workflow
    assert "Install Dependencies (Windows with retry)" in workflow
    assert "Dependency install failed after 3 attempts." in workflow


def test_ci_benchmark_regression_jobs_should_use_auto_baseline_resolution() -> None:
    workflow = Path(".github/workflows/ci.yml").read_text(encoding="utf-8")
    assert "benchmarks/check_regression.py" in workflow
    assert "benchmarks/summarize_benchmarks.py" in workflow
    assert "--baseline auto" in workflow
