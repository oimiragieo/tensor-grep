<#
.SYNOPSIS
    Installs Tensor-Grep with automatic GPU detection and Python 3.12 isolation via uv.
#>

$ErrorActionPreference = "Stop"
$originalPath = (Get-Location).Path
$originalProcessPath = $env:Path
$installChannel = if ($env:TENSOR_GREP_CHANNEL) { $env:TENSOR_GREP_CHANNEL } else { "stable" }
$requestedVersion = $env:TENSOR_GREP_VERSION

function Convert-ToMsysPath {
    param([Parameter(Mandatory = $true)][string]$Path)

    $normalizedPath = $Path -replace '\\', '/'
    if ($normalizedPath -match '^([A-Za-z]):/(.*)$') {
        return "/$($Matches[1].ToLowerInvariant())/$($Matches[2])"
    }
    return $normalizedPath
}

function Convert-ToWslPath {
    param([Parameter(Mandatory = $true)][string]$Path)

    $normalizedPath = $Path -replace '\\', '/'
    if ($normalizedPath -match '^([A-Za-z]):/(.*)$') {
        return "/mnt/$($Matches[1].ToLowerInvariant())/$($Matches[2])"
    }
    return $normalizedPath
}

function Write-AsciiFile {
    param(
        [Parameter(Mandatory = $true)][string]$Path,
        [Parameter(Mandatory = $true)][string]$Value
    )

    [System.IO.File]::WriteAllText($Path, $Value, [System.Text.Encoding]::ASCII)
}

function Write-BashFile {
    param(
        [Parameter(Mandatory = $true)][string]$Path,
        [Parameter(Mandatory = $true)][string]$Value
    )

    # WSL bash treats CR in shebangs or final "$@" lines as syntax or argv bytes.
    $lfValue = ($Value -replace "`r`n", "`n") -replace "`r", "`n"
    Write-AsciiFile -Path $Path -Value $lfValue
}

function Invoke-CheckedNativeCommand {
    param(
        [Parameter(Mandatory = $true)][string]$Description,
        [Parameter(Mandatory = $true)][scriptblock]$Command
    )

    & $Command | Out-Host
    if ($LASTEXITCODE -ne 0) {
        throw "$Description failed with exit code $LASTEXITCODE"
    }
}

function Install-NativeFrontdoorBinary {
    param(
        [Parameter(Mandatory = $true)][string]$frontdoorDir,
        [Parameter(Mandatory = $true)][string]$installedVersion,
        [Parameter(Mandatory = $true)][string]$installChannel
    )

    $nativeFrontdoorPath = Join-Path $frontdoorDir "tg.exe"
    if ($installChannel -eq "main") {
        Write-Host "      Main-channel install: using Python front door until release-native assets exist."
        return $nativeFrontdoorPath
    }

    $nativeAssetName = "tg-windows-amd64-cpu.exe"
    $nativeDownloadUrl = "https://github.com/oimiragieo/tensor-grep/releases/download/v$installedVersion/$nativeAssetName"
    $nativeTempPath = "$nativeFrontdoorPath.download"
    Write-Host "      Downloading native tg front door: $nativeAssetName"
    try {
        Invoke-WebRequest -Uri $nativeDownloadUrl -OutFile $nativeTempPath
        Move-Item -LiteralPath $nativeTempPath -Destination $nativeFrontdoorPath -Force
        & $nativeFrontdoorPath --version | Out-Host
        if ($LASTEXITCODE -ne 0) {
            throw "native tg front door smoke test failed"
        }
        Write-Host "      Native tg front door installed: $nativeFrontdoorPath"
    } catch {
        Remove-Item -LiteralPath $nativeTempPath -Force -ErrorAction SilentlyContinue
        Remove-Item -LiteralPath $nativeFrontdoorPath -Force -ErrorAction SilentlyContinue
        Write-Warning (
            "Native tg front-door download failed; falling back to Python wrapper. " +
            "Expected asset: $nativeDownloadUrl. Error: $_"
        )
    }
    return $nativeFrontdoorPath
}

