#![cfg(feature = "cuda")]

use std::fs;
use std::path::{Path, PathBuf};

use tempfile::tempdir;
use tensor_grep_rs::gpu_native::{
    benchmark_cuda_graph_search_paths, enumerate_cuda_devices, GpuNativeSearchConfig,
};

fn first_device_id() -> Option<i32> {
    enumerate_cuda_devices()
        .ok()
        .and_then(|devices| devices.first().map(|device| device.device_id))
}

fn write_cuda_graph_corpus(dir: &Path, file_count: usize) -> PathBuf {
    let corpus = dir.join("cuda-graph-corpus");
    fs::create_dir(&corpus).unwrap();

    let repeated = "padding block for cuda graph batches ".repeat(96);
    let body = format!(
        "INFO graph capture bootstrap\n{repeated}\nERROR cuda graph sentinel\nWARN graph replay footer\n"
    );

    for index in 0..file_count {
        fs::write(corpus.join(format!("batch-{index:03}.log")), &body).unwrap();
    }

    corpus
}

#[test]
fn test_gpu_native_replays_captured_cuda_graphs_without_changing_results() {
    let Some(device_id) = first_device_id() else {
        return;
    };

    let dir = tempdir().unwrap();
    let corpus = write_cuda_graph_corpus(dir.path(), 128);
    let config = GpuNativeSearchConfig {
        patterns: vec!["ERROR cuda graph sentinel".to_string()],
        paths: vec![corpus],
        no_ignore: true,
        glob: Vec::new(),
        max_batch_bytes: Some(4 * 1024),
    };

    let benchmark = benchmark_cuda_graph_search_paths(&config, device_id).unwrap();

    assert!(
        benchmark.baseline.pipeline.batch_count >= 100,
        "benchmark={benchmark:?}"
    );
    assert!(benchmark.results_identical, "benchmark={benchmark:?}");
    assert_eq!(benchmark.baseline.matches, benchmark.graphed.matches);
    assert!(
        benchmark.graphed.pipeline.cuda_graph_captures >= 1,
        "benchmark={benchmark:?}"
    );
    assert!(
        benchmark.graphed.pipeline.cuda_graph_replays >= 1,
        "benchmark={benchmark:?}"
    );
}

#[test]
#[ignore = "benchmark-style CUDA graph throughput gate for manual advanced GPU verification"]
fn test_gpu_native_cuda_graphs_reduce_batch_overhead_by_ten_percent() {
    let Some(device_id) = first_device_id() else {
        return;
    };

    let dir = tempdir().unwrap();
    let corpus = write_cuda_graph_corpus(dir.path(), 160);
    let config = GpuNativeSearchConfig {
        patterns: vec!["ERROR cuda graph sentinel".to_string()],
        paths: vec![corpus],
        no_ignore: true,
        glob: Vec::new(),
        max_batch_bytes: Some(4 * 1024),
    };

    let benchmark = benchmark_cuda_graph_search_paths(&config, device_id).unwrap();

    assert!(
        benchmark.baseline.pipeline.batch_count >= 100,
        "benchmark={benchmark:?}"
    );
    assert!(benchmark.results_identical, "benchmark={benchmark:?}");
    assert!(
        benchmark.wall_time_reduction_pct >= 10.0,
        "benchmark={benchmark:?}"
    );
}
