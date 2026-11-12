#[cfg(windows)]
mod budget;

#[cfg(test)]
#[path = "../tests/checkpoint_state/publication.rs"]
mod publication_tests;

#[cfg(windows)]
use anyhow::Context;
#[cfg(windows)]
use std::ffi::OsStr;
#[cfg(windows)]
use std::path::{Component, Path, PathBuf};
#[cfg(windows)]
use std::time::{SystemTime, UNIX_EPOCH};

use super::CheckpointCreateSummary;
#[cfg(windows)]
use super::{
    checkpoint_display_path, checkpoint_index_path, checkpoint_timestamp_string,
    collect_checkpoint_entries, detect_checkpoint_scope, JSON_OUTPUT_VERSION,
};
#[cfg(windows)]
use super::{CheckpointIndexRecord, CheckpointMetadata};
#[cfg(windows)]
use tensor_grep_rs::safe_write::{AnchoredDirectory, AnchoredTarget, AtomicPublishError};

// 64 default retained records get 256 KiB each, with room for unknown JSON fields.
#[cfg(windows)]
const MAX_CHECKPOINT_INDEX_BYTES: u64 = 16 * 1024 * 1024;

#[cfg(windows)]
#[derive(Clone, Copy, Debug, Eq, PartialEq)]
enum CheckpointArtifact {
    Metadata,
    Index,
}

#[cfg(windows)]
pub(super) fn create_checkpoint(path: &str) -> anyhow::Result<CheckpointCreateSummary> {
    let created_at = checkpoint_timestamp_string();
    let unique = SystemTime::now()
        .duration_since(UNIX_EPOCH)
        .map(|duration| duration.as_nanos())
        .unwrap_or(0);
    let checkpoint_id = format!("ckpt-{created_at}-{unique:x}");
    create_checkpoint_with_publisher(
        path,
        &checkpoint_id,
        &created_at,
        |artifact, target, bytes| match artifact {
            CheckpointArtifact::Metadata => target.publish_atomic_new(bytes),
            CheckpointArtifact::Index => target.publish_atomic_preserving_permissions(bytes),
        },
    )
}

#[cfg(not(windows))]
pub(super) fn create_checkpoint(_path: &str) -> anyhow::Result<CheckpointCreateSummary> {
    anyhow::bail!(
        "native checkpoint creation is unsupported on this platform without atomic directory ownership"
    )
}

