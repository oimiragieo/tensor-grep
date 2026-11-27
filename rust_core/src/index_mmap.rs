use crate::index::{
    canonical_root_of, compute_tree_fingerprint, extract_trigrams, index_walker, metadata_mtime_ns,
    relativize_entry, select_regex_prefilter_literals, validate_entry_rel_path, FileEntry,
    IndexQueryResult, PostingEntry, TrigramIndex, MAX_INDEXED_FILE_BYTES, TRIGRAM_LEN,
};
use crate::index_cache::open_confined_regular_file;
use anyhow::{Context, Result};
use hmac::{Hmac, Mac};
use ignore::WalkState;
use memmap2::{Mmap, MmapOptions};
use rayon::prelude::*;
use sha2::Sha256;
use std::collections::{HashMap, HashSet};
use std::fs;
use std::io;
use std::path::{Path, PathBuf};
use std::sync::Mutex;

const MMAP_INDEX_MAGIC: &[u8; 4] = b"TGMM";
const MMAP_INDEX_VERSION: u32 = 1;
const MMAP_HEADER_LEN: usize = 80;
const MMAP_HMAC_LEN: usize = 32;
const MMAP_FILE_RECORD_LEN: usize = 40;
const MMAP_TRIGRAM_RECORD_LEN: usize = 16;
const MMAP_HMAC_DOMAIN_KEY: &[u8] = b"tensor-grep-mmap-index-v1";

type HmacSha256 = Hmac<Sha256>;
type IndexQueryOutput = (Vec<IndexQueryResult>, Vec<(PathBuf, u64)>);
type FileQueryMatches = (Vec<IndexQueryResult>, Option<(PathBuf, u64)>);

pub fn mmap_index_path(index_path: &Path) -> PathBuf {
    let mut os = index_path.as_os_str().to_os_string();
    os.push(".mmap");
    PathBuf::from(os)
}

fn align_up_8(offset: usize) -> usize {
    (offset + 7) & !7
}

fn compute_hmac(payload: &[u8]) -> Result<[u8; MMAP_HMAC_LEN]> {
    let mut mac = HmacSha256::new_from_slice(MMAP_HMAC_DOMAIN_KEY)
        .map_err(|_| anyhow::anyhow!("failed to initialize mmap index HMAC"))?;
    mac.update(payload);
    let bytes = mac.finalize().into_bytes();
    let mut out = [0u8; MMAP_HMAC_LEN];
    out.copy_from_slice(&bytes);
    Ok(out)
}

fn verify_hmac(payload: &[u8], tag: &[u8]) -> Result<()> {
    let mut mac = HmacSha256::new_from_slice(MMAP_HMAC_DOMAIN_KEY)
        .map_err(|_| anyhow::anyhow!("failed to initialize mmap index HMAC"))?;
    mac.update(payload);
    mac.verify_slice(tag)
        .map_err(|_| anyhow::anyhow!("mmap index HMAC verification failed"))
}

fn read_u32_at(data: &[u8], offset: usize) -> u32 {
    u32::from_le_bytes([
        data[offset],
        data[offset + 1],
        data[offset + 2],
        data[offset + 3],
    ])
}

fn read_u64_at(data: &[u8], offset: usize) -> u64 {
    u64::from_le_bytes([
        data[offset],
        data[offset + 1],
        data[offset + 2],
        data[offset + 3],
        data[offset + 4],
        data[offset + 5],
        data[offset + 6],
        data[offset + 7],
    ])
}

fn read_u128_at(data: &[u8], offset: usize) -> u128 {
    u128::from_le_bytes([
        data[offset],
        data[offset + 1],
        data[offset + 2],
        data[offset + 3],
        data[offset + 4],
        data[offset + 5],
        data[offset + 6],
        data[offset + 7],
        data[offset + 8],
        data[offset + 9],
        data[offset + 10],
        data[offset + 11],
        data[offset + 12],
        data[offset + 13],
        data[offset + 14],
        data[offset + 15],
    ])
}

#[derive(Debug, Clone)]
struct MmapFileMeta {
    path: PathBuf,
    mtime_ns: u128,
    size: u64,
    deleted: bool,
}

pub struct MmapIndex {
    mmap: Mmap,
    canonical_root: PathBuf,
    tree_fingerprint: u64,
    no_ignore: bool,
    files: Vec<MmapFileMeta>,
    trigram_table_offset: usize,
    trigram_count: usize,
    postings_offset: usize,
    payload_len: usize,
}

