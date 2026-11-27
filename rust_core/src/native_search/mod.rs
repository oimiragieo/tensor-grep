use aho_corasick::{AhoCorasick, MatchKind};
use anyhow::{anyhow, Context, Result};
use base64::engine::general_purpose::STANDARD as BASE64_STANDARD;
use base64::Engine as _;
use grep_matcher::LineTerminator;
use grep_printer::StandardBuilder;
use grep_regex::{RegexMatcher, RegexMatcherBuilder};
use grep_searcher::sinks::Bytes;
use grep_searcher::{
    BinaryDetection, MmapChoice, Searcher, SearcherBuilder, Sink, SinkContext, SinkFinish,
    SinkMatch,
};
use ignore::{overrides::OverrideBuilder, WalkBuilder, WalkState};
use memchr::{memchr, memchr_iter};
use memmap2::MmapOptions;
use rayon::prelude::*;
use regex::RegexBuilder as OutputRegexBuilder;
use serde::Serialize;
use std::borrow::Cow;
use std::collections::BTreeMap;
use std::fs;
use std::io::{self, Write};
use std::path::{Path, PathBuf};
use std::sync::atomic::{AtomicBool, AtomicUsize, Ordering};
use std::sync::{Arc, Mutex};

use crate::routing::gpu_proof_fields;
use crate::search_file_open::{
    open_native_search_file as open_search_file, resolve_explicit_search_root, search_opened_path,
    sink_io_error,
};
#[path = "../native_search_binary_summary.rs"]
mod binary_summary;
use binary_summary::emit_ndjson_binary_summary;
pub(crate) mod binary;
use binary::emit_binary_match_warning;
pub use binary::emit_index_binary_match_warning;
pub(crate) use binary::{
    binary_file_matches_pattern, first_binary_nul_offset, index_binary_matcher,
};

const JSON_OUTPUT_VERSION: u32 = 1;
const LARGE_FILE_CHUNK_THRESHOLD_BYTES: usize = 50 * 1024 * 1024;
const STREAMING_OUTPUT_FLUSH_BYTES: usize = 64 * 1024;
const STREAMING_OUTPUT_FLUSH_BYTES_DEBUG: usize = 8 * 1024;
/// Mirrors `grep_searcher`'s guaranteed 64 KiB prefix scan for `BinaryDetection::quit`.
/// Chunked search checks this whole-file prefix too because per-chunk `Bytes` sinks do not
/// propagate `binary_data` callbacks to this caller.
const BINARY_DETECTION_PREFIX_BYTES: usize = 64 * 1024;

#[derive(Debug, Clone, PartialEq, Eq, Serialize)]
pub struct NativeSearchMatch {
    pub path: PathBuf,
    pub line_number: Option<u64>,
    /// Exact matched-line bytes, with AT MOST the single trailing `\n` line terminator
    /// stripped -- a genuine trailing `\r` from a CRLF source line is left intact, matching
    /// `rg` and `rg --json`'s own `lines.text` field (task #266's first defect; mirrors the
    /// Python-side fix in `core/result.py::strip_line_terminator`, task #262/#743). Never
    /// passed through `String::from_utf8_lossy`: genuinely invalid-UTF-8 source content
    /// (e.g. Latin-1) must survive to JSON/NDJSON output losslessly rather than being
    /// silently replaced with U+FFFD (task #266's second defect) -- see
    /// `native_json_text_fields`, which mirrors real `rg --json`'s own `text`/`bytes`
    /// (base64) protocol for exactly this case.
    pub raw: Vec<u8>,
}

#[derive(Debug, Clone, PartialEq, Eq, Serialize)]
pub struct NativeMultiPatternMatch {
    pub path: PathBuf,
    pub line_number: u64,
    /// Same raw-bytes contract as `NativeSearchMatch.raw` (task #266/#271 gate follow-up): at
    /// most the single trailing `\n` stripped (moot here -- the caller's `memchr`-based line
    /// splitter already excludes it), a genuine trailing `\r` preserved, never lossily
    /// `String::from_utf8_lossy`-converted. `main.rs`'s `collect_native_multi_pattern_matches`
    /// derives `SearchMatchJson.text`/`bytes` from this the same way it does for the
    /// single-pattern `NativeSearchMatch` path, via `native_json_text_fields`.
    pub raw: Vec<u8>,
    pub pattern_id: usize,
    pub pattern_text: String,
}

#[derive(Debug, Clone, Default, PartialEq, Eq, Serialize)]
pub struct SearchStats {
    pub searched_files: usize,
    pub matched_files: usize,
    pub total_matches: usize,
    pub skipped_binary_files: usize,
    pub binary_match_files: usize,
    #[serde(skip)]
    pub binary_match_paths: Vec<PathBuf>,
    pub matches: Vec<NativeSearchMatch>,
    /// Count unreadable paths skipped by the walk; consumers need the incomplete signal, not paths.
    pub walk_errors: usize,
}

impl SearchStats {
    /// True when this worker observed nothing worth merging. Keep every result-bearing field here.
    pub fn is_empty(&self) -> bool {
        self.searched_files == 0
            && self.matched_files == 0
            && self.total_matches == 0
            && self.skipped_binary_files == 0
            && self.binary_match_files == 0
            && self.binary_match_paths.is_empty()
            && self.walk_errors == 0
            && self.matches.is_empty()
    }
}

#[derive(Debug, Clone, Default)]
pub enum NativeOutputTarget {
    #[default]
    Stdout,
    Buffer(Arc<Mutex<Vec<u8>>>),
}

impl NativeOutputTarget {
    fn write_all(&self, bytes: &[u8]) -> Result<()> {
        match self {
            Self::Stdout => {
                let mut stdout = std::io::stdout().lock();
                stdout.write_all(bytes)?;
                stdout.flush()?;
            }
            Self::Buffer(buffer) => {
                buffer
                    .lock()
                    .map_err(|_| anyhow!("failed to acquire native search output buffer"))?
                    .extend_from_slice(bytes);
            }
        }
        Ok(())
    }
}

