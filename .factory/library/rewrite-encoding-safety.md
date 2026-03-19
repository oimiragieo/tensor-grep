# Rewrite encoding safety notes

- `rust_core/src/backend_ast.rs` now centralizes rewrite-file loading in `load_rewrite_source()`.
- Guard order matters: large-file skip (`> 100 * 1024 * 1024` bytes) happens before binary-prefix scanning so sparse/zero-filled oversized files still emit the large-file warning instead of being treated as binary.
- Binary detection only inspects the first 8192 bytes for `NUL` and silently skips matching files.
- UTF-8 BOM handling is explicit: AST planning strips the BOM before parsing, then shifts planned byte ranges by 3 bytes so apply/verify preserve exactly one BOM on disk.
- `ensure_valid_utf8_range()` now validates rewrite byte ranges against UTF-8 boundaries before slicing; reuse this helper for future batch-rewrite paths.
