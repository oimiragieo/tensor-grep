#![allow(unused_imports)]
use super::*;

pub(super) fn search_file_collect_matches_with_searcher(
    config: &NativeSearchConfig,
    matcher: &RegexMatcher,
    path: &Path,
    searcher: &mut Searcher,
) -> Result<FileSearchResult> {
    let path_buf = path.to_path_buf();
    let mut matches = Vec::new();
    let mut sink = BinaryAwareSink::new(Bytes(|line_number, line| {
        let trimmed_line = strip_native_line_terminator(line);
        let rendered_bytes = render_matched_line_bytes(config, trimmed_line)
            .map_err(sink_io_error)?
            .into_owned();
        matches.push(NativeSearchMatch {
            path: path_buf.clone(),
            line_number: Some(line_number),
            raw: rendered_bytes,
        });
        Ok(true)
    }));
    search_opened_path(searcher, matcher, path, &mut sink, "native search failed")?;

    let binary_detected = sink.saw_binary();
    let binary_byte_offset = sink.binary_byte_offset();
    let binary_match_detected = binary_file_matches_pattern(matcher, path, binary_detected)?;
    if binary_detected {
        matches.clear();
    }

    Ok(FileSearchResult {
        match_count: matches.len(),
        matches,
        binary_detected,
        binary_match_detected,
        binary_byte_offset,
    })
}

pub(super) fn search_file_ndjson_with_searcher(
    config: &NativeSearchConfig,
    matcher: &RegexMatcher,
    path: &Path,
    searcher: &mut Searcher,
) -> Result<FileSearchResult> {
    let mut matches = Vec::new();
    let path_buf = path.to_path_buf();
    let search_path = display_search_path(&config.paths);
    let mut sink = BinaryAwareSink::new(Bytes(|line_number, line| {
        let trimmed_line = strip_native_line_terminator(line);
        let rendered_bytes = render_matched_line_bytes(config, trimmed_line)
            .map_err(sink_io_error)?
            .into_owned();
        let matched = NativeSearchMatch {
            path: path_buf.clone(),
            line_number: Some(line_number),
            raw: rendered_bytes,
        };
        matches.push(matched);
        Ok(true)
    }));
    search_opened_path(
        searcher,
        matcher,
        path,
        &mut sink,
        "native NDJSON search failed",
    )?;

    let binary_detected = sink.saw_binary();
    let binary_byte_offset = sink.binary_byte_offset();
    let binary_match_detected = binary_file_matches_pattern(matcher, path, binary_detected)?;
    if binary_detected {
        matches.clear();
    } else {
        for matched in &matches {
            emit_ndjson_match(config, &search_path, matched).with_context(|| {
                format!(
                    "failed to write native NDJSON output for {}",
                    path.display()
                )
            })?;
        }
    }

    Ok(FileSearchResult {
        match_count: matches.len(),
        matches,
        binary_detected,
        binary_match_detected,
        binary_byte_offset,
    })
}

pub(super) fn search_file_count_with_searcher(
    matcher: &RegexMatcher,
    path: &Path,
    searcher: &mut Searcher,
) -> Result<FileSearchResult> {
    let mut match_count = 0usize;
    let mut sink = BinaryAwareSink::new(Bytes(|_, _| {
        match_count = match_count.saturating_add(1);
        Ok(true)
    }));
    search_opened_path(
        searcher,
        matcher,
        path,
        &mut sink,
        "native count output search failed",
    )?;

    let binary_detected = sink.saw_binary();
    let binary_byte_offset = sink.binary_byte_offset();
    let binary_match_detected = binary_file_matches_pattern(matcher, path, binary_detected)?;
    if binary_detected {
        match_count = 0;
    }

    Ok(FileSearchResult {
        matches: Vec::new(),
        match_count,
        binary_detected,
        binary_match_detected,
        binary_byte_offset,
    })
}

