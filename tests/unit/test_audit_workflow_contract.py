from __future__ import annotations

import re
import tomllib
from pathlib import Path


def test_audit_workflow_requires_repo_owned_cargo_deny_policy() -> None:
    repo_root = Path(__file__).resolve().parents[2]
    workflow = (repo_root / ".github" / "workflows" / "audit.yml").read_text(encoding="utf-8")

    assert "working-directory: rust_core" in workflow
    assert "cargo deny check" in workflow
    assert (repo_root / "rust_core" / "deny.toml").exists()


def test_audit_workflow_checks_out_pull_request_head_ref() -> None:
    repo_root = Path(__file__).resolve().parents[2]
    workflow = (repo_root / ".github" / "workflows" / "audit.yml").read_text(encoding="utf-8")

    # checkout is SHA-pinned for supply-chain hardening; the `# v6` comment keeps Dependabot updating it.
    assert re.search(r"uses: actions/checkout@[0-9a-f]{40} # v6", workflow)
    assert (
        "repository: ${{ github.event_name == 'pull_request' && "
        "github.event.pull_request.head.repo.full_name || github.repository }}"
    ) in workflow
    assert (
        "ref: ${{ github.event_name == 'pull_request' && "
        "github.event.pull_request.head.sha || github.sha }}"
    ) in workflow


def test_audit_workflow_audits_exported_locked_requirements_with_isolated_tool() -> None:
    repo_root = Path(__file__).resolve().parents[2]
    workflow = (repo_root / ".github" / "workflows" / "audit.yml").read_text(encoding="utf-8")

    setup_python = "uv python install 3.12"
    export_requirements = (
        "uv export --format requirements.txt --all-extras --no-emit-project --output-file "
        '"$RUNNER_TEMP/python-audit-requirements.txt" --locked'
    )
    run_audit = "uv run --no-project --python 3.12 --with pip-audit -- pip-audit"

    assert setup_python in workflow
    assert export_requirements in workflow
    assert run_audit in workflow
    assert "--require-hashes" in workflow
    assert "--disable-pip" in workflow
    assert "--progress-spinner off" in workflow
    assert '-r "$RUNNER_TEMP/python-audit-requirements.txt"' in workflow
    assert "upstream no-fixed-version advisories" in workflow
    for ignored_id in [
        "PYSEC-2025-183",
        "PYSEC-2025-189",
        "PYSEC-2025-210",
        "PYSEC-2025-218",
        "PYSEC-2026-139",
    ]:
        assert f"--ignore-vuln {ignored_id}" in workflow
    assert (
        workflow.index(setup_python)
        < workflow.index(export_requirements)
        < workflow.index(run_audit)
    )


def test_cargo_deny_policy_declares_explicit_license_allowlist() -> None:
    repo_root = Path(__file__).resolve().parents[2]
    policy = tomllib.loads((repo_root / "rust_core" / "deny.toml").read_text(encoding="utf-8"))

    licenses = policy["licenses"]
    assert licenses["allow"]
    assert licenses["confidence-threshold"] >= 0.9
