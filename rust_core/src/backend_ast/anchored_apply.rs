use super::{
    apply_edits_to_file_with_writer, apply_grouped_edit_files_with_reader, group_edits_by_file,
    planned_mtime_ns_for_file, system_time_to_unix_nanos, ApplyExecution, OverlapRejection,
    PerFileRewriteOutcome, RewriteEdit, MAX_REWRITE_FILE_BYTES,
};
use crate::safe_write::AnchoredTarget;
use anyhow::{Context, Result};
use rayon::prelude::*;
use std::collections::HashMap;
use std::path::{Path, PathBuf};
use std::sync::Arc;

pub(super) fn apply_edit_set(edits: &[RewriteEdit]) -> Result<usize> {
    let targets = anchor_targets_for_edits(edits)?;
    apply_edit_set_with_targets(edits, &targets)
}

pub(super) fn apply_edit_set_with_targets(
    edits: &[RewriteEdit],
    targets: &HashMap<PathBuf, Arc<AnchoredTarget>>,
) -> Result<usize> {
    let files = group_edits_by_file(edits);
    ensure_files_not_stale(&files, targets)?;
    apply_grouped_edit_files_with_reader(
        &files,
        None,
        ApplyExecution::Parallel,
        &|file| {
            let target = targets
                .get(file)
                .with_context(|| format!("no retained rewrite target for {}", file.display()))?;
            let bytes = target
                .read_bounded(MAX_REWRITE_FILE_BYTES)
                .with_context(|| {
                    format!(
                        "failed to read {} through its retained target",
                        file.display()
                    )
                })?;
            let source = String::from_utf8(bytes)
                .map_err(|error| anyhow::anyhow!("invalid UTF-8 in {}: {error}", file.display()))?;
            Ok(source)
        },
        &|file, bytes| {
            let target = targets
                .get(file)
                .with_context(|| format!("no retained rewrite target for {}", file.display()))?;
            target.write_atomic_with_hook(bytes, |_| Ok(()))
        },
    )
}

fn anchor_targets_for_edits(
    edits: &[RewriteEdit],
) -> Result<HashMap<PathBuf, Arc<AnchoredTarget>>> {
    let files = group_edits_by_file(edits);
    let results: Vec<Result<(PathBuf, Arc<AnchoredTarget>)>> = files
        .par_iter()
        .map(|(file, _)| Ok((file.to_path_buf(), Arc::new(AnchoredTarget::open(file)?))))
        .collect();
    results.into_iter().collect()
}

fn ensure_files_not_stale(
    files: &[(&Path, Vec<&RewriteEdit>)],
    targets: &HashMap<PathBuf, Arc<AnchoredTarget>>,
) -> Result<()> {
    let results: Vec<Result<()>> = files
        .par_iter()
        .map(|(file, edits)| {
            let planned_mtime_ns = planned_mtime_ns_for_file(file, edits)?;
            let target = targets
                .get(*file)
                .with_context(|| format!("no retained rewrite target for {}", file.display()))?;
            let modified = target
                .metadata()
                .with_context(|| format!("failed to read metadata for {}", file.display()))?
                .modified()
                .with_context(|| format!("failed to read modified time for {}", file.display()))?;
            let current_mtime_ns = system_time_to_unix_nanos(file, modified)?;
            if current_mtime_ns != planned_mtime_ns {
                anyhow::bail!(
                    "stale file modified since planning: {} (planned mtime ns {}, current mtime ns {})",
                    file.display(),
                    planned_mtime_ns,
                    current_mtime_ns
                );
            }
            Ok(())
        })
        .collect();
    for result in results {
        result?;
    }
    Ok(())
}

pub(super) fn apply_prepared_rewrite_file(
    target: Arc<AnchoredTarget>,
    file: PathBuf,
    original_source: String,
    valid_edits: Vec<RewriteEdit>,
    rejected_overlaps: Vec<OverlapRejection>,
) -> Result<Option<PerFileRewriteOutcome>> {
    if valid_edits.is_empty() && rejected_overlaps.is_empty() {
        return Ok(None);
    }
    if !valid_edits.is_empty() {
        let file_edits: Vec<&RewriteEdit> = valid_edits.iter().collect();
        ensure_file_not_stale(&file, &file_edits, &target)?;
        let write_file = |_: &Path, bytes: &[u8]| target.write_direct(bytes);
        apply_edits_to_file_with_writer(
            &file,
            &file_edits,
            Some(original_source.as_str()),
            &write_file,
        )?;
    }
    Ok(Some(PerFileRewriteOutcome {
        edits: valid_edits,
        rejected_overlaps,
    }))
}

fn ensure_file_not_stale(
    file: &Path,
    edits: &[&RewriteEdit],
    target: &AnchoredTarget,
) -> Result<()> {
    let planned_mtime_ns = planned_mtime_ns_for_file(file, edits)?;
    let modified = target
        .metadata()
        .with_context(|| format!("failed to read metadata for {}", file.display()))?
        .modified()
        .with_context(|| format!("failed to read modified time for {}", file.display()))?;
    let current_mtime_ns = system_time_to_unix_nanos(file, modified)?;
    if current_mtime_ns != planned_mtime_ns {
        anyhow::bail!(
            "stale file modified since planning: {} (planned mtime ns {}, current mtime ns {})",
            file.display(),
            planned_mtime_ns,
            current_mtime_ns
        );
    }
    Ok(())
}
