#![allow(unused_imports)]
use super::*;

#[allow(clippy::module_inception)]
#[cfg(test)]
mod tests {
    // M16 audit: Rust `tg scan` (handle_ast_scan, config route) DROPPED composite
    // rules (`rule: { any: [...] }` and `pattern:` LIST shapes) and custom
    // severity/message. Pre-fix, `parse_rule_item` (`:879`) early-returns None
    // via `extract_rule_pattern(item)?` whenever the item has no flat string
    // `pattern`/`rule.pattern` (`:1074-1087`), and `AstRuleSpec` carried only
    // `{id, pattern, language}` so severity/message could not survive discovery.
    // The Python project-scan twin (`cli/ast_workflows.py:_load_rule_specs_and_meta`)
    // already threads severity/message into rule specs and findings; the Rust
    // route is the gap this module closes. CI is the compile/test oracle; the
    // structural RED arguments are recorded per test so a regression is
    // attributable without a local compile.
    use super::*;
    use tempfile::tempdir;

    fn orchestrator_for_tests() -> AstWorkflowOrchestrator {
        AstWorkflowOrchestrator {
            root_dir: PathBuf::from("."),
            config_path: PathBuf::from("sgconfig.yml"),
        }
    }

    fn config_for_tests() -> AstProjectConfig {
        AstProjectConfig {
            rule_dirs: vec!["rules".to_string()],
            test_dirs: vec!["tests".to_string()],
            language: "python".to_string(),
        }
    }

    /// RED (pre-fix): `extract_rule_pattern` only reads flat string
    /// `pattern` / `rule.pattern`, so a `rule: { any: [...] }` item yields None
    /// at `parse_rule_item:879` and the rule is DROPPED (`discover_rules` skips
    /// None entries). GREEN (post-fix): `extract_rule_member_patterns` reads the
    /// `any:` sequence, so one spec is produced whose `pattern` is the FIRST
    /// member and whose `patterns` carries ALL members.
    #[test]
    fn composite_any_rule_parses_with_all_members_kept() {
        let item: serde_yaml::Value = serde_yaml::from_str(
            "id: no-print\n\
             language: python\n\
             severity: high\n\
             message: Avoid print calls.\n\
             rule:\n\
             \x20 any:\n\
             \x20   - pattern: print($A)\n\
             \x20   - pattern: println($A)\n",
        )
        .unwrap();
        let payload = item.clone();
        let spec = orchestrator_for_tests()
            .parse_rule_item(
                &item,
                &payload,
                &config_for_tests(),
                Path::new("no-print.yml"),
                Some(0),
            )
            .expect("composite any-of rule must be parsed, not dropped");
        assert_eq!(spec.id, "no-print");
        assert_eq!(spec.pattern, "print($A)");
        assert_eq!(spec.patterns, vec!["print($A)", "println($A)"]);
        assert_eq!(spec.language, "python");
    }

    /// RED (pre-fix): a `pattern:` LIST has no `as_str`, so
    /// `extract_rule_pattern` returns None and the rule is dropped.
    /// GREEN (post-fix): the sequence is collected into member patterns.
    #[test]
    fn pattern_list_rule_parses_with_all_members_kept() {
        let item: serde_yaml::Value = serde_yaml::from_str(
            "id: multi-pattern\n\
             language: python\n\
             pattern:\n\
             \x20 - alpha(x)\n\
             \x20 - beta(x)\n",
        )
        .unwrap();
        let payload = item.clone();
        let spec = orchestrator_for_tests()
            .parse_rule_item(
                &item,
                &payload,
                &config_for_tests(),
                Path::new("multi-pattern.yml"),
                Some(0),
            )
            .expect("pattern-list rule must be parsed, not dropped");
        assert_eq!(spec.id, "multi-pattern");
        assert_eq!(spec.pattern, "alpha(x)");
        assert_eq!(spec.patterns, vec!["alpha(x)", "beta(x)"]);
        assert_eq!(spec.language, "python");
    }

