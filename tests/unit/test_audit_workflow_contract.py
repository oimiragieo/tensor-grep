from __future__ import annotations

import tomllib
from pathlib import Path


def test_audit_workflow_requires_repo_owned_cargo_deny_policy() -> None:
    repo_root = Path(__file__).resolve().parents[2]
    workflow = (repo_root / ".github" / "workflows" / "audit.yml").read_text(encoding="utf-8")

    assert "working-directory: rust_core" in workflow
    assert "cargo deny check" in workflow
    assert (repo_root / "rust_core" / "deny.toml").exists()


def test_audit_workflow_creates_uv_environment_before_installing_pip_audit() -> None:
    repo_root = Path(__file__).resolve().parents[2]
    workflow = (repo_root / ".github" / "workflows" / "audit.yml").read_text(encoding="utf-8")

    create_env = "uv venv --python 3.12"
    install_audit = "uv pip install pip-audit"
    run_audit = "uv run pip-audit"

    assert create_env in workflow
    assert install_audit in workflow
    assert run_audit in workflow
    assert workflow.index(create_env) < workflow.index(install_audit) < workflow.index(run_audit)


def test_cargo_deny_policy_declares_explicit_license_allowlist() -> None:
    repo_root = Path(__file__).resolve().parents[2]
    policy = tomllib.loads((repo_root / "rust_core" / "deny.toml").read_text(encoding="utf-8"))

    licenses = policy["licenses"]
    assert licenses["allow"]
    assert licenses["confidence-threshold"] >= 0.9
