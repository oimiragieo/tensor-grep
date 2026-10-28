from copy import deepcopy
from pathlib import Path

import yaml

from scripts._release_assets_checks.workflow_checks import (
    validate_audit_workflow_content,
    validate_dependabot_config,
)


def test_nested_dependency_audits_are_structurally_required() -> None:
    root = Path(__file__).resolve().parents[2]
    text = (root / ".github/workflows/audit.yml").read_text(encoding="utf-8")
    assert not [
        error
        for error in validate_audit_workflow_content(workflow_content=text)
        if "recovery preflight step" in error
    ]
    workflow = yaml.safe_load(text)
    steps = workflow["jobs"]["audit"]["steps"]
    for name in ("Audit recovery preflight lockfile", "Check recovery preflight dependencies"):
        index = next(i for i, step in enumerate(steps) if step.get("name") == name)
        for key, value in (
            ("run", "echo skipped"),
            ("working-directory", "."),
            ("if", "false"),
            ("continue-on-error", True),
        ):
            mutated = deepcopy(workflow)
            mutated["jobs"]["audit"]["steps"][index][key] = value
            errors = validate_audit_workflow_content(workflow_content=yaml.safe_dump(mutated))
            assert any(f"recovery preflight step `{name}`" in error for error in errors), (
                name,
                key,
            )
        mutated = deepcopy(workflow)
        mutated["jobs"]["audit"]["steps"].pop(index)
        errors = validate_audit_workflow_content(workflow_content=yaml.safe_dump(mutated))
        assert any(f"recovery preflight step `{name}`" in error for error in errors)


def test_dependabot_monitors_nested_package() -> None:
    root = Path(__file__).resolve().parents[2]
    text = (root / ".github/dependabot.yml").read_text(encoding="utf-8")
    assert not validate_dependabot_config(dependabot_content=text)
    changed = text.replace('directory: "/rust_core/recovery-preflight"', 'directory: "/wrong"')
    assert changed != text
    assert any(
        "missing required update target `cargo` in `/rust_core/recovery-preflight`" in error
        for error in validate_dependabot_config(dependabot_content=changed)
    )
