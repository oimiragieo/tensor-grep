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

1. Go to the [GitHub Releases](https://github.com/tensor-grep/tensor-grep/releases) page.
2. Download the binary for your platform (e.g., `tg-windows-amd64.exe`).
3. Add it to your system PATH.

## Option 3: Python (pip)

If you prefer to run the tool from source or within a Python environment:

```bash
pip install tensor-grep
tg --help
```

*Note: The pip version requires a configured Python environment and may require additional setup for GPU acceleration (like installing `cudf` and `torch`).*

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
tg --version
```

### Winget Flow

1. Keep `scripts/oimiragieo.tensor-grep.yaml` aligned with the tagged version and Windows artifact URL.
2. Validate manifest locally on Windows:

```powershell
winget validate --manifest scripts\oimiragieo.tensor-grep.yaml
```

3. Submit/update the manifest in `microsoft/winget-pkgs`.

CI coverage:
- `ci.yml` now includes `package-manager-readiness` on Linux + Windows.
- `release.yml` also validates Homebrew and Winget manifests before building release artifacts.
