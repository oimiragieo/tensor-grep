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
        "validate-pypi-artifacts:",
        "Validate built PyPI artifact set",
        "Smoke-test install from built PyPI artifacts",
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

    return errors


def validate_all() -> list[str]:
    errors: list[str] = []
    py_version = _version_from_pyproject()
    cargo_version = _version_from_cargo()
    npm_version = _version_from_npm()

    if cargo_version != py_version:
        errors.append(
            f"Version mismatch: rust_core/Cargo.toml={cargo_version} != pyproject={py_version}"
        )
    if npm_version != py_version:
        errors.append(f"Version mismatch: npm/package.json={npm_version} != pyproject={py_version}")

    winget_path = ROOT / "scripts" / "oimiragieo.tensor-grep.yaml"
    winget = _read(winget_path)
    errors.extend(validate_winget_manifest(winget_content=winget, py_version=py_version))

    brew = _read(ROOT / "scripts" / "tensor-grep.rb")
    if f'version "{py_version}"' not in brew:
        errors.append("Homebrew formula version does not match pyproject version")
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
    for expected in (
        "on:",
        "tags:",
        "- 'v*'",
        "validate-release-assets:",
        "validate-package-managers:",
        "build-binaries:",
        "create-release:",
        "publish-npm:",
        "publish-docs:",
        "Validate release binary artifact matrix and generate checksums",
        "artifacts/CHECKSUMS.txt",
    ):
        if expected not in release_workflow:
            errors.append(f"Release workflow missing expected job block: {expected.rstrip(':')}")
    if "needs: [validate-release-assets, validate-package-managers]" not in release_workflow:
        errors.append(
            "Release workflow build-binaries must depend on release/package-manager validators"
        )
    if "uses: astral-sh/setup-uv@v5" not in release_workflow:
        errors.append(
            "Release workflow package-manager validation must install uv before fallback checks"
        )

    ci_workflow = _read(ROOT / ".github" / "workflows" / "ci.yml")
    errors.extend(validate_ci_workflow_content(ci_workflow=ci_workflow))

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
        "scripts/tensor-grep.rb:version",
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