impl MmapIndex {
    pub(crate) fn serialize_from_parts(
        canonical_root: &Path,
        tree_fingerprint: u64,
        no_ignore: bool,
        files: &[FileEntry],
        postings: &HashMap<[u8; 3], Vec<PostingEntry>>,
    ) -> Result<Vec<u8>> {
        let canonical_root_str = canonical_root
            .to_str()
            .ok_or_else(|| anyhow::anyhow!("canonical root is not valid UTF-8"))?;
        let canonical_root_bytes = canonical_root_str.as_bytes();

        let mut path_slices: Vec<&[u8]> = Vec::with_capacity(files.len());
        let mut total_path_bytes = 0usize;
        for entry in files {
            let s = entry
                .path
                .to_str()
                .ok_or_else(|| anyhow::anyhow!("index entry path is not valid UTF-8"))?;
            let bytes = s.as_bytes();
            total_path_bytes = total_path_bytes.saturating_add(bytes.len());
            path_slices.push(bytes);
        }

        let mut sorted_trigrams: Vec<([u8; 3], &[PostingEntry])> =
            postings.iter().map(|(k, v)| (*k, v.as_slice())).collect();
        sorted_trigrams.sort_unstable_by_key(|(k, _)| *k);

        let total_postings: usize = sorted_trigrams.iter().map(|(_, v)| v.len()).sum();

        let canonical_root_offset = MMAP_HEADER_LEN;
        let files_offset = align_up_8(canonical_root_offset + canonical_root_bytes.len());
        let file_records_len = files.len() * MMAP_FILE_RECORD_LEN;
        let file_strings_offset = files_offset + file_records_len;
        let trigram_table_offset = align_up_8(file_strings_offset + total_path_bytes);
        let trigram_table_len = sorted_trigrams.len() * MMAP_TRIGRAM_RECORD_LEN;
        let postings_offset = align_up_8(trigram_table_offset + trigram_table_len);
        let payload_len = postings_offset + total_postings * 8;

        let mut buf = vec![0u8; payload_len + MMAP_HMAC_LEN];

        // Header
        buf[0..4].copy_from_slice(MMAP_INDEX_MAGIC);
        buf[4..8].copy_from_slice(&MMAP_INDEX_VERSION.to_le_bytes());
        buf[8..12].copy_from_slice(&u32::from(no_ignore).to_le_bytes());
        buf[12..16].copy_from_slice(&0u32.to_le_bytes());
        buf[16..24].copy_from_slice(&tree_fingerprint.to_le_bytes());
        buf[24..28].copy_from_slice(&(canonical_root_bytes.len() as u32).to_le_bytes());
        buf[28..32].copy_from_slice(&(files.len() as u32).to_le_bytes());
        buf[32..36].copy_from_slice(&(sorted_trigrams.len() as u32).to_le_bytes());
        buf[36..40].copy_from_slice(&0u32.to_le_bytes());
        buf[40..48].copy_from_slice(&(canonical_root_offset as u64).to_le_bytes());
        buf[48..56].copy_from_slice(&(files_offset as u64).to_le_bytes());
        buf[56..64].copy_from_slice(&(trigram_table_offset as u64).to_le_bytes());
        buf[64..72].copy_from_slice(&(postings_offset as u64).to_le_bytes());
        buf[72..80].copy_from_slice(&(payload_len as u64).to_le_bytes());

        // Canonical root
        buf[canonical_root_offset..canonical_root_offset + canonical_root_bytes.len()]
            .copy_from_slice(canonical_root_bytes);

        // File entries + path strings
        let mut str_cursor = file_strings_offset;
        for (idx, (entry, path_bytes)) in files.iter().zip(path_slices.iter()).enumerate() {
            let rec = files_offset + idx * MMAP_FILE_RECORD_LEN;
            buf[rec..rec + 16].copy_from_slice(&entry.mtime_ns.to_le_bytes());
            buf[rec + 16..rec + 24].copy_from_slice(&entry.size.to_le_bytes());
            buf[rec + 24..rec + 32].copy_from_slice(&(str_cursor as u64).to_le_bytes());
            buf[rec + 32..rec + 36].copy_from_slice(&(path_bytes.len() as u32).to_le_bytes());
            buf[rec + 36] = u8::from(entry.deleted);
            buf[str_cursor..str_cursor + path_bytes.len()].copy_from_slice(path_bytes);
            str_cursor += path_bytes.len();
        }

        // Trigram table + sorted (u32, u32) posting lists
        let mut post_cursor = postings_offset;
        for (idx, (trigram, entries)) in sorted_trigrams.iter().enumerate() {
            let rec = trigram_table_offset + idx * MMAP_TRIGRAM_RECORD_LEN;
            buf[rec..rec + 3].copy_from_slice(trigram);
            buf[rec + 3] = 0;
            buf[rec + 4..rec + 8].copy_from_slice(&(entries.len() as u32).to_le_bytes());
            buf[rec + 8..rec + 16].copy_from_slice(&(post_cursor as u64).to_le_bytes());
            for posting in *entries {
                buf[post_cursor..post_cursor + 4].copy_from_slice(&posting.file_id.to_le_bytes());
                buf[post_cursor + 4..post_cursor + 8].copy_from_slice(&posting.line.to_le_bytes());
                post_cursor += 8;
            }
        }

        let tag = compute_hmac(&buf[..payload_len])?;
        buf[payload_len..payload_len + MMAP_HMAC_LEN].copy_from_slice(&tag);
        Ok(buf)
    }

