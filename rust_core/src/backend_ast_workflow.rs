use anyhow::{Context, Result};
use serde::{Deserialize, Serialize};
use std::collections::{HashMap, BTreeSet, HashSet};
use std::fs;
use std::io::Write;
use std::path::{Path, PathBuf};
use std::time::SystemTime;
use crate::backend_ast::AstBackend;

#[derive(Debug, Serialize, Deserialize, Clone)]
pub struct AstProjectConfig {
    #[serde(rename = "ruleDirs", default = "default_rule_dirs")]
    pub rule_dirs: Vec<String>,
    #[serde(rename = "testDirs", default = "default_test_dirs")]
    pub test_dirs: Vec<String>,
    #[serde(default = "default_language")]
    pub language: String,
}

fn default_rule_dirs() -> Vec<String> {
    vec!["rules".to_string()]
}

fn default_test_dirs() -> Vec<String> {
    vec!["tests".to_string()]
}

fn default_language() -> String {
    "python".to_string()
}

#[derive(Debug, Serialize, Deserialize, Clone)]
pub struct AstRuleSpec {
    pub id: String,
    pub pattern: String,
    pub language: String,
}

#[derive(Debug, Serialize, Deserialize, Clone)]
pub struct ValidationMetadata {
    pub rule_files: HashMap<String, u64>,
    pub test_files: HashMap<String, u64>,
    pub tree_dirs: HashMap<String, u64>,
}

#[derive(Debug, Serialize, Deserialize, Clone)]
pub struct ProjectDataV6 {
    pub project_cfg: serde_json::Value,
    pub rule_specs: Vec<AstRuleSpec>,
    pub candidate_files: Vec<String>,
    pub test_data: Vec<serde_json::Value>,
    pub orchestration_hints: serde_json::Value,
    pub validation_metadata: ValidationMetadata,
}

pub struct AstWorkflowOrchestrator {
    pub root_dir: PathBuf,
    pub config_path: PathBuf,
}

#[derive(Debug, Clone)]
struct BatchTestSnippet {
    case_key: String,
    snippet: String,
    expected_match: bool,
}

#[derive(Debug, Serialize, Deserialize)]
#[serde(tag = "command", content = "args")]
pub enum SessionRequest {
    #[serde(rename = "scan")]
    Scan { config_path: Option<String> },
    #[serde(rename = "test")]
    Test { config_path: Option<String> },
    #[serde(rename = "defs")]
    Defs {
        path: String,
        symbol: String,
        provider: String,
    },
    #[serde(rename = "refs")]
    Refs {
        path: String,
        symbol: String,
        provider: String,
    },
    #[serde(rename = "context")]
    Context {
        path: String,
        query: String,
    },
    #[serde(rename = "stop")]
    Stop,
}

#[derive(Debug, Serialize, Deserialize)]
pub struct SessionResponse {
    pub success: bool,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub error: Option<String>,
}

/// Resident worker state to keep metadata and backend warm in memory.
pub struct ResidentAstWorker {
    pub orchestrator: Option<AstWorkflowOrchestrator>,
    pub data: Option<ProjectDataV6>,
    pub backend: AstBackend,
    pub lang_to_files: HashMap<String, Vec<PathBuf>>,
}

impl ResidentAstWorker {
    pub fn new() -> Self {
        Self {
            orchestrator: None,
            data: None,
            backend: AstBackend::new(),
            lang_to_files: HashMap::new(),
        }
    }

    pub fn ensure_project(&mut self, config_path: Option<&str>) -> Result<()> {
        let orchestrator = AstWorkflowOrchestrator::new(config_path)?;
        
        let reload = match &self.orchestrator {
            Some(existing) => existing.config_path != orchestrator.config_path,
            None => true,
        };

        if reload {
            let data = orchestrator.load_project_data()?;
            self.update_data(data);
            self.orchestrator = Some(orchestrator);
        } else {
            let orch = self.orchestrator.as_ref().unwrap();
            if orch.load_cache()?.is_none() {
                let data = orch.load_project_data()?;
                self.update_data(data);
            }
        }

        Ok(())
    }

