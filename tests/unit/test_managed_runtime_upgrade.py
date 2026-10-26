import subprocess
import sys
from pathlib import Path

import pytest
from typer.testing import CliRunner

from tensor_grep.cli import main as cli_main
from tensor_grep.cli import managed_runtime_upgrade as upgrade
from tensor_grep.cli import session_daemon


def _patch_managed_upgrade(monkeypatch, tmp_path, previous_version, latest_version) -> Path:
    executable = tmp_path / ".tensor-grep" / ".venv" / "python.exe"
    monkeypatch.setattr(sys, "platform", "win32")
    monkeypatch.setattr(sys, "executable", str(executable))
    monkeypatch.setattr("importlib.metadata.version", lambda _name: previous_version)
    monkeypatch.setattr(cli_main, "_upgrade_running_session_daemon_snapshot", lambda: {})
    monkeypatch.setattr(cli_main, "_latest_pypi_tensor_grep_version", lambda: latest_version)
    monkeypatch.setattr(
        cli_main._native_frontdoor,
        "managed_windows_install_root",
        lambda _executable: tmp_path / ".tensor-grep",
    )
    monkeypatch.setattr(
        cli_main,
        "_run_upgrade",
        lambda _attempts: (_ for _ in ()).throw(
            AssertionError("managed Windows upgrades must not replace the live environment")
        ),
    )
    return executable


@pytest.mark.parametrize(
    ("staged", "expected_restart_count", "expected_message"),
    [
        (
            cli_main._native_frontdoor.ManagedWindowsRuntimeInstall("a" * 32, "1.1.0", "uv", "cpu"),
            1,
            "Successfully upgraded tensor-grep to 1.1.0 via managed staging.",
        ),
        (
            cli_main._native_frontdoor.ManagedWindowsRuntimeInstall(
                "b" * 32,
                "1.1.0",
                "uv",
                "cpu",
                activation_pending=True,
                activation_log=Path("activation.log"),
            ),
            0,
            "activation will follow after this command exits",
        ),
    ],
    ids=["activated", "activation-pending"],
)
def test_upgrade_WhenManagedWindowsRuntimeExists_StagesWithoutReplacingLiveEnvironment(
    monkeypatch, tmp_path, staged, expected_restart_count, expected_message
):
    restarts: list[tuple[object, ...]] = []
    staged_versions: list[tuple[str, str]] = []
    _patch_managed_upgrade(monkeypatch, tmp_path, "1.0.0", "1.1.0")

    def stage_runtime(_executable, package_spec, version):
        staged_versions.append((package_spec, version))
        return staged

    monkeypatch.setattr(cli_main._native_frontdoor, "stage_managed_windows_runtime", stage_runtime)
    monkeypatch.setattr(
        upgrade, "_restart_managed_session_daemon", lambda *args: restarts.append(args)
    )

    result = CliRunner().invoke(cli_main.app, ["upgrade"])

    assert result.exit_code == 0
    assert staged_versions == [("tensor-grep==1.1.0", "1.1.0")]
    assert len(restarts) == expected_restart_count
    assert expected_message in result.output


def test_upgrade_WhenManagedRuntimeAlreadyCurrent_DoesNotRestartHealthyDaemon(
    monkeypatch, tmp_path
):
    _patch_managed_upgrade(monkeypatch, tmp_path, "1.1.0", "1.1.0")
    monkeypatch.setattr(
        cli_main._native_frontdoor,
        "selected_managed_windows_version",
        lambda _root: "1.1.0",
    )
    monkeypatch.setattr(
        cli_main._native_frontdoor,
        "_stable_dispatcher_matches_release",
        lambda _version, _path: True,
    )
    monkeypatch.setattr(
        cli_main, "_upgrade_running_session_daemon_snapshot", lambda: {"root": "repo"}
    )
    monkeypatch.setattr(
        cli_main._native_frontdoor,
        "stage_managed_windows_runtime",
        lambda *_args: (_ for _ in ()).throw(
            AssertionError("an up-to-date managed runtime should not be restaged")
        ),
    )
    monkeypatch.setattr(
        upgrade,
        "_restart_managed_session_daemon",
        lambda *_args: (_ for _ in ()).throw(
            AssertionError("an up-to-date no-op must not interrupt a healthy daemon")
        ),
    )

    result = CliRunner().invoke(cli_main.app, ["upgrade"])

    assert result.exit_code == 0
    assert "already at the latest PyPI version (1.1.0)" in result.output


