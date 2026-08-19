from __future__ import annotations

import json
import re
import sys
from pathlib import Path

try:
    import tomllib
except ModuleNotFoundError:  # pragma: no cover
    import tomli as tomllib

# The check functions below live in scripts/_release_assets_checks/ (split out 2026-08-19 to
# bring this file under the 1500-line file-size budget). This shim adds the scripts/ directory to
# sys.path so the subpackage import resolves BOTH when this file is run directly
# (``python scripts/validate_release_assets.py`` -- Python already puts scripts/ on sys.path[0])
# and when a test loads it via ``importlib.util.spec_from_file_location`` (which does NOT put
# scripts/ on sys.path on its own).
_SCRIPTS_DIR = Path(__file__).resolve().parent
if str(_SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(_SCRIPTS_DIR))

from _release_assets_checks.ci_workflow import validate_ci_workflow_content  # noqa: E402
from _release_assets_checks.constants import RELEASE_DOC_PATHS, ROOT  # noqa: E402
from _release_assets_checks.docs_and_manifest_checks import (  # noqa: E402
    validate_ast_grep_version_parity,
    validate_benchmarks_docs,
    validate_ci_pipeline_doc_contract,
    validate_dev_tooling_constraints,
    validate_homebrew_formula_contract,
    validate_installation_docs,
    validate_npm_installer_contract,
    validate_npm_manifest_contract,
    validate_package_manager_docs,
    validate_readme_contract,
    validate_release_docs_current_prose,
    validate_semantic_release_config,
    validate_uv_security_constraints,
    validate_winget_manifest,
)
from _release_assets_checks.release_workflow import validate_release_workflow_content  # noqa: E402
from _release_assets_checks.workflow_checks import (  # noqa: E402
    validate_actions_sha_pinned,
    validate_audit_workflow_content,
    validate_dependabot_automation_workflow_content,
    validate_dependabot_config,
    validate_public_gpu_proof_workflow_content,
)

__all__ = [
    "RELEASE_DOC_PATHS",
    "ROOT",
    "main",
    "validate_actions_sha_pinned",
    "validate_all",
    "validate_ast_grep_version_parity",
    "validate_audit_workflow_content",
    "validate_benchmarks_docs",
    "validate_ci_pipeline_doc_contract",
    "validate_ci_workflow_content",
    "validate_dependabot_automation_workflow_content",
    "validate_dependabot_config",
    "validate_dev_tooling_constraints",
    "validate_homebrew_formula_contract",
    "validate_installation_docs",
    "validate_native_cli_contract",
    "validate_npm_installer_contract",
    "validate_npm_manifest_contract",
    "validate_package_manager_docs",
    "validate_public_gpu_proof_workflow_content",
    "validate_readme_contract",
    "validate_release_docs_current_prose",
    "validate_release_workflow_content",
    "validate_semantic_release_config",
    "validate_uv_security_constraints",
    "validate_winget_manifest",
]

# --- primitives kept here verbatim: tests monkeypatch these directly on the loaded module
# (``module._read = fake_read`` etc.) and ``validate_all`` below must observe the patched
# values, which requires both to share this module's global namespace. ---


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


def _version_from_cargo_lock() -> str:
    data = tomllib.loads(_read(ROOT / "rust_core" / "Cargo.lock"))
    packages = data.get("package")
    if not isinstance(packages, list):
        raise ValueError("rust_core/Cargo.lock package list is missing or invalid")

    for package in packages:
        if isinstance(package, dict) and package.get("name") == "tensor_grep_rs":
            version = package.get("version")
            if not version:
                raise ValueError("Missing tensor_grep_rs version in rust_core/Cargo.lock")
            return str(version)

    raise ValueError("Missing tensor_grep_rs package in rust_core/Cargo.lock")


def _version_from_npm() -> str:
    data = json.loads(_read(ROOT / "npm" / "package.json"))
    return str(data["version"])


def _winget_installer_manifest_content(*, py_version: str) -> str:
    installer_path = (
        ROOT
        / "scripts"
        / "winget-pkgs"
        / "manifests"
        / "o"
        / "oimiragieo"
        / "tensor-grep"
        / py_version
        / "oimiragieo.tensor-grep.installer.yaml"
    )
    if installer_path.is_file():
        return _read(installer_path)
    return _read(ROOT / "scripts" / "oimiragieo.tensor-grep.yaml")


def _version_from_uv_lock() -> str:
    data = tomllib.loads(_read(ROOT / "uv.lock"))
    packages = data.get("package", [])
    if not isinstance(packages, list):
        raise ValueError("uv.lock package list is missing or invalid")

    for package in packages:
        if not isinstance(package, dict):
            continue
        if str(package.get("name")) != "tensor-grep":
            continue
        source = package.get("source") or {}
        if isinstance(source, dict) and source.get("editable") == ".":
            return str(package["version"])

    raise ValueError("Missing uv.lock editable tensor-grep package version")


def validate_native_cli_contract(*, main_rs_content: str, expected_version: str) -> list[str]:
    errors: list[str] = []
    if '#[command(version = "0.2.0")]' in main_rs_content:
        errors.append(
            "rust_core/src/main.rs must derive native CLI version from Cargo/package metadata instead of hardcoding 0.2.0"
        )
    if "#[command(version)]" not in main_rs_content:
        errors.append("rust_core/src/main.rs must use #[command(version)] for Clap version output")
    if expected_version not in _read(ROOT / "pyproject.toml"):
        errors.append(f"Expected version {expected_version} was not found in pyproject.toml")
    return errors


