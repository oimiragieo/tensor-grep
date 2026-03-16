use anyhow::{anyhow, Context, Result};
use grep_matcher::{LineTerminator, Matcher};
use grep_printer::StandardBuilder;
use grep_regex::{RegexMatcher, RegexMatcherBuilder};
use grep_searcher::sinks::Lossy;
use grep_searcher::{
    BinaryDetection, MmapChoice, Searcher, SearcherBuilder, Sink, SinkContext, SinkFinish,
    SinkMatch,
};
use ignore::{overrides::OverrideBuilder, WalkBuilder, WalkState};
use memchr::{memchr, memchr_iter};
use memmap2::MmapOptions;
use rayon::prelude::*;
use serde::Serialize;
use std::fs::{self, File};
use std::io::{self, Write};
use std::path::{Path, PathBuf};
use std::sync::atomic::{AtomicBool, Ordering};
use std::sync::{Arc, Mutex};

const JSON_OUTPUT_VERSION: u32 = 1;
const LARGE_FILE_CHUNK_THRESHOLD_BYTES: usize = 50 * 1024 * 1024;
const STREAMING_OUTPUT_FLUSH_BYTES: usize = 64 * 1024;
const STREAMING_OUTPUT_FLUSH_BYTES_DEBUG: usize = 8 * 1024;

#[derive(Debug, Clone, PartialEq, Eq, Serialize)]
pub struct NativeSearchMatch {
    pub path: PathBuf,
    pub line_number: Option<u64>,
    pub text: String,
}

