import importlib.util
from pathlib import Path


def test_should_validate_release_and_package_assets_consistency():
    root = Path(__file__).resolve().parents[2]
    script_path = root / "scripts" / "validate_release_assets.py"
    spec = importlib.util.spec_from_file_location("validate_release_assets", script_path)
    assert spec is not None and spec.loader is not None

    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)

    errors = module.validate_all()
    assert errors == []


def test_should_validate_winget_manifest_structure():
    root = Path(__file__).resolve().parents[2]
    script_path = root / "scripts" / "validate_release_assets.py"
    spec = importlib.util.spec_from_file_location("validate_release_assets", script_path)
    assert spec is not None and spec.loader is not None

    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)

    winget = (
        "PackageIdentifier: oimiragieo.tensor-grep\n"
        "PackageVersion: 1.2.3\n"
        "Installers:\n"
        "  - Architecture: x64\n"
        "    InstallerType: portable\n"
        "    InstallerUrl: "
        "https://github.com/oimiragieo/tensor-grep/releases/download/v1.2.3/tg-windows-amd64-cpu.exe\n"
    )
    errors = module.validate_winget_manifest(winget_content=winget, py_version="1.2.3")
    assert errors == []


def test_should_fail_winget_manifest_when_installer_url_not_nested():
    root = Path(__file__).resolve().parents[2]
    script_path = root / "scripts" / "validate_release_assets.py"
    spec = importlib.util.spec_from_file_location("validate_release_assets", script_path)
    assert spec is not None and spec.loader is not None

    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)

    winget = (
        "PackageIdentifier: oimiragieo.tensor-grep\n"
        "PackageVersion: 1.2.3\n"
        "Installers:\n"
        "  - Architecture: x64\n"
        "    InstallerType: portable\n"
        "InstallerUrl: "
        "https://github.com/oimiragieo/tensor-grep/releases/download/v1.2.3/tg-windows-amd64-cpu.exe\n"
    )
    errors = module.validate_winget_manifest(winget_content=winget, py_version="1.2.3")
    assert any("InstallerUrl must be nested under first installer mapping" in err for err in errors)


def test_should_require_ci_pypi_parity_retry_arguments():
    root = Path(__file__).resolve().parents[2]
    script_path = root / "scripts" / "validate_release_assets.py"
    spec = importlib.util.spec_from_file_location("validate_release_assets", script_path)
    assert spec is not None and spec.loader is not None

    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)

    ci_workflow = """
    publish-pypi:
      needs: [release, build-wheels-pypi, build-sdist-pypi, validate-pypi-artifacts]
      steps:
        - uses: astral-sh/setup-uv@v5
        - uses: astral-sh/setup-uv@v5
        - run: |
            python scripts/validate_release_version_parity.py
    """
    errors = module.validate_ci_workflow_content(ci_workflow=ci_workflow)
    assert any("--pypi-wait-seconds" in err for err in errors)
    assert any("--pypi-poll-interval-seconds" in err for err in errors)


def test_should_require_ci_package_manager_bundle_build_and_checksum_verification():
    root = Path(__file__).resolve().parents[2]
    script_path = root / "scripts" / "validate_release_assets.py"
    spec = importlib.util.spec_from_file_location("validate_release_assets", script_path)
    assert spec is not None and spec.loader is not None

    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)

    ci_workflow = """
    package-manager-readiness:
      steps:
        - run: uv run python scripts/prepare_package_manager_release.py --check
    """
    errors = module.validate_ci_workflow_content(ci_workflow=ci_workflow)
    assert any("Build package-manager publish bundle artifact" in err for err in errors)
    assert any("Verify package-manager publish bundle checksums" in err for err in errors)
    assert any("Smoke-test package-manager bundle contracts" in err for err in errors)
    assert any("Upload package-manager bundle artifact" in err for err in errors)


def test_should_require_ci_terminal_publish_success_gate():
    root = Path(__file__).resolve().parents[2]
    script_path = root / "scripts" / "validate_release_assets.py"
    spec = importlib.util.spec_from_file_location("validate_release_assets", script_path)
    assert spec is not None and spec.loader is not None

    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)

    ci_workflow = """
    publish-pypi:
      needs: [release, build-wheels-pypi, build-sdist-pypi, validate-pypi-artifacts]
      steps:
        - uses: astral-sh/setup-uv@v5
        - uses: astral-sh/setup-uv@v5
        - run: |
            python scripts/validate_release_version_parity.py \
              --pypi-wait-seconds 180 \
              --pypi-poll-interval-seconds 10
    """
    errors = module.validate_ci_workflow_content(ci_workflow=ci_workflow)
    assert any("publish-success-gate" in err for err in errors)
    assert any("empty release_version output" in err for err in errors)
    assert any("non-empty release_version" in err for err in errors)


