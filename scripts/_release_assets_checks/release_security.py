"""Release publisher security contracts."""

from __future__ import annotations

import hashlib
import re
import shlex

import yaml

from .helpers import _normalize_pinned_actions

_AUTHORITATIVE_PUBLISHER = ".github/workflows/ci.yml"
_RELEASE_WRITE_PERMISSIONS = {"contents", "packages", "id-token"}
_WRITE_AUTHORITY_EXCEPTIONS = {
    ".github/workflows/dependabot-automation.yml": (
        "8214e4c71c9767daf80d282aca42ad90f95839850fdc8930edce7fb8be01d480"
    )
}
_PUBLISH_ACTIONS = (
    "softprops/action-gh-release",
    "pypa/gh-action-pypi-publish",
    "python-semantic-release/python-semantic-release",
)
_SCRIPT_PUBLISH = re.compile(r"\b(?:createRelease|uploadReleaseAsset|createOrUpdateRelease)\b")
_NON_GITHUB_SECRET = re.compile(r"\${{\s*secrets(?:\.|\[)")


def _shell_commands(script: str) -> list[str]:
    """Return non-comment shell commands with line continuations joined."""
    commands: list[str] = []
    pending = ""
    for raw_line in script.splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue
        pending = f"{pending} {line}".strip()
        if pending.endswith(("\\", "`")):
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


def _shell_segments(command: str) -> list[list[str]]:
    try:
        lexer = shlex.shlex(command, posix=True, punctuation_chars=";&|")
        lexer.commenters = "#"
        lexer.whitespace_split = True
        words = list(lexer)
    except ValueError:
        return []
    segments: list[list[str]] = [[]]
    for word in words:
        if word and set(word) <= {";", "&", "|"}:
            segments.append([])
        else:
            segments[-1].append(word)
    return [segment for segment in segments if segment]


def _publishes_command(command: str) -> bool:
    for words in _shell_segments(command):
        while words and re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*=.*", words[0]):
            words = words[1:]
        if words[:1] == ["env"]:
            words = words[1:]
            while words and re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*=.*", words[0]):
                words = words[1:]
        while words and words[0] in {"then", "do", "command", "exec", "sudo"}:
            words = words[1:]
        executable = words[0].replace("\\", "/").rsplit("/", 1)[-1].lower() if words else ""
        arguments = [word.lower() for word in words[1:]]
        if executable == "gh" and arguments[:1] in (["release"], ["api"]):
            return True
        if executable in {"uv", "npm"} and arguments[:1] == ["publish"]:
            return True
        if executable in {"twine", "maturin"} and arguments[:1] in (["upload"], ["publish"]):
            return True
        if (
            executable in {"python", "python3"}
            and arguments[:2] == ["-m", "twine"]
            and arguments[2:3] in (["upload"], ["publish"])
        ):
            return True
    return False


def _release_write_permissions(permissions: object) -> set[str]:
    if permissions == "write-all":
        return {"write-all"}
    if not isinstance(permissions, dict):
        return set()
    return {
        str(name)
        for name, value in permissions.items()
        if name in _RELEASE_WRITE_PERMISSIONS and value == "write"
    }


def _workflow_fingerprint(parsed: object) -> str:
    canonical = yaml.safe_dump(parsed, sort_keys=True).encode()
    return hashlib.sha256(canonical).hexdigest()


def _shell_argv(command: str) -> list[str]:
    if re.search(r"[|&;`<>]|\$\(", command):
        return []
    return _shell_words(command)