#[derive(Debug, Clone, Default, PartialEq, Eq, Serialize)]
pub struct SearchStats {
    pub searched_files: usize,
    pub matched_files: usize,
    pub total_matches: usize,
    pub skipped_binary_files: usize,
    pub binary_match_files: usize,
    pub matches: Vec<NativeSearchMatch>,
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
            self.target.write_all(&line).map_err(io::Error::other)?;
        }
        Ok(())
    }

    fn finish(&mut self) -> io::Result<()> {
        self.flush_complete_lines()?;
        if !self.pending.is_empty() {
            self.target.write_all(&self.pending).map_err(io::Error::other)?;
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
    pub ignore_case: bool,
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
    pub null_data: bool,
    pub count: bool,
    pub crlf: bool,
    pub no_ignore: bool,
    pub line_number: bool,
    pub with_filename: bool,
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
            ignore_case: false,
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
            null_data: false,
            count: false,
            crlf: false,
            no_ignore: false,
            line_number: true,
            with_filename: false,
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

#[derive(Debug, Clone, Default)]
struct FileSearchResult {
    matches: Vec<NativeSearchMatch>,
    match_count: usize,
    binary_detected: bool,
    binary_match_detected: bool,
    used_chunk_parallel: bool,
}

#[derive(Debug)]
struct BinaryAwareSink<S> {
    inner: S,
    saw_binary: bool,
}

impl<S> BinaryAwareSink<S> {
    fn new(inner: S) -> Self {
        Self {
            inner,
            saw_binary: false,
        }
    }

    fn saw_binary(&self) -> bool {
        self.saw_binary
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

    fn context(&mut self, searcher: &Searcher, context: &SinkContext<'_>) -> Result<bool, Self::Error> {
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
            config,
        })
    }

    fn search_path(&mut self, path: &Path) -> Result<()> {
        self.output_buffer.clear();

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
            ..
        } = file_result;

        self.local_stats.searched_files += 1;
        if binary_detected {
            self.local_stats.skipped_binary_files += 1;
            if binary_match_detected {
                emit_binary_match_warning(path)?;
                self.local_stats.binary_match_files += 1;
            }
            self.output_buffer.clear();
            return Ok(());
        }

        if !self.output_buffer.is_empty() {
            self.config.output_target.write_all(&self.output_buffer)?;
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
        let mut sink = BinaryAwareSink::new(Lossy(|line_number, line| {
            let trimmed_line = line.trim_end_matches(['\n', '\r']);
            append_standard_match_bytes(
                output_buffer,
                &self.config,
                &path_display,
                line_number,
                trimmed_line,
            )
            .map_err(io::Error::other)?;
            match_count = match_count.saturating_add(1);
            if retain_matches {
                matches.push(NativeSearchMatch {
                    path: path_buf.clone(),
                    line_number: Some(line_number),
                    text: trimmed_line.to_string(),
                });
            }
            Ok(true)
        }));

        self.searcher_with_line_numbers
            .search_path(&self.matcher, path, &mut sink)
            .with_context(|| format!("native standard output search failed for {}", path.display()))?;

        let binary_detected = sink.saw_binary();
        let binary_match_detected = binary_file_matches_pattern(&self.matcher, path, binary_detected)?;
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
            used_chunk_parallel: false,
        })
    }

    fn search_ndjson(&mut self, path: &Path) -> Result<FileSearchResult> {
        let mut matches = Vec::new();
        let mut match_count = 0usize;
        let path_buf = path.to_path_buf();
        let search_path = self.search_path.clone();
        let output_buffer = &mut self.output_buffer;
        let mut sink = BinaryAwareSink::new(Lossy(|line_number, line| {
            let trimmed_line = line.trim_end_matches(['\n', '\r']);
            let matched = NativeSearchMatch {
                path: path_buf.clone(),
                line_number: Some(line_number),
                text: trimmed_line.to_string(),
            };
            append_ndjson_match_bytes(output_buffer, &self.config, &search_path, &matched)
                .map_err(io::Error::other)?;
            match_count = match_count.saturating_add(1);
            matches.push(matched);
            Ok(true)
        }));

        self.searcher_with_line_numbers
            .search_path(&self.matcher, path, &mut sink)
            .with_context(|| format!("native NDJSON search failed for {}", path.display()))?;

        let binary_detected = sink.saw_binary();
        let binary_match_detected = binary_file_matches_pattern(&self.matcher, path, binary_detected)?;
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
            used_chunk_parallel: false,
        })
    }

    fn search_count(&mut self, path: &Path) -> Result<FileSearchResult> {
        let mut match_count = 0usize;
        let mut sink = BinaryAwareSink::new(Lossy(|_, _| {
            match_count = match_count.saturating_add(1);
            Ok(true)
        }));

        self.searcher_with_line_numbers
            .search_path(&self.matcher, path, &mut sink)
            .with_context(|| format!("native count output search failed for {}", path.display()))?;

        let binary_detected = sink.saw_binary();
        let binary_match_detected = binary_file_matches_pattern(&self.matcher, path, binary_detected)?;
        if !binary_detected {
            append_count_output_bytes(&mut self.output_buffer, &self.config, path, match_count)?;
        } else {
            match_count = 0;
        }

        Ok(FileSearchResult {
            matches: Vec::new(),
            match_count,
            binary_detected,
            binary_match_detected,
            used_chunk_parallel: false,
        })
    }
}

impl Drop for ParallelWalkWorker {
    fn drop(&mut self) {
        if self.local_stats.searched_files == 0
            && self.local_stats.matched_files == 0
            && self.local_stats.total_matches == 0
            && self.local_stats.skipped_binary_files == 0
            && self.local_stats.matches.is_empty()
        {
            return;
        }

        if let Ok(mut shared_stats) = self.shared_stats.lock() {
            merge_search_stats(&mut shared_stats, std::mem::take(&mut self.local_stats));
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

    fn context(&mut self, searcher: &Searcher, context: &SinkContext<'_>) -> Result<bool, Self::Error> {
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
    query: &'a str,
    path: String,
    total_matches: usize,
    matches: Vec<NativeJsonMatch>,
}

#[derive(Debug, Serialize)]
struct NativeJsonMatch {
    file: String,
    line: usize,
    text: String,
}

#[derive(Debug, Serialize)]
struct NativeNdjsonMatch<'a> {
    version: u32,
    routing_backend: &'static str,
    routing_reason: &'static str,
    sidecar_used: bool,
    query: &'a str,
    path: &'a str,
    file: &'a str,
    line: usize,
    text: &'a str,
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
            let files = collect_walked_files(&effective_config, &inputs.roots)?;
            run_native_search_files(&effective_config, &matcher, files)?
        };
        merge_search_stats(&mut stats, root_stats);
    }

