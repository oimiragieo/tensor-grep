from __future__ import annotations

from pathlib import Path

import pytest

import tensor_grep.cli.lsp_provider_setup as provider_setup


def test_supported_lsp_languages_should_include_extended_matrix() -> None:
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


def test_managed_provider_command_should_support_php_go_and_csharp(tmp_path: Path) -> None:
    root = tmp_path / "providers"
    node_bin = root / "node-packages" / "node_modules" / ".bin"
    managed_bin = root / "bin"
    suffix = ".cmd" if provider_setup._is_windows() else ""
    exe_suffix = ".exe" if provider_setup._is_windows() else ""
    (node_bin / f"intelephense{suffix}").parent.mkdir(parents=True, exist_ok=True)
    (node_bin / f"intelephense{suffix}").write_text("", encoding="utf-8")
    managed_bin.mkdir(parents=True, exist_ok=True)
    (managed_bin / f"gopls{exe_suffix}").write_text("", encoding="utf-8")
    (managed_bin / f"csharp-ls{exe_suffix}").write_text("", encoding="utf-8")

    php = provider_setup.managed_provider_command("php", managed_root=root)
    go = provider_setup.managed_provider_command("go", managed_root=root)
    csharp = provider_setup.managed_provider_command("csharp", managed_root=root)

    assert php is not None and php[0].endswith(f"intelephense{suffix}") and php[1] == "--stdio"
    assert go is not None and go[0].endswith(f"gopls{exe_suffix}")
    assert csharp is not None and csharp[0].endswith(f"csharp-ls{exe_suffix}")


@pytest.mark.parametrize(
    ("language", "binary_name", "expected_arg"),
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
    expected_arg: str,
) -> None:
    monkeypatch.setattr(
        provider_setup.shutil,
        "which",
        lambda candidate: f"/usr/bin/{candidate}" if candidate == binary_name else None,
    )

    command = provider_setup.path_provider_command(language)

    assert command == [f"/usr/bin/{expected_arg}"]


def test_resolved_provider_command_should_use_sourcekit_lsp_from_xcrun(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(provider_setup, "sys_platform", lambda: "darwin")
    monkeypatch.setattr(
        provider_setup.shutil,
        "which",
        lambda candidate: "/usr/bin/xcrun" if candidate == "xcrun" else None,
    )

    class _Completed:
        def __init__(self) -> None:
            self.returncode = 0
            self.stdout = "/Applications/Xcode.app/Contents/Developer/Toolchains/XcodeDefault.xctoolchain/usr/bin/sourcekit-lsp\n"
            self.stderr = ""

    monkeypatch.setattr(provider_setup.subprocess, "run", lambda *args, **kwargs: _Completed())

    command = provider_setup.path_provider_command("swift")

    assert command == [
        "/Applications/Xcode.app/Contents/Developer/Toolchains/XcodeDefault.xctoolchain/usr/bin/sourcekit-lsp"
    ]


def test_install_managed_lsp_providers_should_return_status_for_extended_languages(
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
    )

    assert payload["managed_provider_root"] == str(root)
    assert payload["providers"]["php"]["available"] is True
    assert payload["providers"]["php"]["command"][0].endswith("intelephense")
    assert payload["providers"]["go"]["command"][0].endswith("gopls")
    assert payload["providers"]["java"]["command_source"] == "path"
    assert payload["providers"]["csharp"]["command_source"] == "managed"