function Remove-StalePythonPackageLauncher {
    param(
        [Parameter(Mandatory = $true)][string]$candidatePath,
        [Parameter(Mandatory = $true)][string]$candidateVersion
    )

    $candidateDir = Split-Path -Parent $candidatePath
    if ((Split-Path -Leaf $candidateDir) -ne "Scripts") {
        return $false
    }

    $pythonExe = Join-Path (Split-Path -Parent $candidateDir) "python.exe"
    try {
        if (!(Test-Path -LiteralPath $pythonExe -ErrorAction Stop)) {
            return $false
        }
        $packageInfo = & $pythonExe -m pip show tensor-grep 2>$null
    } catch {
        return $false
    }
    if (!$packageInfo) {
        return $false
    }

    Write-Host (
        "Attempting to uninstall stale tensor-grep package that owns PATH launcher: " +
        "$candidatePath ($candidateVersion)"
    )
    try {
        & $pythonExe -m pip uninstall -y tensor-grep | Out-Host
        if ($LASTEXITCODE -ne 0) {
            return $false
        }
        if (Test-Path -LiteralPath $candidatePath -ErrorAction Stop) {
            Remove-Item -LiteralPath $candidatePath -Force -ErrorAction Stop
        }
        Write-Host "Removed stale tensor-grep Python package from PATH owner: $pythonExe"
        return $true
    } catch {
        return $false
    }
}

function Remove-StalePathLauncher {
    param(
        [Parameter(Mandatory = $true)][string[]]$effectivePathParts,
        [Parameter(Mandatory = $true)][hashtable]$managedPathSet
    )

    foreach ($pathPart in $effectivePathParts) {
        if (!$pathPart) {
            continue
        }
        $pathPartExists = $false
        try {
            $pathPartExists = Test-Path -LiteralPath $pathPart -ErrorAction Stop
        } catch {
            Write-Verbose "Skipping inaccessible PATH entry while checking tg launchers: $pathPart"
            continue
        }
        if (!$pathPartExists) {
            continue
        }
        foreach ($launcherName in @("tg.com", "tg.exe", "tg.bat", "tg.cmd", "tg.ps1", "tg")) {
            $candidatePath = Join-Path $pathPart $launcherName
            $candidateExists = $false
            try {
                $candidateExists = Test-Path -LiteralPath $candidatePath -ErrorAction Stop
            } catch {
                continue
            }
            if (!$candidateExists) {
                continue
            }
            try {
                $resolvedCandidate = (Resolve-Path -LiteralPath $candidatePath -ErrorAction Stop).Path
            } catch {
                continue
            }
            if ($managedPathSet.ContainsKey($resolvedCandidate.ToLowerInvariant())) {
                continue
            }
            $candidateVersion = ""
            try {
                $candidateVersion = (& $candidatePath --version 2>$null | Select-Object -First 1)
            } catch {
                continue
            }
            if (!$candidateVersion.StartsWith("tensor-grep ")) {
                continue
            }
            try {
                Remove-Item -LiteralPath $candidatePath -Force -ErrorAction Stop
                Write-Host "Removed unmanaged tg launcher from PATH: $candidatePath ($candidateVersion)"
            } catch {
                if (Remove-StalePythonPackageLauncher `
                        -candidatePath $candidatePath `
                        -candidateVersion $candidateVersion) {
                    continue
                }
                Write-Warning (
                    "WARNING: stale tg launcher remains ahead of managed shim: " +
                    "$candidatePath ($candidateVersion). Remove it or move the managed shim " +
                    "directories earlier in Machine PATH."
                )
            }
        }
    }
}

function Clear-TensorGrepUvCache {
    param(
        [Parameter(Mandatory = $true)][string]$uvPath,
        [Parameter(Mandatory = $true)][string]$installChannel
    )

    if ($installChannel -ne "stable") {
        return
    }
    Write-Host "      Clearing cached tensor-grep package metadata for stable install..."
    try {
        & $uvPath cache clean tensor-grep | Out-Host
        if ($LASTEXITCODE -ne 0) {
            Write-Warning "Unable to clear cached tensor-grep package metadata; continuing with fresh install attempt. Exit code: $LASTEXITCODE"
        }
    } catch {
        Write-Warning "Unable to clear cached tensor-grep package metadata; continuing with fresh install attempt. Error: $_"
    }
}

