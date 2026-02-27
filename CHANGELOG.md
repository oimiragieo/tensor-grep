TBD
===
Unreleased changes. Release notes have not yet been written.

0.1.4
===
- **Features**
  - Completely rewrote README.md documentation matching ripgrep's exhaustive structure.
  - Improved cross-platform environment testing (Ruff/Mypy Strict) using custom pyproject.toml flag `warn_unused_ignores = false`.
- **Fixes**
  - Fixed Windows PowerShell installation script by splitting `--index-url` flags so that `uv pip install` parses the arguments correctly.

0.1.3
===
- **Fixes**
  - Corrected Nuitka cross-compiler binary output extension mapping for Linux and macOS (`tg` instead of `tg.bin`) in the automated GitHub Actions CI release pipeline.

0.1.2
===
- **Features**
  - Triggered first fully automated Nuitka compilation matrix for standalone IT/SecOps executables.

0.1.1
===
- **Features**
  - Merged dependabot automated security patches across Cargo, NPM, and UV package ecosystems.
  - Implemented exhaustive `ripgrep`-style CI validation matrix (Rustfmt, Clippy, Ruff, Mypy, and Pytest fallback validations).

0.1.0
===
- **Features**
  - Initial `tensor-grep` release.
  - Tripartite backend engine routing (CPU fallback via Rust PyO3 `memmap2`, RAPIDS `cuDF` via PCIe VRAM streams, `Torch` native via Windows CUDA spawn pools).
  - Neural `cyBERT` semantic threat classification transformer backend.
  - Structural source-code querying via `tree-sitter` and PyTorch Geometric Graph Neural Networks (`--ast`).
