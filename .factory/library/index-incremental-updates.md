# Index Incremental Updates

- `rust_core/src/index.rs` now persists **format version 3** (`TGI\x00` magic unchanged).
- File table entries carry a `deleted` flag so incremental updates can keep stable `file_id` values and avoid remapping unchanged postings.
- `TrigramIndex` now reconstructs an in-memory `file_trigrams` reverse map on load; incremental add/modify/delete operations use it to remove or append only affected postings.
- Verbose explicit-index rebuilds now distinguish `full rebuild` vs `incremental update` in `rust_core/src/main.rs`.
- Local 5k-file benchmark showed incremental **build-stage** time faster than full rebuild (`~0.49s` vs `~1.50s` after a single-file add), but total CLI wall time was still dominated by index load/deserialization. Future index-scaling work should measure both build-stage and end-to-end wall time.
