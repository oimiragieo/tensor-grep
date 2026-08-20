"""Windows launcher repair: stale `tg.exe` COM bridges, Python-Scripts shadows, user PATH.

The tail half of the `cli/native_frontdoor.py` split (2026-08-20,
`docs/design/2026-08-19-split-floor-escape.md`). Everything that keeps a Windows install
resolving `tg` to the managed native front door rather than to a stale
`...\\Python\\Scripts\\tg.exe`: the COM-bridge marker files, the Scripts-launcher scan and
removal, the user-PATH reordering, the subprocess-resolution blocker probe, and the scheduled
background refresh. Nothing in `native_frontdoor.py` calls back into this module.

`_self` is `cli/main.py`'s module object, imported from `cli/_main_binding` -- notably
`_self.subprocess`, which the test suite patches on `main`. Read that module's docstring before
adding a bare cross-module reference.
"""

import json
import os
import shutil
import sys
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from uuid import uuid4

from tensor_grep.cli._main_binding import _self as _self
from tensor_grep.cli.native_frontdoor import (
    _install_release_native_frontdoor as _install_release_native_frontdoor,
)
from tensor_grep.cli.native_frontdoor import (
    _managed_native_frontdoor_path as _managed_native_frontdoor_path,
)
from tensor_grep.cli.native_frontdoor import (
    _requested_native_frontdoor_flavor as _requested_native_frontdoor_flavor,
)
from tensor_grep.cli.native_frontdoor import (
    _windows_managed_native_bin_dir as _windows_managed_native_bin_dir,
)

# Cross-module reads back into the head half of the split. These are NOT patched on
# `main`, so a direct import is correct -- anything the test suite patches is reached
# through `_self` instead.
from tensor_grep.cli.native_frontdoor import (
    _WindowsStalePythonLauncher as _WindowsStalePythonLauncher,
)
from tensor_grep.cli.native_frontdoor import (
    _WindowsUnownedPythonLauncher as _WindowsUnownedPythonLauncher,
)


def _same_path(left: Path, right: Path) -> bool:
    try:
        return left.resolve() == right.resolve()
    except OSError:
        return left == right


_WINDOWS_EXE_BRIDGE_MARKER = "tg.exe.tensor-grep-bridge"
_WINDOWS_EXE_BRIDGE_MARKER_CONTENT = "tensor-grep managed tg.exe bridge\n"


def _windows_exe_bridge_marker_path(path: Path) -> Path:
    return path.with_name(_WINDOWS_EXE_BRIDGE_MARKER)


def _write_windows_exe_bridge_marker(path: Path) -> None:
    if path.name.lower() == "tg.exe":
        _windows_exe_bridge_marker_path(path).write_text(
            _WINDOWS_EXE_BRIDGE_MARKER_CONTENT,
            encoding="ascii",
        )


def _windows_managed_compat_shim_dirs() -> set[str]:
    if not sys.platform.startswith("win"):
        return set()
    homes: list[Path] = []
    for env_name in ("USERPROFILE", "HOME"):
        value = os.environ.get(env_name)
        if not value:
            continue
        home = Path(value)
        if home not in homes:
            homes.append(home)
    dirs: set[str] = set()
    for home in homes:
        dirs.add(_windows_path_part_key(str(home / "bin")))
        dirs.add(_windows_path_part_key(str(home / ".local" / "bin")))
    return dirs


