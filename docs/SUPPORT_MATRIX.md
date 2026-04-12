# Support Matrix

This document defines the officially supported operating systems, runtime environments, and distribution channels for `tensor-grep`.

## Operating Systems
- **Linux:** Ubuntu 20.04+, Debian 11+, RHEL 11+. (glibc 2.31+)
- **Windows:** Windows 10, Windows 11, Windows Server 2019+. (amd64)
- **macOS:** macOS 12+ (Apple Silicon and Intel).

## Python Versions
- **Supported:** Python 3.9, 3.10, 3.11, 3.12, 3.13, 3.14.
- Python < 3.9 is completely unsupported and untested.

## Rust Toolchain
- **Supported:** Stable Rust (1.75+).

## Package Managers
- **pip:** Official distribution channel for Python integration.
- **winget:** Official distribution for Windows standalone binaries.
- **Homebrew:** Official distribution for macOS standalone binaries.

## Semantic Versioning & Deprecation
`tensor-grep` follows Semantic Versioning (SemVer) 2.0.0.
- **Major versions** may introduce breaking changes to CLI flags, `sgconfig.yml` schemas, or JSON outputs.
- **Minor versions** add features in a backward-compatible manner.
- **Deprecation Policy:** Features, flags, or fields scheduled for removal will be marked as `DEPRECATED` for at least 2 minor versions before removal.
