use anyhow::{Context, Result};
use ast_grep_core::{
    matcher::NodeMatch, meta_var::MetaVariable, tree_sitter::LanguageExt, Pattern,
};
use ast_grep_language::SupportLang;
use ignore::{
    types::{Types, TypesBuilder},
    WalkBuilder,
};
use rayon::prelude::*;
use serde::{Deserialize, Serialize};
use std::collections::{BTreeMap, HashMap};
use std::convert::TryFrom;
use std::fs::OpenOptions;
use std::io::{ErrorKind, Write};
use std::ops::Range;
use std::path::{Path, PathBuf};
use std::sync::atomic::{AtomicU64, Ordering};
use std::time::{SystemTime, UNIX_EPOCH};

static TEMP_FILE_COUNTER: AtomicU64 = AtomicU64::new(0);
const UTF8_BOM: &[u8; 3] = b"\xEF\xBB\xBF";
const BINARY_SCAN_BYTES: usize = 8192;
const MAX_REWRITE_FILE_BYTES: u64 = 100 * 1024 * 1024;
const PAR_SORT_EDIT_THRESHOLD: usize = 512;

struct RewriteSource {
    bom_len: usize,
    original_source: String,
    planned_mtime_ns: u64,
}

impl RewriteSource {
    fn ast_source(&self) -> &str {
        &self.original_source[self.bom_len..]
    }
}

struct PreparedRewriteFile {
    file: PathBuf,
    original_source: String,
    edits: Vec<RewriteEdit>,
}

struct PerFileRewriteOutcome {
    edits: Vec<RewriteEdit>,
    rejected_overlaps: Vec<OverlapRejection>,
}

#[derive(Clone, Copy)]
enum ApplyExecution {
    Parallel,
    #[cfg(test)]
    Sequential,
}

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
        format!(
            "{}:{}:{}",
            self.file.display(),
            self.line,
            self.matched_text
        )
    }
}

#[derive(Debug, Clone, PartialEq, Eq)]
pub struct AstCliMatch {
    pub line: usize,
    pub matched_text: String,
}

#[derive(Debug, Clone, PartialEq, Eq)]
pub struct AstCliFileMatches {
    pub file: PathBuf,
    pub matches: Vec<AstCliMatch>,
}

#[derive(Debug, Clone, PartialEq, Eq, Serialize)]
pub struct RewriteEdit {
    pub id: String,
    pub file: PathBuf,
    pub planned_mtime_ns: u64,
    pub line: usize,
    pub byte_range: Range<usize>,
    pub original_text: String,
    pub replacement_text: String,
    pub metavar_env: HashMap<String, String>,
}

#[derive(Debug, Clone, PartialEq, Eq, Serialize)]
pub struct RewritePlan {
    pub version: u32,
    pub routing_backend: &'static str,
    pub routing_reason: &'static str,
    pub sidecar_used: bool,
    pub pattern: String,
    pub replacement: String,
    pub lang: String,
    pub total_files_scanned: usize,
    pub total_edits: usize,
    pub edits: Vec<RewriteEdit>,
    #[serde(skip_serializing_if = "Vec::is_empty")]
    pub rejected_overlaps: Vec<OverlapRejection>,
}

#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
pub struct BatchRewriteRule {
    pub pattern: String,
    pub replacement: String,
    pub lang: String,
}

#[derive(Debug, Clone, PartialEq, Eq, Serialize)]
pub struct BatchRewritePlan {
    pub version: u32,
    pub routing_backend: &'static str,
    pub routing_reason: &'static str,
    pub sidecar_used: bool,
    pub rewrites: Vec<BatchRewriteRule>,
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
        verify_rewrite_edits(&self.edits)
    }

    pub fn generate_diff(&self) -> Result<String> {
        generate_diff_for_edits(&self.edits)
    }
}

impl BatchRewritePlan {
    pub fn verify(&self, _backend: &AstBackend) -> Result<VerifyResult> {
        verify_rewrite_edits(&self.edits)
    }

    pub fn generate_diff(&self) -> Result<String> {
        generate_diff_for_edits(&self.edits)
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
            if oi < hunk_old.len()
                && hunk_old[oi].0 == ' '
                && ni < hunk_new.len()
                && hunk_new[ni].0 == ' '
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

struct CompiledBatchRewrite {
    replacement: String,
    pattern: Pattern,
}

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
        self.search_with_prefilter(pattern, lang, path, true)
    }

    fn search_with_prefilter(
        &self,
        pattern: &str,
        lang: &str,
        path: &str,
        prefilter_enabled: bool,
    ) -> Result<Vec<AstMatch>> {
        let language = resolve_language(lang)?;
        let compiled_pattern = compile_ast_pattern(pattern, language)?;
        search_path_with_prefilter(
            &compiled_pattern,
            language,
            Path::new(path),
            prefilter_enabled,
        )
    }

    pub fn search_for_cli(
        &self,
        pattern: &str,
        lang: &str,
        path: &str,
    ) -> Result<Vec<AstCliFileMatches>> {
        let language = resolve_language(lang)?;
        let compiled_pattern = compile_ast_pattern(pattern, language)?;
        search_path_for_cli(&compiled_pattern, language, Path::new(path), true)
    }

    pub fn plan_rewrites(
        &self,
        pattern: &str,
        replacement: &str,
        lang: &str,
        path: &str,
    ) -> Result<RewritePlan> {
        let language = resolve_language(lang)?;
        let compiled_pattern = Pattern::try_new(pattern, language)
            .map_err(|err| anyhow::anyhow!("Invalid pattern: {err}"))?;
        if compiled_pattern.has_error() {
            anyhow::bail!("Invalid pattern: parse error");
        }
        let (total_files_scanned, prepared_files) = Self::collect_prepared_single_rewrite_files(
            &compiled_pattern,
            replacement,
            language,
            Path::new(path),
        )?;
        let mut edits = Vec::new();
        for prepared in prepared_files {
            edits.extend(prepared.edits);
        }

        sort_rewrite_edits(&mut edits);
        assign_edit_ids(&mut edits);
        let (valid_edits, rejected_overlaps) = validate_no_overlaps(edits);

        Ok(RewritePlan {
            version: 1,
            routing_backend: "AstBackend",
            routing_reason: "ast-native",
            sidecar_used: false,
            pattern: pattern.to_string(),
            replacement: replacement.to_string(),
            lang: lang.to_string(),
            total_files_scanned,
            total_edits: valid_edits.len(),
            edits: valid_edits,
            rejected_overlaps,
        })
    }

    pub fn plan_batch_rewrites(
        &self,
        rewrites: &[BatchRewriteRule],
        path: &str,
    ) -> Result<BatchRewritePlan> {
        if rewrites.is_empty() {
            anyhow::bail!("batch rewrite config requires at least one rewrite rule");
        }

        let compiled_by_lang = compile_batch_rewrites_by_lang(rewrites)?;
        let (total_files_scanned, prepared_files) =
            Self::collect_prepared_batch_rewrite_files(Path::new(path), &compiled_by_lang)?;
        let mut edits = Vec::new();
        for prepared in prepared_files {
            edits.extend(prepared.edits);
        }

        sort_rewrite_edits(&mut edits);
        assign_edit_ids(&mut edits);
        let (valid_edits, rejected_overlaps) = validate_batch_no_overlaps(edits);

        Ok(BatchRewritePlan {
            version: 1,
            routing_backend: "AstBackend",
            routing_reason: "ast-native",
            sidecar_used: false,
            rewrites: rewrites.to_vec(),
            total_files_scanned,
            total_edits: valid_edits.len(),
            edits: valid_edits,
            rejected_overlaps,
        })
    }

