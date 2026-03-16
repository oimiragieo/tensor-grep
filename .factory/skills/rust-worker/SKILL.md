---
name: rust-worker
description: Handles all Rust-side work: control plane, native search engine (grep crates), native GPU engine (cudarc/CUDA), routing, index subsystem, rewrite substrate, and benchmark gates.
---

# rust-worker

NOTE: Startup and cleanup are handled by `worker-base`. This skill defines the WORK PROCEDURE.

## When to Use This Skill

Use this worker for:
- Rust control plane changes (`rust_core/src/main.rs`, routing, JSON output)
- Native search engine embedding (grep-searcher, grep-regex, grep-matcher, grep-printer, ignore crates)
- Native GPU engine (cudarc, CUDA kernels, NVRTC JIT compilation)
- Chunk parallelism for large files (mmap + rayon)
- Routing logic and smart router (`routing.rs`)
- ast-grep-core embedding and AST backend (`backend_ast.rs`)
- Index subsystem (compression, incremental, regex) in `index.rs`
- Rewrite pipeline safety (atomic writes, stale-file detection, encoding)
- NDJSON streaming output, batch rewrite API
- Routing regression tests (`test_routing.rs`)
- Schema compatibility tests

## Windows-Specific Notes

- **Shell**: PowerShell is the default. Use `$env:PYTHONPATH = '...\src'` syntax.
- **Cargo path**: `C:\Users\oimir\.cargo\bin\cargo.exe` (may not be on PATH).
- **init.sh not executable**: Run `uv pip install -e ".[dev,ast,nlp]"` instead.
- **rg.exe**: Available via `benchmarks/rg.zip` auto-extract or `benchmarks/ripgrep-14.1.0-x86_64-pc-windows-msvc/rg.exe`.

## Work Procedure

1. **Read feature description and preconditions** carefully. Understand what assertions this feature fulfills.

2. **Test-Driven Development FIRST**: Write a failing Rust test (in `rust_core/tests/` or inline `#[cfg(test)]`) that asserts the new behavior. Run it and verify it fails. Only then implement.

3. Implement the Rust logic to make the test pass. Follow these rules:
   - MSRV Rust 1.79 — no newer features.
   - `pyo3 auto-initialize` is REMOVED. Never add it back.
   - Tree-sitter parsers are NOT `Sync` — use `thread_local!` or per-task allocation.
   - ast-grep-core API: `AstGrep::new(source, lang)`, `Pattern::new(pattern, lang)`, `root.find_all(pattern)`.
   - Preserve all existing invariants from AGENTS.md.

4. **For JSON output changes**: Ensure all JSON outputs include the unified envelope: `version` (u32), `routing_backend` (string), `routing_reason` (string), `sidecar_used` (bool). Test by parsing output with serde_json.

5. **For native search engine (grep crates)**: Use grep-searcher for search orchestration, grep-regex for pattern compilation, grep-matcher for the Matcher trait, grep-printer for output formatting, ignore crate for .gitignore-aware parallel directory walking. Reference the grep crate docs and the ripgrep source code for usage patterns. The grep crates handle binary detection, encoding, line counting, and output formatting — leverage them instead of reimplementing.

6. **For CUDA/GPU work**: 
   - Feature-gate all CUDA code behind `cfg(feature = "cuda")`.
   - Use `cudarc` for CUDA runtime: `CudaContext::new(device_id)` for device init, `compile_ptx()` for NVRTC kernel compilation, `stream.clone_htod()` for host-to-device copy, `stream.clone_dtoh()` for device-to-host.
   - CUDA kernels must be JIT-compiled via NVRTC (not pre-compiled PTX) to support both sm_89 (RTX 4070) and sm_120 (RTX 5070).
   - All CUDA errors must be caught and converted to user-facing messages. No panics, no raw CUDA error codes leaked.
   - Test with `cargo test --features cuda` and `cargo build --features cuda --release`.
   - Reference `.factory/research/gpu-text-search.md` for kernel design patterns.

