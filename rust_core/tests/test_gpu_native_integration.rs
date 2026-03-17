#![cfg(feature = "cuda")]

use std::fs;
use std::path::{Path, PathBuf};
use std::process::Command;

use serde_json::Value;
use tempfile::tempdir;
use tensor_grep_rs::gpu_native::{
    enumerate_cuda_devices, gpu_native_search_paths, GpuNativeSearchConfig,
};

fn tg() -> Command {
    Command::new(env!("CARGO_BIN_EXE_tg"))
}

fn repo_root() -> PathBuf {
    Path::new(env!("CARGO_MANIFEST_DIR"))
        .parent()
        .unwrap()
        .to_path_buf()
}

fn parse_match_tuple(payload: &Value) -> (String, usize, String) {
    let file = payload["file"].as_str().unwrap().to_string();
    let line_number = payload["line"]
        .as_u64()
        .or_else(|| payload["line_number"].as_u64())
        .unwrap() as usize;
    let text = payload["text"].as_str().unwrap().to_string();
    (file, line_number, text)
}

fn parse_pattern_match_tuple(payload: &Value) -> (String, usize, String, usize, String) {
    let (file, line_number, text) = parse_match_tuple(payload);
    let pattern_id = payload["pattern_id"].as_u64().unwrap() as usize;
    let pattern_text = payload["pattern_text"].as_str().unwrap().to_string();
    (file, line_number, text, pattern_id, pattern_text)
}

fn parse_json_tuples(stdout: &[u8]) -> Vec<(String, usize, String)> {
    let payload: Value = serde_json::from_slice(stdout).unwrap();
    let mut tuples = payload["matches"]
        .as_array()
        .unwrap()
        .iter()
        .map(parse_match_tuple)
        .collect::<Vec<_>>();
    tuples.sort();
    tuples
}

fn parse_json_payload(stdout: &[u8]) -> Value {
    serde_json::from_slice(stdout).unwrap()
}

fn parse_pattern_json_tuples(stdout: &[u8]) -> Vec<(String, usize, String, usize, String)> {
    let payload = parse_json_payload(stdout);
    let mut tuples = payload["matches"]
        .as_array()
        .unwrap()
        .iter()
        .map(parse_pattern_match_tuple)
        .collect::<Vec<_>>();
    tuples.sort();
    tuples
}

fn write_single_file_fixture(dir: &Path) -> PathBuf {
    let file_path = dir.join("single.log");
    fs::write(
        &file_path,
        "INFO boot\nERROR first failure\nWARN retry\nERROR second failure\n",
    )
    .unwrap();
    file_path
}

fn write_multi_file_fixture(dir: &Path) -> PathBuf {
    let corpus = dir.join("corpus");
    fs::create_dir(&corpus).unwrap();
    fs::write(corpus.join("a.log"), "INFO ok\nERROR alpha\n").unwrap();
    fs::write(corpus.join("b.log"), "WARN beta\nERROR beta\n").unwrap();
    fs::write(corpus.join("c.log"), "INFO gamma\nERROR gamma\n").unwrap();
    corpus
}

fn write_boundary_fixture(dir: &Path) -> PathBuf {
    let corpus = dir.join("boundary");
    fs::create_dir(&corpus).unwrap();
    fs::write(corpus.join("a.txt"), "prefix ABC").unwrap();
    fs::write(corpus.join("b.txt"), "D standalone should not match\nABCD real match\n").unwrap();
    corpus
}

fn write_multi_pattern_file_fixture(dir: &Path) -> PathBuf {
    let file_path = dir.join("multi-pattern.log");
    fs::write(
        &file_path,
        "INFO boot\nERROR first failure\nWARN retry budget\nERRORWARN combo\nFATAL shutdown\n",
    )
    .unwrap();
    file_path
}

fn write_pattern_count_corpus(dir: &Path, pattern_count: usize) -> (PathBuf, Vec<String>) {
    let corpus = dir.join(format!("pattern-count-{pattern_count}"));
    fs::create_dir(&corpus).unwrap();

    let patterns = (0..pattern_count)
        .map(|index| format!("pattern-{index:03}"))
        .collect::<Vec<_>>();
    let file_path = corpus.join("patterns.log");
    let mut content = String::new();
    for pattern in &patterns {
        content.push_str(pattern);
        content.push('\n');
    }
    fs::write(file_path, content).unwrap();

    (corpus, patterns)
}

