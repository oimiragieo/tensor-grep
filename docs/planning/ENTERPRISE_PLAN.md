# Enterprise Distribution Plan: tensor-grep (tg)

To elevate `tensor-grep` from a standard Python package to a universally distributable enterprise binary similar to `ripgrep`, we need to implement standalone binaries, native package managers, an `npm` wrapper, comprehensive documentation, and automated CI/CD releases.

## Phase 1: Standalone Binary Compilation (Nuitka)
Our priority is allowing users to run `tg` without requiring a Python environment.
- [ ] Install and configure **Nuitka** (`pip install nuitka`) in the development environment.
- [ ] Create a compilation script (`build_binaries.py`) that uses Nuitka to compile the CLI entrypoint (`src/tensor_grep/cli/main.py`).
- [ ] Ensure the compilation includes dynamic native libraries used by `cudf`, `transformers`, and `KvikIO`. (Note: Since `cudf` and GPU tooling are large, we need to carefully define `--include-package` flags and potentially test `--standalone` with external CUDA/toolkit linking).
- [ ] Build and verify a test executable (`tg.exe`) on the local Windows machine.

## Phase 2: The NPM Wrapper (`npx tensor-grep`)
Frontend engineers expect to run tools via `npx`. We will create a thin Node.js wrapper that fetches the correct binary.
- [ ] Initialize a new `npm` project structure within a `npm/` directory in the repo.
- [ ] Add the `binary-install` library (or similar post-install script) to `package.json`.
- [ ] Write a `postinstall.js` script that:
  - Detects the host OS (Linux, Windows, Darwin) and architecture (x64, arm64).
  - Fetches the appropriate `vX.Y.Z` binary payload from GitHub Releases.
  - Places it in a local `bin/` directory.
- [ ] Expose a wrapper executable in the `bin` field of `package.json` that routes to the downloaded binary.

## Phase 3: Enterprise Documentation (MkDocs Material)
Professional projects require professional documentation.
- [ ] Install `mkdocs-material` (`pip install mkdocs-material`).
- [ ] Create a `mkdocs.yml` configuration defining the theme, color scheme (e.g., slate/dark mode), and navigation structure.
- [ ] Scaffold the `docs/` directory with:
  - `index.md`: Hero page, "What is tensor-grep?"
  - `installation.md`: Showing `npm`, `pip`, and direct binary downloads.
  - `benchmarks.md`: Detailing the 3x speedup vs Ripgrep on semantic parsing.
  - `architecture.md`: Explaining the Multi-Pass Query Analyzer and dual CPU/GPU paths.
- [ ] Set up a `.github/workflows/docs.yml` to automatically publish to GitHub Pages on pushes to `main`.

## Phase 4: Automated CI/CD Release Pipelines
The release process must be fully automated to ensure consistency.
- [ ] Create `.github/workflows/release.yml` triggered on Git tags (e.g., `v*`).
- [ ] **Job 1 (Build Binaries):** Use a matrix strategy (Windows, Ubuntu) to compile the native executables via Nuitka. Upload the binaries as build artifacts.
- [ ] **Job 2 (GitHub Release):** Create a formal GitHub Release and attach the compiled artifacts (e.g., `tg-windows-amd64.exe`, `tg-linux-amd64`).
- [ ] **Job 3 (Publish NPM):** After the binaries are live on GitHub Releases, trigger an `npm publish` step for the `npm/` directory.
- [ ] **Job 4 (Publish PyPI):** Trigger `poetry publish` or `flit publish` / `twine` to publish the standard Python wheel to PyPI.

## Phase 5: Native Package Managers (Post-V1)
Once the CI/CD pipeline stably produces binaries:
- [ ] Create a Homebrew Tap (`homebrew-tensor-grep`) with a `tensor-grep.rb` formula pointing to the macOS/Linux binaries.
- [ ] Create a Winget manifest (`tensor-grep.yaml`) and submit it to the Microsoft `winget-pkgs` repository.
