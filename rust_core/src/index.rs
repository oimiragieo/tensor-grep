use anyhow::{Context, Result};
use memmap2::MmapOptions;
use rayon::prelude::*;
use regex_syntax::{
    hir::{
        literal::{ExtractKind, Extractor},
        Hir, HirKind,
    },
    parse as parse_regex_hir,
};
use serde::{Deserialize, Serialize};
use std::collections::HashMap;
use std::fs::File;
use std::io::{self, Write as _};
use std::path::{Path, PathBuf};
use std::time::SystemTime;

const TRIGRAM_LEN: usize = 3;
const MAX_REGEX_CLASS_LITERALS: usize = 10;
const MAX_REGEX_PREFILTER_LITERALS: usize = 64;

type FileTrigramHits = Vec<([u8; 3], u32)>;

#[derive(Debug, Clone, Default, PartialEq, Eq)]
pub struct IncrementalUpdateStats {
    pub added_files: usize,
    pub modified_files: usize,
    pub deleted_files: usize,
    pub reused_files: usize,
}

#[derive(Debug, Clone)]
pub struct IncrementalUpdateResult {
    pub index: TrigramIndex,
    pub stats: IncrementalUpdateStats,
}

#[derive(Debug, Clone, PartialEq, Eq)]
struct RegexLiteralPlan {
    literals: Vec<Vec<u8>>,
}

impl RegexLiteralPlan {
    fn from_raw(literals: Vec<Vec<u8>>, ignore_case: bool) -> Option<Self> {
        if literals.is_empty() || literals.len() > MAX_REGEX_PREFILTER_LITERALS {
            return None;
        }

        let mut normalized = Vec::with_capacity(literals.len());
        for literal in literals {
            let literal = normalize_prefilter_literal(&literal, ignore_case)?;
            if literal.len() < TRIGRAM_LEN {
                return None;
            }
            normalized.push(literal);
        }

        normalized.sort();
        normalized.dedup();
        (!normalized.is_empty()).then_some(Self {
            literals: normalized,
        })
    }

    fn min_len(&self) -> usize {
        self.literals.iter().map(Vec::len).min().unwrap_or(0)
    }

    fn total_len(&self) -> usize {
        self.literals.iter().map(Vec::len).sum()
    }
}

enum RegexCandidateSelection {
    Indexed(Vec<(PathBuf, usize)>),
    FullScan,
}

enum SearchMatcher {
    Fixed {
        needle: String,
        lower_needle: Option<String>,
    },
    Regex(regex::Regex),
}

impl SearchMatcher {
    fn new(pattern: &str, ignore_case: bool, fixed_strings: bool) -> Result<Self> {
        if fixed_strings {
            return Ok(Self::Fixed {
                needle: pattern.to_string(),
                lower_needle: ignore_case.then(|| pattern.to_lowercase()),
            });
        }

        regex::RegexBuilder::new(pattern)
            .case_insensitive(ignore_case)
            .build()
            .context(format!(
                "failed to compile index search pattern '{pattern}'"
            ))
            .map(Self::Regex)
    }

    fn is_match(&self, line: &str) -> bool {
        match self {
            Self::Fixed {
                needle,
                lower_needle,
            } => {
                if let Some(lower_needle) = lower_needle {
                    line.to_lowercase().contains(lower_needle)
                } else {
                    line.contains(needle)
                }
            }
            Self::Regex(re) => re.is_match(line),
        }
    }
}

#[derive(Debug, Clone, Serialize, Deserialize)]
struct FileEntry {
    path: PathBuf,
    mtime_ns: u128,
    size: u64,
    #[serde(default)]
    deleted: bool,
}

#[derive(Debug, Clone, Serialize, Deserialize)]
struct PostingEntry {
    file_id: u32,
    line: u32,
}

#[derive(Debug, Clone)]
pub struct TrigramIndex {
    root: PathBuf,
    files: Vec<FileEntry>,
    file_trigrams: Vec<FileTrigramHits>,
    postings: HashMap<[u8; 3], Vec<PostingEntry>>,
    /// Whether this index was built with `--no-ignore` (gitignored files included).
    /// Persisted so a query whose --no-ignore mode differs from the build mode is
    /// detected as stale (audit H1d) instead of silently serving the wrong file set --
    /// either leaking gitignored content into a default query, or missing gitignored
    /// files a --no-ignore query asked for.
    no_ignore: bool,
}

#[derive(Debug, Clone, Serialize, Deserialize)]
struct SerializableIndex {
    files: Vec<FileEntry>,
    postings: HashMap<String, Vec<PostingEntry>>,
    no_ignore: bool,
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
            no_ignore: self.no_ignore,
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
        normalize_postings(&mut postings);
        let file_trigrams = rebuild_file_trigrams(s.files.len(), &postings)?;
        Ok(Self {
            root: PathBuf::new(),
            files: s.files,
            file_trigrams,
            postings,
            no_ignore: s.no_ignore,
        })
    }
}

const INDEX_MAGIC: &[u8; 4] = b"TGI\x00";
// Bumped 3 -> 4 (audit H1d) to add the `no_ignore` build-mode byte below; any index
// written by an older binary fails the version check in bincode_deserialize and is
// rebuilt from scratch by every caller of TrigramIndex::load (main.rs's
// detect_warm_index_state and handle_index_search both already treat a load error as
// "stale, rebuild"), so the bump is safe.
const INDEX_FORMAT_VERSION: u8 = 4;

fn normalize_postings(postings: &mut HashMap<[u8; 3], Vec<PostingEntry>>) {
    for entries in postings.values_mut() {
        entries.sort_unstable_by_key(|entry| (entry.file_id, entry.line));
        entries.dedup_by_key(|entry| (entry.file_id, entry.line));
    }
}

fn read_exact<'a>(data: &'a [u8], pos: &mut usize, len: usize) -> Result<&'a [u8]> {
    let end = pos
        .checked_add(len)
        .ok_or_else(|| anyhow::anyhow!("index file is truncated"))?;
    if end > data.len() {
        anyhow::bail!("index file is truncated");
    }
    let slice = &data[*pos..end];
    *pos = end;
    Ok(slice)
}

fn read_u8(data: &[u8], pos: &mut usize) -> Result<u8> {
    Ok(read_exact(data, pos, 1)?[0])
}

fn read_u32_le(data: &[u8], pos: &mut usize) -> Result<u32> {
    let bytes = read_exact(data, pos, 4)?;
    Ok(u32::from_le_bytes(bytes.try_into()?))
}

fn read_u64_le(data: &[u8], pos: &mut usize) -> Result<u64> {
    let bytes = read_exact(data, pos, 8)?;
    Ok(u64::from_le_bytes(bytes.try_into()?))
}

fn read_u128_le(data: &[u8], pos: &mut usize) -> Result<u128> {
    let bytes = read_exact(data, pos, 16)?;
    Ok(u128::from_le_bytes(bytes.try_into()?))
}

fn write_varint_u32(buf: &mut Vec<u8>, mut value: u32) {
    while value >= 0x80 {
        buf.push(((value & 0x7f) as u8) | 0x80);
        value >>= 7;
    }
    buf.push(value as u8);
}

fn read_varint_u32(data: &[u8], pos: &mut usize) -> Result<u32> {
    let mut value = 0u32;
    let mut shift = 0u32;

    for _ in 0..5 {
        let byte = read_u8(data, pos)?;
        value |= u32::from(byte & 0x7f) << shift;
        if byte & 0x80 == 0 {
            return Ok(value);
        }
        shift += 7;
    }

    anyhow::bail!("invalid varint in index postings")
}

