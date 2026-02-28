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
