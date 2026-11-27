#![allow(unused_imports, dead_code)]
use super::*;
use serde::de::DeserializeOwned;
use serde::Deserialize;
use std::collections::{BTreeSet, HashMap};
use std::fs;
use std::path::{Path, PathBuf};

#[allow(dead_code)]
pub fn assert_edit_plan_common(
    path: &Path,
    candidate_edit_targets: &CandidateEditTargetsExample,
    edit_plan_seed: &EditPlanSeedExample,
    imports: &[RankedRepoImportExample],
    tests: &[PathBuf],
    test_matches: &[RankedPathMatchExample],
    related_paths: &[PathBuf],
) {
    for candidate_file in &candidate_edit_targets.files {
        assert!(
            is_portable_absolute_path(candidate_file),
            "{} candidate file should be absolute or an absolute Windows path literal",
            path.display()
        );
    }
    for candidate_symbol in &candidate_edit_targets.symbols {
        assert!(
            !candidate_symbol.name.is_empty(),
            "{} candidate symbol name must not be empty",
            path.display()
        );
    }
    for candidate_test in &candidate_edit_targets.tests {
        assert!(
            is_portable_absolute_path(candidate_test),
            "{} candidate test should be absolute or an absolute Windows path literal",
            path.display()
        );
    }
    for candidate_span in &candidate_edit_targets.spans {
        assert!(
            is_portable_absolute_path(&candidate_span.file),
            "{} candidate span file should be absolute or an absolute Windows path literal",
            path.display()
        );
        assert!(
            !candidate_span.symbol.is_empty(),
            "{} candidate span symbol must not be empty",
            path.display()
        );
        assert!(
            candidate_span.end_line >= candidate_span.start_line,
            "{} candidate span lines must be ordered",
            path.display()
        );
        assert!(
            !candidate_span.reasons.is_empty(),
            "{} candidate span reasons must not be empty",
            path.display()
        );
    }
    if let Some(primary_file) = &edit_plan_seed.primary_file {
        assert!(
            is_portable_absolute_path(primary_file),
            "{} primary_file should be absolute or an absolute Windows path literal",
            path.display()
        );
    }
    if let Some(primary_symbol) = &edit_plan_seed.primary_symbol {
        assert!(
            !primary_symbol.name.is_empty(),
            "{} primary_symbol name must not be empty",
            path.display()
        );
    }
    if let Some(primary_span) = &edit_plan_seed.primary_span {
        assert!(
            primary_span.end_line >= primary_span.start_line,
            "{} primary_span lines must be ordered",
            path.display()
        );
    }
    if let Some(primary_test) = &edit_plan_seed.primary_test {
        assert!(
            is_portable_absolute_path(primary_test),
            "{} primary_test should be absolute or an absolute Windows path literal",
            path.display()
        );
    }
    for validation_test in &edit_plan_seed.validation_tests {
        assert!(
            is_portable_absolute_path(validation_test),
            "{} validation_tests should be absolute or an absolute Windows path literal",
            path.display()
        );
    }
    for validation_command in &edit_plan_seed.validation_commands {
        assert!(
            !validation_command.is_empty(),
            "{} validation_commands entries must not be empty",
            path.display()
        );
    }
    assert!(
        !edit_plan_seed.validation_plan.is_empty(),
        "{} validation_plan must not be empty",
        path.display()
    );
    for step in &edit_plan_seed.validation_plan {
        assert!(
            !step.command.is_empty(),
            "{} validation_plan command must not be empty",
            path.display()
        );
        assert!(
            matches!(step.scope.as_str(), "symbol" | "file" | "repo"),
            "{} validation_plan scope must be symbol, file, or repo",
            path.display()
        );
        assert!(
            !step.runner.is_empty(),
            "{} validation_plan runner must not be empty",
            path.display()
        );
        assert!(
            (0.0..=1.0).contains(&step.confidence),
            "{} validation_plan confidence must be normalized",
            path.display()
        );
    }
    assert!(
        !edit_plan_seed.reasons.is_empty(),
        "{} edit_plan_seed reasons must not be empty",
        path.display()
    );
    assert!(
        (0.0..=1.0).contains(&edit_plan_seed.confidence.file),
        "{} file confidence must be normalized",
        path.display()
    );
    assert!(
        (0.0..=1.0).contains(&edit_plan_seed.confidence.symbol),
        "{} symbol confidence must be normalized",
        path.display()
    );
    assert!(
        (0.0..=1.0).contains(&edit_plan_seed.confidence.test),
        "{} test confidence must be normalized",
        path.display()
    );
    for related_span in &edit_plan_seed.related_spans {
        assert!(
            is_portable_absolute_path(&related_span.file),
            "{} related span path should be absolute or an absolute Windows path literal",
            path.display()
        );
        assert!(
            !related_span.symbol.is_empty(),
            "{} related span symbol must not be empty",
            path.display()
        );
        assert!(
            related_span.end_line >= related_span.start_line,
            "{} related span lines must be ordered",
            path.display()
        );
        assert!(
            !related_span.reasons.is_empty(),
            "{} related span reasons must not be empty",
            path.display()
        );
    }
    for dependent_file in &edit_plan_seed.dependent_files {
        assert!(
            is_portable_absolute_path(dependent_file),
            "{} dependent file should be absolute or an absolute Windows path literal",
            path.display()
        );
    }
    for ordered_path in &edit_plan_seed.edit_ordering {
        assert!(
            is_portable_absolute_path(ordered_path),
            "{} edit_ordering path should be absolute or an absolute Windows path literal",
            path.display()
        );
    }
    if let Some(rollback_risk) = edit_plan_seed.rollback_risk {
        assert!(
            (0.0..=1.0).contains(&rollback_risk),
            "{} rollback_risk must be normalized",
            path.display()
        );
    }
    for import in imports {
        assert!(
            is_portable_absolute_path(&import.file),
            "{} import file should be absolute or an absolute Windows path literal",
            path.display()
        );
    }
    for test_path in tests {
        assert!(
            is_portable_absolute_path(test_path),
            "{} test path should be absolute or an absolute Windows path literal",
            path.display()
        );
    }
    assert_eq!(
        tests.len(),
        test_matches.len(),
        "{} test_matches should align with tests",
        path.display()
    );
    for related_path in related_paths {
        assert!(
            is_portable_absolute_path(related_path),
            "{} related path should be absolute or an absolute Windows path literal",
            path.display()
        );
    }
}

