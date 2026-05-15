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
            "New-Item -ItemType Directory -Path $stagingFrontdoorDir -Force",
            'Set-Alias -Name tg -Value "$installDir\\bin\\tg.exe" -Scope Global',
        )
    )
    assert r"$installDir\.venv\Scripts\tg.exe" not in content


def test_install_ps1_should_download_release_native_frontdoor_and_configure_sidecar():
    content = _read_script("scripts/install.ps1")

    assert "tg-windows-amd64-cpu.exe" in content
    assert (
        "https://github.com/oimiragieo/tensor-grep/releases/download/v$installedVersion" in content
    )
    assert '$nativeFrontdoorPath = Join-Path $frontdoorDir "tg.exe"' in content
    assert "$env:TG_SIDECAR_PYTHON" in content
    assert "$env:TG_NATIVE_TG_BINARY" in content
    assert "set TG_SIDECAR_PYTHON=" in content
    assert "set TG_NATIVE_TG_BINARY=" in content
    assert "Remove-Item -LiteralPath $nativeFrontdoorPath -Force" in content


def test_install_ps1_should_require_opt_in_nvidia_native_frontdoor_with_cpu_fallback():
    content = _read_script("scripts/install.ps1")

    assert "TENSOR_GREP_NATIVE_FRONTDOOR_FLAVOR" in content
    assert "TG_NATIVE_FRONTDOOR_REQUESTED_FLAVOR" in content
    assert "TG_NATIVE_FRONTDOOR_FLAVOR" in content
    assert "tg-windows-amd64-nvidia.exe" in content
    assert (
        '$requestedFlavor = if ($requestedFlavor) { $requestedFlavor.ToLowerInvariant() } else { "cpu" }'
        in content
    )
    assert '$requestedFlavor -eq "auto"' not in content
    assert '$hardwareFlag -eq "nvidia"' not in content
    assert "Falling back to CPU native tg front-door asset" in content
    assert "asset flavor" in content
    assert "GPU promotion" not in content


def test_install_ps1_should_fail_fast_when_native_install_steps_fail():
    content = _read_script("scripts/install.ps1")

    assert "function Invoke-CheckedNativeCommand" in content
    assert "if ($LASTEXITCODE -ne 0)" in content
    assert 'Invoke-CheckedNativeCommand -Description "Create managed Python environment"' in content
    assert 'Invoke-CheckedNativeCommand -Description "Install tensor-grep package"' in content
    assert 'Invoke-CheckedNativeCommand -Description "Install AST runtime grammars"' in content
    assert content.index(
        'Invoke-CheckedNativeCommand -Description "Install AST runtime grammars"'
    ) < content.index("Commit-StagedManagedInstall `")


def test_install_ps1_should_write_frontdoor_inside_staging_before_swap():
    content = _read_script("scripts/install.ps1")

    assert '$stagingFrontdoorDir = Join-Path $stagingInstallDir "bin"' in content
    assert "$stagingFrontdoorCmdPath" in content
    assert "$stagingFrontdoorPs1Path" in content
    assert "$stagingFrontdoorBashPath" in content
    assert "Write-AsciiFile -Path $stagingFrontdoorCmdPath" in content
    assert "Write-AsciiFile -Path $stagingFrontdoorPs1Path" in content
    assert "Write-BashFile -Path $stagingFrontdoorBashPath" in content
    staged_frontdoor_write_index = content.index("Write-BashFile -Path $stagingFrontdoorBashPath")
    assert staged_frontdoor_write_index < content.index(
        "Commit-StagedManagedInstall `", staged_frontdoor_write_index
    )


def test_install_ps1_should_refresh_managed_lsp_providers_via_frontdoor():
    content = _read_script("scripts/install.ps1")

    assert "Installing managed external LSP providers" in content
    assert "& $frontdoorCmdPath lsp-setup --json | Out-Null" in content
    assert "Managed external LSP provider setup failed; run 'tg lsp-setup' manually." in content


def test_install_ps1_should_prepend_native_frontdoor_ahead_of_cmd_shim_dirs():
    content = _read_script("scripts/install.ps1")

    assert "$managedPathDirs = @($frontdoorDir) + $shimDirs" in content
    assert (
        "$userPathParts = @($managedPathDirs + "
        "($userPathParts | Where-Object { $managedPathDirs -notcontains $_ }))"
    ) in content
    assert "$currentPathParts = @($managedPathDirs + " in content
    assert "$env:Path = ($currentPathParts -join ';')" in content
    assert '"$userPath;$shimDir"' not in content
    assert '"$env:Path;$shimDir"' not in content