def test_should_require_release_job_to_depend_on_benchmark_regression_gate():
    root = Path(__file__).resolve().parents[2]
    script_path = root / "scripts" / "validate_release_assets.py"
    spec = importlib.util.spec_from_file_location("validate_release_assets", script_path)
    assert spec is not None and spec.loader is not None

    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)

    ci_workflow = """
    jobs:
      release:
        needs: [release-readiness, package-manager-readiness, static-analysis, test-python, test-rust-core, test-gpu-linux]
    """
    errors = module.validate_ci_workflow_content(ci_workflow=ci_workflow)
    assert any("release job must depend on benchmark-regression" in err for err in errors)


def test_should_require_ci_benchmark_jobs_to_use_auto_baseline_resolution():
    root = Path(__file__).resolve().parents[2]
    script_path = root / "scripts" / "validate_release_assets.py"
    spec = importlib.util.spec_from_file_location("validate_release_assets", script_path)
    assert spec is not None and spec.loader is not None

    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)

    ci_workflow = """
    jobs:
      benchmark-regression:
        steps:
          - run: |
              uv run python benchmarks/check_regression.py \
                --baseline benchmarks/baselines/run_benchmarks.ubuntu.json \
                --current artifacts/bench_run_benchmarks.json
          - run: |
              uv run python benchmarks/summarize_benchmarks.py \
                --baseline benchmarks/baselines/run_benchmarks.ubuntu.json \
                --current artifacts/bench_run_benchmarks.json \
                --output artifacts/benchmark_summary.md
    """
    errors = module.validate_ci_workflow_content(ci_workflow=ci_workflow)
    assert any("check_regression.py" in err and "--baseline auto" in err for err in errors)
    assert any("summarize_benchmarks.py" in err and "--baseline auto" in err for err in errors)


def test_should_require_auto_baseline_per_benchmark_command_invocation():
    root = Path(__file__).resolve().parents[2]
    script_path = root / "scripts" / "validate_release_assets.py"
    spec = importlib.util.spec_from_file_location("validate_release_assets", script_path)
    assert spec is not None and spec.loader is not None

    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)

    ci_workflow = """
    jobs:
      benchmark-regression:
        steps:
          - run: |
              uv run python benchmarks/check_regression.py \
                --current artifacts/bench_run_benchmarks.json
          - run: |
              uv run python benchmarks/summarize_benchmarks.py \
                --baseline auto \
                --current artifacts/bench_run_benchmarks.json \
                --output artifacts/benchmark_summary.md
    """
    errors = module.validate_ci_workflow_content(ci_workflow=ci_workflow)
    assert any("check_regression.py" in err and "--baseline auto" in err for err in errors)
    assert not any("summarize_benchmarks.py" in err and "--baseline auto" in err for err in errors)


def test_should_require_structural_gpu_ci_steps_for_retry_and_gpu_pytest():
    root = Path(__file__).resolve().parents[2]
    script_path = root / "scripts" / "validate_release_assets.py"
    spec = importlib.util.spec_from_file_location("validate_release_assets", script_path)
    assert spec is not None and spec.loader is not None

    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)

    ci_workflow = """
    jobs:
      test-gpu-linux:
        runs-on: ubuntu-latest
        steps:
          - name: Verify cuDF / RAPIDS Configuration
            run: uv pip install cudf-cu12
    """
    errors = module.validate_ci_workflow_content(ci_workflow=ci_workflow)
    assert any(
        "test-gpu-linux job must include step `Verify cuDF / RAPIDS Configuration (with retry)`"
        in err
        for err in errors
    )
    assert any(
        "test-gpu-linux job must include step `Run Pytest with GPU Hooks`" in err for err in errors
    )


