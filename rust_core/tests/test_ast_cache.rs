use std::fs;
use std::path::{Path, PathBuf};
use std::process::Command;
use tempfile::tempdir;
use std::thread::sleep;
use std::time::Duration;

fn tg() -> Command {
    Command::new(env!("CARGO_BIN_EXE_tg"))
}

fn repo_root() -> PathBuf {
    Path::new(env!("CARGO_MANIFEST_DIR"))
        .parent()
        .unwrap()
        .to_path_buf()
}

fn repo_python() -> PathBuf {
    let windows = repo_root().join(".venv").join("Scripts").join("python.exe");
    if windows.exists() {
        return windows;
    }

    repo_root().join(".venv").join("bin").join("python")
}

fn setup_mock_project(dir: &Path) {
    fs::write(
        dir.join("sgconfig.yml"),
        "ruleDirs: [rules]\ntestDirs: [tests]\nlanguage: python\n",
    ).unwrap();

    let rules_dir = dir.join("rules");
    fs::create_dir(&rules_dir).unwrap();
    fs::write(
        rules_dir.join("rule1.yml"),
        "id: rule1\nlanguage: python\nrule:\n  pattern: 'x = 1'\n",
    ).unwrap();

    let tests_dir = dir.join("tests");
    fs::create_dir(&tests_dir).unwrap();
    fs::write(
        tests_dir.join("test1.yml"),
        "id: test1\nruleId: rule1\ninvalid: ['x = 1']\n",
    ).unwrap();

    fs::write(
        dir.join("src.py"),
        "x = 1\n",
    ).unwrap();
}

fn run_scan(dir: &Path) -> (i32, String, String) {
    let output = tg()
        .current_dir(dir)
        .arg("scan")
        .env("TG_SIDECAR_PYTHON", repo_python())
        .output()
        .unwrap();

    (
        output.status.code().unwrap_or(-1),
        String::from_utf8_lossy(&output.stdout).to_string(),
        String::from_utf8_lossy(&output.stderr).to_string(),
    )
}

#[test]
fn test_cache_creation_and_reuse() {
    let dir = tempdir().unwrap();
    setup_mock_project(dir.path());

    let cache_file = dir.path().join(".tg_cache").join("ast").join("project_data_v6.json");
    assert!(!cache_file.exists());

    // First scan creates cache. Might change root_dir mtime.
    run_scan(dir.path());
    assert!(cache_file.exists());

    // Second scan: if root_dir mtime changed on first run, this might still rebuild.
    // But after this, it should be stable.
    run_scan(dir.path());
    let mtime1 = fs::metadata(&cache_file).unwrap().modified().unwrap();

    // Third scan MUST reuse cache
    sleep(Duration::from_millis(100));
    let (code, stdout, stderr) = run_scan(dir.path());
    assert_eq!(code, 0, "Scan failed: {}{}", stdout, stderr);
    let mtime2 = fs::metadata(&cache_file).unwrap().modified().unwrap();

    assert_eq!(mtime1, mtime2, "Cache should be reused on subsequent scans");
}

#[test]
fn test_cache_invalidation_on_config_change() {
    let dir = tempdir().unwrap();
    setup_mock_project(dir.path());
    run_scan(dir.path());
    
    let cache_file = dir.path().join(".tg_cache").join("ast").join("project_data_v6.json");
    let mtime1 = fs::metadata(&cache_file).unwrap().modified().unwrap();

    sleep(Duration::from_millis(1100)); // Ensure mtime change is detectable
    fs::write(
        dir.path().join("sgconfig.yml"),
        "ruleDirs: [rules]\nlanguage: rust\n",
    ).unwrap();

    run_scan(dir.path());
    let mtime2 = fs::metadata(&cache_file).unwrap().modified().unwrap();
    assert!(mtime2 > mtime1);
}

#[test]
fn test_cache_invalidation_on_rule_change() {
    let dir = tempdir().unwrap();
    setup_mock_project(dir.path());
    run_scan(dir.path());
    
    let cache_file = dir.path().join(".tg_cache").join("ast").join("project_data_v6.json");
    let mtime1 = fs::metadata(&cache_file).unwrap().modified().unwrap();

    sleep(Duration::from_millis(1100));
    fs::write(
        dir.path().join("rules").join("rule1.yml"),
        "id: rule1\nlanguage: python\nrule:\n  pattern: 'y = 2'\n",
    ).unwrap();

    run_scan(dir.path());
    let mtime2 = fs::metadata(&cache_file).unwrap().modified().unwrap();
    assert!(mtime2 > mtime1);
}

#[test]
fn test_cache_invalidation_on_test_change() {
    let dir = tempdir().unwrap();
    setup_mock_project(dir.path());
    run_scan(dir.path());
    
    let cache_file = dir.path().join(".tg_cache").join("ast").join("project_data_v6.json");
    let mtime1 = fs::metadata(&cache_file).unwrap().modified().unwrap();

    sleep(Duration::from_millis(1100));
    fs::write(
        dir.path().join("tests").join("test1.yml"),
        "id: test1\nruleId: rule1\ninvalid: ['x = 2']\n",
    ).unwrap();

    run_scan(dir.path());
    let mtime2 = fs::metadata(&cache_file).unwrap().modified().unwrap();
    
    assert!(mtime2 > mtime1, "Cache should be invalidated when a test file changes");
}

#[test]
fn test_cache_invalidation_on_new_file() {
    let dir = tempdir().unwrap();
    setup_mock_project(dir.path());
    run_scan(dir.path());
    
    let cache_file = dir.path().join(".tg_cache").join("ast").join("project_data_v6.json");
    let mtime1 = fs::metadata(&cache_file).unwrap().modified().unwrap();

    sleep(Duration::from_millis(1100));
    fs::write(dir.path().join("new_file.py"), "z = 3\n").unwrap();

    run_scan(dir.path());
    let mtime2 = fs::metadata(&cache_file).unwrap().modified().unwrap();
    
    // Adding a file to root_dir changes root_dir's mtime, which is in tree_dirs
    assert!(mtime2 > mtime1, "Cache should be invalidated when a new file is added to a tracked directory");
}
