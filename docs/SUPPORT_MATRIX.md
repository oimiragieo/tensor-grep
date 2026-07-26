# Support Matrix

This document distinguishes CI-tested environments from best-effort compatibility and operator-managed deployments for `tensor-grep`.

## Platform Tiers

### Tier 1: CI-tested and release-validated
- **Linux amd64:** `ubuntu-latest` CI, published CPU/NVIDIA release binaries, package-manager bundle validation.
- **Windows amd64:** `windows-latest` CI, published CPU/NVIDIA release binaries, Winget manifest validation.
- **macOS amd64:** `macos-15-intel` native-build-smoke and release-native asset CI, published CPU release binary (`tg-macos-amd64-cpu`), Homebrew formula validation.
- **macOS arm64:** `macos-latest` native-build-smoke CI for Apple Silicon build coverage.

### Best-effort / operator-validated
- **Other glibc-compatible Linux distributions:** expected to work when they remain compatible with the published release binaries and Python dependency set.
- **Windows Server variants:** expected to track the supported Windows runner base closely enough for standard CLI use, but not exhaustively CI-covered.
- **Apple Silicon macOS:** use Rosetta with the published amd64 binary or build from source until a native arm64 release artifact is introduced.

## Python Versions
- **CI-tested:** Python 3.11 and 3.12.
- **Source/package floor:** Python >= 3.11, matching `pyproject.toml`.
- **Unsupported:** Python < 3.11.

## Rust Toolchain
- **Maintainer baseline:** stable Rust 1.75+.
- **Expectation:** use the stable toolchain from CI/release workflows when validating release builds or reproducing artifacts.

## Distribution Channels
- **Official / release-validated:** GitHub Releases, PyPI, Homebrew formula, Winget manifest.
- **Convenience channel:** `npx` wrapper for lightweight Node-based invocation.
- **Operational guidance:** prefer PyPI or the install scripts when you need `tg update`; prefer GitHub Releases, Homebrew, or Winget for managed workstation/server rollout.

## Semantic Versioning & Deprecation
`tensor-grep` follows Semantic Versioning (SemVer) 2.0.0.
- **Major versions** may introduce breaking changes to CLI flags, `sgconfig.yml` schemas, or machine-readable outputs.
- **Minor versions** add features in a backward-compatible manner.
- **Deprecation Policy:** stable features, flags, or fields scheduled for removal are marked
  `DEPRECATED` for at least **90 days AND 2 minor versions** -- whichever is longer.

  The time floor is the load-bearing half. This project releases on merge via semantic-release,
  and the measured cadence is **a median of 0 days between minor bumps** (v1.95, v1.96, v1.97 and
  v1.98 all shipped on 2026-07-24). A version-only window is therefore not a window: read
  literally, "2 minor versions" can elapse in an afternoon, while a reader reasonably infers
  months. If you are pinning `tensor-grep` in a managed environment, 90 days is the number to
  plan against.
- **Supported versions / security patches:** the **latest released version only**. There are no
  maintenance branches -- every fix ships forward from `main` -- so a security fix arrives as a new
  release, never as a backport to an older line. Upgrade is the patch path. Air-gapped or
  version-pinned deployments should budget for this explicitly rather than assume an LTS line
  exists; there isn't one.
- **Experimental Surface:** items documented in [docs/EXPERIMENTAL.md](EXPERIMENTAL.md) are outside the stable compatibility guarantees and may change in minor releases.
