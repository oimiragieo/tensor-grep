import json
from pathlib import Path

from typer.testing import CliRunner

from tensor_grep.cli.main import app


def test_dogfood_command_wraps_agent_readiness_report(tmp_path: Path) -> None:
    scripts_dir = tmp_path / "scripts"
    scripts_dir.mkdir()
    script = scripts_dir / "agent_readiness.py"
    script.write_text(
        "\n".join([
            "import json",
            "import sys",
            "payload = {",
            "  'artifact': 'agent_readiness_report',",
            "  'expected_version': '9.9.9',",
            "  'root': sys.argv[sys.argv.index('--root') + 1],",
            "  'summary': {'passed': 2, 'failed': 0, 'skipped': 1},",
            "  'results': [],",
            "}",
            "print(json.dumps(payload))",
        ]),
        encoding="utf-8",
    )
    output = tmp_path / "artifacts" / "dogfood.json"

    result = CliRunner().invoke(
        app,
        [
            "dogfood",
            "--root",
            str(tmp_path),
            "--output",
            str(output),
            "--no-shell-probes",
            "--no-wsl-probe",
        ],
    )

    assert result.exit_code == 0
    assert "Dogfood verdict: PASS" in result.stdout
    assert "passed=2 failed=0 skipped=1" in result.stdout
    assert "world-class claim: not_claimed" in result.stdout
    payload = json.loads(output.read_text(encoding="utf-8"))
    assert payload["artifact"] == "dogfood_readiness_report"
    assert payload["verdict"]["status"] == "PASS"
    assert payload["world_class_readiness"]["status"] == "not_claimed"
    assert "fast release-readiness gate" in payload["world_class_readiness"]["summary"]
    limitation_surfaces = {
        item["surface"] for item in payload["world_class_readiness"]["limitations"]
    }
    assert {
        "raw_cold_text_search",
        "full_ast_grep_surface",
        "public_gpu_acceleration",
        "lsp_semantic_provider",
        "agent_target_selection_metrics",
    }.issubset(limitation_surfaces)
    gpu_limitation = next(
        item
        for item in payload["world_class_readiness"]["limitations"]
        if item["surface"] == "public_gpu_acceleration"
    )
    assert "declared workload class" in gpu_limitation["required_evidence"]
    assert "NativeGpuBackend" in gpu_limitation["required_evidence"]
    assert "sidecar_used=false" in gpu_limitation["required_evidence"]
    assert "1GB/5GB correctness" in gpu_limitation["required_evidence"]
    assert "rg -F -e ... -e ..." in gpu_limitation["required_evidence"]
    assert payload["agent_readiness"]["summary"]["passed"] == 2


def test_dogfood_command_returns_failure_when_readiness_fails(tmp_path: Path) -> None:
    scripts_dir = tmp_path / "scripts"
    scripts_dir.mkdir()
    script = scripts_dir / "agent_readiness.py"
    script.write_text(
        "\n".join([
            "import json",
            "payload = {",
            "  'artifact': 'agent_readiness_report',",
            "  'expected_version': '9.9.9',",
            "  'root': '.',",
            "  'summary': {'passed': 1, 'failed': 1, 'skipped': 0},",
            "  'results': [{'name': 'docs-claim-check', 'status': 'failed'}],",
            "}",
            "print(json.dumps(payload))",
            "raise SystemExit(1)",
        ]),
        encoding="utf-8",
    )

    result = CliRunner().invoke(
        app,
        ["dogfood", "--root", str(tmp_path), "--json", "--no-shell-probes", "--no-wsl-probe"],
    )

    assert result.exit_code == 1
    payload = json.loads(result.stdout)
    assert payload["verdict"]["status"] == "FAIL"
    assert payload["verdict"]["failed_checks"] == ["docs-claim-check"]


def test_dogfood_json_progress_always_uses_stderr_only(tmp_path: Path) -> None:
    scripts_dir = tmp_path / "scripts"
    scripts_dir.mkdir()
    script = scripts_dir / "agent_readiness.py"
    script.write_text(
        "\n".join([
            "import json",
            "payload = {",
            "  'artifact': 'agent_readiness_report',",
            "  'expected_version': '9.9.9',",
            "  'root': '.',",
            "  'summary': {'passed': 1, 'failed': 0, 'skipped': 0},",
            "  'results': [],",
            "}",
            "print(json.dumps(payload))",
        ]),
        encoding="utf-8",
    )

    result = CliRunner().invoke(
        app,
        [
            "dogfood",
            "--root",
            str(tmp_path),
            "--json",
            "--progress",
            "always",
            "--no-shell-probes",
            "--no-wsl-probe",
        ],
    )

    assert result.exit_code == 0
    payload = json.loads(result.stdout)
    assert payload["verdict"]["status"] == "PASS"
    assert payload["world_class_readiness"]["status"] == "not_claimed"
    assert payload["stderr_tail"] == []
    assert "[progress]" in result.stderr
    assert "[progress]" not in result.stdout


def test_dogfood_command_caps_nested_readiness_tails(tmp_path: Path) -> None:
    scripts_dir = tmp_path / "scripts"
    scripts_dir.mkdir()
    script = scripts_dir / "agent_readiness.py"
    giant = "z" * 9000
    script.write_text(
        "\n".join([
            "import sys",
            f"print({giant!r})",
            f"print({giant!r}, file=sys.stderr)",
        ]),
        encoding="utf-8",
    )
    output = tmp_path / "artifacts" / "dogfood.json"

    result = CliRunner().invoke(
        app,
        [
            "dogfood",
            "--root",
            str(tmp_path),
            "--output",
            str(output),
            "--no-shell-probes",
            "--no-wsl-probe",
        ],
    )

    assert result.exit_code == 1
    payload = json.loads(output.read_text(encoding="utf-8"))
    nested = payload["agent_readiness"]["results"][0]
    assert "truncated" in nested["stdout_tail"][0]
    assert "truncated" in nested["stderr_tail"][0]
    assert "truncated" in payload["stderr_tail"][0]
    assert len(nested["stdout_tail"][0]) < 4200
    assert len(nested["stderr_tail"][0]) < 4200
    assert len(payload["stderr_tail"][0]) < 4200


def test_dogfood_rejects_non_positive_progress_interval(tmp_path: Path) -> None:
    result = CliRunner().invoke(
        app,
        [
            "dogfood",
            "--root",
            str(tmp_path),
            "--progress-interval-s",
            "0",
            "--no-shell-probes",
            "--no-wsl-probe",
        ],
    )

    assert result.exit_code == 2
    assert "progress interval must be greater than 0" in result.stderr
