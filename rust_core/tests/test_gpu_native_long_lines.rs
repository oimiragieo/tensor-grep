#![cfg(feature = "cuda")]

use std::fs;
use std::path::{Path, PathBuf};

use tempfile::tempdir;
use tensor_grep_rs::gpu_native::{
    enumerate_cuda_devices, gpu_native_search_paths, GpuNativeSearchConfig,
};

fn first_device_id() -> Option<i32> {
    enumerate_cuda_devices()
        .ok()
        .and_then(|devices| devices.first().map(|device| device.device_id))
}

fn build_line_with_pattern(target_len: usize, pattern: &str, seed: usize) -> String {
    let prefix = format!("line-{seed:04} ");
    let suffix = format!(" {pattern} tail-{seed:04}");
    let filler_len = target_len
        .saturating_sub(prefix.len())
        .saturating_sub(suffix.len());
    format!("{prefix}{}{suffix}", "x".repeat(filler_len.max(1)))
}

fn write_single_file_corpus(dir: &Path, name: &str, line_lens: &[usize], pattern: &str) -> PathBuf {
    let corpus = dir.join(name);
    fs::create_dir(&corpus).unwrap();
    let file_path = corpus.join("corpus.log");
    let mut body = String::new();
    for (index, line_len) in line_lens.iter().enumerate() {
        body.push_str(&build_line_with_pattern(*line_len, pattern, index));
        body.push('\n');
    }
    fs::write(file_path, body).unwrap();
    corpus
}

fn write_mixed_dispatch_corpus(dir: &Path, pattern: &str) -> PathBuf {
    let corpus = dir.join("mixed-dispatch");
    fs::create_dir(&corpus).unwrap();
    let file_path = corpus.join("mixed.log");
    let mut body = String::new();
    body.push_str(&format!("short {pattern}\n"));
    body.push_str(&build_line_with_pattern(512, pattern, 1));
    body.push('\n');
    body.push_str(&build_line_with_pattern(10 * 1024, pattern, 2));
    body.push('\n');
    body.push_str(&build_line_with_pattern(100 * 1024, pattern, 3));
    body.push('\n');
    fs::write(file_path, body).unwrap();
    corpus
}

fn write_short_vs_long_perf_corpora(dir: &Path, pattern: &str) -> (PathBuf, PathBuf) {
    let short_lines = vec![96usize; 32_768];
    let long_lines = vec![8 * 1024usize; 384];
    let short = write_single_file_corpus(dir, "short-lines", &short_lines, pattern);
    let long = write_single_file_corpus(dir, "long-lines", &long_lines, pattern);
    (short, long)
}

fn cpu_expected_matches(corpus: &Path, pattern: &str) -> Vec<(String, usize, String)> {
    let path = corpus.join("corpus.log");
    let body = fs::read_to_string(path).unwrap();
    body.lines()
        .enumerate()
        .filter_map(|(index, line)| {
            line.contains(pattern).then(|| {
                (
                    corpus.join("corpus.log").to_string_lossy().into_owned(),
                    index + 1,
                    line.to_string(),
                )
            })
        })
        .collect()
}

fn gpu_match_tuples(
    config: &GpuNativeSearchConfig,
    device_id: i32,
) -> Vec<(String, usize, String)> {
    let mut tuples = gpu_native_search_paths(config, device_id)
        .unwrap()
        .matches
        .into_iter()
        .map(|matched| {
            (
                matched.path.to_string_lossy().into_owned(),
                matched.line_number,
                matched.text,
            )
        })
        .collect::<Vec<_>>();
    tuples.sort();
    tuples
}

fn median_ms(samples: &mut [f64]) -> f64 {
    samples.sort_by(|left, right| left.partial_cmp(right).unwrap());
    samples[samples.len() / 2]
}

#[test]
fn test_gpu_native_adaptive_dispatch_matches_cpu_for_ten_kib_lines() {
    let Some(device_id) = first_device_id() else {
        return;
    };

    let dir = tempdir().unwrap();
    let pattern = "ERROR warp sentinel";
    let corpus = write_single_file_corpus(dir.path(), "ten-kib-lines", &[10 * 1024; 8], pattern);
    let config = GpuNativeSearchConfig {
        patterns: vec![pattern.to_string()],
        paths: vec![corpus.clone()],
        no_ignore: true,
        glob: Vec::new(),
        max_batch_bytes: Some(256 * 1024),
    };

    let stats = gpu_native_search_paths(&config, device_id).unwrap();
    let mut actual = stats
        .matches
        .iter()
        .map(|matched| {
            (
                matched.path.to_string_lossy().into_owned(),
                matched.line_number,
                matched.text.clone(),
            )
        })
        .collect::<Vec<_>>();
    actual.sort();

    let mut expected = cpu_expected_matches(&corpus, pattern);
    expected.sort();

    assert_eq!(actual, expected);
    assert_eq!(stats.pipeline.long_line_count, 8, "stats={stats:?}");
    assert!(stats.pipeline.block_dispatch_count >= 1, "stats={stats:?}");
}

