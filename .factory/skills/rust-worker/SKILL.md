---
name: rust-worker
description: Handles all Rust-side work: control plane, PyO3 isolation, ast-grep-core embedding, index subsystem, rewrite substrate, and benchmark gates.
---

# rust-worker

NOTE: Startup and cleanup are handled by `worker-base`. This skill defines the WORK PROCEDURE.

## When to Use This Skill

Use this worker for:
- Rust control plane changes (`rust_core/src/main.rs`, routing, JSON output)
- ast-grep-core embedding and AST backend (`backend_ast.rs`)
- Index subsystem (compression, incremental, regex) in `index.rs`
- Rewrite pipeline safety (atomic writes, stale-file detection, encoding)
- GPU sidecar error handling (`python_sidecar.rs`, `main.rs`)
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
   - Preserve all 8 key invariants listed in AGENTS.md.

4. **For JSON output changes**: Ensure all JSON outputs include the unified envelope: `version` (u32), `routing_backend` (string), `routing_reason` (string), `sidecar_used` (bool). Test by parsing output with serde_json.

5. **For rewrite safety changes**: Test with edge cases: BOM files, CRLF files, binary files (NUL bytes), large files (>100MB), non-ASCII content (CJK, emoji). Verify atomic write via temp+rename pattern.

6. **For index changes**: Preserve `TGI\x00` magic + version byte. If format changes, bump FORMAT_VERSION and handle old-format migration (rebuild with warning, not crash). Verify query result parity with uncompressed index.

7. Run local Rust gates:
   ```powershell
   Set-Location "C:\dev\projects\tensor-grep\rust_core"
   & "C:\Users\oimir\.cargo\bin\cargo.exe" test
   & "C:\Users\oimir\.cargo\bin\cargo.exe" clippy -- -D warnings
   ```

8. Run local Python gates:
   ```powershell
   uv run ruff check .
   uv run mypy src/tensor_grep
   uv run pytest -q
   ```

9. If touching a performance-sensitive path, run the relevant benchmark and confirm no regression.

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