#[derive(Debug, Clone)]
struct AtomicLineWriter {
    target: NativeOutputTarget,
    pending: Vec<u8>,
}

impl AtomicLineWriter {
    fn new(target: NativeOutputTarget) -> Self {
        Self {
            target,
            pending: Vec::new(),
        }
    }

    fn flush_complete_lines(&mut self) -> io::Result<()> {
        while let Some(newline_index) = memchr(b'\n', &self.pending) {
            let line = self.pending.drain(..=newline_index).collect::<Vec<_>>();
            self.target.write_all(&line).map_err(sink_io_error)?;
        }
        Ok(())
    }

    fn finish(&mut self) -> io::Result<()> {
        self.flush_complete_lines()?;
        if !self.pending.is_empty() {
            self.target
                .write_all(&self.pending)
                .map_err(sink_io_error)?;
            self.pending.clear();
        }
        Ok(())
    }
}

impl Write for AtomicLineWriter {
    fn write(&mut self, buf: &[u8]) -> io::Result<usize> {
        self.pending.extend_from_slice(buf);
        self.flush_complete_lines()?;
        Ok(buf.len())
    }

    fn flush(&mut self) -> io::Result<()> {
        self.flush_complete_lines()
    }
}

#[derive(Debug, Clone)]
pub struct NativeSearchConfig {
    pub pattern: String,
    pub paths: Vec<PathBuf>,
    pub routing_backend: &'static str,
    pub routing_reason: &'static str,
    pub sidecar_used: bool,
    pub requested_gpu_device_ids: Vec<i32>,
    pub ignore_case: bool,
    pub smart_case: bool,
    pub fixed_strings: bool,
    pub word_boundary: bool,
    pub invert_match: bool,
    pub before_context: usize,
    pub after_context: usize,
    pub max_count: Option<u64>,
    pub quiet: bool,
    pub only_matching: bool,
    pub max_depth: Option<usize>,
    pub glob: Vec<String>,
    pub hidden: bool,
    /// Whether the caller omitted an explicit PATH positional (the search root defaulted to `.`
    /// instead of a user-supplied path). Gates `check_native_implicit_walk_ceiling`, this
    /// engine's own refuse-before-enumerate guard (audit #105 -- the native-CPU sibling of
    /// `RipgrepSearchArgs::path_was_implicit`, audit #100). An explicit, deliberately-scoped PATH
    /// must never be refused regardless of its size. Every production construction site
    /// (`native_search_config_for_positional`, `native_search_config_for_command`,
    /// `native_search_config_for_gpu_params` in main.rs) must set this correctly and is covered
    /// by a dedicated regression test -- `Default`'s `false` is NOT a safe fallback for the walk
    /// guard itself (it means "never refuse"), it only exists so ad hoc test fixtures that build
    /// via `NativeSearchConfig::default()` and don't care about this field get deterministic,
    /// non-refusing behavior, mirroring `RipgrepSearchArgs`'s convention.
    pub path_was_implicit: bool,
    pub text: bool,
    pub null_data: bool,
    pub count: bool,
    pub crlf: bool,
    pub no_ignore: bool,
    /// Task #267: mirrors `RipgrepSearchArgs::no_ignore_vcs` / `rg_passthrough::
    /// root_ignore_file_args`'s `no_ignore_vcs` gate (rg docs: "--no-ignore-vcs" restricts
    /// disabling to source-control ignore files, i.e. skip only `.gitignore`, unlike the
    /// blanket `no_ignore`). Before this field existed, `build_walk_builder` had no way to
    /// honor `--no-ignore-vcs` at all -- it only ever branched on `no_ignore` -- so any
    /// caller that routes through this engine (`--json`/`--ndjson`, the structured_output
    /// arm of `route_search`) silently ignored `--no-ignore-vcs` and kept excluding
    /// `.gitignore`-matched files, even though the identical flag combination on the
    /// non-structured-output path (rg passthrough, `rg_passthrough.rs::root_ignore_file_args`)
    /// correctly re-includes them. An output-format flag must never change the file set.
    pub no_ignore_vcs: bool,
    pub line_number: bool,
    pub with_filename: bool,
    pub replace: Option<String>,
    pub mmap: bool,
    pub json: bool,
    pub ndjson: bool,
    pub verbose: bool,
    pub large_file_chunk_threshold_bytes: usize,
    pub parallel_large_files: bool,
    pub chunk_parallelism_threads: Option<usize>,
    pub track_binary_match_paths: bool,
    pub output_target: NativeOutputTarget,
}

impl Default for NativeSearchConfig {
    fn default() -> Self {
        Self {
            pattern: String::new(),
            paths: vec![PathBuf::from(".")],
            routing_backend: "NativeCpuBackend",
            routing_reason: "native_search",
            sidecar_used: false,
            requested_gpu_device_ids: Vec::new(),
            ignore_case: false,
            smart_case: false,
            fixed_strings: false,
            word_boundary: false,
            invert_match: false,
            before_context: 0,
            after_context: 0,
            max_count: None,
            quiet: false,
            only_matching: false,
            max_depth: None,
            glob: Vec::new(),
            hidden: false,
            path_was_implicit: false,
            text: false,
            null_data: false,
            count: false,
            crlf: false,
            no_ignore: false,
            no_ignore_vcs: false,
            line_number: true,
            with_filename: false,
            replace: None,
            mmap: true,
            json: false,
            ndjson: false,
            verbose: false,
            large_file_chunk_threshold_bytes: LARGE_FILE_CHUNK_THRESHOLD_BYTES,
            parallel_large_files: true,
            chunk_parallelism_threads: None,
            track_binary_match_paths: false,
            output_target: NativeOutputTarget::Stdout,
        }
    }
}

