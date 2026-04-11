use std::fs;
use std::path::Path;
use std::process::{Command, Child};
use std::thread::sleep;
use std::time::Duration;
use tempfile::tempdir;

fn tg() -> Command {
    Command::new(env!("CARGO_BIN_EXE_tg"))
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

struct WorkerHandle {
    child: Child,
}

impl Drop for WorkerHandle {
    fn drop(&mut self) {
        let _ = self.child.kill();
    }
}

fn start_worker(dir: &Path, port: u16) -> WorkerHandle {
    let child = tg()
        .current_dir(dir)
        .arg("worker")
        .arg("--port")
        .arg(port.to_string())
        .spawn()
        .expect("failed to spawn worker");
    
    // Wait for worker to start and port file to appear
    let port_file = dir.join(".tg_cache").join("ast").join("worker_port.txt");
    for _ in 0..50 {
        if port_file.exists() {
            break;
        }
        sleep(Duration::from_millis(100));
    }

    WorkerHandle { child }
}

#[test]
fn test_resident_worker_scan_and_test() {
    let dir = tempdir().unwrap();
    setup_mock_project(dir.path());
    let _worker = start_worker(dir.path(), 12345);

    // Run scan via resident worker
    let output = tg()
        .current_dir(dir.path())
        .arg("scan")
        .env("TG_RESIDENT_AST", "1")
        .output()
        .unwrap();
    
    let stdout = String::from_utf8_lossy(&output.stdout);
    assert!(stdout.contains("[scan] rule=rule1 lang=python matches=1 files=1"));
    assert!(stdout.contains("Scan completed"));

    // Run test via resident worker
    let output = tg()
        .current_dir(dir.path())
        .arg("test")
        .env("TG_RESIDENT_AST", "1")
        .output()
        .unwrap();
    
    let stdout = String::from_utf8_lossy(&output.stdout);
    assert!(stdout.contains("All tests passed. cases=1"));
}

#[test]
fn test_resident_worker_invalidation() {
    let dir = tempdir().unwrap();
    setup_mock_project(dir.path());
    let _worker = start_worker(dir.path(), 12346);

    // Warm up
    tg().current_dir(dir.path()).arg("scan").env("TG_RESIDENT_AST", "1").output().unwrap();

    // Modify rule
    sleep(Duration::from_millis(1100));
    fs::write(
        dir.path().join("rules").join("rule1.yml"),
        "id: rule1\nlanguage: python\nrule:\n  pattern: 'y = 2'\n",
    ).unwrap();

    // Run scan, should see new pattern
    let output = tg()
        .current_dir(dir.path())
        .arg("scan")
        .env("TG_RESIDENT_AST", "1")
        .output()
        .unwrap();
    
    let stdout = String::from_utf8_lossy(&output.stdout);
    // Since we changed pattern to 'y = 2' and src.py has 'x = 1', it should be 0 matches
    assert!(stdout.contains("matches=0"));
}

#[test]
fn test_resident_worker_stop() {
    let dir = tempdir().unwrap();
    setup_mock_project(dir.path());
    let mut worker = start_worker(dir.path(), 12347);

    let output = tg()
        .current_dir(dir.path())
        .arg("worker")
        .arg("--stop")
        .output()
        .unwrap();
    
    assert!(output.status.success());
    assert!(String::from_utf8_lossy(&output.stdout).contains("Stopped resident worker"));

    // Check if process exited
    sleep(Duration::from_millis(500));
    assert!(worker.child.try_wait().unwrap().is_some());
}

#[test]
fn test_resident_worker_duplicate_start() {
    let dir = tempdir().unwrap();
    setup_mock_project(dir.path());
    
    // Start first worker
    let _worker1 = start_worker(dir.path(), 12350);
    
    // Try to start second worker on different port
    let output = tg()
        .current_dir(dir.path())
        .arg("worker")
        .arg("--port")
        .arg("12351")
        .output()
        .unwrap();
    
    assert!(!output.status.success());
    let stderr = String::from_utf8_lossy(&output.stderr);
    assert!(stderr.contains("A resident AST worker is already running"));
}