def _exact_commands(script: str) -> tuple[tuple[str, ...], ...]:
    commands = tuple(tuple(_shell_argv(command)) for command in _shell_commands(script))
    return commands if all(commands) else ()


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
            if any(
                _publishes_command(command) for command in _shell_commands(str(step.get("run", "")))
            ):
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
    expected_permissions = {"contents": "write", "id-token": "write", "attestations": "write"}
    for permission, value in expected_permissions.items():
        if permissions.get(permission) != value:
            errors.append(f"CI release publisher `{job_name}` must request `{permission}: write`")
    if permissions != expected_permissions:
        errors.append("CI release publisher must request only its sealed permissions")
    if publisher.get("if") != "needs.release.outputs.released == 'true'":
        errors.append("CI release publisher must run only for a semantic-release tag")
    if publisher.get("name") != "publish-github-release-assets":
        errors.append("CI release publisher must keep its authoritative job name")
    expected_job_keys = {"name", "needs", "if", "runs-on", "permissions", "env", "steps"}
    if set(publisher) != expected_job_keys:
        errors.append("CI release publisher job must use only its sealed configuration")
    if publisher.get("runs-on") != "ubuntu-latest":
        errors.append("CI release publisher must run on ubuntu-latest")
    if publisher.get("env") != {
        "RELEASE_NATIVE_ASSET_PROFILE": "${{ vars.TENSOR_GREP_RELEASE_NATIVE_ASSET_PROFILE || 'native-frontdoor' }}",
        "RELEASE_VERSION": "${{ needs.release.outputs.release_version }}",
        "GITHUB_REPOSITORY": "${{ github.repository }}",
    }:
        errors.append("CI release publisher must bind its release environment exactly")
    needs = publisher.get("needs", [])
    needs = [needs] if isinstance(needs, str) else needs if isinstance(needs, list) else []
    if needs != ["release", "build-release-native-assets", "build-wheels-pypi"]:
        errors.append(
            "CI release publisher must depend on semantic release, native assets, and the tag-matched Python wheel"
        )

    named_steps = [step for step in steps if isinstance(step, dict) and step.get("name")]
    named = {str(step.get("name")): step for step in named_steps}
    required = (
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
    )
    for name in required:
        if name not in named:
            errors.append(f"CI release publisher is missing `{name}`")
        elif sum(str(step.get("name")) == name for step in named_steps) != 1:
            errors.append(f"CI release publisher must define `{name}` exactly once")
        elif "if" in named[name] or "continue-on-error" in named[name]:
            errors.append(f"CI release publisher must not bypass `{name}`")
    observed_names = tuple(
        str(step.get("name", "")) if isinstance(step, dict) else "" for step in steps
    )
    if observed_names != required:
        errors.append("CI release publisher steps must equal the sealed sequence")
    if errors:
        return errors

    runs = {name: str(step.get("run", "")) for name, step in named.items()}
    setup_actions = {
        "Checkout publisher source": (
            "actions/checkout@v6",
            {"ref": "v${{ needs.release.outputs.release_version }}"},
        ),
        "Install uv": ("astral-sh/setup-uv@v8.0.0", None),
        "Download native release front doors": (
            "actions/download-artifact@v8",
            {"pattern": "release-native-*", "path": "artifacts/native"},
        ),
        "Download tag-matched Python wheel": (
            "actions/download-artifact@v8",
            {"name": "pypi-wheels-ubuntu-latest", "path": "artifacts/wheel"},
        ),
    }
    for name, (uses, with_block) in setup_actions.items():
        expected_step = {"name": name, "uses": uses}
        if with_block is not None:
            expected_step["with"] = with_block
        if named[name] != expected_step:
            errors.append(f"CI release publisher `{name}` must use its exact pinned action")

    commands = {
        "Setup Python": (("uv", "python", "install", "3.12"),),
        "Install cargo-cyclonedx": (
            (
                "cargo",
                "install",
                "--locked",
                "cargo-cyclonedx",
                "--version",
                "0.5.10",
            ),
        ),
        "Generate Rust SBOM": (
            (
                "uv",
                "run",
                "--no-project",
                "--python",
                "3.12",
                "--",
                "python",
                "scripts/generate_release_rust_sbom.py",
                "--expected-profile",
                "$RELEASE_NATIVE_ASSET_PROFILE",
            ),
        ),
        "Prepare clean Python SBOM environments": (
            (
                "uv",
                "export",
                "--locked",
                "--no-dev",
                "--no-emit-project",
                "--format",
                "requirements.txt",
                "--output-file",
                "$RUNNER_TEMP/tg-core-requirements.txt",
            ),
            ("uv", "venv", "--python", "3.12", ".sbom-python-core"),
            (
                "uv",
                "pip",
                "sync",
                "--python",
                ".sbom-python-core/bin/python",
                "--require-hashes",
                "$RUNNER_TEMP/tg-core-requirements.txt",
            ),
            (
                "uv",
                "pip",
                "install",
                "--python",
                ".sbom-python-core/bin/python",
                "--no-deps",
                "artifacts/wheel/*.whl",
            ),
            ("uv", "venv", "--python", "3.12", ".sbom-python-tools"),
            (
                "uv",
                "pip",
                "sync",
                "--python",
                ".sbom-python-tools/bin/python",
                "--require-hashes",
                "--only-binary",
                ":all:",
                "scripts/release-sbom-tools.txt",
            ),
            (
                ".sbom-python-tools/bin/cyclonedx-py",
                "environment",
                "-E",
                ".sbom-python-core/bin/python",
                "--pyproject",
                "pyproject.toml",
                "--output-file",
                "artifacts/sbom-python.json",
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
                "--artifacts-dir",
                "artifacts",
                "--checksums-out",
                "artifacts/CHECKSUMS.txt",
                "--additional-checksum-asset",
                "artifacts/sbom-rust.json",
                "--additional-checksum-asset",
                "artifacts/sbom-python.json",
                "--expected-profile",
                "$RELEASE_NATIVE_ASSET_PROFILE",
            ),
        ),
        "Build package-manager publish bundle": (
            (
                "uv",
                "run",
                "--no-project",
                "--python",
                "3.12",
                "--with",
                "pyyaml==6.0.3",
                "--",
                "python",
                "scripts/prepare_package_manager_release.py",
                "--output-dir",
                "artifacts/package-manager-bundle",
                "--release-checksums",
                "artifacts/CHECKSUMS.txt",
            ),
        ),
        "Verify package-manager bundle checksums": (
            (
                "uv",
                "run",
                "--no-project",
                "--python",
                "3.12",
                "--",
                "python",
                "scripts/verify_package_manager_bundle_checksums.py",
                "--bundle-dir",
                "artifacts/package-manager-bundle",
            ),
        ),
        "Smoke-test package-manager bundle contracts": (
            (
                "uv",
                "run",
                "--no-project",
                "--python",
                "3.12",
                "--with",
                "pyyaml==6.0.3",
                "--",
                "python",
                "scripts/smoke_test_package_manager_bundle.py",
                "--bundle-dir",
                "artifacts/package-manager-bundle",
                "--expected-version",
                "$RELEASE_VERSION",
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
                "--artifacts-dir",
                "artifacts",
                "--expected-profile",
                "$RELEASE_NATIVE_ASSET_PROFILE",
                "--github-output",
                "$GITHUB_OUTPUT",
            ),
        ),
        "Verify SLSA provenance": (
            (
                "uv",
                "run",
                "--no-project",
                "--python",
                "3.12",
                "--",
                "python",
                "scripts/verify_release_subject_attestations.py",
                "--artifacts-dir",
                "artifacts",
                "--expected-profile",
                "$RELEASE_NATIVE_ASSET_PROFILE",
                "--repository",
                "$GITHUB_REPOSITORY",
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
                "--repo",
                "$GITHUB_REPOSITORY",
                "--tag",
                "v${{ needs.release.outputs.release_version }}",
                "--token",
                "${{ secrets.GITHUB_TOKEN }}",
                "--local-artifacts-dir",
                "artifacts",
                "--expected-profile",
                "$RELEASE_NATIVE_ASSET_PROFILE",
                "--wait-seconds",
                "120",
                "--poll-interval-seconds",
                "5",
            ),
        ),
    }
    for name, expected_commands in commands.items():
        if _exact_commands(runs[name]) != expected_commands:
            expected_text = " and ".join(" ".join(command) for command in expected_commands)
            errors.append(
                f"CI release publisher `{name}` must run only its exact commands: {expected_text}"
            )
        expected_keys = (
            {"name", "run", "id"} if name == "Prepare release asset path list" else {"name", "run"}
        )
        if set(named[name]) != expected_keys:
            errors.append(f"CI release publisher `{name}` must use only its sealed fields")
    if named["Prepare release asset path list"].get("id") != "release_paths":
        errors.append("CI release asset path preparation must use `id: release_paths`")
    attest = named["Generate SLSA provenance"]
    if set(attest) != {"name", "uses", "with"}:
        errors.append("CI release provenance step must use only its sealed fields")
    if attest.get("uses") != "actions/attest-build-provenance@v4":
        errors.append("CI release publisher must use the pinned build-provenance action")
    attest_with = attest.get("with", {})
    if attest_with != {"subject-path": "${{ steps.release_paths.outputs.subject_paths }}"}:
        errors.append("CI release provenance must attest only the native/SBOM/checksum subjects")

    signing = named["Sign release subjects with Sigstore"]
    if set(signing) != {"name", "uses", "with"}:
        errors.append("CI release Sigstore step must use only its sealed fields")
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
    if set(signing_with) != set(expected_signing):
        errors.append("CI release Sigstore step must use only its sealed configuration")

    upload = named["Upload GitHub release assets"]
    if set(upload) != {"name", "uses", "with"}:
        errors.append("CI release upload step must use only its sealed fields")
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
    if set(upload_with) != {"tag_name", "fail_on_unmatched_files", "files"}:
        errors.append("CI release upload must use only its sealed configuration")
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
        if filename != _AUTHORITATIVE_PUBLISHER:
            write_permissions = _release_write_permissions(parsed.get("permissions"))
            for job in jobs.values():
                if isinstance(job, dict):
                    write_permissions.update(_release_write_permissions(job.get("permissions")))
            expected_fingerprint = _WRITE_AUTHORITY_EXCEPTIONS.get(filename)
            if write_permissions and _workflow_fingerprint(parsed) != expected_fingerprint:
                names = ", ".join(sorted(write_permissions))
                errors.append(
                    f"Release write authority is only allowed in {_AUTHORITATIVE_PUBLISHER}; "
                    f"found {filename}: {names}"
                )
            content_without_github_token = re.sub(r"\${{\s*secrets\.GITHUB_TOKEN\s*}}", "", content)
            if _NON_GITHUB_SECRET.search(content_without_github_token):
                errors.append(
                    f"Release-capable secrets are only allowed in {_AUTHORITATIVE_PUBLISHER}; "
                    f"found {filename}"
                )
        for job_name, job in jobs.items():
            if not isinstance(job, dict):
                continue
            if "uses" in job:
                errors.append(
                    f"Workflow publisher census forbids opaque reusable job {filename}:{job_name}"
                )
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
                if action.startswith("./"):
                    errors.append(
                        f"Workflow publisher census forbids opaque local action "
                        f"{filename}:{job_name}:{action}"
                    )
                    continue
                with_block = step.get("with", {})
                script = str(with_block.get("script", "")) if isinstance(with_block, dict) else ""
                commands = _shell_commands(str(step.get("run", "")))
                if (
                    any(action.startswith(prefix + "@") for prefix in _PUBLISH_ACTIONS)
                    or any(_publishes_command(command) for command in commands)
                    or _SCRIPT_PUBLISH.search(script)
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
