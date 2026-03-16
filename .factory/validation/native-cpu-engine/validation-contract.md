# Validation Contract: Native CPU Search Engine

**Milestone:** native-cpu-engine
**Status:** draft
**Author:** factory-droid
**Created:** 2026-03-16

## Overview

This milestone replaces the current ripgrep (rg) subprocess delegation for cold text search with an embedded native CPU search engine using grep crates (`grep-searcher`, `grep-regex`, `grep-matcher`, `ignore`). The native engine provides SIMD newline scanning, parallel file walk, streaming output, and single-file chunk parallelism for large files while maintaining text-semantics parity with rg.

---

## Assertions

### Basic Search — Literal & Regex

#### VAL-CPU-001: Cold literal search on a single file returns correct matches
- **Behavior:** `tg search "ERROR" <single-file>` using the native CPU engine returns all lines containing the literal "ERROR", with correct line numbers and file paths.
- **Pass condition:** Output matches are identical to `rg "ERROR" <single-file>` (same lines, same line numbers, same order).
- **Evidence:** Side-by-side diff of `tg` vs `rg` output on a sample log file (≥100 lines with known ERROR count). Exit code 0 when matches exist.

#### VAL-CPU-002: Cold literal search across multiple files returns correct matches
- **Behavior:** `tg search "ERROR" <directory-with-10+-files>` using the native CPU engine returns matches from all files that contain "ERROR".
- **Pass condition:** Set of matched files and per-file match counts are identical to `rg "ERROR" <same-directory>`. File paths appear in output.
- **Evidence:** Diff of sorted outputs from `tg` and `rg`. Both tools report the same total match count.

#### VAL-CPU-003: Regex search returns correct matches
- **Behavior:** `tg search "ERROR.*timeout|WARN\s+\d{3}" <directory>` using the native CPU engine returns all regex-matching lines.
- **Pass condition:** Output matches are identical to `rg "ERROR.*timeout|WARN\s+\d{3}" <directory>` (same lines, same line numbers).
- **Evidence:** Side-by-side diff on a corpus with known regex match counts. Test at least 3 different regex patterns: alternation, character classes, quantifiers.

#### VAL-CPU-004: Case-insensitive search works correctly
- **Behavior:** `tg search -i "error" <file>` using the native CPU engine matches "ERROR", "error", "ErRoR", and all case variants.
- **Pass condition:** Match count and matched lines identical to `rg -i "error" <file>`.
- **Evidence:** Test file contains at least 5 different case variants. Output diff is empty.

#### VAL-CPU-005: Fixed-string search treats metacharacters as literals
- **Behavior:** `tg search -F "file[0].log" <file>` matches the literal string `file[0].log`, not a regex.
- **Pass condition:** Only lines containing the exact literal `file[0].log` are returned. Lines matching `file` followed by any single character and `.log` (regex interpretation) are NOT returned.
- **Evidence:** Test file with both `file[0].log` and `fileX.log` lines. Only the literal match appears.

---

### Large File & Many-File Search

#### VAL-CPU-006: Large file search (100MB+) completes correctly
- **Behavior:** `tg search "PATTERN" <100MB+-file>` using the native CPU engine returns all matches without truncation, crash, or memory exhaustion.
- **Pass condition:** Total match count equals `rg "PATTERN" <same-file>` count. No OOM errors. Process completes with exit code 0.
- **Evidence:** Generate a 100MB+ file with known pattern frequency. Compare `tg --count` vs `rg -c` output.

#### VAL-CPU-007: Single-file chunk parallelism activates for large files
- **Behavior:** When searching a 100MB+ file, the native CPU engine splits the file into chunks and processes them in parallel.
- **Pass condition:** With `--debug` or verbose logging, evidence shows multiple chunks being processed. Match results are identical to sequential processing (no missed matches at chunk boundaries, no duplicates).
- **Evidence:** Run with debug output. Verify chunk boundary handling by placing a known match at exact chunk-size boundaries.

