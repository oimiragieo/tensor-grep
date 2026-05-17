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
