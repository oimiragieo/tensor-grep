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
        "release-intent:",
        "Validate PR title for semantic release",
        "scripts/validate_pr_title_semver.py",
        "package-manager-readiness:",
        "Build docs site (strict)",
        "pip install mkdocs-material",
        "mkdocs build --strict",
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
        "Dependency install failed after 5 attempts.",
        "Verify cuDF / RAPIDS Configuration (with retry)",
        "GPU dependency install failed (attempt ${attempt}/5); retrying after backoff...",
        "GPU dependency install failed after 5 attempts.",
        "publish-success-gate:",
        "Confirm publish job result when publishing is required",
        "Verify PyPI parity for semantic-release version (always)",
        "Skip publish parity gate when semantic-release produced no version",
        "Verify release version parity across tag/assets/PyPI",
        "scripts/validate_release_version_parity.py",
        "uses: actions/checkout@v6",
        "uses: actions/setup-python@v6",
        "uses: actions/upload-artifact@v7",
        "uses: actions/download-artifact@v8",
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

    uv_bootstrap_count = ci_workflow.count("uses: astral-sh/setup-uv@v8.0.0") + ci_workflow.count(
        "python -m pip install uv"
    )
    if uv_bootstrap_count < 2:
        errors.append("CI workflow should bootstrap uv in package-manager/release validation paths")

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
            release_intent_job = jobs.get("release-intent")
            if isinstance(release_intent_job, dict):
                release_intent_if = release_intent_job.get("if")
                if release_intent_if != "github.event_name == 'pull_request'":
                    errors.append(
                        "CI workflow release-intent job must run only for pull_request events"
                    )
                release_intent_steps = release_intent_job.get("steps", [])
                release_intent_run_by_name: dict[str, str] = {}
                if isinstance(release_intent_steps, list):
                    for step in release_intent_steps:
                        if not isinstance(step, dict):
                            continue
                        name = step.get("name")
                        run = step.get("run")
                        if isinstance(name, str) and isinstance(run, str):
                            release_intent_run_by_name[name] = run
                step_name = "Validate PR title for semantic release"
                run = release_intent_run_by_name.get(step_name)
                if run is None:
                    errors.append(f"CI workflow release-intent job must include step `{step_name}`")
                elif "scripts/validate_pr_title_semver.py" not in run:
                    errors.append(
                        "CI workflow release-intent "
                        f"`{step_name}` step must invoke `scripts/validate_pr_title_semver.py`"
                    )

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
                benchmark_step_names: set[str] = set()
                if isinstance(benchmark_steps, list):
                    for step in benchmark_steps:
                        if not isinstance(step, dict):
                            continue
                        name = step.get("name")
                        if isinstance(name, str):
                            benchmark_step_names.add(name)
                        run = step.get("run")
                        if isinstance(name, str) and isinstance(run, str):
                            benchmark_run_by_name[name] = run
                install_benchmark_run = benchmark_run_by_name.get("Install benchmark dependencies")
                if install_benchmark_run is None:
                    errors.append(
                        "CI workflow benchmark-regression job must include step "
                        "`Install benchmark dependencies`"
                    )
                elif '".[bench,dev]"' not in install_benchmark_run:
                    errors.append(
                        "CI workflow benchmark-regression `Install benchmark dependencies` step "
                        "must install `.[bench,dev]`"
                    )
                required_benchmark_step_names = {"Run hot-query benchmark suite"}
                for step_name in required_benchmark_step_names:
                    if step_name not in benchmark_step_names:
                        errors.append(
                            f"CI workflow benchmark-regression job must include step `{step_name}`"
                        )
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
                gate_if_by_name: dict[str, str] = {}
                gate_step_names: set[str] = set()
                if isinstance(gate_steps, list):
                    for step in gate_steps:
                        if not isinstance(step, dict):
                            continue
                        name = step.get("name")
                        if isinstance(name, str):
                            gate_step_names.add(name)
                        step_if = step.get("if")
                        if isinstance(name, str) and isinstance(step_if, str):
                            gate_if_by_name[name] = step_if
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
                        "--check-pypi",
                        "--pypi-wait-seconds",
                        "--pypi-poll-interval-seconds",
                    ):
                        if required_flag not in gate_parity_run:
                            errors.append(
                                "CI workflow publish-success-gate "
                                f"`{gate_parity_step}` step must include `{required_flag}`"
                            )
                    if "--dist-dir" not in gate_parity_run:
                        errors.append(
                            "CI workflow publish-success-gate "
                            f"`{gate_parity_step}` step must include `--dist-dir` in its publish_pypi conditional branch"
                        )
                    if "publish_pypi" not in gate_parity_run:
                        errors.append(
                            "CI workflow publish-success-gate "
                            f"`{gate_parity_step}` step must conditionally gate `--dist-dir` on `publish_pypi`"
                        )
                if "Download all distributions" not in gate_step_names:
                    errors.append(
                        "CI workflow publish-success-gate job must include step `Download all distributions`"
                    )
                else:
                    download_if = gate_if_by_name.get("Download all distributions", "")
                    if "publish_pypi == 'true'" not in download_if:
                        errors.append(
                            "CI workflow publish-success-gate `Download all distributions` step must run only when `publish_pypi == 'true'`"
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

    action_versions = {
        "actions/checkout": "v6",
        "actions/setup-python": "v6",
        "actions/setup-node": "v6",
        "actions/upload-artifact": "v7",
        "actions/download-artifact": "v8",
        "astral-sh/setup-uv": "v8.0.0",
    }
    for match in re.finditer(r"uses:\s+([^@\s\n]+)@([^\s\n]+)", ci_workflow):
        action = match.group(1)
        version = match.group(2)
        if action in action_versions:
            expected_version = action_versions[action]
            if version != expected_version:
                errors.append(
                    f"CI workflow must use {action}@{expected_version}, found @{version}"
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
        "feat: ...` -> minor",
        "fix: ...` or `perf: ...` -> patch",
        "feat!: ...` / `fix!: ...` -> major",
        "Squash and merge",
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


def validate_readme_contract(*, readme_content: str) -> list[str]:
    errors: list[str] = []
    for expected in (
        "## Canonical Docs",
        "[docs/benchmarks.md](docs/benchmarks.md)",
        "[docs/gpu_crossover.md](docs/gpu_crossover.md)",
        "[docs/routing_policy.md](docs/routing_policy.md)",
        "[docs/harness_api.md](docs/harness_api.md)",
        "[docs/harness_cookbook.md](docs/harness_cookbook.md)",
    ):
        if expected not in readme_content:
            errors.append(f"README missing canonical docs reference: {expected}")

    if "[docs/installation.md](docs/installation.md)" not in readme_content:
        errors.append(
            "README must link installation docs: [docs/installation.md](docs/installation.md)"
        )

    if "[docs/RELEASE_CHECKLIST.md](docs/RELEASE_CHECKLIST.md)" not in readme_content:
        errors.append(
            "README must link release checklist: [docs/RELEASE_CHECKLIST.md](docs/RELEASE_CHECKLIST.md)"
        )

    if "`tensor-grep` has first class support on Windows, macOS and Linux." not in readme_content:
        errors.append("README must state current first-class platform support explicitly")

    if "public contracts in [docs/harness_api.md](docs/harness_api.md)" not in readme_content:
        errors.append("README must direct harness consumers to docs/harness_api.md")

    return errors


def validate_benchmarks_docs(*, benchmarks_content: str) -> list[str]:
    errors: list[str] = []
    for expected in (
        "## Benchmark Matrix",
        "| Surface | Script | Default artifact |",
        "`benchmarks/run_benchmarks.py`",
        "`benchmarks/run_hot_query_benchmarks.py`",
        "`benchmarks/run_ast_rewrite_benchmarks.py`",
    ):
        if expected not in benchmarks_content:
            errors.append(f"Benchmark docs missing required matrix contract: {expected}")

    for expected in (
        "## Artifact Conventions",
        "`suite`",
        "`artifact`",
        "`environment`",
        "`generated_at_epoch_s`",
    ):
        if expected not in benchmarks_content:
            errors.append(f"Benchmark docs missing required artifact convention: {expected}")

    for expected in (
        "## Acceptance Rules",
        "Do not update benchmark docs or claims until the relevant artifact has been rerun on the accepted line.",
        "Compare against the current accepted baseline, not memory.",
        "Keep backend labels explicit in artifacts so routing claims are auditable.",
    ):
        if expected not in benchmarks_content:
            errors.append(f"Benchmark docs missing required acceptance rule: {expected}")

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
        "Generate Rust SBOM",
        "Generate Python SBOM",
        "Sign artifacts with Sigstore",
        "gh-action-sigstore-python",
        "Generate SLSA Provenance",
        "attest-build-provenance",
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
    build_binaries_job = jobs.get("build-binaries")
    if isinstance(build_binaries_job, dict):
        build_steps = build_binaries_job.get("steps", [])
        build_steps_by_name: dict[str, dict[str, object]] = {}
        build_run_by_name: dict[str, str] = {}
        if isinstance(build_steps, list):
            for step in build_steps:
                if not isinstance(step, dict):
                    continue
                name = step.get("name")
                if isinstance(name, str):
                    build_steps_by_name[name] = step
                    run = step.get("run")
                    if isinstance(run, str):
                        build_run_by_name[name] = run
        build_install_uv_step = build_steps_by_name.get("Install uv")
        if build_install_uv_step is None:
            errors.append("Release workflow build-binaries job must include step `Install uv`")
        else:
            uses_value = build_install_uv_step.get("uses")
            if uses_value != "astral-sh/setup-uv@v8.0.0":
                errors.append(
                    "Release workflow build-binaries `Install uv` step must use `astral-sh/setup-uv@v8.0.0`"
                )
        build_setup_python_run = build_run_by_name.get("Set up Python")
        if build_setup_python_run is None:
            errors.append("Release workflow build-binaries job must include step `Set up Python`")
        elif "uv python install 3.12" not in build_setup_python_run:
            errors.append(
                "Release workflow build-binaries `Set up Python` step must invoke `uv python install 3.12`"
            )
        build_binary_run = build_run_by_name.get("Build Binary")
        if build_binary_run is None:
            errors.append("Release workflow build-binaries job must include step `Build Binary`")
        elif "scripts/build_binaries.py" not in build_binary_run:
            errors.append(
                "Release workflow build-binaries `Build Binary` step must invoke `scripts/build_binaries.py`"
            )
        build_install_contracts = {
            "Install dependencies (CPU)": (
                "uv venv",
                'uv pip install -e ".[dev]"',
                "uv pip install nuitka",
            ),
            "Install dependencies (NVIDIA)": (
                "uv venv",
                "uv pip install torch torchvision torchaudio --index-url https://download.pytorch.org/whl/cu124",
                'uv pip install -e ".[gpu-win,nlp,ast,dev]"',
                "uv pip install nuitka",
            ),
        }
        for step_name, required_tokens in build_install_contracts.items():
            run_script = build_run_by_name.get(step_name)
            if run_script is None:
                errors.append(
                    f"Release workflow build-binaries job must include step `{step_name}`"
                )
                continue
            for required_token in required_tokens:
                if required_token not in run_script:
                    errors.append(
                        "Release workflow build-binaries "
                        f"`{step_name}` step must invoke `{required_token}`"
                    )

        upload_step = build_steps_by_name.get("Upload Artifact")
        if upload_step is None:
            errors.append("Release workflow build-binaries job must include step `Upload Artifact`")
        else:
            uses_value = upload_step.get("uses")
            if uses_value != "actions/upload-artifact@v7":
                errors.append(
                    "Release workflow build-binaries `Upload Artifact` step must use `actions/upload-artifact@v7`"
                )
            with_block = upload_step.get("with")
            if not isinstance(with_block, dict):
                errors.append(
                    "Release workflow build-binaries `Upload Artifact` step must define a `with` mapping"
                )
            elif str(with_block.get("path")) != "tg-*":
                errors.append(
                    "Release workflow build-binaries `Upload Artifact` step must include `path: tg-*`"
                )
        build_step_contracts = {
            "Rename Artifact (Windows)": ("mv tg.exe tg-windows-amd64-${{ matrix.gpu }}.exe",),
            "Rename Artifact (Linux)": ("mv tg tg-linux-amd64-${{ matrix.gpu }}",),
            "Rename Artifact (macOS)": ("mv tg tg-macos-amd64-${{ matrix.gpu }}",),
            "Smoke-test Binary (Windows)": (r".\tg-windows-amd64-${{ matrix.gpu }}.exe --version",),
            "Smoke-test Binary (Linux)": (
                "chmod +x tg-linux-amd64-${{ matrix.gpu }}",
                "./tg-linux-amd64-${{ matrix.gpu }} --version",
            ),
            "Smoke-test Binary (macOS)": (
                "chmod +x tg-macos-amd64-${{ matrix.gpu }}",
                "./tg-macos-amd64-${{ matrix.gpu }} --version",
            ),
        }
        for step_name, required_tokens in build_step_contracts.items():
            run_script = build_run_by_name.get(step_name)
            if run_script is None:
                errors.append(
                    f"Release workflow build-binaries job must include step `{step_name}`"
                )
                continue
            for required_token in required_tokens:
                if required_token not in run_script:
                    errors.append(
                        "Release workflow build-binaries "
                        f"`{step_name}` step must invoke `{required_token}`"
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
    publish_docs_job = jobs.get("publish-docs")
    if isinstance(publish_docs_job, dict):
        docs_steps = publish_docs_job.get("steps", [])
        docs_run_by_name: dict[str, str] = {}
        docs_step_names: set[str] = set()
        docs_steps_by_name: dict[str, dict[str, object]] = {}
        docs_uses_values: list[str] = []
        if isinstance(docs_steps, list):
            for step in docs_steps:
                if not isinstance(step, dict):
                    continue
                name = step.get("name")
                run = step.get("run")
                uses_value = step.get("uses")
                if isinstance(uses_value, str):
                    docs_uses_values.append(uses_value)
                if isinstance(name, str):
                    docs_step_names.add(name)
                    docs_steps_by_name[name] = step
                    if isinstance(run, str):
                        docs_run_by_name[name] = run
        if "actions/checkout@v6" not in docs_uses_values:
            errors.append("Release workflow publish-docs job must include `actions/checkout@v6`")
        setup_python_step = docs_steps_by_name.get("Set up Python")
        if setup_python_step is None:
            errors.append("Release workflow publish-docs job must include step `Set up Python`")
        else:
            uses_value = setup_python_step.get("uses")
            if uses_value != "actions/setup-python@v6":
                errors.append(
                    "Release workflow publish-docs `Set up Python` step must use `actions/setup-python@v6`"
                )
            with_block = setup_python_step.get("with")
            if not isinstance(with_block, dict):
                errors.append(
                    "Release workflow publish-docs `Set up Python` step must define a `with` mapping"
                )
            elif str(with_block.get("python-version")) != "3.11":
                errors.append(
                    "Release workflow publish-docs `Set up Python` step must include `python-version: 3.11`"
                )
        docs_step_contracts = {
            "Install mkdocs": ("pip install mkdocs-material",),
            "Build Docs": ("mkdocs build --strict",),
            "Deploy Docs": ("mkdocs gh-deploy --force",),
        }
        for step_name, required_tokens in docs_step_contracts.items():
            run_script = docs_run_by_name.get(step_name)
            if run_script is None:
                errors.append(f"Release workflow publish-docs job must include step `{step_name}`")
                continue
            for required_token in required_tokens:
                if required_token not in run_script:
                    errors.append(
                        "Release workflow publish-docs "
                        f"`{step_name}` step must invoke `{required_token}`"
                    )
        install_mkdocs_run = docs_run_by_name.get("Install mkdocs")
        if install_mkdocs_run is not None and not install_mkdocs_run.lstrip().startswith(
            "pip install mkdocs-material"
        ):
            errors.append(
                "Release workflow publish-docs "
                "`Install mkdocs` step must invoke `pip install mkdocs-material`"
            )
        build_docs_run = docs_run_by_name.get("Build Docs")
        if build_docs_run is not None and not build_docs_run.lstrip().startswith(
            "mkdocs build --strict"
        ):
            errors.append(
                "Release workflow publish-docs "
                "`Build Docs` step must invoke `mkdocs build --strict`"
            )
        deploy_docs_run = docs_run_by_name.get("Deploy Docs")
        if deploy_docs_run is not None:
            if not deploy_docs_run.lstrip().startswith("mkdocs gh-deploy --force"):
                errors.append(
                    "Release workflow publish-docs "
                    "`Deploy Docs` step must invoke `mkdocs gh-deploy --force`"
                )
            elif "mkdocs gh-deploy --force" in deploy_docs_run:
                for required_token in ("mkdocs", "gh-deploy", "--force"):
                    if required_token not in deploy_docs_run:
                        errors.append(
                            "Release workflow publish-docs "
                            f"`Deploy Docs` step must invoke `{required_token}`"
                        )

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
            "Validate package-manager publish bundle source state",
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
            "Validate package-manager publish bundle source state": (
                "scripts/prepare_package_manager_release.py",
                "--check",
            ),
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
        create_release_steps_by_name: dict[str, dict[str, object]] = {}
        if isinstance(create_release_steps, list):
            for step in create_release_steps:
                if not isinstance(step, dict):
                    continue
                name = step.get("name")
                if isinstance(name, str):
                    create_release_step_names.add(name)
                    create_release_steps_by_name[name] = step
                    run = step.get("run")
                    if isinstance(run, str):
                        create_release_run_by_name[name] = run
        install_uv_step = create_release_steps_by_name.get("Install uv")
        if install_uv_step is None:
            errors.append("Release workflow create-release job must include step `Install uv`")
        else:
            uses_value = install_uv_step.get("uses")
            if uses_value != "astral-sh/setup-uv@v8.0.0":
                errors.append(
                    "Release workflow create-release `Install uv` step must use `astral-sh/setup-uv@v8.0.0`"
                )
        setup_python_run = create_release_run_by_name.get("Setup Python")
        if setup_python_run is None:
            errors.append("Release workflow create-release job must include step `Setup Python`")
        elif "uv python install 3.12" not in setup_python_run:
            errors.append(
                "Release workflow create-release `Setup Python` step must invoke `uv python install 3.12`"
            )
        download_artifacts_step = create_release_steps_by_name.get("Download Artifacts")
        if download_artifacts_step is None:
            errors.append(
                "Release workflow create-release job must include step `Download Artifacts`"
            )
        else:
            uses_value = download_artifacts_step.get("uses")
            if uses_value != "actions/download-artifact@v8":
                errors.append(
                    "Release workflow create-release `Download Artifacts` step must use `actions/download-artifact@v8`"
                )
            with_block = download_artifacts_step.get("with")
            if not isinstance(with_block, dict):
                errors.append(
                    "Release workflow create-release `Download Artifacts` step must define a `with` mapping"
                )
            elif str(with_block.get("path")) != "artifacts":
                errors.append(
                    "Release workflow create-release `Download Artifacts` step must include `path: artifacts`"
                )
        for required_step in (
            "Validate release binary artifact matrix and generate checksums",
            "Build package-manager publish bundle",
            "Verify package-manager bundle checksums",
            "Smoke-test package-manager bundle contracts",
            "Smoke-verify Linux release binary version",
            "Generate Rust SBOM",
            "Generate Python SBOM",
            "Sign artifacts with Sigstore",
            "Generate SLSA Provenance",
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
            "Generate Rust SBOM": (
                "cargo cyclonedx",
                "--format json",
                "--all-features",
            ),
            "Generate Python SBOM": (
                "cyclonedx-py environment",
                "--outfile artifacts/sbom-python.json",
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

        sigstore_step = create_release_steps_by_name.get("Sign artifacts with Sigstore")
        if sigstore_step is not None:
            uses_value = sigstore_step.get("uses", "")
            if "sigstore/gh-action-sigstore-python" not in str(uses_value):
                errors.append(
                    "Release workflow create-release `Sign artifacts with Sigstore` step must use `sigstore/gh-action-sigstore-python`"
                )

        slsa_step = create_release_steps_by_name.get("Generate SLSA Provenance")
        if slsa_step is not None:
            uses_value = slsa_step.get("uses", "")
            if "actions/attest-build-provenance" not in str(uses_value):
                errors.append(
                    "Release workflow create-release `Generate SLSA Provenance` step must use `actions/attest-build-provenance`"
                )
        github_release_step = create_release_steps_by_name.get("Create GitHub Release")
        if github_release_step is None:
            errors.append(
                "Release workflow create-release job must include step `Create GitHub Release`"
            )
        else:
            uses_value = github_release_step.get("uses")
            if uses_value != "softprops/action-gh-release@v2":
                errors.append(
                    "Release workflow create-release `Create GitHub Release` step must use `softprops/action-gh-release@v2`"
                )
            with_block = github_release_step.get("with")
            if not isinstance(with_block, dict):
                errors.append(
                    "Release workflow create-release `Create GitHub Release` step must define a `with` mapping"
                )
            else:
                files_value = with_block.get("files")
                files_text = files_value if isinstance(files_value, str) else ""
                for required_asset in (
                    "artifacts/**/tg-*",
                    "artifacts/CHECKSUMS.txt",
                    "artifacts/package-manager-bundle/**",
                ):
                    if required_asset not in files_text:
                        errors.append(
                            "Release workflow create-release "
                            f"`Create GitHub Release` step must include `{required_asset}`"
                        )
                if with_block.get("generate_release_notes") is not True:
                    errors.append(
                        "Release workflow create-release `Create GitHub Release` step must set `generate_release_notes: true`"
                    )

    validate_tag_parity_job = jobs.get("validate-tag-version-parity")
    if isinstance(validate_tag_parity_job, dict):
        tag_steps = validate_tag_parity_job.get("steps", [])
        tag_run_by_name: dict[str, str] = {}
        tag_step_names: set[str] = set()
        tag_steps_by_name: dict[str, dict[str, object]] = {}
        tag_uses_values: list[str] = []
        if isinstance(tag_steps, list):
            for step in tag_steps:
                if not isinstance(step, dict):
                    continue
                name = step.get("name")
                run = step.get("run")
                uses_value = step.get("uses")
                if isinstance(uses_value, str):
                    tag_uses_values.append(uses_value)
                if isinstance(name, str):
                    tag_step_names.add(name)
                    tag_steps_by_name[name] = step
                    if isinstance(run, str):
                        tag_run_by_name[name] = run
        if "actions/checkout@v6" not in tag_uses_values:
            errors.append(
                "Release workflow validate-tag-version-parity job must include `actions/checkout@v6`"
            )
        for required_step in ("Install uv", "Setup Python"):
            if required_step not in tag_step_names:
                errors.append(
                    "Release workflow validate-tag-version-parity "
                    f"job must include step `{required_step}`"
                )
        install_uv_step = tag_steps_by_name.get("Install uv")
        if install_uv_step is not None:
            uses_value = install_uv_step.get("uses")
            if uses_value != "astral-sh/setup-uv@v8.0.0":
                errors.append(
                    "Release workflow validate-tag-version-parity "
                    "`Install uv` step must use `astral-sh/setup-uv@v8.0.0`"
                )
        setup_python_run = tag_run_by_name.get("Setup Python")
        if setup_python_run is not None and "uv python install 3.12" not in setup_python_run:
            errors.append(
                "Release workflow validate-tag-version-parity "
                "`Setup Python` step must invoke `uv python install 3.12`"
            )
        tag_parity_step = "Validate release tag/version parity across package metadata"
        tag_parity_run = tag_run_by_name.get(tag_parity_step)
        if tag_parity_run is None:
            errors.append(
                "Release workflow validate-tag-version-parity "
                f"job must include step `{tag_parity_step}`"
            )
        else:
            if not tag_parity_run.lstrip().startswith(
                "python scripts/validate_release_version_parity.py"
            ):
                errors.append(
                    "Release workflow validate-tag-version-parity "
                    f"`{tag_parity_step}` step must invoke "
                    "`python scripts/validate_release_version_parity.py`"
                )
            if "scripts/validate_release_version_parity.py" not in tag_parity_run:
                errors.append(
                    "Release workflow validate-tag-version-parity "
                    f"`{tag_parity_step}` step must invoke `scripts/validate_release_version_parity.py`"
                )
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
    verify_release_assets_job = jobs.get("verify-release-assets")
    if isinstance(verify_release_assets_job, dict):
        verify_release_assets_steps = verify_release_assets_job.get("steps", [])
        checkout_uses_values = []
        if isinstance(verify_release_assets_steps, list):
            for step in verify_release_assets_steps:
                if not isinstance(step, dict):
                    continue
                uses_value = step.get("uses")
                if isinstance(uses_value, str):
                    checkout_uses_values.append(uses_value)
        if "actions/checkout@v6" not in checkout_uses_values:
            errors.append(
                "Release workflow verify-release-assets job must include `actions/checkout@v6`"
            )
    verify_assets_step = "Verify uploaded release assets and checksum coverage"
    verify_assets_run = verify_release_assets_runs.get(verify_assets_step)
    if verify_assets_run is None:
        errors.append(
            f"Release workflow verify-release-assets job must include step `{verify_assets_step}`"
        )
    else:
        if not verify_assets_run.lstrip().startswith(
            "python scripts/verify_github_release_assets.py"
        ):
            errors.append(
                "Release workflow verify-release-assets "
                f"`{verify_assets_step}` step must invoke `python scripts/verify_github_release_assets.py`"
            )
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
    publish_npm_job = jobs.get("publish-npm")
    if isinstance(publish_npm_job, dict):
        npm_steps = publish_npm_job.get("steps", [])
        npm_steps_by_name: dict[str, dict[str, object]] = {}
        npm_uses_values: list[str] = []
        if isinstance(npm_steps, list):
            for step in npm_steps:
                if not isinstance(step, dict):
                    continue
                name = step.get("name")
                uses_value = step.get("uses")
                if isinstance(uses_value, str):
                    npm_uses_values.append(uses_value)
                if isinstance(name, str):
                    npm_steps_by_name[name] = step
        if "actions/checkout@v6" not in npm_uses_values:
            errors.append("Release workflow publish-npm job must include `actions/checkout@v6`")
        setup_node_step = npm_steps_by_name.get("Setup Node.js")
        if setup_node_step is None:
            errors.append("Release workflow publish-npm job must include step `Setup Node.js`")
        else:
            uses_value = setup_node_step.get("uses")
            if uses_value != "actions/setup-node@v6":
                errors.append(
                    "Release workflow publish-npm `Setup Node.js` step must use `actions/setup-node@v6`"
                )
            with_block = setup_node_step.get("with")
            if not isinstance(with_block, dict):
                errors.append(
                    "Release workflow publish-npm `Setup Node.js` step must define a `with` mapping"
                )
            else:
                if str(with_block.get("node-version")) != "22":
                    errors.append(
                        "Release workflow publish-npm `Setup Node.js` step must include `node-version: 22`"
                    )
                if str(with_block.get("registry-url")) != "https://registry.npmjs.org":
                    errors.append(
                        "Release workflow publish-npm `Setup Node.js` step must include `registry-url: https://registry.npmjs.org`"
                    )
        install_uv_step = npm_steps_by_name.get("Install uv")
        if install_uv_step is None:
            errors.append("Release workflow publish-npm job must include step `Install uv`")
        else:
            uses_value = install_uv_step.get("uses")
            if uses_value != "astral-sh/setup-uv@v8.0.0":
                errors.append(
                    "Release workflow publish-npm `Install uv` step must use `astral-sh/setup-uv@v8.0.0`"
                )
        setup_python_run = publish_npm_runs.get("Setup Python")
        if setup_python_run is None:
            errors.append("Release workflow publish-npm job must include step `Setup Python`")
        elif "uv python install 3.12" not in setup_python_run:
            errors.append(
                "Release workflow publish-npm `Setup Python` step must invoke `uv python install 3.12`"
            )

    npm_version_match_step = "Verify Version Match"
    npm_version_match_run = publish_npm_runs.get(npm_version_match_step)
    if npm_version_match_run is None:
        errors.append(
            f"Release workflow publish-npm job must include step `{npm_version_match_step}`"
        )
    else:
        if not npm_version_match_run.lstrip().startswith("TAG_VERSION=${GITHUB_REF#refs/tags/v}"):
            errors.append(
                "Release workflow publish-npm "
                f"`{npm_version_match_step}` step must begin with `TAG_VERSION=${{GITHUB_REF#refs/tags/v}}`"
            )
        required_tokens = (
            "node -p \"require('./npm/package.json').version\"",
            'if [ "$TAG_VERSION" != "$NPM_VERSION" ]',
        )
        for required_token in required_tokens:
            if required_token not in npm_version_match_run:
                errors.append(
                    "Release workflow publish-npm "
                    f"`{npm_version_match_step}` step must invoke `{required_token}`"
                )

    npm_publish_step = "Publish NPM Package"
    npm_publish_run = publish_npm_runs.get(npm_publish_step)
    npm_publish_step_config = None
    if isinstance(publish_npm_job, dict):
        npm_publish_step_config = npm_steps_by_name.get(npm_publish_step)
    if npm_publish_run is None:
        errors.append(f"Release workflow publish-npm job must include step `{npm_publish_step}`")
    else:
        if "npm publish --access public" not in npm_publish_run:
            errors.append(
                "Release workflow publish-npm "
                f"`{npm_publish_step}` step must invoke `npm publish --access public`"
            )
        if not isinstance(npm_publish_step_config, dict):
            errors.append(
                f"Release workflow publish-npm job must include step `{npm_publish_step}`"
            )
        else:
            if str(npm_publish_step_config.get("working-directory")) != "npm":
                errors.append(
                    "Release workflow publish-npm "
                    f"`{npm_publish_step}` step must include `working-directory: npm`"
                )
            env_block = npm_publish_step_config.get("env")
            if not isinstance(env_block, dict):
                errors.append(
                    "Release workflow publish-npm "
                    f"`{npm_publish_step}` step must define an `env` mapping"
                )
            elif str(env_block.get("NODE_AUTH_TOKEN")) != "${{ secrets.NPM_TOKEN }}":
                errors.append(
                    "Release workflow publish-npm "
                    f"`{npm_publish_step}` step must include `NODE_AUTH_TOKEN: ${{{{ secrets.NPM_TOKEN }}}}`"
                )

    npm_verify_step = "Verify npm registry parity for release version"
    npm_verify_run = publish_npm_runs.get(npm_verify_step)
    release_identity_flags = ("--expected-version", "--expected-tag")
    if npm_verify_run is None:
        errors.append(f"Release workflow publish-npm job must include step `{npm_verify_step}`")
    else:
        if not npm_verify_run.lstrip().startswith(
            "python scripts/validate_release_version_parity.py"
        ):
            errors.append(
                "Release workflow publish-npm "
                f"`{npm_verify_step}` step must invoke `python scripts/validate_release_version_parity.py`"
            )
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
    release_gate_job = jobs.get("release-success-gate")
    if isinstance(release_gate_job, dict):
        release_gate_steps = release_gate_job.get("steps", [])
        release_gate_uses_values: list[str] = []
        release_gate_steps_by_name: dict[str, dict[str, object]] = {}
        if isinstance(release_gate_steps, list):
            for step in release_gate_steps:
                if not isinstance(step, dict):
                    continue
                uses_value = step.get("uses")
                if isinstance(uses_value, str):
                    release_gate_uses_values.append(uses_value)
                name = step.get("name")
                if isinstance(name, str):
                    release_gate_steps_by_name[name] = step
        if "actions/checkout@v6" not in release_gate_uses_values:
            errors.append(
                "Release workflow release-success-gate job must include `actions/checkout@v6`"
            )
        install_uv_step = release_gate_steps_by_name.get("Install uv")
        if install_uv_step is None:
            errors.append(
                "Release workflow release-success-gate job must include step `Install uv`"
            )
        else:
            uses_value = install_uv_step.get("uses")
            if uses_value != "astral-sh/setup-uv@v8.0.0":
                errors.append(
                    "Release workflow release-success-gate `Install uv` step must use `astral-sh/setup-uv@v8.0.0`"
                )
        setup_python_run = release_gate_runs.get("Setup Python")
        if setup_python_run is None:
            errors.append(
                "Release workflow release-success-gate job must include step `Setup Python`"
            )
        elif "uv python install 3.12" not in setup_python_run:
            errors.append(
                "Release workflow release-success-gate `Setup Python` step must invoke `uv python install 3.12`"
            )
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
        if not step_run.lstrip().startswith("python scripts/validate_release_version_parity.py"):
            errors.append(
                "Release workflow release-success-gate "
                f"`{step_name}` step must invoke `python scripts/validate_release_version_parity.py`"
            )
        if "scripts/validate_release_version_parity.py" not in step_run:
            errors.append(
                "Release workflow release-success-gate "
                f"`{step_name}` step must invoke `scripts/validate_release_version_parity.py`"
            )
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
    release_gate_confirm_step = "Confirm release publication gates"
    release_gate_confirm_run = release_gate_runs.get(release_gate_confirm_step)
    if release_gate_confirm_run is None:
        errors.append(
            "Release workflow release-success-gate "
            f"job must include step `{release_gate_confirm_step}`"
        )
    elif (
        'echo "Release publication gates passed: parity, npm, docs."'
        not in release_gate_confirm_run
    ):
        errors.append(
            "Release workflow release-success-gate "
            f"`{release_gate_confirm_step}` step must invoke "
            '`echo "Release publication gates passed: parity, npm, docs."`'
        )

    if "uses: astral-sh/setup-uv@v8.0.0" not in release_workflow:
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
    benchmarks_docs = _read(ROOT / "docs" / "benchmarks.md")
    readme = _read(ROOT / "README.md")
    errors.extend(
        validate_package_manager_docs(
            runbook_content=package_manager_runbook,
            checklist_content=release_checklist,
        )
    )
    errors.extend(validate_installation_docs(installation_content=installation_docs))
    errors.extend(validate_benchmarks_docs(benchmarks_content=benchmarks_docs))
    errors.extend(validate_readme_contract(readme_content=readme))

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
