"""release.yml content contract -- the second-largest release-asset check."""

from __future__ import annotations

import yaml

from .helpers import _normalize_pinned_actions


def validate_release_workflow_content(*, release_workflow: str) -> list[str]:
    release_workflow = _normalize_pinned_actions(release_workflow)
    errors: list[str] = []
    for expected in (
        "on:",
        # release.yml is workflow_dispatch-only (NOT tag-push) so a manually-pushed v* tag
        # cannot bypass semantic-release and auto-publish (audit HIGH, 2026-06-29). Backfill is
        # via `gh workflow run release.yml --ref vX.Y.Z`; the body still uses ${GITHUB_REF#refs/tags/}.
        "workflow_dispatch:",
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
                "uv pip install torch torchvision torchaudio --index-url https://download.pytorch.org/whl/cu128",
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
            if uses_value != "softprops/action-gh-release@v3":
                errors.append(
                    "Release workflow create-release `Create GitHub Release` step must use `softprops/action-gh-release@v3`"
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
