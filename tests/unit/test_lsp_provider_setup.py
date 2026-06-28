from __future__ import annotations

from pathlib import Path

import pytest

import tensor_grep.cli.lsp_provider_setup as provider_setup


def test_wrap_windows_batch_command_routes_cmd_through_cmd_exe(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # On Windows a .cmd/.bat shim cannot be launched directly (WinError 193); it must go
    # through cmd.exe. npm/pyright-langserver/intelephense all ship as .cmd shims.
    monkeypatch.setattr(provider_setup, "is_windows", lambda: True)
    assert provider_setup.wrap_windows_batch_command(["C:\\p\\npm.cmd", "install"]) == [
        "cmd.exe",
        "/C",
        "C:\\p\\npm.cmd",
        "install",
    ]
    assert provider_setup.wrap_windows_batch_command(["C:\\p\\run.BAT"]) == [
        "cmd.exe",
        "/C",
        "C:\\p\\run.BAT",
    ]


def test_wrap_windows_batch_command_leaves_real_exe_and_posix_untouched(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(provider_setup, "is_windows", lambda: True)
    # A real .exe on Windows launches directly — no wrapping.
    assert provider_setup.wrap_windows_batch_command(["C:\\p\\node.exe", "x"]) == [
        "C:\\p\\node.exe",
        "x",
    ]
    # Non-Windows: a .cmd-suffixed path is never wrapped.
    monkeypatch.setattr(provider_setup, "is_windows", lambda: False)
    assert provider_setup.wrap_windows_batch_command(["/usr/bin/npm.cmd", "install"]) == [
        "/usr/bin/npm.cmd",
        "install",
    ]
    assert provider_setup.wrap_windows_batch_command([]) == []


def test_gopls_install_pins_exact_version(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    root = tmp_path / "providers"
    captured: dict[str, list[str]] = {}
    monkeypatch.setattr(
        provider_setup.shutil, "which", lambda c: None if c == "gopls" else "/usr/bin/go"
    )
    monkeypatch.setattr(provider_setup, "_run_checked", lambda cmd, **kw: captured.update(cmd=cmd))
    built = tmp_path / "gopls"
    built.write_text("", encoding="utf-8")
    monkeypatch.setattr(provider_setup, "_find_go_binary_name", lambda *a: built)
    monkeypatch.setattr(provider_setup, "_copy_binary_to_managed", lambda _b, dest: dest)

    provider_setup._ensure_gopls(root)

    assert f"golang.org/x/tools/gopls@{provider_setup._GOPLS_VERSION}" in captured["cmd"]
    assert "golang.org/x/tools/gopls@latest" not in captured["cmd"]


def test_csharp_ls_install_pins_exact_version(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = tmp_path / "providers"
    captured: dict[str, list[str]] = {}
    monkeypatch.setattr(
        provider_setup.shutil, "which", lambda c: None if c == "csharp-ls" else "/usr/bin/dotnet"
    )

    def _fake_run(cmd, **_kw):
        captured["cmd"] = cmd
        dest = provider_setup._managed_bin_binary(root, "csharp-ls")
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_text("", encoding="utf-8")
        return type("R", (), {"returncode": 0, "stderr": ""})()

    monkeypatch.setattr(provider_setup.subprocess, "run", _fake_run)

    provider_setup._ensure_csharp_ls(root)

    assert "--version" in captured["cmd"]
    assert provider_setup._CSHARP_LS_VERSION in captured["cmd"]


def test_csharp_ls_already_installed_converges_to_pinned_version(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = tmp_path / "providers"
    update: dict[str, list[str]] = {}
    monkeypatch.setattr(
        provider_setup.shutil, "which", lambda c: None if c == "csharp-ls" else "/usr/bin/dotnet"
    )
    monkeypatch.setattr(
        provider_setup.subprocess,
        "run",
        lambda cmd, **_kw: type(
            "R", (), {"returncode": 1, "stderr": "Tool 'csharp-ls' is already installed."}
        )(),
    )

    def _fake_run_checked(cmd, **_kw):
        update["cmd"] = cmd
        dest = provider_setup._managed_bin_binary(root, "csharp-ls")
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_text("", encoding="utf-8")

    monkeypatch.setattr(provider_setup, "_run_checked", _fake_run_checked)

    provider_setup._ensure_csharp_ls(root)

    assert "update" in update["cmd"]
    assert provider_setup._CSHARP_LS_VERSION in update["cmd"]


def test_csharp_ls_install_failure_raises(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    root = tmp_path / "providers"
    monkeypatch.setattr(
        provider_setup.shutil, "which", lambda c: None if c == "csharp-ls" else "/usr/bin/dotnet"
    )
    monkeypatch.setattr(
        provider_setup.subprocess,
        "run",
        lambda cmd, **_kw: type("R", (), {"returncode": 1, "stderr": "network error"})(),
    )
    with pytest.raises(RuntimeError, match="csharp-ls failed"):
        provider_setup._ensure_csharp_ls(root)


def test_node_sha256_table_is_fully_populated() -> None:
    # CI completeness gate: every pinned Node archive must carry a real SHA-256. An empty entry
    # would silently defeat fail-closed verification (this is the gate that would have caught the
    # all-empty rust-analyzer table).
    assert provider_setup._NODE_SHA256, "Node SHA table must not be empty"
    for name, sha in provider_setup._NODE_SHA256.items():
        assert len(sha) == 64 and all(c in "0123456789abcdef" for c in sha.lower()), (
            f"{name} has a malformed/empty SHA-256: {sha!r}"
        )


def test_verify_node_archive_passes_on_match(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import hashlib

    archive = tmp_path / "node.tar.xz"
    archive.write_bytes(b"node-bytes")
    monkeypatch.setattr(
        provider_setup, "_NODE_SHA256", {"node.tar.xz": hashlib.sha256(b"node-bytes").hexdigest()}
    )
    monkeypatch.delenv("TG_ALLOW_UNVERIFIED_TOOLCHAIN", raising=False)
    provider_setup._verify_node_archive(archive, "node.tar.xz")  # no raise


def test_verify_node_archive_fails_closed_on_mismatch(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    archive = tmp_path / "node.tar.xz"
    archive.write_bytes(b"tampered")
    monkeypatch.setattr(provider_setup, "_NODE_SHA256", {"node.tar.xz": "a" * 64})
    monkeypatch.delenv("TG_ALLOW_UNVERIFIED_TOOLCHAIN", raising=False)
    with pytest.raises(RuntimeError, match="failed checksum verification"):
        provider_setup._verify_node_archive(archive, "node.tar.xz")


def test_verify_node_archive_fails_closed_on_unknown_archive(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    archive = tmp_path / "node.tar.xz"
    archive.write_bytes(b"x")
    monkeypatch.setattr(provider_setup, "_NODE_SHA256", {})
    monkeypatch.delenv("TG_ALLOW_UNVERIFIED_TOOLCHAIN", raising=False)
    with pytest.raises(RuntimeError, match="No pinned SHA-256"):
        provider_setup._verify_node_archive(archive, "node.tar.xz")


def test_verify_node_archive_opt_out_skips_verification(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    archive = tmp_path / "node.tar.xz"
    archive.write_bytes(b"whatever")
    monkeypatch.setattr(provider_setup, "_NODE_SHA256", {})
    monkeypatch.setenv("TG_ALLOW_UNVERIFIED_TOOLCHAIN", "1")
    provider_setup._verify_node_archive(archive, "node.tar.xz")  # opt-out -> no raise


def test_supported_lsp_languages_should_include_managed_provider_matrix() -> None:
    assert provider_setup.supported_lsp_languages() == [
        "python",
        "javascript",
        "typescript",
        "go",
        "rust",
        "java",
        "c",
        "cpp",
        "csharp",
        "php",
        "kotlin",
        "swift",
        "lua",
    ]


def test_managed_provider_command_should_resolve_node_and_bin_providers(tmp_path: Path) -> None:
    root = tmp_path / "providers"
    node_bin = root / "node-packages" / "node_modules" / ".bin"
    managed_bin = root / "bin"
    cmd_suffix = ".cmd" if provider_setup.is_windows() else ""
    exe_suffix = ".exe" if provider_setup.is_windows() else ""
    node_bin.mkdir(parents=True)
    managed_bin.mkdir(parents=True)
    (node_bin / f"pyright-langserver{cmd_suffix}").write_text("", encoding="utf-8")
    (node_bin / f"typescript-language-server{cmd_suffix}").write_text("", encoding="utf-8")
    (node_bin / f"intelephense{cmd_suffix}").write_text("", encoding="utf-8")
    (managed_bin / f"gopls{exe_suffix}").write_text("", encoding="utf-8")
    (managed_bin / f"rust-analyzer{exe_suffix}").write_text("", encoding="utf-8")
    (managed_bin / f"csharp-ls{exe_suffix}").write_text("", encoding="utf-8")

    assert provider_setup.managed_provider_command("python", managed_root=root) == [
        str(node_bin / f"pyright-langserver{cmd_suffix}"),
        "--stdio",
    ]
    assert provider_setup.managed_provider_command("ts", managed_root=root) == [
        str(node_bin / f"typescript-language-server{cmd_suffix}"),
        "--stdio",
    ]
    assert provider_setup.managed_provider_command("php", managed_root=root) == [
        str(node_bin / f"intelephense{cmd_suffix}"),
        "--stdio",
    ]
    assert provider_setup.managed_provider_command("go", managed_root=root) == [
        str(managed_bin / f"gopls{exe_suffix}")
    ]
    assert provider_setup.managed_provider_command("rust", managed_root=root) == [
        str(managed_bin / f"rust-analyzer{exe_suffix}")
    ]
    assert provider_setup.managed_provider_command("csharp", managed_root=root) == [
        str(managed_bin / f"csharp-ls{exe_suffix}")
    ]


def test_managed_provider_env_should_prefix_managed_node_runtime_for_node_shims(
    tmp_path: Path,
) -> None:
    root = tmp_path / "providers"
    node_runtime = root / "node-runtime"
    if provider_setup.is_windows():
        node_path_entry = node_runtime
    else:
        node_path_entry = node_runtime / "bin"
    node_bin = root / "node-packages" / "node_modules" / ".bin"
    node_bin.mkdir(parents=True)
    command = [str((node_bin / "pyright-langserver").resolve()), "--stdio"]

    env = provider_setup.managed_provider_env(
        command,
        base_env={"PATH": "system-path"},
        managed_root=root,
    )

    assert env["PATH"].split(provider_setup.os.pathsep)[:2] == [
        str(node_path_entry),
        str((root / "bin").resolve()),
    ]
    assert env["PATH"].endswith("system-path")


def test_managed_provider_env_should_strip_python_runtime_vars_for_managed_node_shims(
    tmp_path: Path,
) -> None:
    root = tmp_path / "providers"
    node_bin = root / "node-packages" / "node_modules" / ".bin"
    node_bin.mkdir(parents=True)
    command = [str((node_bin / "pyright-langserver").resolve()), "--stdio"]

    env = provider_setup.managed_provider_env(
        command,
        base_env={
            "PATH": "system-path",
            "PYTHONHOME": r"C:\stale-python",
            "PYTHONPATH": r"C:\stale-python\Lib",
            "VIRTUAL_ENV": r"C:\stale-venv",
            "__PYVENV_LAUNCHER__": r"C:\stale-venv\python.exe",
        },
        managed_root=root,
    )

    assert "PYTHONHOME" not in env
    assert "PYTHONPATH" not in env
    assert "VIRTUAL_ENV" not in env
    assert "__PYVENV_LAUNCHER__" not in env
    assert env["PATH"].endswith("system-path")


def test_managed_provider_env_should_preserve_python_runtime_vars_for_path_providers(
    tmp_path: Path,
) -> None:
    root = tmp_path / "providers"
    command = [str((tmp_path / "path-provider" / "pyright-langserver").resolve()), "--stdio"]
    base_env = {
        "PATH": "system-path",
        "PYTHONHOME": r"C:\custom-python",
        "PYTHONPATH": r"C:\custom-python\Lib",
        "VIRTUAL_ENV": r"C:\custom-venv",
        "__PYVENV_LAUNCHER__": r"C:\custom-venv\python.exe",
    }

    env = provider_setup.managed_provider_env(
        command,
        base_env=base_env,
        managed_root=root,
    )

    assert env == base_env


@pytest.mark.parametrize(
    ("language", "binary_name", "expected_command"),
    [
        ("java", "jdtls", "jdtls"),
        ("c", "clangd", "clangd"),
        ("cpp", "clangd", "clangd"),
        ("kotlin", "kotlin-lsp", "kotlin-lsp"),
        ("lua", "lua-language-server", "lua-language-server"),
    ],
)
def test_path_provider_command_should_resolve_extended_path_binaries(
    monkeypatch: pytest.MonkeyPatch,
    language: str,
    binary_name: str,
    expected_command: str,
) -> None:
    monkeypatch.setattr(
        provider_setup.shutil,
        "which",
        lambda candidate: f"/usr/bin/{candidate}" if candidate == binary_name else None,
    )

    command = provider_setup.path_provider_command(language)

    assert command == [f"/usr/bin/{expected_command}"]


def test_resolved_provider_command_should_prefer_managed_over_path(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    root = tmp_path / "providers"
    node_bin = root / "node-packages" / "node_modules" / ".bin"
    suffix = ".cmd" if provider_setup.is_windows() else ""
    node_bin.mkdir(parents=True)
    managed_pyright = node_bin / f"pyright-langserver{suffix}"
    managed_pyright.write_text("", encoding="utf-8")
    monkeypatch.setattr(provider_setup.shutil, "which", lambda _candidate: "/usr/bin/path-lsp")

    command = provider_setup.resolved_provider_command("python", managed_root=root)

    assert command == [str(managed_pyright), "--stdio"]


def test_ensure_node_packages_should_install_pinned_package_specs(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    root = tmp_path / "providers"
    captured: dict[str, list[str]] = {}

    def _fake_run_checked(command: list[str], *, cwd: Path | None = None) -> None:
        captured["command"] = command
        suffix = ".cmd" if provider_setup.is_windows() else ""
        node_bin = root / "node-packages" / "node_modules" / ".bin"
        node_bin.mkdir(parents=True)
        for binary in ("pyright-langserver", "typescript-language-server", "intelephense"):
            (node_bin / f"{binary}{suffix}").write_text("", encoding="utf-8")

    monkeypatch.setattr(provider_setup, "_ensure_node_runtime", lambda _root: root)
    monkeypatch.setattr(provider_setup, "_run_checked", _fake_run_checked)

    provider_setup._ensure_node_packages(root)

    assert "pyright@1.1.409" in captured["command"]
    assert "typescript@6.0.3" in captured["command"]
    assert "typescript-language-server@5.1.3" in captured["command"]
    assert "intelephense@1.18.0" in captured["command"]
    # Supply-chain: lifecycle/binding.gyp scripts must be disabled (pure-JS providers need none).
    assert "--ignore-scripts" in captured["command"]


def test_install_managed_lsp_providers_should_not_mutate_toolchains_by_default(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    root = tmp_path / "providers"

    monkeypatch.setattr(provider_setup, "_ensure_node_packages", lambda _root: None)
    monkeypatch.setattr(
        provider_setup,
        "_ensure_rust_analyzer",
        lambda _root: (_ for _ in ()).throw(AssertionError("rustup should not run")),
    )
    monkeypatch.setattr(
        provider_setup,
        "_ensure_gopls",
        lambda _root: (_ for _ in ()).throw(AssertionError("go install should not run")),
    )
    monkeypatch.setattr(
        provider_setup,
        "_ensure_csharp_ls",
        lambda _root: (_ for _ in ()).throw(AssertionError("dotnet should not run")),
    )
    monkeypatch.setattr(
        provider_setup,
        "resolved_provider_command",
        lambda _language, *, managed_root=None: None,
    )

    payload = provider_setup.install_managed_lsp_providers(
        python_executable="python",
        managed_root=root,
    )

    assert payload["include_toolchain_providers"] is False
    assert "install_errors" not in payload


def test_install_managed_lsp_providers_should_return_status_without_network(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    root = tmp_path / "providers"

    monkeypatch.setattr(provider_setup, "_ensure_node_packages", lambda _root: None)
    monkeypatch.setattr(
        provider_setup, "_ensure_rust_analyzer", lambda _root: root / "bin" / "rust-analyzer"
    )
    monkeypatch.setattr(provider_setup, "_ensure_gopls", lambda _root: root / "bin" / "gopls")
    monkeypatch.setattr(
        provider_setup, "_ensure_csharp_ls", lambda _root: root / "bin" / "csharp-ls"
    )

    def _fake_resolved(language: str, *, managed_root: Path | None = None) -> list[str] | None:
        mapping = {
            "python": [
                str(root / "node-packages" / "node_modules" / ".bin" / "pyright-langserver"),
                "--stdio",
            ],
            "javascript": [
                str(
                    root / "node-packages" / "node_modules" / ".bin" / "typescript-language-server"
                ),
                "--stdio",
            ],
            "typescript": [
                str(
                    root / "node-packages" / "node_modules" / ".bin" / "typescript-language-server"
                ),
                "--stdio",
            ],
            "go": [str(root / "bin" / "gopls")],
            "rust": [str(root / "bin" / "rust-analyzer")],
            "java": ["/usr/bin/jdtls"],
            "c": ["/usr/bin/clangd"],
            "cpp": ["/usr/bin/clangd"],
            "csharp": [str(root / "bin" / "csharp-ls")],
            "php": [
                str(root / "node-packages" / "node_modules" / ".bin" / "intelephense"),
                "--stdio",
            ],
            "kotlin": ["/usr/bin/kotlin-lsp"],
            "swift": ["/usr/bin/sourcekit-lsp"],
            "lua": ["/usr/bin/lua-language-server"],
        }
        return mapping.get(language)

    monkeypatch.setattr(provider_setup, "resolved_provider_command", _fake_resolved)

    payload = provider_setup.install_managed_lsp_providers(
        python_executable="python",
        managed_root=root,
        include_toolchain_providers=True,
    )

    assert payload["managed_provider_root"] == str(root.resolve())
    assert payload["include_toolchain_providers"] is True
    assert payload["providers"]["php"]["available"] is True
    assert payload["providers"]["php"]["command"][0].endswith("intelephense")
    assert payload["providers"]["go"]["command"][0].endswith("gopls")
    assert payload["providers"]["java"]["command_source"] == "path"
    assert payload["providers"]["csharp"]["command_source"] == "managed"
