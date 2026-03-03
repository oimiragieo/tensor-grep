from __future__ import annotations

import argparse
import re
import tomllib
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def _read(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def _write(path: Path, content: str) -> None:
    path.write_text(content, encoding="utf-8")


def _version_from_pyproject() -> str:
    data = tomllib.loads(_read(ROOT / "pyproject.toml"))
    return str(data["project"]["version"])


def stamp_assets(*, check_only: bool) -> int:
    version = _version_from_pyproject()

    brew_path = ROOT / "scripts" / "tensor-grep.rb"
    winget_path = ROOT / "scripts" / "oimiragieo.tensor-grep.yaml"

    brew_before = _read(brew_path)
    winget_before = _read(winget_path)

    brew_after = re.sub(
        r'(?m)^(\s*version\s+)"[^"]+"(\s*)$',
        rf'\g<1>"{version}"\2',
        brew_before,
        count=1,
    )
    winget_after = re.sub(
        r"(?m)^(\ufeff)?# Winget Manifest for tensor-grep v[^\s]+$",
        rf"\1# Winget Manifest for tensor-grep v{version}",
        winget_before,
        count=1,
    )
    winget_after = re.sub(
        r"(?m)^(PackageVersion:\s*).*$",
        rf"\g<1>{version}",
        winget_after,
        count=1,
    )
    winget_after = re.sub(
        r"(?m)^\s*InstallerUrl:\s*https://github\.com/oimiragieo/tensor-grep/releases/download/v[^/]+/tg-windows-amd64-cpu\.exe\s*$",
        rf"    InstallerUrl: https://github.com/oimiragieo/tensor-grep/releases/download/v{version}/tg-windows-amd64-cpu.exe",
        winget_after,
        count=1,
    )

    changed = False
    if brew_after != brew_before:
        changed = True
        if not check_only:
            _write(brew_path, brew_after)

    if winget_after != winget_before:
        changed = True
        if not check_only:
            _write(winget_path, winget_after)

    if check_only and changed:
        print("Release assets are not stamped to current pyproject version.")
        print("Run: python scripts/stamp_release_assets.py")
        return 1

    if changed:
        print(f"Stamped release assets to version {version}.")
    else:
        print(f"Release assets already stamped to version {version}.")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description="Stamp release asset versions.")
    parser.add_argument(
        "--check",
        action="store_true",
        help="Only check whether release assets are stamped; do not modify files.",
    )
    args = parser.parse_args()
    return stamp_assets(check_only=args.check)


if __name__ == "__main__":
    raise SystemExit(main())
