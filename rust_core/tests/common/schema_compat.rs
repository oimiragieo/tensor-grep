#![allow(unused_imports, dead_code)]
use serde::de::DeserializeOwned;
use serde::Deserialize;
use std::collections::{BTreeSet, HashMap};
use std::fs;
use std::path::{Path, PathBuf};

pub const EXPECTED_EXAMPLES: &[&str] = &[
    "attempt_ledger.json",
    "ast_run.json",
    "audit_manifest_verify.json",
    "blast_radius.json",
    "blast_radius_plan.json",
    "blast_radius_render.json",
    "calibrate.json",
    "callers.json",
    "context_pack.json",
    "context_render.json",
    "defs.json",
    "defs_provider_disagreement.json",
    "edit_plan.json",
    "gpu_cpu_fallback_search.json",
    "gpu_sidecar_search.json",
    "impact.json",
    "index_search.json",
    "mcp_rewrite_diff.json",
    "multi_session_attempt_ledger.json",
    "multi_task_attempt_ledger.json",
    "patch_bakeoff.json",
    "patch_bakeoff_incomplete.json",
    "patch_bakeoff_no_patch.json",
    "provider_status_unavailable.json",
    "refs.json",
    "repo_map.json",
    "rewrite_apply_verify.json",
    "rewrite_apply_verify_validation_failed.json",
    "rewrite_plan.json",
    "ruleset_scan.json",
    "rulesets.json",
    "session_context.json",
    "session_invalid_request_stale.json",
    "session_open.json",
    "search.json",
    "source.json",
];

#[allow(dead_code)]
#[derive(Debug, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct SearchExample {
    pub version: u32,
    pub routing_backend: String,
    pub routing_reason: String,
    pub sidecar_used: bool,
    #[serde(default)]
    pub requested_gpu_device_ids: Vec<u32>,
    #[serde(default)]
    pub routing_gpu_device_ids: Vec<u32>,
    pub query: String,
    pub path: String,
    pub total_matches: usize,
    pub matches: Vec<SearchMatch>,
}

#[allow(dead_code)]
#[derive(Debug, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct RulesetMetadataExample {
    pub name: String,
    pub description: String,
    pub category: String,
    pub status: String,
    pub default_language: String,
    pub languages: Vec<String>,
    pub rule_count: usize,
}

#[allow(dead_code)]
#[derive(Debug, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct RulesetsExample {
    pub version: u32,
    pub routing_backend: String,
    pub routing_reason: String,
    pub sidecar_used: bool,
    pub rulesets: Vec<RulesetMetadataExample>,
}

#[allow(dead_code)]
#[derive(Debug, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct RulesetFindingExample {
    pub rule_id: String,
    pub language: String,
    pub severity: String,
    pub message: String,
    pub fingerprint: String,
    pub status: Option<String>,
    pub matches: usize,
    pub files: Vec<String>,
    pub evidence: Vec<RulesetEvidenceExample>,
}

#[allow(dead_code)]
#[derive(Debug, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct RulesetEvidenceExample {
    pub file: String,
    pub match_count: usize,
    #[serde(default)]
    pub snippets: Vec<RulesetEvidenceSnippetExample>,
}

#[allow(dead_code)]
#[derive(Debug, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct RulesetEvidenceSnippetExample {
    pub text: String,
    pub truncated: bool,
}

#[allow(dead_code)]
#[derive(Debug, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct RulesetScanExample {
    pub version: u32,
    pub routing_backend: String,
    pub routing_reason: String,
    pub sidecar_used: bool,
    pub config_path: String,
    pub path: String,
    pub ruleset: String,
    pub language: String,
    pub rule_count: usize,
    pub matched_rules: usize,
    pub total_matches: usize,
    pub backends: Vec<String>,
    pub findings: Vec<RulesetFindingExample>,
    pub baseline: Option<RulesetBaselineSummaryExample>,
    pub baseline_written: Option<RulesetBaselineWrittenExample>,
    pub suppressions: Option<RulesetSuppressionsSummaryExample>,
    pub suppressions_written: Option<RulesetBaselineWrittenExample>,
}

