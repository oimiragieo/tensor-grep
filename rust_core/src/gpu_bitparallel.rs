//! Bit-parallel Shift-Or multi-pattern literal and character-class matcher.
//!
//! Uses 64-bit bitmasks (`u64`) per pattern (supporting up to 64 positions per
//! pattern) to scan input bytes in a single pass, supporting case-sensitive and
//! case-insensitive matching, overlapping matches, and exact 1-based
//! line/column extraction.
//!
//! **Byte-level (ASCII) semantics:** Wildcards (`.`) and character classes
//! (`[...]`, `[^...]`, `\d`, `\w`, `\s`, `\D`, `\W`, `\S`) operate on single
//! bytes rather than multi-byte Unicode scalar values. For example, `.` matches
//! a single non-`\n` byte (and results are filtered to UTF-8 char boundaries),
//! so `.` does not match a multi-byte UTF-8 character such as `é`.

use serde::{Deserialize, Serialize};
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
    /// Whether this pattern uses single-byte wildcards (`.`) or character classes.
    has_wildcard_or_char_class: bool,
}

impl CompiledPattern {
    fn compile(pattern: &str, case_insensitive: bool, literal_only: bool) -> Result<Self, String> {
        let (positions, has_wildcard_or_char_class) = if literal_only {
            (parse_literal_positions(pattern)?, false)
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
            has_wildcard_or_char_class,
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
) -> Result<(Vec<[bool; 256]>, bool), String> {
    let bytes = pattern.as_bytes();
    let mut i = 0;
    let mut positions = Vec::new();
    let mut has_wildcard_or_char_class = false;

    while i < bytes.len() {
        let mut allowed = [false; 256];
        match bytes[i] {
            b'.' => {
                has_wildcard_or_char_class = true;
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
                if matches!(bytes[i], b'd' | b'w' | b's' | b'D' | b'W' | b'S') {
                    has_wildcard_or_char_class = true;
                }
                apply_escape(bytes[i], &mut allowed);
                i += 1;
            }
            b'[' => {
                has_wildcard_or_char_class = true;
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

    Ok((positions, has_wildcard_or_char_class))
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
    /// pattern. Wildcards (`.`) and character classes operate on single bytes
    /// (ASCII semantics), not multi-byte Unicode scalar values.
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

    /// Reference scalar matcher producing exact byte-identical matches (including
    /// overlapping matches) for parity verification against the Shift-Or path.
    pub fn search_scalar(&self, text: &str) -> Vec<BitParallelMatch> {
        self.search_scalar_impl(text, true)
    }

    /// Reference scalar matcher producing per-pattern non-overlapping matches for
    /// parity verification against [`Self::search_non_overlapping`].
    pub fn search_scalar_non_overlapping(&self, text: &str) -> Vec<BitParallelMatch> {
        self.search_scalar_impl(text, false)
    }

    fn search_scalar_impl(
        &self,
        text: &str,
        allow_same_pattern_overlap: bool,
    ) -> Vec<BitParallelMatch> {
        if self.patterns.is_empty() || text.is_empty() {
            return Vec::new();
        }

        let bytes = text.as_bytes();
        let mut line_starts = vec![0usize];
        for nl_pos in memchr::memchr_iter(b'\n', bytes) {
            line_starts.push(nl_pos + 1);
        }

        let mut next_allowed_start = vec![0usize; self.patterns.len()];
        let mut matches = Vec::new();

        for start in 0..bytes.len() {
            if !text.is_char_boundary(start) {
                continue;
            }
            for (pat_idx, pat) in self.patterns.iter().enumerate() {
                if !allow_same_pattern_overlap && start < next_allowed_start[pat_idx] {
                    continue;
                }
                let end = start + pat.len;
                if end > bytes.len() || !text.is_char_boundary(end) {
                    continue;
                }
                let matched = (0..pat.len)
                    .all(|pos| (pat.masks[bytes[start + pos] as usize] & (1u64 << pos)) == 0);
                if matched {
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

    /// Verify byte-identical match parity (`dyno-ratchet` invariant) between the
    /// bit-parallel Shift-Or matcher and the scalar reference matcher across
    /// overlapping, non-overlapping, and pattern-index modes.
    ///
    /// Because `.` and character classes use single-byte (ASCII) semantics,
    /// `parity_verified` is set to `false` when any pattern contains `.` or a
    /// character class and `text` contains non-ASCII multi-byte UTF-8, so it
    /// never claims Unicode regex equivalence for byte-level wildcards/classes.
    pub fn verify_byte_parity(&self, text: &str) -> Result<BitParallelParityReport, String> {
        let bp_all = self.search(text);
        let sc_all = self.search_scalar(text);
        if bp_all != sc_all {
            return Err(format!(
                "overlapping parity mismatch: bit-parallel produced {} matches, scalar produced {}",
                bp_all.len(),
                sc_all.len()
            ));
        }

        let bp_non = self.search_non_overlapping(text);
        let sc_non = self.search_scalar_non_overlapping(text);
        if bp_non != sc_non {
            return Err(format!(
                "non-overlapping parity mismatch: bit-parallel produced {} matches, scalar produced {}",
                bp_non.len(),
                sc_non.len()
            ));
        }

        let bp_indices = self.matching_patterns(text);
        let mut sc_indices: Vec<usize> = sc_all.iter().map(|m| m.pattern_index).collect();
        sc_indices.sort_unstable();
        sc_indices.dedup();
        if bp_indices != sc_indices {
            return Err(format!(
                "matching_patterns parity mismatch: bit-parallel={bp_indices:?}, scalar={sc_indices:?}"
            ));
        }

        let has_byte_wildcard_or_class = self.patterns.iter().any(|p| p.has_wildcard_or_char_class);
        let parity_verified = !has_byte_wildcard_or_class || text.is_ascii();

        Ok(BitParallelParityReport {
            overlapping_match_count: bp_all.len(),
            non_overlapping_match_count: bp_non.len(),
            matching_pattern_indices: bp_indices,
            byte_identical: true,
            parity_verified,
        })
    }
}

/// Summary of a byte-identical parity check between bit-parallel and scalar matchers.
#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
pub struct BitParallelParityReport {
    pub overlapping_match_count: usize,
    pub non_overlapping_match_count: usize,
    pub matching_pattern_indices: Vec<usize>,
    pub byte_identical: bool,
    pub parity_verified: bool,
}

/// Execution path used to evaluate the GPU side of crossover calibration.
#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize, Deserialize)]
#[serde(rename_all = "snake_case")]
pub enum ExecutionBackendKind {
    /// Simulated or CPU fallback path; never qualifies as native GPU promotion proof.
    CpuFallback,
    /// Native CUDA GPU kernel execution.
    NativeGpuKernel,
}

/// Honest crossover calibration report distinguishing CPU fallback / simulated runs
/// from verified native GPU kernel execution.
#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
pub struct CrossoverCalibrationReport {
    pub execution_path: ExecutionBackendKind,
    pub native_gpu_executed: bool,
    pub promotion_proof: bool,
    pub crossover_threshold_bytes: Option<u64>,
    pub parity_verified: bool,
    pub reason: String,
}

impl CrossoverCalibrationReport {
    /// Construct an honest CPU-fallback report. `promotion_proof` and
    /// `native_gpu_executed` are always `false` and `crossover_threshold_bytes` is `None`.
    pub fn cpu_fallback(parity_verified: bool, reason: impl Into<String>) -> Self {
        Self {
            execution_path: ExecutionBackendKind::CpuFallback,
            native_gpu_executed: false,
            promotion_proof: false,
            crossover_threshold_bytes: None,
            parity_verified,
            reason: reason.into(),
        }
    }

    /// Evaluate a calibration outcome into an honest [`CrossoverCalibrationReport`].
    ///
    /// `CpuFallback` never sets `promotion_proof = true`, regardless of timing inputs.
    pub fn evaluate(
        execution_path: ExecutionBackendKind,
        crossover_threshold_bytes: Option<u64>,
        parity_verified: bool,
        gpu_beats_cpu: bool,
    ) -> Self {
        match execution_path {
            ExecutionBackendKind::CpuFallback => Self::cpu_fallback(
                parity_verified,
                "CPU fallback / simulated path cannot prove native GPU promotion",
            ),
            ExecutionBackendKind::NativeGpuKernel => {
                let threshold = if gpu_beats_cpu {
                    crossover_threshold_bytes
                } else {
                    None
                };
                let promotion_proof = parity_verified && gpu_beats_cpu && threshold.is_some();
                let reason = if !parity_verified {
                    "native GPU kernel ran but byte-identical parity was not verified".to_string()
                } else if let Some(bytes) = threshold {
                    format!(
                        "native GPU kernel verified byte-identical parity and outperformed CPU at >= {bytes} bytes"
                    )
                } else {
                    "native GPU kernel did not outperform CPU at any calibrated corpus size"
                        .to_string()
                };
                Self {
                    execution_path: ExecutionBackendKind::NativeGpuKernel,
                    native_gpu_executed: true,
                    promotion_proof,
                    crossover_threshold_bytes: threshold,
                    parity_verified,
                    reason,
                }
            }
        }
    }

    /// Run CPU-fallback parity verification and return an honest report with
    /// `promotion_proof = false`.
    pub fn calibrate_cpu_fallback(corpus: &str, patterns: &[&str]) -> Result<Self, String> {
        let matcher = BitParallelMatcher::new(patterns, false)?;
        let parity = matcher.verify_byte_parity(corpus)?;
        let reason = if parity.parity_verified {
            "CPU bit-parallel fallback verified byte-identical parity; native GPU kernel not executed"
        } else {
            "CPU bit-parallel fallback matched scalar byte semantics, but Unicode regex parity is unverified for byte-level wildcards/classes on non-ASCII UTF-8; native GPU kernel not executed"
        };
        Ok(Self::cpu_fallback(parity.parity_verified, reason))
    }
}

/// Verify bit-parallel vs scalar byte parity on a built-in edge-case corpus
/// (empty lines, multi-pattern, overlapping boundaries, and UTF-8 multi-byte chars).
pub fn verify_default_bitparallel_parity() -> Result<BitParallelParityReport, String> {
    let patterns = ["aa", "aba", "ERROR", "WARN_\\d", "é"];
    let corpus = "\n\naaa\nababa\n\nERROR WARN_7 éé\n";
    let matcher = BitParallelMatcher::new(&patterns, false)?;
    matcher.verify_byte_parity(corpus)
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

    #[test]
    fn parity_scalar_vs_bitparallel_edge_cases() {
        let patterns = ["aa", "aba", "ERROR", "WARN_\\d", "é", "🚀"];
        let corpus = "\n\n\naaa\nababa\n\nERROR WARN_7 éé 🚀\nαβγ\nabaabaa\n";
        let matcher = BitParallelMatcher::new(&patterns, false).expect("valid patterns");
        let report = matcher
            .verify_byte_parity(corpus)
            .expect("bit-parallel and scalar matchers must be byte-identical");
        assert!(report.byte_identical);
        assert_eq!(report.matching_pattern_indices, vec![0, 1, 2, 3, 4, 5]);
        assert!(report.overlapping_match_count >= report.non_overlapping_match_count);

        let ci_matcher =
            BitParallelMatcher::new(&["error", "warn_\\w", "é"], true).expect("valid ci patterns");
        let ci_report = ci_matcher
            .verify_byte_parity("\nERROR\nwarn_A\né\n")
            .expect("case-insensitive parity must hold");
        assert!(ci_report.byte_identical);

        let default_report = verify_default_bitparallel_parity().expect("default parity");
        assert!(default_report.byte_identical);
        assert!(!default_report.parity_verified);
    }

    #[test]
    fn verify_byte_parity_rejects_non_ascii_wildcard_or_char_class_parity() {
        // `.` and character classes operate on single bytes (ASCII semantics), so `.`
        // does not match the 2-byte UTF-8 sequence `é`.
        let dot = BitParallelMatcher::new(&["."], false).expect("valid dot");
        assert!(dot.search("é").is_empty());
        let dot_non_ascii = dot.verify_byte_parity("é").expect("byte parity holds");
        assert!(dot_non_ascii.byte_identical);
        assert!(!dot_non_ascii.parity_verified);

        let class_matcher = BitParallelMatcher::new(&["[a-z]", "\\d", "[^x]"], false)
            .expect("valid class patterns");
        let class_non_ascii = class_matcher
            .verify_byte_parity("café 1")
            .expect("byte parity holds");
        assert!(class_non_ascii.byte_identical);
        assert!(!class_non_ascii.parity_verified);

        // Pure ASCII text with wildcard/class patterns still verifies parity.
        let dot_ascii = dot.verify_byte_parity("ab").expect("ascii byte parity");
        assert!(dot_ascii.byte_identical);
        assert!(dot_ascii.parity_verified);

        // Pure literals on non-ASCII UTF-8 do not use byte wildcards/classes.
        let literal_dot = BitParallelMatcher::from_literals(&["."], false).expect("literal dot");
        let literal_report = literal_dot
            .verify_byte_parity("é.")
            .expect("literal byte parity");
        assert!(literal_report.byte_identical);
        assert!(literal_report.parity_verified);

        let fallback_non_ascii =
            CrossoverCalibrationReport::calibrate_cpu_fallback("café 1", &["."])
                .expect("cpu fallback calibration");
        assert!(!fallback_non_ascii.native_gpu_executed);
        assert!(!fallback_non_ascii.promotion_proof);
        assert!(!fallback_non_ascii.parity_verified);
    }

    #[test]
    fn crossover_calibration_report_cpu_fallback_never_claims_promotion_proof() {
        let fallback = CrossoverCalibrationReport::cpu_fallback(true, "simulated CPU run");
        assert_eq!(fallback.execution_path, ExecutionBackendKind::CpuFallback);
        assert!(!fallback.native_gpu_executed);
        assert!(!fallback.promotion_proof);
        assert_eq!(fallback.crossover_threshold_bytes, None);
        assert!(fallback.parity_verified);

        // Even if a caller passes a positive threshold and gpu_beats_cpu=true under CpuFallback,
        // promotion_proof must remain false.
        let evaluated = CrossoverCalibrationReport::evaluate(
            ExecutionBackendKind::CpuFallback,
            Some(1024 * 1024),
            true,
            true,
        );
        assert_eq!(evaluated.execution_path, ExecutionBackendKind::CpuFallback);
        assert!(!evaluated.native_gpu_executed);
        assert!(!evaluated.promotion_proof);
        assert_eq!(evaluated.crossover_threshold_bytes, None);

        let live_cpu = CrossoverCalibrationReport::calibrate_cpu_fallback(
            "ERROR\n\nWARN_1\n",
            &["ERROR", "WARN_\\d"],
        )
        .expect("cpu fallback calibration");
        assert!(live_cpu.parity_verified);
        assert!(!live_cpu.native_gpu_executed);
        assert!(!live_cpu.promotion_proof);
    }

    #[test]
    fn crossover_calibration_report_native_gpu_requires_parity_and_measured_crossover() {
        let no_parity = CrossoverCalibrationReport::evaluate(
            ExecutionBackendKind::NativeGpuKernel,
            Some(104_857_600),
            false,
            true,
        );
        assert!(no_parity.native_gpu_executed);
        assert!(!no_parity.promotion_proof);

        let cpu_faster = CrossoverCalibrationReport::evaluate(
            ExecutionBackendKind::NativeGpuKernel,
            Some(104_857_600),
            true,
            false,
        );
        assert!(cpu_faster.native_gpu_executed);
        assert!(!cpu_faster.promotion_proof);
        assert_eq!(cpu_faster.crossover_threshold_bytes, None);

        let proven = CrossoverCalibrationReport::evaluate(
            ExecutionBackendKind::NativeGpuKernel,
            Some(104_857_600),
            true,
            true,
        );
        assert_eq!(proven.execution_path, ExecutionBackendKind::NativeGpuKernel);
        assert!(proven.native_gpu_executed);
        assert!(proven.promotion_proof);
        assert_eq!(proven.crossover_threshold_bytes, Some(104_857_600));
    }
}
