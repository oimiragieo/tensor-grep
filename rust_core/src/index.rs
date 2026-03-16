use anyhow::{Context, Result};
use memmap2::MmapOptions;
use rayon::prelude::*;
use serde::{Deserialize, Serialize};
use std::collections::HashMap;
use std::fs::File;
use std::path::{Path, PathBuf};
use std::time::SystemTime;

const TRIGRAM_LEN: usize = 3;

#[derive(Debug, Clone, Serialize, Deserialize)]
struct FileEntry {
    path: PathBuf,
    mtime_ns: u128,
    size: u64,
}

#[derive(Debug, Clone, Serialize, Deserialize)]
struct PostingEntry {
    file_id: u32,
    line: u32,
}

#[derive(Debug, Clone)]
pub struct TrigramIndex {
    files: Vec<FileEntry>,
    postings: HashMap<[u8; 3], Vec<PostingEntry>>,
}

#[derive(Debug, Clone, Serialize, Deserialize)]
struct SerializableIndex {
    files: Vec<FileEntry>,
    postings: HashMap<String, Vec<PostingEntry>>,
}

impl TrigramIndex {
    fn to_serializable(&self) -> SerializableIndex {
        let postings = self
            .postings
            .iter()
            .map(|(k, v)| {
                let key = format!("{:02x}{:02x}{:02x}", k[0], k[1], k[2]);
                (key, v.clone())
            })
            .collect();
        SerializableIndex {
            files: self.files.clone(),
            postings,
        }
    }

    fn from_serializable(s: SerializableIndex) -> Result<Self> {
        let mut postings = HashMap::new();
        for (key, value) in s.postings {
            if key.len() != 6 {
                anyhow::bail!("invalid trigram key: {key}");
            }
            let bytes = hex_to_trigram(&key)?;
            postings.insert(bytes, value);
        }
        Ok(Self {
            files: s.files,
            postings,
        })
    }
}

const INDEX_MAGIC: &[u8; 4] = b"TGI\x00";
const INDEX_FORMAT_VERSION: u8 = 1;

fn bincode_serialize(index: &TrigramIndex) -> Result<Vec<u8>> {
    let mut buf = Vec::new();
    buf.extend_from_slice(INDEX_MAGIC);
    buf.push(INDEX_FORMAT_VERSION);

    let files_count = index.files.len() as u32;
    buf.extend_from_slice(&files_count.to_le_bytes());
    for entry in &index.files {
        let path_bytes = entry.path.to_string_lossy().as_bytes().to_vec();
        buf.extend_from_slice(&(path_bytes.len() as u32).to_le_bytes());
        buf.extend_from_slice(&path_bytes);
        buf.extend_from_slice(&entry.mtime_ns.to_le_bytes());
        buf.extend_from_slice(&entry.size.to_le_bytes());
    }

    let trigram_count = index.postings.len() as u32;
    buf.extend_from_slice(&trigram_count.to_le_bytes());
    for (trigram, postings) in &index.postings {
        buf.extend_from_slice(trigram);
        buf.extend_from_slice(&(postings.len() as u32).to_le_bytes());
        for p in postings {
            buf.extend_from_slice(&p.file_id.to_le_bytes());
            buf.extend_from_slice(&p.line.to_le_bytes());
        }
    }

    Ok(buf)
}

