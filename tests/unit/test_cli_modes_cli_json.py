import json
import os
import subprocess
import sys
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


def test_upgrade_does_not_unlink_owned_python_launcher_when_uninstall_fails(
    monkeypatch,
    tmp_path,
):
    native_binary = tmp_path / ".tensor-grep" / "bin" / "tg.exe"
    stale_dir = tmp_path / "Python314" / "Scripts"
    native_binary.parent.mkdir(parents=True)
    stale_dir.mkdir(parents=True)
    native_binary.write_text("managed native", encoding="utf-8")
    stale_tg = stale_dir / "tg.exe"
    stale_tg.write_text("stale tensor-grep launcher", encoding="utf-8")
    stale_python = stale_dir.parent / "python.exe"
    stale_python.write_text("", encoding="utf-8")
    package_location = stale_dir.parent / "Lib" / "site-packages"
    package_launcher = os.path.relpath(stale_tg, package_location)

    def _fake_candidate_version(path):
        candidate = Path(path)
        if candidate == native_binary:
            return "tg 0.33.0"
        if candidate == stale_tg:
            return "tensor-grep 0.32.0"
        return None

    def _fake_run(cmd, capture_output=True, text=True, timeout=None, **_kwargs):
        command = [str(part) for part in cmd]
        if command[:5] == [str(stale_python), "-m", "pip", "show", "-f"]:
            return subprocess.CompletedProcess(
                cmd,
                0,
                stdout=(
                    "Name: tensor-grep\n"
                    "Version: 0.32.0\n"
                    f"Location: {package_location}\n"
                    "Files:\n"
                    f"{package_launcher}\n"
                ),
                stderr="",
            )
        if command[:4] == [str(stale_python), "-m", "pip", "uninstall"]:
            return subprocess.CompletedProcess(cmd, 1, stdout="", stderr="permission denied\n")
        raise AssertionError(f"unexpected command: {command}")

    monkeypatch.setattr(sys, "platform", "win32")
    monkeypatch.setenv("PATH", f"{stale_dir};{native_binary.parent}")
    monkeypatch.setattr(cli_main, "_doctor_fresh_shell_path_value", lambda: "")
    monkeypatch.setattr(cli_main, "_doctor_tg_candidate_version", _fake_candidate_version)
    monkeypatch.setattr("subprocess.run", _fake_run)

    message = cli_main._remove_windows_stale_tensor_grep_python_launchers(
        "0.33.0",
        native_binary,
    )

    assert message is not None
    assert "WARNING: stale tensor-grep Python package launchers remain" in message
    assert "permission denied" in message
    assert stale_tg.exists()


def test_upgrade_does_not_remove_unowned_broken_python_scripts_launcher(
    monkeypatch,
    tmp_path,
):
    native_binary = tmp_path / ".tensor-grep" / "bin" / "tg.exe"
    tool_dir = tmp_path / "Python314" / "Scripts"
    native_binary.parent.mkdir(parents=True)
    tool_dir.mkdir(parents=True)
    native_binary.write_text("managed native", encoding="utf-8")
    tool_tg = tool_dir / "tg.exe"
    tool_tg.write_text("foreign broken launcher", encoding="utf-8")
    tool_python = tool_dir.parent / "python.exe"
    tool_python.write_text("", encoding="utf-8")
    calls: list[list[str]] = []

    def _fake_candidate_version(path):
        candidate = Path(path)
        if candidate == native_binary:
            return "tg 0.33.0"
        if candidate == tool_tg:
            return None
        return None

    def _fake_run(cmd, capture_output=True, text=True, timeout=None, **_kwargs):
        command = [str(part) for part in cmd]
        calls.append(command)
        if command[:5] == [str(tool_python), "-m", "pip", "show", "-f"]:
            return subprocess.CompletedProcess(
                cmd,
                0,
                stdout=(
                    "Name: tensor-grep\n"
                    "Version: 0.32.0\n"
                    f"Location: {tool_dir.parent / 'Lib' / 'site-packages'}\n"
                    "Files:\n"
                    "tensor_grep\\__main__.py\n"
                ),
                stderr="",
            )
        raise AssertionError(f"unexpected command: {command}")

    monkeypatch.setattr(sys, "platform", "win32")
    monkeypatch.setenv("PATH", f"{tool_dir};{native_binary.parent}")
    monkeypatch.setattr(cli_main, "_doctor_fresh_shell_path_value", lambda: str(tool_dir))
    monkeypatch.setattr(cli_main, "_doctor_tg_candidate_version", _fake_candidate_version)
    monkeypatch.setattr("subprocess.run", _fake_run)

    message = cli_main._remove_windows_stale_tensor_grep_python_launchers(
        "0.33.0",
        native_binary,
    )

    assert message is not None
    assert "package ownership could not be verified" in message
    assert tool_tg.exists()
    assert [str(tool_python), "-m", "pip", "uninstall", "-y", "tensor-grep"] not in calls


def test_upgrade_ignores_foreign_python_scripts_launcher(
    monkeypatch,
    tmp_path,
):
    native_binary = tmp_path / ".tensor-grep" / "bin" / "tg.exe"
    tool_dir = tmp_path / "Python314" / "Scripts"
    native_binary.parent.mkdir(parents=True)
    tool_dir.mkdir(parents=True)
    native_binary.write_text("managed native", encoding="utf-8")
    tool_tg = tool_dir / "tg.exe"
    tool_tg.write_text("foreign launcher", encoding="utf-8")
    tool_python = tool_dir.parent / "python.exe"
    tool_python.write_text("", encoding="utf-8")
    calls: list[list[str]] = []

    def _fake_candidate_version(path):
        candidate = Path(path)
        if candidate == native_binary:
            return "tg 0.33.0"
        if candidate == tool_tg:
            return "together 0.32.0"
        return None

    def _fake_run(cmd, capture_output=True, text=True, timeout=None, **_kwargs):
        command = [str(part) for part in cmd]
        calls.append(command)
        raise AssertionError(f"unexpected command: {command}")

    monkeypatch.setattr(sys, "platform", "win32")
    monkeypatch.setenv("PATH", f"{tool_dir};{native_binary.parent}")
    monkeypatch.setattr(cli_main, "_doctor_fresh_shell_path_value", lambda: str(tool_dir))
    monkeypatch.setattr(cli_main, "_doctor_tg_candidate_version", _fake_candidate_version)
    monkeypatch.setattr("subprocess.run", _fake_run)

    message = cli_main._remove_windows_stale_tensor_grep_python_launchers(
        "0.33.0",
        native_binary,
    )

    assert message is None
    assert tool_tg.exists()
    assert not list(tool_dir.glob("tg.exe.orphaned-tensor-grep-*.bak"))
    assert calls == []


def test_upgrade_backs_up_readable_unowned_tensor_grep_python_scripts_launcher(
    monkeypatch,
    tmp_path,
):
    native_binary = tmp_path / ".tensor-grep" / "bin" / "tg.exe"
    tool_dir = tmp_path / "Python314" / "Scripts"
    native_binary.parent.mkdir(parents=True)
    tool_dir.mkdir(parents=True)
    native_binary.write_text("managed native", encoding="utf-8")
    tool_tg = tool_dir / "tg.exe"
    tool_tg.write_text("manually copied tensor-grep-looking launcher", encoding="utf-8")
    tool_python = tool_dir.parent / "python.exe"
    tool_python.write_text("", encoding="utf-8")
    calls: list[list[str]] = []

    def _fake_candidate_version(path):
        candidate = Path(path)
        if candidate == native_binary:
            return "tg 0.33.0"
        if candidate == tool_tg:
            return "tensor-grep 0.32.0"
        return None

    def _fake_run(cmd, capture_output=True, text=True, timeout=None, **_kwargs):
        command = [str(part) for part in cmd]
        calls.append(command)
        if command[:5] == [str(tool_python), "-m", "pip", "show", "-f"]:
            return subprocess.CompletedProcess(
                cmd,
                0,
                stdout=(
                    "Name: tensor-grep\n"
                    "Version: 0.32.0\n"
                    f"Location: {tool_dir.parent / 'Lib' / 'site-packages'}\n"
                    "Files:\n"
                    "tensor_grep\\__main__.py\n"
                ),
                stderr="",
            )
        raise AssertionError(f"unexpected command: {command}")

    monkeypatch.setattr(sys, "platform", "win32")
    monkeypatch.setenv("PATH", f"{tool_dir};{native_binary.parent}")
    monkeypatch.setattr(cli_main, "_doctor_fresh_shell_path_value", lambda: str(tool_dir))
    monkeypatch.setattr(cli_main, "_doctor_tg_candidate_version", _fake_candidate_version)
    monkeypatch.setattr("subprocess.run", _fake_run)

    message = cli_main._remove_windows_stale_tensor_grep_python_launchers(
        "0.33.0",
        native_binary,
    )

    assert message is not None
    assert "Backed up orphaned tensor-grep Python Scripts launchers" in message
    assert not tool_tg.exists()
    backups = list(tool_dir.glob("tg.exe.orphaned-tensor-grep-*.bak"))
    assert len(backups) == 1
    assert backups[0].read_text(encoding="utf-8") == "manually copied tensor-grep-looking launcher"
    assert [str(tool_python), "-m", "pip", "show", "-f", "tensor-grep"] in calls
    assert [str(tool_python), "-m", "pip", "uninstall", "-y", "tensor-grep"] not in calls