#### VAL-CPU-008: Many-file directory search (1000+ files) completes correctly
- **Behavior:** `tg search "PATTERN" <directory-with-1000+-files>` returns correct results for all matching files.
- **Pass condition:** Matched file count and per-file match counts are identical to `rg "PATTERN" <same-directory>`.
- **Evidence:** Generate a directory with 1000+ small files (1KB–10KB each) with known pattern distribution. Compare `tg -c` vs `rg -c` sorted output.

#### VAL-CPU-009: Parallel file walk does not produce duplicate or missing results
- **Behavior:** The parallel file walker visits each file exactly once during a directory search.
- **Pass condition:** Number of unique files searched equals number of files in directory (respecting ignore rules). No file is matched twice. No file is silently skipped.
- **Evidence:** Compare `tg --files` output against a known file listing of the directory. Run 5 times to check for non-deterministic issues.

---

### Output Format & Flags

#### VAL-CPU-010: Line numbers are correct and 1-based
- **Behavior:** `tg search -n "PATTERN" <file>` outputs 1-based line numbers for each match.
- **Pass condition:** Line numbers in output match `rg -n "PATTERN" <file>` exactly.
- **Evidence:** Test file with known match at lines 1, 50, and last line. All line numbers correct.

#### VAL-CPU-011: Filenames appear in multi-file output
- **Behavior:** When searching multiple files, each match line is prefixed with the file path.
- **Pass condition:** Output format matches `rg` default: `<filepath>:<line_number>:<text>`.
- **Evidence:** Search across 3+ files and verify every match has a file path prefix.

#### VAL-CPU-012: `--count` flag returns per-file match counts
- **Behavior:** `tg search -c "PATTERN" <directory>` outputs `<filepath>:<count>` per file with matches.
- **Pass condition:** Output is identical to `rg -c "PATTERN" <directory>`.
- **Evidence:** Diff of sorted `tg -c` vs `rg -c` output on a multi-file corpus.

#### VAL-CPU-013: `--json` output with native engine produces valid JSON
- **Behavior:** `tg search --json "PATTERN" <file>` using the native CPU engine emits valid JSON with the v1 contract schema (version, routing_backend, routing_reason, total_matches, matches).
- **Pass condition:** Output parses as valid JSON. Contains required v1 fields. `routing_backend` indicates the native CPU engine (not RipgrepBackend). Match data is correct.
- **Evidence:** Parse output with `ConvertFrom-Json` (PowerShell) or `json.loads`. Verify schema fields.

#### VAL-CPU-014: `--ndjson` output with native engine produces valid NDJSON
- **Behavior:** `tg search --ndjson "PATTERN" <file>` emits one valid JSON object per matching line.
- **Pass condition:** Each line of output independently parses as valid JSON. Each object contains file, line, text fields. Total line count equals match count.
- **Evidence:** Parse each line independently. Confirm no wrapping array. Confirm line count matches.

#### VAL-CPU-015: Context lines (`-A`, `-B`, `-C`) are correct
- **Behavior:** `tg search -C 2 "PATTERN" <file>` shows 2 lines before and 2 lines after each match.
- **Pass condition:** Context output is identical to `rg -C 2 "PATTERN" <file>` including context separators (`--`).
- **Evidence:** Diff of `tg -C 2` vs `rg -C 2` output. Also test `-A 3` and `-B 1` independently.

---

### Routing & Force-CPU Flag

#### VAL-CPU-016: `--force-cpu` (or `--cpu`) selects native CPU engine
- **Behavior:** `tg search --cpu "PATTERN" <file>` routes to the native CPU engine even when rg is available.
- **Pass condition:** With `--debug`, routing log shows the native CPU backend selected (not RipgrepBackend). Results are correct.
- **Evidence:** Debug output shows `routing.backend=CpuBackend` or equivalent native indicator, not `RipgrepBackend`.

