# Hotfix & Rollback Procedure

This document outlines the operational procedures for addressing critical bugs or security vulnerabilities in `tensor-grep` production releases.

## 1. Hotfix Release Procedure

When a critical issue requires an immediate patch, the preferred path is still a corrective patch release through the normal `main` + semantic-release flow. Do not manually create release tags while semantic-release is authoritative.

1. **Branching and scope freeze:**
   Create a short-lived hotfix branch from the current `main` after rebasing to origin.
   ```bash
   git checkout main
   git pull --rebase origin main
   git checkout -b hotfix/<issue>-vX.Y.Z
   ```
   Keep the patch minimal. Do not batch unrelated cleanup or feature work into the hotfix.

2. **Patching:**
   Cherry-pick the specific bugfix commits or implement the minimal required correction directly on the hotfix branch.

3. **Validation:**
   Run the local quality gates and any relevant benchmark before the fix is proposed for merge.
   ```bash
   uv run ruff check .
   uv run mypy src/tensor_grep
   uv run pytest -q
   ```
   For hot-path behavior changes, also run the relevant benchmark from `benchmarks/` and compare against the accepted baseline.

4. **Release through `main`:**
   Open a PR with a conventional patch title such as `fix: correct <hotfix subject>`.
   - Merge with **Squash and merge** so the validated PR title becomes the commit subject on `main`.
   - Let semantic-release generate `vX.Y.(Z+1)` and publish the corrected artifacts.
   - Confirm CI, parity checks, SBOM generation, provenance, and release asset validation are green on the exact merged commit.

## 2. Rollback Procedure

If a deployed version causes severe regressions, administrators must roll back to the previous stable version.

### Winget (Windows)
```powershell
winget install oimiragieo.tensor-grep --version A.B.C
```

### pip (Python)
```bash
pip install tensor-grep==A.B.C
```

### Homebrew (macOS)
Homebrew users should use the exact URL to the previous formula commit or download the binary directly from the GitHub Releases page.

## 3. Reproducible Release Verification

To verify that a published binary matches the source code:
1. Clone the repository and checkout the exact tag (for example `vX.Y.Z`).
2. Execute the build scripts in `scripts/build_binaries.py` using the exact Rust/Python toolchains specified in `.github/workflows/release.yml`.
3. Compare the SHA256 hashes of the generated binaries against the `CHECKSUMS.txt` provided in the GitHub Release assets.
