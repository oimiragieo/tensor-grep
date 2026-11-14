"""Release publisher security contracts."""

from __future__ import annotations

import re
import shlex
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
    r"(?m)(?:\bgh\s+release\s+(?:create|upload)\b|\bnpm\s+publish\b|"
    r"(?:twine|maturin)\s+(?:upload|publish)\b)"
)


def _shell_commands(script: str) -> list[str]:
    """Return non-comment shell commands with line continuations joined."""
    commands: list[str] = []
    pending = ""
    for raw_line in script.splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue
        pending = f"{pending} {line}".strip()
        if pending.endswith("\\"):
            pending = pending[:-1].rstrip()
            continue
        commands.append(pending)
        pending = ""
    if pending:
        commands.append(pending)
    return commands


def _shell_words(command: str) -> list[str]:
    try:
        return shlex.split(command, comments=True, posix=True)
    except ValueError:
        return []


def _shell_argv(command: str) -> list[str]:
    if re.search(r"(?:\|\||&&|[;`]|\$\()", command):
        return []
    return _shell_words(command)


def _command_with(script: str, prefix: tuple[str, ...], required: tuple[str, ...]) -> bool:
    for command in _shell_commands(script):
        argv = _shell_argv(command)
        if tuple(argv[: len(prefix)]) == prefix and all(token in argv for token in required):
            return True
    return False


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
    release_actions: list[tuple[str, dict[object, object]]] = []
    direct_publishers: list[str] = []
    for name, job in jobs.items():
        if not isinstance(job, dict):
            continue
        steps = job.get("steps", [])
        if not isinstance(steps, list):
            continue
        job_release_steps = []
        for step in steps:
            if not isinstance(step, dict):
                continue
            if str(step.get("uses", "")).startswith("softprops/action-gh-release@"):
                job_release_steps.append(step)
                release_actions.append((str(name), step))
            commands = (
                " ".join(_shell_words(command))
                for command in _shell_commands(str(step.get("run", "")))
            )
            if any(_PUBLISH_COMMAND.search(command) for command in commands):
                direct_publishers.append(str(name))
        if job_release_steps:
            publishers.append((str(name), job, steps))
    if len(publishers) != 1:
        return ["CI release security contract requires exactly one GitHub release publisher job"]

    job_name, publisher, steps = publishers[0]
    errors: list[str] = []
    if len(release_actions) != 1:
        errors.append("CI release security contract requires exactly one GitHub release action")
    if direct_publishers:
        errors.append("CI release publisher must not contain direct release publication commands")
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

    named_steps = [step for step in steps if isinstance(step, dict) and step.get("name")]
    named = {str(step.get("name")): step for step in named_steps}
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
        elif sum(str(step.get("name")) == name for step in named_steps) != 1:
            errors.append(f"CI release publisher must define `{name}` exactly once")
        elif "if" in named[name] or "continue-on-error" in named[name]:
            errors.append(f"CI release publisher must not bypass `{name}`")
    if errors:
        return errors

    positions = {str(step.get("name")): index for index, step in enumerate(steps)}
    for earlier, later in pairwise(required):
        if positions[earlier] >= positions[later]:
            errors.append(f"CI release publisher must run `{earlier}` before `{later}`")

    runs = {name: str(step.get("run", "")) for name, step in named.items()}
    commands = {
        "Install cargo-cyclonedx": (
            ("cargo", "install"),
            ("--locked", "cargo-cyclonedx", "--version", "0.5.10"),
        ),
        "Generate Rust SBOM": (
            ("cargo", "cyclonedx"),
            (
                "--format",
                "json",
                "--target",
                "all",
                "--override-filename",
                "sbom-rust",
                "--features",
                "cuda",
            ),
        ),
        "Validate release SBOM identity": (
            (
                "uv",
                "run",
                "--no-project",
                "--python",
                "3.12",
                "--",
                "python",
                "scripts/validate_release_sbom_identity.py",
            ),
            (
                "--rust-sbom",
                "artifacts/sbom-rust.json",
                "--python-sbom",
                "artifacts/sbom-python.json",
                "--expected-version",
                "$RELEASE_VERSION",
            ),
        ),
        "Validate release asset matrix and generate checksums": (
            (
                "uv",
                "run",
                "--no-project",
                "--python",
                "3.12",
                "--",
                "python",
                "scripts/validate_release_binary_artifacts.py",
            ),
            (
                "--artifacts-dir",
                "artifacts",
                "--checksums-out",
                "artifacts/CHECKSUMS.txt",
                "artifacts/sbom-rust.json",
                "artifacts/sbom-python.json",
                "--expected-profile",
                "$RELEASE_NATIVE_ASSET_PROFILE",
            ),
        ),
        "Prepare release asset path list": (
            (
                "uv",
                "run",
                "--no-project",
                "--python",
                "3.12",
                "--",
                "python",
                "scripts/prepare_release_asset_paths.py",
            ),
            (
                "--artifacts-dir",
                "artifacts",
                "--expected-profile",
                "$RELEASE_NATIVE_ASSET_PROFILE",
                "--github-output",
            ),
        ),
        "Verify SLSA provenance": (
            ("gh", "attestation", "verify"),
            (
                "--repo",
                "$GITHUB_REPOSITORY",
                "--signer-workflow",
                "$GITHUB_REPOSITORY/.github/workflows/ci.yml",
                "--source-ref",
                "refs/heads/main",
            ),
        ),
        "Verify GitHub release asset coverage": (
            (
                "uv",
                "run",
                "--no-project",
                "--python",
                "3.12",
                "--",
                "python",
                "scripts/verify_github_release_assets.py",
            ),
            (
                "--local-artifacts-dir",
                "artifacts",
                "--expected-profile",
                "$RELEASE_NATIVE_ASSET_PROFILE",
                "--wait-seconds",
                "--poll-interval-seconds",
            ),
        ),
    }
    for name, (prefix, tokens) in commands.items():
        if not _command_with(runs[name], prefix, tokens):
            errors.append(
                f"CI release publisher `{name}` must run `{' '.join(prefix)}` with {tokens}"
            )
    python_sbom = runs["Prepare clean Python SBOM environments"]
    for prefix, tokens in (
        (("uv", "export"), ("--locked", "--no-dev", "--no-emit-project")),
        (("uv", "pip", "sync"), ("--require-hashes", "scripts/release-sbom-tools.txt")),
        (
            ("cyclonedx-py", "environment"),
            ("-E", "--output-file", "artifacts/sbom-python.json"),
        ),
    ):
        if not _command_with(python_sbom, prefix, tokens):
            errors.append(
                f"CI release Python SBOM step must run `{' '.join(prefix)}` with {tokens}"
            )

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
    expected_uploads = {
        "${{ steps.release_paths.outputs.upload_files }}",
        "artifacts/native/**/tg-*.sigstore.json",
        "artifacts/sbom-rust.json.sigstore.json",
        "artifacts/sbom-python.json.sigstore.json",
        "artifacts/CHECKSUMS.txt.sigstore.json",
    }
    upload_files = {
        line.strip() for line in str(upload_with.get("files", "")).splitlines() if line.strip()
    }
    if upload_files != expected_uploads:
        errors.append(
            "CI release upload must contain exactly the managed assets and signing bundles"
        )
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