#### VAL-CPU-017: Without `--force-cpu`, default routing still selects appropriate backend
- **Behavior:** When rg is available, `tg search "PATTERN" <file>` may still route to rg for passthrough. When rg is unavailable, the native CPU engine handles the search.
- **Pass condition:** Search completes successfully regardless of rg availability. Routing reason is logged correctly.
- **Evidence:** Test with rg on PATH (expect passthrough or native), then rename/remove rg from PATH and re-test (expect native CPU engine). Both produce correct results.

#### VAL-CPU-018: Explicit rg fallback still works when requested
- **Behavior:** An explicit mechanism to force rg delegation (if one exists) still works after the native engine is added.
- **Pass condition:** When rg is available and explicitly requested, the RipgrepBackend handles the search and produces correct output.
- **Evidence:** Routing log shows RipgrepBackend selected. Output matches direct `rg` invocation.

---

### Filter & Ignore Rules

#### VAL-CPU-019: `.gitignore` rules are respected
- **Behavior:** Files matched by `.gitignore` patterns are excluded from search results by default.
- **Pass condition:** A file listed in `.gitignore` (e.g., `*.log` or `build/`) does not appear in search results. Adding `--no-ignore` includes it.
- **Evidence:** Create a test directory with `.gitignore` excluding `secret.txt`. Search for a pattern in `secret.txt`. Confirm 0 matches without `--no-ignore`, correct matches with `--no-ignore`.

#### VAL-CPU-020: `.rgignore` and `.ignore` files are respected
- **Behavior:** The native engine honors `.rgignore` and `.ignore` files in addition to `.gitignore`.
- **Pass condition:** Files matching patterns in `.rgignore` are excluded. `--no-ignore-dot` overrides.
- **Evidence:** Create `.rgignore` with `*.tmp`, create `test.tmp` with matches. Verify 0 results by default, correct results with `--no-ignore-dot`.

#### VAL-CPU-021: Hidden files are excluded by default
- **Behavior:** Files and directories starting with `.` are not searched by default.
- **Pass condition:** `.hidden_file` is not in results. `--hidden` flag includes it.
- **Evidence:** Create `.hidden_log` with pattern match. No match without `--hidden`, match present with `--hidden`.

#### VAL-CPU-022: `--glob` filtering works correctly
- **Behavior:** `tg search -g "*.py" "PATTERN" <dir>` restricts search to Python files only.
- **Pass condition:** Only `.py` files appear in results, even if `.js`, `.txt`, etc. also contain the pattern.
- **Evidence:** Create mixed-extension files. Verify only `.py` matches appear.

#### VAL-CPU-023: `--max-depth` limits directory traversal
- **Behavior:** `tg search --max-depth 1 "PATTERN" <dir>` searches only the top-level directory.
- **Pass condition:** Files in subdirectories are not searched. Only files at depth ≤ 1 appear in results.
- **Evidence:** Create `dir/file.txt` and `dir/sub/deep.txt` both with pattern. Only `dir/file.txt` matches with `--max-depth 1`.

---

### Binary File & Encoding Handling

#### VAL-CPU-024: Binary files are skipped by default with appropriate message
- **Behavior:** When a file contains NUL bytes, the native engine skips it by default (or shows a "binary file matches" warning).
- **Pass condition:** Behavior matches rg: either silently skip or print a notice. No crash. `--text` or `-a` flag forces search into binary content.
- **Evidence:** Create a file with embedded NUL bytes and a pattern. Default search shows warning or skips. `--text` flag shows the match.

#### VAL-CPU-025: UTF-8 files are searched correctly
- **Behavior:** Files with valid UTF-8 content (including multi-byte characters like emoji, CJK, accented letters) are searched correctly.
- **Pass condition:** Matches on UTF-8 content are correct. Line numbers account for multi-byte characters properly.
- **Evidence:** Test file with `café`, `日本語`, `🔍` on known lines. Search for each and verify correct line numbers.

