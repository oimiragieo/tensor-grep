use serde::de::DeserializeOwned;
use serde::Deserialize;
use std::collections::{BTreeSet, HashMap};
use std::fs;
use std::path::{Path, PathBuf};

const EXPECTED_EXAMPLES: &[&str] = &[
    "calibrate.json",
    "context_pack.json",
    "context_render.json",
    "gpu_sidecar_search.json",
    "index_search.json",
    "mcp_rewrite_diff.json",
    "repo_map.json",
    "defs.json",
    "source.json",
    "impact.json",
    "refs.json",
    "callers.json",
    "session_open.json",
    "session_context.json",
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
struct RepoSymbolExample {
    name: String,
    kind: String,
    file: PathBuf,
    line: usize,
    #[serde(default)]
    score: Option<i64>,
}

#[derive(Debug, Deserialize)]
#[serde(deny_unknown_fields)]
struct CoverageExample {
    language_scope: String,
    symbol_navigation: String,
    test_matching: String,
}

#[derive(Debug, Deserialize)]
#[serde(deny_unknown_fields)]
struct SymbolDefsExample {
    version: u32,
    routing_backend: String,
    routing_reason: String,
    sidecar_used: bool,
    coverage: CoverageExample,
    path: String,
    symbol: String,
    files: Vec<String>,
    symbols: Vec<RepoSymbolExample>,
    imports: Vec<serde_json::Value>,
    tests: Vec<String>,
    related_paths: Vec<String>,
    definitions: Vec<RepoSymbolExample>,
}

#[derive(Debug, Deserialize)]
#[serde(deny_unknown_fields)]
struct SymbolImpactExample {
    version: u32,
    routing_backend: String,
    routing_reason: String,
    sidecar_used: bool,
    coverage: CoverageExample,
    path: String,
    symbol: String,
    definitions: Vec<RepoSymbolExample>,
    files: Vec<String>,
    file_matches: Vec<RankedPathMatchExample>,
    file_summaries: Vec<FileSummaryExample>,
    tests: Vec<String>,
    test_matches: Vec<RankedPathMatchExample>,
    imports: Vec<serde_json::Value>,
    symbols: Vec<RepoSymbolExample>,
    related_paths: Vec<String>,
}

#[derive(Debug, Deserialize)]
#[serde(deny_unknown_fields)]
struct SymbolSourceBlockExample {
    name: String,
    kind: String,
    file: PathBuf,
    start_line: usize,
    end_line: usize,
    source: String,
}

#[derive(Debug, Deserialize)]
#[serde(deny_unknown_fields)]
struct SymbolSourceExample {
    version: u32,
    routing_backend: String,
    routing_reason: String,
    sidecar_used: bool,
    coverage: CoverageExample,
    path: String,
    symbol: String,
    files: Vec<String>,
    symbols: Vec<RepoSymbolExample>,
    imports: Vec<serde_json::Value>,
    tests: Vec<String>,
    related_paths: Vec<String>,
    definitions: Vec<RepoSymbolExample>,
    sources: Vec<SymbolSourceBlockExample>,
}

#[derive(Debug, Deserialize)]
#[serde(deny_unknown_fields)]
struct SymbolReferenceExample {
    name: String,
    kind: String,
    file: PathBuf,
    line: usize,
    text: String,
}

#[derive(Debug, Deserialize)]
#[serde(deny_unknown_fields)]
struct SymbolRefsExample {
    version: u32,
    routing_backend: String,
    routing_reason: String,
    sidecar_used: bool,
    coverage: CoverageExample,
    path: String,
    symbol: String,
    files: Vec<String>,
    symbols: Vec<RepoSymbolExample>,
    imports: Vec<serde_json::Value>,
    tests: Vec<String>,
    related_paths: Vec<String>,
    definitions: Vec<RepoSymbolExample>,
    references: Vec<SymbolReferenceExample>,
}

#[derive(Debug, Deserialize)]
#[serde(deny_unknown_fields)]
struct SymbolCallersExample {
    version: u32,
    routing_backend: String,
    routing_reason: String,
    sidecar_used: bool,
    coverage: CoverageExample,
    path: String,
    symbol: String,
    definitions: Vec<RepoSymbolExample>,
    callers: Vec<SymbolReferenceExample>,
    files: Vec<String>,
    tests: Vec<String>,
    imports: Vec<serde_json::Value>,
    symbols: Vec<RepoSymbolExample>,
    related_paths: Vec<String>,
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
    checkpoint: Option<CheckpointExample>,
    plan: RewritePlanExample,
    validation: Option<ValidationSummaryExample>,
    verification: Option<VerifyResultExample>,
}

#[derive(Debug, Deserialize)]
#[serde(deny_unknown_fields)]
struct CheckpointExample {
    checkpoint_id: String,
    mode: String,
    root: String,
    created_at: String,
    file_count: usize,
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
struct ValidationSummaryExample {
    success: bool,
    commands: Vec<ValidationCommandExample>,
}

#[derive(Debug, Deserialize)]
#[serde(deny_unknown_fields)]
struct ValidationCommandExample {
    kind: String,
    command: String,
    success: bool,
    exit_code: Option<i32>,
    stdout: String,
    stderr: String,
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

#[derive(Debug, Deserialize)]
#[serde(deny_unknown_fields)]
struct RepoMapExample {
    version: u32,
    routing_backend: String,
    routing_reason: String,
    sidecar_used: bool,
    coverage: CoverageExample,
    path: PathBuf,
    files: Vec<PathBuf>,
    symbols: Vec<RepoSymbolExample>,
    imports: Vec<RepoImportExample>,
    tests: Vec<PathBuf>,
    related_paths: Vec<PathBuf>,
}

#[derive(Debug, Deserialize)]
#[serde(deny_unknown_fields)]
struct RankedRepoSymbolExample {
    name: String,
    kind: String,
    file: PathBuf,
    line: usize,
    score: usize,
}

#[derive(Debug, Deserialize)]
#[serde(deny_unknown_fields)]
struct RepoImportExample {
    file: PathBuf,
    imports: Vec<String>,
}

#[derive(Debug, Deserialize)]
#[serde(deny_unknown_fields)]
struct RankedRepoImportExample {
    file: PathBuf,
    imports: Vec<String>,
    score: usize,
}

#[derive(Debug, Deserialize)]
#[serde(deny_unknown_fields)]
struct RankedPathMatchExample {
    path: PathBuf,
    score: usize,
    #[serde(default)]
    graph_score: Option<f64>,
    reasons: Vec<String>,
}

#[derive(Debug, Deserialize)]
#[serde(deny_unknown_fields)]
struct FileSummarySymbolExample {
    name: String,
    kind: String,
    line: usize,
}

#[derive(Debug, Deserialize)]
#[serde(deny_unknown_fields)]
struct FileSummaryExample {
    path: PathBuf,
    symbols: Vec<FileSummarySymbolExample>,
}

#[derive(Debug, Deserialize)]
#[serde(deny_unknown_fields)]
struct RenderSectionExample {
    kind: String,
    start: usize,
    end: usize,
    #[serde(default)]
    path: Option<PathBuf>,
    #[serde(default)]
    symbol: Option<String>,
    #[serde(default)]
    paths: Vec<PathBuf>,
}

#[derive(Debug, Deserialize)]
#[serde(deny_unknown_fields)]
struct CandidateEditTargetsExample {
    files: Vec<PathBuf>,
    symbols: Vec<RankedRepoSymbolExample>,
    tests: Vec<PathBuf>,
}

#[derive(Debug, Deserialize)]
#[serde(deny_unknown_fields)]
struct ContextPackExample {
    version: u32,
    routing_backend: String,
    routing_reason: String,
    sidecar_used: bool,
    coverage: CoverageExample,
    query: String,
    path: PathBuf,
    files: Vec<PathBuf>,
    file_matches: Vec<RankedPathMatchExample>,
    file_summaries: Vec<FileSummaryExample>,
    symbols: Vec<RankedRepoSymbolExample>,
    imports: Vec<RankedRepoImportExample>,
    tests: Vec<PathBuf>,
    test_matches: Vec<RankedPathMatchExample>,
    related_paths: Vec<PathBuf>,
}

#[derive(Debug, Deserialize)]
#[serde(deny_unknown_fields)]
struct ContextRenderExample {
    version: u32,
    routing_backend: String,
    routing_reason: String,
    sidecar_used: bool,
    coverage: CoverageExample,
    query: String,
    path: PathBuf,
    files: Vec<PathBuf>,
    file_matches: Vec<RankedPathMatchExample>,
    file_summaries: Vec<FileSummaryExample>,
    symbols: Vec<RankedRepoSymbolExample>,
    imports: Vec<RankedRepoImportExample>,
    tests: Vec<PathBuf>,
    test_matches: Vec<RankedPathMatchExample>,
    related_paths: Vec<PathBuf>,
    sources: Vec<SymbolSourceBlockExample>,
    max_files: usize,
    max_sources: usize,
    max_symbols_per_file: usize,
    max_render_chars: Option<usize>,
    truncated: bool,
    sections: Vec<RenderSectionExample>,
    candidate_edit_targets: CandidateEditTargetsExample,
    rendered_context: String,
}

#[derive(Debug, Deserialize)]
#[serde(deny_unknown_fields)]
struct SessionOpenExample {
    session_id: String,
    root: PathBuf,
    created_at: String,
    file_count: usize,
    symbol_count: usize,
}

#[derive(Debug, Deserialize)]
#[serde(deny_unknown_fields)]
struct SessionContextExample {
    version: u32,
    routing_backend: String,
    routing_reason: String,
    sidecar_used: bool,
    coverage: CoverageExample,
    query: String,
    path: PathBuf,
    files: Vec<PathBuf>,
    file_matches: Vec<RankedPathMatchExample>,
    file_summaries: Vec<FileSummaryExample>,
    symbols: Vec<RankedRepoSymbolExample>,
    imports: Vec<RankedRepoImportExample>,
    tests: Vec<PathBuf>,
    test_matches: Vec<RankedPathMatchExample>,
    related_paths: Vec<PathBuf>,
    session_id: String,
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
            "repo_map.json" => assert_repo_map_example(path),
            "context_pack.json" => assert_context_pack_example(path),
            "context_render.json" => assert_context_render_example(path),
            "defs.json" => assert_symbol_defs_example(path),
            "source.json" => assert_symbol_source_example(path),
            "impact.json" => assert_symbol_impact_example(path),
            "refs.json" => assert_symbol_refs_example(path),
            "callers.json" => assert_symbol_callers_example(path),
            "session_open.json" => assert_session_open_example(path),
            "session_context.json" => assert_session_context_example(path),
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
    if let Some(checkpoint) = &example.checkpoint {
        assert!(
            !checkpoint.checkpoint_id.is_empty(),
            "{} checkpoint id must not be empty",
            path.display()
        );
        assert!(
            checkpoint.mode == "filesystem-snapshot" || checkpoint.mode == "git-worktree-snapshot",
            "{} checkpoint mode must be a supported snapshot mode",
            path.display()
        );
        assert!(
            !checkpoint.root.is_empty(),
            "{} checkpoint root must not be empty",
            path.display()
        );
        assert!(
            !checkpoint.created_at.is_empty(),
            "{} checkpoint created_at must not be empty",
            path.display()
        );
        assert!(
            checkpoint.file_count > 0,
            "{} checkpoint file_count must be positive",
            path.display()
        );
    }

    if let Some(validation) = &example.validation {
        assert_eq!(
            validation.success,
            validation.commands.iter().all(|command| command.success),
            "{} validation success flag must match command results",
            path.display()
        );
        for command in &validation.commands {
            assert!(
                !command.kind.is_empty(),
                "{} validation command missing kind",
                path.display()
            );
            assert!(
                !command.command.is_empty(),
                "{} validation command missing command text",
                path.display()
            );
            if let Some(exit_code) = command.exit_code {
                if command.success {
                    assert_eq!(
                        exit_code,
                        0,
                        "{} successful validation command should exit 0",
                        path.display()
                    );
                }
            }
            if !command.success {
                assert!(
                    !command.stderr.is_empty() || command.exit_code.is_some(),
                    "{} failed validation command should report stderr or exit code",
                    path.display()
                );
            }
            let _ = &command.stdout;
        }
    }

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

fn assert_symbol_defs_example(path: &Path) {
    let example: SymbolDefsExample = parse_json_document(path);
    assert_common_envelope(
        path,
        example.version,
        &example.routing_backend,
        &example.routing_reason,
    );
    assert_eq!(example.routing_backend, "RepoMap");
    assert_eq!(example.routing_reason, "symbol-defs");
    assert!(
        !example.sidecar_used,
        "{} should be native symbol defs output",
        path.display()
    );
    assert_repo_map_coverage(path, &example.coverage);
    assert!(
        !example.path.is_empty(),
        "{} path must not be empty",
        path.display()
    );
    assert!(
        !example.symbol.is_empty(),
        "{} symbol must not be empty",
        path.display()
    );
    assert!(
        !example.definitions.is_empty(),
        "{} definitions must not be empty",
        path.display()
    );
    for definition in &example.definitions {
        assert_eq!(
            definition.name,
            example.symbol,
            "{} definition name must match symbol",
            path.display()
        );
        assert!(
            !definition.kind.is_empty(),
            "{} definition kind must not be empty",
            path.display()
        );
        assert!(
            definition.line > 0,
            "{} definition line must be positive",
            path.display()
        );
    }
    assert!(
        !example.files.is_empty(),
        "{} files must not be empty",
        path.display()
    );
    assert!(
        !example.symbols.is_empty(),
        "{} symbols must not be empty",
        path.display()
    );
    assert!(
        !example.related_paths.is_empty(),
        "{} related_paths must not be empty",
        path.display()
    );
    let _ = &example.imports;
    let _ = &example.tests;
}

fn assert_symbol_impact_example(path: &Path) {
    let example: SymbolImpactExample = parse_json_document(path);
    assert_common_envelope(
        path,
        example.version,
        &example.routing_backend,
        &example.routing_reason,
    );
    assert_eq!(example.routing_backend, "RepoMap");
    assert_eq!(example.routing_reason, "symbol-impact");
    assert!(
        !example.sidecar_used,
        "{} should be native symbol impact output",
        path.display()
    );
    assert_repo_map_coverage(path, &example.coverage);
    assert!(
        !example.path.is_empty(),
        "{} path must not be empty",
        path.display()
    );
    assert!(
        !example.symbol.is_empty(),
        "{} symbol must not be empty",
        path.display()
    );
    assert!(
        !example.definitions.is_empty(),
        "{} definitions must not be empty",
        path.display()
    );
    assert!(
        !example.files.is_empty(),
        "{} files must not be empty",
        path.display()
    );
    assert_eq!(
        example.files.len(),
        example.file_matches.len(),
        "{} file_matches should align with files",
        path.display()
    );
    assert_eq!(
        example.files.len(),
        example.file_summaries.len(),
        "{} file_summaries should align with files",
        path.display()
    );
    assert_eq!(
        example.tests.len(),
        example.test_matches.len(),
        "{} test_matches should align with tests",
        path.display()
    );
    assert!(
        !example.related_paths.is_empty(),
        "{} related_paths must not be empty",
        path.display()
    );
    for file_match in &example.file_matches {
        assert!(
            is_portable_absolute_path(&file_match.path),
            "{} file_match path should be absolute or an absolute Windows path literal",
            path.display()
        );
        assert!(
            file_match.score > 0,
            "{} file_match score must be positive",
            path.display()
        );
        if let Some(graph_score) = file_match.graph_score {
            assert!(
                graph_score >= 0.0,
                "{} file_match graph_score must be non-negative",
                path.display()
            );
        }
        assert!(
            !file_match.reasons.is_empty(),
            "{} file_match reasons must not be empty",
            path.display()
        );
    }
    for file_summary in &example.file_summaries {
        assert!(
            is_portable_absolute_path(&file_summary.path),
            "{} file_summary path should be absolute or an absolute Windows path literal",
            path.display()
        );
        assert!(
            !file_summary.symbols.is_empty(),
            "{} file_summary symbols must not be empty",
            path.display()
        );
        for symbol in &file_summary.symbols {
            assert!(
                !symbol.name.is_empty(),
                "{} file_summary symbol name must not be empty",
                path.display()
            );
            assert!(
                !symbol.kind.is_empty(),
                "{} file_summary symbol kind must not be empty",
                path.display()
            );
            assert!(
                symbol.line > 0,
                "{} file_summary symbol line must be 1-based",
                path.display()
            );
        }
    }
    for test_match in &example.test_matches {
        assert!(
            is_portable_absolute_path(&test_match.path),
            "{} test_match path should be absolute or an absolute Windows path literal",
            path.display()
        );
        assert!(
            test_match.score > 0,
            "{} test_match score must be positive",
            path.display()
        );
        assert!(
            !test_match.reasons.is_empty(),
            "{} test_match reasons must not be empty",
            path.display()
        );
    }
    for symbol in &example.symbols {
        assert!(
            symbol.score.unwrap_or_default() >= 0,
            "{} symbol score must be non-negative",
            path.display()
        );
    }
    let _ = &example.imports;
    let _ = &example.tests;
}

fn assert_symbol_source_example(path: &Path) {
    let example: SymbolSourceExample = parse_json_document(path);
    assert_common_envelope(
        path,
        example.version,
        &example.routing_backend,
        &example.routing_reason,
    );
    assert_eq!(example.routing_backend, "RepoMap");
    assert_eq!(example.routing_reason, "symbol-source");
    assert!(
        !example.sidecar_used,
        "{} should be native symbol source output",
        path.display()
    );
    assert_repo_map_coverage(path, &example.coverage);
    assert!(
        !example.path.is_empty(),
        "{} path must not be empty",
        path.display()
    );
    assert!(
        !example.symbol.is_empty(),
        "{} symbol must not be empty",
        path.display()
    );
    assert!(
        !example.definitions.is_empty(),
        "{} definitions must not be empty",
        path.display()
    );
    assert!(
        !example.sources.is_empty(),
        "{} sources must not be empty",
        path.display()
    );
    for source in &example.sources {
        assert!(
            !source.name.is_empty(),
            "{} source name must not be empty",
            path.display()
        );
        assert_eq!(
            source.name,
            example.symbol,
            "{} source name must match symbol",
            path.display()
        );
        assert!(
            !source.kind.is_empty(),
            "{} source kind must not be empty",
            path.display()
        );
        assert!(
            is_portable_absolute_path(&source.file),
            "{} source file should be absolute or an absolute Windows path literal",
            path.display()
        );
        assert!(
            source.start_line > 0,
            "{} source start_line must be positive",
            path.display()
        );
        assert!(
            source.end_line >= source.start_line,
            "{} source end_line must be >= start_line",
            path.display()
        );
        assert!(
            !source.source.trim().is_empty(),
            "{} source body must not be empty",
            path.display()
        );
    }
    let _ = &example.files;
    let _ = &example.symbols;
    let _ = &example.imports;
    let _ = &example.tests;
    let _ = &example.related_paths;
}

fn assert_symbol_refs_example(path: &Path) {
    let example: SymbolRefsExample = parse_json_document(path);
    assert_common_envelope(
        path,
        example.version,
        &example.routing_backend,
        &example.routing_reason,
    );
    assert_eq!(example.routing_backend, "RepoMap");
    assert_eq!(example.routing_reason, "symbol-refs");
    assert!(
        !example.sidecar_used,
        "{} should be native symbol refs output",
        path.display()
    );
    assert_repo_map_coverage(path, &example.coverage);
    assert!(
        !example.path.is_empty(),
        "{} path must not be empty",
        path.display()
    );
    assert!(
        !example.symbol.is_empty(),
        "{} symbol must not be empty",
        path.display()
    );
    assert!(
        !example.definitions.is_empty(),
        "{} definitions must not be empty",
        path.display()
    );
    assert!(
        !example.references.is_empty(),
        "{} references must not be empty",
        path.display()
    );
    for reference in &example.references {
        assert_eq!(
            reference.name,
            example.symbol,
            "{} reference name must match symbol",
            path.display()
        );
        assert_eq!(reference.kind, "reference");
        assert!(
            is_portable_absolute_path(&reference.file),
            "{} reference file should be absolute or an absolute Windows path literal",
            path.display()
        );
        assert!(
            reference.line > 0,
            "{} reference line must be positive",
            path.display()
        );
        assert!(
            !reference.text.is_empty(),
            "{} reference text must not be empty",
            path.display()
        );
    }
    assert!(
        !example.files.is_empty(),
        "{} files must not be empty",
        path.display()
    );
    assert!(
        !example.symbols.is_empty(),
        "{} symbols must not be empty",
        path.display()
    );
    assert!(
        !example.related_paths.is_empty(),
        "{} related_paths must not be empty",
        path.display()
    );
    let _ = &example.imports;
    let _ = &example.tests;
}

fn assert_symbol_callers_example(path: &Path) {
    let example: SymbolCallersExample = parse_json_document(path);
    assert_common_envelope(
        path,
        example.version,
        &example.routing_backend,
        &example.routing_reason,
    );
    assert_eq!(example.routing_backend, "RepoMap");
    assert_eq!(example.routing_reason, "symbol-callers");
    assert!(
        !example.sidecar_used,
        "{} should be native symbol callers output",
        path.display()
    );
    assert_repo_map_coverage(path, &example.coverage);
    assert!(
        !example.path.is_empty(),
        "{} path must not be empty",
        path.display()
    );
    assert!(
        !example.symbol.is_empty(),
        "{} symbol must not be empty",
        path.display()
    );
    assert!(
        !example.definitions.is_empty(),
        "{} definitions must not be empty",
        path.display()
    );
    assert!(
        !example.callers.is_empty(),
        "{} callers must not be empty",
        path.display()
    );
    for caller in &example.callers {
        assert_eq!(
            caller.name,
            example.symbol,
            "{} caller name must match symbol",
            path.display()
        );
        assert_eq!(caller.kind, "call");
        assert!(
            is_portable_absolute_path(&caller.file),
            "{} caller file should be absolute or an absolute Windows path literal",
            path.display()
        );
        assert!(
            caller.line > 0,
            "{} caller line must be positive",
            path.display()
        );
        assert!(
            !caller.text.is_empty(),
            "{} caller text must not be empty",
            path.display()
        );
    }
    assert!(
        !example.files.is_empty(),
        "{} files must not be empty",
        path.display()
    );
    assert!(
        !example.related_paths.is_empty(),
        "{} related_paths must not be empty",
        path.display()
    );
    let _ = &example.imports;
    let _ = &example.tests;
    let _ = &example.symbols;
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

fn assert_repo_map_example(path: &Path) {
    let example: RepoMapExample = parse_json_document(path);
    assert_common_envelope(
        path,
        example.version,
        &example.routing_backend,
        &example.routing_reason,
    );
    assert_eq!(
        example.routing_reason,
        "repo-map",
        "{} should keep repo-map routing reason",
        path.display()
    );
    assert!(
        !example.sidecar_used,
        "{} should stay native",
        path.display()
    );
    assert_repo_map_coverage(path, &example.coverage);
    assert!(
        is_portable_absolute_path(&example.path),
        "{} path should be absolute or an absolute Windows path literal",
        path.display()
    );
    assert!(
        !example.files.is_empty(),
        "{} should list files",
        path.display()
    );
    assert!(
        !example.symbols.is_empty(),
        "{} should list symbols",
        path.display()
    );
    assert!(
        !example.related_paths.is_empty(),
        "{} should list related paths",
        path.display()
    );
    for file in &example.files {
        assert!(
            is_portable_absolute_path(file),
            "{} file entry should be absolute or an absolute Windows path literal",
            path.display()
        );
    }
    for symbol in &example.symbols {
        assert!(
            !symbol.name.is_empty(),
            "{} symbol missing name",
            path.display()
        );
        assert!(
            !symbol.kind.is_empty(),
            "{} symbol missing kind",
            path.display()
        );
        assert!(
            is_portable_absolute_path(&symbol.file),
            "{} symbol file should be absolute or an absolute Windows path literal",
            path.display()
        );
        assert!(
            symbol.line > 0,
            "{} symbol line must be 1-based",
            path.display()
        );
    }
    for import in &example.imports {
        assert!(
            is_portable_absolute_path(&import.file),
            "{} import file should be absolute or an absolute Windows path literal",
            path.display()
        );
        assert!(
            !import.imports.is_empty(),
            "{} import entry should include at least one import",
            path.display()
        );
        for import_name in &import.imports {
            assert!(
                !import_name.is_empty(),
                "{} import name must not be empty",
                path.display()
            );
        }
    }
    for test_path in &example.tests {
        assert!(
            is_portable_absolute_path(test_path),
            "{} test path should be absolute or an absolute Windows path literal",
            path.display()
        );
    }
    for related_path in &example.related_paths {
        assert!(
            is_portable_absolute_path(related_path),
            "{} related path should be absolute or an absolute Windows path literal",
            path.display()
        );
    }
}

fn assert_context_pack_example(path: &Path) {
    let example: ContextPackExample = parse_json_document(path);
    assert_common_envelope(
        path,
        example.version,
        &example.routing_backend,
        &example.routing_reason,
    );
    assert_eq!(
        example.routing_reason,
        "context-pack",
        "{} should keep context-pack routing reason",
        path.display()
    );
    assert!(
        !example.sidecar_used,
        "{} should stay native",
        path.display()
    );
    assert_repo_map_coverage(path, &example.coverage);
    assert!(
        !example.query.is_empty(),
        "{} query must not be empty",
        path.display()
    );
    assert!(
        is_portable_absolute_path(&example.path),
        "{} path should be absolute or an absolute Windows path literal",
        path.display()
    );
    assert!(
        !example.files.is_empty(),
        "{} should rank files",
        path.display()
    );
    assert_eq!(
        example.files.len(),
        example.file_matches.len(),
        "{} file_matches should align with files",
        path.display()
    );
    assert_eq!(
        example.files.len(),
        example.file_summaries.len(),
        "{} file_summaries should align with files",
        path.display()
    );
    assert!(
        !example.symbols.is_empty(),
        "{} should rank symbols",
        path.display()
    );
    assert!(
        !example.related_paths.is_empty(),
        "{} should list related paths",
        path.display()
    );
    for file in &example.files {
        assert!(
            is_portable_absolute_path(file),
            "{} file entry should be absolute or an absolute Windows path literal",
            path.display()
        );
    }
    for file_match in &example.file_matches {
        assert!(
            is_portable_absolute_path(&file_match.path),
            "{} file_match path should be absolute or an absolute Windows path literal",
            path.display()
        );
        assert!(
            file_match.score > 0,
            "{} file_match score must be positive",
            path.display()
        );
        if let Some(graph_score) = file_match.graph_score {
            assert!(
                graph_score >= 0.0,
                "{} file_match graph_score must be non-negative",
                path.display()
            );
        }
        assert!(
            !file_match.reasons.is_empty(),
            "{} file_match reasons must not be empty",
            path.display()
        );
    }
    for file_summary in &example.file_summaries {
        assert!(
            is_portable_absolute_path(&file_summary.path),
            "{} file_summary path should be absolute or an absolute Windows path literal",
            path.display()
        );
        assert!(
            !file_summary.symbols.is_empty(),
            "{} file_summary symbols must not be empty",
            path.display()
        );
    }
    for symbol in &example.symbols {
        assert!(
            !symbol.name.is_empty(),
            "{} symbol missing name",
            path.display()
        );
        assert!(
            !symbol.kind.is_empty(),
            "{} symbol missing kind",
            path.display()
        );
        assert!(
            is_portable_absolute_path(&symbol.file),
            "{} symbol file should be absolute or an absolute Windows path literal",
            path.display()
        );
        assert!(
            symbol.line > 0,
            "{} symbol line must be 1-based",
            path.display()
        );
        assert!(
            symbol.score > 0,
            "{} symbol score must be positive",
            path.display()
        );
    }
    for import in &example.imports {
        assert!(
            is_portable_absolute_path(&import.file),
            "{} import file should be absolute or an absolute Windows path literal",
            path.display()
        );
        assert!(
            import.score > 0,
            "{} import score must be positive",
            path.display()
        );
        for import_name in &import.imports {
            assert!(
                !import_name.is_empty(),
                "{} import name must not be empty",
                path.display()
            );
        }
    }
    for test_path in &example.tests {
        assert!(
            is_portable_absolute_path(test_path),
            "{} test path should be absolute or an absolute Windows path literal",
            path.display()
        );
    }
    assert_eq!(
        example.tests.len(),
        example.test_matches.len(),
        "{} test_matches should align with tests",
        path.display()
    );
    for test_match in &example.test_matches {
        assert!(
            is_portable_absolute_path(&test_match.path),
            "{} test_match path should be absolute or an absolute Windows path literal",
            path.display()
        );
        assert!(
            test_match.score > 0,
            "{} test_match score must be positive",
            path.display()
        );
        assert!(
            !test_match.reasons.is_empty(),
            "{} test_match reasons must not be empty",
            path.display()
        );
    }
    for related_path in &example.related_paths {
        assert!(
            is_portable_absolute_path(related_path),
            "{} related path should be absolute or an absolute Windows path literal",
            path.display()
        );
    }
}

fn assert_context_render_example(path: &Path) {
    let example: ContextRenderExample = parse_json_document(path);
    assert_common_envelope(
        path,
        example.version,
        &example.routing_backend,
        &example.routing_reason,
    );
    assert_eq!(
        example.routing_reason,
        "context-render",
        "{} should keep context-render routing reason",
        path.display()
    );
    assert!(
        !example.sidecar_used,
        "{} should stay native",
        path.display()
    );
    assert_repo_map_coverage(path, &example.coverage);
    assert!(
        !example.query.is_empty(),
        "{} query must not be empty",
        path.display()
    );
    assert!(
        is_portable_absolute_path(&example.path),
        "{} path should be absolute or an absolute Windows path literal",
        path.display()
    );
    assert!(
        !example.files.is_empty(),
        "{} should rank files",
        path.display()
    );
    assert_eq!(
        example.files.len(),
        example.file_matches.len(),
        "{} file_matches should align with files",
        path.display()
    );
    assert_eq!(
        example.files.len(),
        example.file_summaries.len(),
        "{} file_summaries should align with files",
        path.display()
    );
    assert!(
        !example.symbols.is_empty(),
        "{} should rank symbols",
        path.display()
    );
    assert!(
        !example.sources.is_empty(),
        "{} should include rendered source blocks",
        path.display()
    );
    assert!(
        example.max_files > 0,
        "{} max_files must be positive",
        path.display()
    );
    assert!(
        example.max_sources > 0,
        "{} max_sources must be positive",
        path.display()
    );
    assert!(
        example.max_symbols_per_file > 0,
        "{} max_symbols_per_file must be positive",
        path.display()
    );
    if let Some(max_render_chars) = example.max_render_chars {
        assert!(
            max_render_chars > 0,
            "{} max_render_chars must be positive when present",
            path.display()
        );
    }
    let _ = example.truncated;
    assert!(
        !example.sections.is_empty(),
        "{} sections must not be empty",
        path.display()
    );
    for section in &example.sections {
        assert!(
            !section.kind.is_empty(),
            "{} section kind must not be empty",
            path.display()
        );
        assert!(
            section.end >= section.start,
            "{} section offsets must be ordered",
            path.display()
        );
        if let Some(section_path) = &section.path {
            assert!(
                is_portable_absolute_path(section_path),
                "{} section path should be absolute or an absolute Windows path literal",
                path.display()
            );
        }
        if let Some(symbol) = &section.symbol {
            assert!(
                !symbol.is_empty(),
                "{} section symbol must not be empty",
                path.display()
            );
        }
        for section_path in &section.paths {
            assert!(
                is_portable_absolute_path(section_path),
                "{} section paths should be absolute or an absolute Windows path literal",
                path.display()
            );
        }
    }
    for candidate_file in &example.candidate_edit_targets.files {
        assert!(
            is_portable_absolute_path(candidate_file),
            "{} candidate file should be absolute or an absolute Windows path literal",
            path.display()
        );
    }
    for candidate_symbol in &example.candidate_edit_targets.symbols {
        assert!(
            !candidate_symbol.name.is_empty(),
            "{} candidate symbol name must not be empty",
            path.display()
        );
    }
    for candidate_test in &example.candidate_edit_targets.tests {
        assert!(
            is_portable_absolute_path(candidate_test),
            "{} candidate test should be absolute or an absolute Windows path literal",
            path.display()
        );
    }
    assert!(
        !example.rendered_context.is_empty(),
        "{} rendered_context must not be empty",
        path.display()
    );
    for import in &example.imports {
        assert!(
            is_portable_absolute_path(&import.file),
            "{} import file should be absolute or an absolute Windows path literal",
            path.display()
        );
    }
    for test_path in &example.tests {
        assert!(
            is_portable_absolute_path(test_path),
            "{} test path should be absolute or an absolute Windows path literal",
            path.display()
        );
    }
    assert_eq!(
        example.tests.len(),
        example.test_matches.len(),
        "{} test_matches should align with tests",
        path.display()
    );
    for related_path in &example.related_paths {
        assert!(
            is_portable_absolute_path(related_path),
            "{} related path should be absolute or an absolute Windows path literal",
            path.display()
        );
    }
}

fn assert_session_open_example(path: &Path) {
    let example: SessionOpenExample = parse_json_document(path);
    assert!(
        !example.session_id.is_empty(),
        "{} session_id must not be empty",
        path.display()
    );
    assert!(
        is_portable_absolute_path(&example.root),
        "{} root should be absolute or an absolute Windows path literal",
        path.display()
    );
    assert!(
        !example.created_at.is_empty(),
        "{} created_at must not be empty",
        path.display()
    );
    assert!(
        example.file_count > 0,
        "{} file_count must be positive",
        path.display()
    );
    assert!(
        example.symbol_count > 0,
        "{} symbol_count must be positive",
        path.display()
    );
}

fn assert_session_context_example(path: &Path) {
    let example: SessionContextExample = parse_json_document(path);
    assert_common_envelope(
        path,
        example.version,
        &example.routing_backend,
        &example.routing_reason,
    );
    assert_eq!(
        example.routing_reason,
        "session-context",
        "{} should keep session-context routing reason",
        path.display()
    );
    assert!(
        !example.sidecar_used,
        "{} should stay native",
        path.display()
    );
    assert_repo_map_coverage(path, &example.coverage);
    assert!(
        !example.query.is_empty(),
        "{} query must not be empty",
        path.display()
    );
    assert!(
        is_portable_absolute_path(&example.path),
        "{} path should be absolute or an absolute Windows path literal",
        path.display()
    );
    assert!(
        !example.files.is_empty(),
        "{} should rank files",
        path.display()
    );
    assert_eq!(
        example.files.len(),
        example.file_matches.len(),
        "{} file_matches should align with files",
        path.display()
    );
    assert_eq!(
        example.files.len(),
        example.file_summaries.len(),
        "{} file_summaries should align with files",
        path.display()
    );
    assert!(
        !example.symbols.is_empty(),
        "{} should rank symbols",
        path.display()
    );
    assert!(
        !example.session_id.is_empty(),
        "{} session_id must not be empty",
        path.display()
    );
    for file in &example.files {
        assert!(
            is_portable_absolute_path(file),
            "{} file entry should be absolute or an absolute Windows path literal",
            path.display()
        );
    }
    for file_match in &example.file_matches {
        assert!(
            is_portable_absolute_path(&file_match.path),
            "{} file_match path should be absolute or an absolute Windows path literal",
            path.display()
        );
        assert!(
            file_match.score > 0,
            "{} file_match score must be positive",
            path.display()
        );
        if let Some(graph_score) = file_match.graph_score {
            assert!(
                graph_score >= 0.0,
                "{} file_match graph_score must be non-negative",
                path.display()
            );
        }
        assert!(
            !file_match.reasons.is_empty(),
            "{} file_match reasons must not be empty",
            path.display()
        );
    }
    for file_summary in &example.file_summaries {
        assert!(
            is_portable_absolute_path(&file_summary.path),
            "{} file_summary path should be absolute or an absolute Windows path literal",
            path.display()
        );
        assert!(
            !file_summary.symbols.is_empty(),
            "{} file_summary symbols must not be empty",
            path.display()
        );
    }
    for symbol in &example.symbols {
        assert!(
            !symbol.name.is_empty(),
            "{} symbol missing name",
            path.display()
        );
        assert!(
            !symbol.kind.is_empty(),
            "{} symbol missing kind",
            path.display()
        );
        assert!(
            is_portable_absolute_path(&symbol.file),
            "{} symbol file should be absolute or an absolute Windows path literal",
            path.display()
        );
        assert!(
            symbol.line > 0,
            "{} symbol line must be 1-based",
            path.display()
        );
        assert!(
            symbol.score > 0,
            "{} symbol score must be positive",
            path.display()
        );
    }
    for import in &example.imports {
        assert!(
            is_portable_absolute_path(&import.file),
            "{} import file should be absolute or an absolute Windows path literal",
            path.display()
        );
        let _ = import.score;
    }
    for test_path in &example.tests {
        assert!(
            is_portable_absolute_path(test_path),
            "{} test path should be absolute or an absolute Windows path literal",
            path.display()
        );
    }
    assert_eq!(
        example.tests.len(),
        example.test_matches.len(),
        "{} test_matches should align with tests",
        path.display()
    );
    for test_match in &example.test_matches {
        assert!(
            is_portable_absolute_path(&test_match.path),
            "{} test_match path should be absolute or an absolute Windows path literal",
            path.display()
        );
        assert!(
            test_match.score > 0,
            "{} test_match score must be positive",
            path.display()
        );
        assert!(
            !test_match.reasons.is_empty(),
            "{} test_match reasons must not be empty",
            path.display()
        );
    }
    for related_path in &example.related_paths {
        assert!(
            is_portable_absolute_path(related_path),
            "{} related path should be absolute or an absolute Windows path literal",
            path.display()
        );
    }
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

fn assert_repo_map_coverage(path: &Path, coverage: &CoverageExample) {
    assert_eq!(
        coverage.language_scope,
        "python-js-ts-rust",
        "{} coverage.language_scope must stay python-js-ts-rust",
        path.display()
    );
    assert_eq!(
        coverage.symbol_navigation,
        "python-ast+parser-js-ts-rust",
        "{} coverage.symbol_navigation must stay python-ast+parser-js-ts-rust",
        path.display()
    );
    assert_eq!(
        coverage.test_matching,
        "filename+import+graph-heuristic",
        "{} coverage.test_matching must stay filename+import+graph-heuristic",
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