def test_install_ps1_should_scan_earlier_path_entries_for_stale_tg_launchers():
    content = _read_script("scripts/install.ps1")

    assert "Remove-StalePathLauncher" in content
    assert "Test-TensorGrepLauncher" in content
    assert 'StartsWith("tg ")' in content
    assert "& $CandidatePath --help" in content
    assert '$helpText -match "tensor-grep"' in content
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
    assert 'if ($staleShimName -eq "tg.exe") {' in content
    assert "Test-TensorGrepLauncher -CandidatePath $staleShimPath" in content
    assert "Skipping foreign tg.exe in tensor-grep shim dir" in content
    assert (
        "Python subprocess tg.exe bridge was not installed because a foreign tg.exe already exists"
        in content
    )
    assert "Remove-Item -LiteralPath $staleShimPath -Force" in content
    assert "Removed stale tg launcher shadowing managed shim" in content
    assert content.index("Remove-Item -LiteralPath $staleShimPath -Force") < content.index(
        "Write-AsciiFile -Path $cmdShimPath"
    )


def test_install_ps1_should_write_exe_bridge_for_python_subprocess_in_shim_dirs():
    content = _read_script("scripts/install.ps1")

    assert '$exeShimPath = "$shimDir\\tg.exe"' in content
    assert '$exeShimMarkerPath = "$shimDir\\tg.exe.tensor-grep-bridge"' in content
    assert "Copy-Item -LiteralPath $nativeFrontdoorPath -Destination $exeShimPath -Force" in content
    assert (
        'Write-AsciiFile -Path $exeShimMarkerPath -Value "tensor-grep managed tg.exe bridge`r`n"'
        in content
    )
    assert "$installedShimPaths += $exeShimPath" in content
    assert "Python subprocess tg.exe bridge" in content


def test_install_ps1_should_create_argv_safe_utf8_powershell_shims():
    content = _read_script("scripts/install.ps1")

    assert "$frontdoorPs1Path" in content
    assert '$env:PYTHONUTF8 = "1"' in content
    assert '$env:PYTHONIOENCODING = "utf-8"' in content
    assert '$env:TG_SIDECAR_PYTHON = "$installDir\\.venv\\Scripts\\python.exe"' in content
    assert 'if (Test-Path -LiteralPath "$nativeFrontdoorPath") {' in content
    assert '& "$nativeFrontdoorPath" @args' in content
    assert '& "$installDir\\.venv\\Scripts\\python.exe" -X utf8 -m tensor_grep @args' in content
    assert '& "$frontdoorPs1Path" @args' in content
    assert 'function tg { & `"$frontdoorPs1Path`" @args }' in content
    assert 'Set-Alias -Name tg -Value `"$frontdoorCmdPath`"' not in content


def test_install_ps1_should_create_cmd_shims_without_child_command_percent_star_expansion():
    content = _read_script("scripts/install.ps1")

    assert "$cmdArgvBridgeContent" in content
    assert "TG_CMD_SHIM_ARGC" in content
    assert "TG_CMD_SHIM_ARG_%TG_CMD_SHIM_ARGC%=%~1" in content
    assert "subprocess.run([native_tg] + argv, check=False)" in content
    assert "raise SystemExit(completed.returncode)" in content
    assert "os.execv" not in content
    assert 'runpy.run_module("tensor_grep", run_name="__main__")' in content
    assert "%*" not in content
    assert " -m tensor_grep %*" not in content
    assert '`"$frontdoorCmdPath`" %*' not in content


def test_install_ps1_should_create_git_bash_shims_without_pathext():
    content = _read_script("scripts/install.ps1")

    assert "$frontdoorBashPath" in content
    assert "$msysInstallDir" in content
    assert 'TG_NATIVE="$msysInstallDir/bin/tg.exe"' in content
    assert 'TG_PYTHON="$msysInstallDir/.venv/Scripts/python.exe"' in content
    assert 'export TG_SIDECAR_PYTHON="`$TG_PYTHON"' in content
    assert 'export TG_NATIVE_TG_BINARY="`$TG_NATIVE"' in content
    assert 'if [ -f "`$TG_NATIVE" ]; then' in content
    assert '    exec "`$TG_NATIVE" "`$@"' in content
    assert 'exec "`$TG_PYTHON" -X utf8 -m tensor_grep "`$@"' in content
    assert 'TG_FRONTDOOR="$msysFrontdoorPath"' in content
    assert 'exec "`$TG_FRONTDOOR" "`$@"' in content