    /// RED (pre-fix): `AstRuleSpec` has no `severity`/`message` fields, so this
    /// test does not COMPILE against the pre-fix struct — the fields (and thus
    /// the drop of the metadata) are the defect. GREEN (post-fix): the custom
    /// severity/message from the rule item round-trip into the emitted spec.
    #[test]
    fn custom_severity_and_message_survive_rule_parsing() {
        let item: serde_yaml::Value = serde_yaml::from_str(
            "id: sev-msg\n\
             language: python\n\
             severity: error\n\
             message: Custom message text.\n\
             pattern: danger(x)\n",
        )
        .unwrap();
        let payload = item.clone();
        let spec = orchestrator_for_tests()
            .parse_rule_item(
                &item,
                &payload,
                &config_for_tests(),
                Path::new("sev-msg.yml"),
                Some(0),
            )
            .expect("rule with severity/message must be parsed");
        assert_eq!(spec.severity, "error");
        assert_eq!(spec.message, "Custom message text.");
    }

    /// Mirrors the Python twin `_load_rule_specs_and_meta`
    /// (`cli/ast_workflows.py:328-329`): severity/message fall back item ->
    /// payload -> default ("warning" / ""). RED (pre-fix) at compile time (fields
    /// absent); GREEN (post-fix) with identical fallback order.
    #[test]
    fn severity_and_message_fall_back_to_payload_then_defaults() {
        let payload: serde_yaml::Value = serde_yaml::from_str(
            "id: top\n\
             language: python\n\
             severity: critical\n\
             message: Payload message.\n\
             rules:\n\
             \x20 - pattern: toprule(x)\n",
        )
        .unwrap();
        let item = payload.get("rules").unwrap().get(0).unwrap().clone();

        let spec = orchestrator_for_tests()
            .parse_rule_item(
                &item,
                &payload,
                &config_for_tests(),
                Path::new("top.yml"),
                None,
            )
            .expect("payload-level metadata must apply to bare rule items");
        assert_eq!(spec.severity, "critical");
        assert_eq!(spec.message, "Payload message.");

        let bare: serde_yaml::Value = serde_yaml::from_str(
            "id: no-meta\n\
             language: python\n\
             pattern: plain(x)\n",
        )
        .unwrap();
        let bare_payload = bare.clone();
        let defaulted = orchestrator_for_tests()
            .parse_rule_item(
                &bare,
                &bare_payload,
                &config_for_tests(),
                Path::new("no-meta.yml"),
                None,
            )
            .expect("metadata-less rule must still parse");
        assert_eq!(defaulted.severity, "warning");
        assert_eq!(defaulted.message, "");
    }

    /// Fail-closed scope pin for M16: an `all:`-only composite body needs
    /// same-node intersection semantics the native per-pattern matcher cannot
    /// express, so it MUST stay dropped (None) on both pre- and post-fix code.
    /// This is a behavior pin, not a red-green arm: it passes in BOTH arms and
    /// exists to prevent a future half-implementation that would under-match.
    #[test]
    fn all_only_composite_rule_stays_dropped_fail_closed() {
        let item: serde_yaml::Value = serde_yaml::from_str(
            "id: all-only\n\
             language: python\n\
             rule:\n\
             \x20 all:\n\
             \x20   - pattern: a(x)\n\
             \x20   - pattern: b(x)\n",
        )
        .unwrap();
        let payload = item.clone();
        assert!(
            orchestrator_for_tests()
                .parse_rule_item(
                    &item,
                    &payload,
                    &config_for_tests(),
                    Path::new("all-only.yml"),
                    Some(0),
                )
                .is_none(),
            "all:-only composite rules must be dropped, never under-matched"
        );
    }

    /// Cache-compatibility pin: `ProjectDataV6` is persisted to
    /// `.tg_cache/ast/project_data_v6.json`; a cache written by a pre-M16 build
    /// serializes `AstRuleSpec` WITHOUT the new fields, so the new fields must
    /// carry `serde(default)` and deserialize to the defaults. RED (pre-fix) at
    /// compile time (fields absent); GREEN (post-fix) with the defaults.
    #[test]
    fn stale_cache_rule_spec_json_without_new_fields_deserializes_with_defaults() {
        let json = r#"{"id":"r1","pattern":"foo(x)","language":"python"}"#;
        let spec: AstRuleSpec = serde_json::from_str(json).unwrap();
        assert_eq!(spec.patterns, Vec::<String>::new());
        assert_eq!(spec.severity, "warning");
        assert_eq!(spec.message, "");
        assert_eq!(spec.pattern, "foo(x)");
    }