fn write_large_pattern_fallback_corpus(dir: &Path) -> (PathBuf, Vec<String>) {
    let corpus = dir.join("large-pattern-fallback");
    fs::create_dir(&corpus).unwrap();

    let patterns = (0..50)
        .map(|index| format!("sentinel-{index:02}-{}", "x".repeat(1024)))
        .collect::<Vec<_>>();
    let file_path = corpus.join("patterns.log");
    fs::write(
        &file_path,
        format!("{}\n{}\n{}\n", patterns[0], patterns[17], patterns[49]),
    )
    .unwrap();

    (corpus, patterns)
}

fn write_timing_corpus(dir: &Path, target_bytes: usize) -> PathBuf {
    let corpus = dir.join("timing-corpus");
    fs::create_dir(&corpus).unwrap();
    let file_path = corpus.join("timing.log");

    let mut content = String::new();
    while content.len() < target_bytes {
        content.push_str("INFO steady state\n");
        content.push_str("ERROR critical path failed\n");
        content.push_str("WARN retry budget exhausted\n");
        content.push_str("FATAL shutdown initiated\n");
        content.push_str("padding line for gpu timing benchmark\n");
    }
    fs::write(&file_path, content).unwrap();
    corpus
}

fn cpu_union_pattern_tuples(corpus: &Path, patterns: &[String]) -> Vec<(String, usize, String, usize, String)> {
    let mut tuples = Vec::new();
    for (pattern_id, pattern) in patterns.iter().enumerate() {
        let output = tg()
            .current_dir(repo_root())
            .arg("search")
            .arg("--fixed-strings")
            .arg("--cpu")
            .arg("--json")
            .arg(pattern)
            .arg(corpus)
            .output()
            .unwrap();

        let exit_code = output.status.code().unwrap_or_default();
        assert!(
            exit_code == 0 || exit_code == 1,
            "pattern={pattern} stderr={}",
            String::from_utf8_lossy(&output.stderr)
        );
        if output.stdout.is_empty() {
            continue;
        }
        let payload = parse_json_payload(&output.stdout);
        for matched in payload["matches"].as_array().unwrap() {
            let (file, line_number, text) = parse_match_tuple(matched);
            tuples.push((file, line_number, text, pattern_id, pattern.clone()));
        }
    }

    tuples.sort();
    tuples
}

fn median_ms(samples: &mut [f64]) -> f64 {
    samples.sort_by(|left, right| left.partial_cmp(right).unwrap());
    samples[samples.len() / 2]
}

fn write_gpu_parity_corpus(dir: &Path, target_bytes: usize) -> PathBuf {
    let corpus = dir.join("gpu-parity-corpus");
    fs::create_dir(&corpus).unwrap();

    let mut content = String::new();
    let lines = [
        "INFO steady state\n",
        "ERROR critical path failed\n",
        "WARN retry budget exhausted\n",
        "Database connection timeout\n",
        "unicode café line\n",
        "日本語 sentinel line\n",
        "emoji 🔍 marker\n",
    ];

    while content.len() < target_bytes {
        for line in &lines {
            content.push_str(line);
        }
    }

    let bytes = content.into_bytes();
    let chunk_size = bytes.len() / 6;
    for index in 0..6 {
        let start = index * chunk_size;
        let end = if index == 5 {
            bytes.len()
        } else {
            (index + 1) * chunk_size
        };
        fs::write(corpus.join(format!("chunk-{index}.log")), &bytes[start..end]).unwrap();
    }

    corpus
}

fn first_two_cuda_devices() -> Option<Vec<(i32, String)>> {
    let devices = enumerate_cuda_devices().ok()?;
    if devices.len() < 2 {
        return None;
    }

    Some(
        devices
            .into_iter()
            .take(2)
            .map(|device| (device.device_id, device.name))
            .collect(),
    )
}

#[test]
fn test_gpu_native_single_file_json_routes_to_native_backend() {
    let dir = tempdir().unwrap();
    let file_path = write_single_file_fixture(dir.path());

    let output = tg()
        .current_dir(repo_root())
        .arg("search")
        .arg("--gpu-device-ids")
        .arg("0")
        .arg("--json")
        .arg("ERROR")
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

    let payload = parse_json_payload(&output.stdout);
    assert_eq!(payload["routing_backend"], "gpu_native");
    assert_eq!(payload["routing_reason"], "gpu-device-ids-explicit-native");
    assert_eq!(payload["sidecar_used"], false);
    assert_eq!(payload["total_files"], 1);
    assert_eq!(payload["total_matches"], 2);
}

