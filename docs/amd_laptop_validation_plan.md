# AMD Laptop Validation Plan

This runbook is for validating the current AMD GPU install path on a real laptop before we claim support beyond best-effort detection.

## Goal

Answer these questions with real machine evidence:

1. Does the installer choose the AMD path correctly?
2. Does the install complete cleanly on that laptop?
3. What runtime/backend state does `tensor-grep` report after install?
4. Does normal search stay correct and stable?
5. Do optional heavier paths behave honestly on that machine?

## Ground Rules

- Run this on the AMD laptop itself, not in a replay worktree.
- Record exact command output.
- Do not infer GPU support from installer text alone.
- Treat CPU fallback as valid behavior if that is what the product actually does.
- Do not update docs/claims until results are captured.

## Prep

Open a fresh PowerShell session on the AMD laptop and capture:

```powershell
Get-Date
$PSVersionTable.PSVersion
systeminfo
Get-WmiObject Win32_VideoController | Select-Object Name, DriverVersion
where.exe python
where.exe uv
where.exe tg
```

If `tg` is already installed, capture the current state first:

```powershell
tg --version
tg doctor --json
```

Save those outputs.

## Clean Install Validation

Run the installer exactly as a user would:

```powershell
irm https://github.com/oimiragieo/tensor-grep/releases/latest/download/install.ps1 | iex
```

Capture:

- whether the installer prints the AMD detection branch
- whether PyTorch install succeeds
- whether the final `tg --version` succeeds
- whether the installer returns cleanly to the original directory

If install fails, stop and save the full output. That is already a valid result.

## Post-Install State

Immediately after install, run:

```powershell
tg --version
Get-Command tg
where.exe tg
tg doctor --json
tg lsp-setup --json
```

Capture:

- resolved `tg` command path
- reported version/build info
- doctor output
- whether managed LSP providers are healthy

## Search Routing Validation

Pick a repo or directory with text files and run:

```powershell
tg search ERROR . --debug
tg search ERROR . --stats
tg search ERROR . --json
```

Capture:

- routing backend
- routing reason
- whether behavior is correct
- whether anything claims GPU routing unexpectedly

## Optional Heavy-Path Checks

Only if the install completed with the heavier stack present, run:

```powershell
tg classify "payment failed for customer 42"
```

If you want a second probe:

```powershell
tg classify "database timeout during checkout"
```

Capture:

- whether the command runs at all
- whether it fails due to missing dependencies
- whether it times out or crashes
- whether any backend message implies AMD-specific acceleration

## Smoke Corpus Check

Run one small correctness probe in a known directory:

```powershell
mkdir $env:TEMP\tg-amd-smoke -Force | Out-Null
Set-Location $env:TEMP\tg-amd-smoke
"ERROR one" | Set-Content app.log
"INFO two" | Set-Content other.log
tg search ERROR . --stats
```

Expected minimum contract:

- command exits successfully
- one matching file
- one total match

## What To Report Back

Bring back:

1. installer transcript
2. `tg --version`
3. `tg doctor --json`
4. `tg lsp-setup --json`
5. one `--debug` search output
6. one `--stats` search output
7. any `classify` output if you ran it
8. GPU model and driver version

## Decision Rules

Use these rules when we interpret the results:

- If install fails in the AMD branch, AMD install support is currently broken.
- If install succeeds but runtime falls back cleanly to CPU, AMD install support exists but AMD acceleration should not be claimed.
- If heavier commands fail due to dependency/runtime issues, those paths are not production-ready on AMD yet.
- If all probes are clean and repeatable, we can tighten docs/tests around the AMD contract.

## Likely Follow-Up Actions

Depending on results, the next patch will be one of:

- fix the AMD installer branch
- narrow docs so AMD is explicitly best-effort
- add AMD-specific doctor output
- add validator-backed install/docs coverage
- add a measured AMD benchmark note if the machine shows a real win