/// Does this error chain carry a typed `io::ErrorKind::BrokenPipe`? Used by
/// `search_walk_roots_parallel` (task #263's third defect) to decide whether a per-file search
/// error should still abort the whole parallel walk: a closed output pipe is a real reason to
/// stop (every remaining file would hit the identical error), so that case is deliberately
/// narrower than "log and continue". A narrower check than `main.rs`'s own
/// `error_chain_has_broken_pipe` (that one also falls back to matching a rendered error STRING
/// when no typed `io::Error` is present in the chain) -- not reachable from here, since
/// `main.rs` is a separate binary target that depends on this library crate, not the other way
/// around. Missing the untyped-fallback case only means an already-rare edge case falls back to
/// "log and continue" instead of aborting immediately -- extra wasted work against a closed
/// pipe until the walk finishes, not a correctness regression.
fn search_path_error_is_broken_pipe(err: &anyhow::Error) -> bool {
    err.chain()
        .filter_map(|cause| cause.downcast_ref::<io::Error>())
        .any(|io_err| io_err.kind() == io::ErrorKind::BrokenPipe)
}

fn render_output_text<'a>(config: &NativeSearchConfig, text: &'a str) -> Result<Cow<'a, str>> {
    let Some(replacement) = &config.replace else {
        return Ok(Cow::Borrowed(text));
    };

    let mut pattern = if config.fixed_strings {
        regex::escape(&config.pattern)
    } else {
        config.pattern.clone()
    };
    if config.word_boundary {
        pattern = format!(r"\b(?:{pattern})\b");
    }

    let regex = OutputRegexBuilder::new(&pattern)
        .case_insensitive(effective_ignore_case(
            &config.pattern,
            config.ignore_case,
            config.smart_case,
        ))
        .build()
        .with_context(|| {
            format!(
                "failed to compile native replace pattern '{}'",
                config.pattern
            )
        })?;

    Ok(Cow::Owned(
        regex.replace_all(text, replacement.as_str()).into_owned(),
    ))
}

/// Strip AT MOST the single trailing `\n` line terminator from a raw matched line's bytes --
/// mirrors `core/result.py::strip_line_terminator` (task #262/#743) for the Rust native-search
/// emitter. NEVER strips a trailing `\r` too: a CRLF source line's own `\r` is genuine line
/// content that both `rg` and `rg --json`'s `lines.text` field keep intact. Every sink closure
/// in this file used to do `line.trim_end_matches(['\n', '\r'])`, which strips ANY trailing run
/// of `\r`/`\n` in any order -- silently eating a CRLF line's own `\r` (task #266's first
/// defect, measured via hexdump against `rg.exe` 15.1.0).
fn strip_native_line_terminator(line: &[u8]) -> &[u8] {
    line.strip_suffix(b"\n").unwrap_or(line)
}

/// Produce the exact bytes to write for a matched line in plain-text (non-JSON) output,
/// applying `--replace` only when the line is valid UTF-8 (the regex substitution machinery in
/// `render_output_text` is `str`-based). A line that is not valid UTF-8 passes through
/// completely unmodified -- `--replace` combined with binary/Latin-1 content has no
/// well-defined substitution semantics, and the pre-fix code path lossily corrupted that
/// content before a replacement was ever attempted, so there is no existing byte-identical
/// contract this narrows.
fn render_matched_line_bytes<'a>(
    config: &NativeSearchConfig,
    raw: &'a [u8],
) -> Result<Cow<'a, [u8]>> {
    if config.replace.is_none() {
        return Ok(Cow::Borrowed(raw));
    }
    match std::str::from_utf8(raw) {
        Ok(text) => match render_output_text(config, text)? {
            Cow::Borrowed(_) => Ok(Cow::Borrowed(raw)),
            Cow::Owned(owned) => Ok(Cow::Owned(owned.into_bytes())),
        },
        Err(_) => Ok(Cow::Borrowed(raw)),
    }
}

/// Render matched line bytes for JSON/NDJSON output the same way real `rg --json` does
/// (verified via hexdump against `rg.exe` 15.1.0): valid-UTF-8 content is returned as `text`;
/// anything else is base64-encoded into `bytes` instead of being lossily replaced with U+FFFD
/// (`grep_searcher::sinks::Lossy`'s internal `String::from_utf8_lossy` -- task #266's second
/// defect). Exactly one of the two return values is `Some`, mirroring `rg`'s own
/// `text`-XOR-`bytes` JSON protocol for a `lines`/match payload.
///
/// `pub`: also called from `main.rs`'s `SearchMatchJson`/`SearchMatchNdjson` construction sites
/// (the multi-pattern native search path) -- `main.rs` is a separate binary target that depends
/// on this library crate, not a module of it, so this must cross the crate boundary explicitly
/// rather than relying on `pub(crate)` visibility.
pub fn native_json_text_fields(raw: &[u8]) -> (Option<&str>, Option<String>) {
    match std::str::from_utf8(raw) {
        Ok(text) => (Some(text), None),
        Err(_) => (None, Some(BASE64_STANDARD.encode(raw))),
    }
}

#[derive(Debug, Clone, Default)]
struct FileSearchResult {
    matches: Vec<NativeSearchMatch>,
    match_count: usize,
    binary_detected: bool,
    binary_match_detected: bool,
    binary_byte_offset: Option<u64>,
}

#[derive(Debug)]
struct BinaryAwareSink<S> {
    inner: S,
    saw_binary: bool,
    first_binary_byte_offset: Option<u64>,
}

impl<S> BinaryAwareSink<S> {
    fn new(inner: S) -> Self {
        Self {
            inner,
            saw_binary: false,
            first_binary_byte_offset: None,
        }
    }

    fn saw_binary(&self) -> bool {
        self.saw_binary
    }

    fn binary_byte_offset(&self) -> Option<u64> {
        self.first_binary_byte_offset
    }

    fn into_inner(self) -> S {
        self.inner
    }
}

