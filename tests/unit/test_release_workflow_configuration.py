import re
from pathlib import Path


def _job_section(workflow: str, job_name: str) -> str:
    start = workflow.index(f"  {job_name}:")
    match = re.search(r"\n  [A-Za-z0-9_-]+:", workflow[start + 1 :])
    if match is None:
        return workflow[start:]
    return workflow[start : start + 1 + match.start()]


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


def test_ci_workflow_should_run_smoke_gate_before_expensive_jobs() -> None:
    workflow = Path(".github/workflows/ci.yml").read_text(encoding="utf-8")
    smoke_section = _job_section(workflow, "smoke")
    assert "needs: repo-hygiene" in smoke_section
    assert "cargo build --no-default-features" in smoke_section
    assert "uv run python -m tensor_grep --version" in smoke_section
    assert "tests/golden/fixture_data" in smoke_section

    for job_name in [
        "release-readiness",
        "agent-readiness",
        "windows-agent-readiness",
        "package-manager-readiness",
        "static-analysis",
        "test-python",
        "test-rust-core",
        "search-golden-parity",
        "native-build-smoke",
        "test-gpu-linux",
        "benchmark-regression",
    ]:
        assert "needs: smoke" in _job_section(workflow, job_name)

    assert "needs: [smoke," in _job_section(workflow, "release")


def test_gitignore_should_track_rust_cargo_lock_and_scope_root_text_artifacts() -> None:
    gitignore = Path(".gitignore").read_text(encoding="utf-8")
    ignore_lines = {
        line.strip()
        for line in gitignore.splitlines()
        if line.strip() and not line.lstrip().startswith("#")
    }
    assert "rust_core/Cargo.lock" not in ignore_lines
    assert "*.txt" not in ignore_lines
    assert "*.log" not in ignore_lines
    assert "/*.txt" in ignore_lines
    assert "/*.log" in ignore_lines


def test_ci_repo_hygiene_guard_should_block_scratch_artifacts_and_missing_lockfile() -> None:
    workflow = Path(".github/workflows/ci.yml").read_text(encoding="utf-8")
    repo_hygiene = _job_section(workflow, "repo-hygiene")
    assert "Repo Hygiene Guard" in repo_hygiene
    assert "python scripts/check_repo_hygiene.py" in repo_hygiene


def test_ci_workflow_should_keep_dependency_install_retry_guards() -> None:
    workflow = Path(".github/workflows/ci.yml").read_text(encoding="utf-8")
    assert "Install Dependencies (Unix with retry)" in workflow
    assert "Install Dependencies (Windows with retry)" in workflow
    assert "Dependency install failed after 5 attempts." in workflow


def test_ci_python_matrix_should_be_timeout_bounded() -> None:
    workflow = Path(".github/workflows/ci.yml").read_text(encoding="utf-8")
    python_section = workflow.split("  test-python:", 1)[1].split("  test-rust-core:", 1)[0]
    assert "timeout-minutes: 30" in python_section


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
    native_smoke_section = _job_section(workflow, "native-build-smoke")
    assert "macos-latest" in native_smoke_section
    assert "macos-15-intel" in native_smoke_section


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


def test_ci_workflow_should_run_agent_readiness_gate() -> None:
    workflow = Path(".github/workflows/ci.yml").read_text(encoding="utf-8")
    agent_section = _job_section(workflow, "agent-readiness")
    assert "scripts/agent_readiness.py" in agent_section
    assert "--no-shell-probes" in agent_section
    assert "--no-wsl-probe" in agent_section
    assert "needs: smoke" in agent_section
    assert "agent-readiness" in _job_section(workflow, "release")