#[cfg(windows)]
fn create_checkpoint_with_publisher<P>(
    path: &str,
    checkpoint_id: &str,
    created_at: &str,
    mut publish: P,
) -> anyhow::Result<CheckpointCreateSummary>
where
    P: FnMut(CheckpointArtifact, &AnchoredTarget, &[u8]) -> Result<(), AtomicPublishError>,
{
    let scope = detect_checkpoint_scope(path);
    let root = scope.root.clone();
    let mode = scope.mode.clone();
    let created_at = created_at.to_owned();
    let entries = collect_checkpoint_entries(&scope)?;
    let root_dir = AnchoredDirectory::open(&root)?;
    // MS-003: refuse BEFORE any snapshot directory exists, exactly as
    // `checkpoint_retention._check_checkpoint_disk_budget` does on the Python path.
    check_disk_budget(&root_dir, &entries, budget::checkpoint_limits())?;

    let app_dir = root_dir.open_or_create_child(OsStr::new(".tensor-grep"))?;
    let storage_dir = app_dir.open_or_create_child(OsStr::new("checkpoints"))?;
    // Exclusive admission means a collision never grants cleanup ownership of another run's data.
    let mut checkpoint_dir = Some(storage_dir.create_child(OsStr::new(checkpoint_id))?);
    let mut index_visible = false;

    let write_checkpoint_body = || -> anyhow::Result<(CheckpointCreateSummary, Vec<String>)> {
        let checkpoint_dir_ref = checkpoint_dir
            .as_ref()
            .expect("checkpoint owner remains until index publication");
        let snapshot_dir = checkpoint_dir_ref.create_child(OsStr::new("snapshot"))?;
        let limits = budget::checkpoint_limits();
        let mut copied_bytes = 0u64;
        for (rel_path, exists) in &entries {
            if !exists {
                continue;
            }
            let source = root.join(rel_path);
            let destination_path = snapshot_dir.path().join(rel_path);
            let destination = snapshot_target(&snapshot_dir, rel_path)?;
            let max_bytes = limits
                .max_file_bytes
                .min(limits.max_total_bytes.saturating_sub(copied_bytes));
            copied_bytes += copy_checkpoint_entry_anchored(
                &root_dir,
                rel_path,
                &destination,
                &destination_path,
                max_bytes,
            )
            .with_context(|| {
                format!(
                    "failed to copy {} into checkpoint snapshot {}",
                    source.display(),
                    destination_path.display()
                )
            })?;
        }

        let summary = CheckpointCreateSummary {
            checkpoint_id: checkpoint_id.to_owned(),
            mode: mode.clone(),
            root: checkpoint_display_path(&root),
            scope: scope.scope_kind().to_string(),
            original_path: checkpoint_display_path(&scope.original_path),
            created_at: created_at.clone(),
            file_count: entries.len(),
        };
        let metadata = CheckpointMetadata {
            version: JSON_OUTPUT_VERSION,
            checkpoint_id: checkpoint_id.to_owned(),
            mode: mode.clone(),
            root: summary.root.clone(),
            scope: summary.scope.clone(),
            original_path: summary.original_path.clone(),
            created_at: created_at.clone(),
            file_count: entries.len(),
            entries,
        };
        let metadata_target = checkpoint_dir_ref.target(OsStr::new("metadata.json"))?;
        let metadata_bytes = serde_json::to_vec_pretty(&metadata)?;
        publish(
            CheckpointArtifact::Metadata,
            &metadata_target,
            &metadata_bytes,
        )
        .map_err(|failure| {
            failure
                .error
                .context("failed to publish checkpoint metadata")
        })?;

        let index_path = checkpoint_index_path(&root);
        // TG-034: the Python twin performs this SAME read-modify-write under `index_lock` on the
        // same `.{name}.lock` sidecar (`_index_lock._lock_path_for` and `index_lock::lock_path_for`
        // agree byte for byte). Unlocked, a concurrent native+Python creation loses a record.
        storage_dir.validate_current()?;
        let _index_guard =
            tensor_grep_rs::index_lock::IndexLockGuard::acquire_anchored(&index_path, &storage_dir)
                .map_err(|err| anyhow::anyhow!("could not lock {}: {err}", index_path.display()))?;
        storage_dir.validate_current()?;
        let index_target = storage_dir.target(OsStr::new("index.json"))?;
        let mut records: Vec<serde_json::Value> = match index_target
            .read_bounded(MAX_CHECKPOINT_INDEX_BYTES)
        {
            Ok(bytes) => {
                let _: Vec<CheckpointIndexRecord> = serde_json::from_slice(&bytes)
                    .with_context(|| format!("failed to parse {}", index_path.display()))?;
                serde_json::from_slice(&bytes)
                    .with_context(|| format!("failed to parse {}", index_path.display()))?
            }
            Err(error)
                if error
                    .downcast_ref::<std::io::Error>()
                    .is_some_and(|io_error| io_error.kind() == std::io::ErrorKind::NotFound) =>
            {
                Vec::new()
            }
            Err(error) => {
                return Err(error)
                    .with_context(|| format!("failed to read {}", index_path.display()))
            }
        };
        records.insert(
            0,
            serde_json::to_value(CheckpointIndexRecord {
                version: JSON_OUTPUT_VERSION,
                checkpoint_id: checkpoint_id.to_owned(),
                mode,
                root: summary.root.clone(),
                created_at,
                file_count: summary.file_count,
            })?,
        );
        // MS-003: bound retention to the same record cap the Python path enforces. Prune by
        // `created_at` (newest first), NOT list position: the timestamp is stamped before the
        // lock, so a concurrent writer can make insertion order disagree with creation order.
        let limits = budget::checkpoint_limits();
        records.sort_by(|left, right| record_created_at(right).cmp(record_created_at(left)));
        let mut dropped_ids: Vec<String> = Vec::new();
        if records.len() > limits.max_records {
            for record in records.drain(limits.max_records..) {
                if let Some(id) = record.get("checkpoint_id").and_then(|value| value.as_str()) {
                    if is_safe_checkpoint_id(id) {
                        dropped_ids.push(id.to_string());
                    }
                }
            }
        }
        let index_bytes = serde_json::to_vec_pretty(&records)?;
        let publication_error =
            match publish(CheckpointArtifact::Index, &index_target, &index_bytes) {
                Ok(()) => {
                    index_visible = true;
                    None
                }
                Err(failure) => {
                    index_visible = failure.published;
                    Some(failure.error)
                }
            };
        if index_visible {
            // Readers may enumerate checkpoint directories as soon as index.json is visible.
            // Release DELETE-capable owner handles first so ordinary Windows readers can open
            // the published checkpoint while the shared sidecar lock is still held.
            drop(snapshot_dir);
            drop(metadata_target);
            drop(checkpoint_dir.take());
        }
        if let Some(error) = publication_error {
            return Err(error.context(format!("failed to publish {}", index_path.display())));
        }

        Ok((summary, dropped_ids))
    };

    match write_checkpoint_body() {
        Ok((summary, dropped_ids)) => {
            // Deleting a dropped checkpoint's snapshot is slow and index-unrelated, so it runs
            // AFTER the closure returned -- i.e. after the index lock was released -- mirroring
            // `_select_retained_checkpoints`' deferred-rmtree split on the Python path. The
            // shared index is tamperable, so a safe-looking ID is not proof that this process
            // owns the directory named by that record. Retention removes index records only;
            // it must not recursively delete an unowned directory based on index contents.
            for dropped_id in dropped_ids {
                if !is_safe_checkpoint_id(&dropped_id) {
                    continue;
                }
                let dropped_path = storage_dir.path().join(&dropped_id);
                eprintln!(
                    "warning: checkpoint retention pruned {:?} from the index but retained {}; index records cannot prove directory cleanup ownership and data may remain",
                    dropped_id,
                    dropped_path.display()
                );
            }
            Ok(summary)
        }
        Err(error) if index_visible => Err(error),
        Err(error) => {
            // Remove only when the platform can prove ownership safely. Unix deliberately leaves
            // the unpublished directory in place when atomic directory ownership is unavailable.
            if let Some(checkpoint_dir) = &checkpoint_dir {
                if let Err(cleanup_error) = checkpoint_dir.remove_owned_tree() {
                    return Err(anyhow::anyhow!(
                        "checkpoint creation failed ({error:#}); unpublished checkpoint directory {} was retained because safe cleanup failed ({cleanup_error:#})",
                        checkpoint_dir.path().display()
                    ));
                }
            }
            Err(error)
        }
    }
}

