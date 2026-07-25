#![cfg(feature = "cuda")]
//! Every test in this file needs a REAL CUDA driver and device -- each one reaches
//! `first_device_id()` (or a device entry point directly), which calls `enumerate_cuda_devices`.
//! On a runner without the driver, cudarc panics with
//! `Unable to dynamically load the "cuda" shared library`.
//!
//! They are therefore all `#[ignore]`d (task #279). Being explicit about this is what LETS the
//! rest of the cuda-gated suite run: `cargo test --features cuda` in `cuda-feature-check`
//! executes 279 other tests -- including `gpu_native.rs`'s own `#[cfg(test)]` module and the
//! lossless-emitter regression tests from #273/#754 -- and this file was the only thing failing
//! the job.
//!
//! To be precise about what `#[ignore]` does and does not buy: these 8 have NEVER executed in
//! CI, because no CUDA runner exists. Marking them makes that pre-existing reality visible in
//! the test output (`8 ignored`) instead of invisible, and it is NOT a claim that they pass.
//! On a machine with a GPU, run them with:
//!
//!     cargo test --features cuda --test test_gpu_native -- --ignored

use tensor_grep_rs::gpu_native::{
    compile_search_kernel, detect_compute_capability, enumerate_cuda_devices, gpu_native_search,
    gpu_native_search_patterns, MatchPosition, PatternMatchPosition,
};

fn cpu_match_positions(pattern: &str, data: &[u8]) -> Vec<MatchPosition> {
    let needle = pattern.as_bytes();
    if needle.is_empty() || needle.len() > data.len() {
        return Vec::new();
    }

    data.windows(needle.len())
        .enumerate()
        .filter_map(|(offset, window)| {
            (window == needle).then_some(MatchPosition {
                byte_offset: offset,
            })
        })
        .collect()
}

fn cpu_match_positions_for_patterns(patterns: &[&str], data: &[u8]) -> Vec<PatternMatchPosition> {
    let mut matches = Vec::new();
    for (pattern_id, pattern) in patterns.iter().enumerate() {
        let needle = pattern.as_bytes();
        if needle.is_empty() || needle.len() > data.len() {
            continue;
        }

        matches.extend(
            data.windows(needle.len())
                .enumerate()
                .filter_map(|(offset, window)| {
                    (window == needle).then_some(PatternMatchPosition {
                        byte_offset: offset,
                        pattern_id,
                    })
                }),
        );
    }
    matches.sort_by_key(|matched| (matched.byte_offset, matched.pattern_id));
    matches
}

fn first_device_id() -> i32 {
    enumerate_cuda_devices().unwrap()[0].device_id
}

#[test]
#[ignore = "requires a real CUDA driver + device; run with `--ignored` on a GPU runner"]
fn test_compile_search_kernel_succeeds() {
    compile_search_kernel(first_device_id()).unwrap();
}

#[test]
#[ignore = "requires a real CUDA driver + device; run with `--ignored` on a GPU runner"]
fn test_gpu_native_search_finds_known_pattern() {
    let haystack = b"INFO start\nERROR one\nWARN mid\nERROR two\n";
    let matches = gpu_native_search("ERROR", haystack, first_device_id()).unwrap();

    assert_eq!(matches, cpu_match_positions("ERROR", haystack));
}

#[test]
#[ignore = "requires a real CUDA driver + device; run with `--ignored` on a GPU runner"]
fn test_gpu_native_matches_cpu_for_same_input() {
    let haystack = "cafe\ncaf\u{00e9}\n\u{65e5}\u{672c}\u{8a9e}\ncaf\u{00e9}\n".as_bytes();
    let matches = gpu_native_search("caf\u{00e9}", haystack, first_device_id()).unwrap();

    assert_eq!(matches, cpu_match_positions("caf\u{00e9}", haystack));
}

#[test]
#[ignore = "requires a real CUDA driver + device; run with `--ignored` on a GPU runner"]
fn test_gpu_native_search_patterns_reports_pattern_ids() {
    let haystack = b"INFO\nERROR one\nWARN two\nERRORWARN combo\nFATAL three\n";
    let patterns = ["ERROR", "WARN", "FATAL"];
    let matches = gpu_native_search_patterns(&patterns, haystack, first_device_id()).unwrap();

    assert_eq!(
        matches,
        cpu_match_positions_for_patterns(&patterns, haystack)
    );
}

#[test]
#[ignore = "requires a real CUDA driver + device; run with `--ignored` on a GPU runner"]
fn test_gpu_native_search_patterns_matches_cpu_for_unicode_inputs() {
    let haystack = "cafe\ncaf\u{00e9}\n\u{65e5}\u{672c}\u{8a9e}\nemoji \u{1f50d}\n".as_bytes();
    let patterns = ["caf\u{00e9}", "\u{65e5}\u{672c}\u{8a9e}", "\u{1f50d}"];
    let matches = gpu_native_search_patterns(&patterns, haystack, first_device_id()).unwrap();

    assert_eq!(
        matches,
        cpu_match_positions_for_patterns(&patterns, haystack)
    );
}

#[test]
#[ignore = "requires a real CUDA driver + device; run with `--ignored` on a GPU runner"]
fn test_device_enumeration_lists_available_gpus() {
    let devices = enumerate_cuda_devices().unwrap();

    assert!(!devices.is_empty());
    assert!(devices.iter().all(|device| !device.name.trim().is_empty()));
    assert!(devices
        .iter()
        .all(|device| device.compute_capability.0 > 0 && device.compute_capability.1 >= 0));
}

#[test]
#[ignore = "requires a real CUDA driver + device; run with `--ignored` on a GPU runner"]
fn test_compute_capability_detected_for_selected_device() {
    let capability = detect_compute_capability(first_device_id()).unwrap();

    assert!(capability.0 > 0);
    assert!(capability.1 >= 0);
}

#[test]
#[ignore = "requires a real CUDA driver + device; run with `--ignored` on a GPU runner"]
fn test_invalid_device_id_returns_clear_error() {
    let err = gpu_native_search("needle", b"needle", 99).unwrap_err();
    let message = err.to_string();

    assert!(message.contains("99"));
    assert!(message.contains("available"));
}
