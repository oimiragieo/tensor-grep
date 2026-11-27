#![allow(unused_imports, dead_code)]
use crate::native_multi_pattern::collect_native_multi_pattern_matches;
use crate::validation_rollback::{
    collect_validation_rollback_snapshots, emit_rollback_status,
    restore_validation_rollback_snapshots, retained_validation_targets, ValidationRollbackSummary,
};
use anyhow::Context;
#[cfg(feature = "cuda")]
use clap::ValueEnum;
use clap::{Args, CommandFactory, Parser, Subcommand};
use hmac::{Hmac, Mac};
use tensor_grep_rs::rg_passthrough::generated_scan_dir_names;
// Bug #88/#480/#100: `implicit_search_walk_exceeds_ceiling` (the WALK-ceiling probe) moved to
// `rg_passthrough.rs` (a library module, not this binary crate root) -- see the breadcrumb
// comment above `parse_early_ripgrep_args`. `WalkBuilder`'s only remaining consumer in THIS file
// is `count_search_corpus_bytes`, which is cuda-gated, so both imports are cuda-gated now.
use crate::main_env::env_flag_enabled;
use crate::search_files::format_broad_generated_scan_error;
use crate::search_flag_registry::{
    raw_args_contain_any_flag, search_args_contain_any_flag, SEARCH_OPTION_FIRST_FLAGS,
    SEARCH_PYTHON_PASSTHROUGH_FLAGS,
};
#[cfg(feature = "cuda")]
use ignore::overrides::OverrideBuilder;
#[cfg(feature = "cuda")]
use ignore::WalkBuilder;
use process_control::{ChildExt, Control};
use serde::{Deserialize, Serialize};
use sha2::{Digest, Sha256};
use std::collections::{BTreeMap, BTreeSet};
use std::env;
use std::ffi::{OsStr, OsString};
#[cfg(feature = "cuda")]
use std::fs;
use std::io::{self, Read, Write};
use std::path::{Path, PathBuf};
use std::process::Command;
use std::sync::{Mutex, OnceLock};
use std::time::Instant;
use std::time::{Duration, SystemTime, UNIX_EPOCH};
use tensor_grep_rs::backend_ast::{
    AstBackend, AstMatch, AstMetaVariables, BatchRewritePlan, BatchRewriteRule,
};
use tensor_grep_rs::broken_pipe::error_chain_has_broken_pipe;
use tensor_grep_rs::crossover::{
    run_crossover_calibration, skip_signal_payload, write_crossover_config, NoCudaBuildError,
};
use tensor_grep_rs::exit_codes::exit_with_run_error;
#[cfg(feature = "cuda")]
use tensor_grep_rs::gpu_native::{
    benchmark_cuda_graph_search_paths, benchmark_pageable_transfer_throughput,
    benchmark_pinned_transfer_throughput, enumerate_cuda_devices, gpu_native_search_paths_multi,
    probe_device_allocation, GpuNativeSearchConfig, GpuNativeSearchStats, GpuPipelineStats,
};
use tensor_grep_rs::index::TrigramIndex;
use tensor_grep_rs::index_mmap::{mmap_index_path, LoadedIndex, MmapIndex};
use tensor_grep_rs::native_search::{
    defaulted_scope_fields, emit_index_binary_match_warning, native_json_text_fields,
    native_search_pattern_compiles, run_native_search, smart_case_pattern_is_case_insensitive,
    NativeSearchConfig, SearchStats,
};
use tensor_grep_rs::python_sidecar::{
    execute_python_passthrough_command, execute_python_passthrough_command_captured,
    execute_python_passthrough_command_with_stdin, execute_sidecar_command, SidecarError,
};
use tensor_grep_rs::rg_passthrough::{
    apply_last_ignore_wins, execute_ripgrep_pcre2_version, execute_ripgrep_search,
    execute_ripgrep_type_list, extract_search_tail_with_matches, implicit_search_paths,
    is_unbounded_implicit_search_walk_refusal, ripgrep_is_available, RipgrepSearchArgs,
};
use tensor_grep_rs::routing::{
    gpu_proof_fields, native_can_serve_plain_text, plain_text_native_cheap_checks_pass,
    plain_text_native_flag_token_is_allowed, route_search, BackendSelection, IndexRoutingState,
    PlainTextNativeRequest, RoutingDecision, SearchRoutingCalibration, SearchRoutingConfig,
};
use tensor_grep_rs::search_error::{
    self, exit_json_search_runtime_error_if_needed, exit_structured_search_error_if_needed,
    handle_search_runtime_error, refuse_structured_pcre2,
};