function Restore-PreviousManagedInstall {
    param(
        [Parameter(Mandatory = $true)][string]$installDir,
        [Parameter(Mandatory = $true)][string]$backupInstallDir
    )

    if (!(Test-Path -LiteralPath $backupInstallDir)) {
        return
    }
    Remove-Item -LiteralPath $installDir -Recurse -Force -ErrorAction SilentlyContinue
    Move-Item -LiteralPath $backupInstallDir -Destination $installDir -Force
}

function Commit-StagedManagedInstall {
    param(
        [Parameter(Mandatory = $true)][string]$installDir,
        [Parameter(Mandatory = $true)][string]$stagingInstallDir,
        [Parameter(Mandatory = $true)][string]$backupInstallDir
    )

    Remove-Item -LiteralPath $backupInstallDir -Recurse -Force -ErrorAction SilentlyContinue
    if (Test-Path -LiteralPath $installDir) {
        Move-Item -LiteralPath $installDir -Destination $backupInstallDir
    }
    try {
        Move-Item -LiteralPath $stagingInstallDir -Destination $installDir
        Remove-Item -LiteralPath $backupInstallDir -Recurse -Force -ErrorAction SilentlyContinue
    } catch {
        Restore-PreviousManagedInstall -installDir $installDir -backupInstallDir $backupInstallDir
        throw
    }
}

Write-Host "=========================================================="
Write-Host "           TENSOR-GREP WINDOWS INSTALLER                  "
Write-Host "=========================================================="

