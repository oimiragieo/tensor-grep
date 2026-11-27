#![allow(unused_imports)]
use super::{
    cached_search_kernel_ptx_path, check_gpu_native_implicit_walk_ceiling, collect_search_files,
    kernel_compile_options_for_compute_capability, line_matches_for_file,
    line_matches_for_file_by_scanning, load_file_batch_into_pinned_slice,
    resolve_adaptive_match_capacity, resolve_max_match_capacity,
    validate_requested_cuda_device_ids, BatchedFile, FileBatchPlan, GpuNativeSearchConfig,
    LineDescriptor, PatternMatchPosition,
};
use base64::engine::general_purpose::STANDARD as TEST_BASE64_STANDARD;
use base64::Engine as _;
use std::fs;
use std::path::{Path, PathBuf};

// --- Audit #109: GPU-native implicit-walk-ceiling gate --------------------------------
// Mirrors native_search.rs's audit #105 test suite for `check_native_implicit_walk_ceiling`.
// #105 hoisted a walk-ceiling gate into the native-CPU engine but left the GPU-native engine
// (this module) with NO ceiling at all -- `GpuNativeSearchConfig` did not even have a
// `path_was_implicit` field, so a bare implicit-path `--gpu-device-ids` search on a huge root
// walked unbounded through `collect_walked_files`.

fn make_stub_file_dir(dir: &Path, file_count: usize) {
    for index in 0..file_count {
        fs::write(
            dir.join(format!("stub_{index}.py")),
            "nothing interesting\n",
        )
        .unwrap();
    }
}

fn config_with_paths(paths: Vec<PathBuf>, path_was_implicit: bool) -> GpuNativeSearchConfig {
    GpuNativeSearchConfig {
        patterns: vec!["TODO".to_string()],
        paths,
        path_was_implicit,
        ..GpuNativeSearchConfig::default()
    }
}

#[test]
fn check_gpu_native_implicit_walk_ceiling_refuses_oversized_implicit_walk() {
    // RED-before-fix: this is the exact shape of the #109 bypass -- an implicit-path search
    // (no explicit PATH positional) on a root over the 1500-file ceiling.
    let dir = tempfile::tempdir().unwrap();
    make_stub_file_dir(dir.path(), 1600);
    let roots = vec![dir.path().to_path_buf()];
    let config = config_with_paths(roots.clone(), true);

    let refusal = check_gpu_native_implicit_walk_ceiling(&config, &roots);

    assert!(
        refusal.is_some(),
        "an oversized implicit-path walk must be refused"
    );
}

#[test]
fn check_gpu_native_implicit_walk_ceiling_allows_explicit_path_even_when_oversized() {
    // Non-regression (Trap #3 parity, mirrors native_search.rs/rg_passthrough.rs): an
    // EXPLICIT, deliberately-scoped PATH must never be refused regardless of size.
    let dir = tempfile::tempdir().unwrap();
    make_stub_file_dir(dir.path(), 1600);
    let roots = vec![dir.path().to_path_buf()];
    let config = config_with_paths(roots.clone(), false);

    let refusal = check_gpu_native_implicit_walk_ceiling(&config, &roots);

    assert!(
        refusal.is_none(),
        "an explicit path must run uninhibited even when the walk exceeds the ceiling"
    );
}

#[test]
fn check_gpu_native_implicit_walk_ceiling_allows_implicit_path_under_ceiling() {
    // Normal-case non-regression: an implicit path under the ceiling is unaffected -- a
    // typical repo must never be refused.
    let dir = tempfile::tempdir().unwrap();
    make_stub_file_dir(dir.path(), 50);
    let roots = vec![dir.path().to_path_buf()];
    let config = config_with_paths(roots.clone(), true);

    let refusal = check_gpu_native_implicit_walk_ceiling(&config, &roots);

    assert!(
        refusal.is_none(),
        "a 50-file implicit root must not be refused"
    );
}

#[test]
fn collect_search_files_refuses_oversized_implicit_walk_before_enumerating() {
    // Hermetic end-to-end test of the actual `collect_search_files` production entry point
    // `gpu_native_search_paths_multi_with_options` calls before ever touching a CUDA device
    // (see that function: `collect_search_files(config)?` runs BEFORE `resolve_cuda_devices`),
    // so this test exercises the real bug with no GPU/CUDA runtime involved. Bounded per
    // anti-hang-test-protocol: run on a joined worker thread with an explicit timeout so a
    // regression (the gate silently stops firing) that falls through to the unbounded
    // parallel walk cannot hang the test runner -- it fails fast with a clear panic instead.
    let dir = tempfile::tempdir().unwrap();
    make_stub_file_dir(dir.path(), 1600);
    let config = config_with_paths(vec![dir.path().to_path_buf()], true);

    let (tx, rx) = std::sync::mpsc::channel();
    std::thread::spawn(move || {
        let result = collect_search_files(&config).map_err(|error| error.to_string());
        let _ = tx.send(result);
    });
    let result = rx.recv_timeout(std::time::Duration::from_secs(10)).expect(
        "collect_search_files must return well within 10s -- a hang here means the \
         walk-ceiling gate did not fire before an unbounded parallel walk",
    );

    let err = result.expect_err("an oversized implicit-path walk must be refused, not Ok");
    assert!(
        crate::rg_passthrough::is_unbounded_implicit_search_walk_refusal(&err),
        "unexpected error (expected the walk-ceiling refusal): {err}"
    );
}