#### VAL-CPU-026: Non-UTF-8 files (Latin-1, Windows-1252) are handled gracefully
- **Behavior:** Files with non-UTF-8 encoding (e.g., Latin-1 `0xe9` for é) are searchable without crashes.
- **Pass condition:** Search completes. ASCII portions of the file are still matchable. Behavior is consistent with rg (which defaults to BOM sniffing then binary detection).
- **Evidence:** Create a Latin-1 encoded file with ASCII pattern. Verify the ASCII match is found.

---

### Performance

#### VAL-CPU-027: Cold single-query search within 5% of rg
- **Behavior:** For cold text search (no cache, typical corpus), the native CPU engine is within 5% of rg's wall-clock time.
- **Pass condition:** `median(tg_time) <= median(rg_time) * 1.05` over 10+ runs on the standard benchmark corpus.
- **Evidence:** Run `benchmarks/run_benchmarks.py` comparing native CPU engine vs rg. Report median times and ratio. Cold cache (drop filesystem caches between runs if possible).

#### VAL-CPU-028: Large file search faster than rg (single-file chunk parallelism)
- **Behavior:** For files ≥100MB, the native engine's chunk parallelism provides a speedup over rg's single-threaded-per-file approach.
- **Pass condition:** `median(tg_time) < median(rg_time)` for a 100MB+ file with moderate match density.
- **Evidence:** Benchmark on a 200MB generated log file. Report median times and speedup factor. Minimum 5 runs each.

#### VAL-CPU-029: Many-file directory search competitive with rg
- **Behavior:** For directory search over 1000+ files, the native engine (parallel file walk) is competitive with rg.
- **Pass condition:** `median(tg_time) <= median(rg_time) * 1.05` for a 1000+ file directory.
- **Evidence:** Run benchmark on standard benchmark corpus directory. Report median times and ratio. At least 5 runs.

#### VAL-CPU-030: No performance regression in repeated-query hot path
- **Behavior:** The addition of the native CPU engine does not regress repeated-query (indexed) search performance.
- **Pass condition:** Hot query benchmark times are within 5% of baseline (pre-milestone).
- **Evidence:** Run `benchmarks/run_hot_query_benchmarks.py` and compare against accepted baseline. `check_regression.py` passes.

---

### Text Parity with rg

#### VAL-CPU-031: Identical matches for all test patterns across parity corpus
- **Behavior:** For a corpus of 20+ diverse regex patterns (literals, alternation, character classes, anchors, quantifiers, backreferences-or-error, Unicode), the native CPU engine produces the same match set as rg.
- **Pass condition:** For every pattern, `diff(sorted(tg_output), sorted(rg_output))` is empty.
- **Evidence:** Run parity test script with pattern list. Report per-pattern pass/fail. All patterns pass.

#### VAL-CPU-032: Word boundary matching (`-w`) is correct
- **Behavior:** `tg search -w "error" <file>` matches whole-word "error" only, not "errors" or "myerror".
- **Pass condition:** Output identical to `rg -w "error" <file>`.
- **Evidence:** Test file with "error", "errors", "myerror", "error-code". Only lines with standalone "error" match.

#### VAL-CPU-033: Line boundary matching (`-x`) is correct
- **Behavior:** `tg search -x "exact line" <file>` matches only lines that are exactly "exact line".
- **Pass condition:** Output identical to `rg -x "exact line" <file>`.
- **Evidence:** Test file with "exact line", "exact line extra", "prefix exact line". Only the first matches.

#### VAL-CPU-034: Invert match (`-v`) returns non-matching lines
- **Behavior:** `tg search -v "ERROR" <file>` returns all lines that do NOT contain "ERROR".
- **Pass condition:** Output identical to `rg -v "ERROR" <file>`. Match count = total lines - ERROR lines.
- **Evidence:** Test file with known line counts. Verify inverted count.

#### VAL-CPU-035: Smart-case search works correctly
- **Behavior:** `tg search -S "error" <file>` is case-insensitive (all lowercase pattern). `tg search -S "Error" <file>` is case-sensitive (mixed case pattern).
- **Pass condition:** All-lowercase pattern matches all case variants. Mixed-case pattern matches only exact case. Behavior identical to `rg -S`.
- **Evidence:** Test both patterns on a file with mixed-case "Error", "ERROR", "error" lines.