impl<S> Sink for BinaryAwareSink<S>
where
    S: Sink<Error = io::Error>,
{
    type Error = io::Error;

    fn matched(&mut self, searcher: &Searcher, mat: &SinkMatch<'_>) -> Result<bool, Self::Error> {
        self.inner.matched(searcher, mat)
    }

    fn context(
        &mut self,
        searcher: &Searcher,
        context: &SinkContext<'_>,
    ) -> Result<bool, Self::Error> {
        self.inner.context(searcher, context)
    }

    fn context_break(&mut self, searcher: &Searcher) -> Result<bool, Self::Error> {
        self.inner.context_break(searcher)
    }

    fn binary_data(
        &mut self,
        searcher: &Searcher,
        binary_byte_offset: u64,
    ) -> Result<bool, Self::Error> {
        self.saw_binary = true;
        if self.first_binary_byte_offset.is_none() {
            self.first_binary_byte_offset = Some(binary_byte_offset);
        }
        self.inner.binary_data(searcher, binary_byte_offset)
    }

    fn begin(&mut self, searcher: &Searcher) -> Result<bool, Self::Error> {
        self.inner.begin(searcher)
    }

    fn finish(&mut self, searcher: &Searcher, finish: &SinkFinish) -> Result<(), Self::Error> {
        self.inner.finish(searcher, finish)
    }
}

#[derive(Debug, Default)]
struct SearchInputs {
    files: Vec<PathBuf>,
    roots: Vec<PathBuf>,
}

#[derive(Debug)]
struct ParallelWalkWorker {
    config: Arc<NativeSearchConfig>,
    matcher: RegexMatcher,
    searcher_with_line_numbers: Searcher,
    output_buffer: Vec<u8>,
    search_path: String,
    local_stats: SearchStats,
    shared_stats: Arc<Mutex<SearchStats>>,
    /// Task 321: set when the failure `search_path` is about to return came from writing OUTPUT,
    /// not from reading the INPUT file.
    ///
    /// `search_path` returns one `anyhow::Error` for two unrelated events -- "I could not read
    /// this file" and "I could not write the answer" -- and the caller could not tell them apart,
    /// so a full disk or an `EIO` on stdout was counted into `walk_errors` and surfaced as
    /// `incomplete_reason_class: "unreadable_path"`. That is a lie in the direction that matters:
    /// it sends a reader to check file permissions when the input was read perfectly.
    ///
    /// A FLAG rather than a marker error type on purpose. The alternative -- wrapping the error so
    /// the call site can `downcast_ref` it, mirroring `search_path_error_is_broken_pipe` -- would
    /// put a new type in front of the `Display` that `eprintln!("tg: {err}")` and the golden tests
    /// pin, and buys nothing here: the writer and the reader of this signal are the same worker,
    /// one call apart, so a field carries it without touching the error's rendering at all.
    output_write_failed: bool,
}

impl ParallelWalkWorker {
    fn new(config: Arc<NativeSearchConfig>, shared_stats: Arc<Mutex<SearchStats>>) -> Result<Self> {
        Ok(Self {
            matcher: build_matcher(&config)?,
            searcher_with_line_numbers: build_searcher(&config, true),
            output_buffer: Vec::with_capacity(STREAMING_OUTPUT_FLUSH_BYTES),
            search_path: display_search_path(&config.paths),
            local_stats: SearchStats::default(),
            shared_stats,
            output_write_failed: false,
            config,
        })
    }

    fn search_path(&mut self, path: &Path) -> Result<()> {
        self.output_buffer.clear();
        // Cleared per call: the caller reads it only on the Err path of THIS call, and a worker is
        // reused across files, so a stale `true` from an earlier file would misclassify a later
        // genuine read failure as an output failure.
        self.output_write_failed = false;

        let file_result = if self.config.count {
            self.search_count(path)?
        } else if self.config.json {
            search_file_collect_matches_with_searcher(
                &self.config,
                &self.matcher,
                path,
                &mut self.searcher_with_line_numbers,
            )?
        } else if self.config.ndjson {
            self.search_ndjson(path)?
        } else {
            self.search_plain_streaming(path)?
        };

        let FileSearchResult {
            matches,
            match_count,
            binary_detected,
            binary_match_detected,
            binary_byte_offset,
            ..
        } = file_result;

        self.local_stats.searched_files += 1;
        if binary_detected {
            self.local_stats.skipped_binary_files += 1;
            if binary_match_detected {
                // Task 321: this warning goes to the OUTPUT target, so its failure is a write
                // failure, not an unreadable input.
                if let Err(err) = emit_binary_match_warning(
                    &self.config.output_target,
                    path,
                    binary_byte_offset,
                    self.config.json || self.config.ndjson,
                    self.config.with_filename,
                ) {
                    self.output_write_failed = true;
                    return Err(err);
                }
                self.local_stats.binary_match_files += 1;
                if self.config.track_binary_match_paths {
                    self.local_stats.binary_match_paths.push(path.to_path_buf());
                }
            }
            self.output_buffer.clear();
            return Ok(());
        }

        if !self.output_buffer.is_empty() {
            // Task 321: the ONLY bulk write on this path. A failure here means the disk is full or
            // stdout is gone -- the input file was read fine.
            if let Err(err) = self.config.output_target.write_all(&self.output_buffer) {
                self.output_write_failed = true;
                return Err(err);
            }
            self.output_buffer.clear();
        }

        if match_count > 0 {
            self.local_stats.matched_files += 1;
            self.local_stats.total_matches += match_count;
            if !matches.is_empty() {
                self.local_stats.matches.extend(matches);
            }
        }

        Ok(())
    }