def _windows_stale_tensor_grep_com_bridges(expected_version: str, native_path: Path) -> list[Path]:
    if not sys.platform.startswith("win"):
        return []

    def _add_path(path: Path) -> None:
        try:
            key = str(path.resolve()).lower()
        except OSError:
            key = str(path).lower()
        if key in seen:
            return
        seen.add(key)
        bridges.append(path)

    def _directory_has_tensor_grep_shim(directory: Path) -> bool:
        for shim_name in ("tg.cmd", "tg.ps1", "tg"):
            shim_path = directory / shim_name
            if not shim_path.is_file():
                continue
            version = _self._doctor_tg_candidate_version(shim_path)
            if _self._doctor_tg_version_looks_like_tensor_grep(version):
                return True
        return False

    path_values = [os.environ.get("PATH", "")]
    fresh_path = _self._doctor_fresh_shell_path_value()
    if fresh_path and fresh_path not in path_values:
        path_values.append(fresh_path)

    managed_compat_dirs = _windows_managed_compat_shim_dirs()
    bridges: list[Path] = []
    seen: set[str] = set()
    for path_value in path_values:
        for candidate in _self._doctor_path_tg_candidates(path_value):
            candidate_path = Path(str(candidate.get("path") or ""))
            candidate_name = candidate_path.name.lower()
            if candidate_name not in {"tg.com", "tg.exe"}:
                continue
            if _same_path(candidate_path, native_path):
                continue
            version = candidate.get("version")
            if not _self._doctor_tg_version_looks_like_tensor_grep(version):
                continue
            if candidate_name == "tg.exe" and not str(version).strip().lower().startswith("tg "):
                continue
            if _self._native_tg_version_matches(expected_version, version):
                continue
            _add_path(candidate_path)

        for entry in path_value.split(_self._doctor_path_list_separator(path_value)):
            if not entry:
                continue
            directory = Path(entry)
            if _windows_path_part_key(str(directory)) not in managed_compat_dirs:
                continue
            target = directory / "tg.exe"
            if _same_path(target, native_path) or target.exists():
                continue
            if _directory_has_tensor_grep_shim(directory):
                _add_path(target)
    return bridges


def _windows_python_install_scripts_executable(candidate: Path) -> Path | None:
    if not sys.platform.startswith("win"):
        return None
    if candidate.name.lower() != "tg.exe":
        return None
    if candidate.parent.name.lower() != "scripts":
        return None
    parts = tuple(part.lower() for part in candidate.parts)
    if ".tensor-grep" in parts or ".venv" in parts or "venv" in parts:
        return None
    python_executable = candidate.parent.parent / "python.exe"
    if not python_executable.is_file():
        return None
    return python_executable


def _windows_python_scripts_tensor_grep_package_version(
    python_executable: Path,
    launcher_path: Path,
) -> str | None:
    try:
        result = _self.subprocess.run(
            [str(python_executable), "-m", "pip", "show", "-f", "tensor-grep"],
            capture_output=True,
            text=True,
            timeout=10,
        )
    except Exception:
        return None
    if result.returncode != 0:
        return None
    location: Path | None = None
    version: str | None = None
    owns_launcher = False
    try:
        resolved_launcher = launcher_path.resolve()
    except OSError:
        resolved_launcher = launcher_path
    for line in result.stdout.splitlines():
        if line.lower().startswith("location:"):
            raw_location = line.split(":", 1)[1].strip()
            if raw_location:
                location = Path(raw_location)
            continue
        if line.lower().startswith("version:"):
            version = line.split(":", 1)[1].strip() or "installed"
            continue
        if location is None:
            continue
        relative_file = line.strip()
        if not relative_file or relative_file.lower() == "files:":
            continue
        try:
            resolved_file = (location / relative_file).resolve()
        except OSError:
            resolved_file = location / relative_file
        if _same_path(resolved_file, resolved_launcher):
            owns_launcher = True
    if not owns_launcher:
        return None
    return version or "installed"


def _windows_tensor_grep_python_launcher_scan(
    expected_version: str,
    native_path: Path,
) -> tuple[list[_WindowsStalePythonLauncher], list[_WindowsUnownedPythonLauncher]]:
    if not sys.platform.startswith("win"):
        return [], []

    path_values = [os.environ.get("PATH", "")]
    fresh_path = _self._doctor_fresh_shell_path_value()
    if fresh_path and fresh_path not in path_values:
        path_values.append(fresh_path)

    stale_launchers: list[_WindowsStalePythonLauncher] = []
    unowned_launchers: list[_WindowsUnownedPythonLauncher] = []
    seen: set[str] = set()
    for path_value in path_values:
        native_seen = False
        for candidate in _self._doctor_path_tg_candidates(path_value):
            candidate_path = Path(str(candidate.get("path") or ""))
            if _same_path(candidate_path, native_path):
                native_seen = True
                continue
            python_executable = _windows_python_install_scripts_executable(candidate_path)
            if python_executable is None:
                continue
            try:
                key = str(candidate_path.resolve()).lower()
            except OSError:
                key = str(candidate_path).lower()
            if key in seen:
                continue

            version = candidate.get("version")
            shadows_managed_native = not native_seen
            if (
                _self._native_tg_version_matches(expected_version, version)
                and not shadows_managed_native
            ):
                continue

            if not _self._doctor_tg_version_looks_like_tensor_grep(version):
                if version:
                    continue
            package_version = _windows_python_scripts_tensor_grep_package_version(
                python_executable,
                candidate_path,
            )
            if package_version is None:
                if not native_seen:
                    seen.add(key)
                    unowned_launchers.append(
                        _WindowsUnownedPythonLauncher(
                            path=candidate_path,
                            version=version,
                        )
                    )
                continue

            seen.add(key)
            stale_launchers.append(
                _WindowsStalePythonLauncher(
                    path=candidate_path,
                    python_executable=python_executable,
                    version=version,
                    package_version=package_version,
                )
            )
    return stale_launchers, unowned_launchers


