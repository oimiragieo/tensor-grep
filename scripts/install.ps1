<#
.SYNOPSIS
    Installs Tensor-Grep with automatic GPU detection and Python 3.12 isolation via uv.
#>

$ErrorActionPreference = "Stop"
$originalPath = (Get-Location).Path
$installChannel = if ($env:TENSOR_GREP_CHANNEL) { $env:TENSOR_GREP_CHANNEL } else { "stable" }
$requestedVersion = $env:TENSOR_GREP_VERSION

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
        } else {
            "$pkgSpec[gpu-win,nlp,ast]"
        }
        & $uvPath pip install $pkgRequirement --python "$installDir\.venv\Scripts\python.exe"
    } else {
        $pkgRequirement = if ($installChannel -eq "main") {
            "tensor-grep[ast,nlp] @ $pkgSpec"
        } else {
            "$pkgSpec[ast,nlp]"
        }
        & $uvPath pip install $pkgRequirement --python "$installDir\.venv\Scripts\python.exe"
    }

    # Ensure AST runtime grammars are present explicitly across environments.
    & $uvPath pip install tree-sitter tree-sitter-python tree-sitter-javascript --python "$installDir\.venv\Scripts\python.exe"

    Write-Host "      Installing managed external LSP providers..."
    & "$installDir\.venv\Scripts\tg.exe" lsp-setup --json | Out-Null

    # 5. Install PATH shims for profile-independent command resolution.
    $shimDirs = @(
        "$env:USERPROFILE\.local\bin",
        "$env:USERPROFILE\bin"
    )
    $cmdShimContent = "@echo off`r`n`"$installDir\.venv\Scripts\tg.exe`" %*`r`n"
    $installedShimPaths = @()
    foreach ($shimDir in $shimDirs) {
        if (!(Test-Path $shimDir)) {
            New-Item -ItemType Directory -Path $shimDir -Force | Out-Null
        }
        $cmdShimPath = "$shimDir\tg.cmd"
        Set-Content -Path $cmdShimPath -Value $cmdShimContent -Encoding ascii
        $installedShimPaths += $cmdShimPath
    }

    # Ensure user PATH includes shim directories for new terminals.
    $userPath = [Environment]::GetEnvironmentVariable("Path", "User")
    $userPathParts = @()
    if ($userPath) {
        $userPathParts = $userPath -split ';' | ForEach-Object { $_.Trim() } | Where-Object { $_ }
    }
    foreach ($shimDir in $shimDirs) {
        if ($userPathParts -notcontains $shimDir) {
            $userPath = if ($userPath) { "$userPath;$shimDir" } else { $shimDir }
            $userPathParts += $shimDir
            Write-Host "Added $shimDir to user PATH."
        }
    }
    [Environment]::SetEnvironmentVariable("Path", $userPath, "User")

    # Ensure current process PATH includes shim directories immediately.
    $currentPathParts = $env:Path -split ';' | ForEach-Object { $_.Trim() } | Where-Object { $_ }
    foreach ($shimDir in $shimDirs) {
        if ($currentPathParts -notcontains $shimDir) {
            $env:Path = "$env:Path;$shimDir"
            $currentPathParts += $shimDir
        }
    }

    # 6. Add Alias to both PowerShell 7 and Windows PowerShell profiles.
    $docsPath = [Environment]::GetFolderPath("MyDocuments")
    $profilePaths = @(
        (Join-Path $docsPath "PowerShell\Microsoft.PowerShell_profile.ps1"),
        (Join-Path $docsPath "WindowsPowerShell\Microsoft.PowerShell_profile.ps1")
    )
    $aliasCommand = "Set-Alias -Name tg -Value `"$installDir\.venv\Scripts\tg.exe`" -Scope Global"
    $aliasPattern = '(?m)^\s*Set-Alias\s+-Name\s+tg\s+-Value\s+.*$'
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
            Write-Host "Updated existing tg alias in profile: $profilePath"
        } else {
            Add-Content -Path $profilePath -Value "`n# Tensor-Grep Alias`n$aliasCommand"
            Write-Host "Added tg alias to profile: $profilePath"
        }
    }

    # Ensure current session resolves tg to the newly installed binary immediately.
    Set-Alias -Name tg -Value "$installDir\.venv\Scripts\tg.exe" -Scope Global -Force
    Write-Host "Current session alias now points to: $((Get-Command tg).Source)"
    Write-Host "Installed PATH shims:"
    $installedShimPaths | ForEach-Object { Write-Host "  - $_" }

    Write-Host "=========================================================="
    Write-Host " Installation complete! Try running: tg search `"ERROR`" ."
    & "$installDir\.venv\Scripts\tg.exe" --version
    Write-Host "=========================================================="
}
finally {
    if (Test-Path -Path $originalPath) {
        Set-Location -Path $originalPath
        Write-Host "Returned to original directory: $originalPath"
    }
}
