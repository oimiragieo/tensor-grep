# SIMD Text Search Research Summary

Key findings for workers:

## Beating ripgrep requires:
1. **Eliminate subprocess overhead** — embed grep crates or build native search
2. **Single-file parallelism** — krep proves ~1.9x over rg by chunking large files across threads
3. **Trigram index** for repeated queries — 10-100x for warm searches

## What ripgrep already does well:
- memchr crate: SIMD-accelerated literal search, O(h+n) worst-case
- Teddy algorithm (from Hyperscan) for multi-literal prefiltering
- ignore crate: parallel gitignore-aware directory walking
- Smart mmap heuristic (single large files only)

## StringZilla vs memchr:
- memchr prebuilt searcher: geometric mean 1.19x (fastest for cold search)
- StringZilla: wins on reverse search (6x), some pathological patterns
- Already used in tg for indexed/cached queries (correct usage)

## Key techniques to implement:
- **grep crates** (grep-searcher, grep-regex, grep-matcher, grep-printer, ignore): designed to be embedded, same engine as ripgrep
- **mmap + rayon chunk parallelism**: split large files, search chunks in parallel
- **memchr::memchr_iter** for newline scanning (current CpuBackend does byte-by-byte)
- **Streaming output** instead of collecting all results into Vec

## Full report: artifacts/simd_text_search_research.md