def _remove_windows_stale_tensor_grep_python_launchers(
    expected_version: str,
    native_path: Path,
) -> str | None:
    stale_launchers, unowned_launchers = _windows_tensor_grep_python_launcher_scan(
        expected_version,
        native_path,
    )
    if not stale_launchers and not unowned_launchers:
        return None

    removed: list[str] = []
    backed_up_orphans: list[str] = []
    failed: list[str] = []
    for launcher in stale_launchers:
        reason = launcher.version or (
            f"tensor-grep package {launcher.package_version}"
            if launcher.package_version
            else "<unreadable --version>"
        )
        try:
            result = _self.subprocess.run(
                [
                    str(launcher.python_executable),
                    "-m",
                    "pip",
                    "uninstall",
                    "-y",
                    "tensor-grep",
                ],
                capture_output=True,
                text=True,
                timeout=120,
            )
            if result.returncode != 0:
                raise RuntimeError(
                    "pip uninstall tensor-grep failed"
                    + (f": {result.stderr.strip()}" if result.stderr else "")
                )
            launcher.path.unlink(missing_ok=True)
            if launcher.path.exists():
                raise OSError("launcher still exists after cleanup")
            removed.append(f"- {launcher.path} ({reason})")
        except Exception as exc:
            failed.append(f"- {launcher.path} ({reason}): {exc}")

    remaining_unowned_launchers: list[_WindowsUnownedPythonLauncher] = []
    for unowned_launcher in unowned_launchers:
        reason = unowned_launcher.version or "<unreadable --version>"
        if unowned_launcher.version is None or not _self._doctor_tg_version_looks_like_tensor_grep(
            unowned_launcher.version
        ):
            remaining_unowned_launchers.append(unowned_launcher)
            continue
        backup_path = unowned_launcher.path.with_name(
            f"{unowned_launcher.path.name}.orphaned-tensor-grep-"
            f"{datetime.now(UTC).strftime('%Y%m%d%H%M%S')}-{uuid4().hex[:8]}.bak"
        )
        try:
            os.replace(unowned_launcher.path, backup_path)
            if unowned_launcher.path.exists():
                raise OSError("launcher still exists after backup")
            backed_up_orphans.append(f"- {unowned_launcher.path} -> {backup_path} ({reason})")
        except Exception as exc:
            failed.append(f"- {unowned_launcher.path} ({reason}): {exc}")

    sections: list[str] = []
    if removed:
        sections.append(
            "Removed stale tensor-grep Python package launchers from PATH:\n" + "\n".join(removed)
        )
    if backed_up_orphans:
        sections.append(
            "Backed up orphaned tensor-grep Python Scripts launchers from PATH:\n"
            + "\n".join(backed_up_orphans)
        )
    if failed:
        sections.append(
            "WARNING: stale tensor-grep Python package launchers remain ahead of managed "
            "native tg.exe:\n" + "\n".join(failed)
        )
    if remaining_unowned_launchers:
        sections.append(
            "WARNING: tensor-grep-looking Python Scripts tg.exe launchers remain ahead of "
            "managed native tg.exe, but package ownership could not be verified:\n"
            + "\n".join(
                f"- {launcher.path} ({launcher.version or '<unreadable --version>'})"
                for launcher in remaining_unowned_launchers
            )
        )
    return "\n".join(sections) if sections else None


def _refresh_windows_tensor_grep_com_bridges(
    expected_version: str,
    native_path: Path,
    bridge_paths: list[Path] | None = None,
) -> list[Path]:
    if not sys.platform.startswith("win"):
        return []
    paths = bridge_paths
    if paths is None:
        paths = _self._windows_stale_tensor_grep_com_bridges(expected_version, native_path)

    refreshed: list[Path] = []
    for bridge_path in paths:
        shutil.copy2(native_path, bridge_path)
        _write_windows_exe_bridge_marker(bridge_path)
        installed_version = _self._native_tg_version(bridge_path)
        if not _self._native_tg_version_matches(expected_version, installed_version):
            raise RuntimeError(
                "refreshed PATH tg.com bridge reported "
                f"{installed_version or 'no version'} instead of {expected_version}: "
                f"{bridge_path}"
            )
        refreshed.append(bridge_path)
    return refreshed


