use crate::backend_ast::{AstBackend, AstCliFileMatches};
use anyhow::{Context, Result};
use serde::{Deserialize, Serialize};
use std::collections::{BTreeSet, HashMap, HashSet};
use std::fs;
use std::io::Write;
use std::path::{Path, PathBuf};
use std::time::SystemTime;

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

/// Schema discriminator for the persisted project cache
/// (`.tg_cache/ast/project_data_v6.json`). Bumped to 2 by M16: a legacy cache
/// carries `AstRuleSpec` records WITHOUT `patterns`/`severity`/`message`
/// (composites were dropped at discovery and metadata never survived), so an
/// mtime-fresh legacy cache would keep serving rule truths that disagree with
/// the source YAML. Any cache whose discriminator is absent/old is REBUILT from
/// source (`load_cache` returns None -> `load_project_data` rediscovers),
/// following the index.rs INDEX_FORMAT_VERSION bump precedent (format 4 -> 5).
const PROJECT_DATA_V6_SCHEMA_VERSION: u32 = 2;

/// Version of caches written before the M16 schema bump (or by anything that
/// omits the field). Deliberately NOT the current version, so a legacy cache
/// fails the read-time version gate instead of silently serving stale rules.
fn default_project_data_schema_version() -> u32 {
    1
}

#[derive(Debug, Serialize, Deserialize, Clone)]
pub struct AstRuleSpec {
    pub id: String,
    /// Primary member pattern. For a composite (multi-pattern `any`-of) rule
    /// this is the FIRST member; for a single-pattern rule it is the only one.
    pub pattern: String,
    /// Additional member patterns for composite (multi-pattern `any`-of) rules.
    /// Empty for single-pattern rules. Pre-M16 caches serialize without this
    /// field, hence `#[serde(default)]`.
    #[serde(default)]
    pub patterns: Vec<String>,
    /// Finding severity, mirrored from the Python project-scan twin
    /// (`cli/ast_workflows.py:_load_rule_specs_and_meta`): item -> payload ->
    /// "warning".
    #[serde(default = "default_rule_severity")]
    pub severity: String,
    /// Finding message (item -> payload -> "").
    #[serde(default)]
    pub message: String,
    pub language: String,
}

