use anyhow::{anyhow, Context, Result};
use grep_matcher::LineTerminator;
use grep_printer::{JSONBuilder, StandardBuilder, SummaryBuilder, SummaryKind};
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
use serde_json::Value;
use std::collections::BTreeSet;
use std::fs::File;
use std::io::{self, Read, Write};
use std::path::{Path, PathBuf};
use std::sync::{Arc, Mutex};

const JSON_OUTPUT_VERSION: u32 = 1;
const LARGE_FILE_CHUNK_THRESHOLD_BYTES: usize = 50 * 1024 * 1024;

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
    used_chunk_parallel: bool,
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

#[derive(Debug)]
struct StreamingNdjsonSink<'a> {
    config: &'a NativeSearchConfig,
    path: PathBuf,
    search_path: String,
    matches: Vec<NativeSearchMatch>,
}

impl<'a> StreamingNdjsonSink<'a> {
    fn new(config: &'a NativeSearchConfig, path: PathBuf) -> Self {
        Self {
            config,
            path,
            search_path: display_search_path(&config.paths),
            matches: Vec::new(),
        }
    }

    fn into_matches(self) -> Vec<NativeSearchMatch> {
        self.matches
    }
}

impl Sink for StreamingNdjsonSink<'_> {
    type Error = io::Error;

    fn matched(&mut self, _searcher: &Searcher, mat: &SinkMatch<'_>) -> Result<bool, Self::Error> {
        let matched = native_match_from_sink(&self.path, mat);
        emit_ndjson_match(self.config, &self.search_path, &matched).map_err(io::Error::other)?;
        self.matches.push(matched);
        Ok(true)
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

    let matcher = build_matcher(&config)?;
    let files = collect_files(&config)?;
    let mut stats = SearchStats::default();

    for file_path in files {
        if is_binary_file(&file_path)? {
            stats.searched_files += 1;
            stats.skipped_binary_files += 1;
            continue;
        }

        let file_result = if config.json {
            search_file(&config, &matcher, &file_path)?
        } else if config.ndjson {
            search_file_streaming_ndjson(&config, &matcher, &file_path)?
        } else if config.count || config.quiet {
            search_file(&config, &matcher, &file_path)?
        } else {
            search_file_streaming_standard(&config, &matcher, &file_path)?
        };

        stats.searched_files += 1;
        if !file_result.matches.is_empty() {
            stats.matched_files += 1;
            stats.total_matches += file_result.matches.len();
            stats.matches.extend(file_result.matches.clone());
        }

        if config.quiet && !file_result.matches.is_empty() {
            break;
        }

        if config.json || config.ndjson || (!config.count && !config.quiet) {
            continue;
        }

        if config.count {
            if file_result.used_chunk_parallel {
                emit_count_output_from_matches(&config, &file_path, file_result.matches.len())?;
            } else {
                emit_count_output(&config, &matcher, &file_path)?;
            }
        }
    }

    if config.json {
        emit_json_matches(&config, &stats)?;
    }

    Ok(stats)
}

fn search_file_streaming_standard(
    config: &NativeSearchConfig,
    matcher: &RegexMatcher,
    path: &Path,
) -> Result<FileSearchResult> {
    search_file_streaming_standard_sequential(config, matcher, path)
}

fn search_file_streaming_standard_sequential(
    config: &NativeSearchConfig,
    matcher: &RegexMatcher,
    path: &Path,
) -> Result<FileSearchResult> {
    if can_stream_plain_matches(config) {
        return search_file_streaming_plain_sequential(config, matcher, path);
    }

    let writer = AtomicLineWriter::new(config.output_target.clone());
    let mut builder = StandardBuilder::new();
    builder.path(true);
    builder.only_matching(config.only_matching);

    let mut printer = builder.build_no_color(writer);
    let mut searcher = build_searcher(config, config.line_number);
    let matches = {
        let mut sink = CollectingSink::new(printer.sink_with_path(matcher, path), path.to_path_buf());
        searcher
            .search_path(matcher, path, &mut sink)
            .with_context(|| format!("native standard output search failed for {}", path.display()))?;
        sink.into_matches()
    };
    printer.get_mut().get_mut().finish()?;

    Ok(FileSearchResult {
        matches,
        used_chunk_parallel: false,
    })
}

