"""Secondary workflow/config content checks: SHA-pinning, Dependabot, GPU-proof,
Dependabot-automation, and the security audit workflow."""

from __future__ import annotations

import re
from typing import Any

import yaml

from .constants import ROOT
from .helpers import _normalize_pinned_actions


def validate_actions_sha_pinned() -> list[str]:
    """Every third-party GitHub Action ``uses:`` must be pinned to a full 40-hex commit SHA
    (supply-chain hardening — tags/branches are mutable). Exempt: ``dtolnay/rust-toolchain``
    (``@stable`` is a moving ref by design that resolves to the latest Rust toolchain) and local
    reusable workflows (``./...``). The ``# vX`` version comment keeps Dependabot updating the pins.
    """
    errors: list[str] = []
    exempt_prefixes = ("dtolnay/rust-toolchain",)
    workflows_dir = ROOT / ".github" / "workflows"
    for workflow in sorted(workflows_dir.glob("*.yml")):
        content = workflow.read_text(encoding="utf-8")
        for match in re.finditer(r"uses:\s*([^\s@]+)@([^\s#]+)", content):
            action, ref = match.group(1), match.group(2)
            if action.startswith("./") or any(action.startswith(p) for p in exempt_prefixes):
                continue
            if not re.fullmatch(r"[0-9a-f]{40}", ref):
                errors.append(
                    f"{workflow.name}: third-party action `{action}@{ref}` must be pinned to a "
                    "full 40-hex commit SHA (add `# vX` so Dependabot keeps it updated)"
                )
    return errors


def validate_dependabot_config(*, dependabot_content: str) -> list[str]:
    errors: list[str] = []
    try:
        parsed = yaml.safe_load(dependabot_content) or {}
    except yaml.YAMLError as exc:
        return [f"Dependabot config is not valid YAML: {exc}"]

    if not isinstance(parsed, dict):
        return ["Dependabot config must deserialize to a mapping"]

    if parsed.get("version") != 2:
        errors.append("Dependabot config must set `version: 2`")

    branch_name = parsed.get("pull-request-branch-name")
    if not isinstance(branch_name, dict) or branch_name.get("separator") != "-":
        errors.append("Dependabot config must set pull-request-branch-name.separator to `-`")

    updates = parsed.get("updates")
    if not isinstance(updates, list) or not updates:
        return [*errors, "Dependabot config must define a non-empty `updates` list"]

    required_targets = {
        ("github-actions", "/"): {"labels": {"github-actions"}},
        ("uv", "/"): {"labels": {"python"}},
        ("cargo", "/rust_core"): {"labels": {"rust"}},
        ("npm", "/npm"): {"labels": {"npm"}},
    }

    seen_targets: set[tuple[str, str]] = set()
    for update in updates:
        if not isinstance(update, dict):
            errors.append("Dependabot config `updates` entries must be mappings")
            continue
        ecosystem = str(update.get("package-ecosystem") or "")
        directory = str(update.get("directory") or "")
        seen_targets.add((ecosystem, directory))

        schedule = update.get("schedule")
        if not isinstance(schedule, dict) or schedule.get("interval") != "weekly":
            errors.append(
                f"Dependabot config `{ecosystem}` `{directory}` update must schedule weekly checks"
            )

        limit = update.get("open-pull-requests-limit")
        if not isinstance(limit, int) or limit <= 0:
            errors.append(
                f"Dependabot config `{ecosystem}` `{directory}` update must set a positive open-pull-requests-limit"
            )

        labels = update.get("labels")
        if (
            not isinstance(labels, list)
            or "dependencies" not in labels
            or "supply-chain" not in labels
        ):
            errors.append(
                f"Dependabot config `{ecosystem}` `{directory}` update must include `dependencies` and `supply-chain` labels"
            )

        commit_message = update.get("commit-message")
        if not isinstance(commit_message, dict) or not str(commit_message.get("prefix") or ""):
            errors.append(
                f"Dependabot config `{ecosystem}` `{directory}` update must set a commit-message prefix"
            )

        groups = update.get("groups")
        if not isinstance(groups, dict) or not groups:
            errors.append(
                f"Dependabot config `{ecosystem}` `{directory}` update must define grouped update rules"
            )

    for target, _contract in required_targets.items():
        if target not in seen_targets:
            ecosystem, directory = target
            errors.append(
                f"Dependabot config missing required update target `{ecosystem}` in `{directory}`"
            )

    return errors


