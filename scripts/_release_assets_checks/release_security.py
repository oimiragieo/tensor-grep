"""Release publisher security contracts."""

from __future__ import annotations

import re
from itertools import pairwise

import yaml

from .helpers import _normalize_pinned_actions

_AUTHORITATIVE_PUBLISHER = ".github/workflows/ci.yml"
_PUBLISH_ACTIONS = (
    "softprops/action-gh-release",
    "pypa/gh-action-pypi-publish",
    "python-semantic-release/python-semantic-release",
)
_PUBLISH_COMMAND = re.compile(
    r"(?m)^\s*(?:gh\s+release\s+(?:create|upload)\b|npm\s+publish\b|"
    r"(?:twine|maturin)\s+(?:upload|publish)\b)"
)


def validate_ci_release_security_contract(*, ci_workflow: str) -> list[str]:
    """Require the authoritative publisher's provenance and signing spine."""
    try:
        parsed = yaml.safe_load(_normalize_pinned_actions(ci_workflow)) or {}
    except yaml.YAMLError as exc:
        return [f"CI release security contract cannot parse workflow YAML: {exc}"]
    jobs = parsed.get("jobs", {}) if isinstance(parsed, dict) else {}
    if not isinstance(jobs, dict):
        return ["CI release security contract requires a jobs mapping"]

    publishers = []
    for name, job in jobs.items():
        if not isinstance(job, dict):
            continue
        steps = job.get("steps", [])
        if isinstance(steps, list) and any(
            isinstance(step, dict)
            and str(step.get("uses", "")).startswith("softprops/action-gh-release@")
            for step in steps
        ):
            publishers.append((str(name), job, steps))
    if len(publishers) != 1:
        return ["CI release security contract requires exactly one GitHub release publisher job"]

    job_name, publisher, steps = publishers[0]
    errors: list[str] = []
    permissions = publisher.get("permissions", {})
    if not isinstance(permissions, dict):
        permissions = {}
    for permission in ("contents", "id-token", "attestations"):
        if permissions.get(permission) != "write":
            errors.append(f"CI release publisher `{job_name}` must request `{permission}: write`")
    if publisher.get("if") != "needs.release.outputs.released == 'true'":
        errors.append("CI release publisher must run only for a semantic-release tag")
    needs = publisher.get("needs", [])
    needs = [needs] if isinstance(needs, str) else needs if isinstance(needs, list) else []
    if not {"release", "build-release-native-assets", "build-wheels-pypi"}.issubset(set(needs)):
        errors.append(
            "CI release publisher must depend on semantic release, native assets, and the tag-matched Python wheel"
        )

    named = {
        str(step.get("name")): step for step in steps if isinstance(step, dict) and step.get("name")
    }
    required = (
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
    )
    for name in required:
        if name not in named:
            errors.append(f"CI release publisher is missing `{name}`")
    if errors:
        return errors

    positions = {str(step.get("name")): index for index, step in enumerate(steps)}
    for earlier, later in pairwise(required):
        if positions[earlier] >= positions[later]:
            errors.append(f"CI release publisher must run `{earlier}` before `{later}`")

    runs = {name: str(step.get("run", "")) for name, step in named.items()}
    if (
        "cargo install --locked cargo-cyclonedx --version 0.5.10"
        not in runs["Install cargo-cyclonedx"]
    ):
        errors.append("CI release publisher must install the locked cargo-cyclonedx 0.5.10 tool")
    rust_sbom = runs["Generate Rust SBOM"]
    for expected in (
        "cargo cyclonedx",
        "--format json",
        "--target all",
        "--override-filename",
        "--no-default-features",
        "--features cuda",
    ):
        if expected not in rust_sbom:
            errors.append(f"CI release Rust SBOM generation must include `{expected}`")
    if not re.search(r"(?m)^\s*--override-filename\s+sbom-rust(?:\s|$)", rust_sbom):
        errors.append(
            "CI release Rust SBOM generation must set `--override-filename sbom-rust` as the exact stem"
        )

    python_sbom = runs["Prepare clean Python SBOM environments"]
    for expected in (
        "uv export --locked --no-dev --no-emit-project",
        "uv pip sync",
        "--require-hashes",
        "scripts/release-sbom-tools.txt",
        "cyclonedx-py environment",
        "-E",
        "--output-file artifacts/sbom-python.json",
    ):
        if expected not in python_sbom:
            errors.append(f"CI release Python SBOM environment must include `{expected}`")

    identity_step = named["Validate release SBOM identity"]
    identity_env = identity_step.get("env", {})
    identity = runs["Validate release SBOM identity"] + str(
        identity_env.get("RELEASE_VERSION", "") if isinstance(identity_env, dict) else ""
    )
    for expected in (
        "sbom-rust.json",
        "sbom-python.json",
        "release_version",
        'component.get("name")',
        'component.get("version")',
        "Cargo.toml",
        "pyproject.toml",
    ):
        if expected not in identity:
            errors.append(f"CI release SBOM identity validation must include `{expected}`")

    checksum = runs["Validate release asset matrix and generate checksums"]
    for expected in (
        "scripts/validate_release_binary_artifacts.py",
        "--artifacts-dir artifacts",
        "--checksums-out artifacts/CHECKSUMS.txt",
        "--additional-checksum-asset artifacts/sbom-rust.json",
        "--additional-checksum-asset artifacts/sbom-python.json",
        '--expected-profile "$RELEASE_NATIVE_ASSET_PROFILE"',
    ):
        if expected not in checksum:
            errors.append(f"CI release checksum generation must include `{expected}`")

    path_run = runs["Prepare release asset path list"]
    for expected in (
        "tg-linux-amd64-cpu",
        "tg-macos-amd64-cpu",
        "tg-windows-amd64-cpu.exe",
        "tg-linux-amd64-nvidia",
        "tg-windows-amd64-nvidia.exe",
        "sbom-rust.json",
        "sbom-python.json",
        "CHECKSUMS.txt",
        "upload_files",
        "subject_paths",
    ):
        if expected not in path_run:
            errors.append(f"CI release asset path preparation must include `{expected}`")

    verify_attest = runs["Verify SLSA provenance"]
    for expected in (
        "gh attestation verify",
        '--repo "$GITHUB_REPOSITORY"',
        '--signer-workflow "$GITHUB_REPOSITORY/.github/workflows/ci.yml"',
        "--source-ref refs/heads/main",
    ):
        if expected not in verify_attest:
            errors.append(f"CI release provenance verification must include `{expected}`")

    verify_upload = runs["Verify GitHub release asset coverage"]
    for expected in (
        "uv run --no-project",
        "scripts/verify_github_release_assets.py",
        "--local-artifacts-dir artifacts",
        '--expected-profile "$RELEASE_NATIVE_ASSET_PROFILE"',
        "--wait-seconds",
        "--poll-interval-seconds",
    ):
        if expected not in verify_upload:
            errors.append(f"CI release upload verification must include `{expected}`")

    attest = named["Generate SLSA provenance"]
    if attest.get("uses") != "actions/attest-build-provenance@v4":
        errors.append("CI release publisher must use the pinned build-provenance action")
    attest_with = attest.get("with", {})
    if not isinstance(attest_with, dict) or attest_with.get("subject-path") != (
        "${{ steps.release_paths.outputs.subject_paths }}"
    ):
        errors.append("CI release provenance must attest only the native/SBOM/checksum subjects")

    signing = named["Sign release subjects with Sigstore"]
    if signing.get("uses") != "sigstore/gh-action-sigstore-python@v3.0.0":
        errors.append("CI release publisher must use the pinned Sigstore signing action")
    signing_with = signing.get("with", {})
    if not isinstance(signing_with, dict):
        signing_with = {}
    expected_signing = {
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
    }
    for key, value in expected_signing.items():
        if signing_with.get(key) != value:
            errors.append(f"CI release Sigstore step must set `{key}` to `{value}`")

    upload = named["Upload GitHub release assets"]
    if upload.get("uses") != "softprops/action-gh-release@v3":
        errors.append("CI release publisher must use the pinned GitHub release action")
    upload_with = upload.get("with", {})
    if not isinstance(upload_with, dict):
        upload_with = {}
    if upload_with.get("tag_name") != "v${{ needs.release.outputs.release_version }}":
        errors.append("CI release upload must use the semantic-release tag")
    if upload_with.get("fail_on_unmatched_files") is not True:
        errors.append("CI release upload must fail on unmatched files")
    upload_files = str(upload_with.get("files", ""))
    for expected in (
        "${{ steps.release_paths.outputs.upload_files }}",
        "artifacts/native/**/tg-*.sigstore.json",
        "artifacts/sbom-rust.json.sigstore.json",
        "artifacts/sbom-python.json.sigstore.json",
        "artifacts/CHECKSUMS.txt.sigstore.json",
    ):
        if expected not in upload_files:
            errors.append(f"CI release upload must explicitly cover `{expected}`")
    return errors