#[test]
fn test_gpu_native_directory_search_batches_files_and_matches_cpu_output() {
    let dir = tempdir().unwrap();
    let corpus = write_multi_file_fixture(dir.path());

    let gpu_output = tg()
        .current_dir(repo_root())
        .arg("search")
        .arg("--gpu-device-ids")
        .arg("0")
        .arg("--json")
        .arg("--verbose")
        .arg("ERROR")
        .arg(&corpus)
        .output()
        .unwrap();

    let cpu_output = tg()
        .current_dir(repo_root())
        .arg("search")
        .arg("--cpu")
        .arg("--json")
        .arg("ERROR")
        .arg(&corpus)
        .output()
        .unwrap();

    assert!(gpu_output.status.success(), "stderr={}", String::from_utf8_lossy(&gpu_output.stderr));
    assert!(cpu_output.status.success(), "stderr={}", String::from_utf8_lossy(&cpu_output.stderr));

    let gpu_payload = parse_json_payload(&gpu_output.stdout);
    assert_eq!(gpu_payload["routing_backend"], "gpu_native");
    assert_eq!(gpu_payload["routing_reason"], "gpu-device-ids-explicit-native");
    assert_eq!(gpu_payload["sidecar_used"], false);
    assert_eq!(gpu_payload["total_files"], 3);
    assert_eq!(gpu_payload["total_matches"], 3);

    let stderr = String::from_utf8_lossy(&gpu_output.stderr);
    assert!(stderr.contains("gpu_batch_files=3"), "stderr={stderr}");
    assert!(stderr.contains("gpu_transfer_bytes="), "stderr={stderr}");

    assert_eq!(parse_json_tuples(&gpu_output.stdout), parse_json_tuples(&cpu_output.stdout));
}

#[test]
fn test_gpu_native_search_avoids_cross_file_boundary_matches() {
    let dir = tempdir().unwrap();
    let corpus = write_boundary_fixture(dir.path());

    let output = tg()
        .current_dir(repo_root())
        .arg("search")
        .arg("--gpu-device-ids")
        .arg("0")
        .arg("--json")
        .arg("ABCD")
        .arg(&corpus)
        .output()
        .unwrap();

    assert!(output.status.success(), "stderr={}", String::from_utf8_lossy(&output.stderr));

    let payload = parse_json_payload(&output.stdout);
    assert_eq!(payload["total_matches"], 1);
    let matches = payload["matches"].as_array().unwrap();
    assert_eq!(matches.len(), 1);
    let tuple = parse_match_tuple(&matches[0]);
    assert!(tuple.0.ends_with("b.txt"), "match tuple={tuple:?}");
    assert_eq!(tuple.1, 2);
    assert_eq!(tuple.2, "ABCD real match");
}

#[test]
fn test_gpu_native_matches_cpu_for_five_patterns_on_ten_mb_corpus() {
    let dir = tempdir().unwrap();
    let corpus = write_gpu_parity_corpus(dir.path(), 10 * 1024 * 1024);
    let patterns = [
        "ERROR critical path failed",
        "WARN retry budget exhausted",
        "Database connection timeout",
        "café",
        "日本語",
    ];

    for pattern in patterns {
        let gpu_output = tg()
            .current_dir(repo_root())
            .arg("search")
            .arg("--fixed-strings")
            .arg("--gpu-device-ids")
            .arg("0")
            .arg("--json")
            .arg(pattern)
            .arg(&corpus)
            .output()
            .unwrap();
        let cpu_output = tg()
            .current_dir(repo_root())
            .arg("search")
            .arg("--fixed-strings")
            .arg("--cpu")
            .arg("--json")
            .arg(pattern)
            .arg(&corpus)
            .output()
            .unwrap();

        assert!(gpu_output.status.success(), "pattern={pattern} stderr={}", String::from_utf8_lossy(&gpu_output.stderr));
        assert!(cpu_output.status.success(), "pattern={pattern} stderr={}", String::from_utf8_lossy(&cpu_output.stderr));

        assert_eq!(parse_json_tuples(&gpu_output.stdout), parse_json_tuples(&cpu_output.stdout), "pattern={pattern}");
    }
}