#[allow(dead_code)]
#[derive(Debug, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct RulesetBaselineSummaryExample {
    pub path: String,
    pub new_findings: usize,
    pub existing_findings: usize,
    pub resolved_findings: usize,
    pub resolved_fingerprints: Vec<String>,
}

#[allow(dead_code)]
#[derive(Debug, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct RulesetBaselineWrittenExample {
    pub path: String,
    pub fingerprints: Vec<String>,
    pub count: usize,
}

#[allow(dead_code)]
#[derive(Debug, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct RulesetSuppressionsSummaryExample {
    pub path: String,
    pub suppressed_findings: usize,
}

#[allow(dead_code)]
#[derive(Debug, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct RepoSymbolExample {
    pub name: String,
    pub kind: String,
    pub file: PathBuf,
    pub line: usize,
    #[serde(default)]
    pub start_line: Option<usize>,
    #[serde(default)]
    pub end_line: Option<usize>,
    #[serde(default)]
    pub provenance: Option<String>,
    #[serde(default)]
    pub score: Option<i64>,
}

#[allow(dead_code)]
#[derive(Debug, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct CoverageExample {
    pub language_scope: String,
    pub symbol_navigation: String,
    pub test_matching: String,
}

#[allow(dead_code)]
#[derive(Debug, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct SymbolDefsExample {
    pub version: u32,
    pub routing_backend: String,
    pub routing_reason: String,
    pub sidecar_used: bool,
    pub coverage: CoverageExample,
    pub path: String,
    pub symbol: String,
    pub files: Vec<String>,
    pub symbols: Vec<RepoSymbolExample>,
    pub imports: Vec<serde_json::Value>,
    pub tests: Vec<String>,
    pub related_paths: Vec<String>,
    pub definitions: Vec<RepoSymbolExample>,
}

#[allow(dead_code)]
#[derive(Debug, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct SymbolImpactExample {
    pub version: u32,
    pub routing_backend: String,
    pub routing_reason: String,
    pub sidecar_used: bool,
    pub coverage: CoverageExample,
    pub path: String,
    pub symbol: String,
    pub definitions: Vec<RepoSymbolExample>,
    pub files: Vec<String>,
    pub file_matches: Vec<RankedPathMatchExample>,
    pub file_summaries: Vec<FileSummaryExample>,
    pub tests: Vec<String>,
    pub test_matches: Vec<RankedPathMatchExample>,
    pub imports: Vec<serde_json::Value>,
    pub symbols: Vec<RepoSymbolExample>,
    pub related_paths: Vec<String>,
}

#[allow(dead_code)]
#[derive(Debug, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct SymbolSourceBlockExample {
    pub name: String,
    pub kind: String,
    pub file: PathBuf,
    pub start_line: usize,
    pub end_line: usize,
    pub source: String,
    #[serde(default)]
    pub render_profile: Option<String>,
    #[serde(default)]
    pub optimize_context: Option<bool>,
    #[serde(default)]
    pub rendered_source: Option<String>,
    #[serde(default)]
    pub line_map: Vec<SourceLineMapEntryExample>,
    #[serde(default)]
    pub render_diagnostics: Option<RenderDiagnosticsExample>,
}

#[allow(dead_code)]
#[derive(Debug, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct SourceLineMapEntryExample {
    pub rendered_start_line: usize,
    pub rendered_end_line: usize,
    pub original_start_line: usize,
    pub original_end_line: usize,
}

#[allow(dead_code)]
#[derive(Debug, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct RenderDiagnosticsExample {
    pub original_line_count: usize,
    pub rendered_line_count: usize,
    pub removed_line_count: usize,
    pub removed_comment_lines: usize,
    pub removed_blank_lines: usize,
    #[serde(default)]
    pub removed_docstring_lines: usize,
    #[serde(default)]
    pub removed_boilerplate_lines: usize,
}

