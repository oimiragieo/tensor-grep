# Installation

`tensor-grep` can be installed through managed binaries, Python packaging, package managers, or the automated install scripts. The right path depends on whether you want self-update behavior, locked-down workstation rollout, or source-level Python integration.

## Recommended Channel by Use Case

- **Individual developers who want `tg update` / `tg upgrade`:** use the install scripts or `pip` / `uv`.
- **Managed workstation rollout:** use GitHub release binaries, Homebrew, or Winget.
- **Node-centric invocation:** use `npx`.
- **Experimental features:** review [docs/EXPERIMENTAL.md](EXPERIMENTAL.md) instead of assuming hidden commands are stable/public.

## Option 1: Install Scripts (Recommended)

The install scripts create an isolated environment, print `tg --version` at the end, and keep Python-level dependencies away from your system interpreter.

Stable script installs prefer the matching release-native CPU binary as the public `tg` front door:

- Windows downloads `tg-windows-amd64-cpu.exe` into `~/.tensor-grep/bin/tg.exe`.
- Linux x64 downloads `tg-linux-amd64-cpu` into `~/.tensor-grep/bin/tg-native`.
- macOS x64 downloads `tg-macos-amd64-cpu` into `~/.tensor-grep/bin/tg-native`.

The isolated Python environment remains installed and is exposed to the native front door with `TG_SIDECAR_PYTHON`; `TG_NATIVE_TG_BINARY` points Python-backed commands back at the managed native binary. If a release-native asset is unavailable, or when installing from `TENSOR_GREP_CHANNEL=main`, the same front door falls back to `python -m tensor_grep`.

They also run `tg lsp-setup --json` after creating the front-door `tg` command. That attempts the safe default managed provider setup under `~/.tensor-grep/providers` for pinned Node-backed providers and warns without failing the core install if optional provider setup is unavailable. If you install through `pip`, `uv`, or a package-manager channel and need provider-backed planning, run `tg lsp-setup` manually. Use `tg lsp-setup --include-toolchain-providers` only when you want tensor-grep to copy or install Rust, Go, and C# provider binaries through local toolchains. Provider availability is install evidence only; use `tg doctor --with-lsp` health fields and navigation `lsp_proof` / `lsp_evidence_status` before relying on `lsp` or `hybrid` output as semantic-provider evidence.

On Windows, the install script removes stale same-directory `tg.exe`/`tg.bat` launchers from the managed shim directories, then puts `~/.tensor-grep/bin` ahead of compatibility shim directories and stale Python `Scripts` launchers on User PATH. That makes `cmd`, unprofiled PowerShell, and Python `subprocess.run(["tg", ...])` resolve the native `tg.exe` first instead of paying the `.cmd` Python bridge startup cost. Normal PowerShell, Git Bash, and WSL shims still route to the managed native front door when it exists. The `.cmd` shim remains as an argv-safe compatibility bridge for direct `.cmd` calls and execs the managed native binary from that bridge when available. If an old tensor-grep-owned `Python*\Scripts\tg.exe` still shadows the managed front door, the installer attempts to uninstall the stale Python package owner. If a profile-free shell still reports an older `tg`, run `where.exe tg`, `Get-Command tg -All`, and `tg doctor --json` to see which launcher is winning.

**Windows (PowerShell):**
```powershell
irm https://raw.githubusercontent.com/oimiragieo/tensor-grep/main/scripts/install.ps1 | iex
```

**Linux & macOS (Bash):**
```bash
curl -LsSf https://raw.githubusercontent.com/oimiragieo/tensor-grep/main/scripts/install.sh | bash
```

## Option 2: Using `npx`

If you have Node.js installed, you can use `npx` to download and run the correct binary for your platform automatically:

```bash
npx tensor-grep search "ERROR" app.log
```

Current npm wrapper notes:

- downloads the current CPU release asset from `oimiragieo/tensor-grep`
- supports Windows x64, Linux x64, and macOS x64
- writes a local `tg` / `tg.exe` shim into the installed npm package

To install it globally via npm:

```bash
npm install -g tensor-grep
tg search "ERROR" app.log
```

## Option 3: Pre-compiled Binaries (Direct Download)

The semantic-release path publishes release-validated CPU front-door binaries for Windows, Linux,
and macOS via GitHub Releases. NVIDIA/GPU binaries are not part of the current main-CI native
front-door asset profile.