    fn update_data(&mut self, data: ProjectDataV6) {
        self.lang_to_files.clear();
        for path_str in &data.candidate_files {
            let path = PathBuf::from(path_str);
            for lang in &["python", "javascript", "typescript", "rust"] {
                if file_matches_language(&path, lang) {
                    self.lang_to_files.entry(lang.to_string()).or_default().push(path.clone());
                }
            }
        }
        self.data = Some(data);
    }
}

pub fn handle_ast_session_serve() -> Result<()> {
    Ok(())
}

pub fn handle_ast_scan(config_path: Option<&str>) -> Result<()> {
    let orchestrator = AstWorkflowOrchestrator::new(config_path)?;
    let data = orchestrator.load_project_data()?;
    let backend = AstBackend::new();
    
    let mut lang_to_files: HashMap<String, Vec<PathBuf>> = HashMap::new();
    for path_str in &data.candidate_files {
        let path = PathBuf::from(path_str);
        for lang in &["python", "javascript", "typescript", "rust"] {
            if file_matches_language(&path, lang) {
                lang_to_files.entry(lang.to_string()).or_default().push(path.clone());
            }
        }
    }

    let mut stdout = std::io::stdout();
    if !execute_ast_scan_core(&orchestrator, &data, &backend, &lang_to_files, &mut stdout)? {
        std::process::exit(1);
    }
    Ok(())
}

pub fn execute_ast_scan_core(
    orchestrator: &AstWorkflowOrchestrator,
    data: &ProjectDataV6,
    backend: &AstBackend,
    lang_to_files: &HashMap<String, Vec<PathBuf>>,
    writer: &mut dyn Write
) -> Result<bool> {
    writeln!(writer, "Scanning project using adaptive AST routing based on {}...", orchestrator.config_path.display())?;

    if data.rule_specs.is_empty() {
        writeln!(writer, "Error: No valid rules found in configured rule directories.")?;
        return Ok(false);
    }
    
    let mut total_matches = 0;
    let mut matched_rules_count = 0;
    let mut backends_used = BTreeSet::new();

    let backend_hints = data.orchestration_hints.get("backend_hints")
        .and_then(|v| v.as_object());

    for rule in &data.rule_specs {
        let backend_name = backend_hints
            .and_then(|h| h.get(&rule.id))
            .and_then(|v| v.as_str())
            .unwrap_or("AstBackend");
        
        backends_used.insert(backend_name.to_string());

        let mut rule_matches_count = 0;
        let mut matched_files_count = 0;

        let file_matches = if backend_name == "AstBackend" {
            if let Some(files) = lang_to_files.get(&rule.language.to_lowercase()) {
                backend.search_many_for_cli(&rule.pattern, &rule.language, files)?
            } else {
                Vec::new()
            }
        } else {
            let root_dir_str = orchestrator.root_dir.to_string_lossy().into_owned();
            backend.search_for_cli(&rule.pattern, &rule.language, &root_dir_str)?
        };
        
        for file_match in file_matches {
            rule_matches_count += file_match.matches.len();
            if !file_match.matches.is_empty() {
                matched_files_count += 1;
            }
        }

        total_matches += rule_matches_count;
        if rule_matches_count > 0 {
            matched_rules_count += 1;
        }

        writeln!(
            writer,
            "[scan] rule={} lang={} matches={} files={}",
            rule.id, rule.language, rule_matches_count, matched_files_count
        )?;
    }

    let backends_str = backends_used.into_iter().collect::<Vec<_>>().join(",");
    writeln!(
        writer,
        "Scan completed. rules={} matched_rules={} total_matches={} backends={}",
        data.rule_specs.len(),
        matched_rules_count,
        total_matches,
        if backends_str.is_empty() { "none".to_string() } else { backends_str }
    )?;

    Ok(true)
}