def _refreshed_com_bridge_message(expected_version: str, paths: list[Path]) -> str | None:
    if not paths:
        return None
    names = {path.name.lower() for path in paths}
    if names == {"tg.com"}:
        subject = f"PATH tg.com {'bridge' if len(paths) == 1 else 'bridges'}"
    elif names == {"tg.exe"}:
        subject = f"PATH tg.exe front-door {'copy' if len(paths) == 1 else 'copies'}"
    else:
        subject = f"PATH tensor-grep front-door {'copy' if len(paths) == 1 else 'copies'}"
    rendered_paths = "\n".join(f"- {path}" for path in paths)
    return f"Refreshed {len(paths)} {subject} to {expected_version}.\n{rendered_paths}"


def _windows_path_parts(path_value: str | None) -> list[str]:
    if not path_value:
        return []
    return [part.strip() for part in path_value.split(";") if part.strip()]


def _windows_path_part_key(path_value: str) -> str:
    normalized = os.path.expandvars(path_value.strip())
    normalized = os.path.normpath(normalized)
    return os.path.normcase(normalized).rstrip("\\/")


def _windows_prepend_path_part(path_value: str | None, preferred_dir: Path) -> tuple[str, bool]:
    preferred_text = str(preferred_dir)
    preferred_key = _windows_path_part_key(preferred_text)
    parts = _windows_path_parts(path_value)
    reordered = [preferred_text]
    reordered.extend(part for part in parts if _windows_path_part_key(part) != preferred_key)
    rendered = ";".join(reordered)
    return rendered, rendered != (path_value or "")


def _windows_user_path_value() -> str | None:
    if not sys.platform.startswith("win"):
        return None
    try:
        import winreg  # type: ignore[import-not-found]
    except ImportError:
        return None
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, "Environment") as key:
            value, _value_type = winreg.QueryValueEx(key, "Path")
    except OSError:
        return ""
    return value if isinstance(value, str) else ""


def _set_windows_user_path_value(path_value: str) -> None:
    if not sys.platform.startswith("win"):
        return
    try:
        import winreg  # type: ignore[import-not-found]
    except ImportError as exc:
        raise OSError("winreg is unavailable") from exc
    value_type = winreg.REG_EXPAND_SZ if "%" in path_value else winreg.REG_SZ
    with winreg.OpenKey(winreg.HKEY_CURRENT_USER, "Environment", 0, winreg.KEY_SET_VALUE) as key:
        winreg.SetValueEx(key, "Path", 0, value_type, path_value)


def _windows_python_subprocess_resolution_blocker(
    *, managed_dir: Path, path_value: str | None
) -> str | None:
    if not sys.platform.startswith("win") or not path_value:
        return None

    candidate = _self._doctor_python_subprocess_path_tg_candidate(path_value)
    if not candidate:
        return None

    candidate_path_text = candidate.get("path")
    candidate_version = candidate.get("version")
    candidate_kind = _self._doctor_tg_launcher_kind(candidate_path_text, candidate_version)
    if candidate_kind == "managed-native":
        return None
    if candidate_kind != "foreign":
        return None

    foreign_path = Path(str(candidate_path_text))
    foreign_dir = foreign_path.parent
    return (
        "Windows PATH repair could not put managed native tg.exe ahead of the first "
        "Python subprocess tg.exe in fresh shells. Windows appends User PATH after "
        "Machine PATH, so a Machine PATH foreign tg.exe can still win "
        'subprocess.run(["tg", ...]) even when shell PATHEXT resolves tg.com.\n'
        f"- managed native dir: {managed_dir}\n"
        f"- first Python subprocess tg.exe: {foreign_path} "
        f"({candidate_version or 'no recognizable --version output'})\n"
        f"Remediation: move {managed_dir} earlier in Machine PATH than {foreign_dir}, "
        f"or run tg repair-launcher --allow-foreign-rename if you own {foreign_path} "
        "and want tensor-grep to back it up into a .bak file. Do not remove unrelated "
        "launchers automatically."
    )


