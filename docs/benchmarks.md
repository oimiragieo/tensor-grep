# Benchmarks

`tensor-grep` is designed to be the fastest log parsing tool available, especially for complex classification tasks.

## Semantic Classification vs Multi-Pass Ripgrep

When checking a log file for multiple distinct error conditions or semantic categories, traditional tools like `ripgrep` require multiple passes or complex, slow regexes. 

`tensor-grep` leverages cyBERT to classify lines in a single pass.

**Test:** 10,000 line mixed log file, 6 different pattern classifications.

| Tool | Time | Passes Required |
|------|------|-----------------|
| `ripgrep` | 0.607s | 6 |
| `tensor-grep` | **0.199s** | 1 |

*Result: `tensor-grep` is ~3x faster for complex semantic parsing.*
