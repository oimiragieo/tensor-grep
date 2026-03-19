# Rewrite apply speed notes

- `rust_core/src/backend_ast.rs` now streams `plan_and_apply` / `plan_and_apply_batch` per file: each worker reads a file once, plans edits, validates per-file overlaps, applies from the already-loaded source, and only then contributes sorted plan output.
- `load_rewrite_source()` now uses a single full-file read for content plus one metadata lookup that also captures `planned_mtime_ns`; the old prefix read was removed.
- Atomic writes still use temp-file + rename, but `sync_all()` was intentionally removed. The code comment documents same-directory NTFS rename atomicity / journaling and notes that per-file fsync was a major Windows hot-path cost.
- A new backend unit test (`apply_edit_set_stops_after_injected_failure_and_leaves_remaining_files_unmodified`) gives future workers a deterministic mid-apply failure harness via `apply_edit_set_with_writer(..., ApplyExecution::Sequential, ...)`.
- Current benchmark snapshot on Windows after these changes: `python benchmarks/run_ast_rewrite_benchmarks.py --output artifacts/bench_ast_rewrite.json` reports `tg apply median ~= 0.960s`, `sg apply median ~= 0.635s`, `ratio ~= 1.513`. This is a clear improvement over the mission's 2.32x baseline, but still above the 1.1 gate for the follow-up `rewrite-benchmark-gate` feature.
