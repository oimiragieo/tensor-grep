# Rust Optimization Patterns

These patterns were discovered and successfully applied to improve the native AST engine's performance.

## Fast CLI Output
When writing many lines to standard output in a hot path, using `println!` in a loop introduces significant overhead due to repeated acquiring and releasing of the stdout lock. Instead, lock stdout once and use a buffered writer:
```rust
use std::io::{self, Write};
let stdout = io::stdout();
let mut handle = io::BufWriter::new(stdout.lock());
// ... writing loops ...
handle.flush().unwrap();
```

## Rayon Parallel Iterators
`rayon::iter::ParallelIterator::collect()` preserves the order of the original sequential collection. If the input iterator is already sorted (e.g., a sorted list of files collected from a sequential walk), you can safely remove redundant `.sort_unstable_by()` calls on the output vector, as `collect()` guarantees the order is maintained.

## Avoiding Unnecessary Allocations
When processing data, ensure that you only allocate what is strictly needed for the current mode. For example, in read-only CLI search modes, avoid building heavy objects (like metadata for rewrites or full match environments) if only the matched string and line numbers are required. Create lightweight projection structs (e.g., `AstCliMatch`) for the hot path.
