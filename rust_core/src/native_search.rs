use aho_corasick::{AhoCorasick, MatchKind};
use anyhow::{anyhow, Context, Result};
use base64::engine::general_purpose::STANDARD as BASE64_STANDARD;
use base64::Engine as _;
use grep_matcher::{LineTerminator, Matcher};
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
use std::fs::{self, File};
use std::io::{self, Write};
use std::path::{Path, PathBuf};
use std::sync::atomic::{AtomicBool, AtomicUsize, Ordering};
use std::sync::{Arc, Mutex};

use crate::routing::gpu_proof_fields;

const JSON_OUTPUT_VERSION: u32 = 1;
const LARGE_FILE_CHUNK_THRESHOLD_BYTES: usize = 50 * 1024 * 1024;
const STREAMING_OUTPUT_FLUSH_BYTES: usize = 64 * 1024;
const STREAMING_OUTPUT_FLUSH_BYTES_DEBUG: usize = 8 * 1024;
/// Mirrors `grep_searcher::line_buffer::DEFAULT_BUFFER_CAPACITY` (64 KiB): the fixed-size prefix
/// that `grep-searcher`'s `BinaryDetection::quit` guarantees to scan for the binary byte when
/// searching mmap-backed content (see that type's doc comment in the `grep-searcher` crate --
/// "only a fixed sized region at the beginning of the contents are detected for binary data").
/// The serial (non-chunked) search path relies on exactly this guaranteed floor when a whole file
/// is searched via one mmap-backed `Searcher`; `search_file_chunk_parallel` must apply the
/// identical floor over the whole file before fanning out per-chunk, since its per-chunk `Bytes`
/// sinks never surface `binary_data` callbacks back up to this caller.
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
    pub matches: Vec<NativeSearchMatch>,
    /// Per-entry walk errors (a permission-denied subdirectory, a vanished path) that were
    /// reported on stderr and stepped over. Task #276 slice A: this is the PLUMBING only. The
    /// count is carried so a later slice can put `result_incomplete` into the `--json` envelope
    /// and exit 2 -- today nothing reads it, so behaviour is byte-identical.
    ///
    /// It is deliberately a COUNT, not a path list: the walk runs in `build_parallel()` across
    /// threads, and an unbounded per-path Vec behind a mutex would be both a contention point on
    /// the hot walker and a DoS surface (a tree with 50k unreadable entries would produce a 50k
    /// -entry payload). A consumer only needs to know THAT the answer is incomplete.
    pub walk_errors: usize,
}

impl SearchStats {
    /// True when this worker observed nothing worth merging.
    ///
    /// One place that knows every countable field, so "did you cover the new field?" is a
    /// single-site question. `ParallelWalkWorker::drop` previously enumerated the fields inline
    /// and covered five of six.
    ///
    /// Any field added to this struct MUST be added here. `search_stats_is_empty_covers_every_
    /// countable_field` fails if one is missed.
    pub fn is_empty(&self) -> bool {
        self.searched_files == 0
            && self.matched_files == 0
            && self.total_matches == 0
            && self.skipped_binary_files == 0
            && self.binary_match_files == 0
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
            output_target: NativeOutputTarget::Stdout,
        }
    }
}

