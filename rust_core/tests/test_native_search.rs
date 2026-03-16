use std::collections::BTreeSet;
use std::fs;
use std::io::{BufWriter, Read, Write};
use std::path::{Path, PathBuf};
use std::process::Command;
use std::sync::{Arc, Mutex, MutexGuard, OnceLock};
use std::time::{Duration, Instant};

use serde_json::Value;
use tempfile::tempdir;
use tensor_grep_rs::native_search::{
    run_native_search, NativeOutputTarget, NativeSearchConfig,
};

const LARGE_FILE_LINE_BYTES: usize = 1024;
const LARGE_FILE_LINE_COUNT: usize = 102_400;
const LARGE_STREAMING_FILE_LINE_COUNT: usize = 131_072;
const LARGE_FILE_CHUNK_COUNT: usize = 4;
const LARGE_FILE_THRESHOLD_BYTES: usize = 50 * 1024 * 1024;
const LARGE_FILE_PATTERN: &str = "NEEDLE-BOUNDARY";
const STREAMING_PATTERN: &str = "STREAM-NEEDLE";
const STREAMING_MATCH_INTERVAL: usize = 24;
const MANY_FILE_STREAMING_LINES_PER_FILE: usize = 64;

struct StreamCapture {
    status: std::process::ExitStatus,
    stdout: Vec<u8>,
    stderr: Vec<u8>,
    ttfb: Duration,
    total: Duration,
}

fn tg() -> Command {
    Command::new(env!("CARGO_BIN_EXE_tg"))
}

fn buffer_target() -> (NativeOutputTarget, Arc<Mutex<Vec<u8>>>) {
    let buffer = Arc::new(Mutex::new(Vec::new()));
    (NativeOutputTarget::Buffer(Arc::clone(&buffer)), buffer)
}

fn read_buffer(buffer: &Arc<Mutex<Vec<u8>>>) -> String {
    String::from_utf8(buffer.lock().unwrap().clone()).unwrap()
}

fn base_config(pattern: &str, path: &Path, output_target: NativeOutputTarget) -> NativeSearchConfig {
    NativeSearchConfig {
        pattern: pattern.to_string(),
        paths: vec![path.to_path_buf()],
        output_target,
        ..NativeSearchConfig::default()
    }
}

fn write_large_chunk_boundary_fixture(dir: &Path) -> (PathBuf, Vec<u64>) {
    let file_path = dir.join("chunk-boundary.log");
    let file = fs::File::create(&file_path).unwrap();
    let mut writer = BufWriter::new(file);
    let lines_per_chunk = LARGE_FILE_LINE_COUNT / LARGE_FILE_CHUNK_COUNT;
    let expected_lines = vec![
        1,
        lines_per_chunk as u64,
        (lines_per_chunk + 1) as u64,
        (lines_per_chunk * 2) as u64,
        (lines_per_chunk * 2 + 1) as u64,
        (lines_per_chunk * 3) as u64,
        (lines_per_chunk * 3 + 1) as u64,
        LARGE_FILE_LINE_COUNT as u64,
    ];
    let expected_set = expected_lines.iter().copied().collect::<BTreeSet<_>>();

    for line_number in 1..=LARGE_FILE_LINE_COUNT {
        let mut line = if expected_set.contains(&(line_number as u64)) {
            format!("INFO {LARGE_FILE_PATTERN} boundary-line-{line_number}")
        } else {
            format!("INFO filler-line-{line_number}")
        };
        assert!(line.len() < LARGE_FILE_LINE_BYTES);
        line.push_str(&"x".repeat(LARGE_FILE_LINE_BYTES - line.len() - 1));
        line.push('\n');
        writer.write_all(line.as_bytes()).unwrap();
    }
    writer.flush().unwrap();

    assert_eq!(
        fs::metadata(&file_path).unwrap().len(),
        (LARGE_FILE_LINE_BYTES * LARGE_FILE_LINE_COUNT) as u64
    );

    (file_path, expected_lines)
}