#[cfg(windows)]
fn snapshot_target(
    snapshot_dir: &AnchoredDirectory,
    relative_path: &str,
) -> anyhow::Result<AnchoredTarget> {
    let relative_path = Path::new(relative_path);
    let name = relative_path
        .file_name()
        .ok_or_else(|| anyhow::anyhow!("checkpoint entry has no file name"))?;
    let mut parent = snapshot_dir.try_clone()?;
    if let Some(relative_parent) = relative_path.parent() {
        for component in relative_parent.components() {
            match component {
                Component::Normal(name) => parent = parent.open_or_create_child(name)?,
                Component::CurDir => {}
                _ => anyhow::bail!("checkpoint entry path is not relative: {relative_path:?}"),
            }
        }
    }
    parent.target(name)
}

#[cfg(windows)]
enum AnchoredCheckpointSource {
    Symlink(PathBuf),
    Regular(std::fs::File),
}

#[cfg(windows)]
fn anchored_source_parent(
    root: &AnchoredDirectory,
    relative_path: &str,
) -> std::io::Result<(AnchoredDirectory, std::ffi::OsString)> {
    let relative_path = Path::new(relative_path);
    let name = relative_path
        .file_name()
        .ok_or_else(|| {
            std::io::Error::new(std::io::ErrorKind::InvalidInput, "missing source leaf")
        })?
        .to_os_string();
    let mut parent = root
        .try_clone()
        .map_err(|error| std::io::Error::other(error.to_string()))?;
    if let Some(relative_parent) = relative_path.parent() {
        for component in relative_parent.components() {
            match component {
                Component::Normal(name) => {
                    parent = parent
                        .open_child(name)
                        .map_err(|error| std::io::Error::other(error.to_string()))?;
                }
                Component::CurDir => {}
                _ => {
                    return Err(std::io::Error::new(
                        std::io::ErrorKind::InvalidInput,
                        format!("checkpoint source path is not relative: {relative_path:?}"),
                    ));
                }
            }
        }
    }
    Ok((parent, name))
}

#[cfg(windows)]
fn open_checkpoint_source(
    root: &AnchoredDirectory,
    relative_path: &str,
) -> std::io::Result<AnchoredCheckpointSource> {
    let (parent, name) = anchored_source_parent(root, relative_path)?;
    if let Some(target) = parent.read_link(&name)? {
        return Ok(AnchoredCheckpointSource::Symlink(target));
    }
    parent
        .open_regular_file(&name)
        .map(AnchoredCheckpointSource::Regular)
}

#[cfg(windows)]
fn symlink_target_size_bytes(target: &Path) -> u64 {
    #[cfg(unix)]
    {
        use std::os::unix::ffi::OsStrExt;
        u64::try_from(target.as_os_str().as_bytes().len()).unwrap_or(u64::MAX)
    }
    #[cfg(windows)]
    {
        use std::os::windows::ffi::OsStrExt;
        u64::try_from(target.as_os_str().encode_wide().count())
            .unwrap_or(u64::MAX)
            .saturating_mul(2)
    }
    #[cfg(not(any(unix, windows)))]
    {
        u64::try_from(target.as_os_str().as_encoded_bytes().len()).unwrap_or(u64::MAX)
    }
}

