import json
import sys
from pathlib import Path

from typer.testing import CliRunner

from tensor_grep.cli import main as cli_main
from tensor_grep.cli.doctor_payload import _render_doctor_payload
from tensor_grep.cli.main import app


def test_broken_managed_launcher_not_misclassified_as_foreign(tmp_path: Path) -> None:
    managed_exe = tmp_path / ".tensor-grep" / "bin" / "tg.exe"
    managed_posix = tmp_path / ".tensor-grep" / "bin" / "tg"
    nested_foreign = tmp_path / ".tensor-grep" / "tools" / "bin" / "tg.exe"

    assert cli_main._doctor_tg_launcher_kind(str(managed_exe), None) == "managed-native-broken"
    assert (
        cli_main._doctor_tg_launcher_kind(str(managed_exe), "error: failed to start")
        == "managed-native-broken"
    )
    assert cli_main._doctor_tg_launcher_kind(str(managed_posix), None) == "managed-native-broken"
    assert cli_main._doctor_tg_launcher_kind(str(managed_exe), "tg 1.138.20") == "managed-native"
    assert cli_main._doctor_tg_launcher_kind(str(nested_foreign), None) == "foreign"


def test_foreign_remediation_never_emits_self_referential_path_advice(
    monkeypatch, tmp_path: Path
) -> None:
    managed_tg = tmp_path / ".tensor-grep" / "bin" / "tg.exe"
    managed_tg.parent.mkdir(parents=True)
    managed_tg.write_text("broken", encoding="utf-8")

    # Broken managed launcher as foreign_path
    remediation = cli_main._doctor_tg_foreign_remediation(
        foreign_path=str(managed_tg),
        candidates=[{"path": str(managed_tg), "version": None}],
    )
    assert remediation is not None
    assert "earlier in PATH" not in remediation
    assert "--version" in remediation
    assert "reset ACLs or reinstall" in remediation

    # Case-insensitive resolved directory match on Windows
    monkeypatch.setattr(sys, "platform", "win32")
    same_dir_remediation = cli_main._doctor_tg_foreign_remediation(
        foreign_path=str(managed_tg).upper(),
        candidates=[{"path": str(managed_tg).lower(), "version": "tg 1.138.20"}],
    )
    assert same_dir_remediation is not None
    assert "earlier in PATH" not in same_dir_remediation
    assert "--version" in same_dir_remediation


def test_doctor_json_broken_managed_launcher_health_and_rendering(
    monkeypatch, tmp_path: Path
) -> None:
    managed_tg = tmp_path / ".tensor-grep" / "bin" / "tg.exe"
    managed_tg.parent.mkdir(parents=True)
    managed_tg.write_text("broken", encoding="utf-8")

    monkeypatch.setattr("tensor_grep.cli.main._doctor_installed_version", lambda: "1.138.20")
    monkeypatch.setattr("tensor_grep.cli.main.resolve_native_tg_binary", lambda: managed_tg)
    monkeypatch.setattr("tensor_grep.cli.main._doctor_rust_binary_version", lambda _b: None)
    monkeypatch.setattr(
        "tensor_grep.cli.main._doctor_session_daemon_status", lambda _p: {"running": False}
    )
    monkeypatch.setattr("tensor_grep.cli.main._latest_pypi_tensor_grep_version", lambda: "1.138.20")
    monkeypatch.setattr(
        "tensor_grep.cli.main._doctor_path_tg_candidates",
        lambda path_value=None: [{"path": str(managed_tg), "version": None}],
    )
    monkeypatch.setattr(
        "tensor_grep.cli.main._doctor_fresh_shell_path_tg_candidates",
        lambda: [{"path": str(managed_tg), "version": None}],
    )
    monkeypatch.setattr(
        "tensor_grep.cli.main._doctor_python_subprocess_path_tg_candidate",
        lambda path_value=None: {"path": str(managed_tg), "version": None},
    )

    result = CliRunner().invoke(app, ["doctor", str(tmp_path), "--json", "--fast"])
    assert result.exit_code == 0
    payload = json.loads(result.stdout)
    assert payload["path_tg_first_launcher_kind"] == "managed-native-broken"
    assert payload["path_tg_first_is_foreign"] is False
    assert payload["path_tg_foreign_warning"] is None
    assert payload["path_tg_foreign_remediation"] is None
    assert payload["installation_health"] == "managed_launcher_broken"
    assert "--version" in payload["path_tg_launcher_warning"]

    rendered = _render_doctor_payload(payload)
    assert "warning: installation_health=managed_launcher_broken" in rendered
    assert "earlier in PATH" not in rendered


def test_doctor_fast_skips_lsp_and_pypi_probes(monkeypatch, tmp_path: Path) -> None:
    def _fail_lsp(_path: str):
        raise AssertionError("LSP probe must not run under --fast")

    def _fail_pypi(*_args, **_kwargs):
        raise AssertionError("PyPI probe must not run under --fast")

    monkeypatch.setattr("tensor_grep.cli.main._doctor_installed_version", lambda: "1.138.20")
    monkeypatch.setattr("tensor_grep.cli.main.resolve_native_tg_binary", lambda: None)
    monkeypatch.setattr(
        "tensor_grep.cli.main._doctor_session_daemon_status", lambda _p: {"running": False}
    )
    monkeypatch.setattr("tensor_grep.cli.main._doctor_path_tg_candidates", lambda path=None: [])
    monkeypatch.setattr("tensor_grep.cli.main._doctor_fresh_shell_path_tg_candidates", lambda: [])
    monkeypatch.setattr(
        "tensor_grep.cli.main._doctor_python_subprocess_path_tg_candidate", lambda path=None: None
    )
    monkeypatch.setattr("tensor_grep.cli.main._doctor_lsp_provider_statuses", _fail_lsp)
    monkeypatch.setattr("tensor_grep.cli.main._latest_pypi_tensor_grep_version", _fail_pypi)

    result = CliRunner().invoke(app, ["doctor", str(tmp_path), "--fast", "--json"])
    assert result.exit_code == 0
    payload = json.loads(result.stdout)
    assert payload["lsp"]["enabled"] is False
    assert payload["pypi_latest"] is None