fn file_matches_language(path: &Path, lang: &str) -> bool {
    let extension = path.extension().and_then(|ext| ext.to_str()).unwrap_or("").to_lowercase();
    match lang.to_lowercase().as_str() {
        "python" | "py" => matches!(extension.as_str(), "py" | "py3" | "pyi" | "pyw" | "bzl"),
        "javascript" | "js" => matches!(extension.as_str(), "js" | "jsx" | "cjs" | "mjs"),
        "typescript" | "ts" => matches!(extension.as_str(), "ts" | "tsx" | "cts" | "mts"),
        "rust" | "rs" => extension == "rs",
        _ => true,
    }
}

pub fn handle_ast_test(config_path: Option<&str>) -> Result<()> {
    let orchestrator = AstWorkflowOrchestrator::new(config_path)?;
    let data = orchestrator.load_project_data()?;
    let backend = AstBackend::new();
    let mut stdout = std::io::stdout();
    if !execute_ast_test_core(&orchestrator, &data, &backend, &mut stdout)? {
        std::process::exit(1);
    }
    Ok(())
}

pub fn execute_ast_test_core(
    orchestrator: &AstWorkflowOrchestrator,
    data: &ProjectDataV6,
    backend: &AstBackend,
    writer: &mut dyn Write
) -> Result<bool> {
    let mut total_cases = 0;
    let mut failures = Vec::new();
    let mut backends_used = BTreeSet::new();

    let backend_hints = data.orchestration_hints.get("backend_hints")
        .and_then(|v| v.as_object());

    let mut rule_case_groups: HashMap<(String, String), Vec<BatchTestSnippet>> = HashMap::new();

    for test_file_entry in &data.test_data {
        let test_file_path = test_file_entry.get("file").and_then(|v| v.as_str()).unwrap_or("test");
        let cases = test_file_entry.get("cases").and_then(|v| v.as_array());
        
        if let Some(cases) = cases {
            for case in cases {
                let case_id = case.get("id").and_then(|v| v.as_str()).unwrap_or("test");
                let linked_rule_id = case.get("ruleId").and_then(|v| v.as_str());
                
                let mut pattern = orchestrator.extract_rule_pattern_json(case);
                let mut language = case.get("language").and_then(|v| v.as_str())
                    .unwrap_or(&data.project_cfg["language"].as_str().unwrap_or("python"))
                    .to_string();

                if pattern.is_none() {
                    if let Some(rid) = linked_rule_id {
                        if let Some(rule) = data.rule_specs.iter().find(|r| r.id == rid) {
                            pattern = Some(rule.pattern.clone());
                            language = rule.language.clone();
                        }
                    }
                }

                let pattern = match pattern {
                    Some(p) => p,
                    None => {
                        failures.push(format!("{}:{}: missing pattern or ruleId", test_file_path, case_id));
                        continue;
                    }
                };

                let valid_snippets = match case.get("valid") {
                    Some(v) => orchestrator.normalize_string_list(Some(v)),
                    None => Vec::new(),
                };
                let invalid_snippets = match case.get("invalid") {
                    Some(v) => orchestrator.normalize_string_list(Some(v)),
                    None => Vec::new(),
                };

                if valid_snippets.is_empty() && invalid_snippets.is_empty() {
                    failures.push(format!("{}:{}: empty valid/invalid test lists", test_file_path, case_id));
                    continue;
                }

                total_cases += valid_snippets.len() + invalid_snippets.len();
                
                let group = rule_case_groups.entry((pattern.clone(), language.clone())).or_insert_with(Vec::new);
                let case_key = format!("{}:{}", test_file_path, case_id);

                for snip in valid_snippets {
                    group.push(BatchTestSnippet {
                        case_key: case_key.clone(),
                        snippet: snip,
                        expected_match: false,
                    });
                }
                for snip in invalid_snippets {
                    group.push(BatchTestSnippet {
                        case_key: case_key.clone(),
                        snippet: snip,
                        expected_match: true,
                    });
                }
            }
        }
    }

    if total_cases == 0 {
        writeln!(writer, "Error: No test cases found.")?;
        return Ok(false);
    }

    let session_temp = tempfile::Builder::new()
        .prefix(".tg_test_session_")
        .tempdir_in(&orchestrator.root_dir)?;

    for ((pattern, language), snippets) in rule_case_groups {
        let results = execute_batched_tests(backend, &session_temp, &pattern, &language, &snippets)?;
        for (snippet_info, has_match) in snippets.iter().zip(results) {
            if has_match != snippet_info.expected_match {
                let expectation = if snippet_info.expected_match { "match" } else { "no match" };
                let actual = if has_match { "match" } else { "no match" };
                failures.push(format!("{}: expected {}, got {} for snippet {:?}", snippet_info.case_key, expectation, actual, snippet_info.snippet));
            }
        }
        
        let backend_name = if let Some(hints) = backend_hints {
            data.rule_specs.iter()
                .find(|r| r.pattern == pattern)
                .and_then(|r| hints.get(&r.id))
                .and_then(|v| v.as_str())
                .unwrap_or("AstBackend")
        } else {
            "AstBackend"
        };
        backends_used.insert(backend_name.to_string());
    }

    let backends_str = backends_used.into_iter().collect::<Vec<_>>().join(",");
    writeln!(
        writer,
        "Testing AST rules using {} from {}...",
        if backends_str.is_empty() { "adaptive AST routing".to_string() } else { backends_str },
        orchestrator.config_path.display()
    )?;

    if !failures.is_empty() {
        for fail in &failures {
            writeln!(writer, "[test] FAIL {}", fail)?;
        }
        writeln!(writer, "Rule tests failed. cases={} failures={}", total_cases, failures.len())?;
        return Ok(false);
    }

    writeln!(writer, "All tests passed. cases={}", total_cases)?;
    Ok(true)
}