fn bincode_serialize(index: &TrigramIndex) -> Result<Vec<u8>> {
    let mut buf = Vec::new();
    buf.extend_from_slice(INDEX_MAGIC);
    buf.push(INDEX_FORMAT_VERSION);
    buf.push(u8::from(index.no_ignore));

    let root_bytes = index.root.to_string_lossy().as_bytes().to_vec();
    buf.extend_from_slice(&(root_bytes.len() as u32).to_le_bytes());
    buf.extend_from_slice(&root_bytes);

    let files_count = index.files.len() as u32;
    buf.extend_from_slice(&files_count.to_le_bytes());
    for entry in &index.files {
        let path_bytes = entry.path.to_string_lossy().as_bytes().to_vec();
        buf.extend_from_slice(&(path_bytes.len() as u32).to_le_bytes());
        buf.extend_from_slice(&path_bytes);
        buf.extend_from_slice(&entry.mtime_ns.to_le_bytes());
        buf.extend_from_slice(&entry.size.to_le_bytes());
        buf.push(u8::from(entry.deleted));
    }

    let trigram_count = index.postings.len() as u32;
    buf.extend_from_slice(&trigram_count.to_le_bytes());
    for (trigram, postings) in &index.postings {
        buf.extend_from_slice(trigram);
        buf.extend_from_slice(&(postings.len() as u32).to_le_bytes());
        let mut previous_file_id = 0u32;
        let mut previous_line = 0u32;
        let mut first = true;
        for posting in postings {
            let file_delta = if first {
                posting.file_id
            } else {
                posting
                    .file_id
                    .checked_sub(previous_file_id)
                    .ok_or_else(|| anyhow::anyhow!("postings are not sorted by file_id"))?
            };
            let line_delta = if first || file_delta > 0 {
                posting.line
            } else {
                posting
                    .line
                    .checked_sub(previous_line)
                    .ok_or_else(|| anyhow::anyhow!("postings are not sorted by line number"))?
            };

            write_varint_u32(&mut buf, file_delta);
            write_varint_u32(&mut buf, line_delta);

            previous_file_id = posting.file_id;
            previous_line = posting.line;
            first = false;
        }
    }

    Ok(buf)
}

/// Cap a length-prefixed pre-allocation to the bytes actually remaining in the buffer.
/// Every element consumes at least one byte, so a declared count larger than the remaining
/// input is corrupt/hostile; clamping prevents a crafted index file from forcing a multi-GB
/// Vec/HashMap allocation (OOM-abort DoS) before the read loop fails cleanly. audit MED.
fn bounded_capacity(declared: usize, data: &[u8], pos: usize) -> usize {
    declared.min(data.len().saturating_sub(pos))
}

