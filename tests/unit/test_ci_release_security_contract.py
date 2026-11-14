from __future__ import annotations

import importlib
import sys
from copy import deepcopy
from pathlib import Path

import pytest
import yaml

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "scripts"))
validate_ci_release_security_contract = importlib.import_module(
    "_release_assets_checks.release_security"
).validate_ci_release_security_contract


def _workflow() -> dict[str, object]:
    step_names = [
        "Install cargo-cyclonedx",
        "Generate Rust SBOM",
        "Prepare clean Python SBOM environments",
        "Validate release SBOM identity",
        "Validate release asset matrix and generate checksums",
        "Prepare release asset path list",
        "Generate SLSA provenance",
        "Verify SLSA provenance",
        "Sign release subjects with Sigstore",
        "Upload GitHub release assets",
        "Verify GitHub release asset coverage",
    ]
    steps: list[dict[str, object]] = [{"name": name, "run": "true"} for name in step_names]
    runs = {
        "Install cargo-cyclonedx": "cargo install --locked cargo-cyclonedx --version 0.5.10",
        "Generate Rust SBOM": """cargo cyclonedx --format json --target all \\
          --override-filename sbom-rust --no-default-features --features cuda""",
        "Prepare clean Python SBOM environments": """uv export --locked --no-dev --no-emit-project
uv pip sync --require-hashes
uv pip sync scripts/release-sbom-tools.txt
cyclonedx-py environment -E --output-file artifacts/sbom-python.json""",
        "Validate release SBOM identity": """release_version
sbom-rust.json sbom-python.json
component.get(\"name\") component.get(\"version\")
Cargo.toml pyproject.toml""",
        "Validate release asset matrix and generate checksums": """scripts/validate_release_binary_artifacts.py
--artifacts-dir artifacts --checksums-out artifacts/CHECKSUMS.txt
--additional-checksum-asset artifacts/sbom-rust.json
--additional-checksum-asset artifacts/sbom-python.json
--expected-profile \"$RELEASE_NATIVE_ASSET_PROFILE\"""",
        "Prepare release asset path list": """tg-linux-amd64-cpu tg-macos-amd64-cpu
tg-windows-amd64-cpu.exe tg-linux-amd64-nvidia tg-windows-amd64-nvidia.exe
sbom-rust.json sbom-python.json CHECKSUMS.txt upload_files subject_paths""",
        "Verify SLSA provenance": """gh attestation verify subject
--repo \"$GITHUB_REPOSITORY\"
--signer-workflow \"$GITHUB_REPOSITORY/.github/workflows/ci.yml\"
--source-ref refs/heads/main""",
        "Verify GitHub release asset coverage": """uv run --no-project scripts/verify_github_release_assets.py
--local-artifacts-dir artifacts --expected-profile \"$RELEASE_NATIVE_ASSET_PROFILE\"
--wait-seconds 120 --poll-interval-seconds 5""",
    }
    for step in steps:
        if step["name"] in runs:
            step["run"] = runs[str(step["name"])]
    attest_index = step_names.index("Generate SLSA provenance")
    steps[attest_index] = {
        "name": step_names[attest_index],
        "uses": "actions/attest-build-provenance@v4",
        "with": {"subject-path": "${{ steps.release_paths.outputs.subject_paths }}"},
    }
    signing_index = step_names.index("Sign release subjects with Sigstore")
    steps[signing_index] = {
        "name": step_names[signing_index],
        "uses": "sigstore/gh-action-sigstore-python@v3.0.0",
        "with": {
            "inputs": (
                "artifacts/native/**/tg-* artifacts/CHECKSUMS.txt "
                "artifacts/sbom-rust.json artifacts/sbom-python.json"
            ),
            "release-signing-artifacts": False,
            "verify": True,
            "verify-cert-identity": (
                "https://github.com/${{ github.repository }}/.github/workflows/ci.yml@refs/heads/main"
            ),
            "verify-oidc-issuer": "https://token.actions.githubusercontent.com",
        },
    }
    upload_index = step_names.index("Upload GitHub release assets")
    steps[upload_index] = {
        "name": step_names[upload_index],
        "uses": "softprops/action-gh-release@v3",
        "with": {
            "tag_name": "v${{ needs.release.outputs.release_version }}",
            "fail_on_unmatched_files": True,
            "files": "\n".join([
                "${{ steps.release_paths.outputs.upload_files }}",
                "artifacts/native/**/tg-*.sigstore.json",
                "artifacts/sbom-rust.json.sigstore.json",
                "artifacts/sbom-python.json.sigstore.json",
                "artifacts/CHECKSUMS.txt.sigstore.json",
            ]),
        },
    }
    return {
        "jobs": {
            "publisher": {
                "if": "needs.release.outputs.released == 'true'",
                "needs": ["release", "build-release-native-assets", "build-wheels-pypi"],
                "permissions": {"contents": "write", "id-token": "write", "attestations": "write"},
                "steps": steps,
            }
        }
    }


