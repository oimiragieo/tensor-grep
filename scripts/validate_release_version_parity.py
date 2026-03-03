from __future__ import annotations

import argparse
import json
import re
import time
import tomllib
import urllib.request
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


def _version_from_brew_formula() -> str:
    content = _read(ROOT / "scripts" / "tensor-grep.rb")
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


def _fetch_pypi_latest(package_name: str = "tensor-grep") -> str:
    with urllib.request.urlopen(f"https://pypi.org/pypi/{package_name}/json", timeout=15) as resp:
        data = json.load(resp)
    return str(data["info"]["version"])


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


def validate_release_version_parity(
    *,
    expected_version: str,
    expected_tag: str | None = None,
    check_pypi: bool = False,
    check_package_managers: bool = True,
    pypi_wait_seconds: int = 0,
    pypi_poll_interval_seconds: int = 5,
) -> list[str]:
    errors: list[str] = []

    versions = {
        "pyproject": _version_from_pyproject(),
        "cargo": _version_from_cargo(),
        "npm": _version_from_npm(),
    }
    if check_package_managers:
        versions["homebrew"] = _version_from_brew_formula()
        versions["winget"] = _version_from_winget_manifest()
    for source, actual in versions.items():
        if actual != expected_version:
            errors.append(f"{source} version {actual} != expected {expected_version}")

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

    return errors


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Validate release version parity across project/package-manager metadata."
    )
    parser.add_argument("--expected-version", required=True)
    parser.add_argument("--expected-tag")
    parser.add_argument("--check-pypi", action="store_true")
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
    args = parser.parse_args()

    errors = validate_release_version_parity(
        expected_version=args.expected_version,
        expected_tag=args.expected_tag,
        check_pypi=args.check_pypi,
        check_package_managers=not args.skip_package_managers,
        pypi_wait_seconds=args.pypi_wait_seconds,
        pypi_poll_interval_seconds=args.pypi_poll_interval_seconds,
    )
    if errors:
        for err in errors:
            print(f"ERROR: {err}")
        return 1
    print("Release version parity validation passed.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