fn bincode_deserialize(data: &[u8]) -> Result<TrigramIndex> {
    let mut pos = 0;

    if data.len() < 5 {
        anyhow::bail!("index file is truncated");
    }

    if read_exact(data, &mut pos, 4)? != INDEX_MAGIC {
        anyhow::bail!("invalid index file magic");
    }

    let version = read_u8(data, &mut pos)?;
    if version != INDEX_FORMAT_VERSION {
        anyhow::bail!(
            "unsupported index format version {} (expected {})",
            version,
            INDEX_FORMAT_VERSION
        );
    }

    let no_ignore = read_u8(data, &mut pos)? != 0;

    let root_len = read_u32_le(data, &mut pos)? as usize;
    let root_str = String::from_utf8_lossy(read_exact(data, &mut pos, root_len)?).to_string();

    let files_count = read_u32_le(data, &mut pos)? as usize;

    let mut files = Vec::with_capacity(bounded_capacity(files_count, data, pos));
    for _ in 0..files_count {
        let path_len = read_u32_le(data, &mut pos)? as usize;
        let path_str = String::from_utf8_lossy(read_exact(data, &mut pos, path_len)?).to_string();
        let mtime_ns = read_u128_le(data, &mut pos)?;
        let size = read_u64_le(data, &mut pos)?;
        let deleted = read_u8(data, &mut pos)? != 0;
        files.push(FileEntry {
            path: PathBuf::from(path_str),
            mtime_ns,
            size,
            deleted,
        });
    }

    let trigram_count = read_u32_le(data, &mut pos)? as usize;

    let mut postings = HashMap::with_capacity(bounded_capacity(trigram_count, data, pos));
    for _ in 0..trigram_count {
        let trigram: [u8; 3] = read_exact(data, &mut pos, 3)?.try_into()?;
        let posting_count = read_u32_le(data, &mut pos)? as usize;
        let mut entries = Vec::with_capacity(bounded_capacity(posting_count, data, pos));
        let mut previous_file_id = 0u32;
        let mut previous_line = 0u32;
        let mut first = true;
        for _ in 0..posting_count {
            let file_delta = read_varint_u32(data, &mut pos)?;
            let line_delta = read_varint_u32(data, &mut pos)?;
            let file_id = if first {
                file_delta
            } else {
                previous_file_id
                    .checked_add(file_delta)
                    .ok_or_else(|| anyhow::anyhow!("index file contains invalid file_id delta"))?
            };
            let line = if first || file_delta > 0 {
                line_delta
            } else {
                previous_line
                    .checked_add(line_delta)
                    .ok_or_else(|| anyhow::anyhow!("index file contains invalid line delta"))?
            };
            entries.push(PostingEntry { file_id, line });
            previous_file_id = file_id;
            previous_line = line;
            first = false;
        }
        postings.insert(trigram, entries);
    }

    let file_trigrams = rebuild_file_trigrams(files.len(), &postings)?;

    Ok(TrigramIndex {
        root: PathBuf::from(root_str),
        files,
        file_trigrams,
        postings,
        no_ignore,
    })
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
        let file_entries = collect_file_entries(root, no_ignore);

        let per_file: Vec<(u32, FileTrigramHits)> = file_entries
            .par_iter()
            .enumerate()
            .map(|(file_id, entry)| {
                let trigrams = extract_file_trigrams(&entry.path).unwrap_or_default();
                (file_id as u32, trigrams)
            })
            .collect();

        let mut file_trigrams = vec![Vec::new(); file_entries.len()];
        let mut postings: HashMap<[u8; 3], Vec<PostingEntry>> = HashMap::new();
        for (file_id, hits) in per_file {
            for (trigram, line) in &hits {
                postings.entry(*trigram).or_default().push(PostingEntry {
                    file_id,
                    line: *line,
                });
            }
            file_trigrams[file_id as usize] = hits;
        }
        normalize_postings(&mut postings);

        Ok(Self {
            root: root.to_path_buf(),
            files: file_entries,
            file_trigrams,
            postings,
            no_ignore,
        })
    }

    pub fn rebuild_incremental_with_options(
        mut self,
        root: &Path,
        no_ignore: bool,
    ) -> Result<IncrementalUpdateResult> {
        let current_entries = collect_file_entries(root, no_ignore);
        let current_paths: HashMap<&Path, &FileEntry> = current_entries
            .iter()
            .map(|entry| (entry.path.as_path(), entry))
            .collect();
        let active_files: HashMap<&Path, usize> = self
            .files
            .iter()
            .enumerate()
            .filter(|(_, entry)| !entry.deleted)
            .map(|(file_id, entry)| (entry.path.as_path(), file_id))
            .collect();

        let mut stats = IncrementalUpdateStats::default();
        let mut modified_entries = Vec::new();
        let mut added_entries = Vec::new();

        for entry in &current_entries {
            match active_files.get(entry.path.as_path()) {
                Some(&file_id) => {
                    let existing = &self.files[file_id];
                    if existing.mtime_ns == entry.mtime_ns && existing.size == entry.size {
                        stats.reused_files += 1;
                    } else {
                        stats.modified_files += 1;
                        modified_entries.push((file_id, entry.clone()));
                    }
                }
                None => {
                    stats.added_files += 1;
                    added_entries.push(entry.clone());
                }
            }
        }

        let deleted_file_ids: Vec<usize> = self
            .files
            .iter()
            .enumerate()
            .filter(|(_, entry)| !entry.deleted)
            .filter_map(|(file_id, entry)| {
                (!current_paths.contains_key(entry.path.as_path())).then_some(file_id)
            })
            .collect();
        stats.deleted_files = deleted_file_ids.len();

        let mut affected_trigrams = std::collections::HashSet::new();

        for file_id in deleted_file_ids {
            remove_file_postings(
                &mut self.postings,
                file_id as u32,
                &self.file_trigrams[file_id],
                &mut affected_trigrams,
            );
            self.file_trigrams[file_id].clear();
            self.files[file_id].deleted = true;
        }

        let changed_postings: Vec<(usize, FileEntry, FileTrigramHits)> = modified_entries
            .par_iter()
            .map(|(file_id, entry)| {
                (
                    *file_id,
                    entry.clone(),
                    extract_file_trigrams(&entry.path).unwrap_or_default(),
                )
            })
            .collect();

        for (file_id, entry, hits) in changed_postings {
            remove_file_postings(
                &mut self.postings,
                file_id as u32,
                &self.file_trigrams[file_id],
                &mut affected_trigrams,
            );
            add_file_postings(
                &mut self.postings,
                file_id as u32,
                &hits,
                &mut affected_trigrams,
            );
            self.files[file_id] = entry;
            self.file_trigrams[file_id] = hits;
        }

        let new_postings: Vec<(FileEntry, FileTrigramHits)> = added_entries
            .par_iter()
            .map(|entry| {
                (
                    entry.clone(),
                    extract_file_trigrams(&entry.path).unwrap_or_default(),
                )
            })
            .collect();

        for (entry, hits) in new_postings {
            let file_id = self.files.len() as u32;
            add_file_postings(&mut self.postings, file_id, &hits, &mut affected_trigrams);
            self.files.push(entry);
            self.file_trigrams.push(hits);
        }

        normalize_affected_postings(&mut self.postings, &affected_trigrams);
        self.root = root.to_path_buf();
        // H1d: persist the query's no_ignore mode onto the rebuilt index so a subsequent
        // staleness check compares against what this rebuild actually walked with, not a
        // stale build-time value.
        self.no_ignore = no_ignore;

        Ok(IncrementalUpdateResult { index: self, stats })
    }

    pub fn query_candidates_fixed(
        &self,
        pattern: &str,
        ignore_case: bool,
    ) -> Vec<(PathBuf, usize)> {
        let pat = if ignore_case {
            pattern.to_lowercase()
        } else {
            pattern.to_string()
        };
        self.query_with_trigrams(&extract_trigrams(pat.as_bytes()))
    }

    pub fn query_candidates(&self, pattern: &str, ignore_case: bool) -> Vec<(PathBuf, usize)> {
        match self.regex_candidate_selection(pattern, ignore_case) {
            RegexCandidateSelection::Indexed(candidates) => candidates,
            RegexCandidateSelection::FullScan => Vec::new(),
        }
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
                (!entry.deleted).then_some((entry.path.clone(), line as usize))
            })
            .collect()
    }

    pub fn search(
        &self,
        pattern: &str,
        ignore_case: bool,
        fixed_strings: bool,
    ) -> Result<Vec<IndexQueryResult>> {
        let candidate_selection = if fixed_strings {
            self.fixed_string_candidate_selection(pattern, ignore_case)
        } else {
            self.regex_candidate_selection(pattern, ignore_case)
        };

        let mut all_results = match candidate_selection {
            RegexCandidateSelection::Indexed(candidates) => {
                if candidates.is_empty() {
                    return Ok(Vec::new());
                }

                let matcher = SearchMatcher::new(pattern, ignore_case, fixed_strings)?;

                let mut by_file: HashMap<&Path, Vec<usize>> = HashMap::new();
                for (file, line) in &candidates {
                    by_file.entry(file.as_path()).or_default().push(*line);
                }

                let file_entries: Vec<(&Path, Vec<usize>)> = by_file.into_iter().collect();
                let results: Vec<Result<Vec<IndexQueryResult>>> = file_entries
                    .par_iter()
                    .map(|(file, candidate_lines)| {
                        collect_matches(file, Some(candidate_lines), &matcher)
                    })
                    .collect();

                let mut matches = Vec::new();
                for result in results {
                    matches.extend(result?);
                }
                matches
            }
            RegexCandidateSelection::FullScan => {
                self.search_all_files(pattern, ignore_case, fixed_strings)?
            }
        };

        all_results.sort_by(|a, b| a.file.cmp(&b.file).then(a.line.cmp(&b.line)));
        Ok(all_results)
    }

    /// Candidate selection for `--fixed-strings` queries. Falls back to a full scan
    /// (never a silently-empty `Indexed([])`) in the two cases the trigram prefilter
    /// cannot answer correctly:
    ///
    /// - H1b: a pattern shorter than TRIGRAM_LEN has no trigrams to index on, so
    ///   `query_candidates_fixed` returns zero candidates -- `search()` would read that
    ///   as "definitely no match" instead of "the index can't accelerate this".
    /// - H1c: build-time trigrams are lowercased with `to_ascii_lowercase` (a no-op on
    ///   multi-byte UTF-8, see `extract_file_trigrams`), but `query_candidates_fixed`'s
    ///   ignore_case path lowercases with Unicode-aware `str::to_lowercase` -- a
    ///   non-ASCII ignore-case pattern's trigrams can never line up with the index's,
    ///   again reading as a false "no match". Same precedent as
    ///   `normalize_prefilter_literal`'s regex-path guard just below.
    fn fixed_string_candidate_selection(
        &self,
        pattern: &str,
        ignore_case: bool,
    ) -> RegexCandidateSelection {
        if ignore_case && !pattern.is_ascii() {
            return RegexCandidateSelection::FullScan;
        }

        let normalized_len = if ignore_case {
            pattern.to_lowercase().len()
        } else {
            pattern.len()
        };
        if normalized_len < TRIGRAM_LEN {
            return RegexCandidateSelection::FullScan;
        }

        RegexCandidateSelection::Indexed(self.query_candidates_fixed(pattern, ignore_case))
    }

    fn regex_candidate_selection(
        &self,
        pattern: &str,
        ignore_case: bool,
    ) -> RegexCandidateSelection {
        let Some(plan) = select_regex_prefilter_literals(pattern, ignore_case) else {
            return RegexCandidateSelection::FullScan;
        };

        let mut candidates = Vec::new();
        for literal in &plan.literals {
            candidates.extend(self.query_with_trigrams(&extract_trigrams(literal)));
        }

        candidates.sort();
        candidates.dedup();
        RegexCandidateSelection::Indexed(candidates)
    }

    fn search_all_files(
        &self,
        pattern: &str,
        ignore_case: bool,
        fixed_strings: bool,
    ) -> Result<Vec<IndexQueryResult>> {
        let matcher = SearchMatcher::new(pattern, ignore_case, fixed_strings)?;

        let results: Vec<Result<Vec<IndexQueryResult>>> = self
            .files
            .par_iter()
            .filter(|entry| !entry.deleted)
            .map(|entry| collect_matches(&entry.path, None, &matcher))
            .collect();

        let mut matches = Vec::new();
        for result in results {
            matches.extend(result?);
        }
        Ok(matches)
    }

    pub fn is_stale(&self, no_ignore: bool) -> bool {
        self.staleness_reason(no_ignore).is_some()
    }

    /// `no_ignore` is the CURRENT query's `--no-ignore` request, compared against the
    /// mode this index was actually built with (`self.no_ignore`).
    pub fn staleness_reason(&self, no_ignore: bool) -> Option<String> {
        // H1d (audit): a stored no_ignore mode that disagrees with the current query's
        // --no-ignore request means this index was built walking a DIFFERENT file set
        // than the one the query now expects -- reusing it as-is either leaks gitignored
        // content into a default query (built --no-ignore, queried without) or misses
        // gitignored files a --no-ignore query asked for (built without, queried with).
        // Treat it as stale so the caller rebuilds under the query's requested mode.
        if self.no_ignore != no_ignore {
            return Some(format!(
                "no_ignore mode changed: index built with no_ignore={}, query requested no_ignore={}",
                self.no_ignore, no_ignore
            ));
        }

        let indexed_paths: std::collections::HashSet<&Path> = self
            .files
            .iter()
            .filter(|entry| !entry.deleted)
            .map(|e| e.path.as_path())
            .collect();

        for entry in self.files.iter().filter(|entry| !entry.deleted) {
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

        if self.root.is_dir() {
            // Mirror collect_file_entries' walk semantics exactly -- this was hardcoded
            // .git_ignore(true) regardless of no_ignore (audit H1d), so the new-file scan
            // could disagree with how this index would actually be rebuilt. #127: also mirror
            // collect_file_entries' add_ignore trio so this scan agrees with it outside a git
            // repo too (see that function for the require_git(false) rationale).
            let mut builder = ignore::WalkBuilder::new(&self.root);
            builder.hidden(true).git_ignore(!self.no_ignore);
            if !self.no_ignore {
                for ignore_name in [".ignore", ".gitignore", ".rgignore"] {
                    let ignore_path = self.root.join(ignore_name);
                    if ignore_path.is_file() {
                        builder.add_ignore(ignore_path);
                    }
                }
            }
            let current_files: Vec<PathBuf> = builder
                .build()
                .filter_map(|e| e.ok())
                .filter(|e| e.file_type().is_some_and(|ft| ft.is_file()))
                .map(|e| e.into_path())
                .collect();

            for file in &current_files {
                if !indexed_paths.contains(file.as_path()) {
                    return Some(format!("new file: {}", file.display()));
                }
            }
        }

        None
    }

    pub fn root(&self) -> &Path {
        &self.root
    }

    /// Persists the bincode-serialized index atomically -- see [`atomic_write_bytes`]. Audit
    /// #138 item #1: the previous `std::fs::write(path, ...)` here wrote the destination
    /// in-place, so a crash mid-write left a truncated/corrupt `.tg_index` behind.
    pub fn save(&self, path: &Path) -> Result<()> {
        let data = bincode_serialize(self)?;
        atomic_write_bytes(path, &data)
    }

    pub fn load(path: &Path) -> Result<Self> {
        let data = std::fs::read(path)
            .with_context(|| format!("failed to read index from {}", path.display()))?;
        bincode_deserialize(&data)
    }

    /// Persists the legacy JSON index representation atomically -- see [`atomic_write_bytes`].
    /// Same audit #138 item #1 rationale as [`Self::save`].
    pub fn save_json(&self, path: &Path) -> Result<()> {
        let data =
            serde_json::to_vec(&self.to_serializable()).context("failed to serialize index")?;
        atomic_write_bytes(path, &data)
    }

    pub fn load_json(path: &Path) -> Result<Self> {
        let data = std::fs::read(path)
            .with_context(|| format!("failed to read index from {}", path.display()))?;
        let serializable: SerializableIndex =
            serde_json::from_slice(&data).context("failed to deserialize index")?;
        Self::from_serializable(serializable)
    }

    pub fn file_count(&self) -> usize {
        self.files.iter().filter(|entry| !entry.deleted).count()
    }

    pub fn trigram_count(&self) -> usize {
        self.postings.len()
    }

    pub fn total_postings(&self) -> usize {
        self.postings.values().map(|v| v.len()).sum()
    }
}

