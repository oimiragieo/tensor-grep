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

function Remove-StalePathLauncher {
    param(
        [Parameter(Mandatory = $true)][string[]]$effectivePathParts,
        [Parameter(Mandatory = $true)][hashtable]$managedPathSet
    )

    foreach ($pathPart in $effectivePathParts) {
        if (!$pathPart -or !(Test-Path -LiteralPath $pathPart)) {
            continue
        }
        foreach ($launcherName in @("tg.com", "tg.exe", "tg.bat", "tg.cmd", "tg.ps1", "tg")) {
            $candidatePath = Join-Path $pathPart $launcherName
            if (!(Test-Path -LiteralPath $candidatePath)) {
                continue
            }
            $resolvedCandidate = (Resolve-Path -LiteralPath $candidatePath).Path
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
                Remove-Item -LiteralPath $candidatePath -Force
                Write-Host "Removed unmanaged tg launcher from PATH: $candidatePath ($candidateVersion)"
            } catch {
                Write-Warning (
                    "WARNING: stale tg launcher remains ahead of managed shim: " +
                    "$candidatePath ($candidateVersion). Remove it or move the managed shim " +
                    "directories earlier in Machine PATH."
                )
            }
        }
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
    if (Test-Path $installDir) {
        Remove-Item -Recurse -Force $installDir
    }
    New-Item -ItemType Directory -Path $installDir | Out-Null

    Write-Host "[3/4] Building isolated Python 3.12 environment..."
    Set-Location $installDir
    & $uvPath venv --python 3.12 .venv

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
        & $uvPath pip install torch torchvision torchaudio $indexArg $indexUrl --python "$installDir\.venv\Scripts\python.exe"
        $pkgRequirement = if ($installChannel -eq "main") {
            "tensor-grep[gpu-win,nlp,ast] @ $pkgSpec"
        } elseif ($requestedVersion) {
            "tensor-grep[gpu-win,nlp,ast]==$requestedVersion"
        } else {
            "tensor-grep[gpu-win,nlp,ast]"
        }
        & $uvPath pip install $pkgRequirement --python "$installDir\.venv\Scripts\python.exe"
    } else {
        $pkgRequirement = if ($installChannel -eq "main") {
            "tensor-grep[ast,nlp] @ $pkgSpec"
        } elseif ($requestedVersion) {
            "tensor-grep[ast,nlp]==$requestedVersion"
        } else {
            "tensor-grep[ast,nlp]"
        }
        & $uvPath pip install $pkgRequirement --python "$installDir\.venv\Scripts\python.exe"
    }

    # Ensure AST runtime grammars are present explicitly across environments.
    & $uvPath pip install tree-sitter tree-sitter-python tree-sitter-javascript --python "$installDir\.venv\Scripts\python.exe"

    # 5. Install front-door wrapper and PATH shims for profile-independent command resolution.
    $frontdoorDir = Join-Path $installDir "bin"
    if (!(Test-Path $frontdoorDir)) {
        New-Item -ItemType Directory -Path (Join-Path $installDir "bin") -Force | Out-Null
    }
    $frontdoorCmdPath = Join-Path $frontdoorDir "tg.cmd"
    $frontdoorPs1Path = Join-Path $frontdoorDir "tg.ps1"
    $frontdoorBashPath = Join-Path $frontdoorDir "tg"
    $msysInstallDir = Convert-ToMsysPath $installDir
    $frontdoorCmdContent = (
        "@echo off`r`n" +
        "set PYTHONUTF8=1`r`n" +
        "set PYTHONIOENCODING=utf-8`r`n" +
        "`"$installDir\.venv\Scripts\python.exe`" -X utf8 -m tensor_grep %*`r`n"
    )
    $frontdoorPs1Content = @"
`$env:PYTHONUTF8 = "1"
`$env:PYTHONIOENCODING = "utf-8"
& "$installDir\.venv\Scripts\python.exe" -X utf8 -m tensor_grep @args
exit `$LASTEXITCODE
"@
    $frontdoorBashContent = @"
#!/usr/bin/env bash
export PYTHONUTF8=1
export PYTHONIOENCODING=utf-8
"$msysInstallDir/.venv/Scripts/python.exe" -X utf8 -m tensor_grep "$@"
"@
    Set-Content -Path $frontdoorCmdPath -Value $frontdoorCmdContent -Encoding ascii
    Set-Content -Path $frontdoorPs1Path -Value $frontdoorPs1Content -Encoding ascii
    Set-Content -Path $frontdoorBashPath -Value $frontdoorBashContent -Encoding ascii

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
    $cmdShimContent = (
        "@echo off`r`n" +
        "set PYTHONUTF8=1`r`n" +
        "set PYTHONIOENCODING=utf-8`r`n" +
        "`"$frontdoorCmdPath`" %*`r`n"
    )
    $ps1ShimContent = @"
& "$frontdoorPs1Path" @args
exit `$LASTEXITCODE
"@
    $msysFrontdoorPath = Convert-ToMsysPath $frontdoorBashPath
    $bashShimContent = @"
#!/usr/bin/env bash
"$msysFrontdoorPath" "$@"
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
        Set-Content -Path $cmdShimPath -Value $cmdShimContent -Encoding ascii
        Set-Content -Path $ps1ShimPath -Value $ps1ShimContent -Encoding ascii
        Set-Content -Path $bashShimPath -Value $bashShimContent -Encoding ascii
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
    $userPathParts = @($shimDirs + ($userPathParts | Where-Object { $shimDirs -notcontains $_ }))
    $userPath = ($userPathParts -join ';')
    foreach ($shimDir in $shimDirs) {
        Write-Host "Ensured $shimDir is ahead of stale tg launchers on user PATH."
    }
    [Environment]::SetEnvironmentVariable("Path", $userPath, "User")

    # Ensure current process PATH resolves managed shims immediately.
    $currentPathParts = $env:Path -split ';' | ForEach-Object { $_.Trim() } | Where-Object { $_ }
    $currentPathParts = @($shimDirs + ($currentPathParts | Where-Object { $shimDirs -notcontains $_ }))
    $env:Path = ($currentPathParts -join ';')

    # Remove unmanaged tensor-grep launchers from effective PATH entries that User PATH cannot outrank.
    $managedPathSet = @{}
    foreach ($managedPath in @($frontdoorCmdPath, $frontdoorPs1Path, $frontdoorBashPath) + $installedShimPaths) {
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
    if (Test-Path -Path $originalPath) {
        Set-Location -Path $originalPath
        Write-Host "Returned to original directory: $originalPath"
    }
}
