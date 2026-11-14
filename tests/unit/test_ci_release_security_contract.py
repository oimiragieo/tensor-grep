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
        "Checkout publisher source",
        "Install uv",
        "Setup Python",
        "Download native release front doors",
        "Download tag-matched Python wheel",
        "Install cargo-cyclonedx",
        "Generate Rust SBOM",
        "Prepare clean Python SBOM environments",
        "Validate release SBOM identity",
        "Validate release asset matrix and generate checksums",
        "Build package-manager publish bundle",
        "Verify package-manager bundle checksums",
        "Smoke-test package-manager bundle contracts",
        "Prepare release asset path list",
        "Generate SLSA provenance",
        "Verify SLSA provenance",
        "Sign release subjects with Sigstore",
        "Upload GitHub release assets",
        "Verify GitHub release asset coverage",
    ]
    steps: list[dict[str, object]] = [{"name": name, "run": "true"} for name in step_names]
    runs = {
        "Setup Python": "uv python install 3.12",
        "Install cargo-cyclonedx": "cargo install --locked cargo-cyclonedx --version 0.5.10",
        "Generate Rust SBOM": """uv run --no-project --python 3.12 -- python scripts/generate_release_rust_sbom.py \\
--expected-profile \"$RELEASE_NATIVE_ASSET_PROFILE\"""",
        "Prepare clean Python SBOM environments": """uv export --locked --no-dev --no-emit-project \\
--format requirements.txt --output-file "$RUNNER_TEMP/tg-core-requirements.txt"
uv venv --python 3.12 .sbom-python-core
uv pip sync --python .sbom-python-core/bin/python --require-hashes \\
"$RUNNER_TEMP/tg-core-requirements.txt"
uv pip install --python .sbom-python-core/bin/python --no-deps artifacts/wheel/*.whl
uv venv --python 3.12 .sbom-python-tools
uv pip sync --python .sbom-python-tools/bin/python --require-hashes \\
--only-binary :all: scripts/release-sbom-tools.txt
.sbom-python-tools/bin/cyclonedx-py environment -E .sbom-python-core/bin/python \\
--pyproject pyproject.toml --output-file artifacts/sbom-python.json""",
        "Validate release SBOM identity": """uv run --no-project --python 3.12 -- python scripts/validate_release_sbom_identity.py \\
--rust-sbom artifacts/sbom-rust.json --python-sbom artifacts/sbom-python.json \\
--expected-version \"$RELEASE_VERSION\"""",
        "Validate release asset matrix and generate checksums": """uv run --no-project --python 3.12 -- python scripts/validate_release_binary_artifacts.py \\
--artifacts-dir artifacts --checksums-out artifacts/CHECKSUMS.txt \\
--additional-checksum-asset artifacts/sbom-rust.json \\
--additional-checksum-asset artifacts/sbom-python.json \\
--expected-profile \"$RELEASE_NATIVE_ASSET_PROFILE\"""",
        "Build package-manager publish bundle": """uv run --no-project --python 3.12 --with pyyaml==6.0.3 -- python scripts/prepare_package_manager_release.py \\
--output-dir artifacts/package-manager-bundle --release-checksums artifacts/CHECKSUMS.txt""",
        "Verify package-manager bundle checksums": """uv run --no-project --python 3.12 -- python scripts/verify_package_manager_bundle_checksums.py \\
--bundle-dir artifacts/package-manager-bundle""",
        "Smoke-test package-manager bundle contracts": """uv run --no-project --python 3.12 --with pyyaml==6.0.3 -- python scripts/smoke_test_package_manager_bundle.py \\
--bundle-dir artifacts/package-manager-bundle --expected-version \"$RELEASE_VERSION\"""",
        "Prepare release asset path list": """uv run --no-project --python 3.12 -- python scripts/prepare_release_asset_paths.py \\
--artifacts-dir artifacts --expected-profile \"$RELEASE_NATIVE_ASSET_PROFILE\" \\
--github-output \"$GITHUB_OUTPUT\"""",
        "Verify SLSA provenance": """uv run --no-project --python 3.12 -- python scripts/verify_release_subject_attestations.py \\
--artifacts-dir artifacts --expected-profile \"$RELEASE_NATIVE_ASSET_PROFILE\" \\
--repository \"$GITHUB_REPOSITORY\"""",
        "Verify GitHub release asset coverage": """uv run --no-project --python 3.12 -- python scripts/verify_github_release_assets.py \\
--repo "$GITHUB_REPOSITORY" --tag "v${{ needs.release.outputs.release_version }}" \\
--token "${{ secrets.GITHUB_TOKEN }}" \\
--local-artifacts-dir artifacts --expected-profile \"$RELEASE_NATIVE_ASSET_PROFILE\" \\
--wait-seconds 120 --poll-interval-seconds 5""",
    }
    for step in steps:
        if step["name"] in runs:
            step["run"] = runs[str(step["name"])]
    setup_actions = {
        "Checkout publisher source": {
            "uses": "actions/checkout@v6",
            "with": {"ref": "v${{ needs.release.outputs.release_version }}"},
        },
        "Install uv": {"uses": "astral-sh/setup-uv@v8.0.0"},
        "Download native release front doors": {
            "uses": "actions/download-artifact@v8",
            "with": {"pattern": "release-native-*", "path": "artifacts/native"},
        },
        "Download tag-matched Python wheel": {
            "uses": "actions/download-artifact@v8",
            "with": {"name": "pypi-wheels-ubuntu-latest", "path": "artifacts/wheel"},
        },
    }
    for name, values in setup_actions.items():
        steps[step_names.index(name)] = {"name": name, **values}
    next(step for step in steps if step["name"] == "Prepare release asset path list")["id"] = (
        "release_paths"
    )
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
                "name": "publish-github-release-assets",
                "if": "needs.release.outputs.released == 'true'",
                "needs": ["release", "build-release-native-assets", "build-wheels-pypi"],
                "runs-on": "ubuntu-latest",
                "permissions": {"contents": "write", "id-token": "write", "attestations": "write"},
                "env": {
                    "RELEASE_NATIVE_ASSET_PROFILE": "${{ vars.TENSOR_GREP_RELEASE_NATIVE_ASSET_PROFILE || 'native-frontdoor' }}",
                    "RELEASE_VERSION": "${{ needs.release.outputs.release_version }}",
                    "GITHUB_REPOSITORY": "${{ github.repository }}",
                },
                "steps": steps,
            }
        }
    }


