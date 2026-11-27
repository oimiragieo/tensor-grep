#![allow(unused_imports)]
use super::*;

impl AstWorkflowOrchestrator {
    pub fn new(config_path: Option<&str>) -> Result<Self> {
        let path = Path::new(config_path.unwrap_or("sgconfig.yml"));
        let resolved_config = if path.is_absolute() {
            path.to_path_buf()
        } else {
            std::env::current_dir()?.join(path)
        };

        if !resolved_config.exists() {
            anyhow::bail!(
                "Config file {:?} not found. Use `tg new` to create one.",
                resolved_config
            );
        }

        let root_dir = resolved_config
            .parent()
            .context("Config file must have a parent directory")?
            .to_path_buf();

        Ok(Self {
            root_dir,
            config_path: resolved_config,
        })
    }

    pub fn load_config(&self) -> Result<AstProjectConfig> {
        let content =
            fs::read_to_string(&self.config_path).context("Failed to read sgconfig.yml")?;
        let mut value: serde_yaml::Value =
            serde_yaml::from_str(&content).context("Failed to parse sgconfig.yml")?;
        value
            .apply_merge()
            .context("Failed to parse sgconfig.yml")?;
        let config: AstProjectConfig =
            serde_yaml::from_value(value).context("Failed to parse sgconfig.yml")?;
        Ok(config)
    }

    pub fn get_cache_dir(&self) -> PathBuf {
        self.root_dir.join(".tg_cache").join("ast")
    }

    pub fn get_cache_file(&self) -> PathBuf {
        self.get_cache_dir().join("project_data_v6.json")
    }

    pub fn load_cache(&self) -> Result<Option<ProjectDataV6>> {
        let cache_file = self.get_cache_file();
        if !cache_file.exists() {
            return Ok(None);
        }

        let content = fs::read_to_string(&cache_file)?;
        let data: ProjectDataV6 = match serde_json::from_str(&content) {
            Ok(data) => data,
            Err(_) => return Ok(None),
        };

        // M16 (F3): a legacy-schema cache (field absent/old) must not be served
        // even when mtime-validation passes -- its rule_specs lack composite
        // members and severity/message. Rebuild from source (callers treat
        // Ok(None) as a cache miss).
        if data.cache_schema_version != PROJECT_DATA_V6_SCHEMA_VERSION {
            return Ok(None);
        }

        let cache_mtime = fs::metadata(&cache_file)?.modified()?;
        let config_mtime = fs::metadata(&self.config_path)?.modified()?;

        if config_mtime > cache_mtime {
            return Ok(None);
        }

        for (path_str, recorded_mtime_ns) in &data.validation_metadata.rule_files {
            let path = Path::new(path_str);
            if !path.exists() {
                return Ok(None);
            }
            let actual_mtime = fs::metadata(path)?.modified()?;
            let actual_ns = actual_mtime
                .duration_since(SystemTime::UNIX_EPOCH)?
                .as_nanos() as u64;
            if actual_ns > *recorded_mtime_ns {
                return Ok(None);
            }
        }

        for (path_str, recorded_mtime_ns) in &data.validation_metadata.test_files {
            let path = Path::new(path_str);
            if !path.exists() {
                return Ok(None);
            }
            let actual_mtime = fs::metadata(path)?.modified()?;
            let actual_ns = actual_mtime
                .duration_since(SystemTime::UNIX_EPOCH)?
                .as_nanos() as u64;
            if actual_ns > *recorded_mtime_ns {
                return Ok(None);
            }
        }

        for (path_str, recorded_mtime_ns) in &data.validation_metadata.tree_dirs {
            let path = Path::new(path_str);
            if !path.exists() {
                return Ok(None);
            }
            let actual_mtime = fs::metadata(path)?.modified()?;
            let actual_ns = actual_mtime
                .duration_since(SystemTime::UNIX_EPOCH)?
                .as_nanos() as u64;
            if actual_ns > *recorded_mtime_ns {
                return Ok(None);
            }
        }

        Ok(Some(data))
    }

