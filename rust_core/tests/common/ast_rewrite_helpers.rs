#![allow(unused_imports, dead_code)]

use std::fs;
use std::path::PathBuf;
use std::process::Command;
use std::time::{Duration, UNIX_EPOCH};

use serde_json::Value;
use sha2::{Digest, Sha256};
use tempfile::{tempdir, TempDir};
use tensor_grep_rs::backend_ast::{AstBackend, BatchRewriteRule};

pub fn write_source_file(extension: &str, content: &str) -> (TempDir, PathBuf) {
    let dir = tempdir().unwrap();
    let file_path = dir.path().join(format!("fixture.{extension}"));
    fs::write(&file_path, content).unwrap();
    (dir, file_path)
}

pub fn write_source_bytes_file(extension: &str, content: &[u8]) -> (TempDir, PathBuf) {
    let dir = tempdir().unwrap();
    let file_path = dir.path().join(format!("fixture.{extension}"));
    fs::write(&file_path, content).unwrap();
    (dir, file_path)
}

pub fn create_sparse_file(path: &std::path::Path, len: u64) {
    let file = std::fs::File::create(path).unwrap();
    file.set_len(len).unwrap();
}

pub fn canonical_display_path(path: &std::path::Path) -> String {
    let canonical = path.canonicalize().unwrap_or_else(|_| path.to_path_buf());
    let rendered = canonical.to_string_lossy();
    rendered
        .strip_prefix(r"\\?\")
        .unwrap_or(&rendered)
        .to_string()
}

pub fn write_batch_config(dir: &std::path::Path, payload: &Value) -> PathBuf {
    let config_path = dir.join("batch-rewrite.json");
    fs::write(&config_path, serde_json::to_vec_pretty(payload).unwrap()).unwrap();
    config_path
}

pub fn file_mtime_ns(path: &std::path::Path) -> u64 {
    path.metadata()
        .unwrap()
        .modified()
        .unwrap()
        .duration_since(UNIX_EPOCH)
        .unwrap()
        .as_nanos() as u64
}