#[allow(dead_code)]
#[derive(Debug, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct SymbolSourceExample {
    pub version: u32,
    pub routing_backend: String,
    pub routing_reason: String,
    pub sidecar_used: bool,
    pub coverage: CoverageExample,
    pub path: String,
    pub symbol: String,
    pub files: Vec<String>,
    pub symbols: Vec<RepoSymbolExample>,
    pub imports: Vec<serde_json::Value>,
    pub tests: Vec<String>,
    pub related_paths: Vec<String>,
    pub definitions: Vec<RepoSymbolExample>,
    pub sources: Vec<SymbolSourceBlockExample>,
}

#[allow(dead_code)]
#[derive(Debug, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct SymbolReferenceExample {
    pub name: String,
    pub kind: String,
    pub file: PathBuf,
    pub line: usize,
    pub text: String,
    #[serde(default)]
    pub provenance: Option<String>,
}

#[allow(dead_code)]
#[derive(Debug, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct SymbolRefsExample {
    pub version: u32,
    pub routing_backend: String,
    pub routing_reason: String,
    pub sidecar_used: bool,
    pub coverage: CoverageExample,
    pub path: String,
    pub symbol: String,
    pub files: Vec<String>,
    pub symbols: Vec<RepoSymbolExample>,
    pub imports: Vec<serde_json::Value>,
    pub tests: Vec<String>,
    pub related_paths: Vec<String>,
    pub definitions: Vec<RepoSymbolExample>,
    pub references: Vec<SymbolReferenceExample>,
}

#[allow(dead_code)]
#[derive(Debug, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct SymbolCallersExample {
    pub version: u32,
    pub routing_backend: String,
    pub routing_reason: String,
    pub sidecar_used: bool,
    pub coverage: CoverageExample,
    pub path: String,
    pub symbol: String,
    pub definitions: Vec<RepoSymbolExample>,
    pub callers: Vec<SymbolReferenceExample>,
    pub files: Vec<String>,
    pub tests: Vec<String>,
    pub imports: Vec<serde_json::Value>,
    pub symbols: Vec<RepoSymbolExample>,
    pub related_paths: Vec<String>,
}

#[allow(dead_code)]
#[derive(Debug, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct BlastRadiusTreeLevelExample {
    pub depth: usize,
    pub files: Vec<String>,
    #[serde(default)]
    pub provenance: Vec<String>,
    #[serde(default)]
    pub graph_completeness: Option<String>,
    #[serde(default)]
    pub edge_summary: Option<serde_json::Value>,
}

#[allow(dead_code)]
#[derive(Debug, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct SymbolBlastRadiusExample {
    pub version: u32,
    pub routing_backend: String,
    pub routing_reason: String,
    pub sidecar_used: bool,
    pub coverage: CoverageExample,
    pub path: String,
    pub symbol: String,
    pub max_depth: usize,
    pub definitions: Vec<RepoSymbolExample>,
    pub callers: Vec<SymbolReferenceExample>,
    pub files: Vec<String>,
    pub file_matches: Vec<RankedPathMatchExample>,
    pub file_summaries: Vec<FileSummaryExample>,
    pub tests: Vec<String>,
    pub test_matches: Vec<RankedPathMatchExample>,
    pub caller_tree: Vec<BlastRadiusTreeLevelExample>,
    pub rendered_caller_tree: String,
    pub imports: Vec<serde_json::Value>,
    pub symbols: Vec<RepoSymbolExample>,
    pub related_paths: Vec<String>,
}

#[allow(dead_code)]
#[derive(Debug, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct SearchMatch {
    pub file: String,
    pub line: usize,
    // task #266: `text` is present for valid-UTF-8 content, `bytes` (base64) otherwise -- never
    // both, never neither. `#[serde(default)]` on both keeps every existing (valid-UTF-8-only)
    // example fixture deserializing unchanged (`bytes` simply absent), while `deny_unknown_
    // fields` above would otherwise reject a future fixture that legitimately uses `bytes`
    // instead of `text`.
    #[serde(default)]
    pub text: Option<String>,
    #[serde(default)]
    pub bytes: Option<String>,
    #[serde(default)]
    pub range: Option<SearchRangeExample>,
    #[serde(default, rename = "metaVariables")]
    pub meta_variables: Option<SearchMetaVariablesExample>,
}