fn bincode_deserialize(data: &[u8]) -> Result<TrigramIndex> {
    let mut pos = 0;

    if data.len() < 5 || &data[0..4] != INDEX_MAGIC {
        anyhow::bail!("invalid index file magic");
    }
    pos += 4;

    let version = data[pos];
    if version != INDEX_FORMAT_VERSION {
        anyhow::bail!(
            "unsupported index format version {} (expected {})",
            version,
            INDEX_FORMAT_VERSION
        );
    }
    pos += 1;

    let files_count = u32::from_le_bytes(data[pos..pos + 4].try_into()?) as usize;
    pos += 4;

    let mut files = Vec::with_capacity(files_count);
    for _ in 0..files_count {
        let path_len = u32::from_le_bytes(data[pos..pos + 4].try_into()?) as usize;
        pos += 4;
        let path_str = String::from_utf8_lossy(&data[pos..pos + path_len]).to_string();
        pos += path_len;
        let mtime_ns = u128::from_le_bytes(data[pos..pos + 16].try_into()?);
        pos += 16;
        let size = u64::from_le_bytes(data[pos..pos + 8].try_into()?);
        pos += 8;
        files.push(FileEntry {
            path: PathBuf::from(path_str),
            mtime_ns,
            size,
        });
    }

    let trigram_count = u32::from_le_bytes(data[pos..pos + 4].try_into()?) as usize;
    pos += 4;

    let mut postings = HashMap::with_capacity(trigram_count);
    for _ in 0..trigram_count {
        let trigram: [u8; 3] = data[pos..pos + 3].try_into()?;
        pos += 3;
        let posting_count = u32::from_le_bytes(data[pos..pos + 4].try_into()?) as usize;
        pos += 4;
        let mut entries = Vec::with_capacity(posting_count);
        for _ in 0..posting_count {
            let file_id = u32::from_le_bytes(data[pos..pos + 4].try_into()?);
            pos += 4;
            let line = u32::from_le_bytes(data[pos..pos + 4].try_into()?);
            pos += 4;
            entries.push(PostingEntry { file_id, line });
        }
        postings.insert(trigram, entries);
    }

    Ok(TrigramIndex { files, postings })
}

fn hex_to_trigram(hex: &str) -> Result<[u8; 3]> {
    let b = |i: usize| -> Result<u8> {
        u8::from_str_radix(&hex[i..i + 2], 16)
            .map_err(|_| anyhow::anyhow!("invalid hex in trigram key"))
    };
    Ok([b(0)?, b(2)?, b(4)?])
}

#[derive(Debug)]
pub struct IndexQueryResult {
    pub file: PathBuf,
    pub line: usize,
    pub text: String,
}

impl TrigramIndex {
    pub fn build(root: &Path) -> Result<Self> {
        Self::build_with_options(root, false)
    }

    pub fn build_with_options(root: &Path, no_ignore: bool) -> Result<Self> {
        let walker = ignore::WalkBuilder::new(root)
            .hidden(true)
            .git_ignore(!no_ignore)
            .build();

        let paths: Vec<PathBuf> = walker
            .filter_map(|e| e.ok())
            .filter(|e| e.file_type().map_or(false, |ft| ft.is_file()))
            .map(|e| e.into_path())
            .collect();

        let file_entries: Vec<FileEntry> = paths
            .iter()
            .filter_map(|p| {
                let meta = p.metadata().ok()?;
                let mtime_ns = meta
                    .modified()
                    .ok()?
                    .duration_since(SystemTime::UNIX_EPOCH)
                    .ok()?
                    .as_nanos();
                Some(FileEntry {
                    path: p.clone(),
                    mtime_ns,
                    size: meta.len(),
                })
            })
            .collect();

        let per_file: Vec<(u32, Vec<([u8; 3], u32)>)> = file_entries
            .par_iter()
            .enumerate()
            .map(|(file_id, entry)| {
                let trigrams = extract_file_trigrams(&entry.path).unwrap_or_default();
                (file_id as u32, trigrams)
            })
            .collect();

        let mut postings: HashMap<[u8; 3], Vec<PostingEntry>> = HashMap::new();
        for (file_id, file_trigrams) in per_file {
            for (trigram, line) in file_trigrams {
                postings
                    .entry(trigram)
                    .or_default()
                    .push(PostingEntry { file_id, line });
            }
        }

        Ok(Self {
            files: file_entries,
            postings,
        })
    }

    pub fn query_candidates_fixed(&self, pattern: &str, ignore_case: bool) -> Vec<(PathBuf, usize)> {
        let pat = if ignore_case {
            pattern.to_lowercase()
        } else {
            pattern.to_string()
        };
        self.query_with_trigrams(&extract_trigrams(pat.as_bytes()))
    }

    pub fn query_candidates(&self, pattern: &str, ignore_case: bool) -> Vec<(PathBuf, usize)> {
        let literal = extract_longest_literal(pattern);
        let pat = if ignore_case {
            literal.to_lowercase()
        } else {
            literal
        };
        if pat.len() < TRIGRAM_LEN {
            return Vec::new();
        }
        self.query_with_trigrams(&extract_trigrams(pat.as_bytes()))
    }