/// Writes `data` to `path` via write-temp-then-atomic-rename, mirroring
/// `checkpoint_store.py::_write_json_atomic`: the temp file lives in the SAME directory as
/// `path` (so the rename is same-filesystem and therefore atomic), is fsync'd before the rename
/// so a crash between the write and the rename can never publish a truncated file (the rename
/// simply never happens -- `path` itself is untouched until it atomically becomes the new
/// complete content in one indivisible step), and the rename is retried via
/// `index_lock::replace_with_retry` to absorb the transient Windows "destination momentarily
/// held open by a reader/AV scanner" case. On `cfg(unix)` the parent directory is ALSO fsync'd
/// after the rename, best-effort, for durability of the directory entry itself (skipped on
/// Windows, where a directory handle cannot be fsync'd this way). Audit #138 item #1.
fn atomic_write_bytes(path: &Path, data: &[u8]) -> Result<()> {
    let parent = match path.parent() {
        Some(p) if !p.as_os_str().is_empty() => p,
        _ => Path::new("."),
    };
    std::fs::create_dir_all(parent)
        .with_context(|| format!("failed to create parent dir for {}", path.display()))?;

    let file_name = path.file_name().and_then(|n| n.to_str()).unwrap_or("index");
    let tmp_path = parent.join(format!(
        ".{file_name}.{}.tmp",
        crate::index_lock::random_token()
    ));

    let write_result = (|| -> io::Result<()> {
        let mut file = std::fs::File::create(&tmp_path)?;
        file.write_all(data)?;
        // fsync the data before the rename so a crash can never publish a truncated index --
        // the rename below is the ONLY step that makes the new content visible at `path`.
        file.sync_all()
    })();

    if let Err(e) = write_result {
        let _ = std::fs::remove_file(&tmp_path); // best-effort cleanup; the original at `path` is untouched
        return Err(e).with_context(|| format!("failed to write temp file {}", tmp_path.display()));
    }

    if let Err(e) = crate::index_lock::replace_with_retry(&tmp_path, path) {
        let _ = std::fs::remove_file(&tmp_path);
        return Err(e).with_context(|| {
            format!(
                "failed to atomically rename {} -> {}",
                tmp_path.display(),
                path.display()
            )
        });
    }

    #[cfg(unix)]
    {
        // Best-effort durability of the rename's directory entry; a failure here does not
        // invalidate the already-completed atomic rename above.
        if let Ok(dir_file) = std::fs::File::open(parent) {
            let _ = dir_file.sync_all();
        }
    }

    Ok(())
}

fn collect_file_entries(root: &Path, no_ignore: bool) -> Vec<FileEntry> {
    let mut builder = ignore::WalkBuilder::new(root);
    builder.hidden(true).git_ignore(!no_ignore);
    // #127: outside a directory the `ignore` crate recognizes as an actual git repo (no
    // `.git`/`.jj` marker in any ancestor), `.git_ignore(true)` alone never auto-discovers
    // `.gitignore` files -- the crate only applies them once it has detected a git repo, so a
    // root `.gitignore` was silently a no-op there (index pollution). Deliberately NOT
    // `.require_git(false)`: that would additionally pull in nested/global gitignores outside
    // git, diverging from `tg search`'s own root-only behavior (BACKLOG #127). Mirror the
    // sibling `add_ignore` trio instead (main.rs:5695 / native_search.rs:1471) -- explicitly
    // added ignore files are honored by the `ignore` crate unconditionally, git repo or not.
    if !no_ignore {
        for ignore_name in [".ignore", ".gitignore", ".rgignore"] {
            let ignore_path = root.join(ignore_name);
            if ignore_path.is_file() {
                builder.add_ignore(ignore_path);
            }
        }
    }
    builder
        .build()
        .filter_map(|entry| entry.ok())
        .filter(|entry| {
            entry
                .file_type()
                .is_some_and(|file_type| file_type.is_file())
        })
        .filter_map(|entry| {
            let path = entry.into_path();
            let meta = path.metadata().ok()?;
            let mtime_ns = meta
                .modified()
                .ok()?
                .duration_since(SystemTime::UNIX_EPOCH)
                .ok()?
                .as_nanos();
            Some(FileEntry {
                path,
                mtime_ns,
                size: meta.len(),
                deleted: false,
            })
        })
        .collect()
}

