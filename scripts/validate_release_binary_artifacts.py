from __future__ import annotations

import argparse
import hashlib
from pathlib import Path

EXPECTED_BINARIES = {
    "tg-linux-amd64-cpu",
    "tg-linux-amd64-nvidia",
    "tg-macos-amd64-cpu",
    "tg-windows-amd64-cpu.exe",
    "tg-windows-amd64-nvidia.exe",
}


def _iter_release_binaries(artifacts_dir: Path) -> list[Path]:
    return sorted(
        path for path in artifacts_dir.rglob("tg-*") if path.is_file() and path.name != "tg-*"
    )


def build_hash_matrix(artifacts_dir: Path) -> dict[str, str]:
    matrix: dict[str, str] = {}
    for artifact in _iter_release_binaries(artifacts_dir):
        matrix[artifact.name] = hashlib.sha256(artifact.read_bytes()).hexdigest()
    return matrix


def validate_artifacts(artifacts_dir: Path) -> list[str]:
    errors: list[str] = []
    found = {path.name for path in _iter_release_binaries(artifacts_dir)}
    if not found:
        return [f"No release binaries found under {artifacts_dir}"]

    missing = sorted(EXPECTED_BINARIES - found)
    unexpected = sorted(found - EXPECTED_BINARIES)
    if missing:
        errors.append(f"Missing expected release binaries: {', '.join(missing)}")
    if unexpected:
        errors.append(f"Unexpected release binaries present: {', '.join(unexpected)}")

    matrix = build_hash_matrix(artifacts_dir)
    for filename, digest in matrix.items():
        if len(digest) != 64:
            errors.append(f"Invalid SHA256 digest length for {filename}")

    return errors


def write_checksums(artifacts_dir: Path, output_path: Path) -> None:
    matrix = build_hash_matrix(artifacts_dir)
    lines = [f"{digest}  {name}" for name, digest in sorted(matrix.items())]
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text("\n".join(lines) + ("\n" if lines else ""), encoding="utf-8")


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Validate GitHub release binary artifact matrix and emit checksums."
    )
    parser.add_argument(
        "--artifacts-dir",
        type=Path,
        default=Path("artifacts"),
        help="Directory containing downloaded build artifacts",
    )
    parser.add_argument(
        "--checksums-out",
        type=Path,
        default=Path("artifacts") / "CHECKSUMS.txt",
        help="Output path for SHA256 checksums",
    )
    args = parser.parse_args()

    errors = validate_artifacts(args.artifacts_dir)
    if errors:
        for err in errors:
            print(f"ERROR: {err}")
        return 1

    write_checksums(args.artifacts_dir, args.checksums_out)
    print(f"Release binary artifact validation passed. Checksums written to {args.checksums_out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