def test_should_require_structural_benchmark_regression_steps_with_auto_baseline():
    root = Path(__file__).resolve().parents[2]
    script_path = root / "scripts" / "validate_release_assets.py"
    spec = importlib.util.spec_from_file_location("validate_release_assets", script_path)
    assert spec is not None and spec.loader is not None

    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)

    ci_workflow = """
    jobs:
      benchmark-regression:
        runs-on: ubuntu-latest
        steps:
          - name: Enforce benchmark regression gate
            run: |
              uv run python benchmarks/check_regression.py --current artifacts/bench_run_benchmarks.json
          - name: Build benchmark markdown summary
            run: |
              uv run python benchmarks/summarize_benchmarks.py --current artifacts/bench_run_benchmarks.json
    """
    errors = module.validate_ci_workflow_content(ci_workflow=ci_workflow)
    assert any(
        "benchmark-regression `Enforce benchmark regression gate` step must pass `--baseline auto`"
        in err
        for err in errors
    )
    assert any(
        "benchmark-regression `Build benchmark markdown summary` step must pass `--baseline auto`"
        in err
        for err in errors
    )


def test_should_require_ci_ruff_preview_formatter_contract():
    root = Path(__file__).resolve().parents[2]
    script_path = root / "scripts" / "validate_release_assets.py"
    spec = importlib.util.spec_from_file_location("validate_release_assets", script_path)
    assert spec is not None and spec.loader is not None

    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)

    ci_workflow = """
    jobs:
      static-analysis:
        steps:
          - run: uv run ruff format --check .
    """
    errors = module.validate_ci_workflow_content(ci_workflow=ci_workflow)
    assert any("ruff format --check --preview" in err for err in errors)


def test_should_require_ci_pypi_publish_job_security_contract():
    root = Path(__file__).resolve().parents[2]
    script_path = root / "scripts" / "validate_release_assets.py"
    spec = importlib.util.spec_from_file_location("validate_release_assets", script_path)
    assert spec is not None and spec.loader is not None

    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)

    ci_workflow = """
    publish-pypi:
      needs: [release, build-wheels-pypi, build-sdist-pypi, validate-pypi-artifacts]
      steps:
        - run: echo publish
    """
    errors = module.validate_ci_workflow_content(ci_workflow=ci_workflow)
    assert any("publish-pypi job must target `environment: pypi`" in err for err in errors)
    assert any(
        "publish-pypi job must request `id-token: write` permission" in err for err in errors
    )
    assert any(
        "publish-pypi job must use pypa/gh-action-pypi-publish@release/v1" in err for err in errors
    )


def test_should_require_ci_pypi_publish_job_url_and_skip_existing_contract():
    root = Path(__file__).resolve().parents[2]
    script_path = root / "scripts" / "validate_release_assets.py"
    spec = importlib.util.spec_from_file_location("validate_release_assets", script_path)
    assert spec is not None and spec.loader is not None

    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)

    ci_workflow = """
    publish-pypi:
      environment:
        name: pypi
      permissions:
        id-token: write
      steps:
        - uses: pypa/gh-action-pypi-publish@release/v1
    """
    errors = module.validate_ci_workflow_content(ci_workflow=ci_workflow)
    assert any(
        "publish-pypi job should set canonical PyPI project URL for deployment visibility" in err
        for err in errors
    )
    assert any(
        "publish-pypi job should pass `skip-existing: true` to avoid duplicate-upload failures"
        in err
        for err in errors
    )


def test_should_require_ci_publish_pypi_parity_step_to_include_check_and_retry_flags():
    root = Path(__file__).resolve().parents[2]
    script_path = root / "scripts" / "validate_release_assets.py"
    spec = importlib.util.spec_from_file_location("validate_release_assets", script_path)
    assert spec is not None and spec.loader is not None

    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)

    ci_workflow = """
    jobs:
      release:
        needs: [benchmark-regression]
      publish-pypi:
        environment:
          name: pypi
          url: https://pypi.org/p/tensor-grep
        permissions:
          id-token: write
        steps:
          - uses: pypa/gh-action-pypi-publish@release/v1
          - name: Verify release version parity across tag/assets/PyPI
            run: |
              python scripts/validate_release_version_parity.py \
                --expected-version "${{ needs.release.outputs.release_version }}" \
                --expected-tag "v${{ needs.release.outputs.release_version }}"
    """
    errors = module.validate_ci_workflow_content(ci_workflow=ci_workflow)
    assert any(
        "publish-pypi `Verify release version parity across tag/assets/PyPI` step must include `--check-pypi`"
        in err
        for err in errors
    )
    assert any(
        "publish-pypi `Verify release version parity across tag/assets/PyPI` step must include `--pypi-wait-seconds`"
        in err
        for err in errors
    )
    assert any(
        "publish-pypi `Verify release version parity across tag/assets/PyPI` step must include `--pypi-poll-interval-seconds`"
        in err
        for err in errors
    )