try {
    # 1. Install or locate uv
    $uvPath = "uv"
    if (!(Get-Command "uv" -ErrorAction SilentlyContinue)) {
        Write-Host "[1/4] Downloading uv package manager..."
        Invoke-WebRequest -Uri "https://astral.sh/uv/install.ps1" -OutFile "$env:TEMP\uv_install.ps1"
        & "$env:TEMP\uv_install.ps1"
        $uvPath = "$env:USERPROFILE\.local\bin\uv.exe"
    } else {
        Write-Host "[1/4] Found existing uv installation."
    }

    # 2. Detect GPU Configuration
    Write-Host "[2/4] Detecting hardware for optimal routing..."
    $gpuQuery = Get-WmiObject Win32_VideoController | Select-Object Name
    $hardwareFlag = "cpu"
    $indexUrl = ""

    if ($gpuQuery.Name -match "NVIDIA") {
        Write-Host "      Detected NVIDIA GPU. Configuring for CUDA 12.4."
        $hardwareFlag = "nvidia"
        $indexArg = "--index-url"
        $indexUrl = "https://download.pytorch.org/whl/cu124"
    } elseif ($gpuQuery.Name -match "AMD" -or $gpuQuery.Name -match "Radeon") {
        Write-Host "      Detected AMD GPU. Configuring for ROCm."
        $hardwareFlag = "amd"
        $indexArg = "--index-url"
        $indexUrl = "https://download.pytorch.org/whl/rocm6.0"
    } else {
        Write-Host "      No compatible GPU detected. Configuring for CPU-only execution."
    }

    # 3. Create Isolated Environment
    $installDir = "$env:USERPROFILE\.tensor-grep"
    $stagingInstallDir = "$installDir.installing"
    $backupInstallDir = "$installDir.previous"
    Remove-Item -LiteralPath $stagingInstallDir -Recurse -Force -ErrorAction SilentlyContinue
    New-Item -ItemType Directory -Path $stagingInstallDir | Out-Null

    Write-Host "[3/4] Building isolated Python 3.12 environment..."
    Clear-TensorGrepUvCache -uvPath $uvPath -installChannel $installChannel
    Set-Location $stagingInstallDir
    Invoke-CheckedNativeCommand -Description "Create managed Python environment" -Command {
        & $uvPath venv --python 3.12 .venv
    }

    # 4. Install PyTorch bindings and the tool
    Write-Host "[4/4] Installing tensor-grep and ML bindings (this may take a few minutes for CUDA)..."
    $pkgSpec = if ($installChannel -eq "main") {
        "git+https://github.com/oimiragieo/tensor-grep.git@main"
    } elseif ($requestedVersion) {
        "tensor-grep==$requestedVersion"
    } else {
        "tensor-grep"
    }
    Write-Host "      Install source: $installChannel"
    if ($installChannel -eq "stable") {
        Write-Host "      Package: $pkgSpec"
    }

    if ($hardwareFlag -ne "cpu") {
        # Install PyTorch with specific index first to ensure correct wheel resolution
        Invoke-CheckedNativeCommand -Description "Install PyTorch bindings" -Command {
            & $uvPath pip install torch torchvision torchaudio $indexArg $indexUrl --python "$stagingInstallDir\.venv\Scripts\python.exe"
        }
        $pkgRequirement = if ($installChannel -eq "main") {
            "tensor-grep[gpu-win,nlp,ast] @ $pkgSpec"
        } elseif ($requestedVersion) {
            "tensor-grep[gpu-win,nlp,ast]==$requestedVersion"
        } else {
            "tensor-grep[gpu-win,nlp,ast]"
        }
        Invoke-CheckedNativeCommand -Description "Install tensor-grep package" -Command {
            & $uvPath pip install $pkgRequirement --python "$stagingInstallDir\.venv\Scripts\python.exe"
        }
    } else {
        $pkgRequirement = if ($installChannel -eq "main") {
            "tensor-grep[ast,nlp] @ $pkgSpec"
        } elseif ($requestedVersion) {
            "tensor-grep[ast,nlp]==$requestedVersion"
        } else {
            "tensor-grep[ast,nlp]"
        }
        Invoke-CheckedNativeCommand -Description "Install tensor-grep package" -Command {
            & $uvPath pip install $pkgRequirement --python "$stagingInstallDir\.venv\Scripts\python.exe"
        }
    }

    # Ensure AST runtime grammars are present explicitly across environments.
    Invoke-CheckedNativeCommand -Description "Install AST runtime grammars" -Command {
        & $uvPath pip install tree-sitter tree-sitter-python tree-sitter-javascript --python "$stagingInstallDir\.venv\Scripts\python.exe"
    }

    # 5. Prepare the front-door wrapper inside the staged install before replacing
    # the existing managed directory. A failed native download or interrupted shim
    # write must not leave public shims pointing at a half-built install.
    $frontdoorDir = Join-Path $installDir "bin"
    $stagingFrontdoorDir = Join-Path $stagingInstallDir "bin"
    if (!(Test-Path $stagingFrontdoorDir)) {
        New-Item -ItemType Directory -Path $stagingFrontdoorDir -Force | Out-Null
    }
    $installedVersion = (& "$stagingInstallDir\.venv\Scripts\python.exe" -c "import importlib.metadata; print(importlib.metadata.version('tensor-grep'))").Trim()
    if ($LASTEXITCODE -ne 0 -or !$installedVersion) {
        throw "Read installed tensor-grep version failed with exit code $LASTEXITCODE"
    }
    $nativeFrontdoorPath = Join-Path $frontdoorDir "tg.exe"
    $stagingNativeFrontdoorPath = Install-NativeFrontdoorBinary `
        -frontdoorDir $stagingFrontdoorDir `
        -installedVersion $installedVersion `
        -installChannel $installChannel
    $frontdoorCmdPath = Join-Path $frontdoorDir "tg.cmd"
    $frontdoorPs1Path = Join-Path $frontdoorDir "tg.ps1"
    $frontdoorBashPath = Join-Path $frontdoorDir "tg"
    $msysInstallDir = Convert-ToMsysPath $installDir
    $frontdoorArgBridgePath = Join-Path $frontdoorDir "tg-cmd-bridge.py"
    $wslInstallDir = Convert-ToWslPath $installDir
    $msysFrontdoorPath = Convert-ToMsysPath $frontdoorBashPath
    $wslFrontdoorPath = Convert-ToWslPath $frontdoorBashPath
    $stagingFrontdoorCmdPath = Join-Path $stagingFrontdoorDir "tg.cmd"
    $stagingFrontdoorPs1Path = Join-Path $stagingFrontdoorDir "tg.ps1"
    $stagingFrontdoorBashPath = Join-Path $stagingFrontdoorDir "tg"
    $stagingFrontdoorArgBridgePath = Join-Path $stagingFrontdoorDir "tg-cmd-bridge.py"
    if ((Test-Path -LiteralPath $stagingNativeFrontdoorPath) -and ($stagingNativeFrontdoorPath -ne (Join-Path $stagingFrontdoorDir "tg.exe"))) {
        Move-Item -LiteralPath $stagingNativeFrontdoorPath -Destination (Join-Path $stagingFrontdoorDir "tg.exe") -Force
    }
    $frontdoorCmdContent = (
        "@echo off`r`n" +
        "setlocal`r`n" +
        "set PYTHONUTF8=1`r`n" +
        "set PYTHONIOENCODING=utf-8`r`n" +
        "set TG_SIDECAR_PYTHON=$installDir\.venv\Scripts\python.exe`r`n" +
        "set TG_NATIVE_TG_BINARY=$nativeFrontdoorPath`r`n" +
        "set /a TG_CMD_SHIM_ARGC=0`r`n" +
        ":tg_arg_loop`r`n" +
        'if "%~1"=="" goto tg_arg_done' + "`r`n" +
        "set /a TG_CMD_SHIM_ARGC+=1`r`n" +
        'set "TG_CMD_SHIM_ARG_%TG_CMD_SHIM_ARGC%=%~1"' + "`r`n" +
        "shift`r`n" +
        "goto tg_arg_loop`r`n" +
        ":tg_arg_done`r`n" +
        "`"$installDir\.venv\Scripts\python.exe`" -X utf8 `"$frontdoorArgBridgePath`"`r`n"
    )
    $frontdoorPs1Content = @"
