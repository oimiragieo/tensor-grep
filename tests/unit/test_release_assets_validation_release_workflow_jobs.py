"""release.yml create/verify/validate/build/parity job contracts."""

import importlib.util
import textwrap
from pathlib import Path

from tests.unit.test_release_assets_validation_shared import _detag


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


def test_should_require_verify_release_assets_to_depend_on_create_release():
    root = Path(__file__).resolve().parents[2]
    script_path = root / "scripts" / "validate_release_assets.py"
    spec = importlib.util.spec_from_file_location("validate_release_assets", script_path)
    assert spec is not None and spec.loader is not None

    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)

    bad_release_workflow = """
    jobs:
      create-release:
        runs-on: ubuntu-latest
      verify-release-assets:
        needs: build-binaries
        runs-on: ubuntu-latest
        steps:
          - name: Verify uploaded release assets and checksum coverage
            run: |
              python scripts/verify_github_release_assets.py \
                --repo "${{ github.repository }}" \
                --tag "${GITHUB_REF#refs/tags/}" \
                --token "${{ secrets.GITHUB_TOKEN }}"
      validate-tag-version-parity:
        needs: verify-release-assets
      publish-docs:
        needs: validate-tag-version-parity
      publish-npm:
        needs: validate-tag-version-parity
      release-success-gate:
        needs: [validate-tag-version-parity, publish-npm, publish-docs]
    """
    errors = module.validate_release_workflow_content(release_workflow=bad_release_workflow)
    assert any("verify-release-assets must depend on create-release" in err for err in errors)


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
            uses: softprops/action-gh-release@v3
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
    errors = module.validate_release_workflow_content(
        release_workflow=textwrap.dedent(release_workflow)
    )
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
    errors = module.validate_release_workflow_content(
        release_workflow=textwrap.dedent(release_workflow)
    )
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


def test_should_require_verify_release_assets_step_contracts():
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
        steps:
          - name: Verify uploaded release assets and checksum coverage
            run: python scripts/verify_github_release_assets.py --repo "${{ github.repository }}"
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
        "verify-release-assets `Verify uploaded release assets and checksum coverage` step must include `--tag`"
        in err
        for err in errors
    )
    assert any(
        "verify-release-assets `Verify uploaded release assets and checksum coverage` step must include `--token`"
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


def test_should_require_release_binary_smoke_verify_expected_version_flag():
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
          - name: Smoke-verify Linux release binary version
            run: uv run python scripts/smoke_verify_release_binary.py
      verify-release-assets:
        needs: create-release
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
        "create-release `Smoke-verify Linux release binary version` step must pass `--expected-version`"
        in err
        for err in errors
    )
    assert any(
        "create-release `Smoke-verify Linux release binary version` step must pass `--artifacts-dir`"
        in err
        for err in errors
    )


def test_should_require_create_release_sbom_slsa_contract():
    root = Path(__file__).resolve().parents[2]
    script_path = root / "scripts" / "validate_release_assets.py"
    spec = importlib.util.spec_from_file_location("validate_release_assets", script_path)
    assert spec is not None and spec.loader is not None

    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)

    release_workflow = """
    jobs:
      create-release:
        steps:
          - name: Build stuff
            run: echo "building"
    """
    errors = module.validate_release_workflow_content(
        release_workflow=textwrap.dedent(release_workflow)
    )
    joined_errors = "\n".join(errors)
    assert (
        "Release workflow create-release job must include step `Generate Rust SBOM`"
        in joined_errors
    )
    assert (
        "Release workflow create-release job must include step `Generate Python SBOM`"
        in joined_errors
    )
    assert (
        "Release workflow create-release job must include step `Sign artifacts with Sigstore`"
        in joined_errors
    )
    assert (
        "Release workflow create-release job must include step `Generate SLSA Provenance`"
        in joined_errors
    )