pub fn assert_context_render_example(path: &Path) {
    let payload: serde_json::Value = parse_json_document(path);
    let object = payload
        .as_object()
        .unwrap_or_else(|| panic!("{} must be a JSON object", path.display()));
    assert_eq!(
        object.get("version").and_then(|value| value.as_u64()),
        Some(1),
        "{} version must be 1",
        path.display()
    );
    assert_eq!(
        object
            .get("routing_backend")
            .and_then(|value| value.as_str()),
        Some("RepoMap"),
        "{} routing_backend must stay RepoMap",
        path.display()
    );
    assert_eq!(
        object
            .get("routing_reason")
            .and_then(|value| value.as_str()),
        Some("context-render"),
        "{} routing_reason must stay context-render",
        path.display()
    );
    let coverage: CoverageExample = serde_json::from_value(
        object
            .get("coverage")
            .cloned()
            .unwrap_or_else(|| panic!("{} coverage missing", path.display())),
    )
    .unwrap_or_else(|error| panic!("{} invalid coverage: {error}", path.display()));
    assert_repo_map_coverage(path, &coverage);
    assert!(
        object
            .get("query")
            .and_then(|value| value.as_str())
            .is_some_and(|value| !value.is_empty()),
        "{} query must not be empty",
        path.display()
    );
    assert!(
        object
            .get("files")
            .and_then(|value| value.as_array())
            .is_some_and(|value| !value.is_empty()),
        "{} files must not be empty",
        path.display()
    );
    assert!(
        object
            .get("symbols")
            .and_then(|value| value.as_array())
            .is_some_and(|value| !value.is_empty()),
        "{} symbols must not be empty",
        path.display()
    );
    assert!(
        object
            .get("sources")
            .and_then(|value| value.as_array())
            .is_some_and(|value| !value.is_empty()),
        "{} sources must not be empty",
        path.display()
    );
    assert!(
        object
            .get("rendered_context")
            .and_then(|value| value.as_str())
            .is_some_and(|value| !value.trim().is_empty()),
        "{} rendered_context must not be empty",
        path.display()
    );
    assert!(
        object
            .get("graph_trust_summary")
            .and_then(|value| value.as_object())
            .is_some(),
        "{} graph_trust_summary missing",
        path.display()
    );
    assert!(
        object
            .get("navigation_pack")
            .and_then(|value| value.as_object())
            .is_some(),
        "{} navigation_pack missing",
        path.display()
    );
}