    fn query_with_trigrams(&self, trigrams: &[[u8; 3]]) -> Vec<(PathBuf, usize)> {
        if trigrams.is_empty() {
            return Vec::new();
        }

        let mut candidate_sets: Vec<&Vec<PostingEntry>> = Vec::new();
        for trigram in trigrams {
            if let Some(postings) = self.postings.get(trigram) {
                candidate_sets.push(postings);
            } else {
                return Vec::new();
            }
        }

        candidate_sets.sort_by_key(|s| s.len());

        let first = candidate_sets[0];
        let mut candidates: Vec<(u32, u32)> = first.iter().map(|p| (p.file_id, p.line)).collect();

        for posting_list in &candidate_sets[1..] {
            let set: std::collections::HashSet<(u32, u32)> =
                posting_list.iter().map(|p| (p.file_id, p.line)).collect();
            candidates.retain(|c| set.contains(c));
            if candidates.is_empty() {
                break;
            }
        }

        candidates.sort();
        candidates.dedup();
        candidates
            .into_iter()
            .filter_map(|(file_id, line)| {
                let entry = self.files.get(file_id as usize)?;
                Some((entry.path.clone(), line as usize))
            })
            .collect()
    }

    pub fn search(
        &self,
        pattern: &str,
        ignore_case: bool,
        fixed_strings: bool,
    ) -> Result<Vec<IndexQueryResult>> {
        let candidates = if fixed_strings {
            self.query_candidates_fixed(pattern, ignore_case)
        } else {
            self.query_candidates(pattern, ignore_case)
        };
        if candidates.is_empty() {
            return Ok(Vec::new());
        }

        let mut by_file: HashMap<&Path, Vec<usize>> = HashMap::new();
        for (file, line) in &candidates {
            by_file.entry(file.as_path()).or_default().push(*line);
        }

        let file_entries: Vec<(&Path, Vec<usize>)> =
            by_file.into_iter().collect();

        let results: Vec<Result<Vec<IndexQueryResult>>> = file_entries
            .par_iter()
            .map(|(file, candidate_lines)| {
                verify_candidates(file, candidate_lines, pattern, ignore_case, fixed_strings)
            })
            .collect();

        let mut all_results = Vec::new();
        for result in results {
            all_results.extend(result?);
        }

        all_results.sort_by(|a, b| a.file.cmp(&b.file).then(a.line.cmp(&b.line)));
        Ok(all_results)
    }

    pub fn is_stale(&self) -> bool {
        self.staleness_reason().is_some()
    }

    pub fn staleness_reason(&self) -> Option<String> {
        let indexed_paths: std::collections::HashSet<&Path> =
            self.files.iter().map(|e| e.path.as_path()).collect();

        for entry in &self.files {
            match entry.path.metadata() {
                Ok(meta) => {
                    let current_mtime = meta
                        .modified()
                        .ok()
                        .and_then(|t| t.duration_since(SystemTime::UNIX_EPOCH).ok())
                        .map(|d| d.as_nanos())
                        .unwrap_or(0);
                    if current_mtime != entry.mtime_ns {
                        return Some(format!("file modified: {}", entry.path.display()));
                    }
                    if meta.len() != entry.size {
                        return Some(format!("file size changed: {}", entry.path.display()));
                    }
                }
                Err(_) => {
                    return Some(format!("file deleted: {}", entry.path.display()));
                }
            }
        }

        if let Some(first) = self.files.first() {
            let root = first
                .path
                .parent()
                .unwrap_or(Path::new("."));
            if root.is_dir() {
                let current_files: Vec<PathBuf> = ignore::WalkBuilder::new(root)
                    .hidden(true)
                    .git_ignore(true)
                    .build()
                    .filter_map(|e| e.ok())
                    .filter(|e| e.file_type().map_or(false, |ft| ft.is_file()))
                    .map(|e| e.into_path())
                    .collect();

                for file in &current_files {
                    if !indexed_paths.contains(file.as_path()) {
                        return Some(format!("new file: {}", file.display()));
                    }
                }
            }
        }

        None
    }

    pub fn save(&self, path: &Path) -> Result<()> {
        let data = bincode_serialize(self)?;
        if let Some(parent) = path.parent() {
            std::fs::create_dir_all(parent)?;
        }
        std::fs::write(path, &data)
            .with_context(|| format!("failed to write index to {}", path.display()))
    }

