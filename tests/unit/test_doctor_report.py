from __future__ import annotations

from pathlib import Path

from tensor_grep.cli import doctor_payload, doctor_report


def test_mcp_stdio_launcher_warning_strips_windows_extended_length_prefix() -> None:
    warning = doctor_report._doctor_mcp_stdio_launcher_warning(
        native_tg_binary=Path(r"\\?\C:\Users\dev\.tensor-grep\bin\tg.exe"),
        launchers=[
            (
                "current process PATH",
                "powershell-shim",
                r"\\?\C:\Users\dev\AppData\Local\Microsoft\WindowsApps\tg.ps1",
            )
        ],
        path_tg_candidates=[
            {"path": r"\\?\C:\Users\dev\bin\tg.ps1", "version": "1.0.0"},
        ],
    )

    assert warning is not None
    assert "\\\\?\\" not in warning
    assert r"C:\Users\dev\.tensor-grep\bin\tg.exe" in warning
    assert r"C:\Users\dev\AppData\Local\Microsoft\WindowsApps\tg.ps1" in warning
    assert r"C:\Users\dev\bin\tg.ps1" in warning


def test_doctor_session_daemon_stale_metadata_emits_cleanup_hint(
    tmp_path: Path, monkeypatch
) -> None:
    root_str = str(tmp_path)
    monkeypatch.setattr(
        "tensor_grep.cli.session_daemon.get_session_daemon_status",
        lambda _path: {
            "running": False,
            "stale_metadata": True,
            "root": rf"\\?\{root_str}",
        },
    )
    monkeypatch.setattr(
        "tensor_grep.cli.session_daemon_trust.diagnose_user_secret",
        lambda: None,
    )
    monkeypatch.setattr(
        "tensor_grep.cli.main._session_daemon_autostart_enabled",
        lambda: False,
    )

    status = doctor_report._doctor_session_daemon_status(root_str)
    assert status["stale_metadata"] is True
    assert "\\\\?\\" not in status["cleanup_hint"]
    assert f"tg session daemon stop {root_str}" in status["cleanup_hint"]

    rendered = doctor_payload._render_doctor_payload({
        "version": "1.0.0",
        "platform": "win32",
        "python_executable": "python.exe",
        "invoked_as": "tg",
        "root": root_str,
        "installed_version": "1.0.0",
        "source_version": "1.0.0",
        "python_package_version_status": "ok",
        "rust_binary": None,
        "rust_binary_version": None,
        "rust_binary_version_status": "missing",
        "native_tg_binary_kind": None,
        "rust_core_extension_available": False,
        "path_tg_candidates": [],
        "fresh_shell_path_tg_candidates": [],
        "python_subprocess_path_tg_first": None,
        "ast_cache": {"exists": False, "file_count": 0},
        "gpu": {"available": False, "devices": []},
        "dense_model": {"available": False},
        "ast_grep": {"available": False},
        "resident_worker": {"port_file_exists": False, "responding": False},
        "env": {},
        "session_daemon": status,
        "lsp": {"enabled": False},
    })
    assert "session_daemon: stale-metadata" in rendered
    assert f"cleanup_hint: Run `tg session daemon stop '{root_str}'`" in rendered


def test_strip_windows_extended_prefix_handles_unc_and_preserves_device_namespaces() -> None:
    from tensor_grep.cli.doctor_launcher_probe import strip_windows_extended_prefix

    assert strip_windows_extended_prefix(r"\\?\unc\server\share\tg.exe") == r"\\server\share\tg.exe"
    assert strip_windows_extended_prefix(r"\\?\UNC\server\share\tg.exe") == r"\\server\share\tg.exe"
    assert strip_windows_extended_prefix(r"\\?\C:\Users\demo\tg.exe") == r"C:\Users\demo\tg.exe"
    assert (
        strip_windows_extended_prefix(r"\\?\GLOBALROOT\Device\Harddisk0")
        == r"\\?\GLOBALROOT\Device\Harddisk0"
    )
