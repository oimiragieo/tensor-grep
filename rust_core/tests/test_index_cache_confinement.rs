use std::fs;
use std::process::Command;

use tempfile::tempdir;

fn tg() -> Command {
    Command::new(env!("CARGO_BIN_EXE_tg"))
}

fn init_git_repo(dir: &std::path::Path) -> bool {
    if Command::new("git").arg("--version").output().is_err() {
        return false;
    }
    let init = Command::new("git")
        .arg("init")
        .current_dir(dir)
        .output()
        .unwrap();
    if !init.status.success() {
        return false;
    }
    let _ = Command::new("git")
        .args(["config", "user.email", "test@example.com"])
        .current_dir(dir)
        .output();
    let _ = Command::new("git")
        .args(["config", "user.name", "Test User"])
        .current_dir(dir)
        .output();
    true
}

#[test]
fn index_cache_confinement_keeps_git_subdirectory_and_status_clean() {
    let dir = tempdir().unwrap();
    let repo = dir.path();
    if !init_git_repo(repo) {
        return;
    }

    let src = repo.join("src");
    fs::create_dir_all(&src).unwrap();
    let sample = src.join("lib.rs");
    fs::write(
        &sample,
        "pub fn needle_token() -> &'static str { \"ok\" }\n",
    )
    .unwrap();

    let add = Command::new("git")
        .args(["add", "src/lib.rs"])
        .current_dir(repo)
        .output()
        .unwrap();
    assert!(add.status.success());
    let commit = Command::new("git")
        .args(["commit", "-m", "init"])
        .current_dir(repo)
        .output()
        .unwrap();
    assert!(commit.status.success());

    let output = tg()
        .args(["search", "--index", "needle_token", "src"])
        .current_dir(repo)
        .output()
        .unwrap();
    assert!(
        output.status.success(),
        "tg search --index failed: {}",
        String::from_utf8_lossy(&output.stderr)
    );
    let stdout = String::from_utf8_lossy(&output.stdout);
    assert!(
        stdout.contains("needle_token"),
        "unexpected stdout: {stdout}"
    );

    assert!(
        !src.join(".tg_index").exists(),
        "must not write .tg_index into git subdirectory"
    );
    assert!(
        !src.join("..tg_index.lock").exists(),
        "must not write ..tg_index.lock into git subdirectory"
    );
    assert!(
        !src.join("..tg_index.lock.os").exists(),
        "must not write ..tg_index.lock.os into git subdirectory"
    );

    let gitignore = repo.join(".tg_cache").join(".gitignore");
    assert_eq!(fs::read(&gitignore).unwrap(), b"*\n");

    let index_v1 = repo.join(".tg_cache").join("index_v1");
    let entries: Vec<_> = fs::read_dir(&index_v1)
        .unwrap()
        .filter_map(Result::ok)
        .collect();
    assert!(
        entries.iter().any(|entry| {
            entry.path().extension().and_then(|ext| ext.to_str()) == Some("tg_index")
        }),
        "expected .tg_index file inside .tg_cache/index_v1"
    );

    let status = Command::new("git")
        .args(["status", "--porcelain"])
        .current_dir(repo)
        .output()
        .unwrap();
    assert!(status.status.success());
    assert!(
        String::from_utf8_lossy(&status.stdout).trim().is_empty(),
        "git status must remain clean, got: {}",
        String::from_utf8_lossy(&status.stdout)
    );
}