fn rebuild_file_trigrams(
    file_count: usize,
    postings: &HashMap<[u8; 3], Vec<PostingEntry>>,
) -> Result<Vec<FileTrigramHits>> {
    let mut file_trigrams = vec![Vec::new(); file_count];
    for (trigram, entries) in postings {
        for entry in entries {
            let Some(file_hits) = file_trigrams.get_mut(entry.file_id as usize) else {
                anyhow::bail!("index postings reference missing file id {}", entry.file_id);
            };
            file_hits.push((*trigram, entry.line));
        }
    }

    for hits in &mut file_trigrams {
        hits.sort_unstable_by_key(|(trigram, line)| (*trigram, *line));
        hits.dedup();
    }

    Ok(file_trigrams)
}

fn add_file_postings(
    postings: &mut HashMap<[u8; 3], Vec<PostingEntry>>,
    file_id: u32,
    hits: &FileTrigramHits,
    affected_trigrams: &mut std::collections::HashSet<[u8; 3]>,
) {
    for (trigram, line) in hits {
        postings.entry(*trigram).or_default().push(PostingEntry {
            file_id,
            line: *line,
        });
        affected_trigrams.insert(*trigram);
    }
}

fn remove_file_postings(
    postings: &mut HashMap<[u8; 3], Vec<PostingEntry>>,
    file_id: u32,
    hits: &FileTrigramHits,
    affected_trigrams: &mut std::collections::HashSet<[u8; 3]>,
) {
    let mut lines_by_trigram: HashMap<[u8; 3], Vec<u32>> = HashMap::new();
    for (trigram, line) in hits {
        lines_by_trigram.entry(*trigram).or_default().push(*line);
    }

    for (trigram, mut lines) in lines_by_trigram {
        affected_trigrams.insert(trigram);
        let Some(entries) = postings.get_mut(&trigram) else {
            continue;
        };

        lines.sort_unstable();
        lines.dedup();
        entries
            .retain(|entry| entry.file_id != file_id || lines.binary_search(&entry.line).is_err());
    }
}

