from __future__ import annotations

import argparse
import os
import shutil
import subprocess
from collections.abc import Callable
from pathlib import Path

from validate_release_binary_artifacts import EXPECTED_BINARY_PROFILES

ROOT = Path(__file__).resolve().parents[1]


def _matches_open_file(file_descriptor: int, path: Path) -> bool:
    return os.path.samestat(os.fstat(file_descriptor), os.stat(path, follow_symlinks=False))


def generate_rust_sbom(
    *,
    expected_profile: str,
    root: Path = ROOT,
    run: Callable[..., subprocess.CompletedProcess[object]] = subprocess.run,
) -> None:
    if expected_profile not in EXPECTED_BINARY_PROFILES:
        raise ValueError(f"Unknown release asset profile: {expected_profile}")
    resolved_root = root.resolve(strict=True)
    rust_dir = resolved_root / "rust_core"
    if rust_dir.is_symlink() or rust_dir.resolve(strict=True) != resolved_root / "rust_core":
        raise ValueError("Rust SBOM manifest directory must be confined to the repository")
    manifest = rust_dir / "Cargo.toml"
    artifacts = resolved_root / "artifacts"
    artifacts.mkdir(exist_ok=True)
    if artifacts.is_symlink() or artifacts.resolve(strict=True) != resolved_root / "artifacts":
        raise ValueError("Rust SBOM output directory must be a confined regular directory")
    generated = manifest.parent / "sbom-rust.json"
    output = artifacts / "sbom-rust.json"
    if generated.exists() or generated.is_symlink() or output.exists() or output.is_symlink():
        raise ValueError("Refusing to overwrite an existing Rust SBOM")
    command = [
        "cargo",
        "cyclonedx",
        "--manifest-path",
        str(manifest),
        "--format",
        "json",
        "--target",
        "all",
        "--override-filename",
        "sbom-rust",
        "--no-default-features",
    ]
    if expected_profile != "native-frontdoor":
        command.extend(("--features", "cuda"))
    run(command, check=True)
    if generated.is_symlink() or not generated.is_file():
        raise ValueError(f"cargo-cyclonedx did not produce a regular file: {generated}")
    nofollow = getattr(os, "O_NOFOLLOW", 0)
    source_fd = os.open(generated, os.O_RDONLY | nofollow)
    try:
        if not _matches_open_file(source_fd, generated):
            raise ValueError("Generated Rust SBOM identity changed while opening it")
        output_fd = os.open(output, os.O_WRONLY | os.O_CREAT | os.O_EXCL | nofollow, 0o644)
        output_stat = os.fstat(output_fd)
        complete = False
        try:
            if not _matches_open_file(output_fd, output):
                raise ValueError("Rust SBOM output identity changed while opening it")
            with (
                os.fdopen(source_fd, "rb", closefd=False) as source,
                os.fdopen(output_fd, "wb", closefd=False) as destination,
            ):
                shutil.copyfileobj(source, destination)
            complete = True
        finally:
            os.close(output_fd)
            if not complete:
                try:
                    if os.path.samestat(output_stat, os.stat(output, follow_symlinks=False)):
                        output.unlink()
                except OSError:
                    pass
    finally:
        os.close(source_fd)
    generated.unlink()


def main() -> int:
    parser = argparse.ArgumentParser(description="Generate the profile-bound Rust release SBOM.")
    parser.add_argument(
        "--expected-profile", choices=sorted(EXPECTED_BINARY_PROFILES), required=True
    )
    args = parser.parse_args()
    try:
        generate_rust_sbom(expected_profile=args.expected_profile)
    except (OSError, ValueError, subprocess.CalledProcessError) as exc:
        print(f"ERROR: {exc}")
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
