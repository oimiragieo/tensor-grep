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

function Write-Utf8NoBomFile {
    param(
        [Parameter(Mandatory = $true)][string]$Path,
        [Parameter(Mandatory = $true)][string]$Value
    )

    [System.IO.File]::WriteAllText($Path, $Value, [System.Text.UTF8Encoding]::new($false))
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

function Resolve-NativeFrontdoorAssetCandidates {
    param(
        [Parameter(Mandatory = $true)][string]$hardwareFlag
    )

    $requestedFlavor = $env:TENSOR_GREP_NATIVE_FRONTDOOR_FLAVOR
    if (!$requestedFlavor) {
        $requestedFlavor = $env:TG_NATIVE_FRONTDOOR_REQUESTED_FLAVOR
    }
    $requestedFlavor = if ($requestedFlavor) { $requestedFlavor.ToLowerInvariant() } else { "cpu" }
    $candidates = @()
    if ($requestedFlavor -eq "nvidia" -or $requestedFlavor -eq "cuda") {
        $candidates += "tg-windows-amd64-nvidia.exe"
    } elseif ($requestedFlavor -ne "cpu") {
        Write-Warning "Unknown native front-door asset flavor '$requestedFlavor'; using CPU asset."
    }
    $candidates += "tg-windows-amd64-cpu.exe"
    return $candidates | Select-Object -Unique
}

function Get-ExpectedAssetSha256 {
    # Look up the published sha256 for an asset in a CHECKSUMS.txt ("<sha256>  <asset>").
    param(
        [Parameter(Mandatory = $true)][AllowEmptyString()][string]$checksumsPath,
        [Parameter(Mandatory = $true)][string]$assetName
    )
    if ([string]::IsNullOrEmpty($checksumsPath) -or -not (Test-Path -LiteralPath $checksumsPath)) {
        return ""
    }
    foreach ($line in Get-Content -LiteralPath $checksumsPath) {
        $parts = ($line.Trim() -split '\s+')
        if ($parts.Count -ge 2 -and $parts[-1] -eq $assetName) {
            return $parts[0].ToLower()
        }
    }
    return ""
}

function Install-NativeFrontdoorBinary {
    param(
        [Parameter(Mandatory = $true)][string]$frontdoorDir,
        [Parameter(Mandatory = $true)][string]$installedVersion,
        [Parameter(Mandatory = $true)][string]$installChannel,
        [Parameter(Mandatory = $true)][string]$hardwareFlag
    )

    $nativeFrontdoorPath = Join-Path $frontdoorDir "tg.exe"
    $script:TensorGrepNativeFrontdoorFlavor = "cpu"
    $script:TensorGrepNativeFrontdoorAssetName = ""
    if ($installChannel -eq "main") {
        Write-Host "      Main-channel install: using Python front door until release-native assets exist."
        return $nativeFrontdoorPath
    }

    # Fetch the published CHECKSUMS.txt so every downloaded asset is verified against a
    # signed digest BEFORE it is made executable or run. Without this, a compromised
    # release/account or a TLS-intercepting proxy could persist arbitrary code as the
    # default `tg` (audit S4). Failure to fetch it means the native front door is skipped
    # (fail closed to the Python wrapper), never executed unverified.
    $checksumsPath = Join-Path $frontdoorDir "CHECKSUMS.txt"
    $checksumsUrl = "https://github.com/oimiragieo/tensor-grep/releases/download/v$installedVersion/CHECKSUMS.txt"
    try {
        Invoke-WebRequest -Uri $checksumsUrl -OutFile $checksumsPath
    } catch {
        $checksumsPath = ""
        Write-Warning "Could not fetch CHECKSUMS.txt; native front door will be skipped (Python fallback). Error: $_"
    }

    $nativeAssetCandidates = Resolve-NativeFrontdoorAssetCandidates -hardwareFlag $hardwareFlag
    foreach ($nativeAssetName in $nativeAssetCandidates) {
        $nativeDownloadUrl = "https://github.com/oimiragieo/tensor-grep/releases/download/v$installedVersion/$nativeAssetName"
        $nativeTempPath = "$nativeFrontdoorPath.download"
        $nativeFlavor = if ($nativeAssetName -match "nvidia") { "nvidia" } else { "cpu" }
        Write-Host "      Downloading native tg front door asset flavor ${nativeFlavor}: $nativeAssetName"
        try {
            Invoke-WebRequest -Uri $nativeDownloadUrl -OutFile $nativeTempPath
            $expectedSha = Get-ExpectedAssetSha256 -checksumsPath $checksumsPath -assetName $nativeAssetName
            $actualSha = (Get-FileHash -LiteralPath $nativeTempPath -Algorithm SHA256).Hash.ToLower()
            if ([string]::IsNullOrEmpty($expectedSha)) {
                throw "no published checksum for $nativeAssetName; refusing to trust the download"
            }
            if ($expectedSha -ne $actualSha) {
                throw "checksum MISMATCH for $nativeAssetName (expected $expectedSha, got $actualSha)"
            }
            Move-Item -LiteralPath $nativeTempPath -Destination $nativeFrontdoorPath -Force
            & $nativeFrontdoorPath --version | Out-Host
            if ($LASTEXITCODE -ne 0) {
                throw "native tg front door smoke test failed"
            }
            $script:TensorGrepNativeFrontdoorFlavor = $nativeFlavor
            $script:TensorGrepNativeFrontdoorAssetName = $nativeAssetName
            Write-Host "      Native tg front door installed: $nativeFrontdoorPath (asset flavor: $nativeFlavor)"
            return $nativeFrontdoorPath
        } catch {
            Remove-Item -LiteralPath $nativeTempPath -Force -ErrorAction SilentlyContinue
            Remove-Item -LiteralPath $nativeFrontdoorPath -Force -ErrorAction SilentlyContinue
            if ($nativeFlavor -eq "nvidia" -and ($nativeAssetCandidates -contains "tg-windows-amd64-cpu.exe")) {
                Write-Warning (
                    "Falling back to CPU native tg front-door asset after NVIDIA asset failed. " +
                    "Expected asset: $nativeDownloadUrl. Error: $_"
                )
                continue
            }
            Write-Warning (
                "Native tg front-door download failed; falling back to Python wrapper. " +
                "Expected asset: $nativeDownloadUrl. Error: $_"
            )
        }
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
        $packageInfo = & $pythonExe -m pip show -f tensor-grep 2>$null
    } catch {
        return $false
    }
    if (!$packageInfo) {
        return $false
    }
    $packageLocation = $null
    $candidateOwnedByPackage = $false
    try {
        $resolvedCandidate = (Resolve-Path -LiteralPath $candidatePath -ErrorAction Stop).Path
    } catch {
        return $false
    }
    foreach ($packageLine in $packageInfo) {
        if ($packageLine -match '^Location:\s*(.+)$') {
            $packageLocation = $Matches[1].Trim()
            continue
        }
        if (!$packageLocation) {
            continue
        }
        $packageFile = $packageLine.Trim()
        if (!$packageFile -or $packageFile -eq "Files:") {
            continue
        }
        try {
            $resolvedPackageFile = (Resolve-Path -LiteralPath (Join-Path $packageLocation $packageFile) -ErrorAction Stop).Path
        } catch {
            continue
        }
        if ($resolvedPackageFile.ToLowerInvariant() -eq $resolvedCandidate.ToLowerInvariant()) {
            $candidateOwnedByPackage = $true
            break
        }
    }
    if (!$candidateOwnedByPackage) {
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

function Test-TensorGrepLauncher {
    param(
        [Parameter(Mandatory = $true)][string]$CandidatePath,
        [Parameter(Mandatory = $true)][string]$VersionLine
    )

    if ($VersionLine.StartsWith("tensor-grep ")) {
        return $true
    }
    if (!$VersionLine.StartsWith("tg ")) {
        return $false
    }
    try {
        $helpText = (& $CandidatePath --help 2>$null | Select-Object -First 8) -join "`n"
        return $helpText -match "tensor-grep"
    } catch {
        return $false
    }
}

function Remove-StaleShimLauncherWithRetry {
    param(
        [Parameter(Mandatory = $true)][string]$Path
    )

    for ($attempt = 1; $attempt -le 5; $attempt++) {
        try {
            Remove-Item -LiteralPath $Path -Force -ErrorAction Stop
            return $true
        } catch {
            if ($attempt -eq 5) {
                Write-Warning (
                    "Unable to remove stale tg launcher shadowing managed shim after retry: " +
                    "$Path. Error: $_"
                )
                return $false
            }
            Start-Sleep -Milliseconds 250
        }
    }
    return $false
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
            $versionProbeFailed = $false
            try {
                $candidateVersion = (& $candidatePath --version 2>$null | Select-Object -First 1)
            } catch {
                $versionProbeFailed = $true
            }
            if ($versionProbeFailed -or !$candidateVersion) {
                if (Remove-StalePythonPackageLauncher `
                        -candidatePath $candidatePath `
                        -candidateVersion "<unreadable --version>") {
                    continue
                }
                continue
            }
            if (!(Test-TensorGrepLauncher -CandidatePath $candidatePath -VersionLine $candidateVersion)) {
                continue
            }
            if (Remove-StalePythonPackageLauncher `
                    -candidatePath $candidatePath `
                    -candidateVersion $candidateVersion) {
                continue
            }
            $candidateDir = Split-Path -Parent $candidatePath
            if ((Split-Path -Leaf $candidateDir) -eq "Scripts") {
                Write-Warning (
                    "WARNING: tensor-grep-looking Python Scripts tg launcher remains ahead of " +
                    "managed shim because package ownership could not be verified: " +
                    "$candidatePath ($candidateVersion). Remove it or move the managed shim " +
                    "directories earlier in Machine PATH."
                )
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

$uvExtractDir = $null

try {
    # 1. Install or locate uv
    # Pin uv to an exact version and verify its SHA-256 before execution. We download the release
    # zip directly from GitHub and check it against a committed checksum table
    # (scripts/uv_checksums.json) instead of running the astral.sh remote installer, which lacks
    # binary checksum verification on Windows (see https://github.com/astral-sh/uv/issues/13074).
    # Bump both $uvVersion and the committed checksums together when upgrading.
    $uvVersion = "0.11.25"
    # SHA-256 of uv-<triple>-pc-windows-msvc.zip — source of truth: scripts/uv_checksums.json.
    $uvKnownSha256 = @{
        "x86_64"  = "15bfd1423b7eaa7aae949922d4712ebaac2bb44a81af64ab59bbe007090cb0d0"
        "aarch64" = "40d65c29c4d97db6a0993df665d3727700bb799b3618992ce9a4dc533c6d1a31"
    }
    $uvPath = "uv"
    if (!(Get-Command "uv" -ErrorAction SilentlyContinue)) {
        Write-Host "[1/4] Downloading uv package manager (pinned $uvVersion)..."
        # Detect CPU architecture to pick the correct release artifact.
        $osArch = [System.Runtime.InteropServices.RuntimeInformation]::OSArchitecture
        $uvArch = switch ($osArch) {
            "Arm64" { "aarch64" }
            default { "x86_64" }
        }
        $uvTriple = "uv-$uvArch-pc-windows-msvc"
        $uvZipName = "$uvTriple.zip"
        $uvZipUrl = "https://github.com/astral-sh/uv/releases/download/$uvVersion/$uvZipName"
        $uvExpectedSha = $uvKnownSha256[$uvArch]
        if (!$uvExpectedSha) {
            throw "No committed SHA-256 for uv arch '$uvArch' (version $uvVersion). Update scripts/uv_checksums.json and the embedded table in install.ps1."
        }
        $uvZipPath = Join-Path $env:TEMP "uv_${uvVersion}_${uvArch}.zip"
        $uvExtractDir = Join-Path $env:TEMP "uv_${uvVersion}_${uvArch}"
        try {
            Invoke-WebRequest -Uri $uvZipUrl -OutFile $uvZipPath
            $actualSha = (Get-FileHash -LiteralPath $uvZipPath -Algorithm SHA256).Hash.ToLower()
            if ($actualSha -ne $uvExpectedSha) {
                throw "uv zip checksum MISMATCH for $uvZipName (expected $uvExpectedSha, got $actualSha). Aborting installation."
            }
            Write-Host "      uv zip checksum verified OK ($uvZipName)."
            Remove-Item -LiteralPath $uvExtractDir -Recurse -Force -ErrorAction SilentlyContinue
            Expand-Archive -LiteralPath $uvZipPath -DestinationPath $uvExtractDir -Force
        } finally {
            Remove-Item -LiteralPath $uvZipPath -Force -ErrorAction SilentlyContinue
        }
        # The zip extracts to a subdirectory named after the triple; fall back to a recursive search.
        $uvExePath = Join-Path $uvExtractDir "$uvTriple\uv.exe"
        if (!(Test-Path -LiteralPath $uvExePath)) {
            $uvExePath = Get-ChildItem -LiteralPath $uvExtractDir -Filter "uv.exe" -Recurse `
                -ErrorAction SilentlyContinue | Select-Object -First 1 -ExpandProperty FullName
        }
        if (!$uvExePath -or !(Test-Path -LiteralPath $uvExePath)) {
            throw "uv.exe not found in extracted zip at $uvExtractDir."
        }
        $uvPath = $uvExePath
    } else {
        Write-Host "[1/4] Found existing uv installation."
    }

    # 2. Detect GPU Configuration
    Write-Host "[2/4] Detecting hardware for optimal routing..."
    $gpuQuery = Get-WmiObject Win32_VideoController | Select-Object Name
    $hardwareFlag = "cpu"
    $indexUrl = ""

    if ($gpuQuery.Name -match "NVIDIA") {
        Write-Host "      Detected NVIDIA GPU. Configuring for CUDA 12.8."
        $hardwareFlag = "nvidia"
        $indexArg = "--index-url"
        $indexUrl = "https://download.pytorch.org/whl/cu128"
    } elseif ($gpuQuery.Name -match "AMD" -or $gpuQuery.Name -match "Radeon") {
        Write-Host "      Detected AMD GPU. Windows ROCm support is selected/experimental; configuring CPU fallback."
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
        -installChannel $installChannel `
        -hardwareFlag $hardwareFlag
    $nativeFrontdoorFlavor = if ($script:TensorGrepNativeFrontdoorFlavor) {
        $script:TensorGrepNativeFrontdoorFlavor
    } else {
        "cpu"
    }
    $nativeFrontdoorRequestedFlavor = $env:TENSOR_GREP_NATIVE_FRONTDOOR_FLAVOR
    if (!$nativeFrontdoorRequestedFlavor) {
        $nativeFrontdoorRequestedFlavor = $env:TG_NATIVE_FRONTDOOR_REQUESTED_FLAVOR
    }
    $nativeFrontdoorRequestedFlavor = if ($nativeFrontdoorRequestedFlavor) {
        $nativeFrontdoorRequestedFlavor.ToLowerInvariant()
    } else {
        "cpu"
    }
    if ($nativeFrontdoorRequestedFlavor -eq "cuda") {
        $nativeFrontdoorRequestedFlavor = "nvidia"
    }
    if ($nativeFrontdoorRequestedFlavor -notin @("nvidia", "cpu")) {
        $nativeFrontdoorRequestedFlavor = "cpu"
    }
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
    $stagingNativeMetadataPath = Join-Path $stagingFrontdoorDir "tg-native-metadata.json"
    if ((Test-Path -LiteralPath $stagingNativeFrontdoorPath) -and ($stagingNativeFrontdoorPath -ne (Join-Path $stagingFrontdoorDir "tg.exe"))) {
        Move-Item -LiteralPath $stagingNativeFrontdoorPath -Destination (Join-Path $stagingFrontdoorDir "tg.exe") -Force
    }
    if (Test-Path -LiteralPath (Join-Path $stagingFrontdoorDir "tg.exe")) {
        $nativeMetadata = @{
            artifact = "tensor_grep_native_frontdoor_metadata"
            version = $installedVersion
            asset_flavor = $nativeFrontdoorFlavor
            requested_asset_flavor = $nativeFrontdoorRequestedFlavor
            asset_name = $script:TensorGrepNativeFrontdoorAssetName
        } | ConvertTo-Json -Depth 3
        Write-Utf8NoBomFile -Path $stagingNativeMetadataPath -Value ($nativeMetadata + "`n")
    }
    $frontdoorCmdContent = (
        "@echo off`r`n" +
        "setlocal`r`n" +
        "set PYTHONUTF8=1`r`n" +
        "set PYTHONIOENCODING=utf-8`r`n" +
        "set TG_SIDECAR_PYTHON=$installDir\.venv\Scripts\python.exe`r`n" +
        "set TG_NATIVE_TG_BINARY=$nativeFrontdoorPath`r`n" +
        "set TG_NATIVE_FRONTDOOR_REQUESTED_FLAVOR=$nativeFrontdoorRequestedFlavor`r`n" +
        "set TG_NATIVE_FRONTDOOR_FLAVOR=$nativeFrontdoorFlavor`r`n" +
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
`$env:TG_NATIVE_FRONTDOOR_REQUESTED_FLAVOR = "$nativeFrontdoorRequestedFlavor"
`$env:TG_NATIVE_FRONTDOOR_FLAVOR = "$nativeFrontdoorFlavor"
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
export TG_NATIVE_FRONTDOOR_REQUESTED_FLAVOR="$nativeFrontdoorRequestedFlavor"
export TG_NATIVE_FRONTDOOR_FLAVOR="$nativeFrontdoorFlavor"
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
        "set TG_NATIVE_FRONTDOOR_REQUESTED_FLAVOR=$nativeFrontdoorRequestedFlavor`r`n" +
        "set TG_NATIVE_FRONTDOOR_FLAVOR=$nativeFrontdoorFlavor`r`n" +
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
        $foreignExeInShimDir = $false
        foreach ($staleShimName in @("tg.com", "tg.exe", "tg.bat", "tg.ps1", "tg")) {
            $staleShimPath = Join-Path $shimDir $staleShimName
            if (Test-Path -LiteralPath $staleShimPath) {
                if ($staleShimName -eq "tg.exe") {
                    $staleVersion = ""
                    try {
                        $staleVersion = (& $staleShimPath --version 2>$null | Select-Object -First 1)
                    } catch {
                        $staleVersion = ""
                    }
                    if (!(Test-TensorGrepLauncher -CandidatePath $staleShimPath -VersionLine $staleVersion)) {
                        $foreignExeInShimDir = $true
                        Write-Warning "Skipping foreign tg.exe in tensor-grep shim dir: $staleShimPath"
                        continue
                    }
                }
                if (Remove-StaleShimLauncherWithRetry -Path $staleShimPath) {
                    Write-Host "Removed stale tg launcher shadowing managed shim: $staleShimPath"
                }
            }
        }
        $cmdShimPath = "$shimDir\tg.cmd"
        $exeShimPath = "$shimDir\tg.exe"
        $exeShimMarkerPath = "$shimDir\tg.exe.tensor-grep-bridge"
        $ps1ShimPath = "$shimDir\tg.ps1"
        $bashShimPath = "$shimDir\tg"
        Write-AsciiFile -Path $cmdShimPath -Value $cmdShimContent
        if ((Test-Path -LiteralPath $nativeFrontdoorPath) -and !$foreignExeInShimDir) {
            try {
                Copy-Item -LiteralPath $nativeFrontdoorPath -Destination $exeShimPath -Force -ErrorAction Stop
                Write-AsciiFile -Path $exeShimMarkerPath -Value "tensor-grep managed tg.exe bridge`r`n"
                Write-Host "Installed Python subprocess tg.exe bridge: $exeShimPath"
                $installedShimPaths += $exeShimPath
            } catch {
                Write-Warning "Python subprocess tg.exe bridge could not be refreshed: $exeShimPath. Error: $_"
            }
        } elseif ($foreignExeInShimDir) {
            Write-Warning "Python subprocess tg.exe bridge was not installed because a foreign tg.exe already exists: $exeShimPath"
        }
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
    if ($uvExtractDir -and (Test-Path -LiteralPath $uvExtractDir)) {
        Remove-Item -LiteralPath $uvExtractDir -Recurse -Force -ErrorAction SilentlyContinue
    }
    if ($stagingInstallDir -and (Test-Path -LiteralPath $stagingInstallDir)) {
        Remove-Item -LiteralPath $stagingInstallDir -Recurse -Force -ErrorAction SilentlyContinue
    }
    if (Test-Path -Path $originalPath) {
        Set-Location -Path $originalPath
        Write-Host "Returned to original directory: $originalPath"
    }
}
