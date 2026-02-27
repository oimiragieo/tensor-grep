TBD
===
Unreleased changes. Release notes have not yet been written.

0.2.0
===
- **Features (The Enterprise Arrow Architecture update)**
  - Replaced high-overhead Python PyO3 String bindings with a true End-to-End Zero-Copy **Apache Arrow PyCapsule** integration. 
  - `tensor-grep-rs` now maps OS files via `memmap2` and constructs Arrow StringArrays without copying the payload into RAM.
  - Implemented dynamic **VRAM Chunking**, yielding Arrow slices to cuDF to prevent Out-Of-Memory (OOM) exceptions when mapping multi-gigabyte log files to GPU VRAM limits.
  - Replaced CPU-bound HuggingFace cyBERT tokenization with native `cudf.core.subword_tokenize()`. Logs stay entirely in VRAM while being mapped to Transformer tensors.
- **Fixes**
  - Updated Pipeline to automatically map complex `ripgrep` regex configurations (like invert-match, boundaries, context cues) directly to the CPU fallback if they exceed Rust's core capabilities.

0.1.5
===
- **Features**
  - Integrated full Model Context Protocol (MCP) server support (`tg mcp`) utilizing the FastMCP SDK, allowing AI assistants (like Claude Desktop and Cursor) to directly utilize GPU-accelerated ripgrep searching, AST parsing, and cyBERT log classification within their context windows.
  - Fully automated cross-platform PyO3 multi-wheel compilation and secure GitHub OIDC PyPI publishing inside the main `.github/workflows/release.yml` pipeline.

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