// Root identity also appears in positional help generated from this binary.
use crate::cli_app::*;
use crate::*;

/// Task 276 task 6: the ONE predicate for "this walk did not finish".
///
/// `None` means "this route observed no walk of its own, so it cannot report a count" -- NEVER
/// "complete". Only a `Some(count)` above zero is an affirmative incompleteness claim.
///
/// Both the envelope fields below AND the exit code read this, so a route cannot say
/// `result_incomplete: true` in its payload and then exit 0. That divergence was the shape of
/// task 6: the disclosure and the exit code were derived independently, so one twin could ship
/// without the other.
pub(crate) fn walk_was_incomplete(incomplete_paths: Option<usize>) -> bool {
    matches!(incomplete_paths, Some(count) if count > 0)
}

/// Task 276: turn a walk-error count into the three envelope fields.
///
/// ONE place, so the `--json` envelope and the `--ndjson` summary cannot drift -- the #276
/// family is a long record of one route disclosing while its twin stayed silent.
pub(crate) fn incomplete_envelope_fields(
    incomplete_paths: Option<usize>,
) -> (Option<bool>, Option<&'static str>, Option<usize>) {
    if walk_was_incomplete(incomplete_paths) {
        return (Some(true), Some("unreadable_path"), incomplete_paths);
    }
    (None, None, None)
}

#[allow(clippy::too_many_arguments)]
pub(crate) fn emit_json_search_results(
    decision: RoutingDecision,
    pattern: &str,
    path: &str,
    requested_gpu_device_ids: &[i32],
    matches: Vec<SearchMatchJson>,
    incomplete_paths: Option<usize>,
    path_was_implicit: bool,
    binary_match_files: Option<usize>,
    ast_context: Option<(&str, Option<&str>)>,
) -> anyhow::Result<()> {
    let (ast_lang, ast_selector) = ast_context.map_or((None, None), |(l, s)| (Some(l), s));
    let proof_fields = gpu_proof_fields(
        requested_gpu_device_ids,
        decision.routing_backend(),
        decision.sidecar_used(),
    );
    let mut match_counts_by_file = std::collections::BTreeMap::<String, usize>::new();
    for matched in &matches {
        *match_counts_by_file
            .entry(matched.file.clone())
            .or_insert(0) += 1;
    }
    let matched_file_paths = match_counts_by_file.keys().cloned().collect::<Vec<_>>();
    let (result_incomplete, incomplete_reason_class, incomplete_paths_count) =
        incomplete_envelope_fields(incomplete_paths);
    let (path_was_defaulted, scope_note) = defaulted_scope_fields(
        path_was_implicit,
        matches
            .len()
            .saturating_add(binary_match_files.unwrap_or_default()),
    );
    let payload = SearchResultJson {
        version: JSON_OUTPUT_VERSION,
        routing_backend: decision.routing_backend(),
        routing_reason: decision.reason,
        sidecar_used: decision.sidecar_used(),
        requested_gpu_device_ids: requested_gpu_device_ids.to_vec(),
        routing_gpu_device_ids: Vec::new(),
        gpu_evidence_status: proof_fields.gpu_evidence_status,
        gpu_proof: proof_fields.gpu_proof,
        native_gpu_unavailable: proof_fields.native_gpu_unavailable,
        not_gpu_proof_reason: proof_fields.not_gpu_proof_reason,
        query: pattern,
        path,
        total_files: matched_file_paths.len(),
        total_matches: matches.len(),
        matched_file_paths,
        match_counts_by_file,
        remediation: ast_run_remediation::json_for_search(pattern, matches.len(), decision.reason),
        advisory_hints: ast_run_remediation::python_return_type_advisory_for_json(
            ast_lang,
            ast_selector,
            pattern,
            path,
            &matches,
        ),
        matches,
        result_incomplete,
        incomplete_reason_class,
        incomplete_paths_count,
        path_was_defaulted,
        scope_note,
        binary_match_files,
    };

    println!("{}", serde_json::to_string(&payload)?);
    Ok(())
}