---

### Streaming Output

#### VAL-CPU-036: Results appear incrementally during search
- **Behavior:** When searching a large corpus (e.g., 1000+ files or 100MB+ file), matches begin appearing on stdout before the entire search completes.
- **Pass condition:** First output byte arrives before total search wall time. Measure time-to-first-byte vs total search time; TTFB < 50% of total time.
- **Evidence:** Pipe `tg search` output through a timestamp logger. Record time of first line vs time of last line. TTFB significantly less than total.

#### VAL-CPU-037: Streaming output does not interleave partial lines
- **Behavior:** Each output line is complete (not split across writes). No garbled output even under parallel file walk.
- **Pass condition:** Every output line is parseable (for structured output) or follows the `file:line:text` format (for default output). No partial lines.
- **Evidence:** Run search on large corpus, capture all output, verify each line matches expected format. Run 10 times to detect race conditions.

---

### Error Handling

#### VAL-CPU-038: Non-existent path produces clear error
- **Behavior:** `tg search "PATTERN" /nonexistent/path` exits with non-zero code and prints an error to stderr.
- **Pass condition:** Exit code is non-zero (1 or 2). Stderr contains a meaningful error message mentioning the path. No crash or panic.
- **Evidence:** Capture exit code and stderr. Verify error message is user-friendly.

#### VAL-CPU-039: Permission-denied file is skipped with warning
- **Behavior:** When a file in the search path is not readable, the native engine skips it and optionally warns on stderr.
- **Pass condition:** Other files in the same directory are still searched. No crash. Exit code reflects whether other matches were found.
- **Evidence:** Create an unreadable file alongside readable files. Search the directory. Verify readable files produce matches. Verify no crash.

#### VAL-CPU-040: Symlink loops do not cause infinite recursion
- **Behavior:** A directory containing symlink loops does not hang or crash the engine.
- **Pass condition:** Search completes in finite time. Symlinked directories are either followed once or skipped based on `--follow` flag. No hang, no crash.
- **Evidence:** Create a symlink loop (`a -> b`, `b -> a`). Run `tg search` on parent directory. Verify completion within reasonable timeout.

---

### Edge Cases

#### VAL-CPU-041: Empty pattern matches every line
- **Behavior:** `tg search "" <file>` matches every line in the file (same as `rg "" <file>`).
- **Pass condition:** Match count equals total line count of the file. Behavior matches rg.
- **Evidence:** Run on a file with known line count. Verify match count.

#### VAL-CPU-042: Empty file produces zero matches
- **Behavior:** `tg search "PATTERN" <empty-file>` returns zero matches.
- **Pass condition:** Exit code is 1 (no matches). No error output. No crash.
- **Evidence:** Create a 0-byte file. Run search. Verify 0 matches and exit code 1.

#### VAL-CPU-043: No-match scenario returns correct exit code
- **Behavior:** `tg search "DEFINITELY_NOT_HERE" <file>` exits with code 1 when no matches are found.
- **Pass condition:** Exit code is 1. Stdout is empty (or only headers). No error messages on stderr.
- **Evidence:** Search for a pattern guaranteed absent. Verify exit code 1.

#### VAL-CPU-044: Very long lines (>1MB) are handled correctly
- **Behavior:** A file containing a single line >1MB is searched without crash or truncation.
- **Pass condition:** If the pattern exists in the long line, it is found. No OOM. No crash.
- **Evidence:** Generate a 2MB single-line file with a known pattern at offset 1.5MB. Verify match found.

#### VAL-CPU-045: Pattern at the very first and very last byte of file
- **Behavior:** A pattern occurring at byte 0 (first line, first character) and at the final byte (last line, last character) of a file is correctly matched.
- **Pass condition:** Both matches are found. Line numbers are correct (line 1 for first, last line for last).
- **Evidence:** Create a file with pattern as first word of first line and last word of last line. Verify both matched.