fn normalize_affected_postings(
    postings: &mut HashMap<[u8; 3], Vec<PostingEntry>>,
    affected_trigrams: &std::collections::HashSet<[u8; 3]>,
) {
    for trigram in affected_trigrams {
        let remove = if let Some(entries) = postings.get_mut(trigram) {
            entries.sort_unstable_by_key(|entry| (entry.file_id, entry.line));
            entries.dedup_by_key(|entry| (entry.file_id, entry.line));
            entries.is_empty()
        } else {
            false
        };

        if remove {
            postings.remove(trigram);
        }
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

    for (line_num, line_bytes) in (1_u32..).zip(mmap.split(|&b| b == b'\n')) {
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
    }

    Ok(trigrams)
}

/// Safe regex acceleration is intentionally conservative.
///
/// We only use the trigram index when the regex parser can prove a finite set
/// of literals that every match must contain. This covers literal alternations
/// like `(foo|bar)`, small character-class expansions such as `de[ab]f` or
/// `[abc]def`, and case-sensitive UTF-8 literals. Patterns with large or
/// unbounded classes that do not leave behind another provable literal,
/// empty/optional branches, or non-ASCII ignore-case literals fall back to a
/// full scan so the index never introduces false negatives.
fn select_regex_prefilter_literals(pattern: &str, ignore_case: bool) -> Option<RegexLiteralPlan> {
    let hir = parse_regex_hir(pattern).ok()?;

    [
        extract_edge_literal_plan(&hir, ExtractKind::Prefix, ignore_case),
        extract_edge_literal_plan(&hir, ExtractKind::Suffix, ignore_case),
        extract_inner_literal_plan(&hir, ignore_case),
    ]
    .into_iter()
    .flatten()
    .max_by(compare_regex_literal_plans)
}

fn extract_edge_literal_plan(
    hir: &Hir,
    kind: ExtractKind,
    ignore_case: bool,
) -> Option<RegexLiteralPlan> {
    let mut extractor = Extractor::new();
    extractor
        .kind(kind)
        .limit_class(MAX_REGEX_CLASS_LITERALS)
        .limit_total(MAX_REGEX_PREFILTER_LITERALS);

    let literals = extractor
        .extract(hir)
        .literals()?
        .iter()
        .map(|literal| literal.as_bytes().to_vec())
        .collect();

    RegexLiteralPlan::from_raw(literals, ignore_case)
}

fn extract_inner_literal_plan(hir: &Hir, ignore_case: bool) -> Option<RegexLiteralPlan> {
    match hir.kind() {
        HirKind::Empty | HirKind::Class(_) | HirKind::Look(_) => None,
        HirKind::Literal(literal) => {
            RegexLiteralPlan::from_raw(vec![literal.0.to_vec()], ignore_case)
        }
        HirKind::Capture(capture) => extract_inner_literal_plan(&capture.sub, ignore_case),
        HirKind::Repetition(repetition) => (repetition.min > 0)
            .then(|| extract_inner_literal_plan(&repetition.sub, ignore_case))
            .flatten(),
        HirKind::Concat(parts) => parts
            .iter()
            .filter_map(|part| extract_inner_literal_plan(part, ignore_case))
            .max_by(compare_regex_literal_plans),
        HirKind::Alternation(parts) => {
            let mut combined = Vec::new();
            for part in parts {
                let plan = extract_inner_literal_plan(part, ignore_case)?;
                combined.extend(plan.literals);
                if combined.len() > MAX_REGEX_PREFILTER_LITERALS {
                    return None;
                }
            }
            RegexLiteralPlan::from_raw(combined, false)
        }
    }
}

fn normalize_prefilter_literal(literal: &[u8], ignore_case: bool) -> Option<Vec<u8>> {
    if ignore_case {
        if !literal.is_ascii() {
            return None;
        }
        Some(
            literal
                .iter()
                .map(|byte| byte.to_ascii_lowercase())
                .collect(),
        )
    } else {
        Some(literal.to_vec())
    }
}

fn compare_regex_literal_plans(a: &RegexLiteralPlan, b: &RegexLiteralPlan) -> std::cmp::Ordering {
    a.min_len()
        .cmp(&b.min_len())
        .then_with(|| a.total_len().cmp(&b.total_len()))
        .then_with(|| b.literals.len().cmp(&a.literals.len()))
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

fn collect_matches(
    file: &Path,
    candidate_lines: Option<&[usize]>,
    matcher: &SearchMatcher,
) -> Result<Vec<IndexQueryResult>> {
    let content = std::fs::read_to_string(file)
        .with_context(|| format!("failed to read {}", file.display()))?;

    let lines: Vec<&str> = content.lines().collect();
    let mut results = Vec::new();

    match candidate_lines {
        Some(candidate_lines) => {
            for &line_num in candidate_lines {
                if line_num == 0 || line_num > lines.len() {
                    continue;
                }
                let line = lines[line_num - 1];
                if matcher.is_match(line) {
                    results.push(IndexQueryResult {
                        file: file.to_path_buf(),
                        line: line_num,
                        text: line.to_string(),
                    });
                }
            }
        }
        None => {
            for (line_index, line) in lines.iter().enumerate() {
                if matcher.is_match(line) {
                    results.push(IndexQueryResult {
                        file: file.to_path_buf(),
                        line: line_index + 1,
                        text: (*line).to_string(),
                    });
                }
            }
        }
    }

    Ok(results)
}

#[cfg(test)]
mod tests {
    use super::*;
    use std::fmt::Write as _;
    use std::fs;
    use tempfile::tempdir;

    fn write_test_file(dir: &Path, name: &str, content: &str) {
        fs::write(dir.join(name), content).unwrap();
    }

    #[test]
    fn bincode_deserialize_rejects_hostile_length_prefix_without_oom() {
        // A crafted index declaring ~4 billion file entries but supplying no data must fail
        // with a clean error, not pre-allocate a multi-GB Vec and OOM-abort. Without the
        // bounded_capacity clamp this is Vec::with_capacity(u32::MAX) -> allocation abort;
        // with it, the read loop fails on the first missing entry and returns Err (audit MED).
        let mut data = Vec::new();
        data.extend_from_slice(INDEX_MAGIC);
        data.push(INDEX_FORMAT_VERSION);
        data.extend_from_slice(&0u32.to_le_bytes()); // root_len = 0
        data.extend_from_slice(&u32::MAX.to_le_bytes()); // files_count = hostile
                                                         // no file data follows (truncated)

        let result = bincode_deserialize(&data);
        assert!(result.is_err(), "hostile length prefix must error, not OOM");
    }

    fn serialize_legacy_v1(index: &TrigramIndex) -> Vec<u8> {
        let mut buf = Vec::new();
        buf.extend_from_slice(INDEX_MAGIC);
        buf.push(1);

        let root_bytes = index.root.to_string_lossy().as_bytes().to_vec();
        buf.extend_from_slice(&(root_bytes.len() as u32).to_le_bytes());
        buf.extend_from_slice(&root_bytes);

        buf.extend_from_slice(&(index.files.len() as u32).to_le_bytes());
        for entry in &index.files {
            let path_bytes = entry.path.to_string_lossy().as_bytes().to_vec();
            buf.extend_from_slice(&(path_bytes.len() as u32).to_le_bytes());
            buf.extend_from_slice(&path_bytes);
            buf.extend_from_slice(&entry.mtime_ns.to_le_bytes());
            buf.extend_from_slice(&entry.size.to_le_bytes());
        }

        buf.extend_from_slice(&(index.postings.len() as u32).to_le_bytes());
        for (trigram, postings) in &index.postings {
            buf.extend_from_slice(trigram);
            buf.extend_from_slice(&(postings.len() as u32).to_le_bytes());
            for posting in postings {
                buf.extend_from_slice(&posting.file_id.to_le_bytes());
                buf.extend_from_slice(&posting.line.to_le_bytes());
            }
        }

        buf
    }

    fn write_size_reduction_corpus(dir: &Path, file_count: usize) {
        for file_idx in 0..file_count {
            let mut contents = String::new();
            for line_idx in 0..24 {
                writeln!(
                    &mut contents,
                    "shared needle alpha beta gamma file_{file_idx:04} line_{line_idx:02}"
                )
                .unwrap();
                writeln!(
                    &mut contents,
                    "error repeated payload delta epsilon zeta file_{file_idx:04} line_{line_idx:02}"
                )
                .unwrap();
            }
            write_test_file(dir, &format!("file_{file_idx:04}.txt"), &contents);
        }
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

    // -- Audit #138 item #1: atomic save -----------------------------------------------------

    #[test]
    fn test_save_leaves_no_temp_file_behind_after_success() {
        let dir = tempdir().unwrap();
        write_test_file(dir.path(), "a.txt", "hello world\n");
        let index = TrigramIndex::build(dir.path()).unwrap();
        let index_path = dir.path().join(".tg_index");
        index.save(&index_path).unwrap();

        assert!(index_path.exists());
        let stray_tmp_files: Vec<_> = fs::read_dir(dir.path())
            .unwrap()
            .filter_map(|entry| entry.ok())
            .filter(|entry| entry.file_name().to_string_lossy().contains(".tmp"))
            .collect();
        assert!(
            stray_tmp_files.is_empty(),
            "a successful save must not leave a temp file behind: {stray_tmp_files:?}"
        );
    }

    #[test]
    fn test_save_overwrite_fully_replaces_previous_content_not_a_merge() {
        let dir = tempdir().unwrap();
        write_test_file(dir.path(), "a.txt", "hello world\n");
        let index_path = dir.path().join(".tg_index");

        let first = TrigramIndex::build(dir.path()).unwrap();
        first.save(&index_path).unwrap();
        assert_eq!(TrigramIndex::load(&index_path).unwrap().file_count(), 1);

        write_test_file(dir.path(), "b.txt", "goodbye moon\n");
        let second = TrigramIndex::build(dir.path()).unwrap();
        second.save(&index_path).unwrap();

        let reloaded = TrigramIndex::load(&index_path).unwrap();
        assert_eq!(
            reloaded.file_count(),
            2,
            "the second save must fully replace the destination's content"
        );
    }

    #[test]
    fn atomic_write_bytes_rename_failure_cleans_up_temp_and_returns_err() {
        // Cross-platform deterministic failure injection: renaming a regular file onto a path
        // that is an existing DIRECTORY fails on both POSIX (EISDIR) and Windows -- regardless
        // of the temp file's randomly-generated name, so this does not need to predict it.
        let dir = tempdir().unwrap();
        let path = dir.path().join(".tg_index");
        fs::create_dir(&path).unwrap();

        let result = atomic_write_bytes(&path, b"NEW_CONTENT_MUST_NOT_LAND");
        assert!(
            result.is_err(),
            "rename onto an existing directory must fail"
        );
        assert!(
            path.is_dir(),
            "a failed atomic_write_bytes must not have disturbed the destination"
        );

        let stray_tmp_files: Vec<_> = fs::read_dir(dir.path())
            .unwrap()
            .filter_map(|entry| entry.ok())
            .filter(|entry| entry.file_name().to_string_lossy().contains(".tmp"))
            .collect();
        assert!(
            stray_tmp_files.is_empty(),
            "a failed atomic_write_bytes must clean up its own temp file: {stray_tmp_files:?}"
        );
    }

    #[test]
    fn test_compressed_index_round_trip_preserves_results() {
        let dir = tempdir().unwrap();
        write_test_file(
            dir.path(),
            "a.txt",
            "alpha beta gamma\nerror: something failed\nregex-target-123\n",
        );
        write_test_file(
            dir.path(),
            "b.txt",
            "alpha beta gamma\nwarning: ok\nregex-target-999\n",
        );

        let index = TrigramIndex::build(dir.path()).unwrap();
        let index_path = dir.path().join(".tg_index");
        index.save(&index_path).unwrap();

        let loaded = TrigramIndex::load(&index_path).unwrap();

        let fixed_original = index.search("alpha beta", false, true).unwrap();
        let fixed_loaded = loaded.search("alpha beta", false, true).unwrap();
        assert_eq!(fixed_loaded.len(), fixed_original.len());
        assert_eq!(
            fixed_loaded
                .iter()
                .map(|r| (&r.file, r.line, &r.text))
                .collect::<Vec<_>>(),
            fixed_original
                .iter()
                .map(|r| (&r.file, r.line, &r.text))
                .collect::<Vec<_>>()
        );

        let regex_original = index.search(r"regex-target-\d+", false, false).unwrap();
        let regex_loaded = loaded.search(r"regex-target-\d+", false, false).unwrap();
        assert_eq!(regex_loaded.len(), regex_original.len());
        assert_eq!(
            regex_loaded
                .iter()
                .map(|r| (&r.file, r.line, &r.text))
                .collect::<Vec<_>>(),
            regex_original
                .iter()
                .map(|r| (&r.file, r.line, &r.text))
                .collect::<Vec<_>>()
        );
    }

    #[test]
    fn test_index_staleness_detection() {
        let dir = tempdir().unwrap();
        write_test_file(dir.path(), "a.txt", "hello\n");

        let index = TrigramIndex::build(dir.path()).unwrap();
        assert!(!index.is_stale(false));

        std::thread::sleep(std::time::Duration::from_millis(50));
        write_test_file(dir.path(), "a.txt", "modified\n");
        assert!(index.is_stale(false));
    }

    #[test]
    fn test_index_regex_search() {
        let dir = tempdir().unwrap();
        write_test_file(
            dir.path(),
            "a.txt",
            "error: something failed\nwarning: ok\nerror: again\n",
        );

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
        assert!(
            candidates.is_empty(),
            "patterns shorter than 3 bytes cannot use trigram index"
        );
    }

    #[test]
    fn test_regex_prefilter_literals_cover_alternation_classes_and_unicode() {
        let alternation = select_regex_prefilter_literals(r"(foo|bar)", false).unwrap();
        assert_eq!(alternation.literals, vec![b"bar".to_vec(), b"foo".to_vec()]);

        let char_class = select_regex_prefilter_literals(r"de[ab]f", false).unwrap();
        assert_eq!(
            char_class.literals,
            vec![b"deaf".to_vec(), b"debf".to_vec()]
        );

        let unicode = select_regex_prefilter_literals(r"(東京|大阪)", false).unwrap();
        assert_eq!(
            unicode.literals,
            vec!["大阪".as_bytes().to_vec(), "東京".as_bytes().to_vec()]
        );
    }

    #[test]
    fn test_regex_prefilter_literals_fallback_for_unsafe_patterns() {
        assert!(select_regex_prefilter_literals(r"(foo|ab)", false).is_none());
        assert!(select_regex_prefilter_literals(r"[a-z]{3}", false).is_none());
        assert!(select_regex_prefilter_literals("東京", true).is_none());
    }

    #[test]
    fn test_staleness_detects_content_change() {
        let dir = tempdir().unwrap();
        write_test_file(dir.path(), "a.txt", "hello world\n");
        let index = TrigramIndex::build(dir.path()).unwrap();
        assert!(index.staleness_reason(false).is_none());

        std::thread::sleep(std::time::Duration::from_millis(50));
        write_test_file(dir.path(), "a.txt", "changed content\n");

        let reason = index.staleness_reason(false).unwrap();
        assert!(reason.contains("a.txt"), "reason={reason}");
    }

    #[test]
    fn test_staleness_detects_file_deletion() {
        let dir = tempdir().unwrap();
        write_test_file(dir.path(), "a.txt", "hello\n");
        write_test_file(dir.path(), "b.txt", "world\n");
        let index = TrigramIndex::build(dir.path()).unwrap();

        fs::remove_file(dir.path().join("b.txt")).unwrap();
        let reason = index.staleness_reason(false).unwrap();
        assert!(reason.contains("deleted"), "reason={reason}");
        assert!(reason.contains("b.txt"), "reason={reason}");
    }

    #[test]
    fn test_staleness_detects_new_file() {
        let dir = tempdir().unwrap();
        write_test_file(dir.path(), "a.txt", "hello\n");
        let index = TrigramIndex::build(dir.path()).unwrap();
        assert!(index.staleness_reason(false).is_none());

        write_test_file(dir.path(), "b.txt", "new file\n");
        let reason = index.staleness_reason(false).unwrap();
        assert!(reason.contains("new file"), "reason={reason}");
    }

    #[test]
    fn test_staleness_detects_size_change_same_mtime() {
        let dir = tempdir().unwrap();
        write_test_file(dir.path(), "a.txt", "short\n");
        let index = TrigramIndex::build(dir.path()).unwrap();

        std::thread::sleep(std::time::Duration::from_millis(50));
        write_test_file(
            dir.path(),
            "a.txt",
            "much longer content here to change size\n",
        );
        let reason = index.staleness_reason(false);
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
        assert_eq!(data[4], 4, "format version should be 4");
    }

    #[test]
    fn test_no_ignore_mode_change_is_stale() {
        let dir = tempdir().unwrap();
        write_test_file(dir.path(), "a.txt", "hello\n");

        let index = TrigramIndex::build_with_options(dir.path(), false).unwrap();
        assert!(
            !index.is_stale(false),
            "same no_ignore mode should not be stale"
        );
        assert!(
            index.is_stale(true),
            "a query requesting a different no_ignore mode must be treated as stale"
        );

        let reason = index.staleness_reason(true).unwrap();
        assert!(
            reason.contains("no_ignore"),
            "staleness reason should name the no_ignore mismatch: reason={reason}"
        );
    }

    #[test]
    fn test_compressed_index_is_at_least_40_percent_smaller_than_legacy_format_on_1000_files() {
        let dir = tempdir().unwrap();
        write_size_reduction_corpus(dir.path(), 1000);

        let index = TrigramIndex::build(dir.path()).unwrap();
        let legacy = serialize_legacy_v1(&index);
        let compressed = bincode_serialize(&index).unwrap();

        assert!(
            compressed.len() * 100 <= legacy.len() * 60,
            "expected compressed index to be >= 40% smaller than legacy format; compressed={} legacy={}",
            compressed.len(),
            legacy.len()
        );
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
        assert!(index1.is_stale(false));

        let index2 = TrigramIndex::build(dir.path()).unwrap();
        let r2_hello = index2.search("hello", false, true).unwrap();
        assert!(
            r2_hello.is_empty(),
            "old content should not match after rebuild"
        );
        let r2_goodbye = index2.search("goodbye", false, true).unwrap();
        assert_eq!(r2_goodbye.len(), 1);
    }

    #[test]
    fn test_incremental_update_detects_file_addition_and_reuses_unchanged_files() {
        let dir = tempdir().unwrap();
        write_test_file(dir.path(), "a.txt", "alpha keep\nshared term\n");
        write_test_file(dir.path(), "b.txt", "beta keep\nshared term\n");

        let index = TrigramIndex::build(dir.path()).unwrap();

        std::thread::sleep(std::time::Duration::from_millis(50));
        write_test_file(dir.path(), "c.txt", "gamma addition\nshared term\n");

        let update = index
            .rebuild_incremental_with_options(dir.path(), false)
            .unwrap();
        assert_eq!(update.stats.added_files, 1);
        assert_eq!(update.stats.modified_files, 0);
        assert_eq!(update.stats.deleted_files, 0);
        assert_eq!(update.stats.reused_files, 2);

        let results = update.index.search("gamma addition", false, true).unwrap();
        assert_eq!(results.len(), 1);
        assert!(results[0].file.ends_with("c.txt"));

        let preserved = update.index.search("alpha keep", false, true).unwrap();
        assert_eq!(preserved.len(), 1);
        assert!(preserved[0].file.ends_with("a.txt"));
    }

    #[test]
    fn test_incremental_update_detects_file_removal_and_drops_stale_entries() {
        let dir = tempdir().unwrap();
        write_test_file(dir.path(), "a.txt", "alpha keep\nshared term\n");
        write_test_file(dir.path(), "b.txt", "remove only needle\nshared term\n");

        let index = TrigramIndex::build(dir.path()).unwrap();

        std::thread::sleep(std::time::Duration::from_millis(50));
        fs::remove_file(dir.path().join("b.txt")).unwrap();

        let update = index
            .rebuild_incremental_with_options(dir.path(), false)
            .unwrap();
        assert_eq!(update.stats.added_files, 0);
        assert_eq!(update.stats.modified_files, 0);
        assert_eq!(update.stats.deleted_files, 1);
        assert_eq!(update.stats.reused_files, 1);

        let removed = update
            .index
            .search("remove only needle", false, true)
            .unwrap();
        assert!(
            removed.is_empty(),
            "removed file content should disappear from the index"
        );

        let preserved = update.index.search("alpha keep", false, true).unwrap();
        assert_eq!(preserved.len(), 1);
        assert!(preserved[0].file.ends_with("a.txt"));
    }

    #[test]
    fn test_incremental_update_detects_file_modification_and_reindexes_only_changed_file() {
        let dir = tempdir().unwrap();
        write_test_file(dir.path(), "a.txt", "old needle\nshared term\n");
        write_test_file(dir.path(), "b.txt", "preserved needle\nshared term\n");

        let index = TrigramIndex::build(dir.path()).unwrap();

        std::thread::sleep(std::time::Duration::from_millis(50));
        write_test_file(dir.path(), "a.txt", "new needle\nshared term\n");

        let update = index
            .rebuild_incremental_with_options(dir.path(), false)
            .unwrap();
        assert_eq!(update.stats.added_files, 0);
        assert_eq!(update.stats.modified_files, 1);
        assert_eq!(update.stats.deleted_files, 0);
        assert_eq!(update.stats.reused_files, 1);

        let old_results = update.index.search("old needle", false, true).unwrap();
        assert!(
            old_results.is_empty(),
            "stale postings for modified files should be removed"
        );

        let new_results = update.index.search("new needle", false, true).unwrap();
        assert_eq!(new_results.len(), 1);
        assert!(new_results[0].file.ends_with("a.txt"));

        let preserved = update
            .index
            .search("preserved needle", false, true)
            .unwrap();
        assert_eq!(preserved.len(), 1);
        assert!(preserved[0].file.ends_with("b.txt"));
    }

    #[test]
    fn test_incremental_update_handles_mixed_changes() {
        let dir = tempdir().unwrap();
        write_test_file(dir.path(), "a.txt", "alpha original\nshared term\n");
        write_test_file(dir.path(), "b.txt", "beta remove\nshared term\n");
        write_test_file(dir.path(), "c.txt", "gamma keep\nshared term\n");

        let index = TrigramIndex::build(dir.path()).unwrap();

        std::thread::sleep(std::time::Duration::from_millis(50));
        write_test_file(dir.path(), "a.txt", "alpha updated\nshared term\n");
        fs::remove_file(dir.path().join("b.txt")).unwrap();
        write_test_file(dir.path(), "d.txt", "delta added\nshared term\n");

        let update = index
            .rebuild_incremental_with_options(dir.path(), false)
            .unwrap();
        assert_eq!(update.stats.added_files, 1);
        assert_eq!(update.stats.modified_files, 1);
        assert_eq!(update.stats.deleted_files, 1);
        assert_eq!(update.stats.reused_files, 1);

        assert!(update
            .index
            .search("beta remove", false, true)
            .unwrap()
            .is_empty());

        let updated = update.index.search("alpha updated", false, true).unwrap();
        assert_eq!(updated.len(), 1);
        assert!(updated[0].file.ends_with("a.txt"));

        let added = update.index.search("delta added", false, true).unwrap();
        assert_eq!(added.len(), 1);
        assert!(added[0].file.ends_with("d.txt"));

        let preserved = update.index.search("gamma keep", false, true).unwrap();
        assert_eq!(preserved.len(), 1);
        assert!(preserved[0].file.ends_with("c.txt"));
    }

    // -- #127: index-build silently no-ops a root .gitignore outside a git repo ------------
    //
    // Both index-build WalkBuilders (collect_file_entries + staleness_reason's new-file scan)
    // set `.git_ignore(!no_ignore)` but never called `.add_ignore(..)`. The `ignore` crate only
    // auto-discovers per-directory `.gitignore` files once it has detected an actual git repo
    // (a `.git`/`.jj` marker in some ancestor); outside one, `.git_ignore(true)` alone is a
    // no-op and gitignored files leak into the index. Fix: mirror the sibling `add_ignore` trio
    // already used by `tg search`'s own walkers (main.rs / native_search.rs) -- explicitly
    // added ignore files are honored by the `ignore` crate unconditionally, git repo or not.
    // Deliberately NOT `.require_git(false)`: that would additionally pull in nested/global
    // gitignores outside git, diverging from the root-only add_ignore behavior of `tg search`
    // (BACKLOG #127).

    fn names_of(entries: &[FileEntry]) -> Vec<String> {
        entries
            .iter()
            .map(|e| e.path.file_name().unwrap().to_string_lossy().into_owned())
            .collect()
    }

    #[test]
    fn collect_file_entries_honors_root_gitignore_outside_git_repo() {
        let dir = tempdir().unwrap();
        assert!(
            !dir.path().join(".git").exists(),
            "sanity: a bare tempdir must not already look like a git repo"
        );
        write_test_file(dir.path(), ".gitignore", "ignoreme.py\n");
        write_test_file(dir.path(), "ignoreme.py", "excluded\n");
        write_test_file(dir.path(), "keep.py", "kept\n");

        let names = names_of(&collect_file_entries(dir.path(), false));

        assert!(
            !names.contains(&"ignoreme.py".to_string()),
            "root .gitignore must be honored outside a git repo: names={names:?}"
        );
        assert!(
            names.contains(&"keep.py".to_string()),
            "non-ignored files must still be indexed: names={names:?}"
        );
    }

    #[test]
    fn collect_file_entries_honors_root_gitignore_inside_git_repo() {
        // Positive control: must stay green both before and after the fix. Inside a git repo,
        // .gitignore was already honored via the `ignore` crate's native git-repo
        // auto-discovery. Mirrors the crate's own test-suite idiom of a bare `mkdirp(.git)`
        // marker (dir.rs) rather than a real `git init` -- the crate detects "is a repo" purely
        // by the existence of a `.git`/`.jj` entry, not by its contents.
        let dir = tempdir().unwrap();
        fs::create_dir(dir.path().join(".git")).unwrap();
        write_test_file(dir.path(), ".gitignore", "ignoreme.py\n");
        write_test_file(dir.path(), "ignoreme.py", "excluded\n");
        write_test_file(dir.path(), "keep.py", "kept\n");

        let names = names_of(&collect_file_entries(dir.path(), false));

        assert!(
            !names.contains(&"ignoreme.py".to_string()),
            "root .gitignore must be honored inside a git repo: names={names:?}"
        );
        assert!(
            names.contains(&"keep.py".to_string()),
            "non-ignored files must still be indexed: names={names:?}"
        );
    }

    #[test]
    fn collect_file_entries_no_ignore_still_includes_gitignored_file_outside_git_repo() {
        // --no-ignore must keep overriding gitignore entirely (unchanged behavior) -- the fix
        // must gate the new add_ignore loop on `!no_ignore`, not add it unconditionally.
        let dir = tempdir().unwrap();
        write_test_file(dir.path(), ".gitignore", "ignoreme.py\n");
        write_test_file(dir.path(), "ignoreme.py", "excluded\n");

        let names = names_of(&collect_file_entries(dir.path(), true));

        assert!(
            names.contains(&"ignoreme.py".to_string()),
            "--no-ignore must still include the gitignored file: names={names:?}"
        );
    }

    #[test]
    fn staleness_new_file_scan_honors_root_gitignore_outside_git_repo() {
        // Sibling site: staleness_reason's own WalkBuilder (the new-file scan) must not
        // disagree with collect_file_entries -- a gitignored new file must not be reported as
        // "new" (and therefore must not force a rebuild) outside a git repo either.
        let dir = tempdir().unwrap();
        write_test_file(dir.path(), ".gitignore", "ignoreme.py\n");
        write_test_file(dir.path(), "keep.py", "kept\n");
        let index = TrigramIndex::build_with_options(dir.path(), false).unwrap();
        assert!(index.staleness_reason(false).is_none());

        write_test_file(dir.path(), "ignoreme.py", "should stay invisible\n");
        assert!(
            index.staleness_reason(false).is_none(),
            "a gitignored new file must not trigger staleness outside a git repo"
        );
    }

    #[test]
    fn staleness_new_file_scan_honors_root_gitignore_inside_git_repo() {
        // Positive control for the new-file-scan site: must stay green before and after.
        let dir = tempdir().unwrap();
        fs::create_dir(dir.path().join(".git")).unwrap();
        write_test_file(dir.path(), ".gitignore", "ignoreme.py\n");
        write_test_file(dir.path(), "keep.py", "kept\n");
        let index = TrigramIndex::build_with_options(dir.path(), false).unwrap();
        assert!(index.staleness_reason(false).is_none());

        write_test_file(dir.path(), "ignoreme.py", "should stay invisible\n");
        assert!(
            index.staleness_reason(false).is_none(),
            "a gitignored new file must not trigger staleness inside a git repo either"
        );
    }
}