pub(crate) fn unique_line_matches(matches: &[SearchMatchJson]) -> Vec<SearchMatchJson> {
    let mut seen = std::collections::BTreeSet::new();
    let mut unique = Vec::new();
    for matched in matches {
        // `raw`, not `text` (task #266): two matches with genuinely different non-UTF-8 content
        // both report `text: None`, so keying on `text` would wrongly collapse them into a
        // single deduped entry -- `raw` is the byte-exact source of truth every producer
        // populates, valid-UTF-8 or not.
        let key = (matched.file.clone(), matched.line, matched.raw.clone());
        if seen.insert(key) {
            let mut deduped = matched.clone();
            deduped.pattern_id = None;
            deduped.pattern_text = None;
            unique.push(deduped);
        }
    }
    unique
}

/// Writes each matched line's raw bytes directly to stdout (never through `println!`/`Display`
/// on `matched.text`), so a genuinely non-UTF-8 match -- reachable via the multi-pattern native
/// path and, since task #273, the CUDA-gated GPU-native path -- prints byte-for-byte instead of
/// via a lossy `Option<String>` unwrap (task #266: the same byte-fidelity guarantee
/// `append_standard_match_bytes` gives the single-pattern native emitter, extended to this shared
/// plain-text writer).
///
/// A closed output pipe (`tg ... | head -1`) is a normal, expected termination and returns
/// `Ok(())` quietly; any OTHER write failure (disk full, I/O error) is a real error and is
/// propagated rather than silently swallowed. `emit_count_search_matches` below applies the
/// identical rule, so the two plain-text emitters no longer disagree on write-failure behavior.
pub(crate) fn emit_plain_search_matches_with_line_number(
    path: &str,
    matches: &[SearchMatchJson],
    line_number: bool,
) -> anyhow::Result<()> {
    let unique = unique_line_matches(matches);
    let with_filename = unique
        .iter()
        .map(|matched| matched.file.as_str())
        .collect::<std::collections::BTreeSet<_>>()
        .len()
        > 1
        || Path::new(path).is_dir();
    let stdout = io::stdout();
    let mut stdout = stdout.lock();
    for matched in unique {
        let mut line = Vec::with_capacity(matched.raw.len() + matched.file.len() + 32);
        match (with_filename, line_number) {
            (true, true) => {
                write!(line, "{}:{}:", matched.file, matched.line)?;
            }
            (true, false) => {
                write!(line, "{}:", matched.file)?;
            }
            (false, true) => {
                write!(line, "{}:", matched.line)?;
            }
            (false, false) => {}
        }
        line.extend_from_slice(&matched.raw);
        line.push(b'\n');
        if let Err(err) = stdout.write_all(&line) {
            if err.kind() == io::ErrorKind::BrokenPipe {
                return Ok(());
            }
            return Err(err.into());
        }
    }
    Ok(())
}

/// Same closed-pipe-is-quiet / other-errors-propagate rule as
/// `emit_plain_search_matches_with_line_number` above (task #266 cleanup): this used to be bare
/// `println!`, which PANICS on any write failure (the stdlib `print!`/`println!` macros
/// `.expect(...)` internally) -- including the routine closed-pipe case every other emitter in
/// this file already handles quietly.
pub(crate) fn emit_count_search_matches(
    path: &str,
    matches: &[SearchMatchJson],
) -> anyhow::Result<()> {
    let unique = unique_line_matches(matches);
    let with_filename = unique
        .iter()
        .map(|matched| matched.file.as_str())
        .collect::<std::collections::BTreeSet<_>>()
        .len()
        > 1
        || Path::new(path).is_dir();
    let mut counts = std::collections::BTreeMap::<String, usize>::new();
    for matched in unique {
        *counts.entry(matched.file).or_default() += 1;
    }

    let stdout = io::stdout();
    let mut stdout = stdout.lock();
    let write_result = if with_filename {
        counts
            .into_iter()
            .try_for_each(|(file, count)| writeln!(stdout, "{file}:{count}"))
    } else {
        writeln!(stdout, "{}", counts.values().copied().next().unwrap_or(0))
    };
    if let Err(err) = write_result {
        if err.kind() == io::ErrorKind::BrokenPipe {
            return Ok(());
        }
        return Err(err.into());
    }
    Ok(())
}

