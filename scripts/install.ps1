<#
.SYNOPSIS
    Installs Tensor-Grep with automatic GPU detection and Python 3.12 isolation via uv.
#>

$ErrorActionPreference = "Stop"

Write-Host "=========================================================="
Write-Host "           TENSOR-GREP WINDOWS INSTALLER                  "
Write-Host "=========================================================="

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
    $indexUrl = "--index-url https://download.pytorch.org/whl/cu124"
} elseif ($gpuQuery.Name -match "AMD" -or $gpuQuery.Name -match "Radeon") {
    Write-Host "      Detected AMD GPU. Configuring for ROCm."
    $hardwareFlag = "amd"
    $indexUrl = "--index-url https://download.pytorch.org/whl/rocm6.0"
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
if ($hardwareFlag -ne "cpu") {
    # Install PyTorch with specific index first to ensure correct wheel resolution
    & $uvPath pip install torch torchvision torchaudio $indexUrl --python "$installDir\.venv\Scripts\python.exe"
    & $uvPath pip install "tensor-grep[gpu-win,nlp,ast]" --python "$installDir\.venv\Scripts\python.exe"
} else {
    & $uvPath pip install "tensor-grep[ast,nlp]" --python "$installDir\.venv\Scripts\python.exe"
}

# 5. Add Alias to User Profile
$profilePath = $PROFILE
if (!(Test-Path $profilePath)) {
    New-Item -ItemType File -Path $profilePath -Force | Out-Null
}

$aliasCommand = "Set-Alias -Name tg -Value `"$installDir\.venv\Scripts\tg.exe`""
$profileContent = Get-Content $profilePath -Raw -ErrorAction SilentlyContinue

if ($profileContent -notmatch "Set-Alias -Name tg") {
    Add-Content -Path $profilePath -Value "`n# Tensor-Grep Alias`n$aliasCommand"
    Write-Host "`nSuccessfully installed tensor-grep!"
    Write-Host "Alias 'tg' added to your PowerShell profile."
    Write-Host "Please restart your terminal or run: . \$PROFILE"
} else {
    Write-Host "`nSuccessfully installed tensor-grep! Alias 'tg' already exists."
}

Write-Host "=========================================================="
Write-Host " Installation complete! Try running: tg search `"ERROR`" ."
Write-Host "=========================================================="