#### VAL-CPU-046: File with no trailing newline is handled correctly
- **Behavior:** A file whose last line lacks a trailing `\n` still has that line searched and matched.
- **Pass condition:** The final line (without newline) is matched if it contains the pattern. Behavior identical to rg.
- **Evidence:** Create file with `echo -n "last ERROR line"`. Search for "ERROR". Verify match found.

#### VAL-CPU-047: CRLF line endings are handled correctly
- **Behavior:** Files with `\r\n` line endings are searched correctly. The `\r` is not included in match text.
- **Pass condition:** Match text does not contain trailing `\r`. Line counts match rg. `--crlf` flag behavior matches rg `--crlf`.
- **Evidence:** Create a file with CRLF endings. Verify match text is clean. Compare with rg output.

#### VAL-CPU-048: Null-data mode (`--null-data`) uses NUL as line terminator
- **Behavior:** `tg search --null-data "PATTERN" <file>` treats NUL bytes as line terminators instead of `\n`.
- **Pass condition:** Behavior matches `rg --null-data "PATTERN" <file>`.
- **Evidence:** Create a file with NUL-separated records containing the pattern. Verify correct matches.

---

### Max-Count & Output Limiting

#### VAL-CPU-049: `--max-count` limits matches per file
- **Behavior:** `tg search -m 3 "PATTERN" <file>` returns at most 3 matches from that file.
- **Pass condition:** Output contains ≤3 match lines. The first 3 matches (by line number order) are the ones returned. Behavior matches `rg -m 3`.
- **Evidence:** File with 10+ matches. Verify only first 3 appear.

#### VAL-CPU-050: `--quiet` mode produces no stdout
- **Behavior:** `tg search -q "PATTERN" <file>` produces no stdout output but sets exit code 0 if matches exist, 1 if not.
- **Pass condition:** Stdout is empty. Exit code is 0 when pattern is present, 1 when absent.
- **Evidence:** Test both cases. Verify empty stdout and correct exit codes.

#### VAL-CPU-051: `--only-matching` prints only matched text
- **Behavior:** `tg search -o "ERR\w+" <file>` prints only the matched portion, not the full line.
- **Pass condition:** Each output line contains only the regex-matched text, not the surrounding line content. Behavior matches `rg -o`.
- **Evidence:** File with "ERROR: timeout occurred". Output should be "ERROR" (or matching capture), not the full line.

---

### CI & Regression Gate

#### VAL-CPU-052: All existing tests pass after native engine integration
- **Behavior:** `uv run pytest -q` passes with no new failures after the native CPU engine is integrated.
- **Pass condition:** Test count ≥ current baseline (549+). 0 failures. No new skips directly caused by the engine change.
- **Evidence:** Full pytest output showing pass count and 0 failures.

#### VAL-CPU-053: Linting and type checking pass
- **Behavior:** `uv run ruff check .` and `uv run mypy src/tensor_grep` pass cleanly.
- **Pass condition:** Both exit with code 0. Zero errors.
- **Evidence:** Terminal output of both commands showing clean pass.

#### VAL-CPU-054: Benchmark regression check passes
- **Behavior:** `benchmarks/check_regression.py` accepts the post-integration benchmark run.
- **Pass condition:** No regression detected. Script exits with code 0.
- **Evidence:** Full regression check output.

---

## Summary Table

