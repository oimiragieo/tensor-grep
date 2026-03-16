use anyhow::{Context, Result};
use ast_grep_core::{meta_var::MetaVariable, matcher::NodeMatch, tree_sitter::LanguageExt, Pattern};
use ast_grep_language::SupportLang;
use ignore::WalkBuilder;
use rayon::prelude::*;
use serde::{Deserialize, Serialize};
use std::collections::{BTreeMap, HashMap};
use std::convert::TryFrom;
use std::fs::File;
use std::fs::OpenOptions;
use std::io::{ErrorKind, Read, Write};
use std::ops::Range;
use std::path::{Path, PathBuf};
use std::sync::atomic::{AtomicU64, Ordering};
use std::time::{SystemTime, UNIX_EPOCH};

static TEMP_FILE_COUNTER: AtomicU64 = AtomicU64::new(0);
const UTF8_BOM: &[u8; 3] = b"\xEF\xBB\xBF";
const BINARY_SCAN_BYTES: usize = 8192;
const MAX_REWRITE_FILE_BYTES: u64 = 100 * 1024 * 1024;

struct RewriteSource {
    bom_len: usize,
    source: String,
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
        format!("{}:{}:{}", self.file.display(), self.line, self.matched_text)
    }
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

    pub fn plan_batch_rewrites(&self, rewrites: &[BatchRewriteRule], path: &str) -> Result<BatchRewritePlan> {
        if rewrites.is_empty() {
            anyhow::bail!("batch rewrite config requires at least one rewrite rule");
        }

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

        let search_root = Path::new(path);
        let mut total_files_scanned = 0usize;
        let mut edits = Vec::new();

        for (lang_name, rules) in &compiled_by_lang {
            let language = resolve_language(lang_name)?;
            let files = collect_batch_source_files(search_root, language)?;
            total_files_scanned += files.len();

            let file_results: Vec<Result<Vec<RewriteEdit>>> = files
                .par_iter()
                .map(|file| Self::plan_file_batch_rewrites(rules, language, file))
                .collect();

            for result in file_results {
                edits.extend(result?);
            }
        }

        edits.sort_by(|a, b| a.file.cmp(&b.file).then(a.byte_range.start.cmp(&b.byte_range.start)));
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

        apply_edit_set(&valid_edits)?;

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

    pub fn plan_and_apply_batch(&self, rewrites: &[BatchRewriteRule], path: &str) -> Result<BatchRewritePlan> {
        let plan = self.plan_batch_rewrites(rewrites, path)?;
        apply_edit_set(&plan.edits)?;
        Ok(plan)
    }

    fn plan_file_rewrites(
        pattern: &Pattern,
        replacement: &str,
        lang: SupportLang,
        file: &Path,
    ) -> Result<Vec<RewriteEdit>> {
        let planned_mtime_ns = file_mtime_ns(file)?;
        let Some(rewrite_source) = load_rewrite_source(file)? else {
            return Ok(Vec::new());
        };
        let RewriteSource { bom_len, source } = rewrite_source;
        if source.is_empty() {
            return Ok(Vec::new());
        }

        let ast = lang.ast_grep(&source);
        let file_owned = file.to_path_buf();
        let mut line_starts: Option<Vec<usize>> = None;
        let mut edits = Vec::new();

        for matched in ast.root().find_all(pattern.clone()) {
            let ls = line_starts.get_or_insert_with(|| build_line_starts(&source));
            let byte_range = matched.range();
            ensure_valid_utf8_range(&source, file, &byte_range)?;
            let original_text = matched.text().to_string();
            let metavar_env = extract_metavar_env(&source, matched.get_env());
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

        Ok(edits)
    }

    fn plan_file_batch_rewrites(
        rewrites: &[CompiledBatchRewrite],
        lang: SupportLang,
        file: &Path,
    ) -> Result<Vec<RewriteEdit>> {
        let planned_mtime_ns = file_mtime_ns(file)?;
        let Some(rewrite_source) = load_rewrite_source(file)? else {
            return Ok(Vec::new());
        };
        let RewriteSource { bom_len, source } = rewrite_source;
        if source.is_empty() {
            return Ok(Vec::new());
        }

        let ast = lang.ast_grep(&source);
        let file_owned = file.to_path_buf();
        let mut line_starts: Option<Vec<usize>> = None;
        let mut edits = Vec::new();

        for rewrite in rewrites {
            for matched in ast.root().find_all(rewrite.pattern.clone()) {
                let ls = line_starts.get_or_insert_with(|| build_line_starts(&source));
                let byte_range = matched.range();
                ensure_valid_utf8_range(&source, file, &byte_range)?;
                let original_text = matched.text().to_string();
                let metavar_env = extract_metavar_env(&source, matched.get_env());
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

fn load_rewrite_source(file: &Path) -> Result<Option<RewriteSource>> {
    let metadata = std::fs::metadata(file)
        .with_context(|| format!("failed to read metadata for {}", file.display()))?;
    let file_len = metadata.len();

    if file_len > MAX_REWRITE_FILE_BYTES {
        eprintln!(
            "warning: skipping large file {} ({} bytes exceeds 100 MB rewrite limit)",
            file.display(),
            file_len
        );
        return Ok(None);
    }

    let mut prefix_reader = File::open(file)
        .with_context(|| format!("failed to open source file {}", file.display()))?;
    let mut prefix = vec![0; BINARY_SCAN_BYTES.min(file_len as usize)];
    if !prefix.is_empty() {
        prefix_reader
            .read_exact(&mut prefix)
            .with_context(|| format!("failed to read source file prefix {}", file.display()))?;
    }
    if prefix.contains(&0) {
        return Ok(None);
    }

    let bytes = std::fs::read(file)
        .with_context(|| format!("failed to read source file {}", file.display()))?;
    let bom_len = usize::from(bytes.starts_with(UTF8_BOM)) * UTF8_BOM.len();
    let source = String::from_utf8(bytes[bom_len..].to_vec())
        .map_err(|e| anyhow::anyhow!("invalid UTF-8 in {}: {e}", file.display()))?;

    Ok(Some(RewriteSource { bom_len, source }))
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

fn apply_edits_to_file(file: &Path, edits: &[&RewriteEdit]) -> Result<()> {
    ensure_file_not_stale(file, edits)?;

    let original = std::fs::read_to_string(file)
        .with_context(|| format!("failed to read {}", file.display()))?;
    let mut result = String::with_capacity(original.len());
    let mut cursor = 0usize;
    for edit in edits {
        ensure_valid_utf8_range(&original, file, &edit.byte_range)?;
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
    atomic_write_file(file, result.as_bytes())
}

fn apply_edit_set(edits: &[RewriteEdit]) -> Result<usize> {
    let mut edits_by_file: HashMap<&Path, Vec<&RewriteEdit>> = HashMap::new();
    for edit in edits {
        edits_by_file.entry(edit.file.as_path()).or_default().push(edit);
    }

    let files: Vec<(&Path, &Vec<&RewriteEdit>)> = edits_by_file.iter().map(|(k, v)| (*k, v)).collect();
    ensure_files_not_stale(&files)?;
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

fn verify_rewrite_edits(edits: &[RewriteEdit]) -> Result<VerifyResult> {
    let mut edits_by_file: HashMap<&Path, Vec<&RewriteEdit>> = HashMap::new();
    for edit in edits {
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

    let verified = edits.len() - mismatches.len();
    Ok(VerifyResult {
        total_edits: edits.len(),
        verified,
        mismatches,
    })
}

fn generate_diff_for_edits(edits: &[RewriteEdit]) -> Result<String> {
    let mut edits_by_file: HashMap<&Path, Vec<&RewriteEdit>> = HashMap::new();
    for edit in edits {
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

fn ensure_files_not_stale(files: &[(&Path, &Vec<&RewriteEdit>)]) -> Result<()> {
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
        anyhow::bail!(
            "inconsistent planned mtime metadata for {}",
            file.display()
        );
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
        temp_file
            .sync_all()
            .with_context(|| format!("failed to sync temp file {}", temp_path.display()))?;
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

        match OpenOptions::new().write(true).create_new(true).open(&temp_path) {
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
        let stem = edit.file.file_name()
            .and_then(|n| n.to_str())
            .unwrap_or("unknown");
        edit.id = format!("e{i:04}:{stem}:{}-{}", edit.byte_range.start, edit.byte_range.end);
    }
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

fn validate_batch_no_overlaps(edits: Vec<RewriteEdit>) -> (Vec<RewriteEdit>, Vec<OverlapRejection>) {
    let mut edits_by_file: BTreeMap<PathBuf, Vec<RewriteEdit>> = BTreeMap::new();
    for edit in edits {
        edits_by_file.entry(edit.file.clone()).or_default().push(edit);
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
    fn verify_uses_byte_offsets_after_atomic_apply() {
        let dir = tempdir().unwrap();
        let file_path = dir.path().join("fixture.py");
        fs::write(&file_path, "def f(x): return x\ndef g(a, b, c): return a + b + c\n").unwrap();

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