def test_should_require_release_binary_artifact_validation_flags():
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
          - name: Validate release binary artifact matrix and generate checksums
            run: uv run python scripts/validate_release_binary_artifacts.py
          - name: Build package-manager publish bundle
            run: uv run python scripts/prepare_package_manager_release.py --output-dir artifacts/package-manager-bundle
          - name: Verify package-manager bundle checksums
            run: uv run python scripts/verify_package_manager_bundle_checksums.py --bundle-dir artifacts/package-manager-bundle
          - name: Smoke-test package-manager bundle contracts
            run: uv run python scripts/smoke_test_package_manager_bundle.py --bundle-dir artifacts/package-manager-bundle
          - name: Smoke-verify Linux release binary version
            run: uv run python scripts/smoke_verify_release_binary.py --artifacts-dir artifacts --expected-version "${GITHUB_REF#refs/tags/v}"
      verify-release-assets:
        needs: create-release
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
        "create-release `Validate release binary artifact matrix and generate checksums` step must pass `--artifacts-dir`"
        in err
        for err in errors
    )
    assert any(
        "create-release `Validate release binary artifact matrix and generate checksums` step must pass `--checksums-out`"
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
        "publish-npm `Verify npm registry parity for release version` step must include `--expected-tag`"
        in err
        for err in errors
    )
    assert any(
        "release-success-gate `Verify final npm parity before release success gate` step must include `--check-npm`"
        in err
        for err in errors
    )
    assert any(
        "release-success-gate `Verify final npm parity before release success gate` step must include `--expected-tag`"
        in err
        for err in errors
    )
    assert any(
        "release-success-gate `Verify final PyPI parity before release success gate` step must include `--check-pypi`"
        in err
        for err in errors
    )
    assert any(
        "release-success-gate `Verify final PyPI parity before release success gate` step must include `--expected-tag`"
        in err
        for err in errors
    )