| ID | Area | Title | Priority |
|----|------|-------|----------|
| VAL-CPU-001 | Search/Literal | Cold literal search, single file | P0 |
| VAL-CPU-002 | Search/Literal | Cold literal search, multi-file | P0 |
| VAL-CPU-003 | Search/Regex | Regex search correctness | P0 |
| VAL-CPU-004 | Search/Case | Case-insensitive search | P0 |
| VAL-CPU-005 | Search/Fixed | Fixed-string search | P0 |
| VAL-CPU-006 | Scale/Large | Large file (100MB+) correctness | P0 |
| VAL-CPU-007 | Scale/Large | Chunk parallelism activation | P1 |
| VAL-CPU-008 | Scale/Many | Many-file (1000+) correctness | P0 |
| VAL-CPU-009 | Scale/Walk | Parallel walk deduplication | P0 |
| VAL-CPU-010 | Output/Format | Line numbers correct | P0 |
| VAL-CPU-011 | Output/Format | Filenames in output | P0 |
| VAL-CPU-012 | Output/Count | `--count` flag | P0 |
| VAL-CPU-013 | Output/JSON | `--json` output | P1 |
| VAL-CPU-014 | Output/NDJSON | `--ndjson` output | P1 |
| VAL-CPU-015 | Output/Context | Context lines (-A/-B/-C) | P0 |
| VAL-CPU-016 | Routing | `--force-cpu` selects native engine | P0 |
| VAL-CPU-017 | Routing | Default routing behavior | P0 |
| VAL-CPU-018 | Routing | rg fallback still works | P0 |
| VAL-CPU-019 | Filter/Ignore | .gitignore respected | P0 |
| VAL-CPU-020 | Filter/Ignore | .rgignore/.ignore respected | P1 |
| VAL-CPU-021 | Filter/Hidden | Hidden files excluded by default | P1 |
| VAL-CPU-022 | Filter/Glob | --glob filtering | P1 |
| VAL-CPU-023 | Filter/Depth | --max-depth | P1 |
| VAL-CPU-024 | Encoding/Binary | Binary file handling | P0 |
| VAL-CPU-025 | Encoding/UTF8 | UTF-8 correctness | P0 |
| VAL-CPU-026 | Encoding/Latin1 | Non-UTF-8 graceful handling | P1 |
| VAL-CPU-027 | Perf/Cold | Cold search within 5% of rg | P0 |
| VAL-CPU-028 | Perf/Large | Large file faster than rg | P1 |
| VAL-CPU-029 | Perf/Many | Many-file competitive with rg | P0 |
| VAL-CPU-030 | Perf/Hot | No hot-path regression | P0 |
| VAL-CPU-031 | Parity | Full pattern parity with rg | P0 |
| VAL-CPU-032 | Parity/Word | Word boundary (-w) | P0 |
| VAL-CPU-033 | Parity/Line | Line boundary (-x) | P1 |
| VAL-CPU-034 | Parity/Invert | Invert match (-v) | P0 |
| VAL-CPU-035 | Parity/SmartCase | Smart-case (-S) | P1 |
| VAL-CPU-036 | Streaming | Incremental output | P1 |
| VAL-CPU-037 | Streaming | No interleaved partial lines | P0 |
| VAL-CPU-038 | Error | Non-existent path | P0 |
| VAL-CPU-039 | Error | Permission denied | P1 |
| VAL-CPU-040 | Error | Symlink loops | P1 |
| VAL-CPU-041 | Edge | Empty pattern | P1 |
| VAL-CPU-042 | Edge | Empty file | P0 |
| VAL-CPU-043 | Edge | No-match exit code | P0 |
| VAL-CPU-044 | Edge | Very long lines (>1MB) | P1 |
| VAL-CPU-045 | Edge | Pattern at file boundaries | P1 |
| VAL-CPU-046 | Edge | No trailing newline | P0 |
| VAL-CPU-047 | Edge | CRLF line endings | P0 |
| VAL-CPU-048 | Edge | Null-data mode | P2 |
| VAL-CPU-049 | Limit | --max-count | P1 |
| VAL-CPU-050 | Limit | --quiet mode | P1 |
| VAL-CPU-051 | Limit | --only-matching | P1 |
| VAL-CPU-052 | CI | All tests pass | P0 |
| VAL-CPU-053 | CI | Lint + type check pass | P0 |
| VAL-CPU-054 | CI | Benchmark regression check | P0 |
