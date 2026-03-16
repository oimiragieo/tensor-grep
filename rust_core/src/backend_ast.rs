use anyhow::{Context, Result};
use ast_grep_core::{meta_var::MetaVariable, matcher::NodeMatch, tree_sitter::LanguageExt, Pattern};
use ast_grep_language::SupportLang;
use ignore::WalkBuilder;
use rayon::prelude::*;
use serde::Serialize;
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

#[derive(Debug, Clone, PartialEq, Eq, Serialize)]
pub struct RewriteEdit {
    pub id: String,
    pub file: PathBuf,
    pub line: usize,
    pub byte_range: Range<usize>,
    pub original_text: String,
    pub replacement_text: String,
    pub metavar_env: HashMap<String, String>,
}

#[derive(Debug, Clone, PartialEq, Eq, Serialize)]
pub struct RewritePlan {
    pub version: u32,
    pub pattern: String,
    pub replacement: String,
    pub lang: String,
    pub total_files_scanned: usize,
    pub total_edits: usize,
    pub edits: Vec<RewriteEdit>,
    #[serde(skip_serializing_if = "Vec::is_empty")]
    pub rejected_overlaps: Vec<OverlapRejection>,
}

#[derive(Debug, Clone, PartialEq, Eq, Serialize)]
pub struct OverlapRejection {
    pub file: PathBuf,
    pub edit_a: Range<usize>,
    pub edit_b: Range<usize>,
    pub reason: String,
}

#[derive(Debug, Clone, PartialEq, Eq, Serialize)]
pub struct VerifyResult {
    pub total_edits: usize,
    pub verified: usize,
    pub mismatches: Vec<VerifyMismatch>,
}

#[derive(Debug, Clone, PartialEq, Eq, Serialize)]
pub struct VerifyMismatch {
    pub edit_id: String,
    pub file: PathBuf,
    pub line: usize,
    pub expected: String,
    pub actual: String,
}

impl RewritePlan {
    pub fn verify(&self, _backend: &AstBackend) -> Result<VerifyResult> {
        let mut edits_by_file: HashMap<&Path, Vec<&RewriteEdit>> = HashMap::new();
        for edit in &self.edits {
            edits_by_file.entry(edit.file.as_path()).or_default().push(edit);
        }

        let mut mismatches = Vec::new();

        for (file, file_edits) in &edits_by_file {
            let content = std::fs::read_to_string(file)
                .with_context(|| format!("verify: failed to read {}", file.display()))?;

            let mut byte_offset_delta: isize = 0;

            for edit in file_edits {
                let adjusted_start = (edit.byte_range.start as isize + byte_offset_delta) as usize;
                let adjusted_end = adjusted_start + edit.replacement_text.len();

                if adjusted_end > content.len() {
                    mismatches.push(VerifyMismatch {
                        edit_id: edit.id.clone(),
                        file: edit.file.clone(),
                        line: edit.line,
                        expected: edit.replacement_text.clone(),
                        actual: format!("<out of bounds: file len {}, expected end {}>", content.len(), adjusted_end),
                    });
                    continue;
                }

                let actual = &content[adjusted_start..adjusted_end];
                if actual != edit.replacement_text {
                    mismatches.push(VerifyMismatch {
                        edit_id: edit.id.clone(),
                        file: edit.file.clone(),
                        line: edit.line,
                        expected: edit.replacement_text.clone(),
                        actual: actual.to_string(),
                    });
                }

                byte_offset_delta += edit.replacement_text.len() as isize
                    - (edit.byte_range.end - edit.byte_range.start) as isize;
            }
        }

        let verified = self.edits.len() - mismatches.len();
        Ok(VerifyResult {
            total_edits: self.edits.len(),
            verified,
            mismatches,
        })
    }