fn execute_batched_tests(
    backend: &AstBackend,
    temp_dir: &tempfile::TempDir,
    pattern: &str,
    language: &str,
    snippets: &[BatchTestSnippet],
) -> Result<Vec<bool>> {
    let suffix = match language.to_lowercase().as_str() {
        "python" | "py" => ".py",
        "javascript" | "js" => ".js",
        "typescript" | "ts" => ".ts",
        _ => ".py",
    };

    let mut snippet_paths = Vec::new();
    for (idx, snip) in snippets.iter().enumerate() {
        let path = temp_dir.path().join(format!("snip_{}{}", idx, suffix));
        fs::write(&path, &snip.snippet)?;
        snippet_paths.push(path);
    }

    let file_matches = backend.search_many_for_cli(pattern, language, &snippet_paths)?;
    let matched_paths: HashSet<String> = file_matches.into_iter()
        .filter(|m| !m.matches.is_empty())
        .map(|m| m.file.to_string_lossy().to_string())
        .collect();

    let mut results = Vec::new();
    for path in snippet_paths {
        results.push(matched_paths.contains(&path.to_string_lossy().to_string()));
    }

    Ok(results)
}

pub fn handle_ast_new(args: Vec<String>) -> Result<()> {
    if args.iter().any(|arg| arg == "--help" || arg == "-h") {
        println!("usage: tg new");
        println!("");
        println!("Create a new AST project configuration.");
        return Ok(());
    }

    let config_path = if !args.is_empty() && (args[0] == "--config" || args[0] == "-c") && args.len() > 1 {
        PathBuf::from(&args[1])
    } else {
        PathBuf::from("sgconfig.yml")
    };

    if config_path.exists() {
        anyhow::bail!("Config file {:?} already exists.", config_path);
    }

    fs::write(
        &config_path,
        "ruleDirs: [rules]\ntestDirs: [tests]\nlanguage: python\n",
    )?;

    let rules_dir = config_path.parent().unwrap_or(Path::new(".")).join("rules");
    fs::create_dir_all(&rules_dir)?;
    
    fs::write(
        rules_dir.join("sample-rule.yml"),
        "id: sample-rule\nlanguage: python\nrule:\n  pattern: 'print($$$ARGS)'\n",
    )?;

    let tests_dir = config_path.parent().unwrap_or(Path::new(".")).join("tests");
    fs::create_dir_all(&tests_dir)?;
    
    fs::write(
        tests_dir.join("sample-test.yml"),
        "id: sample-test\nruleId: sample-rule\nvalid:\n  - 'pass'\ninvalid:\n  - 'print(\"hello\")'\n",
    )?;

    println!("Initialized new structural search project in {:?}", config_path);
    Ok(())
}