pub fn assert_context_edit_plan_example(path: &Path) {
    let payload: serde_json::Value = parse_json_document(path);
    let object = payload
        .as_object()
        .unwrap_or_else(|| panic!("{} must be a JSON object", path.display()));
    assert_eq!(
        object.get("version").and_then(|value| value.as_u64()),
        Some(1),
        "{} version must be 1",
        path.display()
    );
    assert_eq!(
        object
            .get("routing_backend")
            .and_then(|value| value.as_str()),
        Some("RepoMap"),
        "{} routing_backend must stay RepoMap",
        path.display()
    );
    assert_eq!(
        object
            .get("routing_reason")
            .and_then(|value| value.as_str()),
        Some("context-edit-plan"),
        "{} routing_reason must stay context-edit-plan",
        path.display()
    );
    let coverage: CoverageExample = serde_json::from_value(
        object
            .get("coverage")
            .cloned()
            .unwrap_or_else(|| panic!("{} coverage missing", path.display())),
    )
    .unwrap_or_else(|error| panic!("{} invalid coverage: {error}", path.display()));
    assert_repo_map_coverage(path, &coverage);
    assert!(
        object
            .get("query")
            .and_then(|value| value.as_str())
            .is_some_and(|value| !value.is_empty()),
        "{} query must not be empty",
        path.display()
    );
    assert!(
        object
            .get("files")
            .and_then(|value| value.as_array())
            .is_some_and(|value| !value.is_empty()),
        "{} files must not be empty",
        path.display()
    );
    assert!(
        object
            .get("symbols")
            .and_then(|value| value.as_array())
            .is_some_and(|value| !value.is_empty()),
        "{} symbols must not be empty",
        path.display()
    );
    assert!(
        object
            .get("candidate_edit_targets")
            .and_then(|value| value.as_object())
            .is_some(),
        "{} candidate_edit_targets missing",
        path.display()
    );
    assert!(
        object
            .get("edit_plan_seed")
            .and_then(|value| value.as_object())
            .is_some(),
        "{} edit_plan_seed missing",
        path.display()
    );
    assert!(
        object
            .get("graph_trust_summary")
            .and_then(|value| value.as_object())
            .is_some(),
        "{} graph_trust_summary missing",
        path.display()
    );
    assert!(
        object
            .get("navigation_pack")
            .and_then(|value| value.as_object())
            .is_some(),
        "{} navigation_pack missing",
        path.display()
    );
    // Parity fix (v1.71.1 dogfood): top-level `validation_plan` is an ADDITIVE sibling of the
    // pre-existing top-level `validation_commands` -- it must be a non-empty array of structured
    // steps, mirroring the `agent` capsule's own top-level `validation_plan` contract.
    let validation_plan: Vec<ValidationPlanStepExample> = serde_json::from_value(
        object
            .get("validation_plan")
            .cloned()
            .unwrap_or_else(|| panic!("{} top-level validation_plan missing", path.display())),
    )
    .unwrap_or_else(|error| {
        panic!(
            "{} invalid top-level validation_plan: {error}",
            path.display()
        )
    });
    assert!(
        !validation_plan.is_empty(),
        "{} top-level validation_plan must not be empty",
        path.display()
    );
    for step in &validation_plan {
        assert!(
            !step.command.is_empty(),
            "{} top-level validation_plan command must not be empty",
            path.display()
        );
        assert!(
            matches!(step.scope.as_str(), "symbol" | "file" | "repo"),
            "{} top-level validation_plan scope must be symbol, file, or repo",
            path.display()
        );
        assert!(
            !step.runner.is_empty(),
            "{} top-level validation_plan runner must not be empty",
            path.display()
        );
        assert!(
            (0.0..=1.0).contains(&step.confidence),
            "{} top-level validation_plan confidence must be normalized",
            path.display()
        );
        assert!(
            matches!(
                step.detection.as_str(),
                "detected" | "heuristic" | "generic"
            ),
            "{} top-level validation_plan detection must be detected, heuristic, or generic",
            path.display()
        );
    }
    assert!(
        object
            .get("validation_commands")
            .and_then(|value| value.as_array())
            .is_some_and(|value| !value.is_empty()),
        "{} top-level validation_commands missing or empty",
        path.display()
    );
    // Parity fix : top-level `confidence` / `ask_user_before_editing` mirror
    // the `agent` capsule contract's own top-level shape (`{overall, downgrade_reasons}` /
    // `{required, reasons}`) -- additive alongside the pre-existing top-level fields above.
    let confidence_overall = object
        .get("confidence")
        .and_then(|value| value.as_object())
        .and_then(|confidence| confidence.get("overall"))
        .and_then(|value| value.as_f64())
        .unwrap_or_else(|| panic!("{} top-level confidence.overall missing", path.display()));
    assert!(
        (0.0..=1.0).contains(&confidence_overall),
        "{} top-level confidence.overall must be normalized",
        path.display()
    );
    assert!(
        object
            .get("confidence")
            .and_then(|value| value.as_object())
            .and_then(|confidence| confidence.get("downgrade_reasons"))
            .and_then(|value| value.as_array())
            .is_some(),
        "{} top-level confidence.downgrade_reasons missing",
        path.display()
    );
    let ask_user_before_editing = object
        .get("ask_user_before_editing")
        .and_then(|value| value.as_object())
        .unwrap_or_else(|| {
            panic!(
                "{} top-level ask_user_before_editing missing",
                path.display()
            )
        });
    assert!(
        ask_user_before_editing
            .get("required")
            .and_then(|value| value.as_bool())
            .is_some(),
        "{} ask_user_before_editing.required must be a bool",
        path.display()
    );
    assert!(
        ask_user_before_editing
            .get("reasons")
            .and_then(|value| value.as_array())
            .is_some(),
        "{} ask_user_before_editing.reasons must be an array",
        path.display()
    );
}

