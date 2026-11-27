use crate::apply_report::{collect_group_results, partition_apply_results};
use anyhow::{Context, Result};
use ast_grep_core::{
    matcher::NodeMatch, meta_var::MetaVariable, tree_sitter::LanguageExt, Node, Pattern,
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
use std::ops::Range;
use std::path::{Path, PathBuf};
use std::sync::Arc;
use std::time::{SystemTime, UNIX_EPOCH};

mod anchored_apply;

const UTF8_BOM: &[u8; 3] = b"\xEF\xBB\xBF";
const BINARY_SCAN_BYTES: usize = 8192;
/// Maximum source-file size read by native rewrite planning and apply paths.
pub const MAX_REWRITE_FILE_BYTES: u64 = 100 * 1024 * 1024;
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
    target: Option<Arc<crate::safe_write::AnchoredTarget>>,
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
pub struct AstMetaVariableCapture {
    pub text: String,
    pub byte_range: Range<usize>,
}

#[derive(Debug, Clone, PartialEq, Eq, Default)]
pub struct AstMetaVariables {
    pub single: BTreeMap<String, AstMetaVariableCapture>,
    pub multi: BTreeMap<String, Vec<AstMetaVariableCapture>>,
}

#[derive(Debug, Clone, PartialEq, Eq)]
pub struct AstMatch {
    pub file: PathBuf,
    pub line: usize,
    pub matched_text: String,
    pub candidate: EditCandidate,
    pub meta_variables: AstMetaVariables,
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
    /// Byte offset of the matched AST node's start (M16 F1: the union
    /// dedupe identity for composite rules is the SPAN, so two distinct nodes
    /// on the same line each count).
    pub start_byte: usize,
    /// Byte offset one past the matched AST node's end (see `start_byte`).
    pub end_byte: usize,
    pub matched_text: String,
}

#[derive(Debug, Clone, PartialEq, Eq)]
pub struct AstCliFileMatches {
    pub file: PathBuf,
    pub matches: Vec<AstCliMatch>,
}

#[derive(Clone)]
struct CompiledAstPattern {
    pattern: Pattern,
    rust_function_signature_fallback: Option<RustFunctionSignatureFallback>,
}

#[derive(Clone)]
struct RustFunctionSignatureFallback {
    visibility: Option<String>,
    name_pattern: Pattern,
    parameters_pattern: Option<Pattern>,
    raw_params: String,
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

    pub fn search_many_for_cli(
        &self,
        pattern: &str,
        lang: &str,
        files: &[PathBuf],
    ) -> Result<Vec<AstCliFileMatches>> {
        let language = resolve_language(lang)?;
        let compiled_pattern = compile_ast_pattern(pattern, language)?;

        let prefilter_literal = extract_prefilter_literal(&compiled_pattern.pattern);

        let per_file_matches: Result<Vec<Vec<AstCliMatch>>> = files
            .par_iter()
            .map(|file| {
                AstBackend::search_file_for_cli_static(
                    &compiled_pattern,
                    language,
                    prefilter_literal.as_deref(),
                    file,
                )
            })
            .collect();

        Ok(files
            .iter()
            .zip(per_file_matches?)
            .filter_map(|(file, matches)| {
                (!matches.is_empty()).then_some(AstCliFileMatches {
                    file: file.clone(),
                    matches,
                })
            })
            .collect())
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

    #[doc(hidden)]
    pub fn apply_rewrites_with_targets(
        plan: &RewritePlan,
        targets: &HashMap<PathBuf, std::sync::Arc<crate::safe_write::AnchoredTarget>>,
    ) -> Result<usize> {
        anchored_apply::apply_edit_set_with_targets(&plan.edits, targets)
    }

    pub fn apply_batch_rewrites(plan: &BatchRewritePlan) -> Result<usize> {
        apply_edit_set(&plan.edits)
    }

    #[doc(hidden)]
    pub fn apply_batch_rewrites_with_targets(
        plan: &BatchRewritePlan,
        targets: &HashMap<PathBuf, std::sync::Arc<crate::safe_write::AnchoredTarget>>,
    ) -> Result<usize> {
        anchored_apply::apply_edit_set_with_targets(&plan.edits, targets)
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

        let (mut valid_edits, mut rejected_overlaps) =
            collect_per_file_rewrite_outcomes(&files, file_results, &mut Vec::new())?;
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
        let mut written_so_far: Vec<PathBuf> = Vec::new();

        for (lang_name, rules) in &compiled_by_lang {
            let language = resolve_language(lang_name)?;
            let files = collect_batch_source_files(Path::new(path), language)?;
            total_files_scanned += files.len();

            let file_results: Vec<Result<Option<PerFileRewriteOutcome>>> = files
                .par_iter()
                .map(|file| Self::plan_and_apply_single_batch_rewrite(rules, language, file))
                .collect();

            let (mut file_edits, mut file_rejections) =
                collect_per_file_rewrite_outcomes(&files, file_results, &mut written_so_far)?;
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
            .map(|file| Self::plan_file_rewrites_prepared(pattern, replacement, lang, file, false))
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
                .map(|file| Self::plan_file_batch_rewrites_prepared(rules, language, file, false))
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
        let Some(mut prepared) =
            Self::plan_file_rewrites_prepared(pattern, replacement, lang, file, true)?
        else {
            return Ok(None);
        };
        sort_rewrite_edits(&mut prepared.edits);
        let (valid_edits, rejected_overlaps) = validate_no_overlaps(prepared.edits);
        let target = prepared
            .target
            .take()
            .context("prepared rewrite target was not retained")?;
        anchored_apply::apply_prepared_rewrite_file(
            target,
            prepared.file,
            prepared.original_source,
            valid_edits,
            rejected_overlaps,
        )
    }

    fn plan_and_apply_single_batch_rewrite(
        rewrites: &[CompiledBatchRewrite],
        lang: SupportLang,
        file: &Path,
    ) -> Result<Option<PerFileRewriteOutcome>> {
        let Some(mut prepared) =
            Self::plan_file_batch_rewrites_prepared(rewrites, lang, file, true)?
        else {
            return Ok(None);
        };
        sort_rewrite_edits(&mut prepared.edits);
        let (valid_edits, rejected_overlaps) = validate_batch_no_overlaps(prepared.edits);
        let target = prepared
            .target
            .take()
            .context("prepared rewrite target was not retained")?;
        anchored_apply::apply_prepared_rewrite_file(
            target,
            prepared.file,
            prepared.original_source,
            valid_edits,
            rejected_overlaps,
        )
    }

    fn plan_file_rewrites_prepared(
        pattern: &Pattern,
        replacement: &str,
        lang: SupportLang,
        file: &Path,
        retain_target: bool,
    ) -> Result<Option<PreparedRewriteFile>> {
        let target = Arc::new(crate::safe_write::AnchoredTarget::open(file)?);
        let Some(rewrite_source) = load_rewrite_source(file, &target)? else {
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
            target: retain_target.then_some(target),
            original_source: rewrite_source.original_source,
            edits,
        }))
    }

    fn plan_file_batch_rewrites_prepared(
        rewrites: &[CompiledBatchRewrite],
        lang: SupportLang,
        file: &Path,
        retain_target: bool,
    ) -> Result<Option<PreparedRewriteFile>> {
        let target = Arc::new(crate::safe_write::AnchoredTarget::open(file)?);
        let Some(rewrite_source) = load_rewrite_source(file, &target)? else {
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
            target: retain_target.then_some(target),
            original_source: rewrite_source.original_source,
            edits,
        }))
    }

    fn search_file_static(
        pattern: &CompiledAstPattern,
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

        for matched in ast.root().find_all(pattern.pattern.clone()) {
            let ls = line_starts.get_or_insert_with(|| build_line_starts(&source));
            matches.push(build_match(&file_owned, &source, ls, matched));
        }

        if let Some(fallback) = &pattern.rust_function_signature_fallback {
            let ls = line_starts.get_or_insert_with(|| build_line_starts(&source));
            for fallback_match in
                collect_rust_function_signature_matches(fallback, ast.root(), file, &source, ls)
            {
                if !matches.iter().any(|existing| {
                    existing.candidate.byte_range == fallback_match.candidate.byte_range
                }) {
                    matches.push(fallback_match);
                }
            }
        }

        Ok(matches)
    }

    fn search_file_for_cli_static(
        pattern: &CompiledAstPattern,
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

        for matched in ast.root().find_all(pattern.pattern.clone()) {
            let ls = line_starts.get_or_insert_with(|| build_line_starts(&source));
            let byte_range = matched.range();
            matches.push(AstCliMatch {
                line: line_number_for_byte(ls, byte_range.start),
                start_byte: byte_range.start,
                end_byte: byte_range.end,
                matched_text: matched.text().to_string(),
            });
        }

        if let Some(fallback) = &pattern.rust_function_signature_fallback {
            let ls = line_starts.get_or_insert_with(|| build_line_starts(&source));
            for fallback_match in
                collect_rust_function_signature_matches(fallback, ast.root(), file, &source, ls)
            {
                if !matches.iter().any(|existing| {
                    existing.line == fallback_match.line
                        && existing.matched_text == fallback_match.matched_text
                }) {
                    matches.push(AstCliMatch {
                        line: fallback_match.line,
                        start_byte: fallback_match.candidate.byte_range.start,
                        end_byte: fallback_match.candidate.byte_range.end,
                        matched_text: fallback_match.matched_text,
                    });
                }
            }
        }

        Ok(matches)
    }
}

mod helpers;
mod rewrite;
pub use helpers::resolve_language;
use helpers::*;
use rewrite::*;
