use std::fs;
use std::process::Command;

use tempfile::tempdir;

fn write_log_file() -> (tempfile::TempDir, std::path::PathBuf) {
    let dir = tempdir().unwrap();
    let file_path = dir.path().join("sample.log");
    fs::write(
        &file_path,
        "INFO ok\nERROR failed\nDEBUG trace\nERROR timeout\n",
    )
    .unwrap();
    (dir, file_path)
}

fn assert_success(output: &std::process::Output) {
    assert!(
        output.status.success(),
        "status={:?}\nstdout={}\nstderr={}",
        output.status.code(),
        String::from_utf8_lossy(&output.stdout),
        String::from_utf8_lossy(&output.stderr)
    );
}

#[test]
fn test_plain_text_search_succeeds_with_invalid_pythonhome() {
    let (_dir, file_path) = write_log_file();
    let bogus_python_home = tempdir().unwrap();

    let output = Command::new(env!("CARGO_BIN_EXE_tg"))
        .arg("ERROR")
        .arg(&file_path)
        .env("PYTHONHOME", bogus_python_home.path())
        .output()
        .unwrap();

    assert_success(&output);
    assert_eq!(
        String::from_utf8_lossy(&output.stdout),
        "2:ERROR failed\n4:ERROR timeout\n"
    );
}

#[test]
fn test_plain_text_count_succeeds_with_invalid_pythonhome() {
    let (_dir, file_path) = write_log_file();
    let bogus_python_home = tempdir().unwrap();

    let output = Command::new(env!("CARGO_BIN_EXE_tg"))
        .arg("-c")
        .arg("ERROR")
        .arg(&file_path)
        .env("PYTHONHOME", bogus_python_home.path())
        .output()
        .unwrap();

    assert_success(&output);
    assert_eq!(String::from_utf8_lossy(&output.stdout), "2\n");
}

#[test]
fn test_plain_text_replace_succeeds_with_invalid_pythonhome() {
    let (_dir, file_path) = write_log_file();
    let bogus_python_home = tempdir().unwrap();

    let output = Command::new(env!("CARGO_BIN_EXE_tg"))
        .arg("ERROR")
        .arg(&file_path)
        .arg("--replace")
        .arg("WARN")
        .env("PYTHONHOME", bogus_python_home.path())
        .output()
        .unwrap();

    assert_success(&output);
    assert_eq!(
        String::from_utf8_lossy(&output.stdout),
        "Replaced matches with 'WARN'\n"
    );
    assert_eq!(
        fs::read_to_string(file_path).unwrap(),
        "INFO ok\nWARN failed\nDEBUG trace\nWARN timeout\n"
    );
}