    pub fn save_from_trigram_index_anchored(
        index: &TrigramIndex,
        dir: &crate::safe_write::AnchoredDirectory,
        file_name: &std::ffi::OsStr,
    ) -> Result<()> {
        let (canonical_root, tree_fingerprint, no_ignore, files, postings) = index.mmap_parts();
        let data = Self::serialize_from_parts(
            canonical_root,
            tree_fingerprint,
            no_ignore,
            files,
            postings,
        )?;
        dir.target(file_name)?
            .publish_atomic(&data)
            .map_err(|failure| failure.error)
    }

    pub fn open_confined(path: &Path) -> Result<Self> {
        let file = open_confined_regular_file(path)?;
        Self::from_file(&file)
    }

    pub fn from_file(file: &fs::File) -> Result<Self> {
        const MAX_MMAP_INDEX_BYTES: u64 = 1024 * 1024 * 1024;
        const MAX_MMAP_FILE_COUNT: usize = 10_000_000;
        const MAX_MMAP_TRIGRAM_COUNT: usize = 16_777_216;

        let file_len = file.metadata()?.len();
        if file_len > MAX_MMAP_INDEX_BYTES {
            anyhow::bail!("mmap index file exceeds {MAX_MMAP_INDEX_BYTES} byte limit");
        }
        let mmap = unsafe { MmapOptions::new().map(file)? };
        if mmap.len() < MMAP_HEADER_LEN + MMAP_HMAC_LEN {
            anyhow::bail!("mmap index file is truncated");
        }
        if &mmap[0..4] != MMAP_INDEX_MAGIC {
            anyhow::bail!("invalid mmap index magic");
        }
        let version = read_u32_at(&mmap, 4);
        if version != MMAP_INDEX_VERSION {
            anyhow::bail!("unsupported mmap index version {version}");
        }
        let flags = read_u32_at(&mmap, 8);
        let no_ignore = (flags & 1) != 0;
        let tree_fingerprint = read_u64_at(&mmap, 16);
        let canonical_root_len = read_u32_at(&mmap, 24) as usize;
        let file_count = read_u32_at(&mmap, 28) as usize;
        let trigram_count = read_u32_at(&mmap, 32) as usize;
        let canonical_root_offset = read_u64_at(&mmap, 40) as usize;
        let files_offset = read_u64_at(&mmap, 48) as usize;
        let trigram_table_offset = read_u64_at(&mmap, 56) as usize;
        let postings_offset = read_u64_at(&mmap, 64) as usize;
        let payload_len = read_u64_at(&mmap, 72) as usize;

        if file_count > MAX_MMAP_FILE_COUNT {
            anyhow::bail!("mmap index file_count exceeds limit");
        }
        if trigram_count > MAX_MMAP_TRIGRAM_COUNT {
            anyhow::bail!("mmap index trigram_count exceeds limit");
        }
        if payload_len.checked_add(MMAP_HMAC_LEN) != Some(mmap.len()) {
            anyhow::bail!("mmap index payload length mismatch");
        }
        if trigram_table_offset > postings_offset || postings_offset > payload_len {
            anyhow::bail!("mmap index section offsets out of bounds");
        }
        let trigram_table_end = trigram_table_offset
            .checked_add(
                trigram_count
                    .checked_mul(MMAP_TRIGRAM_RECORD_LEN)
                    .ok_or_else(|| anyhow::anyhow!("mmap index trigram count overflow"))?,
            )
            .filter(|&end| end <= postings_offset)
            .ok_or_else(|| anyhow::anyhow!("mmap index trigram table out of bounds"))?;
        let _ = trigram_table_end;
        verify_hmac(&mmap[..payload_len], &mmap[payload_len..])?;

        let root_end = canonical_root_offset
            .checked_add(canonical_root_len)
            .filter(|&end| end <= payload_len)
            .ok_or_else(|| anyhow::anyhow!("mmap index canonical root out of bounds"))?;
        let canonical_root_str = std::str::from_utf8(&mmap[canonical_root_offset..root_end])
            .context("mmap index canonical root is not valid UTF-8")?;
        let canonical_root = PathBuf::from(canonical_root_str);

        let files_end = files_offset
            .checked_add(
                file_count
                    .checked_mul(MMAP_FILE_RECORD_LEN)
                    .ok_or_else(|| anyhow::anyhow!("mmap index file count overflow"))?,
            )
            .filter(|&end| end <= payload_len)
            .ok_or_else(|| anyhow::anyhow!("mmap index file table out of bounds"))?;
        let _ = files_end;

        let mut files = Vec::with_capacity(file_count);
        for idx in 0..file_count {
            let rec = files_offset + idx * MMAP_FILE_RECORD_LEN;
            let mtime_ns = read_u128_at(&mmap, rec);
            let size = read_u64_at(&mmap, rec + 16);
            let path_off = read_u64_at(&mmap, rec + 24) as usize;
            let path_len = read_u32_at(&mmap, rec + 32) as usize;
            let deleted = mmap[rec + 36] != 0;
            let path_end = path_off
                .checked_add(path_len)
                .filter(|&end| end <= trigram_table_offset && end <= payload_len)
                .ok_or_else(|| anyhow::anyhow!("mmap index file path out of bounds"))?;
            let path_str = std::str::from_utf8(&mmap[path_off..path_end])
                .context("mmap index entry path is not valid UTF-8")?;
            let rel = PathBuf::from(path_str);
            validate_entry_rel_path(&rel)?;
            files.push(MmapFileMeta {
                path: rel,
                mtime_ns,
                size,
                deleted,
            });
        }

        let trigram_table_end = trigram_table_offset
            .checked_add(
                trigram_count
                    .checked_mul(MMAP_TRIGRAM_RECORD_LEN)
                    .ok_or_else(|| anyhow::anyhow!("mmap index trigram count overflow"))?,
            )
            .filter(|&end| end <= postings_offset && end <= payload_len)
            .ok_or_else(|| anyhow::anyhow!("mmap index trigram table out of bounds"))?;
        let _ = trigram_table_end;

        Ok(Self {
            mmap,
            canonical_root,
            tree_fingerprint,
            no_ignore,
            files,
            trigram_table_offset,
            trigram_count,
            postings_offset,
            payload_len,
        })
    }