def test_should_require_ci_publish_success_gate_pypi_parity_step_flags():
    root = Path(__file__).resolve().parents[2]
    script_path = root / "scripts" / "validate_release_assets.py"
    spec = importlib.util.spec_from_file_location("validate_release_assets", script_path)
    assert spec is not None and spec.loader is not None

    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)

    ci_workflow = """
    jobs:
      release:
        needs: [benchmark-regression]
      publish-success-gate:
        if: always()
        needs: [release, publish-pypi]
        steps:
          - name: Verify PyPI parity for semantic-release version (always)
            run: |
              python scripts/validate_release_version_parity.py \
                --expected-version "${{ needs.release.outputs.release_version }}" \
                --expected-tag "v${{ needs.release.outputs.release_version }}"
    """
    errors = module.validate_ci_workflow_content(ci_workflow=ci_workflow)
    assert any(
        "publish-success-gate `Verify PyPI parity for semantic-release version (always)` step must include `--check-pypi`"
        in err
        for err in errors
    )
    assert any(
        "publish-success-gate `Verify PyPI parity for semantic-release version (always)` step must include `--pypi-wait-seconds`"
        in err
        for err in errors
    )
    assert any(
        "publish-success-gate `Verify PyPI parity for semantic-release version (always)` step must include `--pypi-poll-interval-seconds`"
        in err
        for err in errors
    )


def test_should_require_ci_publish_pypi_and_publish_success_gate_parity_step_presence():
    root = Path(__file__).resolve().parents[2]
    script_path = root / "scripts" / "validate_release_assets.py"
    spec = importlib.util.spec_from_file_location("validate_release_assets", script_path)
    assert spec is not None and spec.loader is not None

    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)

    ci_workflow = """
    jobs:
      release:
        needs: [benchmark-regression]
      publish-pypi:
        environment:
          name: pypi
          url: https://pypi.org/p/tensor-grep
        permissions:
          id-token: write
        steps:
          - uses: pypa/gh-action-pypi-publish@release/v1
      publish-success-gate:
        if: always()
        needs: [release, publish-pypi]
        steps:
          - name: Confirm publish job result when publishing is required
            run: echo ok
    """
    errors = module.validate_ci_workflow_content(ci_workflow=ci_workflow)
    assert any(
        "publish-pypi job must include step `Verify release version parity across tag/assets/PyPI`"
        in err
        for err in errors
    )
    assert any(
        "publish-success-gate job must include step `Verify PyPI parity for semantic-release version (always)`"
        in err
        for err in errors
    )


def test_should_fail_when_npm_repository_url_is_not_canonical():
    root = Path(__file__).resolve().parents[2]
    script_path = root / "scripts" / "validate_release_assets.py"
    spec = importlib.util.spec_from_file_location("validate_release_assets", script_path)
    assert spec is not None and spec.loader is not None

    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)

    module._version_from_pyproject = lambda: "1.2.3"
    module._version_from_cargo = lambda: "1.2.3"

    real_read = module._read

    def fake_read(path):
        path_str = str(path).replace("\\", "/")
        if path_str.endswith("npm/package.json"):
            return (
                "{"
                '"version":"1.2.3",'
                '"repository":{"type":"git","url":"git+https://github.com/tensor-grep/tensor-grep.git"}'
                "}"
            )
        return real_read(path)

    module._read = fake_read
    errors = module.validate_all()
    assert any("npm/package.json repository.url must be" in err for err in errors)


def test_should_fail_ci_workflow_when_parity_gate_skips_package_managers():
    root = Path(__file__).resolve().parents[2]
    script_path = root / "scripts" / "validate_release_assets.py"
    spec = importlib.util.spec_from_file_location("validate_release_assets", script_path)
    assert spec is not None and spec.loader is not None

    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)

    ci_workflow = """
    publish-pypi:
      steps:
        - run: |
            python scripts/validate_release_version_parity.py --skip-package-managers
    """
    errors = module.validate_ci_workflow_content(ci_workflow=ci_workflow)
    assert any("must not skip package-manager version checks" in err for err in errors)


def test_should_require_package_manager_runbook_and_checklist_sections():
    root = Path(__file__).resolve().parents[2]
    script_path = root / "scripts" / "validate_release_assets.py"
    spec = importlib.util.spec_from_file_location("validate_release_assets", script_path)
    assert spec is not None and spec.loader is not None

    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)

    errors = module.validate_package_manager_docs(
        runbook_content="## Homebrew Tap Flow\n",
        checklist_content="## 5. Rollback runbook\n",
    )
    assert any("## Winget Flow" in err for err in errors)
    assert any("## Rollback Procedures" in err for err in errors)
    assert any("Package-manager distribution finalization" in err for err in errors)


