use super::{
    build_matcher, detect_binary_prefix, NativeOutputTarget, NativeSearchConfig,
    BINARY_DETECTION_PREFIX_BYTES,
};
use anyhow::{Context, Result};
use grep_matcher::Matcher;
use grep_regex::RegexMatcher;
use grep_searcher::sinks::Bytes;
use std::fs::File;
use std::io::Write;
use std::path::Path;

const MAX_BINARY_PROBE_BYTES: u64 = 64 * 1024 * 1024;

pub(crate) fn index_binary_matcher(
    pattern: &str,
    ignore_case: bool,
    fixed_strings: bool,
) -> Result<RegexMatcher> {
    build_matcher(&NativeSearchConfig {
        pattern: pattern.to_string(),
        ignore_case,
        fixed_strings,
        ..NativeSearchConfig::default()
    })
}

pub(crate) fn first_binary_nul_offset(path: &Path) -> Result<Option<u64>> {
    use std::io::Read;

    let mut prefix = Vec::with_capacity(BINARY_DETECTION_PREFIX_BYTES);
    File::open(path)
        .with_context(|| format!("failed to read binary candidate {}", path.display()))?
        .take(BINARY_DETECTION_PREFIX_BYTES as u64)
        .read_to_end(&mut prefix)
        .with_context(|| format!("failed to read binary candidate {}", path.display()))?;
    Ok(detect_binary_prefix(
        &NativeSearchConfig::default(),
        &prefix,
    ))
}

pub(crate) fn native_binary_nul_offset(matcher: &RegexMatcher, path: &Path) -> Result<Option<u64>> {
    let config = NativeSearchConfig::default();
    let mut searcher = super::build_searcher(&config, true);
    let mut sink = super::BinaryAwareSink::new(Bytes(|_, _| Ok(true)));
    searcher
        .search_path(matcher, path, &mut sink)
        .with_context(|| {
            format!(
                "native binary detection search failed for {}",
                path.display()
            )
        })?;
    Ok(sink.binary_byte_offset())
}

pub(crate) fn binary_file_matches_pattern(
    matcher: &RegexMatcher,
    path: &Path,
    binary_detected: bool,
) -> Result<bool> {
    if !binary_detected {
        return Ok(false);
    }

    use std::io::Read;

    let file = File::open(path)
        .with_context(|| format!("failed to open binary candidate {}", path.display()))?;
    let max_read = file
        .metadata()
        .with_context(|| format!("failed to stat binary candidate {}", path.display()))?
        .len()
        .min(MAX_BINARY_PROBE_BYTES);
    let mut contents = Vec::new();
    file.take(max_read)
        .read_to_end(&mut contents)
        .with_context(|| format!("failed to read binary candidate {}", path.display()))?;
    matcher
        .is_match(&contents)
        .with_context(|| format!("failed to match binary candidate {}", path.display()))
}

/// `with_filename` mirrors the SAME rule real `rg` uses for every other match line (and that
/// `NativeSearchConfig.with_filename`/`should_print_with_filename` already compute): shown
/// whenever more than one file is in play (a directory walk or multiple explicit paths) or
/// explicitly requested, omitted for a single explicit file. Verified against `rg.exe` 15.1.0:
/// `rg needle bin.dat` prints a bare notice, `rg needle bin.dat bin2.dat` prefixes each with
/// `"<path>: "`. Without this, multiple binary files hit during one walk previously produced
/// identical, unattributed notices with no way to tell which file each one was about (task
/// #263's first defect -- `path` used to be accepted and silently ignored, `_path`-prefixed).
pub(super) fn emit_binary_match_warning(
    output_target: &NativeOutputTarget,
    path: &Path,
    binary_byte_offset: Option<u64>,
    structured_output: bool,
    with_filename: bool,
) -> Result<()> {
    if structured_output {
        return Ok(());
    }

    let mut bytes = Vec::new();
    if with_filename {
        write!(bytes, "{}: ", path.display())?;
    }
    match binary_byte_offset {
        // `\\0` (not `/0`, task #263's second defect): real `rg` spells the NUL escape
        // `"\\0"`, verified via hexdump against `rg.exe` 15.1.0 -- `rg needle bin.dat` prints
        // `binary file matches (found "\\0" byte around offset 5)`. The previous `"/0"` spelling
        // was pinned by 4 stale sites (a golden e2e snapshot, `rust_backend.py`'s Python-side
        // twin, and 2 test assertions), all corrected alongside this fix.
        Some(offset) => writeln!(
            bytes,
            "binary file matches (found \"\\0\" byte around offset {offset})"
        )?,
        None => writeln!(bytes, "binary file matches")?,
    }
    output_target.write_all(&bytes)
}

pub fn emit_index_binary_match_warning(
    path: &Path,
    binary_byte_offset: u64,
    with_filename: bool,
) -> Result<()> {
    emit_binary_match_warning(
        &NativeOutputTarget::Stdout,
        path,
        Some(binary_byte_offset),
        false,
        with_filename,
    )
}
