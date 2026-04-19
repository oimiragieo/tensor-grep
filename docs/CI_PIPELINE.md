# CI Pipeline

`tensor-grep` treats CI as part of the product contract, not a best-effort test runner. If you change workflow behavior, release behavior, package-manager behavior, or supply-chain automation, update the validator-backed tests in `scripts/validate_release_assets.py` and `tests/unit/test_release_assets_validation.py`.

## Workflow Overview

### `ci.yml`

Runs on pushes to `main`, pull requests targeting `main`, and a weekly schedule.

Primary responsibilities:

- `release-readiness`: strict docs build plus workflow/package-manager validator checks
- `Formatting & Linting`: Rust formatting/clippy plus Python Ruff and mypy
- `test-python`: cross-platform Python test matrix
- `test-rust-core`: cross-platform Rust test matrix
- `search-golden-parity`: Windows routing parity guard
- `native-build-smoke`: native binary smoke tests across platforms
- `package-manager-readiness`: Homebrew/Winget/package-manager bundle validation
- `benchmark-regression`: blocking same-runner base-vs-head regression gate plus accepted-baseline drift reporting
- `Semantic Release`: semantic-release on `main`
- PyPI build, validation, and publish jobs when semantic-release emits a new version

Release behavior:

- Conventional commit subjects drive semantic-release intent.
- `feat:` => minor
- `fix:` / `perf:` => patch
- `feat!:` / `fix!:` => major
- `docs:` / `test:` / `ci:` / `chore:` / `build:` => no package release

Benchmark behavior:

- Pull requests benchmark the candidate checkout and the PR base revision in the same runner job; the explicit base-vs-head comparison is the blocking regression gate.
- Pushes to `main` benchmark the current checkout and `${{ github.event.before }}` in the same runner job; that explicit comparison is the blocking regression gate before semantic-release.
- Scheduled runs keep accepted-baseline drift reporting and summary generation, but do not block on a synthetic base-vs-head comparison.
- Accepted baseline JSONs remain the public benchmark truth surface, but drift against them is reported separately from the release-blocking same-runner comparison.

### `release.yml`

Builds and verifies tagged release artifacts, publishes binary assets, docs, npm artifacts, SBOMs, provenance, and signing metadata. This workflow is the release artifact pipeline; `ci.yml` is the semantic-release decision and PyPI pipeline.

### `audit.yml`

Runs dependency and license audits:

- `cargo audit`
- `cargo deny check`
- `pip-audit`

`pip-audit` runs inside a uv-created Python environment after `uv python install 3.12`.
Do not invoke `uv pip install` in this workflow without creating that environment first, or the job will fail before the audit runs.

The Rust license policy for `cargo deny check` is owned in-repo at `rust_core/deny.toml`. If the Rust dependency graph changes, update that policy and the audit workflow contract tests together rather than relying on cargo-deny defaults.

Triggers:

- nightly schedule
- pull requests to `main`
- manual dispatch

On the nightly scheduled run only:

- if the audit fails, CI creates or updates a single tracked GitHub issue: `[Security Audit] Scheduled dependency audit failure`
- if the next nightly audit succeeds, CI closes that issue automatically

This closes the loop from detection to owned remediation instead of silently failing in Actions history.

## Dependabot Maintenance

Dependabot is configured in `.github/dependabot.yml` for:

- `github-actions`
- `uv`
- `cargo`
- `npm`

The automation workflow `.github/workflows/dependabot-automation.yml` applies labels, triages update risk, and enables auto-merge only for low-risk updates:

- patch/minor GitHub Actions updates
- patch/minor non-production (`direct:development`) or indirect dependency updates

It does **not** auto-merge:

- semver-major updates
- direct production/runtime dependency updates

Those remain manual-review changes.

## Rules For Future Agents

If you touch `.github/workflows/*.yml`, `.github/dependabot.yml`, or release/package-manager behavior:

1. update the workflow/config
2. update `scripts/validate_release_assets.py`
3. update `tests/unit/test_release_assets_validation.py`
4. run:

```bash
uv run ruff check .
uv run mypy src/tensor_grep
uv run pytest -q
uv run python scripts/validate_release_assets.py
```

Do not hand-wave workflow changes. This repo treats CI behavior as a versioned contract.