fn write_large_streaming_fixture(dir: &Path) -> (PathBuf, usize) {
    let file_path = dir.join("streaming-large.log");
    let file = fs::File::create(&file_path).unwrap();
    let mut writer = BufWriter::new(file);
    let mut expected_matches = 0usize;

    for line_number in 1..=LARGE_STREAMING_FILE_LINE_COUNT {
        let mut line = if line_number % STREAMING_MATCH_INTERVAL == 0 {
            expected_matches += 1;
            format!("INFO {STREAMING_PATTERN} streaming-line-{line_number}")
        } else {
            format!("INFO filler-line-{line_number}")
        };
        assert!(line.len() < LARGE_FILE_LINE_BYTES);
        line.push_str(&"x".repeat(LARGE_FILE_LINE_BYTES - line.len() - 1));
        line.push('\n');
        writer.write_all(line.as_bytes()).unwrap();
    }
    writer.flush().unwrap();

    assert_eq!(
        fs::metadata(&file_path).unwrap().len(),
        (LARGE_FILE_LINE_BYTES * LARGE_STREAMING_FILE_LINE_COUNT) as u64
    );

    (file_path, expected_matches)
}

fn write_many_file_streaming_fixture(dir: &Path, file_count: usize) -> (PathBuf, usize) {
    let root = dir.join("many-streaming");
    fs::create_dir_all(&root).unwrap();
    let mut expected_matches = 0usize;

    for file_index in 0..file_count {
        let file_path = root.join(format!("fixture-{file_index:04}.log"));
        let mut lines = Vec::new();
        for line_index in 0..MANY_FILE_STREAMING_LINES_PER_FILE {
            if file_index % 23 == 0 && line_index == 7 {
                expected_matches += 1;
                lines.push(format!("INFO {STREAMING_PATTERN} file={file_index} line={line_index}\n"));
            } else {
                lines.push(format!("INFO filler file={file_index} line={line_index}\n"));
            }
        }
        fs::write(file_path, lines.concat()).unwrap();
    }

    (root, expected_matches)
}

fn capture_streaming_output(command: &mut Command) -> StreamCapture {
    let started = Instant::now();
    let mut child = command
        .stdout(std::process::Stdio::piped())
        .stderr(std::process::Stdio::piped())
        .spawn()
        .unwrap();

    let mut stdout = child.stdout.take().unwrap();
    let stderr = child.stderr.take().unwrap();
    let stderr_reader = std::thread::spawn(move || {
        let mut stderr = stderr;
        let mut bytes = Vec::new();
        stderr.read_to_end(&mut bytes).unwrap();
        bytes
    });

    let mut first_byte = [0u8; 1];
    stdout.read_exact(&mut first_byte).unwrap();
    let ttfb = started.elapsed();

    let mut stdout_bytes = vec![first_byte[0]];
    stdout.read_to_end(&mut stdout_bytes).unwrap();

    let status = child.wait().unwrap();
    let total = started.elapsed();
    let stderr = stderr_reader.join().unwrap();

    StreamCapture {
        status,
        stdout: stdout_bytes,
        stderr,
        ttfb,
        total,
    }
}

fn median_duration(samples: &[Duration]) -> Duration {
    let mut sorted = samples.to_vec();
    sorted.sort_unstable();
    sorted[sorted.len() / 2]
}

fn timing_test_guard() -> MutexGuard<'static, ()> {
    static TIMING_TEST_MUTEX: OnceLock<Mutex<()>> = OnceLock::new();
    TIMING_TEST_MUTEX
        .get_or_init(|| Mutex::new(()))
        .lock()
        .unwrap_or_else(|poisoned| poisoned.into_inner())
}

fn assert_streaming_ratio(capture: &StreamCapture, mode: &str) {
    assert!(
        capture.status.success(),
        "mode={mode} status={:?}\nstdout={}\nstderr={}",
        capture.status.code(),
        String::from_utf8_lossy(&capture.stdout),
        String::from_utf8_lossy(&capture.stderr)
    );

    let ttfb = capture.ttfb.as_secs_f64();
    let total = capture.total.as_secs_f64();
    assert!(total > 0.0, "mode={mode} total must be positive");
    assert!(
        ttfb < total * 0.5,
        "expected streaming {mode} output to arrive before halfway point: ttfb={:?} total={:?}",
        capture.ttfb,
        capture.total
    );
}