#[cfg(feature = "cuda")]
pub(crate) fn gpu_native_match_json_entries(stats: &GpuNativeSearchStats) -> Vec<SearchMatchJson> {
    // Preserve raw bytes; lossy conversion corrupts structured output. CUDA CI type-checks these
    // targets with `--all-targets` but does not execute the CUDA-gated tests.
    stats
        .matches
        .iter()
        .map(|matched| {
            let (text, bytes) = native_json_text_fields(&matched.raw);
            let text = text.map(str::to_string);
            SearchMatchJson {
                file: matched.path.to_string_lossy().into_owned(),
                line: matched.line_number,
                text,
                bytes,
                raw: matched.raw.clone(),
                range: None,
                meta_variables: None,
                pattern_id: (stats.pattern_count > 1).then_some(matched.pattern_id),
                pattern_text: (stats.pattern_count > 1).then(|| matched.pattern_text.clone()),
            }
        })
        .collect()
}

#[cfg(feature = "cuda")]
pub(crate) fn emit_gpu_native_json_results(
    decision: RoutingDecision,
    params: &GpuSearchParams<'_>,
    stats: &GpuNativeSearchStats,
) -> anyhow::Result<()> {
    let proof_fields = gpu_proof_fields(
        params.gpu_device_ids,
        decision.routing_backend(),
        decision.sidecar_used(),
    );
    // Task 316. Reuse the helper the CPU envelopes use rather than re-deriving the triple here:
    // re-deriving truncation locally is the exact defect class this campaign closes (task 332
    // found 3 of 3 repo_map gates deadline-blind for that reason), and `walk_was_incomplete`
    // below reads the SAME `Some(stats.walk_errors)` so the payload and the exit code cannot
    // disagree.
    let (result_incomplete, incomplete_reason_class, incomplete_paths_count) =
        incomplete_envelope_fields(Some(stats.walk_errors));
    let (path_was_defaulted, scope_note) =
        defaulted_scope_fields(params.path_was_implicit, stats.total_matches);
    let payload = GpuNativeSearchResultJson {
        version: JSON_OUTPUT_VERSION,
        routing_backend: decision.routing_backend(),
        routing_reason: decision.reason,
        sidecar_used: decision.sidecar_used(),
        query: params.query,
        path: params.path,
        total_matches: stats.total_matches,
        total_files: stats.matched_files,
        requested_gpu_device_ids: params.gpu_device_ids.to_vec(),
        routing_gpu_device_ids: stats
            .selected_devices
            .iter()
            .map(|device| device.device_id)
            .collect(),
        gpu_evidence_status: proof_fields.gpu_evidence_status,
        gpu_proof: proof_fields.gpu_proof,
        native_gpu_unavailable: proof_fields.native_gpu_unavailable,
        not_gpu_proof_reason: proof_fields.not_gpu_proof_reason,
        result_incomplete,
        incomplete_reason_class,
        incomplete_paths_count,
        path_was_defaulted,
        scope_note,
        pipeline: &stats.pipeline,
        matches: gpu_native_match_json_entries(stats),
    };

    println!("{}", serde_json::to_string_pretty(&payload)?);
    Ok(())
}

#[cfg(feature = "cuda")]
pub(crate) fn emit_gpu_native_plain_results(
    params: &GpuSearchParams<'_>,
    stats: &GpuNativeSearchStats,
) -> anyhow::Result<()> {
    // Task #131 F3: this used to call the now-removed `emit_plain_search_matches`, which
    // hardcoded `line_number: true` regardless of `-N`/`--no-line-number` -- the double bug
    // (`GpuSearchParams::line_number` was ALSO hardcoded at every construction site until this
    // same fix). Thread the real, derived value through instead.
    let matches = gpu_native_match_json_entries(stats);
    emit_plain_search_matches_with_line_number(params.path, &matches, params.line_number)
}

#[cfg(feature = "cuda")]
pub(crate) fn emit_gpu_native_count_results(
    params: &GpuSearchParams<'_>,
    stats: &GpuNativeSearchStats,
) -> anyhow::Result<()> {
    let matches = gpu_native_match_json_entries(stats);
    emit_count_search_matches(params.path, &matches)
}