def test_managed_frontdoor_refresh_runs_stale_python_launcher_cleanup(
    monkeypatch,
    tmp_path,
):
    install_dir = tmp_path / ".tensor-grep"
    python_executable = install_dir / ".venv" / "Scripts" / "python.exe"
    native_binary = install_dir / "bin" / "tg.exe"
    python_executable.parent.mkdir(parents=True)
    native_binary.parent.mkdir(parents=True)
    python_executable.write_text("", encoding="utf-8")
    native_binary.write_text("managed native", encoding="utf-8")
    cleanup_calls: list[tuple[str, Path]] = []

    monkeypatch.setattr(sys, "platform", "win32")
    monkeypatch.setattr(sys, "executable", str(python_executable))
    monkeypatch.setattr(cli_main, "_native_tg_version", lambda path: "tg 0.33.0")
    monkeypatch.setattr(cli_main, "_ensure_windows_managed_native_first_on_path", lambda path: None)
    monkeypatch.setattr(cli_main, "_windows_stale_tensor_grep_com_bridges", lambda *_args: [])
    monkeypatch.setattr(cli_main, "_refresh_windows_tensor_grep_com_bridges", lambda *_args: [])

    def _fake_cleanup(expected_version, native_path):
        cleanup_calls.append((expected_version, native_path))
        return "Removed stale tensor-grep Python package launchers from PATH:\n- stale"

    monkeypatch.setattr(
        cli_main,
        "_remove_windows_stale_tensor_grep_python_launchers",
        _fake_cleanup,
    )

    message = cli_main._refresh_managed_native_frontdoor("0.33.0")

    assert cleanup_calls == [("0.33.0", native_binary)]
    assert message is not None
    assert "Removed stale tensor-grep Python package launchers" in message


def test_managed_frontdoor_refresh_uses_default_install_when_upgrade_runs_from_external_python(
    monkeypatch,
    tmp_path,
):
    install_dir = tmp_path / ".tensor-grep"
    external_python = tmp_path / "Python314" / "python.exe"
    native_binary = install_dir / "bin" / "tg.exe"
    external_python.parent.mkdir(parents=True)
    native_binary.parent.mkdir(parents=True)
    external_python.write_text("", encoding="utf-8")
    native_binary.write_text("managed native", encoding="utf-8")
    cleanup_calls: list[tuple[str, Path]] = []

    monkeypatch.setattr(sys, "platform", "win32")
    monkeypatch.setattr(sys, "executable", str(external_python))
    monkeypatch.setenv("USERPROFILE", str(tmp_path))
    monkeypatch.delenv("TG_NATIVE_TG_BINARY", raising=False)
    monkeypatch.delenv("TG_SIDECAR_PYTHON", raising=False)
    monkeypatch.setattr(cli_main, "_native_tg_version", lambda path: "tg 0.33.0")
    monkeypatch.setattr(cli_main, "_ensure_windows_managed_native_first_on_path", lambda path: None)
    monkeypatch.setattr(cli_main, "_windows_stale_tensor_grep_com_bridges", lambda *_args: [])
    monkeypatch.setattr(cli_main, "_refresh_windows_tensor_grep_com_bridges", lambda *_args: [])

    def _fake_cleanup(expected_version, native_path):
        cleanup_calls.append((expected_version, native_path))
        return "Removed stale tensor-grep Python package launchers from PATH:\n- stale"

    monkeypatch.setattr(
        cli_main,
        "_remove_windows_stale_tensor_grep_python_launchers",
        _fake_cleanup,
    )

    message = cli_main._refresh_managed_native_frontdoor("0.33.0")

    assert cleanup_calls == [("0.33.0", native_binary)]
    assert message is not None
    assert "Removed stale tensor-grep Python package launchers" in message


def test_repair_launcher_requires_explicit_foreign_rename(monkeypatch, tmp_path):
    install_dir = tmp_path / ".tensor-grep"
    native_binary = install_dir / "bin" / "tg.exe"
    foreign_dir = tmp_path / "MachinePython314" / "Scripts"
    native_binary.parent.mkdir(parents=True)
    foreign_dir.mkdir(parents=True)
    native_binary.write_text("managed native", encoding="utf-8")
    foreign_tg = foreign_dir / "tg.exe"
    foreign_tg.write_text("Together CLI", encoding="utf-8")

    def _fake_candidate_version(path):
        text = Path(path).read_text(encoding="utf-8")
        if text == "managed native":
            return "tg 0.33.0"
        if text == "Together CLI":
            return "Together CLI (v2.12.0)"
        return None

    monkeypatch.setattr(sys, "platform", "win32")
    monkeypatch.setenv("PATH", f"{foreign_dir};{native_binary.parent}")
    monkeypatch.setattr(cli_main, "resolve_native_tg_binary", lambda: native_binary)
    monkeypatch.setattr(cli_main, "_doctor_installed_version", lambda: "0.33.0")
    monkeypatch.setattr(cli_main, "_doctor_tg_candidate_version", _fake_candidate_version)

    blocked = cli_main._repair_windows_python_subprocess_launcher(allow_foreign_rename=False)

    assert blocked["status"] == "blocked_requires_allow_foreign_rename"
    assert foreign_tg.read_text(encoding="utf-8") == "Together CLI"
    assert "allow-foreign-rename" in str(blocked["message"])

    repaired = cli_main._repair_windows_python_subprocess_launcher(allow_foreign_rename=True)

    assert repaired["status"] == "repaired"
    assert Path(str(repaired["replaced_path"])) == foreign_tg
    backup_path = Path(str(repaired["backup_path"]))
    assert backup_path.is_file()
    assert backup_path.read_text(encoding="utf-8") == "Together CLI"
    assert foreign_tg.read_text(encoding="utf-8") == "managed native"
    assert repaired["post_repair_version"] == "tg 0.33.0"


def test_repair_launcher_removes_owned_python_scripts_entrypoint(monkeypatch, tmp_path):
    install_dir = tmp_path / ".tensor-grep"
    native_binary = install_dir / "bin" / "tg.exe"
    scripts_dir = tmp_path / "Python314" / "Scripts"
    native_binary.parent.mkdir(parents=True)
    scripts_dir.mkdir(parents=True)
    native_binary.write_text("managed native", encoding="utf-8")
    python_tg = scripts_dir / "tg.exe"
    python_tg.write_text("tensor-grep console launcher", encoding="utf-8")
    python_executable = scripts_dir.parent / "python.exe"
    python_executable.write_text("", encoding="utf-8")
    package_location = scripts_dir.parent / "Lib" / "site-packages"
    package_launcher = os.path.relpath(python_tg, package_location)
    calls: list[list[str]] = []

    def _fake_candidate_version(path):
        candidate = Path(path)
        if candidate == native_binary:
            return "tg 0.33.0"
        if candidate == python_tg:
            return "tensor-grep 0.33.0"
        return None

    def _fake_run(cmd, capture_output=True, text=True, timeout=None, **_kwargs):
        command = [str(part) for part in cmd]
        calls.append(command)
        if command[:5] == [str(python_executable), "-m", "pip", "show", "-f"]:
            return subprocess.CompletedProcess(
                cmd,
                0,
                stdout=(
                    "Name: tensor-grep\n"
                    "Version: 0.33.0\n"
                    f"Location: {package_location}\n"
                    "Files:\n"
                    f"{package_launcher}\n"
                ),
                stderr="",
            )
        if command[:4] == [str(python_executable), "-m", "pip", "uninstall"]:
            python_tg.unlink(missing_ok=True)
            return subprocess.CompletedProcess(cmd, 0, stdout="uninstalled\n", stderr="")
        raise AssertionError(f"unexpected command: {command}")

    monkeypatch.setattr(sys, "platform", "win32")
    monkeypatch.setenv("PATH", f"{scripts_dir};{native_binary.parent}")
    monkeypatch.setattr(cli_main, "resolve_native_tg_binary", lambda: native_binary)
    monkeypatch.setattr(cli_main, "_doctor_installed_version", lambda: "0.33.0")
    monkeypatch.setattr(cli_main, "_doctor_tg_candidate_version", _fake_candidate_version)
    monkeypatch.setattr(cli_main, "_doctor_fresh_shell_path_value", lambda: "")
    monkeypatch.setattr("subprocess.run", _fake_run)

    repaired = cli_main._repair_windows_python_subprocess_launcher(allow_foreign_rename=False)

    assert repaired["status"] == "repaired"
    assert repaired["replaced_path"] == str(python_tg)
    assert repaired["post_repair_version"] == "tg 0.33.0"
    assert not python_tg.exists()
    assert [str(python_executable), "-m", "pip", "uninstall", "-y", "tensor-grep"] in calls


def test_repair_launcher_backs_up_orphaned_tensor_grep_python_scripts_entrypoint(
    monkeypatch,
    tmp_path,
):
    install_dir = tmp_path / ".tensor-grep"
    native_binary = install_dir / "bin" / "tg.exe"
    scripts_dir = tmp_path / "Python314" / "Scripts"
    native_binary.parent.mkdir(parents=True)
    scripts_dir.mkdir(parents=True)
    native_binary.write_text("managed native", encoding="utf-8")
    python_tg = scripts_dir / "tg.exe"
    python_tg.write_text("orphaned tensor-grep launcher", encoding="utf-8")
    python_executable = scripts_dir.parent / "python.exe"
    python_executable.write_text("", encoding="utf-8")

    def _fake_candidate_version(path):
        candidate = Path(path)
        if candidate == native_binary:
            return "tg 0.33.0"
        if candidate == python_tg:
            return "tensor-grep 0.32.0"
        return None

    def _fake_run(cmd, capture_output=True, text=True, timeout=None, **_kwargs):
        command = [str(part) for part in cmd]
        if command[:5] == [str(python_executable), "-m", "pip", "show", "-f"]:
            return subprocess.CompletedProcess(
                cmd,
                1,
                stdout="",
                stderr="WARNING: Package(s) not found: tensor-grep\n",
            )
        raise AssertionError(f"unexpected command: {command}")

    monkeypatch.setattr(sys, "platform", "win32")
    monkeypatch.setenv("PATH", f"{scripts_dir};{native_binary.parent}")
    monkeypatch.setattr(cli_main, "resolve_native_tg_binary", lambda: native_binary)
    monkeypatch.setattr(cli_main, "_doctor_installed_version", lambda: "0.33.0")
    monkeypatch.setattr(cli_main, "_doctor_tg_candidate_version", _fake_candidate_version)
    monkeypatch.setattr(cli_main, "_doctor_fresh_shell_path_value", lambda: "")
    monkeypatch.setattr("subprocess.run", _fake_run)

    repaired = cli_main._repair_windows_python_subprocess_launcher(allow_foreign_rename=False)

    assert repaired["status"] == "repaired"
    assert repaired["replaced_path"] == str(python_tg)
    assert repaired["post_repair_version"] == "tg 0.33.0"
    assert not python_tg.exists()
    backups = list(scripts_dir.glob("tg.exe.orphaned-tensor-grep-*.bak"))
    assert len(backups) == 1
    assert backups[0].read_text(encoding="utf-8") == "orphaned tensor-grep launcher"


