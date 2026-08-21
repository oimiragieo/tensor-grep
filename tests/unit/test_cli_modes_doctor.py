import json
import subprocess
import sys
import types
from pathlib import Path

import pytest
from typer.testing import CliRunner

from tensor_grep.cli import main as cli_main
from tensor_grep.cli.main import (
    app,
)
from tensor_grep.core.result import MatchLine, SearchResult
from tests.unit.test_cli_modes_shared import *  # noqa: F403

# ruff: noqa: F405  -- names come from the shared wildcard import above (W4-d split)


def test_doctor_json_reports_cold_daemon_autostart_hint(monkeypatch, tmp_path: Path) -> None:
    """End-to-end through `tg doctor --json`: a cold box's `session_daemon.running: false` must
    come with the additive `autostart` field, without touching any other doctor field."""
    monkeypatch.setattr("tensor_grep.cli.main._doctor_installed_version", lambda: "9.9.9")
    monkeypatch.setattr("tensor_grep.cli.main.resolve_native_tg_binary", lambda: None)
    monkeypatch.setattr(
        "tensor_grep.cli.session_daemon.get_session_daemon_status",
        lambda path: {"version": 1, "root": path, "discovered": False, "running": False},
    )
    monkeypatch.setattr("tensor_grep.cli.main._session_daemon_autostart_enabled", lambda: True)

    result = CliRunner().invoke(app, ["doctor", str(tmp_path), "--json", "--no-lsp"])

    assert result.exit_code == 0
    payload = json.loads(result.stdout)
    assert payload["session_daemon"]["running"] is False
    assert payload["session_daemon"]["autostart"] == "on-first-use (not yet warmed)"
    # Additive-only: doctor's own top-level schema_version must NOT need a bump for a nested,
    # conditionally-present diagnostic field (CONTRACTS.md section 5: "Individual diagnostic
    # fields may grow as new probes are added").
    assert payload["schema_version"] == 3
    assert payload["doctor_schema_version"] == 3


