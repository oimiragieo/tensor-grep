#![allow(unused_imports)]
use super::*;

pub(super) fn normalize_postings(postings: &mut HashMap<[u8; 3], Vec<PostingEntry>>) {
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

pub(super) fn bincode_serialize(index: &TrigramIndex) -> Result<Vec<u8>> {
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

pub(super) fn bincode_deserialize(data: &[u8]) -> Result<TrigramIndex> {
    let mut budget = DecodeBudget::default();
    bincode_deserialize_with_budget(data, &mut budget)
}

pub(super) fn bincode_deserialize_with_budget(
    data: &[u8],
    budget: &mut DecodeBudget,
) -> Result<TrigramIndex> {
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
    budget.charge(
        canonical_root_str.len().saturating_add(1).saturating_mul(4),
        "canonical root paths",
    )?;
    let canonical_root = PathBuf::from(canonical_root_str);

    let tree_fingerprint = read_u64_le(data, &mut pos)?;

    let files_count = read_u32_le(data, &mut pos)? as usize;
    bounded_json::ensure_count_fits(files_count, 29, data, pos, "file entries")?;

    let mut files = Vec::new();
    budget.reserve_vec(&mut files, files_count, "binary file entries")?;
    for _ in 0..files_count {
        let path_len = read_u32_le(data, &mut pos)? as usize;
        // M17 F5 gate: STRICT UTF-8 on entry paths too (from_utf8, never from_utf8_lossy) --
        // a lossy decode collapses distinct non-UTF-8 names into one identity.
        let path_str = std::str::from_utf8(read_exact(data, &mut pos, path_len)?)
            .with_context(|| "index entry path is not valid UTF-8")?;
        budget.charge(
            path_str.len().saturating_add(1).saturating_mul(2),
            "binary index paths",
        )?;
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
    bounded_json::ensure_count_fits(trigram_count, 7, data, pos, "trigram entries")?;

    let mut postings = HashMap::new();
    for _ in 0..trigram_count {
        let trigram: [u8; 3] = read_exact(data, &mut pos, 3)?.try_into()?;
        if postings.contains_key(&trigram) {
            anyhow::bail!("duplicate trigram entry in binary index");
        }
        let posting_count = read_u32_le(data, &mut pos)? as usize;
        bounded_json::ensure_count_fits(posting_count, 2, data, pos, "posting entries")?;
        let mut entries = Vec::new();
        budget.reserve_vec(&mut entries, posting_count, "binary posting entries")?;
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
        budget.reserve_hash_entry(&mut postings, "binary trigram map")?;
        postings.insert(trigram, entries);
    }

    if pos != data.len() {
        anyhow::bail!("index file contains trailing data");
    }

    let file_trigrams = rebuild_file_trigrams(files.len(), &postings, budget)?;

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