#[cfg(feature = "cuda")]
pub(crate) fn emit_gpu_native_verbose(stats: &GpuNativeSearchStats) {
    if stats.selected_devices.len() <= 1 {
        eprintln!(
            "[gpu-native] selected_gpu_device_id={} selected_gpu_device_name={} gpu_batch_files={} gpu_transfer_bytes={} gpu_streams={} gpu_double_buffered={} pinned_host_buffers={} gpu_batch_count={} gpu_overlap_batches={} gpu_pattern_count={} gpu_pattern_batches={} gpu_single_dispatch={} gpu_transfer_time_ms={:.3} gpu_kernel_time_ms={:.3} gpu_host_file_read_time_ms={:.3} gpu_host_preprocess_time_ms={:.3} gpu_host_to_pinned_copy_time_ms={:.3} gpu_cpu_staging_bytes={} gpu_pageable_host_staging_bytes={} gpu_transfer_throughput_gbps={:.2}",
            stats.selected_device.device_id,
            stats.selected_device.name,
            stats.searched_files,
            stats.transfer_bytes,
            stats.pipeline.stream_count,
            stats.pipeline.double_buffered,
            stats.pipeline.pinned_host_buffers,
            stats.pipeline.batch_count,
            stats.pipeline.overlapped_batches,
            stats.pipeline.pattern_count,
            stats.pipeline.pattern_batch_count,
            stats.pipeline.single_dispatch,
            stats.pipeline.transfer_time_ms,
            stats.pipeline.kernel_time_ms,
            stats.pipeline.host_file_read_time_ms,
            stats.pipeline.host_preprocess_time_ms,
            stats.pipeline.host_to_pinned_copy_time_ms,
            stats.pipeline.cpu_staging_bytes,
            stats.pipeline.pageable_host_staging_bytes,
            stats.pipeline.transfer_throughput_bytes_s / 1_000_000_000.0
        );
        return;
    }
    eprintln!(
        "[gpu-native] selected_gpu_device_ids={} selected_gpu_device_names={} gpu_batch_files={} gpu_transfer_bytes={} gpu_streams={} gpu_double_buffered={} pinned_host_buffers={} gpu_batch_count={} gpu_overlap_batches={} gpu_pattern_count={} gpu_pattern_batches={} gpu_single_dispatch={} gpu_transfer_time_ms={:.3} gpu_kernel_time_ms={:.3} gpu_host_file_read_time_ms={:.3} gpu_host_preprocess_time_ms={:.3} gpu_host_to_pinned_copy_time_ms={:.3} gpu_cpu_staging_bytes={} gpu_pageable_host_staging_bytes={} gpu_transfer_throughput_gbps={:.2}",
        stats.selected_devices.iter().map(|d| d.device_id.to_string()).collect::<Vec<_>>().join(","),
        stats.selected_devices.iter().map(|d| d.name.as_str()).collect::<Vec<_>>().join(" | "),
        stats.searched_files,
        stats.transfer_bytes,
        stats.pipeline.stream_count,
        stats.pipeline.double_buffered,
        stats.pipeline.pinned_host_buffers,
        stats.pipeline.batch_count,
        stats.pipeline.overlapped_batches,
        stats.pipeline.pattern_count,
        stats.pipeline.pattern_batch_count,
        stats.pipeline.single_dispatch,
        stats.pipeline.transfer_time_ms,
        stats.pipeline.kernel_time_ms,
        stats.pipeline.host_file_read_time_ms,
        stats.pipeline.host_preprocess_time_ms,
        stats.pipeline.host_to_pinned_copy_time_ms,
        stats.pipeline.cpu_staging_bytes,
        stats.pipeline.pageable_host_staging_bytes,
        stats.pipeline.transfer_throughput_bytes_s / 1_000_000_000.0
    );

    for device_stats in &stats.device_stats {
        eprintln!(
            "[gpu-native] gpu_device_id={} gpu_device_name={} gpu_device_files={} gpu_device_matches={} gpu_device_transfer_bytes={} gpu_device_streams={} gpu_device_batch_count={} gpu_device_transfer_time_ms={:.3} gpu_device_kernel_time_ms={:.3} gpu_device_host_file_read_time_ms={:.3} gpu_device_host_preprocess_time_ms={:.3} gpu_device_host_to_pinned_copy_time_ms={:.3} gpu_device_cpu_staging_bytes={} gpu_device_pageable_host_staging_bytes={} gpu_device_transfer_throughput_gbps={:.2}",
            device_stats.device.device_id,
            device_stats.device.name,
            device_stats.searched_files,
            device_stats.total_matches,
            device_stats.transfer_bytes,
            device_stats.pipeline.stream_count,
            device_stats.pipeline.batch_count,
            device_stats.pipeline.transfer_time_ms,
            device_stats.pipeline.kernel_time_ms,
            device_stats.pipeline.host_file_read_time_ms,
            device_stats.pipeline.host_preprocess_time_ms,
            device_stats.pipeline.host_to_pinned_copy_time_ms,
            device_stats.pipeline.cpu_staging_bytes,
            device_stats.pipeline.pageable_host_staging_bytes,
            device_stats.pipeline.transfer_throughput_bytes_s / 1_000_000_000.0
        );
    }
}

