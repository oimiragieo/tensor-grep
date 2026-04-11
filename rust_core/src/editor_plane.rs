use anyhow::Result;
use serde::{Deserialize, Serialize};
use std::path::{Path, PathBuf};
use crate::backend_ast_workflow::{AstWorkflowOrchestrator, ProjectDataV6};
use crate::backend_ast::{AstBackend, resolve_language};
use std::collections::HashSet;
use ast_grep_core::tree_sitter::LanguageExt;
use std::fs;
use std::io::Write;

#[derive(Debug, Serialize, Deserialize, Clone)]
pub struct SymbolDefinition {
    pub name: String,
    pub kind: String,
    pub file: PathBuf,
    pub line: usize,
    pub end_line: usize,
}

#[derive(Debug, Serialize, Deserialize, Clone)]
pub struct SymbolReference {
    pub name: String,
    pub kind: String,
    pub file: PathBuf,
    pub line: usize,
    pub text: String,
}

#[derive(Debug, Serialize, Deserialize)]
pub struct DefsResponse {
    pub symbol: String,
    pub path: PathBuf,
    pub definitions: Vec<SymbolDefinition>,
    pub files: Vec<PathBuf>,
    pub related_paths: Vec<PathBuf>,
    pub graph_completeness: String,
}

#[derive(Debug, Serialize, Deserialize)]
pub struct RefsResponse {
    pub symbol: String,
    pub path: PathBuf,
    pub references: Vec<SymbolReference>,
    pub files: Vec<PathBuf>,
    pub related_paths: Vec<PathBuf>,
}

#[derive(Debug, Serialize, Deserialize)]
pub struct ContextResponse {
    pub query: String,
    pub path: PathBuf,
    pub definitions: Vec<SymbolDefinition>,
    pub references: Vec<SymbolReference>,
    pub symbols: Vec<SymbolDefinition>,
    pub files: Vec<PathBuf>,
    pub related_paths: Vec<PathBuf>,
}

pub fn handle_defs(path: PathBuf, symbol: String, _provider: String, json: bool) -> Result<()> {
    let config_path = find_config_file(&path)?;
    let orchestrator = AstWorkflowOrchestrator::new(config_path.as_ref().and_then(|p| p.to_str()))?;
    let data = orchestrator.load_project_data()?;
    let backend = AstBackend::new();
    
    let mut stdout = std::io::stdout();
    execute_defs_core(&path, &symbol, &data, &backend, json, &mut stdout)
}

pub fn execute_defs_core(
    path: &Path,
    symbol: &str,
    data: &ProjectDataV6,
    backend: &AstBackend,
    json: bool,
    writer: &mut dyn Write
) -> Result<()> {
    let definitions = find_definitions(backend, data, symbol)?;
    
    let mut definition_files = HashSet::new();
    for d in &definitions {
        definition_files.insert(d.file.clone());
    }
    
    let mut files: Vec<PathBuf> = definition_files.into_iter().collect();
    files.sort();

    let response = DefsResponse {
        symbol: symbol.to_string(),
        path: path.to_path_buf(),
        definitions,
        files: files.clone(),
        related_paths: files,
        graph_completeness: "strong".to_string(),
    };
    
    if json {
        writeln!(writer, "{}", serde_json::to_string_pretty(&response)?)?;
    } else {
        writeln!(writer, "Definitions for {} in {:?}", symbol, path)?;
        for d in &response.definitions {
            writeln!(writer, "{}:{}: {}", d.file.display(), d.line, d.name)?;
        }
        writeln!(writer, "definitions={} files={}", response.definitions.len(), response.files.len())?;
    }
    
    Ok(())
}

pub fn handle_refs(path: PathBuf, symbol: String, _provider: String, json: bool) -> Result<()> {
    let config_path = find_config_file(&path)?;
    let orchestrator = AstWorkflowOrchestrator::new(config_path.as_ref().and_then(|p| p.to_str()))?;
    let data = orchestrator.load_project_data()?;
    let backend = AstBackend::new();
    
    let mut stdout = std::io::stdout();
    execute_refs_core(&path, &symbol, &data, &backend, json, &mut stdout)
}

