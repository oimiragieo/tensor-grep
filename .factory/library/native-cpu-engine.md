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
