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


def test_ci_workflow_should_run_on_a_schedule() -> None:
    workflow = Path(".github/workflows/ci.yml").read_text(encoding="utf-8")
    assert "schedule:" in workflow
    assert "cron:" in workflow


def test_ci_workflow_should_include_native_build_smoke_matrix() -> None:
    workflow = Path(".github/workflows/ci.yml").read_text(encoding="utf-8")
    assert "native-build-smoke" in workflow
    assert "cargo build --release" in workflow
    assert "--version" in workflow
    assert "--help" in workflow
    assert "sample.log" in workflow
    assert "target/release/tg" in workflow or "target\\release\\tg.exe" in workflow


def test_benchmark_workflow_should_prepare_ast_benchmark_tools_before_running() -> None:
    workflow = Path(".github/workflows/benchmark.yml").read_text(encoding="utf-8")
    setup_rust = workflow.index("Setup Rust stable")
    install_tools = workflow.index("Install ripgrep and hyperfine")
    install_ast_grep = workflow.index("Install ast-grep comparator")
    build_binary = workflow.index("Build native release binary")
    run_benchmarks = workflow.index("Run benchmark suites")

    assert install_tools < run_benchmarks
    assert install_ast_grep < run_benchmarks
    assert setup_rust < build_binary
    assert build_binary < run_benchmarks
    assert "dtolnay/rust-toolchain@stable" in workflow
    assert "sudo apt-get install -y ripgrep hyperfine" in workflow
    assert "cargo install ast-grep --version 0.41.1 --locked" in workflow
    assert "cargo build --release --no-default-features" in workflow


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