def test_repair_launcher_command_emits_json_and_nonzero_when_blocked(
    monkeypatch,
    tmp_path,
):
    native_binary = tmp_path / ".tensor-grep" / "bin" / "tg.exe"
    foreign_tg = tmp_path / "Python314" / "Scripts" / "tg.exe"
    native_binary.parent.mkdir(parents=True)
    foreign_tg.parent.mkdir(parents=True)
    native_binary.write_text("managed native", encoding="utf-8")
    foreign_tg.write_text("Together CLI", encoding="utf-8")

    def _fake_candidate_version(path):
        return "Together CLI (v2.12.0)" if Path(path) == foreign_tg else "tg 0.33.0"

    monkeypatch.setattr(sys, "platform", "win32")
    monkeypatch.setenv("PATH", f"{foreign_tg.parent};{native_binary.parent}")
    monkeypatch.setattr(cli_main, "resolve_native_tg_binary", lambda: native_binary)
    monkeypatch.setattr(cli_main, "_doctor_installed_version", lambda: "0.33.0")
    monkeypatch.setattr(cli_main, "_doctor_tg_candidate_version", _fake_candidate_version)

    result = CliRunner().invoke(app, ["repair-launcher", "--json"])

    assert result.exit_code == 1
    payload = json.loads(result.stdout)
    assert payload["status"] == "blocked_requires_allow_foreign_rename"
    assert payload["foreign_path"] == str(foreign_tg.resolve())
    assert "allow-foreign-rename" in payload["message"]


def test_upgrade_does_not_treat_repo_dev_venv_as_managed_frontdoor(monkeypatch, tmp_path):
    from tensor_grep.cli import main as cli_main

    project = tmp_path / "project"
    python_executable = project / ".venv" / "Scripts" / "python.exe"
    native_binary = project / "bin" / "tg.exe"
    python_executable.parent.mkdir(parents=True)
    native_binary.parent.mkdir(parents=True)
    python_executable.write_text("", encoding="utf-8")
    native_binary.write_text("", encoding="utf-8")

    monkeypatch.setattr("sys.executable", str(python_executable))
    monkeypatch.setattr(sys, "platform", "win32")

    assert cli_main._managed_native_frontdoor_path_from_env() is None


def test_upgrade_refreshes_stale_tensor_grep_com_bridge_after_native_update(monkeypatch, tmp_path):
    install_dir = tmp_path / ".tensor-grep"
    python_executable = install_dir / ".venv" / "Scripts" / "python.exe"
    native_binary = install_dir / "bin" / "tg.exe"
    bridge_tg = tmp_path / "Python314" / "Scripts" / "tg.com"
    repaired_tg = tmp_path / "MachinePython314" / "Scripts" / "tg.exe"
    foreign_tg = tmp_path / "ForeignPython" / "Scripts" / "tg.com"
    python_executable.parent.mkdir(parents=True)
    native_binary.parent.mkdir(parents=True)
    bridge_tg.parent.mkdir(parents=True)
    repaired_tg.parent.mkdir(parents=True)
    foreign_tg.parent.mkdir(parents=True)
    python_executable.write_text("", encoding="utf-8")
    native_binary.write_text("old native", encoding="utf-8")
    bridge_tg.write_text("old native", encoding="utf-8")
    repaired_tg.write_text("old native", encoding="utf-8")
    foreign_tg.write_text("foreign", encoding="utf-8")

    def _fake_run(cmd, capture_output=True, text=True, check=True, timeout=None, env=None):
        command = [str(part) for part in cmd]
        if command[0] == "uv":
            return subprocess.CompletedProcess(cmd, 0, stdout="Installed 1 package", stderr="")
        if command[:2] == [str(python_executable), "-c"]:
            return subprocess.CompletedProcess(cmd, 0, stdout="0.33.0\n", stderr="")
        if command[0] in {str(native_binary), str(bridge_tg), str(repaired_tg)}:
            version = (
                "0.33.0"
                if Path(command[0]).read_text(encoding="utf-8") == "new native"
                else "0.32.0"
            )
            return subprocess.CompletedProcess(cmd, 0, stdout=f"tg {version}\n", stderr="")
        if command[0] == str(foreign_tg):
            return subprocess.CompletedProcess(cmd, 0, stdout="2.12.0\n", stderr="")
        if command[0].endswith(".tmp"):
            return subprocess.CompletedProcess(cmd, 0, stdout="tg 0.33.0\n", stderr="")
        raise AssertionError(f"unexpected command: {command}")

    def _fake_urlopen(url, timeout=None):
        return _FakeUrlopenResponse(b"new native")

    monkeypatch.setattr("sys.executable", str(python_executable))
    monkeypatch.setattr(sys, "platform", "win32")
    monkeypatch.setenv(
        "PATH",
        os.pathsep.join([str(bridge_tg.parent), str(repaired_tg.parent), str(foreign_tg.parent)]),
    )
    monkeypatch.setattr("platform.machine", lambda: "AMD64")
    monkeypatch.setattr("importlib.metadata.version", lambda _name: "0.32.0")
    monkeypatch.setattr("subprocess.run", _fake_run)
    monkeypatch.setattr("urllib.request.urlopen", _fake_urlopen)
    _allow_native_frontdoor_checksum(monkeypatch)
    monkeypatch.setattr(
        "tensor_grep.cli.main._latest_pypi_tensor_grep_version",
        lambda: "0.33.0",
        raising=False,
    )
    monkeypatch.setattr(
        "tensor_grep.cli.main._doctor_fresh_shell_path_value",
        lambda: str(bridge_tg.parent),
        raising=False,
    )

    runner = CliRunner()
    result = runner.invoke(app, ["upgrade"])

    assert result.exit_code == 0
    assert native_binary.read_text(encoding="utf-8") == "new native"
    assert bridge_tg.read_text(encoding="utf-8") == "new native"
    assert repaired_tg.read_text(encoding="utf-8") == "new native"
    assert foreign_tg.read_text(encoding="utf-8") == "foreign"
    assert "Refreshed 2 PATH tensor-grep front-door copies to 0.33.0." in result.stdout
    assert str(bridge_tg) in result.stdout
    assert str(repaired_tg) in result.stdout


def test_upgrade_targets_current_cmd_shim_dir_for_python_subprocess_bridge(
    monkeypatch,
    tmp_path,
):
    from tensor_grep.cli import main as cli_main

    install_dir = tmp_path / ".tensor-grep"
    native_binary = install_dir / "bin" / "tg.exe"
    shim_dir = tmp_path / "bin"
    shim_cmd = shim_dir / "tg.cmd"
    native_binary.parent.mkdir(parents=True)
    shim_dir.mkdir(parents=True)
    native_binary.write_text("new native", encoding="utf-8")
    shim_cmd.write_text("@echo off\n", encoding="utf-8")

    def _fake_candidate_version(path):
        candidate = Path(path)
        if candidate in {native_binary, shim_cmd, shim_dir / "tg.exe"}:
            return "tg 0.33.0"
        return None

    monkeypatch.setattr(sys, "platform", "win32")
    monkeypatch.setenv("USERPROFILE", str(tmp_path))
    monkeypatch.delenv("HOME", raising=False)
    monkeypatch.setenv("PATH", str(shim_dir))
    monkeypatch.setattr(cli_main, "_doctor_fresh_shell_path_value", lambda: None)
    monkeypatch.setattr(cli_main, "_doctor_tg_candidate_version", _fake_candidate_version)
    monkeypatch.setattr(cli_main, "_native_tg_version", lambda path: _fake_candidate_version(path))

    targets = cli_main._windows_stale_tensor_grep_com_bridges("0.33.0", native_binary)

    assert targets == [shim_dir / "tg.exe"]
    refreshed = cli_main._refresh_windows_tensor_grep_com_bridges(
        "0.33.0",
        native_binary,
        targets,
    )
    assert refreshed == [shim_dir / "tg.exe"]
    assert (shim_dir / "tg.exe").read_text(encoding="utf-8") == "new native"
    assert (shim_dir / "tg.exe.tensor-grep-bridge").read_text(encoding="ascii") == (
        "tensor-grep managed tg.exe bridge\n"
    )


def test_upgrade_does_not_create_python_subprocess_bridge_for_foreign_cmd(
    monkeypatch,
    tmp_path,
):
    from tensor_grep.cli import main as cli_main

    native_binary = tmp_path / ".tensor-grep" / "bin" / "tg.exe"
    shim_dir = tmp_path / "bin"
    foreign_cmd = shim_dir / "tg.cmd"
    native_binary.parent.mkdir(parents=True)
    shim_dir.mkdir(parents=True)
    native_binary.write_text("new native", encoding="utf-8")
    foreign_cmd.write_text("@echo off\n", encoding="utf-8")

    def _fake_candidate_version(path):
        if Path(path) == foreign_cmd:
            return "Together CLI (v2.12.0)"
        if Path(path) == native_binary:
            return "tg 0.33.0"
        return None

    monkeypatch.setattr(sys, "platform", "win32")
    monkeypatch.setenv("USERPROFILE", str(tmp_path))
    monkeypatch.delenv("HOME", raising=False)
    monkeypatch.setenv("PATH", str(shim_dir))
    monkeypatch.setattr(cli_main, "_doctor_fresh_shell_path_value", lambda: None)
    monkeypatch.setattr(cli_main, "_doctor_tg_candidate_version", _fake_candidate_version)

    targets = cli_main._windows_stale_tensor_grep_com_bridges("0.33.0", native_binary)

    assert targets == []
    assert not (shim_dir / "tg.exe").exists()