def test_should_require_package_manager_runbook_command_contract():
    root = Path(__file__).resolve().parents[2]
    script_path = root / "scripts" / "validate_release_assets.py"
    spec = importlib.util.spec_from_file_location("validate_release_assets", script_path)
    assert spec is not None and spec.loader is not None

    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)

    errors = module.validate_package_manager_docs(
        runbook_content=(
            "## Homebrew Tap Flow\n"
            "## Winget Flow\n"
            "## Rollback Procedures\n"
            "## Verification Commands\n"
        ),
        checklist_content=(
            "## 4. Package-manager distribution finalization\n"
            "## 5. Rollback runbook\n"
            "Homebrew\n"
            "Winget\n"
        ),
    )
    assert any("prepare_package_manager_release.py --check" in err for err in errors)
    assert any("winget validate --manifest" in err for err in errors)
    assert any("verify_package_manager_bundle_checksums.py --bundle-dir" in err for err in errors)
    assert any(
        "--expected-version X.Y.Z --expected-tag vX.Y.Z --check-pypi" in err for err in errors
    )
    assert any("npm/GitHub rollback guidance" in err for err in errors)


def test_should_require_package_manager_runbook_smoke_install_commands():
    root = Path(__file__).resolve().parents[2]
    script_path = root / "scripts" / "validate_release_assets.py"
    spec = importlib.util.spec_from_file_location("validate_release_assets", script_path)
    assert spec is not None and spec.loader is not None

    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)

    errors = module.validate_package_manager_docs(
        runbook_content=(
            "## Homebrew Tap Flow\n"
            "## Winget Flow\n"
            "## Rollback Procedures\n"
            "## Verification Commands\n"
            "uv run python scripts/prepare_package_manager_release.py --check\n"
            "winget validate --manifest\n"
            "uv run python scripts/verify_package_manager_bundle_checksums.py --bundle-dir\n"
            "python scripts/validate_release_version_parity.py --expected-version X.Y.Z --expected-tag vX.Y.Z --check-npm\n"
            "npm/GitHub mismatch\n"
        ),
        checklist_content=(
            "## 4. Package-manager distribution finalization\n"
            "## 5. Rollback runbook\n"
            "Homebrew\n"
            "Winget\n"
        ),
    )
    assert any("brew install oimiragieo/tap/tensor-grep" in err for err in errors)
    assert any("winget install oimiragieo.tensor-grep" in err for err in errors)
    assert any("tg --version" in err for err in errors)


def test_should_require_explicit_homebrew_version_contract():
    root = Path(__file__).resolve().parents[2]
    script_path = root / "scripts" / "validate_release_assets.py"
    spec = importlib.util.spec_from_file_location("validate_release_assets", script_path)
    assert spec is not None and spec.loader is not None

    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)

    bad_brew = 'class TensorGrep < Formula\n  version "1.2.3"\nend\n'
    errors = module.validate_homebrew_formula_contract(brew_content=bad_brew, py_version="1.2.3")
    assert any("TENSOR_GREP_VERSION assignment" in err for err in errors)
    assert any("version TENSOR_GREP_VERSION" in err for err in errors)


def test_should_require_package_manager_sections_in_installation_docs():
    root = Path(__file__).resolve().parents[2]
    script_path = root / "scripts" / "validate_release_assets.py"
    spec = importlib.util.spec_from_file_location("validate_release_assets", script_path)
    assert spec is not None and spec.loader is not None

    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)

    errors = module.validate_installation_docs(
        installation_content="### Homebrew Tap Flow\n### Winget Flow\n"
    )
    assert any("### Repeatable Release Checklist" in err for err in errors)
    assert any("### Rollback Playbook" in err for err in errors)
    assert any("oimiragieo/tensor-grep" in err for err in errors)
    assert any("npm parity checks" in err for err in errors)