def _errors(workflow: dict[str, object]) -> list[str]:
    return validate_ci_release_security_contract(
        ci_workflow=yaml.safe_dump(workflow, sort_keys=False)
    )


def _step(workflow: dict[str, object], name: str) -> dict[str, object]:
    return next(  # type: ignore[return-value,union-attr]
        step
        for step in workflow["jobs"]["publisher"]["steps"]
        if step["name"] == name  # type: ignore[index]
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
    signing = _step(workflow, "Sign release subjects with Sigstore")
    signing["with"]["verify-cert-identity"] = "https://example.invalid/workflow"  # type: ignore[index]
    assert any("verify-cert-identity" in error for error in _errors(workflow))


def test_release_security_contract_rejects_unuploaded_bundle() -> None:
    workflow = _workflow()
    upload = _step(workflow, "Upload GitHub release assets")
    upload["with"]["files"] = "${{ steps.release_paths.outputs.upload_files }}"  # type: ignore[index]
    assert any("exactly the managed assets" in error for error in _errors(workflow))


def test_release_security_contract_rejects_second_publisher() -> None:
    workflow = _workflow()
    workflow["jobs"]["second"] = deepcopy(workflow["jobs"]["publisher"])  # type: ignore[index]
    assert _errors(workflow) == [
        "CI release security contract requires exactly one GitHub release publisher job"
    ]


@pytest.mark.parametrize(
    ("step_name", "removed", "expected"),
    [
        ("Install cargo-cyclonedx", "--locked", "cargo install"),
        ("Generate Rust SBOM", "--expected-profile", "generate_release_rust_sbom.py"),
        ("Prepare clean Python SBOM environments", "--require-hashes", "uv pip sync"),
        ("Validate release SBOM identity", "--rust-sbom", "validate_release_sbom_identity.py"),
        (
            "Validate release asset matrix and generate checksums",
            "--checksums-out",
            "validate_release_binary",
        ),
        ("Prepare release asset path list", "--github-output", "prepare_release_asset_paths.py"),
        ("Verify SLSA provenance", "--repository", "verify_release_subject_attestations.py"),
        (
            "Verify GitHub release asset coverage",
            "--local-artifacts-dir",
            "verify_github_release_assets.py",
        ),
    ],
)
def test_release_security_contract_rejects_missing_command_contract(
    step_name: str, removed: str, expected: str
) -> None:
    workflow = _workflow()
    step = _step(workflow, step_name)
    step["run"] = str(step["run"]).replace(removed, "")
    assert any(expected in error for error in _errors(workflow))


@pytest.mark.parametrize(
    "bypass",
    [{"if": "false"}, {"continue-on-error": True}, {"continue-on-error": "${{ true }}"}],
)
def test_release_security_contract_rejects_bypassed_security_step(
    bypass: dict[str, object],
) -> None:
    workflow = _workflow()
    step = _step(workflow, "Verify SLSA provenance")
    step.update(bypass)
    assert any("must not bypass `Verify SLSA provenance`" in error for error in _errors(workflow))


@pytest.mark.parametrize("prefix", ["# ", "echo "])
def test_release_security_contract_rejects_nonexecuted_command_text(prefix: str) -> None:
    workflow = _workflow()
    step = _step(workflow, "Verify SLSA provenance")
    step["run"] = prefix + str(step["run"])
    assert any("verify_release_subject_attestations.py" in error for error in _errors(workflow))


@pytest.mark.parametrize(
    "run",
    [
        'gh attestation verify --help # --repo "$GITHUB_REPOSITORY" --signer-workflow "$GITHUB_REPOSITORY/.github/workflows/ci.yml" --source-ref refs/heads/main',
        'uv run echo python scripts/validate_release_sbom_identity.py --rust-sbom artifacts/sbom-rust.json --python-sbom artifacts/sbom-python.json --expected-version "$RELEASE_VERSION"',
        'gh attestation verify subject --repo "$GITHUB_REPOSITORY" --signer-workflow "$GITHUB_REPOSITORY/.github/workflows/ci.yml" --source-ref refs/heads/main ||:',
        'gh attestation verify subject --repo "$GITHUB_REPOSITORY" --signer-workflow "$GITHUB_REPOSITORY/.github/workflows/ci.yml" --source-ref refs/heads/main &',
    ],
)
def test_release_security_contract_rejects_executable_noop(run: str) -> None:
    workflow = _workflow()
    name = "Verify SLSA provenance" if run.startswith("gh ") else "Validate release SBOM identity"
    _step(workflow, name)["run"] = run
    assert _errors(workflow)


def test_release_security_contract_rejects_duplicate_security_option() -> None:
    workflow = _workflow()
    step = _step(workflow, "Verify SLSA provenance")
    step["run"] = str(step["run"]) + " --repo attacker/example"
    assert any("verify_release_subject_attestations.py" in error for error in _errors(workflow))


@pytest.mark.parametrize("extra", ["--help", "-R attacker/example", "--repo=attacker/example"])
def test_release_security_contract_rejects_extra_security_argv(extra: str) -> None:
    workflow = _workflow()
    step = _step(workflow, "Verify SLSA provenance")
    step["run"] = str(step["run"]) + f" {extra}"
    assert any("verify_release_subject_attestations.py" in error for error in _errors(workflow))


@pytest.mark.parametrize(
    "extra",
    ["printf malicious > artifacts/sbom-rust.json", "gh() { echo shadowed; }"],
)
def test_release_security_contract_rejects_extra_security_command(extra: str) -> None:
    workflow = _workflow()
    step = _step(workflow, "Verify SLSA provenance")
    step["run"] = extra + "\n" + str(step["run"])
    assert any("only its exact commands" in error for error in _errors(workflow))


@pytest.mark.parametrize("position", [0, 5])
def test_release_security_contract_rejects_unsealed_step(position: int) -> None:
    workflow = _workflow()
    steps = workflow["jobs"]["publisher"]["steps"]  # type: ignore[index]
    steps.insert(position, {"name": "Mutate artifacts", "run": "true"})
    assert any("steps must equal the sealed sequence" in error for error in _errors(workflow))


def test_release_security_contract_rejects_step_environment_injection() -> None:
    workflow = _workflow()
    _step(workflow, "Verify SLSA provenance")["env"] = {"PATH": "/tmp/shims"}
    assert any("sealed fields" in error for error in _errors(workflow))


@pytest.mark.parametrize("fault", ["id", "output", "rust-profile"])
def test_release_security_contract_binds_outputs_and_rust_features(fault: str) -> None:
    workflow = _workflow()
    if fault == "id":
        _step(workflow, "Prepare release asset path list")["id"] = "wrong"
    elif fault == "output":
        step = _step(workflow, "Prepare release asset path list")
        step["run"] = str(step["run"]).replace("$GITHUB_OUTPUT", "$RUNNER_TEMP/out")
    else:
        step = _step(workflow, "Generate Rust SBOM")
        step["run"] = str(step["run"]).replace("--expected-profile", "")
    assert _errors(workflow)


def test_release_security_contract_rejects_second_action_in_same_job() -> None:
    workflow = _workflow()
    steps = workflow["jobs"]["publisher"]["steps"]  # type: ignore[index]
    steps.insert(0, {"name": "Alternate upload", "uses": "softprops/action-gh-release@v3"})
    assert any("exactly one GitHub release action" in error for error in _errors(workflow))


@pytest.mark.parametrize(
    "command",
    ["gh release upload v1 artifact", "if true; then command gh release upload v1 artifact; fi"],
)
def test_release_security_contract_rejects_direct_release_command(command: str) -> None:
    workflow = _workflow()
    steps = workflow["jobs"]["publisher"]["steps"]  # type: ignore[index]
    steps.insert(0, {"name": "Alternate upload", "run": command})
    assert any("direct release publication" in error for error in _errors(workflow))


def test_release_security_contract_rejects_extra_upload_glob() -> None:
    workflow = _workflow()
    upload = _step(workflow, "Upload GitHub release assets")
    upload["with"]["files"] += "\nartifacts/**"  # type: ignore[index,operator]
    assert any("exactly the managed assets" in error for error in _errors(workflow))


def test_release_security_contract_rejects_malformed_workflow() -> None:
    errors = validate_ci_release_security_contract(ci_workflow="jobs: [")
    assert any("cannot parse workflow YAML" in error for error in errors)


def test_release_security_contract_rejects_non_mapping_jobs() -> None:
    assert validate_ci_release_security_contract(ci_workflow="jobs: []") == [
        "CI release security contract requires a jobs mapping"
    ]


@pytest.mark.parametrize(
    ("fault", "expected"),
    [
        ("job-gate", "semantic-release tag"),
        ("needs", "tag-matched Python wheel"),
        ("order", "steps must equal the sealed sequence"),
        ("attest-subject", "native/SBOM/checksum subjects"),
        ("upload-tag", "semantic-release tag"),
        ("upload-unmatched", "fail on unmatched files"),
    ],
)
def test_release_security_contract_rejects_control_plane_drift(fault: str, expected: str) -> None:
    workflow = _workflow()
    publisher = workflow["jobs"]["publisher"]  # type: ignore[index]
    steps = publisher["steps"]
    if fault == "job-gate":
        publisher["if"] = "always()"
    elif fault == "needs":
        publisher["needs"].remove("build-wheels-pypi")
    elif fault == "order":
        steps[5], steps[6] = steps[6], steps[5]
    else:
        step_name = (
            "Generate SLSA provenance"
            if fault == "attest-subject"
            else "Upload GitHub release assets"
        )
        step = next(item for item in steps if item["name"] == step_name)
        key = {
            "attest-subject": "subject-path",
            "upload-tag": "tag_name",
            "upload-unmatched": "fail_on_unmatched_files",
        }[fault]
        step["with"][key] = "wrong"
    assert any(expected in error for error in _errors(workflow))
