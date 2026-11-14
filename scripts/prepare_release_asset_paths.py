from __future__ import annotations

import argparse
from pathlib import Path

from validate_release_binary_artifacts import EXPECTED_BINARY_PROFILES, validate_artifacts

MANAGED_ASSETS = ("sbom-rust.json", "sbom-python.json", "CHECKSUMS.txt")
PACKAGE_ASSETS = (
    "package-manager-bundle/homebrew-tap/Formula/tensor-grep.rb",
    "package-manager-bundle/PUBLISH_INSTRUCTIONS.md",
    "package-manager-bundle/BUNDLE_CHECKSUMS.txt",
)


def _checked_file(path: Path, root: Path) -> Path:
    try:
        resolved = path.resolve(strict=True)
        resolved.relative_to(root.resolve(strict=True))
    except (OSError, ValueError) as exc:
        raise ValueError(f"Release asset is missing or outside the artifact root: {path}") from exc
    if path.is_symlink() or not path.is_file():
        raise ValueError(f"Release asset must be a regular non-symlink file: {path}")
    return path


def prepare_release_asset_paths(
    *, artifacts_dir: Path, expected_profile: str
) -> tuple[list[Path], list[Path]]:
    if expected_profile not in EXPECTED_BINARY_PROFILES:
        raise ValueError(f"Unknown release asset profile: {expected_profile}")
    native_dir = artifacts_dir / "native"
    if any(path.is_symlink() for path in native_dir.rglob("tg-*")):
        raise ValueError("Native release assets must not contain symlinks")
    errors = validate_artifacts(native_dir, expected_profile=expected_profile)
    if errors:
        raise ValueError("; ".join(errors))

    native_paths: list[Path] = []
    for name in sorted(EXPECTED_BINARY_PROFILES[expected_profile]):
        matches = sorted(
            path for path in native_dir.rglob(name) if path.name == name and path.is_file()
        )
        if len(matches) != 1:
            raise ValueError(f"Expected one release binary named {name}; found {len(matches)}")
        native_paths.append(_checked_file(matches[0], artifacts_dir))

    subjects = [*native_paths]
    subjects.extend(_checked_file(artifacts_dir / name, artifacts_dir) for name in MANAGED_ASSETS)
    uploads = [*subjects]
    uploads.extend(
        _checked_file(artifacts_dir / relative, artifacts_dir) for relative in PACKAGE_ASSETS
    )
    winget = sorted(
        (artifacts_dir / "package-manager-bundle" / "winget-pkgs" / "manifests").rglob(
            "oimiragieo.tensor-grep.yaml"
        )
    )
    if len(winget) != 1:
        raise ValueError(f"Expected one Winget release manifest; found {len(winget)}")
    uploads.append(_checked_file(winget[0], artifacts_dir))
    return subjects, uploads


def write_github_output(path: Path, *, subjects: list[Path], uploads: list[Path]) -> None:
    with path.open("a", encoding="utf-8", newline="\n") as output:
        for name, values in (("subject_paths", subjects), ("upload_files", uploads)):
            output.write(f"{name}<<{name.upper()}\n")
            output.writelines(f"{value.as_posix()}\n" for value in values)
            output.write(f"{name.upper()}\n")


def main() -> int:
    parser = argparse.ArgumentParser(description="Prepare the exact release subject/upload paths.")
    parser.add_argument("--artifacts-dir", type=Path, required=True)
    parser.add_argument(
        "--expected-profile", choices=sorted(EXPECTED_BINARY_PROFILES), required=True
    )
    parser.add_argument("--github-output", type=Path, required=True)
    args = parser.parse_args()
    try:
        subjects, uploads = prepare_release_asset_paths(
            artifacts_dir=args.artifacts_dir,
            expected_profile=args.expected_profile,
        )
        write_github_output(args.github_output, subjects=subjects, uploads=uploads)
    except ValueError as exc:
        print(f"ERROR: {exc}")
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