def test_should_require_smoke_test_package_manager_bundle_command_in_runbook():
    root = Path(__file__).resolve().parents[2]
    script_path = root / "scripts" / "validate_release_assets.py"
    spec = importlib.util.spec_from_file_location("validate_release_assets", script_path)
    assert spec is not None and spec.loader is not None

    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)

    runbook = """
    ## Homebrew Tap Flow
    ## Winget Flow
    ## Rollback Procedures
    ## Verification Commands
    uv run python scripts/prepare_package_manager_release.py --check
    winget validate --manifest
    uv run python scripts/verify_package_manager_bundle_checksums.py --bundle-dir artifacts/package-manager-bundle
    python scripts/validate_release_version_parity.py --expected-version X.Y.Z --expected-tag vX.Y.Z --check-pypi
    python scripts/validate_release_version_parity.py --expected-version X.Y.Z --expected-tag vX.Y.Z --check-npm
    brew install oimiragieo/tap/tensor-grep
    winget install oimiragieo.tensor-grep
    tg --version
    """
    checklist = """
    ## 4. Package-manager distribution finalization
    ## 5. Rollback runbook
    Homebrew
    Winget
    """
    errors = module.validate_package_manager_docs(
        runbook_content=runbook,
        checklist_content=checklist,
    )
    assert any("smoke_test_package_manager_bundle.py" in err for err in errors)


def test_should_require_publish_jobs_to_depend_on_tag_version_parity():
    root = Path(__file__).resolve().parents[2]
    script_path = root / "scripts" / "validate_release_assets.py"
    spec = importlib.util.spec_from_file_location("validate_release_assets", script_path)
    assert spec is not None and spec.loader is not None

    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)

    bad_release_workflow = """
    jobs:
      validate-tag-version-parity:
        needs: verify-release-assets
        runs-on: ubuntu-latest
      publish-docs:
        needs: verify-release-assets
        runs-on: ubuntu-latest
      publish-npm:
        needs: verify-release-assets
        runs-on: ubuntu-latest
    """
    errors = module.validate_release_workflow_content(release_workflow=bad_release_workflow)
    assert any("publish-docs must depend on validate-tag-version-parity" in err for err in errors)
    assert any("publish-npm must depend on validate-tag-version-parity" in err for err in errors)


def test_should_require_release_to_publish_package_manager_bundle_assets():
    root = Path(__file__).resolve().parents[2]
    script_path = root / "scripts" / "validate_release_assets.py"
    spec = importlib.util.spec_from_file_location("validate_release_assets", script_path)
    assert spec is not None and spec.loader is not None

    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)

    bad_release_workflow = """
    jobs:
      create-release:
        steps:
          - name: Create GitHub Release
            uses: softprops/action-gh-release@v2
            with:
              files: |
                artifacts/**/tg-*
                artifacts/CHECKSUMS.txt
    """
    errors = module.validate_release_workflow_content(release_workflow=bad_release_workflow)
    assert any("Build package-manager publish bundle" in err for err in errors)
    assert any("Verify package-manager bundle checksums" in err for err in errors)
    assert any("Smoke-test package-manager bundle contracts" in err for err in errors)
    assert any("Smoke-test Binary (Windows)" in err for err in errors)
    assert any("artifacts/package-manager-bundle/**" in err for err in errors)


def test_should_fail_release_workflow_when_removed_skip_pypi_flag_is_present():
    root = Path(__file__).resolve().parents[2]
    script_path = root / "scripts" / "validate_release_assets.py"
    spec = importlib.util.spec_from_file_location("validate_release_assets", script_path)
    assert spec is not None and spec.loader is not None

    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)

    release_workflow = """
    jobs:
      validate-tag-version-parity:
        steps:
          - run: |
              python scripts/validate_release_version_parity.py --skip-pypi
    """
    errors = module.validate_release_workflow_content(release_workflow=release_workflow)
    assert any("unsupported --skip-pypi" in err for err in errors)


def test_should_require_terminal_release_success_gate_dependencies():
    root = Path(__file__).resolve().parents[2]
    script_path = root / "scripts" / "validate_release_assets.py"
    spec = importlib.util.spec_from_file_location("validate_release_assets", script_path)
    assert spec is not None and spec.loader is not None

    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)

    bad_release_workflow = """
    jobs:
      validate-tag-version-parity:
        needs: verify-release-assets
      publish-npm:
        needs: validate-tag-version-parity
      publish-docs:
        needs: validate-tag-version-parity
      release-success-gate:
        needs: validate-tag-version-parity
        runs-on: ubuntu-latest
    """
    errors = module.validate_release_workflow_content(release_workflow=bad_release_workflow)
    assert any(
        "release-success-gate must depend on parity + publish-npm + publish-docs" in err
        for err in errors
    )


