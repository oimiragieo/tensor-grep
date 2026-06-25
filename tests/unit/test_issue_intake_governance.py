import importlib.util
import sys
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[2]


def _load_triage_module():
    script_path = ROOT / "scripts" / "triage_issue.py"
    spec = importlib.util.spec_from_file_location("triage_issue", script_path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def test_issue_templates_should_exist_and_disable_blank_public_reports() -> None:
    template_dir = ROOT / ".github" / "ISSUE_TEMPLATE"
    required = {
        "bug_report.yml",
        "feature_request.yml",
        "question.yml",
        "docs.yml",
        "config.yml",
    }

    assert required.issubset({path.name for path in template_dir.iterdir()})

    config = yaml.safe_load((template_dir / "config.yml").read_text(encoding="utf-8"))
    assert config["blank_issues_enabled"] is False
    links = config.get("contact_links", [])
    assert any("security/advisories/new" in link["url"] for link in links)
    assert all("/discussions" not in link["url"] for link in links)


def test_issue_templates_should_require_non_security_confirmation() -> None:
    template_dir = ROOT / ".github" / "ISSUE_TEMPLATE"
    for name in ["bug_report.yml", "feature_request.yml", "question.yml", "docs.yml"]:
        template = yaml.safe_load((template_dir / name).read_text(encoding="utf-8"))
        body = template["body"]
        security_sections = [
            section
            for section in body
            if section.get("id") == "security" and section.get("type") == "checkboxes"
        ]
        assert security_sections, f"{name} must include a security confirmation"
        options = security_sections[0]["attributes"]["options"]
        assert any(option.get("required") is True for option in options)


def test_issue_triage_workflow_should_be_least_privilege_and_issue_only() -> None:
    workflow_path = ROOT / ".github" / "workflows" / "issue-triage.yml"
    workflow_text = workflow_path.read_text(encoding="utf-8")
    workflow = yaml.safe_load(workflow_text)

    assert "pull_request_target" not in workflow_text
    assert workflow[True] == {"issues": {"types": ["opened", "edited", "reopened"]}}
    assert workflow["permissions"] == {"contents": "read", "issues": "write"}
    assert "secrets.OPENAI" not in workflow_text
    assert "secrets.ANTHROPIC" not in workflow_text
    assert "scripts/triage_issue.py" in workflow_text
    assert "persist-credentials: false" in workflow_text


def test_issue_triage_workflow_should_not_run_reporter_content() -> None:
    workflow_text = (ROOT / ".github" / "workflows" / "issue-triage.yml").read_text(
        encoding="utf-8"
    )

    forbidden = [
        "github.event.issue.body |",
        "${{ github.event.issue.body }}",
        'bash -c "$',
        "eval ",
    ]
    for token in forbidden:
        assert token not in workflow_text


def test_issue_triage_workflow_should_define_every_script_label() -> None:
    workflow_text = (ROOT / ".github" / "workflows" / "issue-triage.yml").read_text(
        encoding="utf-8"
    )
    module = _load_triage_module()

    for label in module.TRIAGE_LABELS:
        assert f'"{label}"' in workflow_text


def test_issue_intake_process_should_be_documented() -> None:
    readme = (ROOT / "README.md").read_text(encoding="utf-8")
    contributing = (ROOT / "CONTRIBUTING.md").read_text(encoding="utf-8")
    security = (ROOT / "SECURITY.md").read_text(encoding="utf-8")

    # README's issue section was simplified in the marketing rewrite; the full intake process is
    # governed in CONTRIBUTING.md + SECURITY.md below. The README must still surface bug reporting.
    assert "issues/new" in readme
    assert "Public Issue Intake" in contributing
    assert "security/advisories/new" in contributing
    assert "does not call external AI services" in contributing
    assert "does not execute issue content" in security
    assert "private security review" in security