#[test]
fn test_gpu_native_adaptive_dispatch_matches_cpu_for_hundred_kib_lines() {
    let Some(device_id) = first_device_id() else {
        return;
    };

    let dir = tempdir().unwrap();
    let pattern = "ERROR block sentinel";
    let corpus =
        write_single_file_corpus(dir.path(), "hundred-kib-lines", &[100 * 1024; 4], pattern);
    let config = GpuNativeSearchConfig {
        patterns: vec![pattern.to_string()],
        paths: vec![corpus.clone()],
        no_ignore: true,
        glob: Vec::new(),
        max_batch_bytes: Some(512 * 1024),
    };

    let stats = gpu_native_search_paths(&config, device_id).unwrap();
    let mut actual = stats
        .matches
        .iter()
        .map(|matched| {
            (
                matched.path.to_string_lossy().into_owned(),
                matched.line_number,
                matched.text.clone(),
            )
        })
        .collect::<Vec<_>>();
    actual.sort();

    let mut expected = cpu_expected_matches(&corpus, pattern);
    expected.sort();

    assert_eq!(actual, expected);
    assert_eq!(stats.pipeline.long_line_count, 4, "stats={stats:?}");
    assert!(stats.pipeline.block_dispatch_count >= 1, "stats={stats:?}");
}

#[test]
fn test_gpu_native_adaptive_dispatch_classifies_mixed_short_medium_and_long_lines() {
    let Some(device_id) = first_device_id() else {
        return;
    };

    let dir = tempdir().unwrap();
    let pattern = "ERROR mixed sentinel";
    let corpus = write_mixed_dispatch_corpus(dir.path(), pattern);
    let config = GpuNativeSearchConfig {
        patterns: vec![pattern.to_string()],
        paths: vec![corpus.clone()],
        no_ignore: true,
        glob: Vec::new(),
        max_batch_bytes: Some(256 * 1024),
    };

    let stats = gpu_native_search_paths(&config, device_id).unwrap();
    assert!(stats.pipeline.short_line_count >= 1, "stats={stats:?}");
    assert!(stats.pipeline.medium_line_count >= 1, "stats={stats:?}");
    assert!(stats.pipeline.long_line_count >= 2, "stats={stats:?}");
    assert!(stats.pipeline.warp_dispatch_count >= 1, "stats={stats:?}");
    assert!(stats.pipeline.block_dispatch_count >= 1, "stats={stats:?}");

    let mut actual = stats
        .matches
        .into_iter()
        .map(|matched| {
            (
                matched.path.to_string_lossy().into_owned(),
                matched.line_number,
                matched.text,
            )
        })
        .collect::<Vec<_>>();
    actual.sort();
    let expected_path = corpus.join("mixed.log").to_string_lossy().into_owned();
    let expected = vec![
        (expected_path.clone(), 1, format!("short {pattern}")),
        (
            expected_path.clone(),
            2,
            build_line_with_pattern(512, pattern, 1),
        ),
        (
            expected_path.clone(),
            3,
            build_line_with_pattern(10 * 1024, pattern, 2),
        ),
        (
            expected_path,
            4,
            build_line_with_pattern(100 * 1024, pattern, 3),
        ),
    ];
    assert_eq!(actual, expected);
}

#[test]
fn test_gpu_native_long_line_corpus_is_not_slower_than_short_line_corpus() {
    let Some(device_id) = first_device_id() else {
        return;
    };

    let dir = tempdir().unwrap();
    let pattern = "ERROR perf sentinel";
    let (short_corpus, long_corpus) = write_short_vs_long_perf_corpora(dir.path(), pattern);

    let short_config = GpuNativeSearchConfig {
        patterns: vec![pattern.to_string()],
        paths: vec![short_corpus],
        no_ignore: true,
        glob: Vec::new(),
        max_batch_bytes: Some(2 * 1024 * 1024),
    };
    let long_config = GpuNativeSearchConfig {
        patterns: vec![pattern.to_string()],
        paths: vec![long_corpus],
        no_ignore: true,
        glob: Vec::new(),
        max_batch_bytes: Some(2 * 1024 * 1024),
    };

    let mut short_samples = Vec::new();
    let mut long_samples = Vec::new();
    for _ in 0..5 {
        short_samples.push(
            gpu_native_search_paths(&short_config, device_id)
                .unwrap()
                .pipeline
                .wall_time_ms,
        );
        long_samples.push(
            gpu_native_search_paths(&long_config, device_id)
                .unwrap()
                .pipeline
                .wall_time_ms,
        );
    }

    let short_ms = median_ms(&mut short_samples);
    let long_ms = median_ms(&mut long_samples);
    // Keep a small tolerance for host scheduling noise while still rejecting
    // meaningful regressions in the adaptive long-line path.
    assert!(
        long_ms <= short_ms * 1.10,
        "short_ms={short_ms} long_ms={long_ms}"
    );
    assert!(gpu_match_tuples(&long_config, device_id).len() > 0);
}
