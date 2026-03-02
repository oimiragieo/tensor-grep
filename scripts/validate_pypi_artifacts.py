from __future__ import annotations

import argparse
import tarfile
import zipfile
from pathlib import Path


def _parse_metadata_version(metadata_text: str) -> str:
    for line in metadata_text.splitlines():
        if line.startswith("Version: "):
            return line.split("Version: ", 1)[1].strip()
    raise ValueError("Metadata is missing Version field")


def _wheel_metadata_version(wheel_path: Path) -> str:
    with zipfile.ZipFile(wheel_path) as zf:
        metadata_members = [name for name in zf.namelist() if name.endswith(".dist-info/METADATA")]
        if not metadata_members:
            raise ValueError(f"{wheel_path.name} is missing METADATA")
        metadata_text = zf.read(metadata_members[0]).decode("utf-8")
        return _parse_metadata_version(metadata_text)


def _sdist_metadata_version(sdist_path: Path) -> str:
    with tarfile.open(sdist_path, mode="r:gz") as tf:
        pkg_info_members = [m for m in tf.getmembers() if m.name.endswith("/PKG-INFO")]
        if not pkg_info_members:
            raise ValueError(f"{sdist_path.name} is missing PKG-INFO")
        data = tf.extractfile(pkg_info_members[0])
        if data is None:
            raise ValueError(f"{sdist_path.name} contains unreadable PKG-INFO")
        metadata_text = data.read().decode("utf-8")
        return _parse_metadata_version(metadata_text)


def validate(
    *,
    dist_dir: Path,
    version: str,
    require_platforms: list[str] | None = None,
) -> list[str]:
    errors: list[str] = []
    wheels = sorted(dist_dir.glob("tensor_grep-*.whl"))
    sdists = sorted(dist_dir.glob("tensor_grep-*.tar.gz"))

    if not wheels:
        errors.append("No wheel artifacts found in dist directory")
    if not sdists:
        errors.append("No sdist artifacts found in dist directory")

    expected_sdist_name = f"tensor_grep-{version}.tar.gz"
    if sdists and expected_sdist_name not in {p.name for p in sdists}:
        errors.append(f"Missing expected sdist artifact: {expected_sdist_name}")

    for wheel in wheels:
        if f"tensor_grep-{version}-" not in wheel.name:
            errors.append(f"Wheel filename version mismatch: {wheel.name}")
            continue
        try:
            wheel_version = _wheel_metadata_version(wheel)
        except Exception as exc:
            errors.append(f"Failed to inspect wheel metadata {wheel.name}: {exc}")
            continue
        if wheel_version != version:
            errors.append(
                f"Wheel metadata version mismatch: {wheel.name} has {wheel_version}, expected {version}"
            )

    for sdist in sdists:
        if sdist.name != expected_sdist_name:
            errors.append(f"Unexpected sdist filename: {sdist.name}")
        try:
            sdist_version = _sdist_metadata_version(sdist)
        except Exception as exc:
            errors.append(f"Failed to inspect sdist metadata {sdist.name}: {exc}")
            continue
        if sdist_version != version:
            errors.append(
                f"sdist metadata version mismatch: {sdist.name} has {sdist_version}, expected {version}"
            )

    if require_platforms:
        wheel_names = [p.name.lower() for p in wheels]
        for platform in require_platforms:
            platform_lower = platform.lower().strip()
            if platform_lower == "linux":
                found = any("linux" in name or "manylinux" in name for name in wheel_names)
            elif platform_lower == "macos":
                found = any("macosx" in name for name in wheel_names)
            elif platform_lower == "windows":
                found = any("win" in name for name in wheel_names)
            else:
                found = any(platform_lower in name for name in wheel_names)
            if not found:
                errors.append(f"Missing required wheel platform artifact: {platform_lower}")

    return errors


def main() -> int:
    parser = argparse.ArgumentParser(description="Validate built PyPI artifacts before publish.")
    parser.add_argument(
        "--dist-dir", type=Path, default=Path("dist"), help="Distribution directory"
    )
    parser.add_argument(
        "--version", required=True, help="Expected package version (without leading v)"
    )
    parser.add_argument(
        "--require-platforms",
        default="linux,macos,windows",
        help="Comma-separated platform tags that must be represented in wheel filenames",
    )
    args = parser.parse_args()

    required_platforms = [
        item.strip() for item in args.require_platforms.split(",") if item.strip()
    ]
    errors = validate(
        dist_dir=args.dist_dir,
        version=args.version,
        require_platforms=required_platforms,
    )
    if errors:
        for err in errors:
            print(f"ERROR: {err}")
        return 1
    print("PyPI artifact validation passed.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