def _repair_windows_python_subprocess_launcher(*, allow_foreign_rename: bool) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "status": "not_windows",
        "platform": sys.platform,
        "message": "Python subprocess launcher repair is only needed on Windows.",
        "managed_native": None,
        "foreign_path": None,
        "backup_path": None,
        "replaced_path": None,
        "pre_repair_version": None,
        "post_repair_version": None,
        "cleanup_message": None,
    }
    if not sys.platform.startswith("win"):
        return payload

    expected_version = _self._doctor_installed_version()
    native_tg_binary = _self.resolve_native_tg_binary()
    payload["expected_version"] = expected_version
    payload["managed_native"] = str(native_tg_binary) if native_tg_binary else None
    if native_tg_binary is None or not native_tg_binary.is_file():
        payload.update({
            "status": "blocked_missing_managed_native",
            "message": (
                "No managed native tg.exe was found. Run tg upgrade or reinstall tensor-grep "
                "before repairing Python subprocess launcher resolution."
            ),
        })
        return payload

    native_version = _self._doctor_tg_candidate_version(native_tg_binary)
    payload["managed_native_version"] = native_version
    if not _self._native_tg_version_matches(expected_version, native_version):
        payload.update({
            "status": "blocked_managed_native_version_mismatch",
            "message": (
                "Managed native tg.exe is not verified for this tensor-grep version: "
                f"{native_tg_binary} reports {native_version or 'no version'}, "
                f"expected {expected_version}."
            ),
        })
        return payload

    candidate = _self._doctor_python_subprocess_path_tg_candidate()
    if not candidate:
        payload.update({
            "status": "blocked_no_python_subprocess_tg",
            "message": "No tg.exe candidate was found on PATH for Python subprocess resolution.",
        })
        return payload

    candidate_path = Path(str(candidate.get("path") or ""))
    candidate_version = candidate.get("version")
    candidate_kind = _self._doctor_tg_launcher_kind(str(candidate_path), candidate_version)
    payload.update({
        "foreign_path": str(candidate_path),
        "pre_repair_version": candidate_version,
        "pre_repair_launcher_kind": candidate_kind,
    })

    if _same_path(candidate_path, native_tg_binary) and _self._native_tg_version_matches(
        expected_version,
        candidate_version,
    ):
        payload.update({
            "status": "already_ok",
            "message": "Python subprocess resolution already finds the managed native tg.exe.",
            "post_repair_version": candidate_version,
        })
        return payload

    if candidate_kind == "python-entrypoint":
        cleanup_message = _self._remove_windows_stale_tensor_grep_python_launchers(
            expected_version,
            native_tg_binary,
        )
        payload["cleanup_message"] = cleanup_message
        post_candidate = _self._doctor_python_subprocess_path_tg_candidate()
        post_path = Path(str(post_candidate.get("path") or "")) if post_candidate else None
        post_version = post_candidate.get("version") if post_candidate else None
        payload["post_repair_version"] = post_version
        if (
            post_path is not None
            and _same_path(post_path, native_tg_binary)
            and _self._native_tg_version_matches(expected_version, post_version)
        ):
            payload.update({
                "status": "repaired",
                "replaced_path": str(candidate_path),
                "message": (
                    "Python subprocess launcher repaired. Removed or backed up the "
                    "tensor-grep Python Scripts entrypoint so the verified managed native "
                    "tg.exe is selected first."
                ),
            })
            return payload

        payload.update({
            "status": "blocked_python_entrypoint_cleanup",
            "message": (
                "Python subprocess resolution is still blocked by a Python Scripts "
                "tensor-grep entrypoint. "
                + (
                    cleanup_message
                    if cleanup_message
                    else "Package ownership could not be verified, so no launcher was removed."
                )
            ),
        })
        return payload

    if candidate_kind != "foreign":
        payload.update({
            "status": "blocked_non_foreign_launcher",
            "message": (
                "Python subprocess resolution does not point at a foreign tg.exe, so "
                "foreign launcher repair is not applicable. Use tg doctor --json for details."
            ),
        })
        return payload

    if candidate_path.name.lower() != "tg.exe":
        payload.update({
            "status": "blocked_unsupported_launcher_name",
            "message": (
                "Python subprocess launcher repair only handles a foreign tg.exe selected "
                f"by Windows CreateProcess, not {candidate_path.name}."
            ),
        })
        return payload

    if not allow_foreign_rename:
        payload.update({
            "status": "blocked_requires_allow_foreign_rename",
            "message": (
                "Python subprocess resolution is blocked by a foreign tg.exe. Re-run with "
                "--allow-foreign-rename only if you own that command and accept that it will "
                "be moved aside to a .bak file before tensor-grep installs its managed "
                f"native front door at {candidate_path}."
            ),
        })
        return payload

    backup_path = candidate_path.with_name(
        f"{candidate_path.name}.foreign-{datetime.now(UTC).strftime('%Y%m%d%H%M%S')}-"
        f"{uuid4().hex[:8]}.bak"
    )
    payload["backup_path"] = str(backup_path)
    try:
        os.replace(candidate_path, backup_path)
        try:
            shutil.copy2(native_tg_binary, candidate_path)
            post_version = _self._doctor_tg_candidate_version(candidate_path)
            payload["post_repair_version"] = post_version
            if not _self._native_tg_version_matches(expected_version, post_version):
                raise RuntimeError(
                    "repaired tg.exe reported "
                    f"{post_version or 'no version'} instead of {expected_version}"
                )
        except Exception:
            candidate_path.unlink(missing_ok=True)
            os.replace(backup_path, candidate_path)
            raise
    except Exception as exc:
        payload.update({
            "status": "failed",
            "message": f"Python subprocess launcher repair failed: {exc}",
        })
        return payload

    payload.update({
        "status": "repaired",
        "replaced_path": str(candidate_path),
        "message": (
            "Python subprocess launcher repaired. The foreign tg.exe was backed up and "
            "the verified managed native tensor-grep front door now occupies that PATH slot."
        ),
    })
    return payload