#[allow(dead_code)]
#[derive(Debug, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct SearchRangeExample {
    #[serde(rename = "byteOffset")]
    pub byte_offset: ByteRange,
    pub start: SearchPositionExample,
    pub end: SearchPositionExample,
}

#[allow(dead_code)]
#[derive(Debug, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct SearchPositionExample {
    pub line: usize,
    pub column: usize,
}

#[allow(dead_code)]
#[derive(Debug, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct SearchMetaVariablesExample {
    #[serde(default)]
    pub single: HashMap<String, SearchMetaVariableExample>,
    #[serde(default)]
    pub multi: HashMap<String, Vec<SearchMetaVariableExample>>,
}

#[allow(dead_code)]
#[derive(Debug, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct SearchMetaVariableExample {
    pub text: String,
    pub range: SearchRangeExample,
}

#[allow(dead_code)]
#[derive(Debug, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct GpuSidecarExample {
    pub version: u32,
    pub routing_backend: String,
    pub routing_reason: String,
    pub sidecar_used: bool,
    pub total_matches: usize,
    pub total_files: usize,
    #[serde(default)]
    pub requested_gpu_device_ids: Vec<u32>,
    pub routing_gpu_device_ids: Vec<u32>,
    pub matches: Vec<GpuSearchMatch>,
}

#[allow(dead_code)]
#[derive(Debug, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct GpuSearchMatch {
    pub file: String,
    pub line_number: usize,
    pub text: String,
}

#[allow(dead_code)]
#[derive(Debug, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct CalibrateExample {
    pub version: u32,
    pub routing_backend: String,
    pub routing_reason: String,
    pub sidecar_used: bool,
    pub corpus_size_breakpoint_bytes: u64,
    pub cpu_median_ms: f64,
    pub gpu_median_ms: f64,
    pub recommendation: String,
    pub calibration_timestamp: u64,
    pub device_name: String,
    pub measurements: Vec<CalibrateMeasurementExample>,
}

#[allow(dead_code)]
#[derive(Debug, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct CalibrateMeasurementExample {
    pub size_bytes: u64,
    pub cpu_median_ms: f64,
    pub gpu_median_ms: f64,
    pub cpu_samples_ms: Vec<f64>,
    pub gpu_samples_ms: Vec<f64>,
}

#[allow(dead_code)]
#[derive(Debug, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct RewritePlanExample {
    pub version: u32,
    pub routing_backend: String,
    pub routing_reason: String,
    pub sidecar_used: bool,
    pub pattern: String,
    pub replacement: String,
    pub lang: String,
    pub total_files_scanned: usize,
    pub total_edits: usize,
    pub edits: Vec<RewriteEditExample>,
    #[serde(default)]
    pub rejected_overlaps: Vec<OverlapRejectionExample>,
}

#[allow(dead_code)]
#[derive(Debug, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct RewriteEditExample {
    pub id: String,
    pub file: PathBuf,
    #[serde(default)]
    pub planned_mtime_ns: Option<u64>,
    pub line: usize,
    pub byte_range: ByteRange,
    pub original_text: String,
    pub replacement_text: String,
    pub metavar_env: HashMap<String, String>,
}

#[allow(dead_code)]
#[derive(Debug, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct ByteRange {
    pub start: usize,
    pub end: usize,
}

#[allow(dead_code)]
#[derive(Debug, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct OverlapRejectionExample {
    pub file: PathBuf,
    pub edit_a: ByteRange,
    pub edit_b: ByteRange,
    pub reason: String,
}

#[allow(dead_code)]
#[derive(Debug, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct ApplyVerifyExample {
    pub version: u32,
    pub routing_backend: String,
    pub routing_reason: String,
    pub sidecar_used: bool,
    pub checkpoint: Option<CheckpointExample>,
    pub audit_manifest: Option<AuditManifestExample>,
    pub plan: RewritePlanExample,
    pub validation: Option<ValidationSummaryExample>,
    pub rollback: Option<ValidationRollbackSummaryExample>,
    pub verification: Option<VerifyResultExample>,
}

