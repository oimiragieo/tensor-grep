use std::fs::File;
use std::io::{BufWriter, Write};
use tempfile::tempdir;
use tensor_grep_rs::backend_cpu::CpuBackend;

#[test]
fn test_rust_cpu_backend_fixed_string_count() {
    let dir = tempdir().unwrap();
    let file_path = dir.path().join("test.log");
    let mut file = File::create(&file_path).unwrap();
    writeln!(file, "INFO ok\nERROR failed\nDEBUG trace\nERROR timeout").unwrap();

    let backend = CpuBackend::new();
    let count = backend
        .count_matches("ERROR", file_path.to_str().unwrap(), false, true, false)
        .unwrap();
    assert_eq!(count, 2);
}

#[test]
fn test_rust_cpu_backend_regex_count() {
    let dir = tempdir().unwrap();
    let file_path = dir.path().join("test.log");
    let mut file = File::create(&file_path).unwrap();
    writeln!(file, "INFO ok\nERROR failed\nDEBUG trace\nCRITICAL timeout").unwrap();

    let backend = CpuBackend::new();
    let count = backend
        .count_matches(
            "ERROR|CRITICAL",
            file_path.to_str().unwrap(),
            false,
            false,
            false,
        )
        .unwrap();
    assert_eq!(count, 2);
}

#[test]
fn test_rust_cpu_backend_invert_count() {
    let dir = tempdir().unwrap();
    let file_path = dir.path().join("test.log");
    let mut file = File::create(&file_path).unwrap();
    writeln!(file, "INFO ok\nERROR failed\nDEBUG trace\nCRITICAL timeout").unwrap();

    let backend = CpuBackend::new();
    // Invert search: count everything EXCEPT 'INFO'
    let count = backend
        .count_matches("INFO", file_path.to_str().unwrap(), false, true, true)
        .unwrap();
    // 3 lines (ERROR, DEBUG, CRITICAL) + trailing empty line if memmap reads it as such.
    // Actually, writeln! puts \n so there are exactly 4 lines in the split logic.
    assert_eq!(count, 3);
}

#[test]
fn test_rust_cpu_backend_invert_includes_blank_lines() {
    // grep -v parity: a blank line does NOT match a non-empty pattern, so inverted
    // search AND count must include it. Regression for the `!is_empty()` guards that
    // silently dropped blank lines from -v output and -c -v counts (audit MED).
    let dir = tempdir().unwrap();
    let file_path = dir.path().join("blanks.txt");
    let mut file = File::create(&file_path).unwrap();
    // "alpha\n\nbeta\n" -> lines: (1)"alpha" (2)"" (3)"beta"
    write!(file, "alpha\n\nbeta\n").unwrap();

    let backend = CpuBackend::new();

    let results = backend
        .search("alpha", file_path.to_str().unwrap(), false, true, true)
        .unwrap();
    // Inverted on "alpha": line 1 excluded; blank line 2 and line 3 included.
    assert_eq!(results, vec![(2, String::new()), (3, "beta".to_string())]);

    // count -v must include the blank line too (count routes through count_file_*).
    let count_v = backend
        .count_matches("alpha", file_path.to_str().unwrap(), false, true, true)
        .unwrap();
    assert_eq!(count_v, 2);

    // Non-inverted count is unchanged: only "alpha" matches.
    let count_pos = backend
        .count_matches("alpha", file_path.to_str().unwrap(), false, true, false)
        .unwrap();
    assert_eq!(count_pos, 1);
}