    pub fn load(path: &Path) -> Result<Self> {
        let data = std::fs::read(path)
            .with_context(|| format!("failed to read index from {}", path.display()))?;
        bincode_deserialize(&data)
    }

    pub fn save_json(&self, path: &Path) -> Result<()> {
        let data = serde_json::to_vec(&self.to_serializable())
            .context("failed to serialize index")?;
        if let Some(parent) = path.parent() {
            std::fs::create_dir_all(parent)?;
        }
        std::fs::write(path, &data)
            .with_context(|| format!("failed to write index to {}", path.display()))
    }

    pub fn load_json(path: &Path) -> Result<Self> {
        let data = std::fs::read(path)
            .with_context(|| format!("failed to read index from {}", path.display()))?;
        let serializable: SerializableIndex =
            serde_json::from_slice(&data).context("failed to deserialize index")?;
        Self::from_serializable(serializable)
    }

    pub fn file_count(&self) -> usize {
        self.files.len()
    }

    pub fn trigram_count(&self) -> usize {
        self.postings.len()
    }

    pub fn total_postings(&self) -> usize {
        self.postings.values().map(|v| v.len()).sum()
    }
}

fn extract_file_trigrams(path: &Path) -> Result<Vec<([u8; 3], u32)>> {
    let file = File::open(path)?;
    let meta = file.metadata()?;
    if meta.len() == 0 || meta.len() > 10 * 1024 * 1024 {
        return Ok(Vec::new());
    }

    let mmap = unsafe { MmapOptions::new().map(&file)? };
    let mut trigrams = Vec::new();
    let mut line_num: u32 = 1;

    for line_bytes in mmap.split(|&b| b == b'\n') {
        let line = if line_bytes.last() == Some(&b'\r') {
            &line_bytes[..line_bytes.len() - 1]
        } else {
            line_bytes
        };

        if line.len() >= TRIGRAM_LEN {
            let lower: Vec<u8> = line.iter().map(|b| b.to_ascii_lowercase()).collect();
            let mut seen = std::collections::HashSet::new();
            for window in lower.windows(TRIGRAM_LEN) {
                let tri: [u8; 3] = [window[0], window[1], window[2]];
                if seen.insert(tri) {
                    trigrams.push((tri, line_num));
                }
            }
        }
        line_num += 1;
    }

    Ok(trigrams)
}

fn extract_longest_literal(pattern: &str) -> String {
    let meta_chars = ['.', '*', '+', '?', '[', ']', '(', ')', '{', '}', '|', '^', '$', '\\'];
    let mut longest = String::new();
    let mut current = String::new();

    for ch in pattern.chars() {
        if meta_chars.contains(&ch) {
            if current.len() > longest.len() {
                longest = current.clone();
            }
            current.clear();
        } else {
            current.push(ch);
        }
    }
    if current.len() > longest.len() {
        longest = current;
    }
    longest
}

fn extract_trigrams(pattern: &[u8]) -> Vec<[u8; 3]> {
    if pattern.len() < TRIGRAM_LEN {
        return Vec::new();
    }
    let lower: Vec<u8> = pattern.iter().map(|b| b.to_ascii_lowercase()).collect();
    let mut trigrams = Vec::new();
    let mut seen = std::collections::HashSet::new();
    for window in lower.windows(TRIGRAM_LEN) {
        let tri: [u8; 3] = [window[0], window[1], window[2]];
        if seen.insert(tri) {
            trigrams.push(tri);
        }
    }
    trigrams
}

fn verify_candidates(
    file: &Path,
    candidate_lines: &[usize],
    pattern: &str,
    ignore_case: bool,
    fixed_strings: bool,
) -> Result<Vec<IndexQueryResult>> {
    let content = std::fs::read_to_string(file)
        .with_context(|| format!("failed to read {}", file.display()))?;

    let lines: Vec<&str> = content.lines().collect();
    let mut results = Vec::new();

    for &line_num in candidate_lines {
        if line_num == 0 || line_num > lines.len() {
            continue;
        }
        let line = lines[line_num - 1];
        let matches = if fixed_strings {
            if ignore_case {
                line.to_lowercase().contains(&pattern.to_lowercase())
            } else {
                line.contains(pattern)
            }
        } else {
            let re = regex::RegexBuilder::new(pattern)
                .case_insensitive(ignore_case)
                .build();
            match re {
                Ok(re) => re.is_match(line),
                Err(_) => false,
            }
        };

        if matches {
            results.push(IndexQueryResult {
                file: file.to_path_buf(),
                line: line_num,
                text: line.to_string(),
            });
        }
    }

    Ok(results)
}