    sort_search_matches(&mut stats.matches);

    if effective_config.json {
        emit_json_matches(&effective_config, &stats)?;
    }

    Ok(stats)
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
        } else if config.count || config.quiet {
            search_file(config, matcher, &file_path)?
        } else {
            search_file_streaming_standard(config, matcher, &file_path, !emitted_stream_output)?
        };

        let FileSearchResult {
            matches,
            match_count,
            binary_detected,
            binary_match_detected,
            used_chunk_parallel,
        } = file_result;

        stats.searched_files += 1;
        if binary_detected {
            stats.skipped_binary_files += 1;
            if binary_match_detected {
                emit_binary_match_warning(&file_path)?;
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
            if used_chunk_parallel {
                emit_count_output_from_matches(config, &file_path, match_count)?;
            } else {
                emit_count_output(config, matcher, &file_path)?;
            }
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
            return Err(anyhow!("native search path does not exist: {}", path.display()));
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

fn search_walk_roots_parallel(config: &NativeSearchConfig, roots: &[PathBuf]) -> Result<SearchStats> {
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
                    should_quit.store(true, Ordering::Relaxed);
                    if let Ok(mut guard) = shared_error.lock() {
                        if guard.is_none() {
                            *guard = Some(anyhow!(err.to_string()));
                        }
                    }
                    return WalkState::Quit;
                }
            };

            if !entry.file_type().map(|kind| kind.is_file()).unwrap_or(false) {
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
                should_quit.store(true, Ordering::Relaxed);
                if let Ok(mut guard) = shared_error.lock() {
                    if guard.is_none() {
                        *guard = Some(err);
                    }
                }
                return WalkState::Quit;
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
    target.matches.extend(source.matches);
}

fn sort_search_matches(matches: &mut [NativeSearchMatch]) {
    matches.sort_by(|left, right| {
        left.path
            .cmp(&right.path)
            .then_with(|| left.line_number.cmp(&right.line_number))
            .then_with(|| left.text.cmp(&right.text))
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
    let (matches, binary_detected) = {
        let sink = CollectingSink::new(printer.sink_with_path(matcher, path), path.to_path_buf());
        let mut sink = BinaryAwareSink::new(sink);
        searcher
            .search_path(matcher, path, &mut sink)
            .with_context(|| format!("native standard output search failed for {}", path.display()))?;
        let binary_detected = sink.saw_binary();
        let matches = sink.into_inner().into_matches();
        (matches, binary_detected)
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
        used_chunk_parallel: false,
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
    let retain_matches = matches!(config.output_target, NativeOutputTarget::Buffer(_))
        || cfg!(debug_assertions);
    let mut matches = Vec::new();
    let mut match_count = 0usize;
    let mut pending_output = Vec::with_capacity(streaming_output_flush_bytes);
    let mut emitted_first_chunk = false;
    let path_buf = path.to_path_buf();
    let path_display = path.display().to_string();
    let mut searcher = build_searcher(config, true);
    let mut sink = BinaryAwareSink::new(Lossy(|line_number, line| {
        let trimmed_line = line.trim_end_matches(['\n', '\r']);
        append_standard_match_bytes(
            &mut pending_output,
            config,
            &path_display,
            line_number,
            trimmed_line,
        )
        .map_err(io::Error::other)?;
        if flush_first_match_immediately && !emitted_first_chunk {
            config
                .output_target
                .write_all(&pending_output)
                .map_err(io::Error::other)?;
            pending_output.clear();
            emitted_first_chunk = true;
        } else if pending_output.len() >= streaming_output_flush_bytes {
            config
                .output_target
                .write_all(&pending_output)
                .map_err(io::Error::other)?;
            pending_output.clear();
        }
        match_count = match_count.saturating_add(1);
        if retain_matches {
            matches.push(NativeSearchMatch {
                path: path_buf.clone(),
                line_number: Some(line_number),
                text: trimmed_line.to_string(),
            });
        }
        Ok(true)
    }));
    searcher
        .search_path(matcher, path, &mut sink)
        .with_context(|| format!("native standard output search failed for {}", path.display()))?;

    let binary_detected = sink.saw_binary();
    let binary_match_detected = binary_file_matches_pattern(matcher, path, binary_detected)?;
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
        used_chunk_parallel: false,
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

fn search_file(config: &NativeSearchConfig, matcher: &RegexMatcher, path: &Path) -> Result<FileSearchResult> {
    if should_use_chunk_parallel_search(config, path)? {
        return search_file_chunk_parallel(config, matcher, path);
    }
    let mut searcher = build_searcher(config, true);
    search_file_collect_matches_with_searcher(config, matcher, path, &mut searcher)
}

fn build_matcher(config: &NativeSearchConfig) -> Result<RegexMatcher> {
    let mut builder = RegexMatcherBuilder::new();
    builder.case_insensitive(config.ignore_case);
    builder.fixed_strings(config.fixed_strings);
    builder.word(config.word_boundary);
    if config.crlf {
        builder.crlf(true);
    }
    builder
        .build(&config.pattern)
        .with_context(|| format!("failed to compile native search pattern '{}'", config.pattern))
}

fn build_searcher(config: &NativeSearchConfig, line_number: bool) -> Searcher {
    let mut builder = SearcherBuilder::new();
    builder.line_number(line_number);
    builder.invert_match(config.invert_match);
    builder.before_context(config.before_context);
    builder.after_context(config.after_context);
    builder.max_matches(config.max_count);
    builder.binary_detection(BinaryDetection::quit(b'\x00'));

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

fn collect_walked_files(config: &NativeSearchConfig, roots: &[PathBuf]) -> Result<Vec<PathBuf>> {
    let builder = build_walk_builder(config, roots)?;
    let walked_files = Arc::new(Mutex::new(Vec::new()));
    let shared_files = Arc::clone(&walked_files);
    builder.build_parallel().run(|| {
        let shared_files = Arc::clone(&shared_files);
        Box::new(move |entry| {
            if let Ok(entry) = entry {
                if entry.file_type().map(|kind| kind.is_file()).unwrap_or(false) {
                    if let Ok(mut guard) = shared_files.lock() {
                        guard.push(entry.path().to_path_buf());
                    }
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
    Ok(walked_files)
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
        for root in roots {
            for ignore_name in [".ignore", ".gitignore", ".rgignore"] {
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
        .with_context(|| format!("failed to read native search metadata for {}", path.display()))?
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
        return search_file_json(config, matcher, path);
    }

    if config.verbose {
        emit_chunk_parallel_debug(path, mmap.len(), requested_chunk_count, &chunk_plan);
    }

    if config.count {
        let chunk_counts = chunk_plan
            .par_iter()
            .map(|chunk| search_chunk_count(config, matcher, path, &mmap[chunk.byte_start..chunk.byte_end]))
            .collect::<Vec<_>>();

        let mut match_count = 0usize;
        for count_result in chunk_counts {
            match_count = match_count.saturating_add(count_result?);
        }

        return Ok(FileSearchResult {
            matches: Vec::new(),
            match_count,
            binary_detected: false,
            binary_match_detected: false,
            used_chunk_parallel: true,
        });
    }

    let chunk_matches = chunk_plan
        .par_iter()
        .map(|chunk| search_chunk(config, matcher, path, &mmap[chunk.byte_start..chunk.byte_end], chunk.first_line_number))
        .collect::<Vec<_>>();

    let mut matches = Vec::new();
    for chunk_result in chunk_matches {
        matches.extend(chunk_result?);
    }

    Ok(FileSearchResult {
        match_count: matches.len(),
        matches,
        binary_detected: false,
        binary_match_detected: false,
        used_chunk_parallel: true,
    })
}

fn plan_file_chunks(contents: &[u8], requested_chunk_count: usize, count_only: bool) -> Vec<FileChunkPlan> {
    if contents.is_empty() || requested_chunk_count == 0 {
        return Vec::new();
    }

    let target_chunk_size = contents.len().div_ceil(requested_chunk_count);
    let mut ranges = Vec::new();
    let mut byte_start = 0usize;

    while byte_start < contents.len() {
        let minimum_end = byte_start.saturating_add(target_chunk_size).min(contents.len());
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
            chunk.byte_start,
            chunk.byte_end,
            chunk.first_line_number
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
            Lossy(|line_number, line| {
                matches.push(NativeSearchMatch {
                    path: path_buf.clone(),
                    line_number: Some(first_line_number + line_number - 1),
                    text: line.trim_end_matches(['\n', '\r']).to_string(),
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
            Lossy(|_, _| {
                match_count = match_count.saturating_add(1);
                Ok(true)
            }),
        )
        .with_context(|| format!("native chunk-parallel count search failed for {}", path.display()))?;
    Ok(match_count)
}

fn search_file_collect_matches_with_searcher(
    _config: &NativeSearchConfig,
    matcher: &RegexMatcher,
    path: &Path,
    searcher: &mut Searcher,
) -> Result<FileSearchResult> {
    let path_buf = path.to_path_buf();
    let mut matches = Vec::new();
    let mut sink = BinaryAwareSink::new(Lossy(|line_number, line| {
        matches.push(NativeSearchMatch {
            path: path_buf.clone(),
            line_number: Some(line_number),
            text: line.trim_end_matches(['\n', '\r']).to_string(),
        });
        Ok(true)
    }));
    searcher
        .search_path(matcher, path, &mut sink)
        .with_context(|| format!("native search failed for {}", path.display()))?;

    let binary_detected = sink.saw_binary();
    let binary_match_detected = binary_file_matches_pattern(matcher, path, binary_detected)?;
    if binary_detected {
        matches.clear();
    }

    Ok(FileSearchResult {
        match_count: matches.len(),
        matches,
        binary_detected,
        binary_match_detected,
        used_chunk_parallel: false,
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
    let mut sink = BinaryAwareSink::new(Lossy(|line_number, line| {
        let matched = NativeSearchMatch {
            path: path_buf.clone(),
            line_number: Some(line_number),
            text: line.trim_end_matches(['\n', '\r']).to_string(),
        };
        emit_ndjson_match(config, &search_path, &matched).map_err(io::Error::other)?;
        matches.push(matched);
        Ok(true)
    }));
    searcher
        .search_path(matcher, path, &mut sink)
        .with_context(|| format!("native NDJSON search failed for {}", path.display()))?;

    let binary_detected = sink.saw_binary();
    let binary_match_detected = binary_file_matches_pattern(matcher, path, binary_detected)?;
    if binary_detected {
        matches.clear();
    }

    Ok(FileSearchResult {
        match_count: matches.len(),
        matches,
        binary_detected,
        binary_match_detected,
        used_chunk_parallel: false,
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

    let contents = fs::read(path)
        .with_context(|| format!("failed to read binary candidate {}", path.display()))?;
    match matcher.is_match(&contents) {
        Ok(matched) => Ok(matched),
        Err(_) => unreachable!("grep_regex::RegexMatcher::is_match returned NoError"),
    }
}

fn emit_binary_match_warning(path: &Path) -> Result<()> {
    let mut stderr = io::stderr().lock();
    writeln!(stderr, "Binary file {} matches", path.display())?;
    stderr.flush()?;
    Ok(())
}

fn count_matches_with_searcher(
    matcher: &RegexMatcher,
    path: &Path,
    searcher: &mut Searcher,
) -> Result<usize> {
    let mut match_count = 0usize;
    let mut sink = BinaryAwareSink::new(Lossy(|_, _| {
        match_count = match_count.saturating_add(1);
        Ok(true)
    }));
    searcher
        .search_path(matcher, path, &mut sink)
        .with_context(|| format!("native count output search failed for {}", path.display()))?;

    if sink.saw_binary() {
        Ok(0)
    } else {
        Ok(match_count)
    }
}

fn search_file_json(
    config: &NativeSearchConfig,
    matcher: &RegexMatcher,
    path: &Path,
) -> Result<FileSearchResult> {
    let mut searcher = build_searcher(config, true);
    search_file_collect_matches_with_searcher(config, matcher, path, &mut searcher)
}

fn emit_count_output(config: &NativeSearchConfig, matcher: &RegexMatcher, path: &Path) -> Result<()> {
    let mut searcher = build_searcher(config, true);
    let count = count_matches_with_searcher(matcher, path, &mut searcher)?;
    let mut bytes = Vec::new();
    append_count_output_bytes(&mut bytes, config, path, count)?;
    config.output_target.write_all(&bytes)
}

fn emit_count_output_from_matches(config: &NativeSearchConfig, path: &Path, count: usize) -> Result<()> {
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

fn append_standard_match_bytes(
    bytes: &mut Vec<u8>,
    config: &NativeSearchConfig,
    path_display: &str,
    line_number: u64,
    text: &str,
) -> Result<()> {
    if config.with_filename && config.line_number {
        writeln!(bytes, "{path_display}:{line_number}:{text}")?;
    } else if config.with_filename {
        writeln!(bytes, "{path_display}:{text}")?;
    } else if config.line_number {
        writeln!(bytes, "{line_number}:{text}")?;
    } else {
        writeln!(bytes, "{text}")?;
    }
    Ok(())
}

fn native_match_from_sink(path: &Path, mat: &SinkMatch<'_>) -> NativeSearchMatch {
    NativeSearchMatch {
        path: path.to_path_buf(),
        line_number: mat.line_number(),
        text: String::from_utf8_lossy(mat.bytes())
            .trim_end_matches(['\n', '\r'])
            .to_string(),
    }
}

fn emit_json_matches(config: &NativeSearchConfig, stats: &SearchStats) -> Result<()> {
    let payload = NativeJsonOutput {
        version: JSON_OUTPUT_VERSION,
        routing_backend: config.routing_backend,
        routing_reason: config.routing_reason,
        sidecar_used: config.sidecar_used,
        query: &config.pattern,
        path: display_search_path(&config.paths),
        total_matches: stats.total_matches,
        matches: stats
            .matches
            .iter()
            .map(native_match_to_json)
            .collect::<Result<Vec<_>>>()?,
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
    let payload = NativeNdjsonMatch {
        version: JSON_OUTPUT_VERSION,
        routing_backend: config.routing_backend,
        routing_reason: config.routing_reason,
        sidecar_used: config.sidecar_used,
        query: &config.pattern,
        path: search_path,
        file: &file,
        line,
        text: &matched.text,
    };

    let mut encoded = serde_json::to_vec(&payload)?;
    encoded.push(b'\n');
    bytes.extend_from_slice(&encoded);
    Ok(())
}

fn native_match_to_json(matched: &NativeSearchMatch) -> Result<NativeJsonMatch> {
    Ok(NativeJsonMatch {
        file: matched.path.to_string_lossy().into_owned(),
        line: native_match_line_number(matched)?,
        text: matched.text.clone(),
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
