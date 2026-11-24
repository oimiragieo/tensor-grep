//! Bit-parallel Shift-Or multi-pattern literal and character-class matcher.
//!
//! Uses 64-bit bitmasks (`u64`) per pattern (supporting up to 64 positions per
//! pattern) to scan input bytes in a single pass, supporting case-sensitive and
//! case-insensitive matching, overlapping matches, and exact 1-based
//! line/column extraction.

use std::time::Instant;

/// Maximum number of pattern positions supported by a 64-bit Shift-Or mask.
pub const MAX_PATTERN_POSITIONS: usize = 64;

/// A single match produced by [`BitParallelMatcher`].
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct BitParallelMatch {
    /// Zero-based index of the pattern in the matcher that produced this match.
    pub pattern_index: usize,
    /// One-based line number where the match begins.
    pub line: usize,
    /// One-based character column on `line` where the match begins.
    pub column: usize,
    /// Inclusive start byte offset in the searched text.
    pub start: usize,
    /// Exclusive end byte offset in the searched text.
    pub end: usize,
    /// Exact matched substring.
    pub matched_text: String,
}

#[derive(Debug, Clone)]
struct CompiledPattern {
    /// Shift-Or bitmask table indexed by byte value (`0` bit = position matches).
    masks: [u64; 256],
    /// Number of positions (`1..=64`) in this pattern.
    len: usize,
    /// Bit mask with bit `len - 1` set to `1`.
    accept_bit: u64,
}

impl CompiledPattern {
    fn compile(pattern: &str, case_insensitive: bool, literal_only: bool) -> Result<Self, String> {
        let positions = if literal_only {
            parse_literal_positions(pattern)?
        } else {
            parse_pattern_positions(pattern, case_insensitive)?
        };
        if positions.is_empty() {
            return Err("bit-parallel pattern must not be empty".to_string());
        }
        if positions.len() > MAX_PATTERN_POSITIONS {
            return Err(format!(
                "bit-parallel pattern exceeds {MAX_PATTERN_POSITIONS} positions (got {})",
                positions.len()
            ));
        }

        let mut masks = [!0u64; 256];
        for (pos_idx, allowed) in positions.iter().enumerate() {
            let clear_mask = !(1u64 << pos_idx);
            for (byte_val, slot) in masks.iter_mut().enumerate() {
                let b = byte_val as u8;
                let matches = if case_insensitive && b.is_ascii_alphabetic() {
                    allowed[b.to_ascii_lowercase() as usize]
                        || allowed[b.to_ascii_uppercase() as usize]
                } else {
                    allowed[b as usize]
                };
                if matches {
                    *slot &= clear_mask;
                }
            }
        }

        let len = positions.len();
        let accept_bit = 1u64 << (len - 1);
        Ok(Self {
            masks,
            len,
            accept_bit,
        })
    }
}

fn parse_literal_positions(pattern: &str) -> Result<Vec<[bool; 256]>, String> {
    let mut positions = Vec::with_capacity(pattern.len());
    for &b in pattern.as_bytes() {
        let mut allowed = [false; 256];
        allowed[b as usize] = true;
        positions.push(allowed);
    }
    Ok(positions)
}

