"""npm / Homebrew / package-manager runbook and installation docs."""

import importlib.util
from pathlib import Path


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


def test_should_fail_when_native_cli_version_is_hardcoded():
    root = Path(__file__).resolve().parents[2]
    script_path = root / "scripts" / "validate_release_assets.py"
    spec = importlib.util.spec_from_file_location("validate_release_assets", script_path)
    assert spec is not None and spec.loader is not None

    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)

    errors = module.validate_native_cli_contract(
        main_rs_content='#[command(version = "0.2.0")]\n#[command(version = "0.2.0")]\n',
        expected_version="1.4.2",
    )

    assert any(
        "must derive native CLI version from Cargo/package metadata" in err for err in errors
    )


def test_should_fail_when_npm_installer_contract_drifts():
    root = Path(__file__).resolve().parents[2]
    script_path = root / "scripts" / "validate_release_assets.py"
    spec = importlib.util.spec_from_file_location("validate_release_assets", script_path)
    assert spec is not None and spec.loader is not None

    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)

    errors = module.validate_npm_installer_contract(
        install_js_content=(
            "const VERSION = 'v0.2.0';\n"
            "const GITHUB_REPO = 'tensor-grep/tensor-grep';\n"
            "const binName = `tg-${platform}-${arch}${exeExt}`;\n"
        ),
        expected_version="1.4.2",
    )

    assert any("must derive the release version from npm/package.json" in err for err in errors)
    assert any("must download from oimiragieo/tensor-grep releases" in err for err in errors)
    assert any("must reference current release asset names" in err for err in errors)


def test_should_fail_when_npm_manifest_declares_js_main_entrypoint():
    root = Path(__file__).resolve().parents[2]
    script_path = root / "scripts" / "validate_release_assets.py"
    spec = importlib.util.spec_from_file_location("validate_release_assets", script_path)
    assert spec is not None and spec.loader is not None

    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)

    errors = module.validate_npm_manifest_contract(
        package_json_content='{"bin":{"tg":"bin/tg.js"},"main":"index.js"}',
        available_paths={"bin/tg.js", "install.js", "package.json"},
    )

    assert any("must not declare `main`" in err for err in errors)


def test_should_fail_when_npm_manifest_bin_target_is_missing():
    root = Path(__file__).resolve().parents[2]
    script_path = root / "scripts" / "validate_release_assets.py"
    spec = importlib.util.spec_from_file_location("validate_release_assets", script_path)
    assert spec is not None and spec.loader is not None

    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)

    errors = module.validate_npm_manifest_contract(
        package_json_content='{"bin":{"tg":"bin/tg.js"}}',
        available_paths={"install.js", "package.json"},
    )

    assert any("bin target must exist in npm/" in err for err in errors)


def test_should_fail_when_npm_manifest_declares_runtime_dependencies_for_wrapper():
    root = Path(__file__).resolve().parents[2]
    script_path = root / "scripts" / "validate_release_assets.py"
    spec = importlib.util.spec_from_file_location("validate_release_assets", script_path)
    assert spec is not None and spec.loader is not None

    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)

    errors = module.validate_npm_manifest_contract(
        package_json_content='{"bin":{"tg":"bin/tg.js"},"dependencies":{"axios":"^1.6.0"}}',
        available_paths={"bin/tg.js", "install.js", "package.json"},
    )

    assert any("wrapper runtime dependencies must be empty" in err for err in errors)


def test_should_accept_dependency_free_npm_wrapper_manifest():
    root = Path(__file__).resolve().parents[2]
    script_path = root / "scripts" / "validate_release_assets.py"
    spec = importlib.util.spec_from_file_location("validate_release_assets", script_path)
    assert spec is not None and spec.loader is not None

    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)

    errors = module.validate_npm_manifest_contract(
        package_json_content=(
            '{"bin":{"tensor-grep":"bin/tg.js","tg":"bin/tg.js"},"dependencies":{}}'
        ),
        available_paths={"bin/tg.js", "install.js", "package.json"},
    )

    assert errors == []


def test_should_fail_when_ci_pipeline_doc_omits_benchmark_workflow_contract():
    root = Path(__file__).resolve().parents[2]
    script_path = root / "scripts" / "validate_release_assets.py"
    spec = importlib.util.spec_from_file_location("validate_release_assets", script_path)
    assert spec is not None and spec.loader is not None

    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)

    errors = module.validate_ci_pipeline_doc_contract(
        ci_pipeline_content=(
            "# CI Pipeline\n\n## Workflow Overview\n\n### `ci.yml`\n\n- Semantic Release\n"
        ),
        benchmark_workflow_content="name: Benchmarks\non:\n  workflow_dispatch:\n",
    )

    assert any("must document the live benchmark workflow" in err for err in errors)


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
            "gh run list --limit 10\n"
            "python scripts/verify_github_release_assets.py --repo oimiragieo/tensor-grep --tag vX.Y.Z\n"
        ),
    )
    assert any("gh run list --limit 10" in err for err in errors)
    assert any("prepare_package_manager_release.py --check" in err for err in errors)
    assert any("winget validate --manifest" in err for err in errors)
    assert any("verify_package_manager_bundle_checksums.py --bundle-dir" in err for err in errors)
    assert any("ruby -c Formula/tensor-grep.rb" in err for err in errors)
    assert any(
        "winget validate --manifest .\\manifests\\o\\oimiragieo\\tensor-grep\\X.Y.Z\\" in err
        for err in errors
    )
    assert any(
        "verify_github_release_assets.py --repo oimiragieo/tensor-grep --tag vX.Y.Z" in err
        for err in errors
    )
    assert any("git revert <tap-formula-commit>" in err for err in errors)
    assert any("git push origin <rollback-branch>" in err for err in errors)
    assert any("brew update" in err for err in errors)
    assert any("winget uninstall oimiragieo.tensor-grep" in err for err in errors)
    assert any(
        "--expected-version X.Y.Z --expected-tag vX.Y.Z --check-pypi" in err for err in errors
    )
    assert any("npm/GitHub rollback guidance" in err for err in errors)


