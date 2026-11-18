use std::fs;
use std::path::Path;
use std::sync::{Arc, Mutex};

use tempfile::tempdir;
use tensor_grep_rs::native_search::{
    run_native_fixed_multi_pattern_search, run_native_search, NativeOutputTarget,
    NativeSearchConfig,
};

fn write_fixture(path: &Path, contents: &str) {
    fs::write(path, contents).unwrap();
}

fn buffer_target() -> (NativeOutputTarget, Arc<Mutex<Vec<u8>>>) {
    let buffer = Arc::new(Mutex::new(Vec::new()));
    (NativeOutputTarget::Buffer(Arc::clone(&buffer)), buffer)
}

fn base_config(
    pattern: &str,
    path: &Path,
    output_target: NativeOutputTarget,
) -> NativeSearchConfig {
    NativeSearchConfig {
        pattern: pattern.to_string(),
        paths: vec![path.to_path_buf()],
        output_target,
        ..NativeSearchConfig::default()
    }
}

fn try_leaf_symlink(link: &Path, target: &Path) -> bool {
    #[cfg(unix)]
    {
        std::os::unix::fs::symlink(target, link).is_ok()
    }
    #[cfg(windows)]
    {
        std::os::windows::fs::symlink_file(target, link).is_ok()
    }
    #[cfg(not(any(unix, windows)))]
    {
        let _ = (link, target);
        false
    }
}

fn try_parent_dir_link(link: &Path, target: &Path) -> bool {
    #[cfg(unix)]
    {
        std::os::unix::fs::symlink(target, link).is_ok()
    }
    #[cfg(windows)]
    {
        if std::os::windows::fs::symlink_dir(target, link).is_ok() {
            return true;
        }
        std::process::Command::new("cmd")
            .args(["/C", "mklink", "/J"])
            .arg(link)
            .arg(target)
            .output()
            .is_ok_and(|output| output.status.success())
    }
    #[cfg(not(any(unix, windows)))]
    {
        let _ = (link, target);
        false
    }
}

#[test]
fn ordinary_native_search_and_count_still_work() {
    let dir = tempdir().unwrap();
    let target = dir.path().join("plain.txt");
    fs::create_dir(dir.path().join("nested")).unwrap();
    write_fixture(&target, "alpha\nneedle here\nbeta\n");

    let (output_target, buffer) = buffer_target();
    let stats = run_native_search(base_config("needle", &target, output_target)).unwrap();
    assert_eq!(stats.total_matches, 1);
    assert!(String::from_utf8(buffer.lock().unwrap().clone())
        .unwrap()
        .contains("needle here"));

    let (count_target, count_buffer) = buffer_target();
    let mut count_config = base_config("needle", &target, count_target);
    count_config.count = true;
    let count_stats = run_native_search(count_config).unwrap();
    assert_eq!(count_stats.total_matches, 1);
    assert!(String::from_utf8(count_buffer.lock().unwrap().clone())
        .unwrap()
        .contains('1'));

    let parent_component_path = dir.path().join("nested").join("..").join("plain.txt");
    assert_eq!(
        run_native_search(base_config(
            "needle",
            &parent_component_path,
            NativeOutputTarget::Buffer(Arc::new(Mutex::new(Vec::new())))
        ))
        .unwrap()
        .total_matches,
        1
    );
}

#[test]
fn native_search_follows_an_explicit_leaf_symlink_root() {
    let dir = tempdir().unwrap();
    let outside = tempdir().unwrap();
    let selected = dir.path().join("selected.txt");
    let sentinel = outside.path().join("outside-sentinel.txt");
    write_fixture(&selected, "safe content without the pattern\n");
    write_fixture(&sentinel, "attacker needle\n");
    fs::remove_file(&selected).unwrap();
    if !try_leaf_symlink(&selected, &sentinel) {
        eprintln!("skipping explicit leaf symlink root assertions: links unavailable");
        return;
    }

    let (output_target, buffer) = buffer_target();
    let stats = run_native_search(base_config("needle", &selected, output_target)).unwrap();
    assert_eq!(stats.total_matches, 1);
    assert!(String::from_utf8(buffer.lock().unwrap().clone())
        .unwrap()
        .contains("attacker needle"));
}