    pub fn generate_diff(&self) -> Result<String> {
        let mut edits_by_file: HashMap<&Path, Vec<&RewriteEdit>> = HashMap::new();
        for edit in &self.edits {
            edits_by_file.entry(edit.file.as_path()).or_default().push(edit);
        }

        let mut output = String::new();
        let mut files: Vec<&&Path> = edits_by_file.keys().collect();
        files.sort();

        for file in files {
            let file_edits = &edits_by_file[*file];
            let original = std::fs::read_to_string(file)
                .with_context(|| format!("failed to read {}", file.display()))?;

            let mut rewritten = String::with_capacity(original.len());
            let mut cursor = 0usize;
            for edit in file_edits {
                rewritten.push_str(&original[cursor..edit.byte_range.start]);
                rewritten.push_str(&edit.replacement_text);
                cursor = edit.byte_range.end;
            }
            rewritten.push_str(&original[cursor..]);

            let orig_lines: Vec<&str> = original.lines().collect();
            let new_lines: Vec<&str> = rewritten.lines().collect();

            let display_path = file.display();
            output.push_str(&format!("--- a/{display_path}\n"));
            output.push_str(&format!("+++ b/{display_path}\n"));
            emit_unified_hunks(&orig_lines, &new_lines, 3, &mut output);
        }

        Ok(output)
    }
}

