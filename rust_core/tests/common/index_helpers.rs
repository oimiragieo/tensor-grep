#![allow(unused_imports, dead_code)]

use std::fs;
use std::path::Path;
use std::process::Command;

use serde_json::Value;
use tempfile::tempdir;
use tensor_grep_rs::index::TrigramIndex;

pub fn tg() -> Command {
    Command::new(env!("CARGO_BIN_EXE_tg"))
}

pub fn write_corpus(dir: &std::path::Path) {
    fs::write(
        dir.join("a.txt"),
        "hello world\nfoo bar baz\ngoodbye world\n",
    )
    .unwrap();
    fs::write(dir.join("b.txt"), "nothing here\nhello again friend\nend\n").unwrap();
    fs::write(
        dir.join("c.log"),
        "error: something failed\nok\nerror: again\n",
    )
    .unwrap();
}

/// `TrigramIndex`'s file walker (`collect_file_entries` / `staleness_reason`) does not set
/// `.require_git(false)`, so it inherits the `ignore` crate's default of only honoring
/// `.gitignore` inside a directory the crate recognizes as an actual git repository. A bare
/// `tempdir()` has no `.git`, so `.gitignore` is silently a no-op there regardless of
/// `--no-ignore` -- returns `false` (and the caller should skip the test) when `git` itself
/// isn't on PATH, mirroring the existing precedent in test_ast_rewrite.rs.
pub fn init_git_repo(dir: &std::path::Path) -> bool {
    if Command::new("git").arg("--version").output().is_err() {
        return false;
    }
    let status = Command::new("git")
        .arg("init")
        .current_dir(dir)
        .output()
        .unwrap();
    status.status.success()
}

pub fn write_regex_prefilter_corpus(dir: &std::path::Path) {
    fs::write(
        dir.join("alternation.txt"),
        "foo branch\nbar branch\nbaz branch\n",
    )
    .unwrap();
    fs::write(
        dir.join("classes.txt"),
        "adef branch\nbdef branch\ncdef branch\ndeaf branch\ndebf branch\ndecf branch\nzzzdef noise\n",
    )
    .unwrap();
    fs::write(
        dir.join("unicode.txt"),
        "東京 terminal\n大阪 station\n京都 garden\n",
    )
    .unwrap();
}

pub fn decode_hex_trigram(key: &str) -> [u8; 3] {
    let b = |o| u8::from_str_radix(&key[o..o + 2], 16).unwrap();
    [b(0), b(2), b(4)]
}

pub fn write_legacy_v1_index(dir: &Path) {
    let index = TrigramIndex::build(dir).unwrap();
    let json_path = dir.join("legacy-index.json");
    index.save_json(&json_path).unwrap();

    let payload: Value = serde_json::from_slice(&fs::read(&json_path).unwrap()).unwrap();
    fs::remove_file(&json_path).unwrap();

    let mut buf = Vec::new();
    buf.extend_from_slice(b"TGI\x00");
    buf.push(1);
    let root_bytes = dir.to_string_lossy().as_bytes().to_vec();
    buf.extend_from_slice(&(root_bytes.len() as u32).to_le_bytes());
    buf.extend_from_slice(&root_bytes);
    let files = payload["files"].as_array().unwrap();
    buf.extend_from_slice(&(files.len() as u32).to_le_bytes());
    for entry in files {
        let path = entry["path"].as_str().unwrap().as_bytes().to_vec();
        buf.extend_from_slice(&(path.len() as u32).to_le_bytes());
        buf.extend_from_slice(&path);
        buf.extend_from_slice(&(entry["mtime_ns"].as_u64().unwrap() as u128).to_le_bytes());
        buf.extend_from_slice(&entry["size"].as_u64().unwrap().to_le_bytes());
    }
    let postings = payload["postings"].as_object().unwrap();
    buf.extend_from_slice(&(postings.len() as u32).to_le_bytes());
    for (key, value) in postings {
        buf.extend_from_slice(&decode_hex_trigram(key));
        let entries = value.as_array().unwrap();
        buf.extend_from_slice(&(entries.len() as u32).to_le_bytes());
        for entry in entries {
            buf.extend_from_slice(&(entry["file_id"].as_u64().unwrap() as u32).to_le_bytes());
            buf.extend_from_slice(&(entry["line"].as_u64().unwrap() as u32).to_le_bytes());
        }
    }

    fs::write(dir.join(".tg_index"), buf).unwrap();
}

pub fn match_tuples(payload: &Value) -> Vec<(String, u64, String)> {
    payload["matches"]
        .as_array()
        .unwrap()
        .iter()
        .map(|entry| {
            (
                entry["file"].as_str().unwrap().to_string(),
                entry["line"].as_u64().unwrap(),
                entry["text"].as_str().unwrap().to_string(),
            )
        })
        .collect()
}

/// Audit fix #1 must-fix : `tg search --index -c` (and warm-index `-c`)
/// now emit per-file `path:count` output -- one `<path>:<count>` line per MATCHED file, matching
/// `rg -c` and the native aggregate emitter (`emit_count_search_matches`) -- instead of the old
/// bare aggregate total. This parses that shape into a basename->count map so the assertions are
/// path-format-agnostic. Splits on the LAST `:` because a Windows path itself contains one
/// (`C:/...`). Returns an empty map for empty stdout (the no-match / zero-file case).
pub fn parse_per_file_counts(stdout: &[u8]) -> std::collections::BTreeMap<String, usize> {
    let text = String::from_utf8_lossy(stdout);
    let mut counts = std::collections::BTreeMap::new();
    for line in text.lines().filter(|line| !line.is_empty()) {
        let (path, count) = line
            .rsplit_once(':')
            .unwrap_or_else(|| panic!("per-file count line must be `path:count`, got {line:?}"));
        let base = Path::new(path)
            .file_name()
            .map(|name| name.to_string_lossy().into_owned())
            .unwrap_or_else(|| path.to_string());
        let count = count
            .trim()
            .parse::<usize>()
            .unwrap_or_else(|_| panic!("count must be a number, got {count:?}"));
        counts.insert(base, count);
    }
    counts
}

/// The per-file counts for the `hello` query over `write_corpus`: a.txt and b.txt each have one
/// `hello` line; c.log has none (omitted, matching `rg -c`). Named so the many count tests share
/// one source of truth for the expected shape.
pub fn hello_per_file_counts() -> std::collections::BTreeMap<String, usize> {
    std::collections::BTreeMap::from([("a.txt".to_string(), 1), ("b.txt".to_string(), 1)])
}

pub fn resolved_index_path(dir: &Path) -> std::path::PathBuf {
    tensor_grep_rs::index_cache::resolve_index_paths(dir.to_str().unwrap()).write_path
}
