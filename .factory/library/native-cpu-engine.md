# Native CPU Engine

## grep crates (from ripgrep ecosystem)
- `grep-searcher`: High-level search orchestration. Handles mmap/read strategy, binary detection, encoding, line counting.
- `grep-regex`: Compiles regex patterns into the `Matcher` trait from `grep-matcher`.
- `grep-matcher`: The `Matcher` trait that `grep-searcher` uses. `grep-regex` provides `RegexMatcher`.
- `grep-printer`: Formats output (standard, JSON, summary/count modes). Handles line numbers, filenames, context lines.
- `grep-cli`: CLI utilities (terminal detection, decompression).
- `ignore`: .gitignore-aware parallel directory walking. `WalkParallel` for concurrent traversal.

## Key integration patterns
```rust
use grep_regex::RegexMatcher;
use grep_searcher::Searcher;
use grep_printer::Standard;

let matcher = RegexMatcher::new("pattern")?;
let mut searcher = Searcher::new();
let mut printer = Standard::new(std::io::stdout());
searcher.search_path(&matcher, "path/to/file", printer.sink(&matcher))?;
```

## Chunk parallelism approach (from krep research)
- For files >= 50MB, split into N chunks (N = num_cpus) aligned to newline boundaries
- Each chunk searched independently via rayon
- Overlap region at boundaries (pattern_length bytes) to avoid missed matches
- Results merged with global line number adjustment

## Performance targets
- Cold search: within 5% of rg (eliminating process spawn overhead is the main win)
- Large file: faster than rg (chunk parallelism gives 1.5-2x)
- Many files: within 5% of rg (ignore::WalkParallel matches rg's walker)

## Implementation notes (2026-03-16)
- `rust_core/src/native_search.rs` now embeds `grep-searcher`, `grep-regex`, `grep-matcher`, `grep-printer`, and `grep-cli` behind a public `run_native_search(NativeSearchConfig)` entrypoint.
- For temp directories or non-repo roots, `ignore::WalkBuilder` needed explicit `add_ignore` calls for `.gitignore` / `.ignore` / `.rgignore` to make fixture-level ignore behavior deterministic in tests.
- Current native NDJSON mode emits raw `grep-printer` JSON-lines messages (`begin`/`match`/`end`), while JSON mode aggregates parsed matches into one document for testability; future routing work should normalize this to the CLI contract when wiring `main.rs`.
- Large files now use newline-aligned mmap chunking when `NativeSearchConfig.parallel_large_files` is enabled and file size is at least `large_file_chunk_threshold_bytes` (default 50 MiB). Chunk count defaults to `available_parallelism()` and can be overridden in tests with `chunk_parallelism_threads`.
- Chunk parallelism currently stays on the fast path only for plain line-oriented searches (no context lines, `only_matching`, `max_count`, or `null_data`). Those modes still fall back to the sequential grep-searcher path to preserve semantics.
- The benchmark-style Rust test `test_native_search_large_file_chunk_parallelism_is_faster_than_sequential` printed `parallel_median=281.7809ms` vs `sequential_median=545.6395ms` on the local 100 MiB fixed-string fixture during this worker run.