#[test]
fn test_rust_cpu_backend_large_file_engages_intra_file_parallel_search() {
    // Public-API, real-file (not in-memory) proof that `CpuBackend::search`'s intra-file
    // parallel chunking (backend_cpu.rs::search_contents_memmem_maybe_parallel, gated on
    // LARGE_FILE_PARALLEL_THRESHOLD_BYTES = 50 MiB) produces exactly the matches a serial scan
    // would: needles placed at the very first line, the very last line, and at the file's
    // quarter/half/three-quarter marks (spread across whatever chunk boundaries this machine's
    // core count produces), with nothing duplicated or dropped at a seam.
    const LINE_BYTES: usize = 1024;
    const TOTAL_LINES: usize = 56_000; // 56_000 * 1024 bytes ~= 54.7MB, over the 50MB threshold
    const NEEDLE: &str = "NEEDLE";

    let dir = tempdir().unwrap();
    let file_path = dir.path().join("large.log");
    let file = File::create(&file_path).unwrap();
    let mut writer = BufWriter::new(file);

    let needle_lines: Vec<usize> = vec![
        1,
        TOTAL_LINES / 4,
        TOTAL_LINES / 2,
        (TOTAL_LINES * 3) / 4,
        TOTAL_LINES,
    ];

    for line_number in 1..=TOTAL_LINES {
        let mut line = if needle_lines.contains(&line_number) {
            format!("L{line_number:06} {NEEDLE}")
        } else {
            format!("L{line_number:06} filler")
        };
        assert!(line.len() < LINE_BYTES);
        line.push_str(&"x".repeat(LINE_BYTES - line.len() - 1));
        line.push('\n');
        writer.write_all(line.as_bytes()).unwrap();
    }
    writer.flush().unwrap();

    let file_len = std::fs::metadata(&file_path).unwrap().len();
    assert_eq!(file_len, (LINE_BYTES * TOTAL_LINES) as u64);
    assert!(
        file_len >= 50 * 1024 * 1024,
        "fixture must actually cross the parallel threshold: {file_len} bytes"
    );

    let backend = CpuBackend::new();
    let results = backend
        .search(NEEDLE, file_path.to_str().unwrap(), false, true, false)
        .unwrap();

    assert_eq!(
        results.iter().map(|(line, _)| *line).collect::<Vec<_>>(),
        needle_lines,
        "matched line numbers must be exactly the needle lines, in ascending order, with no \
         duplicate or dropped match at a chunk seam"
    );
    for (_, text) in &results {
        assert!(text.contains(NEEDLE), "unexpected match text: {text}");
    }
}

macro_rules! tg066_missing_root_test {
    ($name:ident, $method:ident, $pattern:expr, $ignore_case:expr, $fixed:expr) => {
        #[test]
        fn $name() {
            let dir = tempdir().unwrap();
            let missing = dir.path().join("missing");
            let error = CpuBackend::new()
                .$method(
                    $pattern,
                    missing.to_str().unwrap(),
                    $ignore_case,
                    $fixed,
                    false,
                )
                .unwrap_err();
            let io = error
                .downcast_ref::<std::io::Error>()
                .expect("expected I/O error");
            assert_eq!(io.kind(), std::io::ErrorKind::NotFound);
        }
    };
}

tg066_missing_root_test!(tg066_count_literal, count_matches, "needle", false, true);
tg066_missing_root_test!(tg066_count_regex, count_matches, "needle", false, false);
tg066_missing_root_test!(
    tg066_count_literal_icase,
    count_matches,
    "needle",
    true,
    true
);
tg066_missing_root_test!(
    tg066_count_regex_icase,
    count_matches,
    "needle",
    true,
    false
);
tg066_missing_root_test!(tg066_search_literal, search, "needle", false, true);
tg066_missing_root_test!(tg066_search_regex, search, "needle", false, false);
tg066_missing_root_test!(tg066_search_literal_icase, search, "needle", true, true);
tg066_missing_root_test!(tg066_search_regex_icase, search, "needle", true, false);
tg066_missing_root_test!(
    tg066_paths_literal,
    search_with_paths,
    "needle",
    false,
    true
);
tg066_missing_root_test!(tg066_paths_regex, search_with_paths, "needle", false, false);
tg066_missing_root_test!(
    tg066_paths_literal_icase,
    search_with_paths,
    "needle",
    true,
    true
);
tg066_missing_root_test!(
    tg066_paths_regex_icase,
    search_with_paths,
    "needle",
    true,
    false
);
tg066_missing_root_test!(
    tg066_paths_literal_paren,
    search_with_paths,
    "(",
    true,
    true
);
tg066_missing_root_test!(tg066_count_literal_paren, count_matches, "(", true, true);
tg066_missing_root_test!(tg066_search_literal_paren, search, "(", true, true);

