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
    import os
    import shlex
    import subprocess

    root_str = str(tmp_path)
    monkeypatch.setattr(
        "tensor_grep.cli.session_daemon.get_session_daemon_status",
        lambda _path: {
            "running": False,
            "stale_metadata": True,
            "root": rf"\\?\{root_str}" if os.name == "nt" else root_str,
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
    expected_quoted = (
        subprocess.list2cmdline([root_str]) if os.name == "nt" else shlex.quote(root_str)
    )
    assert f"tg session daemon stop {expected_quoted}" in status["cleanup_hint"]

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
    assert f"cleanup_hint: Run `tg session daemon stop {expected_quoted}`" in rendered


def test_strip_windows_extended_prefix_handles_unc_and_preserves_device_namespaces() -> None:
    from tensor_grep.cli.doctor_launcher_probe import strip_windows_extended_prefix

    assert strip_windows_extended_prefix(r"\\?\unc\server\share\tg.exe") == r"\\server\share\tg.exe"
    assert strip_windows_extended_prefix(r"\\?\UNC\server\share\tg.exe") == r"\\server\share\tg.exe"
    assert strip_windows_extended_prefix(r"\\?\C:\Users\demo\tg.exe") == r"C:\Users\demo\tg.exe"
    assert strip_windows_extended_prefix(r"\\?\/tmp/demo/tg.exe") == "/tmp/demo/tg.exe"
    assert (
        strip_windows_extended_prefix(r"\\?\GLOBALROOT\Device\Harddisk0")
        == r"\\?\GLOBALROOT\Device\Harddisk0"
    )


def test_mcp_stdio_launcher_warning_prefers_stable_managed_shim_over_hash_runtime(
    tmp_path: Path,
) -> None:
    import os

    tg_root = tmp_path / ".tensor-grep"
    shim_bin = tg_root / "bin" / "tg.exe"
    shim_bin.parent.mkdir(parents=True, exist_ok=True)
    shim_bin.write_text("exe", encoding="utf-8")
    runtime_bin = tg_root / "runtimes" / "0123456789abcdef0123456789abcdef" / "bin" / "tg.exe"
    runtime_bin.parent.mkdir(parents=True, exist_ok=True)
    runtime_bin.write_text("exe", encoding="utf-8")

    native_path = Path(rf"\\?\{runtime_bin}") if os.name == "nt" else runtime_bin
    warning = doctor_report._doctor_mcp_stdio_launcher_warning(
        native_tg_binary=native_path,
        launchers=[
            (
                "PATH",
                "powershell-shim",
                r"\\?\C:\Users\dev\AppData\Local\Microsoft\WindowsApps\tg.ps1",
            )
        ],
    )

    assert warning is not None
    assert "\\\\?\\" not in warning
    assert "runtimes" not in warning
    assert str(shim_bin) in warning


def test_mcp_stdio_launcher_warning_falls_back_when_stable_managed_shim_missing(
    tmp_path: Path,
) -> None:
    import os

    tg_root = tmp_path / ".tensor-grep"
    runtime_bin = tg_root / "runtimes" / "0123456789abcdef0123456789abcdef" / "bin" / "tg.exe"
    runtime_bin.parent.mkdir(parents=True, exist_ok=True)
    runtime_bin.write_text("exe", encoding="utf-8")

    native_path = Path(rf"\\?\{runtime_bin}") if os.name == "nt" else runtime_bin
    warning = doctor_report._doctor_mcp_stdio_launcher_warning(
        native_tg_binary=native_path,
        launchers=[
            (
                "PATH",
                "powershell-shim",
                r"\\?\C:\Users\dev\AppData\Local\Microsoft\WindowsApps\tg.ps1",
            )
        ],
    )

    assert warning is not None
    assert "\\\\?\\" not in warning
    assert str(runtime_bin) in warning


def test_doctor_payload_and_gpu_probe_strip_windows_extended_prefix(
    monkeypatch, tmp_path: Path
) -> None:
    fake_bin = tmp_path / "tg.exe"
    fake_bin.write_text("exe", encoding="utf-8")
    extended_bin = Path(rf"\\?\{fake_bin}")

    class _FakeCompleted:
        returncode = 0
        stdout = (
            b'{"routing_backend":"NativeCpuBackend","routing_reason":"gpu_unavailable",'
            b'"sidecar_used":false,"routing_gpu_device_ids":[]}'
        )
        stderr = b""

    monkeypatch.setenv("TG_NATIVE_TG_BINARY", str(extended_bin))
    monkeypatch.setattr("tensor_grep.cli.main._doctor_installed_version", lambda: "1.141.0")
    monkeypatch.setattr("tensor_grep.cli.main.resolve_native_tg_binary", lambda: extended_bin)
    monkeypatch.setattr("tensor_grep.cli.main._doctor_rust_binary_version", lambda _b: "tg 1.141.0")
    monkeypatch.setattr(
        "tensor_grep.cli.main.inspect_native_tg_binary",
        lambda _b, **_kw: {"native_frontdoor_flavor": "nvidia"},
    )
    monkeypatch.setattr("tensor_grep.cli.main._doctor_rust_core_extension_available", lambda: True)
    monkeypatch.setattr("tensor_grep.cli.main._doctor_path_tg_candidates", lambda: [])
    monkeypatch.setattr("tensor_grep.cli.main._doctor_fresh_shell_path_tg_candidates", lambda: [])
    monkeypatch.setattr(
        "tensor_grep.cli.main._doctor_python_subprocess_path_tg_candidate", lambda: None
    )
    monkeypatch.setattr(
        "tensor_grep.cli.main._doctor_session_daemon_status", lambda _p: {"running": False}
    )
    monkeypatch.setattr("tensor_grep.cli.main.is_cross_domain_native_binary", lambda _b: False)
    monkeypatch.setattr("tensor_grep.cli.main.subprocess.run", lambda *a, **kw: _FakeCompleted())

    payload = doctor_payload._build_doctor_payload(str(tmp_path), with_lsp=False, fast=True)
    assert payload["native_tg_binary"] == str(fake_bin)
    assert payload["env"]["TG_NATIVE_TG_BINARY"] == str(fake_bin)
    probe_cmd = payload["gpu"]["search_runtime_probe"]["command"]
    assert probe_cmd is not None
    assert "\\\\?\\" not in probe_cmd
    assert probe_cmd.startswith(str(fake_bin))