#[test]
fn test_native_search_literal_search_on_tempfile() {
    let dir = tempdir().unwrap();
    let file_path = dir.path().join("app.log");
    fs::write(
        &file_path,
        "INFO ready\nERROR failed\nDEBUG trace\nERROR timeout\n",
    )
    .unwrap();

    let (target, _buffer) = buffer_target();
    let mut config = base_config("ERROR", &file_path, target);
    config.fixed_strings = true;

    let stats = run_native_search(config).unwrap();

    assert_eq!(stats.total_matches, 2);
    assert_eq!(stats.matched_files, 1);
    assert_eq!(stats.searched_files, 1);
    assert_eq!(stats.matches.len(), 2);
    assert_eq!(stats.matches[0].line_number, Some(2));
    assert_eq!(stats.matches[0].text, "ERROR failed");
    assert_eq!(stats.matches[1].line_number, Some(4));
    assert_eq!(stats.matches[1].text, "ERROR timeout");
}

#[test]
fn test_native_search_regex_search() {
    let dir = tempdir().unwrap();
    let file_path = dir.path().join("events.log");
    fs::write(
        &file_path,
        "INFO ready\nERROR network timeout\nWARN 503 retrying\nDEBUG trace\n",
    )
    .unwrap();

    let (target, _buffer) = buffer_target();
    let config = base_config(r"ERROR.*timeout|WARN\s+\d{3}", &file_path, target);

    let stats = run_native_search(config).unwrap();

    assert_eq!(stats.total_matches, 2);
    assert_eq!(
        stats.matches.iter().map(|entry| entry.text.as_str()).collect::<Vec<_>>(),
        vec!["ERROR network timeout", "WARN 503 retrying"]
    );
}

#[test]
fn test_native_search_case_insensitive_search() {
    let dir = tempdir().unwrap();
    let file_path = dir.path().join("mixed.log");
    fs::write(&file_path, "error lower\nERROR upper\ninfo\n").unwrap();

    let (target, _buffer) = buffer_target();
    let mut config = base_config("error", &file_path, target);
    config.fixed_strings = true;
    config.ignore_case = true;

    let stats = run_native_search(config).unwrap();

    assert_eq!(stats.total_matches, 2);
}

#[test]
fn test_native_search_fixed_string_treats_meta_characters_literally() {
    let dir = tempdir().unwrap();
    let file_path = dir.path().join("literal.log");
    fs::write(
        &file_path,
        "ERROR.*timeout literal\nERROR abc timeout regex\n",
    )
    .unwrap();

    let (target, _buffer) = buffer_target();
    let mut config = base_config("ERROR.*timeout", &file_path, target);
    config.fixed_strings = true;

    let stats = run_native_search(config).unwrap();

    assert_eq!(stats.total_matches, 1);
    assert_eq!(stats.matches[0].text, "ERROR.*timeout literal");
}

#[test]
fn test_native_search_count_mode_outputs_per_file_counts() {
    let dir = tempdir().unwrap();
    let file_path = dir.path().join("count.log");
    fs::write(&file_path, "ERROR one\nINFO\nERROR two\n").unwrap();

    let (target, buffer) = buffer_target();
    let mut config = base_config("ERROR", &file_path, target);
    config.fixed_strings = true;
    config.count = true;

    let stats = run_native_search(config).unwrap();
    let output = read_buffer(&buffer);

    assert_eq!(stats.total_matches, 2);
    assert!(output.contains(&format!("{}:2", file_path.display())), "output={output}");
}

#[test]
fn test_native_search_json_output_is_valid() {
    let dir = tempdir().unwrap();
    let file_path = dir.path().join("json.log");
    fs::write(&file_path, "ERROR alpha\nERROR beta\n").unwrap();

    let (target, buffer) = buffer_target();
    let mut config = base_config("ERROR", &file_path, target);
    config.fixed_strings = true;
    config.json = true;

    let stats = run_native_search(config).unwrap();
    let payload: Value = serde_json::from_str(&read_buffer(&buffer)).unwrap();

    assert_eq!(stats.total_matches, 2);
    assert_eq!(payload["version"], 1);
    assert_eq!(payload["routing_backend"], "NativeCpuBackend");
    assert_eq!(payload["routing_reason"], "native_search");
    assert_eq!(payload["sidecar_used"], false);
    assert_eq!(payload["query"], "ERROR");
    assert_eq!(payload["path"], file_path.display().to_string());
    assert_eq!(payload["total_matches"], 2);
    assert_eq!(payload["matches"].as_array().unwrap().len(), 2);
}