    /// End-to-end union counting: a composite rule (two member patterns) must
    /// match when EITHER member matches, count distinct files once, and sum
    /// member matches — proven through the REAL AstBackend (tree-sitter) over a
    /// temp fixture, so no rg/ast-grep binary is involved. RED (pre-fix) at
    /// compile time: `AstRuleSpec` had no `patterns` field and there was no
    /// member-union path in `execute_ast_scan_core` (it consumed only
    /// `rule.pattern`, `:236/:242`). GREEN (post-fix): members are unioned.
    #[test]
    fn scan_core_counts_composite_rule_as_union_across_members() {
        let dir = tempdir().unwrap();
        let file_path = dir.path().join("sample.py");
        fs::write(
            &file_path,
            "alpha_result = alpha(1)\nbeta_result = beta(2)\n",
        )
        .unwrap();

        let orchestrator = AstWorkflowOrchestrator {
            root_dir: dir.path().to_path_buf(),
            config_path: dir.path().join("sgconfig.yml"),
        };
        let rule_spec = AstRuleSpec {
            id: "composite".to_string(),
            pattern: "alpha".to_string(),
            patterns: vec!["alpha".to_string(), "beta".to_string()],
            severity: "high".to_string(),
            message: "avoid alpha and beta".to_string(),
            language: "python".to_string(),
        };
        let data = ProjectDataV6 {
            project_cfg: serde_json::json!({}),
            rule_specs: vec![rule_spec.clone()],
            candidate_files: vec![file_path.to_string_lossy().into_owned()],
            test_data: Vec::new(),
            orchestration_hints: orchestrator.precompute_orchestration_hints(&[rule_spec]),
            validation_metadata: ValidationMetadata {
                rule_files: HashMap::new(),
                test_files: HashMap::new(),
                tree_dirs: HashMap::new(),
            },
            cache_schema_version: PROJECT_DATA_V6_SCHEMA_VERSION,
        };
        let mut lang_to_files: HashMap<String, Vec<PathBuf>> = HashMap::new();
        lang_to_files.insert("python".to_string(), vec![file_path]);

        let backend = AstBackend::new();
        let mut output = Vec::new();
        let success =
            execute_ast_scan_core(&orchestrator, &data, &backend, &lang_to_files, &mut output)
                .unwrap();
        assert!(success);
        let stdout = String::from_utf8(output).unwrap();
        assert!(
            stdout.contains("[scan] rule=composite lang=python matches=2 files=1"),
            "unexpected scan line in: {stdout}"
        );
        assert!(
            stdout.contains("Scan completed. rules=1 matched_rules=1 total_matches=2"),
            "unexpected summary in: {stdout}"
        );
    }