    pub fn discover_rules(
        &self,
        config: &AstProjectConfig,
    ) -> Result<(Vec<AstRuleSpec>, HashMap<String, u64>)> {
        let mut specs = Vec::new();
        let mut meta = HashMap::new();

        for rule_dir_rel in &config.rule_dirs {
            let rule_dir = self.root_dir.join(rule_dir_rel);
            if !rule_dir.exists() {
                continue;
            }

            for entry in walkdir::WalkDir::new(rule_dir)
                .into_iter()
                .filter_map(|e| e.ok())
                .filter(|e| e.file_type().is_file())
            {
                let path = entry.path();
                let ext = path.extension().and_then(|s| s.to_str()).unwrap_or("");
                if ext != "yml" && ext != "yaml" {
                    continue;
                }

                let mtime = entry.metadata()?.modified()?;
                let ns = mtime.duration_since(SystemTime::UNIX_EPOCH)?.as_nanos() as u64;
                meta.insert(path.to_string_lossy().to_string(), ns);

                let content = fs::read_to_string(path)?;
                let mut payload: serde_yaml::Value = serde_yaml::from_str(&content)?;
                payload.apply_merge()?;

                if let Some(rules) = payload.get("rules").and_then(|v| v.as_sequence()) {
                    for (idx, item) in rules.iter().enumerate() {
                        if let Some(spec) =
                            self.parse_rule_item(item, &payload, config, path, Some(idx))
                        {
                            specs.push(spec);
                        }
                    }
                } else if let Some(spec) =
                    self.parse_rule_item(&payload, &payload, config, path, None)
                {
                    specs.push(spec);
                }
            }
        }

        Ok((specs, meta))
    }

    pub(super) fn parse_rule_item(
        &self,
        item: &serde_yaml::Value,
        payload: &serde_yaml::Value,
        config: &AstProjectConfig,
        path: &Path,
        idx: Option<usize>,
    ) -> Option<AstRuleSpec> {
        let members = self.extract_rule_member_patterns(item)?;
        let id = item
            .get("id")
            .and_then(|v| v.as_str())
            .map(|s| s.to_string())
            .unwrap_or_else(|| {
                let stem = path.file_stem().and_then(|s| s.to_str()).unwrap_or("rule");
                match idx {
                    Some(i) => format!("{}-{}", stem, i + 1),
                    None => stem.to_string(),
                }
            });

        let language = item
            .get("language")
            .and_then(|v| v.as_str())
            .or_else(|| payload.get("language").and_then(|v| v.as_str()))
            .map(|s| s.to_string())
            .unwrap_or_else(|| config.language.clone());

        // Item -> payload -> default fallback, mirroring the Python project-scan
        // twin (`cli/ast_workflows.py:328-329`: `item.get(...) or payload.get(...)
        // or "warning"` / `or ""`). Empty strings fall through exactly like the
        // Python `or`.
        let severity =
            rule_metadata_string(item, payload, "severity").unwrap_or_else(default_rule_severity);
        let message = rule_metadata_string(item, payload, "message").unwrap_or_default();

        Some(AstRuleSpec {
            id,
            pattern: members[0].clone(),
            patterns: if members.len() > 1 {
                members
            } else {
                Vec::new()
            },
            severity,
            message,
            language,
        })
    }

    pub fn discover_files(
        &self,
        _config: &AstProjectConfig,
    ) -> Result<(Vec<String>, HashMap<String, u64>)> {
        use ignore::WalkBuilder;
        let mut files = Vec::new();
        let mut dir_meta = HashMap::new();

        let walker = WalkBuilder::new(&self.root_dir)
            .hidden(true)
            .git_ignore(true)
            .parents(true)
            .ignore(true)
            .filter_entry(|e| e.file_name() != ".tg_cache")
            .build();

        for entry in walker.filter_map(|e| e.ok()) {
            let path = entry.path();
            if path.is_dir() {
                let mtime = entry.metadata()?.modified()?;
                let ns = mtime.duration_since(SystemTime::UNIX_EPOCH)?.as_nanos() as u64;
                dir_meta.insert(path.to_string_lossy().to_string(), ns);
            } else if path.is_file() {
                files.push(path.to_string_lossy().to_string());
            }
        }

        Ok((files, dir_meta))
    }

