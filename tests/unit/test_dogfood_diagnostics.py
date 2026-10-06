from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest
from typer.testing import CliRunner

from tensor_grep.cli import main as cli_main
from tensor_grep.cli import repair_env
from tensor_grep.cli.main import app

runner = CliRunner()


def _prepare_repair_env(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    (tmp_path / "pyproject.toml").write_text(
        '[project]\nname = "tensor-grep"\nversion = "2.0.0"\n', encoding="utf-8"
    )
    monkeypatch.setattr(repair_env.runtime_paths, "_repo_root", lambda: tmp_path)
    monkeypatch.setattr(repair_env, "editable_install_points_at", lambda _root: (True, "1.0.0"))
    monkeypatch.setattr(repair_env.shutil, "which", lambda _name: None)
    monkeypatch.setattr(repair_env.runtime_paths, "_read_project_version_fallback", lambda: "2.0.0")
    monkeypatch.setattr(
        "importlib.metadata.Distribution.from_name",
        lambda _name: type("Dist", (), {"version": "2.0.0"})(),
    )


def test_repair_env_decodes_utf8_and_replaces_malformed_installer_bytes(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    _prepare_repair_env(monkeypatch, tmp_path)
    real_run = subprocess.run
    captured_kwargs: dict[str, object] = {}

    def emit_diagnostic(command: list[str], **kwargs: object) -> subprocess.CompletedProcess[str]:
        captured_kwargs.update(kwargs)
        return real_run(
            [
                sys.executable,
                "-c",
                "import sys; sys.stderr.buffer.write(b'installer: \\xe2\\x98\\x83 bad=\\xff'); sys.exit(7)",
            ],
            **kwargs,
        )

    monkeypatch.setattr(repair_env.subprocess, "run", emit_diagnostic)

    result = runner.invoke(app, ["repair-env", "--json"])

    assert result.exit_code == 1
    payload = json.loads(result.stdout)
    assert captured_kwargs["capture_output"] is True
    assert captured_kwargs["text"] is True
    assert captured_kwargs["encoding"] == "utf-8"
    assert captured_kwargs["errors"] == "replace"
    assert captured_kwargs["check"] is False
    assert captured_kwargs["timeout"] == 60
    assert "☃" in payload["error"]
    assert "�" in payload["error"]


def test_repair_env_uses_return_code_when_process_output_is_absent(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    _prepare_repair_env(monkeypatch, tmp_path)

    def no_output(command: list[str], **_kwargs: object) -> subprocess.CompletedProcess[str]:
        return subprocess.CompletedProcess(command, 17, None, None)

    monkeypatch.setattr(repair_env.subprocess, "run", no_output)

    result = runner.invoke(app, ["repair-env", "--json"])

    assert result.exit_code == 1
    payload = json.loads(result.stdout)
    assert "Process exited with 17" in payload["error"]


def test_repair_env_uses_return_code_when_process_output_is_blank(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    _prepare_repair_env(monkeypatch, tmp_path)

    def blank_output(command: list[str], **_kwargs: object) -> subprocess.CompletedProcess[str]:
        return subprocess.CompletedProcess(command, 23, "  ", "\n")

    monkeypatch.setattr(repair_env.subprocess, "run", blank_output)

    result = runner.invoke(app, ["repair-env", "--json"])

    assert result.exit_code == 1
    assert "Process exited with 23" in json.loads(result.stdout)["error"]


def test_repair_env_reports_installer_timeout(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    _prepare_repair_env(monkeypatch, tmp_path)

    def timeout(command: list[str], **kwargs: object) -> subprocess.CompletedProcess[str]:
        raise subprocess.TimeoutExpired(command, kwargs["timeout"])

    monkeypatch.setattr(repair_env.subprocess, "run", timeout)

    result = runner.invoke(app, ["repair-env", "--json"])

    assert result.exit_code == 1
    payload = json.loads(result.stdout)
    assert payload["status"] == "failed"
    assert "timed out" in payload["error"]


@pytest.mark.parametrize(
    ("backend", "sidecar", "expected_status"),
    [
        ("NativeGpuBackend", False, "supported"),
        ("GpuSidecar", True, "unsupported"),
    ],
)
def test_doctor_gpu_probe_uses_supported_argv_and_preserves_execution_proof(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    backend: str,
    sidecar: bool,
    expected_status: str,
) -> None:
    binary = tmp_path / "tg.exe"
    binary.write_text("native", encoding="utf-8")
    monkeypatch.setattr(cli_main, "is_cross_domain_native_binary", lambda _binary: False)
    captured: dict[str, object] = {}

    def run(command: list[str], **kwargs: object) -> subprocess.CompletedProcess[str]:
        captured["command"] = command
        captured["kwargs"] = kwargs
        payload = {
            "routing_backend": backend,
            "routing_reason": "gpu-device-ids-explicit",
            "sidecar_used": sidecar,
            "routing_gpu_device_ids": [0] if not sidecar else [],
        }
        return subprocess.CompletedProcess(command, 0, json.dumps(payload), "")

    monkeypatch.setattr(cli_main.subprocess, "run", run)

    result = cli_main._doctor_gpu_search_runtime_probe(binary)
    command = captured["command"]
    assert isinstance(command, list)

    assert result["status"] == expected_status
    assert "-F" not in command
    assert command[command.index("--") + 1] == "tg doctor gpu runtime probe"
    assert command[command.index("--") + 2].endswith("probe.log")
    kwargs = captured["kwargs"]
    assert isinstance(kwargs, dict)
    assert kwargs["timeout"] > 0
    if sidecar:
        assert result["error"] == (
            "GPU route lacks proved native execution "
            "(routing_backend=GpuSidecar, sidecar_used=True)."
        )


@pytest.mark.parametrize("stdout", ["[]", "null", "7", '"probe"'])
def test_doctor_gpu_probe_rejects_non_object_json(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, stdout: str
) -> None:
    binary = tmp_path / "tg.exe"
    binary.write_text("native", encoding="utf-8")
    monkeypatch.setattr(cli_main, "is_cross_domain_native_binary", lambda _binary: False)
    monkeypatch.setattr(
        cli_main.subprocess,
        "run",
        lambda command, **_kwargs: subprocess.CompletedProcess(command, 0, stdout, ""),
    )

    result = cli_main._doctor_gpu_search_runtime_probe(binary)

    assert result["status"] == "failed"
    assert result["error"] == (
        "GPU runtime probe returned invalid JSON object: expected a JSON object"
    )


def test_doctor_gpu_probe_preserves_native_failure_classification(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    binary = tmp_path / "tg.exe"
    binary.write_text("native", encoding="utf-8")
    monkeypatch.setattr(cli_main, "is_cross_domain_native_binary", lambda _binary: False)

    def run(command: list[str], **_kwargs: object) -> subprocess.CompletedProcess[str]:
        payload = {"error": "gpu_fatal", "detail": "CUDA initialization failed"}
        return subprocess.CompletedProcess(command, 2, json.dumps(payload), "driver unavailable")

    monkeypatch.setattr(cli_main.subprocess, "run", run)

    result = cli_main._doctor_gpu_search_runtime_probe(binary)

    assert result["status"] == "failed_gpu_unavailable"
    assert result["native_error_kind"] == "gpu_fatal"
    assert result["error"] == "driver unavailable"
