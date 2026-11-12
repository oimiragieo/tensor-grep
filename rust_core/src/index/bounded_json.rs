use super::{
    FileEntry, PostingEntry, SerializableIndex, TrigramIndex, MAX_INDEX_DECODED_ALLOCATION_BYTES,
    MAX_INDEX_INPUT_BYTES,
};
use anyhow::{Context, Result};
use serde::de::{self, DeserializeSeed, IgnoredAny, MapAccess, SeqAccess, Visitor};
use serde::Deserialize;
use std::collections::HashMap;
use std::fs::File;
use std::io::{self, Read};
use std::path::{Path, PathBuf};
#[derive(Default)]
pub(super) struct DecodeBudget {
    charged_bytes: usize,
}

impl DecodeBudget {
    pub(super) fn charge(&mut self, bytes: usize, purpose: &str) -> Result<()> {
        let total = self
            .charged_bytes
            .checked_add(bytes)
            .ok_or_else(|| anyhow::anyhow!("decoded index allocation size overflow"))?;
        if total > MAX_INDEX_DECODED_ALLOCATION_BYTES {
            anyhow::bail!(
                "decoded index allocation budget exceeded while reserving {purpose} (limit: {} bytes)",
                MAX_INDEX_DECODED_ALLOCATION_BYTES
            );
        }
        self.charged_bytes = total;
        Ok(())
    }

    pub(super) fn release_bytes(&mut self, bytes: usize) {
        self.charged_bytes = self.charged_bytes.saturating_sub(bytes);
    }

    fn hash_map_bytes<K, V>(&self, capacity: usize) -> Result<usize> {
        capacity
            .checked_mul(
                std::mem::size_of::<(K, V)>()
                    .checked_mul(2)
                    .and_then(|bytes| bytes.checked_add(16))
                    .ok_or_else(|| anyhow::anyhow!("decoded index allocation size overflow"))?,
            )
            .ok_or_else(|| anyhow::anyhow!("decoded index allocation size overflow"))
    }

    pub(super) fn reserve_vec<T>(
        &mut self,
        values: &mut Vec<T>,
        capacity: usize,
        purpose: &str,
    ) -> Result<()> {
        if capacity <= values.capacity() {
            return Ok(());
        }
        let old_capacity_bytes = values
            .capacity()
            .checked_mul(std::mem::size_of::<T>())
            .ok_or_else(|| anyhow::anyhow!("decoded index allocation size overflow"))?;
        let requested_bytes = capacity
            .checked_mul(std::mem::size_of::<T>())
            .ok_or_else(|| anyhow::anyhow!("decoded index allocation size overflow"))?;
        self.charge(requested_bytes, purpose)?;
        if let Err(error) = values.try_reserve_exact(capacity.saturating_sub(values.len())) {
            self.release_bytes(requested_bytes);
            return Err(error)
                .with_context(|| format!("failed to reserve decoded index {purpose}"));
        }
        let actual_bytes = values
            .capacity()
            .checked_mul(std::mem::size_of::<T>())
            .ok_or_else(|| anyhow::anyhow!("decoded index allocation size overflow"))?;
        if actual_bytes > requested_bytes {
            self.charge(actual_bytes - requested_bytes, purpose)?;
        } else {
            self.release_bytes(requested_bytes - actual_bytes);
        }
        self.release_bytes(old_capacity_bytes);
        Ok(())
    }

    pub(super) fn grow_vec<T>(&mut self, values: &mut Vec<T>, purpose: &str) -> Result<()> {
        if values.len() < values.capacity() {
            return Ok(());
        }
        let capacity = values
            .capacity()
            .saturating_mul(2)
            .max(values.len().saturating_add(1))
            .max(4);
        self.reserve_vec(values, capacity, purpose)
    }

