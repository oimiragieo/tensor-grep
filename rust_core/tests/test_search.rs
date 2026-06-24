use std::fs::File;
use std::io::Write;
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