    pub fn apply_rewrites(plan: &RewritePlan) -> Result<usize> {
        apply_edit_set(&plan.edits)
    }

    pub fn apply_batch_rewrites(plan: &BatchRewritePlan) -> Result<usize> {
        apply_edit_set(&plan.edits)
    }

    pub fn plan_and_apply(
        &self,
        pattern: &str,
        replacement: &str,
        lang: &str,
        path: &str,
    ) -> Result<RewritePlan> {
        let language = resolve_language(lang)?;
        let compiled_pattern = Pattern::try_new(pattern, language)
            .map_err(|err| anyhow::anyhow!("Invalid pattern: {err}"))?;
        if compiled_pattern.has_error() {
            anyhow::bail!("Invalid pattern: parse error");
        }
        let files = collect_source_files(Path::new(path), language)?;
        let total_files_scanned = files.len();
        let file_results: Vec<Result<Option<PerFileRewriteOutcome>>> = files
            .par_iter()
            .map(|file| {
                Self::plan_and_apply_single_file_rewrite(
                    &compiled_pattern,
                    replacement,
                    language,
                    file,
                )
            })
            .collect();

        let (mut valid_edits, mut rejected_overlaps) = collect_per_file_rewrite_outcomes(file_results)?;
        sort_rewrite_edits(&mut valid_edits);
        sort_overlap_rejections(&mut rejected_overlaps);
        assign_edit_ids(&mut valid_edits);

        Ok(RewritePlan {
            version: 1,
            routing_backend: "AstBackend",
            routing_reason: "ast-native",
            sidecar_used: false,
            pattern: pattern.to_string(),
            replacement: replacement.to_string(),
            lang: lang.to_string(),
            total_files_scanned,
            total_edits: valid_edits.len(),
            edits: valid_edits,
            rejected_overlaps,
        })
    }

    pub fn plan_and_apply_batch(
        &self,
        rewrites: &[BatchRewriteRule],
        path: &str,
    ) -> Result<BatchRewritePlan> {
        if rewrites.is_empty() {
            anyhow::bail!("batch rewrite config requires at least one rewrite rule");
        }

        let compiled_by_lang = compile_batch_rewrites_by_lang(rewrites)?;
        let mut total_files_scanned = 0usize;
        let mut valid_edits = Vec::new();
        let mut rejected_overlaps = Vec::new();

        for (lang_name, rules) in &compiled_by_lang {
            let language = resolve_language(lang_name)?;
            let files = collect_batch_source_files(Path::new(path), language)?;
            total_files_scanned += files.len();

            let file_results: Vec<Result<Option<PerFileRewriteOutcome>>> = files
                .par_iter()
                .map(|file| Self::plan_and_apply_single_batch_rewrite(rules, language, file))
                .collect();

            let (mut file_edits, mut file_rejections) =
                collect_per_file_rewrite_outcomes(file_results)?;
            valid_edits.append(&mut file_edits);
            rejected_overlaps.append(&mut file_rejections);
        }

        sort_rewrite_edits(&mut valid_edits);
        sort_overlap_rejections(&mut rejected_overlaps);
        assign_edit_ids(&mut valid_edits);

        Ok(BatchRewritePlan {
            version: 1,
            routing_backend: "AstBackend",
            routing_reason: "ast-native",
            sidecar_used: false,
            rewrites: rewrites.to_vec(),
            total_files_scanned,
            total_edits: valid_edits.len(),
            edits: valid_edits,
            rejected_overlaps,
        })
    }

    fn collect_prepared_single_rewrite_files(
        pattern: &Pattern,
        replacement: &str,
        lang: SupportLang,
        path: &Path,
    ) -> Result<(usize, Vec<PreparedRewriteFile>)> {
        let files = collect_source_files(path, lang)?;
        let total_files_scanned = files.len();

        let file_results: Vec<Result<Option<PreparedRewriteFile>>> = files
            .par_iter()
            .map(|file| {
                Self::plan_file_rewrites_prepared(pattern, replacement, lang, file)
            })
            .collect();

        let mut prepared_files = Vec::new();
        for result in file_results {
            if let Some(prepared) = result? {
                prepared_files.push(prepared);
            }
        }

        Ok((total_files_scanned, prepared_files))
    }

    fn collect_prepared_batch_rewrite_files(
        search_root: &Path,
        compiled_by_lang: &BTreeMap<String, Vec<CompiledBatchRewrite>>,
    ) -> Result<(usize, Vec<PreparedRewriteFile>)> {
        let mut total_files_scanned = 0usize;
        let mut prepared_files = Vec::new();

        for (lang_name, rules) in compiled_by_lang {
            let language = resolve_language(lang_name)?;
            let files = collect_batch_source_files(search_root, language)?;
            total_files_scanned += files.len();

            let file_results: Vec<Result<Option<PreparedRewriteFile>>> = files
                .par_iter()
                .map(|file| Self::plan_file_batch_rewrites_prepared(rules, language, file))
                .collect();

            for result in file_results {
                if let Some(prepared) = result? {
                    prepared_files.push(prepared);
                }
            }
        }

        Ok((total_files_scanned, prepared_files))
    }

    fn plan_and_apply_single_file_rewrite(
        pattern: &Pattern,
        replacement: &str,
        lang: SupportLang,
        file: &Path,
    ) -> Result<Option<PerFileRewriteOutcome>> {
        let Some(mut prepared) = Self::plan_file_rewrites_prepared(pattern, replacement, lang, file)? else {
            return Ok(None);
        };
        sort_rewrite_edits(&mut prepared.edits);
        let (valid_edits, rejected_overlaps) = validate_no_overlaps(prepared.edits);
        apply_prepared_rewrite_file(prepared.file, prepared.original_source, valid_edits, rejected_overlaps)
    }

    fn plan_and_apply_single_batch_rewrite(
        rewrites: &[CompiledBatchRewrite],
        lang: SupportLang,
        file: &Path,
    ) -> Result<Option<PerFileRewriteOutcome>> {
        let Some(mut prepared) = Self::plan_file_batch_rewrites_prepared(rewrites, lang, file)? else {
            return Ok(None);
        };
        sort_rewrite_edits(&mut prepared.edits);
        let (valid_edits, rejected_overlaps) = validate_batch_no_overlaps(prepared.edits);
        apply_prepared_rewrite_file(prepared.file, prepared.original_source, valid_edits, rejected_overlaps)
    }

