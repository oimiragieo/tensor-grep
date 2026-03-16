# Index Compression

- `rust_core/src/index.rs` now writes trigram posting lists in **format version 2** while preserving the `TGI\x00` magic prefix.
- Posting entries are sorted by `(file_id, line)` and encoded as **u32 varint deltas**: first entry stores absolute `file_id`/`line`, later entries store `file_id` delta and either absolute `line` (new file) or same-file `line` delta.
- The root path, file table, trigram count, trigram bytes, and per-trigram posting counts remain uncompressed fixed-width fields; only the posting pairs changed format.
- `tg search --index` now prints a warning and rebuilds when loading an old/corrupt index fails. Warm auto-routing still silently ignores incompatible cached indices and falls back to the non-index path.
- Regression coverage lives in `rust_core/src/index.rs` (round-trip + 1000-file size reduction) and `rust_core/tests/test_index.rs` (old-format rebuild + query parity).