    pub fn save_cache(&self, data: &ProjectDataV6) -> Result<()> {
        let cache_file = self.get_cache_file();
        if let Some(parent) = cache_file.parent() {
            fs::create_dir_all(parent)?;
        }
        let content = serde_json::to_string_pretty(data)?;
        fs::write(cache_file, content)?;
        Ok(())
    }

    pub fn precompute_orchestration_hints(&self, rule_specs: &[AstRuleSpec]) -> serde_json::Value {
        let mut backend_hints = HashMap::new();
        for rule in rule_specs {
            let backend_name = self.select_ast_backend_name_for_pattern(&rule.pattern);
            backend_hints.insert(rule.id.clone(), backend_name);
        }
        serde_json::json!({
            "backend_hints": backend_hints
        })
    }

    pub fn discover_tests(
        &self,
        config: &AstProjectConfig,
    ) -> Result<(Vec<serde_json::Value>, HashMap<String, u64>)> {
        let mut test_data = Vec::new();
        let mut meta = HashMap::new();

        for test_dir_rel in &config.test_dirs {
            let test_dir = self.root_dir.join(test_dir_rel);
            if !test_dir.exists() {
                continue;
            }

            for entry in walkdir::WalkDir::new(test_dir)
                .into_iter()
                .filter_map(|e| e.ok())
                .filter(|e| e.file_type().is_file())
            {
                let path = entry.path();
                let ext = path.extension().and_then(|s| s.to_str()).unwrap_or("");
                if ext != "yml" && ext != "yaml" {
                    continue;
                }

                let mtime = entry.metadata()?.modified()?;
                let ns = mtime.duration_since(SystemTime::UNIX_EPOCH)?.as_nanos() as u64;
                meta.insert(path.to_string_lossy().to_string(), ns);

                let content = fs::read_to_string(path)?;
                let payload: serde_yaml::Value = serde_yaml::from_str(&content)?;

                let raw_cases = payload
                    .get("tests")
                    .and_then(|v| v.as_sequence())
                    .cloned()
                    .unwrap_or_else(|| {
                        if payload.is_mapping() {
                            vec![payload.clone()]
                        } else {
                            Vec::new()
                        }
                    });

                let cases: Vec<serde_json::Value> = raw_cases
                    .iter()
                    .filter_map(|v| serde_json::to_value(v).ok())
                    .filter(|v| v.is_object())
                    .collect();

                test_data.push(serde_json::json!({
                    "file": path.to_string_lossy().to_string(),
                    "stem": path.file_stem().and_then(|s| s.to_str()).unwrap_or("test"),
                    "cases": cases,
                }));
            }
        }

        Ok((test_data, meta))
    }

    pub fn load_project_data(&self) -> Result<ProjectDataV6> {
        if let Some(cached) = self.load_cache().ok().flatten() {
            return Ok(cached);
        }

        let config = self.load_config()?;
        let (rule_specs, rule_files_meta) = self.discover_rules(&config)?;
        let (test_data, test_files_meta) = self.discover_tests(&config)?;
        let (candidate_files, tree_dirs_meta) = self.discover_files(&config)?;

        let orchestration_hints = self.precompute_orchestration_hints(&rule_specs);

        let data = ProjectDataV6 {
            project_cfg: serde_json::to_value(&config)?,
            rule_specs,
            candidate_files,
            test_data,
            orchestration_hints,
            validation_metadata: ValidationMetadata {
                rule_files: rule_files_meta,
                test_files: test_files_meta,
                tree_dirs: tree_dirs_meta,
            },
            cache_schema_version: PROJECT_DATA_V6_SCHEMA_VERSION,
        };

        self.save_cache(&data)?;
        Ok(data)
    }