def _ensure_windows_managed_native_first_on_path(native_path: Path) -> str | None:
    if not sys.platform.startswith("win"):
        return None

    managed_dir = native_path.parent
    expected_managed_dir = _windows_managed_native_bin_dir()
    if expected_managed_dir is None or _windows_path_part_key(str(managed_dir)) != (
        _windows_path_part_key(str(expected_managed_dir))
    ):
        return None

    messages: list[str] = []
    try:
        user_path = _windows_user_path_value()
        reordered_user_path, user_changed = _windows_prepend_path_part(user_path, managed_dir)
        if user_changed:
            _set_windows_user_path_value(reordered_user_path)
            messages.append("persistent User PATH")
    except OSError as exc:
        messages.append(f"User PATH repair warning: {exc}")

    current_path = os.environ.get("PATH", "")
    reordered_current_path, current_changed = _windows_prepend_path_part(current_path, managed_dir)
    if current_changed:
        os.environ["PATH"] = reordered_current_path
        messages.append("current process PATH")

    fresh_shell_blocker = _windows_python_subprocess_resolution_blocker(
        managed_dir=managed_dir,
        path_value=_self._doctor_fresh_shell_path_value(),
    )

    if not messages and not fresh_shell_blocker:
        return None
    if fresh_shell_blocker:
        update_line = (
            f"Updated: {', '.join(messages)}." if messages else "Updated: no PATH entries."
        )
        return f"{fresh_shell_blocker}\n{update_line}"
    return (
        "Windows PATH now prefers managed native tg.exe for Python subprocesses.\n"
        f"- {managed_dir}\n"
        f"Updated: {', '.join(messages)}."
    )


def _looks_like_windows_file_lock_error(message: str) -> bool:
    lowered = message.lower()
    return (
        "winerror 32" in lowered
        or "os error 32" in lowered
        or "being used by another process" in lowered
        or "access is denied" in lowered
        or "permission denied" in lowered
    )


