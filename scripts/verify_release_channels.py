"""Verify release channel declarations across CI, package managers, npm, Cargo, and Docker."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

try:
    import tomllib
except ModuleNotFoundError:  # pragma: no cover
    import tomli as tomllib

import yaml

ROOT = Path(__file__).resolve().parents[1]

REQUIRED_NATIVE_ASSETS = (
    "tg-windows-amd64-cpu.exe",
    "tg-linux-amd64-cpu",
    "tg-linux-arm64-cpu",
    "tg-macos-amd64-cpu",
    "tg-macos-arm64-cpu",
)

REQUIRED_NPM_OPTIONAL_PACKAGES = (
    "@tensor-grep/cli-win32-x64",
    "@tensor-grep/cli-darwin-arm64",
    "@tensor-grep/cli-darwin-x64",
    "@tensor-grep/cli-linux-x64",
    "@tensor-grep/cli-linux-arm64",
)


def verify_release_channels(root: Path = ROOT) -> list[str]:
    """Validate all 5 native target declarations and package-manager channel contracts."""
    errors: list[str] = []

    pyproject_path = root / "pyproject.toml"
    if not pyproject_path.is_file():
        return [f"Missing pyproject.toml at {pyproject_path}"]
    expected_version = str(
        tomllib.loads(pyproject_path.read_text(encoding="utf-8"))["project"]["version"]
    )

    # 1. CI workflow: all 5 native CPU targets in build-release-native-assets
    ci_path = root / ".github" / "workflows" / "ci.yml"
    if not ci_path.is_file():
        errors.append("Missing .github/workflows/ci.yml")
    else:
        ci_text = ci_path.read_text(encoding="utf-8")
        ci_data = yaml.safe_load(ci_text) or {}
        for line in ci_text.splitlines():
            stripped = line.strip()
            if stripped.startswith("if:") and "secrets." in stripped:
                errors.append("ci.yml must not reference secrets.* inside step/job if: conditions")
            if stripped.startswith("uses:") and "@stable" in stripped:
                errors.append(f"ci.yml must pin action SHA instead of @stable: {stripped}")
        jobs = ci_data.get("jobs", {}) if isinstance(ci_data, dict) else {}
        native_job = jobs.get("build-release-native-assets", {}) if isinstance(jobs, dict) else {}
        include = (
            native_job.get("strategy", {}).get("matrix", {}).get("include", [])
            if isinstance(native_job, dict)
            else []
        )
        ci_assets = {
            str(entry.get("asset_name"))
            for entry in include
            if isinstance(entry, dict) and entry.get("asset_name")
        }
        for asset in REQUIRED_NATIVE_ASSETS:
            if asset not in ci_assets:
                errors.append(f"ci.yml build-release-native-assets missing target asset: {asset}")

    # 2. Homebrew formula: macos-arm64, macos-amd64, linux-arm64, linux-amd64
    brew_path = root / "scripts" / "tensor-grep.rb"
    if not brew_path.is_file():
        errors.append("Missing scripts/tensor-grep.rb")
    else:
        brew_text = brew_path.read_text(encoding="utf-8")
        if f'TENSOR_GREP_VERSION = "{expected_version}"' not in brew_text:
            errors.append(f"scripts/tensor-grep.rb version mismatch (expected {expected_version})")
        for marker in (
            "on_arm",
            "on_intel",
            "Hardware::CPU.arm?",
            "tg-macos-arm64-cpu",
            "tg-macos-amd64-cpu",
            "tg-linux-arm64-cpu",
            "tg-linux-amd64-cpu",
        ):
            if marker not in brew_text:
                errors.append(f"scripts/tensor-grep.rb missing {marker!r}")

    # 3. Winget manifest
    winget_path = root / "scripts" / "oimiragieo.tensor-grep.yaml"
    if not winget_path.is_file():
        errors.append("Missing scripts/oimiragieo.tensor-grep.yaml")
    else:
        winget_data = yaml.safe_load(winget_path.read_text(encoding="utf-8")) or {}
        if str(winget_data.get("PackageVersion")) != expected_version:
            errors.append(f"Winget PackageVersion != {expected_version}")
        if winget_data.get("License") != "Apache-2.0":
            errors.append("Winget License must be Apache-2.0")
        installers = winget_data.get("Installers") or []
        if not installers or "tg-windows-amd64-cpu.exe" not in str(
            installers[0].get("InstallerUrl", "")
        ):
            errors.append("Winget InstallerUrl must point to tg-windows-amd64-cpu.exe")

    # 4. npm/package.json (no postinstall, 5 optionalDependencies + manifests)
    npm_pkg_path = root / "npm" / "package.json"
    if not npm_pkg_path.is_file():
        errors.append("Missing npm/package.json")
    else:
        npm_data = json.loads(npm_pkg_path.read_text(encoding="utf-8"))
        if str(npm_data.get("version")) != expected_version:
            errors.append(f"npm/package.json version != {expected_version}")
        scripts_block = npm_data.get("scripts") or {}
        if "postinstall" in scripts_block:
            errors.append("npm/package.json must not define a postinstall script")
        npm_lock_path = root / "npm" / "package-lock.json"
        if not npm_lock_path.is_file():
            errors.append("Missing npm/package-lock.json")
        else:
            lock_data = json.loads(npm_lock_path.read_text(encoding="utf-8"))
            if str(lock_data.get("version")) != expected_version:
                errors.append(f"npm/package-lock.json version != {expected_version}")
            root_pkg = (lock_data.get("packages") or {}).get("") or {}
            if root_pkg.get("hasInstallScript"):
                errors.append("npm/package-lock.json root package must not set hasInstallScript")
        opt_deps = npm_data.get("optionalDependencies") or {}
        for pkg_name in REQUIRED_NPM_OPTIONAL_PACKAGES:
            if opt_deps.get(pkg_name) != expected_version:
                errors.append(
                    f"npm/package.json optionalDependencies[{pkg_name!r}] != {expected_version}"
                )
            sub_dir = pkg_name.split("/", 1)[1]
            sub_pkg = root / "npm" / "packages" / sub_dir / "package.json"
            if not sub_pkg.is_file():
                errors.append(f"Missing npm platform manifest: {sub_pkg.relative_to(root)}")
            else:
                sub_data = json.loads(sub_pkg.read_text(encoding="utf-8"))
                if (
                    sub_data.get("name") != pkg_name
                    or str(sub_data.get("version")) != expected_version
                    or not sub_data.get("os")
                    or not sub_data.get("cpu")
                    or not sub_data.get("files")
                ):
                    errors.append(
                        f"Invalid name/version/os/cpu/files in {sub_pkg.relative_to(root)}"
                    )

    # 5. rust_core/Cargo.toml
    cargo_path = root / "rust_core" / "Cargo.toml"
    if not cargo_path.is_file():
        errors.append("Missing rust_core/Cargo.toml")
    else:
        cargo_data = tomllib.loads(cargo_path.read_text(encoding="utf-8"))
        pkg = cargo_data.get("package", {})
        if str(pkg.get("version")) != expected_version:
            errors.append(f"rust_core/Cargo.toml version != {expected_version}")
        if pkg.get("license") != "Apache-2.0":
            errors.append("rust_core/Cargo.toml license must be Apache-2.0")
        if not pkg.get("repository"):
            errors.append("rust_core/Cargo.toml must declare repository")
        bins = cargo_data.get("bin", [])
        if not any(isinstance(b, dict) and b.get("name") == "tg" for b in bins):
            errors.append("rust_core/Cargo.toml must declare [[bin]] name = 'tg'")

    # 6. Dockerfile
    dockerfile_path = root / "Dockerfile"
    if not dockerfile_path.is_file():
        errors.append("Missing Dockerfile")
    else:
        dockerfile_text = dockerfile_path.read_text(encoding="utf-8")
        if "distroless" not in dockerfile_text:
            errors.append("Dockerfile must use a distroless runtime base image")
        if "ghcr.io/oimiragieo/tensor-grep" not in dockerfile_text:
            errors.append("Dockerfile must reference ghcr.io/oimiragieo/tensor-grep")
        if "src/tensor_grep/cli/commands.py" not in dockerfile_text:
            errors.append("Dockerfile must copy src/tensor_grep/cli/commands.py for include_str!")
        if "ENTRYPOINT" not in dockerfile_text:
            errors.append("Dockerfile must declare ENTRYPOINT")

    return errors


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Verify tensor-grep release channel manifests and target coverage."
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Validate channel manifests without mutating files or contacting registries.",
    )
    parser.add_argument(
        "--root",
        type=Path,
        default=ROOT,
        help="Repository root directory.",
    )
    args = parser.parse_args()
    errors = verify_release_channels(root=args.root)
    if errors:
        for err in errors:
            print(f"ERROR: {err}")
        return 1
    mode = "dry-run" if args.dry_run else "verify"
    print(f"Release channel verification ({mode}) passed: all 5 targets and channels valid.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