def validate_public_gpu_proof_workflow_content(*, workflow_content: str) -> list[str]:
    workflow_content = _normalize_pinned_actions(workflow_content)
    errors: list[str] = []

    # Supply-chain: pin uv like the main CI bootstrap (audit MEDIUM). The lookahead allows
    # `uv==<version>` and unrelated packages (uvloop) while rejecting a bare `uv`.
    if re.search(r"pip install uv(?![=\w])", workflow_content):
        errors.append(
            "Public GPU proof workflow must pin uv (`pip install uv==<version>`); unpinned uv "
            "is not allowed"
        )

    try:
        parsed = yaml.safe_load(workflow_content) or {}
    except yaml.YAMLError as exc:
        return [f"Public GPU proof workflow is not valid YAML: {exc}"]
    if not isinstance(parsed, dict):
        return ["Public GPU proof workflow must deserialize to a mapping"]

    triggers = parsed.get("on")
    if triggers is None:
        triggers = parsed.get(True)
    if not isinstance(triggers, dict) or set(triggers) != {"workflow_dispatch"}:
        errors.append("Public GPU proof workflow must be workflow_dispatch-only")
    permissions = parsed.get("permissions")
    if not isinstance(permissions, dict) or permissions.get("contents") != "read":
        errors.append("Public GPU proof workflow must request read-only contents permission")
    elif any(value == "write" for value in permissions.values()):
        errors.append("Public GPU proof workflow must not request write permissions")

    jobs = parsed.get("jobs")
    job = jobs.get("public-managed-gpu-proof") if isinstance(jobs, dict) else None
    if not isinstance(job, dict):
        errors.append("Public GPU proof workflow must define public-managed-gpu-proof job")
        return errors

    if job.get("environment") != "public-gpu-proof":
        errors.append(
            "Public GPU proof workflow must target `environment: public-gpu-proof` for reviewer gating"
        )

    runs_on = job.get("runs-on")
    if not isinstance(runs_on, list) or any("${{" in str(item) for item in runs_on):
        errors.append("Public GPU proof workflow must use fixed GPU runner labels")
    else:
        required_labels = {"self-hosted", "linux", "x64", "gpu", "tensor-grep-public-gpu-proof"}
        observed_labels = {str(item) for item in runs_on}
        if not required_labels.issubset(observed_labels):
            errors.append("Public GPU proof workflow must use fixed GPU runner labels")

    job_env = job.get("env", {})
    if (
        not isinstance(job_env, dict)
        or job_env.get("TENSOR_GREP_NATIVE_FRONTDOOR_FLAVOR") != "nvidia"
    ):
        errors.append("Public GPU proof workflow must request the managed NVIDIA native front door")

    steps = job.get("steps", [])
    if not isinstance(steps, list):
        errors.append("Public GPU proof workflow steps must be a list")
        steps = []
    joined_runs = "\n".join(
        str(step.get("run"))
        for step in steps
        if isinstance(step, dict) and isinstance(step.get("run"), str)
    )
    joined_uses = "\n".join(
        str(step.get("uses"))
        for step in steps
        if isinstance(step, dict) and isinstance(step.get("uses"), str)
    )
    if "scripts/verify_github_release_assets.py" not in joined_runs:
        errors.append("Public GPU proof workflow must verify GitHub release native assets")
    if "--expected-profile native-frontdoor-gpu" not in joined_runs:
        errors.append(
            "Public GPU proof workflow must verify the native-frontdoor-gpu asset profile"
        )
    if 'TENSOR_GREP_VERSION="$TG_PUBLIC_GPU_PROOF_RELEASE_VERSION"' not in joined_runs:
        errors.append("Public GPU proof workflow must install the requested release version")
    if "--public-managed-proof" not in joined_runs:
        errors.append("Public GPU proof workflow must run with --public-managed-proof")
    if "--corpus-sizes 1GB,5GB" not in joined_runs:
        errors.append("Public GPU proof workflow must require 1GB and 5GB proof corpora")
    if "nvidia-smi" not in joined_runs:
        errors.append("Public GPU proof workflow must collect nvidia-smi device provenance")
    if "v[0-9]+\\.[0-9]+\\.[0-9]+" not in joined_runs:
        errors.append("Public GPU proof workflow must validate release_tag format")
    if "actions/upload-artifact@v7" not in joined_uses:
        errors.append("Public GPU proof workflow must upload proof artifacts")
    return errors