#[allow(dead_code)]
#[derive(Debug, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct CheckpointExample {
    pub checkpoint_id: String,
    pub mode: String,
    pub root: String,
    pub created_at: String,
    pub file_count: usize,
}

#[allow(dead_code)]
#[derive(Debug, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct AuditManifestExample {
    pub path: String,
    pub file_count: usize,
    pub applied_edit_count: usize,
    pub signed: bool,
    pub signature_kind: Option<String>,
}

#[allow(dead_code)]
#[derive(Debug, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct ValidationRollbackSummaryExample {
    pub triggered_by: String,
    pub success: bool,
    pub files_restored: Vec<PathBuf>,
    pub errors: Vec<String>,
}

#[allow(dead_code)]
#[derive(Debug, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct AuditManifestVerifyChecksExample {
    pub digest_valid: bool,
    pub chain_valid: bool,
    pub signature_valid: bool,
}

#[allow(dead_code)]
#[derive(Debug, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct AuditManifestVerifyExample {
    pub version: u32,
    pub routing_backend: String,
    pub routing_reason: String,
    pub sidecar_used: bool,
    pub manifest_path: String,
    pub signing_key_path: Option<String>,
    pub previous_manifest_path: Option<String>,
    pub kind: Option<String>,
    pub manifest_sha256: Option<String>,
    pub previous_manifest_sha256: Option<String>,
    pub checks: AuditManifestVerifyChecksExample,
    pub signature_kind: Option<String>,
    pub valid: bool,
    pub errors: Vec<String>,
}

#[allow(dead_code)]
#[derive(Debug, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct VerifyResultExample {
    pub total_edits: usize,
    pub verified: usize,
    pub mismatches: Vec<VerifyMismatchExample>,
}

#[allow(dead_code)]
#[derive(Debug, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct VerifyMismatchExample {
    pub edit_id: String,
    pub file: PathBuf,
    pub line: usize,
    pub expected: String,
    pub actual: String,
}

#[allow(dead_code)]
#[derive(Debug, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct ValidationSummaryExample {
    pub success: bool,
    pub commands: Vec<ValidationCommandExample>,
    #[serde(default)]
    pub errors: Vec<String>,
}

#[allow(dead_code)]
#[derive(Debug, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct ValidationCommandExample {
    pub kind: String,
    pub command: String,
    pub success: bool,
    pub exit_code: Option<i32>,
    pub stdout: String,
    pub stderr: String,
}

#[allow(dead_code)]
#[derive(Debug, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct McpRewriteDiffExample {
    pub version: u32,
    pub routing_backend: String,
    pub routing_reason: String,
    pub sidecar_used: bool,
    pub diff: String,
}

#[allow(dead_code)]
#[derive(Debug, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct SearchNdjsonExample {
    pub version: u32,
    pub routing_backend: String,
    pub routing_reason: String,
    pub sidecar_used: bool,
    #[serde(default)]
    pub requested_gpu_device_ids: Vec<u32>,
    #[serde(default)]
    pub routing_gpu_device_ids: Vec<u32>,
    pub query: String,
    pub path: String,
    pub file: String,
    pub line: usize,
    // task #266: `text` is present for valid-UTF-8 content, `bytes` (base64) otherwise -- never
    // both, never neither. `#[serde(default)]` on both keeps every existing (valid-UTF-8-only)
    // example fixture deserializing unchanged (`bytes` simply absent), while `deny_unknown_
    // fields` above would otherwise reject a future fixture that legitimately uses `bytes`
    // instead of `text`.
    #[serde(default)]
    pub text: Option<String>,
    #[serde(default)]
    pub bytes: Option<String>,
    pub pattern_id: Option<usize>,
    pub pattern_text: Option<String>,
}

