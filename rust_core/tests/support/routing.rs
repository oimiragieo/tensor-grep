use std::path::{Path, PathBuf};
use std::process::{Command, Output};

pub(crate) fn combined_output(output: &Output) -> String {
    format!(
        "{}{}",
        String::from_utf8_lossy(&output.stdout),
        String::from_utf8_lossy(&output.stderr)
    )
}

pub(crate) fn tg() -> Command {
    Command::new(env!("CARGO_BIN_EXE_tg"))
}

pub(crate) fn repo_root() -> PathBuf {
    Path::new(env!("CARGO_MANIFEST_DIR"))
        .parent()
        .unwrap()
        .to_path_buf()
}

pub(crate) fn repo_python() -> PathBuf {
    let windows = repo_root().join(".venv").join("Scripts").join("python.exe");
    if windows.exists() {
        return windows;
    }
    repo_root().join(".venv").join("bin").join("python")
}