1. Go to the [GitHub Releases](https://github.com/oimiragieo/tensor-grep/releases) page.
2. Download the binary for your platform:
   - `tg-windows-amd64-cpu.exe`
   - `tg-linux-amd64-cpu`
   - `tg-macos-amd64-cpu`
3. Add it to your system PATH.
4. Verify the binary against `CHECKSUMS.txt` before rollout.

## Option 4: Python (`pip` / `uv`)

If you prefer to run the tool from source or within a Python environment:

```bash
pip install tensor-grep
tg --help
```

*Note: the Python package path is the one that supports `tg update` / `tg upgrade`. It requires a configured Python environment and may need additional GPU dependencies such as `cudf` and `torch`.*

On Windows, the Python package installs a launcher shim under a Python `Scripts` directory. That shim is for invoking the Python CLI path, not for native delegation. Simple AST rewrite plan/apply is still available through the packaged PyO3 Rust extension. If you need native-only features such as rewrite diff, checkpoint, audit, validation, verify, or explicit MCP handoff to the standalone executable, point `TG_NATIVE_TG_BINARY` at an explicit native `tg.exe` path or use a release binary / in-tree Rust build.

## Option 5: Package Managers

- **Homebrew:** use the published formula for macOS and Linux rollout.
- **Winget:** use the published manifest for Windows rollout.
- **PyPI:** use for Python integration or self-managed virtual environments.

## Maintainer Notes: Package Manager Publish Flow

The repository includes package-manager manifests:
- Homebrew formula: `scripts/tensor-grep.rb`
- Winget manifest: `scripts/oimiragieo.tensor-grep.yaml`

Before cutting a tag release:
1. Keep `pyproject.toml`, `rust_core/Cargo.toml`, and `npm/package.json` versions aligned.
2. Ensure manifest URLs point to release artifact names produced by main CI's `publish-github-release-assets` job.
3. Run:

```bash
uv run python scripts/validate_release_assets.py
```

Main CI now runs this same validation in the `release-readiness` job to prevent release drift.

### Homebrew Tap Flow

1. Keep `scripts/tensor-grep.rb` aligned with the tagged version and release artifact URLs.
2. Validate formula syntax:

```bash
ruby -c scripts/tensor-grep.rb
```

3. Commit/update the formula in your tap repository (for example `oimiragieo/homebrew-tap`), then test install:

```bash
brew tap oimiragieo/tap
brew install tensor-grep
brew install oimiragieo/tap/tensor-grep
tg --version
```

### Winget Flow

1. Keep `scripts/oimiragieo.tensor-grep.yaml` aligned with the tagged version, Windows artifact URL, `PackageLocale`, and Windows artifact SHA256.
2. Validate manifest locally on Windows:

```powershell
winget validate --manifest scripts\oimiragieo.tensor-grep.yaml
```

3. Submit/update the manifest in `microsoft/winget-pkgs`, then smoke-test install:

```powershell
winget install oimiragieo.tensor-grep
tg --version
```

CI coverage:
- `ci.yml` includes `package-manager-readiness` on Linux + Windows.
- `ci.yml` builds release-native CPU front-door assets from the semantic-release tag and uploads them to the GitHub release before PyPI publish is allowed.
- On runners where `winget validate` is unavailable, workflows fall back to `scripts/validate_release_assets.py`. If hosted-runner `winget validate` reports only the known schema-header URL warning from embedded schema-version skew, CI also requires `scripts/validate_release_assets.py` to pass before continuing; other `winget` failures remain blocking.
- CI/release package-manager jobs also run `scripts/prepare_package_manager_release.py --check` to ensure manifests are ready for tap/winget-pkgs publication.
- `publish-github-release-assets` builds `artifacts/package-manager-bundle`, stamps Winget `InstallerSha256` from generated `CHECKSUMS.txt`, verifies `BUNDLE_CHECKSUMS.txt`, and runs `scripts/smoke_test_package_manager_bundle.py` before publishing release assets.

Release automation notes:
- The normal release path is main CI after semantic-release. Tags created by the default `GITHUB_TOKEN` do not trigger a separate tag-push workflow run, so `release.yml` is a manual/backfill path rather than the authoritative asset publisher.
- `scripts/validate_release_assets.py` verifies cross-file version/URL consistency across PyPI, npm, Homebrew, and Winget release assets.
- CI and release workflows install `uv` before Windows Winget fallback checks to keep validation deterministic on runner images without `winget validate`.
- Main CI (`ci.yml`) validates built PyPI artifacts before publish with `scripts/validate_pypi_artifacts.py`.
- Main CI also runs `scripts/smoke_test_pypi_artifacts.py` to install from local `dist/` artifacts in an isolated virtual environment before publish.
- `publish-pypi` depends on `publish-github-release-assets` so PyPI cannot publish before installer-critical GitHub release assets are verified.
- `publish-pypi` verifies PyPI's latest version matches the semantic-release tag version before the job is marked successful.
- `publish-success-gate` in main CI always verifies GitHub release native assets and PyPI latest parity for the semantic-release version, even when PyPI publish is skipped.
- `release.yml` verifies npm registry latest parity (`--check-npm`) after `npm publish` before release success gate completion.

### Repeatable Release Checklist

1. Merge to `main` only after CI is green.
2. Confirm semantic-release created tag `vX.Y.Z` and matching GitHub release.
3. Confirm CI `validate-pypi-artifacts` is green before `publish-pypi`.
4. Confirm PyPI latest version is exactly `X.Y.Z`.
5. Confirm `scripts/tensor-grep.rb` and `scripts/oimiragieo.tensor-grep.yaml` reference `vX.Y.Z` assets.
6. Confirm the uploaded GitHub release assets and checksum coverage:

```bash
python scripts/verify_github_release_assets.py --repo oimiragieo/tensor-grep --tag vX.Y.Z
```

### Rollback Playbook

If a publish is bad or inconsistent:

1. Stop new releases by merging a corrective patch or temporarily disabling the release path.
2. Ship an immediate patch release (`X.Y.(Z+1)`) with corrected artifacts. Do not attempt to overwrite an existing PyPI version.
3. For package managers:
   - Homebrew: update formula to corrected version and re-run tap tests.
     ```bash
     git revert <tap-formula-commit>
     brew update
     brew install tensor-grep
     tg --version
     ```
   - Winget: submit corrected manifest version to `winget-pkgs`.
     ```powershell
     winget uninstall oimiragieo.tensor-grep
     winget install oimiragieo.tensor-grep
     tg --version
     ```
4. Update `CHANGELOG.md` with rollback reason and remediation commit hash.