def test_upgrade_does_not_create_python_subprocess_bridge_outside_managed_shim_dirs(
    monkeypatch,
    tmp_path,
):
    from tensor_grep.cli import main as cli_main

    native_binary = tmp_path / ".tensor-grep" / "bin" / "tg.exe"
    tool_dir = tmp_path / "tools"
    wrapper_cmd = tool_dir / "tg.cmd"
    native_binary.parent.mkdir(parents=True)
    tool_dir.mkdir(parents=True)
    native_binary.write_text("new native", encoding="utf-8")
    wrapper_cmd.write_text("@echo off\n", encoding="utf-8")

    def _fake_candidate_version(path):
        if Path(path) == wrapper_cmd:
            return "tg 0.33.0"
        if Path(path) == native_binary:
            return "tg 0.33.0"
        return None

    monkeypatch.setattr(sys, "platform", "win32")
    monkeypatch.setenv("USERPROFILE", str(tmp_path / "home"))
    monkeypatch.delenv("HOME", raising=False)
    monkeypatch.setenv("PATH", str(tool_dir))
    monkeypatch.setattr(cli_main, "_doctor_fresh_shell_path_value", lambda: None)
    monkeypatch.setattr(cli_main, "_doctor_tg_candidate_version", _fake_candidate_version)

    targets = cli_main._windows_stale_tensor_grep_com_bridges("0.33.0", native_binary)

    assert targets == []
    assert not (tool_dir / "tg.exe").exists()


def test_upgrade_refreshes_stale_com_bridge_when_native_frontdoor_is_current(monkeypatch, tmp_path):
    install_dir = tmp_path / ".tensor-grep"
    python_executable = install_dir / ".venv" / "Scripts" / "python.exe"
    native_binary = install_dir / "bin" / "tg.exe"
    bridge_tg = tmp_path / "Python314" / "Scripts" / "tg.com"
    python_executable.parent.mkdir(parents=True)
    native_binary.parent.mkdir(parents=True)
    bridge_tg.parent.mkdir(parents=True)
    python_executable.write_text("", encoding="utf-8")
    native_binary.write_text("new native", encoding="utf-8")
    bridge_tg.write_text("old native", encoding="utf-8")
    downloads: list[str] = []

    def _fake_run(cmd, capture_output=True, text=True, check=True, timeout=None, env=None):
        command = [str(part) for part in cmd]
        if command[0] == "uv":
            return subprocess.CompletedProcess(cmd, 0, stdout="Audited 1 package", stderr="")
        if command[:2] == [str(python_executable), "-c"]:
            return subprocess.CompletedProcess(cmd, 0, stdout="0.33.0\n", stderr="")
        if command[0] in {str(native_binary), str(bridge_tg)}:
            version = (
                "0.33.0"
                if Path(command[0]).read_text(encoding="utf-8") == "new native"
                else "0.32.0"
            )
            return subprocess.CompletedProcess(cmd, 0, stdout=f"tg {version}\n", stderr="")
        raise AssertionError(f"unexpected command: {command}")

    def _fake_urlopen(url, timeout=None):
        downloads.append(str(url))
        return _FakeUrlopenResponse(b"new native")

    monkeypatch.setattr("sys.executable", str(python_executable))
    monkeypatch.setattr(sys, "platform", "win32")
    monkeypatch.setenv("PATH", str(bridge_tg.parent))
    monkeypatch.setattr("importlib.metadata.version", lambda _name: "0.33.0")
    monkeypatch.setattr("subprocess.run", _fake_run)
    monkeypatch.setattr("urllib.request.urlopen", _fake_urlopen)
    _allow_native_frontdoor_checksum(monkeypatch)
    monkeypatch.setattr(
        "tensor_grep.cli.main._latest_pypi_tensor_grep_version",
        lambda: "0.33.0",
        raising=False,
    )
    monkeypatch.setattr(
        "tensor_grep.cli.main._doctor_fresh_shell_path_value",
        lambda: str(bridge_tg.parent),
        raising=False,
    )

    runner = CliRunner()
    result = runner.invoke(app, ["upgrade"])

    assert result.exit_code == 0
    assert downloads == []
    assert native_binary.read_text(encoding="utf-8") == "new native"
    assert bridge_tg.read_text(encoding="utf-8") == "new native"
    assert "tensor-grep is already at the latest PyPI version (0.33.0)." in result.stdout
    assert "Refreshed 1 PATH tg.com bridge to 0.33.0." in result.stdout


def test_upgrade_refreshes_stale_native_frontdoor_when_python_package_is_latest(
    monkeypatch, tmp_path
):
    install_dir = tmp_path / ".tensor-grep"
    python_executable = install_dir / ".venv" / "Scripts" / "python.exe"
    native_binary = install_dir / "bin" / "tg.exe"
    python_executable.parent.mkdir(parents=True)
    native_binary.parent.mkdir(parents=True)
    python_executable.write_text("", encoding="utf-8")
    native_binary.write_text("old native", encoding="utf-8")
    downloads: list[str] = []

    def _fake_run(cmd, capture_output=True, text=True, check=True, timeout=None):
        command = [str(part) for part in cmd]
        if command[0] == "uv":
            return subprocess.CompletedProcess(cmd, 0, stdout="Audited 1 package", stderr="")
        if command[:2] == [str(python_executable), "-c"]:
            return subprocess.CompletedProcess(cmd, 0, stdout="0.33.0\n", stderr="")
        if command[0] == str(native_binary):
            version = (
                "0.33.0" if native_binary.read_text(encoding="utf-8") == "new native" else "0.32.0"
            )
            return subprocess.CompletedProcess(cmd, 0, stdout=f"tg {version}\n", stderr="")
        if command[0].endswith(".tmp"):
            return subprocess.CompletedProcess(cmd, 0, stdout="tg 0.33.0\n", stderr="")
        raise AssertionError(f"unexpected command: {command}")

    def _fake_urlopen(url, timeout=None):
        downloads.append(str(url))
        return _FakeUrlopenResponse(b"new native")

    monkeypatch.setattr("sys.executable", str(python_executable))
    monkeypatch.setattr(sys, "platform", "win32")
    monkeypatch.setattr("platform.machine", lambda: "AMD64")
    monkeypatch.setenv("TENSOR_GREP_NATIVE_FRONTDOOR_FLAVOR", "cpu")
    monkeypatch.setattr("importlib.metadata.version", lambda _name: "0.33.0")
    monkeypatch.setattr("subprocess.run", _fake_run)
    monkeypatch.setattr("urllib.request.urlopen", _fake_urlopen)
    _allow_native_frontdoor_checksum(monkeypatch)
    monkeypatch.setattr(
        "tensor_grep.cli.main._latest_pypi_tensor_grep_version",
        lambda: "0.33.0",
        raising=False,
    )

    runner = CliRunner()
    result = runner.invoke(app, ["upgrade"])

    assert result.exit_code == 0
    assert downloads
    assert native_binary.read_text(encoding="utf-8") == "new native"
    assert "tensor-grep is already at the latest PyPI version (0.33.0)." in result.stdout
    assert "Native tg front door refreshed to 0.33.0." in result.stdout


def test_upgrade_schedules_native_frontdoor_refresh_when_windows_exe_is_locked(
    monkeypatch, tmp_path
):
    install_dir = tmp_path / ".tensor-grep"
    python_executable = install_dir / ".venv" / "Scripts" / "python.exe"
    native_binary = install_dir / "bin" / "tg.exe"
    bridge_tg = tmp_path / "Python314" / "Scripts" / "tg.com"
    python_executable.parent.mkdir(parents=True)
    native_binary.parent.mkdir(parents=True)
    bridge_tg.parent.mkdir(parents=True)
    python_executable.write_text("", encoding="utf-8")
    native_binary.write_text("old native", encoding="utf-8")
    bridge_tg.write_text("old native", encoding="utf-8")
    popen_calls: list[list[str]] = []

    class _LockedExeError(PermissionError):
        winerror = 32

    def _fake_run(cmd, capture_output=True, text=True, check=True, timeout=None, env=None):
        command = [str(part) for part in cmd]
        if command[0] == "uv":
            return subprocess.CompletedProcess(cmd, 0, stdout="Audited 1 package", stderr="")
        if command[:2] == [str(python_executable), "-c"]:
            return subprocess.CompletedProcess(cmd, 0, stdout="0.33.0\n", stderr="")
        if command[0] == str(native_binary):
            return subprocess.CompletedProcess(cmd, 0, stdout="tg 0.32.0\n", stderr="")
        if command[0] == str(bridge_tg):
            return subprocess.CompletedProcess(cmd, 0, stdout="tg 0.32.0\n", stderr="")
        if command[0].endswith(".tmp"):
            return subprocess.CompletedProcess(cmd, 0, stdout="tg 0.33.0\n", stderr="")
        raise AssertionError(f"unexpected command: {command}")

    def _fake_urlopen(url, timeout=None):
        return _FakeUrlopenResponse(b"new native")

    def _fake_replace(src, dst):
        if Path(dst) == native_binary:
            raise _LockedExeError("The process cannot access the file")
        os.replace(src, dst)

    class _FakePopen:
        def __init__(
            self,
            cmd,
            stdout=None,
            stderr=None,
            stdin=None,
            close_fds=None,
            creationflags=0,
        ):
            popen_calls.append([str(part) for part in cmd])

    monkeypatch.setattr("sys.executable", str(python_executable))
    monkeypatch.setattr(sys, "platform", "win32")
    monkeypatch.setenv("PATH", str(bridge_tg.parent))
    monkeypatch.setattr("platform.machine", lambda: "AMD64")
    monkeypatch.setenv("TENSOR_GREP_NATIVE_FRONTDOOR_FLAVOR", "nvidia")
    monkeypatch.setattr("importlib.metadata.version", lambda _name: "0.33.0")
    monkeypatch.setattr("subprocess.run", _fake_run)
    monkeypatch.setattr("subprocess.Popen", _FakePopen)
    monkeypatch.setattr("urllib.request.urlopen", _fake_urlopen)
    _allow_native_frontdoor_checksum(monkeypatch)
    monkeypatch.setattr("tensor_grep.cli.main.os.replace", _fake_replace)
    monkeypatch.setattr(
        "tensor_grep.cli.main._latest_pypi_tensor_grep_version",
        lambda: "0.33.0",
        raising=False,
    )
    monkeypatch.setattr(
        "tensor_grep.cli.main._doctor_fresh_shell_path_value",
        lambda: str(bridge_tg.parent),
        raising=False,
    )

    runner = CliRunner()
    result = runner.invoke(app, ["upgrade"])

    assert result.exit_code == 0
    assert native_binary.read_text(encoding="utf-8") == "old native"
    assert popen_calls
    assert "urlretrieve" in popen_calls[0][2]
    assert "0.33.0" in popen_calls[0]
    helper_assets = json.loads(popen_calls[0][7])
    assert helper_assets == [
        {
            "url": (
                "https://github.com/oimiragieo/tensor-grep/releases/download/v0.33.0/"
                "tg-windows-amd64-nvidia.exe"
            ),
            "flavor": "nvidia",
            "asset_name": "tg-windows-amd64-nvidia.exe",
            "requested_flavor": "nvidia",
            "sha256": _STUB_ASSET_SHA256,
        },
        {
            "url": (
                "https://github.com/oimiragieo/tensor-grep/releases/download/v0.33.0/"
                "tg-windows-amd64-cpu.exe"
            ),
            "flavor": "cpu",
            "asset_name": "tg-windows-amd64-cpu.exe",
            "requested_flavor": "nvidia",
            "sha256": _STUB_ASSET_SHA256,
        },
    ]
    assert json.loads(popen_calls[0][8]) == [str(bridge_tg)]
    assert "Native tg front door refresh scheduled for 0.33.0." in result.stdout