#[allow(clippy::too_many_arguments)]
pub(crate) fn emit_ndjson_search_results(
    decision: RoutingDecision,
    pattern: &str,
    path: &str,
    requested_gpu_device_ids: &[i32],
    matches: Vec<SearchMatchJson>,
    incomplete_paths: Option<usize>,
    path_was_implicit: bool,
    binary_match_files: Option<usize>,
) -> anyhow::Result<()> {
    let total_matches = matches.len();
    for matched in matches {
        let proof_fields = gpu_proof_fields(
            requested_gpu_device_ids,
            decision.routing_backend(),
            decision.sidecar_used(),
        );
        let payload = SearchMatchNdjson {
            version: JSON_OUTPUT_VERSION,
            routing_backend: decision.routing_backend(),
            routing_reason: decision.reason,
            sidecar_used: decision.sidecar_used(),
            requested_gpu_device_ids: requested_gpu_device_ids.to_vec(),
            routing_gpu_device_ids: Vec::new(),
            gpu_evidence_status: proof_fields.gpu_evidence_status,
            gpu_proof: proof_fields.gpu_proof,
            native_gpu_unavailable: proof_fields.native_gpu_unavailable,
            not_gpu_proof_reason: proof_fields.not_gpu_proof_reason,
            query: pattern,
            path,
            file: &matched.file,
            line: matched.line,
            // Reuse the fields the producer already computed (via `native_json_text_fields` or
            // `guaranteed_utf8_match_fields`) -- no need to re-derive from `matched.raw` here.
            text: matched.text.as_deref(),
            bytes: matched.bytes.clone(),
            pattern_id: matched.pattern_id,
            pattern_text: matched.pattern_text.as_deref(),
        };
        println!("{}", serde_json::to_string(&payload)?);
    }

    let (result_incomplete, incomplete_reason_class, incomplete_paths_count) =
        incomplete_envelope_fields(incomplete_paths);
    let (path_was_defaulted, scope_note) = defaulted_scope_fields(
        path_was_implicit,
        total_matches.saturating_add(binary_match_files.unwrap_or_default()),
    );
    let summary_proof_fields = binary_match_files.map(|_| {
        gpu_proof_fields(
            requested_gpu_device_ids,
            decision.routing_backend(),
            decision.sidecar_used(),
        )
    });
    println!(
        "{}",
        serde_json::to_string(&SearchSummaryNdjson {
            record_type: "summary",
            version: JSON_OUTPUT_VERSION,
            total_matches,
            routing_backend: binary_match_files.map(|_| decision.routing_backend()),
            routing_reason: binary_match_files.map(|_| decision.reason),
            sidecar_used: binary_match_files.map(|_| decision.sidecar_used()),
            requested_gpu_device_ids: binary_match_files.map(|_| requested_gpu_device_ids.to_vec()),
            routing_gpu_device_ids: binary_match_files.map(|_| Vec::new()),
            gpu_evidence_status: summary_proof_fields
                .as_ref()
                .and_then(|fields| fields.gpu_evidence_status),
            gpu_proof: summary_proof_fields
                .as_ref()
                .and_then(|fields| fields.gpu_proof),
            native_gpu_unavailable: summary_proof_fields
                .as_ref()
                .and_then(|fields| fields.native_gpu_unavailable),
            not_gpu_proof_reason: summary_proof_fields
                .and_then(|fields| fields.not_gpu_proof_reason),
            result_incomplete,
            incomplete_reason_class,
            incomplete_paths_count,
            path_was_defaulted,
            scope_note,
            binary_match_files,
        })?
    );

    Ok(())
}

