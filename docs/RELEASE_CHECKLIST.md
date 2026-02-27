# Release Checklist

1. **Ensure local `main` is up to date** with respect to `origin/main`.
   ```bash
   git checkout main
   git pull origin main
   ```

2. **Update Dependencies** and review semver incompatible updates. Unless there is a strong motivation otherwise, review and update every dependency.
   * **Python (`uv`)**: Run `uv lock --upgrade` and review changes.
   * **Rust (`cargo`)**: Run `cargo update` in the `rust_core` directory.
   * **Node (`npm`)**: Run `npm update` in the `npm` directory.
   * Commit all updated lock files (`uv.lock`, `rust_core/Cargo.lock`, `npm/package-lock.json`).

3. **Update the `CHANGELOG.md`** as appropriate.
   * Move the current "TBD" changes into a versioned header (e.g., `0.1.4`).
   * Group changes into `Features`, `Fixes`, and `Performance` sections.

4. **Bump Version Numbers** across the tripartite architecture:
   * Edit `pyproject.toml` `[project]` version.
   * Edit `rust_core/Cargo.toml` `[package]` version.
   * Edit `npm/package.json` `"version"`.
   * *Ensure these versions perfectly align before proceeding.*

5. **Local Build & Validation**
   * Run the full Python test suite: `uv run pytest tests`
   * Run the Rust core tests: `cd rust_core && cargo test`
   * Run the Nuitka standalone build script locally: `uv run python scripts/build_binaries.py`
   * Ensure it succeeds without fatal C-compiler errors.

6. **Push Changes to GitHub (Without Tag)**
   ```bash
   git add pyproject.toml rust_core/Cargo.toml npm/package.json CHANGELOG.md uv.lock rust_core/Cargo.lock npm/package-lock.json
   git commit -m "chore: bump version to {VERSION}"
   git push origin main
   ```

7. **Wait for CI Validation**
   * Monitor the Actions tab and wait for the `CI` pipeline for `main` to finish successfully.
   * Ensure the Python tests, Rust tests, and static analysis (Ruff/Clippy) pass on all OS targets.

8. **Create and Push the Git Tag**
   * Once CI passes, push the signed tag. *(Doing this in a separate step ensures the GitHub Actions Release workflow triggers cleanly).*
   ```bash
   git tag v{VERSION}
   git push origin v{VERSION}
   ```

9. **Monitor the Release Build**
   * Wait 10-15 minutes for the `Release` workflow to finish cross-compiling the `Nuitka` monolithic binaries (Linux, macOS, Windows).
   * If the release build fails, delete the tag from GitHub, make fixes, re-tag, delete the broken release draft, and push again.

10. **Finalize GitHub Release Notes**
    * Copy the relevant section of the `CHANGELOG.md` to the tagged release notes on GitHub.
    * Include this blurb at the top describing what tensor-grep is:
      > In case you haven't heard of it before, `tensor-grep` (tg) is a GPU-accelerated semantic log parsing CLI that combines the lightning-fast string matching of `ripgrep` with PyTorch, NVIDIA RAPIDS, and transformer AI models to perform deep structural and semantic context searches across massive log databases.

11. **Publish to Package Managers**
    * The GitHub Actions pipeline should automatically publish the Node wrapper to `npm`.
    * Manually publish the Python wheel to PyPI:
      ```bash
      uv build
      uv publish
      ```

12. **Prepare for the Next Cycle**
    * Add a new `TBD` section to the top of `CHANGELOG.md`:
      ```markdown
      TBD
      ===
      Unreleased changes. Release notes have not yet been written.
      ```
    * Commit and push.