def test_should_require_release_parity_step_presence_for_publish_npm_and_release_success_gate():
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
            run: python scripts/validate_release_version_parity.py --expected-version "${GITHUB_REF#refs/tags/v}" --expected-tag "${GITHUB_REF#refs/tags/}"
      publish-docs:
        needs: validate-tag-version-parity
      publish-npm:
        needs: validate-tag-version-parity
        steps:
          - name: Verify Version Match
            run: echo "ok"
      release-success-gate:
        needs: [validate-tag-version-parity, publish-npm, publish-docs]
        steps:
          - name: Confirm release publication gates
            run: echo "ok"
    """
    errors = module.validate_release_workflow_content(release_workflow=release_workflow)
    assert any(
        "publish-npm job must include step `Verify npm registry parity for release version`" in err
        for err in errors
    )
    assert any(
        "release-success-gate job must include step `Verify final npm parity before release success gate`"
        in err
        for err in errors
    )
    assert any(
        "release-success-gate job must include step `Verify final PyPI parity before release success gate`"
        in err
        for err in errors
    )


def test_should_require_validate_pypi_artifacts_job_step_flags():
    root = Path(__file__).resolve().parents[2]
    script_path = root / "scripts" / "validate_release_assets.py"
    spec = importlib.util.spec_from_file_location("validate_release_assets", script_path)
    assert spec is not None and spec.loader is not None

    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)

    ci_workflow = """
    jobs:
      validate-pypi-artifacts:
        steps:
          - name: Download all distributions
            uses: actions/download-artifact@v8
            with:
              pattern: pypi-*
              path: dist
              merge-multiple: true
          - name: Validate built PyPI artifact set
            run: |
              python scripts/validate_pypi_artifacts.py \
                --dist-dir dist
          - name: Smoke-test install from built PyPI artifacts
            run: |
              python scripts/smoke_test_pypi_artifacts.py \
                --dist-dir dist
    """
    errors = module.validate_ci_workflow_content(ci_workflow=ci_workflow)
    assert any(
        "validate-pypi-artifacts `Validate built PyPI artifact set` step must include `--version`"
        in err
        for err in errors
    )
    assert any(
        "validate-pypi-artifacts `Validate built PyPI artifact set` step must include `--require-platforms`"
        in err
        for err in errors
    )
    assert any(
        "validate-pypi-artifacts `Smoke-test install from built PyPI artifacts` step must include `--version`"
        in err
        for err in errors
    )
    assert any(
        "validate-pypi-artifacts `Smoke-test install from built PyPI artifacts` step must include `--work-dir`"
        in err
        for err in errors
    )


def test_should_require_validate_pypi_artifacts_job_step_commands():
    root = Path(__file__).resolve().parents[2]
    script_path = root / "scripts" / "validate_release_assets.py"
    spec = importlib.util.spec_from_file_location("validate_release_assets", script_path)
    assert spec is not None and spec.loader is not None

    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)

    ci_workflow = """
    jobs:
      validate-pypi-artifacts:
        steps:
          - name: Validate built PyPI artifact set
            run: |
              python scripts/check_dist.py \
                --dist-dir dist \
                --version "${{ needs.release.outputs.release_version }}" \
                --require-platforms "linux,macos,windows"
          - name: Smoke-test install from built PyPI artifacts
            run: |
              python scripts/install_from_dist.py \
                --dist-dir dist \
                --version "${{ needs.release.outputs.release_version }}" \
                --work-dir .tmp
    """
    errors = module.validate_ci_workflow_content(ci_workflow=ci_workflow)
    assert any(
        "validate-pypi-artifacts `Validate built PyPI artifact set` step must invoke `scripts/validate_pypi_artifacts.py`"
        in err
        for err in errors
    )
    assert any(
        "validate-pypi-artifacts `Smoke-test install from built PyPI artifacts` step must invoke `scripts/smoke_test_pypi_artifacts.py`"
        in err
        for err in errors
    )


def test_should_require_release_validate_package_managers_step_commands():
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
            run: uv run python scripts/build_bundle.py --output-dir artifacts/package-manager-bundle
          - name: Preflight verify package-manager bundle checksums
            run: uv run python scripts/check_bundle.py --bundle-dir artifacts/package-manager-bundle
          - name: Preflight smoke-test package-manager bundle contracts
            run: uv run python scripts/check_bundle_smoke.py --bundle-dir artifacts/package-manager-bundle
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
      release-success-gate:
        needs: [validate-tag-version-parity, publish-npm, publish-docs]
    """
    errors = module.validate_release_workflow_content(release_workflow=release_workflow)
    assert any(
        "validate-package-managers `Preflight build package-manager publish bundle artifact` step must invoke `scripts/prepare_package_manager_release.py`"
        in err
        for err in errors
    )
    assert any(
        "validate-package-managers `Preflight verify package-manager bundle checksums` step must invoke `scripts/verify_package_manager_bundle_checksums.py`"
        in err
        for err in errors
    )
    assert any(
        "validate-package-managers `Preflight smoke-test package-manager bundle contracts` step must invoke `scripts/smoke_test_package_manager_bundle.py`"
        in err
        for err in errors
    )


def test_should_require_release_validate_package_manager_source_state_command():
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
          - name: Validate package-manager publish bundle source state
            run: uv run python scripts/prepare_bundle.py
          - name: Preflight build package-manager publish bundle artifact
            run: uv run python scripts/prepare_package_manager_release.py --output-dir artifacts/package-manager-bundle
          - name: Preflight verify package-manager bundle checksums
            run: uv run python scripts/verify_package_manager_bundle_checksums.py --bundle-dir artifacts/package-manager-bundle
          - name: Preflight smoke-test package-manager bundle contracts
            run: uv run python scripts/smoke_test_package_manager_bundle.py --bundle-dir artifacts/package-manager-bundle
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
      release-success-gate:
        needs: [validate-tag-version-parity, publish-npm, publish-docs]
    """
    errors = module.validate_release_workflow_content(release_workflow=release_workflow)
    assert any(
        "validate-package-managers `Validate package-manager publish bundle source state` step must invoke `scripts/prepare_package_manager_release.py`"
        in err
        for err in errors
    )
    assert any(
        "validate-package-managers `Validate package-manager publish bundle source state` step must pass `--check`"
        in err
        for err in errors
    )


def test_should_require_release_publish_docs_step_commands():
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
          - name: Validate package-manager publish bundle source state
            run: uv run python scripts/prepare_package_manager_release.py --check
          - name: Preflight build package-manager publish bundle artifact
            run: uv run python scripts/prepare_package_manager_release.py --output-dir artifacts/package-manager-bundle
          - name: Preflight verify package-manager bundle checksums
            run: uv run python scripts/verify_package_manager_bundle_checksums.py --bundle-dir artifacts/package-manager-bundle
          - name: Preflight smoke-test package-manager bundle contracts
            run: uv run python scripts/smoke_test_package_manager_bundle.py --bundle-dir artifacts/package-manager-bundle
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
        steps:
          - name: Install docs
            run: pip install mkdocs
          - name: Deploy Docs
            run: mkdocs build
      publish-npm:
        needs: validate-tag-version-parity
      release-success-gate:
        needs: [validate-tag-version-parity, publish-npm, publish-docs]
    """
    errors = module.validate_release_workflow_content(release_workflow=release_workflow)
    assert any("publish-docs job must include step `Install mkdocs`" in err for err in errors)
    assert any(
        "publish-docs `Deploy Docs` step must invoke `mkdocs gh-deploy --force`" in err
        for err in errors
    )


