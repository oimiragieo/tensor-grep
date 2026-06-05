from __future__ import annotations

import argparse
import hashlib
import importlib.util
import re
import shutil
import tomllib
from pathlib import Path

WINDOWS_NATIVE_ASSET = "tg-windows-amd64-cpu.exe"

SCRIPT_DIR = Path(__file__).resolve().parent
ROOT = SCRIPT_DIR.parents[0]


def _load_release_assets_module():
    path = SCRIPT_DIR / "validate_release_assets.py"
    spec = importlib.util.spec_from_file_location("validate_release_assets", path)
    if spec is None or spec.loader is None:
        raise RuntimeError("Unable to load scripts/validate_release_assets.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _read(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def _version_from_pyproject() -> str:
    data = tomllib.loads(_read(ROOT / "pyproject.toml"))
    return str(data["project"]["version"])


def _validate_sources(version: str) -> list[str]:
    validators = _load_release_assets_module()
    errors: list[str] = []
    brew_path = ROOT / "scripts" / "tensor-grep.rb"
    winget_path = ROOT / "scripts" / "oimiragieo.tensor-grep.yaml"

    brew = _read(brew_path)
    winget = _read(winget_path)

    errors.extend(
        validators.validate_homebrew_formula_contract(brew_content=brew, py_version=version)
    )
    errors.extend(validators.validate_winget_manifest(winget_content=winget, py_version=version))
    return errors


def _bundle_paths(output_dir: Path, version: str) -> tuple[Path, Path, Path, Path]:
    brew_dest = output_dir / "homebrew-tap" / "Formula" / "tensor-grep.rb"
    winget_dest = (
        output_dir
        / "winget-pkgs"
        / "manifests"
        / "o"
        / "oimiragieo"
        / "tensor-grep"
        / version
        / "oimiragieo.tensor-grep.yaml"
    )
    summary = output_dir / "PUBLISH_INSTRUCTIONS.md"
    checksums = output_dir / "BUNDLE_CHECKSUMS.txt"
    return brew_dest, winget_dest, summary, checksums


def _write_summary(summary: Path, version: str) -> None:
    summary.parent.mkdir(parents=True, exist_ok=True)
    summary.write_text(
        (
            f"# Package Manager Publish Bundle v{version}\n\n"
            "## Homebrew\n"
            "1. Copy `homebrew-tap/Formula/tensor-grep.rb` into your tap repo `Formula/`.\n"
            "2. Validate syntax:\n"
            "   - `ruby -c Formula/tensor-grep.rb`\n"
            "3. Commit and open a PR:\n"
            f"   - `git checkout -b release/tensor-grep-v{version}`\n"
            "   - `git add Formula/tensor-grep.rb`\n"
            f'   - `git commit -m "chore(brew): publish tensor-grep v{version}"`\n'
            f"   - `git push origin release/tensor-grep-v{version}`\n\n"
            "4. Smoke-test install:\n"
            "   - `brew install oimiragieo/tap/tensor-grep`\n"
            "   - `tg --version`\n\n"
            "## Winget\n"
            "1. Copy `winget-pkgs/manifests/o/oimiragieo/tensor-grep/"
            f"{version}/oimiragieo.tensor-grep.yaml` into `winget-pkgs`.\n"
            "2. Validate manifest:\n"
            f"   - `winget validate --manifest .\\manifests\\o\\oimiragieo\\tensor-grep\\{version}\\`\n"
            "3. Commit and open a PR:\n"
            f"   - `git checkout -b release/tensor-grep-v{version}`\n"
            f"   - `git add manifests/o/oimiragieo/tensor-grep/{version}`\n"
            f'   - `git commit -m "chore(winget): publish tensor-grep v{version}"`\n'
            f"   - `git push origin release/tensor-grep-v{version}`\n"
            "4. Smoke-test install:\n"
            "   - `winget install oimiragieo.tensor-grep`\n"
            "   - `tg --version`\n"
            "\n## Integrity\n"
            "Verify copied files against `BUNDLE_CHECKSUMS.txt` before opening PRs.\n"
        ),
        encoding="utf-8",
    )


def _write_bundle_checksums(*, output_dir: Path, checksums_path: Path) -> None:
    files = sorted(
        path for path in output_dir.rglob("*") if path.is_file() and path != checksums_path
    )
    lines: list[str] = []
    for path in files:
        digest = hashlib.sha256(path.read_bytes()).hexdigest()
        rel = path.relative_to(output_dir).as_posix()
        lines.append(f"{digest}  {rel}")
    checksums_path.write_text("\n".join(lines) + ("\n" if lines else ""), encoding="utf-8")


def _windows_installer_sha_from_checksums(checksums_path: Path | None) -> str | None:
    if checksums_path is None or not checksums_path.exists():
        return None

    for raw_line in checksums_path.read_text(encoding="utf-8").splitlines():
        parts = raw_line.split()
        if len(parts) == 2 and parts[1] == WINDOWS_NATIVE_ASSET:
            digest = parts[0]
            if re.fullmatch(r"[0-9a-fA-F]{64}", digest):
                return digest.lower()
            raise ValueError(f"Invalid SHA256 digest for {WINDOWS_NATIVE_ASSET}")
    raise ValueError(f"Missing {WINDOWS_NATIVE_ASSET} entry in {checksums_path}")


def _stamp_winget_installer_sha(*, winget_content: str, installer_sha256: str) -> str:
    replacement = rf"\g<1>{installer_sha256}"
    if re.search(r"(?m)^(\s*InstallerSha256:\s*)[0-9a-fA-F]{64}\s*$", winget_content):
        return re.sub(
            r"(?m)^(\s*InstallerSha256:\s*)[0-9a-fA-F]{64}\s*$",
            replacement,
            winget_content,
            count=1,
        )
    return re.sub(
        r"(?m)^(\s*InstallerUrl:\s*https://github\.com/oimiragieo/tensor-grep/releases/download/v[^/]+/tg-windows-amd64-cpu\.exe\s*)$",
        lambda match: f"{match.group(1)}\n    InstallerSha256: {installer_sha256}",
        winget_content,
        count=1,
    )


def prepare_bundle(
    *, output_dir: Path, check_only: bool, release_checksums: Path | None = None
) -> int:
    version = _version_from_pyproject()
    errors = _validate_sources(version)
    if errors:
        for err in errors:
            print(f"ERROR: {err}")
        return 1

    if check_only:
        print(f"Package manager sources validated for v{version}.")
        return 0

    try:
        windows_installer_sha = _windows_installer_sha_from_checksums(release_checksums)
    except ValueError as exc:
        print(f"ERROR: {exc}")
        return 1

    brew_src = ROOT / "scripts" / "tensor-grep.rb"
    winget_src = ROOT / "scripts" / "oimiragieo.tensor-grep.yaml"
    brew_dest, winget_dest, summary, checksums = _bundle_paths(
        output_dir=output_dir, version=version
    )

    if output_dir.exists():
        shutil.rmtree(output_dir)

    brew_dest.parent.mkdir(parents=True, exist_ok=True)
    winget_dest.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(brew_src, brew_dest)
    winget_content = _read(winget_src)
    if windows_installer_sha is not None:
        winget_content = _stamp_winget_installer_sha(
            winget_content=winget_content,
            installer_sha256=windows_installer_sha,
        )
    winget_dest.write_text(winget_content, encoding="utf-8")
    _write_summary(summary=summary, version=version)
    _write_bundle_checksums(output_dir=output_dir, checksums_path=checksums)

    print(f"Prepared package-manager publish bundle at {output_dir}")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Prepare package-manager publish bundle artifacts."
    )
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=Path("artifacts") / "package-manager-bundle",
        help="Output directory for generated package-manager publish bundle",
    )
    parser.add_argument(
        "--check",
        action="store_true",
        help="Validate package-manager source manifests only",
    )
    parser.add_argument(
        "--release-checksums",
        type=Path,
        default=Path("artifacts") / "CHECKSUMS.txt",
        help="Optional release CHECKSUMS.txt used to stamp winget InstallerSha256 in output bundles",
    )
    args = parser.parse_args()
    return prepare_bundle(
        output_dir=args.output_dir,
        check_only=args.check,
        release_checksums=args.release_checksums,
    )


if __name__ == "__main__":
    raise SystemExit(main())
