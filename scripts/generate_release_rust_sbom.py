from __future__ import annotations

import argparse
import os
import shutil
import stat
import subprocess
from collections.abc import Callable
from pathlib import Path

from validate_release_binary_artifacts import EXPECTED_BINARY_PROFILES

ROOT = Path(__file__).resolve().parents[1]


def _matches_open_file(file_descriptor: int, path: Path) -> bool:
    return os.path.samestat(os.fstat(file_descriptor), os.stat(path, follow_symlinks=False))


def _exists_at(name: str, directory: int) -> bool:
    try:
        os.stat(name, dir_fd=directory, follow_symlinks=False)
    except FileNotFoundError:
        return False
    return True


def _matches_open_at(file_descriptor: int, name: str, directory: int) -> bool:
    return os.path.samestat(
        os.fstat(file_descriptor),
        os.stat(name, dir_fd=directory, follow_symlinks=False),
    )


def generate_rust_sbom(
    *,
    expected_profile: str,
    root: Path = ROOT,
    run: Callable[..., subprocess.CompletedProcess[object]] = subprocess.run,
) -> None:
    if expected_profile not in EXPECTED_BINARY_PROFILES:
        raise ValueError(f"Unknown release asset profile: {expected_profile}")
    if not {os.open, os.stat, os.unlink}.issubset(os.supports_dir_fd):
        raise OSError("Rust SBOM publication requires directory-descriptor support")
    resolved_root = root.resolve(strict=True)
    rust_dir = resolved_root / "rust_core"
    manifest = rust_dir / "Cargo.toml"
    artifacts = resolved_root / "artifacts"
    directory_flags = os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW
    root_fd = os.open(resolved_root, directory_flags)
    try:
        rust_fd = os.open("rust_core", directory_flags, dir_fd=root_fd)
        try:
            artifacts_fd = os.open("artifacts", directory_flags, dir_fd=root_fd)
            try:
                if _exists_at("sbom-rust.json", rust_fd) or _exists_at(
                    "sbom-rust.json", artifacts_fd
                ):
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
                if not _matches_open_file(rust_fd, rust_dir) or not _matches_open_file(
                    artifacts_fd, artifacts
                ):
                    raise ValueError("Rust SBOM parent identity changed during generation")
                source_fd = os.open("sbom-rust.json", os.O_RDONLY | os.O_NOFOLLOW, dir_fd=rust_fd)
                try:
                    if not stat.S_ISREG(os.fstat(source_fd).st_mode) or not _matches_open_at(
                        source_fd, "sbom-rust.json", rust_fd
                    ):
                        raise ValueError("Generated Rust SBOM must be a stable regular file")
                    output_fd = os.open(
                        "sbom-rust.json",
                        os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW,
                        0o644,
                        dir_fd=artifacts_fd,
                    )
                    output_stat = os.fstat(output_fd)
                    complete = False
                    try:
                        if not _matches_open_at(output_fd, "sbom-rust.json", artifacts_fd):
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
                                if os.path.samestat(
                                    output_stat,
                                    os.stat(
                                        "sbom-rust.json",
                                        dir_fd=artifacts_fd,
                                        follow_symlinks=False,
                                    ),
                                ):
                                    os.unlink("sbom-rust.json", dir_fd=artifacts_fd)
                            except OSError:
                                pass
                finally:
                    os.close(source_fd)
                os.unlink("sbom-rust.json", dir_fd=rust_fd)
            finally:
                os.close(artifacts_fd)
        finally:
            os.close(rust_fd)
    finally:
        os.close(root_fd)


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