    fn plan_file_rewrites_prepared(
        pattern: &Pattern,
        replacement: &str,
        lang: SupportLang,
        file: &Path,
    ) -> Result<Option<PreparedRewriteFile>> {
        let Some(rewrite_source) = load_rewrite_source(file)? else {
            return Ok(None);
        };
        let file_owned = file.to_path_buf();
        let bom_len = rewrite_source.bom_len;
        let planned_mtime_ns = rewrite_source.planned_mtime_ns;
        let edits = {
            let source = rewrite_source.ast_source();
            if source.is_empty() {
                return Ok(None);
            }

            let ast = lang.ast_grep(source);
            let mut line_starts: Option<Vec<usize>> = None;
            let mut edits = Vec::new();

            for matched in ast.root().find_all(pattern.clone()) {
                let ls = line_starts.get_or_insert_with(|| build_line_starts(source));
                let byte_range = matched.range();
                ensure_valid_utf8_range(source, file, &byte_range)?;
                let original_text = matched.text().to_string();
                let metavar_env = extract_metavar_env(source, matched.get_env());
                let edit = matched.replace_by(replacement);
                let inserted_bytes = edit.inserted_text;
                let replacement_text = String::from_utf8(inserted_bytes)
                    .unwrap_or_else(|e| String::from_utf8_lossy(e.as_bytes()).to_string());

                edits.push(RewriteEdit {
                    id: String::new(),
                    file: file_owned.clone(),
                    planned_mtime_ns,
                    line: line_number_for_byte(ls, byte_range.start),
                    byte_range: (byte_range.start + bom_len)..(byte_range.end + bom_len),
                    original_text,
                    replacement_text,
                    metavar_env,
                });
            }

            edits
        };

        if edits.is_empty() {
            return Ok(None);
        }

        Ok(Some(PreparedRewriteFile {
            file: file_owned,
            original_source: rewrite_source.original_source,
            edits,
        }))
    }

    fn plan_file_batch_rewrites_prepared(
        rewrites: &[CompiledBatchRewrite],
        lang: SupportLang,
        file: &Path,
    ) -> Result<Option<PreparedRewriteFile>> {
        let Some(rewrite_source) = load_rewrite_source(file)? else {
            return Ok(None);
        };
        let file_owned = file.to_path_buf();
        let bom_len = rewrite_source.bom_len;
        let planned_mtime_ns = rewrite_source.planned_mtime_ns;
        let edits = {
            let source = rewrite_source.ast_source();
            if source.is_empty() {
                return Ok(None);
            }

            let ast = lang.ast_grep(source);
            let mut line_starts: Option<Vec<usize>> = None;
            let mut edits = Vec::new();

            for rewrite in rewrites {
                for matched in ast.root().find_all(rewrite.pattern.clone()) {
                    let ls = line_starts.get_or_insert_with(|| build_line_starts(source));
                    let byte_range = matched.range();
                    ensure_valid_utf8_range(source, file, &byte_range)?;
                    let original_text = matched.text().to_string();
                    let metavar_env = extract_metavar_env(source, matched.get_env());
                    let edit = matched.replace_by(rewrite.replacement.as_str());
                    let inserted_bytes = edit.inserted_text;
                    let replacement_text = String::from_utf8(inserted_bytes)
                        .unwrap_or_else(|e| String::from_utf8_lossy(e.as_bytes()).to_string());

                    edits.push(RewriteEdit {
                        id: String::new(),
                        file: file_owned.clone(),
                        planned_mtime_ns,
                        line: line_number_for_byte(ls, byte_range.start),
                        byte_range: (byte_range.start + bom_len)..(byte_range.end + bom_len),
                        original_text,
                        replacement_text,
                        metavar_env,
                    });
                }
            }

            edits
        };

        if edits.is_empty() {
            return Ok(None);
        }

        Ok(Some(PreparedRewriteFile {
            file: file_owned,
            original_source: rewrite_source.original_source,
            edits,
        }))
    }

