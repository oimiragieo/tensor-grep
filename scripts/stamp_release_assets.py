from __future__ import annotations

import argparse
import re
import tomllib
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
RELEASE_DOC_PATHS = (
    "AGENTS.md",
    "README.md",
    "SKILL.md",
    "docs/SESSION_HANDOFF.md",
    "docs/CONTINUATION_PLAN.md",
    "docs/CONTRACTS.md",
)


def _read(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def _write(path: Path, content: str) -> None:
    path.write_text(content, encoding="utf-8")


def _version_from_pyproject() -> str:
    data = tomllib.loads(_read(ROOT / "pyproject.toml"))
    return str(data["project"]["version"])


def _stamp_release_doc(content: str, *, version: str) -> str:
    tag = f"v{version}"
    replacements = [
        (
            r"(?m)^(release_docs_current_tag:\s*)v\d+\.\d+\.\d+\b",
            rf"\g<1>{tag}",
        ),
        (
            r"current `v\d+\.\d+\.\d+` (shell/version resolution|positioning|release line)",
            rf"current `{tag}` \1",
        ),
        (
            r"current tagged (version|release state) is `v\d+\.\d+\.\d+`",
            rf"current tagged \1 is `{tag}`",
        ),
        (
            r"Latest tagged GitHub release: \[`v\d+\.\d+\.\d+`\]\(https://github\.com/oimiragieo/tensor-grep/releases/tag/v\d+\.\d+\.\d+\)",
            f"Latest tagged GitHub release: [`{tag}`](https://github.com/oimiragieo/tensor-grep/releases/tag/{tag})",
        ),
        (
            r"Latest complete PyPI release: \[`v\d+\.\d+\.\d+`\]\(https://github\.com/oimiragieo/tensor-grep/releases/tag/v\d+\.\d+\.\d+\)",
            f"Latest complete PyPI release: [`{tag}`](https://github.com/oimiragieo/tensor-grep/releases/tag/{tag})",
        ),
        (
            r"(?m)^(- Latest tagged version:\s*)`v\d+\.\d+\.\d+`",
            rf"\g<1>`{tag}`",
        ),
        (
            r"(?m)^(- Latest complete PyPI version:\s*)`v\d+\.\d+\.\d+`",
            rf"\g<1>`{tag}`",
        ),
    ]
    stamped = content
    for pattern, replacement in replacements:
        stamped = re.sub(pattern, replacement, stamped)
    return stamped


def stamp_assets(*, check_only: bool) -> int:
    version = _version_from_pyproject()

    brew_path = ROOT / "scripts" / "tensor-grep.rb"
    winget_path = ROOT / "scripts" / "oimiragieo.tensor-grep.yaml"

    brew_before = _read(brew_path)
    winget_before = _read(winget_path)

    brew_after = brew_before
    if re.search(r'(?m)^\s*TENSOR_GREP_VERSION\s*=\s*"[^"]+"\s*$', brew_after):
        brew_after = re.sub(
            r'(?m)^(\s*TENSOR_GREP_VERSION\s*=\s*)"[^"]+"(\s*)$',
            rf'\g<1>"{version}"\2',
            brew_after,
            count=1,
        )
    else:
        brew_after = re.sub(
            r'(?m)^(\s*version\s+)"[^"]+"(\s*)$',
            rf'\g<1>"{version}"\2',
            brew_after,
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

    for relative in RELEASE_DOC_PATHS:
        doc_path = ROOT / relative
        if not doc_path.exists():
            continue
        before = _read(doc_path)
        after = _stamp_release_doc(before, version=version)
        if after != before:
            changed = True
            if not check_only:
                _write(doc_path, after)

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