    /// F1 RED (pre-fix of THIS commit): the round-1 union deduplicated by
    /// (file, line), so `alpha(1); alpha(2)` with members `alpha` + `alpha(1)`
    /// counted 1 — but whole-config ast-grep counts 3 (two DISTINCT identifier
    /// nodes and one call node all on the same line; measured against real
    /// ast-grep by the codex gate). GREEN (post-fix): the union identity is the
    /// AST SPAN (file, start_byte, end_byte) — each node counts once; only the
    /// SAME node matched by several members deduplicates. A single-pattern rule
    /// keeps the legacy per-node count (2), and a duplicate-member composite
    /// (same span twice) counts 1, not 2.
    #[test]
    fn scan_core_counts_composite_span_union_and_keeps_single_pattern_node_counts() {
        let dir = tempdir().unwrap();
        let file_path = dir.path().join("sample.py");
        // One line: two `alpha` identifier nodes (at bytes [0,5) and [9,14))
        // and one `alpha(1)` call node ([0,8)) — three distinct AST spans.
        fs::write(&file_path, "alpha(1); alpha(2)\n").unwrap();

        let orchestrator = AstWorkflowOrchestrator {
            root_dir: dir.path().to_path_buf(),
            config_path: dir.path().join("sgconfig.yml"),
        };
        let single = AstRuleSpec {
            id: "single".to_string(),
            pattern: "alpha".to_string(),
            patterns: Vec::new(),
            severity: "warning".to_string(),
            message: String::new(),
            language: "python".to_string(),
        };
        let composite = AstRuleSpec {
            id: "composite".to_string(),
            pattern: "alpha".to_string(),
            patterns: vec!["alpha".to_string(), "alpha(1)".to_string()],
            severity: "high".to_string(),
            message: String::new(),
            language: "python".to_string(),
        };
        let duplicate_member = AstRuleSpec {
            id: "duplicate-member".to_string(),
            pattern: "alpha(1)".to_string(),
            patterns: vec!["alpha(1)".to_string(), "alpha(1)".to_string()],
            severity: "medium".to_string(),
            message: String::new(),
            language: "python".to_string(),
        };
        let data = ProjectDataV6 {
            project_cfg: serde_json::json!({}),
            rule_specs: vec![single.clone(), composite.clone(), duplicate_member.clone()],
            candidate_files: vec![file_path.to_string_lossy().into_owned()],
            test_data: Vec::new(),
            orchestration_hints: orchestrator.precompute_orchestration_hints(&[
                single,
                composite.clone(),
                duplicate_member,
            ]),
            validation_metadata: ValidationMetadata {
                rule_files: HashMap::new(),
                test_files: HashMap::new(),
                tree_dirs: HashMap::new(),
            },
            cache_schema_version: PROJECT_DATA_V6_SCHEMA_VERSION,
        };
        let mut lang_to_files: HashMap<String, Vec<PathBuf>> = HashMap::new();
        lang_to_files.insert("python".to_string(), vec![file_path]);

        let backend = AstBackend::new();
        let mut output = Vec::new();
        execute_ast_scan_core(&orchestrator, &data, &backend, &lang_to_files, &mut output).unwrap();
        let stdout = String::from_utf8(output).unwrap();
        assert!(
            stdout.contains("[scan] rule=single lang=python matches=2 files=1"),
            "single-pattern per-node count must be preserved: {stdout}"
        );
        assert!(
            stdout.contains("[scan] rule=composite lang=python matches=3 files=1"),
            "composite must count each distinct AST span (2 identifiers + 1 call): {stdout}"
        );
        assert!(
            stdout.contains("[scan] rule=duplicate-member lang=python matches=1 files=1"),
            "the same node matched by two members must count once: {stdout}"
        );
        assert!(
            stdout.contains("Scan completed. rules=3 matched_rules=3 total_matches=6"),
            "unexpected summary in: {stdout}"
        );
    }

    /// F3 RED (pre-fix of THIS commit): a pre-M16 cache (no `cache_schema_version`,
    /// specs without severity/message) deserialized via serde defaults and was
    /// SERVED on mtime freshness, keeping stale rule truths forever. GREEN
    /// (post-fix): `load_cache` rejects the legacy discriminator and returns
    /// None so `load_project_data` rebuilds from source YAML.
    #[test]
    fn load_cache_rejects_legacy_schema_cache() {
        let dir = tempdir().unwrap();
        let config_path = dir.path().join("sgconfig.yml");
        fs::write(&config_path, "language: python\n").unwrap();
        let orchestrator = AstWorkflowOrchestrator {
            root_dir: dir.path().to_path_buf(),
            config_path,
        };
        let cache_file = orchestrator.get_cache_file();
        fs::create_dir_all(cache_file.parent().unwrap()).unwrap();
        fs::write(
            &cache_file,
            r#"{"project_cfg":{},"rule_specs":[{"id":"r1","pattern":"foo(x)","language":"python"}],"candidate_files":[],"test_data":[],"orchestration_hints":{},"validation_metadata":{"rule_files":{},"test_files":{},"tree_dirs":{}}}"#,
        )
        .unwrap();
        assert!(
            orchestrator.load_cache().unwrap().is_none(),
            "legacy-schema cache must be rejected and rebuilt from source"
        );
    }