def test_managed_native_frontdoor_path_uses_unix_native_binary_when_env_is_absent(
    monkeypatch, tmp_path
):

    install_dir = tmp_path / ".tensor-grep"
    python_executable = install_dir / ".venv" / "bin" / "python"
    python_executable.parent.mkdir(parents=True)
    python_executable.write_text("", encoding="utf-8")

    monkeypatch.delenv("TG_NATIVE_TG_BINARY", raising=False)
    monkeypatch.delenv("TG_SIDECAR_PYTHON", raising=False)
    monkeypatch.setattr("sys.executable", str(python_executable))
    monkeypatch.setattr(sys, "platform", "linux")

    assert cli_main._managed_native_frontdoor_path_from_env() == install_dir / "bin" / "tg-native"


def test_upgrade_falls_back_to_ensurepip_then_pip(monkeypatch):
    calls: list[list[str]] = []
    pip_attempts = {"count": 0}

    def _fake_run(cmd, capture_output=True, text=True, check=True):
        calls.append(list(cmd))
        if cmd[0] == "uv":
            raise FileNotFoundError("uv not found")
        if cmd[:3] == ["python", "-m", "ensurepip"]:
            return subprocess.CompletedProcess(cmd, 0, stdout="ensurepip ok", stderr="")
        if cmd[:3] == ["python", "-m", "pip"]:
            pip_attempts["count"] += 1
            if pip_attempts["count"] == 1:
                raise subprocess.CalledProcessError(
                    returncode=1, cmd=cmd, stderr="No module named pip"
                )
            return subprocess.CompletedProcess(cmd, 0, stdout="Successfully installed", stderr="")
        if cmd[:2] == ["python", "-c"]:
            return subprocess.CompletedProcess(cmd, 0, stdout="0.32.0\n", stderr="")
        raise AssertionError(f"unexpected command: {cmd}")

    monkeypatch.setattr("sys.executable", "python")
    versions = iter(["0.31.0", "0.32.0"])

    monkeypatch.setattr("importlib.metadata.version", lambda _name: next(versions))
    monkeypatch.setattr("subprocess.run", _fake_run)
    monkeypatch.setattr(
        "tensor_grep.cli.main._latest_pypi_tensor_grep_version",
        lambda: "0.32.0",
        raising=False,
    )

    runner = CliRunner()
    result = runner.invoke(app, ["upgrade"])

    assert result.exit_code == 0
    assert any(cmd[:3] == ["python", "-m", "ensurepip"] for cmd in calls)
    assert pip_attempts["count"] == 2
    assert "Successfully upgraded tensor-grep via pip+ensurepip!" in result.stdout


def test_upgrade_fails_when_post_upgrade_python_cannot_import_tensor_grep(monkeypatch):
    calls: list[list[str]] = []

    def _fake_run(cmd, capture_output=True, text=True, check=True):
        calls.append(list(cmd))
        if cmd[0] == "uv":
            return subprocess.CompletedProcess(cmd, 0, stdout="Installed 1 package", stderr="")
        if cmd[:2] == ["python", "-c"]:
            raise subprocess.CalledProcessError(
                returncode=1,
                cmd=cmd,
                stderr="No module named tensor_grep",
            )
        raise AssertionError(f"unexpected command: {cmd}")

    monkeypatch.setattr("sys.executable", "python")
    monkeypatch.setattr("importlib.metadata.version", lambda _name: "0.32.0")
    monkeypatch.setattr("subprocess.run", _fake_run)
    monkeypatch.setattr(
        "tensor_grep.cli.main._latest_pypi_tensor_grep_version",
        lambda: "0.33.0",
        raising=False,
    )

    runner = CliRunner()
    result = runner.invoke(app, ["upgrade"])

    assert result.exit_code == 1
    assert any("tensor-grep==0.33.0" in cmd for cmd in calls)
    assert "post-upgrade verification failed" in result.output
    assert "No module named tensor_grep" in result.output
    assert "already at the latest PyPI version" not in result.output


def test_upgrade_fails_with_clear_error_messages_when_uv_and_pip_fail(monkeypatch):
    calls: list[list[str]] = []

    def _fake_run(cmd, capture_output=True, text=True, check=True):
        calls.append(list(cmd))
        if cmd[0] == "uv":
            raise FileNotFoundError("uv not found")
        if cmd[:3] == ["python", "-m", "pip"]:
            raise subprocess.CalledProcessError(
                returncode=1,
                cmd=cmd,
                stderr="network timeout while contacting package index",
            )
        raise AssertionError(f"unexpected command: {cmd}")

    monkeypatch.setattr("sys.executable", "python")
    monkeypatch.setattr("subprocess.run", _fake_run)
    monkeypatch.setattr(
        "tensor_grep.cli.main._latest_pypi_tensor_grep_version",
        lambda: "0.32.0",
        raising=False,
    )

    runner = CliRunner()
    result = runner.invoke(app, ["upgrade"])

    assert result.exit_code == 1
    assert calls[0][0] == "uv"
    assert any(cmd[:3] == ["python", "-m", "pip"] for cmd in calls)
    assert "Error occurred while upgrading tensor-grep." in result.output
    assert "uv:" in result.output
    assert "pip:" in result.output
    assert "network timeout while contacting package index" in result.output


def test_upgrade_schedules_windows_helper_when_tg_exe_is_locked(monkeypatch, tmp_path):
    calls: list[list[str]] = []
    popen_calls: list[list[str]] = []

    locked_error = (
        "failed to remove file `C:\\Users\\oimir\\.tensor-grep\\.venv\\Scripts\\tg.exe`: "
        "The process cannot access the file because it is being used by another process. "
        "(os error 32)"
    )

    def _fake_run(cmd, capture_output=True, text=True, check=True):
        command = list(cmd)
        calls.append(command)
        if command[0] == "uv":
            raise subprocess.CalledProcessError(returncode=1, cmd=command, stderr=locked_error)
        if command[:3] == ["python", "-m", "pip"]:
            raise subprocess.CalledProcessError(returncode=1, cmd=command, stderr=locked_error)
        raise AssertionError(f"unexpected command: {command}")

    class _FakePopen:
        def __init__(
            self,
            cmd,
            stdout=None,
            stderr=None,
            stdin=None,
            close_fds=None,
            creationflags=0,
        ):
            popen_calls.append(list(cmd))

    monkeypatch.setattr("subprocess.run", _fake_run)
    monkeypatch.setattr("subprocess.Popen", _FakePopen)
    monkeypatch.setattr("sys.executable", "python")
    monkeypatch.setattr("importlib.metadata.version", lambda _name: "0.31.0")
    monkeypatch.setattr(sys, "platform", "win32")
    monkeypatch.setattr("platform.machine", lambda: "AMD64")
    monkeypatch.setenv("USERPROFILE", str(tmp_path))
    monkeypatch.setattr(
        "tensor_grep.cli.main._latest_pypi_tensor_grep_version",
        lambda: "0.32.0",
        raising=False,
    )
    _allow_native_frontdoor_checksum(monkeypatch)

    runner = CliRunner()
    result = runner.invoke(app, ["upgrade"])

    assert result.exit_code == 0
    assert calls[0][0] == "uv"
    assert any(cmd[:3] == ["python", "-m", "pip"] for cmd in calls)
    assert popen_calls
    assert popen_calls[0][0] == "python"
    assert popen_calls[0][1] == "-c"
    helper_code = popen_calls[0][2]
    assert "def _verify_installed_version" in helper_code
    assert "import tensor_grep" in helper_code
    assert "post-upgrade verification failed" in helper_code
    assert "tensor-grep==0.32.0" in popen_calls[0][5]
    assert popen_calls[0][6] == "0.32.0"
    assert "Windows is still using tg.exe" in result.output
    assert "Wait a few seconds, then run `tg --version` again." in result.output
    assert "Upgrade log:" in result.output