@pytest.mark.parametrize(
    ("selected_version", "launcher_matches"),
    [(None, False), ("1.1.0", False)],
    ids=["legacy-runtime", "stale-stable-launcher"],
)
def test_upgrade_WhenAlreadyCurrentLegacyRuntimeOrLauncherNeedsRepair_StagesGeneration(
    monkeypatch, tmp_path, selected_version, launcher_matches
):
    _patch_managed_upgrade(monkeypatch, tmp_path, "1.1.0", "1.1.0")
    monkeypatch.setattr(
        cli_main._native_frontdoor,
        "selected_managed_windows_version",
        lambda _root: selected_version,
    )
    launcher_checks = []

    def launcher_matches_release(version, path):
        launcher_checks.append((version, path))
        return launcher_matches

    monkeypatch.setattr(
        cli_main._native_frontdoor,
        "_stable_dispatcher_matches_release",
        launcher_matches_release,
    )
    staged = cli_main._native_frontdoor.ManagedWindowsRuntimeInstall("c" * 32, "1.1.0", "uv", "cpu")
    staged_versions = []

    def stage_runtime(_executable, package_spec, version):
        staged_versions.append((package_spec, version))
        return staged

    monkeypatch.setattr(cli_main._native_frontdoor, "stage_managed_windows_runtime", stage_runtime)

    result = CliRunner().invoke(cli_main.app, ["upgrade"])

    assert result.exit_code == 0
    assert staged_versions == [("tensor-grep==1.1.0", "1.1.0")]
    assert "Successfully upgraded tensor-grep to 1.1.0 via managed staging." in result.output
    assert len(launcher_checks) == (0 if selected_version is None else 1)


def test_upgrade_WhenCurrentLauncherChecksumReadFails_RefusesWithoutTraceback(
    monkeypatch, tmp_path
):
    _patch_managed_upgrade(monkeypatch, tmp_path, "1.1.0", "1.1.0")
    monkeypatch.setattr(
        cli_main._native_frontdoor,
        "selected_managed_windows_version",
        lambda _root: "1.1.0",
    )

    def refuse_checksum(_version, _path):
        raise PermissionError("launcher read refused")

    monkeypatch.setattr(
        cli_main._native_frontdoor, "_stable_dispatcher_matches_release", refuse_checksum
    )
    monkeypatch.setattr(
        cli_main._native_frontdoor,
        "stage_managed_windows_runtime",
        lambda *_args: pytest.fail("untrusted launcher verification must stop before staging"),
    )

    result = CliRunner().invoke(cli_main.app, ["upgrade"])

    assert result.exit_code == 1
    assert "cannot safely inspect the current managed runtime" in result.output
    assert "Traceback" not in result.output


def test_upgrade_WhenManagedRuntimeVersionIsUnknown_FailsWithoutInPlaceMutation(
    monkeypatch, tmp_path
):
    _patch_managed_upgrade(monkeypatch, tmp_path, "1.0.0", None)

    result = CliRunner().invoke(cli_main.app, ["upgrade"])

    assert result.exit_code == 1
    assert "latest PyPI version could not be verified" in result.output


def test_upgrade_WhenManagedStageFailsWithFileLock_DoesNotScheduleLegacyUpdater(
    monkeypatch, tmp_path
):
    _patch_managed_upgrade(monkeypatch, tmp_path, "1.0.0", "1.1.0")
    monkeypatch.setattr(
        cli_main._native_frontdoor,
        "stage_managed_windows_runtime",
        lambda *_args: (_ for _ in ()).throw(
            RuntimeError("[WinError 32] file is being used by another process")
        ),
    )
    monkeypatch.setattr(
        cli_main.subprocess,
        "Popen",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            AssertionError("managed staging failure must not schedule in-place replacement")
        ),
    )

    result = CliRunner().invoke(cli_main.app, ["upgrade"])

    assert result.exit_code == 1
    assert "Error occurred while staging the managed Windows runtime upgrade." in result.output


def test_upgrade_WhenActivationIsPending_PrintsPowerShellSafeDaemonCommands(monkeypatch, tmp_path):
    executable = _patch_managed_upgrade(monkeypatch, tmp_path, "1.0.0", "1.1.0")
    monkeypatch.setattr(
        cli_main,
        "_upgrade_running_session_daemon_snapshot",
        lambda: {"root": "C:\\Users\\O'Neil\\repo with spaces"},
    )
    monkeypatch.setattr(
        cli_main._native_frontdoor,
        "stage_managed_windows_runtime",
        lambda *_args: cli_main._native_frontdoor.ManagedWindowsRuntimeInstall(
            "c" * 32,
            "1.1.0",
            "uv",
            "cpu",
            activation_pending=True,
        ),
    )

    result = CliRunner().invoke(cli_main.app, ["upgrade"])

    launcher = str(executable.parent.parent / "bin" / "tg.exe")
    assert result.exit_code == 0
    assert (
        f"& '{launcher}' 'session' 'daemon' 'stop' 'C:\\Users\\O''Neil\\repo with spaces'"
        in result.output
    )
    assert (
        f"& '{launcher}' 'session' 'daemon' 'start' 'C:\\Users\\O''Neil\\repo with spaces'"
        in result.output
    )