#[cfg(windows)]
fn copy_checkpoint_entry_anchored(
    root: &AnchoredDirectory,
    relative_path: &str,
    destination: &AnchoredTarget,
    _destination_path: &Path,
    max_bytes: u64,
) -> std::io::Result<u64> {
    match open_checkpoint_source(root, relative_path)? {
        AnchoredCheckpointSource::Symlink(link_target) => {
            let size = symlink_target_size_bytes(&link_target);
            if size > max_bytes {
                return Err(std::io::Error::new(
                    std::io::ErrorKind::InvalidData,
                    format!(
                        "checkpoint symlink target is {size} bytes, over the remaining copy budget of {max_bytes} bytes"
                    ),
                ));
            }
            #[cfg(windows)]
            let is_directory = _destination_path
                .parent()
                .map(|parent| parent.join(&link_target))
                .and_then(|path| std::fs::metadata(path).ok())
                .is_some_and(|metadata| metadata.is_dir());
            #[cfg(not(windows))]
            let is_directory = false;
            destination.create_symlink(&link_target, is_directory)?;
            Ok(size)
        }
        AnchoredCheckpointSource::Regular(source) => {
            destination.copy_opened_regular_file_from(source, max_bytes)
        }
    }
}

/// MS-003 admission budget: refuse before a single byte is copied. Stats every entry (cheap, no
/// copying yet) and refuses when one file breaks the per-file cap, the running total breaks the
/// per-checkpoint cap, or the copy would leave the filesystem under its free-space margin.
///
/// Missing entries and failed metadata reads are errors; no snapshot directory is created until
/// every collected source has been checked successfully.
#[cfg(windows)]
fn check_disk_budget(
    root: &AnchoredDirectory,
    entries: &std::collections::BTreeMap<String, bool>,
    limits: budget::CheckpointLimits,
) -> anyhow::Result<()> {
    let mut total_bytes: u64 = 0;
    for (rel_path, exists) in entries {
        if !exists {
            continue;
        }
        let source = open_checkpoint_source(root, rel_path)
            .with_context(|| format!("failed to inspect checkpoint source {rel_path:?}"))?;
        let size = match source {
            AnchoredCheckpointSource::Symlink(target) => symlink_target_size_bytes(&target),
            AnchoredCheckpointSource::Regular(file) => file
                .metadata()
                .with_context(|| format!("failed to read checkpoint source metadata {rel_path:?}"))?
                .len(),
        };
        if size > limits.max_file_bytes {
            anyhow::bail!(
                "Checkpoint refused: {rel_path:?} is {size} bytes, over the per-file limit of \
                 {} bytes (raise {} to allow larger files).",
                limits.max_file_bytes,
                budget::MAX_FILE_BYTES_ENV
            );
        }
        total_bytes = total_bytes.saturating_add(size);
        if total_bytes > limits.max_total_bytes {
            anyhow::bail!(
                "Checkpoint refused: snapshot size exceeds the per-checkpoint limit of {} bytes \
                 (raise {} to allow a larger checkpoint).",
                limits.max_total_bytes,
                budget::MAX_TOTAL_BYTES_ENV
            );
        }
    }
    // Cannot introspect free space -> do not block on a diagnostic we could not compute; the two
    // size caps above still apply.
    if let Some(free_bytes) = budget::available_bytes(root.path()) {
        let required = total_bytes.saturating_add(limits.free_margin_bytes);
        if free_bytes < required {
            anyhow::bail!(
                "Checkpoint refused: only {free_bytes} bytes free, but this checkpoint needs \
                 {total_bytes} bytes plus a {}-byte safety margin (lower {} to change the margin).",
                limits.free_margin_bytes,
                budget::FREE_MARGIN_ENV
            );
        }
    }
    Ok(())
}

#[cfg(windows)]
fn record_created_at(record: &serde_json::Value) -> &str {
    record
        .get("created_at")
        .and_then(|value| value.as_str())
        .unwrap_or("")
}

/// A checkpoint id must be exactly ONE plain, portable path component before the shared,
/// tamperable index can retain or prune the corresponding record.
///
/// The component check is what stops `..`/`.`; the charset check is what stops a Windows
/// DRIVE-RELATIVE prefix like `C:evil`, which `Path::join` treats as a replacement rather than a
/// child -- a separator blacklist alone passes `C:evil` and steers cleanup outside the store.
#[cfg(windows)]
fn is_safe_checkpoint_id(id: &str) -> bool {
    if id.is_empty() || id.contains(':') {
        return false;
    }
    if !id
        .chars()
        .all(|c| c.is_ascii_alphanumeric() || matches!(c, '-' | '_' | '.'))
    {
        return false;
    }
    let mut components = std::path::Path::new(id).components();
    matches!(
        (components.next(), components.next()),
        (Some(std::path::Component::Normal(_)), None)
    )
}