#[test]
fn test_native_search_ndjson_output_is_valid() {
    let dir = tempdir().unwrap();
    let file_path = dir.path().join("stream.log");
    fs::write(&file_path, "ERROR alpha\nINFO\nERROR beta\n").unwrap();

    let (target, buffer) = buffer_target();
    let mut config = base_config("ERROR", &file_path, target);
    config.fixed_strings = true;
    config.ndjson = true;

    let stats = run_native_search(config).unwrap();
    let output = read_buffer(&buffer);
    let payloads = output
        .lines()
        .filter(|line| !line.trim().is_empty())
        .map(|line| serde_json::from_str::<Value>(line).unwrap())
        .collect::<Vec<_>>();

    assert_eq!(stats.total_matches, 2);
    assert!(!payloads.is_empty());
    assert_eq!(payloads.len(), 2);
    for payload in &payloads {
        assert_eq!(payload["version"], 1);
        assert_eq!(payload["routing_backend"], "NativeCpuBackend");
        assert_eq!(payload["routing_reason"], "native_search");
        assert_eq!(payload["sidecar_used"], false);
        assert_eq!(payload["query"], "ERROR");
        assert_eq!(payload["path"], file_path.display().to_string());
        assert!(payload["file"].is_string());
        assert!(payload["line"].is_number());
        assert!(payload["text"].is_string());
        assert!(payload.get("type").is_none());
    }
}

#[test]
fn test_native_search_skips_binary_files_by_default() {
    let dir = tempdir().unwrap();
    let text_path = dir.path().join("text.log");
    let binary_path = dir.path().join("binary.bin");
    fs::write(&text_path, "ERROR visible\n").unwrap();
    fs::write(&binary_path, b"\0ERROR hidden\0").unwrap();

    let (target, _buffer) = buffer_target();
    let mut config = base_config("ERROR", dir.path(), target);
    config.fixed_strings = true;

    let stats = run_native_search(config).unwrap();

    assert_eq!(stats.total_matches, 1);
    assert_eq!(stats.skipped_binary_files, 1);
    assert_eq!(stats.matches[0].path, text_path);
}

#[test]
fn test_native_search_respects_gitignore_rules() {
    let dir = tempdir().unwrap();
    fs::write(dir.path().join(".gitignore"), "ignored.log\n").unwrap();
    let visible_path = dir.path().join("visible.log");
    let ignored_path = dir.path().join("ignored.log");
    fs::write(&visible_path, "ERROR visible\n").unwrap();
    fs::write(&ignored_path, "ERROR ignored\n").unwrap();

    let (target, _buffer) = buffer_target();
    let mut config = base_config("ERROR", dir.path(), target);
    config.fixed_strings = true;

    let stats = run_native_search(config).unwrap();

    assert_eq!(stats.total_matches, 1);
    assert_eq!(stats.matched_files, 1);
    assert_eq!(stats.matches[0].path, visible_path);
}

#[test]
fn test_native_search_no_ignore_includes_ignored_files_without_searching_dotfiles() {
    let dir = tempdir().unwrap();
    fs::write(dir.path().join(".gitignore"), "ignored.log\n").unwrap();
    let visible_path = dir.path().join("visible.log");
    let ignored_path = dir.path().join("ignored.log");
    fs::write(&visible_path, "visible marker\n").unwrap();
    fs::write(&ignored_path, "ignored marker\n").unwrap();

    let (target, _buffer) = buffer_target();
    let mut config = base_config("ignored", dir.path(), target);
    config.fixed_strings = true;
    config.no_ignore = true;

    let stats = run_native_search(config).unwrap();
    let matched_paths = stats.matches.iter().map(|entry| entry.path.clone()).collect::<Vec<_>>();

    assert_eq!(stats.total_matches, 1);
    assert_eq!(matched_paths, vec![ignored_path]);
}

