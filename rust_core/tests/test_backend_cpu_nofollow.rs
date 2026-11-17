use std::fs;
use std::path::Path;
use tensor_grep_rs::backend_cpu::CpuBackend;

fn write_fixture(path: &Path, contents: &str) {
    fs::write(path, contents).unwrap();
}

#[test]
fn ordinary_search_and_count_still_work() {
    let dir = tempfile::tempdir().unwrap();
    let target = dir.path().join("plain.txt");
    write_fixture(&target, "alpha\nneedle here\nbeta\n");
    let backend = CpuBackend::new();
    let path = target.to_str().unwrap();

    assert_eq!(
        backend
            .search_with_paths("needle", path, false, true, false)
            .unwrap()
            .len(),
        1
    );
    assert_eq!(
        backend
            .count_matches("needle", path, false, true, false)
            .unwrap(),
        1
    );
    assert_eq!(
        backend
            .search_with_paths(r"need\w+", path, false, false, false)
            .unwrap()
            .len(),
        1
    );
    assert_eq!(
        backend
            .count_matches(r"need\w+", path, false, false, false)
            .unwrap(),
        1
    );
}

#[test]
fn search_and_count_refuse_leaf_symlink_to_outside_sentinel() {
    let dir = tempfile::tempdir().unwrap();
    let outside = tempfile::tempdir().unwrap();
    let target = dir.path().join("selected.txt");
    let sentinel = outside.path().join("outside-sentinel.txt");
    write_fixture(&target, "safe content without the pattern\n");
    write_fixture(&sentinel, "attacker needle\n");
    fs::remove_file(&target).unwrap();

    #[cfg(unix)]
    let linked = std::os::unix::fs::symlink(&sentinel, &target).is_ok();
    #[cfg(windows)]
    let linked = std::os::windows::fs::symlink_file(&sentinel, &target).is_ok();
    #[cfg(not(any(unix, windows)))]
    let linked = false;
    if !linked {
        eprintln!("skipping symlink refusal assertions: links unavailable");
        return;
    }

    let backend = CpuBackend::new();
    let path = target.to_str().unwrap();
    assert!(backend
        .search_with_paths("needle", path, false, true, false)
        .is_err());
    assert!(backend
        .count_matches("needle", path, false, true, false)
        .is_err());
    assert!(backend
        .search_with_paths(r"need\w+", path, false, false, false)
        .is_err());
    assert!(backend
        .count_matches(r"need\w+", path, false, false, false)
        .is_err());
    assert_eq!(fs::read_to_string(sentinel).unwrap(), "attacker needle\n");
}