pub(super) fn search_file_json(
    config: &NativeSearchConfig,
    matcher: &RegexMatcher,
    path: &Path,
) -> Result<FileSearchResult> {
    let mut searcher = build_searcher(config, true);
    search_file_collect_matches_with_searcher(config, matcher, path, &mut searcher)
}

pub(super) fn emit_count_output_from_matches(
    config: &NativeSearchConfig,
    path: &Path,
    count: usize,
) -> Result<()> {
    let mut bytes = Vec::new();
    append_count_output_bytes(&mut bytes, config, path, count)?;
    config.output_target.write_all(&bytes)
}

pub(super) fn append_count_output_bytes(
    bytes: &mut Vec<u8>,
    config: &NativeSearchConfig,
    path: &Path,
    count: usize,
) -> Result<()> {
    if config.with_filename {
        writeln!(bytes, "{}:{count}", path.display())?;
    } else {
        writeln!(bytes, "{count}")?;
    }
    Ok(())
}

pub(super) fn can_stream_plain_matches(config: &NativeSearchConfig) -> bool {
    config.before_context == 0 && config.after_context == 0 && !config.only_matching
}

/// Writes a matched line to plain-text (non-JSON) output. `text` is the exact bytes to emit
/// for the line's content -- written raw via `extend_from_slice`, never through `Display`/
/// `write!`, so a genuine trailing `\r` (CRLF content) or non-UTF-8 byte survives byte-for-byte
/// instead of being re-encoded (task #266).
pub(super) fn append_standard_match_bytes(
    bytes: &mut Vec<u8>,
    config: &NativeSearchConfig,
    path_display: &str,
    line_number: u64,
    text: &[u8],
) -> Result<()> {
    if config.with_filename && config.line_number {
        write!(bytes, "{path_display}:{line_number}:")?;
    } else if config.with_filename {
        write!(bytes, "{path_display}:")?;
    } else if config.line_number {
        write!(bytes, "{line_number}:")?;
    }
    bytes.extend_from_slice(text);
    bytes.push(b'\n');
    Ok(())
}

pub(super) fn native_match_from_sink(path: &Path, mat: &SinkMatch<'_>) -> NativeSearchMatch {
    NativeSearchMatch {
        path: path.to_path_buf(),
        line_number: mat.line_number(),
        raw: strip_native_line_terminator(mat.bytes()).to_vec(),
    }
}

pub(super) fn emit_json_matches(config: &NativeSearchConfig, stats: &SearchStats) -> Result<()> {
    let proof_fields = gpu_proof_fields(
        &config.requested_gpu_device_ids,
        config.routing_backend,
        config.sidecar_used,
    );
    let mut match_counts_by_file: BTreeMap<String, usize> = BTreeMap::new();
    for matched in &stats.matches {
        let path = matched.path.to_string_lossy().into_owned();
        *match_counts_by_file.entry(path).or_insert(0) += 1;
    }
    let matched_file_paths = match_counts_by_file.keys().cloned().collect::<Vec<_>>();
    let (path_was_defaulted, scope_note) = defaulted_scope_fields(
        config.path_was_implicit,
        stats.total_matches.saturating_add(stats.binary_match_files),
    );
    let payload = NativeJsonOutput {
        version: JSON_OUTPUT_VERSION,
        routing_backend: config.routing_backend,
        routing_reason: config.routing_reason,
        sidecar_used: config.sidecar_used,
        requested_gpu_device_ids: config.requested_gpu_device_ids.clone(),
        routing_gpu_device_ids: Vec::new(),
        gpu_evidence_status: proof_fields.gpu_evidence_status,
        gpu_proof: proof_fields.gpu_proof,
        native_gpu_unavailable: proof_fields.native_gpu_unavailable,
        not_gpu_proof_reason: proof_fields.not_gpu_proof_reason,
        query: &config.pattern,
        path: display_search_path(&config.paths),
        total_files: stats.matched_files,
        total_matches: stats.total_matches,
        binary_match_files: (stats.binary_match_files > 0).then_some(stats.binary_match_files),
        matched_file_paths,
        match_counts_by_file,
        matches: stats
            .matches
            .iter()
            .map(native_match_to_json)
            .collect::<Result<Vec<_>>>()?,
        // Task #276 slice B2. Emitted ONLY when the walk actually skipped something, so a
        // complete envelope stays byte-identical to every prior release.
        //
        // The class is `unreadable_path` and not one of the budget causes on purpose: a walk
        // error is the ONE value in the closed set that is NOT budget-remediable. No
        // `--max-repo-files` or `--deadline` value makes a permission-denied subtree readable,
        // and `docs/CONTRACTS.md` says so explicitly. Labelling it `scan_limit` or `deadline`
        // would hand the reader a knob that cannot help -- the wrong-knob defect #283 already
        // cost us once.
        result_incomplete: (stats.walk_errors > 0).then_some(true),
        incomplete_reason_class: (stats.walk_errors > 0).then_some("unreadable_path"),
        incomplete_paths_count: (stats.walk_errors > 0).then_some(stats.walk_errors),
        path_was_defaulted,
        scope_note,
    };

    let mut bytes = serde_json::to_vec(&payload)?;
    bytes.push(b'\n');
    config.output_target.write_all(&bytes)
}

