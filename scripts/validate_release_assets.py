from __future__ import annotations

import json
import re
import tomllib
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[1]


def _read(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def _version_from_pyproject() -> str:
    data = tomllib.loads(_read(ROOT / "pyproject.toml"))
    return str(data["project"]["version"])


def _version_from_cargo() -> str:
    content = _read(ROOT / "rust_core" / "Cargo.toml")
    match = re.search(r'(?m)^version\s*=\s*"([^"]+)"\s*$', content)
    if not match:
        raise ValueError("Missing rust_core/Cargo.toml package version")
    return match.group(1)


def _version_from_npm() -> str:
    data = json.loads(_read(ROOT / "npm" / "package.json"))
    return str(data["version"])


def validate_winget_manifest(*, winget_content: str, py_version: str) -> list[str]:
    errors: list[str] = []
    if f"PackageVersion: {py_version}" not in winget_content:
        errors.append("Winget manifest PackageVersion does not match pyproject version")
    expected_windows_url = (
        f"https://github.com/oimiragieo/tensor-grep/releases/download/v{py_version}/"
        "tg-windows-amd64-cpu.exe"
    )
    if expected_windows_url not in winget_content:
        errors.append("Winget manifest InstallerUrl does not match expected release artifact URL")
    if "PLACEHOLDER" in winget_content:
        errors.append("Winget manifest contains unresolved PLACEHOLDER text")
    try:
        parsed_winget = yaml.safe_load(winget_content) or {}
    except yaml.YAMLError as exc:
        errors.append(f"Winget manifest is not valid YAML: {exc}")
        parsed_winget = {}
    if not isinstance(parsed_winget, dict):
        errors.append("Winget manifest must deserialize to a mapping")
        return errors

    installers = parsed_winget.get("Installers")
    if not isinstance(installers, list) or not installers:
        errors.append("Winget manifest must contain a non-empty Installers list")
        return errors

    first = installers[0]
    if not isinstance(first, dict):
        errors.append("Winget manifest first installer must be a mapping")
        return errors

    installer_url = first.get("InstallerUrl")
    if installer_url != expected_windows_url:
        errors.append("Winget manifest InstallerUrl must be nested under first installer mapping")
    return errors


def validate_ci_workflow_content(*, ci_workflow: str) -> list[str]:
    errors: list[str] = []
    for expected in (
        "package-manager-readiness:",
        "Validate Homebrew formula syntax",
        "Validate winget manifest syntax",
        "Validate package-manager publish bundle source state",
        "scripts/prepare_package_manager_release.py --check",
        "Build package-manager publish bundle artifact",
        "scripts/prepare_package_manager_release.py --output-dir artifacts/package-manager-bundle",
        "--output-dir artifacts/package-manager-bundle",
        "Verify package-manager publish bundle checksums",
        "scripts/verify_package_manager_bundle_checksums.py --bundle-dir artifacts/package-manager-bundle",
        "--bundle-dir artifacts/package-manager-bundle",
        "Smoke-test package-manager bundle contracts",
        "scripts/smoke_test_package_manager_bundle.py --bundle-dir artifacts/package-manager-bundle",
        "Upload package-manager bundle artifact",
        "package-manager-bundle-${{ matrix.os }}",
        "validate-pypi-artifacts:",
        "Validate built PyPI artifact set",
        "Smoke-test install from built PyPI artifacts",
        "Install Dependencies (Unix with retry)",
        "Install Dependencies (Windows with retry)",
        "Dependency install failed after 3 attempts.",
        "Verify cuDF / RAPIDS Configuration (with retry)",
        "GPU dependency install failed (attempt ${attempt}/3); retrying after backoff...",
        "GPU dependency install failed after 3 attempts.",
        "publish-success-gate:",
        "Confirm publish job result when publishing is required",
        "Verify PyPI parity for semantic-release version (always)",
        "Skip publish parity gate when semantic-release produced no version",
        "Verify release version parity across tag/assets/PyPI",
        "scripts/validate_release_version_parity.py",
    ):
        if expected not in ci_workflow:
            errors.append(
                f"CI workflow missing expected package-manager validation block: {expected}"
            )

    if "ref: v${{ needs.release.outputs.release_version }}" not in ci_workflow:
        errors.append("CI workflow must build PyPI artifacts from semantic-release tag ref")

    if (
        "needs: [release, build-wheels-pypi, build-sdist-pypi, validate-pypi-artifacts]"
        not in ci_workflow
    ):
        errors.append(
            "publish-pypi must depend on validate-pypi-artifacts before uploading to PyPI"
        )

    if ci_workflow.count("uses: astral-sh/setup-uv@v5") < 2:
        errors.append("CI workflow should install uv in package-manager/release validation paths")

    if "--pypi-wait-seconds" not in ci_workflow:
        errors.append("CI workflow must pass --pypi-wait-seconds to release parity validation")

    if "--pypi-poll-interval-seconds" not in ci_workflow:
        errors.append(
            "CI workflow must pass --pypi-poll-interval-seconds to release parity validation"
        )

    if "needs: [release, publish-pypi]" not in ci_workflow:
        errors.append("CI workflow publish-success-gate must depend on release + publish-pypi")

    if "if: always()" not in ci_workflow:
        errors.append("CI workflow publish-success-gate must run with if: always()")

    if "if: needs.release.outputs.release_version == ''" not in ci_workflow:
        errors.append(
            "CI workflow publish-success-gate must explicitly handle empty release_version output"
        )

    if "if: needs.release.outputs.release_version != ''" not in ci_workflow:
        errors.append(
            "CI workflow publish-success-gate must guard checkout/parity steps behind non-empty release_version"
        )

    try:
        parsed_ci = yaml.safe_load(ci_workflow) or {}
    except yaml.YAMLError:
        parsed_ci = {}
    if isinstance(parsed_ci, dict):
        jobs = parsed_ci.get("jobs")
        if isinstance(jobs, dict):
            release_job = jobs.get("release")
            if isinstance(release_job, dict):
                needs = release_job.get("needs", [])
                if isinstance(needs, str):
                    needs_list = [needs]
                elif isinstance(needs, list):
                    needs_list = [str(item) for item in needs]
                else:
                    needs_list = []
                if "benchmark-regression" not in needs_list:
                    errors.append("CI workflow release job must depend on benchmark-regression")

            gpu_job = jobs.get("test-gpu-linux")
            if isinstance(gpu_job, dict):
                raw_steps = gpu_job.get("steps", [])
                step_names: set[str] = set()
                if isinstance(raw_steps, list):
                    for step in raw_steps:
                        if not isinstance(step, dict):
                            continue
                        name = step.get("name")
                        if isinstance(name, str):
                            step_names.add(name)
                for required_step in (
                    "Verify cuDF / RAPIDS Configuration (with retry)",
                    "Run Pytest with GPU Hooks",
                ):
                    if required_step not in step_names:
                        errors.append(
                            f"CI workflow test-gpu-linux job must include step `{required_step}`"
                        )

            benchmark_job = jobs.get("benchmark-regression")
            if isinstance(benchmark_job, dict):
                benchmark_steps = benchmark_job.get("steps", [])
                benchmark_run_by_name: dict[str, str] = {}
                if isinstance(benchmark_steps, list):
                    for step in benchmark_steps:
                        if not isinstance(step, dict):
                            continue
                        name = step.get("name")
                        run = step.get("run")
                        if isinstance(name, str) and isinstance(run, str):
                            benchmark_run_by_name[name] = run
                required_benchmark_steps = {
                    "Enforce benchmark regression gate": "benchmarks/check_regression.py",
                    "Build benchmark markdown summary": "benchmarks/summarize_benchmarks.py",
                }
                for step_name, command in required_benchmark_steps.items():
                    run_script = benchmark_run_by_name.get(step_name)
                    if run_script is None:
                        errors.append(
                            f"CI workflow benchmark-regression job must include step `{step_name}`"
                        )
                        continue
                    if command not in run_script:
                        errors.append(
                            "CI workflow benchmark-regression "
                            f"`{step_name}` step must invoke `{command}`"
                        )
                    if "--baseline auto" not in run_script:
                        errors.append(
                            "CI workflow benchmark-regression "
                            f"`{step_name}` step must pass `--baseline auto`"
                        )

            publish_pypi_job = jobs.get("publish-pypi")
            if isinstance(publish_pypi_job, dict):
                publish_steps = publish_pypi_job.get("steps", [])
                publish_run_by_name: dict[str, str] = {}
                publish_step_names: set[str] = set()
                if isinstance(publish_steps, list):
                    for step in publish_steps:
                        if not isinstance(step, dict):
                            continue
                        name = step.get("name")
                        if isinstance(name, str):
                            publish_step_names.add(name)
                        run = step.get("run")
                        if isinstance(name, str) and isinstance(run, str):
                            publish_run_by_name[name] = run
                parity_step = "Verify release version parity across tag/assets/PyPI"
                if parity_step not in publish_step_names:
                    errors.append(f"CI workflow publish-pypi job must include step `{parity_step}`")
                parity_run = publish_run_by_name.get(parity_step)
                if parity_run is not None:
                    for required_flag in ("--expected-version", "--expected-tag"):
                        if required_flag not in parity_run:
                            errors.append(
                                "CI workflow publish-pypi "
                                f"`{parity_step}` step must include `{required_flag}`"
                            )
                    for required_flag in (
                        "--dist-dir",
                        "--check-pypi",
                        "--pypi-wait-seconds",
                        "--pypi-poll-interval-seconds",
                    ):
                        if required_flag not in parity_run:
                            errors.append(
                                "CI workflow publish-pypi "
                                f"`{parity_step}` step must include `{required_flag}`"
                            )

            validate_pypi_job = jobs.get("validate-pypi-artifacts")
            if isinstance(validate_pypi_job, dict):
                validate_steps = validate_pypi_job.get("steps", [])
                validate_run_by_name: dict[str, str] = {}
                validate_step_names: set[str] = set()
                if isinstance(validate_steps, list):
                    for step in validate_steps:
                        if not isinstance(step, dict):
                            continue
                        name = step.get("name")
                        if isinstance(name, str):
                            validate_step_names.add(name)
                        run = step.get("run")
                        if isinstance(name, str) and isinstance(run, str):
                            validate_run_by_name[name] = run

                validate_step = "Validate built PyPI artifact set"
                if validate_step not in validate_step_names:
                    errors.append(
                        "CI workflow validate-pypi-artifacts job must include "
                        f"step `{validate_step}`"
                    )
                validate_run = validate_run_by_name.get(validate_step)
                if validate_run is not None:
                    if "scripts/validate_pypi_artifacts.py" not in validate_run:
                        errors.append(
                            "CI workflow validate-pypi-artifacts "
                            f"`{validate_step}` step must invoke `scripts/validate_pypi_artifacts.py`"
                        )
                    for required_flag in ("--dist-dir", "--version", "--require-platforms"):
                        if required_flag not in validate_run:
                            errors.append(
                                "CI workflow validate-pypi-artifacts "
                                f"`{validate_step}` step must include `{required_flag}`"
                            )

                smoke_step = "Smoke-test install from built PyPI artifacts"
                if smoke_step not in validate_step_names:
                    errors.append(
                        f"CI workflow validate-pypi-artifacts job must include step `{smoke_step}`"
                    )
                smoke_run = validate_run_by_name.get(smoke_step)
                if smoke_run is not None:
                    if "scripts/smoke_test_pypi_artifacts.py" not in smoke_run:
                        errors.append(
                            "CI workflow validate-pypi-artifacts "
                            f"`{smoke_step}` step must invoke `scripts/smoke_test_pypi_artifacts.py`"
                        )
                    for required_flag in ("--dist-dir", "--version", "--work-dir"):
                        if required_flag not in smoke_run:
                            errors.append(
                                "CI workflow validate-pypi-artifacts "
                                f"`{smoke_step}` step must include `{required_flag}`"
                            )

            publish_success_gate_job = jobs.get("publish-success-gate")
            if isinstance(publish_success_gate_job, dict):
                gate_steps = publish_success_gate_job.get("steps", [])
                gate_run_by_name: dict[str, str] = {}
                gate_step_names: set[str] = set()
                if isinstance(gate_steps, list):
                    for step in gate_steps:
                        if not isinstance(step, dict):
                            continue
                        name = step.get("name")
                        if isinstance(name, str):
                            gate_step_names.add(name)
                        run = step.get("run")
                        if isinstance(name, str) and isinstance(run, str):
                            gate_run_by_name[name] = run
                gate_parity_step = "Verify PyPI parity for semantic-release version (always)"
                if gate_parity_step not in gate_step_names:
                    errors.append(
                        "CI workflow publish-success-gate job must include "
                        f"step `{gate_parity_step}`"
                    )
                gate_parity_run = gate_run_by_name.get(gate_parity_step)
                if gate_parity_run is not None:
                    for required_flag in ("--expected-version", "--expected-tag"):
                        if required_flag not in gate_parity_run:
                            errors.append(
                                "CI workflow publish-success-gate "
                                f"`{gate_parity_step}` step must include `{required_flag}`"
                            )
                    for required_flag in (
                        "--dist-dir",
                        "--check-pypi",
                        "--pypi-wait-seconds",
                        "--pypi-poll-interval-seconds",
                    ):
                        if required_flag not in gate_parity_run:
                            errors.append(
                                "CI workflow publish-success-gate "
                                f"`{gate_parity_step}` step must include `{required_flag}`"
                            )
                if "Download all distributions" not in gate_step_names:
                    errors.append(
                        "CI workflow publish-success-gate job must include step `Download all distributions`"
                    )

    if "--skip-package-managers" in ci_workflow:
        errors.append("CI workflow parity validation must not skip package-manager version checks")

    if "publish-pypi:" in ci_workflow:
        if "name: pypi" not in ci_workflow:
            errors.append("CI workflow publish-pypi job must target `environment: pypi`")
        if "url: https://pypi.org/p/tensor-grep" not in ci_workflow:
            errors.append(
                "CI workflow publish-pypi job should set canonical PyPI project URL for deployment visibility"
            )
        if "id-token: write" not in ci_workflow:
            errors.append("CI workflow publish-pypi job must request `id-token: write` permission")
        if "uses: pypa/gh-action-pypi-publish@release/v1" not in ci_workflow:
            errors.append(
                "CI workflow publish-pypi job must use pypa/gh-action-pypi-publish@release/v1"
            )
        if "skip-existing: true" not in ci_workflow:
            errors.append(
                "CI workflow publish-pypi job should pass `skip-existing: true` to avoid duplicate-upload failures"
            )

    if "uv run ruff format --check --preview ." not in ci_workflow:
        errors.append(
            "CI workflow must run formatter with `ruff format --check --preview .` to keep local/CI formatting semantics aligned"
        )

    def _command_invocations_have_flag(command: str, required: str) -> bool:
        command_indexes = [m.start() for m in re.finditer(re.escape(command), ci_workflow)]
        if not command_indexes:
            return False

        for idx in command_indexes:
            # Restrict inspection to the current YAML step block; do not confuse
            # command flags like `--baseline` with a new `- <step>` entry.
            next_step_match = re.search(
                r"\n\s{6,}-\s+(?:name|run|uses|if|with|env|id|working-directory|shell)\s*:",
                ci_workflow[idx + len(command) :],
            )
            if next_step_match:
                next_step_idx = idx + len(command) + next_step_match.start()
            else:
                next_step_idx = len(ci_workflow)
            block = ci_workflow[idx:next_step_idx]
            if required not in block:
                return False
        return True

    if not _command_invocations_have_flag("benchmarks/check_regression.py", "--baseline auto"):
        errors.append(
            "CI workflow benchmark regression gate must pass `--baseline auto` to check_regression.py"
        )

    if not _command_invocations_have_flag("benchmarks/summarize_benchmarks.py", "--baseline auto"):
        errors.append(
            "CI workflow benchmark summary generation must pass `--baseline auto` to summarize_benchmarks.py"
        )

    return errors


def validate_package_manager_docs(*, runbook_content: str, checklist_content: str) -> list[str]:
    errors: list[str] = []
    for heading in (
        "## Homebrew Tap Flow",
        "## Winget Flow",
        "## Rollback Procedures",
        "## Verification Commands",
    ):
        if heading not in runbook_content:
            errors.append(f"Package manager runbook missing required heading: {heading}")

    for marker in (
        "## 4. Package-manager distribution finalization",
        "## 5. Rollback runbook",
        "Homebrew",
        "Winget",
    ):
        if marker not in checklist_content:
            errors.append(f"Release checklist missing package-manager marker: {marker}")

    for required_checklist_cmd in (
        "gh run list --limit 10",
        "python scripts/verify_github_release_assets.py --repo oimiragieo/tensor-grep --tag vX.Y.Z",
    ):
        if required_checklist_cmd not in checklist_content:
            errors.append(
                "Release checklist missing required operator verification command: "
                f"{required_checklist_cmd}"
            )

    for required_cmd in (
        "gh run list --limit 10",
        "uv run python scripts/prepare_package_manager_release.py --check",
        "ruby -c Formula/tensor-grep.rb",
        "winget validate --manifest",
        "winget validate --manifest .\\manifests\\o\\oimiragieo\\tensor-grep\\X.Y.Z\\",
        "uv run python scripts/verify_package_manager_bundle_checksums.py --bundle-dir",
        "uv run python scripts/smoke_test_package_manager_bundle.py --bundle-dir",
        "python scripts/verify_github_release_assets.py --repo oimiragieo/tensor-grep --tag vX.Y.Z",
        "python scripts/validate_release_version_parity.py --expected-version X.Y.Z --expected-tag vX.Y.Z --check-pypi",
        "python scripts/validate_release_version_parity.py --expected-version X.Y.Z --expected-tag vX.Y.Z --check-npm",
    ):
        if required_cmd not in runbook_content:
            errors.append(
                f"Package manager runbook missing required verification/publish command: {required_cmd}"
            )

    for required_smoke_cmd in (
        "brew install oimiragieo/tap/tensor-grep",
        "winget install oimiragieo.tensor-grep",
        "tg --version",
    ):
        if required_smoke_cmd not in runbook_content:
            errors.append(
                f"Package manager runbook missing required smoke-install command: {required_smoke_cmd}"
            )

    for required_rollback_cmd in (
        "git revert <tap-formula-commit>",
        "git push origin <rollback-branch>",
        "brew update",
        "winget uninstall oimiragieo.tensor-grep",
    ):
        if required_rollback_cmd not in runbook_content:
            errors.append(
                "Package manager runbook missing required rollback command: "
                f"{required_rollback_cmd}"
            )

    if "npm/GitHub mismatch" not in runbook_content:
        errors.append("Package manager runbook missing npm/GitHub rollback guidance")
    return errors


def validate_installation_docs(*, installation_content: str) -> list[str]:
    errors: list[str] = []
    for expected in (
        "### Homebrew Tap Flow",
        "### Winget Flow",
        "### Repeatable Release Checklist",
        "### Rollback Playbook",
    ):
        if expected not in installation_content:
            errors.append(f"Installation docs missing package-manager section: {expected}")

    if "https://github.com/oimiragieo/tensor-grep/releases" not in installation_content:
        errors.append("Installation docs must point GitHub Releases link to oimiragieo/tensor-grep")

    if "--check-npm" not in installation_content:
        errors.append("Installation docs release automation notes must mention npm parity checks")

    for required_cmd in (
        "brew tap oimiragieo/tap",
        "brew install tensor-grep",
        "brew install oimiragieo/tap/tensor-grep",
        "winget validate --manifest",
        "winget-pkgs",
        "winget install oimiragieo.tensor-grep",
        "tg --version",
        "python scripts/verify_github_release_assets.py --repo oimiragieo/tensor-grep --tag vX.Y.Z",
        "git revert <tap-formula-commit>",
        "winget uninstall oimiragieo.tensor-grep",
    ):
        if required_cmd not in installation_content:
            errors.append(
                "Installation docs missing required package-manager command/reference: "
                f"{required_cmd}"
            )
    return errors


def validate_release_workflow_content(*, release_workflow: str) -> list[str]:
    errors: list[str] = []
    for expected in (
        "on:",
        "tags:",
        "- 'v*'",
        "validate-release-assets:",
        "validate-package-managers:",
        "build-binaries:",
        "create-release:",
        "verify-release-assets:",
        "validate-tag-version-parity:",
        "publish-npm:",
        "Verify npm registry parity for release version",
        "--check-npm",
        "publish-docs:",
        "release-success-gate:",
        "Verify final npm parity before release success gate",
        "Verify final PyPI parity before release success gate",
        "Smoke-test Binary (Windows)",
        "Smoke-test Binary (Linux)",
        "Smoke-test Binary (macOS)",
        "Validate release binary artifact matrix and generate checksums",
        "Smoke-verify Linux release binary version",
        "Verify uploaded release assets and checksum coverage",
        "scripts/verify_github_release_assets.py",
        "scripts/smoke_verify_release_binary.py",
        "Validate release tag/version parity across package metadata",
        "scripts/validate_release_version_parity.py",
        "artifacts/CHECKSUMS.txt",
        "Build package-manager publish bundle",
        "Verify package-manager bundle checksums",
        "Smoke-test package-manager bundle contracts",
        "scripts/prepare_package_manager_release.py \\",
        "--output-dir artifacts/package-manager-bundle",
        "scripts/verify_package_manager_bundle_checksums.py \\",
        "--bundle-dir artifacts/package-manager-bundle",
        "scripts/smoke_test_package_manager_bundle.py \\",
        "artifacts/package-manager-bundle/**",
        "Validate package-manager publish bundle source state",
        "Preflight build package-manager publish bundle artifact",
        "Preflight verify package-manager bundle checksums",
        "Preflight smoke-test package-manager bundle contracts",
        "scripts/prepare_package_manager_release.py --check",
        "Confirm release publication gates",
    ):
        if expected not in release_workflow:
            errors.append(f"Release workflow missing expected job block: {expected.rstrip(':')}")

    try:
        parsed = yaml.safe_load(release_workflow) or {}
    except yaml.YAMLError as exc:
        errors.append(f"Release workflow is not valid YAML: {exc}")
        parsed = {}

    jobs = parsed.get("jobs", {}) if isinstance(parsed, dict) else {}
    if not isinstance(jobs, dict):
        errors.append("Release workflow must define jobs as a mapping")
        return errors

    def _needs(job_name: str) -> list[str]:
        job = jobs.get(job_name)
        if not isinstance(job, dict):
            return []
        needs = job.get("needs")
        if isinstance(needs, str):
            return [needs]
        if isinstance(needs, list):
            return [str(item) for item in needs]
        return []

    build_needs = _needs("build-binaries")
    if not {"validate-release-assets", "validate-package-managers"}.issubset(set(build_needs)):
        errors.append(
            "Release workflow build-binaries must depend on release/package-manager validators"
        )

    parity_needs = _needs("validate-tag-version-parity")
    if "verify-release-assets" not in parity_needs:
        errors.append(
            "Release workflow validate-tag-version-parity must depend on verify-release-assets"
        )

    verify_assets_needs = _needs("verify-release-assets")
    if "create-release" not in verify_assets_needs:
        errors.append("Release workflow verify-release-assets must depend on create-release")

    docs_needs = _needs("publish-docs")
    if "validate-tag-version-parity" not in docs_needs:
        errors.append("Release workflow publish-docs must depend on validate-tag-version-parity")

    npm_needs = _needs("publish-npm")
    if "validate-tag-version-parity" not in npm_needs:
        errors.append("Release workflow publish-npm must depend on validate-tag-version-parity")

    release_gate_needs = _needs("release-success-gate")
    if not {"validate-tag-version-parity", "publish-npm", "publish-docs"}.issubset(
        set(release_gate_needs)
    ):
        errors.append(
            "Release workflow release-success-gate must depend on parity + publish-npm + publish-docs"
        )

    validate_pm_job = jobs.get("validate-package-managers")
    if isinstance(validate_pm_job, dict):
        steps = validate_pm_job.get("steps", [])
        step_names: set[str] = set()
        step_runs_by_name: dict[str, str] = {}
        if isinstance(steps, list):
            for step in steps:
                if not isinstance(step, dict):
                    continue
                name = step.get("name")
                if isinstance(name, str):
                    step_names.add(name)
                    run = step.get("run")
                    if isinstance(run, str):
                        step_runs_by_name[name] = run

        for required_step in (
            "Preflight build package-manager publish bundle artifact",
            "Preflight verify package-manager bundle checksums",
            "Preflight smoke-test package-manager bundle contracts",
        ):
            if required_step not in step_names:
                errors.append(
                    "Release workflow validate-package-managers job must include "
                    f"step `{required_step}`"
                )
        validate_pm_step_contracts = {
            "Preflight build package-manager publish bundle artifact": (
                "scripts/prepare_package_manager_release.py",
                "--output-dir artifacts/package-manager-bundle",
            ),
            "Preflight verify package-manager bundle checksums": (
                "scripts/verify_package_manager_bundle_checksums.py",
                "--bundle-dir artifacts/package-manager-bundle",
            ),
            "Preflight smoke-test package-manager bundle contracts": (
                "scripts/smoke_test_package_manager_bundle.py",
                "--bundle-dir artifacts/package-manager-bundle",
            ),
        }
        for step_name, required_contract_tokens in validate_pm_step_contracts.items():
            run_script = step_runs_by_name.get(step_name)
            if run_script is None:
                continue
            required_command = required_contract_tokens[0]
            if required_command not in run_script:
                errors.append(
                    "Release workflow validate-package-managers "
                    f"`{step_name}` step must invoke `{required_command}`"
                )
            for required_flag in required_contract_tokens[1:]:
                if required_flag not in run_script:
                    errors.append(
                        "Release workflow validate-package-managers "
                        f"`{step_name}` step must pass `{required_flag}`"
                    )

    create_release_job = jobs.get("create-release")
    if isinstance(create_release_job, dict):
        create_release_steps = create_release_job.get("steps", [])
        create_release_step_names: set[str] = set()
        create_release_run_by_name: dict[str, str] = {}
        if isinstance(create_release_steps, list):
            for step in create_release_steps:
                if not isinstance(step, dict):
                    continue
                name = step.get("name")
                if isinstance(name, str):
                    create_release_step_names.add(name)
                    run = step.get("run")
                    if isinstance(run, str):
                        create_release_run_by_name[name] = run
        for required_step in (
            "Build package-manager publish bundle",
            "Verify package-manager bundle checksums",
            "Smoke-test package-manager bundle contracts",
        ):
            if required_step not in create_release_step_names:
                errors.append(
                    f"Release workflow create-release job must include step `{required_step}`"
                )
        create_release_step_contracts = {
            "Validate release binary artifact matrix and generate checksums": (
                "scripts/validate_release_binary_artifacts.py",
                "--artifacts-dir",
                "--checksums-out",
            ),
            "Build package-manager publish bundle": (
                "scripts/prepare_package_manager_release.py",
                "--output-dir artifacts/package-manager-bundle",
            ),
            "Verify package-manager bundle checksums": (
                "scripts/verify_package_manager_bundle_checksums.py",
                "--bundle-dir artifacts/package-manager-bundle",
            ),
            "Smoke-test package-manager bundle contracts": (
                "scripts/smoke_test_package_manager_bundle.py",
                "--bundle-dir artifacts/package-manager-bundle",
            ),
            "Smoke-verify Linux release binary version": (
                "scripts/smoke_verify_release_binary.py",
                "--artifacts-dir",
                "--expected-version",
            ),
        }
        for step_name, required_contract_tokens in create_release_step_contracts.items():
            run_script = create_release_run_by_name.get(step_name)
            if run_script is None:
                continue
            required_command = required_contract_tokens[0]
            if required_command not in run_script:
                errors.append(
                    "Release workflow create-release "
                    f"`{step_name}` step must invoke `{required_command}`"
                )
            for required_flag in required_contract_tokens[1:]:
                if required_flag not in run_script:
                    errors.append(
                        "Release workflow create-release "
                        f"`{step_name}` step must pass `{required_flag}`"
                    )

    validate_tag_parity_job = jobs.get("validate-tag-version-parity")
    if isinstance(validate_tag_parity_job, dict):
        tag_steps = validate_tag_parity_job.get("steps", [])
        tag_run_by_name: dict[str, str] = {}
        if isinstance(tag_steps, list):
            for step in tag_steps:
                if not isinstance(step, dict):
                    continue
                name = step.get("name")
                run = step.get("run")
                if isinstance(name, str) and isinstance(run, str):
                    tag_run_by_name[name] = run
        tag_parity_step = "Validate release tag/version parity across package metadata"
        tag_parity_run = tag_run_by_name.get(tag_parity_step)
        if tag_parity_run is not None:
            for required_flag in ("--expected-version", "--expected-tag"):
                if required_flag not in tag_parity_run:
                    errors.append(
                        "Release workflow validate-tag-version-parity "
                        f"`{tag_parity_step}` step must include `{required_flag}`"
                    )

    def _step_runs_by_name(job_name: str) -> dict[str, str]:
        job = jobs.get(job_name)
        if not isinstance(job, dict):
            return {}
        raw_steps = job.get("steps", [])
        runs: dict[str, str] = {}
        if not isinstance(raw_steps, list):
            return runs
        for step in raw_steps:
            if not isinstance(step, dict):
                continue
            name = step.get("name")
            run = step.get("run")
            if isinstance(name, str) and isinstance(run, str):
                runs[name] = run
        return runs

    verify_release_assets_runs = _step_runs_by_name("verify-release-assets")
    verify_assets_step = "Verify uploaded release assets and checksum coverage"
    verify_assets_run = verify_release_assets_runs.get(verify_assets_step)
    if verify_assets_run is None:
        errors.append(
            f"Release workflow verify-release-assets job must include step `{verify_assets_step}`"
        )
    else:
        if "scripts/verify_github_release_assets.py" not in verify_assets_run:
            errors.append(
                "Release workflow verify-release-assets "
                f"`{verify_assets_step}` step must invoke `scripts/verify_github_release_assets.py`"
            )
        for required_flag in ("--repo", "--tag", "--token"):
            if required_flag not in verify_assets_run:
                errors.append(
                    "Release workflow verify-release-assets "
                    f"`{verify_assets_step}` step must include `{required_flag}`"
                )

    publish_npm_runs = _step_runs_by_name("publish-npm")
    npm_verify_step = "Verify npm registry parity for release version"
    npm_verify_run = publish_npm_runs.get(npm_verify_step)
    release_identity_flags = ("--expected-version", "--expected-tag")
    if npm_verify_run is None:
        errors.append(f"Release workflow publish-npm job must include step `{npm_verify_step}`")
    else:
        for required_flag in release_identity_flags:
            if required_flag not in npm_verify_run:
                errors.append(
                    "Release workflow publish-npm "
                    f"`{npm_verify_step}` step must include `{required_flag}`"
                )
        for required_flag in (
            "--check-npm",
            "--npm-wait-seconds",
            "--npm-poll-interval-seconds",
        ):
            if required_flag not in npm_verify_run:
                errors.append(
                    "Release workflow publish-npm "
                    f"`{npm_verify_step}` step must include `{required_flag}`"
                )

    release_gate_runs = _step_runs_by_name("release-success-gate")
    release_gate_step_contracts = {
        "Verify final npm parity before release success gate": (
            "--check-npm",
            "--npm-wait-seconds",
            "--npm-poll-interval-seconds",
        ),
        "Verify final PyPI parity before release success gate": (
            "--check-pypi",
            "--pypi-wait-seconds",
            "--pypi-poll-interval-seconds",
        ),
    }
    for step_name, required_flags in release_gate_step_contracts.items():
        step_run = release_gate_runs.get(step_name)
        if step_run is None:
            errors.append(
                f"Release workflow release-success-gate job must include step `{step_name}`"
            )
            continue
        for required_flag in release_identity_flags:
            if required_flag not in step_run:
                errors.append(
                    "Release workflow release-success-gate "
                    f"`{step_name}` step must include `{required_flag}`"
                )
        for required_flag in required_flags:
            if required_flag not in step_run:
                errors.append(
                    "Release workflow release-success-gate "
                    f"`{step_name}` step must include `{required_flag}`"
                )

    if "uses: astral-sh/setup-uv@v5" not in release_workflow:
        errors.append(
            "Release workflow package-manager validation must install uv before fallback checks"
        )
    if "--skip-pypi" in release_workflow:
        errors.append("Release workflow must not pass unsupported --skip-pypi flag")
    return errors


def validate_homebrew_formula_contract(*, brew_content: str, py_version: str) -> list[str]:
    errors: list[str] = []
    has_direct_version = f'version "{py_version}"' in brew_content
    has_constant_version = f'TENSOR_GREP_VERSION = "{py_version}"' in brew_content
    if not has_direct_version and not has_constant_version:
        errors.append("Homebrew formula version does not match pyproject version")

    if "TENSOR_GREP_VERSION =" not in brew_content:
        errors.append("Homebrew formula must use explicit TENSOR_GREP_VERSION assignment")

    if "version TENSOR_GREP_VERSION" not in brew_content:
        errors.append("Homebrew formula must declare `version TENSOR_GREP_VERSION`")

    return errors


def validate_all() -> list[str]:
    errors: list[str] = []
    py_version = _version_from_pyproject()
    cargo_version = _version_from_cargo()
    npm_manifest = json.loads(_read(ROOT / "npm" / "package.json"))
    npm_version = str(npm_manifest["version"])

    if cargo_version != py_version:
        errors.append(
            f"Version mismatch: rust_core/Cargo.toml={cargo_version} != pyproject={py_version}"
        )
    if npm_version != py_version:
        errors.append(f"Version mismatch: npm/package.json={npm_version} != pyproject={py_version}")

    npm_repository_url = str((npm_manifest.get("repository") or {}).get("url") or "")
    expected_npm_repo_url = "git+https://github.com/oimiragieo/tensor-grep.git"
    if npm_repository_url != expected_npm_repo_url:
        errors.append(
            "npm/package.json repository.url must be "
            f"{expected_npm_repo_url}, got {npm_repository_url or '<empty>'}"
        )

    winget_path = ROOT / "scripts" / "oimiragieo.tensor-grep.yaml"
    winget = _read(winget_path)
    errors.extend(validate_winget_manifest(winget_content=winget, py_version=py_version))

    brew = _read(ROOT / "scripts" / "tensor-grep.rb")
    errors.extend(validate_homebrew_formula_contract(brew_content=brew, py_version=py_version))
    expected_macos_url = f"https://github.com/oimiragieo/tensor-grep/releases/download/v{py_version}/tg-macos-amd64-cpu"
    expected_linux_url = f"https://github.com/oimiragieo/tensor-grep/releases/download/v{py_version}/tg-linux-amd64-cpu"
    templated_macos_url = (
        "https://github.com/oimiragieo/tensor-grep/releases/download/v#{version}/tg-macos-amd64-cpu"
    )
    templated_linux_url = (
        "https://github.com/oimiragieo/tensor-grep/releases/download/v#{version}/tg-linux-amd64-cpu"
    )
    if expected_macos_url not in brew and templated_macos_url not in brew:
        errors.append("Homebrew formula macOS URL does not match expected release artifact URL")
    if expected_linux_url not in brew and templated_linux_url not in brew:
        errors.append("Homebrew formula Linux URL does not match expected release artifact URL")
    if "PLACEHOLDER" in brew:
        errors.append("Homebrew formula contains unresolved PLACEHOLDER text")

    release_workflow = _read(ROOT / ".github" / "workflows" / "release.yml")
    errors.extend(validate_release_workflow_content(release_workflow=release_workflow))

    ci_workflow = _read(ROOT / ".github" / "workflows" / "ci.yml")
    errors.extend(validate_ci_workflow_content(ci_workflow=ci_workflow))

    package_manager_runbook = _read(ROOT / "docs" / "package_manager_publish.md")
    release_checklist = _read(ROOT / "docs" / "RELEASE_CHECKLIST.md")
    installation_docs = _read(ROOT / "docs" / "installation.md")
    errors.extend(
        validate_package_manager_docs(
            runbook_content=package_manager_runbook,
            checklist_content=release_checklist,
        )
    )
    errors.extend(validate_installation_docs(installation_content=installation_docs))

    pyproject_data = tomllib.loads(_read(ROOT / "pyproject.toml"))
    semantic_release = pyproject_data.get("tool", {}).get("semantic_release", {})
    build_command = str(semantic_release.get("build_command", ""))
    if "scripts/stamp_release_assets.py" not in build_command:
        errors.append(
            "semantic_release.build_command must run scripts/stamp_release_assets.py before build"
        )
    version_toml = semantic_release.get("version_toml", [])
    version_variables = semantic_release.get("version_variables", [])
    required_toml_entries = {
        "pyproject.toml:project.version",
        "rust_core/Cargo.toml:package.version",
    }
    required_variable_entries = {
        "src/tensor_grep/cli/main.py:pkg_version",
        "npm/package.json:version",
        "scripts/tensor-grep.rb:TENSOR_GREP_VERSION",
        "scripts/oimiragieo.tensor-grep.yaml:PackageVersion",
        "scripts/oimiragieo.tensor-grep.yaml:InstallerUrl",
    }
    missing_toml = sorted(required_toml_entries - set(version_toml))
    missing_variables = sorted(required_variable_entries - set(version_variables))
    if missing_toml:
        errors.append("semantic_release.version_toml missing entries: " + ", ".join(missing_toml))
    if missing_variables:
        errors.append(
            "semantic_release.version_variables missing entries: " + ", ".join(missing_variables)
        )

    return errors


def main() -> int:
    errors = validate_all()
    if errors:
        for err in errors:
            print(f"ERROR: {err}")
        return 1
    print("Release/package assets validation passed.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