    pub fn file_count(&self) -> usize {
        self.files.iter().filter(|e| !e.deleted).count()
    }

    pub fn trigram_count(&self) -> usize {
        self.trigram_count
    }

    pub fn display_path(&self, query_spelling: &Path, canonical_file: &Path) -> PathBuf {
        let Ok(rel) = canonical_file.strip_prefix(&self.canonical_root) else {
            return canonical_file.to_path_buf();
        };
        if rel.as_os_str().is_empty() {
            return query_spelling.to_path_buf();
        }
        if query_spelling == Path::new(".") {
            return rel.to_path_buf();
        }
        query_spelling.join(rel)
    }

    pub fn root_servability_reason(&self, query_root: &Path) -> Option<String> {
        let query_canonical = match canonical_root_of(query_root) {
            Ok(canonical) => canonical,
            Err(err) => return Some(format!("query root cannot be canonicalized: {err:#}")),
        };
        if query_canonical != self.canonical_root {
            return Some(format!(
                "index root mismatch: index built for {}, query targets {}",
                self.canonical_root.display(),
                query_canonical.display()
            ));
        }
        None
    }

    /// Parallel staleness check: parallel stat() across indexed files + parallel WalkBuilder for new files.
    pub fn staleness_reason(&self, no_ignore: bool) -> Option<String> {
        if self.no_ignore != no_ignore {
            return Some(format!(
                "no_ignore mode changed: index built with no_ignore={}, query requested no_ignore={}",
                self.no_ignore, no_ignore
            ));
        }

        let stale_file = self.files.par_iter().find_map_any(|entry| {
            if entry.deleted {
                return None;
            }
            let absolute = self.canonical_root.join(&entry.path);
            let meta = match absolute.metadata() {
                Ok(meta) => meta,
                Err(error) if error.kind() == io::ErrorKind::NotFound => {
                    return Some(format!("file deleted: {}", absolute.display()));
                }
                Err(error) => {
                    return Some(format!(
                        "failed to read indexed file metadata for {}: {error}",
                        absolute.display()
                    ));
                }
            };
            let current_mtime = match metadata_mtime_ns(&meta, &absolute) {
                Ok(mtime) => mtime,
                Err(error) => return Some(format!("{error:#}")),
            };
            if current_mtime != entry.mtime_ns {
                return Some(format!("file modified: {}", absolute.display()));
            }
            if meta.len() != entry.size {
                return Some(format!("file size changed: {}", absolute.display()));
            }
            None
        });
        if stale_file.is_some() {
            return stale_file;
        }

        if self.canonical_root.is_dir() {
            let indexed_paths: HashSet<&Path> = self
                .files
                .iter()
                .filter(|entry| !entry.deleted)
                .map(|entry| entry.path.as_path())
                .collect();
            let new_file_err: Mutex<Option<String>> = Mutex::new(None);
            let mut builder = index_walker(&self.canonical_root, self.no_ignore);
            builder.threads(0);
            builder.build_parallel().run(|| {
                let indexed_paths = &indexed_paths;
                let new_file_err = &new_file_err;
                let canonical_root = &self.canonical_root;
                Box::new(move |entry| {
                    if new_file_err.lock().is_ok_and(|g| g.is_some()) {
                        return WalkState::Quit;
                    }
                    let entry = match entry {
                        Ok(entry) => entry,
                        Err(err) => {
                            if let Ok(mut guard) = new_file_err.lock() {
                                *guard = Some(format!("index traversal failed: {err}"));
                            }
                            return WalkState::Quit;
                        }
                    };
                    if !entry.file_type().is_some_and(|ft| ft.is_file()) {
                        return WalkState::Continue;
                    }
                    let path = entry.path();
                    match relativize_entry(path, canonical_root) {
                        Ok(rel) => {
                            if !indexed_paths.contains(rel.as_path()) {
                                if let Ok(mut guard) = new_file_err.lock() {
                                    *guard = Some(format!("new file: {}", path.display()));
                                }
                                return WalkState::Quit;
                            }
                        }
                        Err(error) => {
                            if let Ok(mut guard) = new_file_err.lock() {
                                *guard = Some(format!("index entry path invalid: {error:#}"));
                            }
                            return WalkState::Quit;
                        }
                    }
                    WalkState::Continue
                })
            });
            if let Ok(mut guard) = new_file_err.lock() {
                if let Some(reason) = guard.take() {
                    return Some(reason);
                }
            };
        }

        match compute_tree_fingerprint(&self.canonical_root, self.no_ignore) {
            Ok(fingerprint) if self.tree_fingerprint != fingerprint => {
                return Some(
                    "tree fingerprint changed (representative top-level identity mismatch)"
                        .to_string(),
                );
            }
            Err(error) => return Some(format!("tree fingerprint failed: {error:#}")),
            _ => {}
        }

        None
    }