#[test]
fn test_native_search_parallel_walk_counts_expected_files() {
    let dir = tempdir().unwrap();
    fs::create_dir_all(dir.path().join("nested").join("deeper")).unwrap();
    fs::write(dir.path().join(".gitignore"), "ignored.txt\n").unwrap();

    for index in 0..8 {
        fs::write(dir.path().join(format!("file-{index}.txt")), format!("ERROR file {index}\n"))
            .unwrap();
    }
    for index in 0..4 {
        fs::write(
            dir.path().join("nested").join(format!("nested-{index}.txt")),
            format!("INFO nested {index}\n"),
        )
        .unwrap();
    }
    fs::write(
        dir.path().join("nested").join("deeper").join("deep.txt"),
        "ERROR deep\n",
    )
    .unwrap();
    fs::write(dir.path().join("ignored.txt"), "ERROR ignored\n").unwrap();

    let (target, _buffer) = buffer_target();
    let mut config = base_config("ERROR", dir.path(), target);
    config.fixed_strings = true;

    let stats = run_native_search(config).unwrap();

    assert_eq!(stats.searched_files, 13);
    assert_eq!(stats.total_matches, 9);
    assert_eq!(stats.matched_files, 9);
}

#[test]
fn test_native_search_large_file_chunk_parallelism_preserves_boundaries_and_global_line_numbers() {
    let dir = tempdir().unwrap();
    let (file_path, expected_lines) = write_large_chunk_boundary_fixture(dir.path());

    let (target, _buffer) = buffer_target();
    let mut config = base_config(LARGE_FILE_PATTERN, &file_path, target);
    config.fixed_strings = true;
    config.json = true;
    config.verbose = true;
    config.large_file_chunk_threshold_bytes = LARGE_FILE_THRESHOLD_BYTES;
    config.chunk_parallelism_threads = Some(LARGE_FILE_CHUNK_COUNT);

    let stats = run_native_search(config).unwrap();
    let actual_lines = stats
        .matches
        .iter()
        .map(|entry| entry.line_number.unwrap())
        .collect::<Vec<_>>();
    let unique_lines = actual_lines.iter().copied().collect::<BTreeSet<_>>();

    assert_eq!(stats.total_matches, expected_lines.len());
    assert_eq!(actual_lines, expected_lines);
    assert_eq!(unique_lines.len(), expected_lines.len(), "duplicate boundary matches found");
    assert!(stats.matches.iter().all(|entry| entry.text.contains(LARGE_FILE_PATTERN)));
}

#[test]
fn test_native_search_large_file_verbose_logs_chunk_boundaries() {
    if std::thread::available_parallelism().map(|count| count.get()).unwrap_or(1) < 2 {
        return;
    }

    let dir = tempdir().unwrap();
    let (file_path, expected_lines) = write_large_chunk_boundary_fixture(dir.path());

    let output = tg()
        .arg("search")
        .arg("--cpu")
        .arg("--fixed-strings")
        .arg("--json")
        .arg("--verbose")
        .arg(LARGE_FILE_PATTERN)
        .arg(&file_path)
        .output()
        .unwrap();

    assert!(output.status.success(), "status={:?}\nstdout={}\nstderr={}", output.status.code(), String::from_utf8_lossy(&output.stdout), String::from_utf8_lossy(&output.stderr));

    let payload: Value = serde_json::from_slice(&output.stdout).unwrap();
    let stderr = String::from_utf8_lossy(&output.stderr);

    assert_eq!(payload["total_matches"], expected_lines.len() as u64);
    assert!(stderr.contains("chunk_parallel file="), "stderr={stderr}");
    assert!(stderr.contains("chunk_count="), "stderr={stderr}");
    assert!(stderr.contains("chunk[0]"), "stderr={stderr}");
    assert!(stderr.contains("byte_start="), "stderr={stderr}");
}