#[cfg(test)]
mod tests {
    use super::*;
    use std::fs;
    use tempfile::tempdir;

    fn write_test_file(dir: &Path, name: &str, content: &str) {
        fs::write(dir.join(name), content).unwrap();
    }

    #[test]
    fn test_build_index_and_search_fixed_string() {
        let dir = tempdir().unwrap();
        write_test_file(dir.path(), "a.txt", "hello world\nfoo bar baz\ngoodbye\n");
        write_test_file(dir.path(), "b.txt", "nothing here\nhello again\n");

        let index = TrigramIndex::build(dir.path()).unwrap();
        assert!(index.file_count() >= 2);
        assert!(index.trigram_count() > 0);

        let results = index.search("hello", false, true).unwrap();
        assert_eq!(results.len(), 2);
        assert!(results.iter().any(|r| r.text.contains("hello world")));
        assert!(results.iter().any(|r| r.text.contains("hello again")));
    }

    #[test]
    fn test_index_case_insensitive_search() {
        let dir = tempdir().unwrap();
        write_test_file(dir.path(), "a.txt", "Hello World\nFOO BAR\n");

        let index = TrigramIndex::build(dir.path()).unwrap();
        let results = index.search("hello", true, true).unwrap();
        assert_eq!(results.len(), 1);
        assert!(results[0].text.contains("Hello World"));
    }

    #[test]
    fn test_index_no_match_returns_empty() {
        let dir = tempdir().unwrap();
        write_test_file(dir.path(), "a.txt", "hello world\n");

        let index = TrigramIndex::build(dir.path()).unwrap();
        let results = index.search("zzzzz", false, true).unwrap();
        assert!(results.is_empty());
    }

    #[test]
    fn test_index_persistence_round_trip() {
        let dir = tempdir().unwrap();
        write_test_file(dir.path(), "a.txt", "hello world\nfoo bar\n");

        let index = TrigramIndex::build(dir.path()).unwrap();
        let index_path = dir.path().join(".tg_index");
        index.save(&index_path).unwrap();

        let loaded = TrigramIndex::load(&index_path).unwrap();
        assert_eq!(loaded.file_count(), index.file_count());
        assert_eq!(loaded.trigram_count(), index.trigram_count());

        let results = loaded.search("hello", false, true).unwrap();
        assert_eq!(results.len(), 1);
    }

    #[test]
    fn test_index_staleness_detection() {
        let dir = tempdir().unwrap();
        write_test_file(dir.path(), "a.txt", "hello\n");

        let index = TrigramIndex::build(dir.path()).unwrap();
        assert!(!index.is_stale());

        std::thread::sleep(std::time::Duration::from_millis(50));
        write_test_file(dir.path(), "a.txt", "modified\n");
        assert!(index.is_stale());
    }

    #[test]
    fn test_index_regex_search() {
        let dir = tempdir().unwrap();
        write_test_file(dir.path(), "a.txt", "error: something failed\nwarning: ok\nerror: again\n");

        let index = TrigramIndex::build(dir.path()).unwrap();
        let results = index.search("error.*failed", false, false).unwrap();
        assert_eq!(results.len(), 1);
        assert!(results[0].text.contains("something failed"));
    }

    #[test]
    fn test_short_pattern_returns_empty() {
        let dir = tempdir().unwrap();
        write_test_file(dir.path(), "a.txt", "ab\n");

        let index = TrigramIndex::build(dir.path()).unwrap();
        let candidates = index.query_candidates("ab", false);
        assert!(candidates.is_empty(), "patterns shorter than 3 bytes cannot use trigram index");
    }

    #[test]
    fn test_staleness_detects_content_change() {
        let dir = tempdir().unwrap();
        write_test_file(dir.path(), "a.txt", "hello world\n");
        let index = TrigramIndex::build(dir.path()).unwrap();
        assert!(index.staleness_reason().is_none());

        std::thread::sleep(std::time::Duration::from_millis(50));
        write_test_file(dir.path(), "a.txt", "changed content\n");

        let reason = index.staleness_reason().unwrap();
        assert!(reason.contains("a.txt"), "reason={reason}");
    }