fn parse_pattern_positions(
    pattern: &str,
    case_insensitive: bool,
) -> Result<Vec<[bool; 256]>, String> {
    let bytes = pattern.as_bytes();
    let mut i = 0;
    let mut positions = Vec::new();

    while i < bytes.len() {
        let mut allowed = [false; 256];
        match bytes[i] {
            b'.' => {
                for (idx, slot) in allowed.iter_mut().enumerate() {
                    *slot = idx as u8 != b'\n';
                }
                i += 1;
            }
            b'\\' => {
                i += 1;
                if i >= bytes.len() {
                    return Err("trailing backslash in pattern".to_string());
                }
                apply_escape(bytes[i], &mut allowed);
                i += 1;
            }
            b'[' => {
                i += 1;
                let negated = if i < bytes.len() && bytes[i] == b'^' {
                    i += 1;
                    true
                } else {
                    false
                };
                let mut closed = false;
                let mut first = true;
                while i < bytes.len() {
                    if bytes[i] == b']' && !first {
                        closed = true;
                        i += 1;
                        break;
                    }
                    first = false;
                    let start_byte = if bytes[i] == b'\\' {
                        i += 1;
                        if i >= bytes.len() {
                            return Err("unterminated escape in character class".to_string());
                        }
                        if matches!(bytes[i], b'd' | b'w' | b's' | b'D' | b'W' | b'S') {
                            apply_escape(bytes[i], &mut allowed);
                            i += 1;
                            continue;
                        }
                        decode_escaped_literal(bytes[i])
                    } else {
                        bytes[i]
                    };

                    if i + 2 < bytes.len() && bytes[i + 1] == b'-' && bytes[i + 2] != b']' {
                        let end_byte = if bytes[i + 2] == b'\\' {
                            if i + 3 >= bytes.len() {
                                return Err("unterminated range escape in character class".into());
                            }
                            let b = decode_escaped_literal(bytes[i + 3]);
                            i += 4;
                            b
                        } else {
                            let b = bytes[i + 2];
                            i += 3;
                            b
                        };
                        if start_byte > end_byte {
                            return Err("invalid character range in character class".to_string());
                        }
                        for b in start_byte..=end_byte {
                            allowed[b as usize] = true;
                        }
                    } else {
                        allowed[start_byte as usize] = true;
                        i += 1;
                    }
                }
                if !closed {
                    return Err("unclosed character class '[' in pattern".to_string());
                }
                if case_insensitive {
                    for lower in b'a'..=b'z' {
                        let upper = lower.to_ascii_uppercase();
                        let either = allowed[lower as usize] || allowed[upper as usize];
                        allowed[lower as usize] = either;
                        allowed[upper as usize] = either;
                    }
                }
                if negated {
                    for slot in allowed.iter_mut() {
                        *slot = !*slot;
                    }
                }
            }
            other => {
                allowed[other as usize] = true;
                i += 1;
            }
        }
        positions.push(allowed);
    }

    Ok(positions)
}

fn decode_escaped_literal(b: u8) -> u8 {
    match b {
        b'n' => b'\n',
        b'r' => b'\r',
        b't' => b'\t',
        other => other,
    }
}

fn apply_escape(b: u8, allowed: &mut [bool; 256]) {
    match b {
        b'd' => {
            for c in b'0'..=b'9' {
                allowed[c as usize] = true;
            }
        }
        b'D' => {
            for (idx, slot) in allowed.iter_mut().enumerate() {
                if !(idx as u8).is_ascii_digit() {
                    *slot = true;
                }
            }
        }
        b'w' => {
            for c in b'0'..=b'9' {
                allowed[c as usize] = true;
            }
            for c in b'a'..=b'z' {
                allowed[c as usize] = true;
            }
            for c in b'A'..=b'Z' {
                allowed[c as usize] = true;
            }
            allowed[b'_' as usize] = true;
        }
        b'W' => {
            for (idx, slot) in allowed.iter_mut().enumerate() {
                let c = idx as u8;
                if !(c.is_ascii_alphanumeric() || c == b'_') {
                    *slot = true;
                }
            }
        }
        b's' => {
            for &c in b" \t\n\r\x0c\x0b" {
                allowed[c as usize] = true;
            }
        }
        b'S' => {
            for (idx, slot) in allowed.iter_mut().enumerate() {
                if !matches!(idx as u8, b' ' | b'\t' | b'\n' | b'\r' | 0x0c | 0x0b) {
                    *slot = true;
                }
            }
        }
        other => {
            allowed[decode_escaped_literal(other) as usize] = true;
        }
    }
}