#[test]
fn test_native_search_default_output_streams_before_search_completion() {
    let _guard = timing_test_guard();
    if std::thread::available_parallelism().map(|count| count.get()).unwrap_or(1) < 2 {
        return;
    }

    let dir = tempdir().unwrap();
    let (file_path, expected_matches) = write_large_streaming_fixture(dir.path());

    let mut command = tg();
    command
        .arg("--cpu")
        .arg("--fixed-strings")
        .arg(STREAMING_PATTERN)
        .arg(&file_path);
    let capture = capture_streaming_output(&mut command);
    let stdout = String::from_utf8(capture.stdout.clone()).unwrap();

    assert_streaming_ratio(&capture, "default");
    assert_eq!(stdout.lines().count(), expected_matches, "stdout={stdout}");
}

#[test]
fn test_native_search_ndjson_output_streams_before_search_completion() {
    let _guard = timing_test_guard();
    if std::thread::available_parallelism().map(|count| count.get()).unwrap_or(1) < 2 {
        return;
    }

    let dir = tempdir().unwrap();
    let (file_path, expected_matches) = write_large_streaming_fixture(dir.path());

    let mut command = tg();
    command
        .arg("--cpu")
        .arg("--fixed-strings")
        .arg("--ndjson")
        .arg(STREAMING_PATTERN)
        .arg(&file_path);
    let capture = capture_streaming_output(&mut command);
    let payloads = String::from_utf8(capture.stdout.clone())
        .unwrap()
        .lines()
        .filter(|line| !line.trim().is_empty())
        .map(|line| serde_json::from_str::<Value>(line).unwrap())
        .collect::<Vec<_>>();

    assert_streaming_ratio(&capture, "ndjson");
    assert_eq!(payloads.len(), expected_matches);
}

#[test]
fn test_native_search_many_file_directory_streams_before_walk_completion() {
    let _guard = timing_test_guard();
    if std::thread::available_parallelism().map(|count| count.get()).unwrap_or(1) < 2 {
        return;
    }

    let dir = tempdir().unwrap();
    let (fixture_dir, expected_matches) = write_many_file_streaming_fixture(dir.path(), 4_000);

    let mut command = tg();
    command
        .arg("search")
        .arg("--cpu")
        .arg("--fixed-strings")
        .arg(STREAMING_PATTERN)
        .arg(&fixture_dir);
    let capture = capture_streaming_output(&mut command);
    let stdout = String::from_utf8(capture.stdout.clone()).unwrap();

    assert_streaming_ratio(&capture, "many-file-directory");
    assert_eq!(stdout.lines().count(), expected_matches, "stdout={stdout}");
}

#[test]
fn test_native_search_default_output_lines_are_not_interleaved() {
    if std::thread::available_parallelism().map(|count| count.get()).unwrap_or(1) < 2 {
        return;
    }

    let dir = tempdir().unwrap();
    let (file_path, expected_matches) = write_large_streaming_fixture(dir.path());
    let file_prefix = file_path.display().to_string();

    for run in 0..10 {
        let output = tg()
            .arg("--cpu")
            .arg("--fixed-strings")
            .arg(STREAMING_PATTERN)
            .arg(&file_path)
            .output()
            .unwrap();

        assert!(
            output.status.success(),
            "run={run} status={:?}\nstdout={}\nstderr={}",
            output.status.code(),
            String::from_utf8_lossy(&output.stdout),
            String::from_utf8_lossy(&output.stderr)
        );

        let stdout = String::from_utf8(output.stdout).unwrap();
        let lines = stdout.lines().collect::<Vec<_>>();
        assert_eq!(lines.len(), expected_matches, "run={run} stdout={stdout}");

        for line in lines {
            assert!(
                line.starts_with(&file_prefix),
                "run={run} line missing file prefix: {line}"
            );
            let suffix = &line[file_prefix.len()..];
            assert!(suffix.starts_with(':'), "run={run} malformed suffix: {line}");
            let mut parts = suffix[1..].splitn(2, ':');
            let line_number = parts.next().unwrap();
            let text = parts.next().unwrap_or_default();
            assert!(
                !line_number.is_empty() && line_number.chars().all(|ch| ch.is_ascii_digit()),
                "run={run} invalid line number: {line}"
            );
            assert!(
                text.contains(STREAMING_PATTERN),
                "run={run} missing pattern in line: {line}"
            );
        }
    }
}

