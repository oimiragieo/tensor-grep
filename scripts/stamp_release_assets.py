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
GPU_DOGFOOD_DOC_PATHS = (
    "README.md",
    "docs/benchmarks.md",
    "docs/gpu_crossover.md",
    "docs/PAPER.md",
)
STAMPED_DOC_PATHS = tuple(dict.fromkeys((*RELEASE_DOC_PATHS, *GPU_DOGFOOD_DOC_PATHS)))


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
            r"current tagged state is `v\d+\.\d+\.\d+`",
            f"current tagged state is `{tag}`",
        ),
        (
            r"latest complete public PyPI/release-asset distribution is also `v\d+\.\d+\.\d+`",
            f"latest complete public PyPI/release-asset distribution is also `{tag}`",
        ),
        (
            r"released through `v\d+\.\d+\.\d+` GitHub assets and PyPI",
            f"released through `{tag}` GitHub assets and PyPI",
        ),
        (
            r"in the public `v\d+\.\d+\.\d+` GitHub asset and PyPI release line",
            f"in the public `{tag}` GitHub asset and PyPI release line",
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
        (
            r"(?m)^(- Current release tag:\s*)`v\d+\.\d+\.\d+`",
            rf"\g<1>`{tag}`",
        ),
        (
            r"(?m)^(- GitHub release: <https://github\.com/oimiragieo/tensor-grep/releases/tag/)v\d+\.\d+\.\d+(>)",
            rf"\g<1>{tag}\2",
        ),
        (
            r"(?m)^(- PyPI pinned install: `uvx --refresh-package tensor-grep --from tensor-grep==)\d+\.\d+\.\d+( tg --version` reports `tensor-grep )\d+\.\d+\.\d+(`)",
            rf"\g<1>{version}\g<2>{version}\3",
        ),
        (
            r"(?m)^(- PyPI/public install proof: `uvx --refresh-package tensor-grep --from tensor-grep==)\d+\.\d+\.\d+( tg --version` reports `tensor-grep )\d+\.\d+\.\d+(`)",
            rf"\g<1>{version}\g<2>{version}\3",
        ),
        (
            r"(?m)^(- GitHub release assets: `)v\d+\.\d+\.\d+(` has uploaded)",
            rf"\g<1>{tag}\2",
        ),
        # The four patterns below replace one former unanchored `post-`vX`` sweep that rewrote
        # EVERY occurrence of the phrase on every release, including dated historical notes in
        # docs/PAPER.md and dated audit entries in docs/gpu_crossover.md (e.g. "dogfood note
        # (2026-05-14):"), silently marching their frozen version forward release after release
        # (audit #71/#73). Each pattern below is anchored with `(?m)^` to one of the small number
        # of genuine "current state" live-pointer shapes (verified via `git log -L` to be
        # periodically hand-refreshed, not frozen) in docs/gpu_crossover.md and docs/benchmarks.md
        # -- the ones `scripts/agent_readiness.py`'s `gpu_fragments` check depends on. A dated note
        # such as `> post-`vX` ... note (YYYY-MM-DD): ...` never matches any of these anchors and is
        # left alone.
        (
            r"(?m)^(## Current post-)`v\d+\.\d+\.\d+`( GPU dogfood Read)",
            rf"\g<1>`{tag}`\2",
        ),
        (
            r"(?m)^(The post-)`v\d+\.\d+\.\d+`( )",
            rf"\g<1>`{tag}`\2",
        ),
        (
            r"(?m)^(- Latest post-)`v\d+\.\d+\.\d+`( )",
            rf"\g<1>`{tag}`\2",
        ),
        (
            r"(?m)^(`benchmarks/run_agent_workflow_benchmarks\.py` is the canonical workflow "
            r"benchmark for the post-)`v\d+\.\d+\.\d+`( dogfood wedge:)",
            rf"\g<1>`{tag}`\2",
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

    for relative in STAMPED_DOC_PATHS:
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