/// Multi-pattern Shift-Or bit-parallel matcher (up to 64 positions per pattern).
#[derive(Debug, Clone)]
pub struct BitParallelMatcher {
    patterns: Vec<CompiledPattern>,
    case_insensitive: bool,
}

impl BitParallelMatcher {
    /// Compile a slice of literal / character-class patterns.
    ///
    /// Supports `.`, `\d`, `\w`, `\s`, `\D`, `\W`, `\S`, `[a-z]`, `[^...]`,
    /// and backslash escapes, up to [`MAX_PATTERN_POSITIONS`] (64) positions per
    /// pattern.
    pub fn new(patterns: &[&str], case_insensitive: bool) -> Result<Self, String> {
        let mut compiled = Vec::with_capacity(patterns.len());
        for &pat in patterns {
            compiled.push(CompiledPattern::compile(pat, case_insensitive, false)?);
        }
        Ok(Self {
            patterns: compiled,
            case_insensitive,
        })
    }

    /// Compile a slice of pure literal patterns (no regex metacharacters).
    pub fn from_literals(patterns: &[&str], case_insensitive: bool) -> Result<Self, String> {
        let mut compiled = Vec::with_capacity(patterns.len());
        for &pat in patterns {
            compiled.push(CompiledPattern::compile(pat, case_insensitive, true)?);
        }
        Ok(Self {
            patterns: compiled,
            case_insensitive,
        })
    }

    /// Number of compiled patterns.
    pub fn pattern_count(&self) -> usize {
        self.patterns.len()
    }

    /// Returns `true` if no patterns are compiled.
    pub fn is_empty(&self) -> bool {
        self.patterns.is_empty()
    }

    /// Returns `true` if compiled in case-insensitive mode.
    pub fn is_case_insensitive(&self) -> bool {
        self.case_insensitive
    }

    /// Return the sorted, deduplicated indices of patterns that match anywhere in `text`.
    pub fn matching_patterns(&self, text: &str) -> Vec<usize> {
        if self.patterns.is_empty() || text.is_empty() {
            return Vec::new();
        }
        let bytes = text.as_bytes();
        let mut states = vec![!0u64; self.patterns.len()];
        let mut matched = vec![false; self.patterns.len()];

        for (byte_idx, &b) in bytes.iter().enumerate() {
            let idx = b as usize;
            for (pat_idx, pat) in self.patterns.iter().enumerate() {
                if matched[pat_idx] {
                    continue;
                }
                let next_state = (states[pat_idx] << 1) | pat.masks[idx];
                states[pat_idx] = next_state;
                if (next_state & pat.accept_bit) == 0 {
                    let end = byte_idx + 1;
                    let start = end - pat.len;
                    if text.is_char_boundary(start) && text.is_char_boundary(end) {
                        matched[pat_idx] = true;
                    }
                }
            }
        }

        matched
            .into_iter()
            .enumerate()
            .filter_map(|(idx, hit)| if hit { Some(idx) } else { None })
            .collect()
    }

    /// Search `text` and return all matches (including overlapping matches) with
    /// 1-based line and column coordinates.
    pub fn search(&self, text: &str) -> Vec<BitParallelMatch> {
        self.search_impl(text, true)
    }

    /// Search `text` and return per-pattern non-overlapping matches (matching
    /// `regex::Regex::find_iter` semantics per pattern) with 1-based line and
    /// column coordinates.
    pub fn search_non_overlapping(&self, text: &str) -> Vec<BitParallelMatch> {
        self.search_impl(text, false)
    }

