import importlib.util
from copy import deepcopy
from pathlib import Path

import yaml

_SPEC = importlib.util.spec_from_file_location(
    "validate_release_assets",
    Path(__file__).resolve().parents[2] / "scripts/validate_release_assets.py",
)
assert _SPEC is not None and _SPEC.loader is not None
_MODULE = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(_MODULE)
validate_ci_workflow_content = _MODULE.validate_ci_workflow_content
validate_audit_workflow_content = _MODULE.validate_audit_workflow_content
validate_dependabot_config = _MODULE.validate_dependabot_config


def test_standalone_preflight_ci_step_is_required() -> None:
    root = Path(__file__).resolve().parents[2]
    workflow = yaml.safe_load((root / ".github/workflows/ci.yml").read_text(encoding="utf-8"))
    steps = workflow["jobs"]["test-rust-core"]["steps"]
    index = next(
        i
        for i, step in enumerate(steps)
        if step.get("name") == "Test standalone recovery preflight"
    )
    assert not any(
        "standalone preflight" in error
        for error in validate_ci_workflow_content(ci_workflow=yaml.safe_dump(workflow))
    )
    for key, value in (
        ("if", "false"),
        ("run", "echo skipped"),
        ("working-directory", "."),
        ("env", {"RUSTUP_TOOLCHAIN": "nightly"}),
        ("env", None),
        ("continue-on-error", True),
    ):
        changed = deepcopy(workflow)
        changed["jobs"]["test-rust-core"]["steps"][index][key] = value
        assert any(
            "standalone preflight" in error
            for error in validate_ci_workflow_content(ci_workflow=yaml.safe_dump(changed))
        ), key
    for duplicate in (False, True):
        changed = deepcopy(workflow)
        target = changed["jobs"]["test-rust-core"]["steps"]
        target.append(deepcopy(target[index])) if duplicate else target.pop(index)
        assert any(
            "standalone preflight" in error
            for error in validate_ci_workflow_content(ci_workflow=yaml.safe_dump(changed))
        ), duplicate


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