`$env:PYTHONUTF8 = "1"
`$env:PYTHONIOENCODING = "utf-8"
`$env:TG_SIDECAR_PYTHON = "$installDir\.venv\Scripts\python.exe"
if (Test-Path -LiteralPath "$nativeFrontdoorPath") {
    `$env:TG_NATIVE_TG_BINARY = "$nativeFrontdoorPath"
    & "$nativeFrontdoorPath" @args
} else {
    & "$installDir\.venv\Scripts\python.exe" -X utf8 -m tensor_grep @args
}
exit `$LASTEXITCODE
"@
    $cmdArgvBridgeContent = @'
import os
import runpy
import subprocess
import sys

try:
    argc = int(os.environ.get("TG_CMD_SHIM_ARGC", "0") or "0")
except ValueError:
    argc = 0

argv = [
    os.environ.get(f"TG_CMD_SHIM_ARG_{index}", "")
    for index in range(1, argc + 1)
]

os.environ["PYTHONUTF8"] = "1"
os.environ["PYTHONIOENCODING"] = "utf-8"
native_tg = os.environ.get("TG_NATIVE_TG_BINARY")
if native_tg and os.path.isfile(native_tg):
    completed = subprocess.run([native_tg] + argv, check=False)
    raise SystemExit(completed.returncode)
sys.argv = ["tensor-grep"] + argv
runpy.run_module("tensor_grep", run_name="__main__")
'@
    $frontdoorBashContent = @"
#!/usr/bin/env bash
if grep -qi microsoft /proc/version 2>/dev/null; then
    TG_NATIVE="$wslInstallDir/bin/tg.exe"
    TG_PYTHON="$wslInstallDir/.venv/Scripts/python.exe"
else
    TG_NATIVE="$msysInstallDir/bin/tg.exe"
    TG_PYTHON="$msysInstallDir/.venv/Scripts/python.exe"
fi
export PYTHONUTF8=1
export PYTHONIOENCODING=utf-8
export TG_SIDECAR_PYTHON="`$TG_PYTHON"
export TG_NATIVE_TG_BINARY="`$TG_NATIVE"
if [ -f "`$TG_NATIVE" ]; then
    exec "`$TG_NATIVE" "`$@"