pub(super) fn emit_ndjson_match(
    config: &NativeSearchConfig,
    search_path: &str,
    matched: &NativeSearchMatch,
) -> Result<()> {
    let mut bytes = Vec::new();
    append_ndjson_match_bytes(&mut bytes, config, search_path, matched)?;
    config.output_target.write_all(&bytes)
}

pub(super) fn append_ndjson_match_bytes(
    bytes: &mut Vec<u8>,
    config: &NativeSearchConfig,
    search_path: &str,
    matched: &NativeSearchMatch,
) -> Result<()> {
    let line = native_match_line_number(matched)?;
    let file = matched.path.to_string_lossy().into_owned();
    let proof_fields = gpu_proof_fields(
        &config.requested_gpu_device_ids,
        config.routing_backend,
        config.sidecar_used,
    );
    let (text, text_bytes) = native_json_text_fields(&matched.raw);
    let payload = NativeNdjsonMatch {
        version: JSON_OUTPUT_VERSION,
        routing_backend: config.routing_backend,
        routing_reason: config.routing_reason,
        sidecar_used: config.sidecar_used,
        requested_gpu_device_ids: config.requested_gpu_device_ids.clone(),
        routing_gpu_device_ids: Vec::new(),
        gpu_evidence_status: proof_fields.gpu_evidence_status,
        gpu_proof: proof_fields.gpu_proof,
        native_gpu_unavailable: proof_fields.native_gpu_unavailable,
        not_gpu_proof_reason: proof_fields.not_gpu_proof_reason,
        query: &config.pattern,
        path: search_path,
        file: &file,
        line,
        text,
        bytes: text_bytes,
    };

    let mut encoded = serde_json::to_vec(&payload)?;
    encoded.push(b'\n');
    bytes.extend_from_slice(&encoded);
    Ok(())
}

pub(super) fn native_match_to_json(matched: &NativeSearchMatch) -> Result<NativeJsonMatch> {
    let (text, bytes) = native_json_text_fields(&matched.raw);
    Ok(NativeJsonMatch {
        file: matched.path.to_string_lossy().into_owned(),
        line: native_match_line_number(matched)?,
        text: text.map(str::to_string),
        bytes,
    })
}

pub(super) fn native_match_line_number(matched: &NativeSearchMatch) -> Result<usize> {
    let line_number = matched
        .line_number
        .ok_or_else(|| anyhow!("native search match missing line number"))?;
    usize::try_from(line_number).context("native search line number overflowed usize")
}

pub(super) fn display_search_path(paths: &[PathBuf]) -> String {
    paths
        .iter()
        .map(|path| path.display().to_string())
        .collect::<Vec<_>>()
        .join(",")
}