    pub(super) fn reserve_hash_entry<K, V>(
        &mut self,
        values: &mut HashMap<K, V>,
        purpose: &str,
    ) -> Result<()>
    where
        K: std::hash::Hash + Eq,
    {
        if values.len() < values.capacity() {
            return Ok(());
        }
        let capacity = values
            .capacity()
            .saturating_mul(2)
            .max(values.len().saturating_add(1))
            .max(4);
        let old_bytes = self.hash_map_bytes::<K, V>(values.capacity())?;
        let reserved_capacity = capacity
            .checked_mul(2)
            .ok_or_else(|| anyhow::anyhow!("decoded index allocation size overflow"))?;
        let requested_bytes = self.hash_map_bytes::<K, V>(reserved_capacity)?;
        self.charge(requested_bytes, purpose)?;
        let additional = capacity.saturating_sub(values.len());
        if let Err(error) = values.try_reserve(additional) {
            self.release_bytes(requested_bytes);
            return Err(error)
                .with_context(|| format!("failed to reserve decoded index {purpose}"));
        }
        let actual_bytes = self.hash_map_bytes::<K, V>(values.capacity())?;
        if actual_bytes > requested_bytes {
            self.charge(actual_bytes - requested_bytes, purpose)?;
        } else {
            self.release_bytes(requested_bytes - actual_bytes);
        }
        self.release_bytes(old_bytes);
        Ok(())
    }
}

#[derive(Clone, Copy)]
enum IndexJsonField {
    Files,
    Postings,
    NoIgnore,
    Path,
    MtimeNs,
    Size,
    Deleted,
    FileId,
    Line,
    Other,
}

impl<'de> Deserialize<'de> for IndexJsonField {
    fn deserialize<D>(deserializer: D) -> std::result::Result<Self, D::Error>
    where
        D: serde::Deserializer<'de>,
    {
        struct FieldVisitor;

        impl Visitor<'_> for FieldVisitor {
            type Value = IndexJsonField;

            fn expecting(&self, formatter: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
                formatter.write_str("an index JSON field")
            }

            fn visit_str<E>(self, value: &str) -> std::result::Result<Self::Value, E>
            where
                E: de::Error,
            {
                Ok(match value {
                    "files" => IndexJsonField::Files,
                    "postings" => IndexJsonField::Postings,
                    "no_ignore" => IndexJsonField::NoIgnore,
                    "path" => IndexJsonField::Path,
                    "mtime_ns" => IndexJsonField::MtimeNs,
                    "size" => IndexJsonField::Size,
                    "deleted" => IndexJsonField::Deleted,
                    "file_id" => IndexJsonField::FileId,
                    "line" => IndexJsonField::Line,
                    _ => IndexJsonField::Other,
                })
            }
        }

        deserializer.deserialize_identifier(FieldVisitor)
    }
}

struct BudgetedJsonIndexSeed<'a> {
    budget: &'a mut DecodeBudget,
}

impl<'de> DeserializeSeed<'de> for BudgetedJsonIndexSeed<'_> {
    type Value = SerializableIndex;

    fn deserialize<D>(self, deserializer: D) -> std::result::Result<Self::Value, D::Error>
    where
        D: serde::Deserializer<'de>,
    {
        deserializer.deserialize_struct(
            "SerializableIndex",
            &["files", "postings", "no_ignore"],
            SerializableIndexVisitor {
                budget: self.budget,
            },
        )
    }
}

struct SerializableIndexVisitor<'a> {
    budget: &'a mut DecodeBudget,
}

impl<'de> Visitor<'de> for SerializableIndexVisitor<'_> {
    type Value = SerializableIndex;

    fn expecting(&self, formatter: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
        formatter.write_str("a serialized tensor-grep index")
    }

    fn visit_map<A>(self, mut map: A) -> std::result::Result<Self::Value, A::Error>
    where
        A: MapAccess<'de>,
    {
        let mut files = None;
        let mut postings = None;
        let mut no_ignore = None;
        while let Some(field) = map.next_key::<IndexJsonField>()? {
            match field {
                IndexJsonField::Files => {
                    if files.is_some() {
                        return Err(de::Error::duplicate_field("files"));
                    }
                    files = Some(map.next_value_seed(BudgetedFilesSeed {
                        budget: &mut *self.budget,
                    })?);
                }
                IndexJsonField::Postings => {
                    if postings.is_some() {
                        return Err(de::Error::duplicate_field("postings"));
                    }
                    postings = Some(map.next_value_seed(BudgetedPostingsSeed {
                        budget: &mut *self.budget,
                    })?);
                }
                IndexJsonField::NoIgnore => {
                    if no_ignore.is_some() {
                        return Err(de::Error::duplicate_field("no_ignore"));
                    }
                    no_ignore = Some(map.next_value()?);
                }
                _ => {
                    let _: IgnoredAny = map.next_value()?;
                }
            }
        }

        Ok(SerializableIndex {
            files: files.ok_or_else(|| de::Error::missing_field("files"))?,
            postings: postings.ok_or_else(|| de::Error::missing_field("postings"))?,
            no_ignore: no_ignore.ok_or_else(|| de::Error::missing_field("no_ignore"))?,
        })
    }
}