fi
exec "`$TG_PYTHON" -X utf8 -m tensor_grep "`$@"
"@
    Write-AsciiFile -Path $stagingFrontdoorCmdPath -Value $frontdoorCmdContent
    Write-AsciiFile -Path $stagingFrontdoorArgBridgePath -Value $cmdArgvBridgeContent
    Write-AsciiFile -Path $stagingFrontdoorPs1Path -Value $frontdoorPs1Content
    Write-BashFile -Path $stagingFrontdoorBashPath -Value $frontdoorBashContent

    Set-Location -Path $env:USERPROFILE
    Commit-StagedManagedInstall `
        -installDir $installDir `
        -stagingInstallDir $stagingInstallDir `
        -backupInstallDir $backupInstallDir
    Set-Location $installDir

    Write-Host "      Installing managed external LSP providers..."
    try {
        & $frontdoorCmdPath lsp-setup --json | Out-Null
        if ($LASTEXITCODE -ne 0) {
            Write-Warning "Managed external LSP provider setup failed; run 'tg lsp-setup' manually."
        } else {
            Write-Host "      Managed external LSP providers installed."
        }
    } catch {
        Write-Warning "Managed external LSP provider setup failed; run 'tg lsp-setup' manually."
    }

    $shimDirs = @(
        "$env:USERPROFILE\.local\bin",
        "$env:USERPROFILE\bin"
    )
    $managedPathDirs = @($frontdoorDir) + $shimDirs
    $cmdShimContent = (
        "@echo off`r`n" +
        "setlocal`r`n" +
        "set PYTHONUTF8=1`r`n" +
        "set PYTHONIOENCODING=utf-8`r`n" +
        "set TG_SIDECAR_PYTHON=$installDir\.venv\Scripts\python.exe`r`n" +
        "set TG_NATIVE_TG_BINARY=$nativeFrontdoorPath`r`n" +
        "set /a TG_CMD_SHIM_ARGC=0`r`n" +
        ":tg_arg_loop`r`n" +
        'if "%~1"=="" goto tg_arg_done' + "`r`n" +
        "set /a TG_CMD_SHIM_ARGC+=1`r`n" +
        'set "TG_CMD_SHIM_ARG_%TG_CMD_SHIM_ARGC%=%~1"' + "`r`n" +
        "shift`r`n" +
        "goto tg_arg_loop`r`n" +
        ":tg_arg_done`r`n" +
        "`"$installDir\.venv\Scripts\python.exe`" -X utf8 `"$frontdoorArgBridgePath`"`r`n"
    )
    $ps1ShimContent = @"