#[test]
fn test_gpu_native_verbose_output_reports_selected_devices() {
    let devices = enumerate_cuda_devices().unwrap();
    if devices.len() < 2 {
        return;
    }

    let dir = tempdir().unwrap();
    let file_path = write_single_file_fixture(dir.path());

    for device in devices.iter().take(2) {
        let output = tg()
            .current_dir(repo_root())
            .arg("search")
            .arg("--gpu-device-ids")
            .arg(device.device_id.to_string())
            .arg("--json")
            .arg("--verbose")
            .arg("ERROR")
            .arg(&file_path)
            .output()
            .unwrap();

        assert!(output.status.success(), "stderr={}", String::from_utf8_lossy(&output.stderr));
        let stderr = String::from_utf8_lossy(&output.stderr);
        assert!(
            stderr.contains(&format!("selected_gpu_device_id={}", device.device_id)),
            "stderr={stderr}"
        );
        assert!(stderr.contains(&device.name), "stderr={stderr}");
    }
}

#[test]
fn test_gpu_native_multi_gpu_json_reports_both_devices_and_matches_single_gpu() {
    let Some(devices) = first_two_cuda_devices() else {
        return;
    };

    let dir = tempdir().unwrap();
    let corpus = write_gpu_parity_corpus(dir.path(), 12 * 1024 * 1024);
    let device_ids = format!("{},{}", devices[0].0, devices[1].0);

    let multi_output = tg()
        .current_dir(repo_root())
        .arg("search")
        .arg("--fixed-strings")
        .arg("--gpu-device-ids")
        .arg(&device_ids)
        .arg("--json")
        .arg("ERROR critical path failed")
        .arg(&corpus)
        .output()
        .unwrap();
    let single_output = tg()
        .current_dir(repo_root())
        .arg("search")
        .arg("--fixed-strings")
        .arg("--gpu-device-ids")
        .arg(devices[0].0.to_string())
        .arg("--json")
        .arg("ERROR critical path failed")
        .arg(&corpus)
        .output()
        .unwrap();

    assert!(multi_output.status.success(), "stderr={}", String::from_utf8_lossy(&multi_output.stderr));
    assert!(single_output.status.success(), "stderr={}", String::from_utf8_lossy(&single_output.stderr));

    let payload = parse_json_payload(&multi_output.stdout);
    assert_eq!(payload["routing_gpu_device_ids"], serde_json::json!([devices[0].0, devices[1].0]));
    assert_eq!(parse_json_tuples(&multi_output.stdout), parse_json_tuples(&single_output.stdout));
}

#[test]
fn test_gpu_native_multi_gpu_verbose_reports_both_devices_active() {
    let Some(devices) = first_two_cuda_devices() else {
        return;
    };

    let dir = tempdir().unwrap();
    let corpus = write_gpu_parity_corpus(dir.path(), 12 * 1024 * 1024);
    let device_ids = format!("{},{}", devices[0].0, devices[1].0);

    let output = tg()
        .current_dir(repo_root())
        .arg("search")
        .arg("--fixed-strings")
        .arg("--gpu-device-ids")
        .arg(&device_ids)
        .arg("--json")
        .arg("--verbose")
        .arg("ERROR critical path failed")
        .arg(&corpus)
        .output()
        .unwrap();

    assert!(output.status.success(), "stderr={}", String::from_utf8_lossy(&output.stderr));
    let stderr = String::from_utf8_lossy(&output.stderr);
    for (device_id, device_name) in devices {
        assert!(
            stderr.contains(&format!("gpu_device_id={device_id}")),
            "stderr={stderr}"
        );
        assert!(stderr.contains(&device_name), "stderr={stderr}");
        assert!(stderr.contains("gpu_device_files="), "stderr={stderr}");
    }
}

#[test]
fn test_gpu_native_multi_pattern_json_reports_pattern_metadata() {
    let dir = tempdir().unwrap();
    let file_path = write_multi_pattern_file_fixture(dir.path());
    let patterns = ["ERROR", "WARN", "FATAL"];

    let mut command = tg();
    command.current_dir(repo_root());
    command.arg("search");
    command.arg("--fixed-strings");
    command.arg("--gpu-device-ids");
    command.arg("0");
    command.arg("--json");
    for pattern in &patterns {
        command.arg("-e");
        command.arg(pattern);
    }
    command.arg(&file_path);

    let output = command.output().unwrap();
    assert!(output.status.success(), "stderr={}", String::from_utf8_lossy(&output.stderr));

    let payload = parse_json_payload(&output.stdout);
    assert_eq!(payload["routing_backend"], "gpu_native");
    assert_eq!(payload["routing_reason"], "gpu-device-ids-explicit-native");
    assert_eq!(payload["sidecar_used"], false);
    let matches = payload["matches"].as_array().unwrap();
    assert!(matches.iter().all(|matched| matched.get("pattern_id").is_some()));
    assert!(matches.iter().all(|matched| matched.get("pattern_text").is_some()));

    let expected = cpu_union_pattern_tuples(
        &file_path,
        &patterns.iter().map(|pattern| pattern.to_string()).collect::<Vec<_>>(),
    );
    assert_eq!(parse_pattern_json_tuples(&output.stdout), expected);
}