struct BudgetedFilesSeed<'a> {
    budget: &'a mut DecodeBudget,
}

impl<'de> DeserializeSeed<'de> for BudgetedFilesSeed<'_> {
    type Value = Vec<FileEntry>;

    fn deserialize<D>(self, deserializer: D) -> std::result::Result<Self::Value, D::Error>
    where
        D: serde::Deserializer<'de>,
    {
        deserializer.deserialize_seq(FilesVisitor {
            budget: self.budget,
        })
    }
}

struct FilesVisitor<'a> {
    budget: &'a mut DecodeBudget,
}

impl<'de> Visitor<'de> for FilesVisitor<'_> {
    type Value = Vec<FileEntry>;

    fn expecting(&self, formatter: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
        formatter.write_str("an array of index file entries")
    }

    fn visit_seq<A>(self, mut seq: A) -> std::result::Result<Self::Value, A::Error>
    where
        A: SeqAccess<'de>,
    {
        let mut files = Vec::new();
        while let Some(entry) = seq.next_element_seed(BudgetedFileEntrySeed {
            budget: &mut *self.budget,
        })? {
            self.budget
                .grow_vec(&mut files, "legacy JSON file entries")
                .map_err(de::Error::custom)?;
            files.push(entry);
        }
        Ok(files)
    }
}

struct BudgetedFileEntrySeed<'a> {
    budget: &'a mut DecodeBudget,
}

impl<'de> DeserializeSeed<'de> for BudgetedFileEntrySeed<'_> {
    type Value = FileEntry;

    fn deserialize<D>(self, deserializer: D) -> std::result::Result<Self::Value, D::Error>
    where
        D: serde::Deserializer<'de>,
    {
        deserializer.deserialize_struct(
            "FileEntry",
            &["path", "mtime_ns", "size", "deleted"],
            FileEntryVisitor {
                budget: self.budget,
            },
        )
    }
}

struct FileEntryVisitor<'a> {
    budget: &'a mut DecodeBudget,
}

impl<'de> Visitor<'de> for FileEntryVisitor<'_> {
    type Value = FileEntry;

    fn expecting(&self, formatter: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
        formatter.write_str("an index file entry")
    }

    fn visit_map<A>(self, mut map: A) -> std::result::Result<Self::Value, A::Error>
    where
        A: MapAccess<'de>,
    {
        let mut path = None;
        let mut mtime_ns = None;
        let mut size = None;
        let mut deleted = None;
        while let Some(field) = map.next_key::<IndexJsonField>()? {
            match field {
                IndexJsonField::Path => {
                    if path.is_some() {
                        return Err(de::Error::duplicate_field("path"));
                    }
                    path = Some(map.next_value_seed(BudgetedPathSeed {
                        budget: &mut *self.budget,
                    })?);
                }
                IndexJsonField::MtimeNs => {
                    if mtime_ns.is_some() {
                        return Err(de::Error::duplicate_field("mtime_ns"));
                    }
                    mtime_ns = Some(map.next_value()?);
                }
                IndexJsonField::Size => {
                    if size.is_some() {
                        return Err(de::Error::duplicate_field("size"));
                    }
                    size = Some(map.next_value()?);
                }
                IndexJsonField::Deleted => {
                    if deleted.is_some() {
                        return Err(de::Error::duplicate_field("deleted"));
                    }
                    deleted = Some(map.next_value()?);
                }
                _ => {
                    let _: IgnoredAny = map.next_value()?;
                }
            }
        }

        Ok(FileEntry {
            path: path.ok_or_else(|| de::Error::missing_field("path"))?,
            mtime_ns: mtime_ns.ok_or_else(|| de::Error::missing_field("mtime_ns"))?,
            size: size.ok_or_else(|| de::Error::missing_field("size"))?,
            deleted: deleted.unwrap_or(false),
        })
    }
}

