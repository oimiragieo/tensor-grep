# Runtime Path Resolution

- After the `runtime-binary-path-resolution` feature, `tg.exe` no longer uses `CARGO_MANIFEST_DIR` (compile-time path) to locate the Python sidecar or bundled `rg` binary. Instead, it resolves paths relative to `std::env::current_exe()` at runtime, walking ancestor directories.
- Shared resolution logic lives in `rust_core/src/runtime_paths.rs`.
- Env var overrides for the rg passthrough binary:
  - `TG_RG_PATH`: override the ripgrep binary path (primary).
  - `TG_RG_BINARY`: legacy alias, still honored as fallback.
- Both overrides are checked with `is_file()` before use; if the path doesn't exist, the override is silently ignored and runtime resolution proceeds.
- The `rg_passthrough` module is now a public library module (`pub mod rg_passthrough` in `rust_core/src/lib.rs`) for cross-crate testability.
