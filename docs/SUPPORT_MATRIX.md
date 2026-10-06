# Support matrix

As of 2026-10-06, this page separates tested build environments from published install channels and best-effort use. CI coverage means a workflow exercises that platform; it does not mean the newest run is green or guarantee every machine configuration.

## Platforms

| Platform | Evidence and practical limits |
|---|---|
| Linux x64 | Main CI tests Linux builds. A CPU native release asset is published. Other glibc-compatible distributions are best-effort and depend on compatibility with that asset and the Python dependencies. |
| Windows x64 | Main CI tests Windows builds. A CPU native release asset is published. Windows Server variants are best-effort and are not separately exhaustive. |
| macOS x64 | Native build and CPU release asset are covered by release workflows. |
| macOS arm64 (Apple Silicon) | CI provides native build coverage, but there is no arm64 release asset in the current published CPU asset set. Use a supported Python install or build from source; the x64 asset under Rosetta is another operator-managed option. |

The default native asset profile is CPU. The CI workflow has a separate `native-frontdoor-gpu` profile that gates GPU asset build steps; that profile is not the default release profile. GPU support is experimental and depends on build options, compatible hardware, drivers, and the route actually selected. See [experimental features](EXPERIMENTAL.md) and the [GPU runbook](runbooks/gpu-troubleshooting.md).

## Python and Rust versions

- Python package floor: Python 3.11 or newer (`pyproject.toml` declares `>=3.11`).
- Python versions exercised by the documented CI matrix: 3.11 and 3.12.
- Python < 3.11 is unsupported.
- Rust maintainers should use the stable toolchain specified by CI and release workflows. The source manifest's minimum toolchain is not a promise that every older stable compiler can reproduce current release builds.

## Published channels

PyPI, GitHub Release CPU binaries, and their managed install scripts are the available user channels described by [installation](installation.md). npm/`npx`, Homebrew, and Winget manifests exist in the repository but are not published to their registries. Do not interpret CI validation of a manifest as registry availability.

## Compatibility and support policy

`tensor-grep` follows Semantic Versioning. Minor releases add features compatibly; major releases may change CLI flags, configuration, or machine-readable outputs. The detailed compatibility rules live in [contracts](CONTRACTS.md).

Stable features, flags, and fields scheduled for removal are marked `DEPRECATED` for at least **90 days and 2 minor versions**, whichever period is longer. The time floor matters because multiple minor releases can occur close together.

Only the latest released version receives security fixes. There are no maintenance branches or older supported lines; upgrades carry fixes forward. Version-pinned or air-gapped deployments should account for that policy.

Experimental features are outside stable compatibility guarantees and may change in a minor release. Their current setup and limits are listed in [Experimental features](EXPERIMENTAL.md).