def test_should_require_release_publish_npm_prepublish_commands():
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
          - name: Validate package-manager publish bundle source state
            run: uv run python scripts/prepare_package_manager_release.py --check
          - name: Preflight build package-manager publish bundle artifact
            run: uv run python scripts/prepare_package_manager_release.py --output-dir artifacts/package-manager-bundle
          - name: Preflight verify package-manager bundle checksums
            run: uv run python scripts/verify_package_manager_bundle_checksums.py --bundle-dir artifacts/package-manager-bundle
          - name: Preflight smoke-test package-manager bundle contracts
            run: uv run python scripts/smoke_test_package_manager_bundle.py --bundle-dir artifacts/package-manager-bundle
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
        steps:
          - name: Install mkdocs
            run: pip install mkdocs-material
          - name: Deploy Docs
            run: mkdocs gh-deploy --force
      publish-npm:
        needs: validate-tag-version-parity
        steps:
          - name: Verify Version Match
            run: echo "ok"
          - name: Publish NPM Package
            run: npm pack
          - name: Verify npm registry parity for release version
            run: python scripts/validate_release_version_parity.py --expected-version "${GITHUB_REF#refs/tags/v}" --expected-tag "${GITHUB_REF#refs/tags/}" --check-npm --npm-wait-seconds 180 --npm-poll-interval-seconds 10
      release-success-gate:
        needs: [validate-tag-version-parity, publish-npm, publish-docs]
    """
    errors = module.validate_release_workflow_content(release_workflow=release_workflow)
    assert any(
        "publish-npm `Verify Version Match` step must invoke `node -p \"require('./npm/package.json').version\"`"
        in err
        for err in errors
    )
    assert any(
        "publish-npm `Publish NPM Package` step must invoke `npm publish --access public`" in err
        for err in errors
    )


def test_should_require_release_create_github_release_step_contract():
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
          - name: Validate package-manager publish bundle source state
            run: uv run python scripts/prepare_package_manager_release.py --check
          - name: Preflight build package-manager publish bundle artifact
            run: uv run python scripts/prepare_package_manager_release.py --output-dir artifacts/package-manager-bundle
          - name: Preflight verify package-manager bundle checksums
            run: uv run python scripts/verify_package_manager_bundle_checksums.py --bundle-dir artifacts/package-manager-bundle
          - name: Preflight smoke-test package-manager bundle contracts
            run: uv run python scripts/smoke_test_package_manager_bundle.py --bundle-dir artifacts/package-manager-bundle
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
          - name: Create GitHub Release
            uses: softprops/action-gh-release@v1
            with:
              files: |
                artifacts/**/tg-*
              generate_release_notes: false
      verify-release-assets:
        needs: create-release
      validate-tag-version-parity:
        needs: verify-release-assets
      publish-docs:
        needs: validate-tag-version-parity
        steps:
          - name: Install mkdocs
            run: pip install mkdocs-material
          - name: Deploy Docs
            run: mkdocs gh-deploy --force
      publish-npm:
        needs: validate-tag-version-parity
        steps:
          - name: Verify Version Match
            run: |
              TAG_VERSION=${GITHUB_REF#refs/tags/v}
              NPM_VERSION=$(node -p "require('./npm/package.json').version")
              if [ "$TAG_VERSION" != "$NPM_VERSION" ]; then
                exit 1
              fi
          - name: Publish NPM Package
            run: npm publish --access public
          - name: Verify npm registry parity for release version
            run: python scripts/validate_release_version_parity.py --expected-version "${GITHUB_REF#refs/tags/v}" --expected-tag "${GITHUB_REF#refs/tags/}" --check-npm --npm-wait-seconds 180 --npm-poll-interval-seconds 10
      release-success-gate:
        needs: [validate-tag-version-parity, publish-npm, publish-docs]
    """
    errors = module.validate_release_workflow_content(release_workflow=release_workflow)
    assert any(
        "create-release `Create GitHub Release` step must use `softprops/action-gh-release@v3`"
        in err
        for err in errors
    )
    assert any(
        "create-release `Create GitHub Release` step must include `artifacts/CHECKSUMS.txt`" in err
        for err in errors
    )
    assert any(
        "create-release `Create GitHub Release` step must include `artifacts/package-manager-bundle/**`"
        in err
        for err in errors
    )
    assert any(
        "create-release `Create GitHub Release` step must set `generate_release_notes: true`" in err
        for err in errors
    )


