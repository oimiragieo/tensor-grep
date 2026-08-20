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
use sha2::{Digest, Sha256};
use std::collections::HashMap;
use std::fs::File;
use std::io::{self, Write as _};
use std::path::{Path, PathBuf};
use std::time::SystemTime;

const TRIGRAM_LEN: usize = 3;
const MAX_REGEX_CLASS_LITERALS: usize = 10;
const MAX_REGEX_PREFILTER_LITERALS: usize = 64;
/// M17 F1 (gate round 2): how many DIRECT children of the canonical root the tree
/// fingerprint samples. Deliberately bounded in FILE COUNT (32); each sampled file is
/// FULLY content-hashed (the old 4 KiB byte cap let a same-size/same-mtime edit past
/// byte 4096 evade the fingerprint -- closed; see `compute_tree_fingerprint`). The honest
/// remaining boundary is the files NOT sampled (33rd+ top-level files, and non-top-level
/// files, whose per-file loop sees only mtime/size) -- tracked as follow-up M17-FU1.
const TREE_FINGERPRINT_TOP_LEVEL_CAP: usize = 32;

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
    /// Canonical-root-RELATIVE path (audit M17 F2). Never a raw spelling: the build walks
    /// the canonical root and strips it, so re-joining with `canonical_root` is the only
    /// way an entry is ever dereferenced -- a relative spelling stored verbatim would
    /// resolve against the QUERY process's cwd and read a different tree's files while
    /// the canonical-root check passes. UTF-8-validated at build (M17 F5).
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
    /// The spelling of the tree this index was built from. Since audit M17 F2 this is the
    /// CANONICALIZED root (the raw caller spelling is never kept): the build walks the
    /// canonical root, entries are stored canonical-root-relative, and every on-disk
    /// dereference goes through [`Self::canonical_root`]. For loaded indices this equals
    /// `canonical_root` (the wire carries only the canonical form). Display/context only.
    root: PathBuf,
    /// The canonicalized (symlink-resolved, absolute) form of the tree this index was built
    /// from, persisted so a REUSE only serves when the QUERY root canonicalizes to the same
    /// tree (audit M17): a `.tg_index` copied across trees, renamed along with its tree, or
    /// reached through a symlink whose target is elsewhere must never serve the wrong tree's
    /// entries. This is the SOLE dereference base for every stored file entry (M17 F2) and the
    /// only reader is `root_servability_reason` / the deref helper.
    canonical_root: PathBuf,
    /// M17 F1: representative-set identity of the tree (SHA-256 over a bounded sample of the
    /// canonical root's direct children). Compared in `staleness_reason` so a wholesale
    /// same-path metadata-preserving tree swap is detected even when every per-file
    /// name/size/mtime matches. See `compute_tree_fingerprint`.
    tree_fingerprint: u64,
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
        // M17 F2 gate: the JSON form is untrusted input too -- an absolute or `..` entry
        // would break `canonical_root.join(rel)` confinement. Reject before building.
        for entry in &s.files {
            validate_entry_rel_path(&entry.path)?;
        }
        normalize_postings(&mut postings);
        let file_trigrams = rebuild_file_trigrams(s.files.len(), &postings)?;
        Ok(Self {
            root: PathBuf::new(),
            // The legacy JSON format never persisted a root; an empty canonical root marks
            // the index UNVERIFIED: `root_servability_reason` refuses it and the search
            // surface errors (M17 F4) -- it can never serve.
            canonical_root: PathBuf::new(),
            // No persisted identity; the empty canonical root already refuses serving, and
            // staleness would report the fingerprint mismatch on any attempt to reuse it.
            tree_fingerprint: 0,
            files: s.files,
            file_trigrams,
            postings,
            no_ignore: s.no_ignore,
        })
    }
}