    fn search_plain_streaming(&mut self, path: &Path) -> Result<FileSearchResult> {
        let retain_matches = matches!(self.config.output_target, NativeOutputTarget::Buffer(_))
            || cfg!(debug_assertions);
        let mut matches = Vec::new();
        let mut match_count = 0usize;
        let path_buf = path.to_path_buf();
        let path_display = path.display().to_string();
        let output_buffer = &mut self.output_buffer;
        let mut sink = BinaryAwareSink::new(Bytes(|line_number, line| {
            let trimmed_line = strip_native_line_terminator(line);
            let rendered_bytes = render_matched_line_bytes(&self.config, trimmed_line)
                .map_err(sink_io_error)?
                .into_owned();
            append_standard_match_bytes(
                output_buffer,
                &self.config,
                &path_display,
                line_number,
                &rendered_bytes,
            )
            .map_err(sink_io_error)?;
            match_count = match_count.saturating_add(1);
            if retain_matches {
                matches.push(NativeSearchMatch {
                    path: path_buf.clone(),
                    line_number: Some(line_number),
                    raw: rendered_bytes,
                });
            }
            Ok(true)
        }));

        search_opened_path(
            &mut self.searcher_with_line_numbers,
            &self.matcher,
            path,
            &mut sink,
            "native standard output search failed",
        )?;

        let binary_detected = sink.saw_binary();
        let binary_byte_offset = sink.binary_byte_offset();
        let binary_match_detected =
            binary_file_matches_pattern(&self.matcher, path, binary_detected)?;
        if binary_detected {
            matches.clear();
            match_count = 0;
            self.output_buffer.clear();
        }

        Ok(FileSearchResult {
            matches,
            match_count,
            binary_detected,
            binary_match_detected,
            binary_byte_offset,
        })
    }

    fn search_ndjson(&mut self, path: &Path) -> Result<FileSearchResult> {
        let mut matches = Vec::new();
        let mut match_count = 0usize;
        let path_buf = path.to_path_buf();
        let search_path = self.search_path.clone();
        let output_buffer = &mut self.output_buffer;
        let mut sink = BinaryAwareSink::new(Bytes(|line_number, line| {
            let trimmed_line = strip_native_line_terminator(line);
            let matched = NativeSearchMatch {
                path: path_buf.clone(),
                line_number: Some(line_number),
                raw: trimmed_line.to_vec(),
            };
            append_ndjson_match_bytes(output_buffer, &self.config, &search_path, &matched)
                .map_err(sink_io_error)?;
            match_count = match_count.saturating_add(1);
            matches.push(matched);
            Ok(true)
        }));

        search_opened_path(
            &mut self.searcher_with_line_numbers,
            &self.matcher,
            path,
            &mut sink,
            "native NDJSON search failed",
        )?;

        let binary_detected = sink.saw_binary();
        let binary_byte_offset = sink.binary_byte_offset();
        let binary_match_detected =
            binary_file_matches_pattern(&self.matcher, path, binary_detected)?;
        if binary_detected {
            matches.clear();
            match_count = 0;
            self.output_buffer.clear();
        }

        Ok(FileSearchResult {
            matches,
            match_count,
            binary_detected,
            binary_match_detected,
            binary_byte_offset,
        })
    }

    fn search_count(&mut self, path: &Path) -> Result<FileSearchResult> {
        let file_result = search_file_count_with_searcher(
            &self.matcher,
            path,
            &mut self.searcher_with_line_numbers,
        )?;
        let mut match_count = file_result.match_count;
        let binary_detected = file_result.binary_detected;
        if !binary_detected {
            append_count_output_bytes(&mut self.output_buffer, &self.config, path, match_count)?;
        } else {
            match_count = 0;
        }

        Ok(FileSearchResult {
            matches: Vec::new(),
            match_count,
            binary_detected: file_result.binary_detected,
            binary_match_detected: file_result.binary_match_detected,
            binary_byte_offset: file_result.binary_byte_offset,
        })
    }
}

impl Drop for ParallelWalkWorker {
    fn drop(&mut self) {
        // Skip the lock when this worker has nothing to contribute. The emptiness test lives on
        // `SearchStats` rather than being enumerated here on purpose: an enumeration kept in sync
        // by hand at every call site drifts. It already had, twice, in opposite directions --
        // this guard once omitted `binary_match_files`, and the task 276 slice-A version of it
        // enumerated `walk_errors` but still omitted `binary_match_files`.
        //
        // `walk_errors` is the load-bearing member and the reason this must not regress: under
        // `build_parallel()` a worker can legitimately be handed ONLY unreadable entries. It
        // searches no files, matches nothing, and returns here with a non-zero `walk_errors`. If
        // the guard short-circuits on it, `std::mem::take` never runs and the count is dropped --
        // producing an envelope that reports a COMPLETE scan of an INCOMPLETE walk, exactly the
        // defect task 276 exists to fix, reintroduced by the fix. `is_empty()` therefore covers
        // walk_errors, and the invariant test below fails if any countable field is left out.
        //
        // HONESTY NOTE, because the tempting version of this commit message is wrong: the
        // omission is NOT currently reachable. Every PRODUCTION writer of `binary_match_files`
        // is preceded by `searched_files += 1` -- the worker path (:544/:556), the serial path
        // (:1121/:1133), and `merge_search_stats`, which merges `searched_files` first
        // (:1348/:1352). So `binary_match_files > 0` implies `searched_files > 0` and the guard
        // could never have returned early on it. This is defense-in-depth against a FUTURE field
        // that is not so protected -- not a fix for a live silent loss.
        //
        // "PRODUCTION" is load-bearing: the invariant test below deliberately violates that
        // implication by setting the field alone, which is exactly why it can test the guard's
        // contract without staging a state production can reach.
        if self.local_stats.is_empty() {
            return;
        }

        match self.shared_stats.lock() {
            Ok(mut shared_stats) => {
                merge_search_stats(&mut shared_stats, std::mem::take(&mut self.local_stats));
            }
            Err(poisoned) => {
                eprintln!(
                    "warning: parallel native search stats lock poisoned; recovering partial worker stats"
                );
                merge_search_stats(
                    &mut poisoned.into_inner(),
                    std::mem::take(&mut self.local_stats),
                );
            }
        }
    }
}