def test_upgrade_scheduled_windows_helper_restarts_preexisting_session_daemon(
    monkeypatch, tmp_path
):
    popen_calls: list[list[str]] = []
    daemon_root = r"C:\dev\projects\tensor-grep"
    locked_error = (
        "failed to remove file `C:\\Users\\oimir\\.tensor-grep\\.venv\\Scripts\\tg.exe`: "
        "The process cannot access the file because it is being used by another process. "
        "(os error 32)"
    )

    def _fake_run(cmd, capture_output=True, text=True, check=True):
        command = list(cmd)
        if command[0] == "uv":
            raise subprocess.CalledProcessError(returncode=1, cmd=command, stderr=locked_error)
        if command[:3] == ["python", "-m", "pip"]:
            raise subprocess.CalledProcessError(returncode=1, cmd=command, stderr=locked_error)
        raise AssertionError(f"unexpected command: {command}")

    class _FakePopen:
        def __init__(
            self,
            cmd,
            stdout=None,
            stderr=None,
            stdin=None,
            close_fds=None,
            creationflags=0,
        ):
            popen_calls.append([str(part) for part in cmd])

    monkeypatch.setattr("subprocess.run", _fake_run)
    monkeypatch.setattr("subprocess.Popen", _FakePopen)
    monkeypatch.setattr("sys.executable", "python")
    monkeypatch.setattr("importlib.metadata.version", lambda _name: "0.31.0")
    monkeypatch.setattr(sys, "platform", "win32")
    monkeypatch.setattr("platform.machine", lambda: "AMD64")
    monkeypatch.setenv("USERPROFILE", str(tmp_path))
    monkeypatch.setattr(
        "tensor_grep.cli.main._latest_pypi_tensor_grep_version",
        lambda: "0.32.0",
        raising=False,
    )
    monkeypatch.setattr(
        "tensor_grep.cli.main._doctor_session_daemon_status",
        lambda _path: {"running": True, "root": daemon_root},
    )
    _allow_native_frontdoor_checksum(monkeypatch)

    runner = CliRunner()
    result = runner.invoke(app, ["upgrade"])

    assert result.exit_code == 0
    assert popen_calls
    helper_code = popen_calls[0][2]
    compile(helper_code, "<scheduled-upgrade-helper>", "exec")
    assert popen_calls[0][-1] == daemon_root
    assert "def _restart_session_daemon_after_upgrade" in helper_code
    assert '"daemon"' in helper_code
    assert '"start"' in helper_code
    assert "daemon_root" in helper_code


def test_upgrade_scheduled_windows_helper_refreshes_stale_com_bridge(monkeypatch, tmp_path):
    install_dir = tmp_path / ".tensor-grep"
    python_executable = install_dir / ".venv" / "Scripts" / "python.exe"
    native_binary = install_dir / "bin" / "tg.exe"
    bridge_tg = tmp_path / "Python314" / "Scripts" / "tg.com"
    python_executable.parent.mkdir(parents=True)
    native_binary.parent.mkdir(parents=True)
    bridge_tg.parent.mkdir(parents=True)
    python_executable.write_text("", encoding="utf-8")
    native_binary.write_text("new native", encoding="utf-8")
    bridge_tg.write_text("old native", encoding="utf-8")
    popen_calls: list[list[str]] = []

    locked_error = (
        "failed to remove file `C:\\Users\\oimir\\.tensor-grep\\.venv\\Scripts\\tg.exe`: "
        "The process cannot access the file because it is being used by another process. "
        "(os error 32)"
    )

    def _fake_run(
        cmd,
        capture_output=True,
        text=True,
        check=True,
        timeout=None,
        env=None,
    ):
        command = [str(part) for part in cmd]
        if command[0] == "uv":
            raise subprocess.CalledProcessError(returncode=1, cmd=command, stderr=locked_error)
        if command[:3] == [str(python_executable), "-m", "pip"]:
            raise subprocess.CalledProcessError(returncode=1, cmd=command, stderr=locked_error)
        if command[0] == str(bridge_tg):
            return subprocess.CompletedProcess(cmd, 0, stdout="tg 0.32.0\n", stderr="")
        if command[0] == str(native_binary):
            return subprocess.CompletedProcess(cmd, 0, stdout="tg 0.33.0\n", stderr="")
        raise AssertionError(f"unexpected command: {command}")

    class _FakePopen:
        def __init__(
            self,
            cmd,
            stdout=None,
            stderr=None,
            stdin=None,
            close_fds=None,
            creationflags=0,
        ):
            popen_calls.append([str(part) for part in cmd])

    monkeypatch.setattr("subprocess.run", _fake_run)
    monkeypatch.setattr("subprocess.Popen", _FakePopen)
    monkeypatch.setattr("sys.executable", str(python_executable))
    monkeypatch.setattr("importlib.metadata.version", lambda _name: "0.32.0")
    monkeypatch.setattr(sys, "platform", "win32")
    monkeypatch.setenv("PATH", str(bridge_tg.parent))
    monkeypatch.setattr("platform.machine", lambda: "AMD64")
    monkeypatch.setenv("TENSOR_GREP_NATIVE_FRONTDOOR_FLAVOR", "cpu")
    monkeypatch.setattr(
        "tensor_grep.cli.main._latest_pypi_tensor_grep_version",
        lambda: "0.33.0",
        raising=False,
    )
    monkeypatch.setattr(
        "tensor_grep.cli.main._doctor_fresh_shell_path_value",
        lambda: str(bridge_tg.parent),
        raising=False,
    )
    _allow_native_frontdoor_checksum(monkeypatch)

    runner = CliRunner()
    result = runner.invoke(app, ["upgrade"])

    assert result.exit_code == 0
    assert popen_calls
    helper_code = popen_calls[0][2]
    compile(helper_code, "<scheduled-upgrade-helper>", "exec")
    assert "refresh native front door, stale PATH copies, and stale Python launchers" in helper_code
    assert '"show",' in helper_code
    assert "Removed stale tensor-grep Python package launchers from PATH" in helper_code
    assert popen_calls[0][7] == str(native_binary)
    helper_assets = json.loads(popen_calls[0][8])
    assert helper_assets == [
        {
            "url": (
                "https://github.com/oimiragieo/tensor-grep/releases/download/v0.33.0/"
                "tg-windows-amd64-cpu.exe"
            ),
            "flavor": "cpu",
            "asset_name": "tg-windows-amd64-cpu.exe",
            "sha256": _STUB_ASSET_SHA256,
        }
    ]
    assert json.loads(popen_calls[0][9]) == [str(bridge_tg)]


def test_schedule_windows_native_frontdoor_refresh_helper_verifies_checksum(monkeypatch, tmp_path):
    """Audit HIGH (2026-06-28): Path A deferred helper must contain sha256 verification
    and the injected payload entries must carry a sha256 field."""
    import tensor_grep.cli.main as m

    install_dir = tmp_path / ".tensor-grep"
    python_executable = install_dir / ".venv" / "Scripts" / "python.exe"
    native_binary = install_dir / "bin" / "tg.exe"
    python_executable.parent.mkdir(parents=True)
    native_binary.parent.mkdir(parents=True)
    python_executable.write_text("", encoding="utf-8")
    native_binary.write_text("old native", encoding="utf-8")

    popen_calls: list[list[str]] = []

    class _FakePopen:
        def __init__(self, cmd, **kwargs):
            popen_calls.append([str(a) for a in cmd])

    monkeypatch.setattr("subprocess.Popen", _FakePopen)
    monkeypatch.setattr("sys.executable", str(python_executable))
    monkeypatch.setattr(sys, "platform", "win32")
    monkeypatch.setattr("platform.machine", lambda: "AMD64")
    monkeypatch.setenv("TENSOR_GREP_NATIVE_FRONTDOOR_FLAVOR", "cpu")
    _allow_native_frontdoor_checksum(monkeypatch)

    m._schedule_windows_native_frontdoor_refresh(native_binary, "1.2.3")

    assert popen_calls, "Popen was not called"
    helper_code = popen_calls[0][2]
    # helper must contain sha256 verification logic
    assert "hashlib.sha256" in helper_code
    assert "checksum mismatch" in helper_code
    assert "sha256" in helper_code
    # injected payload must carry a sha256 per entry
    asset_payload = json.loads(popen_calls[0][7])
    assert asset_payload, "asset_payload must be non-empty"
    for entry in asset_payload:
        assert "sha256" in entry, f"entry {entry} missing sha256"
        assert entry["sha256"] == _STUB_ASSET_SHA256
    # Durability (audit review): the helper is a generated STRING — a syntax error would pass the
    # suite yet crash the real detached subprocess, and the substring asserts above survive a
    # security-defeating mutation. Compile it, and require the checksum gate BEFORE os.replace.
    compile(helper_code, "<path-a-refresh-helper>", "exec")
    assert "os.replace" in helper_code
    assert helper_code.index("hashlib.sha256") < helper_code.index("os.replace"), (
        "checksum gate must precede os.replace"
    )