def validate_release_publisher_census(*, workflows: dict[str, str]) -> list[str]:
    """Require every registered release publisher to live in authoritative CI."""
    errors: list[str] = []
    publishers: dict[str, set[str]] = {}
    for filename, content in workflows.items():
        try:
            parsed = yaml.safe_load(_normalize_pinned_actions(content)) or {}
        except yaml.YAMLError as exc:
            errors.append(f"Workflow publisher census cannot parse {filename}: {exc}")
            continue
        jobs = parsed.get("jobs", {}) if isinstance(parsed, dict) else {}
        if not isinstance(jobs, dict):
            continue
        for job_name, job in jobs.items():
            if not isinstance(job, dict):
                continue
            steps = job.get("steps", [])
            if not isinstance(steps, list):
                errors.append(
                    f"Workflow publisher census requires {filename}:{job_name} steps to be a list"
                )
                continue
            for step in steps:
                if not isinstance(step, dict):
                    continue
                action = str(step.get("uses", ""))
                run = "\n".join(
                    line
                    for line in str(step.get("run", "")).splitlines()
                    if not line.lstrip().startswith("#")
                )
                if any(action.startswith(prefix + "@") for prefix in _PUBLISH_ACTIONS) or (
                    _PUBLISH_COMMAND.search(run)
                ):
                    publishers.setdefault(filename, set()).add(str(job_name))

    if _AUTHORITATIVE_PUBLISHER not in publishers:
        errors.append("Workflow publisher census did not find the authoritative CI publisher")
    for filename in sorted(set(publishers) - {_AUTHORITATIVE_PUBLISHER}):
        jobs = ", ".join(sorted(publishers[filename]))
        errors.append(
            "Release publishing is only allowed in .github/workflows/ci.yml; "
            f"found {filename}:{jobs}"
        )
    return errors
