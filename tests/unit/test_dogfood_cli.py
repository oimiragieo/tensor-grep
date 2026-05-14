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
    payload = json.loads(output.read_text(encoding="utf-8"))
    assert payload["artifact"] == "dogfood_readiness_report"
    assert payload["verdict"]["status"] == "PASS"
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
