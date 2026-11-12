use anyhow::Context;
use serde::Serialize;
use std::collections::{BTreeMap, HashMap};
use std::path::PathBuf;
use std::sync::Arc;
#[cfg(test)]
use tensor_grep_rs::backend_ast::AstBackend;
use tensor_grep_rs::backend_ast::MAX_REWRITE_FILE_BYTES;
use tensor_grep_rs::safe_write::AnchoredTarget;

#[derive(Debug, Clone, Serialize)]
pub(super) struct ValidationRollbackSummary {
    triggered_by: &'static str,
    success: bool,
    files_restored: Vec<String>,
    errors: Vec<String>,
}

pub(super) struct ValidationRollbackSnapshot {
    path: PathBuf,
    target: Arc<AnchoredTarget>,
    bytes: Vec<u8>,
}

pub(super) fn collect_validation_rollback_snapshots(
    edits: &[tensor_grep_rs::backend_ast::RewriteEdit],
) -> anyhow::Result<BTreeMap<String, ValidationRollbackSnapshot>> {
    let mut snapshots = BTreeMap::new();
    for file in edits
        .iter()
        .map(|edit| edit.file.clone())
        .collect::<std::collections::BTreeSet<_>>()
    {
        let target = Arc::new(AnchoredTarget::open(&file)?);
        let bytes = target
            .read_bounded(MAX_REWRITE_FILE_BYTES)
            .with_context(|| {
                format!("failed to read {} for validation rollback", file.display())
            })?;
        snapshots.insert(
            file.to_string_lossy().to_string(),
            ValidationRollbackSnapshot {
                path: file,
                target,
                bytes,
            },
        );
    }
    Ok(snapshots)
}

pub(super) fn retained_validation_targets(
    snapshots: &BTreeMap<String, ValidationRollbackSnapshot>,
) -> HashMap<PathBuf, Arc<AnchoredTarget>> {
    snapshots
        .values()
        .map(|snapshot| (snapshot.path.clone(), Arc::clone(&snapshot.target)))
        .collect()
}

pub(super) fn restore_validation_rollback_snapshots(
    snapshots: &BTreeMap<String, ValidationRollbackSnapshot>,
) -> ValidationRollbackSummary {
    let mut files_restored = Vec::new();
    let mut errors = Vec::new();

    for (file, snapshot) in snapshots {
        match snapshot.target.write_direct(&snapshot.bytes) {
            Ok(()) => files_restored.push(file.clone()),
            Err(error) => errors.push(format!("failed to restore {file}: {error}")),
        }
    }

    ValidationRollbackSummary {
        triggered_by: "validation",
        success: errors.is_empty(),
        files_restored,
        errors,
    }
}

pub(super) fn emit_rollback_status(summary: &ValidationRollbackSummary) {
    if summary.success {
        eprintln!(
            "[rollback] restored {} file(s) after failed validation",
            summary.files_restored.len()
        );
    } else {
        eprintln!(
            "[rollback] failed to restore {} file(s) after failed validation",
            summary.errors.len()
        );
    }
}

#[cfg(test)]
#[path = "../tests/validation_rollback/mod.rs"]
mod tests;