fn search_file_streaming_plain_sequential(
    config: &NativeSearchConfig,
    matcher: &RegexMatcher,
    path: &Path,
) -> Result<FileSearchResult> {
    let mut matches = Vec::new();
    let path_buf = path.to_path_buf();
    let mut searcher = build_searcher(config, true);
    searcher
        .search_path(
            matcher,
            path,
            Lossy(|line_number, line| {
                let matched = NativeSearchMatch {
                    path: path_buf.clone(),
                    line_number: Some(line_number),
                    text: line.trim_end_matches(['\n', '\r']).to_string(),
                };
                emit_standard_match(config, &matched).map_err(io::Error::other)?;
                matches.push(matched);
                Ok(true)
            }),
        )
        .with_context(|| format!("native standard output search failed for {}", path.display()))?;

    Ok(FileSearchResult {
        matches,
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

    let mut sink = StreamingNdjsonSink::new(config, path.to_path_buf());
    let mut searcher = build_searcher(config, true);
    searcher
        .search_path(matcher, path, &mut sink)
        .with_context(|| format!("native NDJSON search failed for {}", path.display()))?;

    Ok(FileSearchResult {
        matches: sink.into_matches(),
        used_chunk_parallel: false,
    })
}

fn search_file(config: &NativeSearchConfig, matcher: &RegexMatcher, path: &Path) -> Result<FileSearchResult> {
    if should_use_chunk_parallel_search(config, path)? {
        return search_file_chunk_parallel(config, matcher, path);
    }
    search_file_json(config, matcher, path)
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

fn collect_files(config: &NativeSearchConfig) -> Result<Vec<PathBuf>> {
    let mut files = BTreeSet::new();
    let mut roots = Vec::new();

    for path in &config.paths {
        if !path.exists() {
            return Err(anyhow!("native search path does not exist: {}", path.display()));
        }
        if path.is_file() {
            files.insert(path.clone());
        } else {
            roots.push(path.clone());
        }
    }

    if roots.is_empty() {
        return Ok(files.into_iter().collect());
    }

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
        for root in &roots {
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

    let walked_files = Arc::new(Mutex::new(BTreeSet::new()));
    let shared_files = Arc::clone(&walked_files);
    builder.build_parallel().run(|| {
        let shared_files = Arc::clone(&shared_files);
        Box::new(move |entry| {
            if let Ok(entry) = entry {
                if entry.file_type().map(|kind| kind.is_file()).unwrap_or(false) {
                    if let Ok(mut guard) = shared_files.lock() {
                        guard.insert(entry.path().to_path_buf());
                    }
                }
            }
            WalkState::Continue
        })
    });

    let walked_files = walked_files
        .lock()
        .map_err(|_| anyhow!("failed to collect native search walk results"))?;
    files.extend(walked_files.iter().cloned());
    Ok(files.into_iter().collect())
}

fn is_binary_file(path: &Path) -> Result<bool> {
    let mut file = File::open(path)
        .with_context(|| format!("failed to open native search path {}", path.display()))?;
    let mut sample = [0u8; 8192];
    let bytes_read = file
        .read(&mut sample)
        .with_context(|| format!("failed to read native search path {}", path.display()))?;
    Ok(sample[..bytes_read].contains(&0))
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
    let chunk_plan = plan_file_chunks(&mmap, requested_chunk_count);
    if chunk_plan.len() <= 1 {
        return search_file_json(config, matcher, path);
    }

    if config.verbose {
        emit_chunk_parallel_debug(path, mmap.len(), requested_chunk_count, &chunk_plan);
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
        matches,
        used_chunk_parallel: true,
    })
}

fn plan_file_chunks(contents: &[u8], requested_chunk_count: usize) -> Vec<FileChunkPlan> {
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
        first_line_number = first_line_number.saturating_add(count_lines(&contents[byte_start..byte_end]));
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

fn search_file_json(
    config: &NativeSearchConfig,
    matcher: &RegexMatcher,
    path: &Path,
) -> Result<FileSearchResult> {
    let mut printer_builder = JSONBuilder::new();
    printer_builder.always_begin_end(true);

    let mut printer = printer_builder.build(Vec::new());
    let mut searcher = build_searcher(config, true);
    searcher
        .search_path(matcher, path, printer.sink_with_path(matcher, path))
        .with_context(|| format!("native search failed for {}", path.display()))?;

    let raw_json_lines = printer.into_inner();
    let matches = parse_json_printer_output(&raw_json_lines, path)?;
    Ok(FileSearchResult {
        matches,
        used_chunk_parallel: false,
    })
}

fn emit_count_output(config: &NativeSearchConfig, matcher: &RegexMatcher, path: &Path) -> Result<()> {
    let mut builder = SummaryBuilder::new();
    builder.kind(SummaryKind::Count);
    builder.path(true);
    builder.exclude_zero(false);

    let mut printer = builder.build_no_color(Vec::new());
    let mut searcher = build_searcher(config, false);
    searcher
        .search_path(matcher, path, printer.sink_with_path(matcher, path))
        .with_context(|| format!("native count output search failed for {}", path.display()))?;

    let bytes = printer.into_inner().into_inner();
    config.output_target.write_all(&bytes)
}

fn emit_count_output_from_matches(config: &NativeSearchConfig, path: &Path, count: usize) -> Result<()> {
    let mut bytes = Vec::new();
    writeln!(&mut bytes, "{}:{count}", path.display())?;
    config.output_target.write_all(&bytes)
}

fn can_stream_plain_matches(config: &NativeSearchConfig) -> bool {
    config.before_context == 0 && config.after_context == 0 && !config.only_matching
}

fn emit_standard_match(config: &NativeSearchConfig, matched: &NativeSearchMatch) -> Result<()> {
    let mut bytes = Vec::new();
    if config.line_number {
        writeln!(
            &mut bytes,
            "{}:{}:{}",
            matched.path.display(),
            native_match_line_number(matched)?,
            matched.text
        )?;
    } else {
        writeln!(&mut bytes, "{}:{}", matched.path.display(), matched.text)?;
    }
    config.output_target.write_all(&bytes)
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

fn parse_json_printer_output(raw_json_lines: &[u8], default_path: &Path) -> Result<Vec<NativeSearchMatch>> {
    let raw = std::str::from_utf8(raw_json_lines).context("native JSON printer emitted non-UTF-8 output")?;
    let mut matches = Vec::new();

    for line in raw.lines() {
        if line.trim().is_empty() {
            continue;
        }
        let payload: Value = serde_json::from_str(line)
            .with_context(|| format!("failed to parse native JSON printer line: {line}"))?;
        if payload.get("type").and_then(Value::as_str) != Some("match") {
            continue;
        }

        let data = payload
            .get("data")
            .ok_or_else(|| anyhow!("native JSON printer match entry missing data field"))?;
        let path = data
            .get("path")
            .and_then(extract_text_value)
            .map(PathBuf::from)
            .unwrap_or_else(|| default_path.to_path_buf());
        let text = data
            .get("lines")
            .and_then(extract_text_value)
            .unwrap_or_default()
            .trim_end_matches(['\n', '\r'])
            .to_string();
        let line_number = data.get("line_number").and_then(Value::as_u64);

        matches.push(NativeSearchMatch {
            path,
            line_number,
            text,
        });
    }

    Ok(matches)
}

fn extract_text_value(value: &Value) -> Option<String> {
    value
        .get("text")
        .and_then(Value::as_str)
        .map(ToOwned::to_owned)
        .or_else(|| value.get("bytes").and_then(Value::as_str).map(ToOwned::to_owned))
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

    let mut bytes = serde_json::to_vec(&payload)?;
    bytes.push(b'\n');
    config.output_target.write_all(&bytes)
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