    #[test]
    fn test_staleness_detects_file_deletion() {
        let dir = tempdir().unwrap();
        write_test_file(dir.path(), "a.txt", "hello\n");
        write_test_file(dir.path(), "b.txt", "world\n");
        let index = TrigramIndex::build(dir.path()).unwrap();

        fs::remove_file(dir.path().join("b.txt")).unwrap();
        let reason = index.staleness_reason().unwrap();
        assert!(reason.contains("deleted"), "reason={reason}");
        assert!(reason.contains("b.txt"), "reason={reason}");
    }

    #[test]
    fn test_staleness_detects_new_file() {
        let dir = tempdir().unwrap();
        write_test_file(dir.path(), "a.txt", "hello\n");
        let index = TrigramIndex::build(dir.path()).unwrap();
        assert!(index.staleness_reason().is_none());

        write_test_file(dir.path(), "b.txt", "new file\n");
        let reason = index.staleness_reason().unwrap();
        assert!(reason.contains("new file"), "reason={reason}");
    }

    #[test]
    fn test_staleness_detects_size_change_same_mtime() {
        let dir = tempdir().unwrap();
        write_test_file(dir.path(), "a.txt", "short\n");
        let index = TrigramIndex::build(dir.path()).unwrap();

        std::thread::sleep(std::time::Duration::from_millis(50));
        write_test_file(dir.path(), "a.txt", "much longer content here to change size\n");
        let reason = index.staleness_reason();
        assert!(reason.is_some(), "should detect change");
    }

    #[test]
    fn test_format_version_in_binary() {
        let dir = tempdir().unwrap();
        write_test_file(dir.path(), "a.txt", "hello\n");
        let index = TrigramIndex::build(dir.path()).unwrap();
        let index_path = dir.path().join(".tg_index");
        index.save(&index_path).unwrap();

        let data = fs::read(&index_path).unwrap();
        assert_eq!(&data[0..4], b"TGI\x00", "magic bytes");
        assert_eq!(data[4], 1, "format version should be 1");
    }

    #[test]
    fn test_load_rejects_bad_magic() {
        let dir = tempdir().unwrap();
        let index_path = dir.path().join(".tg_index");
        fs::write(&index_path, b"BADMAGIC").unwrap();

        let result = TrigramIndex::load(&index_path);
        assert!(result.is_err());
        let err = result.unwrap_err().to_string();
        assert!(err.contains("magic"), "err={err}");
    }

    #[test]
    fn test_load_rejects_future_version() {
        let dir = tempdir().unwrap();
        write_test_file(dir.path(), "a.txt", "hello\n");
        let index = TrigramIndex::build(dir.path()).unwrap();
        let index_path = dir.path().join(".tg_index");
        index.save(&index_path).unwrap();

        let mut data = fs::read(&index_path).unwrap();
        data[4] = 99;
        fs::write(&index_path, &data).unwrap();

        let result = TrigramIndex::load(&index_path);
        assert!(result.is_err());
        let err = result.unwrap_err().to_string();
        assert!(err.contains("version"), "err={err}");
    }

    #[test]
    fn test_load_rejects_truncated_file() {
        let dir = tempdir().unwrap();
        let index_path = dir.path().join(".tg_index");
        fs::write(&index_path, b"TGI").unwrap();

        let result = TrigramIndex::load(&index_path);
        assert!(result.is_err());
    }

    #[test]
    fn test_rebuild_after_staleness_produces_correct_results() {
        let dir = tempdir().unwrap();
        write_test_file(dir.path(), "a.txt", "hello world\n");
        let index1 = TrigramIndex::build(dir.path()).unwrap();
        let r1 = index1.search("hello", false, true).unwrap();
        assert_eq!(r1.len(), 1);

        std::thread::sleep(std::time::Duration::from_millis(50));
        write_test_file(dir.path(), "a.txt", "goodbye world\n");
        assert!(index1.is_stale());

        let index2 = TrigramIndex::build(dir.path()).unwrap();
        let r2_hello = index2.search("hello", false, true).unwrap();
        assert!(r2_hello.is_empty(), "old content should not match after rebuild");
        let r2_goodbye = index2.search("goodbye", false, true).unwrap();
        assert_eq!(r2_goodbye.len(), 1);
    }
}