    fn search_impl(&self, text: &str, allow_same_pattern_overlap: bool) -> Vec<BitParallelMatch> {
        if self.patterns.is_empty() || text.is_empty() {
            return Vec::new();
        }

        let bytes = text.as_bytes();
        let mut line_starts = vec![0usize];
        for nl_pos in memchr::memchr_iter(b'\n', bytes) {
            line_starts.push(nl_pos + 1);
        }

        let mut states = vec![!0u64; self.patterns.len()];
        let mut next_allowed_start = vec![0usize; self.patterns.len()];
        let mut matches = Vec::new();

        for (byte_idx, &b) in bytes.iter().enumerate() {
            let b_idx = b as usize;
            for (pat_idx, pat) in self.patterns.iter().enumerate() {
                let next_state = (states[pat_idx] << 1) | pat.masks[b_idx];
                states[pat_idx] = next_state;
                if (next_state & pat.accept_bit) == 0 {
                    let end = byte_idx + 1;
                    let start = end - pat.len;
                    if !allow_same_pattern_overlap && start < next_allowed_start[pat_idx] {
                        continue;
                    }
                    if !text.is_char_boundary(start) || !text.is_char_boundary(end) {
                        continue;
                    }
                    if !allow_same_pattern_overlap {
                        next_allowed_start[pat_idx] = end;
                    }
                    let line_idx = line_starts.partition_point(|&ls| ls <= start) - 1;
                    let line_start = line_starts[line_idx];
                    let column = text[line_start..start].chars().count() + 1;
                    matches.push(BitParallelMatch {
                        pattern_index: pat_idx,
                        line: line_idx + 1,
                        column,
                        start,
                        end,
                        matched_text: text[start..end].to_string(),
                    });
                }
            }
        }

        matches.sort_by_key(|m| (m.start, m.pattern_index, m.end));
        matches
    }
}

/// Run a quick micro-benchmark sanity check and return `(elapsed_nanos, match_count)`.
pub fn benchmark_matcher_sanity(
    matcher: &BitParallelMatcher,
    text: &str,
    iters: usize,
) -> (u128, usize) {
    let start = Instant::now();
    let mut total_matches = 0usize;
    for _ in 0..iters {
        total_matches += matcher.search(text).len();
    }
    (start.elapsed().as_nanos(), total_matches)
}

#[cfg(test)]
mod tests {
    use super::*;
    use regex::{RegexBuilder, RegexSetBuilder};
    use std::collections::BTreeSet;

    #[test]
    fn parity_against_regex_set_multi_pattern_and_char_classes() {
        let patterns = ["err[0-9][0-9]", "WARN_[A-Z][a-z]", "ok\\d\\d", "foo.bar"];
        let corpus = "\
line 1: ok12 and fooXbar
line 2: err07 WARN_Ab
line 3: foo\nbar should not match dot, err99 does
line 4: ok00 WARN_Zy foo-bar
";
        let matcher = BitParallelMatcher::new(&patterns, false).expect("valid patterns");
        let regex_set = RegexSetBuilder::new(patterns)
            .build()
            .expect("valid regex set");

        assert_eq!(
            matcher.matching_patterns(corpus),
            regex_set.matches(corpus).into_iter().collect::<Vec<_>>()
        );

        // Verify exact match-set parity per pattern against Regex::find_iter.
        let bp_matches = matcher.search_non_overlapping(corpus);
        let bp_set: BTreeSet<(usize, usize, usize, String)> = bp_matches
            .into_iter()
            .map(|m| (m.pattern_index, m.start, m.end, m.matched_text))
            .collect();

        let mut re_set = BTreeSet::new();
        for (idx, pat) in patterns.iter().enumerate() {
            let re = RegexBuilder::new(pat).build().expect("valid regex");
            for m in re.find_iter(corpus) {
                re_set.insert((idx, m.start(), m.end(), m.as_str().to_string()));
            }
        }
        assert_eq!(bp_set, re_set);
    }