pub fn execute_refs_core(
    path: &Path,
    symbol: &str,
    data: &ProjectDataV6,
    backend: &AstBackend,
    json: bool,
    writer: &mut dyn Write
) -> Result<()> {
    let references = find_references(backend, data, symbol)?;
    
    let mut reference_files = HashSet::new();
    for r in &references {
        reference_files.insert(r.file.clone());
    }
    
    let mut files: Vec<PathBuf> = reference_files.into_iter().collect();
    files.sort();

    let response = RefsResponse {
        symbol: symbol.to_string(),
        path: path.to_path_buf(),
        references,
        files: files.clone(),
        related_paths: files,
    };
    
    if json {
        writeln!(writer, "{}", serde_json::to_string_pretty(&response)?)?;
    } else {
        writeln!(writer, "References for {} in {:?}", symbol, path)?;
        for r in &response.references {
            writeln!(writer, "{}:{}: {}", r.file.display(), r.line, r.text.trim())?;
        }
        writeln!(writer, "references={} files={}", response.references.len(), response.files.len())?;
    }
    
    Ok(())
}

pub fn handle_context(path: PathBuf, query: String, json: bool) -> Result<()> {
    let config_path = find_config_file(&path)?;
    let orchestrator = AstWorkflowOrchestrator::new(config_path.as_ref().and_then(|p| p.to_str()))?;
    let data = orchestrator.load_project_data()?;
    let backend = AstBackend::new();
    
    let mut stdout = std::io::stdout();
    execute_context_core(&path, &query, &data, &backend, json, &mut stdout)
}

pub fn execute_context_core(
    path: &Path,
    query: &str,
    data: &ProjectDataV6,
    backend: &AstBackend,
    json: bool,
    writer: &mut dyn Write
) -> Result<()> {
    let definitions = find_definitions(backend, data, query)?;
    let references = find_references(backend, data, query)?;
    
    let mut all_files = HashSet::new();
    for d in &definitions {
        all_files.insert(d.file.clone());
    }
    for r in &references {
        all_files.insert(r.file.clone());
    }
    
    let mut files: Vec<PathBuf> = all_files.into_iter().collect();
    files.sort();

    let response = ContextResponse {
        query: query.to_string(),
        path: path.to_path_buf(),
        definitions: definitions.clone(),
        references,
        symbols: definitions,
        files: files.clone(),
        related_paths: files,
    };
    
    if json {
        writeln!(writer, "{}", serde_json::to_string_pretty(&response)?)?;
    } else {
        writeln!(writer, "Context for {} in {:?}", query, path)?;
        writeln!(writer, "definitions={} references={} files={}", response.definitions.len(), response.references.len(), response.files.len())?;
    }
    
    Ok(())
}

fn find_definitions(backend: &AstBackend, data: &ProjectDataV6, symbol: &str) -> Result<Vec<SymbolDefinition>> {
    let mut definitions = Vec::new();
    let lang_str = data.project_cfg.get("language").and_then(|v| v.as_str()).unwrap_or("python");
    let language = resolve_language(lang_str)?;
    
    let patterns = match lang_str.to_lowercase().as_str() {
        "python" | "py" => vec![
            (format!("def {}($$$ARGS): $$$BODY", symbol), "function"),
            (format!("class {}: $$$BODY", symbol), "class"),
            (format!("class {}($$$BASE): $$$BODY", symbol), "class"),
        ],
        "javascript" | "js" | "typescript" | "ts" => vec![
            (format!("function {}($$$ARGS) {{ $$$BODY }}", symbol), "function"),
            (format!("class {} {{ $$$BODY }}", symbol), "class"),
            (format!("const {} = $$$VAL", symbol), "variable"),
            (format!("let {} = $$$VAL", symbol), "variable"),
            (format!("var {} = $$$VAL", symbol), "variable"),
        ],
        "rust" | "rs" => vec![
            (format!("fn {}($$$ARGS) {{ $$$BODY }}", symbol), "function"),
            (format!("struct {} {{ $$$BODY }}", symbol), "struct"),
            (format!("enum {} {{ $$$BODY }}", symbol), "enum"),
            (format!("trait {} {{ $$$BODY }}", symbol), "trait"),
            (format!("type {} = $$$VAL", symbol), "type"),
        ],
        _ => vec![(symbol.to_string(), "definition")],
    };

    for file_path_str in &data.candidate_files {
        let file_path = Path::new(file_path_str);
        if !file_matches_language_type(file_path, language) { continue; }

        for (pattern, kind) in &patterns {
            let matches = backend.search_for_cli(pattern, lang_str, file_path_str)?;
            for file_match in matches {
                for m in file_match.matches {
                    definitions.push(SymbolDefinition {
                        name: symbol.to_string(),
                        kind: kind.to_string(),
                        file: file_path.to_path_buf(),
                        line: m.line,
                        end_line: m.line,
                    });
                }
            }
        }
    }
    Ok(definitions)
}