def test_should_require_release_validate_tag_version_parity_setup_and_command():
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
          - name: Validate package-manager publish bundle source state
            run: uv run python scripts/prepare_package_manager_release.py --check
          - name: Preflight build package-manager publish bundle artifact
            run: uv run python scripts/prepare_package_manager_release.py --output-dir artifacts/package-manager-bundle
          - name: Preflight verify package-manager bundle checksums
            run: uv run python scripts/verify_package_manager_bundle_checksums.py --bundle-dir artifacts/package-manager-bundle
          - name: Preflight smoke-test package-manager bundle contracts
            run: uv run python scripts/smoke_test_package_manager_bundle.py --bundle-dir artifacts/package-manager-bundle
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
          - name: Create GitHub Release
            uses: softprops/action-gh-release@v3
            with:
              files: |
                artifacts/**/tg-*
                artifacts/CHECKSUMS.txt
                artifacts/package-manager-bundle/**
              generate_release_notes: true
      verify-release-assets:
        needs: create-release
      validate-tag-version-parity:
        needs: verify-release-assets
        steps:
          - name: Validate release tag/version parity across package metadata
            run: python scripts/check_release_parity.py --expected-version "${GITHUB_REF#refs/tags/v}"
      publish-docs:
        needs: validate-tag-version-parity
        steps:
          - name: Install mkdocs
            run: pip install mkdocs-material
          - name: Deploy Docs
            run: mkdocs gh-deploy --force
      publish-npm:
        needs: validate-tag-version-parity
        steps:
          - name: Verify Version Match
            run: |
              TAG_VERSION=${GITHUB_REF#refs/tags/v}
              NPM_VERSION=$(node -p "require('./npm/package.json').version")
              if [ "$TAG_VERSION" != "$NPM_VERSION" ]; then
                exit 1
              fi
          - name: Publish NPM Package
            run: npm publish --access public
          - name: Verify npm registry parity for release version
            run: python scripts/validate_release_version_parity.py --expected-version "${GITHUB_REF#refs/tags/v}" --expected-tag "${GITHUB_REF#refs/tags/}" --check-npm --npm-wait-seconds 180 --npm-poll-interval-seconds 10
      release-success-gate:
        needs: [validate-tag-version-parity, publish-npm, publish-docs]
    """
    errors = module.validate_release_workflow_content(release_workflow=release_workflow)
    assert any(
        "validate-tag-version-parity job must include step `Install uv`" in err for err in errors
    )
    assert any(
        "validate-tag-version-parity job must include step `Setup Python`" in err for err in errors
    )
    assert any(
        "validate-tag-version-parity `Validate release tag/version parity across package metadata` step must invoke `scripts/validate_release_version_parity.py`"
        in err
        for err in errors
    )
    assert any(
        "validate-tag-version-parity `Validate release tag/version parity across package metadata` step must include `--expected-tag`"
        in err
        for err in errors
    )


def test_should_require_release_publish_npm_setup_node_contract():
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
          - name: Validate package-manager publish bundle source state
            run: uv run python scripts/prepare_package_manager_release.py --check
          - name: Preflight build package-manager publish bundle artifact
            run: uv run python scripts/prepare_package_manager_release.py --output-dir artifacts/package-manager-bundle
          - name: Preflight verify package-manager bundle checksums
            run: uv run python scripts/verify_package_manager_bundle_checksums.py --bundle-dir artifacts/package-manager-bundle
          - name: Preflight smoke-test package-manager bundle contracts
            run: uv run python scripts/smoke_test_package_manager_bundle.py --bundle-dir artifacts/package-manager-bundle
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
          - name: Create GitHub Release
            uses: softprops/action-gh-release@v3
            with:
              files: |
                artifacts/**/tg-*
                artifacts/CHECKSUMS.txt
                artifacts/package-manager-bundle/**
              generate_release_notes: true
      verify-release-assets:
        needs: create-release
      validate-tag-version-parity:
        needs: verify-release-assets
        steps:
          - name: Install uv
            uses: astral-sh/setup-uv@v8.0.0
          - name: Setup Python
            run: uv python install 3.12
          - name: Validate release tag/version parity across package metadata
            run: python scripts/validate_release_version_parity.py --expected-version "${GITHUB_REF#refs/tags/v}" --expected-tag "${GITHUB_REF#refs/tags/}"
      publish-docs:
        needs: validate-tag-version-parity
        steps:
          - name: Install mkdocs
            run: pip install mkdocs-material
          - name: Deploy Docs
            run: mkdocs gh-deploy --force
      publish-npm:
        needs: validate-tag-version-parity
        steps:
          - name: Setup Node.js
            uses: actions/setup-node@v3
            with:
              node-version: '22'
          - name: Verify Version Match
            run: |
              TAG_VERSION=${GITHUB_REF#refs/tags/v}
              NPM_VERSION=$(node -p "require('./npm/package.json').version")
              if [ "$TAG_VERSION" != "$NPM_VERSION" ]; then
                exit 1
              fi
          - name: Publish NPM Package
            run: npm publish --access public
          - name: Verify npm registry parity for release version
            run: python scripts/validate_release_version_parity.py --expected-version "${GITHUB_REF#refs/tags/v}" --expected-tag "${GITHUB_REF#refs/tags/}" --check-npm --npm-wait-seconds 180 --npm-poll-interval-seconds 10
      release-success-gate:
        needs: [validate-tag-version-parity, publish-npm, publish-docs]
    """
    errors = module.validate_release_workflow_content(release_workflow=release_workflow)
    assert any(
        "publish-npm `Setup Node.js` step must use `actions/setup-node@v6`" in err for err in errors
    )
    assert any(
        "publish-npm `Setup Node.js` step must include `registry-url: https://registry.npmjs.org`"
        in err
        for err in errors
    )


def test_should_require_release_build_binaries_step_contracts():
    root = Path(__file__).resolve().parents[2]
    script_path = root / "scripts" / "validate_release_assets.py"
    spec = importlib.util.spec_from_file_location("validate_release_assets", script_path)
    assert spec is not None and spec.loader is not None

    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)

    release_workflow = _detag(
        (root / ".github" / "workflows" / "release.yml").read_text(encoding="utf-8")
    )
    build_binaries_prefix, build_binaries_rest = release_workflow.split("  build-binaries:", 1)
    build_binaries_section, remainder = build_binaries_rest.split("  create-release:", 1)
    build_binaries_section = build_binaries_section.replace(
        "astral-sh/setup-uv@v8.0.0",
        "astral-sh/setup-uv@v4.0.0",
        1,
    )
    build_binaries_section = build_binaries_section.replace(
        "uv python install 3.12",
        "python -V",
        1,
    )
    release_workflow = (
        build_binaries_prefix
        + "  build-binaries:"
        + build_binaries_section
        + "  create-release:"
        + remainder
    )
    release_workflow = release_workflow.replace(
        'uv pip install -e ".[dev]"',
        'uv pip install -e "."',
        1,
    )
    release_workflow = release_workflow.replace(
        "uv pip install torch torchvision torchaudio --index-url https://download.pytorch.org/whl/cu128",
        "uv pip install torch torchvision torchaudio",
        1,
    )
    release_workflow = release_workflow.replace(
        'uv pip install -e ".[gpu-win,nlp,ast,dev]"',
        'uv pip install -e ".[dev]"',
        1,
    )
    release_workflow = release_workflow.replace(
        "uv run python scripts/build_binaries.py",
        "python scripts/build.py",
        1,
    )
    release_workflow = release_workflow.replace(
        "mv tg.exe tg-windows-amd64-${{ matrix.gpu }}.exe",
        "mv tg.exe tg.exe",
        1,
    )
    release_workflow = release_workflow.replace(
        "mv tg tg-linux-amd64-${{ matrix.gpu }}",
        "mv tg tg-linux",
        1,
    )
    release_workflow = release_workflow.replace(
        "mv tg tg-macos-amd64-${{ matrix.gpu }}",
        "mv tg tg-macos",
        1,
    )
    release_workflow = release_workflow.replace(
        r".\tg-windows-amd64-${{ matrix.gpu }}.exe --version",
        r".\tg.exe --version",
        1,
    )
    release_workflow = release_workflow.replace(
        "./tg-linux-amd64-${{ matrix.gpu }} --version",
        "./tg --version",
        1,
    )
    release_workflow = release_workflow.replace(
        "./tg-macos-amd64-${{ matrix.gpu }} --version",
        "./tg --version",
        1,
    )
    release_workflow = release_workflow.replace(
        "actions/upload-artifact@v7", "actions/upload-artifact@v3", 1
    )
    release_workflow = release_workflow.replace("path: tg-*", "path: dist/*", 1)
    errors = module.validate_release_workflow_content(
        release_workflow=textwrap.dedent(release_workflow)
    )
    joined_errors = "\n".join(errors)
    assert "build-binaries `Install uv` step must use `astral-sh/setup-uv@v8.0.0`" in joined_errors
    assert (
        "build-binaries `Set up Python` step must invoke `uv python install 3.12`" in joined_errors
    )
    assert (
        "build-binaries `Build Binary` step must invoke `scripts/build_binaries.py`"
        in joined_errors
    )
    assert (
        'build-binaries `Install dependencies (CPU)` step must invoke `uv pip install -e ".[dev]"`'
        in joined_errors
    )
    assert (
        "build-binaries `Install dependencies (NVIDIA)` step must invoke "
        "`uv pip install torch torchvision torchaudio --index-url https://download.pytorch.org/whl/cu128`"
        in joined_errors
    )
    assert (
        'build-binaries `Install dependencies (NVIDIA)` step must invoke `uv pip install -e ".[gpu-win,nlp,ast,dev]"`'
        in joined_errors
    )
    assert (
        "build-binaries `Upload Artifact` step must use `actions/upload-artifact@v7`"
        in joined_errors
    )
    assert "build-binaries `Upload Artifact` step must include `path: tg-*`" in joined_errors
    assert (
        "build-binaries `Rename Artifact (Windows)` step must invoke "
        "`mv tg.exe tg-windows-amd64-${{ matrix.gpu }}.exe`" in joined_errors
    )
    assert (
        "build-binaries `Rename Artifact (Linux)` step must invoke "
        "`mv tg tg-linux-amd64-${{ matrix.gpu }}`" in joined_errors
    )
    assert (
        "build-binaries `Rename Artifact (macOS)` step must invoke "
        "`mv tg tg-macos-amd64-${{ matrix.gpu }}`" in joined_errors
    )
    assert "build-binaries `Smoke-test Binary (Windows)` step must invoke" in joined_errors
    assert "tg-windows-amd64-${{ matrix.gpu }}.exe --version" in joined_errors
    assert (
        "build-binaries `Smoke-test Binary (Linux)` step must invoke "
        "`./tg-linux-amd64-${{ matrix.gpu }} --version`" in joined_errors
    )
    assert (
        "build-binaries `Smoke-test Binary (macOS)` step must invoke "
        "`./tg-macos-amd64-${{ matrix.gpu }} --version`" in joined_errors
    )