def validate_dependabot_automation_workflow_content(*, workflow_content: str) -> list[str]:
    workflow_content = _normalize_pinned_actions(workflow_content)
    errors: list[str] = []
    for expected in (
        "name: Dependabot Automation",
        "pull_request_target:",
        "github.actor == 'dependabot[bot]'",
        "dependabot/fetch-metadata@d7267f607e9d3fb96fc2fbe83e0af444713e90b7",
        "gh label create dependencies",
        'gh pr edit "$PR_URL"',
        'gh pr review "$PR_URL"',
        "gh pr merge",
        '--repo "$GH_REPO"',
        "automerge:eligible",
        "manual-review",
    ):
        if expected not in workflow_content:
            errors.append(f"Dependabot automation workflow missing expected contract: {expected}")

    try:
        parsed = yaml.safe_load(workflow_content) or {}
    except yaml.YAMLError as exc:
        return [*errors, f"Dependabot automation workflow is not valid YAML: {exc}"]

    if not isinstance(parsed, dict):
        return [*errors, "Dependabot automation workflow must deserialize to a mapping"]

    permissions = parsed.get("permissions")
    if not isinstance(permissions, dict):
        return [*errors, "Dependabot automation workflow must define top-level permissions"]
    for key in ("contents", "pull-requests", "issues"):
        if permissions.get(key) != "write":
            errors.append(f"Dependabot automation workflow must grant `{key}: write` permissions")

    jobs = parsed.get("jobs")
    if not isinstance(jobs, dict):
        return [*errors, "Dependabot automation workflow must define jobs"]
    triage = jobs.get("dependabot-triage")
    if not isinstance(triage, dict):
        return [
            *errors,
            "Dependabot automation workflow must define `dependabot-triage` job",
        ]

    if triage.get("if") != "github.actor == 'dependabot[bot]'":
        errors.append(
            "Dependabot automation workflow `dependabot-triage` job must only run for dependabot[bot]"
        )

    job_env = triage.get("env")
    if not isinstance(job_env, dict) or job_env.get("GH_REPO") != "${{ github.repository }}":
        errors.append(
            "Dependabot automation workflow `dependabot-triage` job must define "
            "`GH_REPO: ${{ github.repository }}`"
        )

    steps = triage.get("steps")
    if not isinstance(steps, list):
        return [
            *errors,
            "Dependabot automation workflow `dependabot-triage` must define steps",
        ]

    steps_by_name: dict[str, dict[str, object]] = {}
    runs_by_name: dict[str, str] = {}
    uses_by_name: dict[str, str] = {}
    for step in steps:
        if not isinstance(step, dict):
            continue
        name = step.get("name")
        if not isinstance(name, str):
            continue
        steps_by_name[name] = step
        run = step.get("run")
        uses = step.get("uses")
        if isinstance(run, str):
            runs_by_name[name] = run
        if isinstance(uses, str):
            uses_by_name[name] = uses

    required_steps = {
        "Fetch Dependabot metadata": "dependabot/fetch-metadata@d7267f607e9d3fb96fc2fbe83e0af444713e90b7",
        "Ensure dependency labels exist": None,
        "Apply dependency labels": None,
        "Determine automerge policy": None,
        "Mark safe updates": None,
        "Mark manual review updates": None,
        "Approve safe updates": None,
        "Enable auto-merge for safe updates": None,
    }
    for name, required_use in required_steps.items():
        step = steps_by_name.get(name)
        if step is None:
            errors.append(f"Dependabot automation workflow must include step `{name}`")
            continue
        if required_use is not None and uses_by_name.get(name) != required_use:
            errors.append(f"Dependabot automation workflow `{name}` step must use `{required_use}`")

    enable_merge_run = runs_by_name.get("Enable auto-merge for safe updates")
    if (
        enable_merge_run is None
        or "gh pr merge" not in enable_merge_run
        or '--repo "$GH_REPO"' not in enable_merge_run
        or '--auto --squash "$PR_URL"' not in enable_merge_run
    ):
        errors.append(
            "Dependabot automation workflow `Enable auto-merge for safe updates` must invoke "
            '`gh pr merge --repo "$GH_REPO" --auto --squash "$PR_URL"`'
        )

    approve_run = runs_by_name.get("Approve safe updates")
    if (
        approve_run is None
        or 'gh pr review "$PR_URL"' not in approve_run
        or '--repo "$GH_REPO"' not in approve_run
        or "--approve" not in approve_run
    ):
        errors.append(
            "Dependabot automation workflow `Approve safe updates` must invoke "
            '`gh pr review "$PR_URL" --repo "$GH_REPO" --approve`'
        )

    for name, run in runs_by_name.items():
        gh_lines = [
            line.strip()
            for line in run.splitlines()
            if "gh " in line and not line.strip().startswith("#")
        ]
        if gh_lines and any('--repo "$GH_REPO"' not in line for line in gh_lines):
            errors.append(
                f"Dependabot automation workflow step `{name}` must pass "
                '`--repo "$GH_REPO"` to every `gh` command'
            )

    return errors