fn find_references(_backend: &AstBackend, data: &ProjectDataV6, symbol: &str) -> Result<Vec<SymbolReference>> {
    let mut references = Vec::new();
    let lang_str = data.project_cfg.get("language").and_then(|v| v.as_str()).unwrap_or("python");
    let language = resolve_language(lang_str)?;
    
    let pattern = symbol;

    let def_kinds: HashSet<&str> = match lang_str.to_lowercase().as_str() {
        "python" | "py" => ["function_definition", "class_definition"].iter().cloned().collect(),
        "javascript" | "js" | "typescript" | "ts" => ["function_declaration", "class_declaration", "variable_declarator"].iter().cloned().collect(),
        "rust" | "rs" => ["function_item", "struct_item", "enum_item", "trait_item", "type_item"].iter().cloned().collect(),
        _ => HashSet::new(),
    };

    for file_path_str in &data.candidate_files {
        let file_path = Path::new(file_path_str);
        if !file_matches_language_type(file_path, language) { continue; }

        let source = fs::read_to_string(file_path)?;
        let ast = language.ast_grep(&source);
        let root = ast.root();
        
        for matched in root.find_all(pattern) {
            let mut is_definition = false;
            
            if let Some(parent) = matched.parent() {
                let kind_owned = parent.kind();
                let kind: &str = kind_owned.as_ref();
                if def_kinds.contains(kind) {
                    if let Some(name_node) = parent.field("name") {
                        if name_node.range() == matched.range() {
                            is_definition = true;
                        }
                    } else {
                        is_definition = true;
                    }
                }
                
                if !is_definition {
                    if kind == "import_from_statement" || kind == "import_statement" || kind == "aliased_import" || kind == "dotted_name" {
                        is_definition = true;
                    }
                }
            }

            if !is_definition {
                let range = matched.range();
                let line = source[..range.start].lines().count();
                let text = source.lines().nth(line.saturating_sub(1)).unwrap_or("").to_string();

                references.push(SymbolReference {
                    name: symbol.to_string(),
                    kind: "reference".to_string(),
                    file: file_path.to_path_buf(),
                    line: line,
                    text,
                });
            }
        }
    }
    Ok(references)
}

fn file_matches_language_type(path: &Path, lang: ast_grep_language::SupportLang) -> bool {
    let extension = path.extension().and_then(|ext| ext.to_str());
    match (lang, extension) {
        (ast_grep_language::SupportLang::Python, Some("py" | "py3" | "pyi" | "bzl")) => true,
        (ast_grep_language::SupportLang::JavaScript, Some("js" | "jsx" | "cjs" | "mjs")) => true,
        (ast_grep_language::SupportLang::TypeScript, Some("ts" | "cts" | "mts")) => true,
        (ast_grep_language::SupportLang::Rust, Some("rs")) => true,
        _ => false,
    }
}

fn find_config_file(path: &Path) -> Result<Option<PathBuf>> {
    let mut current = if path.is_absolute() {
        path.to_path_buf()
    } else {
        std::env::current_dir()?.join(path)
    };

    loop {
        let config = current.join("sgconfig.yml");
        if config.exists() {
            return Ok(Some(config));
        }
        let config_yaml = current.join("sgconfig.yaml");
        if config_yaml.exists() {
            return Ok(Some(config_yaml));
        }

        if let Some(parent) = current.parent() {
            current = parent.to_path_buf();
        } else {
            break;
        }
    }

    Ok(None)
}
