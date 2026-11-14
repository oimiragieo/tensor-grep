from __future__ import annotations

import argparse
import json
import tomllib
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def validate_sbom_identity(
    *,
    rust_sbom: Path,
    python_sbom: Path,
    expected_version: str,
    cargo_manifest: Path = ROOT / "rust_core" / "Cargo.toml",
    pyproject: Path = ROOT / "pyproject.toml",
) -> list[str]:
    expected = {
        rust_sbom: str(
            tomllib.loads(cargo_manifest.read_text(encoding="utf-8"))["package"]["name"]
        ),
        python_sbom: str(tomllib.loads(pyproject.read_text(encoding="utf-8"))["project"]["name"]),
    }
    errors: list[str] = []
    for path, expected_name in expected.items():
        try:
            document = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            errors.append(f"Unable to read release SBOM {path}: {exc}")
            continue
        if not isinstance(document, dict):
            errors.append(f"Release SBOM {path} must contain a JSON object")
            continue
        metadata = document.get("metadata")
        component = metadata.get("component") if isinstance(metadata, dict) else {}
        if not isinstance(component, dict):
            component = {}
        name = component.get("name")
        version = component.get("version")
        if name != expected_name or version != expected_version:
            errors.append(
                f"{path.name} root component {name!r}@{version!r} does not match "
                f"{expected_name!r}@{expected_version!r}"
            )
    return errors


def main() -> int:
    parser = argparse.ArgumentParser(description="Validate release SBOM project identity.")
    parser.add_argument("--rust-sbom", type=Path, required=True)
    parser.add_argument("--python-sbom", type=Path, required=True)
    parser.add_argument("--expected-version", required=True)
    args = parser.parse_args()
    errors = validate_sbom_identity(
        rust_sbom=args.rust_sbom,
        python_sbom=args.python_sbom,
        expected_version=args.expected_version,
    )
    for error in errors:
        print(f"ERROR: {error}")
    return int(bool(errors))


if __name__ == "__main__":
    raise SystemExit(main())