    pub fn is_stale(&self, no_ignore: bool) -> bool {
        self.staleness_reason(no_ignore).is_some()
    }

    fn lookup_trigram(&self, trigram: [u8; 3]) -> Option<(usize, usize)> {
        let mut lo = 0usize;
        let mut hi = self.trigram_count;
        while lo < hi {
            let mid = lo + (hi - lo) / 2;
            let rec = self.trigram_table_offset + mid * MMAP_TRIGRAM_RECORD_LEN;
            let mid_tri = [self.mmap[rec], self.mmap[rec + 1], self.mmap[rec + 2]];
            match mid_tri.cmp(&trigram) {
                std::cmp::Ordering::Less => lo = mid + 1,
                std::cmp::Ordering::Greater => hi = mid,
                std::cmp::Ordering::Equal => {
                    let count = read_u32_at(&self.mmap, rec + 4) as usize;
                    let offset = read_u64_at(&self.mmap, rec + 8) as usize;
                    let byte_len = count.checked_mul(8)?;
                    if offset < self.postings_offset
                        || offset
                            .checked_add(byte_len)
                            .is_none_or(|end| end > self.payload_len)
                    {
                        return None;
                    }
                    return Some((offset, count));
                }
            }
        }
        None
    }

    #[inline]
    fn posting_at(&self, base_offset: usize, idx: usize) -> (u32, u32) {
        let off = base_offset + idx * 8;
        (
            read_u32_at(&self.mmap, off),
            read_u32_at(&self.mmap, off + 4),
        )
    }