def test_should_require_validate_package_managers_job_to_include_preflight_bundle_steps():
    root = Path(__file__).resolve().parents[2]
    script_path = root / "scripts" / "validate_release_assets.py"
    spec = importlib.util.spec_from_file_location("validate_release_assets", script_path)
    assert spec is not None and spec.loader is not None

    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)

    release_workflow = """
    jobs:
      validate-package-managers:
        runs-on: ubuntu-latest
        steps:
          - name: Validate package-manager publish bundle source state
            run: uv run python scripts/prepare_package_manager_release.py --check
      build-binaries:
        needs: [validate-release-assets, validate-package-managers]
      validate-tag-version-parity:
        needs: verify-release-assets
      publish-docs:
        needs: validate-tag-version-parity
      publish-npm:
        needs: validate-tag-version-parity
      release-success-gate:
        needs: [validate-tag-version-parity, publish-npm, publish-docs]
    """
    errors = module.validate_release_workflow_content(release_workflow=release_workflow)
    assert any(
        "validate-package-managers job must include step `Preflight build package-manager publish bundle artifact`"
        in err
        for err in errors
    )
    assert any(
        "validate-package-managers job must include step `Preflight verify package-manager bundle checksums`"
        in err
        for err in errors
    )
    assert any(
        "validate-package-managers job must include step `Preflight smoke-test package-manager bundle contracts`"
        in err
        for err in errors
    )


def test_should_require_create_release_job_to_include_bundle_build_verify_and_smoke_steps():
    root = Path(__file__).resolve().parents[2]
    script_path = root / "scripts" / "validate_release_assets.py"
    spec = importlib.util.spec_from_file_location("validate_release_assets", script_path)
    assert spec is not None and spec.loader is not None

    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)

    release_workflow = """
    jobs:
      validate-package-managers:
        steps:
          - name: Preflight build package-manager publish bundle artifact
          - name: Preflight verify package-manager bundle checksums
          - name: Preflight smoke-test package-manager bundle contracts
      build-binaries:
        needs: [validate-release-assets, validate-package-managers]
      create-release:
        steps:
          - name: Build package-manager publish bundle
            run: uv run python scripts/prepare_package_manager_release.py --output-dir artifacts/package-manager-bundle
      validate-tag-version-parity:
        needs: verify-release-assets
      publish-docs:
        needs: validate-tag-version-parity
      publish-npm:
        needs: validate-tag-version-parity
      release-success-gate:
        needs: [validate-tag-version-parity, publish-npm, publish-docs]
    """
    errors = module.validate_release_workflow_content(release_workflow=release_workflow)
    assert any(
        "create-release job must include step `Verify package-manager bundle checksums`" in err
        for err in errors
    )
    assert any(
        "create-release job must include step `Smoke-test package-manager bundle contracts`" in err
        for err in errors
    )


def test_should_require_create_release_bundle_steps_to_invoke_expected_scripts():
    root = Path(__file__).resolve().parents[2]
    script_path = root / "scripts" / "validate_release_assets.py"
    spec = importlib.util.spec_from_file_location("validate_release_assets", script_path)
    assert spec is not None and spec.loader is not None

    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)

    release_workflow = """
    jobs:
      validate-package-managers:
        steps:
          - name: Preflight build package-manager publish bundle artifact
          - name: Preflight verify package-manager bundle checksums
          - name: Preflight smoke-test package-manager bundle contracts
      build-binaries:
        needs: [validate-release-assets, validate-package-managers]
      create-release:
        steps:
          - name: Build package-manager publish bundle
            run: uv run python scripts/prepare_package_manager_release.py
          - name: Verify package-manager bundle checksums
            run: uv run python scripts/verify_package_manager_bundle_checksums.py
          - name: Smoke-test package-manager bundle contracts
            run: uv run python scripts/smoke_test_package_manager_bundle.py
      validate-tag-version-parity:
        needs: verify-release-assets
      publish-docs:
        needs: validate-tag-version-parity
      publish-npm:
        needs: validate-tag-version-parity
      release-success-gate:
        needs: [validate-tag-version-parity, publish-npm, publish-docs]
    """
    errors = module.validate_release_workflow_content(release_workflow=release_workflow)
    assert any(
        "create-release `Build package-manager publish bundle` step must pass `--output-dir artifacts/package-manager-bundle`"
        in err
        for err in errors
    )
    assert any(
        "create-release `Verify package-manager bundle checksums` step must pass `--bundle-dir artifacts/package-manager-bundle`"
        in err
        for err in errors
    )
    assert any(
        "create-release `Smoke-test package-manager bundle contracts` step must pass `--bundle-dir artifacts/package-manager-bundle`"
        in err
        for err in errors
    )


