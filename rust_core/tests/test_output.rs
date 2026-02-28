use std::fs::File;
use std::io::Write;
use tempfile::tempdir;
use tensor_grep_rs::backend_cpu::CpuBackend;

#[test]
fn test_rust_cpu_backend_search_output() {
    let dir = tempdir().unwrap();
    let file_path = dir.path().join("test_search.log");
    let mut file = File::create(&file_path).unwrap();
    writeln!(file, "INFO ok\nERROR failed\nDEBUG trace\nERROR timeout").unwrap();

    let backend = CpuBackend::new();
    let results = backend
        .search("ERROR", file_path.to_str().unwrap(), false, true, false)
        .unwrap();

    assert_eq!(results.len(), 2);
    assert_eq!(results[0].0, 2);
    assert_eq!(results[0].1, "ERROR failed");
    assert_eq!(results[1].0, 4);
    assert_eq!(results[1].1, "ERROR timeout");
}
