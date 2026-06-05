<#
.SYNOPSIS
    Stage argv-safe Windows tg.cmd launchers for CI shell-probe dogfood.
#>

param(
    [Parameter(Mandatory = $true)][string]$RepoRoot,
    [string]$VenvDir = ""
)

$ErrorActionPreference = "Stop"

if (-not $VenvDir) {
    $VenvDir = Join-Path $RepoRoot ".venv"
}

$pythonExe = Join-Path $VenvDir "Scripts\python.exe"
if (-not (Test-Path -LiteralPath $pythonExe)) {
    throw "Expected managed venv python at $pythonExe"
}

$launcherDir = Join-Path $RepoRoot "artifacts\windows-ci-launchers"
New-Item -ItemType Directory -Path $launcherDir -Force | Out-Null

$bridgePath = Join-Path $launcherDir "tg-cmd-bridge.py"
$cmdPath = Join-Path $launcherDir "tg.cmd"

$bridgeContent = @'
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

$cmdContent = (
    "@echo off`r`n" +
    "setlocal`r`n" +
    "set PYTHONUTF8=1`r`n" +
    "set PYTHONIOENCODING=utf-8`r`n" +
    "set TG_SIDECAR_PYTHON=$pythonExe`r`n" +
    "set /a TG_CMD_SHIM_ARGC=0`r`n" +
    ":tg_arg_loop`r`n" +
    'if "%~1"=="" goto tg_arg_done' + "`r`n" +
    "set /a TG_CMD_SHIM_ARGC+=1`r`n" +
    'set "TG_CMD_SHIM_ARG_%TG_CMD_SHIM_ARGC%=%~1"' + "`r`n" +
    "shift`r`n" +
    "goto tg_arg_loop`r`n" +
    ":tg_arg_done`r`n" +
    "`"$pythonExe`" -X utf8 `"$bridgePath`"`r`n"
)

[System.IO.File]::WriteAllText($bridgePath, $bridgeContent, [System.Text.Encoding]::ASCII)
[System.IO.File]::WriteAllText($cmdPath, $cmdContent, [System.Text.Encoding]::ASCII)

$venvScripts = Join-Path $VenvDir "Scripts"
if ($env:GITHUB_PATH) {
    Add-Content -Path $env:GITHUB_PATH -Value $launcherDir
    Add-Content -Path $env:GITHUB_PATH -Value $venvScripts
} else {
    $env:Path = "$launcherDir;$venvScripts;$env:Path"
}

Write-Host "Staged Windows CI launchers in $launcherDir"