def _schedule_windows_native_frontdoor_refresh(
    native_path: Path, expected_version: str, bridge_paths: list[Path] | None = None
) -> Path:
    import textwrap

    # Audit HIGH (2026-06-28): fetch checksums on the parent side and embed the
    # expected sha256 into each payload entry so the detached helper can verify
    # each download WITHOUT importing main.py.  Fail-closed: skip any candidate
    # whose sha256 can't be resolved; refuse to schedule if none remain.
    checksums_text = _self._fetch_native_frontdoor_checksums(expected_version)
    if checksums_text is None:
        raise RuntimeError(
            "release-native front-door asset refresh refused: could not fetch "
            f"CHECKSUMS.txt for v{expected_version}; refusing to schedule an unverified native binary refresh"
        )
    verifiable_entries: list[dict[str, str]] = []
    for candidate, url in _self._native_frontdoor_download_candidates(expected_version):
        sha256 = _self._expected_asset_sha256(checksums_text, candidate.asset_name)
        if sha256 is None:
            continue
        verifiable_entries.append({
            "url": url,
            "flavor": candidate.flavor,
            "asset_name": candidate.asset_name,
            "requested_flavor": _requested_native_frontdoor_flavor(),
            "sha256": sha256,
        })
    if not verifiable_entries:
        raise RuntimeError("no release-native front-door asset is available for this platform")
    asset_payload = json.dumps(verifiable_entries)
    bridge_payload = json.dumps([str(path) for path in bridge_paths or []])

    helper_code = textwrap.dedent(
        """
        import hashlib
        import json
        import os
        import shutil
        import subprocess
        import sys
        import time
        import urllib.request
        from pathlib import Path
        from uuid import uuid4

        parent_pid = int(sys.argv[1])
        log_path = Path(sys.argv[2])
        native_path = Path(sys.argv[3])
        expected_version = sys.argv[4]
        asset_candidates = json.loads(sys.argv[5])
        bridge_paths = [Path(path) for path in json.loads(sys.argv[6])]
        log_path.parent.mkdir(parents=True, exist_ok=True)

        for _ in range(300):
            try:
                subprocess.run(
                    [
                        "powershell",
                        "-NoProfile",
                        "-Command",
                        f"Get-Process -Id {parent_pid} -ErrorAction Stop | Out-Null",
                    ],
                    capture_output=True,
                    text=True,
                    check=True,
                )
            except subprocess.CalledProcessError:
                break
            time.sleep(0.1)

        def _version(path: Path) -> str:
            result = subprocess.run([str(path), "--version"], capture_output=True, text=True)
            if result.returncode != 0:
                return ""
            for line in result.stdout.splitlines():
                line = line.strip()
                if line:
                    return line
            return ""

        errors: list[str] = []
        for attempt in range(120):
            for asset_candidate in asset_candidates:
                url = asset_candidate.get("url", "")
                flavor = asset_candidate.get("flavor", "unknown")
                asset_name = asset_candidate.get("asset_name", "")
                requested_flavor = asset_candidate.get("requested_flavor", "cpu")
                temp_path = native_path.with_name(native_path.name + ".download-" + uuid4().hex)
                try:
                    try:

                        def _cap(block_num, block_size, total_size):
                            if block_num * block_size > 512 * 1024 * 1024:
                                raise RuntimeError("native asset download exceeded 512MB")

                        # O_EXCL claims the temp name as a regular file first; urlretrieve's 'wb'
                        # FOLLOWS a symlink and the payload is an executable. Full rationale in
                        # _download_native_frontdoor_asset (found by the #859 ratchet).
                        _fd = os.open(temp_path, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
                        os.close(_fd)
                        urllib.request.urlretrieve(url, temp_path, reporthook=_cap)
                    except Exception as exc:
                        errors.append(f"{flavor} asset unavailable: {exc}")
                        continue
                    sha256 = asset_candidate.get("sha256", "")
                    if not sha256:
                        errors.append(
                            f"{flavor} asset has no published checksum; "
                            "refusing to install unverified binary"
                        )
                        continue
                    actual_sha256 = hashlib.sha256(temp_path.read_bytes()).hexdigest().lower()
                    if actual_sha256 != sha256.lower():
                        errors.append(
                            f"{flavor} asset checksum mismatch "
                            f"(expected {sha256}, got {actual_sha256})"
                        )
                        continue
                    temp_version = _version(temp_path)
                    if expected_version not in temp_version:
                        raise RuntimeError(
                            "downloaded native tg front door reported "
                            + (temp_version or "no version")
                        )
                    os.replace(temp_path, native_path)
                    installed_version = _version(native_path)
                    if expected_version not in installed_version:
                        raise RuntimeError(
                            "installed native tg front door reported "
                            + (installed_version or "no version")
                        )
                    metadata_path = native_path.with_name("tg-native-metadata.json")
                    metadata_path.write_text(
                        json.dumps(
                            {
                                "artifact": "tensor_grep_native_frontdoor_metadata",
                                "asset_flavor": flavor,
                                "asset_name": asset_name,
                                "requested_asset_flavor": requested_flavor,
                                "version": expected_version,
                            },
                            indent=2,
                            sort_keys=True,
                        )
                        + "\\n",
                        encoding="utf-8",
                    )
                    refreshed_bridges: list[str] = []
                    for bridge_path in bridge_paths:
                        shutil.copy2(native_path, bridge_path)
                        bridge_version = _version(bridge_path)
                        if expected_version not in bridge_version:
                            raise RuntimeError(
                                "refreshed PATH tensor-grep front-door copy reported "
                                + (bridge_version or "no version")
                                + " for "
                                + str(bridge_path)
                            )
                        refreshed_bridges.append(str(bridge_path))
                    bridge_text = ""
                    if refreshed_bridges:
                        bridge_text = (
                            "\\nRefreshed PATH tensor-grep front-door copies:\\n"
                            + "\\n".join(refreshed_bridges)
                        )
                    log_path.write_text(
                        "Native tg front-door refresh completed.\\n"
                        + "Verified "
                        + installed_version
                        + ".\\nNative asset flavor: "
                        + flavor
                        + ".\\n"
                        + url
                        + bridge_text,
                        encoding="utf-8",
                    )
                    raise SystemExit(0)
                except Exception as exc:
                    errors.append(str(exc))
                finally:
                    try:
                        temp_path.unlink()
                    except FileNotFoundError:
                        pass
            time.sleep(0.5)

        log_path.write_text(
            "Native tg front-door refresh failed.\\n" + "\\n".join(errors[-10:]),
            encoding="utf-8",
        )
        raise SystemExit(1)
        """
    ).strip()

    log_path = Path.home() / ".tensor-grep" / "logs" / f"native-upgrade-{uuid4().hex}.log"
    creationflags = 0
    for flag_name in ("DETACHED_PROCESS", "CREATE_NEW_PROCESS_GROUP", "CREATE_NO_WINDOW"):
        creationflags |= int(getattr(_self.subprocess, flag_name, 0))
    _self.subprocess.Popen(
        [
            sys.executable,
            "-c",
            helper_code,
            str(os.getpid()),
            str(log_path),
            str(native_path),
            expected_version,
            asset_payload,
            bridge_payload,
        ],
        stdout=_self.subprocess.DEVNULL,
        stderr=_self.subprocess.DEVNULL,
        stdin=_self.subprocess.DEVNULL,
        close_fds=True,
        creationflags=creationflags,
    )
    return log_path


