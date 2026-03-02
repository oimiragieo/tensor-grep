from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]


def test_windows_installer_writes_path_shims_and_both_profiles() -> None:
    script = (ROOT / "scripts" / "install.ps1").read_text(encoding="utf-8")
    assert "$env:USERPROFILE\\.local\\bin" in script
    assert "$env:USERPROFILE\\bin" in script
    assert '$cmdShimPath = "$shimDir\\tg.cmd"' in script
    assert "PowerShell\\Microsoft.PowerShell_profile.ps1" in script
    assert "WindowsPowerShell\\Microsoft.PowerShell_profile.ps1" in script
    assert "Set-Alias -Name tg" in script
    assert '"tensor-grep"' in script
    assert "tensor-grep==$defaultVersion" not in script


def test_unix_installer_writes_shims_and_exports_path() -> None:
    script = (ROOT / "scripts" / "install.sh").read_text(encoding="utf-8")
    assert 'SHIM_DIRS=("$HOME/.local/bin" "$HOME/bin")' in script
    assert 'chmod +x "$SHIM_PATH"' in script
    assert "PATH_EXPORT_LOCAL='export PATH=\"$HOME/.local/bin:$PATH\"'" in script
    assert "PATH_EXPORT_BIN='export PATH=\"$HOME/bin:$PATH\"'" in script
    assert "ALIAS_CMD=\"alias tg='$INSTALL_DIR/.venv/bin/tg'\"" in script
    assert 'PKG_SPEC="tensor-grep"' in script
    assert "DEFAULT_VERSION=" not in script