def _errors(workflow: dict[str, object]) -> list[str]:
    return validate_ci_release_security_contract(
        ci_workflow=yaml.safe_dump(workflow, sort_keys=False)
    )


def test_release_security_contract_accepts_complete_security_spine() -> None:
    assert _errors(_workflow()) == []


@pytest.mark.parametrize("permission", ["contents", "id-token", "attestations"])
def test_release_security_contract_requires_write_permissions(permission: str) -> None:
    workflow = _workflow()
    workflow["jobs"]["publisher"]["permissions"][permission] = "read"  # type: ignore[index]
    assert any(permission in error for error in _errors(workflow))


@pytest.mark.parametrize(
    ("step_name", "expected"),
    [
        ("Generate Rust SBOM", "Generate Rust SBOM"),
        ("Generate SLSA provenance", "Generate SLSA provenance"),
        ("Sign release subjects with Sigstore", "Sign release subjects with Sigstore"),
    ],
)
def test_release_security_contract_requires_each_capability(step_name: str, expected: str) -> None:
    workflow = _workflow()
    steps = workflow["jobs"]["publisher"]["steps"]  # type: ignore[index]
    workflow["jobs"]["publisher"]["steps"] = [  # type: ignore[index]
        step for step in steps if step["name"] != step_name
    ]
    assert any(expected in error for error in _errors(workflow))


def test_release_security_contract_rejects_signing_identity_drift() -> None:
    workflow = deepcopy(_workflow())
    signing = next(  # type: ignore[union-attr]
        step
        for step in workflow["jobs"]["publisher"]["steps"]  # type: ignore[index]
        if step["name"] == "Sign release subjects with Sigstore"
    )
    signing["with"]["verify-cert-identity"] = "https://example.invalid/workflow"  # type: ignore[index]
    assert any("verify-cert-identity" in error for error in _errors(workflow))


def test_release_security_contract_rejects_unuploaded_bundle() -> None:
    workflow = _workflow()
    upload = next(  # type: ignore[union-attr]
        step
        for step in workflow["jobs"]["publisher"]["steps"]  # type: ignore[index]
        if step["name"] == "Upload GitHub release assets"
    )
    upload["with"]["files"] = "${{ steps.release_paths.outputs.upload_files }}"  # type: ignore[index]
    assert any("sigstore.json" in error for error in _errors(workflow))


def test_release_security_contract_rejects_second_publisher() -> None:
    workflow = _workflow()
    workflow["jobs"]["second"] = deepcopy(workflow["jobs"]["publisher"])  # type: ignore[index]
    assert _errors(workflow) == [
        "CI release security contract requires exactly one GitHub release publisher job"
    ]


@pytest.mark.parametrize(
    ("step_name", "expected"),
    [
        ("Install cargo-cyclonedx", "cargo-cyclonedx 0.5.10"),
        ("Generate Rust SBOM", "cargo cyclonedx"),
        ("Prepare clean Python SBOM environments", "uv export"),
        ("Validate release SBOM identity", "sbom-rust.json"),
        ("Validate release asset matrix and generate checksums", "CHECKSUMS.txt"),
        ("Prepare release asset path list", "tg-linux-amd64-cpu"),
        ("Verify SLSA provenance", "gh attestation verify"),
        ("Verify GitHub release asset coverage", "verify_github_release_assets.py"),
    ],
)
def test_release_security_contract_rejects_missing_command_contract(
    step_name: str, expected: str
) -> None:
    workflow = _workflow()
    step = next(  # type: ignore[union-attr]
        item
        for item in workflow["jobs"]["publisher"]["steps"]  # type: ignore[index]
        if item["name"] == step_name
    )
    step["run"] = "true"
    assert any(expected in error for error in _errors(workflow))
