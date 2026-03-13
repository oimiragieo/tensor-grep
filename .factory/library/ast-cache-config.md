# AST Parsed-Source Cache Configuration

The AST backend (`src/tensor_grep/backends/ast_backend.py`) uses a byte-bounded LRU cache for parsed source files.

## Environment Variable

- **`TENSOR_GREP_AST_PARSED_SOURCE_CACHE_MAX_BYTES`**: Maximum total bytes for the parsed-source cache. Defaults to `67108864` (64 MB). The cache tracks entry sizes based on raw source byte length (conservative lower bound — actual memory usage is higher due to decoded lines, AST objects, and tuple overhead).

## Cache Behavior

- Eviction: LRU (OrderedDict-based), evicts oldest entries when byte budget exceeded.
- Oversized entries: Files larger than the entire cache limit are not cached (bypass).
- Invalidation: Based on file identity signature `(dev, ino, mtime_ns, ctime_ns, size)` from `os.stat()`.
- Platform note: On Windows with certain filesystems, `st_ino` may be `0`, reducing invalidation precision for file replacements that preserve mtime and size.
