import json
from pathlib import Path

from typer.testing import CliRunner

from tensor_grep.cli import dogfood as dogfood_module
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
    assert payload["world_class_readiness"]["raw_cold_search_baseline"] == "rg"
    assert payload["world_class_readiness"]["raw_cold_search_claim_status"] == "not_claimed"
    assert payload["world_class_readiness"]["launcher_startup_tax_status"] == "measured_separately"
    assert payload["world_class_readiness"]["gpu_promotion_ready"] is False
    assert "NativeGpuBackend" in payload["world_class_readiness"]["gpu_promotion_blockers"]
    assert "sidecar_used=false" in payload["world_class_readiness"]["gpu_promotion_blockers"]
    assert "public managed NVIDIA" in payload["world_class_readiness"]["gpu_promotion_blockers"]
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
    assert (
        payload["write_policy"]["mode"] == "read_only_except_explicit_output_and_readiness_probes"
    )
    assert payload["write_policy"]["tracked_release_docs_mutation"] == "not_performed"
    assert payload["write_policy"]["allowed_writes"] == [
        str((tmp_path / "artifacts" / "agent_readiness").resolve()),
        str(output.resolve()),
    ]
    assert payload["write_policy"]["release_docs_stamp_command"] == (
        "python scripts/stamp_release_assets.py"
    )
    assert payload["release_docs_worktree"]["read_only"] is True


def test_dogfood_help_documents_probe_artifact_write_policy() -> None:
    result = CliRunner().invoke(app, ["dogfood", "--help"])

    assert result.exit_code == 0
    normalized = " ".join(result.stdout.split())
    assert "writes only probe artifacts and --output" in normalized


def test_dogfood_release_docs_worktree_status_reports_dirty_docs(
    monkeypatch, tmp_path: Path
) -> None:
    calls: list[list[str]] = []

    class Completed:
        returncode = 0
        stdout = " M README.md\n?? docs/SESSION_HANDOFF.md\n"
        stderr = ""

    def fake_run(command, **_kwargs):
        calls.append([str(part) for part in command])
        return Completed()

    monkeypatch.setattr(dogfood_module.subprocess, "run", fake_run)

    status = dogfood_module._build_release_docs_worktree_status(tmp_path)

    assert status["status"] == "dirty"
    assert status["dirty_paths"] == ["README.md", "docs/SESSION_HANDOFF.md"]
    assert status["read_only"] is True
    assert calls
    assert calls[0][:4] == ["git", "-C", str(tmp_path.resolve()), "status"]


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


def test_dogfood_non_repo_root_uses_public_self_check(tmp_path: Path) -> None:
    output = tmp_path / "dogfood.json"
    non_repo_root = tmp_path / "user-project"
    non_repo_root.mkdir()

    result = CliRunner().invoke(
        app,
        [
            "dogfood",
            "--root",
            str(non_repo_root),
            "--output",
            str(output),
            "--no-shell-probes",
            "--no-wsl-probe",
        ],
    )

    assert result.exit_code == 0
    assert "Dogfood verdict: PASS" in result.stdout
    assert "failed=0" in result.stdout
    payload = json.loads(output.read_text(encoding="utf-8"))
    assert payload["agent_readiness"]["mode"] == "public-self-check"
    assert payload["agent_readiness"]["summary"]["failed"] == 0
    skipped = [
        result for result in payload["agent_readiness"]["results"] if result["status"] == "skipped"
    ]
    assert skipped
    repo_script_result = next(
        result for result in skipped if result["name"] == "repo-agent-readiness-script"
    )
    assert "scripts/agent_readiness.py" in repo_script_result["message"]


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
