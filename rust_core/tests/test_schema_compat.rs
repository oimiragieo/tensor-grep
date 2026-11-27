#![allow(unused_imports, dead_code)]
use serde::de::DeserializeOwned;
use serde::Deserialize;
use std::collections::{BTreeSet, HashMap};
use std::fs;
use std::path::{Path, PathBuf};
#[path = "common/schema_compat.rs"]
mod common;
#[path = "common/schema_compat_helpers_a.rs"]
mod helpers_a;
#[path = "common/schema_compat_helpers_b.rs"]
mod helpers_b;
use common::*;
use helpers_a::*;
use helpers_b::*;

#[test]
fn test_docs_examples_match_v1_schema() {
    let examples_dir = repo_root().join("docs").join("examples");
    assert!(
        examples_dir.is_dir(),
        "missing examples directory: {}",
        examples_dir.display()
    );

    let mut example_paths: Vec<PathBuf> = fs::read_dir(&examples_dir)
        .unwrap_or_else(|error| panic!("failed to read {}: {error}", examples_dir.display()))
        .filter_map(Result::ok)
        .map(|entry| entry.path())
        .filter(|path| path.extension().and_then(|ext| ext.to_str()) == Some("json"))
        .collect();
    example_paths.sort();

    let actual_names: BTreeSet<String> = example_paths
        .iter()
        .map(|path| example_file_name(path).to_owned())
        .collect();
    let expected_names: BTreeSet<String> = EXPECTED_EXAMPLES
        .iter()
        .map(|name| (*name).to_owned())
        .collect();

    assert_eq!(
        actual_names, expected_names,
        "docs/examples/*.json changed; update schema coverage"
    );

    for path in &example_paths {
        match example_file_name(path) {
            "search.json" | "index_search.json" | "ast_run.json" => assert_search_example(path),
            "rulesets.json" => assert_rulesets_example(path),
            "ruleset_scan.json" => assert_ruleset_scan_example(path),
            "rewrite_plan.json" => assert_rewrite_plan_example(path),
            "rewrite_apply_verify.json" => assert_apply_verify_example(path),
            "audit_manifest_verify.json" => assert_audit_manifest_verify_example(path),
            "gpu_cpu_fallback_search.json" => assert_gpu_cpu_fallback_example(path),
            "gpu_sidecar_search.json" => assert_gpu_sidecar_example(path),
            "calibrate.json" => assert_calibrate_example(path),
            "mcp_rewrite_diff.json" => assert_mcp_rewrite_diff_example(path),
            "repo_map.json" => assert_repo_map_example(path),
            "context_pack.json" => assert_context_pack_example(path),
            "edit_plan.json" => assert_context_edit_plan_example(path),
            "context_render.json" => assert_context_render_example(path),
            "defs.json" => assert_symbol_defs_example(path),
            "defs_provider_disagreement.json" => assert_defs_provider_disagreement_example(path),
            "source.json" => assert_symbol_source_example(path),
            "impact.json" => assert_symbol_impact_example(path),
            "refs.json" => assert_symbol_refs_example(path),
            "callers.json" => assert_symbol_callers_example(path),
            "blast_radius.json" => assert_symbol_blast_radius_example(path),
            "blast_radius_plan.json" => assert_blast_radius_plan_example(path),
            "blast_radius_render.json" => assert_blast_radius_render_example(path),
            "session_open.json" => assert_session_open_example(path),
            "session_context.json" => assert_session_context_example(path),
            "session_invalid_request_stale.json" => assert_session_invalid_request_example(path),
            "attempt_ledger.json"
            | "multi_session_attempt_ledger.json"
            | "multi_task_attempt_ledger.json" => assert_attempt_ledger_example(path),
            "patch_bakeoff.json"
            | "patch_bakeoff_incomplete.json"
            | "patch_bakeoff_no_patch.json" => assert_patch_bakeoff_example(path),
            "provider_status_unavailable.json" => assert_provider_status_example(path),
            "rewrite_apply_verify_validation_failed.json" => {
                assert_apply_verify_validation_failed_example(path)
            }
            other => panic!("missing schema validation for {other}"),
        }
    }
}

#[test]
fn test_docs_examples_include_parseable_ndjson_stream() {
    let path = repo_root()
        .join("docs")
        .join("examples")
        .join("search.ndjson");
    let content = fs::read_to_string(&path)
        .unwrap_or_else(|error| panic!("failed to read {}: {error}", path.display()));
    let lines = content
        .lines()
        .filter(|line| !line.trim().is_empty())
        .collect::<Vec<_>>();

    assert!(
        lines.len() >= 2,
        "{} should contain multiple NDJSON rows",
        path.display()
    );

    for line in lines {
        let row: SearchNdjsonExample = serde_json::from_str(line).unwrap_or_else(|error| {
            panic!(
                "{} failed NDJSON schema validation: {error}",
                path.display()
            )
        });
        assert_common_envelope(
            &path,
            row.version,
            &row.routing_backend,
            &row.routing_reason,
        );
        assert!(
            !row.sidecar_used,
            "{} committed NDJSON example should stay native",
            path.display()
        );
        assert!(
            !row.query.is_empty(),
            "{} row missing query",
            path.display()
        );
        assert!(!row.path.is_empty(), "{} row missing path", path.display());
        assert!(!row.file.is_empty(), "{} row missing file", path.display());
        assert!(row.line > 0, "{} row line must be 1-based", path.display());
        // task #266: exactly one of `text` (valid UTF-8) / `bytes` (base64 fallback) is present.
        assert!(
            row.text.as_deref().is_some_and(|text| !text.is_empty())
                != row.bytes.as_deref().is_some_and(|bytes| !bytes.is_empty()),
            "{} row must have exactly one of `text`/`bytes` populated",
            path.display()
        );
        if let Some(pattern_text) = row.pattern_text {
            assert!(
                !pattern_text.is_empty(),
                "{} row pattern_text must not be empty",
                path.display()
            );
            assert!(
                row.pattern_id.is_some(),
                "{} row pattern_text requires pattern_id",
                path.display()
            );
        }
    }
}