#[test]
fn collect_search_files_does_not_refuse_explicit_oversized_path() {
    // Non-regression: an explicit PATH (even oversized) must complete normally, not be
    // refused -- fail-open for explicit scoping is the whole point of the guard (Trap #3
    // parity). Bounded per anti-hang-test-protocol.
    let dir = tempfile::tempdir().unwrap();
    make_stub_file_dir(dir.path(), 1600);
    let config = config_with_paths(vec![dir.path().to_path_buf()], false);

    let (tx, rx) = std::sync::mpsc::channel();
    std::thread::spawn(move || {
        let result = collect_search_files(&config).map_err(|error| error.to_string());
        let _ = tx.send(result);
    });
    let result = rx
        .recv_timeout(std::time::Duration::from_secs(20))
        .expect("collect_search_files must return well within 20s for an explicit path");

    result.expect("an explicit oversized path must not be refused");
}

#[test]
fn validate_requested_cuda_device_ids_preserves_selected_subset_without_expanding() {
    let selected = validate_requested_cuda_device_ids(&[0, 0], 2).unwrap();

    assert_eq!(selected, vec![0]);
}

#[test]
fn validate_requested_cuda_device_ids_rejects_out_of_range_with_available_ids() {
    let err = validate_requested_cuda_device_ids(&[3], 2).unwrap_err();
    let message = err.to_string();

    assert!(message.contains("invalid CUDA device id 3"));
    assert!(message.contains("available CUDA devices: 0, 1"));
}

#[test]
fn adaptive_line_match_capacity_scales_with_lines_not_batch_bytes() {
    let five_gb_shard_bytes = 671_088_644usize;
    let generated_line_count = 182_324usize;

    let byte_capacity = resolve_max_match_capacity(five_gb_shard_bytes, 1).unwrap();
    let adaptive_capacity = resolve_adaptive_match_capacity(generated_line_count, 1).unwrap();

    assert_eq!(adaptive_capacity, generated_line_count);
    assert!(byte_capacity > adaptive_capacity * 1_000);
}

#[test]
fn kernel_compile_options_include_selected_compute_capability_architecture() {
    let options = kernel_compile_options_for_compute_capability((12, 0)).unwrap();

    assert_eq!(options.name.as_deref(), Some("gpu_native_search.cu"));
    assert!(options
        .options
        .contains(&"--gpu-architecture=compute_120".to_string()));
}

#[test]
fn kernel_compile_options_reject_invalid_capability_without_downgrade() {
    let err = kernel_compile_options_for_compute_capability((0, 0)).unwrap_err();
    let message = err.to_string();

    assert!(message.contains("invalid CUDA compute capability 0.0"));
    assert!(message.contains("not falling back"));
}

#[test]
fn file_batch_loading_preprocesses_pageable_memory_before_pinned_copy() {
    let temp = tempfile::tempdir().unwrap();
    let text_path = temp.path().join("text.log");
    let binary_path = temp.path().join("binary.log");
    fs::write(&text_path, b"INFO start\nERROR target\n").unwrap();
    fs::write(&binary_path, b"INFO\0binary\nERROR ignored\n").unwrap();
    let text_len = fs::metadata(&text_path).unwrap().len() as usize;
    let binary_len = fs::metadata(&binary_path).unwrap().len() as usize;
    let plan = FileBatchPlan {
        files: vec![text_path.clone(), binary_path],
        estimated_bytes: text_len + binary_len + 1,
    };
    let mut host = vec![0u8; plan.estimated_bytes + 16];

    let loaded = load_file_batch_into_pinned_slice(&mut host, &plan, true).unwrap();

    assert_eq!(loaded.files.len(), 1);
    assert_eq!(loaded.files[0].path, text_path);
    assert_eq!(loaded.cpu_staging_bytes, text_len);
    assert_eq!(loaded.pageable_host_staging_bytes, text_len + binary_len);
    assert!(loaded.host_to_pinned_copy_time_ms >= 0.0);
    assert_eq!(loaded.classified_lines.short_line_count(), 2);
    assert_eq!(&host[..text_len], b"INFO start\nERROR target\n");
}

