# Environment

**What belongs here:** Required env vars, external dependencies, platform-specific notes.
**What does NOT belong here:** Service ports/commands (use `.factory/services.yaml`).

---

## Platform
- Windows 10 (build 26200)
- Python 3.14.0 via uv
- Rust MSRV 1.79, cargo at C:\Users\oimir\.cargo\bin\cargo.exe
- GPUs: NVIDIA RTX 4070 (12GB), RTX 5070 (12GB), CUDA available

## Hardware
- 131GB RAM, ~78GB available
- 8 cores / 16 logical processors
- NVMe SSD

## External Dependencies
- rg (ripgrep): available via benchmarks/rg.zip auto-extract or benchmarks/ripgrep-14.1.0-x86_64-pc-windows-msvc/rg.exe
- sg (ast-grep): may need installation via cargo install ast-grep
- hyperfine: for timing measurements
- mcp>=1.2.0: Python MCP server library

## Environment Variables
- TG_SIDECAR_PYTHON: override Python interpreter for sidecar (optional)
- TG_SIDECAR_MODULE: override sidecar module path (optional)
- CUDA_VISIBLE_DEVICES: control GPU visibility (optional)
- PYTHONPATH=src: required for pytest in some configurations