/// Converts an `anyhow::Error` into the `io::Error` that `grep_searcher::Sink::Error` requires,
/// PRESERVING the original `ErrorKind` when the chain contains one.
///
/// `io::Error::other(...)` — what every one of these call sites used before — always produces kind
/// `Other`, which silently discards `BrokenPipe`. That mattered: a consumer closing the pipe
/// (`tg ... | head -1`) surfaced as a generic failure, so
/// `run_native_search_with_optional_rg_fallback`'s broken-pipe guard could not recognise it, and a
/// normal early termination was reported as a search error (and, with a fallback configured, was
/// followed by a `warning: ...` line and a full re-run into the already-closed pipe). CI proved the
/// typed chain walk alone was not enough; this fixes it at the source rather than pattern-matching
/// error text downstream.
///
/// Only the error's KIND is affected. No output byte, no rendering, and no success path changes.
fn sink_io_error(err: anyhow::Error) -> io::Error {
    for cause in err.chain() {
        if let Some(io_err) = cause.downcast_ref::<io::Error>() {
            // `err.to_string()`, not `{err:#}`: `io::Error::other(err)` rendered exactly this, so
            // the message stays byte-identical and ONLY the kind changes.
            return io::Error::new(io_err.kind(), err.to_string());
        }
    }
    io::Error::other(err)
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

        self.searcher_with_line_numbers
            .search_path(&self.matcher, path, &mut sink)
            .with_context(|| {
                format!(
                    "native standard output search failed for {}",
                    path.display()
                )
            })?;

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

        self.searcher_with_line_numbers
            .search_path(&self.matcher, path, &mut sink)
            .with_context(|| format!("native NDJSON search failed for {}", path.display()))?;

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
    matched_file_paths: Vec<String>,
    match_counts_by_file: BTreeMap<String, usize>,
    matches: Vec<NativeJsonMatch>,
    // Task #276 slice B2 -- the whole point of the issue. Until now this envelope reported
    // success on a walk that skipped unreadable paths, so an agent parsing --json could not tell
    // "no matches exist" from "I could not finish looking".
    //
    // NAMES ARE NOT NEW. `result_incomplete` and `incomplete_reason_class` are the vocabulary the
    // PYTHON routes have emitted since slice 1 (formatters/json_fmt.py:127, :140), with a closed
    // class set (unreadable_path | timeout | deadline | scan_limit) that #293 documented and
    // ratcheted. The native path adopting them verbatim is the point: one contract, two engines.
    //
    // `skip_serializing_if` on ALL THREE keeps a COMPLETE envelope byte-identical to before this
    // change, which is what makes the fix additive for every existing consumer.
    #[serde(skip_serializing_if = "Option::is_none")]
    result_incomplete: Option<bool>,
    #[serde(skip_serializing_if = "Option::is_none")]
    incomplete_reason_class: Option<&'static str>,
    #[serde(skip_serializing_if = "Option::is_none")]
    incomplete_paths_count: Option<usize>,
    // Task #26. The scope-disclosure pair, SIBLING to the incompleteness triple above and
    // deliberately NOT part of it: a search whose PATH defaulted to `.` RAN TO COMPLETION. It
    // answered a narrower question than the caller may have meant, which is an advisory, not an
    // incompleteness -- setting `result_incomplete` here would be false AND would drag the exit
    // code to 2, breaking the closed 0/1/2 contract for the most ordinary invocation there is.
    //
    // Same `skip_serializing_if` convention as everything above it, for the same reason: an
    // explicitly-scoped search emits neither key and stays byte-identical for every consumer.
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
            // Task 276 slice B3: the serial walk's own error count must reach `stats`, or the
            // `--json` envelope at :2436 reports a COMPLETE scan of an INCOMPLETE walk -- the
            // exact defect task 276 exists to close, on the one path that had no channel for it.
            let walked = collect_walked_files(&effective_config, &inputs.roots)?;
            let mut walk_stats =
                run_native_search_files(&effective_config, &matcher, walked.files)?;
            walk_stats.walk_errors += walked.walk_errors;
            walk_stats
        };
        merge_search_stats(&mut stats, root_stats);
    }

    sort_search_matches(&mut stats.matches);

    if effective_config.json {
        emit_json_matches(&effective_config, &stats)?;
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
        let file = fs::File::open(&file_path).with_context(|| {
            format!("failed to open native search path {}", file_path.display())
        })?;
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

fn should_print_with_filename(config: &NativeSearchConfig, inputs: &SearchInputs) -> bool {
    config.with_filename || !inputs.roots.is_empty() || inputs.files.len() > 1
}

fn split_search_inputs(config: &NativeSearchConfig) -> Result<SearchInputs> {
    let mut inputs = SearchInputs::default();

    for path in &config.paths {
        if !path.exists() {
            return Err(anyhow!(
                "native search path does not exist: {}",
                path.display()
            ));
        }
        if path.is_file() {
            inputs.files.push(path.clone());
        } else {
            inputs.roots.push(path.clone());
        }
    }

    inputs.files.sort_unstable();
    inputs.files.dedup();
    Ok(inputs)
}

fn should_use_parallel_walk_search(config: &NativeSearchConfig) -> bool {
    !config.quiet
        && config.before_context == 0
        && config.after_context == 0
        && !config.only_matching
        && config.max_count.is_none()
}

/// Bounded refuse-before-enumerate gate for the native-CPU engine's own root walk -- the
/// native-CPU sibling of `rg_passthrough::check_implicit_walk_ceiling` (audit #100). Audit #105
/// found #100's hoist covered only `execute_ripgrep_search`'s callers (the rg-passthrough
/// engine); `run_native_search` (reached via `--json`, `--force-cpu`, single-pattern
/// `--fixed-strings`, and rg-unavailable routing) had NO ceiling at all, so a bare implicit-path
/// search on a huge root still walked unbounded through this engine.
///
/// Only meaningful when `config.path_was_implicit` -- an explicit, deliberately-scoped PATH is
/// never refused regardless of size. Called as the FIRST statement of both
/// `search_walk_roots_parallel` and `collect_walked_files`: those are the only two functions
/// that ever hand a root to `WalkBuilder` in this module (`build_walk_builder`'s only two
/// callers), and `collect_walked_files` is also called directly by
/// `run_native_fixed_multi_pattern_search` (the AhoCorasick multi-pattern fast path) -- so
/// gating at this shared low-level pair, rather than in `run_native_search` alone, protects
/// every native-CPU walk entry point in one place instead of relying on each of main.rs's
/// several dispatch sites (positional CLI, `tg search`, GPU-CPU-fallback) to remember it.
fn check_native_implicit_walk_ceiling(
    config: &NativeSearchConfig,
    roots: &[PathBuf],
) -> Option<String> {
    if !config.path_was_implicit {
        return None;
    }
    let probe_roots: Vec<String> = roots
        .iter()
        .map(|root| root.to_string_lossy().into_owned())
        .collect();
    if crate::rg_passthrough::implicit_search_walk_exceeds_ceiling(
        &probe_roots,
        config.max_depth,
        config.no_ignore,
        config.hidden,
        crate::rg_passthrough::IMPLICIT_SEARCH_WALK_FILE_CEILING,
    ) {
        Some(
            crate::rg_passthrough::format_unbounded_implicit_search_walk_error(
                crate::rg_passthrough::IMPLICIT_SEARCH_WALK_FILE_CEILING,
            ),
        )
    } else {
        None
    }
}

fn search_walk_roots_parallel(
    config: &NativeSearchConfig,
    roots: &[PathBuf],
) -> Result<SearchStats> {
    if let Some(refusal) = check_native_implicit_walk_ceiling(config, roots) {
        return Err(anyhow!(refusal));
    }
    let shared_stats = Arc::new(Mutex::new(SearchStats::default()));
    let shared_error = Arc::new(Mutex::new(None));
    let should_quit = Arc::new(AtomicBool::new(false));
    let config = Arc::new(config.clone());
    let walker = build_walk_builder(config.as_ref(), roots)?;

    walker.build_parallel().run(|| {
        let config = Arc::clone(&config);
        let shared_stats = Arc::clone(&shared_stats);
        let shared_error = Arc::clone(&shared_error);
        let should_quit = Arc::clone(&should_quit);
        let mut worker = ParallelWalkWorker::new(config, shared_stats);
        Box::new(move |entry| {
            if should_quit.load(Ordering::Relaxed) {
                return WalkState::Quit;
            }

            let entry = match entry {
                Ok(entry) => entry,
                Err(err) => {
                    // A per-entry walk error (e.g. a permission-denied subdirectory) used to
                    // abort the ENTIRE parallel walk here, where real `rg` logs one stderr line
                    // and keeps searching every other file (task #263's third defect --
                    // confirmed by direct comparison against `rg.exe` 15.1.0: `rg needle .`
                    // over a tree with one access-denied subdirectory still reports every match
                    // outside it, plus one `rg: <path>: Access is denied.` stderr line; the old
                    // abort-on-first-error behavior would have silently returned ZERO matches
                    // for the exact same tree depending on walk order). Logging and continuing
                    // matches `rg`'s own `ignore`-crate-backed walker -- the same crate this
                    // engine uses -- so this is real parity, not a new guess at rg's behavior.
                    // Task #276 slice B: count it as well as printing it. The stderr line is
                    // rg-parity (#263) and stays; the COUNT is what lets the --json envelope stop
                    // claiming a complete result on an incomplete walk. Flows to the aggregate
                    // via the existing local->shared merge, so no new lock on the hot path.
                    // `worker` is still the `Result<ParallelWalkWorker>` from
                    // `ParallelWalkWorker::new` here -- the shadowing `let worker = match
                    // worker.as_mut()` happens BELOW this arm, so a bare `worker.local_stats`
                    // is field access on a Result and does not compile. (It did not compile;
                    // an audit caught it because every Rust CI leg on the commit that
                    // introduced it was CANCELLED, never green.)
                    //
                    // The `Err` arm is deliberately silent: a thread whose matcher failed to
                    // build owns no worker and therefore no stats channel. It quits on its
                    // first real entry and the whole search returns Err, so there is no
                    // "complete" envelope for a lost count to corrupt.
                    // ALLOW-LIST the count (#282: fail-closed guidance enumerates the SAFE
                    // cases, never the unsafe ones). `ignore::Error` is NOT only "path was
                    // unreadable" -- the parallel walker also reports a failure to PARSE an
                    // ancestor/global gitignore (`Error::Glob` / `WithLineNumber`, surfaced via
                    // `add_parents`) and `UnrecognizedFileType` / `InvalidDefinition`. Counting
                    // those would label a COMPLETE walk `result_incomplete` with a
                    // budget-non-remediable cause: one malformed glob in a user's
                    // ~/.config/git/ignore would make EVERY `tg search --json` on that machine
                    // claim it could not finish. A false "incomplete" is worse than the silence
                    // #276 set out to fix, because it teaches an agent to distrust a true answer.
                    //
                    // `is_io()` is the crate's own predicate for "exclusively an I/O error": it
                    // recurses through WithPath/WithDepth/WithLineNumber, treats a single-element
                    // Partial as its inner error, and returns FALSE for Glob, UnrecognizedFileType,
                    // InvalidDefinition and Loop. Loop cannot fire here anyway -- `build_walk_builder`
                    // never calls `follow_links`.
                    //
                    // Non-I/O errors still PRINT (rg-parity, #263); they just do not claim the
                    // answer is incomplete.
                    if err.is_io() {
                        if let Ok(worker) = worker.as_mut() {
                            worker.local_stats.walk_errors += 1;
                        }
                    }
                    eprintln!("tg: {err}");
                    return WalkState::Continue;
                }
            };

            if !entry
                .file_type()
                .map(|kind| kind.is_file())
                .unwrap_or(false)
            {
                return WalkState::Continue;
            }

            let worker = match worker.as_mut() {
                Ok(worker) => worker,
                Err(err) => {
                    should_quit.store(true, Ordering::Relaxed);
                    if let Ok(mut guard) = shared_error.lock() {
                        if guard.is_none() {
                            *guard = Some(anyhow!(err.to_string()));
                        }
                    }
                    return WalkState::Quit;
                }
            };

            if let Err(err) = worker.search_path(entry.path()) {
                // A closed output pipe (`tg ... | head -1`) is a real reason to stop the whole
                // walk -- every remaining file would just hit the same error -- so that case
                // still aborts. Any OTHER per-file search error (e.g. the file itself became
                // unreadable between being listed and opened) now logs and continues, matching
                // `rg`'s own behavior for exactly this case (task #263's third defect; verified
                // against `rg.exe` 15.1.0 the same way as the entry-error case above).
                // Task 321: an OUTPUT-write failure aborts on exactly the same reasoning as the
                // broken pipe beside it -- every remaining file would hit the same dead target,
                // so continuing burns the whole walk to produce nothing. `ENOSPC`/`EIO` were
                // previously NOT caught here (only `BrokenPipe` was), so they fell through to the
                // `walk_errors` counter below and the envelope reported
                // `incomplete_reason_class: "unreadable_path"` for a file it had read perfectly.
                // Wrong in the direction that matters: it sends the reader to check permissions on
                // an input that was fine, and hides that the OUTPUT is the thing that failed.
                if search_path_error_is_broken_pipe(&err) || worker.output_write_failed {
                    should_quit.store(true, Ordering::Relaxed);
                    if let Ok(mut guard) = shared_error.lock() {
                        if guard.is_none() {
                            *guard = Some(err);
                        }
                    }
                    return WalkState::Quit;
                }
                // Task #276 slice B: same reasoning as the entry-error arm above -- a file we
                // could not read is a hole in the answer, and the envelope has to be able to say
                // so. Counted here, emitted by slice B2. Reached ONLY for genuine INPUT failures
                // now -- the output-write arm above returns before this.
                worker.local_stats.walk_errors += 1;
                eprintln!("tg: {err}");
                return WalkState::Continue;
            }

            WalkState::Continue
        })
    });

    if let Some(err) = shared_error
        .lock()
        .map_err(|_| anyhow!("failed to inspect native search worker errors"))?
        .take()
    {
        return Err(err);
    }

    let mut stats = std::mem::take(
        &mut *shared_stats
            .lock()
            .map_err(|_| anyhow!("failed to collect native search worker stats"))?,
    );
    sort_search_matches(&mut stats.matches);
    Ok(stats)
}

fn merge_search_stats(target: &mut SearchStats, source: SearchStats) {
    target.searched_files += source.searched_files;
    target.matched_files += source.matched_files;
    target.total_matches += source.total_matches;
    target.skipped_binary_files += source.skipped_binary_files;
    target.binary_match_files += source.binary_match_files;
    // Task #276 slice A. Missing this line is the whole failure mode: every worker would count
    // its own walk errors and the aggregate would report zero, so the envelope would claim a
    // complete result on an incomplete walk -- the exact defect #276 exists to fix.
    target.walk_errors += source.walk_errors;
    target.matches.extend(source.matches);
}

fn sort_search_matches(matches: &mut [NativeSearchMatch]) {
    matches.sort_by(|left, right| {
        left.path
            .cmp(&right.path)
            .then_with(|| left.line_number.cmp(&right.line_number))
            .then_with(|| left.raw.cmp(&right.raw))
    });
}

fn search_file_streaming_standard(
    config: &NativeSearchConfig,
    matcher: &RegexMatcher,
    path: &Path,
    flush_first_match_immediately: bool,
) -> Result<FileSearchResult> {
    search_file_streaming_standard_sequential(config, matcher, path, flush_first_match_immediately)
}

fn search_file_streaming_standard_sequential(
    config: &NativeSearchConfig,
    matcher: &RegexMatcher,
    path: &Path,
    flush_first_match_immediately: bool,
) -> Result<FileSearchResult> {
    if can_stream_plain_matches(config) {
        return search_file_streaming_plain_sequential(
            config,
            matcher,
            path,
            flush_first_match_immediately,
        );
    }

    let writer = AtomicLineWriter::new(config.output_target.clone());
    let mut builder = StandardBuilder::new();
    builder.path(config.with_filename);
    builder.only_matching(config.only_matching);

    let mut printer = builder.build_no_color(writer);
    let mut searcher = build_searcher(config, config.line_number);
    let (matches, binary_detected, binary_byte_offset) = {
        let sink = CollectingSink::new(printer.sink_with_path(matcher, path), path.to_path_buf());
        let mut sink = BinaryAwareSink::new(sink);
        searcher
            .search_path(matcher, path, &mut sink)
            .with_context(|| {
                format!(
                    "native standard output search failed for {}",
                    path.display()
                )
            })?;
        let binary_detected = sink.saw_binary();
        let binary_byte_offset = sink.binary_byte_offset();
        let matches = sink.into_inner().into_matches();
        (matches, binary_detected, binary_byte_offset)
    };
    printer.get_mut().get_mut().finish()?;

    let binary_match_detected = binary_file_matches_pattern(matcher, path, binary_detected)?;
    let (matches, match_count) = if binary_detected {
        (Vec::new(), 0)
    } else {
        let match_count = matches.len();
        (matches, match_count)
    };

    Ok(FileSearchResult {
        match_count,
        matches,
        binary_detected,
        binary_match_detected,
        binary_byte_offset,
    })
}

fn search_file_streaming_plain_sequential(
    config: &NativeSearchConfig,
    matcher: &RegexMatcher,
    path: &Path,
    flush_first_match_immediately: bool,
) -> Result<FileSearchResult> {
    let streaming_output_flush_bytes = if cfg!(debug_assertions) {
        STREAMING_OUTPUT_FLUSH_BYTES_DEBUG
    } else {
        STREAMING_OUTPUT_FLUSH_BYTES
    };
    let retain_matches =
        matches!(config.output_target, NativeOutputTarget::Buffer(_)) || cfg!(debug_assertions);
    let mut matches = Vec::new();
    let mut match_count = 0usize;
    let mut pending_output = Vec::with_capacity(streaming_output_flush_bytes);
    let mut emitted_first_chunk = false;
    let path_buf = path.to_path_buf();
    let path_display = path.display().to_string();
    let mut searcher = build_searcher(config, true);
    let mut sink = BinaryAwareSink::new(Bytes(|line_number, line| {
        let trimmed_line = strip_native_line_terminator(line);
        let rendered_bytes = render_matched_line_bytes(config, trimmed_line)
            .map_err(sink_io_error)?
            .into_owned();
        append_standard_match_bytes(
            &mut pending_output,
            config,
            &path_display,
            line_number,
            &rendered_bytes,
        )
        .map_err(sink_io_error)?;
        if flush_first_match_immediately && !emitted_first_chunk {
            config
                .output_target
                .write_all(&pending_output)
                .map_err(sink_io_error)?;
            pending_output.clear();
            emitted_first_chunk = true;
        } else if pending_output.len() >= streaming_output_flush_bytes {
            config
                .output_target
                .write_all(&pending_output)
                .map_err(sink_io_error)?;
            pending_output.clear();
        }
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
    searcher
        .search_path(matcher, path, &mut sink)
        .with_context(|| {
            format!(
                "native standard output search failed for {}",
                path.display()
            )
        })?;

    let binary_detected = sink.saw_binary();
    let binary_match_detected = binary_file_matches_pattern(matcher, path, binary_detected)?;
    let binary_byte_offset = sink.binary_byte_offset();
    if binary_detected {
        matches.clear();
        match_count = 0;
        pending_output.clear();
    }

    if !pending_output.is_empty() {
        config.output_target.write_all(&pending_output)?;
    }

    Ok(FileSearchResult {
        matches,
        match_count,
        binary_detected,
        binary_match_detected,
        binary_byte_offset,
    })
}

fn search_file_streaming_ndjson(
    config: &NativeSearchConfig,
    matcher: &RegexMatcher,
    path: &Path,
) -> Result<FileSearchResult> {
    search_file_streaming_ndjson_sequential(config, matcher, path)
}

fn search_file_streaming_ndjson_sequential(
    config: &NativeSearchConfig,
    matcher: &RegexMatcher,
    path: &Path,
) -> Result<FileSearchResult> {
    let mut searcher = build_searcher(config, true);
    search_file_ndjson_with_searcher(config, matcher, path, &mut searcher)
}

fn search_file(
    config: &NativeSearchConfig,
    matcher: &RegexMatcher,
    path: &Path,
) -> Result<FileSearchResult> {
    if should_use_chunk_parallel_search(config, path)? {
        return search_file_chunk_parallel(config, matcher, path);
    }
    let mut searcher = build_searcher(config, true);
    search_file_collect_matches_with_searcher(config, matcher, path, &mut searcher)
}

fn search_file_count(
    config: &NativeSearchConfig,
    matcher: &RegexMatcher,
    path: &Path,
) -> Result<FileSearchResult> {
    if should_use_chunk_parallel_search(config, path)? {
        return search_file_chunk_parallel(config, matcher, path);
    }
    let mut searcher = build_searcher(config, true);
    search_file_count_with_searcher(matcher, path, &mut searcher)
}

fn build_matcher(config: &NativeSearchConfig) -> Result<RegexMatcher> {
    let mut builder = RegexMatcherBuilder::new();
    builder.case_insensitive(effective_ignore_case(
        &config.pattern,
        config.ignore_case,
        config.smart_case,
    ));
    builder.fixed_strings(config.fixed_strings);
    builder.word(config.word_boundary);
    if config.crlf {
        builder.crlf(true);
    }
    builder.build(&config.pattern).with_context(|| {
        format!(
            "failed to compile native search pattern '{}'",
            config.pattern
        )
    })
}

/// Fail-closed pre-flight for the plain-text native route: can `build_matcher` -- the EXACT
/// matcher `run_native_search` will construct for this request -- compile this pattern under
/// these flags?
///
/// This exists because a `run_native_search` failure is NOT free. `allow_rg_fallback` does catch
/// it and hand the request to real `rg`, but only after printing
/// `warning: native CPU search failed, falling back to ripgrep: failed to compile native search
/// pattern '...'` to stderr -- a line `rg` never emits. So an uncompilable pattern (`[`, `(`,
/// `\Qx\E`, `a{500}{500}{500}`, ...) must be refused BEFORE routing, not discovered mid-request.
///
/// Only the inputs `build_matcher` actually reads are parameters. It also reads `config.crlf`,
/// which no caller on this path ever sets (`--crlf` is a Python-passthrough flag), so the
/// `Default` value is the truthful one here.
pub fn native_search_pattern_compiles(
    pattern: &str,
    ignore_case: bool,
    smart_case: bool,
    fixed_strings: bool,
    word_boundary: bool,
) -> bool {
    let config = NativeSearchConfig {
        pattern: pattern.to_string(),
        ignore_case,
        smart_case,
        fixed_strings,
        word_boundary,
        ..NativeSearchConfig::default()
    };
    build_matcher(&config).is_ok()
}

pub fn effective_ignore_case(pattern: &str, ignore_case: bool, smart_case: bool) -> bool {
    ignore_case || (smart_case && smart_case_pattern_is_case_insensitive(pattern))
}

pub fn smart_case_pattern_is_case_insensitive(pattern: &str) -> bool {
    !pattern.chars().any(|ch| ch.is_uppercase())
}

fn build_searcher(config: &NativeSearchConfig, line_number: bool) -> Searcher {
    let mut builder = SearcherBuilder::new();
    builder.line_number(line_number);
    builder.invert_match(config.invert_match);
    builder.before_context(config.before_context);
    builder.after_context(config.after_context);
    builder.max_matches(config.max_count);
    if config.text {
        builder.binary_detection(BinaryDetection::none());
    } else {
        builder.binary_detection(BinaryDetection::quit(b'\x00'));
    }

    if config.null_data {
        builder.line_terminator(LineTerminator::byte(b'\0'));
    } else if config.crlf {
        builder.line_terminator(LineTerminator::crlf());
    }

    if config.mmap {
        // SAFETY: This is the intended opt-in API from grep-searcher for mmap-backed search.
        builder.memory_map(unsafe { MmapChoice::auto() });
    } else {
        builder.memory_map(MmapChoice::never());
    }

    builder.build()
}

/// A completed walk: the files, plus how many entries the walker could not read.
///
/// Task 276 slice B3. `collect_walked_files` used to return a bare `Vec<PathBuf>`, which gave it
/// no way to tell its caller that the list is INCOMPLETE -- the Err arm printed to stderr and the
/// count died there. Its sibling `search_walk_roots_parallel` never had this problem: its worker
/// owns a `SearchStats` and increments `walk_errors` directly (:1330).
struct WalkedFiles {
    files: Vec<PathBuf>,
    /// Entries the walker reported an I/O error for. Same `is_io()` filter as the streaming path
    /// (:1328) -- a malformed global gitignore must NOT make a complete walk claim incompleteness.
    walk_errors: usize,
}

fn collect_walked_files(config: &NativeSearchConfig, roots: &[PathBuf]) -> Result<WalkedFiles> {
    if let Some(refusal) = check_native_implicit_walk_ceiling(config, roots) {
        return Err(anyhow!(refusal));
    }
    let builder = build_walk_builder(config, roots)?;
    let walked_files = Arc::new(Mutex::new(Vec::new()));
    let shared_files = Arc::clone(&walked_files);
    // Task 276 slice B3. AtomicUsize rather than a Mutex<usize>: this is a pure counter on the
    // hot walker, incremented under contention by every worker thread. Relaxed ordering is
    // sufficient -- no other memory is published through it, and the value is only read after
    // `run()` has joined every worker, which is itself the synchronisation point.
    let walk_errors = Arc::new(AtomicUsize::new(0));
    let shared_walk_errors = Arc::clone(&walk_errors);
    builder.build_parallel().run(|| {
        let shared_files = Arc::clone(&shared_files);
        let shared_walk_errors = Arc::clone(&shared_walk_errors);
        Box::new(move |entry| {
            let entry = match entry {
                Ok(entry) => entry,
                Err(err) => {
                    // Report the per-entry walk error, exactly as the streaming walker in
                    // `search_walk_roots_parallel` already does (task #263). This collector
                    // used to drop the Err arm on the floor with NO output at all, so a
                    // permission-denied subtree vanished from the file list COMPLETELY
                    // silently -- strictly worse than the streaming path, which at least
                    // prints one line. Real `rg` prints one stderr line per unreadable path
                    // and keeps walking every readable file; both tg walkers now match that.
                    //
                    // NOTE (task 280 -> 276 slice A -> 276 slice B3/task 315, CLOSED here).
                    // Printing is only half the contract: `rg` also exits 2 on an unreadable path
                    // while still emitting its matches, and the JSON envelope carries
                    // `result_incomplete` + `incomplete_reason_class` the way the Python routes
                    // have since 276 slice 1 (c0c3404). The rest of that chain lives at:
                    //     :91                     `walk_errors: usize` on SearchStats
                    //     :2436-2438              the envelope emits result_incomplete /
                    //                             incomplete_reason_class / incomplete_paths_count
                    //     main.rs:8388            exit(2) when walk_errors > 0
                    // Do NOT re-derive any of those as missing; earlier revisions of this comment
                    // said they were, and that sent readers off to rebuild what already shipped.
                    //
                    // What WAS missing until slice B3 is the link below: `collect_walked_files`
                    // returned a bare Vec<PathBuf>, so it owned no channel back to its caller and
                    // the count died at this `eprintln!` -- unlike `search_walk_roots_parallel`,
                    // whose worker owns `local_stats` and increments directly (:1330). It now
                    // returns `WalkedFiles`, and `run_native_search` folds the count into `stats`.
                    //
                    // Same `is_io()` gate as the streaming walker (:1328), for the same reason
                    // recorded there: `ignore::Error` also covers a malformed ancestor/global
                    // gitignore, and counting those would label a COMPLETE walk incomplete with a
                    // budget-non-remediable cause. Non-I/O errors still PRINT; they just do not
                    // claim the answer is partial.
                    if err.is_io() {
                        shared_walk_errors.fetch_add(1, Ordering::Relaxed);
                    }
                    eprintln!("tg: {err}");
                    return WalkState::Continue;
                }
            };
            if entry
                .file_type()
                .map(|kind| kind.is_file())
                .unwrap_or(false)
            {
                if let Ok(mut guard) = shared_files.lock() {
                    guard.push(entry.path().to_path_buf());
                }
            }
            WalkState::Continue
        })
    });

    let mut walked_files = walked_files
        .lock()
        .map_err(|_| anyhow!("failed to collect native search walk results"))?
        .clone();
    walked_files.sort_unstable();
    walked_files.dedup();
    Ok(WalkedFiles {
        files: walked_files,
        walk_errors: walk_errors.load(Ordering::Relaxed),
    })
}

fn build_walk_builder(config: &NativeSearchConfig, roots: &[PathBuf]) -> Result<WalkBuilder> {
    let first_root = roots[0].clone();
    let mut builder = WalkBuilder::new(&first_root);
    for root in roots.iter().skip(1) {
        builder.add(root);
    }
    builder.hidden(!config.hidden);
    builder.max_depth(config.max_depth);
    builder.threads(0);

    if config.no_ignore {
        builder.ignore(false);
        builder.git_ignore(false);
        builder.git_global(false);
        builder.git_exclude(false);
        builder.parents(false);
    } else {
        // Task #267 BLOCKING-1 (independent gate on the first cut of this fix): the `add_ignore`
        // trio below exists SOLELY to compensate for the `ignore` crate's own `require_git(true)`
        // default OUTSIDE a git repository (see `index.rs`'s identical `collect_file_entries`
        // comment) -- INSIDE a git repo, `WalkBuilder`'s own git machinery
        // (`git_ignore`/`git_global`/`git_exclude`, all `true` by default) already applies
        // `.gitignore` natively, so the per-filename `.gitignore` skip immediately below this
        // comment is a NO-OP there: nothing ever called `add_ignore(".gitignore")` to skip in the
        // first place. The first cut of this fix only touched that no-op half and left the
        // walker's own git knobs untouched, so `--no-ignore-vcs` kept doing nothing inside a git
        // repo (execution-verified on the byte-identical walker: a root `.gitignore` + a child
        // dir with no ignore files of its own still excluded the git-ignored file under
        // `--json --no-ignore-vcs`, because the native git path -- not `add_ignore` -- was the
        // one honoring it). Flipping these three knobs is the actual fix for that path; it must
        // NOT also flip `parents(false)` -- unlike blanket `--no-ignore`, `--no-ignore-vcs` is
        // scoped to VCS-sourced ignore files only, and `.ignore`/`.rgignore` parent-directory
        // ascent must keep working. `git_exclude(false)` is also required for
        // `.git/info/exclude`, which real `rg --no-ignore-vcs` re-includes too (verified live)
        // and which is a VCS-scoped ignore source distinct from `.gitignore` proper.
        if config.no_ignore_vcs {
            builder.git_ignore(false);
            builder.git_global(false);
            builder.git_exclude(false);
        }
        for root in roots {
            for ignore_name in [".ignore", ".gitignore", ".rgignore"] {
                // Mirrors `rg_passthrough::root_ignore_file_args`'s per-filename `no_ignore_vcs`
                // scope for the NON-git-repo case. NOT rg-parity: real rg deliberately never
                // honors a root `.gitignore` outside a git repo at all (that's `--no-require-git`
                // territory, rejected on purpose -- see `rg_passthrough.rs::root_ignore_file_args`'s
                // own doc comment); this `add_ignore` trio is tg's own deliberate DIVERGENCE from
                // that rg behavior (#127), not compensation for a gap in rg itself. `.gitignore`
                // is the only VCS-sourced filename in this trio, so it alone is skipped here when
                // `no_ignore_vcs` is set; `.ignore`/`.rgignore` stay honored exactly like the
                // rg-passthrough engine, both inside and outside a git repo.
                if ignore_name == ".gitignore" && config.no_ignore_vcs {
                    continue;
                }
                let ignore_path = root.join(ignore_name);
                if ignore_path.is_file() {
                    builder.add_ignore(ignore_path);
                }
            }
        }
    }

    if !config.glob.is_empty() {
        let mut overrides = OverrideBuilder::new(&first_root);
        for glob in &config.glob {
            overrides
                .add(glob)
                .with_context(|| format!("failed to add glob override '{glob}'"))?;
        }
        builder.overrides(
            overrides
                .build()
                .context("failed to build ignore override matcher")?,
        );
    }

    Ok(builder)
}

fn should_use_chunk_parallel_search(config: &NativeSearchConfig, path: &Path) -> Result<bool> {
    if !config.parallel_large_files
        || !config.mmap
        || config.null_data
        || config.ndjson
        || (!config.json && !config.count && !config.quiet)
        || config.only_matching
        || config.before_context > 0
        || config.after_context > 0
        || config.max_count.is_some()
        || configured_chunk_parallelism_threads(config) < 2
    {
        return Ok(false);
    }

    let file_len = std::fs::metadata(path)
        .with_context(|| {
            format!(
                "failed to read native search metadata for {}",
                path.display()
            )
        })?
        .len();
    Ok(file_len >= config.large_file_chunk_threshold_bytes as u64)
}

fn configured_chunk_parallelism_threads(config: &NativeSearchConfig) -> usize {
    config.chunk_parallelism_threads.unwrap_or_else(|| {
        std::thread::available_parallelism()
            .map(|count| count.get())
            .unwrap_or(1)
    })
}

/// Detects binary content the same way `build_searcher` configures every serial-path `Searcher`
/// to: presence of a NUL byte within the guaranteed-detection prefix
/// (`BINARY_DETECTION_PREFIX_BYTES`) means binary, UNLESS `config.text` is set (mirrors
/// `BinaryDetection::none()` -- `--text` never treats input as binary). Returns the offset of the
/// first NUL byte found (relative to the start of `contents`), or `None` if the prefix is clean.
/// Deliberately does NOT scan past the guaranteed prefix -- doing so would make this path detect
/// binary content the serial path would miss for the same file, which is its own divergent-
/// detection bug.
fn detect_binary_prefix(config: &NativeSearchConfig, contents: &[u8]) -> Option<u64> {
    if config.text {
        return None;
    }
    let prefix_len = contents.len().min(BINARY_DETECTION_PREFIX_BYTES);
    memchr(b'\x00', &contents[..prefix_len]).map(|offset| offset as u64)
}

fn search_file_chunk_parallel(
    config: &NativeSearchConfig,
    matcher: &RegexMatcher,
    path: &Path,
) -> Result<FileSearchResult> {
    let file = File::open(path)
        .with_context(|| format!("failed to open native search path {}", path.display()))?;
    let mmap = {
        // SAFETY: The file handle remains alive for the lifetime of the mmap, and the mapping is read-only.
        unsafe { MmapOptions::new().map(&file) }
    }
    .with_context(|| format!("failed to memory-map native search path {}", path.display()))?;

    let requested_chunk_count = configured_chunk_parallelism_threads(config);
    let chunk_plan = plan_file_chunks(&mmap, requested_chunk_count, config.count);
    if chunk_plan.len() <= 1 {
        if config.count {
            let mut searcher = build_searcher(config, true);
            return search_file_count_with_searcher(matcher, path, &mut searcher);
        }
        return search_file_json(config, matcher, path);
    }

    // The per-chunk searches below run on raw `&[u8]` slices via `search_slice` with a bare
    // `Bytes` sink (not wrapped in `BinaryAwareSink`), so any `binary_data` callback a per-chunk
    // `Searcher` fires internally never reaches this function. Detect binary content over the
    // whole file up front -- mirroring the serial path's GUARANTEED detection floor (see
    // `detect_binary_prefix`; grep_searcher's mmap `BinaryDetection::quit` also opportunistically
    // scans bytes inside matched/context lines beyond that floor, which this check does not
    // reproduce -- a conservative gap, since it can only under-flag relative to the serial path,
    // never over-flag) -- so a binary file above the chunk-parallel threshold is flagged/skipped
    // like the serial path instead of falling through to the parallel scan and emitting raw byte
    // "matches" (mojibake).
    if let Some(binary_byte_offset) = detect_binary_prefix(config, &mmap) {
        let binary_match_detected = binary_file_matches_pattern(matcher, path, true)?;
        return Ok(FileSearchResult {
            matches: Vec::new(),
            match_count: 0,
            binary_detected: true,
            binary_match_detected,
            binary_byte_offset: Some(binary_byte_offset),
        });
    }

    if config.verbose {
        emit_chunk_parallel_debug(path, mmap.len(), requested_chunk_count, &chunk_plan);
    }

    if config.count {
        let chunk_counts = chunk_plan
            .par_iter()
            .map(|chunk| {
                search_chunk_count(
                    config,
                    matcher,
                    path,
                    &mmap[chunk.byte_start..chunk.byte_end],
                )
            })
            .collect::<Vec<_>>();

        let mut match_count = 0usize;
        for count_result in chunk_counts {
            match_count = match_count.saturating_add(count_result?);
        }

        return Ok(FileSearchResult {
            matches: Vec::new(),
            match_count,
            // Confirmed non-binary by the `detect_binary_prefix` early return above.
            binary_detected: false,
            binary_match_detected: false,
            binary_byte_offset: None,
        });
    }

    let chunk_matches = chunk_plan
        .par_iter()
        .map(|chunk| {
            search_chunk(
                config,
                matcher,
                path,
                &mmap[chunk.byte_start..chunk.byte_end],
                chunk.first_line_number,
            )
        })
        .collect::<Vec<_>>();

    let mut matches = Vec::new();
    for chunk_result in chunk_matches {
        matches.extend(chunk_result?);
    }

    Ok(FileSearchResult {
        match_count: matches.len(),
        matches,
        // Confirmed non-binary by the `detect_binary_prefix` early return above.
        binary_detected: false,
        binary_match_detected: false,
        binary_byte_offset: None,
    })
}

fn plan_file_chunks(
    contents: &[u8],
    requested_chunk_count: usize,
    count_only: bool,
) -> Vec<FileChunkPlan> {
    if contents.is_empty() || requested_chunk_count == 0 {
        return Vec::new();
    }

    let target_chunk_size = contents.len().div_ceil(requested_chunk_count);
    let mut ranges = Vec::new();
    let mut byte_start = 0usize;

    while byte_start < contents.len() {
        let minimum_end = byte_start
            .saturating_add(target_chunk_size)
            .min(contents.len());
        let byte_end = if minimum_end >= contents.len() {
            contents.len()
        } else {
            align_chunk_end_to_newline(contents, minimum_end)
        };
        if byte_end <= byte_start {
            break;
        }
        ranges.push((byte_start, byte_end));
        byte_start = byte_end;
    }

    let mut chunks = Vec::with_capacity(ranges.len());
    let mut first_line_number = 1u64;
    for (byte_start, byte_end) in ranges {
        chunks.push(FileChunkPlan {
            byte_start,
            byte_end,
            first_line_number,
        });
        if !count_only {
            first_line_number =
                first_line_number.saturating_add(count_lines(&contents[byte_start..byte_end]));
        }
    }
    chunks
}

fn align_chunk_end_to_newline(contents: &[u8], minimum_end: usize) -> usize {
    if minimum_end == 0 || minimum_end >= contents.len() {
        return contents.len();
    }
    if contents[minimum_end - 1] == b'\n' {
        return minimum_end;
    }
    match memchr(b'\n', &contents[minimum_end..]) {
        Some(relative_offset) => minimum_end + relative_offset + 1,
        None => contents.len(),
    }
}

fn count_lines(contents: &[u8]) -> u64 {
    if contents.is_empty() {
        return 0;
    }
    let newline_count = memchr_iter(b'\n', contents).count() as u64;
    if contents.last() == Some(&b'\n') {
        newline_count
    } else {
        newline_count + 1
    }
}

fn emit_chunk_parallel_debug(
    path: &Path,
    file_len: usize,
    requested_chunk_count: usize,
    chunk_plan: &[FileChunkPlan],
) {
    eprintln!(
        "[native-search] chunk_parallel file={} size_bytes={} requested_chunk_count={} chunk_count={}",
        path.display(),
        file_len,
        requested_chunk_count,
        chunk_plan.len()
    );
    for (index, chunk) in chunk_plan.iter().enumerate() {
        eprintln!(
            "[native-search] chunk[{index}] byte_start={} byte_end={} first_line={}",
            chunk.byte_start, chunk.byte_end, chunk.first_line_number
        );
    }
}

fn search_chunk(
    config: &NativeSearchConfig,
    matcher: &RegexMatcher,
    path: &Path,
    contents: &[u8],
    first_line_number: u64,
) -> Result<Vec<NativeSearchMatch>> {
    let mut matches = Vec::new();
    let mut searcher = build_searcher(config, true);
    let path_buf = path.to_path_buf();
    searcher
        .search_slice(
            matcher,
            contents,
            Bytes(|line_number, line| {
                let trimmed_line = strip_native_line_terminator(line);
                let rendered_bytes = render_matched_line_bytes(config, trimmed_line)
                    .map_err(sink_io_error)?
                    .into_owned();
                matches.push(NativeSearchMatch {
                    path: path_buf.clone(),
                    line_number: Some(first_line_number + line_number - 1),
                    raw: rendered_bytes,
                });
                Ok(true)
            }),
        )
        .with_context(|| format!("native chunk-parallel search failed for {}", path.display()))?;
    Ok(matches)
}

fn search_chunk_count(
    config: &NativeSearchConfig,
    matcher: &RegexMatcher,
    path: &Path,
    contents: &[u8],
) -> Result<usize> {
    let mut match_count = 0usize;
    let mut searcher = build_searcher(config, true);
    searcher
        .search_slice(
            matcher,
            contents,
            Bytes(|_, _| {
                match_count = match_count.saturating_add(1);
                Ok(true)
            }),
        )
        .with_context(|| {
            format!(
                "native chunk-parallel count search failed for {}",
                path.display()
            )
        })?;
    Ok(match_count)
}

fn search_file_collect_matches_with_searcher(
    config: &NativeSearchConfig,
    matcher: &RegexMatcher,
    path: &Path,
    searcher: &mut Searcher,
) -> Result<FileSearchResult> {
    let path_buf = path.to_path_buf();
    let mut matches = Vec::new();
    let mut sink = BinaryAwareSink::new(Bytes(|line_number, line| {
        let trimmed_line = strip_native_line_terminator(line);
        let rendered_bytes = render_matched_line_bytes(config, trimmed_line)
            .map_err(sink_io_error)?
            .into_owned();
        matches.push(NativeSearchMatch {
            path: path_buf.clone(),
            line_number: Some(line_number),
            raw: rendered_bytes,
        });
        Ok(true)
    }));
    searcher
        .search_path(matcher, path, &mut sink)
        .with_context(|| format!("native search failed for {}", path.display()))?;

    let binary_detected = sink.saw_binary();
    let binary_byte_offset = sink.binary_byte_offset();
    let binary_match_detected = binary_file_matches_pattern(matcher, path, binary_detected)?;
    if binary_detected {
        matches.clear();
    }

    Ok(FileSearchResult {
        match_count: matches.len(),
        matches,
        binary_detected,
        binary_match_detected,
        binary_byte_offset,
    })
}

fn search_file_ndjson_with_searcher(
    config: &NativeSearchConfig,
    matcher: &RegexMatcher,
    path: &Path,
    searcher: &mut Searcher,
) -> Result<FileSearchResult> {
    let mut matches = Vec::new();
    let path_buf = path.to_path_buf();
    let search_path = display_search_path(&config.paths);
    let mut sink = BinaryAwareSink::new(Bytes(|line_number, line| {
        let trimmed_line = strip_native_line_terminator(line);
        let rendered_bytes = render_matched_line_bytes(config, trimmed_line)
            .map_err(sink_io_error)?
            .into_owned();
        let matched = NativeSearchMatch {
            path: path_buf.clone(),
            line_number: Some(line_number),
            raw: rendered_bytes,
        };
        emit_ndjson_match(config, &search_path, &matched).map_err(sink_io_error)?;
        matches.push(matched);
        Ok(true)
    }));
    searcher
        .search_path(matcher, path, &mut sink)
        .with_context(|| format!("native NDJSON search failed for {}", path.display()))?;

    let binary_detected = sink.saw_binary();
    let binary_byte_offset = sink.binary_byte_offset();
    let binary_match_detected = binary_file_matches_pattern(matcher, path, binary_detected)?;
    if binary_detected {
        matches.clear();
    }

    Ok(FileSearchResult {
        match_count: matches.len(),
        matches,
        binary_detected,
        binary_match_detected,
        binary_byte_offset,
    })
}

fn binary_file_matches_pattern(
    matcher: &RegexMatcher,
    path: &Path,
    binary_detected: bool,
) -> Result<bool> {
    if !binary_detected {
        return Ok(false);
    }

    use std::io::Read;

    const MAX_BINARY_PROBE_BYTES: u64 = 64 * 1024 * 1024;

    let file = fs::File::open(path)
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
fn emit_binary_match_warning(
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
        // `"\0"`, verified via hexdump against `rg.exe` 15.1.0 -- `rg needle bin.dat` prints
        // `binary file matches (found "\0" byte around offset 5)`. The previous `"/0"` spelling
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

fn search_file_count_with_searcher(
    matcher: &RegexMatcher,
    path: &Path,
    searcher: &mut Searcher,
) -> Result<FileSearchResult> {
    let mut match_count = 0usize;
    let mut sink = BinaryAwareSink::new(Bytes(|_, _| {
        match_count = match_count.saturating_add(1);
        Ok(true)
    }));
    searcher
        .search_path(matcher, path, &mut sink)
        .with_context(|| format!("native count output search failed for {}", path.display()))?;

    let binary_detected = sink.saw_binary();
    let binary_byte_offset = sink.binary_byte_offset();
    let binary_match_detected = binary_file_matches_pattern(matcher, path, binary_detected)?;
    if binary_detected {
        match_count = 0;
    }

    Ok(FileSearchResult {
        matches: Vec::new(),
        match_count,
        binary_detected,
        binary_match_detected,
        binary_byte_offset,
    })
}

fn search_file_json(
    config: &NativeSearchConfig,
    matcher: &RegexMatcher,
    path: &Path,
) -> Result<FileSearchResult> {
    let mut searcher = build_searcher(config, true);
    search_file_collect_matches_with_searcher(config, matcher, path, &mut searcher)
}

fn emit_count_output_from_matches(
    config: &NativeSearchConfig,
    path: &Path,
    count: usize,
) -> Result<()> {
    let mut bytes = Vec::new();
    append_count_output_bytes(&mut bytes, config, path, count)?;
    config.output_target.write_all(&bytes)
}

fn append_count_output_bytes(
    bytes: &mut Vec<u8>,
    config: &NativeSearchConfig,
    path: &Path,
    count: usize,
) -> Result<()> {
    if config.with_filename {
        writeln!(bytes, "{}:{count}", path.display())?;
    } else {
        writeln!(bytes, "{count}")?;
    }
    Ok(())
}

fn can_stream_plain_matches(config: &NativeSearchConfig) -> bool {
    config.before_context == 0 && config.after_context == 0 && !config.only_matching
}

/// Writes a matched line to plain-text (non-JSON) output. `text` is the exact bytes to emit
/// for the line's content -- written raw via `extend_from_slice`, never through `Display`/
/// `write!`, so a genuine trailing `\r` (CRLF content) or non-UTF-8 byte survives byte-for-byte
/// instead of being re-encoded (task #266).
fn append_standard_match_bytes(
    bytes: &mut Vec<u8>,
    config: &NativeSearchConfig,
    path_display: &str,
    line_number: u64,
    text: &[u8],
) -> Result<()> {
    if config.with_filename && config.line_number {
        write!(bytes, "{path_display}:{line_number}:")?;
    } else if config.with_filename {
        write!(bytes, "{path_display}:")?;
    } else if config.line_number {
        write!(bytes, "{line_number}:")?;
    }
    bytes.extend_from_slice(text);
    bytes.push(b'\n');
    Ok(())
}

fn native_match_from_sink(path: &Path, mat: &SinkMatch<'_>) -> NativeSearchMatch {
    NativeSearchMatch {
        path: path.to_path_buf(),
        line_number: mat.line_number(),
        raw: strip_native_line_terminator(mat.bytes()).to_vec(),
    }
}

fn emit_json_matches(config: &NativeSearchConfig, stats: &SearchStats) -> Result<()> {
    let proof_fields = gpu_proof_fields(
        &config.requested_gpu_device_ids,
        config.routing_backend,
        config.sidecar_used,
    );
    let mut match_counts_by_file: BTreeMap<String, usize> = BTreeMap::new();
    for matched in &stats.matches {
        let path = matched.path.to_string_lossy().into_owned();
        *match_counts_by_file.entry(path).or_insert(0) += 1;
    }
    let matched_file_paths = match_counts_by_file.keys().cloned().collect::<Vec<_>>();
    let (path_was_defaulted, scope_note) =
        defaulted_scope_fields(config.path_was_implicit, stats.total_matches);
    let payload = NativeJsonOutput {
        version: JSON_OUTPUT_VERSION,
        routing_backend: config.routing_backend,
        routing_reason: config.routing_reason,
        sidecar_used: config.sidecar_used,
        requested_gpu_device_ids: config.requested_gpu_device_ids.clone(),
        routing_gpu_device_ids: Vec::new(),
        gpu_evidence_status: proof_fields.gpu_evidence_status,
        gpu_proof: proof_fields.gpu_proof,
        native_gpu_unavailable: proof_fields.native_gpu_unavailable,
        not_gpu_proof_reason: proof_fields.not_gpu_proof_reason,
        query: &config.pattern,
        path: display_search_path(&config.paths),
        total_files: stats.matched_files,
        total_matches: stats.total_matches,
        matched_file_paths,
        match_counts_by_file,
        matches: stats
            .matches
            .iter()
            .map(native_match_to_json)
            .collect::<Result<Vec<_>>>()?,
        // Task #276 slice B2. Emitted ONLY when the walk actually skipped something, so a
        // complete envelope stays byte-identical to every prior release.
        //
        // The class is `unreadable_path` and not one of the budget causes on purpose: a walk
        // error is the ONE value in the closed set that is NOT budget-remediable. No
        // `--max-repo-files` or `--deadline` value makes a permission-denied subtree readable,
        // and `docs/CONTRACTS.md` says so explicitly. Labelling it `scan_limit` or `deadline`
        // would hand the reader a knob that cannot help -- the wrong-knob defect #283 already
        // cost us once.
        result_incomplete: (stats.walk_errors > 0).then_some(true),
        incomplete_reason_class: (stats.walk_errors > 0).then_some("unreadable_path"),
        incomplete_paths_count: (stats.walk_errors > 0).then_some(stats.walk_errors),
        path_was_defaulted,
        scope_note,
    };

    let mut bytes = serde_json::to_vec(&payload)?;
    bytes.push(b'\n');
    config.output_target.write_all(&bytes)
}

fn emit_ndjson_match(
    config: &NativeSearchConfig,
    search_path: &str,
    matched: &NativeSearchMatch,
) -> Result<()> {
    let mut bytes = Vec::new();
    append_ndjson_match_bytes(&mut bytes, config, search_path, matched)?;
    config.output_target.write_all(&bytes)
}

fn append_ndjson_match_bytes(
    bytes: &mut Vec<u8>,
    config: &NativeSearchConfig,
    search_path: &str,
    matched: &NativeSearchMatch,
) -> Result<()> {
    let line = native_match_line_number(matched)?;
    let file = matched.path.to_string_lossy().into_owned();
    let proof_fields = gpu_proof_fields(
        &config.requested_gpu_device_ids,
        config.routing_backend,
        config.sidecar_used,
    );
    let (text, text_bytes) = native_json_text_fields(&matched.raw);
    let payload = NativeNdjsonMatch {
        version: JSON_OUTPUT_VERSION,
        routing_backend: config.routing_backend,
        routing_reason: config.routing_reason,
        sidecar_used: config.sidecar_used,
        requested_gpu_device_ids: config.requested_gpu_device_ids.clone(),
        routing_gpu_device_ids: Vec::new(),
        gpu_evidence_status: proof_fields.gpu_evidence_status,
        gpu_proof: proof_fields.gpu_proof,
        native_gpu_unavailable: proof_fields.native_gpu_unavailable,
        not_gpu_proof_reason: proof_fields.not_gpu_proof_reason,
        query: &config.pattern,
        path: search_path,
        file: &file,
        line,
        text,
        bytes: text_bytes,
    };

    let mut encoded = serde_json::to_vec(&payload)?;
    encoded.push(b'\n');
    bytes.extend_from_slice(&encoded);
    Ok(())
}

fn native_match_to_json(matched: &NativeSearchMatch) -> Result<NativeJsonMatch> {
    let (text, bytes) = native_json_text_fields(&matched.raw);
    Ok(NativeJsonMatch {
        file: matched.path.to_string_lossy().into_owned(),
        line: native_match_line_number(matched)?,
        text: text.map(str::to_string),
        bytes,
    })
}

fn native_match_line_number(matched: &NativeSearchMatch) -> Result<usize> {
    let line_number = matched
        .line_number
        .ok_or_else(|| anyhow!("native search match missing line number"))?;
    usize::try_from(line_number).context("native search line number overflowed usize")
}

fn display_search_path(paths: &[PathBuf]) -> String {
    paths
        .iter()
        .map(|path| path.display().to_string())
        .collect::<Vec<_>>()
        .join(",")
}

#[cfg(test)]
#[path = "native_search_tests.rs"]
mod tests;