struct BudgetedPathSeed<'a> {
    budget: &'a mut DecodeBudget,
}

impl<'de> DeserializeSeed<'de> for BudgetedPathSeed<'_> {
    type Value = PathBuf;

    fn deserialize<D>(self, deserializer: D) -> std::result::Result<Self::Value, D::Error>
    where
        D: serde::Deserializer<'de>,
    {
        struct PathVisitor<'a> {
            budget: &'a mut DecodeBudget,
        }

        impl Visitor<'_> for PathVisitor<'_> {
            type Value = PathBuf;

            fn expecting(&self, formatter: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
                formatter.write_str("a UTF-8 index path")
            }

            fn visit_str<E>(self, value: &str) -> std::result::Result<Self::Value, E>
            where
                E: de::Error,
            {
                self.budget
                    .charge(
                        value.len().saturating_add(1).saturating_mul(2),
                        "legacy JSON paths",
                    )
                    .map_err(E::custom)?;
                Ok(PathBuf::from(value))
            }
        }

        deserializer.deserialize_str(PathVisitor {
            budget: self.budget,
        })
    }
}

struct BudgetedPostingsSeed<'a> {
    budget: &'a mut DecodeBudget,
}

impl<'de> DeserializeSeed<'de> for BudgetedPostingsSeed<'_> {
    type Value = HashMap<String, Vec<PostingEntry>>;

    fn deserialize<D>(self, deserializer: D) -> std::result::Result<Self::Value, D::Error>
    where
        D: serde::Deserializer<'de>,
    {
        deserializer.deserialize_map(PostingsVisitor {
            budget: self.budget,
        })
    }
}

struct PostingsVisitor<'a> {
    budget: &'a mut DecodeBudget,
}

impl<'de> Visitor<'de> for PostingsVisitor<'_> {
    type Value = HashMap<String, Vec<PostingEntry>>;

    fn expecting(&self, formatter: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
        formatter.write_str("a map of trigram keys to postings")
    }

    fn visit_map<A>(self, mut map: A) -> std::result::Result<Self::Value, A::Error>
    where
        A: MapAccess<'de>,
    {
        let mut postings = HashMap::new();
        while let Some(key) = map.next_key_seed(BudgetedTrigramKeySeed {
            budget: &mut *self.budget,
        })? {
            if postings.contains_key(&key) {
                return Err(de::Error::custom("duplicate trigram key in legacy index"));
            }
            let entries = map.next_value_seed(BudgetedPostingListSeed {
                budget: &mut *self.budget,
            })?;
            self.budget
                .reserve_hash_entry(&mut postings, "legacy JSON trigram map")
                .map_err(de::Error::custom)?;
            postings.insert(key, entries);
        }
        Ok(postings)
    }
}

struct BudgetedTrigramKeySeed<'a> {
    budget: &'a mut DecodeBudget,
}

impl<'de> DeserializeSeed<'de> for BudgetedTrigramKeySeed<'_> {
    type Value = String;

    fn deserialize<D>(self, deserializer: D) -> std::result::Result<Self::Value, D::Error>
    where
        D: serde::Deserializer<'de>,
    {
        struct TrigramKeyVisitor<'a> {
            budget: &'a mut DecodeBudget,
        }

        impl Visitor<'_> for TrigramKeyVisitor<'_> {
            type Value = String;

            fn expecting(&self, formatter: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
                formatter.write_str("a six-character trigram key")
            }

            fn visit_str<E>(self, value: &str) -> std::result::Result<Self::Value, E>
            where
                E: de::Error,
            {
                if value.len() != 6 {
                    return Err(E::custom("invalid trigram key length"));
                }
                self.budget
                    .charge(value.len().saturating_add(1), "legacy JSON trigram keys")
                    .map_err(E::custom)?;
                Ok(value.to_owned())
            }
        }

        deserializer.deserialize_str(TrigramKeyVisitor {
            budget: self.budget,
        })
    }
}

struct BudgetedPostingListSeed<'a> {
    budget: &'a mut DecodeBudget,
}

impl<'de> DeserializeSeed<'de> for BudgetedPostingListSeed<'_> {
    type Value = Vec<PostingEntry>;

    fn deserialize<D>(self, deserializer: D) -> std::result::Result<Self::Value, D::Error>
    where
        D: serde::Deserializer<'de>,
    {
        deserializer.deserialize_seq(PostingListVisitor {
            budget: self.budget,
        })
    }
}

