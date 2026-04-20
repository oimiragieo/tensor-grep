# Experimental and Hidden Features

This document catalogs features and commands in `tensor-grep` that are intentionally hidden from public CLI help or marked as experimental. These features are generally unsupported for production enterprise use, but are maintained for testing, advanced workflows, or future graduation to stable status.

## 1. Resident AST Worker (`tg worker`)
The Resident AST Worker is an experimental feature designed to keep the AST metadata cache (`project_data_v6.json`) warm in memory via TCP IPC.

- **Status:** Opt-in, Experimental.
- **Support boundary:** Not covered by the stable enterprise contract in [docs/CONTRACTS.md](CONTRACTS.md). It may change or be removed in a minor release.
- **Hidden Command:** `tg worker`
  - This command is hidden from `tg --help` to prevent confusion, but is fully functional when invoked directly (e.g., `tg worker --port 12345`).
- **Feature Flag:** Set the environment variable `TG_RESIDENT_AST=1` to enable the worker workflow.
- **Performance note:** This path is workload-dependent. It can help startup-dominated repeated micro-workflows, but it is not the default performance path for larger scans.
- **Runbook:** Refer to [Resident AST Worker Runbook](runbooks/resident-worker.md) for troubleshooting IPC port conflicts and orphaned processes.

## 2. Experimental Backend Overrides
Various environment variables allow forcing specific backends to bypass the standard routing logic. These are intentionally undocumented in `--help` to prevent users from breaking the optimal routing paths.

- `TG_FORCE_CPU=1`: Bypasses GPU acceleration entirely. (Documented in [gpu-troubleshooting.md](runbooks/gpu-troubleshooting.md))
- `TG_RUST_FIRST_SEARCH=1`: Prefer native Rust search delegation when the invocation stays on the supported cold-search surface.
- `TG_RUST_EARLY_RG=1`: Prefer the early native ripgrep-compatible routing path during Rust-core migration testing.
- `TG_RUST_EARLY_POSITIONAL_RG=1`: Extend the early native ripgrep-compatible routing path to bare positional search invocations.
- `TG_RESIDENT_AST=1`: Enable the resident AST worker workflow described above.

*Note: As `tensor-grep` stabilizes these features, they will either be graduated to public `sgconfig.yml` settings, converted into standard CLI flags, or formally deprecated.*
