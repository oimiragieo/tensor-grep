---
name: tensor-grep-build-and-env
description: Set up the development environment and verify Python and Rust artifact locations before testing tensor-grep changes.
---

# Development environment

Read `pyproject.toml`, `uv.lock`, and `rust_core/rust-toolchain.toml` for current
dependencies and toolchain requirements. The Python package includes a Rust
extension built by maturin.

From the repository root:

```text
uv sync --frozen --extra dev --extra ast
uv run --no-sync python -c "import tensor_grep, tensor_grep.rust_core; print(tensor_grep.__file__); print(tensor_grep.rust_core.__file__)"
uv run --no-sync tg --version
```

The sync may compile Rust. Confirm imports point to the intended checkout and
environment before trusting test results. A globally installed `tg` may be a
different version.

After Rust changes, rebuild the extension before Python verification. Building
the standalone Rust executable alone does not replace the imported Python
extension. The standalone project is `rust_core/Cargo.toml`; its build outputs
are distinct from the Python extension.

Keep Windows and WSL virtual environments separate. Never run WSL environment
management against a Windows checkout's `.venv`. Use a native checkout and
environment for each platform.

Use locked dependency resolution; do not hand-edit `uv.lock`. Follow
[CONTRIBUTING.md](../../../CONTRIBUTING.md) for validation and
[installation](../../../docs/installation.md) for user-facing setup.
