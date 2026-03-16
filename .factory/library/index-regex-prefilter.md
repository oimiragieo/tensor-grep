# Index Regex Prefilter Strategy

The trigram index acceleration path in `rust_core/src/index.rs` uses `regex-syntax` HIR-based literal extraction to decide whether a regex pattern can be safely prefiltered via the index.

## Safe patterns (index-accelerated)
- Fixed strings >= 3 bytes: exact trigram lookup
- Alternation `(foo|bar)`: union of literal sets from each branch (up to 64 literals)
- Small character classes `[abc]def`: expansion of all combinations (class size <= 10)
- Unicode multi-byte literals (case-sensitive): byte-level trigram matching

## Unsafe patterns (fall back to full scan)
- Non-ASCII patterns with case-insensitive flag (can't safely lowercase non-ASCII bytes)
- Character classes with > 10 elements
- More than 64 total prefilter literals
- Patterns with no extractable literal >= 3 bytes (e.g., `.*`, `.+`, `\d+`)

## Key constants
- `MAX_REGEX_CLASS_LITERALS = 10` — max character class expansion
- `MAX_REGEX_PREFILTER_LITERALS = 64` — max total prefilter candidates

## FullScan fallback
When no safe literal set exists, the index `search()` falls back to `FullScan`: it iterates all non-deleted indexed files and applies the regex matcher directly. This prevents false negatives but may be slow on large corpora.

Note: the FullScan path only covers files already in the index (those < 10MB that passed the ignore walker). Files > 10MB or ignored files are missed compared to non-indexed rg passthrough.