& "$frontdoorPs1Path" @args
exit `$LASTEXITCODE
"@
    $bashShimContent = @"
#!/usr/bin/env bash
if grep -qi microsoft /proc/version 2>/dev/null; then
    TG_FRONTDOOR="$wslFrontdoorPath"
else
    TG_FRONTDOOR="$msysFrontdoorPath"
fi
exec "`$TG_FRONTDOOR" "`$@"
"@
    $installedShimPaths = @()
    foreach ($shimDir in $shimDirs) {
        if (!(Test-Path $shimDir)) {
            New-Item -ItemType Directory -Path $shimDir -Force | Out-Null
        }
        foreach ($staleShimName in @("tg.com", "tg.exe", "tg.bat", "tg.ps1", "tg")) {
            $staleShimPath = Join-Path $shimDir $staleShimName
            if (Test-Path -LiteralPath $staleShimPath) {
                Remove-Item -LiteralPath $staleShimPath -Force
                Write-Host "Removed stale tg launcher shadowing managed shim: $staleShimPath"
            }
        }
        $cmdShimPath = "$shimDir\tg.cmd"
        $ps1ShimPath = "$shimDir\tg.ps1"
        $bashShimPath = "$shimDir\tg"
        Write-AsciiFile -Path $cmdShimPath -Value $cmdShimContent
        Write-AsciiFile -Path $ps1ShimPath -Value $ps1ShimContent
        Write-BashFile -Path $bashShimPath -Value $bashShimContent
        $installedShimPaths += $cmdShimPath
        $installedShimPaths += $ps1ShimPath
        $installedShimPaths += $bashShimPath
    }

    # Ensure user PATH resolves managed shims before stale Python Scripts entries.
    $userPath = [Environment]::GetEnvironmentVariable("Path", "User")
    $userPathParts = @()
    if ($userPath) {
        $userPathParts = $userPath -split ';' | ForEach-Object { $_.Trim() } | Where-Object { $_ }
    }
    $userPathParts = @($managedPathDirs + ($userPathParts | Where-Object { $managedPathDirs -notcontains $_ }))
    $userPath = ($userPathParts -join ';')
    foreach ($managedPathDir in $managedPathDirs) {
        Write-Host "Ensured $managedPathDir is ahead of stale tg launchers on user PATH."
    }
    [Environment]::SetEnvironmentVariable("Path", $userPath, "User")

    # Ensure current process PATH resolves managed shims immediately.
    $currentPathParts = $env:Path -split ';' | ForEach-Object { $_.Trim() } | Where-Object { $_ }
    $currentPathParts = @($managedPathDirs + ($currentPathParts | Where-Object { $managedPathDirs -notcontains $_ }))
    $env:Path = ($currentPathParts -join ';')

    # Remove unmanaged tensor-grep launchers from effective PATH entries that User PATH cannot outrank.
    $managedPathSet = @{}
    foreach ($managedPath in @($nativeFrontdoorPath, $frontdoorCmdPath, $frontdoorPs1Path, $frontdoorBashPath) + $installedShimPaths) {
        if (Test-Path -LiteralPath $managedPath) {
            $managedPathSet[((Resolve-Path -LiteralPath $managedPath).Path.ToLowerInvariant())] = $true
        }
    }
    $machinePath = [Environment]::GetEnvironmentVariable("Path", "Machine")
    $effectivePathParts = @()
    foreach ($pathText in @($originalProcessPath, $machinePath, $userPath)) {
        if (!$pathText) {
            continue
        }
        foreach ($pathPart in ($pathText -split ';')) {
            $trimmedPathPart = $pathPart.Trim()
            if ($trimmedPathPart -and $effectivePathParts -notcontains $trimmedPathPart) {
                $effectivePathParts += $trimmedPathPart
            }
        }
    }
    Remove-StalePathLauncher `
        -effectivePathParts $effectivePathParts `
        -managedPathSet $managedPathSet

    # 6. Add Alias to both PowerShell 7 and Windows PowerShell profiles.
    $docsPath = [Environment]::GetFolderPath("MyDocuments")
    $profilePaths = @(
        (Join-Path $docsPath "PowerShell\Microsoft.PowerShell_profile.ps1"),
        (Join-Path $docsPath "WindowsPowerShell\Microsoft.PowerShell_profile.ps1")
    )
    $aliasCommand = "function tg { & `"$frontdoorPs1Path`" @args }"
    $aliasPattern = '(?m)^\s*(Set-Alias\s+-Name\s+tg\s+-Value\s+.*|function\s+tg\s*\{.*\})\s*$'
    foreach ($profilePath in $profilePaths) {
        $profileDir = Split-Path -Parent $profilePath
        if (!(Test-Path $profileDir)) {
            New-Item -ItemType Directory -Path $profileDir -Force | Out-Null
        }
        if (!(Test-Path $profilePath)) {
            New-Item -ItemType File -Path $profilePath -Force | Out-Null
        }
        $profileContent = Get-Content $profilePath -Raw -ErrorAction SilentlyContinue
        if ($profileContent -match $aliasPattern) {
            $updatedProfile = [regex]::Replace($profileContent, $aliasPattern, $aliasCommand)
            Set-Content -Path $profilePath -Value $updatedProfile
            Write-Host "Updated existing tg function in profile: $profilePath"
        } else {
            Add-Content -Path $profilePath -Value "`n# Tensor-Grep Function`n$aliasCommand"
            Write-Host "Added tg function to profile: $profilePath"
        }
    }

    # Ensure current session resolves tg to the newly installed front-door immediately.
    Remove-Item Alias:tg -ErrorAction SilentlyContinue
    $global:TensorGrepFrontdoorPs1 = $frontdoorPs1Path
    Set-Item -Path Function:\global:tg -Value { & $global:TensorGrepFrontdoorPs1 @args }
    Write-Host "Current session tg command now points to: $((Get-Command tg).Source)"
    Write-Host "Installed PATH shims:"
    Write-Host "  - $frontdoorCmdPath"
    $installedShimPaths | ForEach-Object { Write-Host "  - $_" }

    Write-Host "=========================================================="
    Write-Host " Installation complete! Try running: tg search `"ERROR`" ."
    & $frontdoorCmdPath --version
    Write-Host "=========================================================="
}
finally {
    if ($stagingInstallDir -and (Test-Path -LiteralPath $stagingInstallDir)) {
        Remove-Item -LiteralPath $stagingInstallDir -Recurse -Force -ErrorAction SilentlyContinue
    }
    if (Test-Path -Path $originalPath) {
        Set-Location -Path $originalPath
        Write-Host "Returned to original directory: $originalPath"
    }
}
