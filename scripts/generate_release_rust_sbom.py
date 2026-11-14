from __future__ import annotations

import argparse
import subprocess
from collections.abc import Callable
from pathlib import Path

from validate_release_binary_artifacts import EXPECTED_BINARY_PROFILES

ROOT = Path(__file__).resolve().parents[1]


def generate_rust_sbom(
    *,
    expected_profile: str,
    output: Path,
    manifest: Path = ROOT / "rust_core" / "Cargo.toml",
    run: Callable[..., subprocess.CompletedProcess[object]] = subprocess.run,
) -> None:
    if expected_profile not in EXPECTED_BINARY_PROFILES:
        raise ValueError(f"Unknown release asset profile: {expected_profile}")
    generated = manifest.parent / "sbom-rust.json"
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
    output.parent.mkdir(parents=True, exist_ok=True)
    generated.replace(output)


def main() -> int:
    parser = argparse.ArgumentParser(description="Generate the profile-bound Rust release SBOM.")
    parser.add_argument(
        "--expected-profile", choices=sorted(EXPECTED_BINARY_PROFILES), required=True
    )
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    try:
        generate_rust_sbom(expected_profile=args.expected_profile, output=args.output)
    except (OSError, ValueError, subprocess.CalledProcessError) as exc:
        print(f"ERROR: {exc}")
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
