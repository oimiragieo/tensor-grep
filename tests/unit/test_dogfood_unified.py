from __future__ import annotations

import json
import subprocess
from pathlib import Path

import pytest
from typer.testing import CliRunner

from tensor_grep.cli import dogfood_unified as unified
from tensor_grep.cli.main import app


def _feature_result(status: str = "passed") -> dict[str, object]:
    return {
        "results": [{"name": "SQL query file", "status": status}],
        "summary": {status: 1},
        "artifact_identity": {"version": "2.0.0"},
    }


def test_mutually_exclusive_modes_before_testing() -> None:
    result = CliRunner().invoke(app, ["dogfood", "--features", "--all"])
    assert result.exit_code == 2
    assert "mutually exclusive" in result.output


def test_features_uses_packaged_runner_outside_checkout(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    commands = []
    monkeypatch.setenv("TG_NATIVE_TG_BINARY", "selected-tg.exe")

    def run(command: list[str], **kwargs: object) -> subprocess.CompletedProcess[str]:
        commands.append(command)
        assert kwargs["cwd"] == tmp_path
        assert kwargs["env"]["TG_BIN"] == "selected-tg.exe"  # type: ignore[index]
        return subprocess.CompletedProcess(command, 0, json.dumps(_feature_result()), "")

    monkeypatch.setattr(unified.subprocess, "run", run)
    code, report = unified.run_unified_dogfood(root=tmp_path, features=True)
    assert code == 0
    assert commands[0][1:4] == ["-I", "-m", "tensor_grep.cli.dogfood_features"]
    assert report["mode"] == "features"
    assert report["selected_checks"] == ["SQL query file"]
    assert not (tmp_path / "scripts").exists()


def test_all_deduplicates_union(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setattr(
        unified.subprocess,
        "run",
        lambda command, **kwargs: subprocess.CompletedProcess(
            command, 0, json.dumps(_feature_result()), ""
        ),
    )
    readiness = {
        "agent_readiness": {
            "results": [
                {"name": "SQL query file", "status": "passed"},
                {"name": "readiness", "status": "passed"},
            ]
        }
    }
    monkeypatch.setattr(unified, "run_dogfood_readiness", lambda **kwargs: (0, readiness))
    code, report = unified.run_unified_dogfood(root=tmp_path, all_checks=True)
    assert code == 0
    assert report["selected_checks"] == ["SQL query file", "readiness"]
    assert report["agent_readiness"]["summary"]["passed"] == 2


def test_artifact_refusal_prevents_readiness(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    refused = _feature_result("failed")
    refused.pop("artifact_identity")
    monkeypatch.setattr(
        unified.subprocess,
        "run",
        lambda command, **kwargs: subprocess.CompletedProcess(command, 1, json.dumps(refused), ""),
    )
    monkeypatch.setattr(
        unified,
        "run_dogfood_readiness",
        lambda **kwargs: pytest.fail("readiness ran after artifact refusal"),
    )
    code, report = unified.run_unified_dogfood(root=tmp_path, all_checks=True)
    assert code == 1
    assert report["verdict"]["status"] == "FAIL"


def test_features_timeout_is_separate_from_skip(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    def run(command: list[str], **kwargs: object) -> subprocess.CompletedProcess[str]:
        raise subprocess.TimeoutExpired(command, 1)

    monkeypatch.setattr(unified.subprocess, "run", run)
    code, report = unified.run_unified_dogfood(root=tmp_path, features=True, timeout_s=1)
    assert code == 1
    assert report["agent_readiness"]["summary"]["timed_out"] == 1
    assert report["agent_readiness"]["summary"]["skipped"] == 0
    assert report["verdict"]["failed_checks"] == ["packaged-feature-runner"]


def test_features_default_expected_version_from_selected_checkout(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    (tmp_path / "pyproject.toml").write_text(
        '[project]\nname="tensor-grep"\nversion="2.0.0"\n', encoding="utf-8"
    )

    def run(command: list[str], **kwargs: object) -> subprocess.CompletedProcess[str]:
        assert command[command.index("--expected-version") + 1] == "2.0.0"
        return subprocess.CompletedProcess(command, 0, json.dumps(_feature_result()), "")

    monkeypatch.setattr(unified.subprocess, "run", run)
    code, report = unified.run_unified_dogfood(root=tmp_path, features=True)
    assert code == 0
    assert report["expected_version_source"] == "selected-checkout-pyproject"


def test_duplicate_pass_cannot_hide_feature_failure(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setattr(
        unified.subprocess,
        "run",
        lambda command, **kwargs: subprocess.CompletedProcess(
            command, 1, json.dumps(_feature_result("failed")), ""
        ),
    )
    readiness = {"agent_readiness": {"results": [{"name": "SQL query file", "status": "passed"}]}}
    monkeypatch.setattr(unified, "run_dogfood_readiness", lambda **kwargs: (0, readiness))
    code, report = unified.run_unified_dogfood(root=tmp_path, all_checks=True)
    assert code == 1
    assert report["agent_readiness"]["summary"]["failed"] == 1
    evidence = report["agent_readiness"]["results"][0]["duplicate_evidence"]
    assert [item["status"] for item in evidence] == ["failed", "passed"]