const INDEX_MAGIC: &[u8; 4] = b"TGI\x00";
// Bumped 5 -> 6 (audit M17 gate): the wire no longer carries the build-spelling `root` (loaded
// indices carry only the canonical root -- M17 F2), it adds the `tree_fingerprint` u64 (F1),
// and the canonical root is strict UTF-8 in both directions (F5). Any index written by an older
// binary fails the version gate in bincode_deserialize and is rebuilt from scratch by every
// caller of TrigramIndex::load (main.rs's detect_warm_index_state and handle_index_search both
// already treat a load error as "stale, rebuild"), so the bump is safe. Previous bump 4 -> 5
// (audit M17) added the canonical root itself; 3 -> 4 (audit H1d) added the no_ignore byte.
// pub so the wire-format tests (tests/test_index.rs) pin the CURRENT version, not a stale
// hardcoded literal that silently goes wrong on the next bump (A27-twin / provenance rules).
pub const INDEX_FORMAT_VERSION: u8 = 6;

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

    // M17 (audit-m17): the canonical root is the serve identity (F2: entries are stored
    // relative to it; F5: it is UTF-8-validated at build, so a lossless write here never
    // collapses distinct paths). The build-spelling `root` is deliberately NOT persisted --
    // it is display-only, and a loaded index reconstructs it as the canonical root.
    let canonical_root_bytes = index.canonical_root.to_string_lossy().as_bytes().to_vec();
    buf.extend_from_slice(&(canonical_root_bytes.len() as u32).to_le_bytes());
    buf.extend_from_slice(&canonical_root_bytes);

    // M17 F1: representative-set tree identity (u64 digest).
    buf.extend_from_slice(&index.tree_fingerprint.to_le_bytes());

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

    // M17 F5: the canonical root is strict UTF-8 on the read side too -- from_utf8_lossy
    // would collapse distinct non-UTF-8 paths into one identity. Writers only ever persist
    // build-validated UTF-8 roots, so this fails closed on anything else (crafted file,
    // cross-version hand-rolled writer) rather than serving a guessed identity.
    let canonical_root_len = read_u32_le(data, &mut pos)? as usize;
    let canonical_root_bytes = read_exact(data, &mut pos, canonical_root_len)?;
    let canonical_root_str = std::str::from_utf8(canonical_root_bytes)
        .with_context(|| "stored canonical root is not valid UTF-8")?;
    let canonical_root = PathBuf::from(canonical_root_str);

    let tree_fingerprint = read_u64_le(data, &mut pos)?;

    let files_count = read_u32_le(data, &mut pos)? as usize;

    let mut files = Vec::with_capacity(bounded_capacity(files_count, data, pos));
    for _ in 0..files_count {
        let path_len = read_u32_le(data, &mut pos)? as usize;
        // M17 F5 gate: STRICT UTF-8 on entry paths too (from_utf8, never from_utf8_lossy) --
        // a lossy decode collapses distinct non-UTF-8 names into one identity.
        let path_str = std::str::from_utf8(read_exact(data, &mut pos, path_len)?)
            .with_context(|| "index entry path is not valid UTF-8")?;
        let path = PathBuf::from(path_str);
        // M17 F2 gate: reject absolute / prefix / `..` entries on load so
        // `canonical_root.join(rel)` is provably confined to the verified root.
        validate_entry_rel_path(&path)?;
        let mtime_ns = read_u128_le(data, &mut pos)?;
        let size = read_u64_le(data, &mut pos)?;
        let deleted = read_u8(data, &mut pos)? != 0;
        files.push(FileEntry {
            path,
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
        // M17 F2: the wire carries only the canonical root; the display spelling of a
        // loaded index IS its canonical spelling.
        root: canonical_root.clone(),
        canonical_root,
        tree_fingerprint,
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

/// Canonicalization of an index root (audit M17). `Path::canonicalize` resolves symlinks and
/// produces an absolute form, giving every alias of the SAME tree one identity; that identity
/// is what the reuse decision compares.
///
/// M17 F3: this FAILS CLOSED -- a root that cannot be canonicalized is an error, never a raw
/// fallback. The query side (`root_servability_reason`) refuses on the same condition, so an
/// uncanonicalizable path can never match "by raw spelling coincidence".
///
/// M17 F5: the canonical identity must be valid UTF-8 -- the index serializes it losslessly
/// (strict on both read and write), so a non-UTF-8 root is rejected here at BUILD time instead
/// of colliding with a different non-UTF-8 root after a lossy round trip.
fn canonical_root_of(root: &Path) -> Result<PathBuf> {
    let canonical = root.canonicalize().with_context(|| {
        format!(
            "index root cannot be canonicalized (fail closed): {}",
            root.display()
        )
    })?;
    if canonical.to_str().is_none() {
        anyhow::bail!(
            "index root is not valid UTF-8 (fail closed): {}",
            canonical.display()
        );
    }
    Ok(canonical)
}

/// M17 F2 (audit-m17 gate): converts a walked entry path to the canonical-root-RELATIVE form
/// the index persists. Builds walk the canonical root, so every walked path is
/// `canonical_root`-joined by construction and the strip always succeeds; failing closed on an
/// entry that somehow lacks the prefix keeps a bad root from storing an out-of-tree path that
/// a later deref would mis-root. Rel paths are validated UTF-8 here (M17 F5): the lossy
/// serializer would otherwise collapse distinct non-UTF-8 names into one identity -- either
/// aliasing two files or dereferencing a mangled name -- so such trees refuse to be indexed
/// (plain text search is unaffected).
fn relativize_entry(walked: &Path, canonical_root: &Path) -> Result<PathBuf> {
    let rel = walked.strip_prefix(canonical_root).with_context(|| {
        format!(
            "index walk produced a path outside the canonical root: {} (root {})",
            walked.display(),
            canonical_root.display()
        )
    })?;
    let rel = rel.to_path_buf();
    if rel.to_str().is_none() {
        anyhow::bail!(
            "index entry is not valid UTF-8 (fail closed): {}",
            walked.display()
        );
    }
    Ok(rel)
}

/// M17 F2 (gate round 2): a loaded entry path must be STRICTLY RELATIVE and confined --
/// `canonical_root.join(rel)` is only provably inside the verified root when `rel` has no
/// absolute/root/prefix component and no `..` escape. A crafted or corrupt index carrying an
/// absolute or escaping entry must be REJECTED (fail closed), never dereferenced outside the
/// root. An empty path is allowed (a file-rooted index stores its single entry as the empty
/// relative path; join("") is the root itself).
fn validate_entry_rel_path(rel: &Path) -> Result<()> {
    use std::path::Component;
    if !rel.is_relative() {
        anyhow::bail!(
            "index entry path is not relative (fail closed): {}",
            rel.display()
        );
    }
    for component in rel.components() {
        match component {
            Component::ParentDir => {
                anyhow::bail!(
                    "index entry path escapes the canonical root (fail closed): {}",
                    rel.display()
                );
            }
            Component::RootDir | Component::Prefix(_) => {
                anyhow::bail!(
                    "index entry path contains an absolute component (fail closed): {}",
                    rel.display()
                );
            }
            Component::CurDir | Component::Normal(_) => {}
        }
    }
    Ok(())
}

/// M17 F2 (gate round 3): is this top-level entry one the tg INDEX OWNS, i.e. part of the
/// persisted-index machinery rather than the user's tree? These must never be sampled into
/// `compute_tree_fingerprint` -- a leftover artifact would consume a sample slot and flip
/// the digest, producing a FALSE staleness transition on a healthy tree. Covered:
///  - `.tg_index` itself;
///  - the atomic-save temp namespace `..tg_index.<token>.tmp` (`atomic_write_bytes`,
///    index.rs) -- a temp left behind by a crash between write and rename persists on disk;
///  - the write-lock file `..tg_index.lock` (`index_lock::lock_path_for`) -- removed on
///    release, but a hard crash leaves it behind like any lock.
///
/// Both artifacts are also invisible to the per-file loop and the new-file walk (they are
/// never indexed and are `.`-hidden to the walker), so the fingerprint is the only surface
/// that could see them.
fn is_tg_index_owned_entry(name: &std::ffi::OsStr) -> bool {
    name == std::ffi::OsStr::new(".tg_index") || name.to_string_lossy().starts_with("..tg_index.")
}

/// M17 F1 (audit-m17 gate): representative-set identity of the tree this index was built
/// from. SHA-256 over the DIRECT children of the canonical root: each entry's name, and for
/// each file its FULL content, size and mtime, in deterministic (sorted) order. The tg
/// index's OWN top-level namespace is excluded BEFORE the sampling cap (the index's own
/// persistence must never consume one of the sampled slots) -- see
/// `is_tg_index_owned_entry`.
///
/// Round-2 gate: the initial 4 KiB-per-file byte cap let a same-size/same-mtime edit past
/// offset 4096 in a sampled file evade detection, and inode identity would not catch that
/// either -- so this hashes sampled files in FULL (bounded by the 32-file cap, not by
/// bytes). The honest remaining boundary is the files NOT sampled: 33rd+ top-level files
/// and every non-top-level file are covered only by the per-file mtime/size loop -- per-file
/// FULL content identity for those is tracked as follow-up M17-FU1.
///
/// Gate 3a (fingerprint-vs-walk agreement): the fingerprint must sample the SAME top-level
/// file set the index walk would produce, or a gitignored file (added after build) flips
/// the digest and falsely reports staleness -- disagreeing with the new-file walk, which
/// correctly ignores it. The population is therefore derived from `ignore::WalkBuilder`
/// with the SAME config as `collect_file_entries` (hidden=true, git_ignore=!no_ignore,
/// add_ignore trio, capped to depth 1), so agreement holds by construction. The tg index's
/// OWN dot-namespace (.tg_index, ..tg_index.*) is hidden and thus excluded by the same
/// filter the walk applies.
fn compute_tree_fingerprint(canonical_root: &Path, no_ignore: bool) -> u64 {
    let mut hasher = Sha256::new();
    let mut builder = ignore::WalkBuilder::new(canonical_root);
    builder
        .hidden(true)
        .git_ignore(!no_ignore)
        .max_depth(Some(1));
    if !no_ignore {
        for ignore_name in [".ignore", ".gitignore", ".rgignore"] {
            let ignore_path = canonical_root.join(ignore_name);
            if ignore_path.is_file() {
                builder.add_ignore(ignore_path);
            }
        }
    }
    let mut names: Vec<PathBuf> = builder
        .build()
        // Walk errors are LOGGED, not silently dropped (task #276 / the
        // walk-error-discard ratchet): a truncated walk must not read as a clean
        // fingerprint. The discard is deliberate (a stale-fingerprint signal is
        // best-effort; the per-file checks in staleness_reason still fail closed),
        // but it is never silent.
        .filter_map(|entry| {
            entry
                .map_err(|e| eprintln!("tg index: fingerprint walk error: {e}"))
                .ok()
        })
        .filter(|entry| entry.depth() == 1)
        // Select FILES first (matching collect_file_entries and the new-file scan) so a
        // directory or symlink can neither flip the digest nor consume a fingerprint slot
        // before the cap -- otherwise 32 early-sorting directories could displace every
        // real file from the sample (codex audit, M17 round-3).
        .filter(|entry| entry.file_type().is_some_and(|ft| ft.is_file()))
        .map(|entry| entry.into_path())
        .collect();
    // An unreadable root walks nothing -> the digest of the empty set (a constant
    // identity); the per-file checks in staleness_reason still fail closed (they cannot
    // read files either).
    // Deterministic across runs: walk order is unspecified.
    names.sort();
    for entry in names
        .into_iter()
        .filter(|path| !path.file_name().is_some_and(is_tg_index_owned_entry))
        .take(TREE_FINGERPRINT_TOP_LEVEL_CAP)
    {
        if let Some(name) = entry.file_name() {
            hasher.update(name.to_string_lossy().as_bytes());
        }
        let meta = match std::fs::metadata(&entry) {
            Ok(meta) => meta,
            Err(_) => continue,
        };
        if !meta.is_file() {
            continue;
        }
        hasher.update(meta.len().to_le_bytes());
        if let Ok(modified) = meta.modified() {
            if let Ok(duration) = modified.duration_since(SystemTime::UNIX_EPOCH) {
                hasher.update(duration.as_nanos().to_le_bytes());
            }
        }
        // FULL content -- a modification anywhere in a sampled file changes the digest,
        // even when size and mtime are preserved.
        if let Ok(mut file) = File::open(&entry) {
            use std::io::Read as _;
            let mut chunk = [0u8; 64 * 1024];
            loop {
                match file.read(&mut chunk) {
                    Ok(0) => break,
                    Ok(n) => hasher.update(&chunk[..n]),
                    Err(_) => break,
                }
            }
        }
    }
    let digest = hasher.finalize();
    u64::from_le_bytes(
        digest[..8]
            .try_into()
            .expect("SHA-256 digest has >= 8 bytes"),
    )
}

impl TrigramIndex {
    pub fn build(root: &Path) -> Result<Self> {
        Self::build_with_options(root, false)
    }

    pub fn build_with_options(root: &Path, no_ignore: bool) -> Result<Self> {
        // M17 (audit-m17): the walk root is the CANONICALIZED root, never the caller's raw
        // spelling -- a relative spelling would dereference from a LATER query's cwd and
        // walk/read a different tree (F2, the relative-root escape). Entries are stored
        // canonical-root-relative; every read dereferences through the verified canonical
        // root, so a built or loaded index is cwd-independent. F3: canonicalize failure
        // fails the build (no raw fallback); F5: non-UTF-8 roots/entries are rejected.
        let canonical_root = canonical_root_of(root)?;
        let file_entries = collect_file_entries(&canonical_root, no_ignore);

        let file_entries: Vec<FileEntry> = file_entries
            .iter()
            .map(|entry| {
                Ok(FileEntry {
                    path: relativize_entry(&entry.path, &canonical_root)?,
                    mtime_ns: entry.mtime_ns,
                    size: entry.size,
                    deleted: false,
                })
            })
            .collect::<Result<Vec<_>>>()?;

        let per_file: Vec<(u32, FileTrigramHits)> = file_entries
            .par_iter()
            .enumerate()
            .map(|(file_id, entry)| {
                let trigrams =
                    extract_file_trigrams(&canonical_root.join(&entry.path)).unwrap_or_default();
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

        let tree_fingerprint = compute_tree_fingerprint(&canonical_root, no_ignore);

        Ok(Self {
            root: canonical_root.clone(),
            canonical_root,
            tree_fingerprint,
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
        // M17: same canonical-walk discipline as the build (F2) and the same fail-closed
        // canonicalization (F3/F5); the incremental caller is only ever reached after the
        // same-root check, so this root canonicalizes to the stored identity.
        let canonical_root = canonical_root_of(root)?;
        let current_entries = collect_file_entries(&canonical_root, no_ignore);
        let current_entries: Vec<FileEntry> = current_entries
            .iter()
            .map(|entry| {
                Ok(FileEntry {
                    path: relativize_entry(&entry.path, &canonical_root)?,
                    mtime_ns: entry.mtime_ns,
                    size: entry.size,
                    deleted: false,
                })
            })
            .collect::<Result<Vec<_>>>()?;
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
                    extract_file_trigrams(&canonical_root.join(&entry.path)).unwrap_or_default(),
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
                    extract_file_trigrams(&canonical_root.join(&entry.path)).unwrap_or_default(),
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
        // M17: the rebuilt index re-records the canonical identity of the tree it rebuilt
        // from and re-computes the tree fingerprint -- otherwise a subsequent reuse would
        // refuse the tree it just built (root) or falsely report it stale (fingerprint).
        self.root = canonical_root.clone();
        self.canonical_root = canonical_root;
        self.tree_fingerprint = compute_tree_fingerprint(&self.canonical_root, no_ignore);
        // H1d: persist the query's no_ignore mode onto the rebuilt index so a subsequent
        // staleness check compares against what this rebuild actually walked with, not a
        // stale build-time value.
        self.no_ignore = no_ignore;

        Ok(IncrementalUpdateResult { index: self, stats })
    }

    /// CHECKED candidate selection (M17 F4): fails closed with an error when the index has
    /// no verified canonical root (legacy JSON / crafted load) and dereferences every
    /// candidate through the verified canonical root.
    pub fn query_candidates_fixed_checked(
        &self,
        pattern: &str,
        ignore_case: bool,
    ) -> Result<Vec<(PathBuf, usize)>> {
        self.ensure_searchable()?;
        let pat = if ignore_case {
            pattern.to_lowercase()
        } else {
            pattern.to_string()
        };
        Ok(self.query_with_trigrams(&extract_trigrams(pat.as_bytes())))
    }

    /// CHECKED candidate selection (M17 F4) -- see `query_candidates_fixed_checked`.
    pub fn query_candidates_checked(
        &self,
        pattern: &str,
        ignore_case: bool,
    ) -> Result<Vec<(PathBuf, usize)>> {
        self.ensure_searchable()?;
        Ok(
            match self.regex_candidate_selection(pattern, ignore_case)? {
                RegexCandidateSelection::Indexed(candidates) => candidates,
                RegexCandidateSelection::FullScan => Vec::new(),
            },
        )
    }

    /// Legacy compatibility wrapper (law A40 disposition, M17 F4): preserves the pre-M17
    /// `Vec` return shape so downstream rlib consumers outside this repository's census
    /// keep compiling while the checked variants own the fail-closed behavior.
    ///
    /// DELIBERATE API-SHAPE DECISION (A49-style record): `query_candidates*_checked` are
    /// the future of this surface -- an unverified index must be reported, not silently
    /// emptied. This wrapper exists ONLY for the transition; for an unverified index it
    /// returns an empty candidate set (the old code could not express the failure either,
    /// and no candidate set is the honest closest-to-old behavior: nothing is served).
    /// Migration: new consumers should call `query_candidates*_checked` and handle the
    /// error; delete this wrapper in a future breaking release.
    pub fn query_candidates_fixed(
        &self,
        pattern: &str,
        ignore_case: bool,
    ) -> Vec<(PathBuf, usize)> {
        self.query_candidates_fixed_checked(pattern, ignore_case)
            .unwrap_or_default()
    }

    /// Legacy compatibility wrapper -- see `query_candidates_fixed` (law A40 disposition).
    pub fn query_candidates(&self, pattern: &str, ignore_case: bool) -> Vec<(PathBuf, usize)> {
        self.query_candidates_checked(pattern, ignore_case)
            .unwrap_or_default()
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
                // M17 F2: candidates are DEREFERENCED through the verified canonical root --
                // never returned in their stored relative form, which a consumer would
                // resolve against its own cwd.
                (!entry.deleted).then_some((self.deref_path(&entry.path), line as usize))
            })
            .collect()
    }

    pub fn search(
        &self,
        pattern: &str,
        ignore_case: bool,
        fixed_strings: bool,
    ) -> Result<Vec<IndexQueryResult>> {
        self.ensure_searchable()?;
        let candidate_selection = if fixed_strings {
            self.fixed_string_candidate_selection(pattern, ignore_case)?
        } else {
            self.regex_candidate_selection(pattern, ignore_case)?
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
    ) -> Result<RegexCandidateSelection> {
        if ignore_case && !pattern.is_ascii() {
            return Ok(RegexCandidateSelection::FullScan);
        }

        let normalized_len = if ignore_case {
            pattern.to_lowercase().len()
        } else {
            pattern.len()
        };
        if normalized_len < TRIGRAM_LEN {
            return Ok(RegexCandidateSelection::FullScan);
        }

        Ok(RegexCandidateSelection::Indexed(
            self.query_candidates_fixed_checked(pattern, ignore_case)?,
        ))
    }

    fn regex_candidate_selection(
        &self,
        pattern: &str,
        ignore_case: bool,
    ) -> Result<RegexCandidateSelection> {
        let Some(plan) = select_regex_prefilter_literals(pattern, ignore_case) else {
            return Ok(RegexCandidateSelection::FullScan);
        };

        let mut candidates = Vec::new();
        for literal in &plan.literals {
            candidates.extend(self.query_with_trigrams(&extract_trigrams(literal)));
        }

        candidates.sort();
        candidates.dedup();
        Ok(RegexCandidateSelection::Indexed(candidates))
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
            // M17 F2: reads go through the verified canonical root, never a stored spelling.
            .map(|entry| collect_matches(&self.deref_path(&entry.path), None, &matcher))
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
            // M17 F2: per-entry checks dereference through the verified canonical root --
            // a stored (relative) spelling must never be resolved against this process's cwd.
            let absolute = self.deref_path(&entry.path);
            match absolute.metadata() {
                Ok(meta) => {
                    let current_mtime = meta
                        .modified()
                        .ok()
                        .and_then(|t| t.duration_since(SystemTime::UNIX_EPOCH).ok())
                        .map(|d| d.as_nanos())
                        .unwrap_or(0);
                    if current_mtime != entry.mtime_ns {
                        return Some(format!("file modified: {}", absolute.display()));
                    }
                    if meta.len() != entry.size {
                        return Some(format!("file size changed: {}", absolute.display()));
                    }
                }
                Err(_) => {
                    return Some(format!("file deleted: {}", absolute.display()));
                }
            }
        }

        if self.canonical_root.is_dir() {
            // Mirror collect_file_entries' walk semantics exactly -- this was hardcoded
            // .git_ignore(true) regardless of no_ignore (audit H1d), so the new-file scan
            // could disagree with how this index would actually be rebuilt. #127: also mirror
            // collect_file_entries' add_ignore trio so this scan agrees with it outside a git
            // repo too (see that function for the require_git(false) rationale). M17 F2: the
            // scan walks the CANONICAL root (never a stored spelling, which would resolve
            // against the query cwd) and compares relativized paths.
            let mut builder = ignore::WalkBuilder::new(&self.canonical_root);
            builder.hidden(true).git_ignore(!self.no_ignore);
            if !self.no_ignore {
                for ignore_name in [".ignore", ".gitignore", ".rgignore"] {
                    let ignore_path = self.canonical_root.join(ignore_name);
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
                let Ok(rel) = file.strip_prefix(&self.canonical_root) else {
                    continue;
                };
                if !indexed_paths.contains(rel) {
                    return Some(format!("new file: {}", file.display()));
                }
            }
        }

        // M17 F1: representative-set identity. Runs AFTER the per-file loop so a normal
        // modification reports the precise file-level reason; a wholesale same-path swap
        // that preserved every name/size/mtime (and thus passed every check above) is
        // caught here -- the only detector that sees it.
        if self.tree_fingerprint != compute_tree_fingerprint(&self.canonical_root, self.no_ignore) {
            return Some(
                "tree fingerprint changed (representative top-level identity mismatch)".to_string(),
            );
        }

        None
    }

    pub fn root(&self) -> &Path {
        &self.root
    }

    pub fn canonical_root(&self) -> &Path {
        &self.canonical_root
    }

    /// M17 F2 (audit-m17 gate): the ONLY way a stored file entry is turned into an on-disk
    /// path. `canonical_root` has been verified to be the queried tree (or is being rebuilt
    /// from it), so joining keeps every read cwd-independent; results carry canonical
    /// absolute paths.
    fn deref_path(&self, rel: &Path) -> PathBuf {
        self.canonical_root.join(rel)
    }

    /// M17 F3 (gate round 2): DISPLAY projection. `search` dereferences canonically (see
    /// `deref_path`), which is sound but changes the path SPELLING a user sees: a query
    /// typed as `tree/` used to emit `tree/a.txt` and now emits the canonical absolute
    /// path. This re-projects a canonical result path back through the QUERY's original
    /// (non-canonical) spelling for EMISSION ONLY -- `query_spelling.join(rel)` -- so a
    /// relative or differently-spelled query sees its own path space while every READ
    /// stays confined to the verified canonical root. A path that does not project (e.g.
    /// the file-rooted empty-relative case) is emitted as canonical. Dereference and
    /// display are deliberately separate contracts: `deref_path` for I/O, this for output.
    pub fn display_path(&self, query_spelling: &Path, canonical_file: &Path) -> PathBuf {
        match canonical_file.strip_prefix(&self.canonical_root) {
            Ok(rel) if !rel.as_os_str().is_empty() => query_spelling.join(rel),
            _ => canonical_file.to_path_buf(),
        }
    }

    /// M17 F4 (audit-m17 gate): an index without a verified canonical root must not be
    /// searchable. `load_json` (legacy form) and a crafted load produce one with an empty
    /// canonical root; refusing here closes the library-consumer bypass of
    /// `root_servability_reason` -- consumers cannot search an unverified index directly.
    /// The CLI already routes every serve through `root_servability_reason` and rebuilds on
    /// mismatch, so this never fires on any in-tree path.
    fn ensure_searchable(&self) -> Result<()> {
        if self.canonical_root.as_os_str().is_empty() {
            anyhow::bail!(
                "index has no verified root (legacy or unverifiable form); refusing to serve \
                 -- rebuild or verify root_servability first"
            );
        }
        Ok(())
    }

    /// M17 (audit-m17): can this LOADED index serve a query rooted at `query_root`?
    ///
    /// `None` means serving is safe; `Some(reason)` means it must NOT serve -- the caller
    /// rebuilds from the current `query_root` tree instead. The comparison is canonicalize-
    /// vs-canonicalize: `canonical_root` records what `build_with_options` /
    /// `rebuild_incremental_with_options` actually walked (in canonical form), and the query
    /// root is canonicalized the same way, so every alias of the SAME tree still matches
    /// while a `.tg_index` reached from a DIFFERENT tree -- copied in, renamed with its
    /// tree, or resolved through a symlink whose target is elsewhere -- never serves.
    ///
    /// This check must run BEFORE `staleness_reason` / any incremental update: the per-file
    /// identity walk asks the STORED root for its health, i.e. on a mismatched tree it asks
    /// the WRONG tree and can pass against an index that must not be served.
    ///
    /// Fail-closed (M17 F3): an index without a stored canonical root (legacy JSON form) and
    /// a query root that CANNOT BE CANONICALIZED both resolve to a refusal -- there is no
    /// raw-spelling fallback comparison on either side, so nothing can ever pass "by spelling
    /// coincidence" without a verified identity.
    pub fn root_servability_reason(&self, query_root: &Path) -> Option<String> {
        if self.canonical_root.as_os_str().is_empty() {
            return Some(format!(
                "index has no stored canonical root (legacy format); refusing to serve query root {}",
                query_root.display()
            ));
        }
        let query_canonical = match query_root.canonicalize() {
            Ok(canonical) => canonical,
            Err(_) => {
                return Some(format!(
                    "query root cannot be canonicalized: {}; refusing to serve",
                    query_root.display()
                ));
            }
        };
        if query_canonical != self.canonical_root {
            return Some(format!(
                "index root mismatch: index was built for {}, but query root {} resolves to {}",
                self.canonical_root.display(),
                query_root.display(),
                query_canonical.display()
            ));
        }
        None
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
#[path = "index_tests.rs"]
mod tests;