def test_should_require_release_checklist_to_include_operator_verification_commands():
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
            "uv run python scripts/verify_package_manager_bundle_checksums.py --bundle-dir artifacts/package-manager-bundle\n"
            "uv run python scripts/smoke_test_package_manager_bundle.py --bundle-dir artifacts/package-manager-bundle\n"
            "python scripts/validate_release_version_parity.py --expected-version X.Y.Z --expected-tag vX.Y.Z --check-pypi\n"
            "python scripts/validate_release_version_parity.py --expected-version X.Y.Z --expected-tag vX.Y.Z --check-npm\n"
            "brew install oimiragieo/tap/tensor-grep\n"
            "winget install oimiragieo.tensor-grep\n"
            "tg --version\n"
            "git revert <tap-formula-commit>\n"
            "winget uninstall oimiragieo.tensor-grep\n"
            "npm/GitHub mismatch\n"
        ),
        checklist_content=(
            "## 4. Package-manager distribution finalization\n"
            "## 5. Rollback runbook\n"
            "Homebrew\n"
            "Winget\n"
        ),
    )
    assert any("gh run list --limit 10" in err for err in errors)
    assert any(
        "verify_github_release_assets.py --repo oimiragieo/tensor-grep --tag vX.Y.Z" in err
        for err in errors
    )


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


def test_should_require_installation_docs_to_include_package_manager_commands():
    root = Path(__file__).resolve().parents[2]
    script_path = root / "scripts" / "validate_release_assets.py"
    spec = importlib.util.spec_from_file_location("validate_release_assets", script_path)
    assert spec is not None and spec.loader is not None

    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)

    errors = module.validate_installation_docs(
        installation_content=(
            "### Homebrew Tap Flow\n"
            "### Winget Flow\n"
            "### Repeatable Release Checklist\n"
            "### Rollback Playbook\n"
            "https://github.com/oimiragieo/tensor-grep/releases\n"
            "--check-npm\n"
        )
    )
    assert any("brew tap oimiragieo/tap" in err for err in errors)
    assert any("brew install tensor-grep" in err for err in errors)
    assert any("brew install oimiragieo/tap/tensor-grep" in err for err in errors)
    assert any("winget validate --manifest" in err for err in errors)
    assert any("winget-pkgs" in err for err in errors)
    assert any("winget install oimiragieo.tensor-grep" in err for err in errors)
    assert any("tg --version" in err for err in errors)


def test_should_validate_readme_canonical_docs_and_installation_contract():
    root = Path(__file__).resolve().parents[2]
    script_path = root / "scripts" / "validate_release_assets.py"
    spec = importlib.util.spec_from_file_location("validate_release_assets", script_path)
    assert spec is not None and spec.loader is not None

    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)

    errors = module.validate_readme_contract(
        readme_content=(
            "# tensor-grep\n"
            "`tensor-grep` has first class support on Windows, macOS and Linux.\n"
            "Harness consumers should use the documented public contracts in [docs/harness_api.md](docs/harness_api.md)\n"
            "and the workflow guide in [docs/harness_cookbook.md](docs/harness_cookbook.md).\n"
            "## Canonical Docs\n"
            "- [docs/benchmarks.md](docs/benchmarks.md)\n"
            "- [docs/tool_comparison.md](docs/tool_comparison.md)\n"
            "- [docs/gpu_crossover.md](docs/gpu_crossover.md)\n"
            "- [docs/routing_policy.md](docs/routing_policy.md)\n"
            "- [docs/harness_api.md](docs/harness_api.md)\n"
            "- [docs/harness_cookbook.md](docs/harness_cookbook.md)\n"
            "- [docs/installation.md](docs/installation.md)\n"
            "- [docs/RELEASE_CHECKLIST.md](docs/RELEASE_CHECKLIST.md)\n"
            "## Installation\n"
            "pip install tensor-grep\n"
            'uv pip install "tensor-grep[ast,nlp]"\n'
            'npx tensor-grep search "ERROR" .\n'
            "GitHub Releases page\n"
        )
    )
    assert errors == []


def test_should_require_readme_canonical_docs_and_installation_surfaces():
    root = Path(__file__).resolve().parents[2]
    script_path = root / "scripts" / "validate_release_assets.py"
    spec = importlib.util.spec_from_file_location("validate_release_assets", script_path)
    assert spec is not None and spec.loader is not None

    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)

    errors = module.validate_readme_contract(
        readme_content=(
            "# tensor-grep\n"
            "## Canonical Docs\n"
            "- [docs/benchmarks.md](docs/benchmarks.md)\n"
            "## Installation\n"
            "pip install tensor-grep\n"
        )
    )
    joined_errors = "\n".join(errors)
    assert "README missing canonical docs reference" in joined_errors
    assert "README must link installation docs" in joined_errors
    assert "README must state platform support" in joined_errors
    assert "README must direct harness consumers to docs/harness_api.md" in joined_errors


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
