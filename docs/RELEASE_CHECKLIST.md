# Release Checklist

This checklist reflects the current enterprise release pipeline:

- `CI` on `main` runs formatting/linting, Python + Rust matrices, GPU hooks, benchmark regression, release/package-manager readiness checks.
- `Semantic Release` on green `main` creates the version/tag/release commit.
- PyPI publish is OIDC-based and runs only when the computed release version is not already on PyPI.
- Release asset/version consistency is enforced by `scripts/validate_release_assets.py`.

## 1. Pre-merge requirements

1. Sync local branch:
   ```bash
   git checkout main
   git pull --rebase origin main
   ```
2. Confirm version-bearing files are aligned:
   - `pyproject.toml`
   - `rust_core/Cargo.toml`
   - `npm/package.json`
   - `scripts/tensor-grep.rb`
   - `scripts/oimiragieo.tensor-grep.yaml`
3. Run local release consistency guard:
   ```bash
   uv run python scripts/validate_release_assets.py
   ```
4. Run local quality gates:
   ```bash
   uv run ruff check .
   uv run ruff format --check --preview .
   uv run mypy src/tensor_grep
   uv run pytest tests -v --tb=short
   ```

## 2. CI gate requirements on `main`

1. Push to `main` only after local checks pass.
2. Ensure all required jobs are green:
   - `Formatting & Linting`
   - `release-readiness`
   - `package-manager-readiness`
   - `test-python` matrix
   - `test-rust-core` matrix
   - `test-gpu-nvidia`
   - `benchmark-regression`
3. Confirm no benchmark regression gate failure.

## 3. Release and publication flow

1. Do not manually bump tags when semantic-release is active.
2. Let the `Semantic Release` job create:
   - release commit
   - Git tag `vX.Y.Z`
   - GitHub release metadata
3. If `publish_pypi=true`, confirm downstream jobs pass:
   - `build-pypi-wheels`
   - `build-pypi-sdist`
   - `validate-pypi-artifacts`
   - `publish-pypi`
4. Verify published version parity:
   - GitHub tag version equals PyPI latest version
   - GitHub tag version equals `npm/package.json` version

## 4. Package-manager distribution finalization

1. Homebrew formula readiness:
   - CI runs `ruby -c scripts/tensor-grep.rb`.
   - Ensure formula URL points at `https://github.com/oimiragieo/tensor-grep/releases/download/v#{version}/...`.
2. Winget manifest readiness:
   - CI runs `winget validate` on Windows when available and falls back to Python validator.
   - Ensure `PackageVersion` and `InstallerUrl` in `scripts/oimiragieo.tensor-grep.yaml` match release version.
3. Post-release operational publish:
   - Homebrew tap update: open/update PR in tap repo with new formula.
   - Winget submission: create/update manifest PR in winget-pkgs for new version.
4. Keep release artifacts canonical:
   - Build artifacts must map 1:1 to tag version and expected filenames.

## 5. Rollback runbook

Use this when a bad release escaped:

1. Stop forward publishes:
   - Temporarily disable release workflow or merge a hotfix with `skip release` in commit message.
2. If PyPI publication is incorrect:
   - Publish a patch release with corrected contents (preferred).
   - Do not rely on deleting artifacts as a primary rollback mechanism.
3. If GitHub release assets are incorrect:
   - Rebuild artifacts for the correct tag.
   - Replace assets in GitHub release and rerun validators.
4. Package manager rollback:
   - Homebrew: revert formula in tap to previous known-good version.
   - Winget: submit manifest update pointing to previous known-good installer.
5. Incident close-out:
   - Add root cause + mitigation to `CHANGELOG.md` and `docs/PAPER.md` (if architecture-impacting).
   - Add/adjust CI assertion so the specific failure cannot recur.

## 6. Operator verification commands

```bash
# CI + release status
gh run list --limit 10

# Quick version parity checks
python - << 'PY'
import tomllib, json
from pathlib import Path
print("pyproject:", tomllib.loads(Path("pyproject.toml").read_text())["project"]["version"])
print("cargo:", [l for l in Path("rust_core/Cargo.toml").read_text().splitlines() if l.startswith("version = ")][0])
print("npm:", json.loads(Path("npm/package.json").read_text())["version"])
PY

# Release asset consistency
uv run python scripts/validate_release_assets.py
```