    pub fn select_ast_backend_name_for_pattern(&self, pattern: &str) -> &str {
        let stripped = pattern.trim();
        if stripped.is_empty() {
            return "AstGrepWrapperBackend";
        }

        let is_native = if stripped.starts_with('(') {
            true
        } else {
            let mut chars = stripped.chars();
            if let Some(first) = chars.next() {
                if first.is_ascii_alphabetic() || first == '_' {
                    chars.all(|c| c.is_ascii_alphanumeric() || c == '_')
                } else {
                    false
                }
            } else {
                false
            }
        };

        if is_native {
            "AstBackend"
        } else {
            "AstGrepWrapperBackend"
        }
    }

    pub fn extract_rule_pattern(&self, item: &serde_yaml::Value) -> Option<String> {
        self.extract_rule_member_patterns(item)
            .map(|mut members| members.swap_remove(0))
    }

    /// Extract the member patterns of a rule item (M16).
    ///
    /// Supported shapes, matching ast-grep rule YAML as the Python project-scan
    /// twin consumes it:
    /// - a flat `pattern:` STRING,
    /// - a `pattern:` LIST of strings,
    /// - a `rule:` mapping whose `pattern` is a string,
    /// - a `rule:` mapping whose `any:` sequence lists sub-rules (each
    ///   sub-rule's `pattern` string, or its nested `rule.pattern` string).
    ///
    /// A composite member that does not carry exactly one extractable pattern
    /// fails the WHOLE rule closed (None) rather than under-matching. `all:` /
    /// `not:` composite bodies require same-node intersection semantics the
    /// native per-pattern matcher cannot express; they also return None, which
    /// keeps them dropped exactly as the Python twin drops them.
    pub fn extract_rule_member_patterns(&self, item: &serde_yaml::Value) -> Option<Vec<String>> {
        if let Some(p) = item.get("pattern") {
            if let Some(s) = p.as_str() {
                let trimmed = s.trim();
                if !trimmed.is_empty() {
                    return Some(vec![trimmed.to_string()]);
                }
            } else if let Some(seq) = p.as_sequence() {
                return collect_pattern_strings(seq);
            }
        }
        if let Some(rule) = item.get("rule").and_then(|v| v.as_mapping()) {
            if let Some(s) = rule
                .get(serde_yaml::Value::String("pattern".to_string()))
                .and_then(|v| v.as_str())
                .filter(|s| !s.trim().is_empty())
            {
                let trimmed = s.trim();
                return Some(vec![trimmed.to_string()]);
            }
            if let Some(any_seq) = rule
                .get(serde_yaml::Value::String("any".to_string()))
                .and_then(|v| v.as_sequence())
            {
                return collect_any_member_patterns(any_seq);
            }
        }
        None
    }

    pub fn extract_rule_pattern_json(&self, item: &serde_json::Value) -> Option<String> {
        if let Some(p) = item.get("pattern").and_then(|v| v.as_str()) {
            if !p.trim().is_empty() {
                return Some(p.trim().to_string());
            }
        }
        if let Some(rule) = item.get("rule").and_then(|v| v.as_object()) {
            if let Some(p) = rule.get("pattern").and_then(|v| v.as_str()) {
                if !p.trim().is_empty() {
                    return Some(p.trim().to_string());
                }
            }
        }
        None
    }

    pub fn normalize_string_list(&self, val: Option<&serde_json::Value>) -> Vec<String> {
        match val {
            Some(serde_json::Value::String(s)) => vec![s.clone()],
            Some(serde_json::Value::Array(arr)) => arr
                .iter()
                .filter_map(|v| v.as_str().map(|s| s.to_string()))
                .collect(),
            _ => Vec::new(),
        }
    }
}