pub fn assert_blast_radius_render_example(path: &Path) {
    let payload: serde_json::Value = parse_json_document(path);
    let object = payload
        .as_object()
        .unwrap_or_else(|| panic!("{} must be a JSON object", path.display()));

    assert_eq!(
        object.get("version").and_then(|value| value.as_u64()),
        Some(1),
        "{} version must be 1",
        path.display()
    );
    assert_eq!(
        object
            .get("routing_backend")
            .and_then(|value| value.as_str()),
        Some("RepoMap"),
        "{} routing_backend must stay RepoMap",
        path.display()
    );
    assert_eq!(
        object
            .get("routing_reason")
            .and_then(|value| value.as_str()),
        Some("symbol-blast-radius-render"),
        "{} routing_reason must stay symbol-blast-radius-render",
        path.display()
    );
    assert_eq!(
        object.get("sidecar_used").and_then(|value| value.as_bool()),
        Some(false),
        "{} should stay native",
        path.display()
    );
    let coverage: CoverageExample = serde_json::from_value(
        object
            .get("coverage")
            .cloned()
            .unwrap_or_else(|| panic!("{} coverage missing", path.display())),
    )
    .unwrap_or_else(|error| panic!("{} invalid coverage: {error}", path.display()));
    assert_repo_map_coverage(path, &coverage);
    assert!(
        object
            .get("symbol")
            .and_then(|value| value.as_str())
            .is_some_and(|value| !value.is_empty()),
        "{} symbol must not be empty",
        path.display()
    );
    assert!(
        object
            .get("definitions")
            .and_then(|value| value.as_array())
            .is_some_and(|value| !value.is_empty()),
        "{} definitions must not be empty",
        path.display()
    );
    assert!(
        object
            .get("callers")
            .and_then(|value| value.as_array())
            .is_some_and(|value| !value.is_empty()),
        "{} callers must not be empty",
        path.display()
    );
    assert!(
        object
            .get("sources")
            .and_then(|value| value.as_array())
            .is_some_and(|value| !value.is_empty()),
        "{} sources must not be empty",
        path.display()
    );
    assert!(
        object
            .get("caller_tree")
            .and_then(|value| value.as_array())
            .is_some_and(|value| !value.is_empty()),
        "{} caller_tree must not be empty",
        path.display()
    );
    assert!(
        object
            .get("rendered_caller_tree")
            .and_then(|value| value.as_str())
            .is_some_and(|value| value.contains("Depth 0:")),
        "{} rendered_caller_tree must include depth headers",
        path.display()
    );
    assert!(
        object
            .get("rendered_context")
            .and_then(|value| value.as_str())
            .is_some_and(|value| !value.trim().is_empty()),
        "{} rendered_context must not be empty",
        path.display()
    );
    assert!(
        object
            .get("graph_trust_summary")
            .and_then(|value| value.as_object())
            .is_some(),
        "{} graph_trust_summary missing",
        path.display()
    );
    assert!(
        object
            .get("navigation_pack")
            .and_then(|value| value.as_object())
            .is_some(),
        "{} navigation_pack missing",
        path.display()
    );
}