fn default_rule_severity() -> String {
    "warning".to_string()
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
    /// Cache schema discriminator; see `PROJECT_DATA_V6_SCHEMA_VERSION`.
    #[serde(default = "default_project_data_schema_version")]
    pub cache_schema_version: u32,
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
    Context { path: String, query: String },
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

impl Default for ResidentAstWorker {
    fn default() -> Self {
        Self::new()
    }
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
                    self.lang_to_files
                        .entry(lang.to_string())
                        .or_default()
                        .push(path.clone());
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
                lang_to_files
                    .entry(lang.to_string())
                    .or_default()
                    .push(path.clone());
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
    writer: &mut dyn Write,
) -> Result<bool> {
    writeln!(
        writer,
        "Scanning project using adaptive AST routing based on {}...",
        orchestrator.config_path.display()
    )?;

    if data.rule_specs.is_empty() {
        writeln!(
            writer,
            "Error: No valid rules found in configured rule directories."
        )?;
        return Ok(false);
    }

    let mut total_matches = 0;
    let mut matched_rules_count = 0;
    let mut backends_used = BTreeSet::new();

    let backend_hints = data
        .orchestration_hints
        .get("backend_hints")
        .and_then(|v| v.as_object());

    for rule in &data.rule_specs {
        let backend_name = backend_hints
            .and_then(|h| h.get(&rule.id))
            .and_then(|v| v.as_str())
            .unwrap_or("AstBackend");

        backends_used.insert(backend_name.to_string());

        // M16 F1: a composite (multi-pattern any-of) rule matches when ANY member
        // matches, and the union is deduplicated by AST NODE SPAN
        // (file, start_byte, end_byte) — the same node matched by several
        // members counts once, but two distinct nodes on the SAME line each
        // count, matching whole-config ast-grep's per-node `any` semantics
        // (measured: `alpha(1); alpha(2)` with members `alpha` + `alpha(1)` =
        // 2 identifier nodes + 1 call node = 3). Files are DISTINCT.
        // Single-pattern rules keep the pre-M16 per-node count exactly
        // (legacy output parity; nothing was dropped there, so nothing changes).
        let members = ast_rule_member_patterns(rule);
        let composite = members.len() > 1;
        let mut per_file_counts: HashMap<PathBuf, usize> = HashMap::new();
        let mut per_file_spans: HashMap<PathBuf, HashSet<(usize, usize)>> = HashMap::new();

        for member_pattern in members {
            let file_matches = if backend_name == "AstBackend" {
                if let Some(files) = lang_to_files.get(&rule.language.to_lowercase()) {
                    backend.search_many_for_cli(&member_pattern, &rule.language, files)?
                } else {
                    Vec::new()
                }
            } else {
                let root_dir_str = orchestrator.root_dir.to_string_lossy().into_owned();
                backend.search_for_cli(&member_pattern, &rule.language, &root_dir_str)?
            };

            for file_match in file_matches {
                let AstCliFileMatches { file, matches } = file_match;
                if composite {
                    per_file_spans
                        .entry(file)
                        .or_default()
                        .extend(matches.into_iter().map(|m| (m.start_byte, m.end_byte)));
                } else {
                    *per_file_counts.entry(file).or_insert(0) += matches.len();
                }
            }
        }

        let (rule_matches_count, matched_files_count) = if composite {
            let matches: usize = per_file_spans.values().map(|spans| spans.len()).sum();
            (matches, per_file_spans.len())
        } else {
            (per_file_counts.values().sum(), per_file_counts.len())
        };

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
        if backends_str.is_empty() {
            "none".to_string()
        } else {
            backends_str
        }
    )?;

    Ok(true)
}

fn file_matches_language(path: &Path, lang: &str) -> bool {
    let extension = path
        .extension()
        .and_then(|ext| ext.to_str())
        .unwrap_or("")
        .to_lowercase();
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
    writer: &mut dyn Write,
) -> Result<bool> {
    let mut total_cases = 0;
    let mut failures = Vec::new();
    let mut backends_used = BTreeSet::new();

    let backend_hints = data
        .orchestration_hints
        .get("backend_hints")
        .and_then(|v| v.as_object());

    let mut rule_case_groups: HashMap<(String, String), Vec<BatchTestSnippet>> = HashMap::new();

    for test_file_entry in &data.test_data {
        let test_file_path = test_file_entry
            .get("file")
            .and_then(|v| v.as_str())
            .unwrap_or("test");
        let cases = test_file_entry.get("cases").and_then(|v| v.as_array());

        if let Some(cases) = cases {
            for case in cases {
                let case_id = case.get("id").and_then(|v| v.as_str()).unwrap_or("test");
                let linked_rule_id = case.get("ruleId").and_then(|v| v.as_str());

                let mut pattern = orchestrator.extract_rule_pattern_json(case);
                let mut language = case
                    .get("language")
                    .and_then(|v| v.as_str())
                    .unwrap_or(data.project_cfg["language"].as_str().unwrap_or("python"))
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
                        failures.push(format!(
                            "{}:{}: missing pattern or ruleId",
                            test_file_path, case_id
                        ));
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
                    failures.push(format!(
                        "{}:{}: empty valid/invalid test lists",
                        test_file_path, case_id
                    ));
                    continue;
                }

                total_cases += valid_snippets.len() + invalid_snippets.len();

                let group = rule_case_groups
                    .entry((pattern.clone(), language.clone()))
                    .or_default();
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
        let results =
            execute_batched_tests(backend, &session_temp, &pattern, &language, &snippets)?;
        for (snippet_info, has_match) in snippets.iter().zip(results) {
            if has_match != snippet_info.expected_match {
                let expectation = if snippet_info.expected_match {
                    "match"
                } else {
                    "no match"
                };
                let actual = if has_match { "match" } else { "no match" };
                failures.push(format!(
                    "{}: expected {}, got {} for snippet {:?}",
                    snippet_info.case_key, expectation, actual, snippet_info.snippet
                ));
            }
        }

        let backend_name = if let Some(hints) = backend_hints {
            data.rule_specs
                .iter()
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
        if backends_str.is_empty() {
            "adaptive AST routing".to_string()
        } else {
            backends_str
        },
        orchestrator.config_path.display()
    )?;

    if !failures.is_empty() {
        for fail in &failures {
            writeln!(writer, "[test] FAIL {}", fail)?;
        }
        writeln!(
            writer,
            "Rule tests failed. cases={} failures={}",
            total_cases,
            failures.len()
        )?;
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
    let matched_paths: HashSet<String> = file_matches
        .into_iter()
        .filter(|m| !m.matches.is_empty())
        .map(|m| m.file.to_string_lossy().to_string())
        .collect();

    let mut results = Vec::new();
    for path in snippet_paths {
        results.push(matched_paths.contains(&path.to_string_lossy().to_string()));
    }

    Ok(results)
}

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
enum AstNewKind {
    Project,
    Rule,
    Test,
    Util,
}

#[derive(Debug, Clone)]
struct AstNewRequest {
    kind: AstNewKind,
    name: Option<String>,
    lang: String,
    base_dir: PathBuf,
}

fn validate_ast_new_name(name: &str) -> Result<()> {
    if name.trim().is_empty()
        || name.contains('/')
        || name.contains('\\')
        || name == "."
        || name == ".."
    {
        anyhow::bail!("Invalid item name {name:?}; use a bare scaffold identifier.");
    }
    Ok(())
}

fn parse_ast_new_args(args: &[String]) -> Result<AstNewRequest> {
    let mut kind = AstNewKind::Project;
    let mut kind_seen = false;
    let mut name: Option<String> = None;
    let mut lang = "python".to_string();
    let mut base_dir = PathBuf::from(".");
    let mut index = 0usize;

    while index < args.len() {
        match args[index].as_str() {
            "project" if !kind_seen => {
                kind = AstNewKind::Project;
                kind_seen = true;
            }
            "rule" if !kind_seen => {
                kind = AstNewKind::Rule;
                kind_seen = true;
            }
            "test" if !kind_seen => {
                kind = AstNewKind::Test;
                kind_seen = true;
            }
            "util" if !kind_seen => {
                kind = AstNewKind::Util;
                kind_seen = true;
            }
            "--lang" | "-l" => {
                index += 1;
                lang = args
                    .get(index)
                    .cloned()
                    .context("--lang requires a language value")?;
            }
            value if value.starts_with("--lang=") => {
                lang = value
                    .split_once('=')
                    .map(|(_, value)| value.to_string())
                    .context("--lang requires a language value")?;
            }
            "--base-dir" | "-b" => {
                index += 1;
                base_dir = PathBuf::from(
                    args.get(index)
                        .context("--base-dir requires a directory value")?,
                );
            }
            value if value.starts_with("--base-dir=") => {
                base_dir = PathBuf::from(
                    value
                        .split_once('=')
                        .map(|(_, value)| value)
                        .context("--base-dir requires a directory value")?,
                );
            }
            "--yes" | "-y" => {}
            value if value.starts_with('-') => {
                anyhow::bail!("Unsupported tg new option {value:?}");
            }
            value => {
                if name.is_some() {
                    anyhow::bail!("Unexpected extra tg new argument {value:?}");
                }
                validate_ast_new_name(value)?;
                name = Some(value.to_string());
            }
        }
        index += 1;
    }

    if kind != AstNewKind::Project && name.is_none() {
        anyhow::bail!("tg new {:?} requires a name", kind);
    }
    Ok(AstNewRequest {
        kind,
        name,
        lang,
        base_dir,
    })
}

fn write_ast_project_scaffold(base_dir: &Path, lang: &str) -> Result<()> {
    let config_path = base_dir.join("sgconfig.yml");
    if config_path.exists() {
        anyhow::bail!("Config file {:?} already exists.", config_path);
    }
    fs::create_dir_all(base_dir)?;
    fs::write(
        &config_path,
        format!("ruleDirs: [rules]\ntestDirs: [tests]\nlanguage: {lang}\n"),
    )?;

    let rules_dir = base_dir.join("rules");
    fs::create_dir_all(&rules_dir)?;
    fs::write(
        rules_dir.join("sample-rule.yml"),
        format!("id: sample-rule\nlanguage: {lang}\nrule:\n  pattern: 'print($$$ARGS)'\n"),
    )?;

    let tests_dir = base_dir.join("tests");
    fs::create_dir_all(&tests_dir)?;
    fs::write(
        tests_dir.join("sample-test.yml"),
        "id: sample-test\nruleId: sample-rule\nvalid:\n  - 'pass'\ninvalid:\n  - 'print(\"hello\")'\n",
    )?;

    println!(
        "Initialized new structural search project in {:?}",
        config_path
    );
    Ok(())
}

pub fn handle_ast_new(args: Vec<String>) -> Result<()> {
    if args.iter().any(|arg| arg == "--help" || arg == "-h") {
        println!(
            "usage: tg new [project|rule|test|util] [NAME] [--lang LANG] [--base-dir DIR] [--yes]"
        );
        println!();
        println!("Create a new AST project configuration or named project/rule/test/util item.");
        return Ok(());
    }

    let request = parse_ast_new_args(&args)?;
    match request.kind {
        AstNewKind::Project => {
            let project_dir = match request.name.as_deref() {
                Some(name) => request.base_dir.join(name),
                None => request.base_dir,
            };
            write_ast_project_scaffold(&project_dir, &request.lang)
        }
        AstNewKind::Rule => {
            let name = request.name.as_deref().expect("validated rule name");
            let rules_dir = request.base_dir.join("rules");
            fs::create_dir_all(&rules_dir)?;
            let path = rules_dir.join(format!("{name}.yml"));
            if path.exists() {
                anyhow::bail!("Rule file {:?} already exists.", path);
            }
            fs::write(
                &path,
                format!(
                    "id: {name}\nlanguage: {}\nrule:\n  pattern: ''\n",
                    request.lang
                ),
            )?;
            println!("Created rule scaffold in {:?}", path);
            Ok(())
        }
        AstNewKind::Test => {
            let name = request.name.as_deref().expect("validated test name");
            let tests_dir = request.base_dir.join("tests");
            fs::create_dir_all(&tests_dir)?;
            let path = tests_dir.join(format!("{name}.yml"));
            if path.exists() {
                anyhow::bail!("Test file {:?} already exists.", path);
            }
            fs::write(
                &path,
                format!("id: {name}\nruleId: {name}\nvalid:\n  - ''\ninvalid: []\n"),
            )?;
            println!("Created test scaffold in {:?}", path);
            Ok(())
        }
        AstNewKind::Util => {
            let name = request.name.as_deref().expect("validated util name");
            let utils_dir = request.base_dir.join("utils");
            fs::create_dir_all(&utils_dir)?;
            let path = utils_dir.join(format!("{name}.yml"));
            if path.exists() {
                anyhow::bail!("Util file {:?} already exists.", path);
            }
            fs::write(&path, format!("id: {name}\npattern: ''\n"))?;
            println!("Created util scaffold in {:?}", path);
            Ok(())
        }
    }
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
        let config: AstProjectConfig =
            serde_yaml::from_str(&content).context("Failed to parse sgconfig.yml")?;
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
                let payload: serde_yaml::Value = serde_yaml::from_str(&content)?;

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

    fn parse_rule_item(
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
            {
                let trimmed = s.trim();
                if !trimmed.is_empty() {
                    return Some(vec![trimmed.to_string()]);
                }
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
fn ast_rule_member_patterns(rule: &AstRuleSpec) -> Vec<String> {
    if rule.patterns.is_empty() {
        vec![rule.pattern.clone()]
    } else {
        rule.patterns.clone()
    }
}

pub fn handle_ast_worker_tcp(port: u16) -> Result<()> {
    use std::net::{TcpListener, TcpStream};

    let port_file = std::env::current_dir()?
        .join(".tg_cache")
        .join("ast")
        .join("worker_port.txt");

    if port_file.exists() {
        if let Ok(existing_port_str) = fs::read_to_string(&port_file) {
            if let Ok(existing_port) = existing_port_str.trim().parse::<u16>() {
                if TcpStream::connect(format!("127.0.0.1:{}", existing_port)).is_ok() {
                    anyhow::bail!(
                        "A resident AST worker is already running on port {} for this repository.",
                        existing_port
                    );
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
                    let resp = SessionResponse {
                        success: false,
                        error: Some(err.to_string()),
                    };
                    let _ = serde_json::to_writer(&mut stream, &resp);
                    let _ = writeln!(&mut stream);
                } else {
                    let mut output = Vec::new();
                    let success = match execute_ast_scan_core(
                        worker.orchestrator.as_ref().unwrap(),
                        worker.data.as_ref().unwrap(),
                        &worker.backend,
                        &worker.lang_to_files,
                        &mut output,
                    ) {
                        Ok(s) => s,
                        Err(err) => {
                            let _ = writeln!(&mut output, "Error: {}", err);
                            false
                        }
                    };

                    let resp = SessionResponse {
                        success,
                        error: None,
                    };
                    let _ = serde_json::to_writer(&mut stream, &resp);
                    let _ = writeln!(&mut stream);
                    let _ = stream.write_all(&output);
                }
            }
            Ok(SessionRequest::Test { config_path }) => {
                if let Err(err) = worker.ensure_project(config_path.as_deref()) {
                    let resp = SessionResponse {
                        success: false,
                        error: Some(err.to_string()),
                    };
                    let _ = serde_json::to_writer(&mut stream, &resp);
                    let _ = writeln!(&mut stream);
                } else {
                    let mut output = Vec::new();
                    let success = match execute_ast_test_core(
                        worker.orchestrator.as_ref().unwrap(),
                        worker.data.as_ref().unwrap(),
                        &worker.backend,
                        &mut output,
                    ) {
                        Ok(s) => s,
                        Err(err) => {
                            let _ = writeln!(&mut output, "Error: {}", err);
                            false
                        }
                    };

                    let resp = SessionResponse {
                        success,
                        error: None,
                    };
                    let _ = serde_json::to_writer(&mut stream, &resp);
                    let _ = writeln!(&mut stream);
                    let _ = stream.write_all(&output);
                }
            }
            Ok(SessionRequest::Defs {
                path,
                symbol,
                provider: _provider,
            }) => {
                let p = PathBuf::from(path);
                if let Err(err) = worker.ensure_project(p.parent().and_then(|p| p.to_str())) {
                    let resp = SessionResponse {
                        success: false,
                        error: Some(err.to_string()),
                    };
                    let _ = serde_json::to_writer(&mut stream, &resp);
                    let _ = writeln!(&mut stream);
                } else {
                    let resp = SessionResponse {
                        success: true,
                        error: None,
                    };
                    let _ = serde_json::to_writer(&mut stream, &resp);
                    let _ = writeln!(&mut stream);

                    use crate::editor_plane::execute_defs_core;
                    if let Err(err) = execute_defs_core(
                        &p,
                        &symbol,
                        worker.data.as_ref().unwrap(),
                        &worker.backend,
                        true,
                        &mut stream,
                    ) {
                        let _ = writeln!(&mut stream, "Error: {}", err);
                    }
                }
            }
            Ok(SessionRequest::Refs {
                path,
                symbol,
                provider: _provider,
            }) => {
                let p = PathBuf::from(path);
                if let Err(err) = worker.ensure_project(p.parent().and_then(|p| p.to_str())) {
                    let resp = SessionResponse {
                        success: false,
                        error: Some(err.to_string()),
                    };
                    let _ = serde_json::to_writer(&mut stream, &resp);
                    let _ = writeln!(&mut stream);
                } else {
                    let resp = SessionResponse {
                        success: true,
                        error: None,
                    };
                    let _ = serde_json::to_writer(&mut stream, &resp);
                    let _ = writeln!(&mut stream);

                    use crate::editor_plane::execute_refs_core;
                    if let Err(err) = execute_refs_core(
                        &p,
                        &symbol,
                        worker.data.as_ref().unwrap(),
                        &worker.backend,
                        true,
                        &mut stream,
                    ) {
                        let _ = writeln!(&mut stream, "Error: {}", err);
                    }
                }
            }
            Ok(SessionRequest::Context { path, query }) => {
                let p = PathBuf::from(path);
                if let Err(err) = worker.ensure_project(p.parent().and_then(|p| p.to_str())) {
                    let resp = SessionResponse {
                        success: false,
                        error: Some(err.to_string()),
                    };
                    let _ = serde_json::to_writer(&mut stream, &resp);
                    let _ = writeln!(&mut stream);
                } else {
                    let resp = SessionResponse {
                        success: true,
                        error: None,
                    };
                    let _ = serde_json::to_writer(&mut stream, &resp);
                    let _ = writeln!(&mut stream);

                    use crate::editor_plane::execute_context_core;
                    if let Err(err) = execute_context_core(
                        &p,
                        &query,
                        worker.data.as_ref().unwrap(),
                        &worker.backend,
                        true,
                        &mut stream,
                    ) {
                        let _ = writeln!(&mut stream, "Error: {}", err);
                    }
                }
            }
            Ok(SessionRequest::Stop) => {
                let resp = SessionResponse {
                    success: true,
                    error: None,
                };
                let _ = serde_json::to_writer(&mut stream, &resp);
                let _ = writeln!(&mut stream);
                let _ = writeln!(&mut stream, "Stopping");
                let _ = fs::remove_file(&port_file);
                let _ = stream.flush();
                break;
            }
            Err(err) => {
                let resp = SessionResponse {
                    success: false,
                    error: Some(err.to_string()),
                };
                let _ = serde_json::to_writer(&mut stream, &resp);
                let _ = writeln!(&mut stream);
            }
        }
        let _ = stream.flush();
    }

    Ok(())
}

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