#[derive(Debug)]
struct CollectingSink<S> {
    inner: S,
    path: PathBuf,
    matches: Vec<NativeSearchMatch>,
}

impl<S> CollectingSink<S> {
    fn new(inner: S, path: PathBuf) -> Self {
        Self {
            inner,
            path,
            matches: Vec::new(),
        }
    }

    fn into_matches(self) -> Vec<NativeSearchMatch> {
        self.matches
    }
}

impl<S> Sink for CollectingSink<S>
where
    S: Sink<Error = io::Error>,
{
    type Error = io::Error;

    fn matched(&mut self, searcher: &Searcher, mat: &SinkMatch<'_>) -> Result<bool, Self::Error> {
        let keep_going = self.inner.matched(searcher, mat)?;
        self.matches.push(native_match_from_sink(&self.path, mat));
        Ok(keep_going)
    }

    fn context(
        &mut self,
        searcher: &Searcher,
        context: &SinkContext<'_>,
    ) -> Result<bool, Self::Error> {
        self.inner.context(searcher, context)
    }

    fn context_break(&mut self, searcher: &Searcher) -> Result<bool, Self::Error> {
        self.inner.context_break(searcher)
    }

    fn binary_data(
        &mut self,
        searcher: &Searcher,
        binary_byte_offset: u64,
    ) -> Result<bool, Self::Error> {
        self.inner.binary_data(searcher, binary_byte_offset)
    }

    fn begin(&mut self, searcher: &Searcher) -> Result<bool, Self::Error> {
        self.inner.begin(searcher)
    }

    fn finish(&mut self, searcher: &Searcher, finish: &SinkFinish) -> Result<(), Self::Error> {
        self.inner.finish(searcher, finish)
    }
}

#[derive(Debug, Clone)]
struct FileChunkPlan {
    byte_start: usize,
    byte_end: usize,
    first_line_number: u64,
}

#[derive(Debug, Serialize)]
struct NativeJsonOutput<'a> {
    version: u32,
    routing_backend: &'static str,
    routing_reason: &'static str,
    sidecar_used: bool,
    requested_gpu_device_ids: Vec<i32>,
    routing_gpu_device_ids: Vec<i32>,
    #[serde(skip_serializing_if = "Option::is_none")]
    gpu_evidence_status: Option<&'static str>,
    #[serde(skip_serializing_if = "Option::is_none")]
    gpu_proof: Option<bool>,
    #[serde(skip_serializing_if = "Option::is_none")]
    native_gpu_unavailable: Option<bool>,
    #[serde(skip_serializing_if = "Option::is_none")]
    not_gpu_proof_reason: Option<String>,
    query: &'a str,
    path: String,
    total_files: usize,
    total_matches: usize,
    #[serde(skip_serializing_if = "Option::is_none")]
    binary_match_files: Option<usize>,
    matched_file_paths: Vec<String>,
    match_counts_by_file: BTreeMap<String, usize>,
    matches: Vec<NativeJsonMatch>,
    // Omit these fields on complete walks to keep existing payloads byte-identical.
    #[serde(skip_serializing_if = "Option::is_none")]
    result_incomplete: Option<bool>,
    #[serde(skip_serializing_if = "Option::is_none")]
    incomplete_reason_class: Option<&'static str>,
    #[serde(skip_serializing_if = "Option::is_none")]
    incomplete_paths_count: Option<usize>,
    // Scope defaulting is advisory, not incomplete; explicit scopes omit these fields.
    #[serde(skip_serializing_if = "Option::is_none")]
    path_was_defaulted: Option<bool>,
    #[serde(skip_serializing_if = "Option::is_none")]
    scope_note: Option<&'static str>,
}

/// The defaulted-scope note, in the ONE place BOTH Rust engines can reach it.
///
/// Lives in the lib crate, not in `main.rs`, for the reason `write_bytes_refuse_symlink` was
/// moved here in #852: `main.rs` is the BINARY crate, so anything defined there is unreachable
/// from this module and a second copy is the only alternative. Two copies of a user-facing string
/// is how two engines start disagreeing.
///
/// Kept byte-identical to `cli/bootstrap.py::_defaulted_scope_note()`, which is the Python front
/// door's single source for the same sentence. `tests/unit/test_scope_note_parity.py` pins the
/// two together -- a doc comment asking for parity is not parity.
pub const DEFAULTED_SCOPE_NOTE: &str = "note: no PATH was given, so the search defaulted to the \
current directory. Zero matches means zero matches in THAT scope, not in the repository. If you \
expected hits, re-run with an explicit PATH: tg search <pattern> <dir>";

/// Task #26: turn "the caller gave no PATH" into the two advisory scope fields.
///
/// GATED ON ZERO MATCHES, not on the default alone. A defaulted search that FOUND something
/// answered the caller's question; annotating it would fire on the overwhelmingly common case and
/// train every consumer to ignore the field. The note only carries information when the answer was
/// empty, because "empty" is the answer a silently-narrowed scope fakes.
pub fn defaulted_scope_fields(
    path_was_implicit: bool,
    total_matches: usize,
) -> (Option<bool>, Option<&'static str>) {
    if path_was_implicit && total_matches == 0 {
        return (Some(true), Some(DEFAULTED_SCOPE_NOTE));
    }
    (None, None)
}

/// Mirrors real `rg --json`'s own `lines` protocol (verified via hexdump against `rg.exe`
/// 15.1.0): `text` is present for valid-UTF-8 line content, `bytes` (base64) is present
/// otherwise -- exactly one of the two, never both, never neither (task #266's second defect;
/// see `native_json_text_fields`).
#[derive(Debug, Serialize)]
struct NativeJsonMatch {
    file: String,
    line: usize,
    #[serde(skip_serializing_if = "Option::is_none")]
    text: Option<String>,
    #[serde(skip_serializing_if = "Option::is_none")]
    bytes: Option<String>,
}