    fn search_file_static(
        pattern: &Pattern,
        lang: SupportLang,
        prefilter_literal: Option<&str>,
        file: &Path,
    ) -> Result<Vec<AstMatch>> {
        let Some(source) = load_search_source(file)? else {
            return Ok(Vec::new());
        };
        if prefilter_literal.is_some_and(|literal| !source.contains(literal)) {
            return Ok(Vec::new());
        }

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

    fn search_file_for_cli_static(
        pattern: &Pattern,
        lang: SupportLang,
        prefilter_literal: Option<&str>,
        file: &Path,
    ) -> Result<Vec<AstCliMatch>> {
        let Some(source) = load_search_source(file)? else {
            return Ok(Vec::new());
        };
        if prefilter_literal.is_some_and(|literal| !source.contains(literal)) {
            return Ok(Vec::new());
        }

        let ast = lang.ast_grep(&source);
        let mut line_starts: Option<Vec<usize>> = None;
        let mut matches = Vec::new();

        for matched in ast.root().find_all(pattern.clone()) {
            let ls = line_starts.get_or_insert_with(|| build_line_starts(&source));
            matches.push(AstCliMatch {
                line: line_number_for_byte(ls, matched.range().start),
                matched_text: matched.text().to_string(),
            });
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

fn compile_ast_pattern(pattern: &str, language: SupportLang) -> Result<Pattern> {
    let compiled_pattern = Pattern::try_new(pattern, language)
        .map_err(|err| anyhow::anyhow!("Invalid pattern: {err}"))?;
    if compiled_pattern.has_error() {
        anyhow::bail!("Invalid pattern: parse error");
    }
    Ok(compiled_pattern)
}

fn search_path_with_prefilter(
    pattern: &Pattern,
    lang: SupportLang,
    path: &Path,
    prefilter_enabled: bool,
) -> Result<Vec<AstMatch>> {
    if !path.exists() {
        anyhow::bail!("Path not found: {}", path.display());
    }

    let prefilter_literal = prefilter_enabled
        .then(|| extract_prefilter_literal(pattern))
        .flatten();

    if path.is_file() {
        return AstBackend::search_file_static(pattern, lang, prefilter_literal.as_deref(), path);
    }

    let files = collect_ast_search_files(path, lang)?;
    let per_file_matches: Result<Vec<Vec<AstMatch>>> = files
        .par_iter()
        .map(|file| {
            AstBackend::search_file_static(pattern, lang, prefilter_literal.as_deref(), file)
        })
        .collect();

    let mut matches = Vec::new();
    for file_matches in per_file_matches? {
        matches.extend(file_matches);
    }
    Ok(matches)
}

fn search_path_for_cli(
    pattern: &Pattern,
    lang: SupportLang,
    path: &Path,
    prefilter_enabled: bool,
) -> Result<Vec<AstCliFileMatches>> {
    if !path.exists() {
        anyhow::bail!("Path not found: {}", path.display());
    }

    let prefilter_literal = prefilter_enabled
        .then(|| extract_prefilter_literal(pattern))
        .flatten();

    if path.is_file() {
        let matches = AstBackend::search_file_for_cli_static(
            pattern,
            lang,
            prefilter_literal.as_deref(),
            path,
        )?;
        if matches.is_empty() {
            return Ok(Vec::new());
        }
        return Ok(vec![AstCliFileMatches {
            file: path.to_path_buf(),
            matches,
        }]);
    }

    let files = collect_ast_search_files(path, lang)?;
    let per_file_matches: Result<Vec<Vec<AstCliMatch>>> = files
        .par_iter()
        .map(|file| {
            AstBackend::search_file_for_cli_static(
                pattern,
                lang,
                prefilter_literal.as_deref(),
                file,
            )
        })
        .collect();

    Ok(files
        .into_iter()
        .zip(per_file_matches?)
        .filter_map(|(file, matches)| {
            (!matches.is_empty()).then_some(AstCliFileMatches { file, matches })
        })
        .collect())
}

/// Collects AST candidate files with ignore/type filters before parallel parsing.
fn collect_ast_search_files(path: &Path, lang: SupportLang) -> Result<Vec<PathBuf>> {
    if !path.exists() {
        anyhow::bail!("Path not found: {}", path.display());
    }

    if path.is_file() {
        return Ok(vec![path.to_path_buf()]);
    }

    let mut files: Vec<PathBuf> = build_ast_search_walk_builder(path, lang)?
        .build()
        .filter_map(|entry| entry.ok())
        .filter(|entry| entry.file_type().is_some_and(|kind| kind.is_file()))
        .map(|entry| entry.into_path())
        .collect();
    files.sort_unstable();
    Ok(files)
}

fn build_ast_search_walk_builder(path: &Path, lang: SupportLang) -> Result<WalkBuilder> {
    let mut builder = WalkBuilder::new(path);
    builder.hidden(true);
    builder.git_ignore(true);
    builder.threads(ast_search_walk_threads());
    builder.types(build_ast_search_types(lang)?);

    for ignore_name in [".ignore", ".gitignore", ".rgignore"] {
        let ignore_path = path.join(ignore_name);
        if ignore_path.is_file() {
            builder.add_ignore(ignore_path);
        }
    }

    Ok(builder)
}

fn build_ast_search_types(lang: SupportLang) -> Result<Types> {
    let mut builder = TypesBuilder::new();
    let (type_name, globs): (&str, &[&str]) = match lang {
        SupportLang::Python => (
            "tgpythonast",
            &[
                "*.py", "*.PY", "*.py3", "*.PY3", "*.pyi", "*.PYI", "*.pyw", "*.PYW", "*.bzl",
                "*.BZL",
            ],
        ),
        SupportLang::JavaScript => (
            "tgjavascriptast",
            &[
                "*.js", "*.JS", "*.jsx", "*.JSX", "*.cjs", "*.CJS", "*.mjs", "*.MJS",
            ],
        ),
        SupportLang::TypeScript => (
            "tgtypescriptast",
            &[
                "*.ts", "*.TS", "*.tsx", "*.TSX", "*.cts", "*.CTS", "*.mts", "*.MTS",
            ],
        ),
        SupportLang::Rust => ("tgrustast", &["*.rs", "*.RS"]),
        _ => anyhow::bail!("Unsupported language type filter"),
    };

    for glob in globs {
        builder
            .add(type_name, glob)
            .with_context(|| format!("failed to register AST file type glob '{glob}'"))?;
    }
    builder.select(type_name);
    builder
        .build()
        .context("failed to build AST file type filter")
}

fn ast_search_walk_threads() -> usize {
    std::thread::available_parallelism()
        .map(|count| count.get().min(12))
        .unwrap_or(1)
}

fn extract_prefilter_literal(pattern: &Pattern) -> Option<String> {
    let fixed = pattern.fixed_string();
    let candidate = fixed.trim();
    if candidate.is_empty() {
        return None;
    }
    if !candidate
        .chars()
        .any(|ch| ch.is_alphanumeric() || ch == '_')
    {
        return None;
    }
    Some(candidate.to_string())
}

fn load_search_source(file: &Path) -> Result<Option<String>> {
    let bytes = std::fs::read(file)
        .with_context(|| format!("failed to read source file {}", file.display()))?;
    if bytes.is_empty() || has_binary_bytes(&bytes) {
        return Ok(None);
    }
    let source = String::from_utf8(bytes)
        .map_err(|e| anyhow::anyhow!("invalid UTF-8 in {}: {e}", file.display()))?;
    Ok(Some(source))
}

fn has_binary_bytes(bytes: &[u8]) -> bool {
    bytes[..bytes.len().min(BINARY_SCAN_BYTES)].contains(&0)
}

fn load_rewrite_source(file: &Path) -> Result<Option<RewriteSource>> {
    let metadata = std::fs::metadata(file)
        .with_context(|| format!("failed to read metadata for {}", file.display()))?;
    let file_len = metadata.len();
    let planned_mtime_ns = metadata
        .modified()
        .with_context(|| format!("failed to read modified time for {}", file.display()))
        .and_then(|modified| system_time_to_unix_nanos(file, modified))?;

    if file_len > MAX_REWRITE_FILE_BYTES {
        eprintln!(
            "warning: skipping large file {} ({} bytes exceeds 100 MB rewrite limit)",
            file.display(),
            file_len
        );
        return Ok(None);
    }

    let bytes = std::fs::read(file)
        .with_context(|| format!("failed to read source file {}", file.display()))?;
    if has_binary_bytes(&bytes) {
        return Ok(None);
    }
    let original_source = String::from_utf8(bytes)
        .map_err(|e| anyhow::anyhow!("invalid UTF-8 in {}: {e}", file.display()))?;
    let bom_len = usize::from(original_source.as_bytes().starts_with(UTF8_BOM)) * UTF8_BOM.len();

    Ok(Some(RewriteSource {
        bom_len,
        original_source,
        planned_mtime_ns,
    }))
}

fn ensure_valid_utf8_range(source: &str, file: &Path, byte_range: &Range<usize>) -> Result<()> {
    if byte_range.start > byte_range.end || byte_range.end > source.len() {
        anyhow::bail!(
            "rewrite byte range {:?} is out of bounds for {} (len {})",
            byte_range,
            file.display(),
            source.len()
        );
    }

    if !source.is_char_boundary(byte_range.start) || !source.is_char_boundary(byte_range.end) {
        anyhow::bail!(
            "rewrite byte range {:?} does not align to UTF-8 boundaries in {}",
            byte_range,
            file.display()
        );
    }

    Ok(())
}

fn rewrite_source_with_edits(file: &Path, original: &str, edits: &[&RewriteEdit]) -> Result<String> {
    let mut result = String::with_capacity(original.len());
    let mut cursor = 0usize;
    for edit in edits {
        ensure_valid_utf8_range(original, file, &edit.byte_range)?;
        if edit.byte_range.start < cursor {
            anyhow::bail!(
                "overlapping edit in {}: byte {} < cursor {}",
                file.display(),
                edit.byte_range.start,
                cursor
            );
        }
        result.push_str(&original[cursor..edit.byte_range.start]);
        result.push_str(&edit.replacement_text);
        cursor = edit.byte_range.end;
    }
    result.push_str(&original[cursor..]);
    Ok(result)
}

fn apply_edit_set(edits: &[RewriteEdit]) -> Result<usize> {
    apply_edit_set_with_writer(edits, None, ApplyExecution::Parallel, &atomic_write_file)
}

fn apply_edit_set_with_writer<W>(
    edits: &[RewriteEdit],
    original_sources: Option<&HashMap<PathBuf, String>>,
    execution: ApplyExecution,
    write_file: &W,
) -> Result<usize>
where
    W: Fn(&Path, &[u8]) -> Result<()> + Sync,
{
    let files = group_edits_by_file(edits);
    ensure_files_not_stale(&files)?;

    let mut files_written = 0;
    let apply_file = |(file, file_edits): &(&Path, Vec<&RewriteEdit>)| {
        let original_source = original_sources
            .and_then(|sources| sources.get(*file))
            .map(String::as_str);
        apply_edits_to_file_with_writer(file, file_edits, original_source, write_file)
    };

    match execution {
        ApplyExecution::Parallel => {
            let results: Vec<Result<()>> = files.par_iter().map(apply_file).collect();
            for result in results {
                result?;
                files_written += 1;
            }
        }
        #[cfg(test)]
        ApplyExecution::Sequential => {
            for file in &files {
                apply_file(file)?;
                files_written += 1;
            }
        }
    }

    Ok(files_written)
}

fn verify_rewrite_edits(edits: &[RewriteEdit]) -> Result<VerifyResult> {
    let mut mismatches = Vec::new();

    for (file, file_edits) in group_edits_by_file(edits) {
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
                    actual: format!(
                        "<out of bounds: file len {}, expected end {}>",
                        content.len(),
                        adjusted_end
                    ),
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

    let verified = edits.len() - mismatches.len();
    Ok(VerifyResult {
        total_edits: edits.len(),
        verified,
        mismatches,
    })
}

fn generate_diff_for_edits(edits: &[RewriteEdit]) -> Result<String> {
    let mut output = String::new();
    for (file, file_edits) in group_edits_by_file(edits) {
        let original = std::fs::read_to_string(file)
            .with_context(|| format!("failed to read {}", file.display()))?;
        let rewritten = rewrite_source_with_edits(file, &original, &file_edits)?;

        let orig_lines: Vec<&str> = original.lines().collect();
        let new_lines: Vec<&str> = rewritten.lines().collect();

        let display_path = file.display();
        output.push_str(&format!("--- a/{display_path}\n"));
        output.push_str(&format!("+++ b/{display_path}\n"));
        emit_unified_hunks(&orig_lines, &new_lines, 3, &mut output);
    }

    Ok(output)
}

fn ensure_files_not_stale(files: &[(&Path, Vec<&RewriteEdit>)]) -> Result<()> {
    let results: Vec<Result<()>> = files
        .par_iter()
        .map(|(file, file_edits)| ensure_file_not_stale(file, file_edits))
        .collect();

    for result in results {
        result?;
    }

    Ok(())
}

fn ensure_file_not_stale(file: &Path, edits: &[&RewriteEdit]) -> Result<()> {
    let planned_mtime_ns = planned_mtime_ns_for_file(file, edits)?;
    let current_mtime_ns = file_mtime_ns(file)?;

    if current_mtime_ns != planned_mtime_ns {
        anyhow::bail!(
            "stale file modified since planning: {} (planned mtime ns {}, current mtime ns {})",
            file.display(),
            planned_mtime_ns,
            current_mtime_ns
        );
    }

    Ok(())
}

fn planned_mtime_ns_for_file(file: &Path, edits: &[&RewriteEdit]) -> Result<u64> {
    let first = edits
        .first()
        .with_context(|| format!("no edits supplied for {}", file.display()))?;
    let planned_mtime_ns = first.planned_mtime_ns;

    if edits
        .iter()
        .any(|edit| edit.planned_mtime_ns != planned_mtime_ns)
    {
        anyhow::bail!("inconsistent planned mtime metadata for {}", file.display());
    }

    Ok(planned_mtime_ns)
}

fn file_mtime_ns(file: &Path) -> Result<u64> {
    let modified = std::fs::metadata(file)
        .with_context(|| format!("failed to read metadata for {}", file.display()))?
        .modified()
        .with_context(|| format!("failed to read modified time for {}", file.display()))?;
    system_time_to_unix_nanos(file, modified)
}

fn system_time_to_unix_nanos(file: &Path, timestamp: SystemTime) -> Result<u64> {
    let duration = timestamp.duration_since(UNIX_EPOCH).with_context(|| {
        format!(
            "file modified time for {} predates the Unix epoch",
            file.display()
        )
    })?;

    u64::try_from(duration.as_nanos()).with_context(|| {
        format!(
            "file modified time for {} exceeds supported nanosecond range",
            file.display()
        )
    })
}

fn atomic_write_file(file: &Path, contents: &[u8]) -> Result<()> {
    atomic_write_file_with_hook(file, contents, |_| Ok(()))
}

fn atomic_write_file_with_hook<F>(file: &Path, contents: &[u8], before_rename: F) -> Result<()>
where
    F: FnOnce(&Path) -> Result<()>,
{
    let (temp_path, mut temp_file) = create_temp_file(file)?;

    let write_result: Result<()> = (|| {
        temp_file
            .write_all(contents)
            .with_context(|| format!("failed to write temp file {}", temp_path.display()))?;
        temp_file
            .flush()
            .with_context(|| format!("failed to flush temp file {}", temp_path.display()))?;
        // Intentionally skip sync_all()/sync_data() here: same-directory renames are atomic on
        // NTFS, and the metadata update is journaled, so temp-file + rename still preserves
        // all-old-or-all-new visibility. A per-file fsync is a large Windows hot-path cost and
        // would not flush the parent directory entry anyway; this matches sg's plain write path.
        drop(temp_file);

        before_rename(&temp_path)?;

        std::fs::rename(&temp_path, file).with_context(|| {
            format!(
                "failed to rename temp file {} to {}",
                temp_path.display(),
                file.display()
            )
        })
    })();

    if let Err(err) = write_result {
        let _ = std::fs::remove_file(&temp_path);
        return Err(err);
    }

    Ok(())
}

fn create_temp_file(file: &Path) -> Result<(PathBuf, std::fs::File)> {
    let directory = file
        .parent()
        .with_context(|| format!("{} has no parent directory", file.display()))?;

    for _ in 0..64 {
        let temp_path = directory.join(format!(
            ".tg_tmp_{:x}_{:x}",
            std::process::id(),
            next_temp_nonce()
        ));

        match OpenOptions::new()
            .write(true)
            .create_new(true)
            .open(&temp_path)
        {
            Ok(temp_file) => return Ok((temp_path, temp_file)),
            Err(err) if err.kind() == ErrorKind::AlreadyExists => continue,
            Err(err) => {
                return Err(err).with_context(|| {
                    format!("failed to create temp file alongside {}", file.display())
                });
            }
        }
    }

    anyhow::bail!(
        "failed to allocate unique temp file alongside {} after 64 attempts",
        file.display()
    )
}

fn next_temp_nonce() -> u128 {
    let counter = TEMP_FILE_COUNTER.fetch_add(1, Ordering::Relaxed) as u128;
    let nanos = SystemTime::now()
        .duration_since(UNIX_EPOCH)
        .map(|duration| duration.as_nanos())
        .unwrap_or(counter);
    nanos ^ counter
}

fn assign_edit_ids(edits: &mut [RewriteEdit]) {
    for (i, edit) in edits.iter_mut().enumerate() {
        let stem = edit
            .file
            .file_name()
            .and_then(|n| n.to_str())
            .unwrap_or("unknown");
        edit.id = format!(
            "e{i:04}:{stem}:{}-{}",
            edit.byte_range.start, edit.byte_range.end
        );
    }
}

fn sort_rewrite_edits(edits: &mut [RewriteEdit]) {
    if edits.len() >= PAR_SORT_EDIT_THRESHOLD {
        edits.par_sort_by(|a, b| {
            a.file
                .cmp(&b.file)
                .then(a.byte_range.start.cmp(&b.byte_range.start))
        });
    } else {
        edits.sort_by(|a, b| {
            a.file
                .cmp(&b.file)
                .then(a.byte_range.start.cmp(&b.byte_range.start))
        });
    }
}

fn compile_batch_rewrites_by_lang(
    rewrites: &[BatchRewriteRule],
) -> Result<BTreeMap<String, Vec<CompiledBatchRewrite>>> {
    let mut compiled_by_lang: BTreeMap<String, Vec<CompiledBatchRewrite>> = BTreeMap::new();
    for rewrite in rewrites {
        let language = resolve_language(&rewrite.lang)?;
        let compiled_pattern = Pattern::try_new(&rewrite.pattern, language)
            .map_err(|err| anyhow::anyhow!("Invalid pattern: {err}"))?;
        if compiled_pattern.has_error() {
            anyhow::bail!("Invalid pattern: parse error");
        }
        compiled_by_lang
            .entry(rewrite.lang.clone())
            .or_default()
            .push(CompiledBatchRewrite {
                replacement: rewrite.replacement.clone(),
                pattern: compiled_pattern,
            });
    }

    Ok(compiled_by_lang)
}

fn collect_per_file_rewrite_outcomes(
    file_results: Vec<Result<Option<PerFileRewriteOutcome>>>,
) -> Result<(Vec<RewriteEdit>, Vec<OverlapRejection>)> {
    let mut edits = Vec::new();
    let mut rejected_overlaps = Vec::new();

    for result in file_results {
        if let Some(outcome) = result? {
            edits.extend(outcome.edits);
            rejected_overlaps.extend(outcome.rejected_overlaps);
        }
    }

    Ok((edits, rejected_overlaps))
}

fn sort_overlap_rejections(rejections: &mut [OverlapRejection]) {
    rejections.sort_by(|left, right| {
        left.file
            .cmp(&right.file)
            .then(left.edit_a.start.cmp(&right.edit_a.start))
            .then(left.edit_b.start.cmp(&right.edit_b.start))
    });
}

fn group_edits_by_file(edits: &[RewriteEdit]) -> Vec<(&Path, Vec<&RewriteEdit>)> {
    let mut edits_by_file: HashMap<&Path, Vec<&RewriteEdit>> = HashMap::new();
    for edit in edits {
        edits_by_file
            .entry(edit.file.as_path())
            .or_default()
            .push(edit);
    }

    let mut files: Vec<(&Path, Vec<&RewriteEdit>)> = edits_by_file.into_iter().collect();
    files.sort_by(|(left, _), (right, _)| left.cmp(right));
    for (_, file_edits) in &mut files {
        file_edits.sort_by(|left, right| left.byte_range.start.cmp(&right.byte_range.start));
    }
    files
}

fn apply_edits_to_file_with_writer<W>(
    file: &Path,
    edits: &[&RewriteEdit],
    original_source: Option<&str>,
    write_file: &W,
) -> Result<()>
where
    W: Fn(&Path, &[u8]) -> Result<()> + Sync,
{
    let loaded_source;
    let original = match original_source {
        Some(original_source) => original_source,
        None => {
            loaded_source = std::fs::read_to_string(file)
                .with_context(|| format!("failed to read {}", file.display()))?;
            &loaded_source
        }
    };

    let rewritten = rewrite_source_with_edits(file, original, edits)?;
    write_file(file, rewritten.as_bytes())
}

fn apply_prepared_rewrite_file(
    file: PathBuf,
    original_source: String,
    valid_edits: Vec<RewriteEdit>,
    rejected_overlaps: Vec<OverlapRejection>,
) -> Result<Option<PerFileRewriteOutcome>> {
    if valid_edits.is_empty() && rejected_overlaps.is_empty() {
        return Ok(None);
    }

    if !valid_edits.is_empty() {
        let file_edits: Vec<&RewriteEdit> = valid_edits.iter().collect();
        ensure_file_not_stale(&file, &file_edits)?;
        apply_edits_to_file_with_writer(
            &file,
            &file_edits,
            Some(original_source.as_str()),
            &atomic_write_file,
        )?;
    }

    Ok(Some(PerFileRewriteOutcome {
        edits: valid_edits,
        rejected_overlaps,
    }))
}

fn validate_no_overlaps(edits: Vec<RewriteEdit>) -> (Vec<RewriteEdit>, Vec<OverlapRejection>) {
    let mut valid = Vec::new();
    let mut rejected = Vec::new();

    let mut prev_range_by_file: HashMap<PathBuf, Range<usize>> = HashMap::new();

    for edit in edits {
        if let Some(prev_range) = prev_range_by_file.get(&edit.file) {
            if edit.byte_range.start < prev_range.end {
                rejected.push(OverlapRejection {
                    file: edit.file.clone(),
                    edit_a: prev_range.clone(),
                    edit_b: edit.byte_range.clone(),
                    reason: format!(
                        "edit at byte {} overlaps with previous edit ending at byte {}",
                        edit.byte_range.start, prev_range.end
                    ),
                });
                continue;
            }
        }
        prev_range_by_file.insert(edit.file.clone(), edit.byte_range.clone());
        valid.push(edit);
    }

    (valid, rejected)
}

fn validate_batch_no_overlaps(
    edits: Vec<RewriteEdit>,
) -> (Vec<RewriteEdit>, Vec<OverlapRejection>) {
    let mut edits_by_file: BTreeMap<PathBuf, Vec<RewriteEdit>> = BTreeMap::new();
    for edit in edits {
        edits_by_file
            .entry(edit.file.clone())
            .or_default()
            .push(edit);
    }

    let mut valid = Vec::new();
    let mut rejected = Vec::new();

    for (file, file_edits) in edits_by_file {
        let mut file_rejections = Vec::new();

        for pair in file_edits.windows(2) {
            let edit_a = &pair[0];
            let edit_b = &pair[1];
            if edit_b.byte_range.start < edit_a.byte_range.end {
                file_rejections.push(OverlapRejection {
                    file: file.clone(),
                    edit_a: edit_a.byte_range.clone(),
                    edit_b: edit_b.byte_range.clone(),
                    reason: format!(
                        "batch rewrite overlap prevents applying edits to {}",
                        file.display()
                    ),
                });
            }
        }

        if file_rejections.is_empty() {
            valid.extend(file_edits);
        } else {
            rejected.extend(file_rejections);
        }
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
        .filter(|entry| entry.file_type().is_some_and(|ft| ft.is_file()))
        .map(|entry| entry.into_path())
        .filter(|file| file_matches_language(file, lang))
        .collect();

    Ok(files)
}

fn collect_batch_source_files(path: &Path, lang: SupportLang) -> Result<Vec<PathBuf>> {
    if !path.exists() {
        anyhow::bail!("Path not found: {}", path.display());
    }

    if path.is_file() {
        if file_matches_language(path, lang) {
            return Ok(vec![path.to_path_buf()]);
        }
        return Ok(Vec::new());
    }

    collect_source_files(path, lang)
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

#[cfg(test)]
mod tests {
    use super::*;
    use std::fs;
    use std::path::PathBuf;
    use std::sync::atomic::{AtomicUsize, Ordering as AtomicOrdering};
    use tempfile::tempdir;

    fn temp_entries(dir: &Path) -> Vec<String> {
        fs::read_dir(dir)
            .unwrap()
            .map(|entry| entry.unwrap().file_name().to_string_lossy().into_owned())
            .collect()
    }

    #[test]
    fn atomic_write_replaces_target_and_cleans_temp_files() {
        let dir = tempdir().unwrap();
        let file_path = dir.path().join("fixture.py");
        fs::write(&file_path, "before\n").unwrap();

        atomic_write_file(&file_path, b"after\n").unwrap();

        assert_eq!(fs::read_to_string(&file_path).unwrap(), "after\n");
        assert!(
            temp_entries(dir.path())
                .into_iter()
                .all(|entry| !entry.starts_with(".tg_tmp_")),
            "temporary files should be removed after success"
        );
    }

    #[test]
    fn atomic_write_cleans_temp_files_after_failure() {
        let dir = tempdir().unwrap();
        let file_path = dir.path().join("fixture.py");
        fs::write(&file_path, "before\n").unwrap();

        let error = atomic_write_file_with_hook(&file_path, b"after\n", |_| {
            anyhow::bail!("injected rename failure")
        })
        .unwrap_err();

        assert!(
            error.to_string().contains("injected rename failure"),
            "unexpected error: {error:#}"
        );
        assert_eq!(fs::read_to_string(&file_path).unwrap(), "before\n");
        assert!(
            temp_entries(dir.path())
                .into_iter()
                .all(|entry| !entry.starts_with(".tg_tmp_")),
            "temporary files should be removed after failure"
        );
    }

    #[test]
    fn apply_edit_set_stops_after_injected_failure_and_leaves_remaining_files_unmodified() {
        let dir = tempdir().unwrap();
        let first_file = dir.path().join("a.py");
        let second_file = dir.path().join("b.py");
        let third_file = dir.path().join("c.py");
        fs::write(&first_file, "def a(x): return x\n").unwrap();
        fs::write(&second_file, "def b(y): return y\n").unwrap();
        fs::write(&third_file, "def c(z): return z\n").unwrap();

        let backend = AstBackend::new();
        let plan = backend
            .plan_rewrites(
                "def $F($$$ARGS): return $EXPR",
                "lambda $$$ARGS: $EXPR",
                "python",
                dir.path().to_str().unwrap(),
            )
            .unwrap();

        let apply_attempts = AtomicUsize::new(0);
        let error = apply_edit_set_with_writer(
            &plan.edits,
            None,
            ApplyExecution::Sequential,
            &|file, contents| {
                if apply_attempts.fetch_add(1, AtomicOrdering::SeqCst) == 1 {
                    anyhow::bail!("injected apply failure for {}", file.display());
                }
                atomic_write_file(file, contents)
            },
        )
        .unwrap_err();

        let message = format!("{error:#}");
        assert!(
            message.contains("injected apply failure"),
            "unexpected error: {message}"
        );
        assert_eq!(fs::read_to_string(&first_file).unwrap(), "lambda x: x\n");
        assert_eq!(fs::read_to_string(&second_file).unwrap(), "def b(y): return y\n");
        assert_eq!(fs::read_to_string(&third_file).unwrap(), "def c(z): return z\n");
        assert!(
            temp_entries(dir.path())
                .into_iter()
                .all(|entry| !entry.starts_with(".tg_tmp_")),
            "temporary files should be removed after mid-apply failure"
        );
    }

    fn write_search_fixture(
        dir: &std::path::Path,
        relative_path: &str,
        contents: impl AsRef<[u8]>,
    ) -> PathBuf {
        let path = dir.join(relative_path);
        if let Some(parent) = path.parent() {
            fs::create_dir_all(parent).unwrap();
        }
        fs::write(&path, contents).unwrap();
        path
    }

    fn matched_files(matches: &[AstMatch]) -> Vec<PathBuf> {
        matches.iter().map(|entry| entry.file.clone()).collect()
    }

    fn search_type_matches(path: &std::path::Path, lang: SupportLang) -> bool {
        build_ast_search_types(lang)
            .unwrap()
            .matched(path, false)
            .is_whitelist()
    }

    #[test]
    fn search_prefilter_false_negative_three_category() {
        let dir = tempdir().unwrap();
        let matching = write_search_fixture(dir.path(), "matching.py", "def keep(x): return x\n");
        write_search_fixture(
            dir.path(),
            "literal_only.py",
            "def keep(x):\n    value = return_value(x)\n    return_value = x\n",
        );
        write_search_fixture(dir.path(), "neither.py", "print('nothing to see here')\n");

        let backend = AstBackend::new();
        let matches = backend
            .search(
                "def $F($$$ARGS): return $EXPR",
                "python",
                dir.path().to_str().unwrap(),
            )
            .unwrap();

        assert_eq!(matched_files(&matches), vec![matching]);
    }

    #[test]
    fn search_respects_gitignore_for_parallel_walk() {
        let dir = tempdir().unwrap();
        write_search_fixture(dir.path(), ".gitignore", "*.generated.py\n");
        let visible = write_search_fixture(dir.path(), "visible.py", "def keep(x): return x\n");
        write_search_fixture(
            dir.path(),
            "ignored.generated.py",
            "def ignored(x): return x\n",
        );

        let backend = AstBackend::new();
        let matches = backend
            .search(
                "def $F($$$ARGS): return $EXPR",
                "python",
                dir.path().to_str().unwrap(),
            )
            .unwrap();

        assert_eq!(matched_files(&matches), vec![visible]);
    }

    #[test]
    fn collect_ast_search_files_applies_type_filters_and_gitignore() {
        let dir = tempdir().unwrap();
        let nested = dir.path().join("nested");
        fs::create_dir_all(&nested).unwrap();
        write_search_fixture(dir.path(), ".gitignore", "*.generated.py\n");
        let uppercase = write_search_fixture(dir.path(), "UPPER.PY", "def upper(x): return x\n");
        let pyw = write_search_fixture(dir.path(), "window.pyw", "def gui(x): return x\n");
        let nested_py = write_search_fixture(&nested, "nested.py", "def nested(x): return x\n");
        write_search_fixture(
            dir.path(),
            "ignored.generated.py",
            "def ignored(x): return x\n",
        );
        write_search_fixture(dir.path(), "notes.txt", "def text(x): return x\n");
        write_search_fixture(dir.path(), "no_extension", "def plain(x): return x\n");

        let files = collect_ast_search_files(dir.path(), SupportLang::Python).unwrap();
        let mut expected = vec![nested_py, uppercase, pyw];
        expected.sort_unstable();

        assert_eq!(files, expected);
    }

    #[test]
    fn search_type_filter_handles_python_edge_cases() {
        let dir = tempdir().unwrap();
        let uppercase = write_search_fixture(dir.path(), "UPPER.PY", "def upper(x): return x\n");
        let pyw = write_search_fixture(dir.path(), "window.pyw", "def gui(x): return x\n");
        write_search_fixture(dir.path(), "no_extension", "def plain(x): return x\n");
        write_search_fixture(dir.path(), "notes.txt", "def text(x): return x\n");

        let backend = AstBackend::new();
        let matches = backend
            .search(
                "def $F($$$ARGS): return $EXPR",
                "python",
                dir.path().to_str().unwrap(),
            )
            .unwrap();

        assert_eq!(matched_files(&matches), vec![uppercase, pyw]);
        assert!(!search_type_matches(
            std::path::Path::new(""),
            SupportLang::Python
        ));
    }

    #[test]
    fn search_prefilter_matches_results_without_prefilter() {
        let dir = tempdir().unwrap();
        write_search_fixture(dir.path(), "keep.py", "def keep(x): return x\n");
        write_search_fixture(
            dir.path(),
            "literal_only.py",
            "def literal_only(x):\n    return_value = x\n    return return_value\n",
        );
        write_search_fixture(dir.path(), "skip.py", "print('no return literal here')\n");

        let backend = AstBackend::new();
        let with_prefilter = backend
            .search_with_prefilter(
                "def $F($$$ARGS): return $EXPR",
                "python",
                dir.path().to_str().unwrap(),
                true,
            )
            .unwrap();
        let without_prefilter = backend
            .search_with_prefilter(
                "def $F($$$ARGS): return $EXPR",
                "python",
                dir.path().to_str().unwrap(),
                false,
            )
            .unwrap();

        assert_eq!(with_prefilter, without_prefilter);
    }

    #[test]
    fn search_prefilter_falls_back_for_wildcard_only_patterns() {
        let pattern = compile_ast_pattern("$F($$$ARGS)", SupportLang::Python).unwrap();
        assert_eq!(extract_prefilter_literal(&pattern), None);

        let dir = tempdir().unwrap();
        write_search_fixture(dir.path(), "calls.py", "first(a)\nsecond(b, c)\n");

        let backend = AstBackend::new();
        let with_prefilter = backend
            .search_with_prefilter("$F($$$ARGS)", "python", dir.path().to_str().unwrap(), true)
            .unwrap();
        let without_prefilter = backend
            .search_with_prefilter("$F($$$ARGS)", "python", dir.path().to_str().unwrap(), false)
            .unwrap();

        assert_eq!(with_prefilter, without_prefilter);
        assert!(!with_prefilter.is_empty());
    }

    #[test]
    fn search_for_cli_matches_backend_search_projection() {
        let dir = tempdir().unwrap();
        let alpha = write_search_fixture(
            dir.path(),
            "alpha.py",
            "def keep(x): return x\ndef also_keep(y): return y\n",
        );
        let beta = write_search_fixture(dir.path(), "beta.py", "def beta(z): return z\n");

        let backend = AstBackend::new();
        let full_matches = backend
            .search(
                "def $F($$$ARGS): return $EXPR",
                "python",
                dir.path().to_str().unwrap(),
            )
            .unwrap();
        let cli_matches = backend
            .search_for_cli(
                "def $F($$$ARGS): return $EXPR",
                "python",
                dir.path().to_str().unwrap(),
            )
            .unwrap();

        let projected_full: Vec<(PathBuf, usize, String)> = full_matches
            .into_iter()
            .map(|matched| (matched.file, matched.line, matched.matched_text))
            .collect();
        let projected_cli: Vec<(PathBuf, usize, String)> = cli_matches
            .into_iter()
            .flat_map(|file_matches| {
                let file = file_matches.file;
                file_matches
                    .matches
                    .into_iter()
                    .map(move |matched| (file.clone(), matched.line, matched.matched_text))
            })
            .collect();

        assert_eq!(projected_cli, projected_full);
        assert_eq!(projected_cli[0].0, alpha);
        assert_eq!(projected_cli[2].0, beta);
    }

    #[test]
    fn search_skips_binary_files_without_error() {
        let dir = tempdir().unwrap();
        write_search_fixture(
            dir.path(),
            "binary.py",
            b"def hidden(x): return x\0garbage\n",
        );
        let text = write_search_fixture(dir.path(), "text.py", "def shown(x): return x\n");

        let backend = AstBackend::new();
        let matches = backend
            .search(
                "def $F($$$ARGS): return $EXPR",
                "python",
                dir.path().to_str().unwrap(),
            )
            .unwrap();

        assert_eq!(matched_files(&matches), vec![text]);
    }

    #[test]
    fn search_handles_empty_dir_and_no_matching_files() {
        let empty_dir = tempdir().unwrap();
        let no_match_dir = tempdir().unwrap();
        write_search_fixture(no_match_dir.path(), "notes.txt", "def text(x): return x\n");

        let backend = AstBackend::new();
        let empty_matches = backend
            .search(
                "def $F($$$ARGS): return $EXPR",
                "python",
                empty_dir.path().to_str().unwrap(),
            )
            .unwrap();
        let no_match_results = backend
            .search(
                "def $F($$$ARGS): return $EXPR",
                "python",
                no_match_dir.path().to_str().unwrap(),
            )
            .unwrap();

        assert!(empty_matches.is_empty());
        assert!(no_match_results.is_empty());
    }

    #[test]
    fn search_handles_utf8_bom_prefixed_python_files() {
        let dir = tempdir().unwrap();
        let path = dir.path().join("bom.py");
        let mut bytes = UTF8_BOM.to_vec();
        bytes.extend_from_slice(b"def bom(x): return x\n");
        fs::write(&path, bytes).unwrap();

        let backend = AstBackend::new();
        let matches = backend
            .search(
                "def $F($$$ARGS): return $EXPR",
                "python",
                dir.path().to_str().unwrap(),
            )
            .unwrap();

        assert_eq!(matches.len(), 1);
        assert_eq!(matches[0].file, path);
        assert_eq!(matches[0].line, 1);
        assert_eq!(matches[0].candidate.byte_range.start, UTF8_BOM.len());
    }

    #[test]
    fn search_handles_large_python_files() {
        let dir = tempdir().unwrap();
        let large_file = dir.path().join("large.py");
        let mut source = String::from("def target(x): return x\n");
        while source.len() <= 11 * 1024 * 1024 {
            source.push_str("print('filler line to keep the parser busy')\n");
        }
        fs::write(&large_file, source).unwrap();

        let backend = AstBackend::new();
        let matches = backend
            .search(
                "def $F($$$ARGS): return $EXPR",
                "python",
                dir.path().to_str().unwrap(),
            )
            .unwrap();

        assert_eq!(matches.len(), 1);
        assert_eq!(matches[0].file, large_file);
    }

    #[test]
    fn search_results_are_sorted_deterministically() {
        let dir = tempdir().unwrap();
        let a_path = write_search_fixture(
            dir.path(),
            "a.py",
            "def first(x): return x\ndef second(y): return y\n",
        );
        let z_path = write_search_fixture(dir.path(), "z.py", "def last(z): return z\n");

        let backend = AstBackend::new();
        let first = backend
            .search(
                "def $F($$$ARGS): return $EXPR",
                "python",
                dir.path().to_str().unwrap(),
            )
            .unwrap();
        let second = backend
            .search(
                "def $F($$$ARGS): return $EXPR",
                "python",
                dir.path().to_str().unwrap(),
            )
            .unwrap();

        assert_eq!(first, second);
        assert_eq!(first.len(), 3);
        assert_eq!(first[0].file, a_path);
        assert_eq!(first[0].line, 1);
        assert_eq!(first[1].file, a_path);
        assert_eq!(first[1].line, 2);
        assert_eq!(first[2].file, z_path);
        assert_eq!(first[2].line, 1);
    }

    #[test]
    fn verify_uses_byte_offsets_after_atomic_apply() {
        let dir = tempdir().unwrap();
        let file_path = dir.path().join("fixture.py");
        fs::write(
            &file_path,
            "def f(x): return x\ndef g(a, b, c): return a + b + c\n",
        )
        .unwrap();

        let backend = AstBackend::new();
        let plan = backend
            .plan_and_apply(
                "def $F($$$ARGS): return $EXPR",
                "lambda $$$ARGS: $EXPR",
                "python",
                file_path.to_str().unwrap(),
            )
            .unwrap();

        let verification = plan.verify(&backend).unwrap();
        assert_eq!(verification.total_edits, 2);
        assert_eq!(verification.verified, 2);
        assert!(verification.mismatches.is_empty());
    }
}