def _refresh_managed_native_frontdoor(expected_version: str) -> str | None:
    native_path = _managed_native_frontdoor_path()
    if native_path is None:
        return None

    messages: list[str] = []
    path_order_message = _self._ensure_windows_managed_native_first_on_path(native_path)
    if path_order_message:
        messages.append(path_order_message)
    stale_python_launcher_message = _self._remove_windows_stale_tensor_grep_python_launchers(
        expected_version,
        native_path,
    )
    if stale_python_launcher_message:
        messages.append(stale_python_launcher_message)
    stale_com_bridges = _self._windows_stale_tensor_grep_com_bridges(expected_version, native_path)
    current_version = _self._native_tg_version(native_path) if native_path.is_file() else None
    if not _self._native_tg_version_matches(expected_version, current_version):
        try:
            install_result = _install_release_native_frontdoor(expected_version, native_path)
        except OSError as exc:
            if sys.platform.startswith("win") and (
                getattr(exc, "winerror", None) == 32
                or _looks_like_windows_file_lock_error(str(exc))
            ):
                log_path = _schedule_windows_native_frontdoor_refresh(
                    native_path, expected_version, stale_com_bridges
                )
                scheduled_message = (
                    f"Native tg front door refresh scheduled for {expected_version}."
                    f"\nUpgrade log: {log_path}"
                )
                return "\n".join([scheduled_message, *messages])
            raise RuntimeError(f"native front-door refresh failed: {exc}") from exc
        except RuntimeError as exc:
            raise RuntimeError(f"native front-door refresh failed: {exc}") from exc
        messages.append(
            f"Native tg front door refreshed to {expected_version}. "
            f"Native asset flavor: {install_result.flavor}."
        )

    try:
        refreshed_bridges = _self._refresh_windows_tensor_grep_com_bridges(
            expected_version, native_path, stale_com_bridges
        )
    except OSError as exc:
        raise RuntimeError(f"PATH tensor-grep front-door copy refresh failed: {exc}") from exc
    bridge_message = _refreshed_com_bridge_message(expected_version, refreshed_bridges)
    if bridge_message:
        messages.append(bridge_message)

    return "\n".join(messages) if messages else None
