"""ci.yml content contract -- the single largest release-asset check."""

from __future__ import annotations

import re

import yaml

from .constants import RELEASE_JOB_REQUIRED_GATES
from .helpers import _normalize_pinned_actions


def validate_ci_workflow_content(*, ci_workflow: str) -> list[str]:
    ci_workflow = _normalize_pinned_actions(ci_workflow)
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
        "Prefetch Rust dependencies for PyPI artifacts",
        'CARGO_NET_RETRY: "10"',
        "cargo fetch --manifest-path rust_core/Cargo.toml",
        "Cargo dependency prefetch failed after 5 attempts.",
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
        "needs: [release, build-wheels-pypi, build-sdist-pypi, validate-pypi-artifacts, publish-github-release-assets]"
        not in ci_workflow
    ):
        errors.append(
            "publish-pypi must depend on validate-pypi-artifacts and publish-github-release-assets before uploading to PyPI"
        )

    uv_bootstrap_count = ci_workflow.count("uses: astral-sh/setup-uv@v8.0.0") + ci_workflow.count(
        "python -m pip install uv"
    )
    if uv_bootstrap_count < 2:
        errors.append("CI workflow should bootstrap uv in package-manager/release validation paths")

    # Supply-chain: every `pip install uv` must pin an exact version. An unpinned install pulls a
    # moving "latest" uv into release-sensitive CI jobs (audit MEDIUM). The lookahead allows
    # `uv==<version>` and unrelated packages like `uvloop` while rejecting a bare `uv`.
    if re.search(r"pip install uv(?![=\w])", ci_workflow):
        errors.append(
            "CI workflow must pin uv (`pip install uv==<version>`); unpinned `pip install uv` "
            "is not allowed in release-sensitive jobs"
        )

    if "--pypi-wait-seconds" not in ci_workflow:
        errors.append("CI workflow must pass --pypi-wait-seconds to release parity validation")

    if "--pypi-poll-interval-seconds" not in ci_workflow:
        errors.append(
            "CI workflow must pass --pypi-poll-interval-seconds to release parity validation"
        )

    if "needs: [release, publish-pypi, publish-github-release-assets]" not in ci_workflow:
        errors.append(
            "CI workflow publish-success-gate must depend on release + publish-pypi + publish-github-release-assets"
        )

    if "if: always()" not in ci_workflow:
        errors.append("CI workflow publish-success-gate must run with if: always()")

    if "if: needs.release.outputs.released != 'true'" not in ci_workflow:
        errors.append(
            "CI workflow publish-success-gate must explicitly handle semantic-release no-release output"
        )

    if "if: needs.release.outputs.released == 'true'" not in ci_workflow:
        errors.append(
            "CI workflow publish-success-gate must guard checkout/parity steps behind semantic-release `released == 'true'`"
        )

    try:
        parsed_ci = yaml.safe_load(ci_workflow) or {}
    except yaml.YAMLError:
        parsed_ci = {}
    if isinstance(parsed_ci, dict):
        # Push-race hardening: a concurrency group keyed only on `github.ref` puts the weekly
        # `schedule` trigger (which also runs against refs/heads/main) in the SAME group as a
        # merge-triggered push run. A queued schedule run can then sit ahead of (or behind) the
        # release-carrying push run and widen the known publish push-race window. The group must
        # key schedule runs into their own bucket.
        concurrency_block = parsed_ci.get("concurrency")
        if not isinstance(concurrency_block, dict):
            errors.append("CI workflow must define a top-level `concurrency` block")
        else:
            concurrency_group = str(concurrency_block.get("group", ""))
            if "github.event_name" not in concurrency_group or "schedule" not in concurrency_group:
                errors.append(
                    "CI workflow concurrency `group` must key the `schedule` trigger into its "
                    "own group (e.g. via `github.event_name == 'schedule'`) so a weekly cron run "
                    "cannot queue behind or block a merge-triggered release sharing the "
                    "push-to-main concurrency group"
                )

        jobs = parsed_ci.get("jobs")
        if isinstance(jobs, dict):
            static_analysis_job = jobs.get("static-analysis")
            if isinstance(static_analysis_job, dict):
                static_analysis_steps = static_analysis_job.get("steps", [])
                registration_step = None
                if isinstance(static_analysis_steps, list):
                    for step in static_analysis_steps:
                        if (
                            isinstance(step, dict)
                            and step.get("name") == "Registration completeness"
                        ):
                            registration_step = step
                            break
                if registration_step is None:
                    errors.append(
                        "CI workflow static-analysis job must include step "
                        "`Registration completeness`"
                    )
                elif registration_step.get("continue-on-error") is True:
                    errors.append(
                        "CI workflow static-analysis `Registration completeness` step must not "
                        "set `continue-on-error: true` (softens the --rank-class front-door "
                        "registration gate back to warn-only)"
                    )

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
                for required_gate in RELEASE_JOB_REQUIRED_GATES:
                    if required_gate not in needs_list:
                        errors.append(f"CI workflow release job must depend on {required_gate}")
                release_outputs = release_job.get("outputs", {})
                if not isinstance(release_outputs, dict):
                    release_outputs = {}
                if release_outputs.get("released") != "${{ steps.publish_check.outputs.released }}":
                    errors.append(
                        "CI workflow release job must expose semantic-release `released` output"
                    )
                if release_outputs.get("release_version") != (
                    "${{ steps.publish_check.outputs.release_version }}"
                ):
                    errors.append(
                        "CI workflow release job must expose the semantic-release version only "
                        "through the publish_check step"
                    )
                release_steps = release_job.get("steps", [])
                release_step_by_name: dict[str, dict[str, object]] = {}
                if isinstance(release_steps, list):
                    for step in release_steps:
                        if not isinstance(step, dict):
                            continue
                        name = step.get("name")
                        if isinstance(name, str):
                            release_step_by_name[name] = step
                publish_check_step = release_step_by_name.get("Determine PyPI Publish Need")
                if publish_check_step is None:
                    errors.append(
                        "CI workflow release job must include step `Determine PyPI Publish Need`"
                    )
                else:
                    publish_check_env = publish_check_step.get("env", {})
                    if not isinstance(publish_check_env, dict):
                        publish_check_env = {}
                    if publish_check_env.get("SEMANTIC_RELEASED") != (
                        "${{ steps.release.outputs.released }}"
                    ):
                        errors.append(
                            "CI workflow Determine PyPI Publish Need step must read "
                            "`steps.release.outputs.released`"
                        )
                    publish_check_run = str(publish_check_step.get("run", ""))
                    for required in (
                        "SEMANTIC_RELEASED",
                        "released = os.environ.get",
                        "if released:",
                        'version = ""',
                        'f.write(f"released=',
                    ):
                        if required not in publish_check_run:
                            errors.append(
                                "CI workflow Determine PyPI Publish Need step must keep "
                                f"release outputs empty unless semantic-release published: `{required}`"
                            )

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

            for job_name in ("build-wheels-pypi", "build-sdist-pypi"):
                pypi_build_job = jobs.get(job_name)
                if not isinstance(pypi_build_job, dict):
                    errors.append(f"CI workflow must define `{job_name}` for PyPI artifacts")
                    continue
                pypi_steps = pypi_build_job.get("steps", [])
                pypi_step_by_name: dict[str, dict[str, object]] = {}
                maturin_steps: list[dict[str, object]] = []
                if isinstance(pypi_steps, list):
                    for step in pypi_steps:
                        if not isinstance(step, dict):
                            continue
                        name = step.get("name")
                        if isinstance(name, str):
                            pypi_step_by_name[name] = step
                        if step.get("uses") == "PyO3/maturin-action@v1":
                            maturin_steps.append(step)
                prefetch_step = pypi_step_by_name.get(
                    "Prefetch Rust dependencies for PyPI artifacts"
                )
                if prefetch_step is None:
                    errors.append(
                        f"CI workflow {job_name} job must prefetch Rust dependencies with retry before maturin"
                    )
                else:
                    prefetch_env = prefetch_step.get("env", {})
                    if not isinstance(prefetch_env, dict):
                        prefetch_env = {}
                    if str(prefetch_env.get("CARGO_NET_RETRY")) != "10":
                        errors.append(
                            f'CI workflow {job_name} prefetch step must set `CARGO_NET_RETRY: "10"`'
                        )
                    if str(prefetch_env.get("CARGO_HTTP_TIMEOUT")) != "60":
                        errors.append(
                            f'CI workflow {job_name} prefetch step must set `CARGO_HTTP_TIMEOUT: "60"`'
                        )
                    prefetch_run = str(prefetch_step.get("run", ""))
                    for required in (
                        "for attempt in 1 2 3 4 5",
                        "cargo fetch --manifest-path rust_core/Cargo.toml",
                        "Cargo dependency prefetch failed after 5 attempts.",
                    ):
                        if required not in prefetch_run:
                            errors.append(
                                f"CI workflow {job_name} prefetch step missing retry contract: `{required}`"
                            )
                if not maturin_steps:
                    errors.append(f"CI workflow {job_name} job must run PyO3/maturin-action@v1")
                for maturin_step in maturin_steps:
                    maturin_env = maturin_step.get("env", {})
                    if not isinstance(maturin_env, dict):
                        maturin_env = {}
                    if str(maturin_env.get("CARGO_NET_RETRY")) != "10":
                        errors.append(
                            f'CI workflow {job_name} maturin step must set `CARGO_NET_RETRY: "10"`'
                        )
                    if str(maturin_env.get("CARGO_HTTP_TIMEOUT")) != "60":
                        errors.append(
                            f'CI workflow {job_name} maturin step must set `CARGO_HTTP_TIMEOUT: "60"`'
                        )

            benchmark_job = jobs.get("benchmark-regression")
            if not isinstance(benchmark_job, dict):
                errors.append(
                    "CI workflow must define benchmark-regression job when release depends on it"
                )
            else:
                benchmark_steps = benchmark_job.get("steps", [])
                benchmark_run_by_name: dict[str, str] = {}
                benchmark_step_names: set[str] = set()
                benchmark_step_by_name: dict[str, dict[str, object]] = {}
                if isinstance(benchmark_steps, list):
                    for step in benchmark_steps:
                        if not isinstance(step, dict):
                            continue
                        name = step.get("name")
                        if isinstance(name, str):
                            benchmark_step_names.add(name)
                            benchmark_step_by_name[name] = step
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
                required_benchmark_step_names = {
                    "Build benchmark native binary",
                    "Build base benchmark native binary",
                    "Run hot-query benchmark suite",
                    "Determine benchmark base revision",
                    "Checkout base revision for same-runner benchmark comparison",
                    "Install base benchmark dependencies",
                    "Run base benchmark suite",
                    "Report accepted benchmark baseline drift",
                }
                for step_name in required_benchmark_step_names:
                    if step_name not in benchmark_step_names:
                        errors.append(
                            f"CI workflow benchmark-regression job must include step `{step_name}`"
                        )
                base_revision_run = benchmark_run_by_name.get("Determine benchmark base revision")
                if base_revision_run is not None:
                    for expected in ("github.event.pull_request.base.sha", "github.event.before"):
                        if expected not in base_revision_run:
                            errors.append(
                                "CI workflow benchmark-regression "
                                "`Determine benchmark base revision` step must inspect "
                                f"`{expected}`"
                            )

                checkout_base_step = benchmark_step_by_name.get(
                    "Checkout base revision for same-runner benchmark comparison"
                )
                if checkout_base_step is not None:
                    if checkout_base_step.get("uses") != "actions/checkout@v6":
                        errors.append(
                            "CI workflow benchmark-regression "
                            "`Checkout base revision for same-runner benchmark comparison` "
                            "step must use `actions/checkout@v6`"
                        )
                    with_block = checkout_base_step.get("with", {})
                    if not isinstance(with_block, dict):
                        with_block = {}
                    if str(with_block.get("path")) != "base-revision":
                        errors.append(
                            "CI workflow benchmark-regression "
                            "`Checkout base revision for same-runner benchmark comparison` "
                            "step must set `path: base-revision`"
                        )
                    if str(with_block.get("ref")) != "${{ steps.benchmark-base.outputs.base_sha }}":
                        errors.append(
                            "CI workflow benchmark-regression "
                            "`Checkout base revision for same-runner benchmark comparison` "
                            "step must checkout `${{ steps.benchmark-base.outputs.base_sha }}`"
                        )

                install_base_benchmark_run = benchmark_run_by_name.get(
                    "Install base benchmark dependencies"
                )
                if install_base_benchmark_run is not None:
                    if '".[bench,dev]"' not in install_base_benchmark_run:
                        errors.append(
                            "CI workflow benchmark-regression "
                            "`Install base benchmark dependencies` step must install `.[bench,dev]`"
                        )
                    if "base-revision" not in install_base_benchmark_run:
                        errors.append(
                            "CI workflow benchmark-regression "
                            "`Install base benchmark dependencies` step must operate inside `base-revision`"
                        )

                build_benchmark_step = benchmark_step_by_name.get("Build benchmark native binary")
                build_benchmark_run = benchmark_run_by_name.get("Build benchmark native binary")
                if build_benchmark_run is not None:
                    if "cargo build --release --no-default-features" not in build_benchmark_run:
                        errors.append(
                            "CI workflow benchmark-regression "
                            "`Build benchmark native binary` step must run "
                            "`cargo build --release --no-default-features`"
                        )
                    if isinstance(build_benchmark_step, dict):
                        if str(build_benchmark_step.get("working-directory")) != "rust_core":
                            errors.append(
                                "CI workflow benchmark-regression "
                                "`Build benchmark native binary` step must set "
                                "`working-directory: rust_core`"
                            )

                build_base_benchmark_step = benchmark_step_by_name.get(
                    "Build base benchmark native binary"
                )
                build_base_benchmark_run = benchmark_run_by_name.get(
                    "Build base benchmark native binary"
                )
                if build_base_benchmark_run is not None:
                    if (
                        "cargo build --release --no-default-features"
                        not in build_base_benchmark_run
                    ):
                        errors.append(
                            "CI workflow benchmark-regression "
                            "`Build base benchmark native binary` step must run "
                            "`cargo build --release --no-default-features`"
                        )
                    if isinstance(build_base_benchmark_step, dict):
                        if (
                            str(build_base_benchmark_step.get("working-directory"))
                            != "base-revision/rust_core"
                        ):
                            errors.append(
                                "CI workflow benchmark-regression "
                                "`Build base benchmark native binary` step must set "
                                "`working-directory: base-revision/rust_core`"
                            )

                run_core_benchmark = benchmark_run_by_name.get("Run core benchmark suite")
                if run_core_benchmark is not None:
                    if "benchmarks/run_benchmarks.py" not in run_core_benchmark:
                        errors.append(
                            "CI workflow benchmark-regression "
                            "`Run core benchmark suite` step must invoke `benchmarks/run_benchmarks.py`"
                        )
                    if (
                        "--output artifacts/bench_run_benchmarks.head.json"
                        not in run_core_benchmark
                    ):
                        errors.append(
                            "CI workflow benchmark-regression "
                            "`Run core benchmark suite` step must emit `artifacts/bench_run_benchmarks.head.json`"
                        )

                run_base_benchmark = benchmark_run_by_name.get("Run base benchmark suite")
                if run_base_benchmark is not None:
                    if "benchmarks/run_benchmarks.py" not in run_base_benchmark:
                        errors.append(
                            "CI workflow benchmark-regression "
                            "`Run base benchmark suite` step must invoke `benchmarks/run_benchmarks.py`"
                        )
                    if (
                        "--output artifacts/bench_run_benchmarks.base.json"
                        not in run_base_benchmark
                    ):
                        errors.append(
                            "CI workflow benchmark-regression "
                            "`Run base benchmark suite` step must emit `artifacts/bench_run_benchmarks.base.json`"
                        )

                enforce_benchmark_gate_run = benchmark_run_by_name.get(
                    "Enforce benchmark regression gate"
                )
                if enforce_benchmark_gate_run is None:
                    errors.append(
                        "CI workflow benchmark-regression job must include step "
                        "`Enforce benchmark regression gate`"
                    )
                else:
                    if "benchmarks/check_regression.py" not in enforce_benchmark_gate_run:
                        errors.append(
                            "CI workflow benchmark-regression "
                            "`Enforce benchmark regression gate` step must invoke "
                            "`benchmarks/check_regression.py`"
                        )
                    if (
                        "--baseline base-revision/artifacts/bench_run_benchmarks.base.json"
                        not in enforce_benchmark_gate_run
                    ):
                        errors.append(
                            "CI workflow benchmark-regression "
                            "`Enforce benchmark regression gate` step must compare against "
                            "`base-revision/artifacts/bench_run_benchmarks.base.json`"
                        )
                    if (
                        "--current artifacts/bench_run_benchmarks.head.json"
                        not in enforce_benchmark_gate_run
                    ):
                        errors.append(
                            "CI workflow benchmark-regression "
                            "`Enforce benchmark regression gate` step must compare current artifact "
                            "`artifacts/bench_run_benchmarks.head.json`"
                        )
                    if "--baseline auto" in enforce_benchmark_gate_run:
                        errors.append(
                            "CI workflow benchmark-regression "
                            "`Enforce benchmark regression gate` step must not use `--baseline auto`"
                        )

                drift_report_run = benchmark_run_by_name.get(
                    "Report accepted benchmark baseline drift"
                )
                drift_report_step = benchmark_step_by_name.get(
                    "Report accepted benchmark baseline drift"
                )
                if drift_report_run is not None:
                    if "benchmarks/check_regression.py" not in drift_report_run:
                        errors.append(
                            "CI workflow benchmark-regression "
                            "`Report accepted benchmark baseline drift` step must invoke "
                            "`benchmarks/check_regression.py`"
                        )
                    if "--baseline auto" not in drift_report_run:
                        errors.append(
                            "CI workflow benchmark-regression "
                            "`Report accepted benchmark baseline drift` step must pass `--baseline auto`"
                        )
                    if "--current artifacts/bench_run_benchmarks.head.json" not in drift_report_run:
                        errors.append(
                            "CI workflow benchmark-regression "
                            "`Report accepted benchmark baseline drift` step must compare current artifact "
                            "`artifacts/bench_run_benchmarks.head.json`"
                        )
                if isinstance(drift_report_step, dict):
                    if drift_report_step.get("continue-on-error") is not True:
                        errors.append(
                            "CI workflow benchmark-regression "
                            "`Report accepted benchmark baseline drift` step must set `continue-on-error: true`"
                        )

                summary_run = benchmark_run_by_name.get("Build benchmark markdown summary")
                if summary_run is None:
                    errors.append(
                        "CI workflow benchmark-regression job must include step "
                        "`Build benchmark markdown summary`"
                    )
                else:
                    if "benchmarks/summarize_benchmarks.py" not in summary_run:
                        errors.append(
                            "CI workflow benchmark-regression "
                            "`Build benchmark markdown summary` step must invoke "
                            "`benchmarks/summarize_benchmarks.py`"
                        )
                    if "--baseline auto" not in summary_run:
                        errors.append(
                            "CI workflow benchmark-regression "
                            "`Build benchmark markdown summary` step must pass `--baseline auto`"
                        )
                    if "--current artifacts/bench_run_benchmarks.head.json" not in summary_run:
                        errors.append(
                            "CI workflow benchmark-regression "
                            "`Build benchmark markdown summary` step must summarize "
                            "`artifacts/bench_run_benchmarks.head.json`"
                        )

            publish_pypi_job = jobs.get("publish-pypi")
            if isinstance(publish_pypi_job, dict):
                publish_needs = publish_pypi_job.get("needs", [])
                if isinstance(publish_needs, str):
                    publish_needs_list = [publish_needs]
                elif isinstance(publish_needs, list):
                    publish_needs_list = [str(item) for item in publish_needs]
                else:
                    publish_needs_list = []
                if "publish-github-release-assets" not in publish_needs_list:
                    errors.append(
                        "CI workflow publish-pypi job must depend on publish-github-release-assets "
                        "so PyPI cannot publish before GitHub release assets are verified"
                    )
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

            native_assets_job = jobs.get("build-release-native-assets")
            if not isinstance(native_assets_job, dict):
                errors.append(
                    "CI workflow must define build-release-native-assets job for semantic-release GitHub assets"
                )
            else:
                native_needs = native_assets_job.get("needs", [])
                native_needs_list = (
                    [native_needs]
                    if isinstance(native_needs, str)
                    else [str(item) for item in native_needs]
                    if isinstance(native_needs, list)
                    else []
                )
                if "release" not in native_needs_list:
                    errors.append(
                        "CI workflow build-release-native-assets job must depend on release"
                    )
                if str(native_assets_job.get("if", "")) != (
                    "needs.release.outputs.released == 'true'"
                ):
                    errors.append(
                        "CI workflow build-release-native-assets job must run only when "
                        "semantic-release reports `released == 'true'`"
                    )
                strategy = native_assets_job.get("strategy", {})
                matrix = strategy.get("matrix", {}) if isinstance(strategy, dict) else {}
                matrix_include = matrix.get("include", []) if isinstance(matrix, dict) else []
                matrix_os = matrix.get("os", []) if isinstance(matrix, dict) else []
                if isinstance(matrix_include, list) and matrix_include:
                    matrix_os_list = [
                        str(item.get("os"))
                        for item in matrix_include
                        if isinstance(item, dict) and item.get("os") is not None
                    ]
                    for item in matrix_include:
                        if not isinstance(item, dict):
                            continue
                        os_name = str(item.get("os", "")).lower()
                        acceleration = str(item.get("acceleration", "")).lower()
                        asset_name = str(item.get("asset_name", "")).lower()
                        if "macos" in os_name and (acceleration != "cpu" or "nvidia" in asset_name):
                            errors.append(
                                "CI workflow build-release-native-assets must keep macOS CPU-only"
                            )
                else:
                    matrix_os_list = (
                        [str(matrix_os)]
                        if isinstance(matrix_os, str)
                        else [str(item) for item in matrix_os]
                        if isinstance(matrix_os, list)
                        else []
                    )
                if "macos-15-intel" not in matrix_os_list:
                    errors.append(
                        "CI workflow build-release-native-assets matrix must use an Intel "
                        "macOS runner label (`macos-15-intel`) for `tg-macos-amd64-cpu`"
                    )
                if "matrix.acceleration" not in ci_workflow:
                    errors.append(
                        "CI workflow build-release-native-assets matrix must include selectable CPU/NVIDIA acceleration"
                    )
                if (
                    "vars.TENSOR_GREP_RELEASE_NATIVE_ASSET_PROFILE || 'native-frontdoor'"
                    not in ci_workflow
                ):
                    errors.append(
                        "CI workflow release-native asset profile must default to CPU-only `native-frontdoor`"
                    )
                if "--features cuda" not in ci_workflow:
                    errors.append(
                        "CI workflow build-release-native-assets matrix must build NVIDIA native front doors with `--features cuda`"
                    )
                native_gpu_gate = "matrix.acceleration == 'cpu' || env.RELEASE_NATIVE_ASSET_PROFILE == 'native-frontdoor-gpu'"
                if native_gpu_gate not in ci_workflow:
                    errors.append(
                        "CI workflow build-release-native-assets NVIDIA entries must be gated by selectable "
                        "`RELEASE_NATIVE_ASSET_PROFILE`"
                    )
                native_steps = native_assets_job.get("steps", [])
                native_run_by_name: dict[str, str] = {}
                native_step_by_name: dict[str, dict[str, object]] = {}
                if isinstance(native_steps, list):
                    for step in native_steps:
                        if not isinstance(step, dict):
                            continue
                        name = step.get("name")
                        if isinstance(name, str):
                            native_step_by_name[name] = step
                        run = step.get("run")
                        if isinstance(name, str) and isinstance(run, str):
                            native_run_by_name[name] = run
                checkout_steps = (
                    [
                        step
                        for step in native_steps
                        if isinstance(step, dict) and step.get("uses") == "actions/checkout@v6"
                    ]
                    if isinstance(native_steps, list)
                    else []
                )
                if not checkout_steps:
                    errors.append(
                        "CI workflow build-release-native-assets job must checkout the semantic-release tag"
                    )
                else:
                    with_block = checkout_steps[0].get("with", {})
                    if not isinstance(with_block, dict):
                        with_block = {}
                    if with_block.get("ref") != "v${{ needs.release.outputs.release_version }}":
                        errors.append(
                            "CI workflow build-release-native-assets checkout must use "
                            "`ref: v${{ needs.release.outputs.release_version }}`"
                        )
                build_run = native_run_by_name.get("Build native release front door")
                if build_run is None:
                    errors.append(
                        "CI workflow build-release-native-assets job must include "
                        "step `Build native release front door`"
                    )
                elif "cargo build --release ${{ matrix.cargo_args }}" not in build_run:
                    errors.append(
                        "CI workflow build-release-native-assets "
                        "`Build native release front door` step must invoke "
                        "`cargo build --release ${{ matrix.cargo_args }}`"
                    )
                package_run = native_run_by_name.get("Package native release front door")
                if package_run is None:
                    errors.append(
                        "CI workflow build-release-native-assets job must include "
                        "step `Package native release front door`"
                    )
                else:
                    for expected_asset in (
                        "tg-linux-amd64-cpu",
                        "tg-linux-amd64-nvidia",
                        "tg-macos-amd64-cpu",
                        "tg-windows-amd64-cpu.exe",
                        "tg-windows-amd64-nvidia.exe",
                    ):
                        if expected_asset not in ci_workflow:
                            errors.append(
                                "CI workflow build-release-native-assets "
                                f"`Package native release front door` step must package `{expected_asset}`"
                            )
                upload_step = native_step_by_name.get("Upload native release front door")
                if upload_step is None:
                    errors.append(
                        "CI workflow build-release-native-assets job must include "
                        "step `Upload native release front door`"
                    )
                elif upload_step.get("uses") != "actions/upload-artifact@v7":
                    errors.append(
                        "CI workflow build-release-native-assets "
                        "`Upload native release front door` step must use `actions/upload-artifact@v7`"
                    )
                joined_native_runs = "\n".join(native_run_by_name.values())
                if (
                    "scripts/build_binaries.py" in joined_native_runs
                    or "nuitka" in joined_native_runs.lower()
                ):
                    errors.append(
                        "CI workflow build-release-native-assets must use Rust native front doors, not the old Nuitka builder"
                    )

            native_smoke_job = jobs.get("native-build-smoke")
            if isinstance(native_smoke_job, dict):
                smoke_strategy = native_smoke_job.get("strategy", {})
                smoke_matrix = (
                    smoke_strategy.get("matrix", {}) if isinstance(smoke_strategy, dict) else {}
                )
                smoke_matrix_os = (
                    smoke_matrix.get("os", []) if isinstance(smoke_matrix, dict) else []
                )
                smoke_matrix_os_list = (
                    [str(smoke_matrix_os)]
                    if isinstance(smoke_matrix_os, str)
                    else [str(item) for item in smoke_matrix_os]
                    if isinstance(smoke_matrix_os, list)
                    else []
                )
                if "macos-15-intel" not in smoke_matrix_os_list:
                    errors.append(
                        "CI workflow native-build-smoke matrix must include Intel macOS runner "
                        "label (`macos-15-intel`) so PR smoke matches `tg-macos-amd64-cpu`"
                    )

            github_assets_job = jobs.get("publish-github-release-assets")
            if not isinstance(github_assets_job, dict):
                errors.append(
                    "CI workflow must define publish-github-release-assets job for semantic-release GitHub assets"
                )
            else:
                if str(github_assets_job.get("if", "")) != (
                    "needs.release.outputs.released == 'true'"
                ):
                    errors.append(
                        "CI workflow publish-github-release-assets job must run only when "
                        "semantic-release reports `released == 'true'`"
                    )
                github_needs = github_assets_job.get("needs", [])
                github_needs_list = (
                    [github_needs]
                    if isinstance(github_needs, str)
                    else [str(item) for item in github_needs]
                    if isinstance(github_needs, list)
                    else []
                )
                for required_need in ("release", "build-release-native-assets"):
                    if required_need not in github_needs_list:
                        errors.append(
                            "CI workflow publish-github-release-assets job must depend on "
                            f"{required_need}"
                        )
                permissions = github_assets_job.get("permissions", {})
                if not isinstance(permissions, dict) or permissions.get("contents") != "write":
                    errors.append(
                        "CI workflow publish-github-release-assets job must request `contents: write`"
                    )
                github_steps = github_assets_job.get("steps", [])
                github_run_by_name: dict[str, str] = {}
                github_step_by_name: dict[str, dict[str, object]] = {}
                if isinstance(github_steps, list):
                    for step in github_steps:
                        if not isinstance(step, dict):
                            continue
                        name = step.get("name")
                        if isinstance(name, str):
                            github_step_by_name[name] = step
                        run = step.get("run")
                        if isinstance(name, str) and isinstance(run, str):
                            github_run_by_name[name] = run
                checkout_steps = (
                    [
                        step
                        for step in github_steps
                        if isinstance(step, dict) and step.get("uses") == "actions/checkout@v6"
                    ]
                    if isinstance(github_steps, list)
                    else []
                )
                if checkout_steps:
                    with_block = checkout_steps[0].get("with", {})
                    if not isinstance(with_block, dict):
                        with_block = {}
                    if with_block.get("ref") != "v${{ needs.release.outputs.release_version }}":
                        errors.append(
                            "CI workflow publish-github-release-assets checkout must use "
                            "`ref: v${{ needs.release.outputs.release_version }}`"
                        )
                else:
                    errors.append(
                        "CI workflow publish-github-release-assets job must checkout the semantic-release tag"
                    )
                download_step = github_step_by_name.get("Download native release front doors")
                if download_step is None:
                    errors.append(
                        "CI workflow publish-github-release-assets job must include "
                        "step `Download native release front doors`"
                    )
                elif download_step.get("uses") != "actions/download-artifact@v8":
                    errors.append(
                        "CI workflow publish-github-release-assets "
                        "`Download native release front doors` step must use `actions/download-artifact@v8`"
                    )
                else:
                    with_block = download_step.get("with", {})
                    if (
                        not isinstance(with_block, dict)
                        or with_block.get("pattern") != "release-native-*"
                    ):
                        errors.append(
                            "CI workflow publish-github-release-assets "
                            "`Download native release front doors` step must download `release-native-*` artifacts"
                        )
                validate_native_run = github_run_by_name.get(
                    "Validate native release asset matrix and generate checksums"
                )
                if validate_native_run is None:
                    errors.append(
                        "CI workflow publish-github-release-assets job must include "
                        "step `Validate native release asset matrix and generate checksums`"
                    )
                else:
                    if (
                        '--expected-profile "$RELEASE_NATIVE_ASSET_PROFILE"'
                        not in validate_native_run
                    ):
                        errors.append(
                            "CI workflow publish-github-release-assets native asset validation and verification "
                            "steps must use selectable `$RELEASE_NATIVE_ASSET_PROFILE`"
                        )
                    for required in (
                        "scripts/validate_release_binary_artifacts.py",
                        '--expected-profile "$RELEASE_NATIVE_ASSET_PROFILE"',
                        "--checksums-out artifacts/CHECKSUMS.txt",
                    ):
                        if required not in validate_native_run:
                            errors.append(
                                "CI workflow publish-github-release-assets "
                                "`Validate native release asset matrix and generate checksums` "
                                f"step must include `{required}`"
                            )
                upload_step = github_step_by_name.get("Upload GitHub release native assets")
                if upload_step is None:
                    errors.append(
                        "CI workflow publish-github-release-assets job must include "
                        "step `Upload GitHub release native assets`"
                    )
                elif upload_step.get("uses") != "softprops/action-gh-release@v3":
                    errors.append(
                        "CI workflow publish-github-release-assets "
                        "`Upload GitHub release native assets` step must use `softprops/action-gh-release@v3`"
                    )
                else:
                    with_block = upload_step.get("with", {})
                    if not isinstance(with_block, dict):
                        with_block = {}
                    if (
                        with_block.get("tag_name")
                        != "v${{ needs.release.outputs.release_version }}"
                    ):
                        errors.append(
                            "CI workflow publish-github-release-assets "
                            "`Upload GitHub release native assets` step must upload to "
                            "`v${{ needs.release.outputs.release_version }}`"
                        )
                    files = str(with_block.get("files", ""))
                    for required_file in (
                        "artifacts/native/**/tg-*",
                        "artifacts/CHECKSUMS.txt",
                        "artifacts/package-manager-bundle/homebrew-tap/Formula/tensor-grep.rb",
                        "artifacts/package-manager-bundle/PUBLISH_INSTRUCTIONS.md",
                        "artifacts/package-manager-bundle/BUNDLE_CHECKSUMS.txt",
                    ):
                        if required_file not in files:
                            errors.append(
                                "CI workflow publish-github-release-assets "
                                "`Upload GitHub release native assets` step must upload "
                                f"`{required_file}`"
                            )
                verify_run = github_run_by_name.get("Verify GitHub release native asset coverage")
                if verify_run is None:
                    errors.append(
                        "CI workflow publish-github-release-assets job must include "
                        "step `Verify GitHub release native asset coverage`"
                    )
                else:
                    if '--expected-profile "$RELEASE_NATIVE_ASSET_PROFILE"' not in verify_run:
                        errors.append(
                            "CI workflow publish-github-release-assets native asset validation and verification "
                            "steps must use selectable `$RELEASE_NATIVE_ASSET_PROFILE`"
                        )
                    for required in (
                        "scripts/verify_github_release_assets.py",
                        '--expected-profile "$RELEASE_NATIVE_ASSET_PROFILE"',
                        "--wait-seconds",
                        "--poll-interval-seconds",
                    ):
                        if required not in verify_run:
                            errors.append(
                                "CI workflow publish-github-release-assets "
                                "`Verify GitHub release native asset coverage` "
                                f"step must include `{required}`"
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
                asset_result_step = (
                    "Confirm GitHub release asset job result when publishing is required"
                )
                if asset_result_step not in gate_step_names:
                    errors.append(
                        "CI workflow publish-success-gate job must include step "
                        f"`{asset_result_step}`"
                    )
                asset_verify_step = (
                    "Verify GitHub release native assets for semantic-release version"
                )
                if asset_verify_step not in gate_step_names:
                    errors.append(
                        "CI workflow publish-success-gate job must include step "
                        f"`{asset_verify_step}`"
                    )
                asset_verify_run = gate_run_by_name.get(asset_verify_step)
                if asset_verify_run is not None:
                    if '--expected-profile "$RELEASE_NATIVE_ASSET_PROFILE"' not in asset_verify_run:
                        errors.append(
                            "CI workflow publish-github-release-assets native asset validation and verification "
                            "steps must use selectable `$RELEASE_NATIVE_ASSET_PROFILE`"
                        )
                    for required in (
                        "scripts/verify_github_release_assets.py",
                        '--expected-profile "$RELEASE_NATIVE_ASSET_PROFILE"',
                        "--wait-seconds",
                        "--poll-interval-seconds",
                    ):
                        if required not in asset_verify_run:
                            errors.append(
                                "CI workflow publish-success-gate "
                                f"`{asset_verify_step}` step must include `{required}`"
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
                errors.append(f"CI workflow must use {action}@{expected_version}, found @{version}")

    return errors