pub fn assert_blast_radius_plan_example(path: &Path) {
    let payload: serde_json::Value = parse_json_document(path);
    let object = payload
        .as_object()
        .unwrap_or_else(|| panic!("{} must be a JSON object", path.display()));

    assert_eq!(
        object.get("version").and_then(|value| value.as_u64()),
        Some(1),
        "{} version must be 1",
        path.display()
    );
    assert_eq!(
        object
            .get("routing_backend")
            .and_then(|value| value.as_str()),
        Some("RepoMap"),
        "{} routing_backend must stay RepoMap",
        path.display()
    );
    assert_eq!(
        object
            .get("routing_reason")
            .and_then(|value| value.as_str()),
        Some("symbol-blast-radius-plan"),
        "{} routing_reason must stay symbol-blast-radius-plan",
        path.display()
    );
    assert_eq!(
        object.get("sidecar_used").and_then(|value| value.as_bool()),
        Some(false),
        "{} should stay native",
        path.display()
    );
    let coverage: CoverageExample = serde_json::from_value(
        object
            .get("coverage")
            .cloned()
            .unwrap_or_else(|| panic!("{} coverage missing", path.display())),
    )
    .unwrap_or_else(|error| panic!("{} invalid coverage: {error}", path.display()));
    assert_repo_map_coverage(path, &coverage);
    assert!(
        object
            .get("symbol")
            .and_then(|value| value.as_str())
            .is_some_and(|value| !value.is_empty()),
        "{} symbol must not be empty",
        path.display()
    );
    assert!(
        object
            .get("definitions")
            .and_then(|value| value.as_array())
            .is_some_and(|value| !value.is_empty()),
        "{} definitions must not be empty",
        path.display()
    );
    assert!(
        object
            .get("callers")
            .and_then(|value| value.as_array())
            .is_some_and(|value| !value.is_empty()),
        "{} callers must not be empty",
        path.display()
    );
    assert!(
        object
            .get("caller_tree")
            .and_then(|value| value.as_array())
            .is_some_and(|value| !value.is_empty()),
        "{} caller_tree must not be empty",
        path.display()
    );
    assert!(
        object
            .get("rendered_caller_tree")
            .and_then(|value| value.as_str())
            .is_some_and(|value| !value.is_empty()),
        "{} rendered_caller_tree must not be empty",
        path.display()
    );
    assert!(
        object
            .get("graph_trust_summary")
            .and_then(|value| value.as_object())
            .is_some(),
        "{} graph_trust_summary missing",
        path.display()
    );
    assert!(
        object
            .get("navigation_pack")
            .and_then(|value| value.as_object())
            .is_some(),
        "{} navigation_pack missing",
        path.display()
    );
}

