"""Safe Windows managed-runtime upgrade routing and daemon handoff."""

from __future__ import annotations

import json
import sys
from contextlib import ExitStack
from pathlib import Path
from typing import Any

import typer

from tensor_grep.cli import native_frontdoor as _native_frontdoor
from tensor_grep.cli._main_binding import _self as _self


class ManagedWindowsUpgradeError(RuntimeError):
    """A verified managed-runtime update failed and must not fall back in place."""


def try_managed_windows_upgrade(
    executable: str,
    previous_version: str | None,
    latest_version: str | None,
    package_spec: str,
    daemon_snapshot: dict[str, Any] | None,
) -> str | None:
    if not sys.platform.startswith("win"):
        return None
    try:
        install_root = _native_frontdoor.managed_windows_install_root(executable)
    except RuntimeError as exc:
        raise ManagedWindowsUpgradeError(str(exc)) from exc
    if install_root is None:
        return None
    if latest_version is None:
        raise ManagedWindowsUpgradeError(
            "cannot safely upgrade the managed Windows runtime because the latest "
            "PyPI version could not be verified"
        )
    if previous_version == latest_version:
        return f"tensor-grep is already at the latest PyPI version ({latest_version})."
    if previous_version is not None and _native_frontdoor._is_version_newer(
        previous_version, latest_version
    ):
        return (
            f"tensor-grep {previous_version} is installed; PyPI metadata reported "
            f"{latest_version}, so no downgrade was attempted."
        )
    try:
        staged = _native_frontdoor.stage_managed_windows_runtime(
            executable, package_spec, latest_version
        )
    except Exception as exc:
        raise ManagedWindowsUpgradeError(f"managed runtime staging failed: {exc}") from exc
    if staged.activation_pending:
        messages = [
            f"Verified tensor-grep {latest_version} staged; activation will follow after this command exits."
        ]
        root = str((daemon_snapshot or {}).get("root") or "").strip()
        if root:
            launcher = str(install_root / "bin" / "tg.exe")
            messages.extend([
                "After activation, restart the pre-existing session daemon with:",
                f"  {_powershell_command([launcher, 'session', 'daemon', 'stop', root])}",
                f"  {_powershell_command([launcher, 'session', 'daemon', 'start', root])}",
            ])
        if staged.activation_log is not None:
            messages.append(f"Activation log: {staged.activation_log}")
        return "\n".join(messages)

    messages = [
        f"Successfully upgraded tensor-grep to {latest_version} via managed staging.",
        f"Native asset flavor: {staged.native_flavor}.",
    ]
    daemon_message = _restart_managed_session_daemon(
        daemon_snapshot, install_root / "bin" / "tg.exe", latest_version
    )
    if daemon_message:
        messages.append(daemon_message)
    return "\n".join(messages)


def handle_managed_windows_upgrade(
    executable: str,
    previous_version: str | None,
    latest_version: str | None,
    package_spec: str,
    daemon_snapshot: dict[str, Any] | None,
) -> bool:
    message = try_managed_windows_upgrade(
        executable, previous_version, latest_version, package_spec, daemon_snapshot
    )
    if message is None:
        return False
    typer.echo(message)
    return True


def exit_managed_upgrade_error(error: ManagedWindowsUpgradeError) -> None:
    typer.echo("Error occurred while staging the managed Windows runtime upgrade.", err=True)
    typer.echo(str(error), err=True)
    sys.exit(1)


def _powershell_command(arguments: list[str]) -> str:
    return "& " + " ".join("'" + argument.replace("'", "''") + "'" for argument in arguments)


def _restart_managed_session_daemon(
    snapshot: dict[str, Any] | None, native_path: Path, expected_version: str
) -> str | None:
    if not snapshot:
        return None
    root = str(snapshot.get("root") or "").strip()
    if not root:
        return None
    try:
        from tensor_grep.cli._index_lock import index_lock
        from tensor_grep.cli.session_daemon import stop_session_daemon

        with ExitStack() as held:
            _native_frontdoor._pin_managed_executable(native_path, held)
            install_root = native_path.parent.parent
            with index_lock(install_root / "runtime-selection.json"):
                selected_version = _native_frontdoor.selected_managed_windows_version(install_root)
                if selected_version != expected_version:
                    return (
                        f"WARNING: managed upgrade activated, but the selected runtime was "
                        f"{selected_version or 'unknown'} instead of {expected_version}; "
                        "the session daemon was left untouched."
                    )
                installed_version = _self._native_tg_version(native_path)
                if not _self._native_tg_version_matches(expected_version, installed_version):
                    return (
                        f"WARNING: managed upgrade activated, but the pinned launcher did not "
                        f"report {expected_version}; the session daemon was left untouched."
                    )
                stopped = stop_session_daemon(root)
                if stopped.get("stopped") is not True:
                    return f"WARNING: managed upgrade could not confirm session daemon shutdown for {root}."
                started = _self.subprocess.run(
                    [str(native_path), "session", "daemon", "start", root, "--json"],
                    capture_output=True,
                    text=True,
                    encoding="utf-8",
                    errors="replace",
                    timeout=60,
                    check=False,
                )
                payload = json.loads(started.stdout)
                if (
                    started.returncode == 0
                    and isinstance(payload, dict)
                    and payload.get("running") is True
                    and _native_frontdoor.selected_managed_windows_version(install_root)
                    == expected_version
                ):
                    # The native start path only reports running after _probe_daemon accepts its
                    # package_version, and the selector lock prevents a concurrent generation swap.
                    return f"Session daemon restarted after managed upgrade for {root}."
                return (
                    f"WARNING: managed upgrade stopped the session daemon but did not confirm a "
                    f"{expected_version} daemon restart for {root} (exit code {started.returncode})."
                )
    except Exception as exc:
        return f"WARNING: managed upgrade could not restart the session daemon for {root}: {exc}"