#[test]
fn ptx_cache_path_is_scoped_by_architecture_and_kernel_hash() {
    let cache_path =
        cached_search_kernel_ptx_path((12, 0), &["--gpu-architecture=compute_120".to_string()])
            .unwrap();
    let cache_file = cache_path
        .file_name()
        .and_then(|name| name.to_str())
        .unwrap();

    assert!(cache_file.starts_with("gpu_native_search_compute_120_"));
    assert!(cache_file.ends_with(".ptx"));
    assert!(cache_file.len() > "gpu_native_search_compute_120_.ptx".len());
}

#[test]
fn line_match_materialization_uses_loaded_line_descriptors() {
    let path = PathBuf::from("sample.log");
    let bytes = b"INFO start\nERROR target\n";
    let file = BatchedFile {
        path: path.clone(),
        start: 100,
        end: 100 + bytes.len(),
        line_descriptors: vec![
            LineDescriptor {
                start: 100,
                len: 10,
                line_number: 1,
            },
            LineDescriptor {
                start: 111,
                len: 12,
                line_number: 2,
            },
        ],
    };
    let offsets = vec![PatternMatchPosition {
        byte_offset: 11,
        pattern_id: 0,
    }];
    let patterns = vec!["ERROR".to_string()];

    let matches = line_matches_for_file(&file, bytes, &offsets, &patterns);

    assert_eq!(matches.len(), 1);
    assert_eq!(matches[0].path, path);
    assert_eq!(matches[0].line_number, 2);
    assert_eq!(matches[0].raw, b"ERROR target");
}

#[test]
fn line_match_materialization_preserves_line_numbers_after_blank_lines() {
    let path = PathBuf::from("blank-lines.log");
    let bytes = b"INFO start\n\nERROR target\n";
    let file = BatchedFile {
        path: path.clone(),
        start: 100,
        end: 100 + bytes.len(),
        line_descriptors: vec![
            LineDescriptor {
                start: 100,
                len: 10,
                line_number: 1,
            },
            LineDescriptor {
                start: 112,
                len: 12,
                line_number: 3,
            },
        ],
    };
    let offsets = vec![PatternMatchPosition {
        byte_offset: 12,
        pattern_id: 0,
    }];
    let patterns = vec!["ERROR".to_string()];

    let matches = line_matches_for_file(&file, bytes, &offsets, &patterns);

    assert_eq!(matches.len(), 1);
    assert_eq!(matches[0].path, path);
    assert_eq!(matches[0].line_number, 3);
    assert_eq!(matches[0].raw, b"ERROR target");
}

// --- Task #273: GpuNativeSearchMatch.raw byte-fidelity (mirrors task #266/#746's fix in
// native_search.rs's NativeSearchMatch::raw / strip_native_line_terminator) ----------------
//
// Before this fix, both `line_matches_for_file` and `line_matches_for_file_by_scanning` built
// `GpuNativeSearchMatch.text: String` via
// `String::from_utf8_lossy(line_bytes).trim_end_matches('\r').to_string()`, which (a) silently
// replaced any genuinely invalid-UTF-8 byte with U+FFFD, and (b) unconditionally stripped a
// trailing `\r` even though `line_bytes` never includes the line's `\n` terminator (both
// producers slice up to but excluding the `\n`), so a trailing `\r` is always genuine CRLF
// content, never a terminator artifact to strip. Each test below asserts the specific byte
// sequence that only the buggy `from_utf8_lossy`/`trim_end_matches('\r')` pipeline would
// corrupt -- reintroducing that pipeline on `raw` (e.g.
// `raw: String::from_utf8_lossy(line_bytes).trim_end_matches('\r').to_string().into_bytes()`)
// fails every test in this group.

