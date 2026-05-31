import importlib.util
import json
import sys
from pathlib import Path


def _load_module():
    root = Path(__file__).resolve().parents[2]
    script_path = root / "scripts" / "triage_issue.py"
    spec = importlib.util.spec_from_file_location("triage_issue", script_path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def test_bug_report_should_get_repro_and_area_labels() -> None:
    module = _load_module()
    result = module.triage_issue({
        "title": "bug: tg search --format rg crashes on Windows",
        "body": "\n".join([
            "### tensor-grep version",
            "tensor-grep 1.13.26",
            "### Command or API call",
            "tg search ERROR . --format rg",
            "### Actual behavior",
            "Traceback",
            "### Security check",
            "- [x] This report does not contain an undisclosed vulnerability, exploit, secret, credential, or private token.",
        ]),
        "labels": [{"name": "bug"}],
    })

    assert "type:bug" in result.labels
    assert "area:cli" in result.labels
    assert "area:windows" in result.labels
    assert "needs-repro" in result.labels
    assert "needs-info" in result.labels
    assert "security-review" not in result.labels


def test_security_sensitive_issue_should_not_echo_reporter_content() -> None:
    module = _load_module()
    secret_text = "API_KEY=abc123"
    result = module.triage_issue({
        "title": "bug: possible token leak in logs",
        "body": f"The tool printed {secret_text}",
        "labels": [{"name": "bug"}],
    })

    assert "security-review" in result.labels
    assert "needs-private-security-review" in result.labels
    assert "manual-review" in result.labels
    assert "area:security" in result.labels
    assert "security/advisories/new" in result.comment_body
    assert secret_text not in result.comment_body


def test_feature_request_for_gpu_performance_should_require_benchmark_evidence() -> None:
    module = _load_module()
    result = module.triage_issue({
        "title": "feat: add faster GPU benchmark routing",
        "body": "\n".join([
            "### Problem",
            "GPU search setup is hard.",
            "### Proposed solution",
            "Improve native GPU benchmark routing.",
            "### Acceptance criteria",
            "- Shows correctness and speed evidence.",
            "### Security check",
            "- [x] This request does not contain an undisclosed vulnerability, exploit, secret, credential, or private token.",
        ]),
        "labels": [{"name": "enhancement"}],
    })

    assert "type:feature" in result.labels
    assert "area:gpu" in result.labels
    assert "area:performance" in result.labels
    assert "benchmark-required" in result.labels
    assert "needs-info" not in result.labels


def test_perf_issue_should_get_performance_labels_and_benchmark_gate() -> None:
    module = _load_module()
    result = module.triage_issue({
        "title": "perf: reduce public shim startup overhead",
        "body": "\n".join([
            "### Problem",
            "Cold CLI startup is slower than rg.",
            "### Proposed solution",
            "Reduce launcher overhead.",
            "### Acceptance criteria",
            "- Preserve semantic parity.",
            "- Include benchmark artifacts.",
            "### Security check",
            "- [x] This request does not contain an undisclosed vulnerability, exploit, secret, credential, or private token.",
        ]),
        "labels": [],
    })

    assert "type:feature" in result.labels
    assert "area:performance" in result.labels
    assert "area:gpu" not in result.labels
    assert "benchmark-required" in result.labels
    assert "priority:medium" in result.labels
    assert "type:question" not in result.labels


def test_cli_entrypoint_should_emit_json(tmp_path) -> None:
    event_path = tmp_path / "event.json"
    output_path = tmp_path / "triage.json"
    event_path.write_text(
        json.dumps({
            "issue": {
                "title": "docs: README typo",
                "body": "### Page or section\nREADME.md\n### What is wrong or missing?\nTypo",
                "labels": [{"name": "documentation"}],
            }
        }),
        encoding="utf-8",
    )

    module = _load_module()

    old_argv = sys.argv
    sys.argv = [
        "triage_issue.py",
        "--event-path",
        str(event_path),
        "--output",
        str(output_path),
    ]
    try:
        assert module.main() == 0
    finally:
        sys.argv = old_argv

    payload = json.loads(output_path.read_text(encoding="utf-8"))
    assert "type:docs" in payload["labels"]
    assert "Automated issue triage" in payload["comment_body"]
