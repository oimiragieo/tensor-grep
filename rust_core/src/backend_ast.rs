use anyhow::{Context, Result};
use ast_grep_core::{meta_var::MetaVariable, matcher::NodeMatch, tree_sitter::LanguageExt, Pattern};
use ast_grep_language::SupportLang;
use ignore::WalkBuilder;
use rayon::prelude::*;
use std::collections::HashMap;
use std::ops::Range;
use std::path::{Path, PathBuf};

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

        let file_results: Vec<Result<Vec<AstMatch>>> = files
            .par_iter()
            .map(|file| Self::search_file_static(&compiled_pattern, language, file))
            .collect();

        let mut matches = Vec::new();
        for result in file_results {
            matches.extend(result?);
        }

        matches.sort_by(|a, b| a.file.cmp(&b.file).then(a.line.cmp(&b.line)));
        Ok(matches)
    }

    fn search_file_static(pattern: &Pattern, lang: SupportLang, file: &Path) -> Result<Vec<AstMatch>> {
        let bytes = std::fs::read(file)
            .with_context(|| format!("failed to read source file {}", file.display()))?;
        if bytes.is_empty() {
            return Ok(Vec::new());
        }
        let source = String::from_utf8(bytes)
            .map_err(|e| anyhow::anyhow!("invalid UTF-8 in {}: {e}", file.display()))?;
        let ast = lang.ast_grep(&source);
        let file_owned = file.to_path_buf();
        let mut line_starts: Option<Vec<usize>> = None;
        let mut matches = Vec::new();

        for matched in ast.root().find_all(pattern.clone()) {
            let ls = line_starts.get_or_insert_with(|| build_line_starts(&source));
            matches.push(build_match(&file_owned, &source, ls, matched));
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
    let file_path = file.to_path_buf();
    let candidate = EditCandidate {
        file: file_path.clone(),
        byte_range: byte_range.clone(),
        metavar_env: extract_metavar_env(source, matched.get_env()),
    };

    AstMatch {
        file: file_path,
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

    let files: Vec<PathBuf> = WalkBuilder::new(path)
        .hidden(true)
        .git_ignore(true)
        .build()
        .filter_map(|entry| entry.ok())
        .filter(|entry| entry.file_type().map_or(false, |ft| ft.is_file()))
        .map(|entry| entry.into_path())
        .filter(|file| file_matches_language(file, lang))
        .collect();

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