#[allow(dead_code)]
#[derive(Debug, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct RepoMapExample {
    pub version: u32,
    pub routing_backend: String,
    pub routing_reason: String,
    pub sidecar_used: bool,
    pub coverage: CoverageExample,
    pub path: PathBuf,
    pub files: Vec<PathBuf>,
    pub symbols: Vec<RepoSymbolExample>,
    pub imports: Vec<RepoImportExample>,
    pub tests: Vec<PathBuf>,
    pub related_paths: Vec<PathBuf>,
}

#[allow(dead_code)]
#[derive(Debug, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct RankedRepoSymbolExample {
    pub name: String,
    pub kind: String,
    pub file: PathBuf,
    pub line: usize,
    #[serde(default)]
    pub start_line: Option<usize>,
    #[serde(default)]
    pub end_line: Option<usize>,
    #[serde(default)]
    pub provenance: Option<String>,
    pub score: usize,
}

#[allow(dead_code)]
#[derive(Debug, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct RepoImportExample {
    pub file: PathBuf,
    pub imports: Vec<String>,
    #[serde(default)]
    pub provenance: Option<String>,
}

#[allow(dead_code)]
#[derive(Debug, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct RankedRepoImportExample {
    pub file: PathBuf,
    pub imports: Vec<String>,
    #[serde(default)]
    pub provenance: Option<String>,
    pub score: usize,
}

#[allow(dead_code)]
#[derive(Debug, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct RankedPathMatchExample {
    pub path: PathBuf,
    pub score: usize,
    #[serde(default)]
    pub depth: Option<usize>,
    #[serde(default)]
    pub graph_score: Option<f64>,
    #[serde(default)]
    pub provenance: Vec<String>,
    #[serde(default)]
    pub association: Option<serde_json::Value>,
    pub reasons: Vec<String>,
}

#[allow(dead_code)]
#[derive(Debug, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct FileSummarySymbolExample {
    pub name: String,
    pub kind: String,
    pub line: usize,
}

#[allow(dead_code)]
#[derive(Debug, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct FileSummaryExample {
    pub path: PathBuf,
    pub symbols: Vec<FileSummarySymbolExample>,
}

#[allow(dead_code)]
#[derive(Debug, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct RenderSectionExample {
    pub kind: String,
    pub start: usize,
    pub end: usize,
    #[serde(default)]
    pub path: Option<PathBuf>,
    #[serde(default)]
    pub symbol: Option<String>,
    #[serde(default)]
    pub paths: Vec<PathBuf>,
    #[serde(default)]
    pub provenance: Option<serde_json::Value>,
}

#[allow(dead_code)]
#[derive(Debug, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct CandidateEditTargetsExample {
    pub files: Vec<PathBuf>,
    pub symbols: Vec<RankedRepoSymbolExample>,
    pub tests: Vec<PathBuf>,
    #[serde(default)]
    pub spans: Vec<RankedEditSpanExample>,
}

#[allow(dead_code)]
#[derive(Debug, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct RankedEditSpanExample {
    pub file: PathBuf,
    pub symbol: String,
    pub start_line: usize,
    pub end_line: usize,
    pub depth: usize,
    pub score: usize,
    pub reasons: Vec<String>,
}

#[allow(dead_code)]
#[derive(Debug, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct EditPlanSeedExample {
    pub primary_file: Option<PathBuf>,
    pub primary_symbol: Option<RankedRepoSymbolExample>,
    pub primary_span: Option<EditPlanSpanExample>,
    pub primary_test: Option<PathBuf>,
    pub validation_tests: Vec<PathBuf>,
    #[serde(default)]
    pub validation_plan: Vec<ValidationPlanStepExample>,
    pub validation_commands: Vec<String>,
    pub reasons: Vec<String>,
    pub confidence: EditPlanSeedConfidenceExample,
    #[serde(default)]
    pub related_spans: Vec<RelatedEditSpanExample>,
    #[serde(default)]
    pub dependent_files: Vec<PathBuf>,
    #[serde(default)]
    pub edit_ordering: Vec<PathBuf>,
    #[serde(default)]
    pub rollback_risk: Option<f64>,
}