def test_doctor_text_reports_ast_grep_availability(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.setattr("tensor_grep.cli.main._doctor_installed_version", lambda: "9.9.9")
    monkeypatch.setattr("tensor_grep.cli.main.resolve_native_tg_binary", lambda: None)
    monkeypatch.setattr(
        "tensor_grep.cli.main._doctor_session_daemon_status",
        lambda path: {"running": False},
    )
    monkeypatch.setattr(
        "tensor_grep.cli.main._doctor_ast_grep_status",
        lambda: {
            "schema_version": 1,
            "available": True,
            "binary": "ast-grep",
            "wrapper_backend": "AstGrepWrapperBackend",
            "required_for": "tg run ast-grep semantic options",
            "semantic_run_options": ["--selector", "--strictness", "--stdin", "--globs"],
            "timeout_env": "TG_AST_GREP_TIMEOUT_SECONDS",
            "timeout_seconds": 60.0,
        },
    )

    result = CliRunner().invoke(app, ["doctor", str(tmp_path), "--no-lsp"])

    assert result.exit_code == 0
    assert "ast_grep: available=True binary=ast-grep" in result.stdout
    assert "semantic_run_options=--selector/--strictness/--stdin/--globs" in result.stdout


def test_doctor_json_includes_gpu_search_runtime_probe(monkeypatch, tmp_path: Path) -> None:
    native_tg = tmp_path / "tg.exe"
    native_tg.write_text("native", encoding="utf-8")
    monkeypatch.setattr("tensor_grep.cli.main._doctor_installed_version", lambda: "9.9.9")
    monkeypatch.setattr("tensor_grep.cli.main.resolve_native_tg_binary", lambda: native_tg)
    monkeypatch.setattr(
        "tensor_grep.cli.main._doctor_session_daemon_status",
        lambda path: {"running": False},
    )
    monkeypatch.setattr(
        "tensor_grep.cli.main._doctor_gpu_search_runtime_probe",
        lambda binary: {
            "status": "unsupported",
            "requested_gpu_device_ids": [0],
            "command": f"{binary} search --gpu-device-ids 0 --json -F tg doctor gpu runtime probe",
            "routing_backend": "GpuSidecar",
            "routing_reason": "gpu-device-ids-explicit",
            "sidecar_used": True,
            "routing_gpu_device_ids": [],
            "error": (
                "GPU route did not use NativeGpuBackend "
                "(routing_backend=GpuSidecar, sidecar_used=True)."
            ),
        },
    )

    result = CliRunner().invoke(app, ["doctor", str(tmp_path), "--json", "--no-lsp"])

    assert result.exit_code == 0
    payload = json.loads(result.stdout)
    probe = payload["gpu"]["search_runtime_probe"]
    assert probe["status"] == "unsupported"
    assert probe["requested_gpu_device_ids"] == [0]
    assert probe["routing_backend"] == "GpuSidecar"
    assert probe["sidecar_used"] is True
    assert "NativeGpuBackend" in probe["error"]


def test_doctor_gpu_runtime_probe_redacts_temp_probe_path(monkeypatch, tmp_path: Path) -> None:
    native_tg = tmp_path / "tg.exe"
    native_tg.write_text("native", encoding="utf-8")

    def _fake_run(command, **_kwargs):
        payload = {
            "routing_backend": "GpuSidecar",
            "routing_reason": "gpu-device-ids-explicit",
            "sidecar_used": True,
            "routing_gpu_device_ids": [],
            "path": str(command[-1]),
            "matches": [{"file": str(command[-1]), "line": 1, "text": "probe"}],
        }
        return subprocess.CompletedProcess(command, 0, json.dumps(payload), "")

    monkeypatch.setattr("tensor_grep.cli.main.subprocess.run", _fake_run)

    probe = cli_main._doctor_gpu_search_runtime_probe(native_tg)
    serialized = json.dumps(probe)

    assert probe["status"] == "unsupported"
    assert "tg-doctor-gpu-probe" not in serialized
    assert "probe.log" not in serialized
    assert "<doctor-gpu-probe-file>" in probe["command"]


def test_doctor_gpu_runtime_probe_cross_domain_translates_probe_path(
    monkeypatch, tmp_path: Path
) -> None:
    native_tg = tmp_path / "tg.exe"
    native_tg.write_text("native", encoding="utf-8")
    translated_path = "C:\\Users\\x\\AppData\\Local\\Temp\\tg-doctor-gpu-probe-abc\\probe.log"

    monkeypatch.setattr("tensor_grep.cli.main.is_cross_domain_native_binary", lambda _binary: True)
    monkeypatch.setattr(
        "tensor_grep.cli.main.translate_path_for_windows_binary",
        lambda _path: translated_path,
    )

    captured_command: list[str] = []

    def _fake_run(command, **_kwargs):
        captured_command.extend(command)
        payload = {
            "routing_backend": "NativeGpuBackend",
            "routing_reason": "gpu-device-ids-explicit",
            "sidecar_used": False,
            "routing_gpu_device_ids": [0],
        }
        return subprocess.CompletedProcess(command, 0, json.dumps(payload), "")

    monkeypatch.setattr("tensor_grep.cli.main.subprocess.run", _fake_run)

    probe = cli_main._doctor_gpu_search_runtime_probe(native_tg)

    assert probe["status"] == "supported"
    # The LAST argv element is the sentinel path -- it must be the TRANSLATED Windows path, not
    # the raw Linux TemporaryDirectory path the sentinel file was actually written under.
    assert captured_command[-1] == translated_path


def test_doctor_gpu_runtime_probe_path_domain_mismatch_when_translation_unavailable(
    monkeypatch, tmp_path: Path
) -> None:
    native_tg = tmp_path / "tg.exe"
    native_tg.write_text("native", encoding="utf-8")

    monkeypatch.setattr("tensor_grep.cli.main.is_cross_domain_native_binary", lambda _binary: True)
    monkeypatch.setattr(
        "tensor_grep.cli.main.translate_path_for_windows_binary", lambda _path: None
    )

    def _fake_run(command, **_kwargs):
        raise AssertionError(
            "must not shell out to the native binary when wslpath translation "
            "is unavailable -- the path would be unresolvable and misreport as a GPU failure"
        )

    monkeypatch.setattr("tensor_grep.cli.main.subprocess.run", _fake_run)

    probe = cli_main._doctor_gpu_search_runtime_probe(native_tg)

    assert probe["status"] == "path_domain_mismatch"
    assert probe["error"]
    assert "wslpath" in probe["error"]


def test_doctor_gpu_runtime_probe_same_domain_is_unaffected(monkeypatch, tmp_path: Path) -> None:
    """Cross-domain detection false (the common case) leaves argv/behavior exactly as before."""
    native_tg = tmp_path / "tg.exe"
    native_tg.write_text("native", encoding="utf-8")

    monkeypatch.setattr("tensor_grep.cli.main.is_cross_domain_native_binary", lambda _binary: False)

    def _fail_translate(_path):
        raise AssertionError("must not be called when cross_domain is False")

    monkeypatch.setattr("tensor_grep.cli.main.translate_path_for_windows_binary", _fail_translate)

    captured_command: list[str] = []

    def _fake_run(command, **_kwargs):
        captured_command.extend(command)
        payload = {
            "routing_backend": "NativeGpuBackend",
            "routing_reason": "gpu-device-ids-explicit",
            "sidecar_used": False,
            "routing_gpu_device_ids": [0],
        }
        return subprocess.CompletedProcess(command, 0, json.dumps(payload), "")

    monkeypatch.setattr("tensor_grep.cli.main.subprocess.run", _fake_run)

    probe = cli_main._doctor_gpu_search_runtime_probe(native_tg)

    assert probe["status"] == "supported"
    assert "tg-doctor-gpu-probe-" in captured_command[-1]


def test_doctor_gpu_runtime_probe_timeout_env_override_honored(monkeypatch, tmp_path: Path) -> None:
    native_tg = tmp_path / "tg.exe"
    native_tg.write_text("native", encoding="utf-8")
    monkeypatch.setenv("TENSOR_GREP_GPU_PROBE_TIMEOUT_S", "17.5")

    captured_kwargs: dict = {}

    def _fake_run(command, **kwargs):
        captured_kwargs.update(kwargs)
        payload = {
            "routing_backend": "NativeGpuBackend",
            "routing_reason": "gpu-device-ids-explicit",
            "sidecar_used": False,
            "routing_gpu_device_ids": [0],
        }
        return subprocess.CompletedProcess(command, 0, json.dumps(payload), "")

    monkeypatch.setattr("tensor_grep.cli.main.subprocess.run", _fake_run)

    probe = cli_main._doctor_gpu_search_runtime_probe(native_tg)

    assert probe["status"] == "supported"
    assert captured_kwargs["timeout"] == pytest.approx(17.5)


def test_doctor_gpu_runtime_probe_native_error_kind_is_none_on_success(
    monkeypatch, tmp_path: Path
) -> None:
    native_tg = tmp_path / "tg.exe"
    native_tg.write_text("native", encoding="utf-8")

    def _fake_run(command, **_kwargs):
        payload = {
            "routing_backend": "NativeGpuBackend",
            "routing_reason": "gpu-device-ids-explicit",
            "sidecar_used": False,
            "routing_gpu_device_ids": [0],
        }
        return subprocess.CompletedProcess(command, 0, json.dumps(payload), "")

    monkeypatch.setattr("tensor_grep.cli.main.subprocess.run", _fake_run)

    probe = cli_main._doctor_gpu_search_runtime_probe(native_tg)

    assert probe["status"] == "supported"
    assert probe["native_error_kind"] is None


def test_doctor_gpu_runtime_probe_maps_path_not_found_to_failed_probe_path_when_not_cross_domain(
    monkeypatch, tmp_path: Path
) -> None:
    native_tg = tmp_path / "tg.exe"
    native_tg.write_text("native", encoding="utf-8")
    monkeypatch.setattr("tensor_grep.cli.main.is_cross_domain_native_binary", lambda _binary: False)

    def _fake_run(command, **_kwargs):
        stdout = json.dumps({
            "version": 1,
            "ok": False,
            "error": "path_not_found",
            "detail": "search path does not exist: <doctor-gpu-probe-file>",
        })
        return subprocess.CompletedProcess(command, 2, stdout, "")

    monkeypatch.setattr("tensor_grep.cli.main.subprocess.run", _fake_run)

    probe = cli_main._doctor_gpu_search_runtime_probe(native_tg)

    assert probe["status"] == "failed_probe_path"
    assert probe["native_error_kind"] == "path_not_found"
    assert probe["exit_code"] == 2


def test_doctor_gpu_runtime_probe_maps_path_not_found_to_failed_path_bridging_when_cross_domain(
    monkeypatch, tmp_path: Path
) -> None:
    native_tg = tmp_path / "tg.exe"
    native_tg.write_text("native", encoding="utf-8")
    monkeypatch.setattr("tensor_grep.cli.main.is_cross_domain_native_binary", lambda _binary: True)
    monkeypatch.setattr(
        "tensor_grep.cli.main.translate_path_for_windows_binary", lambda _path: "C:\\translated"
    )

    def _fake_run(command, **_kwargs):
        stdout = json.dumps({
            "version": 1,
            "ok": False,
            "error": "path_not_found",
            "detail": "search path does not exist: <doctor-gpu-probe-file>",
        })
        return subprocess.CompletedProcess(command, 2, stdout, "")

    monkeypatch.setattr("tensor_grep.cli.main.subprocess.run", _fake_run)

    probe = cli_main._doctor_gpu_search_runtime_probe(native_tg)

    assert probe["status"] == "failed_path_bridging"
    assert probe["native_error_kind"] == "path_not_found"
    assert probe["exit_code"] == 2


def test_doctor_gpu_runtime_probe_maps_empty_pattern_to_failed_input(
    monkeypatch, tmp_path: Path
) -> None:
    native_tg = tmp_path / "tg.exe"
    native_tg.write_text("native", encoding="utf-8")

    def _fake_run(command, **_kwargs):
        stdout = json.dumps({
            "version": 1,
            "ok": False,
            "error": "empty_pattern",
            "detail": "PATTERN must not be empty.",
        })
        return subprocess.CompletedProcess(command, 2, stdout, "")

    monkeypatch.setattr("tensor_grep.cli.main.subprocess.run", _fake_run)

    probe = cli_main._doctor_gpu_search_runtime_probe(native_tg)

    assert probe["status"] == "failed_input"
    assert probe["native_error_kind"] == "empty_pattern"


def test_doctor_gpu_runtime_probe_maps_invalid_regex_to_failed_input(
    monkeypatch, tmp_path: Path
) -> None:
    native_tg = tmp_path / "tg.exe"
    native_tg.write_text("native", encoding="utf-8")

    def _fake_run(command, **_kwargs):
        stdout = json.dumps({
            "version": 1,
            "ok": False,
            "error": "invalid_regex",
            "detail": "invalid regex pattern: unterminated group",
        })
        return subprocess.CompletedProcess(command, 2, stdout, "")

    monkeypatch.setattr("tensor_grep.cli.main.subprocess.run", _fake_run)

    probe = cli_main._doctor_gpu_search_runtime_probe(native_tg)

    assert probe["status"] == "failed_input"
    assert probe["native_error_kind"] == "invalid_regex"


def test_doctor_gpu_runtime_probe_maps_gpu_fatal_to_failed_gpu_unavailable(
    monkeypatch, tmp_path: Path
) -> None:
    native_tg = tmp_path / "tg.exe"
    native_tg.write_text("native", encoding="utf-8")

    def _fake_run(command, **_kwargs):
        stdout = json.dumps({
            "version": 1,
            "ok": False,
            "error": "gpu_fatal",
            "detail": "CUDA initialization failed: driver too old",
        })
        return subprocess.CompletedProcess(command, 2, stdout, "")

    monkeypatch.setattr("tensor_grep.cli.main.subprocess.run", _fake_run)

    probe = cli_main._doctor_gpu_search_runtime_probe(native_tg)

    assert probe["status"] == "failed_gpu_unavailable"
    assert probe["native_error_kind"] == "gpu_fatal"


def test_doctor_gpu_runtime_probe_maps_gpu_invalid_device_id_to_failed_input(
    monkeypatch, tmp_path: Path
) -> None:
    native_tg = tmp_path / "tg.exe"
    native_tg.write_text("native", encoding="utf-8")

    def _fake_run(command, **_kwargs):
        stdout = json.dumps({
            "version": 1,
            "ok": False,
            "error": "gpu_invalid_device_id",
            "detail": "invalid CUDA device id 99; available CUDA devices: (none)",
        })
        return subprocess.CompletedProcess(command, 2, stdout, "")

    monkeypatch.setattr("tensor_grep.cli.main.subprocess.run", _fake_run)

    probe = cli_main._doctor_gpu_search_runtime_probe(native_tg)

    assert probe["status"] == "failed_input"
    assert probe["native_error_kind"] == "gpu_invalid_device_id"


def test_doctor_gpu_runtime_probe_maps_unrecognized_error_kind_to_failed_other(
    monkeypatch, tmp_path: Path
) -> None:
    native_tg = tmp_path / "tg.exe"
    native_tg.write_text("native", encoding="utf-8")

    def _fake_run(command, **_kwargs):
        stdout = json.dumps({
            "version": 1,
            "ok": False,
            "error": "some_future_error_kind",
            "detail": "an error the doctor has never seen before",
        })
        return subprocess.CompletedProcess(command, 2, stdout, "")

    monkeypatch.setattr("tensor_grep.cli.main.subprocess.run", _fake_run)

    probe = cli_main._doctor_gpu_search_runtime_probe(native_tg)

    assert probe["status"] == "failed_other"
    assert probe["native_error_kind"] == "some_future_error_kind"


def test_doctor_gpu_runtime_probe_maps_non_json_stdout_to_failed_other(
    monkeypatch, tmp_path: Path
) -> None:
    """rc!=0 with no structured JSON on stdout at all (e.g. a raw panic) must still fail
    closed to a bucketed status instead of raising -- native_error_kind stays None so the
    caller can tell 'could not classify' apart from 'the native binary named a kind'."""
    native_tg = tmp_path / "tg.exe"
    native_tg.write_text("native", encoding="utf-8")

    def _fake_run(command, **_kwargs):
        return subprocess.CompletedProcess(command, 101, "", "thread panicked at ...")

    monkeypatch.setattr("tensor_grep.cli.main.subprocess.run", _fake_run)

    probe = cli_main._doctor_gpu_search_runtime_probe(native_tg)

    assert probe["status"] == "failed_other"
    assert probe["native_error_kind"] is None
    assert probe["error"] == "thread panicked at ..."


def test_doctor_gpu_runtime_probe_native_error_kind_absent_for_path_domain_mismatch(
    monkeypatch, tmp_path: Path
) -> None:
    """The P0-1 WSL path_domain_mismatch short-circuit never execs the native binary at all,
    so there is no native stdout to classify; native_error_kind must stay None, not invent a
    kind, and the pre-existing status must survive untouched."""
    native_tg = tmp_path / "tg.exe"
    native_tg.write_text("native", encoding="utf-8")

    def _fail_run(*_args, **_kwargs):
        raise AssertionError("must not shell out when translation is unavailable")

    monkeypatch.setattr("tensor_grep.cli.main.is_cross_domain_native_binary", lambda _binary: True)
    monkeypatch.setattr("tensor_grep.cli.main.translate_path_for_windows_binary", lambda _p: None)
    monkeypatch.setattr("tensor_grep.cli.main.subprocess.run", _fail_run)

    probe = cli_main._doctor_gpu_search_runtime_probe(native_tg)

    assert probe["status"] == "path_domain_mismatch"
    assert probe["native_error_kind"] is None


def test_doctor_payload_threads_rust_binary_version_into_inspect_native_tg_binary(
    monkeypatch, tmp_path: Path
) -> None:
    native_tg = tmp_path / "tg.exe"
    native_tg.write_text("native", encoding="utf-8")

    monkeypatch.setattr("tensor_grep.cli.main._doctor_installed_version", lambda: "1.75.2")
    monkeypatch.setattr("tensor_grep.cli.main.resolve_native_tg_binary", lambda: native_tg)
    monkeypatch.setattr(
        "tensor_grep.cli.main._doctor_rust_binary_version",
        lambda _binary: "tg 1.75.2",
    )
    monkeypatch.setattr(
        "tensor_grep.cli.main._doctor_session_daemon_status",
        lambda path: {"running": False},
    )

    captured_kwargs: list[dict] = []
    real_inspect = cli_main.inspect_native_tg_binary

    def _capturing_inspect(candidate, **kwargs):
        captured_kwargs.append(kwargs)
        return real_inspect(candidate, **kwargs)

    monkeypatch.setattr("tensor_grep.cli.main.inspect_native_tg_binary", _capturing_inspect)

    runner = CliRunner()
    result = runner.invoke(app, ["doctor", str(tmp_path), "--json", "--no-lsp"])

    assert result.exit_code == 0
    assert len(captured_kwargs) == 1
    assert captured_kwargs[0].get("version_text") == "tg 1.75.2"
    payload = json.loads(result.stdout)
    assert payload["rust_binary_version"] == "tg 1.75.2"


def test_doctor_payload_does_not_double_spawn_native_tg_version(
    monkeypatch, tmp_path: Path
) -> None:
    """The doctor-level spawn-count pin: with rust_binary_version threaded through as
    version_text, inspect_native_tg_binary's own internal _native_tg_version subprocess call
    (runtime_paths.py) must never fire for the doctor's resolved native_tg_binary."""
    from tensor_grep.cli import runtime_paths as runtime_paths_module

    native_tg = tmp_path / "tg.exe"
    native_tg.write_text("native", encoding="utf-8")

    monkeypatch.setattr("tensor_grep.cli.main._doctor_installed_version", lambda: "1.75.2")
    monkeypatch.setattr("tensor_grep.cli.main.resolve_native_tg_binary", lambda: native_tg)
    monkeypatch.setattr(
        "tensor_grep.cli.main._doctor_rust_binary_version",
        lambda _binary: "tg 1.75.2",
    )
    monkeypatch.setattr(
        "tensor_grep.cli.main._doctor_session_daemon_status",
        lambda path: {"running": False},
    )

    native_tg_version_call_count = 0
    real_native_tg_version = runtime_paths_module._native_tg_version

    def _counting_native_tg_version(candidate):
        nonlocal native_tg_version_call_count
        native_tg_version_call_count += 1
        return real_native_tg_version(candidate)

    monkeypatch.setattr(
        "tensor_grep.cli.runtime_paths._native_tg_version", _counting_native_tg_version
    )

    runner = CliRunner()
    result = runner.invoke(app, ["doctor", str(tmp_path), "--json", "--no-lsp"])

    assert result.exit_code == 0
    assert native_tg_version_call_count == 0, (
        "inspect_native_tg_binary must not spawn its own _native_tg_version subprocess when the "
        "doctor already threaded version_text through (would double-spawn `tg --version`)"
    )


def test_doctor_json_reports_native_version_mismatch(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.setattr("tensor_grep.cli.main._doctor_installed_version", lambda: "1.8.1")
    monkeypatch.setattr(
        "tensor_grep.cli.main.resolve_native_tg_binary",
        lambda: tmp_path / "rust_core" / "target" / "release" / "tg.exe",
    )
    monkeypatch.setattr(
        "tensor_grep.cli.main._doctor_rust_binary_version",
        lambda _binary: "tg 1.8.0",
    )
    monkeypatch.setattr(
        "tensor_grep.cli.main._doctor_session_daemon_status",
        lambda path: {"running": False},
    )

    runner = CliRunner()
    result = runner.invoke(app, ["doctor", str(tmp_path), "--json", "--no-lsp"])

    assert result.exit_code == 0
    payload = json.loads(result.stdout)
    assert payload["version"] == "1.8.1"
    assert payload["rust_binary_version"] == "tg 1.8.0"
    assert payload["rust_binary_version_matches"] is False
    assert payload["rust_binary_expected_version"] == "1.8.1"


def test_doctor_json_reports_stale_in_tree_native_binary_as_skipped(
    monkeypatch,
    tmp_path: Path,
) -> None:
    repo_root = Path(__file__).resolve().parents[2]
    native_binary = repo_root / "rust_core" / "target" / "debug" / "tg.exe"

    monkeypatch.setattr("tensor_grep.cli.main._doctor_installed_version", lambda: "1.8.19")
    monkeypatch.setattr("tensor_grep.cli.main.resolve_native_tg_binary", lambda: None)
    monkeypatch.setattr(
        "tensor_grep.cli.main._doctor_skipped_native_tg_binaries",
        lambda _expected_version, _selected_binary: [
            {
                "path": str(native_binary),
                "kind": "in-tree-debug",
                "version": "tg 1.8.14",
                "version_status": "stale",
            }
        ],
        raising=False,
    )
    monkeypatch.setattr(
        "tensor_grep.cli.main._doctor_rust_binary_version",
        lambda _binary: "tg 1.8.14",
    )
    monkeypatch.setattr(
        "tensor_grep.cli.main._doctor_session_daemon_status",
        lambda path: {"running": False},
    )

    runner = CliRunner()
    result = runner.invoke(app, ["doctor", str(tmp_path), "--json", "--no-lsp"])

    assert result.exit_code == 0
    payload = json.loads(result.stdout)
    assert payload["native_tg_binary"] is None
    assert payload["native_tg_binary_kind"] == "missing"
    assert payload["search_acceleration_backend"] in {"rust-core-extension", "python"}
    assert payload["rust_binary_version_status"] == "stale-skipped"
    assert payload["skipped_native_tg_binaries"] == [
        {
            "path": str(native_binary),
            "kind": "in-tree-debug",
            "version": "tg 1.8.14",
            "version_status": "stale",
        }
    ]
    assert "ignored stale in-tree native tg binary" in payload["rust_binary_version_warning"]
    assert "TG_NATIVE_TG_BINARY" in payload["rust_binary_remediation"]


def test_doctor_json_reports_path_tg_candidates(monkeypatch, tmp_path: Path) -> None:
    stale_tg = tmp_path / "Python314" / "Scripts" / "tg.exe"
    current_tg = tmp_path / "bin" / "tg.cmd"
    stale_tg.parent.mkdir(parents=True)
    current_tg.parent.mkdir(parents=True)
    stale_tg.write_text("stale\n", encoding="utf-8")
    current_tg.write_text("current\n", encoding="utf-8")

    monkeypatch.setattr("tensor_grep.cli.main._doctor_installed_version", lambda: "1.8.11")
    monkeypatch.setattr("tensor_grep.cli.main.resolve_native_tg_binary", lambda: None)
    monkeypatch.setattr(
        "tensor_grep.cli.main._doctor_rust_core_extension_available",
        lambda: True,
        raising=False,
    )
    monkeypatch.setattr(
        "tensor_grep.cli.main._doctor_session_daemon_status",
        lambda path: {"running": False},
    )
    monkeypatch.setattr(
        "tensor_grep.cli.main._doctor_lsp_provider_statuses",
        lambda path: [],
    )
    monkeypatch.setattr(
        "tensor_grep.cli.main._doctor_path_tg_candidates",
        lambda: [
            {"path": str(stale_tg), "version": "tensor-grep 1.8.0"},
            {"path": str(current_tg), "version": "tensor-grep 1.8.11"},
        ],
    )
    monkeypatch.setattr(
        "tensor_grep.cli.main._doctor_fresh_shell_path_tg_candidates",
        lambda: [],
    )

    runner = CliRunner()
    result = runner.invoke(app, ["doctor", str(tmp_path), "--json", "--no-lsp"])

    assert result.exit_code == 0
    payload = json.loads(result.stdout)
    assert payload["path_tg_candidates"] == [
        {"path": str(stale_tg), "version": "tensor-grep 1.8.0"},
        {"path": str(current_tg), "version": "tensor-grep 1.8.11"},
    ]
    assert payload["path_tg_first_version_matches"] is False
    assert payload["path_tg_first_version"] == "tensor-grep 1.8.0"
    assert payload["path_tg_first_launcher_kind"] == "python-entrypoint"


def test_doctor_path_tg_candidates_splits_windows_pathext_on_semicolon(
    monkeypatch,
    tmp_path: Path,
) -> None:

    monkeypatch.chdir(tmp_path)
    bridge_tg = Path("Python314") / "Scripts" / "tg.com"
    bridge_tg.parent.mkdir(parents=True)
    bridge_tg.write_text("tensor-grep bridge\n", encoding="utf-8")

    monkeypatch.setattr(cli_main.sys, "platform", "win32")
    monkeypatch.setattr(cli_main.os, "pathsep", ":")
    monkeypatch.setenv("PATHEXT", ".COM;.EXE;.BAT;.CMD")
    monkeypatch.setattr(
        "tensor_grep.cli.main._doctor_tg_candidate_version",
        lambda _candidate: "tg 1.9.7",
    )

    candidates = cli_main._doctor_path_tg_candidates(str(bridge_tg.parent))

    assert candidates == [{"path": str(bridge_tg.resolve()), "version": "tg 1.9.7"}]


def test_doctor_path_tg_candidates_includes_powershell_shim_when_not_in_pathext(
    monkeypatch,
    tmp_path: Path,
) -> None:
    bin_dir = tmp_path / "bin"
    native_tg = bin_dir / "tg.exe"
    shim_tg = bin_dir / "tg.ps1"
    bin_dir.mkdir(parents=True)
    native_tg.write_text("native\n", encoding="utf-8")
    shim_tg.write_text("& $PSScriptRoot/tg.exe @args\n", encoding="utf-8")

    monkeypatch.setattr(cli_main.sys, "platform", "win32")
    monkeypatch.setenv("PATHEXT", ".COM;.EXE;.BAT;.CMD")
    monkeypatch.setattr(
        "tensor_grep.cli.main._doctor_tg_candidate_version",
        lambda candidate: (
            "tensor-grep 1.13.12" if Path(candidate).suffix.lower() == ".ps1" else "tg 1.13.12"
        ),
    )

    candidates = cli_main._doctor_path_tg_candidates(str(bin_dir))

    assert candidates == [
        {"path": str(native_tg.resolve()), "version": "tg 1.13.12"},
        {"path": str(shim_tg.resolve()), "version": "tensor-grep 1.13.12"},
    ]


def test_doctor_fresh_shell_path_uses_windows_registry_separator(monkeypatch) -> None:

    fake_winreg = types.SimpleNamespace()
    fake_winreg.HKEY_LOCAL_MACHINE = object()
    fake_winreg.HKEY_CURRENT_USER = object()

    class _FakeKey:
        def __init__(self, root: object) -> None:
            self.root = root

        def __enter__(self) -> "_FakeKey":
            return self

        def __exit__(self, *_exc_info: object) -> bool:
            return False

    def _open_key(root: object, _subkey: str) -> _FakeKey:
        return _FakeKey(root)

    def _query_value_ex(key: _FakeKey, _value_name: str) -> tuple[str, int]:
        if key.root is fake_winreg.HKEY_LOCAL_MACHINE:
            return (r"C:\MachineA;C:\MachineB", 0)
        return (r"C:\UserBin", 0)

    fake_winreg.OpenKey = _open_key
    fake_winreg.QueryValueEx = _query_value_ex

    monkeypatch.setitem(sys.modules, "winreg", fake_winreg)
    monkeypatch.setattr(cli_main.sys, "platform", "win32")
    monkeypatch.setattr(cli_main.os, "pathsep", ":")

    assert cli_main._doctor_fresh_shell_path_value() == r"C:\MachineA;C:\MachineB;C:\UserBin"


def test_doctor_json_reports_foreign_first_path_tg_remediation(monkeypatch, tmp_path: Path) -> None:
    foreign_tg = tmp_path / "Python314" / "Scripts" / "tg.exe"
    managed_tg = tmp_path / ".tensor-grep" / "bin" / "tg.exe"
    foreign_tg.parent.mkdir(parents=True)
    managed_tg.parent.mkdir(parents=True)
    foreign_tg.write_text("foreign\n", encoding="utf-8")
    managed_tg.write_text("managed\n", encoding="utf-8")

    monkeypatch.setattr("tensor_grep.cli.main._doctor_installed_version", lambda: "1.9.4")
    monkeypatch.setattr("tensor_grep.cli.main.resolve_native_tg_binary", lambda: managed_tg)
    monkeypatch.setattr(
        "tensor_grep.cli.main._doctor_rust_binary_version",
        lambda _binary: "tg 1.9.4",
    )
    monkeypatch.setattr(
        "tensor_grep.cli.main._doctor_rust_core_extension_available",
        lambda: True,
        raising=False,
    )
    monkeypatch.setattr(
        "tensor_grep.cli.main._doctor_session_daemon_status",
        lambda path: {"running": False},
    )
    monkeypatch.setattr(
        "tensor_grep.cli.main._doctor_lsp_provider_statuses",
        lambda path: [],
    )
    monkeypatch.setattr(
        "tensor_grep.cli.main._doctor_path_tg_candidates",
        lambda: [
            {"path": str(foreign_tg), "version": "Together CLI (v2.12.0)"},
            {"path": str(managed_tg), "version": "tg 1.9.4"},
        ],
    )
    monkeypatch.setattr(
        "tensor_grep.cli.main._doctor_fresh_shell_path_tg_candidates",
        lambda: [
            {"path": str(foreign_tg), "version": "Together CLI (v2.12.0)"},
            {"path": str(managed_tg), "version": "tg 1.9.4"},
        ],
        raising=False,
    )

    result = CliRunner().invoke(app, ["doctor", str(tmp_path), "--json", "--no-lsp"])

    assert result.exit_code == 0
    payload = json.loads(result.stdout)
    assert payload["path_tg_first_launcher_kind"] == "foreign"
    assert payload["fresh_shell_path_tg_first_launcher_kind"] == "foreign"
    assert payload["path_tg_first_is_foreign"] is True
    assert payload["fresh_shell_path_tg_first_is_foreign"] is True
    assert "Together CLI" in payload["path_tg_foreign_warning"]
    assert str(foreign_tg) in payload["path_tg_foreign_warning"]
    assert str(managed_tg.parent) in payload["path_tg_foreign_remediation"]
    assert "delete" not in payload["path_tg_foreign_remediation"].lower()


def test_doctor_tg_candidate_version_sanitizes_sidecar_python_env(
    monkeypatch, tmp_path: Path
) -> None:

    candidate = tmp_path / "Python314" / "Scripts" / "tg.exe"
    candidate.parent.mkdir(parents=True)
    candidate.write_text("foreign\n", encoding="utf-8")
    seen: dict[str, object] = {}

    def _fake_run(command, **kwargs):  # type: ignore[no-untyped-def]
        seen["command"] = command
        seen["env"] = kwargs.get("env")
        return subprocess.CompletedProcess(command, 0, stdout="2.12.0\n", stderr="")

    monkeypatch.setenv("PYTHONHOME", r"C:\managed-sidecar-python")
    monkeypatch.setenv("PYTHONPATH", r"C:\managed-sidecar-python\Lib")
    monkeypatch.setenv("VIRTUAL_ENV", r"C:\managed-sidecar")
    monkeypatch.setenv("__PYVENV_LAUNCHER__", r"C:\managed-sidecar\python.exe")
    monkeypatch.setattr(cli_main.subprocess, "run", _fake_run)

    assert cli_main._doctor_tg_candidate_version(candidate) == "2.12.0"
    assert seen["command"] == [str(candidate), "--version"]
    env = seen["env"]
    assert isinstance(env, dict)
    assert "PYTHONHOME" not in env
    assert "PYTHONPATH" not in env
    assert "VIRTUAL_ENV" not in env
    assert "__PYVENV_LAUNCHER__" not in env


def test_doctor_launcher_kind_classifies_virtualenv_console_entrypoint(tmp_path: Path) -> None:

    venv_tg = tmp_path / ".venv" / "Scripts" / "tg.exe"
    python_scripts_tg = tmp_path / "Python314" / "Scripts" / "tg.exe"

    assert cli_main._doctor_tg_launcher_kind(str(venv_tg)) == "python-entrypoint"
    assert (
        cli_main._doctor_tg_launcher_kind(str(python_scripts_tg), "tensor-grep 1.10.9")
        == "python-entrypoint"
    )
    assert cli_main._doctor_tg_launcher_kind(str(python_scripts_tg), "tg 1.10.9") == "native-exe"


def test_doctor_launcher_kind_classifies_windows_com_bridge(tmp_path: Path) -> None:

    bridge_tg = tmp_path / "Python314" / "Scripts" / "tg.com"

    assert cli_main._doctor_tg_launcher_kind(str(bridge_tg), "tg 1.9.5") == "native-exe"
    assert cli_main._doctor_tg_launcher_kind(str(bridge_tg), "2.12.0") == "foreign"
    assert cli_main._doctor_tg_launcher_kind(str(bridge_tg), None) == "foreign"


def test_doctor_json_with_unversioned_bridge_emits_foreign_warning(
    monkeypatch, tmp_path: Path
) -> None:
    foreign_tg = tmp_path / "Python314" / "Scripts" / "tg.com"
    managed_tg = tmp_path / ".tensor-grep" / "bin" / "tg.exe"
    foreign_tg.parent.mkdir(parents=True)
    managed_tg.parent.mkdir(parents=True)
    foreign_tg.write_text("bridge\n", encoding="utf-8")
    managed_tg.write_text("managed\n", encoding="utf-8")

    monkeypatch.setattr("tensor_grep.cli.main._doctor_installed_version", lambda: "1.9.4")
    monkeypatch.setattr("tensor_grep.cli.main.resolve_native_tg_binary", lambda: managed_tg)
    monkeypatch.setattr(
        "tensor_grep.cli.main._doctor_rust_binary_version",
        lambda _binary: "tg 1.9.4",
    )
    monkeypatch.setattr(
        "tensor_grep.cli.main._doctor_rust_core_extension_available",
        lambda: True,
        raising=False,
    )
    monkeypatch.setattr(
        "tensor_grep.cli.main._doctor_session_daemon_status",
        lambda path: {"running": False},
    )
    monkeypatch.setattr(
        "tensor_grep.cli.main._doctor_lsp_provider_statuses",
        lambda path: [],
    )
    monkeypatch.setattr(
        "tensor_grep.cli.main._doctor_path_tg_candidates",
        lambda: [
            {"path": str(foreign_tg), "version": None},
            {"path": str(managed_tg), "version": "tg 1.9.4"},
        ],
    )
    monkeypatch.setattr(
        "tensor_grep.cli.main._doctor_fresh_shell_path_tg_candidates",
        lambda: [
            {"path": str(managed_tg), "version": "tg 1.9.4"},
        ],
        raising=False,
    )

    result = CliRunner().invoke(app, ["doctor", str(tmp_path), "--json", "--no-lsp"])

    assert result.exit_code == 0
    payload = json.loads(result.stdout)
    assert payload["path_tg_first_launcher_kind"] == "foreign"
    assert payload["path_tg_first_version"] is None
    assert payload["path_tg_first_is_foreign"] is True
    assert payload["path_tg_first_version_matches"] is None
    assert "not tensor-grep" in payload["path_tg_foreign_warning"]
    assert "no recognizable --version output" in payload["path_tg_foreign_warning"]
    assert payload["path_tg_foreign_remediation"] is not None
    assert str(managed_tg.parent) in payload["path_tg_foreign_remediation"]


def test_doctor_json_warns_when_current_path_hits_compat_shim_before_fresh_native(
    monkeypatch, tmp_path: Path
) -> None:
    shim_tg = tmp_path / "bin" / "tg.cmd"
    native_tg = tmp_path / ".tensor-grep" / "bin" / "tg.exe"
    shim_tg.parent.mkdir(parents=True)
    native_tg.parent.mkdir(parents=True)
    shim_tg.write_text("@echo off\n", encoding="utf-8")
    native_tg.write_text("native\n", encoding="utf-8")

    monkeypatch.setattr("tensor_grep.cli.main._doctor_installed_version", lambda: "1.8.31")
    monkeypatch.setattr("tensor_grep.cli.main.resolve_native_tg_binary", lambda: native_tg)
    monkeypatch.setattr(
        "tensor_grep.cli.main._doctor_rust_binary_version",
        lambda _binary: "tensor-grep 1.8.31",
    )
    monkeypatch.setattr(
        "tensor_grep.cli.main._doctor_rust_core_extension_available",
        lambda: True,
        raising=False,
    )
    monkeypatch.setattr(
        "tensor_grep.cli.main._doctor_session_daemon_status",
        lambda path: {"running": False},
    )
    monkeypatch.setattr(
        "tensor_grep.cli.main._doctor_lsp_provider_statuses",
        lambda path: [],
    )
    monkeypatch.setattr(
        "tensor_grep.cli.main._doctor_path_tg_candidates",
        lambda: [
            {"path": str(shim_tg), "version": "tensor-grep 1.8.31"},
            {"path": str(native_tg), "version": "tensor-grep 1.8.31"},
        ],
    )
    monkeypatch.setattr(
        "tensor_grep.cli.main._doctor_fresh_shell_path_tg_candidates",
        lambda: [{"path": str(native_tg), "version": "tensor-grep 1.8.31"}],
        raising=False,
    )

    result = CliRunner().invoke(app, ["doctor", str(tmp_path), "--json", "--no-lsp"])

    assert result.exit_code == 0
    payload = json.loads(result.stdout)
    assert payload["path_tg_first_launcher_kind"] == "cmd-shim"
    assert payload["fresh_shell_path_tg_first_launcher_kind"] == "managed-native"
    assert payload["fresh_shell_path_tg_first_version_matches"] is True
    assert (
        "current process PATH resolves a compatibility shim" in payload["path_tg_launcher_warning"]
    )
    assert "restart the shell" in payload["path_tg_launcher_warning"]


def test_doctor_json_reports_mcp_stdio_launcher_warning_for_powershell_shim(
    monkeypatch, tmp_path: Path
) -> None:
    shim_tg = tmp_path / "bin" / "tg.ps1"
    native_tg = tmp_path / ".tensor-grep" / "bin" / "tg.exe"
    shim_tg.parent.mkdir(parents=True)
    native_tg.parent.mkdir(parents=True)
    shim_tg.write_text("& $PSScriptRoot/tg.exe @args\n", encoding="utf-8")
    native_tg.write_text("native\n", encoding="utf-8")

    monkeypatch.setattr("tensor_grep.cli.main._doctor_installed_version", lambda: "1.12.52")
    monkeypatch.setattr("tensor_grep.cli.main.resolve_native_tg_binary", lambda: native_tg)
    monkeypatch.setattr(
        "tensor_grep.cli.main._doctor_rust_binary_version",
        lambda _binary: "tg 1.12.52",
    )
    monkeypatch.setattr(
        "tensor_grep.cli.main._doctor_rust_core_extension_available",
        lambda: True,
        raising=False,
    )
    monkeypatch.setattr(
        "tensor_grep.cli.main._doctor_session_daemon_status",
        lambda path: {"running": False},
    )
    monkeypatch.setattr("tensor_grep.cli.main._doctor_lsp_provider_statuses", lambda path: [])
    monkeypatch.setattr(
        "tensor_grep.cli.main._doctor_path_tg_candidates",
        lambda: [
            {"path": str(shim_tg), "version": "tensor-grep 1.12.52"},
            {"path": str(native_tg), "version": "tg 1.12.52"},
        ],
    )
    monkeypatch.setattr(
        "tensor_grep.cli.main._doctor_fresh_shell_path_tg_candidates",
        lambda: [{"path": str(native_tg), "version": "tg 1.12.52"}],
        raising=False,
    )
    monkeypatch.setattr(
        "tensor_grep.cli.main._doctor_python_subprocess_path_tg_candidate",
        lambda path_value=None: {"path": str(shim_tg), "version": "tensor-grep 1.12.52"},
        raising=False,
    )

    result = CliRunner().invoke(app, ["doctor", str(tmp_path), "--json", "--no-lsp"])

    assert result.exit_code == 0
    payload = json.loads(result.stdout)
    assert payload["path_tg_first_launcher_kind"] == "powershell-shim"
    assert payload["python_subprocess_path_tg_first_launcher_kind"] == "powershell-shim"
    warning = payload["mcp_stdio_launcher_warning"]
    assert "MCP stdio" in warning
    assert "Start-Process" in warning
    assert "managed native tg.exe directly" in warning
    assert "not `tg.ps1`" in warning
    assert str(native_tg) in warning
    assert "pwsh -NoProfile -File" in warning
    assert str(shim_tg) in warning


def test_doctor_json_reports_mcp_stdio_launcher_warning_from_candidate_native(
    monkeypatch, tmp_path: Path
) -> None:
    shim_tg = tmp_path / "bin" / "tg.ps1"
    native_tg = tmp_path / "bin" / "tg.exe"
    shim_tg.parent.mkdir(parents=True)
    shim_tg.write_text("& $PSScriptRoot/tg.exe @args\n", encoding="utf-8")
    native_tg.write_text("native\n", encoding="utf-8")

    monkeypatch.setattr("tensor_grep.cli.main._doctor_installed_version", lambda: "1.13.1")
    monkeypatch.setattr("tensor_grep.cli.main.resolve_native_tg_binary", lambda: None)
    monkeypatch.setattr(
        "tensor_grep.cli.main._doctor_rust_core_extension_available",
        lambda: True,
        raising=False,
    )
    monkeypatch.setattr(
        "tensor_grep.cli.main._doctor_session_daemon_status",
        lambda path: {"running": False},
    )
    monkeypatch.setattr("tensor_grep.cli.main._doctor_lsp_provider_statuses", lambda path: [])
    monkeypatch.setattr(
        "tensor_grep.cli.main._doctor_path_tg_candidates",
        lambda: [
            {"path": str(shim_tg), "version": "tensor-grep 1.13.1"},
            {"path": str(native_tg), "version": "tg 1.13.1"},
        ],
    )
    monkeypatch.setattr(
        "tensor_grep.cli.main._doctor_fresh_shell_path_tg_candidates",
        lambda: [
            {"path": str(shim_tg), "version": "tensor-grep 1.13.1"},
            {"path": str(native_tg), "version": "tg 1.13.1"},
        ],
        raising=False,
    )
    monkeypatch.setattr(
        "tensor_grep.cli.main._doctor_python_subprocess_path_tg_candidate",
        lambda path_value=None: {"path": str(shim_tg), "version": "tensor-grep 1.13.1"},
        raising=False,
    )

    result = CliRunner().invoke(app, ["doctor", str(tmp_path), "--json", "--no-lsp"])

    assert result.exit_code == 0
    payload = json.loads(result.stdout)
    warning = payload["mcp_stdio_launcher_warning"]
    assert "Start-Process" in warning
    assert "not `tg.ps1`" in warning
    assert str(native_tg) in warning
    assert str(shim_tg) in warning


def test_doctor_mcp_stdio_warning_flags_ps1_path_candidate_without_version(
    tmp_path: Path,
) -> None:
    native_tg = tmp_path / ".tensor-grep" / "bin" / "tg.exe"
    shim_tg = tmp_path / "bin" / "tg.ps1"
    native_tg.parent.mkdir(parents=True)
    shim_tg.parent.mkdir(parents=True)
    native_tg.write_text("native\n", encoding="utf-8")
    shim_tg.write_text("& $PSScriptRoot/tg.exe @args\n", encoding="utf-8")

    warning = cli_main._doctor_mcp_stdio_launcher_warning(
        native_tg_binary=native_tg,
        launchers=[("PATH", "managed-native", str(native_tg))],
        path_tg_candidates=[{"path": str(shim_tg), "version": None}],
    )

    assert warning is not None
    assert "Start-Process" in warning
    assert "not `tg.ps1`" in warning
    assert str(native_tg) in warning
    assert str(shim_tg) in warning


def test_doctor_text_reports_mcp_stdio_launcher_warning(monkeypatch, tmp_path: Path) -> None:
    shim_tg = tmp_path / "bin" / "tg.ps1"
    native_tg = tmp_path / ".tensor-grep" / "bin" / "tg.exe"
    shim_tg.parent.mkdir(parents=True)
    native_tg.parent.mkdir(parents=True)
    shim_tg.write_text("& $PSScriptRoot/tg.exe @args\n", encoding="utf-8")
    native_tg.write_text("native\n", encoding="utf-8")

    monkeypatch.setattr("tensor_grep.cli.main._doctor_installed_version", lambda: "1.12.52")
    monkeypatch.setattr("tensor_grep.cli.main.resolve_native_tg_binary", lambda: native_tg)
    monkeypatch.setattr(
        "tensor_grep.cli.main._doctor_rust_binary_version",
        lambda _binary: "tg 1.12.52",
    )
    monkeypatch.setattr(
        "tensor_grep.cli.main._doctor_rust_core_extension_available",
        lambda: True,
        raising=False,
    )
    monkeypatch.setattr(
        "tensor_grep.cli.main._doctor_session_daemon_status",
        lambda path: {"running": False},
    )
    monkeypatch.setattr("tensor_grep.cli.main._doctor_lsp_provider_statuses", lambda path: [])
    monkeypatch.setattr(
        "tensor_grep.cli.main._doctor_path_tg_candidates",
        lambda: [{"path": str(shim_tg), "version": "tensor-grep 1.12.52"}],
    )
    monkeypatch.setattr(
        "tensor_grep.cli.main._doctor_fresh_shell_path_tg_candidates",
        lambda: [{"path": str(native_tg), "version": "tg 1.12.52"}],
        raising=False,
    )
    monkeypatch.setattr(
        "tensor_grep.cli.main._doctor_python_subprocess_path_tg_candidate",
        lambda path_value=None: None,
        raising=False,
    )

    result = CliRunner().invoke(app, ["doctor", str(tmp_path), "--no-lsp"])

    assert result.exit_code == 0
    assert "mcp_stdio_launcher_warning:" in result.stdout
    assert "Start-Process" in result.stdout
    assert "managed native tg.exe directly" in result.stdout
    assert "not `tg.ps1`" in result.stdout
    assert "pwsh -NoProfile -File" in result.stdout


def test_doctor_json_reports_python_subprocess_foreign_tg_exe(monkeypatch, tmp_path: Path) -> None:
    foreign_tg = tmp_path / "Python314" / "Scripts" / "tg.exe"
    managed_tg = tmp_path / ".tensor-grep" / "bin" / "tg.exe"
    foreign_tg.parent.mkdir(parents=True)
    managed_tg.parent.mkdir(parents=True)
    foreign_tg.write_text("foreign\n", encoding="utf-8")
    managed_tg.write_text("managed\n", encoding="utf-8")

    monkeypatch.setattr("tensor_grep.cli.main._doctor_installed_version", lambda: "1.10.5")
    monkeypatch.setattr("tensor_grep.cli.main.resolve_native_tg_binary", lambda: managed_tg)
    monkeypatch.setattr(
        "tensor_grep.cli.main._doctor_rust_binary_version",
        lambda _binary: "tg 1.10.5",
    )
    monkeypatch.setattr(
        "tensor_grep.cli.main._doctor_rust_core_extension_available",
        lambda: True,
        raising=False,
    )
    monkeypatch.setattr(
        "tensor_grep.cli.main._doctor_session_daemon_status",
        lambda path: {"running": False},
    )
    monkeypatch.setattr("tensor_grep.cli.main._doctor_lsp_provider_statuses", lambda path: [])
    monkeypatch.setattr(
        "tensor_grep.cli.main._doctor_path_tg_candidates",
        lambda path_value=None: [
            {"path": str(managed_tg), "version": "tg 1.10.5"},
            {"path": str(foreign_tg), "version": "Together CLI (v2.12.0)"},
        ],
    )
    monkeypatch.setattr(
        "tensor_grep.cli.main._doctor_fresh_shell_path_tg_candidates",
        lambda: [{"path": str(managed_tg), "version": "tg 1.10.5"}],
        raising=False,
    )
    monkeypatch.setattr(
        "tensor_grep.cli.main._doctor_python_subprocess_path_tg_candidate",
        lambda path_value=None: {
            "path": str(foreign_tg),
            "version": "Together CLI (v2.12.0)",
        },
        raising=False,
    )

    result = CliRunner().invoke(app, ["doctor", str(tmp_path), "--json", "--no-lsp"])

    assert result.exit_code == 0
    payload = json.loads(result.stdout)
    assert payload["python_subprocess_path_tg_first_launcher_kind"] == "foreign"
    assert payload["python_subprocess_path_tg_first_is_foreign"] is True
    assert payload["python_subprocess_path_tg_first_version_matches"] is False
    assert "Python subprocess" in payload["python_subprocess_path_tg_foreign_warning"]
    assert str(foreign_tg) in payload["python_subprocess_path_tg_foreign_warning"]
    assert str(managed_tg.parent) in payload["python_subprocess_path_tg_foreign_remediation"]
    assert "Machine PATH" in payload["python_subprocess_path_tg_foreign_remediation"]
    assert (
        "repair-launcher --allow-foreign-rename"
        in payload["python_subprocess_path_tg_foreign_remediation"]
    )
    assert "delete" not in payload["python_subprocess_path_tg_foreign_remediation"].lower()


def test_lsp_setup_runs_managed_provider_installer(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    seen: dict[str, object] = {}

    def _fake_install(
        *,
        python_executable: str,
        managed_root: Path | None,
        include_toolchain_providers: bool,
    ) -> dict[str, object]:
        seen["python_executable"] = python_executable
        seen["managed_root"] = managed_root
        seen["include_toolchain_providers"] = include_toolchain_providers
        return {
            "managed_provider_root": str(tmp_path / "providers"),
            "include_toolchain_providers": include_toolchain_providers,
            "node": {"installed": True},
            "providers": {
                "python": {
                    "command": [str(tmp_path / "providers" / "pyright-langserver"), "--stdio"],
                    "available": True,
                    "command_source": "managed",
                },
                "php": {
                    "command": [str(tmp_path / "providers" / "intelephense"), "--stdio"],
                    "available": True,
                    "command_source": "managed",
                },
                "go": {
                    "command": [str(tmp_path / "providers" / "gopls")],
                    "available": True,
                    "command_source": "managed",
                },
            },
        }

    monkeypatch.setattr(
        "tensor_grep.cli.lsp_provider_setup.install_managed_lsp_providers", _fake_install
    )

    runner = CliRunner()
    result = runner.invoke(app, ["lsp-setup", "--json"])

    assert result.exit_code == 0
    payload = json.loads(result.stdout)
    assert payload["managed_provider_root"] == str(tmp_path / "providers")
    assert payload["providers"]["python"]["command"][0].endswith("pyright-langserver")
    assert payload["providers"]["php"]["command"][0].endswith("intelephense")
    assert payload["providers"]["go"]["command"][0].endswith("gopls")
    assert seen["python_executable"] == sys.executable
    assert seen["managed_root"] is None
    assert seen["include_toolchain_providers"] is False


def test_lsp_setup_can_enable_toolchain_provider_install(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    seen: dict[str, object] = {}

    def _fake_install(
        *,
        python_executable: str,
        managed_root: Path | None,
        include_toolchain_providers: bool,
    ) -> dict[str, object]:
        seen["include_toolchain_providers"] = include_toolchain_providers
        return {
            "managed_provider_root": str(tmp_path / "providers"),
            "include_toolchain_providers": include_toolchain_providers,
            "node": {"installed": True},
            "providers": {},
        }

    monkeypatch.setattr(
        "tensor_grep.cli.lsp_provider_setup.install_managed_lsp_providers", _fake_install
    )

    runner = CliRunner()
    result = runner.invoke(app, ["lsp-setup", "--json", "--include-toolchain-providers"])

    assert result.exit_code == 0
    assert seen["include_toolchain_providers"] is True


def test_lsp_setup_json_exits_nonzero_when_install_errors(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    def _fake_install(
        *,
        python_executable: str,
        managed_root: Path | None,
        include_toolchain_providers: bool,
    ) -> dict[str, object]:
        return {
            "managed_provider_root": str(tmp_path / "providers"),
            "include_toolchain_providers": include_toolchain_providers,
            "node": {"installed": False},
            "providers": {
                "python": {
                    "command": None,
                    "available": False,
                    "command_source": "missing",
                    "install_error": "network unavailable",
                }
            },
            "install_errors": {"node": "network unavailable"},
        }

    monkeypatch.setattr(
        "tensor_grep.cli.lsp_provider_setup.install_managed_lsp_providers", _fake_install
    )

    runner = CliRunner()
    result = runner.invoke(app, ["lsp-setup", "--json"])

    assert result.exit_code == 1
    payload = json.loads(result.stdout)
    assert payload["install_errors"]["node"] == "network unavailable"


def test_doctor_json_passes_non_default_config_to_payload_builder(
    monkeypatch, tmp_path: Path
) -> None:
    seen: dict[str, object] = {}

    def _fake_build(path: str, *, config: str | None, with_lsp: bool) -> dict[str, object]:
        seen.update({"path": path, "config": config, "with_lsp": with_lsp})
        return {"ok": True}

    monkeypatch.setattr("tensor_grep.cli.main._build_doctor_payload", _fake_build)

    runner = CliRunner()
    result = runner.invoke(
        app,
        ["doctor", str(tmp_path), "--config", "configs/custom.yml", "--json", "--no-lsp"],
    )

    assert result.exit_code == 0
    assert json.loads(result.stdout) == {"ok": True}
    assert seen == {
        "path": str(tmp_path),
        "config": "configs/custom.yml",
        "with_lsp": False,
    }


def test_doctor_text_reports_disabled_lsp_and_stopped_daemon(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.setattr("tensor_grep.cli.main._doctor_installed_version", lambda: "1.2.3")
    monkeypatch.setattr("tensor_grep.cli.main.resolve_native_tg_binary", lambda: None)
    monkeypatch.setattr(
        "tensor_grep.cli.main._doctor_session_daemon_status",
        lambda path: {"running": False},
    )

    runner = CliRunner()
    result = runner.invoke(app, ["doctor", str(tmp_path), "--no-lsp"])

    assert result.exit_code == 0
    assert "tensor-grep doctor" in result.stdout
    assert "version: 1.2.3" in result.stdout
    assert "native_tg_binary: missing" in result.stdout
    assert "session_daemon: stopped" in result.stdout
    assert "lsp_providers: disabled" in result.stdout
    assert "shell_escaping_guidance:" in result.stdout
    assert "PowerShell double quotes expand $NAME" in result.stdout
    assert "cmd.exe metacharacters" in result.stdout


def test_doctor_text_reports_lsp_health_and_proof_fields(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.setattr("tensor_grep.cli.main._doctor_installed_version", lambda: "1.2.3")
    monkeypatch.setattr("tensor_grep.cli.main.resolve_native_tg_binary", lambda: None)
    monkeypatch.setattr(
        "tensor_grep.cli.main._doctor_session_daemon_status",
        lambda path: {"running": False},
    )
    monkeypatch.setenv("TG_DOCTOR_LSP_PROBE_TIMEOUT_SECONDS", "4.5")
    monkeypatch.setattr(
        "tensor_grep.cli.main._doctor_lsp_provider_statuses",
        lambda path: [
            {
                "language": "python",
                "available": True,
                "running": False,
                "command": ["pyright-langserver", "--stdio"],
                "command_source": "managed",
                "managed_provider_root": str(tmp_path / "providers"),
                "last_error": None,
                "health_status": "available_unverified",
                "health_check": "not_run",
                "lsp_proof": False,
                "not_lsp_proof_reason": "Provider binary is available but health was not verified.",
            }
        ],
    )

    result = CliRunner().invoke(app, ["doctor", str(tmp_path)])

    assert result.exit_code == 0
    assert "lsp_probe_timeout_seconds: 4.5" in result.stdout
    assert "health=available_unverified" in result.stdout
    assert "health_check=not_run" in result.stdout
    assert "lsp_proof=False" in result.stdout
    assert "not_lsp_proof_reason=" in result.stdout


def test_doctor_json_explains_rust_core_extension_when_standalone_binary_missing(
    monkeypatch, tmp_path: Path
) -> None:
    monkeypatch.setattr("tensor_grep.cli.main._doctor_installed_version", lambda: "1.8.2")
    monkeypatch.setattr("tensor_grep.cli.main.resolve_native_tg_binary", lambda: None)
    monkeypatch.setattr(
        "tensor_grep.cli.main._doctor_rust_core_extension_available",
        lambda: True,
        raising=False,
    )
    monkeypatch.setattr(
        "tensor_grep.cli.main._doctor_session_daemon_status",
        lambda path: {"running": False},
    )
    monkeypatch.setattr("tensor_grep.cli.main._doctor_lsp_provider_statuses", lambda path: [])

    runner = CliRunner()
    result = runner.invoke(app, ["doctor", str(tmp_path), "--json", "--no-lsp"])

    assert result.exit_code == 0
    payload = json.loads(result.stdout)
    assert payload["native_tg_binary"] is None
    assert payload["native_tg_binary_exists"] is False
    assert payload["rust_core_extension_available"] is True
    assert payload["search_acceleration_backend"] == "rust-core-extension"


def test_doctor_native_frontdoor_flavor_mismatch_note_helper() -> None:
    assert (
        cli_main._doctor_native_frontdoor_flavor_mismatch_note(
            installed_flavor=None, requested_flavor=None
        )
        is None
    )
    assert (
        cli_main._doctor_native_frontdoor_flavor_mismatch_note(
            installed_flavor="cpu", requested_flavor="cpu"
        )
        is None
    )
    assert (
        cli_main._doctor_native_frontdoor_flavor_mismatch_note(
            installed_flavor="cpu", requested_flavor=None
        )
        is None
    )
    note = cli_main._doctor_native_frontdoor_flavor_mismatch_note(
        installed_flavor="cpu", requested_flavor="nvidia"
    )
    assert note is not None
    assert "cpu" in note
    assert "nvidia" in note


def test_doctor_json_includes_native_frontdoor_flavor_fields_when_metadata_present(
    monkeypatch, tmp_path: Path
) -> None:
    native_tg = tmp_path / ".tensor-grep" / "bin" / "tg.exe"
    native_tg.parent.mkdir(parents=True, exist_ok=True)
    native_tg.write_text("native", encoding="utf-8")
    metadata_path = native_tg.with_name("tg-native-metadata.json")
    metadata_path.write_text(
        json.dumps({
            "artifact": "tensor_grep_native_frontdoor_metadata",
            "asset_flavor": "cpu",
            "requested_asset_flavor": "nvidia",
            "asset_name": "tg-windows-amd64-cpu.exe",
            "version": "9.9.9",
        }),
        encoding="utf-8",
    )
    monkeypatch.setattr("tensor_grep.cli.main._doctor_installed_version", lambda: "9.9.9")
    monkeypatch.setattr("tensor_grep.cli.main.resolve_native_tg_binary", lambda: native_tg)
    monkeypatch.setattr(
        "tensor_grep.cli.main._doctor_session_daemon_status",
        lambda path: {"running": False},
    )
    monkeypatch.setattr(
        "tensor_grep.cli.main._doctor_gpu_search_runtime_probe",
        lambda binary: {"status": "not_run"},
    )

    result = CliRunner().invoke(app, ["doctor", str(tmp_path), "--json", "--no-lsp"])

    assert result.exit_code == 0
    payload = json.loads(result.stdout)
    assert payload["native_frontdoor_flavor"] == "cpu"
    assert payload["native_frontdoor_requested_flavor"] == "nvidia"
    assert payload["native_frontdoor_asset_name"] == "tg-windows-amd64-cpu.exe"
    assert payload["native_frontdoor_metadata_status"] == "present"
    note = payload["native_frontdoor_flavor_mismatch_note"]
    assert note is not None
    assert "cpu" in note
    assert "nvidia" in note


def test_doctor_json_native_frontdoor_flavor_fields_absent_without_metadata_file(
    monkeypatch, tmp_path: Path
) -> None:
    native_tg = tmp_path / "tg.exe"
    native_tg.write_text("native", encoding="utf-8")
    monkeypatch.setattr("tensor_grep.cli.main._doctor_installed_version", lambda: "9.9.9")
    monkeypatch.setattr("tensor_grep.cli.main.resolve_native_tg_binary", lambda: native_tg)
    monkeypatch.setattr(
        "tensor_grep.cli.main._doctor_session_daemon_status",
        lambda path: {"running": False},
    )
    monkeypatch.setattr(
        "tensor_grep.cli.main._doctor_gpu_search_runtime_probe",
        lambda binary: {"status": "not_run"},
    )

    result = CliRunner().invoke(app, ["doctor", str(tmp_path), "--json", "--no-lsp"])

    assert result.exit_code == 0
    payload = json.loads(result.stdout)
    assert payload["native_frontdoor_flavor"] is None
    assert payload["native_frontdoor_requested_flavor"] is None
    assert payload["native_frontdoor_metadata_status"] is None
    assert payload["native_frontdoor_flavor_mismatch_note"] is None


def test_doctor_json_native_frontdoor_flavor_fields_none_when_binary_missing(
    monkeypatch, tmp_path: Path
) -> None:
    monkeypatch.setattr("tensor_grep.cli.main._doctor_installed_version", lambda: "9.9.9")
    monkeypatch.setattr("tensor_grep.cli.main.resolve_native_tg_binary", lambda: None)
    monkeypatch.setattr(
        "tensor_grep.cli.main._doctor_session_daemon_status",
        lambda path: {"running": False},
    )

    result = CliRunner().invoke(app, ["doctor", str(tmp_path), "--json", "--no-lsp"])

    assert result.exit_code == 0
    payload = json.loads(result.stdout)
    assert payload["native_frontdoor_flavor"] is None
    assert payload["native_frontdoor_flavor_mismatch_note"] is None


def test_doctor_text_reports_native_frontdoor_flavor_mismatch(monkeypatch, tmp_path: Path) -> None:
    native_tg = tmp_path / ".tensor-grep" / "bin" / "tg.exe"
    native_tg.parent.mkdir(parents=True, exist_ok=True)
    native_tg.write_text("native", encoding="utf-8")
    metadata_path = native_tg.with_name("tg-native-metadata.json")
    metadata_path.write_text(
        json.dumps({
            "artifact": "tensor_grep_native_frontdoor_metadata",
            "asset_flavor": "cpu",
            "requested_asset_flavor": "nvidia",
            "asset_name": "tg-windows-amd64-cpu.exe",
            "version": "9.9.9",
        }),
        encoding="utf-8",
    )
    monkeypatch.setattr("tensor_grep.cli.main._doctor_installed_version", lambda: "9.9.9")
    monkeypatch.setattr("tensor_grep.cli.main.resolve_native_tg_binary", lambda: native_tg)
    monkeypatch.setattr(
        "tensor_grep.cli.main._doctor_session_daemon_status",
        lambda path: {"running": False},
    )
    monkeypatch.setattr(
        "tensor_grep.cli.main._doctor_gpu_search_runtime_probe",
        lambda binary: {"status": "not_run"},
    )

    result = CliRunner().invoke(app, ["doctor", str(tmp_path), "--no-lsp"])

    assert result.exit_code == 0
    assert "native_frontdoor_flavor: cpu requested=nvidia" in result.stdout
    assert "native_frontdoor_flavor_mismatch_note:" in result.stdout


def test_cli_should_parse_gpu_device_ids_into_search_config(monkeypatch):
    global _FAKE_WALK, _FAKE_BACKEND, _LAST_PIPELINE_CONFIG
    _FAKE_WALK = {".": ["a.log"]}
    _FAKE_BACKEND = _FakeBackend(
        results_by_file={
            "a.log": SearchResult(
                matches=[MatchLine(line_number=1, text="ERROR", file="a.log")],
                total_files=1,
                total_matches=1,
            )
        }
    )
    _LAST_PIPELINE_CONFIG = None
    _patch_cli_dependencies(monkeypatch)

    runner = CliRunner()
    result = runner.invoke(
        app,
        ["search", "ERROR -> eventually ERROR", ".", "--ltl", "--gpu-device-ids", "3,7,7"],
    )

    assert result.exit_code == 0
    assert _LAST_PIPELINE_CONFIG is not None
    assert _LAST_PIPELINE_CONFIG.gpu_device_ids == [3, 7]


def test_cli_should_fail_fast_on_invalid_gpu_device_ids(monkeypatch):
    global _FAKE_WALK, _FAKE_BACKEND
    _FAKE_WALK = {".": ["a.log"]}
    _FAKE_BACKEND = _FakeBackend(results_by_file={})
    _patch_cli_dependencies(monkeypatch)

    runner = CliRunner()
    result = runner.invoke(
        app,
        ["search", "ERROR", ".", "--gpu-device-ids", "0,foo"],
    )

    assert result.exit_code == 2
    assert "Invalid GPU device id 'foo'" in result.output


def test_warn_unavailable_gpu_device_ids_silent_when_all_ids_in_inventory(monkeypatch) -> None:
    import tensor_grep.core.hardware.device_detect as device_detect_mod

    monkeypatch.setattr(
        device_detect_mod.DeviceDetector, "enumerate_device_ids", lambda self: [0, 1]
    )
    captured: list[str] = []
    monkeypatch.setattr(
        cli_main.typer, "echo", lambda message="", **kwargs: captured.append(str(message))
    )

    cli_main._warn_unavailable_gpu_device_ids([0, 1])

    assert captured == []


def test_warn_unavailable_gpu_device_ids_warns_naming_available_ids_when_out_of_range(
    monkeypatch,
) -> None:
    import tensor_grep.core.hardware.device_detect as device_detect_mod

    monkeypatch.setattr(
        device_detect_mod.DeviceDetector, "enumerate_device_ids", lambda self: [0, 1]
    )
    captured: list[str] = []
    monkeypatch.setattr(
        cli_main.typer, "echo", lambda message="", **kwargs: captured.append(str(message))
    )

    cli_main._warn_unavailable_gpu_device_ids([0, 5])

    assert len(captured) == 1
    assert "5" in captured[0]
    assert "0, 1" in captured[0]


def test_warn_unavailable_gpu_device_ids_silent_when_inventory_cannot_be_enumerated(
    monkeypatch,
) -> None:
    """A CPU-only environment can't enumerate CUDA devices at all (empty inventory) -- warning
    here would be a FALSE claim that id X is invalid. Silence is the honest move; the native
    Rust classifier owns the authoritative rejection on an actual CUDA build."""
    import tensor_grep.core.hardware.device_detect as device_detect_mod

    monkeypatch.setattr(device_detect_mod.DeviceDetector, "enumerate_device_ids", lambda self: [])
    captured: list[str] = []
    monkeypatch.setattr(
        cli_main.typer, "echo", lambda message="", **kwargs: captured.append(str(message))
    )

    cli_main._warn_unavailable_gpu_device_ids([0, 99])

    assert captured == []


def test_warn_unavailable_gpu_device_ids_noop_for_empty_request(monkeypatch) -> None:
    import tensor_grep.core.hardware.device_detect as device_detect_mod

    def _fail(_self):
        raise AssertionError("must not probe hardware when no ids were requested")

    monkeypatch.setattr(device_detect_mod.DeviceDetector, "enumerate_device_ids", _fail)

    cli_main._warn_unavailable_gpu_device_ids(None)
    cli_main._warn_unavailable_gpu_device_ids([])


def test_warn_unavailable_gpu_device_ids_never_hard_fails_on_detector_error(monkeypatch) -> None:
    import tensor_grep.core.hardware.device_detect as device_detect_mod

    def _raise(_self):
        raise RuntimeError("simulated NVML failure")

    monkeypatch.setattr(device_detect_mod.DeviceDetector, "enumerate_device_ids", _raise)

    # Must not raise -- NEVER hard-fail is the explicit contract.
    cli_main._warn_unavailable_gpu_device_ids([0])


def test_cli_search_warns_when_gpu_device_id_out_of_local_inventory(monkeypatch) -> None:
    global _FAKE_WALK, _FAKE_BACKEND
    _FAKE_WALK = {".": ["a.log"]}
    # Task #289. This fixture used to be `results_by_file={}` -- zero matches -- while the test
    # asserted `exit_code == 0`. Exit 1 is the CORRECT three-state code for "not found"
    # (docs/CONTRACTS.md: 0 complete / 1 not-found / 2 incomplete), so the assertion was simply
    # wrong and the test failed locally on a contract tg was honouring.
    #
    # Flipping the assertion to 1 would have been the wrong fix: the warning this test exists to
    # cover promises "the search will still run", so the test has to supply a match and earn the
    # 0. Mirrors the sibling fixture in test_cli_should_parse_gpu_device_ids_into_search_config.
    _FAKE_BACKEND = _FakeBackend(
        results_by_file={
            "a.log": SearchResult(
                matches=[MatchLine(line_number=1, text="ERROR", file="a.log")],
                total_files=1,
                total_matches=1,
            )
        }
    )
    _patch_cli_dependencies(monkeypatch)

    import tensor_grep.core.hardware.device_detect as device_detect_mod

    monkeypatch.setattr(
        device_detect_mod.DeviceDetector, "enumerate_device_ids", lambda self: [0, 1]
    )

    runner = CliRunner()
    result = runner.invoke(app, ["search", "ERROR", ".", "--gpu-device-ids", "5"])

    # PREMISE: a match exists, so 0 means "searched and found", not "vacuously fine". Without the
    # fixture above this asserts a value the command is contractually right to refuse.
    assert result.exit_code == 0, result.output
    assert "5" in result.output
    assert "0, 1" in result.output


def test_cli_should_delegate_force_cpu_search_to_native_binary(monkeypatch):
    seen: dict[str, object] = {}

    monkeypatch.setattr("tensor_grep.cli.main.resolve_native_tg_binary", lambda: Path("tg.exe"))
    monkeypatch.setattr(
        "tensor_grep.cli.main._can_delegate_to_native_tg_search",
        lambda *args, **kwargs: True,
    )

    def _fake_run(cmd, check=False, timeout=None):
        seen["cmd"] = list(cmd)
        seen["check"] = check
        seen["timeout"] = timeout
        return subprocess.CompletedProcess(cmd, 0, stdout="", stderr="")

    monkeypatch.setattr("tensor_grep.cli.main.subprocess.run", _fake_run)

    runner = CliRunner()
    result = runner.invoke(
        app,
        ["search", "ERROR", ".", "--cpu", "-F", "-c", "-g", "*.log", "--no-ignore"],
    )

    assert result.exit_code == 0
    assert seen["cmd"] == [
        "tg.exe",
        "search",
        "--cpu",
        "-F",
        "-c",
        "-g",
        "*.log",
        "--no-ignore",
        "--",
        "ERROR",
        ".",
    ]
    assert seen["check"] is False
    assert isinstance(seen["timeout"], float) and seen["timeout"] > 0


def test_cli_should_force_cpu_pipeline_when_env_override_is_enabled(monkeypatch):
    global _FAKE_WALK, _FAKE_BACKEND, _LAST_PIPELINE_CONFIG
    _FAKE_WALK = {".": ["a.log"]}
    _FAKE_BACKEND = _FakeBackend(
        results_by_file={
            "a.log": SearchResult(
                matches=[MatchLine(line_number=1, text="ERROR", file="a.log")],
                total_files=1,
                total_matches=1,
            )
        }
    )
    _LAST_PIPELINE_CONFIG = None
    _patch_cli_dependencies(monkeypatch)
    monkeypatch.setenv("TG_FORCE_CPU", "1")
    monkeypatch.setattr("tensor_grep.cli.main.resolve_native_tg_binary", lambda: None)

    runner = CliRunner()
    result = runner.invoke(app, ["search", "ERROR", "."])

    assert result.exit_code == 0
    assert _LAST_PIPELINE_CONFIG is not None
    assert _LAST_PIPELINE_CONFIG.force_cpu is True


def test_cli_search_no_line_number_overrides_line_number(monkeypatch):
    global _FAKE_WALK, _FAKE_BACKEND, _LAST_PIPELINE_CONFIG
    _FAKE_WALK = {".": ["a.log"]}
    _FAKE_BACKEND = _FakeBackend(
        results_by_file={
            "a.log": SearchResult(
                matches=[MatchLine(line_number=1, text="ERROR", file="a.log")],
                total_files=1,
                total_matches=1,
            )
        }
    )
    _LAST_PIPELINE_CONFIG = None
    _patch_cli_dependencies(monkeypatch)
    monkeypatch.setattr("tensor_grep.cli.main.resolve_native_tg_binary", lambda: None)

    result = CliRunner().invoke(app, ["search", "-n", "-N", "--cpu", "ERROR", "."])

    assert result.exit_code == 0
    assert _LAST_PIPELINE_CONFIG is not None
    assert _LAST_PIPELINE_CONFIG.line_number is False


def test_cli_search_without_path_defaults_to_current_directory(monkeypatch):
    global _FAKE_WALK, _FAKE_BACKEND, _LAST_PIPELINE_CONFIG
    _FAKE_WALK = {".": ["a.log"]}
    _FAKE_BACKEND = _FakeBackend(
        results_by_file={
            "a.log": SearchResult(
                matches=[MatchLine(line_number=1, text="safeParseJSON", file="a.log")],
                matched_file_paths=["a.log"],
                match_counts_by_file={"a.log": 1},
                total_files=1,
                total_matches=1,
            )
        }
    )
    _LAST_PIPELINE_CONFIG = None
    _patch_cli_dependencies(monkeypatch)
    monkeypatch.setattr("tensor_grep.cli.main.resolve_native_tg_binary", lambda: None)

    runner = CliRunner()
    result = runner.invoke(app, ["search", "safeParseJSON", "--cpu"])

    assert result.exit_code == 0
    assert _LAST_PIPELINE_CONFIG is not None
    assert "safeParseJSON" in result.stdout
