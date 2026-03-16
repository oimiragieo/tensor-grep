#![cfg(feature = "cuda")]

use tensor_grep_rs::gpu_native::{
    compile_search_kernel, detect_compute_capability, enumerate_cuda_devices, gpu_native_search,
    MatchPosition,
};

fn cpu_match_positions(pattern: &str, data: &[u8]) -> Vec<MatchPosition> {
    let needle = pattern.as_bytes();
    if needle.is_empty() || needle.len() > data.len() {
        return Vec::new();
    }

    data.windows(needle.len())
        .enumerate()
        .filter_map(|(offset, window)| {
            (window == needle).then_some(MatchPosition { byte_offset: offset })
        })
        .collect()
}

fn first_device_id() -> i32 {
    enumerate_cuda_devices().unwrap()[0].device_id
}

#[test]
fn test_compile_search_kernel_succeeds() {
    compile_search_kernel(first_device_id()).unwrap();
}

#[test]
fn test_gpu_native_search_finds_known_pattern() {
    let haystack = b"INFO start\nERROR one\nWARN mid\nERROR two\n";
    let matches = gpu_native_search("ERROR", haystack, first_device_id()).unwrap();

    assert_eq!(matches, cpu_match_positions("ERROR", haystack));
}

#[test]
fn test_gpu_native_matches_cpu_for_same_input() {
    let haystack = "cafe\ncaf\u{00e9}\n\u{65e5}\u{672c}\u{8a9e}\ncaf\u{00e9}\n".as_bytes();
    let matches = gpu_native_search("caf\u{00e9}", haystack, first_device_id()).unwrap();

    assert_eq!(matches, cpu_match_positions("caf\u{00e9}", haystack));
}

#[test]
fn test_device_enumeration_lists_available_gpus() {
    let devices = enumerate_cuda_devices().unwrap();

    assert!(!devices.is_empty());
    assert!(devices.iter().all(|device| !device.name.trim().is_empty()));
    assert!(
        devices
            .iter()
            .all(|device| device.compute_capability.0 > 0 && device.compute_capability.1 >= 0)
    );
}

#[test]
fn test_compute_capability_detected_for_selected_device() {
    let capability = detect_compute_capability(first_device_id()).unwrap();

    assert!(capability.0 > 0);
    assert!(capability.1 >= 0);
}

#[test]
fn test_invalid_device_id_returns_clear_error() {
    let err = gpu_native_search("needle", b"needle", 99).unwrap_err();
    let message = err.to_string();

    assert!(message.contains("99"));
    assert!(message.contains("available"));
}