fn emit_unified_hunks(old: &[&str], new: &[&str], context: usize, out: &mut String) {
    let mut i = 0;
    let mut j = 0;

    while i < old.len() || j < new.len() {
        if i < old.len() && j < new.len() && old[i] == new[j] {
            i += 1;
            j += 1;
            continue;
        }

        let hunk_start_i = i.saturating_sub(context);
        let hunk_start_j = j.saturating_sub(context);

        let mut hunk_old: Vec<(char, &str)> = Vec::new();
        let mut hunk_new: Vec<(char, &str)> = Vec::new();

        for line in &old[hunk_start_i..i] {
            hunk_old.push((' ', line));
        }
        for line in &new[hunk_start_j..j] {
            hunk_new.push((' ', line));
        }

        while i < old.len() || j < new.len() {
            if i < old.len() && j < new.len() && old[i] == new[j] {
                let mut trailing = 0;
                let mut ti = i;
                let mut tj = j;
                while ti < old.len() && tj < new.len() && old[ti] == new[tj] {
                    trailing += 1;
                    ti += 1;
                    tj += 1;
                }
                if trailing > context * 2 || (ti >= old.len() && tj >= new.len()) {
                    let take = trailing.min(context);
                    for k in 0..take {
                        hunk_old.push((' ', old[i + k]));
                        hunk_new.push((' ', new[j + k]));
                    }
                    i += trailing;
                    j += trailing;
                    break;
                }
                for k in 0..trailing {
                    hunk_old.push((' ', old[i + k]));
                    hunk_new.push((' ', new[j + k]));
                }
                i += trailing;
                j += trailing;
            } else {
                if i < old.len() && (j >= new.len() || old[i] != *new.get(j).unwrap_or(&"")) {
                    hunk_old.push(('-', old[i]));
                    i += 1;
                }
                if j < new.len() && (i >= old.len() || new[j] != *old.get(i).unwrap_or(&"")) {
                    hunk_new.push(('+', new[j]));
                    j += 1;
                }
            }
        }

        let old_count = hunk_old.len();
        let new_count = hunk_new.len();
        out.push_str(&format!(
            "@@ -{},{} +{},{} @@\n",
            hunk_start_i + 1,
            old_count,
            hunk_start_j + 1,
            new_count,
        ));

        let mut oi = 0;
        let mut ni = 0;
        while oi < hunk_old.len() || ni < hunk_new.len() {
            if oi < hunk_old.len() && hunk_old[oi].0 == ' '
                && ni < hunk_new.len() && hunk_new[ni].0 == ' '
                && hunk_old[oi].1 == hunk_new[ni].1
            {
                out.push_str(&format!(" {}\n", hunk_old[oi].1));
                oi += 1;
                ni += 1;
            } else {
                while oi < hunk_old.len() && hunk_old[oi].0 == '-' {
                    out.push_str(&format!("-{}\n", hunk_old[oi].1));
                    oi += 1;
                }
                while ni < hunk_new.len() && hunk_new[ni].0 == '+' {
                    out.push_str(&format!("+{}\n", hunk_new[ni].1));
                    ni += 1;
                }
                if oi < hunk_old.len() && hunk_old[oi].0 == ' ' {
                    continue;
                }
                if ni < hunk_new.len() && hunk_new[ni].0 == ' ' {
                    continue;
                }
            }
        }
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

    pub fn plan_rewrites(&self, pattern: &str, replacement: &str, lang: &str, path: &str) -> Result<RewritePlan> {
        let language = resolve_language(lang)?;
        let compiled_pattern = Pattern::try_new(pattern, language)
            .map_err(|err| anyhow::anyhow!("Invalid pattern: {err}"))?;
        if compiled_pattern.has_error() {
            anyhow::bail!("Invalid pattern: parse error");
        }
        let files = collect_source_files(Path::new(path), language)?;
        let total_files_scanned = files.len();

        let file_results: Vec<Result<Vec<RewriteEdit>>> = files
            .par_iter()
            .map(|file| Self::plan_file_rewrites(&compiled_pattern, replacement, language, file))
            .collect();

        let mut edits = Vec::new();
        for result in file_results {
            edits.extend(result?);
        }

        edits.sort_by(|a, b| a.file.cmp(&b.file).then(a.byte_range.start.cmp(&b.byte_range.start)));
        assign_edit_ids(&mut edits);
        let (valid_edits, rejected_overlaps) = validate_no_overlaps(edits);

        Ok(RewritePlan {
            version: 1,
            pattern: pattern.to_string(),
            replacement: replacement.to_string(),
            lang: lang.to_string(),
            total_files_scanned,
            total_edits: valid_edits.len(),
            edits: valid_edits,
            rejected_overlaps,
        })
    }

    pub fn apply_rewrites(plan: &RewritePlan) -> Result<usize> {
        let mut edits_by_file: HashMap<&Path, Vec<&RewriteEdit>> = HashMap::new();
        for edit in &plan.edits {
            edits_by_file.entry(edit.file.as_path()).or_default().push(edit);
        }

        let files: Vec<(&Path, &Vec<&RewriteEdit>)> = edits_by_file.iter().map(|(k, v)| (*k, v)).collect();
        let results: Vec<Result<()>> = files
            .par_iter()
            .map(|(file, file_edits)| apply_edits_to_file(file, file_edits))
            .collect();

        let mut files_written = 0;
        for result in results {
            result?;
            files_written += 1;
        }

        Ok(files_written)
    }

    pub fn plan_and_apply(&self, pattern: &str, replacement: &str, lang: &str, path: &str) -> Result<RewritePlan> {
        let language = resolve_language(lang)?;
        let compiled_pattern = Pattern::try_new(pattern, language)
            .map_err(|err| anyhow::anyhow!("Invalid pattern: {err}"))?;
        if compiled_pattern.has_error() {
            anyhow::bail!("Invalid pattern: parse error");
        }
        let files = collect_source_files(Path::new(path), language)?;
        let total_files_scanned = files.len();

        let file_results: Vec<Result<Vec<RewriteEdit>>> = files
            .par_iter()
            .map(|file| Self::plan_file_rewrites(&compiled_pattern, replacement, language, file))
            .collect();

        let mut all_edits = Vec::new();
        for result in file_results {
            all_edits.extend(result?);
        }

        all_edits.sort_by(|a, b| a.file.cmp(&b.file).then(a.byte_range.start.cmp(&b.byte_range.start)));
        assign_edit_ids(&mut all_edits);
        let (valid_edits, rejected_overlaps) = validate_no_overlaps(all_edits);

        let mut edits_by_file: HashMap<&Path, Vec<&RewriteEdit>> = HashMap::new();
        for edit in &valid_edits {
            edits_by_file.entry(edit.file.as_path()).or_default().push(edit);
        }

        let write_ops: Vec<(&Path, &Vec<&RewriteEdit>)> =
            edits_by_file.iter().map(|(k, v)| (*k, v)).collect();
        write_ops
            .par_iter()
            .try_for_each(|(file, file_edits)| apply_edits_to_file(file, file_edits))?;

        Ok(RewritePlan {
            version: 1,
            pattern: pattern.to_string(),
            replacement: replacement.to_string(),
            lang: lang.to_string(),
            total_files_scanned,
            total_edits: valid_edits.len(),
            edits: valid_edits,
            rejected_overlaps,
        })
    }

    fn plan_file_rewrites(
        pattern: &Pattern,
        replacement: &str,
        lang: SupportLang,
        file: &Path,
    ) -> Result<Vec<RewriteEdit>> {
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
        let mut edits = Vec::new();

        for matched in ast.root().find_all(pattern.clone()) {
            let ls = line_starts.get_or_insert_with(|| build_line_starts(&source));
            let byte_range = matched.range();
            let original_text = matched.text().to_string();
            let metavar_env = extract_metavar_env(&source, matched.get_env());
            let edit = matched.replace_by(replacement);
            let inserted_bytes = edit.inserted_text;
            let replacement_text = String::from_utf8(inserted_bytes)
                .unwrap_or_else(|e| String::from_utf8_lossy(e.as_bytes()).to_string());

            edits.push(RewriteEdit {
                id: String::new(),
                file: file_owned.clone(),
                line: line_number_for_byte(ls, byte_range.start),
                byte_range,
                original_text,
                replacement_text,
                metavar_env,
            });
        }

        Ok(edits)
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

fn apply_edits_to_file(file: &Path, edits: &[&RewriteEdit]) -> Result<()> {
    let original = std::fs::read_to_string(file)
        .with_context(|| format!("failed to read {}", file.display()))?;
    let mut result = String::with_capacity(original.len());
    let mut cursor = 0usize;
    for edit in edits {
        if edit.byte_range.start < cursor {
            anyhow::bail!(
                "overlapping edit in {}: byte {} < cursor {}",
                file.display(), edit.byte_range.start, cursor
            );
        }
        result.push_str(&original[cursor..edit.byte_range.start]);
        result.push_str(&edit.replacement_text);
        cursor = edit.byte_range.end;
    }
    result.push_str(&original[cursor..]);
    std::fs::write(file, &result)
        .with_context(|| format!("failed to write {}", file.display()))
}

fn assign_edit_ids(edits: &mut [RewriteEdit]) {
    for (i, edit) in edits.iter_mut().enumerate() {
        let stem = edit.file.file_name()
            .and_then(|n| n.to_str())
            .unwrap_or("unknown");
        edit.id = format!("e{i:04}:{stem}:{}-{}", edit.byte_range.start, edit.byte_range.end);
    }
}

fn validate_no_overlaps(edits: Vec<RewriteEdit>) -> (Vec<RewriteEdit>, Vec<OverlapRejection>) {
    let mut valid = Vec::new();
    let mut rejected = Vec::new();

    let mut prev_end_by_file: HashMap<PathBuf, usize> = HashMap::new();

    for edit in edits {
        let prev_end = prev_end_by_file.get(&edit.file).copied().unwrap_or(0);
        if edit.byte_range.start < prev_end {
            rejected.push(OverlapRejection {
                file: edit.file.clone(),
                edit_a: (prev_end.saturating_sub(100))..prev_end,
                edit_b: edit.byte_range.clone(),
                reason: format!(
                    "edit at byte {} overlaps with previous edit ending at byte {}",
                    edit.byte_range.start, prev_end
                ),
            });
            continue;
        }
        prev_end_by_file.insert(edit.file.clone(), edit.byte_range.end);
        valid.push(edit);
    }

    (valid, rejected)
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
