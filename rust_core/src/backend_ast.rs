use anyhow::{Context, Result};
use ast_grep_core::{meta_var::MetaVariable, matcher::NodeMatch, tree_sitter::LanguageExt, Pattern};
use ast_grep_language::SupportLang;
use std::collections::HashMap;
use std::ops::Range;
use std::path::{Path, PathBuf};
use walkdir::WalkDir;

#[derive(Debug, Clone, PartialEq, Eq)]
pub struct EditCandidate {
    pub file: PathBuf,
    pub byte_range: Range<usize>,
    pub metavar_env: HashMap<String, String>,
}

#[derive(Debug, Clone, PartialEq, Eq)]
pub struct AstMatch {
    pub file: PathBuf,
    pub line: usize,
    pub matched_text: String,
    pub candidate: EditCandidate,
}

impl AstMatch {
    pub fn format_for_cli(&self) -> String {
        format!("{}:{}:{}", self.file.display(), self.line, self.matched_text)
    }
}

pub struct AstBackend;

impl Default for AstBackend {
    fn default() -> Self {
        Self::new()
    }
}

impl AstBackend {
    pub fn new() -> Self {
        Self
    }

    pub fn search(&self, pattern: &str, lang: &str, path: &str) -> Result<Vec<AstMatch>> {
        let language = resolve_language(lang)?;
        let compiled_pattern = Pattern::try_new(pattern, language)
            .map_err(|err| anyhow::anyhow!("Invalid pattern: {err}"))?;
        if compiled_pattern.has_error() {
            anyhow::bail!("Invalid pattern: parse error");
        }
        let files = collect_source_files(Path::new(path), language)?;
        let mut matches = Vec::new();

        for file in files {
            matches.extend(self.search_file(&compiled_pattern, language, &file)?);
        }

        Ok(matches)
    }

    fn search_file(&self, pattern: &Pattern, lang: SupportLang, file: &Path) -> Result<Vec<AstMatch>> {
        let source = std::fs::read_to_string(file)
            .with_context(|| format!("failed to read source file {}", file.display()))?;
        let ast = lang.ast_grep(&source);
        let line_starts = build_line_starts(&source);
        let mut matches = Vec::new();

        for matched in ast.root().find_all(pattern.clone()) {
            matches.push(build_match(file, &source, &line_starts, matched));
        }

        Ok(matches)
    }
}

fn build_match<'tree>(
    file: &Path,
    source: &str,
    line_starts: &[usize],
    matched: NodeMatch<'tree, ast_grep_core::tree_sitter::StrDoc<SupportLang>>,
) -> AstMatch {
    let byte_range = matched.range();
    let matched_text = matched.text().to_string();
    let candidate = EditCandidate {
        file: file.to_path_buf(),
        byte_range: byte_range.clone(),
        metavar_env: extract_metavar_env(source, matched.get_env()),
    };

    AstMatch {
        file: file.to_path_buf(),
        line: line_number_for_byte(line_starts, byte_range.start),
        matched_text,
        candidate,
    }
}

fn resolve_language(lang: &str) -> Result<SupportLang> {
    let language = lang
        .parse::<SupportLang>()
        .map_err(|_| anyhow::anyhow!("Unsupported language: {lang}"))?;

    match language {
        SupportLang::Python
        | SupportLang::JavaScript
        | SupportLang::TypeScript
        | SupportLang::Rust => Ok(language),
        _ => anyhow::bail!("Unsupported language: {lang}"),
    }
}

fn collect_source_files(path: &Path, lang: SupportLang) -> Result<Vec<PathBuf>> {
    if !path.exists() {
        anyhow::bail!("Path not found: {}", path.display());
    }

    if path.is_file() {
        return Ok(vec![path.to_path_buf()]);
    }

    let mut files = WalkDir::new(path)
        .into_iter()
        .filter_map(|entry| entry.ok())
        .filter(|entry| entry.file_type().is_file())
        .map(|entry| entry.into_path())
        .filter(|file| file_matches_language(file, lang))
        .collect::<Vec<_>>();

    files.sort();
    Ok(files)
}

fn file_matches_language(path: &Path, lang: SupportLang) -> bool {
    let extension = path.extension().and_then(|ext| ext.to_str());
    matches!(
        (lang, extension),
        (SupportLang::Python, Some("py" | "py3" | "pyi" | "bzl"))
            | (SupportLang::JavaScript, Some("js" | "jsx" | "cjs" | "mjs"))
            | (SupportLang::TypeScript, Some("ts" | "cts" | "mts"))
            | (SupportLang::Rust, Some("rs"))
    )
}

fn build_line_starts(source: &str) -> Vec<usize> {
    let mut line_starts = vec![0];
    for (index, byte) in source.as_bytes().iter().enumerate() {
        if *byte == b'\n' {
            line_starts.push(index + 1);
        }
    }
    line_starts
}

fn line_number_for_byte(line_starts: &[usize], byte_offset: usize) -> usize {
    line_starts.partition_point(|start| *start <= byte_offset)
}

fn extract_metavar_env(
    source: &str,
    env: &ast_grep_core::meta_var::MetaVarEnv<'_, ast_grep_core::tree_sitter::StrDoc<SupportLang>>,
) -> HashMap<String, String> {
    let mut extracted = HashMap::new();

    for variable in env.get_matched_variables() {
        match variable {
            MetaVariable::Capture(name, _) => {
                if let Some(node) = env.get_match(&name) {
                    extracted.insert(name, node.text().to_string());
                }
            }
            MetaVariable::MultiCapture(name) => {
                let nodes = env.get_multiple_matches(&name);
                if let (Some(first), Some(last)) = (nodes.first(), nodes.last()) {
                    extracted.insert(
                        name,
                        source[first.range().start..last.range().end].to_string(),
                    );
                }
            }
            MetaVariable::Dropped(_) | MetaVariable::Multiple => {}
        }
    }

    extracted
}
