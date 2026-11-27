#![allow(unused_imports, dead_code)]
use super::*;
use serde::de::DeserializeOwned;
use serde::Deserialize;
use std::collections::{BTreeSet, HashMap};
use std::fs;
use std::path::{Path, PathBuf};

pub fn assert_search_example(path: &Path) {
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
        // task #266: exactly one of `text` (valid UTF-8) / `bytes` (base64 fallback) is present.
        assert!(
            matched.text.as_deref().is_some_and(|text| !text.is_empty())
                != matched
                    .bytes
                    .as_deref()
                    .is_some_and(|bytes| !bytes.is_empty()),
            "{} match must have exactly one of `text`/`bytes` populated",
            path.display()
        );
        if let Some(range) = &matched.range {
            assert!(
                range.byte_offset.end > range.byte_offset.start,
                "{} match range must span at least one byte",
                path.display()
            );
            assert!(
                range.end.line > range.start.line || range.end.column >= range.start.column,
                "{} match range end must not precede start",
                path.display()
            );
        }
        if let Some(meta_variables) = &matched.meta_variables {
            for capture in meta_variables.single.values() {
                assert!(
                    !capture.text.is_empty(),
                    "{} single metavariable text must not be empty",
                    path.display()
                );
                assert!(
                    capture.range.byte_offset.end > capture.range.byte_offset.start,
                    "{} single metavariable range must span bytes",
                    path.display()
                );
            }
            for captures in meta_variables.multi.values() {
                assert!(
                    !captures.is_empty(),
                    "{} multi metavariable list must not be empty",
                    path.display()
                );
                for capture in captures {
                    assert!(
                        !capture.text.is_empty(),
                        "{} multi metavariable text must not be empty",
                        path.display()
                    );
                    assert!(
                        capture.range.byte_offset.end > capture.range.byte_offset.start,
                        "{} multi metavariable range must span bytes",
                        path.display()
                    );
                }
            }
        }
    }
    if example.routing_backend == "AstBackend" {
        for matched in &example.matches {
            assert!(
                matched.range.is_some(),
                "{} AST matches must include range metadata",
                path.display()
            );
            assert!(
                matched.meta_variables.is_some(),
                "{} AST matches must include metavariable metadata",
                path.display()
            );
        }
    }
}

pub fn assert_gpu_cpu_fallback_example(path: &Path) {
    let example: SearchExample = parse_json_document(path);
    assert_common_envelope(
        path,
        example.version,
        &example.routing_backend,
        &example.routing_reason,
    );
    assert_eq!(
        example.routing_backend,
        "NativeCpuBackend",
        "{} should make CPU fallback backend explicit",
        path.display()
    );
    assert_eq!(
        example.routing_reason,
        "gpu-auto-fallback-cpu",
        "{} should make CPU fallback reason explicit",
        path.display()
    );
    assert!(
        !example.sidecar_used,
        "{} CPU fallback example must not claim sidecar GPU routing",
        path.display()
    );
    assert!(
        !example.requested_gpu_device_ids.is_empty(),
        "{} should preserve caller-requested GPU ids",
        path.display()
    );
    assert!(
        example.routing_gpu_device_ids.is_empty(),
        "{} should not list routed GPU ids for CPU fallback",
        path.display()
    );
    assert_eq!(
        example.total_matches,
        example.matches.len(),
        "{} total_matches mismatch",
        path.display()
    );
}

