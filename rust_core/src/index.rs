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
mod tests {
    use super::*;
    use std::fmt::Write as _;
    use std::fs;
    use tempfile::tempdir;

    fn write_test_file(dir: &Path, name: &str, content: &str) {
        fs::write(dir.join(name), content).unwrap();
    }

    /// Sets a file's modified time to an exact `SystemTime` via std (stable FileTimes API).
    /// Used by the M17 F1 metadata-preserving-swap test to make a swapped-in file
    /// byte-for-byte identical in mtime (and size, by construction) to the indexed original.
    fn set_modified_time(path: &Path, time: SystemTime) {
        let file = fs::OpenOptions::new().write(true).open(path).unwrap();
        file.set_times(std::fs::FileTimes::new().set_modified(time))
            .unwrap();
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
        // M17 gate (audit): 6 adds the tree_fingerprint u64 and drops the build-spelling root
        // byte; an older index fails the version gate and is rebuilt from scratch (safe, by the
        // same rationale the 3->4 no_ignore bump and the 4->5 canonical-root bump used).
        assert_eq!(data[4], 6, "format version should be 6");
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
    fn test_m17_stored_canonical_root_matches_canonicalized_build_root() {
        // M17 (audit-m17): the identity a reuse compares is the CANONICALIZED build root,
        // persisted through save/load. Pre-fix the `canonical_root()` accessor does not
        // exist at all -- this test is a compile-time RED on the old code. Post-fix it
        // pins the canonical identity end to end.
        let dir = tempdir().unwrap();
        write_test_file(dir.path(), "a.txt", "hello world\n");

        let index = TrigramIndex::build(dir.path()).unwrap();
        let canonical = dir.path().canonicalize().unwrap();
        assert_eq!(
            index.canonical_root(),
            canonical.as_path(),
            "the stored identity must be the canonicalized build root"
        );

        // The persisted form round-trips: a reuse decision after load compares against it.
        let index_path = dir.path().join(".tg_index");
        index.save(&index_path).unwrap();
        let loaded = TrigramIndex::load(&index_path).unwrap();
        assert_eq!(
            loaded.canonical_root(),
            canonical.as_path(),
            "canonical root must survive the save/load round trip"
        );

        // A rebuild (incremental path) must re-record the identity of the tree it rebuilt
        // from, or the rebuilt index would refuse the very tree it just built.
        let updated = loaded
            .rebuild_incremental_with_options(dir.path(), false)
            .unwrap();
        assert_eq!(
            updated.index.canonical_root(),
            canonical.as_path(),
            "an incremental rebuild must re-record the canonical root"
        );
    }

    #[test]
    fn test_m17_root_servability_refuses_different_tree_but_serves_same_tree() {
        // M17 (audit-m17) decision seam. Pre-fix `root_servability_reason` does not exist
        // -- the reuse path in main.rs has no root comparison at all, so calling it is a
        // compile-time RED on the old code (the strongest possible failure: the seam is a
        // structural absence). Post-fix:
        //   - a DIFFERENT tree's index must refuse to serve (caller rebuilds);
        //   - the SAME tree via the same spelling, and via the canonicalized spelling (the
        //     aliased-form control), must serve -- canonicalize-vs-canonicalize is what
        //     keeps legitimate re-spellings of one tree from looking like mismatches.
        let tree_a = tempdir().unwrap();
        let tree_b = tempdir().unwrap();
        write_test_file(tree_a.path(), "a.txt", "hello from tree A\n");
        write_test_file(tree_b.path(), "b.txt", "hello from tree B\n");

        let index = TrigramIndex::build(tree_a.path()).unwrap();

        assert!(
            index.root_servability_reason(tree_a.path()).is_none(),
            "same root, same spelling must serve"
        );
        assert!(
            index
                .root_servability_reason(&tree_a.path().canonicalize().unwrap())
                .is_none(),
            "the canonicalized spelling of the SAME tree must still serve (alias control)"
        );

        let reason = index
            .root_servability_reason(tree_b.path())
            .expect("a different tree's index must never serve");
        assert!(
            reason.contains("root mismatch"),
            "the refusal must disclose the rebuild reason: reason={reason}"
        );
    }

    #[test]
    fn test_m17_empty_canonical_root_fails_closed() {
        // M17 (audit-m17): an index without a stored canonical root (the legacy JSON form
        // never persisted one) must refuse to serve rather than guess -- fail-closed
        // toward a rebuild.
        let dir = tempdir().unwrap();
        write_test_file(dir.path(), "a.txt", "hello world\n");
        let mut index = TrigramIndex::build(dir.path()).unwrap();
        index.canonical_root = PathBuf::new(); // same-module field access, test-only reach

        let reason = index
            .root_servability_reason(dir.path())
            .expect("an empty stored canonical root must refuse to serve");
        assert!(
            reason.contains("no stored canonical root"),
            "reason={reason}"
        );
    }

    #[test]
    fn test_m17_f1_tree_fingerprint_detects_metadata_preserving_swap() {
        // M17 F1 (audit-m17 gate): per-file mtime/size checks cannot see a wholesale tree swap
        // at the SAME path whose names/sizes/mtimes are preserved -- the boundary check the
        // gate found missing. This test builds an index, then replaces every file with a
        // SAME-NAME, SAME-SIZE, SAME-MTIME, DIFFERENT-CONTENT version (the metadata-preserving
        // swap) and asserts staleness_reason reports the FINGERPRINT, not a file-level reason.
        //
        // Structural argument: the swap defeats the mtime/size loop BY CONSTRUCTION (equal
        // values), it leaves the file set exactly as indexed (no new/deleted names), so the
        // ONLY remaining detector is `tree_fingerprint`; the replacement content bytes differ,
        // so the SHA-256 digest differs. Pre-fix (gate's F1), no fingerprint existed and the
        // swap read as fresh -- the index served the old postings against the new tree.
        let dir = tempdir().unwrap();
        write_test_file(dir.path(), "a.txt", "hello world\n"); // 12 bytes
        write_test_file(dir.path(), "b.txt", "another line\n"); // 13 bytes
        let index = TrigramIndex::build(dir.path()).unwrap();
        assert!(
            index.staleness_reason(false).is_none(),
            "a fresh index must not be stale"
        );

        let mtime_a = fs::metadata(dir.path().join("a.txt"))
            .unwrap()
            .modified()
            .unwrap();
        let mtime_b = fs::metadata(dir.path().join("b.txt"))
            .unwrap()
            .modified()
            .unwrap();

        fs::remove_file(dir.path().join("a.txt")).unwrap();
        fs::remove_file(dir.path().join("b.txt")).unwrap();
        write_test_file(dir.path(), "a.txt", "swapped out\n"); // 12 bytes, different content
        write_test_file(dir.path(), "b.txt", "fresh sender\n"); // 13 bytes, different content
        set_modified_time(&dir.path().join("a.txt"), mtime_a);
        set_modified_time(&dir.path().join("b.txt"), mtime_b);

        let reason = index
            .staleness_reason(false)
            .expect("the metadata-preserving swap must be detected");
        assert!(
            reason.contains("fingerprint"),
            "the swap must be reported as a tree-identity change: reason={reason}"
        );
        assert!(
            !reason.contains("modified") && !reason.contains("deleted") && !reason.contains("new file"),
            "the per-file checks must not be the detector here (they were defeated by design): reason={reason}"
        );
    }

    #[test]
    fn test_m17_f1_fingerprint_detects_change_beyond_4096_bytes() {
        // M17 F1 (gate round 2): the initial fingerprint sampled only the first 4 KiB of each
        // file, so a same-size/same-mtime edit PAST offset 4096 in a sampled file was
        // invisible to every check (per-file loop sees size/mtime only; the walk sees the
        // same names). With full-content hashing of the sampled files this closes: bytes
        // beyond 4096 are part of the digest.
        //
        // Structural argument: the first 4096 bytes are IDENTICAL (so the old 4 KiB sample
        // digest would have been unchanged -- the exact old evasion), size and mtime are
        // preserved (so the per-file loop passes), the file set is unchanged (so the walk
        // passes) -- only FULL-content hashing can see the tail change.
        let dir = tempdir().unwrap();
        let content_before = format!("{}OLD_TAIL_MARKER", "x".repeat(7000));
        let content_after = format!("{}NEW_TAIL_MARKER", "x".repeat(7000));
        assert_eq!(content_before.len(), content_after.len());

        write_test_file(dir.path(), "big.txt", &content_before);
        let index = TrigramIndex::build(dir.path()).unwrap();
        assert!(
            index.staleness_reason(false).is_none(),
            "a fresh index must not be stale"
        );

        let mtime = fs::metadata(dir.path().join("big.txt"))
            .unwrap()
            .modified()
            .unwrap();
        write_test_file(dir.path(), "big.txt", &content_after);
        set_modified_time(&dir.path().join("big.txt"), mtime);

        let reason = index
            .staleness_reason(false)
            .expect("a change beyond byte 4096 must be detected");
        assert!(
            reason.contains("fingerprint"),
            "the beyond-4096 change must be reported via the tree fingerprint: reason={reason}"
        );
        assert!(
            !reason.contains("modified")
                && !reason.contains("deleted")
                && !reason.contains("new file"),
            "size/mtime/name checks were all preserved by construction: reason={reason}"
        );
    }

    #[test]
    fn test_m17_f1_fingerprint_slots_not_consumed_by_tg_index() {
        // M17 F1 (gate round 2): `.tg_index` must be excluded BEFORE the sampling cap --
        // if it counted toward the 32 sampled slots, an index persisted into a root with
        // exactly 32 other top-level entries would displace one REAL file from the sample,
        // so a change to that displaced file would evade the fingerprint. Structurally:
        // persistence happens after build, so the fingerprint computed at build (no
        // `.tg_index`) would be recomputed at staleness WITH `.tg_index` present; the
        // pre-cap exclusion makes both digest inputs identical.
        let dir = tempdir().unwrap();
        for i in 0..40 {
            write_test_file(dir.path(), &format!("f{i:03}.txt"), "same content\n");
        }
        let index = TrigramIndex::build(dir.path()).unwrap();
        assert!(
            index.staleness_reason(false).is_none(),
            "a fresh 40-file index must not be reported stale by its own persisted index file"
        );

        // Save into the tree, then re-check: the just-written `.tg_index` must not trip the
        // fingerprint (it is filtered before sampling, so the sampled set is unchanged).
        let index_path = dir.path().join(".tg_index");
        index.save(&index_path).unwrap();
        assert!(
            index.staleness_reason(false).is_none(),
            "the persisted .tg_index must not consume a fingerprint slot"
        );
    }

    #[test]
    fn test_m17_f2_fingerprint_ignores_leftover_index_machinery_files() {
        // M17 F2 (gate round 3): the atomic-save temp namespace `..tg_index.<token>.tmp`
        // (`atomic_write_bytes`) and the write-lock file `..tg_index.lock`
        // (`index_lock::lock_path_for`) live in the index's own top-level namespace. A
        // leftover temp (crash between write and rename) or lock (hard crash) persists on
        // disk; WITHOUT exclusion it sorts first (`.` < `f`) and consumes one of the 32
        // sample slots, flipping the digest into a FALSE staleness transition on a healthy
        // tree. With exclusion the sampled set is unchanged.
        //
        // Structural argument: the artifacts are never indexed (so the per-file loop skips
        // them) and are `.`-hidden (so the new-file walk skips them) -- the fingerprint is
        // the ONLY check that could see them, and this test isolates exactly that surface.
        let dir = tempdir().unwrap();
        for i in 0..40 {
            write_test_file(dir.path(), &format!("f{i:03}.txt"), "same content\n");
        }
        let index = TrigramIndex::build(dir.path()).unwrap();
        let digest_before = compute_tree_fingerprint(dir.path(), false);

        // Fixture premise: the leftover artifacts must actually be visible to read_dir.
        // This is a read_dir enumeration, not a walk -- the walk-error-discard ratchet
        // (task #276) counts WALK sites, so use the non-ratcheted binding here to keep the
        // census at the audited walk sites only.
        write_test_file(dir.path(), "..tg_index.deadbeef.tmp", "crash leftover\n");
        write_test_file(dir.path(), "..tg_index.lock", "stale token\n");
        let names: Vec<String> = fs::read_dir(dir.path())
            .unwrap()
            .filter_map(|entry| entry.ok())
            .map(|entry| entry.file_name().to_string_lossy().into_owned())
            .collect();
        assert!(
            names.iter().any(|n| n.starts_with("..tg_index.")),
            "fixture premise: the leftover temp/lock must be present in the top-level listing"
        );

        assert_eq!(
            compute_tree_fingerprint(dir.path(), false),
            digest_before,
            "the leftover index-machinery files must not change the fingerprint digest"
        );
        assert!(
            index.staleness_reason(false).is_none(),
            "a leftover atomic-save temp or lock must not produce a false stale transition"
        );
    }

    #[test]
    fn fingerprint_ignores_top_level_directories() {
        // M17 round-3 (codex audit): the fingerprint must select FILES first -- a top-level
        // directory added after build must neither flip the digest (the walks only ever see
        // files) nor consume one of the 32 sampled slots (which would displace a real file
        // and weaken F1 coverage). The symlink variant is Unix-gated separately below.
        let dir = tempdir().unwrap();
        write_test_file(dir.path(), "keep.txt", "kept\n");
        let index = TrigramIndex::build(dir.path()).unwrap();
        let digest_before = compute_tree_fingerprint(dir.path(), false);

        fs::create_dir(dir.path().join("added_dir")).unwrap();
        assert_eq!(
            compute_tree_fingerprint(dir.path(), false),
            digest_before,
            "an added empty directory must not change the fingerprint"
        );
        assert!(
            index.staleness_reason(false).is_none(),
            "an added empty directory must not trigger staleness"
        );
    }

    #[test]
    fn fingerprint_cap_not_consumed_by_early_sorting_directories() {
        // M17 round-3 (codex audit): 32 directories that sort before the sampled files must
        // not displace every real file from the 32-slot representative sample -- otherwise a
        // metadata-preserving swap in the displaced file would evade the fingerprint.
        // The directories sort FIRST (adir* < zzz_target.txt), so the pre-fix fingerprint
        // (raw path sort + take(32)) would sample the 32 dirs and drop the real file --
        // making this test genuinely RED on the pre-fix code.
        let dir = tempdir().unwrap();
        for i in 0..32 {
            fs::create_dir(dir.path().join(format!("adir{i:02}"))).unwrap();
        }
        write_test_file(dir.path(), "zzz_target.txt", "before\n");
        let index = TrigramIndex::build(dir.path()).unwrap();
        assert!(index.staleness_reason(false).is_none());

        // Metadata-preserving swap on the only real file: same size, same mtime.
        let mtime = fs::metadata(dir.path().join("zzz_target.txt"))
            .unwrap()
            .modified()
            .unwrap();
        write_test_file(dir.path(), "zzz_target.txt", "after!\n"); // 7 bytes, same as "before\n"
        set_modified_time(&dir.path().join("zzz_target.txt"), mtime);

        let reason = index
            .staleness_reason(false)
            .expect("the swap must be detected via the fingerprint");
        assert!(reason.contains("fingerprint"), "reason={reason}");
    }

    #[cfg(unix)]
    #[test]
    fn fingerprint_ignores_top_level_symlink() {
        // M17 round-3 (codex audit): a top-level symlink must not flip the fingerprint (the
        // walks only ever yield files). Unix-gated: creating a symlink needs privileges on
        // Windows CI, so the symlink arm only runs where std::os::unix::fs::symlink exists.
        use std::os::unix::fs::symlink;
        let dir = tempdir().unwrap();
        write_test_file(dir.path(), "keep.txt", "kept\n");
        let index = TrigramIndex::build(dir.path()).unwrap();
        let digest_before = compute_tree_fingerprint(dir.path(), false);

        symlink(dir.path().join("keep.txt"), dir.path().join("link.txt")).unwrap();
        assert_eq!(
            compute_tree_fingerprint(dir.path(), false),
            digest_before,
            "an added top-level symlink must not change the fingerprint"
        );
        assert!(
            index.staleness_reason(false).is_none(),
            "an added top-level symlink must not trigger staleness"
        );
    }

    #[test]
    fn test_m17_f2_entries_relative_and_deref_through_canonical_root() {
        // M17 F2 (audit-m17 gate): entries must be stored canonical-root-RELATIVE and every
        // result path must dereference through the verified canonical root. This is the
        // invariant that makes the cross-cwd escape structurally impossible: nothing in the
        // index is ever a cwd-dependent spelling, so no query process can re-root a stored
        // path at its own working directory.
        //
        // M17 F3 (gate round 2): DEREFERENCE is canonical (asserted below); DISPLAY is a
        // separate contract -- `display_path` re-projects through the QUERY's original
        // spelling so relative queries see relative output (asserted at the end).
        let dir = tempdir().unwrap();
        write_test_file(dir.path(), "a.txt", "hello world\n");
        fs::create_dir(dir.path().join("sub")).unwrap();
        write_test_file(dir.path(), "sub/b.txt", "nested content\n");

        let index = TrigramIndex::build(dir.path()).unwrap();
        let canonical = dir.path().canonicalize().unwrap();

        assert!(
            index.files.iter().all(|entry| entry.path.is_relative()),
            "entries must be stored canonical-root-relative; got {:?}",
            index
                .files
                .iter()
                .map(|e| e.path.display().to_string())
                .collect::<Vec<_>>()
        );
        assert_eq!(
            index.root(),
            index.canonical_root(),
            "the build-spelling root is retired; a built index's root IS its canonical root"
        );

        let results = index.search("hello", false, true).unwrap();
        assert_eq!(results.len(), 1, "deref must find the real file content");
        for result in &results {
            assert!(
                result.file.is_absolute(),
                "search must dereference canonically: {}",
                result.file.display()
            );
            assert!(
                result.file.starts_with(&canonical),
                "search results must be rooted at the canonical root: {} vs {}",
                result.file.display(),
                canonical.display()
            );
        }

        // M17 F3: the DISPLAY projection uses the QUERY's spelling while reads stay
        // canonical -- a query typed as a relative `tree` emits `tree/a.txt`, not the
        // canonical absolute path.
        let displayed = index.display_path(Path::new("tree"), &results[0].file);
        assert!(
            displayed.is_relative(),
            "display must re-project through the query spelling: {}",
            displayed.display()
        );
        assert_eq!(
            displayed,
            Path::new("tree").join("a.txt"),
            "display = query_spelling.join(rel)"
        );
        // The same spelling-but-different-casing/full query form still emits the user's form.
        let displayed_abs = index.display_path(Path::new("TREE"), &results[0].file);
        assert_eq!(
            displayed_abs,
            Path::new("TREE").join("a.txt"),
            "display preserves the caller's spelling even when it differs from canonical"
        );
        // Dereference is untouched by the display projection.
        assert_eq!(
            index.deref_path(Path::new("a.txt")),
            index.canonical_root().join("a.txt")
        );

        // Round trip: a loaded index dereferences identically (no stored spelling survives).
        let index_path = dir.path().join(".tg_index");
        index.save(&index_path).unwrap();
        let loaded = TrigramIndex::load(&index_path).unwrap();
        let loaded_results = loaded.search("hello", false, true).unwrap();
        assert_eq!(loaded_results[0].file, results[0].file);
        assert!(loaded.files.iter().all(|entry| entry.path.is_relative()));
    }

    #[test]
    fn test_m17_f3_uncanonicalizable_query_root_is_unconditional_refusal() {
        // M17 F3 (audit-m17 gate): a query root that cannot be canonicalized must be refused
        // UNCONDITIONALLY -- the earlier raw-spelling fallback comparison would let an
        // unverifiable spelling pass "by coincidence". A nonexistent path cannot be
        // canonicalized on any platform.
        let dir = tempdir().unwrap();
        write_test_file(dir.path(), "a.txt", "hello world\n");
        let index = TrigramIndex::build(dir.path()).unwrap();

        let missing = dir.path().join("does-not-exist");
        let reason = index
            .root_servability_reason(&missing)
            .expect("an uncanonicalizable query root must never serve");
        assert!(
            reason.contains("cannot be canonicalized"),
            "the refusal must name the canonicalize failure: reason={reason}"
        );
    }

    #[test]
    fn test_m17_f4_legacy_json_loaded_index_is_not_searchable() {
        // M17 F4 (audit-m17 gate): `load_json` returns an index with NO verified canonical
        // root; a library consumer must not be able to search it directly, bypassing
        // `root_servability_reason`. The public serving surface refuses with an error.
        let dir = tempdir().unwrap();
        write_test_file(dir.path(), "a.txt", "hello world\n");
        let index = TrigramIndex::build(dir.path()).unwrap();
        let json_path = dir.path().join("legacy.json");
        index.save_json(&json_path).unwrap();
        let legacy = TrigramIndex::load_json(&json_path).unwrap();

        let err = legacy.search("hello", false, true).unwrap_err();
        assert!(
            err.to_string().contains("no verified root"),
            "search must refuse an unverified index: {err}"
        );
        assert!(
            legacy.query_candidates_checked("hello", false).is_err(),
            "the checked candidate surface must refuse an unverified index"
        );
        assert!(
            legacy.query_candidates("hello", false).is_empty(),
            "the legacy compatibility wrapper degrades to an empty candidate set (documented)"
        );
        assert!(
            legacy.root_servability_reason(dir.path()).is_some(),
            "root_servability already refuses the legacy form"
        );
    }

    #[test]
    fn test_m17_f5_non_utf8_canonical_root_fails_load_closed() {
        // M17 F5 (audit-m17 gate): to_string_lossy/from_utf8_lossy collapse DISTINCT non-UTF-8
        // paths into one identity (the alias collision). Build rejects non-UTF-8 roots (the
        // build-side arm; a non-UTF-8 tempdir is not portable to create), and the load side
        // rejects a hand-crafted wire format whose canonical root bytes are invalid UTF-8 --
        // fail closed in both directions, never a lossy identity.
        let dir = tempdir().unwrap();
        let index_path = dir.path().join("crafted.tg_index");
        let (mut buf, root_pos) = craft_v6_index_header(1);
        buf[root_pos] = 0xFF; // invalid UTF-8 canonical root byte
        fs::write(&index_path, &buf).unwrap();
        let err = TrigramIndex::load(&index_path).unwrap_err();
        assert!(
            err.to_string().contains("not valid UTF-8"),
            "a lossy identity must never be accepted: {err}"
        );
    }

    /// Crafts the header of a v6 index file with ONE declared file entry. Returns
    /// `(buffer, canonical_root_bytes_pos)`; the buffer is
    /// magic + version + no_ignore + canonical_root_len + canonical_root("X")
    /// + tree_fingerprint(0) + files_count(1), with the entry payload appended separately.
    fn craft_v6_index_header(canonical_root_len: u32) -> (Vec<u8>, usize) {
        let mut buf = Vec::new();
        buf.extend_from_slice(INDEX_MAGIC);
        buf.push(INDEX_FORMAT_VERSION);
        buf.push(0); // no_ignore
        buf.extend_from_slice(&canonical_root_len.to_le_bytes());
        let root_pos = buf.len();
        buf.extend_from_slice(b"X"); // canonical root placeholder byte
        buf.extend_from_slice(&0u64.to_le_bytes()); // tree_fingerprint = 0
        buf.extend_from_slice(&1u32.to_le_bytes()); // files_count = 1
        (buf, root_pos)
    }

    /// Appends one file entry (path + mtime + size + deleted) to a crafted v6 buffer.
    fn append_crafted_entry(buf: &mut Vec<u8>, path_bytes: &[u8]) {
        buf.extend_from_slice(&(path_bytes.len() as u32).to_le_bytes());
        buf.extend_from_slice(path_bytes);
        buf.extend_from_slice(&0u128.to_le_bytes()); // mtime_ns
        buf.extend_from_slice(&0u64.to_le_bytes()); // size
        buf.push(0); // deleted
    }

    #[test]
    fn test_m17_f2_load_rejects_unconfined_entry_paths() {
        // M17 F2 (gate round 2): loaded entries must be strictly relative and confined --
        // absolute, prefix, and `..` paths must REJECT the whole index so
        // `canonical_root.join(rel)` is provably inside the verified root. The per-entry
        // decode is also STRICT UTF-8 (round-2 F5 extension for entry paths).
        let dir = tempdir().unwrap();
        let index_path = dir.path().join("crafted.tg_index");

        // (a) non-UTF-8 entry name: reject, never a lossy decode.
        let (mut buf, _) = craft_v6_index_header(1);
        append_crafted_entry(&mut buf, &[0xFF]);
        fs::write(&index_path, &buf).unwrap();
        let err = TrigramIndex::load(&index_path).unwrap_err();
        assert!(
            err.to_string().contains("not valid UTF-8"),
            "a non-UTF-8 entry name must reject the index: {err}"
        );

        // (b) absolute/rooted entry path: reject (join would escape the canonical root by root).
        // Note: on Windows `/etc/passwd` is rooted-but-"relative" (no drive prefix), so the
        // refusal can fire on EITHER check -- accept both stable halves of the message.
        let (mut buf, _) = craft_v6_index_header(1);
        append_crafted_entry(&mut buf, b"/etc/passwd");
        fs::write(&index_path, &buf).unwrap();
        let err = TrigramIndex::load(&index_path).unwrap_err().to_string();
        assert!(
            err.contains("not relative") || err.contains("absolute component"),
            "a rooted/absolute entry must reject the index: {err}"
        );

        // (c) `..` escape component: reject.
        let (mut buf, _) = craft_v6_index_header(1);
        append_crafted_entry(&mut buf, b"../outside.txt");
        fs::write(&index_path, &buf).unwrap();
        let err = TrigramIndex::load(&index_path).unwrap_err();
        assert!(
            err.to_string().contains("escapes the canonical root"),
            "a `..` entry must reject the index: {err}"
        );

        // (d) a confined relative entry loads fine (the control arm), with the empty
        // postings section (trigram_count = 0) that the success path requires.
        let (mut buf, _) = craft_v6_index_header(1);
        append_crafted_entry(&mut buf, b"sub/a.txt");
        buf.extend_from_slice(&0u32.to_le_bytes()); // trigram_count = 0
        fs::write(&index_path, &buf).unwrap();
        let loaded = TrigramIndex::load(&index_path).unwrap();
        assert_eq!(loaded.file_count(), 1);
        assert_eq!(loaded.files[0].path, PathBuf::from("sub/a.txt"));
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