def validate_all() -> list[str]:
    errors: list[str] = []
    py_version = _version_from_pyproject()
    cargo_version = _version_from_cargo()
    cargo_lock_version = _version_from_cargo_lock()
    uv_lock_version = _version_from_uv_lock()
    pyproject_content = _read(ROOT / "pyproject.toml")
    npm_root = ROOT / "npm"
    npm_manifest = json.loads(_read(ROOT / "npm" / "package.json"))
    npm_version = str(npm_manifest["version"])

    if cargo_version != py_version:
        errors.append(
            f"Version mismatch: rust_core/Cargo.toml={cargo_version} != pyproject={py_version}"
        )
    if cargo_lock_version != py_version:
        errors.append(
            "rust_core/Cargo.lock tensor_grep_rs version does not match pyproject version: "
            f"{cargo_lock_version} != {py_version}"
        )
    if npm_version != py_version:
        errors.append(f"Version mismatch: npm/package.json={npm_version} != pyproject={py_version}")
    if uv_lock_version != py_version:
        errors.append(
            "uv.lock editable tensor-grep version does not match pyproject version: "
            f"{uv_lock_version} != {py_version}"
        )

    npm_repository_url = str((npm_manifest.get("repository") or {}).get("url") or "")
    expected_npm_repo_url = "git+https://github.com/oimiragieo/tensor-grep.git"
    if npm_repository_url != expected_npm_repo_url:
        errors.append(
            "npm/package.json repository.url must be "
            f"{expected_npm_repo_url}, got {npm_repository_url or '<empty>'}"
        )

    winget = _winget_installer_manifest_content(py_version=py_version)
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

    public_gpu_proof_workflow = _read(ROOT / ".github" / "workflows" / "public-gpu-proof.yml")
    errors.extend(
        validate_public_gpu_proof_workflow_content(workflow_content=public_gpu_proof_workflow)
    )

    audit_workflow = _read(ROOT / ".github" / "workflows" / "audit.yml")
    errors.extend(validate_audit_workflow_content(workflow_content=audit_workflow))

    dependabot_config = _read(ROOT / ".github" / "dependabot.yml")
    errors.extend(validate_dependabot_config(dependabot_content=dependabot_config))

    dependabot_workflow = _read(ROOT / ".github" / "workflows" / "dependabot-automation.yml")
    errors.extend(
        validate_dependabot_automation_workflow_content(workflow_content=dependabot_workflow)
    )

    errors.extend(validate_actions_sha_pinned())

    package_manager_runbook = _read(ROOT / "docs" / "package_manager_publish.md")
    release_checklist = _read(ROOT / "docs" / "RELEASE_CHECKLIST.md")
    installation_docs = _read(ROOT / "docs" / "installation.md")
    benchmarks_docs = _read(ROOT / "docs" / "benchmarks.md")
    readme = _read(ROOT / "README.md")
    release_docs = {
        relative: _read(ROOT / relative)
        for relative in RELEASE_DOC_PATHS
        if (ROOT / relative).exists()
    }
    errors.extend(
        validate_package_manager_docs(
            runbook_content=package_manager_runbook,
            checklist_content=release_checklist,
        )
    )
    errors.extend(validate_installation_docs(installation_content=installation_docs))
    errors.extend(validate_benchmarks_docs(benchmarks_content=benchmarks_docs))
    errors.extend(validate_readme_contract(readme_content=readme))
    errors.extend(
        validate_release_docs_current_prose(
            documents=release_docs,
            expected_version=py_version,
        )
    )
    errors.extend(
        validate_native_cli_contract(
            main_rs_content=_read(ROOT / "rust_core" / "src" / "main.rs"),
            expected_version=py_version,
        )
    )
    errors.extend(
        validate_npm_manifest_contract(
            package_json_content=_read(npm_root / "package.json"),
            available_paths={
                path.relative_to(npm_root).as_posix()
                for path in npm_root.rglob("*")
                if path.is_file()
            },
        )
    )
    errors.extend(
        validate_npm_installer_contract(
            install_js_content=_read(ROOT / "npm" / "install.js"),
            expected_version=py_version,
        )
    )
    benchmark_workflow_content = _read(ROOT / ".github" / "workflows" / "benchmark.yml")
    errors.extend(
        validate_ci_pipeline_doc_contract(
            ci_pipeline_content=_read(ROOT / "docs" / "CI_PIPELINE.md"),
            benchmark_workflow_content=benchmark_workflow_content,
        )
    )
    errors.extend(
        validate_ast_grep_version_parity(
            ci_workflow_content=ci_workflow,
            benchmark_workflow_content=benchmark_workflow_content,
        )
    )
    support_matrix = _read(ROOT / "docs" / "SUPPORT_MATRIX.md")
    if "Python < 3.11" not in support_matrix:
        errors.append("docs/SUPPORT_MATRIX.md must mark Python < 3.11 as unsupported")
    for unsupported_minor in ("3.9", "3.10", "3.13", "3.14"):
        if unsupported_minor in support_matrix:
            errors.append(
                "docs/SUPPORT_MATRIX.md must not advertise unsupported Python minors: "
                f"found {unsupported_minor}"
            )
    if "[SECURITY.md](SECURITY.md)" in readme and not (ROOT / "SECURITY.md").exists():
        errors.append("README links to SECURITY.md, but SECURITY.md is missing from the repo root")

    errors.extend(validate_uv_security_constraints(pyproject_content=pyproject_content))
    errors.extend(validate_dev_tooling_constraints(pyproject_content=pyproject_content))
    errors.extend(validate_semantic_release_config(pyproject_content=pyproject_content))

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