    /// Galloping binary-search intersection over sorted `(u32, u32)` mmap posting lists,
    /// ordered from rarest (smallest posting_count) to most common (REI selective n-gram pattern).
    fn query_with_trigrams(&self, trigrams: &[[u8; 3]]) -> Vec<(PathBuf, usize)> {
        if trigrams.is_empty() {
            return Vec::new();
        }

        let mut lists: Vec<(usize, usize)> = Vec::with_capacity(trigrams.len());
        for &tri in trigrams {
            match self.lookup_trigram(tri) {
                Some((offset, count)) if count > 0 => lists.push((offset, count)),
                _ => return Vec::new(),
            }
        }

        // Selective n-gram filtering: rarest trigram first
        lists.sort_unstable_by_key(|&(_, count)| count);

        let (first_off, first_count) = lists[0];
        let max_possible = (self.payload_len.saturating_sub(first_off)) / 8;
        let bounded_count = first_count.min(max_possible);
        let mut candidates: Vec<(u32, u32)> = Vec::with_capacity(bounded_count);
        for i in 0..bounded_count {
            candidates.push(self.posting_at(first_off, i));
        }

        for &(list_off, list_count) in &lists[1..] {
            if candidates.is_empty() {
                break;
            }
            let mut next = Vec::with_capacity(candidates.len().min(list_count));
            let mut cursor = 0usize;
            for &(fid, line) in &candidates {
                let target = (fid, line);
                // Galloping search from `cursor`
                let mut step = 1usize;
                let mut prev = cursor;
                while prev < list_count && self.posting_at(list_off, prev) < target {
                    cursor = prev;
                    let next_idx = prev + step;
                    if next_idx >= list_count {
                        prev = list_count;
                        break;
                    }
                    prev = next_idx;
                    step <<= 1;
                }
                let mut lo = cursor;
                let mut hi = prev.saturating_add(1).min(list_count);
                while lo < hi {
                    let mid = lo + (hi - lo) / 2;
                    match self.posting_at(list_off, mid).cmp(&target) {
                        std::cmp::Ordering::Less => lo = mid + 1,
                        std::cmp::Ordering::Greater => hi = mid,
                        std::cmp::Ordering::Equal => {
                            next.push(target);
                            lo = mid + 1;
                            break;
                        }
                    }
                }
                cursor = lo;
            }
            candidates = next;
        }

        candidates.dedup();
        candidates
            .into_iter()
            .filter_map(|(file_id, line)| {
                let entry = self.files.get(file_id as usize)?;
                (!entry.deleted).then_some((self.canonical_root.join(&entry.path), line as usize))
            })
            .collect()
    }