def test_schedule_windows_self_upgrade_helper_verifies_checksum(monkeypatch, tmp_path):
    """Audit HIGH (2026-06-28): Path B deferred helper must contain sha256 verification
    and the injected native_asset payload entries must carry sha256 and asset_name."""
    # Simulate enough state to call _schedule_windows_self_upgrade directly via the
    # CliRunner upgrade path so we can inspect the helper code.
    install_dir = tmp_path / ".tensor-grep"
    python_executable = install_dir / ".venv" / "Scripts" / "python.exe"
    native_binary = install_dir / "bin" / "tg.exe"
    python_executable.parent.mkdir(parents=True)
    native_binary.parent.mkdir(parents=True)
    python_executable.write_text("", encoding="utf-8")
    native_binary.write_text("old native", encoding="utf-8")
    popen_calls: list[list[str]] = []

    locked_error = (
        "failed to remove file `...tg.exe`: "
        "The process cannot access the file because it is being used by another process. "
        "(os error 32)"
    )

    def _fake_run(cmd, **kwargs):
        command = [str(p) for p in cmd]
        if command[0] in ("uv", str(python_executable)) or command[:2] == [
            str(python_executable),
            "-m",
        ]:
            raise subprocess.CalledProcessError(returncode=1, cmd=command, stderr=locked_error)
        raise AssertionError(f"unexpected command: {command}")

    class _FakePopen:
        def __init__(self, cmd, **kwargs):
            popen_calls.append([str(a) for a in cmd])

    monkeypatch.setattr("subprocess.run", _fake_run)
    monkeypatch.setattr("subprocess.Popen", _FakePopen)
    monkeypatch.setattr("sys.executable", str(python_executable))
    monkeypatch.setattr("importlib.metadata.version", lambda _name: "1.1.0")
    monkeypatch.setattr(sys, "platform", "win32")
    monkeypatch.setattr("platform.machine", lambda: "AMD64")
    monkeypatch.setenv("TENSOR_GREP_NATIVE_FRONTDOOR_FLAVOR", "cpu")
    monkeypatch.setattr(
        "tensor_grep.cli.main._latest_pypi_tensor_grep_version",
        lambda: "1.2.0",
        raising=False,
    )
    _allow_native_frontdoor_checksum(monkeypatch)

    runner = CliRunner()
    result = runner.invoke(app, ["upgrade"])

    assert result.exit_code == 0, result.output
    assert popen_calls, "Popen was not called"
    helper_code = popen_calls[0][2]
    # helper must contain sha256 verification logic
    assert "hashlib.sha256" in helper_code
    assert "checksum mismatch" in helper_code
    # native asset payload must carry sha256 and asset_name per entry
    native_asset_payload = json.loads(popen_calls[0][8])
    assert native_asset_payload, "native_asset_payload must be non-empty"
    for entry in native_asset_payload:
        assert "sha256" in entry, f"entry {entry} missing sha256"
        assert "asset_name" in entry, f"entry {entry} missing asset_name"
        assert entry["sha256"] == _STUB_ASSET_SHA256
    # Durability (audit review): compile the generated helper string and require the checksum
    # gate BEFORE os.replace, so a syntax error or a moved/inverted check can't pass on substrings.
    compile(helper_code, "<path-b-self-upgrade-helper>", "exec")
    assert "os.replace" in helper_code
    assert helper_code.index("hashlib.sha256") < helper_code.index("os.replace"), (
        "checksum gate must precede os.replace"
    )


def test_schedule_windows_native_frontdoor_refresh_fails_closed_when_checksums_unavailable(
    monkeypatch, tmp_path
):
    """Audit HIGH (2026-06-28): Path A parent must raise when checksums can't be fetched."""
    import tensor_grep.cli.main as m

    install_dir = tmp_path / ".tensor-grep"
    python_executable = install_dir / ".venv" / "Scripts" / "python.exe"
    native_binary = install_dir / "bin" / "tg.exe"
    python_executable.parent.mkdir(parents=True)
    native_binary.parent.mkdir(parents=True)
    python_executable.write_text("", encoding="utf-8")
    native_binary.write_text("old native", encoding="utf-8")

    monkeypatch.setattr("sys.executable", str(python_executable))
    monkeypatch.setattr(sys, "platform", "win32")
    monkeypatch.setattr("platform.machine", lambda: "AMD64")
    monkeypatch.setenv("TENSOR_GREP_NATIVE_FRONTDOOR_FLAVOR", "cpu")
    monkeypatch.setattr(
        "tensor_grep.cli.main._fetch_native_frontdoor_checksums",
        lambda version: None,
        raising=False,
    )

    with pytest.raises(RuntimeError) as exc_info:
        m._schedule_windows_native_frontdoor_refresh(native_binary, "1.2.3")

    msg = str(exc_info.value).lower()
    assert "checksums" in msg or "unverified" in msg


def test_upgrade_schedules_windows_self_upgrade_fails_closed_when_checksums_unavailable(
    monkeypatch, tmp_path
):
    """Audit HIGH (2026-06-28): Path B parent must abort when checksums can't be fetched."""
    install_dir = tmp_path / ".tensor-grep"
    python_executable = install_dir / ".venv" / "Scripts" / "python.exe"
    python_executable.parent.mkdir(parents=True)
    python_executable.write_text("", encoding="utf-8")

    locked_error = (
        "failed to remove file `...tg.exe`: "
        "The process cannot access the file because it is being used by another process. "
        "(os error 32)"
    )

    def _fake_run(cmd, **kwargs):
        command = [str(p) for p in cmd]
        if command[0] in ("uv",) or command[:3] == [str(python_executable), "-m", "pip"]:
            raise subprocess.CalledProcessError(returncode=1, cmd=command, stderr=locked_error)
        raise AssertionError(f"unexpected command: {command}")

    monkeypatch.setattr("subprocess.run", _fake_run)
    monkeypatch.setattr("sys.executable", str(python_executable))
    monkeypatch.setattr("importlib.metadata.version", lambda _name: "1.1.0")
    monkeypatch.setattr(sys, "platform", "win32")
    monkeypatch.setattr("platform.machine", lambda: "AMD64")
    monkeypatch.setenv("TENSOR_GREP_NATIVE_FRONTDOOR_FLAVOR", "cpu")
    monkeypatch.setattr(
        "tensor_grep.cli.main._latest_pypi_tensor_grep_version",
        lambda: "1.2.0",
        raising=False,
    )
    monkeypatch.setattr(
        "tensor_grep.cli.main._fetch_native_frontdoor_checksums",
        lambda version: None,
        raising=False,
    )

    runner = CliRunner()
    result = runner.invoke(app, ["upgrade"])

    # A RuntimeError raised inside the except-handler propagates out of the command;
    # typer exits with code 1 in that case.
    assert result.exit_code != 0
    # The error text should indicate the fail-closed refusal
    full_output = (result.output or "") + str(result.exception or "")
    assert "checksums" in full_output.lower() or "unverified" in full_output.lower()


def test_native_frontdoor_download_helpers_use_timeouts(monkeypatch, tmp_path):
    # Reliability: the native front-door asset + CHECKSUMS downloads must be time-bounded, or a
    # stalled CDN read hangs install/upgrade indefinitely. Both now bound their request via
    # urlopen(..., timeout=...) directly (frontdoor-download-held-fd task: the asset download used
    # to have no timeout param on `urlretrieve` and instead wrapped the call in a process-global
    # `socket.setdefaulttimeout(60)` / restore pair; replacing `urlretrieve` with a held-fd
    # `urlopen` + chunked-read loop let that global-state workaround be dropped in favor of
    # `urlopen`'s own `timeout=` argument, matching the CHECKSUMS fetch below and removing the
    # only call in this module that relied on the process-global socket default being set).
    import tensor_grep.cli.main as m

    # CHECKSUMS fetch uses urlopen(timeout=...).
    checksum_timeouts: list = []

    class _Resp:
        def __enter__(self):
            return self

        def __exit__(self, *exc):
            return False

        def read(self):
            return b"deadbeef  tg-x.exe\n"

    def _fake_checksum_urlopen(url, timeout=None, *args, **kwargs):
        checksum_timeouts.append(timeout)
        return _Resp()

    monkeypatch.setattr("urllib.request.urlopen", _fake_checksum_urlopen)
    assert m._fetch_native_frontdoor_checksums("9.9.9") is not None
    assert checksum_timeouts and all(t is not None and t > 0 for t in checksum_timeouts)

    # Asset download now bounds its own urlopen() call with timeout=60 directly.
    seen_timeout: list = []

    def _fake_asset_urlopen(url, timeout=None, *args, **kwargs):
        seen_timeout.append(timeout)
        return _FakeUrlopenResponse(b"x")

    monkeypatch.setattr("urllib.request.urlopen", _fake_asset_urlopen)
    m._download_native_frontdoor_asset("https://example.test/tg-x.exe", tmp_path / "tg.exe")
    assert seen_timeout == [60], f"download did not pass a urlopen timeout: {seen_timeout}"


def test_upgrade_schedules_windows_helper_for_realworld_uv_pip_ensurepip_lock(
    monkeypatch, tmp_path
):
    calls: list[list[str]] = []
    popen_calls: list[list[str]] = []

    uv_locked_error = (
        "uv: Using Python 3.12.12 environment at: .tensor-grep\\.venv\n"
        "Resolved 57 packages in 3.09s\n"
        "Downloading tensor-grep (3.1MiB)\n"
        "Downloading cryptography (3.3MiB)\n"
        " Downloaded tensor-grep\n"
        " Downloaded cryptography\n"
        "Prepared 14 packages in 1.00s\n"
        "error: failed to remove file "
        "`C:\\Users\\oimir\\.tensor-grep\\.venv\\Lib\\site-packages\\../../Scripts\\tg.exe`: "
        "The process cannot access the file because it is being used by another process. "
        "(os error 32)"
    )
    pip_missing_error = (
        "C:\\Users\\oimir\\.tensor-grep\\.venv\\Scripts\\python.exe: No module named pip"
    )
    ensurepip_locked_error = (
        "ERROR: Could not install packages due to an OSError: [WinError 32] "
        "The process cannot access the file because it is being used by another process: "
        "'c:\\users\\oimir\\.tensor-grep\\.venv\\scripts\\tg.exe'\n"
        "Check the permissions."
    )

    def _fake_run(cmd, capture_output=True, text=True, check=True):
        command = list(cmd)
        calls.append(command)
        if command[0] == "uv":
            raise subprocess.CalledProcessError(
                returncode=1,
                cmd=command,
                stderr=uv_locked_error,
            )
        if command[:3] == ["python", "-m", "pip"]:
            raise subprocess.CalledProcessError(
                returncode=1,
                cmd=command,
                stderr=pip_missing_error,
            )
        if command[:3] == ["python", "-m", "ensurepip"]:
            raise subprocess.CalledProcessError(
                returncode=1,
                cmd=command,
                stderr=ensurepip_locked_error,
            )
        raise AssertionError(f"unexpected command: {command}")

    class _FakePopen:
        def __init__(
            self,
            cmd,
            stdout=None,
            stderr=None,
            stdin=None,
            close_fds=None,
            creationflags=0,
        ):
            popen_calls.append(list(cmd))

    monkeypatch.setattr("subprocess.run", _fake_run)
    monkeypatch.setattr("subprocess.Popen", _FakePopen)
    monkeypatch.setattr("sys.executable", "python")
    monkeypatch.setattr("importlib.metadata.version", lambda _name: "0.31.0")
    monkeypatch.setattr(sys, "platform", "win32")
    monkeypatch.setattr("platform.machine", lambda: "AMD64")
    monkeypatch.setenv("USERPROFILE", str(tmp_path))
    monkeypatch.setattr(
        "tensor_grep.cli.main._latest_pypi_tensor_grep_version",
        lambda: "0.32.0",
        raising=False,
    )
    _allow_native_frontdoor_checksum(monkeypatch)

    runner = CliRunner()
    result = runner.invoke(app, ["upgrade"])

    assert result.exit_code == 0
    assert calls[0][0] == "uv"
    assert any(cmd[:3] == ["python", "-m", "pip"] for cmd in calls)
    assert any(cmd[:3] == ["python", "-m", "ensurepip"] for cmd in calls)
    assert popen_calls
    assert "Windows is still using tg.exe" in result.output
    assert "Wait a few seconds, then run `tg --version` again." in result.output
    assert "Upgrade log:" in result.output


