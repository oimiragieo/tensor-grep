#![cfg(feature = "cuda")]

use std::fs;
use std::path::{Path, PathBuf};

use cudarc::driver::CudaContext;
use tempfile::tempdir;
use tensor_grep_rs::gpu_native::{
    benchmark_pinned_transfer_throughput, enumerate_cuda_devices, gpu_native_search_paths,
    GpuNativeSearchConfig, GpuPinnedTransferBenchmark,
};

fn first_device_id() -> Option<i32> {
    enumerate_cuda_devices()
        .ok()
        .and_then(|devices| devices.first().map(|device| device.device_id))
}

fn write_overlap_corpus(dir: &Path) -> (PathBuf, Vec<(PathBuf, usize, String)>) {
    let corpus = dir.join("overlap-corpus");
    fs::create_dir(&corpus).unwrap();

    let mut expected = Vec::new();
    for index in 0..4 {
        let path = corpus.join(format!("chunk-{index}.log"));
        let body = format!(
            "INFO batch {index} start\n{}\nWARN filler batch {index}\n{}\n",
            "padding line ".repeat(262_144),
            "ERROR overlap sentinel".repeat(128)
        );
        fs::write(&path, &body).unwrap();
        expected.push((path.clone(), 4, "ERROR overlap sentinel".repeat(128)));
    }

    (corpus, expected)
}

#[test]
fn test_cuda_pinned_host_allocation_succeeds() {
    let Some(device_id) = first_device_id() else {
        return;
    };

    let context = CudaContext::new(device_id as usize).unwrap();
    let pinned = unsafe { context.alloc_pinned::<u8>(4 * 1024) }.unwrap();

    assert_eq!(pinned.len(), 4 * 1024);
    assert_eq!(pinned.num_bytes(), 4 * 1024);
}

#[test]
fn test_gpu_native_search_reports_pinned_double_buffered_stream_pipeline() {
    let Some(device_id) = first_device_id() else {
        return;
    };

    let dir = tempdir().unwrap();
    let (corpus, _expected) = write_overlap_corpus(dir.path());
    let config = GpuNativeSearchConfig {
        pattern: "ERROR overlap sentinel".to_string(),
        paths: vec![corpus],
        no_ignore: true,
        glob: Vec::new(),
        max_batch_bytes: Some(64 * 1024),
    };

    let stats = gpu_native_search_paths(&config, device_id).unwrap();

    assert!(stats.pipeline.pinned_host_buffers);
    assert!(stats.pipeline.double_buffered);
    assert!(stats.pipeline.stream_count >= 2);
    assert!(stats.pipeline.batch_count >= 2);
    assert!(stats.pipeline.overlapped_batches >= 1);
}

#[test]
fn test_gpu_native_overlap_matches_expected_results_without_races() {
    let Some(device_id) = first_device_id() else {
        return;
    };

    let dir = tempdir().unwrap();
    let (corpus, expected) = write_overlap_corpus(dir.path());
    let config = GpuNativeSearchConfig {
        pattern: "ERROR overlap sentinel".to_string(),
        paths: vec![corpus],
        no_ignore: true,
        glob: Vec::new(),
        max_batch_bytes: Some(64 * 1024),
    };

    let mut actual = gpu_native_search_paths(&config, device_id)
        .unwrap()
        .matches
        .into_iter()
        .map(|matched| (matched.path, matched.line_number, matched.text))
        .collect::<Vec<_>>();
    actual.sort();

    let mut expected = expected;
    expected.sort();
    assert_eq!(actual, expected);
}

#[test]
fn test_gpu_native_overlap_metrics_show_transfer_compute_overlap() {
    let Some(device_id) = first_device_id() else {
        return;
    };

    let dir = tempdir().unwrap();
    let (corpus, _expected) = write_overlap_corpus(dir.path());
    let config = GpuNativeSearchConfig {
        pattern: "ERROR overlap sentinel".to_string(),
        paths: vec![corpus],
        no_ignore: true,
        glob: Vec::new(),
        max_batch_bytes: Some(64 * 1024),
    };

    let stats = gpu_native_search_paths(&config, device_id).unwrap();

    assert!(stats.pipeline.transfer_time_ms > 0.0);
    assert!(stats.pipeline.kernel_time_ms > 0.0);
    assert!(stats.pipeline.wall_time_ms > 0.0);
    assert!(
        stats.pipeline.wall_time_ms
            < f64::from(stats.pipeline.transfer_time_ms + stats.pipeline.kernel_time_ms),
        "wall={} transfer={} kernel={}",
        stats.pipeline.wall_time_ms,
        stats.pipeline.transfer_time_ms,
        stats.pipeline.kernel_time_ms,
    );
}

#[test]
fn test_benchmark_pinned_transfer_throughput_reports_positive_bandwidth() {
    let Some(device_id) = first_device_id() else {
        return;
    };

    let benchmark = benchmark_pinned_transfer_throughput(device_id, 32 * 1024 * 1024, 8 * 1024 * 1024)
        .unwrap();

    assert!(benchmark.pinned_host_buffers);
    assert!(benchmark.batch_count >= 1);
    assert!(benchmark.stream_count >= 1);
    assert!(benchmark.throughput_bytes_per_s > 0.0);
}

#[test]
#[ignore = "benchmark-style throughput gate for manual advanced GPU verification"]
fn test_benchmark_pinned_transfer_throughput_reaches_ten_gb_per_second_at_one_gib() {
    let Ok(devices) = enumerate_cuda_devices() else {
        return;
    };
    if devices.is_empty() {
        return;
    }

    let mut best_device_id = None;
    let mut best_benchmark: Option<GpuPinnedTransferBenchmark> = None;
    for device in devices {
        let benchmark = benchmark_pinned_transfer_throughput(
            device.device_id,
            1024 * 1024 * 1024,
            1024 * 1024 * 1024,
        )
        .unwrap();
        let is_better = match best_benchmark.as_ref() {
            Some(current) => benchmark.throughput_bytes_per_s > current.throughput_bytes_per_s,
            None => true,
        };
        if is_better {
            best_device_id = Some(device.device_id);
            best_benchmark = Some(benchmark);
        }
    }

    let benchmark = best_benchmark.unwrap();
    let device_id = best_device_id.unwrap();

    assert!(
        benchmark.throughput_bytes_per_s >= 10_000_000_000.0,
        "device_id={} throughput_bytes_per_s={} wall_time_ms={} transfer_time_ms={}",
        device_id,
        benchmark.throughput_bytes_per_s,
        benchmark.wall_time_ms,
        benchmark.transfer_time_ms,
    );
}
