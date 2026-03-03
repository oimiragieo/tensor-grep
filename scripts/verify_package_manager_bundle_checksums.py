from __future__ import annotations

import argparse
import hashlib
from pathlib import Path


def _parse_checksums(content: str) -> dict[str, str]:
    entries: dict[str, str] = {}
    for raw in content.splitlines():
        line = raw.strip()
        if not line:
            continue
        parts = line.split()
        if len(parts) < 2:
            continue
        digest = parts[0].lower()
        rel = parts[-1]
        entries[rel] = digest
    return entries


def verify_bundle_checksums(*, bundle_dir: Path) -> list[str]:
    errors: list[str] = []
    checksums_path = bundle_dir / "BUNDLE_CHECKSUMS.txt"
    if not checksums_path.exists():
        return [f"Missing bundle checksum manifest: {checksums_path}"]

    entries = _parse_checksums(checksums_path.read_text(encoding="utf-8"))
    if not entries:
        return ["BUNDLE_CHECKSUMS.txt is empty or invalid"]

    for rel, expected_digest in entries.items():
        if len(expected_digest) != 64:
            errors.append(f"Invalid digest length for {rel} in BUNDLE_CHECKSUMS.txt")
            continue
        path = bundle_dir / Path(rel)
        if not path.exists():
            errors.append(f"Bundle file listed in checksums missing on disk: {rel}")
            continue
        digest = hashlib.sha256(path.read_bytes()).hexdigest()
        if digest != expected_digest:
            errors.append(f"Checksum mismatch for bundle file: {rel}")

    for path in bundle_dir.rglob("*"):
        if not path.is_file() or path == checksums_path:
            continue
        rel = path.relative_to(bundle_dir).as_posix()
        if rel not in entries:
            errors.append(f"Bundle file missing from BUNDLE_CHECKSUMS.txt: {rel}")
    return errors


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Verify package-manager bundle checksums against generated files."
    )
    parser.add_argument(
        "--bundle-dir",
        type=Path,
        default=Path("artifacts") / "package-manager-bundle",
        help="Path to package-manager bundle directory",
    )
    args = parser.parse_args()

    errors = verify_bundle_checksums(bundle_dir=args.bundle_dir)
    if errors:
        for err in errors:
            print(f"ERROR: {err}")
        return 1
    print("Package-manager bundle checksums verified.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
