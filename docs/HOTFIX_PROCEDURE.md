# Hotfix & Rollback Procedure

This document outlines the operational procedures for addressing critical bugs or security vulnerabilities in `tensor-grep` production releases.

## 1. Hotfix Release Procedure

When a critical issue requires an immediate patch without including unreleased features from `main`:

1. **Branching:**
   Create a hotfix branch from the affected stable tag.
   ```bash
   git checkout -b hotfix/v1.0.1 v1.0.0
   ```

2. **Patching:**
   Cherry-pick the specific bugfix commits from `main`, or implement the minimal required fix directly on the hotfix branch.

3. **Validation:**
   Run the full local test suite and ensure no regressions are introduced.
   ```bash
   uv run pytest
   cargo test
   ```

4. **Tagging & Releasing:**
   Push the branch and create a new patch tag.
   ```bash
   git tag -a v1.0.1 -m "Hotfix for critical issue"
   git push origin v1.0.1
   ```
   The `.github/workflows/release.yml` will automatically trigger, build the binaries, generate SBOMs, sign artifacts, and publish the release.

## 2. Rollback Procedure

If a deployed version causes severe regressions, administrators must roll back to the previous stable version.

### Winget (Windows)
```powershell
winget install oimiragieo.tensor-grep --version 1.0.0
```

### pip (Python)
```bash
pip install tensor-grep==1.0.0
```

### Homebrew (macOS)
Homebrew users should use the exact URL to the previous formula commit or download the binary directly from the GitHub Releases page.

## 3. Reproducible Release Verification
To verify that a published binary matches the source code:
1. Clone the repository and checkout the exact tag (e.g., `v1.0.0`).
2. Execute the build scripts in `scripts/build_binaries.py` using the exact Rust/Python toolchains specified in `.github/workflows/release.yml`.
3. Compare the SHA256 hashes of the generated binaries against the `CHECKSUMS.txt` provided in the GitHub Release assets.