#[allow(dead_code)]
#[derive(Debug, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct EditPlanSeedConfidenceExample {
    pub file: f64,
    pub symbol: f64,
    pub test: f64,
}

#[allow(dead_code)]
#[derive(Debug, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct EditPlanSpanExample {
    pub start_line: usize,
    pub end_line: usize,
}

#[allow(dead_code)]
#[derive(Debug, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct RelatedEditSpanExample {
    pub file: PathBuf,
    pub symbol: String,
    pub start_line: usize,
    pub end_line: usize,
    pub depth: usize,
    pub score: usize,
    pub reasons: Vec<String>,
}

#[allow(dead_code)]
#[derive(Debug, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct ValidationPlanStepExample {
    pub command: String,
    pub scope: String,
    pub runner: String,
    pub confidence: f64,
    // Pre-existing contract field (docs/CONTRACTS.md, docs/harness_api.md): every real
    // `validation_plan[]` step carries `detection` (`detected`/`heuristic`/`generic`). This
    // struct predates any active caller exercising it (see `#[allow(dead_code)]`), so the gap
    // was latent until the new top-level `validation_plan` assertion below started deserializing
    // real payload steps through it.
    pub detection: String,
    #[serde(default)]
    pub target: Option<String>,
}

#[allow(dead_code)]
#[derive(Debug, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct ContextPackExample {
    pub version: u32,
    pub routing_backend: String,
    pub routing_reason: String,
    pub sidecar_used: bool,
    pub coverage: CoverageExample,
    pub query: String,
    pub path: PathBuf,
    pub files: Vec<PathBuf>,
    pub file_matches: Vec<RankedPathMatchExample>,
    pub file_summaries: Vec<FileSummaryExample>,
    pub symbols: Vec<RankedRepoSymbolExample>,
    pub imports: Vec<RankedRepoImportExample>,
    pub tests: Vec<PathBuf>,
    pub test_matches: Vec<RankedPathMatchExample>,
    pub related_paths: Vec<PathBuf>,
}

#[allow(dead_code)]
#[derive(Debug, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct ContextEditPlanExample {
    pub version: u32,
    pub routing_backend: String,
    pub routing_reason: String,
    pub sidecar_used: bool,
    pub coverage: CoverageExample,
    pub query: String,
    pub path: PathBuf,
    pub files: Vec<PathBuf>,
    pub file_matches: Vec<RankedPathMatchExample>,
    pub file_summaries: Vec<FileSummaryExample>,
    pub symbols: Vec<RankedRepoSymbolExample>,
    pub imports: Vec<RankedRepoImportExample>,
    pub tests: Vec<PathBuf>,
    pub test_matches: Vec<RankedPathMatchExample>,
    pub related_paths: Vec<PathBuf>,
    pub max_files: usize,
    pub max_symbols: usize,
    pub candidate_edit_targets: CandidateEditTargetsExample,
    pub edit_plan_seed: EditPlanSeedExample,
}

#[allow(dead_code)]
#[derive(Debug, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct ContextRenderExample {
    pub version: u32,
    pub routing_backend: String,
    pub routing_reason: String,
    pub sidecar_used: bool,
    pub coverage: CoverageExample,
    pub query: String,
    pub path: PathBuf,
    pub files: Vec<PathBuf>,
    pub file_matches: Vec<RankedPathMatchExample>,
    pub file_summaries: Vec<FileSummaryExample>,
    pub symbols: Vec<RankedRepoSymbolExample>,
    pub imports: Vec<RankedRepoImportExample>,
    pub tests: Vec<PathBuf>,
    pub test_matches: Vec<RankedPathMatchExample>,
    pub related_paths: Vec<PathBuf>,
    pub sources: Vec<SymbolSourceBlockExample>,
    pub max_files: usize,
    pub max_sources: usize,
    pub max_symbols_per_file: usize,
    pub max_render_chars: Option<usize>,
    pub optimize_context: bool,
    pub render_profile: String,
    pub truncated: bool,
    pub sections: Vec<RenderSectionExample>,
    pub candidate_edit_targets: CandidateEditTargetsExample,
    pub edit_plan_seed: EditPlanSeedExample,
    pub rendered_context: String,
}