    /// F3 pin: a CURRENT-schema cache (with the discriminator) is still served
    /// on mtime freshness, carrying composite members and severity/message.
    #[test]
    fn load_cache_accepts_current_schema_cache() {
        let dir = tempdir().unwrap();
        let config_path = dir.path().join("sgconfig.yml");
        fs::write(&config_path, "language: python\n").unwrap();
        let orchestrator = AstWorkflowOrchestrator {
            root_dir: dir.path().to_path_buf(),
            config_path,
        };
        let data = ProjectDataV6 {
            project_cfg: serde_json::json!({"language": "python"}),
            rule_specs: vec![AstRuleSpec {
                id: "r1".to_string(),
                pattern: "foo(x)".to_string(),
                patterns: vec!["foo(x)".to_string(), "bar(x)".to_string()],
                severity: "high".to_string(),
                message: "custom message".to_string(),
                language: "python".to_string(),
            }],
            candidate_files: Vec::new(),
            test_data: Vec::new(),
            orchestration_hints: serde_json::json!({}),
            validation_metadata: ValidationMetadata {
                rule_files: HashMap::new(),
                test_files: HashMap::new(),
                tree_dirs: HashMap::new(),
            },
            cache_schema_version: PROJECT_DATA_V6_SCHEMA_VERSION,
        };
        let cache_file = orchestrator.get_cache_file();
        fs::create_dir_all(cache_file.parent().unwrap()).unwrap();
        fs::write(&cache_file, serde_json::to_string(&data).unwrap()).unwrap();
        let loaded = orchestrator
            .load_cache()
            .unwrap()
            .expect("current-schema cache must be served");
        assert_eq!(loaded.rule_specs[0].patterns.len(), 2);
        assert_eq!(loaded.rule_specs[0].severity, "high");
        assert_eq!(loaded.rule_specs[0].message, "custom message");
    }

    /// F4 RED (pre-fix of THIS commit): `as_str()` discarded truthy non-string
    /// scalars, so `severity: 5` / `severity: true` fell back to "warning"
    /// while Python's `str(...)` produced "5" / "True". GREEN (post-fix): the
    /// conversion mirrors Python's truthiness + str().
    #[test]
    fn severity_message_accept_truthy_nonstring_scalars_like_python() {
        let orch = orchestrator_for_tests();
        let cfg = config_for_tests();

        let int_item: serde_yaml::Value = serde_yaml::from_str(
            "id: nummeta\n\
             language: python\n\
             severity: 5\n\
             pattern: danger(x)\n",
        )
        .unwrap();
        let int_payload = int_item.clone();
        let spec = orch
            .parse_rule_item(
                &int_item,
                &int_payload,
                &cfg,
                Path::new("nummeta.yml"),
                None,
            )
            .expect("numeric severity must parse");
        assert_eq!(spec.severity, "5");

        let bool_item: serde_yaml::Value = serde_yaml::from_str(
            "id: boolmeta\n\
             language: python\n\
             severity: true\n\
             pattern: danger(x)\n",
        )
        .unwrap();
        let bool_payload = bool_item.clone();
        let spec = orch
            .parse_rule_item(
                &bool_item,
                &bool_payload,
                &cfg,
                Path::new("boolmeta.yml"),
                None,
            )
            .expect("boolean severity must parse");
        assert_eq!(spec.severity, "True");

        let falsy_item: serde_yaml::Value = serde_yaml::from_str(
            "id: falsymeta\n\
             language: python\n\
             severity: 0\n\
             message: 7\n\
             pattern: danger(x)\n",
        )
        .unwrap();
        let falsy_payload = falsy_item.clone();
        let spec = orch
            .parse_rule_item(
                &falsy_item,
                &falsy_payload,
                &cfg,
                Path::new("falsymeta.yml"),
                None,
            )
            .expect("falsy metadata must still parse");
        assert_eq!(spec.severity, "warning");
        assert_eq!(spec.message, "7");
    }

    /// F3 RED (pre-fix of THIS commit): serde_yaml's `Number` display emits
    /// `.nan` / `.inf` / `-.inf` while Python's `str(float)` emits `nan` /
    /// `inf` / `-inf`. GREEN (post-fix): non-finite floats are special-cased to
    /// the Python spellings (all truthy, so they pass through like Python).
    #[test]
    fn nonfinite_float_metadata_uses_python_spellings() {
        let orch = orchestrator_for_tests();
        let cfg = config_for_tests();

        for (yaml_scalar, expected) in [(".nan", "nan"), (".inf", "inf"), ("-.inf", "-inf")] {
            let item: serde_yaml::Value = serde_yaml::from_str(&format!(
                "id: floatmeta\n\
                 language: python\n\
                 severity: {yaml_scalar}\n\
                 pattern: danger(x)\n"
            ))
            .unwrap();
            let payload = item.clone();
            let spec = orch
                .parse_rule_item(&item, &payload, &cfg, Path::new("floatmeta.yml"), None)
                .expect("non-finite float severity must parse");
            assert_eq!(spec.severity, expected);
        }
    }
}

#[cfg(test)]
#[path = "../backend_ast_workflow_scaffold_tests.rs"]
mod scaffold_tests;