def validate_audit_workflow_content(*, workflow_content: str) -> list[str]:
    workflow_content = _normalize_pinned_actions(workflow_content)
    errors: list[str] = []
    for expected in (
        "name: Security Audit",
        "schedule:",
        "pull_request:",
        "workflow_dispatch:",
        "Dependency & License Audit",
        "cargo audit",
        "cargo deny check",
        "pip-audit",
        "report-audit-status:",
        "if: always() && github.event_name == 'schedule'",
        "Create or update scheduled audit issue on failure",
        "Close scheduled audit issue on success",
        "[Security Audit] Scheduled dependency audit failure",
        "actions/github-script@v8",
    ):
        if expected not in workflow_content:
            errors.append(f"Audit workflow missing expected contract: {expected}")

    try:
        parsed = yaml.safe_load(workflow_content) or {}
    except yaml.YAMLError as exc:
        return [*errors, f"Audit workflow is not valid YAML: {exc}"]

    if not isinstance(parsed, dict):
        return [*errors, "Audit workflow must deserialize to a mapping"]

    jobs = parsed.get("jobs")
    if not isinstance(jobs, dict):
        return [*errors, "Audit workflow must define jobs"]

    audit_job = jobs.get("audit")
    if not isinstance(audit_job, dict):
        errors.append("Audit workflow must define `audit` job")
    else:
        audit_steps = audit_job.get("steps")
        if not isinstance(audit_steps, list):
            errors.append("Audit workflow `audit` job must define steps")
        else:
            audit_steps_by_name: dict[str, dict[str, Any]] = {}
            audit_runs_by_name: dict[str, str] = {}
            audit_step_order: list[str] = []
            for step in audit_steps:
                if not isinstance(step, dict):
                    continue
                uses = step.get("uses")
                if uses == "actions/checkout@v6":
                    with_block = step.get("with")
                    if not isinstance(with_block, dict):
                        errors.append(
                            "Audit workflow checkout step must explicitly set pull request head "
                            "repository/ref to avoid unauthenticated synthetic merge-ref fetches"
                        )
                    else:
                        if (
                            with_block.get("repository")
                            != "${{ github.event_name == 'pull_request' && github.event.pull_request.head.repo.full_name || github.repository }}"
                        ):
                            errors.append(
                                "Audit workflow checkout step must set `repository` to the pull "
                                "request head repository on PR events"
                            )
                        if (
                            with_block.get("ref")
                            != "${{ github.event_name == 'pull_request' && github.event.pull_request.head.sha || github.sha }}"
                        ):
                            errors.append(
                                "Audit workflow checkout step must set `ref` to the pull request "
                                "head SHA on PR events"
                            )
                name = step.get("name")
                if not isinstance(name, str):
                    continue
                audit_step_order.append(name)
                audit_steps_by_name[name] = step
                run = step.get("run")
                if isinstance(run, str):
                    audit_runs_by_name[name] = run

            install_uv_step = audit_steps_by_name.get("Install uv")
            if install_uv_step is None:
                errors.append("Audit workflow `audit` job must include step `Install uv`")
            else:
                uses_value = install_uv_step.get("uses")
                if uses_value != "astral-sh/setup-uv@v8.0.0":
                    errors.append(
                        "Audit workflow `Install uv` step must use `astral-sh/setup-uv@v8.0.0`"
                    )

            setup_python_run = audit_runs_by_name.get("Setup Python")
            if setup_python_run is None:
                errors.append("Audit workflow `audit` job must include step `Setup Python`")
            elif "uv python install 3.12" not in setup_python_run:
                errors.append(
                    "Audit workflow `Setup Python` step must invoke `uv python install 3.12`"
                )

            for step_name, step_run in audit_runs_by_name.items():
                if "uv venv --python" in step_run:
                    errors.append(
                        "Audit workflow must not create a project audit environment; "
                        f"step `{step_name}` must audit exported locked requirements instead"
                    )
                if "uv pip install pip-audit" in step_run:
                    errors.append(
                        "Audit workflow must not install pip-audit into the project environment; "
                        f"step `{step_name}` must use isolated `uv run --no-project --with pip-audit`"
                    )
                if "uv run pip-audit" in step_run:
                    errors.append(
                        "Audit workflow must not run legacy project-environment `uv run pip-audit`; "
                        f"step `{step_name}` must use exported locked requirements"
                    )

            export_requirements_run = audit_runs_by_name.get("Export Python audit requirements")
            if export_requirements_run is None:
                errors.append(
                    "Audit workflow `audit` job must include step "
                    "`Export Python audit requirements` with `uv export --format requirements.txt`"
                )
            else:
                for expected_export_arg in (
                    "uv export --format requirements.txt",
                    "--all-extras",
                    "--no-emit-project",
                    '--output-file "$RUNNER_TEMP/python-audit-requirements.txt"',
                    "--locked",
                ):
                    if expected_export_arg not in export_requirements_run:
                        errors.append(
                            "Audit workflow `Export Python audit requirements` step must include "
                            f"`{expected_export_arg}`"
                        )

            run_pip_audit_run = audit_runs_by_name.get("Run pip-audit")
            if run_pip_audit_run is None:
                errors.append("Audit workflow `audit` job must include step `Run pip-audit`")
            else:
                for expected_audit_arg in (
                    "uv run --no-project",
                    "--python 3.12",
                    "--with pip-audit",
                    "-- pip-audit",
                    "--require-hashes",
                    "--disable-pip",
                    "--progress-spinner off",
                    '-r "$RUNNER_TEMP/python-audit-requirements.txt"',
                ):
                    if expected_audit_arg not in run_pip_audit_run:
                        errors.append(
                            "Audit workflow `Run pip-audit` step must include "
                            f"`{expected_audit_arg}`"
                        )

            required_step_order = [
                "Install uv",
                "Setup Python",
                "Export Python audit requirements",
                "Run pip-audit",
            ]
            if all(step_name in audit_step_order for step_name in required_step_order):
                order_positions = {
                    name: audit_step_order.index(name) for name in required_step_order
                }
                if (
                    order_positions["Setup Python"]
                    > order_positions["Export Python audit requirements"]
                ):
                    errors.append(
                        "Audit workflow `Export Python audit requirements` step must run after "
                        "`Setup Python`"
                    )
                if (
                    order_positions["Export Python audit requirements"]
                    > order_positions["Run pip-audit"]
                ):
                    errors.append(
                        "Audit workflow `Export Python audit requirements` step must run before "
                        "`Run pip-audit`"
                    )

    report_job = jobs.get("report-audit-status")
    if not isinstance(report_job, dict):
        return [*errors, "Audit workflow must define `report-audit-status` job"]

    if report_job.get("if") != "always() && github.event_name == 'schedule'":
        errors.append(
            "Audit workflow `report-audit-status` job must run only for scheduled events with if: always()"
        )

    needs = report_job.get("needs")
    if needs != "audit":
        errors.append("Audit workflow `report-audit-status` job must depend on `audit`")

    permissions = report_job.get("permissions")
    if not isinstance(permissions, dict):
        errors.append("Audit workflow `report-audit-status` job must define permissions")
    else:
        if permissions.get("issues") != "write":
            errors.append(
                "Audit workflow `report-audit-status` job must grant `issues: write` permissions"
            )
        if permissions.get("contents") != "read":
            errors.append(
                "Audit workflow `report-audit-status` job must grant `contents: read` permissions"
            )

    steps = report_job.get("steps")
    if not isinstance(steps, list):
        return [*errors, "Audit workflow `report-audit-status` job must define steps"]

    uses_by_name: dict[str, str] = {}
    scripts_by_name: dict[str, str] = {}
    for step in steps:
        if not isinstance(step, dict):
            continue
        name = step.get("name")
        if not isinstance(name, str):
            continue
        uses = step.get("uses")
        run = step.get("run")
        with_block = step.get("with")
        if isinstance(uses, str):
            uses_by_name[name] = uses
        if isinstance(run, str):
            scripts_by_name[name] = run
        elif isinstance(with_block, dict):
            script = with_block.get("script")
            if isinstance(script, str):
                scripts_by_name[name] = script

    for step_name in (
        "Create or update scheduled audit issue on failure",
        "Close scheduled audit issue on success",
    ):
        if uses_by_name.get(step_name) != "actions/github-script@v8":
            errors.append(f"Audit workflow `{step_name}` step must use `actions/github-script@v8`")

    create_run = scripts_by_name.get("Create or update scheduled audit issue on failure")
    if create_run is not None:
        for expected in (
            'const title = "[Security Audit] Scheduled dependency audit failure";',
            "issues.create({",
            "issues.update({",
            "issues.createComment({",
        ):
            if expected not in create_run:
                errors.append(
                    "Audit workflow `Create or update scheduled audit issue on failure` step "
                    f"must include `{expected}`"
                )

    close_run = scripts_by_name.get("Close scheduled audit issue on success")
    if close_run is not None:
        for expected in (
            'const title = "[Security Audit] Scheduled dependency audit failure";',
            'state: "closed"',
            "issues.createComment({",
        ):
            if expected not in close_run:
                errors.append(
                    "Audit workflow `Close scheduled audit issue on success` step "
                    f"must include `{expected}`"
                )

    return errors