#[allow(dead_code)]
#[derive(Debug, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct BlastRadiusRenderExample {
    pub version: u32,
    pub routing_backend: String,
    pub routing_reason: String,
    pub sidecar_used: bool,
    pub coverage: CoverageExample,
    pub query: String,
    pub path: PathBuf,
    pub symbol: String,
    pub max_depth: usize,
    pub definitions: Vec<RepoSymbolExample>,
    pub callers: Vec<SymbolReferenceExample>,
    pub files: Vec<PathBuf>,
    pub file_matches: Vec<RankedPathMatchExample>,
    pub file_summaries: Vec<FileSummaryExample>,
    pub symbols: Vec<RankedRepoSymbolExample>,
    pub imports: Vec<RankedRepoImportExample>,
    pub tests: Vec<PathBuf>,
    pub test_matches: Vec<RankedPathMatchExample>,
    pub related_paths: Vec<PathBuf>,
    pub sources: Vec<SymbolSourceBlockExample>,
    pub max_files: usize,
    pub max_sources: usize,
    pub max_symbols_per_file: usize,
    pub max_render_chars: Option<usize>,
    pub optimize_context: bool,
    pub render_profile: String,
    pub truncated: bool,
    pub sections: Vec<RenderSectionExample>,
    pub candidate_edit_targets: CandidateEditTargetsExample,
    pub edit_plan_seed: EditPlanSeedExample,
    pub rendered_context: String,
    pub caller_tree: Vec<BlastRadiusTreeLevelExample>,
    pub rendered_caller_tree: String,
}

#[allow(dead_code)]
#[derive(Debug, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct BlastRadiusPlanExample {
    pub version: u32,
    pub routing_backend: String,
    pub routing_reason: String,
    pub sidecar_used: bool,
    pub coverage: CoverageExample,
    pub query: String,
    pub path: PathBuf,
    pub symbol: String,
    pub max_depth: usize,
    pub definitions: Vec<RepoSymbolExample>,
    pub callers: Vec<SymbolReferenceExample>,
    pub files: Vec<PathBuf>,
    pub file_matches: Vec<RankedPathMatchExample>,
    pub file_summaries: Vec<FileSummaryExample>,
    pub symbols: Vec<RankedRepoSymbolExample>,
    pub imports: Vec<RankedRepoImportExample>,
    pub tests: Vec<PathBuf>,
    pub test_matches: Vec<RankedPathMatchExample>,
    pub related_paths: Vec<PathBuf>,
    pub max_files: usize,
    pub max_symbols: usize,
    pub candidate_edit_targets: CandidateEditTargetsExample,
    pub edit_plan_seed: EditPlanSeedExample,
    pub caller_tree: Vec<BlastRadiusTreeLevelExample>,
    pub rendered_caller_tree: String,
}

#[allow(dead_code)]
#[derive(Debug, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct SessionOpenExample {
    pub session_id: String,
    pub root: PathBuf,
    pub created_at: String,
    pub file_count: usize,
    pub symbol_count: usize,
}

#[allow(dead_code)]
#[derive(Debug, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct SessionContextExample {
    pub version: u32,
    pub routing_backend: String,
    pub routing_reason: String,
    pub sidecar_used: bool,
    pub coverage: CoverageExample,
    pub query: String,
    pub path: PathBuf,
    pub files: Vec<PathBuf>,
    pub file_matches: Vec<RankedPathMatchExample>,
    pub file_summaries: Vec<FileSummaryExample>,
    pub symbols: Vec<RankedRepoSymbolExample>,
    pub imports: Vec<RankedRepoImportExample>,
    pub tests: Vec<PathBuf>,
    pub test_matches: Vec<RankedPathMatchExample>,
    pub related_paths: Vec<PathBuf>,
    pub session_id: String,
}