#[test]
fn test_gpu_native_multi_pattern_matches_cpu_union_for_various_pattern_counts() {
    for pattern_count in [2usize, 10, 50] {
        let dir = tempdir().unwrap();
        let (corpus, patterns) = write_pattern_count_corpus(dir.path(), pattern_count);

        let mut command = tg();
        command.current_dir(repo_root());
        command.arg("search");
        command.arg("--fixed-strings");
        command.arg("--gpu-device-ids");
        command.arg("0");
        command.arg("--json");
        for pattern in &patterns {
            command.arg("-e");
            command.arg(pattern);
        }
        command.arg(&corpus);

        let output = command.output().unwrap();
        assert!(output.status.success(), "pattern_count={pattern_count} stderr={}", String::from_utf8_lossy(&output.stderr));

        let expected = cpu_union_pattern_tuples(&corpus, &patterns);
        assert_eq!(parse_pattern_json_tuples(&output.stdout), expected, "pattern_count={pattern_count}");
    }
}

#[test]
fn test_gpu_native_multi_pattern_falls_back_to_batched_passes_for_large_pattern_sets() {
    let Some(device_id) = enumerate_cuda_devices().ok().and_then(|devices| devices.first().map(|device| device.device_id)) else {
        return;
    };

    let dir = tempdir().unwrap();
    let (corpus, patterns) = write_large_pattern_fallback_corpus(dir.path());
    let config = GpuNativeSearchConfig {
        patterns: patterns.clone(),
        paths: vec![corpus.clone()],
        no_ignore: true,
        glob: Vec::new(),
        max_batch_bytes: Some(8 * 1024),
    };

    let stats = gpu_native_search_paths(&config, device_id).unwrap();

    assert!(stats.pipeline.pattern_batch_count > 1, "stats={stats:?}");
    assert!(!stats.pipeline.single_dispatch, "stats={stats:?}");

    let mut actual = stats
        .matches
        .into_iter()
        .map(|matched| {
            (
                matched.path.to_string_lossy().into_owned(),
                matched.line_number,
                matched.text,
                matched.pattern_id,
                matched.pattern_text,
            )
        })
        .collect::<Vec<_>>();
    actual.sort();
    let expected = cpu_union_pattern_tuples(&corpus, &patterns);
    assert_eq!(actual, expected);
}

#[test]
fn test_gpu_native_three_pattern_dispatch_is_below_two_times_single_pattern() {
    let Some(device_id) = enumerate_cuda_devices().ok().and_then(|devices| devices.first().map(|device| device.device_id)) else {
        return;
    };

    let dir = tempdir().unwrap();
    let corpus = write_timing_corpus(dir.path(), 24 * 1024 * 1024);
    let single_config = GpuNativeSearchConfig {
        patterns: vec!["ERROR critical path failed".to_string()],
        paths: vec![corpus.clone()],
        no_ignore: true,
        glob: Vec::new(),
        max_batch_bytes: None,
    };
    let multi_config = GpuNativeSearchConfig {
        patterns: vec![
            "ERROR critical path failed".to_string(),
            "WARN retry budget exhausted".to_string(),
            "FATAL shutdown initiated".to_string(),
        ],
        paths: vec![corpus],
        no_ignore: true,
        glob: Vec::new(),
        max_batch_bytes: None,
    };

    let mut single_samples = Vec::new();
    let mut multi_samples = Vec::new();
    for _ in 0..5 {
        let stats = gpu_native_search_paths(&single_config, device_id).unwrap();
        single_samples.push(stats.pipeline.wall_time_ms);

        let stats = gpu_native_search_paths(&multi_config, device_id).unwrap();
        multi_samples.push(stats.pipeline.wall_time_ms);
    }

    let single_ms = median_ms(&mut single_samples);
    let multi_ms = median_ms(&mut multi_samples);
    assert!(multi_ms < single_ms * 2.0, "single_ms={single_ms} multi_ms={multi_ms}");
}
