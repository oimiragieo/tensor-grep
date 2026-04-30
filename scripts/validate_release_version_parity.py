from __future__ import annotations

import argparse
import json
import re
import tarfile
import time
import tomllib
import urllib.request
import zipfile
from email.parser import Parser
from pathlib import Path

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


def _version_from_uv_lock() -> str:
    data = tomllib.loads(_read(ROOT / "uv.lock"))
    packages = data.get("package")
    if not isinstance(packages, list):
        raise ValueError("Missing package list in uv.lock")

    for package in packages:
        if not isinstance(package, dict) or package.get("name") != "tensor-grep":
            continue
        source = package.get("source")
        if not isinstance(source, dict) or source.get("editable") != ".":
            continue
        version = package.get("version")
        if not version:
            raise ValueError("Missing editable tensor-grep version in uv.lock")
        return str(version)

    raise ValueError("Missing editable tensor-grep package in uv.lock")


def _version_from_brew_formula() -> str:
    content = _read(ROOT / "scripts" / "tensor-grep.rb")
    constant_match = re.search(r'(?m)^\s*TENSOR_GREP_VERSION\s*=\s*"([^"]+)"\s*$', content)
    if constant_match:
        return constant_match.group(1)
    match = re.search(r'(?m)^\s*version\s+"([^"]+)"\s*$', content)
    if not match:
        raise ValueError("Missing version in scripts/tensor-grep.rb")
    return match.group(1)


def _version_from_winget_manifest() -> str:
    content = _read(ROOT / "scripts" / "oimiragieo.tensor-grep.yaml")
    match = re.search(r"(?m)^PackageVersion:\s*([^\s]+)\s*$", content)
    if not match:
        raise ValueError("Missing PackageVersion in scripts/oimiragieo.tensor-grep.yaml")
    return match.group(1)


def _artifact_versions_from_dist_dir(dist_dir: Path) -> dict[str, str]:
    versions: dict[str, str] = {}
    if not dist_dir.exists():
        raise ValueError(f"dist directory does not exist: {dist_dir}")

    wheel_files = sorted(dist_dir.glob("*.whl"))
    sdist_files = sorted(dist_dir.glob("*.tar.gz"))

    for wheel_path in wheel_files:
        with zipfile.ZipFile(wheel_path) as zf:
            metadata_name = next(
                (name for name in zf.namelist() if name.endswith(".dist-info/METADATA")),
                None,
            )
            if metadata_name is None:
                raise ValueError(f"wheel missing dist-info METADATA: {wheel_path.name}")
            metadata = Parser().parsestr(zf.read(metadata_name).decode("utf-8"))
            version = metadata.get("Version")
            if not version:
                raise ValueError(f"wheel METADATA missing Version field: {wheel_path.name}")
            versions["wheel"] = str(version)
            break

    for sdist_path in sdist_files:
        with tarfile.open(sdist_path, "r:gz") as tf:
            pkg_info_member = next(
                (member for member in tf.getmembers() if member.name.endswith("/PKG-INFO")),
                None,
            )
            if pkg_info_member is None:
                raise ValueError(f"sdist missing PKG-INFO: {sdist_path.name}")
            pkg_info_file = tf.extractfile(pkg_info_member)
            if pkg_info_file is None:
                raise ValueError(f"sdist PKG-INFO unreadable: {sdist_path.name}")
            metadata = Parser().parsestr(pkg_info_file.read().decode("utf-8"))
            version = metadata.get("Version")
            if not version:
                raise ValueError(f"sdist PKG-INFO missing Version field: {sdist_path.name}")
            versions["sdist"] = str(version)
            break

    return versions


def _fetch_pypi_latest(package_name: str = "tensor-grep") -> str:
    with urllib.request.urlopen(f"https://pypi.org/pypi/{package_name}/json", timeout=15) as resp:
        data = json.load(resp)
    return str(data["info"]["version"])


def _fetch_npm_latest(package_name: str = "tensor-grep") -> str:
    with urllib.request.urlopen(f"https://registry.npmjs.org/{package_name}", timeout=15) as resp:
        data = json.load(resp)
    dist_tags = data.get("dist-tags", {})
    latest = dist_tags.get("latest")
    if not latest:
        raise ValueError(f"npm package {package_name} missing dist-tags.latest")
    return str(latest)


def _fetch_pypi_latest_with_retry(
    *,
    expected_version: str,
    wait_seconds: int,
    poll_interval_seconds: int,
    package_name: str = "tensor-grep",
) -> str:
    if wait_seconds <= 0:
        return _fetch_pypi_latest(package_name=package_name)

    deadline = time.monotonic() + wait_seconds
    interval = max(1, poll_interval_seconds)
    latest = ""
    while True:
        latest = _fetch_pypi_latest(package_name=package_name)
        if latest == expected_version:
            return latest
        if time.monotonic() >= deadline:
            return latest
        time.sleep(interval)


