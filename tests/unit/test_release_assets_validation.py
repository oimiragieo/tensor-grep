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
