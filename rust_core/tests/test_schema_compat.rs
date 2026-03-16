use serde::de::DeserializeOwned;
use serde::Deserialize;
use std::collections::{BTreeSet, HashMap};
use std::fs;
use std::path::{Path, PathBuf};

const EXPECTED_EXAMPLES: &[&str] = &[
    "gpu_sidecar_search.json",
    "index_search.json",
    "rewrite_apply_verify.json",
    "rewrite_plan.json",
    "search.json",
];

#[derive(Debug, Deserialize)]
#[serde(deny_unknown_fields)]
struct SearchExample {
    version: u32,
    routing_backend: String,
    routing_reason: String,
    sidecar_used: bool,
    query: String,
    path: String,
    total_matches: usize,
    matches: Vec<SearchMatch>,
}

#[derive(Debug, Deserialize)]
#[serde(deny_unknown_fields)]
struct SearchMatch {
    file: String,
    line: usize,
    text: String,
}

#[derive(Debug, Deserialize)]
#[serde(deny_unknown_fields)]
struct GpuSidecarExample {
    version: u32,
    routing_backend: String,
    routing_reason: String,
    sidecar_used: bool,
    total_matches: usize,
    total_files: usize,
    routing_gpu_device_ids: Vec<u32>,
    matches: Vec<GpuSearchMatch>,
}

#[derive(Debug, Deserialize)]
#[serde(deny_unknown_fields)]
struct GpuSearchMatch {
    file: String,
    line_number: usize,
    text: String,
}

#[derive(Debug, Deserialize)]
#[serde(deny_unknown_fields)]
struct RewritePlanExample {
    version: u32,
    routing_backend: String,
    routing_reason: String,
    sidecar_used: bool,
    pattern: String,
    replacement: String,
    lang: String,
    total_files_scanned: usize,
    total_edits: usize,
    edits: Vec<RewriteEditExample>,
    #[serde(default)]
    rejected_overlaps: Vec<OverlapRejectionExample>,
}

#[derive(Debug, Deserialize)]
#[serde(deny_unknown_fields)]
struct RewriteEditExample {
    id: String,
    file: PathBuf,
    line: usize,
    byte_range: ByteRange,
    original_text: String,
    replacement_text: String,
    metavar_env: HashMap<String, String>,
}

#[derive(Debug, Deserialize)]
#[serde(deny_unknown_fields)]
struct ByteRange {
    start: usize,
    end: usize,
}

#[derive(Debug, Deserialize)]
#[serde(deny_unknown_fields)]
struct OverlapRejectionExample {
    file: PathBuf,
    edit_a: ByteRange,
    edit_b: ByteRange,
    reason: String,
}

#[derive(Debug, Deserialize)]
#[serde(deny_unknown_fields)]
struct ApplyVerifyExample {
    version: u32,
    routing_backend: String,
    routing_reason: String,
    sidecar_used: bool,
    plan: RewritePlanExample,
    verification: Option<VerifyResultExample>,
}

#[derive(Debug, Deserialize)]
#[serde(deny_unknown_fields)]
struct VerifyResultExample {
    total_edits: usize,
    verified: usize,
    mismatches: Vec<VerifyMismatchExample>,
}

#[derive(Debug, Deserialize)]
#[serde(deny_unknown_fields)]
struct VerifyMismatchExample {
    edit_id: String,
    file: PathBuf,
    line: usize,
    expected: String,
    actual: String,
}

#[test]
fn test_docs_examples_match_v1_schema() {
    let examples_dir = repo_root().join("docs").join("examples");
    assert!(examples_dir.is_dir(), "missing examples directory: {}", examples_dir.display());

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
    let expected_names: BTreeSet<String> = EXPECTED_EXAMPLES.iter().map(|name| (*name).to_owned()).collect();

    assert_eq!(actual_names, expected_names, "docs/examples/*.json changed; update schema coverage");

    for path in &example_paths {
        match example_file_name(path) {
            "search.json" | "index_search.json" => assert_search_example(path),
            "rewrite_plan.json" => assert_rewrite_plan_example(path),
            "rewrite_apply_verify.json" => assert_apply_verify_example(path),
            "gpu_sidecar_search.json" => assert_gpu_sidecar_example(path),
            other => panic!("missing schema validation for {other}"),
        }
    }
}

fn assert_search_example(path: &Path) {
    let example: SearchExample = parse_json_document(path);
    assert_common_envelope(path, example.version, &example.routing_backend, &example.routing_reason);
    assert!(!example.query.is_empty(), "{} has empty query", path.display());
    assert!(!example.path.is_empty(), "{} has empty path", path.display());
    assert!(!example.sidecar_used, "{} should be native search output", path.display());
    assert!(example.total_matches > 0, "{} should contain at least one match", path.display());
    assert_eq!(example.total_matches, example.matches.len(), "{} total_matches mismatch", path.display());
    for matched in &example.matches {
        assert!(!matched.file.is_empty(), "{} match missing file", path.display());
        assert!(matched.line > 0, "{} match line must be 1-based", path.display());
        assert!(!matched.text.is_empty(), "{} match missing text", path.display());
    }
}

fn assert_rewrite_plan_example(path: &Path) {
    let plan: RewritePlanExample = parse_json_document(path);
    assert_rewrite_plan_payload(path, &plan);
}

