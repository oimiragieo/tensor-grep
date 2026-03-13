# AST Parsed-Source Cache Configuration

The AST backend (`src/tensor_grep/backends/ast_backend.py`) uses a byte-bounded LRU cache for parsed source files.

## Environment Variable

- **`TENSOR_GREP_AST_PARSED_SOURCE_CACHE_MAX_BYTES`**: Maximum total bytes for the parsed-source cache. Defaults to `67108864` (64 MB). The cache tracks entry sizes as `len(source_bytes) * 3` using a calibration multiplier (`_PARSED_SOURCE_CACHE_ENTRY_SIZE_CALIBRATION_MULTIPLIER = 3`) that accounts for decoded lines list, tree-sitter AST objects, and tuple overhead (real footprint is ~2-4x raw bytes; 3x is the calibrated midpoint).

## Cache Behavior

- Eviction: LRU (OrderedDict-based), evicts oldest entries when byte budget exceeded.
- Oversized entries: Files larger than the entire cache limit are not cached (bypass).
- Invalidation: Based on file identity signature `(dev, ino, mtime_ns, ctime_ns, size)` from `os.stat()`.
- Platform note: On Windows with certain filesystems, `st_ino` may be `0`, reducing invalidation precision for file replacements that preserve mtime and size.
