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
    assert "Dependency install failed after 5 attempts." in workflow


def test_ci_gpu_workflow_should_keep_dependency_retry_guards() -> None:
    workflow = Path(".github/workflows/ci.yml").read_text(encoding="utf-8")
    assert "Verify cuDF / RAPIDS Configuration (with retry)" in workflow
    assert "GPU dependency install failed (attempt ${attempt}/5)" in workflow
    assert "GPU dependency install failed after 5 attempts." in workflow


def test_ci_benchmark_regression_jobs_should_use_auto_baseline_resolution() -> None:
    workflow = Path(".github/workflows/ci.yml").read_text(encoding="utf-8")
    assert "benchmarks/check_regression.py" in workflow
    assert "benchmarks/summarize_benchmarks.py" in workflow
    assert "--baseline auto" in workflow


def test_ci_workflow_should_run_windows_search_golden_parity_job() -> None:
    workflow = Path(".github/workflows/ci.yml").read_text(encoding="utf-8")
    assert "search-golden-parity" in workflow
    assert "cargo test --test test_search_golden" in workflow


def test_release_workflow_should_smoke_test_package_manager_bundle_before_publish() -> None:
    workflow = Path(".github/workflows/release.yml").read_text(encoding="utf-8")
    assert "Smoke-test package-manager bundle contracts" in workflow
    assert "scripts/smoke_test_package_manager_bundle.py" in workflow
    assert "--bundle-dir artifacts/package-manager-bundle" in workflow


def test_release_workflow_should_preflight_build_and_verify_package_manager_bundle() -> None:
    workflow = Path(".github/workflows/release.yml").read_text(encoding="utf-8")
    assert "Preflight build package-manager publish bundle artifact" in workflow
    assert "Preflight verify package-manager bundle checksums" in workflow
    assert "Preflight smoke-test package-manager bundle contracts" in workflow