def test_cli_debug_prints_pipeline_routing_reason(monkeypatch):
    global _FAKE_WALK, _FAKE_BACKEND
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
    _patch_cli_dependencies(monkeypatch)

    runner = CliRunner()
    result = runner.invoke(app, ["search", "ERROR -> eventually ERROR", ".", "--debug", "--ltl"])

    assert result.exit_code == 0
    assert "[debug] routing.backend=FakeBackend reason=unit_test_fake_pipeline" in result.output


def test_cli_debug_passthrough_does_not_emit_tg_routing_banner(monkeypatch):
    def _fake_passthrough(self, paths, pattern, config=None):
        return 0

    monkeypatch.setattr(
        "tensor_grep.backends.ripgrep_backend.RipgrepBackend.is_available", lambda self: True
    )
    monkeypatch.setattr(
        "tensor_grep.backends.ripgrep_backend.RipgrepBackend.search_passthrough",
        _fake_passthrough,
    )

    runner = CliRunner()
    result = runner.invoke(app, ["search", "ERROR", ".", "--debug"])

    assert result.exit_code == 0
    assert "routing.backend=RipgrepBackend" not in result.output


def test_cli_stats_prints_summary_when_matches_found(monkeypatch):
    global _FAKE_WALK, _FAKE_BACKEND
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
    _patch_cli_dependencies(monkeypatch)

    runner = CliRunner()
    result = runner.invoke(app, ["search", "ERROR -> eventually ERROR", ".", "--stats", "--ltl"])

    assert result.exit_code == 0
    assert "[stats] scanned_files=1 matched_files=1 total_matches=1" in result.output
    assert "[stats] backend=FakeBackend reason=unit_test_fake_pipeline" in result.output


def test_cli_debug_prints_gpu_routing_details_when_available(monkeypatch):
    global _FAKE_WALK, _FAKE_BACKEND
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
    monkeypatch.setattr("tensor_grep.core.pipeline.Pipeline", _FakeGpuPipeline)
    monkeypatch.setattr("tensor_grep.io.directory_scanner.DirectoryScanner", _FakeScanner)

    runner = CliRunner()
    result = runner.invoke(app, ["search", "ERROR -> eventually ERROR", ".", "--debug", "--ltl"])

    assert result.exit_code == 0
    assert "[debug] routing.gpu_device_ids=[7, 3]" in result.output
    assert "routing.gpu_chunk_plan_mb=[(7, 256), (3, 512)]" in result.output


def test_cli_stats_prints_gpu_routing_details_when_available(monkeypatch):
    global _FAKE_WALK, _FAKE_BACKEND
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
    monkeypatch.setattr("tensor_grep.core.pipeline.Pipeline", _FakeGpuPipeline)
    monkeypatch.setattr("tensor_grep.io.directory_scanner.DirectoryScanner", _FakeScanner)

    runner = CliRunner()
    result = runner.invoke(app, ["search", "ERROR -> eventually ERROR", ".", "--stats", "--ltl"])

    assert result.exit_code == 0
    assert "[stats] gpu_device_ids=[7, 3]" in result.output
    assert "gpu_chunk_plan_mb=[(7, 256), (3, 512)]" in result.output


def test_cli_json_output_includes_routing_metadata_fields(monkeypatch):
    global _FAKE_WALK, _FAKE_BACKEND
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
    monkeypatch.setattr("tensor_grep.core.pipeline.Pipeline", _FakeGpuPipeline)
    monkeypatch.setattr("tensor_grep.io.directory_scanner.DirectoryScanner", _FakeScanner)

    runner = CliRunner()
    result = runner.invoke(
        app,
        [
            "search",
            "ERROR -> eventually ERROR",
            ".",
            "--gpu-device-ids",
            "7,3",
            "--ltl",
            "--format",
            "json",
        ],
    )

    assert result.exit_code == 0
    payload = json.loads(result.stdout)
    assert payload["version"] == 1
    assert payload["sidecar_used"] is False
    assert payload["routing_backend"] == "FakeBackend"
    assert payload["routing_reason"] == "unit_test_fake_pipeline"
    assert payload["requested_gpu_device_ids"] == [7, 3]
    assert payload["routing_gpu_device_ids"] == [7, 3]
    assert payload["routing_gpu_chunk_plan_mb"] == [
        {"device_id": 7, "chunk_mb": 256},
        {"device_id": 3, "chunk_mb": 512},
    ]
    assert payload["routing_distributed"] is True
    assert payload["routing_worker_count"] == 2


def test_cli_json_output_should_surface_distributed_worker_metadata_from_backend(monkeypatch):
    global _FAKE_WALK, _FAKE_BACKEND
    _FAKE_WALK = {".": ["a.log"]}
    _FAKE_BACKEND = _FakeBackend(
        results_by_file={
            "a.log": SearchResult(
                matches=[MatchLine(line_number=1, text="ERROR", file="a.log")],
                total_files=1,
                total_matches=1,
                routing_distributed=True,
                routing_worker_count=2,
            )
        }
    )
    monkeypatch.setattr("tensor_grep.core.pipeline.Pipeline", _FakeGpuPipeline)
    monkeypatch.setattr("tensor_grep.io.directory_scanner.DirectoryScanner", _FakeScanner)

    runner = CliRunner()
    result = runner.invoke(
        app, ["search", "ERROR -> eventually ERROR", ".", "--ltl", "--format", "json"]
    )

    assert result.exit_code == 0
    payload = json.loads(result.stdout)
    assert payload["version"] == 1
    assert payload["sidecar_used"] is False
    assert payload["routing_backend"] == "FakeBackend"
    assert payload["routing_reason"] == "unit_test_fake_pipeline"
    assert payload["routing_gpu_device_ids"] == [7, 3]
    assert payload["routing_gpu_chunk_plan_mb"] == [
        {"device_id": 7, "chunk_mb": 256},
        {"device_id": 3, "chunk_mb": 512},
    ]
    assert payload["routing_distributed"] is True
    assert payload["routing_worker_count"] == 2


def test_cli_json_output_should_include_aggregated_matched_file_metadata(monkeypatch):
    global _FAKE_WALK, _FAKE_BACKEND
    _FAKE_WALK = {".": ["a.log", "b.log"]}
    _FAKE_BACKEND = _FakeBackend(
        results_by_file={
            "a.log": SearchResult(
                matches=[MatchLine(line_number=1, text="ERROR one", file="a.log")],
                total_files=1,
                total_matches=1,
            ),
            "b.log": SearchResult(
                matches=[MatchLine(line_number=2, text="ERROR two", file="b.log")],
                total_files=1,
                total_matches=1,
            ),
        }
    )
    _patch_cli_dependencies(monkeypatch)

    runner = CliRunner()
    result = runner.invoke(app, ["search", "ERROR", ".", "--format", "json"])

    assert result.exit_code == 0
    payload = json.loads(result.stdout)
    assert sorted(payload["matched_file_paths"]) == ["a.log", "b.log"]
    assert payload["match_counts_by_file"] == {"a.log": 1, "b.log": 1}


def test_cli_json_output_should_preserve_ast_range_and_meta_variables(monkeypatch):
    global _FAKE_WALK, _FAKE_BACKEND
    _FAKE_WALK = {".": ["a.py"]}
    _FAKE_BACKEND = _FakeBackend(
        results_by_file={
            "a.py": SearchResult(
                matches=[
                    MatchLine(
                        line_number=1,
                        text="def hello(name):",
                        file="a.py",
                        range={
                            "byteOffset": {"start": 0, "end": 16},
                            "start": {"line": 0, "column": 0},
                            "end": {"line": 0, "column": 16},
                        },
                        meta_variables={
                            "single": {"F": {"text": "hello"}},
                            "multi": {"ARGS": [{"text": "name"}]},
                        },
                    )
                ],
                total_files=1,
                total_matches=1,
            )
        }
    )
    _patch_cli_dependencies(monkeypatch)

    runner = CliRunner()
    result = runner.invoke(app, ["search", "def $F($$$ARGS):", ".", "--ast", "--format", "json"])

    assert result.exit_code == 0
    payload = json.loads(result.stdout)
    assert payload["matches"][0]["range"] == {
        "byteOffset": {"start": 0, "end": 16},
        "start": {"line": 0, "column": 0},
        "end": {"line": 0, "column": 16},
    }
    assert payload["matches"][0]["metaVariables"] == {
        "single": {"F": {"text": "hello"}},
        "multi": {"ARGS": [{"text": "name"}]},
    }