impl AstWorkflowOrchestrator {
    pub fn new(config_path: Option<&str>) -> Result<Self> {
        let path = Path::new(config_path.unwrap_or("sgconfig.yml"));
        let resolved_config = if path.is_absolute() {
            path.to_path_buf()
        } else {
            std::env::current_dir()?.join(path)
        };

        if !resolved_config.exists() {
            anyhow::bail!("Config file {:?} not found. Use `tg new` to create one.", resolved_config);
        }
        
        let root_dir = resolved_config.parent()
            .context("Config file must have a parent directory")?
            .to_path_buf();

        Ok(Self {
            root_dir,
            config_path: resolved_config,
        })
    }

    pub fn load_config(&self) -> Result<AstProjectConfig> {
        let content = fs::read_to_string(&self.config_path)
            .context("Failed to read sgconfig.yml")?;
        let config: AstProjectConfig = serde_yaml::from_str(&content)
            .context("Failed to parse sgconfig.yml")?;
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
        let data: ProjectDataV6 = serde_json::from_str(&content)?;
        
        let cache_mtime = fs::metadata(&cache_file)?.modified()?;
        let config_mtime = fs::metadata(&self.config_path)?.modified()?;
        
        if config_mtime > cache_mtime {
            return Ok(None);
        }

        for (path_str, recorded_mtime_ns) in &data.validation_metadata.rule_files {
            let path = Path::new(path_str);
            if !path.exists() { return Ok(None); }
            let actual_mtime = fs::metadata(path)?.modified()?;
            let actual_ns = actual_mtime.duration_since(SystemTime::UNIX_EPOCH)?.as_nanos() as u64;
            if actual_ns > *recorded_mtime_ns {
                return Ok(None);
            }
        }

        for (path_str, recorded_mtime_ns) in &data.validation_metadata.test_files {
            let path = Path::new(path_str);
            if !path.exists() { return Ok(None); }
            let actual_mtime = fs::metadata(path)?.modified()?;
            let actual_ns = actual_mtime.duration_since(SystemTime::UNIX_EPOCH)?.as_nanos() as u64;
            if actual_ns > *recorded_mtime_ns {
                return Ok(None);
            }
        }

        for (path_str, recorded_mtime_ns) in &data.validation_metadata.tree_dirs {
            let path = Path::new(path_str);
            if !path.exists() { return Ok(None); }
            let actual_mtime = fs::metadata(path)?.modified()?;
            let actual_ns = actual_mtime.duration_since(SystemTime::UNIX_EPOCH)?.as_nanos() as u64;
            if actual_ns > *recorded_mtime_ns {
                return Ok(None);
            }
        }

        Ok(Some(data))
    }

    pub fn discover_rules(&self, config: &AstProjectConfig) -> Result<(Vec<AstRuleSpec>, HashMap<String, u64>)> {
        let mut specs = Vec::new();
        let mut meta = HashMap::new();

        for rule_dir_rel in &config.rule_dirs {
            let rule_dir = self.root_dir.join(rule_dir_rel);
            if !rule_dir.exists() { continue; }

            for entry in walkdir::WalkDir::new(rule_dir)
                .into_iter()
                .filter_map(|e| e.ok())
                .filter(|e| e.file_type().is_file())
            {
                let path = entry.path();
                let ext = path.extension().and_then(|s| s.to_str()).unwrap_or("");
                if ext != "yml" && ext != "yaml" { continue; }

                let mtime = entry.metadata()?.modified()?;
                let ns = mtime.duration_since(SystemTime::UNIX_EPOCH)?.as_nanos() as u64;
                meta.insert(path.to_string_lossy().to_string(), ns);

                let content = fs::read_to_string(path)?;
                let payload: serde_yaml::Value = serde_yaml::from_str(&content)?;

                if let Some(rules) = payload.get("rules").and_then(|v| v.as_sequence()) {
                    for (idx, item) in rules.iter().enumerate() {
                        if let Some(spec) = self.parse_rule_item(item, &payload, config, path, Some(idx)) {
                            specs.push(spec);
                        }
                    }
                } else if let Some(spec) = self.parse_rule_item(&payload, &payload, config, path, None) {
                    specs.push(spec);
                }
            }
        }

        Ok((specs, meta))
    }