pub(crate) fn parse_gpu_sidecar_search_payload(
    stdout: &str,
) -> anyhow::Result<GpuSidecarSearchPayload> {
    serde_json::from_str(stdout).map_err(|err| {
        anyhow::anyhow!(
            "GPU sidecar returned malformed search JSON payload: expected {{total_matches, total_files, matches[]}} with string file/text fields and integer line_number values ({err})"
        )
    })
}

/// Hand-built sidecar envelope; keep it aligned with the derived emitters in all builds.
pub(crate) fn normalize_gpu_sidecar_json(
    stdout: &str,
    requested_gpu_device_ids: &[i32],
    path_was_implicit: bool,
) -> anyhow::Result<serde_json::Value> {
    let payload = parse_gpu_sidecar_search_payload(stdout)?;
    let proof_fields = gpu_proof_fields(
        requested_gpu_device_ids,
        RoutingDecision::gpu_sidecar().routing_backend(),
        RoutingDecision::gpu_sidecar().sidecar_used(),
    );
    let requested_gpu_device_ids = requested_gpu_device_ids
        .iter()
        .copied()
        .filter(|device_id| *device_id >= 0)
        .map(|device_id| device_id as u32)
        .collect::<Vec<_>>();

    let normalized_matches = payload
        .matches
        .into_iter()
        .map(|entry| {
            let mut value = serde_json::json!({
                "file": entry.file,
                "line_number": entry.line_number,
                "text": entry.text,
            });
            if let Some(pattern_id) = entry.pattern_id {
                value["pattern_id"] = serde_json::json!(pattern_id);
            }
            if let Some(pattern_text) = entry.pattern_text {
                value["pattern_text"] = serde_json::json!(pattern_text);
            }
            value
        })
        .collect::<Vec<_>>();

    let mut value = serde_json::json!({
        "version": JSON_OUTPUT_VERSION,
        "routing_backend": RoutingDecision::gpu_sidecar().routing_backend(),
        "routing_reason": RoutingDecision::gpu_sidecar().reason,
        "sidecar_used": RoutingDecision::gpu_sidecar().sidecar_used(),
        "total_matches": payload.total_matches,
        "total_files": payload.total_files,
        "requested_gpu_device_ids": requested_gpu_device_ids,
        "routing_gpu_device_ids": payload.routing_gpu_device_ids,
        "matches": normalized_matches,
    });
    if let Some(gpu_evidence_status) = proof_fields.gpu_evidence_status {
        value["gpu_evidence_status"] = serde_json::json!(gpu_evidence_status);
    }
    if let Some(gpu_proof) = proof_fields.gpu_proof {
        value["gpu_proof"] = serde_json::json!(gpu_proof);
    }
    if let Some(native_gpu_unavailable) = proof_fields.native_gpu_unavailable {
        value["native_gpu_unavailable"] = serde_json::json!(native_gpu_unavailable);
    }
    if let Some(not_gpu_proof_reason) = proof_fields.not_gpu_proof_reason {
        value["not_gpu_proof_reason"] = serde_json::json!(not_gpu_proof_reason);
    }
    // Task #26. Same shared gate as every sibling envelope, so this route cannot drift from them
    // -- which is exactly what it had already done by being invisible to a derive-macro census.
    // Insert-when-applicable mirrors the siblings' `skip_serializing_if`: an explicitly-scoped
    // search adds neither key and stays byte-identical for existing consumers.
    let (path_was_defaulted, scope_note) =
        defaulted_scope_fields(path_was_implicit, payload.total_matches);
    if let Some(path_was_defaulted) = path_was_defaulted {
        value["path_was_defaulted"] = serde_json::json!(path_was_defaulted);
    }
    if let Some(scope_note) = scope_note {
        value["scope_note"] = serde_json::json!(scope_note);
    }
    Ok(value)
}

pub(crate) fn emit_verbose_metadata(decision: RoutingDecision) {
    eprintln!(
        "[routing] routing_backend={} routing_reason={} sidecar_used={}",
        decision.routing_backend(),
        decision.reason,
        decision.sidecar_used()
    );
}