#[test]
fn test_native_search_json_output_remains_single_document_for_large_file() {
    if std::thread::available_parallelism().map(|count| count.get()).unwrap_or(1) < 2 {
        return;
    }

    let dir = tempdir().unwrap();
    let (file_path, expected_matches) = write_large_streaming_fixture(dir.path());

    let output = tg()
        .arg("--cpu")
        .arg("--fixed-strings")
        .arg("--json")
        .arg(STREAMING_PATTERN)
        .arg(&file_path)
        .output()
        .unwrap();

    assert!(
        output.status.success(),
        "status={:?}\nstdout={}\nstderr={}",
        output.status.code(),
        String::from_utf8_lossy(&output.stdout),
        String::from_utf8_lossy(&output.stderr)
    );

    let stdout = String::from_utf8(output.stdout).unwrap();
    assert_eq!(
        stdout.lines().filter(|line| !line.trim().is_empty()).count(),
        1,
        "stdout={stdout}"
    );

    let payload: Value = serde_json::from_str(&stdout).unwrap();
    assert_eq!(payload["total_matches"], expected_matches as u64);
    assert_eq!(payload["matches"].as_array().unwrap().len(), expected_matches);
}

#[test]
#[ignore = "benchmark-style validation for large-file chunk parallelism"]
fn test_native_search_large_file_chunk_parallelism_is_faster_than_sequential() {
    if std::thread::available_parallelism().map(|count| count.get()).unwrap_or(1) < 2 {
        return;
    }

    let dir = tempdir().unwrap();
    let (file_path, expected_lines) = write_large_chunk_boundary_fixture(dir.path());

    let mut parallel_samples = Vec::new();
    let mut sequential_samples = Vec::new();

    for parallel_large_files in [true, false] {
        let (target, _buffer) = buffer_target();
        let mut config = base_config(LARGE_FILE_PATTERN, &file_path, target);
        config.fixed_strings = true;
        config.json = true;
        config.large_file_chunk_threshold_bytes = LARGE_FILE_THRESHOLD_BYTES;
        config.chunk_parallelism_threads = Some(LARGE_FILE_CHUNK_COUNT);
        config.parallel_large_files = parallel_large_files;
        let stats = run_native_search(config).unwrap();
        assert_eq!(stats.total_matches, expected_lines.len());
    }

    for _ in 0..3 {
        let (target, _buffer) = buffer_target();
        let mut parallel = base_config(LARGE_FILE_PATTERN, &file_path, target);
        parallel.fixed_strings = true;
        parallel.json = true;
        parallel.large_file_chunk_threshold_bytes = LARGE_FILE_THRESHOLD_BYTES;
        parallel.chunk_parallelism_threads = Some(LARGE_FILE_CHUNK_COUNT);
        parallel.parallel_large_files = true;
        let started = Instant::now();
        let stats = run_native_search(parallel).unwrap();
        parallel_samples.push(started.elapsed());
        assert_eq!(stats.total_matches, expected_lines.len());

        let (target, _buffer) = buffer_target();
        let mut sequential = base_config(LARGE_FILE_PATTERN, &file_path, target);
        sequential.fixed_strings = true;
        sequential.json = true;
        sequential.large_file_chunk_threshold_bytes = LARGE_FILE_THRESHOLD_BYTES;
        sequential.chunk_parallelism_threads = Some(LARGE_FILE_CHUNK_COUNT);
        sequential.parallel_large_files = false;
        let started = Instant::now();
        let stats = run_native_search(sequential).unwrap();
        sequential_samples.push(started.elapsed());
        assert_eq!(stats.total_matches, expected_lines.len());
    }

    let parallel_median = median_duration(&parallel_samples);
    let sequential_median = median_duration(&sequential_samples);
    eprintln!(
        "parallel_median={parallel_median:?} sequential_median={sequential_median:?}"
    );
    assert!(
        parallel_median < sequential_median,
        "expected parallel median {:?} to beat sequential median {:?}",
        parallel_median,
        sequential_median
    );
}
