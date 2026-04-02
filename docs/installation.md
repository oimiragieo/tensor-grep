# Installation

`tensor-grep` is distributed as a standalone binary, meaning you do not need Python installed to run it.

## Option 1: Using npx (Recommended for Frontend Devs)

If you have Node.js installed, you can use `npx` to download and run the correct binary for your platform automatically:

```bash
npx tensor-grep search "ERROR" app.log
```

To install it globally via npm:

```bash
npm install -g tensor-grep
tg search "ERROR" app.log
```

## Option 2: Pre-compiled Binaries (Direct Download)

We provide pre-compiled binaries for Windows, Linux, and macOS.

1. Go to the [GitHub Releases](https://github.com/oimiragieo/tensor-grep/releases) page.
2. Download the binary for your platform (e.g., `tg-windows-amd64.exe`).
3. Add it to your system PATH.

## Option 3: Python (pip)

If you prefer to run the tool from source or within a Python environment:

```bash
pip install tensor-grep
tg --help
```

*Note: The pip version requires a configured Python environment and may require additional setup for GPU acceleration (like installing `cudf` and `torch`).*

Installer note:
- The script-driven install now provisions managed external LSP providers under `~/.tensor-grep/providers`.
- Use `tg lsp-setup` to refresh that managed provider toolchain if `tg doctor --with-lsp` reports a missing or stale provider binary.

## Maintainer Notes: Package Manager Publish Flow

The repository includes package-manager manifests:
- Homebrew formula: `scripts/tensor-grep.rb`
- Winget manifest: `scripts/oimiragieo.tensor-grep.yaml`

Before cutting a tag release:
1. Keep `pyproject.toml`, `rust_core/Cargo.toml`, and `npm/package.json` versions aligned.
2. Ensure manifest URLs point to release artifact names produced by `.github/workflows/release.yml`.
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

1. Keep `scripts/oimiragieo.tensor-grep.yaml` aligned with the tagged version and Windows artifact URL.
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
- `ci.yml` now includes `package-manager-readiness` on Linux + Windows.
- `release.yml` also validates Homebrew and Winget manifests before building release artifacts.
- On runners where `winget validate` is unavailable, workflows fall back to `scripts/validate_release_assets.py`.
- CI/release package-manager jobs also run `scripts/prepare_package_manager_release.py --check` to ensure manifests are ready for tap/winget-pkgs publication.
- Tag release workflow (`release.yml`) also builds `artifacts/package-manager-bundle`, verifies `BUNDLE_CHECKSUMS.txt`, and runs `scripts/smoke_test_package_manager_bundle.py` before publishing release assets.

Release automation notes:
- Tag pushes (`v*`) run `release.yml` and require `validate-release-assets` and `validate-package-managers` before binaries are built.
- `scripts/validate_release_assets.py` verifies cross-file version/URL consistency across PyPI, npm, Homebrew, and Winget release assets.
- CI and release workflows install `uv` before Windows Winget fallback checks to keep validation deterministic on runner images without `winget validate`.
- Main CI (`ci.yml`) now validates built PyPI artifacts before publish with `scripts/validate_pypi_artifacts.py`, checking:
  - expected version in wheel/sdist filenames,
  - wheel/sdist package metadata version,
  - platform wheel coverage (linux/macos/windows),
  - SHA256 hash matrix generation for all built artifacts.
- Main CI also runs `scripts/smoke_test_pypi_artifacts.py` to install from local `dist/` artifacts in an isolated virtual environment before publish.
- `publish-pypi` now verifies PyPI's latest version matches the semantic-release tag version before the job is marked successful.
- `publish-success-gate` in main CI always verifies PyPI latest parity for the semantic-release version, even when publish is skipped.
- `release.yml` now verifies npm registry latest parity (`--check-npm`) after `npm publish` before release success gate completion.

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

1. Stop new releases by pushing a hotfix commit that sets `publish_pypi=false` behavior or temporarily disables release gating branch.
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