def test_should_require_validate_tag_version_parity_step_contracts():
    root = Path(__file__).resolve().parents[2]
    script_path = root / "scripts" / "validate_release_assets.py"
    spec = importlib.util.spec_from_file_location("validate_release_assets", script_path)
    assert spec is not None and spec.loader is not None

    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)

    release_workflow = """
    jobs:
      validate-package-managers:
        steps:
          - name: Preflight build package-manager publish bundle artifact
          - name: Preflight verify package-manager bundle checksums
          - name: Preflight smoke-test package-manager bundle contracts
      build-binaries:
        needs: [validate-release-assets, validate-package-managers]
      create-release:
        steps:
          - name: Build package-manager publish bundle
            run: uv run python scripts/prepare_package_manager_release.py --output-dir artifacts/package-manager-bundle
          - name: Verify package-manager bundle checksums
            run: uv run python scripts/verify_package_manager_bundle_checksums.py --bundle-dir artifacts/package-manager-bundle
          - name: Smoke-test package-manager bundle contracts
            run: uv run python scripts/smoke_test_package_manager_bundle.py --bundle-dir artifacts/package-manager-bundle
      verify-release-assets:
        needs: create-release
      validate-tag-version-parity:
        needs: verify-release-assets
        steps:
          - name: Validate release tag/version parity across package metadata
            run: python scripts/validate_release_version_parity.py
      publish-docs:
        needs: validate-tag-version-parity
      publish-npm:
        needs: validate-tag-version-parity
      release-success-gate:
        needs: [validate-tag-version-parity, publish-npm, publish-docs]
    """
    errors = module.validate_release_workflow_content(release_workflow=release_workflow)
    assert any(
        "validate-tag-version-parity `Validate release tag/version parity across package metadata` step must include `--expected-version`"
        in err
        for err in errors
    )
    assert any(
        "validate-tag-version-parity `Validate release tag/version parity across package metadata` step must include `--expected-tag`"
        in err
        for err in errors
    )


def test_should_require_release_parity_steps_to_include_registry_check_flags_and_retries():
    root = Path(__file__).resolve().parents[2]
    script_path = root / "scripts" / "validate_release_assets.py"
    spec = importlib.util.spec_from_file_location("validate_release_assets", script_path)
    assert spec is not None and spec.loader is not None

    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)

    release_workflow = """
    jobs:
      validate-package-managers:
        steps:
          - name: Preflight build package-manager publish bundle artifact
          - name: Preflight verify package-manager bundle checksums
          - name: Preflight smoke-test package-manager bundle contracts
      build-binaries:
        needs: [validate-release-assets, validate-package-managers]
      create-release:
        steps:
          - name: Build package-manager publish bundle
            run: uv run python scripts/prepare_package_manager_release.py --output-dir artifacts/package-manager-bundle
          - name: Verify package-manager bundle checksums
            run: uv run python scripts/verify_package_manager_bundle_checksums.py --bundle-dir artifacts/package-manager-bundle
          - name: Smoke-test package-manager bundle contracts
            run: uv run python scripts/smoke_test_package_manager_bundle.py --bundle-dir artifacts/package-manager-bundle
      verify-release-assets:
        needs: create-release
      validate-tag-version-parity:
        needs: verify-release-assets
      publish-docs:
        needs: validate-tag-version-parity
      publish-npm:
        needs: validate-tag-version-parity
        steps:
          - name: Verify npm registry parity for release version
            run: python scripts/validate_release_version_parity.py --expected-version "${GITHUB_REF#refs/tags/v}"
      release-success-gate:
        needs: [validate-tag-version-parity, publish-npm, publish-docs]
        steps:
          - name: Verify final npm parity before release success gate
            run: python scripts/validate_release_version_parity.py --expected-version "${GITHUB_REF#refs/tags/v}"
          - name: Verify final PyPI parity before release success gate
            run: python scripts/validate_release_version_parity.py --expected-version "${GITHUB_REF#refs/tags/v}"
    """
    errors = module.validate_release_workflow_content(release_workflow=release_workflow)
    assert any(
        "publish-npm `Verify npm registry parity for release version` step must include `--check-npm`"
        in err
        for err in errors
    )
    assert any(
        "publish-npm `Verify npm registry parity for release version` step must include `--npm-wait-seconds`"
        in err
        for err in errors
    )
    assert any(
        "release-success-gate `Verify final npm parity before release success gate` step must include `--check-npm`"
        in err
        for err in errors
    )
    assert any(
        "release-success-gate `Verify final PyPI parity before release success gate` step must include `--check-pypi`"
        in err
        for err in errors
    )