fn assert_apply_verify_example(path: &Path) {
    let example: ApplyVerifyExample = parse_json_document(path);
    assert_common_envelope(path, example.version, &example.routing_backend, &example.routing_reason);
    assert!(!example.sidecar_used, "{} should be native apply+verify output", path.display());
    assert_rewrite_plan_payload(path, &example.plan);

    let verification = example
        .verification
        .as_ref()
        .unwrap_or_else(|| panic!("{} missing verification payload", path.display()));
    assert_eq!(verification.total_edits, example.plan.total_edits, "{} verification total_edits mismatch", path.display());
    assert_eq!(
        verification.total_edits,
        verification.verified + verification.mismatches.len(),
        "{} verification counts are inconsistent",
        path.display()
    );
    for mismatch in &verification.mismatches {
        assert!(!mismatch.edit_id.is_empty(), "{} mismatch missing edit_id", path.display());
        assert!(mismatch.file.is_absolute(), "{} mismatch file should be absolute", path.display());
        assert!(mismatch.line > 0, "{} mismatch line must be 1-based", path.display());
        assert!(!mismatch.expected.is_empty(), "{} mismatch missing expected text", path.display());
        assert!(!mismatch.actual.is_empty(), "{} mismatch missing actual text", path.display());
    }
}

fn assert_gpu_sidecar_example(path: &Path) {
    let example: GpuSidecarExample = parse_json_document(path);
    assert_common_envelope(path, example.version, &example.routing_backend, &example.routing_reason);
    assert!(example.sidecar_used, "{} should report sidecar_used=true", path.display());
    assert!(example.total_files > 0, "{} should report total_files > 0", path.display());
    assert!(example.total_matches > 0, "{} should report total_matches > 0", path.display());
    assert_eq!(example.total_matches, example.matches.len(), "{} total_matches mismatch", path.display());
    assert!(
        !example.routing_gpu_device_ids.is_empty(),
        "{} should list at least one GPU device id",
        path.display()
    );
    for matched in &example.matches {
        assert!(!matched.file.is_empty(), "{} match missing file", path.display());
        assert!(matched.line_number > 0, "{} line_number must be 1-based", path.display());
        assert!(!matched.text.is_empty(), "{} match missing text", path.display());
    }
}

fn assert_rewrite_plan_payload(path: &Path, plan: &RewritePlanExample) {
    assert_common_envelope(path, plan.version, &plan.routing_backend, &plan.routing_reason);
    assert!(!plan.sidecar_used, "{} should be native rewrite output", path.display());
    assert!(!plan.pattern.is_empty(), "{} has empty pattern", path.display());
    assert!(!plan.replacement.is_empty(), "{} has empty replacement", path.display());
    assert!(!plan.lang.is_empty(), "{} has empty lang", path.display());
    assert!(plan.total_files_scanned > 0, "{} should scan at least one file", path.display());
    assert!(plan.total_edits > 0, "{} should contain at least one edit", path.display());
    assert_eq!(plan.total_edits, plan.edits.len(), "{} total_edits mismatch", path.display());
    for edit in &plan.edits {
        assert!(!edit.id.is_empty(), "{} edit missing id", path.display());
        assert!(edit.file.is_absolute(), "{} edit file should be absolute", path.display());
        assert!(edit.line > 0, "{} edit line must be 1-based", path.display());
        assert!(edit.byte_range.end > edit.byte_range.start, "{} edit byte range invalid", path.display());
        assert!(!edit.original_text.is_empty(), "{} edit missing original_text", path.display());
        assert!(!edit.replacement_text.is_empty(), "{} edit missing replacement_text", path.display());
        assert!(!edit.metavar_env.is_empty(), "{} edit missing metavariables", path.display());
    }
    for rejected in &plan.rejected_overlaps {
        assert!(rejected.file.is_absolute(), "{} overlap file should be absolute", path.display());
        assert!(rejected.edit_a.end > rejected.edit_a.start, "{} overlap edit_a invalid", path.display());
        assert!(rejected.edit_b.end > rejected.edit_b.start, "{} overlap edit_b invalid", path.display());
        assert!(!rejected.reason.is_empty(), "{} overlap missing reason", path.display());
    }
}

fn assert_common_envelope(path: &Path, version: u32, routing_backend: &str, routing_reason: &str) {
    assert_eq!(version, 1, "{} must remain on schema version 1", path.display());
    assert!(!routing_backend.is_empty(), "{} missing routing_backend", path.display());
    assert!(!routing_reason.is_empty(), "{} missing routing_reason", path.display());
}

fn parse_json_document<T>(path: &Path) -> T
where
    T: DeserializeOwned,
{
    let json = fs::read_to_string(path)
        .unwrap_or_else(|error| panic!("failed to read {}: {error}", path.display()));
    serde_json::from_str(&json)
        .unwrap_or_else(|error| panic!("{} failed schema validation: {error}", path.display()))
}

fn example_file_name(path: &Path) -> &str {
    path.file_name()
        .and_then(|name| name.to_str())
        .unwrap_or_else(|| panic!("invalid example file name: {}", path.display()))
}

fn repo_root() -> PathBuf {
    Path::new(env!("CARGO_MANIFEST_DIR"))
        .parent()
        .expect("rust_core must live under the repo root")
        .to_path_buf()
}