pub fn assert_rulesets_example(path: &Path) {
    let example: RulesetsExample = parse_json_document(path);
    assert_common_envelope(
        path,
        example.version,
        &example.routing_backend,
        &example.routing_reason,
    );
    assert!(
        !example.sidecar_used,
        "{} rulesets example should stay native",
        path.display()
    );
    assert!(
        !example.rulesets.is_empty(),
        "{} should include at least one built-in ruleset",
        path.display()
    );
    for ruleset in &example.rulesets {
        assert!(
            !ruleset.name.is_empty(),
            "{} ruleset name must not be empty",
            path.display()
        );
        assert!(
            !ruleset.description.is_empty(),
            "{} ruleset description must not be empty",
            path.display()
        );
        assert!(
            !ruleset.category.is_empty(),
            "{} ruleset category must not be empty",
            path.display()
        );
        assert!(
            !ruleset.status.is_empty(),
            "{} ruleset status must not be empty",
            path.display()
        );
        assert!(
            !ruleset.default_language.is_empty(),
            "{} default_language must not be empty",
            path.display()
        );
        assert!(
            !ruleset.languages.is_empty(),
            "{} ruleset languages must not be empty",
            path.display()
        );
        assert!(
            ruleset.rule_count > 0,
            "{} ruleset rule_count must be positive",
            path.display()
        );
    }
}

pub fn assert_ruleset_scan_example(path: &Path) {
    let example: RulesetScanExample = parse_json_document(path);
    assert_common_envelope(
        path,
        example.version,
        &example.routing_backend,
        &example.routing_reason,
    );
    assert!(
        !example.sidecar_used,
        "{} ruleset scan example should stay native",
        path.display()
    );
    assert!(
        example.config_path.starts_with("builtin:"),
        "{} config_path should reference a built-in ruleset",
        path.display()
    );
    assert!(
        !example.path.is_empty(),
        "{} path must not be empty",
        path.display()
    );
    assert!(
        !example.ruleset.is_empty(),
        "{} ruleset name must not be empty",
        path.display()
    );
    assert!(
        !example.language.is_empty(),
        "{} language must not be empty",
        path.display()
    );
    assert!(
        example.rule_count >= example.findings.len(),
        "{} rule_count must cover the findings array",
        path.display()
    );
    assert!(
        example.total_matches >= example.matched_rules,
        "{} total_matches must be at least matched_rules",
        path.display()
    );
    assert!(
        !example.backends.is_empty(),
        "{} ruleset scan should record at least one backend",
        path.display()
    );
    assert!(
        !example.findings.is_empty(),
        "{} ruleset scan should include findings",
        path.display()
    );
    for finding in &example.findings {
        assert!(
            !finding.rule_id.is_empty(),
            "{} finding rule_id must not be empty",
            path.display()
        );
        assert!(
            !finding.language.is_empty(),
            "{} finding language must not be empty",
            path.display()
        );
        assert!(
            !finding.severity.is_empty(),
            "{} finding severity must not be empty",
            path.display()
        );
        assert!(
            !finding.message.is_empty(),
            "{} finding message must not be empty",
            path.display()
        );
        assert!(
            !finding.fingerprint.is_empty(),
            "{} finding fingerprint must not be empty",
            path.display()
        );
        if let Some(status) = &finding.status {
            assert!(
                matches!(status.as_str(), "new" | "existing" | "suppressed" | "clear"),
                "{} finding status must be new, existing, suppressed, or clear",
                path.display()
            );
        }
        assert_eq!(
            finding.files.len(),
            finding.evidence.len(),
            "{} finding evidence rows must align with the matched file list",
            path.display()
        );
        let evidence_total: usize = finding
            .evidence
            .iter()
            .map(|evidence| evidence.match_count)
            .sum();
        assert_eq!(
            finding.matches,
            evidence_total,
            "{} finding matches must equal the summed evidence match_count values",
            path.display()
        );
        for file in &finding.files {
            assert!(
                Path::new(file).is_absolute() || is_windows_absolute_path_literal(file),
                "{} finding file should be absolute or an absolute Windows path literal",
                path.display()
            );
        }
        for evidence in &finding.evidence {
            assert!(
                Path::new(&evidence.file).is_absolute()
                    || is_windows_absolute_path_literal(&evidence.file),
                "{} evidence file should be absolute or an absolute Windows path literal",
                path.display()
            );
            assert!(
                evidence.match_count > 0,
                "{} evidence match_count must be positive",
                path.display()
            );
            for snippet in &evidence.snippets {
                assert!(
                    !snippet.text.is_empty() || snippet.truncated,
                    "{} evidence snippet must carry text unless it is fully truncated",
                    path.display()
                );
            }
        }
    }
    if let Some(baseline) = &example.baseline {
        assert!(
            !baseline.path.is_empty(),
            "{} baseline path must not be empty",
            path.display()
        );
        assert!(
            baseline.new_findings + baseline.existing_findings >= example.matched_rules,
            "{} baseline counts should cover current matched rules",
            path.display()
        );
        assert!(
            baseline.resolved_findings == baseline.resolved_fingerprints.len(),
            "{} resolved_findings must match resolved_fingerprints length",
            path.display()
        );
    }
    if let Some(written) = &example.baseline_written {
        assert!(
            !written.path.is_empty(),
            "{} baseline_written path must not be empty",
            path.display()
        );
        assert!(
            written.count == written.fingerprints.len(),
            "{} baseline_written count must match fingerprint length",
            path.display()
        );
    }
    if let Some(suppressions) = &example.suppressions {
        assert!(
            !suppressions.path.is_empty(),
            "{} suppressions path must not be empty",
            path.display()
        );
        let suppressed = example
            .findings
            .iter()
            .filter(|finding| finding.status.as_deref() == Some("suppressed"))
            .count();
        assert!(
            suppressions.suppressed_findings == suppressed,
            "{} suppressed_findings must match the number of suppressed findings",
            path.display()
        );
    }
    if let Some(written) = &example.suppressions_written {
        assert!(
            !written.path.is_empty(),
            "{} suppressions_written path must not be empty",
            path.display()
        );
        assert!(
            written.count == written.fingerprints.len(),
            "{} suppressions_written count must match fingerprint length",
            path.display()
        );
    }
}

