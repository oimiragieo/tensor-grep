use super::{defaulted_scope_fields, NativeSearchConfig, SearchStats, JSON_OUTPUT_VERSION};
use crate::routing::gpu_proof_fields;

pub(super) fn emit_ndjson_binary_summary(
    config: &NativeSearchConfig,
    stats: &SearchStats,
) -> anyhow::Result<()> {
    let proof_fields = gpu_proof_fields(
        &config.requested_gpu_device_ids,
        config.routing_backend,
        config.sidecar_used,
    );
    let mut summary = serde_json::json!({
        "type": "summary",
        "version": JSON_OUTPUT_VERSION,
        "routing_backend": config.routing_backend,
        "routing_reason": config.routing_reason,
        "sidecar_used": config.sidecar_used,
        "requested_gpu_device_ids": config.requested_gpu_device_ids,
        "routing_gpu_device_ids": [],
        "total_matches": stats.total_matches,
        "binary_match_files": stats.binary_match_files,
    });
    if let Some(value) = proof_fields.gpu_evidence_status {
        summary["gpu_evidence_status"] = value.into();
    }
    if let Some(value) = proof_fields.gpu_proof {
        summary["gpu_proof"] = value.into();
    }
    if let Some(value) = proof_fields.native_gpu_unavailable {
        summary["native_gpu_unavailable"] = value.into();
    }
    if let Some(value) = proof_fields.not_gpu_proof_reason {
        summary["not_gpu_proof_reason"] = value.into();
    }
    if stats.walk_errors > 0 {
        summary["result_incomplete"] = true.into();
        summary["incomplete_reason_class"] = "unreadable_path".into();
        summary["incomplete_paths_count"] = stats.walk_errors.into();
    }
    let (path_was_defaulted, scope_note) = defaulted_scope_fields(
        config.path_was_implicit,
        stats.total_matches.saturating_add(stats.binary_match_files),
    );
    if let Some(path_was_defaulted) = path_was_defaulted {
        summary["path_was_defaulted"] = path_was_defaulted.into();
    }
    if let Some(scope_note) = scope_note {
        summary["scope_note"] = scope_note.into();
    }
    let mut output = summary.to_string();
    output.push('\n');
    config.output_target.write_all(output.as_bytes())
}
