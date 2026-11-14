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
        "Generate Rust SBOM",
        "Prepare clean Python SBOM environments",
        "Validate release asset matrix and generate checksums",
        "Prepare release asset path list",
        "Generate SLSA provenance",
        "Verify SLSA provenance",
        "Sign release subjects with Sigstore",
        "Upload GitHub release assets",
        "Verify GitHub release asset coverage",
    ]
    steps: list[dict[str, object]] = [{"name": name, "run": "true"} for name in step_names]
    steps[4] = {
        "name": step_names[4],
        "uses": "actions/attest-build-provenance@v4",
        "with": {"subject-path": "${{ steps.release_paths.outputs.subject_paths }}"},
    }
    steps[6] = {
        "name": step_names[6],
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
    steps[7] = {
        "name": step_names[7],
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
    signing = workflow["jobs"]["publisher"]["steps"][6]  # type: ignore[index]
    signing["with"]["verify-cert-identity"] = "https://example.invalid/workflow"  # type: ignore[index]
    assert any("verify-cert-identity" in error for error in _errors(workflow))


def test_release_security_contract_rejects_unuploaded_bundle() -> None:
    workflow = _workflow()
    upload = workflow["jobs"]["publisher"]["steps"][7]  # type: ignore[index]
    upload["with"]["files"] = "${{ steps.release_paths.outputs.upload_files }}"  # type: ignore[index]
    assert any("sigstore.json" in error for error in _errors(workflow))


def test_release_security_contract_rejects_second_publisher() -> None:
    workflow = _workflow()
    workflow["jobs"]["second"] = deepcopy(workflow["jobs"]["publisher"])  # type: ignore[index]
    assert _errors(workflow) == [
        "CI release security contract requires exactly one GitHub release publisher job"
    ]