pub fn assert_rewrite_plan_example(path: &Path) {
    let plan: RewritePlanExample = parse_json_document(path);
    assert_rewrite_plan_payload(path, &plan);
}

pub fn assert_apply_verify_example(path: &Path) {
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
    if let Some(audit_manifest) = &example.audit_manifest {
        assert!(
            !audit_manifest.path.is_empty(),
            "{} audit manifest path must not be empty",
            path.display()
        );
        assert!(
            audit_manifest.file_count > 0,
            "{} audit manifest file_count must be positive",
            path.display()
        );
        assert!(
            audit_manifest.applied_edit_count > 0,
            "{} audit manifest applied_edit_count must be positive",
            path.display()
        );
        if audit_manifest.signed {
            assert_eq!(
                audit_manifest.signature_kind.as_deref(),
                Some("hmac-sha256"),
                "{} signed audit manifests must report hmac-sha256",
                path.display()
            );
        }
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

pub fn assert_apply_verify_validation_failed_example(path: &Path) {
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
    assert_common_envelope(
        path,
        example.plan.version,
        &example.plan.routing_backend,
        &example.plan.routing_reason,
    );
    assert!(
        !example.plan.pattern.is_empty(),
        "{} plan pattern must not be empty",
        path.display()
    );
    assert!(
        !example.plan.replacement.is_empty(),
        "{} plan replacement must not be empty",
        path.display()
    );
    assert!(
        !example.plan.edits.is_empty(),
        "{} plan edits must not be empty",
        path.display()
    );
    assert!(
        example.validation.is_some(),
        "{} validation payload must be present",
        path.display()
    );
    let validation = example
        .validation
        .as_ref()
        .unwrap_or_else(|| panic!("{} validation payload missing", path.display()));
    assert!(
        !validation.success,
        "{} validation_failed example must record validation.success = false",
        path.display()
    );
    assert!(
        !validation.commands.is_empty(),
        "{} validation commands must not be empty",
        path.display()
    );
    let rollback = example
        .rollback
        .as_ref()
        .unwrap_or_else(|| panic!("{} rollback payload missing", path.display()));
    assert_eq!(
        rollback.triggered_by,
        "validation",
        "{} rollback should be triggered by validation failure",
        path.display()
    );
    assert!(
        rollback.success,
        "{} validation failure should record successful rollback",
        path.display()
    );
    assert!(
        !rollback.files_restored.is_empty(),
        "{} rollback files_restored must not be empty",
        path.display()
    );
    for restored_file in &rollback.files_restored {
        assert!(
            is_portable_absolute_path(restored_file),
            "{} rollback restored file should be absolute or an absolute Windows path literal",
            path.display()
        );
    }
    assert!(
        rollback.errors.is_empty(),
        "{} successful rollback must not include errors",
        path.display()
    );
    let verification = example
        .verification
        .as_ref()
        .unwrap_or_else(|| panic!("{} verification payload missing", path.display()));
    assert!(
        !verification.mismatches.is_empty() || verification.verified <= example.plan.edits.len(),
        "{} verification payload must stay bounded by the planned edits",
        path.display()
    );
}

pub fn assert_audit_manifest_verify_example(path: &Path) {
    let example: AuditManifestVerifyExample = parse_json_document(path);
    assert_common_envelope(
        path,
        example.version,
        &example.routing_backend,
        &example.routing_reason,
    );
    assert_eq!(
        example.routing_reason,
        "audit-manifest-verify",
        "{} should keep audit-manifest-verify routing reason",
        path.display()
    );
    assert!(
        !example.sidecar_used,
        "{} should be native audit-manifest verification output",
        path.display()
    );
    assert!(
        !example.manifest_path.is_empty(),
        "{} manifest_path must not be empty",
        path.display()
    );
    if let Some(signing_key_path) = &example.signing_key_path {
        assert!(
            !signing_key_path.is_empty(),
            "{} signing_key_path must not be empty when present",
            path.display()
        );
    }
    if let Some(previous_manifest_path) = &example.previous_manifest_path {
        assert!(
            !previous_manifest_path.is_empty(),
            "{} previous_manifest_path must not be empty when present",
            path.display()
        );
    }
    if let Some(kind) = &example.kind {
        assert!(
            !kind.is_empty(),
            "{} kind must not be empty when present",
            path.display()
        );
    }
    if let Some(manifest_sha256) = &example.manifest_sha256 {
        assert_eq!(
            manifest_sha256.len(),
            64,
            "{} manifest_sha256 should be a 64-character hex digest",
            path.display()
        );
    }
    if let Some(previous_manifest_sha256) = &example.previous_manifest_sha256 {
        assert_eq!(
            previous_manifest_sha256.len(),
            64,
            "{} previous_manifest_sha256 should be a 64-character hex digest",
            path.display()
        );
    }
    if let Some(signature_kind) = &example.signature_kind {
        assert_eq!(
            signature_kind,
            "hmac-sha256",
            "{} signature_kind should currently be hmac-sha256",
            path.display()
        );
    }
    assert_eq!(
        example.valid,
        example.checks.digest_valid && example.checks.chain_valid && example.checks.signature_valid,
        "{} valid should match combined digest/chain/signature checks",
        path.display()
    );
    if example.valid {
        assert!(
            example.errors.is_empty(),
            "{} valid audit verification examples should not report errors",
            path.display()
        );
    }
}

pub fn assert_symbol_defs_example(path: &Path) {
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

pub fn assert_symbol_blast_radius_example(path: &Path) {
    let example: SymbolBlastRadiusExample = parse_json_document(path);
    assert_common_envelope(
        path,
        example.version,
        &example.routing_backend,
        &example.routing_reason,
    );
    assert_eq!(example.routing_backend, "RepoMap");
    assert_eq!(example.routing_reason, "symbol-blast-radius");
    assert!(
        !example.sidecar_used,
        "{} should be native symbol blast radius output",
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
        example.tests.len(),
        example.test_matches.len(),
        "{} test_matches should align with tests",
        path.display()
    );
    assert!(
        !example.caller_tree.is_empty(),
        "{} caller_tree must not be empty",
        path.display()
    );
    assert!(
        example.rendered_caller_tree.contains("Depth 0:"),
        "{} rendered_caller_tree must include depth headers",
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
        assert!(is_portable_absolute_path(&caller.file));
        assert!(caller.line > 0);
        assert!(!caller.text.is_empty());
    }
    for file_match in &example.file_matches {
        assert!(is_portable_absolute_path(&file_match.path));
        assert!(file_match.depth.unwrap_or(0) <= example.max_depth);
        assert!(!file_match.reasons.is_empty());
    }
    for test_match in &example.test_matches {
        assert!(is_portable_absolute_path(&test_match.path));
        assert!(!test_match.reasons.is_empty());
    }
    for level in &example.caller_tree {
        assert!(level.depth <= example.max_depth);
        assert!(!level.files.is_empty());
    }
    let _ = &example.file_summaries;
    let _ = &example.imports;
    let _ = &example.symbols;
    let _ = &example.related_paths;
}

pub fn assert_symbol_impact_example(path: &Path) {
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

pub fn assert_symbol_source_example(path: &Path) {
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
        assert_symbol_source_block(path, source);
        assert_eq!(
            source.name,
            example.symbol,
            "{} source name must match symbol",
            path.display()
        );
    }
    let _ = &example.files;
    let _ = &example.symbols;
    let _ = &example.imports;
    let _ = &example.tests;
    let _ = &example.related_paths;
}

pub fn assert_symbol_source_block(path: &Path, source: &SymbolSourceBlockExample) {
    assert!(
        !source.name.is_empty(),
        "{} source name must not be empty",
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
    if let Some(render_profile) = &source.render_profile {
        assert!(
            matches!(render_profile.as_str(), "full" | "compact" | "llm"),
            "{} render_profile must be full, compact, or llm",
            path.display()
        );
    }
    if let Some(optimize_context) = source.optimize_context {
        if optimize_context {
            assert!(
                source.rendered_source.is_some(),
                "{} optimized source blocks should include rendered_source",
                path.display()
            );
        }
    }
    if let Some(rendered_source) = &source.rendered_source {
        assert!(
            !rendered_source.trim().is_empty(),
            "{} rendered_source must not be empty when present",
            path.display()
        );
    }
    for line_map_entry in &source.line_map {
        assert!(
            line_map_entry.rendered_start_line > 0
                && line_map_entry.rendered_end_line >= line_map_entry.rendered_start_line,
            "{} rendered line-map entries must be ordered",
            path.display()
        );
        assert!(
            line_map_entry.original_start_line >= source.start_line
                && line_map_entry.original_end_line >= line_map_entry.original_start_line,
            "{} original line-map entries must be ordered",
            path.display()
        );
    }
    if let Some(diagnostics) = &source.render_diagnostics {
        assert!(
            diagnostics.original_line_count >= diagnostics.rendered_line_count,
            "{} original line count must be >= rendered line count",
            path.display()
        );
        assert_eq!(
            diagnostics.removed_line_count,
            diagnostics
                .original_line_count
                .saturating_sub(diagnostics.rendered_line_count),
            "{} removed line count must match line delta",
            path.display()
        );
        assert!(
            diagnostics.removed_comment_lines + diagnostics.removed_blank_lines
                <= diagnostics.removed_line_count,
            "{} removed comment and blank lines must not exceed total removed lines",
            path.display()
        );
        assert!(
            diagnostics.removed_docstring_lines + diagnostics.removed_boilerplate_lines
                <= diagnostics.removed_line_count,
            "{} removed docstring and boilerplate lines must not exceed total removed lines",
            path.display()
        );
    }
}

pub fn assert_symbol_refs_example(path: &Path) {
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

pub fn assert_symbol_callers_example(path: &Path) {
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

pub fn assert_gpu_sidecar_example(path: &Path) {
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
    assert!(
        !example.requested_gpu_device_ids.is_empty(),
        "{} should list the caller-requested GPU device ids",
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

pub fn assert_calibrate_example(path: &Path) {
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

pub fn assert_mcp_rewrite_diff_example(path: &Path) {
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

pub fn assert_repo_map_example(path: &Path) {
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

pub fn assert_context_pack_example(path: &Path) {
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
