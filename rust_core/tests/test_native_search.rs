use std::fs;
use std::path::Path;
use std::sync::{Arc, Mutex};

use serde_json::Value;
use tempfile::tempdir;
use tensor_grep_rs::native_search::{
    run_native_search, NativeOutputTarget, NativeSearchConfig,
};

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
    assert!(payloads.iter().any(|payload| payload["type"] == "match"));
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