#[derive(Debug, Serialize)]
struct NativeNdjsonMatch<'a> {
    version: u32,
    routing_backend: &'static str,
    routing_reason: &'static str,
    sidecar_used: bool,
    requested_gpu_device_ids: Vec<i32>,
    routing_gpu_device_ids: Vec<i32>,
    #[serde(skip_serializing_if = "Option::is_none")]
    gpu_evidence_status: Option<&'static str>,
    #[serde(skip_serializing_if = "Option::is_none")]
    gpu_proof: Option<bool>,
    #[serde(skip_serializing_if = "Option::is_none")]
    native_gpu_unavailable: Option<bool>,
    #[serde(skip_serializing_if = "Option::is_none")]
    not_gpu_proof_reason: Option<String>,
    query: &'a str,
    path: &'a str,
    file: &'a str,
    line: usize,
    // Same text/bytes protocol as `NativeJsonMatch` above (task #266's second defect).
    #[serde(skip_serializing_if = "Option::is_none")]
    text: Option<&'a str>,
    #[serde(skip_serializing_if = "Option::is_none")]
    bytes: Option<String>,
}

/// Whether retrying through another engine would bypass a failed anchored file open.
pub fn is_native_search_open_failure(error: &anyhow::Error) -> bool {
    error.is::<crate::search_file_open::NativeSearchOpenFailure>()
}

pub fn run_native_search(config: NativeSearchConfig) -> Result<SearchStats> {
    if config.json && config.ndjson {
        return Err(anyhow!(
            "native search cannot enable both JSON and NDJSON output simultaneously"
        ));
    }
    if config.pattern.is_empty() {
        return Err(anyhow!("native search requires a non-empty pattern"));
    }

    let inputs = split_search_inputs(&config)?;
    let mut effective_config = config;
    effective_config.track_binary_match_paths |= effective_config.json || effective_config.ndjson;
    effective_config.count &= !(effective_config.json || effective_config.ndjson); // J-06
    effective_config.with_filename = should_print_with_filename(&effective_config, &inputs);
    let matcher = build_matcher(&effective_config)?;
    let mut stats = SearchStats::default();

    if !inputs.files.is_empty() {
        let file_stats = run_native_search_files(&effective_config, &matcher, inputs.files)?;
        merge_search_stats(&mut stats, file_stats);
    }

    if !inputs.roots.is_empty() {
        let root_stats = if should_use_parallel_walk_search(&effective_config) {
            search_walk_roots_parallel(&effective_config, &inputs.roots)?
        } else {
            // Task 276 slice B3: the serial walk's error count must reach `stats`, or the `--json`
            // envelope reports a COMPLETE scan of an INCOMPLETE walk.
            let walked = collect_walked_files(&effective_config, &inputs.roots)?;
            let mut walk_stats =
                run_native_search_files(&effective_config, &matcher, walked.files)?;
            walk_stats.walk_errors += walked.walk_errors;
            walk_stats
        };
        merge_search_stats(&mut stats, root_stats);
    }

    if effective_config.track_binary_match_paths {
        stats.binary_match_files = stats
            .binary_match_paths
            .iter()
            .map(|path| fs::canonicalize(path).unwrap_or_else(|_| path.clone()))
            .collect::<std::collections::BTreeSet<_>>()
            .len();
    }

    sort_search_matches(&mut stats.matches);

    if effective_config.json {
        emit_json_matches(&effective_config, &stats)?;
    } else if effective_config.ndjson && stats.binary_match_files > 0 {
        emit_ndjson_binary_summary(&effective_config, &stats)?;
    }

    Ok(stats)
}

pub fn run_native_fixed_multi_pattern_search(
    config: NativeSearchConfig,
    patterns: &[String],
) -> Result<Option<Vec<NativeMultiPatternMatch>>> {
    if !supports_native_fixed_multi_pattern_search(&config, patterns) {
        return Ok(None);
    }

    let inputs = split_search_inputs(&config)?;
    let mut files = inputs.files;
    if !inputs.roots.is_empty() {
        // TASK 317, deliberately NOT closed here: this route returns
        // `Option<Vec<NativeMultiPatternMatch>>` and owns no `SearchStats`, so there is nowhere
        // to fold the count. Discarding it is explicit rather than incidental -- `.files` names
        // the drop at the call site so the next reader sees a decision, not an oversight.
        // Closing 317 means giving this function a stats channel, which changes its public
        // signature and every caller; that is its own slice.
        files.extend(collect_walked_files(&config, &inputs.roots)?.files);
    }
    files.sort_unstable();
    files.dedup();

    let matcher = AhoCorasick::builder()
        .match_kind(MatchKind::Standard)
        .build(patterns)
        .context("failed to build native fixed multi-pattern matcher")?;
    let mut matches = Vec::new();
    for file_path in files {
        let file = open_search_file(&file_path)?;
        let mmap = unsafe { MmapOptions::new().map(&file) }.with_context(|| {
            format!("failed to mmap native search path {}", file_path.display())
        })?;
        if !config.text && memchr(0, &mmap).is_some() {
            return Ok(None);
        }
        if !matcher.is_match(&mmap) {
            continue;
        }
        collect_fixed_multi_pattern_file_matches(
            &matcher,
            patterns,
            &file_path,
            &mmap,
            &mut matches,
        );
    }

    matches.sort_by(|left, right| {
        left.path
            .cmp(&right.path)
            .then(left.line_number.cmp(&right.line_number))
            .then(left.pattern_id.cmp(&right.pattern_id))
            .then(left.raw.cmp(&right.raw))
    });
    Ok(Some(matches))
}

fn supports_native_fixed_multi_pattern_search(
    config: &NativeSearchConfig,
    patterns: &[String],
) -> bool {
    patterns.len() > 1
        && config.fixed_strings
        && !patterns.iter().any(|pattern| pattern.is_empty())
        && !config.ignore_case
        && !config.smart_case
        && !config.word_boundary
        && !config.invert_match
        && config.before_context == 0
        && config.after_context == 0
        && config.max_count.is_none()
        && !config.quiet
        && !config.only_matching
        && !config.null_data
        && !config.crlf
        && config.replace.is_none()
}

