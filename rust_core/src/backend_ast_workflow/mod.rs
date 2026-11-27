use crate::backend_ast::{AstBackend, AstCliFileMatches};
use anyhow::{Context, Result};
use serde::{Deserialize, Serialize};
use std::collections::{BTreeSet, HashMap, HashSet};
use std::fs;
use std::io::Write;
use std::path::{Path, PathBuf};
use std::time::SystemTime;

#[path = "../backend_ast_workflow_new_args.rs"]
mod new_args;
#[path = "../backend_ast_workflow_scaffold.rs"]
mod scaffold;
#[path = "../backend_ast_workflow_rules.rs"]
mod workflow_rules;

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
#[serde(deny_unknown_fields)]
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
    let empty_rule_ids = workflow_rules::empty_rule_ids(data);

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

                if pattern.is_none() {
                    if let Some(rid) = linked_rule_id.filter(|rid| empty_rule_ids.contains(*rid)) {
                        failures.push(format!(
                            "{}:{}: rule {} has an empty pattern",
                            test_file_path, case_id, rid
                        ));
                        continue;
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

    if total_cases == 0 && failures.is_empty() {
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
    if !matches!(language, "python" | "javascript" | "typescript" | "rust") {
        let sources = snippets
            .iter()
            .map(|snippet| snippet.snippet.clone())
            .collect::<Vec<_>>();
        return scaffold::match_test_snippets(pattern, language, &sources);
    }
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
    lang: Option<String>,
    base_dir: PathBuf,
}

fn write_ast_project_scaffold(base_dir: &Path, lang: &str) -> Result<()> {
    let config_path = base_dir.join("sgconfig.yml");
    fs::create_dir_all(base_dir)?;
    scaffold::write_new_file(
        &config_path,
        format!("ruleDirs: [rules]\ntestDirs: [tests]\nlanguage: {lang}\n").as_bytes(),
        "Config file",
    )?;

    let rules_dir = base_dir.join("rules");
    fs::create_dir_all(&rules_dir)?;
    scaffold::write_new_file(
        &rules_dir.join("sample-rule.yml"),
        format!("id: \"sample-rule\"\nlanguage: {lang}\nrule:\n  pattern: 'print($$$ARGS)'\n")
            .as_bytes(),
        "Rule file",
    )?;

    let tests_dir = base_dir.join("tests");
    fs::create_dir_all(&tests_dir)?;
    scaffold::write_new_file(
        &tests_dir.join("sample-test.yml"),
        b"id: \"sample-test\"\nruleId: \"sample-rule\"\nvalid:\n  - 'pass'\ninvalid:\n  - 'print(\"hello\")'\n",
        "Test file",
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

    let request = new_args::parse_ast_new_args(&args)?;
    match request.kind {
        AstNewKind::Project => {
            let project_dir = match request.name.as_deref() {
                Some(name) => request.base_dir.join(name),
                None => request.base_dir,
            };
            write_ast_project_scaffold(&project_dir, request.lang.as_deref().unwrap_or("python"))
        }
        AstNewKind::Rule => {
            let name = request.name.as_deref().expect("validated rule name");
            scaffold::write_rule(
                &request.base_dir,
                name,
                request.lang.as_deref().unwrap_or("python"),
            )
        }
        AstNewKind::Test => {
            let name = request.name.as_deref().expect("validated test name");
            scaffold::write_test(&request.base_dir, name, request.lang.as_deref())
        }
        AstNewKind::Util => {
            let name = request.name.as_deref().expect("validated util name");
            let utils_dir = request.base_dir.join("utils");
            fs::create_dir_all(&utils_dir)?;
            let path = utils_dir.join(format!("{name}.yml"));
            scaffold::write_new_file(
                &path,
                format!("id: {}\npattern: ''\n", serde_json::to_string(name)?).as_bytes(),
                "Util file",
            )?;
            println!("Created util scaffold in {:?}", path);
            Ok(())
        }
    }
}

mod orchestrator;
use orchestrator::ast_rule_member_patterns;

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
mod tests;