def test_install_ps1_should_create_wsl_aware_bash_shims():
    content = _read_script("scripts/install.ps1")

    assert "function Convert-ToWslPath" in content
    assert "$wslInstallDir = Convert-ToWslPath $installDir" in content
    assert "$wslFrontdoorPath = Convert-ToWslPath $frontdoorBashPath" in content
    assert "grep -qi microsoft /proc/version" in content
    assert 'TG_NATIVE="$wslInstallDir/bin/tg.exe"' in content
    assert 'TG_PYTHON="$wslInstallDir/.venv/Scripts/python.exe"' in content
    assert 'TG_FRONTDOOR="$wslFrontdoorPath"' in content


def test_install_ps1_should_write_bash_shims_without_windows_newline_append():
    content = _read_script("scripts/install.ps1")

    assert "function Write-AsciiFile" in content
    assert "function Write-BashFile" in content
    assert "[System.IO.File]::WriteAllText" in content
    assert "WSL bash treats CR in shebangs" in content
    assert '$lfValue = ($Value -replace "`r`n", "`n") -replace "`r", "`n"' in content
    assert "Set-Content -Path $frontdoorBashPath" not in content
    assert "Set-Content -Path $bashShimPath" not in content
    assert "Write-BashFile -Path $stagingFrontdoorBashPath -Value $frontdoorBashContent" in content
    assert "Write-BashFile -Path $bashShimPath -Value $bashShimContent" in content


def test_install_ps1_should_place_extras_before_pinned_version_specifier():
    content = _read_script("scripts/install.ps1")

    assert '"tensor-grep[gpu-win,nlp,ast]==$requestedVersion"' in content
    assert '"tensor-grep[ast,nlp]==$requestedVersion"' in content
    assert '"$pkgSpec[gpu-win,nlp,ast]"' not in content
    assert '"$pkgSpec[ast,nlp]"' not in content


def test_install_ps1_should_use_current_gpu_wheel_indexes():
    content = _read_script("scripts/install.ps1")

    assert "https://download.pytorch.org/whl/cu128" in content
    assert "Windows ROCm support is selected/experimental; configuring CPU fallback" in content
    assert "https://download.pytorch.org/whl/cu124" not in content
    assert "https://download.pytorch.org/whl/rocm6.0" not in content
    assert "https://download.pytorch.org/whl/rocm7.2" not in content


def test_install_ps1_should_refresh_tensor_grep_uv_cache_before_stable_install():
    content = _read_script("scripts/install.ps1")

    assert "Clearing cached tensor-grep package metadata" in content
    assert "& $uvPath cache clean tensor-grep" in content
    assert content.index("& $uvPath cache clean tensor-grep") < content.index(
        "& $uvPath pip install $pkgRequirement"
    )


def test_install_ps1_should_stage_install_before_replacing_existing_managed_dir():
    content = _read_script("scripts/install.ps1")

    assert '$stagingInstallDir = "$installDir.installing"' in content
    assert '$backupInstallDir = "$installDir.previous"' in content
    assert "Set-Location $stagingInstallDir" in content
    assert "Set-Location -Path $env:USERPROFILE" in content
    assert "Move-Item -LiteralPath $installDir -Destination $backupInstallDir" in content
    assert "Move-Item -LiteralPath $stagingInstallDir -Destination $installDir" in content
    assert "Restore-PreviousManagedInstall" in content
    assert "Write-AsciiFile -Path $stagingFrontdoorCmdPath" in content
    staged_frontdoor_write_index = content.index("Write-AsciiFile -Path $stagingFrontdoorCmdPath")
    assert staged_frontdoor_write_index < content.index(
        "Commit-StagedManagedInstall `", staged_frontdoor_write_index
    )
    assert "Remove-Item -Recurse -Force $installDir" not in content


def test_install_sh_should_target_native_frontdoor_instead_of_venv_console_script():
    content = _read_script("scripts/install.sh")

    assert any(
        marker in content
        for marker in (
            'mkdir -p "$INSTALL_DIR/bin"',
            'mkdir -p "$STAGING_INSTALL_DIR/bin"',
            'cat > "$INSTALL_DIR/bin/tg" << EOF',
            'cat > "$STAGING_INSTALL_DIR/bin/tg" << EOF',
            "ALIAS_CMD=\"alias tg='$INSTALL_DIR/bin/tg'\"",
        )
    )
    assert '"$INSTALL_DIR/.venv/bin/tg"' not in content