    fn parse_rule_item(
        &self, 
        item: &serde_yaml::Value, 
        payload: &serde_yaml::Value,
        config: &AstProjectConfig,
        path: &Path,
        idx: Option<usize>
    ) -> Option<AstRuleSpec> {
        let pattern = self.extract_rule_pattern(item)?;
        let id = item.get("id")
            .and_then(|v| v.as_str())
            .map(|s| s.to_string())
            .unwrap_or_else(|| {
                let stem = path.file_stem().and_then(|s| s.to_str()).unwrap_or("rule");
                match idx {
                    Some(i) => format!("{}-{}", stem, i + 1),
                    None => stem.to_string(),
                }
            });
        
        let language = item.get("language")
            .and_then(|v| v.as_str())
            .or_else(|| payload.get("language").and_then(|v| v.as_str()))
            .map(|s| s.to_string())
            .unwrap_or_else(|| config.language.clone());

        Some(AstRuleSpec { id, pattern, language })
    }

    pub fn discover_files(&self, _config: &AstProjectConfig) -> Result<(Vec<String>, HashMap<String, u64>)> {
        use ignore::WalkBuilder;
        let mut files = Vec::new();
        let mut dir_meta = HashMap::new();

        let walker = WalkBuilder::new(&self.root_dir)
            .hidden(true)
            .git_ignore(true)
            .parents(true)
            .ignore(true)
            .filter_entry(|e| {
                e.file_name() != ".tg_cache"
            })
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

    pub fn discover_tests(&self, config: &AstProjectConfig) -> Result<(Vec<serde_json::Value>, HashMap<String, u64>)> {
        let mut test_data = Vec::new();
        let mut meta = HashMap::new();

        for test_dir_rel in &config.test_dirs {
            let test_dir = self.root_dir.join(test_dir_rel);
            if !test_dir.exists() { continue; }

            for entry in walkdir::WalkDir::new(test_dir)
                .into_iter()
                .filter_map(|e| e.ok())
                .filter(|e| e.file_type().is_file())
            {
                let path = entry.path();
                let ext = path.extension().and_then(|s| s.to_str()).unwrap_or("");
                if ext != "yml" && ext != "yaml" { continue; }

                let mtime = entry.metadata()?.modified()?;
                let ns = mtime.duration_since(SystemTime::UNIX_EPOCH)?.as_nanos() as u64;
                meta.insert(path.to_string_lossy().to_string(), ns);

                let content = fs::read_to_string(path)?;
                let payload: serde_yaml::Value = serde_yaml::from_str(&content)?;
                
                let raw_cases = payload.get("tests")
                    .and_then(|v| v.as_sequence())
                    .cloned()
                    .unwrap_or_else(|| {
                        if payload.is_mapping() {
                            vec![payload.clone()]
                        } else {
                            Vec::new()
                        }
                    });

                let cases: Vec<serde_json::Value> = raw_cases.iter()
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
        };

        self.save_cache(&data)?;
        Ok(data)
    }

    pub fn select_ast_backend_name_for_pattern(&self, pattern: &str) -> &str {
        let stripped = pattern.trim();
        if stripped.is_empty() { return "AstGrepWrapperBackend"; }

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

        if is_native { "AstBackend" } else { "AstGrepWrapperBackend" }
    }

    pub fn extract_rule_pattern(&self, item: &serde_yaml::Value) -> Option<String> {
        if let Some(p) = item.get("pattern").and_then(|v| v.as_str()) {
            return Some(p.trim().to_string());
        }
        if let Some(rule) = item.get("rule").and_then(|v| v.as_mapping()) {
            if let Some(p) = rule.get(&serde_yaml::Value::String("pattern".to_string())).and_then(|v| v.as_str()) {
                return Some(p.trim().to_string());
            }
        }
        None
    }

    pub fn extract_rule_pattern_json(&self, item: &serde_json::Value) -> Option<String> {
        if let Some(p) = item.get("pattern").and_then(|v| v.as_str()) {
            return Some(p.trim().to_string());
        }
        if let Some(rule) = item.get("rule").and_then(|v| v.as_object()) {
            if let Some(p) = rule.get("pattern").and_then(|v| v.as_str()) {
                return Some(p.trim().to_string());
            }
        }
        None
    }

    pub fn normalize_string_list(&self, val: Option<&serde_json::Value>) -> Vec<String> {
        match val {
            Some(serde_json::Value::String(s)) => vec![s.clone()],
            Some(serde_json::Value::Array(arr)) => {
                arr.iter().filter_map(|v| v.as_str().map(|s| s.to_string())).collect()
            }
            _ => Vec::new(),
        }
    }
}

pub fn handle_ast_worker_tcp(port: u16) -> Result<()> {
    use std::net::{TcpListener, TcpStream};
    
    let port_file = std::env::current_dir()?.join(".tg_cache").join("ast").join("worker_port.txt");
    
    if port_file.exists() {
        if let Ok(existing_port_str) = fs::read_to_string(&port_file) {
            if let Ok(existing_port) = existing_port_str.trim().parse::<u16>() {
                if TcpStream::connect(format!("127.0.0.1:{}", existing_port)).is_ok() {
                    anyhow::bail!("A resident AST worker is already running on port {} for this repository.", existing_port);
                }
            }
        }
    }

    let listener = TcpListener::bind(format!("127.0.0.1:{}", port))?;
    println!("Resident AST worker listening on 127.0.0.1:{}", port);

    if let Some(parent) = port_file.parent() {
        fs::create_dir_all(parent)?;
    }
    fs::write(&port_file, port.to_string())?;

    let mut worker = ResidentAstWorker::new();

    for stream in listener.incoming() {
        let mut stream = stream?;
        
        let mut de = serde_json::Deserializer::from_reader(&stream);
        let request: Result<SessionRequest, _> = SessionRequest::deserialize(&mut de);
        
        match request {
            Ok(SessionRequest::Scan { config_path }) => {
                if let Err(err) = worker.ensure_project(config_path.as_deref()) {
                    let resp = SessionResponse { success: false, error: Some(err.to_string()) };
                    let _ = serde_json::to_writer(&mut stream, &resp);
                    let _ = writeln!(&mut stream);
                } else {
                    let mut output = Vec::new();
                    let success = match execute_ast_scan_core(
                        worker.orchestrator.as_ref().unwrap(),
                        worker.data.as_ref().unwrap(),
                        &worker.backend,
                        &worker.lang_to_files,
                        &mut output
                    ) {
                        Ok(s) => s,
                        Err(err) => {
                            let _ = writeln!(&mut output, "Error: {}", err);
                            false
                        }
                    };

                    let resp = SessionResponse { success, error: None };
                    let _ = serde_json::to_writer(&mut stream, &resp);
                    let _ = writeln!(&mut stream);
                    let _ = stream.write_all(&output);
                }
            }
            Ok(SessionRequest::Test { config_path }) => {
                if let Err(err) = worker.ensure_project(config_path.as_deref()) {
                    let resp = SessionResponse { success: false, error: Some(err.to_string()) };
                    let _ = serde_json::to_writer(&mut stream, &resp);
                    let _ = writeln!(&mut stream);
                } else {
                    let mut output = Vec::new();
                    let success = match execute_ast_test_core(
                        worker.orchestrator.as_ref().unwrap(),
                        worker.data.as_ref().unwrap(),
                        &worker.backend,
                        &mut output
                    ) {
                        Ok(s) => s,
                        Err(err) => {
                            let _ = writeln!(&mut output, "Error: {}", err);
                            false
                        }
                    };

                    let resp = SessionResponse { success, error: None };
                    let _ = serde_json::to_writer(&mut stream, &resp);
                    let _ = writeln!(&mut stream);
                    let _ = stream.write_all(&output);
                }
            }
            Ok(SessionRequest::Defs { path, symbol, provider: _provider }) => {
                let p = PathBuf::from(path);
                if let Err(err) = worker.ensure_project(p.parent().and_then(|p| p.to_str())) {
                    let resp = SessionResponse { success: false, error: Some(err.to_string()) };
                    let _ = serde_json::to_writer(&mut stream, &resp);
                    let _ = writeln!(&mut stream);
                } else {
                    let resp = SessionResponse { success: true, error: None };
                    let _ = serde_json::to_writer(&mut stream, &resp);
                    let _ = writeln!(&mut stream);
                    
                    use crate::editor_plane::execute_defs_core;
                    if let Err(err) = execute_defs_core(
                        &p,
                        &symbol,
                        worker.data.as_ref().unwrap(),
                        &worker.backend,
                        true,
                        &mut stream
                    ) {
                        let _ = writeln!(&mut stream, "Error: {}", err);
                    }
                }
            }
            Ok(SessionRequest::Refs { path, symbol, provider: _provider }) => {
                let p = PathBuf::from(path);
                if let Err(err) = worker.ensure_project(p.parent().and_then(|p| p.to_str())) {
                    let resp = SessionResponse { success: false, error: Some(err.to_string()) };
                    let _ = serde_json::to_writer(&mut stream, &resp);
                    let _ = writeln!(&mut stream);
                } else {
                    let resp = SessionResponse { success: true, error: None };
                    let _ = serde_json::to_writer(&mut stream, &resp);
                    let _ = writeln!(&mut stream);
                    
                    use crate::editor_plane::execute_refs_core;
                    if let Err(err) = execute_refs_core(
                        &p,
                        &symbol,
                        worker.data.as_ref().unwrap(),
                        &worker.backend,
                        true,
                        &mut stream
                    ) {
                        let _ = writeln!(&mut stream, "Error: {}", err);
                    }
                }
            }
            Ok(SessionRequest::Context { path, query }) => {
                let p = PathBuf::from(path);
                if let Err(err) = worker.ensure_project(p.parent().and_then(|p| p.to_str())) {
                    let resp = SessionResponse { success: false, error: Some(err.to_string()) };
                    let _ = serde_json::to_writer(&mut stream, &resp);
                    let _ = writeln!(&mut stream);
                } else {
                    let resp = SessionResponse { success: true, error: None };
                    let _ = serde_json::to_writer(&mut stream, &resp);
                    let _ = writeln!(&mut stream);
                    
                    use crate::editor_plane::execute_context_core;
                    if let Err(err) = execute_context_core(
                        &p,
                        &query,
                        worker.data.as_ref().unwrap(),
                        &worker.backend,
                        true,
                        &mut stream
                    ) {
                        let _ = writeln!(&mut stream, "Error: {}", err);
                    }
                }
            }
            Ok(SessionRequest::Stop) => {
                let resp = SessionResponse { success: true, error: None };
                let _ = serde_json::to_writer(&mut stream, &resp);
                let _ = writeln!(&mut stream);
                let _ = writeln!(&mut stream, "Stopping");
                let _ = fs::remove_file(&port_file);
                let _ = stream.flush();
                break;
            }
            Err(err) => {
                let resp = SessionResponse { success: false, error: Some(err.to_string()) };
                let _ = serde_json::to_writer(&mut stream, &resp);
                let _ = writeln!(&mut stream);
            }
        }
        let _ = stream.flush();
    }

    Ok(())
}