def test_managedUpgrade_WhenDaemonWasRunning_UsesPinnedVerifiedLauncher(
    monkeypatch, tmp_path
) -> None:
    events: list[str] = []
    launches: list[list[str]] = []
    pinned: list[Path] = []
    native_path = tmp_path / "bin" / "tg.exe"

    def stop(root: str) -> dict[str, object]:
        events.append(f"stop:{root}")
        return {"stopped": True}

    def launch(command, **_kwargs):
        launches.append([str(part) for part in command])
        events.append(f"launch:{command[4]}")
        return subprocess.CompletedProcess(command, 0, stdout='{"running":true}', stderr="")

    monkeypatch.setattr(session_daemon, "stop_session_daemon", stop)
    monkeypatch.setattr(
        upgrade._native_frontdoor,
        "_pin_managed_executable",
        lambda path, _held: pinned.append(path),
    )
    monkeypatch.setattr(
        upgrade._native_frontdoor, "selected_managed_windows_version", lambda _root: "1.1.0"
    )
    monkeypatch.setattr(cli_main, "_native_tg_version", lambda _path: "1.1.0")
    monkeypatch.setattr(cli_main, "_native_tg_version_matches", lambda *_args: True)
    monkeypatch.setattr(upgrade._self.subprocess, "run", launch)

    result = upgrade._restart_managed_session_daemon({"root": "repo"}, native_path, "1.1.0")

    assert pinned == [native_path]
    assert events == ["stop:repo", "launch:repo"]
    assert launches == [[str(native_path), "session", "daemon", "start", "repo", "--json"]]
    assert result == "Session daemon restarted after managed upgrade for repo."


@pytest.mark.parametrize(
    ("selected_version", "launcher_version", "expected_message"),
    [("1.1.0", "1.0.0", "did not report 1.1.0"), ("1.0.0", "1.1.0", "selected runtime was")],
)
def test_managedUpgrade_WhenSelectedOrLauncherVersionDiffers_LeavesDaemonUntouched(
    monkeypatch, tmp_path, selected_version, launcher_version, expected_message
) -> None:
    monkeypatch.setattr(upgrade._native_frontdoor, "_pin_managed_executable", lambda *_args: None)
    monkeypatch.setattr(
        upgrade._native_frontdoor,
        "selected_managed_windows_version",
        lambda _root: selected_version,
    )
    monkeypatch.setattr(cli_main, "_native_tg_version", lambda _path: launcher_version)
    monkeypatch.setattr(
        cli_main,
        "_native_tg_version_matches",
        lambda expected, actual: expected == actual,
    )
    monkeypatch.setattr(
        session_daemon,
        "stop_session_daemon",
        lambda *_args: (_ for _ in ()).throw(
            AssertionError("version mismatch must not stop the daemon")
        ),
    )

    native_path = tmp_path / "bin" / "tg.exe"
    result = upgrade._restart_managed_session_daemon({"root": "repo"}, native_path, "1.1.0")

    assert expected_message in result


def test_managedUpgrade_WhenDaemonShutdownIsUnconfirmed_DoesNotStartReplacement(
    monkeypatch, tmp_path
) -> None:
    monkeypatch.setattr(
        upgrade._native_frontdoor,
        "_pin_managed_executable",
        lambda *_args: None,
    )
    monkeypatch.setattr(
        upgrade._native_frontdoor, "selected_managed_windows_version", lambda _root: "1.1.0"
    )
    monkeypatch.setattr(cli_main, "_native_tg_version", lambda _path: "1.1.0")
    monkeypatch.setattr(cli_main, "_native_tg_version_matches", lambda *_args: True)
    monkeypatch.setattr(
        session_daemon,
        "stop_session_daemon",
        lambda _root: {"stopped": False, "unconfirmed_reason": "still_running"},
    )
    monkeypatch.setattr(
        upgrade._self.subprocess,
        "run",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            AssertionError("do not start while the old daemon may still live")
        ),
    )

    native_path = tmp_path / "bin" / "tg.exe"
    result = upgrade._restart_managed_session_daemon({"root": "repo"}, native_path, "1.1.0")

    assert "could not confirm session daemon shutdown" in result


def test_managedUpgrade_WhenDaemonPinFails_ReturnsVisibleWarning(monkeypatch, tmp_path):
    def fail_pin(_path, _held):
        raise OSError("launcher pin refused")

    monkeypatch.setattr(upgrade._native_frontdoor, "_pin_managed_executable", fail_pin)

    result = upgrade._restart_managed_session_daemon({"root": "repo"}, tmp_path / "tg.exe", "1.1.0")

    assert result == (
        "WARNING: managed upgrade could not restart the session daemon for repo: "
        "launcher pin refused"
    )