def test_install_sh_should_download_release_native_frontdoor_and_configure_sidecar():
    content = _read_script("scripts/install.sh")

    assert "tg-linux-amd64-cpu" in content
    assert "tg-macos-amd64-cpu" in content
    assert 'NATIVE_BINARY="$INSTALL_DIR/bin/tg-native"' in content
    assert (
        "https://github.com/oimiragieo/tensor-grep/releases/download/v${INSTALLED_VERSION}"
        in content
    )
    assert 'export TG_SIDECAR_PYTHON="$INSTALL_DIR/.venv/bin/python"' in content
    assert 'export TG_NATIVE_TG_BINARY="\\$NATIVE_BINARY"' in content
    assert 'exec "\\$NATIVE_BINARY" "\\$@"' in content


def test_install_sh_should_require_opt_in_nvidia_native_frontdoor_with_cpu_fallback():
    content = _read_script("scripts/install.sh")

    assert "TENSOR_GREP_NATIVE_FRONTDOOR_FLAVOR" in content
    assert "TG_NATIVE_FRONTDOOR_REQUESTED_FLAVOR" in content
    assert "TG_NATIVE_FRONTDOOR_FLAVOR" in content
    assert "tg-linux-amd64-nvidia" in content
    assert (
        'TG_NATIVE_FRONTDOOR_REQUESTED_FLAVOR="${TENSOR_GREP_NATIVE_FRONTDOOR_FLAVOR:-${TG_NATIVE_FRONTDOOR_REQUESTED_FLAVOR:-cpu}}"'
        in content
    )
    assert "auto:nvidia" not in content
    assert "Falling back to CPU native tg front-door asset" in content
    assert "asset flavor" in content
    assert "GPU promotion" not in content


def test_install_sh_should_use_current_gpu_wheel_indexes():
    content = _read_script("scripts/install.sh")

    assert "https://download.pytorch.org/whl/cu128" in content
    assert "https://download.pytorch.org/whl/rocm7.2" in content
    assert "https://download.pytorch.org/whl/cu124" not in content
    assert "https://download.pytorch.org/whl/rocm6.0" not in content


def test_install_sh_should_write_frontdoor_inside_staging_before_swap():
    content = _read_script("scripts/install.sh")

    assert 'mkdir -p "$STAGING_INSTALL_DIR/bin"' in content
    assert 'STAGING_NATIVE_BINARY="$STAGING_INSTALL_DIR/bin/tg-native"' in content
    assert 'curl -fL "$NATIVE_URL" -o "$STAGING_NATIVE_BINARY.tmp"' in content
    assert 'cat > "$STAGING_INSTALL_DIR/bin/tg" << EOF' in content
    assert 'chmod +x "$STAGING_INSTALL_DIR/bin/tg"' in content
    assert content.index('chmod +x "$STAGING_INSTALL_DIR/bin/tg"') < content.rindex(
        "\ncommit_staged_install"
    )


def test_install_sh_should_refresh_tensor_grep_uv_cache_before_stable_install():
    content = _read_script("scripts/install.sh")

    assert "Clearing cached tensor-grep package metadata" in content
    assert "uv cache clean tensor-grep" in content
    assert content.index("uv cache clean tensor-grep") < content.index(
        'uv pip install "$PKG_REQUIREMENT"'
    )


def test_install_sh_should_stage_install_before_replacing_existing_managed_dir():
    content = _read_script("scripts/install.sh")

    assert 'STAGING_INSTALL_DIR="$INSTALL_DIR.installing"' in content
    assert 'BACKUP_INSTALL_DIR="$INSTALL_DIR.previous"' in content
    assert 'cd "$STAGING_INSTALL_DIR"' in content
    assert 'cd "$HOME"' in content
    assert 'mv "$INSTALL_DIR" "$BACKUP_INSTALL_DIR"' in content
    assert 'mv "$STAGING_INSTALL_DIR" "$INSTALL_DIR"' in content
    assert "restore_previous_install" in content
    assert 'cat > "$STAGING_INSTALL_DIR/bin/tg"' in content
    staged_frontdoor_write_index = content.index('cat > "$STAGING_INSTALL_DIR/bin/tg"')
    assert staged_frontdoor_write_index < content.index(
        "commit_staged_install", staged_frontdoor_write_index
    )
    assert 'rm -rf "$INSTALL_DIR"\nmkdir -p "$INSTALL_DIR"' not in content


def test_install_sh_should_refresh_managed_lsp_providers_via_frontdoor():
    content = _read_script("scripts/install.sh")

    assert "Installing managed external LSP providers" in content
    assert 'if "$INSTALL_DIR/bin/tg" lsp-setup --json > /dev/null; then' in content
    assert "Managed external LSP provider setup failed; run 'tg lsp-setup' manually." in content