def _fetch_npm_latest_with_retry(
    *,
    expected_version: str,
    wait_seconds: int,
    poll_interval_seconds: int,
    package_name: str = "tensor-grep",
) -> str:
    if wait_seconds <= 0:
        return _fetch_npm_latest(package_name=package_name)

    deadline = time.monotonic() + wait_seconds
    interval = max(1, poll_interval_seconds)
    latest = ""
    while True:
        latest = _fetch_npm_latest(package_name=package_name)
        if latest == expected_version:
            return latest
        if time.monotonic() >= deadline:
            return latest
        time.sleep(interval)


def validate_release_version_parity(
    *,
    expected_version: str,
    expected_tag: str | None = None,
    check_pypi: bool = False,
    check_npm: bool = False,
    check_package_managers: bool = True,
    pypi_wait_seconds: int = 0,
    pypi_poll_interval_seconds: int = 5,
    npm_wait_seconds: int = 0,
    npm_poll_interval_seconds: int = 5,
    dist_dir: Path | None = None,
) -> list[str]:
    errors: list[str] = []

    versions = {
        "pyproject": _version_from_pyproject(),
        "cargo": _version_from_cargo(),
        "npm": _version_from_npm(),
        "uv.lock editable": _version_from_uv_lock(),
    }
    if check_package_managers:
        versions["homebrew"] = _version_from_brew_formula()
        versions["winget"] = _version_from_winget_manifest()
    for source, actual in versions.items():
        if actual != expected_version:
            errors.append(f"{source} version {actual} != expected {expected_version}")

    if dist_dir is not None:
        artifact_versions = _artifact_versions_from_dist_dir(dist_dir)
        for source, actual in artifact_versions.items():
            if actual != expected_version:
                errors.append(f"{source} metadata version {actual} != expected {expected_version}")

    if expected_tag is not None and expected_tag != f"v{expected_version}":
        errors.append(f"expected tag {expected_tag} != v{expected_version}")

    if check_package_managers:
        brew_content = _read(ROOT / "scripts" / "tensor-grep.rb")
        for platform, artifact in {
            "macOS": "tg-macos-amd64-cpu",
            "Linux": "tg-linux-amd64-cpu",
        }.items():
            expected_url = f"https://github.com/oimiragieo/tensor-grep/releases/download/v{expected_version}/{artifact}"
            templated_url = f"https://github.com/oimiragieo/tensor-grep/releases/download/v#{{version}}/{artifact}"
            if expected_url not in brew_content and templated_url not in brew_content:
                errors.append(f"homebrew {platform} url does not target v{expected_version}")

        winget_content = _read(ROOT / "scripts" / "oimiragieo.tensor-grep.yaml")
        expected_winget_url = (
            "https://github.com/oimiragieo/tensor-grep/releases/download/"
            f"v{expected_version}/tg-windows-amd64-cpu.exe"
        )
        if expected_winget_url not in winget_content:
            errors.append("winget installer url does not target expected release version")

    if check_pypi:
        latest = _fetch_pypi_latest_with_retry(
            expected_version=expected_version,
            wait_seconds=pypi_wait_seconds,
            poll_interval_seconds=pypi_poll_interval_seconds,
        )
        if latest != expected_version:
            errors.append(f"pypi latest {latest} != expected {expected_version}")

    if check_npm:
        latest_npm = _fetch_npm_latest_with_retry(
            expected_version=expected_version,
            wait_seconds=npm_wait_seconds,
            poll_interval_seconds=npm_poll_interval_seconds,
        )
        if latest_npm != expected_version:
            errors.append(f"npm latest {latest_npm} != expected {expected_version}")

    return errors


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Validate release version parity across project/package-manager metadata."
    )
    parser.add_argument("--expected-version", required=True)
    parser.add_argument("--expected-tag")
    parser.add_argument(
        "--dist-dir",
        type=Path,
        help="Optional directory containing built wheel/sdist artifacts to validate",
    )
    parser.add_argument("--check-pypi", action="store_true")
    parser.add_argument("--check-npm", action="store_true")
    parser.add_argument("--skip-package-managers", action="store_true")
    parser.add_argument(
        "--pypi-wait-seconds",
        type=int,
        default=0,
        help="Optional wait window for PyPI eventual consistency checks",
    )
    parser.add_argument(
        "--pypi-poll-interval-seconds",
        type=int,
        default=5,
        help="Polling interval for PyPI parity checks",
    )
    parser.add_argument(
        "--npm-wait-seconds",
        type=int,
        default=0,
        help="Optional wait window for npm eventual consistency checks",
    )
    parser.add_argument(
        "--npm-poll-interval-seconds",
        type=int,
        default=5,
        help="Polling interval for npm parity checks",
    )
    args = parser.parse_args()

    errors = validate_release_version_parity(
        expected_version=args.expected_version,
        expected_tag=args.expected_tag,
        check_pypi=args.check_pypi,
        check_npm=args.check_npm,
        check_package_managers=not args.skip_package_managers,
        pypi_wait_seconds=args.pypi_wait_seconds,
        pypi_poll_interval_seconds=args.pypi_poll_interval_seconds,
        npm_wait_seconds=args.npm_wait_seconds,
        npm_poll_interval_seconds=args.npm_poll_interval_seconds,
        dist_dir=args.dist_dir,
    )
    if errors:
        for err in errors:
            print(f"ERROR: {err}")
        return 1
    print("Release version parity validation passed.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
