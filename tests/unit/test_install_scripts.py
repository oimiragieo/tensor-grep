from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]


def _read_script(path: str) -> str:
    return (ROOT / path).read_text(encoding="utf-8")


def test_install_ps1_should_restore_original_directory_in_finally_block():
    content = _read_script("scripts/install.ps1")
    assert "$originalPath = (Get-Location).Path" in content
    assert "finally {" in content
    assert "Set-Location -Path $originalPath" in content


def test_install_sh_should_capture_original_directory_and_restore_on_exit():
    content = _read_script("scripts/install.sh")
    assert 'ORIGINAL_DIR="$(pwd)"' in content
    assert "trap restore_original_dir EXIT" in content
    assert 'cd "$ORIGINAL_DIR"' in content
    assert 'echo "Returned to original directory: $ORIGINAL_DIR"' in content


def test_install_ps1_should_target_native_frontdoor_instead_of_venv_console_script():
    content = _read_script("scripts/install.ps1")

    assert any(
        marker in content
        for marker in (
            'New-Item -ItemType Directory -Path "$installDir\\bin"',
            'New-Item -ItemType Directory -Path (Join-Path $installDir "bin")',
            'Set-Alias -Name tg -Value "$installDir\\bin\\tg.exe" -Scope Global',
        )
    )
    assert r"$installDir\.venv\Scripts\tg.exe" not in content


def test_install_ps1_should_refresh_managed_lsp_providers_via_frontdoor():
    content = _read_script("scripts/install.ps1")

    assert "Installing managed external LSP providers" in content
    assert "& $frontdoorCmdPath lsp-setup --json | Out-Null" in content
    assert "Managed external LSP provider setup failed; run 'tg lsp-setup' manually." in content


def test_install_ps1_should_prepend_shim_dirs_ahead_of_stale_python_scripts():
    content = _read_script("scripts/install.ps1")

    assert (
        "$userPathParts = @($shimDirs + "
        "($userPathParts | Where-Object { $shimDirs -notcontains $_ }))"
    ) in content
    assert "$env:Path = ($currentPathParts -join ';')" in content
    assert '"$userPath;$shimDir"' not in content
    assert '"$env:Path;$shimDir"' not in content


def test_install_ps1_should_scan_earlier_path_entries_for_stale_tg_launchers():
    content = _read_script("scripts/install.ps1")

    assert "Remove-StalePathLauncher" in content
    assert "$effectivePathParts" in content
    assert "$managedPathSet" in content
    assert "& $candidatePath --version" in content
    assert "stale tg launcher remains ahead of managed shim" in content


def test_install_ps1_should_remove_unmanaged_tg_launchers_even_when_version_matches():
    content = _read_script("scripts/install.ps1")

    assert "Removed unmanaged tg launcher from PATH" in content
    assert "if ($candidateVersion -eq $managedVersionLine)" not in content


def test_install_ps1_should_uninstall_python_package_that_owns_stale_launcher():
    content = _read_script("scripts/install.ps1")

    assert "Attempting to uninstall stale tensor-grep package that owns PATH launcher" in content
    assert "-m pip uninstall -y tensor-grep" in content
    assert "Removed stale tensor-grep Python package from PATH owner" in content


def test_install_ps1_should_skip_inaccessible_path_entries_when_scanning_launchers():
    content = _read_script("scripts/install.ps1")

    assert "Test-Path -LiteralPath $pathPart -ErrorAction Stop" in content
    assert "Test-Path -LiteralPath $candidatePath -ErrorAction Stop" in content
    assert "Resolve-Path -LiteralPath $candidatePath -ErrorAction Stop" in content
    assert "Skipping inaccessible PATH entry while checking tg launchers" in content


def test_install_ps1_should_remove_stale_same_dir_tg_launchers_before_cmd_shim():
    content = _read_script("scripts/install.ps1")

    assert '"tg.com", "tg.exe", "tg.bat", "tg.ps1"' in content
    assert "Remove-Item -LiteralPath $staleShimPath -Force" in content
    assert "Removed stale tg launcher shadowing managed shim" in content
    assert content.index("Remove-Item -LiteralPath $staleShimPath -Force") < content.index(
        "Set-Content -Path $cmdShimPath"
    )


def test_install_ps1_should_create_argv_safe_utf8_powershell_shims():
    content = _read_script("scripts/install.ps1")

    assert "$frontdoorPs1Path" in content
    assert '$env:PYTHONUTF8 = "1"' in content
    assert '$env:PYTHONIOENCODING = "utf-8"' in content
    assert '& "$installDir\\.venv\\Scripts\\python.exe" -X utf8 -m tensor_grep @args' in content
    assert '& "$frontdoorPs1Path" @args' in content
    assert 'function tg { & `"$frontdoorPs1Path`" @args }' in content
    assert 'Set-Alias -Name tg -Value `"$frontdoorCmdPath`"' not in content


def test_install_ps1_should_create_git_bash_shims_without_pathext():
    content = _read_script("scripts/install.ps1")

    assert "$frontdoorBashPath" in content
    assert "$msysInstallDir" in content
    assert '"$msysInstallDir/.venv/Scripts/python.exe" -X utf8 -m tensor_grep "$@"' in content
    assert '"$msysFrontdoorPath" "$@"' in content


def test_install_ps1_should_place_extras_before_pinned_version_specifier():
    content = _read_script("scripts/install.ps1")

    assert '"tensor-grep[gpu-win,nlp,ast]==$requestedVersion"' in content
    assert '"tensor-grep[ast,nlp]==$requestedVersion"' in content
    assert '"$pkgSpec[gpu-win,nlp,ast]"' not in content
    assert '"$pkgSpec[ast,nlp]"' not in content


def test_install_sh_should_target_native_frontdoor_instead_of_venv_console_script():
    content = _read_script("scripts/install.sh")

    assert any(
        marker in content
        for marker in (
            'mkdir -p "$INSTALL_DIR/bin"',
            'cat > "$INSTALL_DIR/bin/tg" << EOF',
            "ALIAS_CMD=\"alias tg='$INSTALL_DIR/bin/tg'\"",
        )
    )
    assert '"$INSTALL_DIR/.venv/bin/tg"' not in content


def test_install_sh_should_refresh_managed_lsp_providers_via_frontdoor():
    content = _read_script("scripts/install.sh")

    assert "Installing managed external LSP providers" in content
    assert 'if "$INSTALL_DIR/bin/tg" lsp-setup --json > /dev/null; then' in content
    assert "Managed external LSP provider setup failed; run 'tg lsp-setup' manually." in content