#[test]
fn native_search_refuses_parent_link_to_outside_sentinel() {
    let dir = tempdir().unwrap();
    let outside = tempdir().unwrap();
    let outside_tree = outside.path().join("outside-tree");
    fs::create_dir(&outside_tree).unwrap();
    let sentinel = outside_tree.join("selected.txt");
    write_fixture(&sentinel, "attacker needle\n");

    let linked_parent = dir.path().join("linked-parent");
    if !try_parent_dir_link(&linked_parent, &outside_tree) {
        eprintln!("skipping parent link refusal assertions: directory links unavailable");
        return;
    }
    let selected = linked_parent.join("selected.txt");

    let (output_target, _) = buffer_target();
    assert!(run_native_search(base_config("needle", &selected, output_target)).is_err());
    assert_eq!(fs::read_to_string(&sentinel).unwrap(), "attacker needle\n");
}

#[test]
fn fixed_multi_pattern_search_follows_explicit_leaf_and_refuses_parent_links() {
    let dir = tempdir().unwrap();
    let outside = tempdir().unwrap();

    let ordinary = dir.path().join("plain.txt");
    write_fixture(&ordinary, "alpha one\nbeta two\n");
    let ordinary_config = NativeSearchConfig {
        paths: vec![ordinary.clone()],
        fixed_strings: true,
        ..NativeSearchConfig::default()
    };
    let ordinary_matches = run_native_fixed_multi_pattern_search(
        ordinary_config,
        &[String::from("one"), String::from("two")],
    )
    .unwrap()
    .expect("ordinary multi-pattern search remains available");
    assert_eq!(ordinary_matches.len(), 2);

    let leaf = dir.path().join("leaf.txt");
    let leaf_sentinel = outside.path().join("leaf-sentinel.txt");
    write_fixture(&leaf, "safe\n");
    write_fixture(&leaf_sentinel, "attacker one two\n");
    fs::remove_file(&leaf).unwrap();
    if try_leaf_symlink(&leaf, &leaf_sentinel) {
        let leaf_config = NativeSearchConfig {
            paths: vec![leaf],
            fixed_strings: true,
            ..NativeSearchConfig::default()
        };
        let matches = run_native_fixed_multi_pattern_search(
            leaf_config,
            &[String::from("one"), String::from("two")],
        )
        .unwrap()
        .expect("explicit leaf symlink root remains searchable");
        assert_eq!(matches.len(), 2);
        assert_eq!(
            fs::read_to_string(&leaf_sentinel).unwrap(),
            "attacker one two\n"
        );
    } else {
        eprintln!("skipping explicit multi-pattern symlink root: links unavailable");
    }

    let outside_tree = outside.path().join("multi-outside-tree");
    fs::create_dir(&outside_tree).unwrap();
    let parent_sentinel = outside_tree.join("selected.txt");
    write_fixture(&parent_sentinel, "attacker one two\n");
    let linked_parent = dir.path().join("multi-linked-parent");
    if !try_parent_dir_link(&linked_parent, &outside_tree) {
        eprintln!("skipping multi-pattern parent link refusal: directory links unavailable");
        return;
    }
    let parent_selected = linked_parent.join("selected.txt");
    let parent_config = NativeSearchConfig {
        paths: vec![parent_selected],
        fixed_strings: true,
        ..NativeSearchConfig::default()
    };
    assert!(run_native_fixed_multi_pattern_search(
        parent_config,
        &[String::from("one"), String::from("two")],
    )
    .is_err());
    assert_eq!(
        fs::read_to_string(&parent_sentinel).unwrap(),
        "attacker one two\n"
    );
}

#[test]
fn backend_cpu_search_follows_an_explicit_leaf_root() {
    use tensor_grep_rs::backend_cpu::CpuBackend;

    let dir = tempdir().unwrap();
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

    let outside = tempdir().unwrap();
    let sentinel = outside.path().join("outside-sentinel.txt");
    write_fixture(&sentinel, "attacker needle\n");
    let linked = dir.path().join("linked.txt");
    if !try_leaf_symlink(&linked, &sentinel) {
        eprintln!("skipping CpuBackend symlink root: links unavailable");
        return;
    }
    assert_eq!(
        backend
            .search_with_paths("needle", linked.to_str().unwrap(), false, true, false)
            .unwrap()
            .len(),
        1
    );
    assert_eq!(fs::read_to_string(sentinel).unwrap(), "attacker needle\n");
}