/// Item -> payload metadata fallback mirroring the Python project-scan twin:
/// `str(item.get(key) or payload.get(key) or "warning" / "")`. Python converts
/// TRUTHY scalars with str(): `"high"` -> `"high"`, `5` -> `"5"`, `true` ->
/// `"True"`; falsy values (`""`, `0`, `0.0`, `false`, `null`) fall through to
/// the payload and then the default — reproduced here. Structural values
/// (sequences/mappings) are FAILED CLOSED (treated as unresolvable, so the
/// default wins) instead of being repr-converted like Python's str(); a
/// severity/message that is a list is malformed input and the safe direction
/// is the default.
fn rule_metadata_string(
    item: &serde_yaml::Value,
    payload: &serde_yaml::Value,
    key: &str,
) -> Option<String> {
    rule_metadata_scalar_string(item.get(key))
        .or_else(|| rule_metadata_scalar_string(payload.get(key)))
}

/// Truthy-scalar conversion for a single YAML value (see `rule_metadata_string`).
/// Non-finite floats are special-cased to Python's `str(float)` spellings
/// (`nan` / `inf` / `-inf`) instead of serde_yaml's `.nan` / `.inf` / `-.inf`
/// (F3); all are truthy in Python like here.
fn rule_metadata_scalar_string(value: Option<&serde_yaml::Value>) -> Option<String> {
    let value = value?;
    match value {
        serde_yaml::Value::String(s) if !s.is_empty() => Some(s.clone()),
        serde_yaml::Value::Bool(true) => Some("True".to_string()),
        serde_yaml::Value::Bool(false) => None,
        serde_yaml::Value::Number(n) => {
            if let Some(f) = n.as_f64() {
                if f.is_nan() {
                    return Some("nan".to_string());
                }
                if f.is_infinite() {
                    return Some(if f.is_sign_negative() {
                        "-inf".to_string()
                    } else {
                        "inf".to_string()
                    });
                }
                let truthy = f != 0.0;
                return truthy.then(|| n.to_string());
            }
            let truthy = n
                .as_i64()
                .map(|i| i != 0)
                .or_else(|| n.as_u64().map(|u| u != 0))
                .unwrap_or(true);
            truthy.then(|| n.to_string())
        }
        _ => None,
    }
}

/// Collect non-empty trimmed strings from a `pattern:` LIST. A member that is
/// not a non-empty string fails the whole rule closed (None).
fn collect_pattern_strings(seq: &[serde_yaml::Value]) -> Option<Vec<String>> {
    let mut out = Vec::with_capacity(seq.len());
    for v in seq {
        let trimmed = v.as_str()?.trim();
        if trimmed.is_empty() {
            return None;
        }
        out.push(trimmed.to_string());
    }
    if out.is_empty() {
        return None;
    }
    Some(out)
}

/// Collect member patterns from a `rule: { any: [...] }` sequence. Each member
/// must carry exactly one extractable pattern (string `pattern` or nested
/// `rule.pattern`); a member that cannot (e.g. an `all:`/`not:` body) fails the
/// whole rule closed rather than under-matching.
fn collect_any_member_patterns(any_seq: &[serde_yaml::Value]) -> Option<Vec<String>> {
    let mut out = Vec::with_capacity(any_seq.len());
    for member in any_seq {
        let m = member.as_mapping()?;
        let pattern_value = m
            .get(serde_yaml::Value::String("pattern".to_string()))
            .and_then(|v| v.as_str())
            .or_else(|| {
                m.get(serde_yaml::Value::String("rule".to_string()))
                    .and_then(|v| v.as_mapping())
                    .and_then(|r| r.get(serde_yaml::Value::String("pattern".to_string())))
                    .and_then(|v| v.as_str())
            });
        let trimmed = pattern_value?.trim();
        if trimmed.is_empty() {
            return None;
        }
        out.push(trimmed.to_string());
    }
    if out.is_empty() {
        return None;
    }
    Some(out)
}

/// The member patterns a rule must be matched against. For a composite rule
/// (`patterns` non-empty) the members are authoritative and `pattern` is the
/// first member; a single-pattern rule yields exactly one member.
pub(super) fn ast_rule_member_patterns(rule: &AstRuleSpec) -> Vec<String> {
    if rule.patterns.is_empty() {
        vec![rule.pattern.clone()]
    } else {
        rule.patterns.clone()
    }
}