#[test]
fn line_match_materialization_preserves_invalid_utf8_bytes_losslessly() {
    // 'E', 0xFF (not a valid UTF-8 lead/continuation byte on its own), 'R', 'O', 'R'.
    let mut bytes = b"INFO start\n".to_vec();
    let invalid_line: &[u8] = &[b'E', 0xFF, b'R', b'O', b'R'];
    bytes.extend_from_slice(invalid_line);
    bytes.push(b'\n');

    let path = PathBuf::from("invalid-utf8.log");
    let file = BatchedFile {
        path: path.clone(),
        start: 100,
        end: 100 + bytes.len(),
        line_descriptors: vec![
            LineDescriptor {
                start: 100,
                len: 10,
                line_number: 1,
            },
            LineDescriptor {
                start: 111,
                len: invalid_line.len() as u32,
                line_number: 2,
            },
        ],
    };
    let offsets = vec![PatternMatchPosition {
        byte_offset: 11,
        pattern_id: 0,
    }];
    let patterns = vec!["ERROR".to_string()];

    let matches = line_matches_for_file(&file, &bytes, &offsets, &patterns);

    assert_eq!(matches.len(), 1);
    // The old `String::from_utf8_lossy` path would have replaced the 0xFF byte with the
    // 3-byte UTF-8 encoding of U+FFFD, changing both the content AND the length. Assert the
    // exact original bytes survive, unchanged.
    assert_eq!(matches[0].raw, invalid_line);
    assert_ne!(
        matches[0].raw,
        String::from_utf8_lossy(invalid_line).as_bytes(),
        "raw must not have been run through a lossy UTF-8 conversion"
    );

    // Full pipeline: `native_json_text_fields` (the shared JSON/NDJSON emitter helper this
    // producer now feeds, task #271) must route genuinely invalid UTF-8 through the
    // `bytes`/base64 side of its `text`-XOR-`bytes` contract, never `text`.
    let (text, encoded_bytes) = crate::native_search::native_json_text_fields(&matches[0].raw);
    assert_eq!(text, None);
    let encoded_bytes = encoded_bytes.expect("invalid UTF-8 must produce a base64 `bytes` field");
    assert_eq!(
        TEST_BASE64_STANDARD
            .decode(encoded_bytes)
            .expect("base64 payload must decode"),
        invalid_line
    );
}

#[test]
fn line_match_materialization_preserves_trailing_carriage_return() {
    // A genuine CRLF source line: "ERROR target\r" followed by the `\n` terminator that the
    // line-descriptor slicing already excludes. The pre-fix `.trim_end_matches('\r')` would
    // wrongly eat the trailing `\r`, producing "ERROR target" (12 bytes) instead of the
    // correct 13-byte "ERROR target\r".
    let bytes = b"INFO start\r\nERROR target\r\n";
    let path = PathBuf::from("crlf.log");
    let file = BatchedFile {
        path: path.clone(),
        start: 100,
        end: 100 + bytes.len(),
        line_descriptors: vec![
            LineDescriptor {
                start: 100,
                len: 11, // "INFO start\r"
                line_number: 1,
            },
            LineDescriptor {
                start: 112,
                len: 13, // "ERROR target\r"
                line_number: 2,
            },
        ],
    };
    let offsets = vec![PatternMatchPosition {
        byte_offset: 12,
        pattern_id: 0,
    }];
    let patterns = vec!["ERROR".to_string()];

    let matches = line_matches_for_file(&file, bytes, &offsets, &patterns);

    assert_eq!(matches.len(), 1);
    assert_eq!(matches[0].raw, b"ERROR target\r");
}

#[test]
fn line_match_materialization_by_scanning_preserves_trailing_carriage_return() {
    // Same CRLF regression as above, but through the fallback `line_matches_for_file_by_scanning`
    // path (taken whenever `line_descriptors` is empty) -- the two producers had the identical
    // `.trim_end_matches('\r')` bug independently and both must be fixed.
    let bytes: &[u8] = b"INFO start\r\nERROR target\r\n";
    let path = PathBuf::from("crlf-scanned.log");
    let offsets = vec![PatternMatchPosition {
        byte_offset: 12,
        pattern_id: 0,
    }];
    let patterns = vec!["ERROR".to_string()];

    let matches = line_matches_for_file_by_scanning(&path, bytes, &offsets, &patterns);

    assert_eq!(matches.len(), 1);
    assert_eq!(matches[0].line_number, 2);
    assert_eq!(matches[0].raw, b"ERROR target\r");
}

#[test]
fn line_match_materialization_valid_utf8_round_trips_through_text() {
    // The `text` side of the `native_json_text_fields` contract for a plain valid-UTF-8 line.
    let path = PathBuf::from("sample.log");
    let bytes = b"INFO start\nERROR target\n";
    let file = BatchedFile {
        path: path.clone(),
        start: 100,
        end: 100 + bytes.len(),
        line_descriptors: vec![
            LineDescriptor {
                start: 100,
                len: 10,
                line_number: 1,
            },
            LineDescriptor {
                start: 111,
                len: 12,
                line_number: 2,
            },
        ],
    };
    let offsets = vec![PatternMatchPosition {
        byte_offset: 11,
        pattern_id: 0,
    }];
    let patterns = vec!["ERROR".to_string()];

    let matches = line_matches_for_file(&file, bytes, &offsets, &patterns);

    assert_eq!(matches.len(), 1);
    let (text, encoded_bytes) = crate::native_search::native_json_text_fields(&matches[0].raw);
    assert_eq!(text, Some("ERROR target"));
    assert_eq!(encoded_bytes, None);
}