pub fn assert_session_open_example(path: &Path) {
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

pub fn assert_session_context_example(path: &Path) {
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

pub fn assert_defs_provider_disagreement_example(path: &Path) {
    let payload: serde_json::Value = parse_json_document(path);
    let object = payload
        .as_object()
        .unwrap_or_else(|| panic!("{} must be a JSON object", path.display()));

    assert_eq!(
        object.get("version").and_then(|value| value.as_u64()),
        Some(1),
        "{} version must be 1",
        path.display()
    );
    assert_eq!(
        object
            .get("routing_backend")
            .and_then(|value| value.as_str()),
        Some("RepoMap"),
        "{} routing_backend must stay RepoMap",
        path.display()
    );
    assert_eq!(
        object
            .get("routing_reason")
            .and_then(|value| value.as_str()),
        Some("symbol-defs"),
        "{} routing_reason must stay symbol-defs",
        path.display()
    );
    assert_eq!(
        object
            .get("semantic_provider")
            .and_then(|value| value.as_str()),
        Some("hybrid"),
        "{} semantic_provider must stay hybrid",
        path.display()
    );

    let definitions = object
        .get("definitions")
        .and_then(|value| value.as_array())
        .unwrap_or_else(|| panic!("{} definitions must be an array", path.display()));
    assert!(
        !definitions.is_empty(),
        "{} definitions must not be empty",
        path.display()
    );
    let first_definition = definitions[0]
        .as_object()
        .unwrap_or_else(|| panic!("{} first definition must be an object", path.display()));
    assert!(
        first_definition
            .get("provenance")
            .and_then(|value| value.as_str())
            .is_some_and(|value| !value.is_empty()),
        "{} definition provenance must not be empty",
        path.display()
    );

    let provider_agreement = object
        .get("provider_agreement")
        .and_then(|value| value.as_object())
        .unwrap_or_else(|| panic!("{} provider_agreement missing", path.display()));
    assert_eq!(
        provider_agreement
            .get("agreement_status")
            .and_then(|value| value.as_str()),
        Some("diverged"),
        "{} provider_agreement.agreement_status must stay diverged",
        path.display()
    );

    let provider_status = object
        .get("provider_status")
        .and_then(|value| value.as_object())
        .unwrap_or_else(|| panic!("{} provider_status missing", path.display()));
    assert!(
        provider_status
            .get("available")
            .and_then(|value| value.as_bool())
            .is_some(),
        "{} provider_status.available missing",
        path.display()
    );
}

pub fn assert_session_invalid_request_example(path: &Path) {
    let payload: serde_json::Value = parse_json_document(path);
    let object = payload
        .as_object()
        .unwrap_or_else(|| panic!("{} must be a JSON object", path.display()));
    assert!(
        object
            .get("session_id")
            .and_then(|value| value.as_str())
            .is_some_and(|value| !value.is_empty()),
        "{} session_id missing",
        path.display()
    );
    let error = object
        .get("error")
        .and_then(|value| value.as_object())
        .unwrap_or_else(|| panic!("{} error object missing", path.display()));
    assert_eq!(
        error.get("code").and_then(|value| value.as_str()),
        Some("invalid_request"),
        "{} error.code must stay invalid_request",
        path.display()
    );
    assert!(
        error
            .get("message")
            .and_then(|value| value.as_str())
            .is_some_and(|value| !value.is_empty()),
        "{} error.message missing",
        path.display()
    );
}

pub fn assert_attempt_ledger_example(path: &Path) {
    let payload: serde_json::Value = parse_json_document(path);
    let object = payload
        .as_object()
        .unwrap_or_else(|| panic!("{} must be a JSON object", path.display()));

    assert_eq!(
        object.get("artifact").and_then(|value| value.as_str()),
        Some("agent_attempt_ledger"),
        "{} artifact must stay agent_attempt_ledger",
        path.display()
    );
    assert!(
        object
            .get("suite")
            .and_then(|value| value.as_str())
            .is_some_and(|value| !value.is_empty()),
        "{} suite missing",
        path.display()
    );
    assert!(
        object
            .get("replay")
            .and_then(|value| value.as_object())
            .and_then(|replay| replay.get("next_action"))
            .and_then(|value| value.as_str())
            .is_some_and(|value| !value.is_empty()),
        "{} replay.next_action missing",
        path.display()
    );

    if let Some(tasks) = object.get("tasks").and_then(|value| value.as_array()) {
        assert!(
            !tasks.is_empty(),
            "{} tasks must not be empty",
            path.display()
        );
    } else {
        assert!(
            object
                .get("task_id")
                .and_then(|value| value.as_str())
                .is_some_and(|value| !value.is_empty()),
            "{} task_id missing",
            path.display()
        );
        let attempts = object
            .get("attempts")
            .and_then(|value| value.as_array())
            .unwrap_or_else(|| panic!("{} attempts must be an array", path.display()));
        assert!(
            !attempts.is_empty(),
            "{} attempts must not be empty",
            path.display()
        );
        assert!(
            object
                .get("final_outcome")
                .and_then(|value| value.as_object())
                .and_then(|outcome| outcome.get("status"))
                .and_then(|value| value.as_str())
                .is_some_and(|value| !value.is_empty()),
            "{} final_outcome.status missing",
            path.display()
        );
    }
}

pub fn assert_patch_bakeoff_example(path: &Path) {
    let payload: serde_json::Value = parse_json_document(path);
    let object = payload
        .as_object()
        .unwrap_or_else(|| panic!("{} must be a JSON object", path.display()));
    assert_eq!(
        object.get("artifact").and_then(|value| value.as_str()),
        Some("bench_patch_bakeoff"),
        "{} artifact must stay bench_patch_bakeoff",
        path.display()
    );
    assert_eq!(
        object.get("suite").and_then(|value| value.as_str()),
        Some("run_patch_bakeoff"),
        "{} suite must stay run_patch_bakeoff",
        path.display()
    );
    let rows = object
        .get("rows")
        .and_then(|value| value.as_array())
        .unwrap_or_else(|| panic!("{} rows must be an array", path.display()));
    assert!(
        !rows.is_empty(),
        "{} rows must not be empty",
        path.display()
    );
    let summary = object
        .get("summary")
        .and_then(|value| value.as_object())
        .unwrap_or_else(|| panic!("{} summary missing", path.display()));
    assert!(
        summary
            .get("scenario_count")
            .and_then(|value| value.as_u64())
            .is_some_and(|count| count > 0),
        "{} summary.scenario_count must be positive",
        path.display()
    );
}

pub fn assert_provider_status_example(path: &Path) {
    let payload: serde_json::Value = parse_json_document(path);
    let provider_status = payload
        .get("provider_status")
        .and_then(|value| value.as_object())
        .unwrap_or_else(|| panic!("{} provider_status missing", path.display()));
    assert!(
        provider_status
            .get("mode")
            .and_then(|value| value.as_str())
            .is_some_and(|value| !value.is_empty()),
        "{} provider_status.mode missing",
        path.display()
    );
    assert!(
        provider_status.contains_key("available"),
        "{} provider_status.available missing",
        path.display()
    );
    assert!(
        provider_status
            .get("fallback_used")
            .and_then(|value| value.as_bool())
            .is_some(),
        "{} provider_status.fallback_used missing",
        path.display()
    );
}

pub fn assert_rewrite_plan_payload(path: &Path, plan: &RewritePlanExample) {
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

pub fn assert_common_envelope(
    path: &Path,
    version: u32,
    routing_backend: &str,
    routing_reason: &str,
) {
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

pub fn assert_repo_map_coverage(path: &Path, coverage: &CoverageExample) {
    // Honesty-bug fix (dogfood-found): language_scope/symbol_navigation are now derived live
    // from lang_registry.LANGUAGE_REGISTRY (see repo_map.py's _language_scope_descriptor /
    // _symbol_navigation_descriptor) instead of a hand-maintained 4-language literal, so these
    // pinned values track the full 10-language symbol-graph registry (python, javascript,
    // typescript, rust, go, java, php, csharp, c, cpp) instead of under-reporting it.
    assert_eq!(
        coverage.language_scope,
        "c-cpp-csharp-go-java-javascript-php-python-rust-typescript",
        "{} coverage.language_scope must stay c-cpp-csharp-go-java-javascript-php-python-rust-typescript",
        path.display()
    );
    // NOT hand-written: this literal must equal repo_map._symbol_navigation_descriptor().
    // Rust cannot import the Python product, so re-derive on any tier change with:
    //   python -c "import sys;sys.path.insert(0,'src');from tensor_grep.cli import repo_map as r;print(r._symbol_navigation_descriptor())"
    assert_eq!(
        coverage.symbol_navigation,
        "parser-backed-refs-callers:c-cpp-csharp-go-java-javascript-php-python-rust-typescript+foundational-defs-imports-only:",
        "{} coverage.symbol_navigation must stay parser-backed-refs-callers:c-cpp-csharp-go-java-javascript-php-python-rust-typescript+foundational-defs-imports-only:",
        path.display()
    );
    assert_eq!(
        coverage.test_matching,
        "filename+import+graph-heuristic",
        "{} coverage.test_matching must stay filename+import+graph-heuristic",
        path.display()
    );
}

pub fn parse_json_document<T>(path: &Path) -> T
where
    T: DeserializeOwned,
{
    let json = fs::read_to_string(path)
        .unwrap_or_else(|error| panic!("failed to read {}: {error}", path.display()));
    serde_json::from_str(&json)
        .unwrap_or_else(|error| panic!("{} failed schema validation: {error}", path.display()))
}

pub fn is_portable_absolute_path(path: &Path) -> bool {
    path.is_absolute() || is_windows_absolute_path_literal(&path.to_string_lossy())
}

pub fn is_windows_absolute_path_literal(value: &str) -> bool {
    let bytes = value.as_bytes();
    bytes.len() >= 3
        && bytes[0].is_ascii_alphabetic()
        && bytes[1] == b':'
        && (bytes[2] == b'\\' || bytes[2] == b'/')
}

pub fn example_file_name(path: &Path) -> &str {
    path.file_name()
        .and_then(|name| name.to_str())
        .unwrap_or_else(|| panic!("invalid example file name: {}", path.display()))
}
pub fn repo_root() -> PathBuf {
    Path::new(env!("CARGO_MANIFEST_DIR"))
        .parent()
        .expect("rust_core must live under the repo root")
        .to_path_buf()
}
