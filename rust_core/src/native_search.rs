use anyhow::{anyhow, Context, Result};
use grep_matcher::LineTerminator;
use grep_printer::{JSONBuilder, StandardBuilder, SummaryBuilder, SummaryKind};
use grep_regex::{RegexMatcher, RegexMatcherBuilder};
use grep_searcher::{BinaryDetection, MmapChoice, Searcher, SearcherBuilder};
use ignore::{overrides::OverrideBuilder, WalkBuilder, WalkState};
use serde::Serialize;
use serde_json::Value;
use std::collections::BTreeSet;
use std::fs::File;
use std::io::{Read, Write};
use std::path::{Path, PathBuf};
use std::sync::{Arc, Mutex};

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
pub struct NativeSearchConfig {
    pub pattern: String,
    pub paths: Vec<PathBuf>,
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
    pub output_target: NativeOutputTarget,
}

impl Default for NativeSearchConfig {
    fn default() -> Self {
        Self {
            pattern: String::new(),
            paths: vec![PathBuf::from(".")],
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
            output_target: NativeOutputTarget::Stdout,
        }
    }
}

#[derive(Debug, Clone, Default)]
struct FileSearchResult {
    matches: Vec<NativeSearchMatch>,
    raw_json_lines: Vec<u8>,
}

#[derive(Debug, Serialize)]
struct NativeJsonOutput<'a> {
    searched_files: usize,
    matched_files: usize,
    total_matches: usize,
    skipped_binary_files: usize,
    matches: &'a [NativeSearchMatch],
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

        let file_result = search_file_json(&config, &matcher, &file_path)?;

        stats.searched_files += 1;
        if !file_result.matches.is_empty() {
            stats.matched_files += 1;
            stats.total_matches += file_result.matches.len();
            stats.matches.extend(file_result.matches.clone());
        }

        if config.quiet && !file_result.matches.is_empty() {
            break;
        }

        if config.json {
            continue;
        }
        if config.ndjson {
            config.output_target.write_all(&file_result.raw_json_lines)?;
            continue;
        }
        if config.count {
            emit_count_output(&config, &matcher, &file_path)?;
        } else if !config.quiet {
            emit_standard_output(&config, &matcher, &file_path)?;
        }
    }

    if config.json {
        let payload = NativeJsonOutput {
            searched_files: stats.searched_files,
            matched_files: stats.matched_files,
            total_matches: stats.total_matches,
            skipped_binary_files: stats.skipped_binary_files,
            matches: &stats.matches,
        };
        let mut bytes = serde_json::to_vec(&payload)?;
        bytes.push(b'\n');
        config.output_target.write_all(&bytes)?;
    }

    Ok(stats)
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

fn build_searcher(config: &NativeSearchConfig) -> Searcher {
    let mut builder = SearcherBuilder::new();
    builder.line_number(config.line_number);
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
        builder.standard_filters(false);
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

fn search_file_json(
    config: &NativeSearchConfig,
    matcher: &RegexMatcher,
    path: &Path,
) -> Result<FileSearchResult> {
    let mut printer_builder = JSONBuilder::new();
    printer_builder.always_begin_end(true);

    let mut printer = printer_builder.build(Vec::new());
    let mut searcher = build_searcher(config);
    searcher
        .search_path(matcher, path, printer.sink_with_path(matcher, path))
        .with_context(|| format!("native search failed for {}", path.display()))?;

    let raw_json_lines = printer.into_inner();
    let matches = parse_json_printer_output(&raw_json_lines, path)?;
    Ok(FileSearchResult {
        matches,
        raw_json_lines,
    })
}

fn emit_standard_output(config: &NativeSearchConfig, matcher: &RegexMatcher, path: &Path) -> Result<()> {
    let mut builder = StandardBuilder::new();
    builder.path(true);
    builder.only_matching(config.only_matching);

    let mut printer = builder.build_no_color(Vec::new());
    let mut searcher = build_searcher(config);
    searcher
        .search_path(matcher, path, printer.sink_with_path(matcher, path))
        .with_context(|| format!("native standard output search failed for {}", path.display()))?;

    let bytes = printer.into_inner().into_inner();
    config.output_target.write_all(&bytes)
}

fn emit_count_output(config: &NativeSearchConfig, matcher: &RegexMatcher, path: &Path) -> Result<()> {
    let mut builder = SummaryBuilder::new();
    builder.kind(SummaryKind::Count);
    builder.path(true);
    builder.exclude_zero(false);

    let mut printer = builder.build_no_color(Vec::new());
    let mut searcher = build_searcher(config);
    searcher
        .search_path(matcher, path, printer.sink_with_path(matcher, path))
        .with_context(|| format!("native count output search failed for {}", path.display()))?;

    let bytes = printer.into_inner().into_inner();
    config.output_target.write_all(&bytes)
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