def test_ci_workflow_should_run_windows_agent_readiness_shell_probes() -> None:
    workflow = Path(".github/workflows/ci.yml").read_text(encoding="utf-8")
    windows_section = _job_section(workflow, "windows-agent-readiness")
    assert "runs-on: windows-latest" in windows_section
    assert "needs: smoke" in windows_section
    assert "scripts/agent_readiness.py" in windows_section
    assert "--only-shell-probes" in windows_section
    assert "--no-wsl-probe" in windows_section
    assert "scripts/stage_windows_ci_launchers.ps1" in windows_section
    assert "windows-agent-readiness" in _job_section(workflow, "release")


def test_ci_workflow_should_validate_release_tag_after_publish_success_gate() -> None:
    workflow = Path(".github/workflows/ci.yml").read_text(encoding="utf-8")
    release_tag_section = _job_section(workflow, "release-tag-smoke")
    assert "needs: [release, publish-success-gate]" in release_tag_section
    assert "validate_release_assets.py" in release_tag_section
    assert "scripts/agent_readiness.py" in release_tag_section


def test_ci_workflow_should_not_cancel_in_progress_main_pushes() -> None:
    workflow = Path(".github/workflows/ci.yml").read_text(encoding="utf-8")
    assert "cancel-in-progress: ${{ github.ref != 'refs/heads/main' }}" in workflow


def test_ci_package_manager_readiness_should_fail_through_validator_fallback() -> None:
    workflow = Path(".github/workflows/ci.yml").read_text(encoding="utf-8")
    package_manager_section = _job_section(workflow, "package-manager-readiness")
    assert (
        "winget validate --manifest scripts\\oimiragieo.tensor-grep.yaml" in package_manager_section
    )
    assert "knownSchemaHeaderWarning" in package_manager_section
    assert "The schema header URL does not match the expected pattern" in package_manager_section
    assert "uv run python scripts/validate_release_assets.py" in package_manager_section
    assert "Python release asset validator fallback failed" in package_manager_section
    assert (
        'throw "winget validate failed with exit code $wingetExitCode"' in package_manager_section
    )
    assert "exit 0" not in package_manager_section


def test_release_workflow_should_not_hide_real_winget_validation_failure() -> None:
    workflow = Path(".github/workflows/release.yml").read_text(encoding="utf-8")
    validate_section = _job_section(workflow, "validate-package-managers")
    assert "knownSchemaHeaderWarning" in validate_section
    assert "The schema header URL does not match the expected pattern" in validate_section
    assert 'throw "winget validate failed with exit code $wingetExitCode"' in validate_section
    assert (
        "winget validate failed with exit code $wingetExitCode; falling back"
        not in validate_section
    )
    assert "exit 0" not in validate_section


def test_ci_workflow_should_gate_hot_query_benchmark_regressions() -> None:
    workflow = Path(".github/workflows/ci.yml").read_text(encoding="utf-8")
    benchmark_section = _job_section(workflow, "benchmark-regression")
    assert "run_hot_query_benchmarks.py" in benchmark_section
    assert "Enforce hot-query benchmark regression gate" in benchmark_section


def test_ci_workflow_should_gate_agent_workflow_benchmark_regressions() -> None:
    workflow = Path(".github/workflows/ci.yml").read_text(encoding="utf-8")
    benchmark_section = _job_section(workflow, "benchmark-regression")
    assert "run_agent_workflow_benchmarks.py" in benchmark_section
    assert "run_agent_success_harness.py" in benchmark_section
    assert "Enforce agent workflow benchmark regression gate" in benchmark_section
    assert "bench_agent_workflow.head.json" in benchmark_section
    assert "bench_agent_success_harness.head.json" in benchmark_section
    assert "TENSOR_GREP_AGENT_WORKFLOW_BENCH_DIR" in benchmark_section
    assert "uv run python ../benchmarks/run_agent_workflow_benchmarks.py" in benchmark_section
    assert "--binary rust_core/target/release/tg" in benchmark_section
    assert "--iterations 1" in benchmark_section
    assert "--files 50" in benchmark_section
    assert "--loc 2500" in benchmark_section