struct PostingListVisitor<'a> {
    budget: &'a mut DecodeBudget,
}

impl<'de> Visitor<'de> for PostingListVisitor<'_> {
    type Value = Vec<PostingEntry>;

    fn expecting(&self, formatter: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
        formatter.write_str("an array of posting entries")
    }

    fn visit_seq<A>(self, mut seq: A) -> std::result::Result<Self::Value, A::Error>
    where
        A: SeqAccess<'de>,
    {
        let mut entries = Vec::new();
        while let Some(entry) = seq.next_element_seed(BudgetedPostingEntrySeed)? {
            self.budget
                .grow_vec(&mut entries, "legacy JSON posting entries")
                .map_err(de::Error::custom)?;
            entries.push(entry);
        }
        Ok(entries)
    }
}

struct BudgetedPostingEntrySeed;

impl<'de> DeserializeSeed<'de> for BudgetedPostingEntrySeed {
    type Value = PostingEntry;

    fn deserialize<D>(self, deserializer: D) -> std::result::Result<Self::Value, D::Error>
    where
        D: serde::Deserializer<'de>,
    {
        deserializer.deserialize_struct("PostingEntry", &["file_id", "line"], PostingEntryVisitor)
    }
}

struct PostingEntryVisitor;

impl<'de> Visitor<'de> for PostingEntryVisitor {
    type Value = PostingEntry;

    fn expecting(&self, formatter: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
        formatter.write_str("a posting entry")
    }

    fn visit_map<A>(self, mut map: A) -> std::result::Result<Self::Value, A::Error>
    where
        A: MapAccess<'de>,
    {
        let mut file_id = None;
        let mut line = None;
        while let Some(field) = map.next_key::<IndexJsonField>()? {
            match field {
                IndexJsonField::FileId => {
                    if file_id.is_some() {
                        return Err(de::Error::duplicate_field("file_id"));
                    }
                    file_id = Some(map.next_value()?);
                }
                IndexJsonField::Line => {
                    if line.is_some() {
                        return Err(de::Error::duplicate_field("line"));
                    }
                    line = Some(map.next_value()?);
                }
                _ => {
                    let _: IgnoredAny = map.next_value()?;
                }
            }
        }
        Ok(PostingEntry {
            file_id: file_id.ok_or_else(|| de::Error::missing_field("file_id"))?,
            line: line.ok_or_else(|| de::Error::missing_field("line"))?,
        })
    }
}
pub(super) fn deserialize_index_json(
    data: &[u8],
    budget: &mut DecodeBudget,
) -> Result<SerializableIndex> {
    // Serde's escaped-string scratch can double before the visitor runs.
    let scratch_bound = data
        .len()
        .checked_mul(2)
        .ok_or_else(|| anyhow::anyhow!("decoded index allocation size overflow"))?;
    budget.charge(scratch_bound, "legacy JSON parser scratch bound")?;
    let mut deserializer = serde_json::Deserializer::from_slice(data);
    let index = BudgetedJsonIndexSeed { budget }
        .deserialize(&mut deserializer)
        .context("failed to deserialize index")?;
    deserializer.end().context("failed to deserialize index")?;
    Ok(index)
}
pub(super) fn read_bounded_index_input(path: &Path) -> Result<Vec<u8>> {
    let file = File::open(path)
        .with_context(|| format!("failed to read index from {}", path.display()))?;
    if file
        .metadata()
        .with_context(|| format!("failed to read index metadata for {}", path.display()))?
        .len()
        > MAX_INDEX_INPUT_BYTES
    {
        anyhow::bail!(
            "index input exceeds the {} byte limit (file metadata): {}",
            MAX_INDEX_INPUT_BYTES,
            path.display()
        );
    }
    read_bounded_input(file, path, MAX_INDEX_INPUT_BYTES)
}