    pub fn search_with_binary_matches(
        &self,
        pattern: &str,
        ignore_case: bool,
        fixed_strings: bool,
    ) -> Result<IndexQueryOutput> {
        let mut work: HashMap<PathBuf, Option<Vec<usize>>> = HashMap::new();
        let full_scan = if fixed_strings {
            if ignore_case && !pattern.is_ascii() {
                true
            } else {
                let normalized = if ignore_case {
                    pattern.to_lowercase()
                } else {
                    pattern.to_string()
                };
                if normalized.len() < TRIGRAM_LEN {
                    true
                } else {
                    for (path, line) in
                        self.query_with_trigrams(&extract_trigrams(normalized.as_bytes()))
                    {
                        work.entry(path)
                            .and_modify(|v| {
                                if let Some(lines) = v {
                                    lines.push(line);
                                }
                            })
                            .or_insert_with(|| Some(vec![line]));
                    }
                    false
                }
            }
        } else if let Some(plan) = select_regex_prefilter_literals(pattern, ignore_case) {
            for literal in &plan.literals {
                for (path, line) in self.query_with_trigrams(&extract_trigrams(literal)) {
                    work.entry(path)
                        .and_modify(|v| {
                            if let Some(lines) = v {
                                lines.push(line);
                            }
                        })
                        .or_insert_with(|| Some(vec![line]));
                }
            }
            false
        } else {
            true
        };

        if full_scan {
            for entry in self.files.iter().filter(|entry| !entry.deleted) {
                work.insert(self.canonical_root.join(&entry.path), None);
            }
        } else {
            for entry in self
                .files
                .iter()
                .filter(|entry| !entry.deleted && entry.size > MAX_INDEXED_FILE_BYTES)
            {
                work.insert(self.canonical_root.join(&entry.path), None);
            }
        }

        let matcher = crate::index::query::SearchMatcher::new(pattern, ignore_case, fixed_strings)?;
        let binary_matcher =
            crate::native_search::index_binary_matcher(pattern, ignore_case, fixed_strings);
        let mut files: Vec<_> = work.into_iter().collect();
        files.sort_by(|left, right| left.0.cmp(&right.0));
        let scanned: Vec<Result<FileQueryMatches>> = files
            .par_iter()
            .map(|(path, lines)| {
                crate::index::query::collect_matches(
                    path,
                    lines.as_deref(),
                    &matcher,
                    &binary_matcher,
                )
            })
            .collect();

        let mut matches = Vec::new();
        let mut binary_matches = Vec::new();
        for result in scanned {
            let (file_matches, binary_match) = result?;
            matches.extend(file_matches);
            binary_matches.extend(binary_match);
        }
        matches.sort_by(|left, right| left.file.cmp(&right.file).then(left.line.cmp(&right.line)));
        binary_matches.sort();
        binary_matches.dedup_by(|left, right| left.0 == right.0);
        Ok((matches, binary_matches))
    }
}

pub enum LoadedIndex {
    Mmap(MmapIndex),
    Memory(TrigramIndex),
}

impl LoadedIndex {
    pub fn load_for_search(index_path: &Path) -> Result<Self> {
        let mmap_path = mmap_index_path(index_path);
        if crate::index_cache::confined_regular_file_exists(&mmap_path) {
            if let Ok(mmap_idx) = MmapIndex::open_confined(&mmap_path) {
                return Ok(Self::Mmap(mmap_idx));
            }
        }
        TrigramIndex::load(index_path).map(Self::Memory)
    }

    pub fn file_count(&self) -> usize {
        match self {
            Self::Mmap(idx) => idx.file_count(),
            Self::Memory(idx) => idx.file_count(),
        }
    }

    pub fn trigram_count(&self) -> usize {
        match self {
            Self::Mmap(idx) => idx.trigram_count(),
            Self::Memory(idx) => idx.trigram_count(),
        }
    }

    pub fn display_path(&self, query_spelling: &Path, canonical_file: &Path) -> PathBuf {
        match self {
            Self::Mmap(idx) => idx.display_path(query_spelling, canonical_file),
            Self::Memory(idx) => idx.display_path(query_spelling, canonical_file),
        }
    }

    pub fn root_servability_reason(&self, query_root: &Path) -> Option<String> {
        match self {
            Self::Mmap(idx) => idx.root_servability_reason(query_root),
            Self::Memory(idx) => idx.root_servability_reason(query_root),
        }
    }

    pub fn staleness_reason(&self, no_ignore: bool) -> Option<String> {
        match self {
            Self::Mmap(idx) => idx.staleness_reason(no_ignore),
            Self::Memory(idx) => idx.staleness_reason(no_ignore),
        }
    }

    pub fn is_stale(&self, no_ignore: bool) -> bool {
        match self {
            Self::Mmap(idx) => idx.is_stale(no_ignore),
            Self::Memory(idx) => idx.is_stale(no_ignore),
        }
    }

