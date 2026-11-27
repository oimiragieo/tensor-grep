#![allow(unused_imports)]
use super::*;

pub(super) fn load_rewrite_source(
    file: &Path,
    target: &crate::safe_write::AnchoredTarget,
) -> Result<Option<RewriteSource>> {
    let metadata = target
        .metadata()
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

    let bytes = target
        .read_bounded(MAX_REWRITE_FILE_BYTES)
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

pub(super) fn ensure_valid_utf8_range(
    source: &str,
    file: &Path,
    byte_range: &Range<usize>,
) -> Result<()> {
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

pub(super) fn rewrite_source_with_edits(
    file: &Path,
    original: &str,
    edits: &[&RewriteEdit],
) -> Result<String> {
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

pub(super) fn apply_edit_set(edits: &[RewriteEdit]) -> Result<usize> {
    anchored_apply::apply_edit_set(edits)
}

#[cfg(test)]
pub(super) fn apply_edit_set_with_writer<W>(
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

    apply_grouped_edit_files_with_reader(
        &files,
        original_sources,
        execution,
        &|file| {
            std::fs::read_to_string(file)
                .with_context(|| format!("failed to read {}", file.display()))
        },
        write_file,
    )
}

pub(super) fn apply_grouped_edit_files_with_reader<R, W>(
    files: &[(&Path, Vec<&RewriteEdit>)],
    original_sources: Option<&HashMap<PathBuf, String>>,
    execution: ApplyExecution,
    read_source: &R,
    write_file: &W,
) -> Result<usize>
where
    R: Fn(&Path) -> Result<String> + Sync,
    W: Fn(&Path, &[u8]) -> Result<()> + Sync,
{
    let apply_file = |(file, file_edits): &(&Path, Vec<&RewriteEdit>)| {
        let original_source = original_sources
            .and_then(|sources| sources.get(*file))
            .map(String::as_str);
        apply_edits_to_file_with_reader(file, file_edits, original_source, read_source, write_file)
    };

    match execution {
        ApplyExecution::Parallel => {
            let outcomes: Vec<(PathBuf, Result<()>)> = files
                .par_iter()
                .map(|item| (item.0.to_path_buf(), apply_file(item)))
                .collect();
            Ok(partition_apply_results(outcomes, |_| true)?.len())
        }
        #[cfg(test)]
        ApplyExecution::Sequential => {
            for file in files {
                apply_file(file)?;
            }
            Ok(files.len())
        }
    }
}

pub(super) fn verify_rewrite_edits(edits: &[RewriteEdit]) -> Result<VerifyResult> {
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

pub(super) fn generate_diff_for_edits(edits: &[RewriteEdit]) -> Result<String> {
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

#[cfg(test)]
pub(super) fn ensure_files_not_stale(files: &[(&Path, Vec<&RewriteEdit>)]) -> Result<()> {
    let results: Vec<Result<()>> = files
        .par_iter()
        .map(|(file, file_edits)| ensure_file_not_stale(file, file_edits))
        .collect();

    for result in results {
        result?;
    }

    Ok(())
}

#[cfg(test)]
pub(super) fn ensure_file_not_stale(file: &Path, edits: &[&RewriteEdit]) -> Result<()> {
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

pub(super) fn planned_mtime_ns_for_file(file: &Path, edits: &[&RewriteEdit]) -> Result<u64> {
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

#[cfg(test)]
pub(super) fn file_mtime_ns(file: &Path) -> Result<u64> {
    let modified = std::fs::metadata(file)
        .with_context(|| format!("failed to read metadata for {}", file.display()))?
        .modified()
        .with_context(|| format!("failed to read modified time for {}", file.display()))?;
    system_time_to_unix_nanos(file, modified)
}

pub(super) fn system_time_to_unix_nanos(file: &Path, timestamp: SystemTime) -> Result<u64> {
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

#[cfg(test)]
pub(super) fn atomic_write_file(file: &Path, contents: &[u8]) -> Result<()> {
    atomic_write_file_with_hook(file, contents, |_| Ok(()))
}

/// Overwrite a file directly for the one-shot plan+apply fast path.
#[cfg(test)]
pub(super) fn direct_write_file(file: &Path, contents: &[u8]) -> Result<()> {
    // The one-shot plan_and_apply path prioritizes sg-parity throughput over the temp-file rename
    // contract used by explicit plan+apply. We still reject stale files before writing, but once a
    // file has been planned from its current on-disk contents we overwrite it directly so Windows
    // rewrite apply is dominated by actual rewrite work instead of temp-file creation + rename.
    // On NTFS, in-place overwrites are journaled at the filesystem level, so after a crash readers
    // observe either the previous committed bytes or the completed overwrite rather than a half-
    // renamed temp file sequence. That trade-off matches sg's std::fs::write() behavior.
    //
    // That trade-off is about ATOMICITY (no temp-file + rename), and it is deliberate. It is NOT
    // a decision to follow symlinks: a bare `std::fs::write` here wrote THROUGH a git-tracked
    // symlink to its target, so `--apply` could overwrite a file outside the repo. Matching sg's
    // atomicity choice does not require inheriting that. The guard costs one open() flag and
    // leaves the direct-overwrite fast path intact.
    crate::safe_write::AnchoredTarget::open(file)?
        .write_direct(contents)
        .with_context(|| format!("failed to overwrite {}", file.display()))
}

#[cfg(test)]
pub(super) fn atomic_write_file_with_hook<F>(
    file: &Path,
    contents: &[u8],
    before_rename: F,
) -> Result<()>
where
    F: FnOnce(&Path) -> Result<()>,
{
    crate::safe_write::AnchoredTarget::open(file)?.write_atomic_with_hook(contents, before_rename)
}

pub(super) fn assign_edit_ids(edits: &mut [RewriteEdit]) {
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

pub(super) fn sort_rewrite_edits(edits: &mut [RewriteEdit]) {
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

pub(super) fn compile_batch_rewrites_by_lang(
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

/// `written_so_far`: files an EARLIER language group already wrote (J-04), named on failure.
pub(super) fn collect_per_file_rewrite_outcomes(
    files: &[PathBuf],
    file_results: Vec<Result<Option<PerFileRewriteOutcome>>>,
    written_so_far: &mut Vec<PathBuf>,
) -> Result<(Vec<RewriteEdit>, Vec<OverlapRejection>)> {
    let wrote = |o: &Option<PerFileRewriteOutcome>| o.as_ref().is_some_and(|o| !o.edits.is_empty());
    let group = collect_group_results(files, file_results, written_so_far, wrote)?;
    let (mut edits, mut rejected_overlaps) = (Vec::new(), Vec::new());
    for outcome in group.into_iter().flatten() {
        edits.extend(outcome.edits);
        rejected_overlaps.extend(outcome.rejected_overlaps);
    }

    Ok((edits, rejected_overlaps))
}

pub(super) fn sort_overlap_rejections(rejections: &mut [OverlapRejection]) {
    rejections.sort_by(|left, right| {
        left.file
            .cmp(&right.file)
            .then(left.edit_a.start.cmp(&right.edit_a.start))
            .then(left.edit_b.start.cmp(&right.edit_b.start))
    });
}

pub(super) fn group_edits_by_file(edits: &[RewriteEdit]) -> Vec<(&Path, Vec<&RewriteEdit>)> {
    let mut edits_by_file: HashMap<&Path, Vec<&RewriteEdit>> = HashMap::new();
    for edit in edits {
        edits_by_file
            .entry(edit.file.as_path())
            .or_default()
            .push(edit);
    }

    let mut files: Vec<(&Path, Vec<&RewriteEdit>)> = edits_by_file.into_iter().collect();
    files.sort_by_key(|(left, _)| *left);
    for (_, file_edits) in &mut files {
        file_edits.sort_by_key(|left| left.byte_range.start);
    }
    files
}

pub(super) fn apply_edits_to_file_with_writer<W>(
    file: &Path,
    edits: &[&RewriteEdit],
    original_source: Option<&str>,
    write_file: &W,
) -> Result<()>
where
    W: Fn(&Path, &[u8]) -> Result<()> + Sync,
{
    apply_edits_to_file_with_reader(
        file,
        edits,
        original_source,
        &|file| {
            std::fs::read_to_string(file)
                .with_context(|| format!("failed to read {}", file.display()))
        },
        write_file,
    )
}

pub(super) fn apply_edits_to_file_with_reader<R, W>(
    file: &Path,
    edits: &[&RewriteEdit],
    original_source: Option<&str>,
    read_source: &R,
    write_file: &W,
) -> Result<()>
where
    R: Fn(&Path) -> Result<String> + Sync,
    W: Fn(&Path, &[u8]) -> Result<()> + Sync,
{
    let loaded_source;
    let original = match original_source {
        Some(original_source) => original_source,
        None => {
            loaded_source = read_source(file)?;
            &loaded_source
        }
    };

    let rewritten = rewrite_source_with_edits(file, original, edits)?;
    write_file(file, rewritten.as_bytes())
}

pub(super) fn validate_no_overlaps(
    edits: Vec<RewriteEdit>,
) -> (Vec<RewriteEdit>, Vec<OverlapRejection>) {
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

pub(super) fn validate_batch_no_overlaps(
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