pub(super) fn read_bounded_input(
    reader: impl Read,
    path: &Path,
    byte_limit: u64,
) -> Result<Vec<u8>> {
    let bounded_limit = byte_limit.saturating_add(1);
    let mut bounded = reader.take(bounded_limit);
    let mut data = Vec::new();
    let max_capacity = usize::try_from(bounded_limit).unwrap_or(usize::MAX);
    let mut chunk = [0u8; 64 * 1024];
    loop {
        let remaining = bounded_limit.saturating_sub(data.len() as u64);
        if remaining == 0 {
            break;
        }
        let read_limit = remaining.min(chunk.len() as u64) as usize;
        let bytes_read = loop {
            match bounded.read(&mut chunk[..read_limit]) {
                Err(error) if error.kind() == io::ErrorKind::Interrupted => continue,
                result => break result,
            }
        }
        .with_context(|| format!("failed to read index from {}", path.display()))?;
        if bytes_read == 0 {
            break;
        }
        let required = data
            .len()
            .checked_add(bytes_read)
            .ok_or_else(|| anyhow::anyhow!("index input size overflow"))?;
        if required > data.capacity() {
            let grown = data
                .capacity()
                .max(chunk.len())
                .checked_mul(2)
                .unwrap_or(max_capacity);
            let capacity = required.max(grown).min(max_capacity);
            data.try_reserve_exact(capacity.saturating_sub(data.len()))
                .context("failed to reserve bounded index input buffer")?;
        }
        data.extend_from_slice(&chunk[..bytes_read]);
    }
    if data.len() as u64 > byte_limit {
        anyhow::bail!(
            "index input exceeds the {} byte limit: {}",
            byte_limit,
            path.display()
        );
    }
    Ok(data)
}
pub(super) fn ensure_count_fits(
    count: usize,
    minimum_record_bytes: usize,
    data: &[u8],
    pos: usize,
    label: &str,
) -> Result<()> {
    let minimum_bytes = count
        .checked_mul(minimum_record_bytes)
        .ok_or_else(|| anyhow::anyhow!("index {label} count overflow"))?;
    if minimum_bytes > data.len().saturating_sub(pos) {
        anyhow::bail!("index file is truncated in {label}");
    }
    Ok(())
}

pub(super) fn from_serializable(
    s: SerializableIndex,
    budget: &mut DecodeBudget,
) -> Result<TrigramIndex> {
    let legacy_map_bytes =
        budget.hash_map_bytes::<String, Vec<PostingEntry>>(s.postings.capacity())?;
    let mut postings = HashMap::new();
    for (key, value) in s.postings {
        if key.len() != 6 {
            anyhow::bail!("invalid trigram key: {key}");
        }
        let bytes = hex_to_trigram(&key)?;
        if postings.contains_key(&bytes) {
            anyhow::bail!("duplicate trigram key in legacy index");
        }
        budget.reserve_hash_entry(&mut postings, "legacy JSON decoded trigram map")?;
        postings.insert(bytes, value);
        budget.release_bytes(key.len().saturating_add(1));
    }
    budget.release_bytes(legacy_map_bytes);
    for entry in &s.files {
        super::validate_entry_rel_path(&entry.path)?;
    }
    super::normalize_postings(&mut postings);
    let file_trigrams = super::rebuild_file_trigrams(s.files.len(), &postings, budget)?;
    Ok(TrigramIndex {
        root: PathBuf::new(),
        canonical_root: PathBuf::new(),
        tree_fingerprint: 0,
        files: s.files,
        file_trigrams,
        postings,
        no_ignore: s.no_ignore,
    })
}

fn hex_to_trigram(hex: &str) -> Result<[u8; 3]> {
    fn nibble(byte: u8) -> Option<u8> {
        match byte {
            b'0'..=b'9' => Some(byte - b'0'),
            b'a'..=b'f' => Some(byte - b'a' + 10),
            b'A'..=b'F' => Some(byte - b'A' + 10),
            _ => None,
        }
    }

    let bytes = hex.as_bytes();
    if bytes.len() != 6 {
        anyhow::bail!("invalid trigram key");
    }
    let mut trigram = [0; 3];
    for (i, byte) in trigram.iter_mut().enumerate() {
        let high =
            nibble(bytes[2 * i]).ok_or_else(|| anyhow::anyhow!("invalid hex in trigram key"))?;
        let low = nibble(bytes[2 * i + 1])
            .ok_or_else(|| anyhow::anyhow!("invalid hex in trigram key"))?;
        *byte = (high << 4) | low;
    }
    Ok(trigram)
}