    #[test]
    fn parity_case_insensitive_mode() {
        let patterns = ["error", "WaRn[0-9]", "tEsT_\\w"];
        let corpus = "ERROR warn5 Test_A\nerror WARN9 test_z\n";
        let matcher = BitParallelMatcher::new(&patterns, true).expect("valid patterns");
        let regex_set = RegexSetBuilder::new(patterns)
            .case_insensitive(true)
            .build()
            .expect("valid regex set");

        assert_eq!(
            matcher.matching_patterns(corpus),
            regex_set.matches(corpus).into_iter().collect::<Vec<_>>()
        );

        let bp_set: BTreeSet<(usize, usize, usize)> = matcher
            .search_non_overlapping(corpus)
            .into_iter()
            .map(|m| (m.pattern_index, m.start, m.end))
            .collect();
        let mut re_set = BTreeSet::new();
        for (idx, pat) in patterns.iter().enumerate() {
            let re = RegexBuilder::new(pat)
                .case_insensitive(true)
                .build()
                .expect("valid regex");
            for m in re.find_iter(corpus) {
                re_set.insert((idx, m.start(), m.end()));
            }
        }
        assert_eq!(bp_set, re_set);
    }

    #[test]
    fn handles_overlapping_matches_and_exact_line_columns() {
        let matcher = BitParallelMatcher::from_literals(&["aa", "aba"], false).expect("valid");
        let text = "aaa\nababa\n";
        let all = matcher.search(text);

        assert_eq!(
            all,
            vec![
                BitParallelMatch {
                    pattern_index: 0,
                    line: 1,
                    column: 1,
                    start: 0,
                    end: 2,
                    matched_text: "aa".to_string(),
                },
                BitParallelMatch {
                    pattern_index: 0,
                    line: 1,
                    column: 2,
                    start: 1,
                    end: 3,
                    matched_text: "aa".to_string(),
                },
                BitParallelMatch {
                    pattern_index: 1,
                    line: 2,
                    column: 1,
                    start: 4,
                    end: 7,
                    matched_text: "aba".to_string(),
                },
                BitParallelMatch {
                    pattern_index: 1,
                    line: 2,
                    column: 3,
                    start: 6,
                    end: 9,
                    matched_text: "aba".to_string(),
                },
            ]
        );
    }

    #[test]
    fn handles_empty_inputs_and_pattern_limits() {
        let empty_matcher = BitParallelMatcher::new(&[], false).expect("empty matcher");
        assert!(empty_matcher.is_empty());
        assert!(empty_matcher.search("hello").is_empty());
        assert!(empty_matcher.matching_patterns("hello").is_empty());

        let matcher = BitParallelMatcher::from_literals(&["abc"], false).expect("valid");
        assert!(matcher.search("").is_empty());
        assert!(matcher.matching_patterns("").is_empty());

        let max_pat = "a".repeat(MAX_PATTERN_POSITIONS);
        let max_matcher = BitParallelMatcher::from_literals(&[&max_pat], false).expect("64-bit ok");
        assert_eq!(max_matcher.search(&max_pat).len(), 1);

        let too_long = "a".repeat(MAX_PATTERN_POSITIONS + 1);
        assert!(BitParallelMatcher::from_literals(&[&too_long], false).is_err());
        assert!(BitParallelMatcher::new(&[""], false).is_err());
    }

    #[test]
    fn micro_benchmark_sanity() {
        let patterns = ["ERROR", "WARN_", "FATAL", "panic!", "timeout"];
        let matcher = BitParallelMatcher::from_literals(&patterns, false).expect("valid");
        let chunk =
            "2026-11-24 INFO normal operation\n2026-11-24 ERROR failed\n2026-11-24 WARN_retry\n";
        let corpus = chunk.repeat(200);
        let (elapsed_ns, total_matches) = benchmark_matcher_sanity(&matcher, &corpus, 5);
        assert_eq!(total_matches, 200 * 2 * 5);
        assert!(elapsed_ns > 0);
        let ci = BitParallelMatcher::new(&["[^a]"], true).unwrap();
        assert!(ci.matching_patterns("a").is_empty());
        assert!(ci.matching_patterns("A").is_empty());
        assert_eq!(ci.matching_patterns("b"), vec![0]);

        let dot = BitParallelMatcher::new(&["."], false).unwrap();
        assert_eq!(
            dot.matching_patterns("é").is_empty(),
            dot.search("é").is_empty()
        );
    }
}
