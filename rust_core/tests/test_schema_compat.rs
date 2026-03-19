use serde::de::DeserializeOwned;
use serde::Deserialize;
use std::collections::{BTreeSet, HashMap};
use std::fs;
use std::path::{Path, PathBuf};

const EXPECTED_EXAMPLES: &[&str] = &[
    "calibrate.json",
    "gpu_sidecar_search.json",
    "index_search.json",
    "mcp_rewrite_diff.json",
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
struct CalibrateExample {
    version: u32,
    routing_backend: String,
    routing_reason: String,
    sidecar_used: bool,
    corpus_size_breakpoint_bytes: u64,
    cpu_median_ms: f64,
    gpu_median_ms: f64,
    recommendation: String,
    calibration_timestamp: u64,
    device_name: String,
    measurements: Vec<CalibrateMeasurementExample>,
}

#[derive(Debug, Deserialize)]
#[serde(deny_unknown_fields)]
struct CalibrateMeasurementExample {
    size_bytes: u64,
    cpu_median_ms: f64,
    gpu_median_ms: f64,
    cpu_samples_ms: Vec<f64>,
    gpu_samples_ms: Vec<f64>,
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
    #[serde(default)]
    planned_mtime_ns: Option<u64>,
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

#[derive(Debug, Deserialize)]
#[serde(deny_unknown_fields)]
struct McpRewriteDiffExample {
    version: u32,
    routing_backend: String,
    routing_reason: String,
    sidecar_used: bool,
    diff: String,
}

#[derive(Debug, Deserialize)]
#[serde(deny_unknown_fields)]
struct SearchNdjsonExample {
    version: u32,
    routing_backend: String,
    routing_reason: String,
    sidecar_used: bool,
    query: String,
    path: String,
    file: String,
    line: usize,
    text: String,
    pattern_id: Option<usize>,
    pattern_text: Option<String>,
}

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
            "search.json" | "index_search.json" => assert_search_example(path),
            "rewrite_plan.json" => assert_rewrite_plan_example(path),
            "rewrite_apply_verify.json" => assert_apply_verify_example(path),
            "gpu_sidecar_search.json" => assert_gpu_sidecar_example(path),
            "calibrate.json" => assert_calibrate_example(path),
            "mcp_rewrite_diff.json" => assert_mcp_rewrite_diff_example(path),
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
        assert!(!row.text.is_empty(), "{} row missing text", path.display());
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

fn assert_search_example(path: &Path) {
    let example: SearchExample = parse_json_document(path);
    assert_common_envelope(
        path,
        example.version,
        &example.routing_backend,
        &example.routing_reason,
    );
    assert!(
        !example.query.is_empty(),
        "{} has empty query",
        path.display()
    );
    assert!(
        !example.path.is_empty(),
        "{} has empty path",
        path.display()
    );
    assert!(
        !example.sidecar_used,
        "{} should be native search output",
        path.display()
    );
    assert!(
        example.total_matches > 0,
        "{} should contain at least one match",
        path.display()
    );
    assert_eq!(
        example.total_matches,
        example.matches.len(),
        "{} total_matches mismatch",
        path.display()
    );
    for matched in &example.matches {
        assert!(
            !matched.file.is_empty(),
            "{} match missing file",
            path.display()
        );
        assert!(
            matched.line > 0,
            "{} match line must be 1-based",
            path.display()
        );
        assert!(
            !matched.text.is_empty(),
            "{} match missing text",
            path.display()
        );
    }
}

fn assert_rewrite_plan_example(path: &Path) {
    let plan: RewritePlanExample = parse_json_document(path);
    assert_rewrite_plan_payload(path, &plan);
}

fn assert_apply_verify_example(path: &Path) {
    let example: ApplyVerifyExample = parse_json_document(path);
    assert_common_envelope(
        path,
        example.version,
        &example.routing_backend,
        &example.routing_reason,
    );
    assert!(
        !example.sidecar_used,
        "{} should be native apply+verify output",
        path.display()
    );
    assert_rewrite_plan_payload(path, &example.plan);

    let verification = example
        .verification
        .as_ref()
        .unwrap_or_else(|| panic!("{} missing verification payload", path.display()));
    assert_eq!(
        verification.total_edits,
        example.plan.total_edits,
        "{} verification total_edits mismatch",
        path.display()
    );
    assert_eq!(
        verification.total_edits,
        verification.verified + verification.mismatches.len(),
        "{} verification counts are inconsistent",
        path.display()
    );
    for mismatch in &verification.mismatches {
        assert!(
            !mismatch.edit_id.is_empty(),
            "{} mismatch missing edit_id",
            path.display()
        );
        assert!(
            mismatch.file.is_absolute(),
            "{} mismatch file should be absolute",
            path.display()
        );
        assert!(
            mismatch.line > 0,
            "{} mismatch line must be 1-based",
            path.display()
        );
        assert!(
            !mismatch.expected.is_empty(),
            "{} mismatch missing expected text",
            path.display()
        );
        assert!(
            !mismatch.actual.is_empty(),
            "{} mismatch missing actual text",
            path.display()
        );
    }
}

fn assert_gpu_sidecar_example(path: &Path) {
    let example: GpuSidecarExample = parse_json_document(path);
    assert_common_envelope(
        path,
        example.version,
        &example.routing_backend,
        &example.routing_reason,
    );
    assert!(
        example.sidecar_used,
        "{} should report sidecar_used=true",
        path.display()
    );
    assert!(
        example.total_files > 0,
        "{} should report total_files > 0",
        path.display()
    );
    assert!(
        example.total_matches > 0,
        "{} should report total_matches > 0",
        path.display()
    );
    assert_eq!(
        example.total_matches,
        example.matches.len(),
        "{} total_matches mismatch",
        path.display()
    );
    assert!(
        !example.routing_gpu_device_ids.is_empty(),
        "{} should list at least one GPU device id",
        path.display()
    );
    for matched in &example.matches {
        assert!(
            !matched.file.is_empty(),
            "{} match missing file",
            path.display()
        );
        assert!(
            matched.line_number > 0,
            "{} line_number must be 1-based",
            path.display()
        );
        assert!(
            !matched.text.is_empty(),
            "{} match missing text",
            path.display()
        );
    }
}

fn assert_calibrate_example(path: &Path) {
    let example: CalibrateExample = parse_json_document(path);
    assert_common_envelope(
        path,
        example.version,
        &example.routing_backend,
        &example.routing_reason,
    );
    assert!(
        !example.sidecar_used,
        "{} calibrate output should stay native",
        path.display()
    );
    assert!(
        example.corpus_size_breakpoint_bytes > 0,
        "{} breakpoint must be positive",
        path.display()
    );
    assert!(
        example.cpu_median_ms > 0.0,
        "{} cpu_median_ms must be positive",
        path.display()
    );
    assert!(
        example.gpu_median_ms > 0.0,
        "{} gpu_median_ms must be positive",
        path.display()
    );
    assert!(
        !example.recommendation.is_empty(),
        "{} recommendation missing",
        path.display()
    );
    assert!(
        example.calibration_timestamp > 0,
        "{} calibration timestamp missing",
        path.display()
    );
    assert!(
        !example.device_name.is_empty(),
        "{} device_name missing",
        path.display()
    );
    assert!(
        !example.measurements.is_empty(),
        "{} should contain at least one calibration measurement",
        path.display()
    );
    for measurement in &example.measurements {
        assert!(
            measurement.size_bytes > 0,
            "{} measurement size_bytes missing",
            path.display()
        );
        assert!(
            measurement.cpu_median_ms > 0.0,
            "{} measurement cpu_median_ms missing",
            path.display()
        );
        assert!(
            measurement.gpu_median_ms > 0.0,
            "{} measurement gpu_median_ms missing",
            path.display()
        );
        assert!(
            !measurement.cpu_samples_ms.is_empty(),
            "{} measurement cpu_samples_ms missing",
            path.display()
        );
        assert!(
            !measurement.gpu_samples_ms.is_empty(),
            "{} measurement gpu_samples_ms missing",
            path.display()
        );
    }
}

fn assert_mcp_rewrite_diff_example(path: &Path) {
    let example: McpRewriteDiffExample = parse_json_document(path);
    assert_common_envelope(
        path,
        example.version,
        &example.routing_backend,
        &example.routing_reason,
    );
    assert!(
        !example.sidecar_used,
        "{} should be native MCP rewrite diff output",
        path.display()
    );
    assert!(
        example.diff.contains("--- "),
        "{} diff missing original file header",
        path.display()
    );
    assert!(
        example.diff.contains("+++ "),
        "{} diff missing rewritten file header",
        path.display()
    );
    assert!(
        example.diff.contains("@@ "),
        "{} diff missing hunk header",
        path.display()
    );
}

fn assert_rewrite_plan_payload(path: &Path, plan: &RewritePlanExample) {
    assert_common_envelope(
        path,
        plan.version,
        &plan.routing_backend,
        &plan.routing_reason,
    );
    assert!(
        !plan.sidecar_used,
        "{} should be native rewrite output",
        path.display()
    );
    assert!(
        !plan.pattern.is_empty(),
        "{} has empty pattern",
        path.display()
    );
    assert!(
        !plan.replacement.is_empty(),
        "{} has empty replacement",
        path.display()
    );
    assert!(!plan.lang.is_empty(), "{} has empty lang", path.display());
    assert!(
        plan.total_files_scanned > 0,
        "{} should scan at least one file",
        path.display()
    );
    assert!(
        plan.total_edits > 0,
        "{} should contain at least one edit",
        path.display()
    );
    assert_eq!(
        plan.total_edits,
        plan.edits.len(),
        "{} total_edits mismatch",
        path.display()
    );
    for edit in &plan.edits {
        assert!(!edit.id.is_empty(), "{} edit missing id", path.display());
        assert!(
            is_portable_absolute_path(&edit.file),
            "{} edit file should be absolute or an absolute Windows path literal",
            path.display()
        );
        if let Some(planned_mtime_ns) = edit.planned_mtime_ns {
            assert!(
                planned_mtime_ns > 0,
                "{} edit planned_mtime_ns must be positive",
                path.display()
            );
        }
        assert!(
            edit.line > 0,
            "{} edit line must be 1-based",
            path.display()
        );
        assert!(
            edit.byte_range.end > edit.byte_range.start,
            "{} edit byte range invalid",
            path.display()
        );
        assert!(
            !edit.original_text.is_empty(),
            "{} edit missing original_text",
            path.display()
        );
        assert!(
            !edit.replacement_text.is_empty(),
            "{} edit missing replacement_text",
            path.display()
        );
        assert!(
            !edit.metavar_env.is_empty(),
            "{} edit missing metavariables",
            path.display()
        );
    }
    for rejected in &plan.rejected_overlaps {
        assert!(
            is_portable_absolute_path(&rejected.file),
            "{} overlap file should be absolute or an absolute Windows path literal",
            path.display()
        );
        assert!(
            rejected.edit_a.end > rejected.edit_a.start,
            "{} overlap edit_a invalid",
            path.display()
        );
        assert!(
            rejected.edit_b.end > rejected.edit_b.start,
            "{} overlap edit_b invalid",
            path.display()
        );
        assert!(
            !rejected.reason.is_empty(),
            "{} overlap missing reason",
            path.display()
        );
    }
}

fn assert_common_envelope(path: &Path, version: u32, routing_backend: &str, routing_reason: &str) {
    assert_eq!(
        version,
        1,
        "{} must remain on schema version 1",
        path.display()
    );
    assert!(
        !routing_backend.is_empty(),
        "{} missing routing_backend",
        path.display()
    );
    assert!(
        !routing_reason.is_empty(),
        "{} missing routing_reason",
        path.display()
    );
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

fn is_portable_absolute_path(path: &Path) -> bool {
    path.is_absolute() || is_windows_absolute_path_literal(&path.to_string_lossy())
}

fn is_windows_absolute_path_literal(value: &str) -> bool {
    let bytes = value.as_bytes();
    bytes.len() >= 3
        && bytes[0].is_ascii_alphabetic()
        && bytes[1] == b':'
        && (bytes[2] == b'\\' || bytes[2] == b'/')
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