#[test]
fn tg066_invalid_regex_precedes_missing_root() {
    let dir = tempdir().unwrap();
    let missing = dir.path().join("missing");
    let path = missing.to_str().unwrap();
    let backend = CpuBackend::new();
    let errors = [
        backend
            .count_matches("(", path, false, false, false)
            .unwrap_err(),
        backend.search("(", path, false, false, false).unwrap_err(),
        backend
            .search_with_paths("(", path, false, false, false)
            .unwrap_err(),
    ];
    for error in errors {
        assert!(error.downcast_ref::<regex::Error>().is_some(), "{error:?}");
    }
}

#[test]
fn tg066_existing_empty_inputs_are_empty() {
    let dir = tempdir().unwrap();
    let empty_file = dir.path().join("empty.txt");
    let empty_dir = dir.path().join("empty-dir");
    std::fs::write(&empty_file, "").unwrap();
    std::fs::create_dir(&empty_dir).unwrap();
    let backend = CpuBackend::new();
    for root in [&empty_file, &empty_dir] {
        for (ignore_case, fixed) in [(false, true), (false, false), (true, true), (true, false)] {
            let path = root.to_str().unwrap();
            assert_eq!(
                backend
                    .count_matches("needle", path, ignore_case, fixed, false)
                    .unwrap(),
                0
            );
            assert!(backend
                .search("needle", path, ignore_case, fixed, false)
                .unwrap()
                .is_empty());
            assert!(backend
                .search_with_paths("needle", path, ignore_case, fixed, false)
                .unwrap()
                .is_empty());
        }
    }
}

#[test]
fn tg066_existing_file_and_directory_keep_matches() {
    let dir = tempdir().unwrap();
    let data_dir = dir.path().join("data");
    std::fs::create_dir(&data_dir).unwrap();
    let data_file = data_dir.join("data.txt");
    std::fs::write(&data_file, "Needle\nother\nneedle (\n").unwrap();
    let backend = CpuBackend::new();
    let cases: [(&str, bool, bool, bool, &[usize]); 6] = [
        ("needle", false, true, false, &[3]),
        ("needle", false, false, false, &[3]),
        ("needle", true, true, false, &[1, 3]),
        ("needle", true, false, false, &[1, 3]),
        ("(", false, true, false, &[3]),
        ("needle", false, true, true, &[1, 2]),
    ];
    for root in [&data_file, &data_dir] {
        for (pattern, ignore_case, fixed, invert, expected) in cases {
            let path = root.to_str().unwrap();
            let count = backend
                .count_matches(pattern, path, ignore_case, fixed, invert)
                .unwrap();
            let search = backend
                .search(pattern, path, ignore_case, fixed, invert)
                .unwrap();
            let with_paths = backend
                .search_with_paths(pattern, path, ignore_case, fixed, invert)
                .unwrap();
            assert_eq!(count, expected.len());
            assert_eq!(
                search
                    .iter()
                    .map(|(line, _)| *line)
                    .collect::<Vec<_>>()
                    .as_slice(),
                expected
            );
            assert_eq!(
                with_paths
                    .iter()
                    .map(|m| m.line)
                    .collect::<Vec<_>>()
                    .as_slice(),
                expected
            );
            assert!(with_paths.iter().all(|m| m.file == data_file));
        }
    }
}

#[cfg(unix)]
#[test]
fn tg066_existing_symlink_roots_follow_targets() {
    let dir = tempdir().unwrap();
    let data_dir = dir.path().join("data");
    std::fs::create_dir(&data_dir).unwrap();
    let data_file = data_dir.join("data.txt");
    std::fs::write(&data_file, "needle\n").unwrap();
    let file_link = dir.path().join("file-link");
    let dir_link = dir.path().join("dir-link");
    std::os::unix::fs::symlink(&data_file, &file_link).unwrap();
    std::os::unix::fs::symlink(&data_dir, &dir_link).unwrap();
    let backend = CpuBackend::new();
    for root in [&file_link, &dir_link] {
        let path = root.to_str().unwrap();
        assert_eq!(
            backend
                .count_matches("needle", path, false, true, false)
                .unwrap(),
            1
        );
        assert_eq!(
            backend
                .search("needle", path, false, true, false)
                .unwrap()
                .len(),
            1
        );
        assert_eq!(
            backend
                .search_with_paths("needle", path, false, true, false)
                .unwrap()
                .len(),
            1
        );
    }
}