    pub fn search_with_binary_matches(
        &self,
        pattern: &str,
        ignore_case: bool,
        fixed_strings: bool,
    ) -> Result<IndexQueryOutput> {
        match self {
            Self::Mmap(idx) => idx.search_with_binary_matches(pattern, ignore_case, fixed_strings),
            Self::Memory(idx) => {
                idx.search_with_binary_matches(pattern, ignore_case, fixed_strings)
            }
        }
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use std::time::Instant;
    use tempfile::tempdir;

    #[test]
    fn index_mmap_perf() {
        let dir = tempdir().unwrap();
        let root = dir.path();
        for i in 0..1834 {
            let sub = root.join(format!("pkg_{:02}", i % 20));
            fs::create_dir_all(&sub).unwrap();
            let content = if i == 42 || i == 999 || i == 1500 {
                format!("fn target_needle_{i}() {{\n    let x = \"REI_MMAP_BENCH_NEEDLE\";\n}}\n")
            } else {
                format!(
                    "fn helper_{i}() {{\n    let value = {i};\n    println!(\"common_prefix_{{value}}\");\n}}\n"
                )
            };
            fs::write(sub.join(format!("file_{i:04}.rs")), content).unwrap();
        }

        let trigram_index = TrigramIndex::build(root).unwrap();
        let mmap_path = root.join("test.tg_index.mmap");
        let (croot, fp, no_ignore, files, postings) = trigram_index.mmap_parts();
        let bytes = MmapIndex::serialize_from_parts(croot, fp, no_ignore, files, postings).unwrap();
        fs::write(&mmap_path, &bytes).unwrap();

        let t0 = Instant::now();
        let mmap_index = MmapIndex::open_confined(&mmap_path).unwrap();
        let (mmap_matches, _) = mmap_index
            .search_with_binary_matches("REI_MMAP_BENCH_NEEDLE", false, true)
            .unwrap();
        let warm_elapsed = t0.elapsed();

        let (tri_matches, _) = trigram_index
            .search_with_binary_matches("REI_MMAP_BENCH_NEEDLE", false, true)
            .unwrap();

        assert_eq!(mmap_matches.len(), 3);
        assert_eq!(mmap_matches.len(), tri_matches.len());
        for (a, b) in mmap_matches.iter().zip(tri_matches.iter()) {
            assert_eq!(a.file, b.file);
            assert_eq!(a.line, b.line);
            assert_eq!(a.text, b.text);
        }
        assert!(
            warm_elapsed.as_secs_f64() < 0.15,
            "warm mmap index query took {:?}, expected < 0.15s",
            warm_elapsed
        );
    }

    #[test]
    fn index_mmap_integrity_rejects_tampered_postings() {
        let dir = tempdir().unwrap();
        let root = dir.path();
        fs::write(root.join("a.rs"), "fn alpha_needle() {}\n").unwrap();
        let trigram_index = TrigramIndex::build(root).unwrap();
        let mmap_path = root.join("tamper.tg_index.mmap");
        let (croot, fp, no_ignore, files, postings) = trigram_index.mmap_parts();
        let mut bytes =
            MmapIndex::serialize_from_parts(croot, fp, no_ignore, files, postings).unwrap();
        let trigram_off = read_u64_at(&bytes, 56) as usize;
        bytes[trigram_off] ^= 0xFF;
        fs::write(&mmap_path, &bytes).unwrap();
        assert!(MmapIndex::open_confined(&mmap_path).is_err());
    }

    #[test]
    fn index_mmap_rejects_oversized_file_count() {
        let dir = tempdir().unwrap();
        let root = dir.path();
        fs::write(root.join("a.rs"), "fn alpha_needle() {}\n").unwrap();
        let trigram_index = TrigramIndex::build(root).unwrap();
        let mmap_path = root.join("oversized.tg_index.mmap");
        let (croot, fp, no_ignore, files, postings) = trigram_index.mmap_parts();
        let mut bytes =
            MmapIndex::serialize_from_parts(croot, fp, no_ignore, files, postings).unwrap();
        bytes[28..32].copy_from_slice(&(u32::MAX).to_le_bytes());
        fs::write(&mmap_path, &bytes).unwrap();
        let err = match MmapIndex::open_confined(&mmap_path) {
            Ok(_) => panic!("expected file_count limit error"),
            Err(e) => e,
        };
        assert!(
            err.to_string().contains("file_count exceeds limit"),
            "unexpected error: {err:#}"
        );
    }
}
