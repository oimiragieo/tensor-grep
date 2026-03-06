from __future__ import annotations

import json
import re
import tomllib
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[1]


def _read(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def _version_from_pyproject() -> str:
    data = tomllib.loads(_read(ROOT / "pyproject.toml"))
    return str(data["project"]["version"])


def _version_from_cargo() -> str:
    content = _read(ROOT / "rust_core" / "Cargo.toml")
    match = re.search(r'(?m)^version\s*=\s*"([^"]+)"\s*$', content)
    if not match:
        raise ValueError("Missing rust_core/Cargo.toml package version")
    return match.group(1)


def _version_from_npm() -> str:
    data = json.loads(_read(ROOT / "npm" / "package.json"))
    return str(data["version"])


def validate_winget_manifest(*, winget_content: str, py_version: str) -> list[str]:
    errors: list[str] = []
    if f"PackageVersion: {py_version}" not in winget_content:
        errors.append("Winget manifest PackageVersion does not match pyproject version")
    expected_windows_url = (
        f"https://github.com/oimiragieo/tensor-grep/releases/download/v{py_version}/"
        "tg-windows-amd64-cpu.exe"
    )
    if expected_windows_url not in winget_content:
        errors.append("Winget manifest InstallerUrl does not match expected release artifact URL")
    if "PLACEHOLDER" in winget_content:
        errors.append("Winget manifest contains unresolved PLACEHOLDER text")
    try:
        parsed_winget = yaml.safe_load(winget_content) or {}
    except yaml.YAMLError as exc:
        errors.append(f"Winget manifest is not valid YAML: {exc}")
        parsed_winget = {}
    if not isinstance(parsed_winget, dict):
        errors.append("Winget manifest must deserialize to a mapping")
        return errors

    installers = parsed_winget.get("Installers")
    if not isinstance(installers, list) or not installers:
        errors.append("Winget manifest must contain a non-empty Installers list")
        return errors

    first = installers[0]
    if not isinstance(first, dict):
        errors.append("Winget manifest first installer must be a mapping")
        return errors

    installer_url = first.get("InstallerUrl")
    if installer_url != expected_windows_url:
        errors.append("Winget manifest InstallerUrl must be nested under first installer mapping")
    return errors


def validate_ci_workflow_content(*, ci_workflow: str) -> list[str]:
    errors: list[str] = []
    for expected in (
        "package-manager-readiness:",
        "Validate Homebrew formula syntax",
        "Validate winget manifest syntax",
        "Validate package-manager publish bundle source state",
        "scripts/prepare_package_manager_release.py --check",
        "Build package-manager publish bundle artifact",
        "scripts/prepare_package_manager_release.py --output-dir artifacts/package-manager-bundle",
        "--output-dir artifacts/package-manager-bundle",
        "Verify package-manager publish bundle checksums",
        "scripts/verify_package_manager_bundle_checksums.py --bundle-dir artifacts/package-manager-bundle",
        "--bundle-dir artifacts/package-manager-bundle",
        "Upload package-manager bundle artifact",
        "package-manager-bundle-${{ matrix.os }}",
        "validate-pypi-artifacts:",
        "Validate built PyPI artifact set",
        "Smoke-test install from built PyPI artifacts",
        "Install Dependencies (Unix with retry)",
        "Install Dependencies (Windows with retry)",
        "Dependency install failed after 3 attempts.",
        "publish-success-gate:",
        "Confirm publish job result when publishing is required",
        "Verify PyPI parity for semantic-release version (always)",
        "Skip publish parity gate when semantic-release produced no version",
        "Verify release version parity across tag/assets/PyPI",
        "scripts/validate_release_version_parity.py",
    ):
        if expected not in ci_workflow:
            errors.append(
                f"CI workflow missing expected package-manager validation block: {expected}"
            )

    if "ref: v${{ needs.release.outputs.release_version }}" not in ci_workflow:
        errors.append("CI workflow must build PyPI artifacts from semantic-release tag ref")

    if (
        "needs: [release, build-wheels-pypi, build-sdist-pypi, validate-pypi-artifacts]"
        not in ci_workflow
    ):
        errors.append(
            "publish-pypi must depend on validate-pypi-artifacts before uploading to PyPI"
        )

    if ci_workflow.count("uses: astral-sh/setup-uv@v5") < 2:
        errors.append("CI workflow should install uv in package-manager/release validation paths")

    if "--pypi-wait-seconds" not in ci_workflow:
        errors.append("CI workflow must pass --pypi-wait-seconds to release parity validation")

    if "--pypi-poll-interval-seconds" not in ci_workflow:
        errors.append(
            "CI workflow must pass --pypi-poll-interval-seconds to release parity validation"
        )

    if "needs: [release, publish-pypi]" not in ci_workflow:
        errors.append("CI workflow publish-success-gate must depend on release + publish-pypi")

    if "if: always()" not in ci_workflow:
        errors.append("CI workflow publish-success-gate must run with if: always()")

    if "if: needs.release.outputs.release_version == ''" not in ci_workflow:
        errors.append(
            "CI workflow publish-success-gate must explicitly handle empty release_version output"
        )

    if "if: needs.release.outputs.release_version != ''" not in ci_workflow:
        errors.append(
            "CI workflow publish-success-gate must guard checkout/parity steps behind non-empty release_version"
        )

    try:
        parsed_ci = yaml.safe_load(ci_workflow) or {}
    except yaml.YAMLError:
        parsed_ci = {}
    if isinstance(parsed_ci, dict):
        jobs = parsed_ci.get("jobs")
        if isinstance(jobs, dict):
            release_job = jobs.get("release")
            if isinstance(release_job, dict):
                needs = release_job.get("needs", [])
                if isinstance(needs, str):
                    needs_list = [needs]
                elif isinstance(needs, list):
                    needs_list = [str(item) for item in needs]
                else:
                    needs_list = []
                if "benchmark-regression" not in needs_list:
                    errors.append("CI workflow release job must depend on benchmark-regression")

    if "--skip-package-managers" in ci_workflow:
        errors.append("CI workflow parity validation must not skip package-manager version checks")

    if "publish-pypi:" in ci_workflow:
        if "name: pypi" not in ci_workflow:
            errors.append("CI workflow publish-pypi job must target `environment: pypi`")
        if "url: https://pypi.org/p/tensor-grep" not in ci_workflow:
            errors.append(
                "CI workflow publish-pypi job should set canonical PyPI project URL for deployment visibility"
            )
        if "id-token: write" not in ci_workflow:
            errors.append("CI workflow publish-pypi job must request `id-token: write` permission")
        if "uses: pypa/gh-action-pypi-publish@release/v1" not in ci_workflow:
            errors.append(
                "CI workflow publish-pypi job must use pypa/gh-action-pypi-publish@release/v1"
            )
        if "skip-existing: true" not in ci_workflow:
            errors.append(
                "CI workflow publish-pypi job should pass `skip-existing: true` to avoid duplicate-upload failures"
            )

    if "uv run ruff format --check --preview ." not in ci_workflow:
        errors.append(
            "CI workflow must run formatter with `ruff format --check --preview .` to keep local/CI formatting semantics aligned"
        )

    return errors


def validate_package_manager_docs(*, runbook_content: str, checklist_content: str) -> list[str]:
    errors: list[str] = []
    for heading in (
        "## Homebrew Tap Flow",
        "## Winget Flow",
        "## Rollback Procedures",
        "## Verification Commands",
    ):
        if heading not in runbook_content:
            errors.append(f"Package manager runbook missing required heading: {heading}")

    for marker in (
        "## 4. Package-manager distribution finalization",
        "## 5. Rollback runbook",
        "Homebrew",
        "Winget",
    ):
        if marker not in checklist_content:
            errors.append(f"Release checklist missing package-manager marker: {marker}")

    for required_cmd in (
        "uv run python scripts/prepare_package_manager_release.py --check",
        "winget validate --manifest",
        "uv run python scripts/verify_package_manager_bundle_checksums.py --bundle-dir",
        "python scripts/validate_release_version_parity.py --expected-version X.Y.Z --expected-tag vX.Y.Z --check-npm",
    ):
        if required_cmd not in runbook_content:
            errors.append(
                f"Package manager runbook missing required verification/publish command: {required_cmd}"
            )

    for required_smoke_cmd in (
        "brew install oimiragieo/tap/tensor-grep",
        "winget install oimiragieo.tensor-grep",
        "tg --version",
    ):
        if required_smoke_cmd not in runbook_content:
            errors.append(
                f"Package manager runbook missing required smoke-install command: {required_smoke_cmd}"
            )

    if "npm/GitHub mismatch" not in runbook_content:
        errors.append("Package manager runbook missing npm/GitHub rollback guidance")
    return errors


def validate_installation_docs(*, installation_content: str) -> list[str]:
    errors: list[str] = []
    for expected in (
        "### Homebrew Tap Flow",
        "### Winget Flow",
        "### Repeatable Release Checklist",
        "### Rollback Playbook",
    ):
        if expected not in installation_content:
            errors.append(f"Installation docs missing package-manager section: {expected}")

    if "https://github.com/oimiragieo/tensor-grep/releases" not in installation_content:
        errors.append("Installation docs must point GitHub Releases link to oimiragieo/tensor-grep")

    if "--check-npm" not in installation_content:
        errors.append("Installation docs release automation notes must mention npm parity checks")
    return errors


def validate_release_workflow_content(*, release_workflow: str) -> list[str]:
    errors: list[str] = []
    for expected in (
        "on:",
        "tags:",
        "- 'v*'",
        "validate-release-assets:",
        "validate-package-managers:",
        "build-binaries:",
        "create-release:",
        "verify-release-assets:",
        "validate-tag-version-parity:",
        "publish-npm:",
        "Verify npm registry parity for release version",
        "--check-npm",
        "publish-docs:",
        "release-success-gate:",
        "Verify final npm parity before release success gate",
        "Smoke-test Binary (Windows)",
        "Smoke-test Binary (Linux)",
        "Smoke-test Binary (macOS)",
        "Validate release binary artifact matrix and generate checksums",
        "Smoke-verify Linux release binary version",
        "Verify uploaded release assets and checksum coverage",
        "scripts/verify_github_release_assets.py",
        "scripts/smoke_verify_release_binary.py",
        "Validate release tag/version parity across package metadata",
        "scripts/validate_release_version_parity.py",
        "artifacts/CHECKSUMS.txt",
        "Build package-manager publish bundle",
        "Verify package-manager bundle checksums",
        "scripts/prepare_package_manager_release.py \\",
        "--output-dir artifacts/package-manager-bundle",
        "scripts/verify_package_manager_bundle_checksums.py \\",
        "--bundle-dir artifacts/package-manager-bundle",
        "artifacts/package-manager-bundle/**",
        "Validate package-manager publish bundle source state",
        "scripts/prepare_package_manager_release.py --check",
        "Confirm release publication gates",
    ):
        if expected not in release_workflow:
            errors.append(f"Release workflow missing expected job block: {expected.rstrip(':')}")

    try:
        parsed = yaml.safe_load(release_workflow) or {}
    except yaml.YAMLError as exc:
        errors.append(f"Release workflow is not valid YAML: {exc}")
        parsed = {}

    jobs = parsed.get("jobs", {}) if isinstance(parsed, dict) else {}
    if not isinstance(jobs, dict):
        errors.append("Release workflow must define jobs as a mapping")
        return errors

    def _needs(job_name: str) -> list[str]:
        job = jobs.get(job_name)
        if not isinstance(job, dict):
            return []
        needs = job.get("needs")
        if isinstance(needs, str):
            return [needs]
        if isinstance(needs, list):
            return [str(item) for item in needs]
        return []

    build_needs = _needs("build-binaries")
    if not {"validate-release-assets", "validate-package-managers"}.issubset(set(build_needs)):
        errors.append(
            "Release workflow build-binaries must depend on release/package-manager validators"
        )

    parity_needs = _needs("validate-tag-version-parity")
    if "verify-release-assets" not in parity_needs:
        errors.append(
            "Release workflow validate-tag-version-parity must depend on verify-release-assets"
        )

    docs_needs = _needs("publish-docs")
    if "validate-tag-version-parity" not in docs_needs:
        errors.append("Release workflow publish-docs must depend on validate-tag-version-parity")

    npm_needs = _needs("publish-npm")
    if "validate-tag-version-parity" not in npm_needs:
        errors.append("Release workflow publish-npm must depend on validate-tag-version-parity")

    release_gate_needs = _needs("release-success-gate")
    if not {"validate-tag-version-parity", "publish-npm", "publish-docs"}.issubset(
        set(release_gate_needs)
    ):
        errors.append(
            "Release workflow release-success-gate must depend on parity + publish-npm + publish-docs"
        )

    if "uses: astral-sh/setup-uv@v5" not in release_workflow:
        errors.append(
            "Release workflow package-manager validation must install uv before fallback checks"
        )
    if "--skip-pypi" in release_workflow:
        errors.append("Release workflow must not pass unsupported --skip-pypi flag")
    return errors


def validate_homebrew_formula_contract(*, brew_content: str, py_version: str) -> list[str]:
    errors: list[str] = []
    has_direct_version = f'version "{py_version}"' in brew_content
    has_constant_version = f'TENSOR_GREP_VERSION = "{py_version}"' in brew_content
    if not has_direct_version and not has_constant_version:
        errors.append("Homebrew formula version does not match pyproject version")

    if "TENSOR_GREP_VERSION =" not in brew_content:
        errors.append("Homebrew formula must use explicit TENSOR_GREP_VERSION assignment")

    if "version TENSOR_GREP_VERSION" not in brew_content:
        errors.append("Homebrew formula must declare `version TENSOR_GREP_VERSION`")

    return errors


def validate_all() -> list[str]:
    errors: list[str] = []
    py_version = _version_from_pyproject()
    cargo_version = _version_from_cargo()
    npm_manifest = json.loads(_read(ROOT / "npm" / "package.json"))
    npm_version = str(npm_manifest["version"])

    if cargo_version != py_version:
        errors.append(
            f"Version mismatch: rust_core/Cargo.toml={cargo_version} != pyproject={py_version}"
        )
    if npm_version != py_version:
        errors.append(f"Version mismatch: npm/package.json={npm_version} != pyproject={py_version}")

    npm_repository_url = str((npm_manifest.get("repository") or {}).get("url") or "")
    expected_npm_repo_url = "git+https://github.com/oimiragieo/tensor-grep.git"
    if npm_repository_url != expected_npm_repo_url:
        errors.append(
            "npm/package.json repository.url must be "
            f"{expected_npm_repo_url}, got {npm_repository_url or '<empty>'}"
        )

    winget_path = ROOT / "scripts" / "oimiragieo.tensor-grep.yaml"
    winget = _read(winget_path)
    errors.extend(validate_winget_manifest(winget_content=winget, py_version=py_version))

    brew = _read(ROOT / "scripts" / "tensor-grep.rb")
    errors.extend(validate_homebrew_formula_contract(brew_content=brew, py_version=py_version))
    expected_macos_url = f"https://github.com/oimiragieo/tensor-grep/releases/download/v{py_version}/tg-macos-amd64-cpu"
    expected_linux_url = f"https://github.com/oimiragieo/tensor-grep/releases/download/v{py_version}/tg-linux-amd64-cpu"
    templated_macos_url = (
        "https://github.com/oimiragieo/tensor-grep/releases/download/v#{version}/tg-macos-amd64-cpu"
    )
    templated_linux_url = (
        "https://github.com/oimiragieo/tensor-grep/releases/download/v#{version}/tg-linux-amd64-cpu"
    )
    if expected_macos_url not in brew and templated_macos_url not in brew:
        errors.append("Homebrew formula macOS URL does not match expected release artifact URL")
    if expected_linux_url not in brew and templated_linux_url not in brew:
        errors.append("Homebrew formula Linux URL does not match expected release artifact URL")
    if "PLACEHOLDER" in brew:
        errors.append("Homebrew formula contains unresolved PLACEHOLDER text")

    release_workflow = _read(ROOT / ".github" / "workflows" / "release.yml")
    errors.extend(validate_release_workflow_content(release_workflow=release_workflow))

    ci_workflow = _read(ROOT / ".github" / "workflows" / "ci.yml")
    errors.extend(validate_ci_workflow_content(ci_workflow=ci_workflow))

    package_manager_runbook = _read(ROOT / "docs" / "package_manager_publish.md")
    release_checklist = _read(ROOT / "docs" / "RELEASE_CHECKLIST.md")
    installation_docs = _read(ROOT / "docs" / "installation.md")
    errors.extend(
        validate_package_manager_docs(
            runbook_content=package_manager_runbook,
            checklist_content=release_checklist,
        )
    )
    errors.extend(validate_installation_docs(installation_content=installation_docs))

    pyproject_data = tomllib.loads(_read(ROOT / "pyproject.toml"))
    semantic_release = pyproject_data.get("tool", {}).get("semantic_release", {})
    build_command = str(semantic_release.get("build_command", ""))
    if "scripts/stamp_release_assets.py" not in build_command:
        errors.append(
            "semantic_release.build_command must run scripts/stamp_release_assets.py before build"
        )
    version_toml = semantic_release.get("version_toml", [])
    version_variables = semantic_release.get("version_variables", [])
    required_toml_entries = {
        "pyproject.toml:project.version",
        "rust_core/Cargo.toml:package.version",
    }
    required_variable_entries = {
        "src/tensor_grep/cli/main.py:pkg_version",
        "npm/package.json:version",
        "scripts/tensor-grep.rb:TENSOR_GREP_VERSION",
        "scripts/oimiragieo.tensor-grep.yaml:PackageVersion",
        "scripts/oimiragieo.tensor-grep.yaml:InstallerUrl",
    }
    missing_toml = sorted(required_toml_entries - set(version_toml))
    missing_variables = sorted(required_variable_entries - set(version_variables))
    if missing_toml:
        errors.append("semantic_release.version_toml missing entries: " + ", ".join(missing_toml))
    if missing_variables:
        errors.append(
            "semantic_release.version_variables missing entries: " + ", ".join(missing_variables)
        )

    return errors


def main() -> int:
    errors = validate_all()
    if errors:
        for err in errors:
            print(f"ERROR: {err}")
        return 1
    print("Release/package assets validation passed.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