7. **For rewrite safety changes**: Test with edge cases: BOM files, CRLF files, binary files (NUL bytes), large files (>100MB), non-ASCII content (CJK, emoji). Verify atomic write via temp+rename pattern.

8. **For index changes**: Preserve `TGI\x00` magic + version byte. If format changes, bump FORMAT_VERSION and handle old-format migration (rebuild with warning, not crash). Verify query result parity with uncompressed index.

9. Run local Rust gates:
   ```powershell
   Set-Location "C:\dev\projects\tensor-grep\rust_core"
   & "C:\Users\oimir\.cargo\bin\cargo.exe" test
   & "C:\Users\oimir\.cargo\bin\cargo.exe" clippy -- -D warnings
   ```
   For CUDA features:
   ```powershell
   & "C:\Users\oimir\.cargo\bin\cargo.exe" test --features cuda
   & "C:\Users\oimir\.cargo\bin\cargo.exe" clippy --features cuda -- -D warnings
   ```

10. Run local Python gates:
   ```powershell
   uv run ruff check .
   uv run mypy src/tensor_grep
   uv run pytest -q
   ```

11. If touching a performance-sensitive path, run the relevant benchmark and confirm no regression.

## Example Handoff

```json
{
  "salientSummary": "Implemented atomic writes in apply_edits_to_file: write-to-temp (.tg_tmp_<random>) + flush + rename. Added stale-file detection with mtime check before apply. Added 5 new tests: atomic_write_success, atomic_write_cleanup_on_failure, stale_file_rejected, no_temp_files_after_success, verify_still_works_with_atomic. cargo test: 100 passed. uv run pytest -q: 510 passed.",
  "whatWasImplemented": "Changed apply_edits_to_file from std::fs::write to write-temp+rename. Added file_mtime_ns field to RewriteEdit for staleness tracking. Mtime captured during plan_file_rewrites, checked in apply_edits_to_file. On mtime mismatch: returns Err with 'file modified since plan' message. Temp files cleaned up in both success and error paths.",
  "whatWasLeftUndone": "",
  "verification": {
    "commandsRun": [
      { "command": "cargo test", "exitCode": 0, "observation": "100 passed in 0.4s" },
      { "command": "cargo clippy -- -D warnings", "exitCode": 0, "observation": "no warnings" },
      { "command": "uv run pytest -q", "exitCode": 0, "observation": "510 passed, 14 skipped" }
    ],
    "interactiveChecks": [
      { "action": "Built release binary, ran tg run --rewrite 'lambda $$$ARGS: $EXPR' 'def $F($$$ARGS): return $EXPR' bench_data", "observed": "Plan JSON includes routing_backend=AstBackend, routing_reason=ast-native" }
    ]
  },
  "tests": {
    "added": [
      { "file": "rust_core/tests/test_ast_rewrite.rs", "cases": [
        { "name": "test_atomic_write_success", "verifies": "Write-to-temp + rename produces correct file" },
        { "name": "test_atomic_write_cleanup_on_failure", "verifies": "No temp files left on write failure" },
        { "name": "test_stale_file_rejected", "verifies": "Modified file between plan and apply is rejected" },
        { "name": "test_no_temp_files_after_success", "verifies": "No .tg_tmp_* files remain after successful apply" },
        { "name": "test_verify_with_atomic_write", "verifies": "Byte-level verify still works with new write path" }
      ]}
    ]
  },
  "discoveredIssues": []
}
```

## When to Return to Orchestrator

- Benchmark degrades and you cannot isolate the regression
- ast-grep-core API changed in a breaking way
- Merge to main fails with conflicts you cannot resolve
- Rust memory safety issue cannot be resolved within safe Rust bounds
- Feature depends on Python-side changes not yet implemented
- Index format migration breaks in a way that cannot be handled with auto-rebuild