fn collect_fixed_multi_pattern_file_matches(
    matcher: &AhoCorasick,
    patterns: &[String],
    path: &Path,
    contents: &[u8],
    matches: &mut Vec<NativeMultiPatternMatch>,
) {
    let mut line_start = 0usize;
    let mut line_number = 1u64;
    for newline_index in memchr_iter(b'\n', contents) {
        collect_fixed_multi_pattern_line_matches(
            matcher,
            patterns,
            path,
            line_number,
            &contents[line_start..newline_index],
            matches,
        );
        line_start = newline_index + 1;
        line_number += 1;
    }

    if line_start < contents.len() {
        collect_fixed_multi_pattern_line_matches(
            matcher,
            patterns,
            path,
            line_number,
            &contents[line_start..],
            matches,
        );
    }
}

fn collect_fixed_multi_pattern_line_matches(
    matcher: &AhoCorasick,
    patterns: &[String],
    path: &Path,
    line_number: u64,
    raw_line: &[u8],
    matches: &mut Vec<NativeMultiPatternMatch>,
) {
    // `raw_line` never includes its own trailing `\n` (the caller splits on `memchr(b'\n', ..)`
    // and excludes the delimiter), but a CRLF source line's own `\r` IS genuine line content --
    // stripping it here was the same over-trim bug as the main sink closures below (task #266's
    // first defect): both `rg` and this engine's own `--json` output keep that `\r`. Used
    // unmodified for both matching and the reported text, matching how the main path's
    // `strip_native_line_terminator` never touches a `\r` either.
    let line = raw_line;
    let mut pattern_ids = std::collections::BTreeSet::new();
    for matched in matcher.find_overlapping_iter(line) {
        pattern_ids.insert(matched.pattern().as_usize());
    }
    if pattern_ids.is_empty() {
        return;
    }

    // Raw bytes, not `String::from_utf8_lossy` -- task #266's second defect applies here exactly
    // as much as it does to the single-pattern path (multi-pattern `--json`/`--ndjson` output is
    // equally capable of carrying a non-UTF-8 match). `main.rs`'s `collect_native_multi_pattern_
    // matches` derives `SearchMatchJson.text`/`bytes` from this via `native_json_text_fields`,
    // the same helper the single-pattern emitter uses.
    let raw = line.to_vec();
    for pattern_id in pattern_ids {
        matches.push(NativeMultiPatternMatch {
            path: path.to_path_buf(),
            line_number,
            raw: raw.clone(),
            pattern_id,
            pattern_text: patterns[pattern_id].clone(),
        });
    }
}

fn run_native_search_files(
    config: &NativeSearchConfig,
    matcher: &RegexMatcher,
    files: Vec<PathBuf>,
) -> Result<SearchStats> {
    let mut stats = SearchStats::default();
    let mut emitted_stream_output = false;
    let buffer_standard_context_output = !config.json
        && !config.ndjson
        && !config.count
        && !config.quiet
        && (config.before_context > 0 || config.after_context > 0);

    for file_path in files {
        let file_result = if buffer_standard_context_output {
            let buffer = Arc::new(Mutex::new(Vec::new()));
            let mut buffered_config = config.clone();
            buffered_config.output_target = NativeOutputTarget::Buffer(Arc::clone(&buffer));
            let file_result = search_file_streaming_standard(
                &buffered_config,
                matcher,
                &file_path,
                !emitted_stream_output,
            )?;
            if file_result.match_count > 0 {
                if emitted_stream_output {
                    config.output_target.write_all(b"--\n")?;
                }
                let bytes = buffer
                    .lock()
                    .map_err(|_| anyhow!("failed to read buffered native context output"))?
                    .clone();
                if !bytes.is_empty() {
                    config.output_target.write_all(&bytes)?;
                }
            }
            file_result
        } else if config.json {
            search_file(config, matcher, &file_path)?
        } else if config.ndjson {
            search_file_streaming_ndjson(config, matcher, &file_path)?
        } else if config.count {
            search_file_count(config, matcher, &file_path)?
        } else if config.quiet {
            search_file(config, matcher, &file_path)?
        } else {
            search_file_streaming_standard(config, matcher, &file_path, !emitted_stream_output)?
        };

        let FileSearchResult {
            matches,
            match_count,
            binary_detected,
            binary_match_detected,
            binary_byte_offset,
            ..
        } = file_result;

        stats.searched_files += 1;
        if binary_detected {
            stats.skipped_binary_files += 1;
            if binary_match_detected {
                emit_binary_match_warning(
                    &config.output_target,
                    &file_path,
                    binary_byte_offset,
                    config.json || config.ndjson,
                    config.with_filename,
                )?;
                stats.binary_match_files += 1;
                if config.track_binary_match_paths {
                    stats.binary_match_paths.push(file_path.clone());
                }
            }
            continue;
        }

        if match_count > 0 {
            stats.matched_files += 1;
            stats.total_matches += match_count;
            if !matches.is_empty() {
                stats.matches.extend(matches);
            }
            if !config.json && !config.ndjson && !config.count && !config.quiet {
                emitted_stream_output = true;
            }
        }

        if config.quiet && (match_count > 0 || binary_match_detected) {
            break;
        }

        if config.json || config.ndjson || (!config.count && !config.quiet) {
            continue;
        }

        if config.count {
            emit_count_output_from_matches(config, &file_path, match_count)?;
        }
    }

    Ok(stats)
}

mod output;
mod walk;
use output::*;
use walk::*;
pub use walk::{
    effective_ignore_case, native_search_pattern_compiles, smart_case_pattern_is_case_insensitive,
};

#[cfg(test)]
#[path = "../native_search_tests.rs"]
mod tests;
