#![allow(unused_imports)]
use anyhow::{anyhow, Context, Result};
use cudarc::driver::{
    result as cuda_result, sys, CudaContext, CudaEvent, CudaFunction, CudaModule, CudaSlice,
    CudaStream, DevicePtr, DevicePtrMut, LaunchConfig, PinnedHostSlice, PushKernelArg,
};
use cudarc::nvrtc::{compile_ptx_with_opts, CompileOptions, Ptx};
use ignore::{overrides::OverrideBuilder, WalkBuilder, WalkState};
use memchr::{memchr, memchr_iter};
use rayon::prelude::*;
use serde::Serialize;
use sha2::{Digest, Sha256};
use std::collections::{BTreeSet, HashMap};
use std::env;
use std::fs;
use std::io::Read;
use std::panic::{catch_unwind, AssertUnwindSafe};
use std::path::{Path, PathBuf};
use std::sync::atomic::{AtomicUsize, Ordering};
use std::sync::Arc;
use std::sync::Once;
use std::time::Instant;

use super::*;

pub(super) fn resolve_cuda_devices(device_ids: &[i32]) -> Result<Vec<CudaDeviceInfo>> {
    let device_count = cuda_device_count()?;
    if device_count <= 0 {
        return Err(anyhow!(
            "CUDA is unavailable: no CUDA devices were detected by cudarc"
        ));
    }
    let requested_device_ids = validate_requested_cuda_device_ids(device_ids, device_count)?;

    let mut selected = Vec::new();
    for device_id in requested_device_ids {
        selected.push(device_info_for_ordinal(device_id)?);
    }

    Ok(selected)
}

pub(super) fn cuda_device_count() -> Result<i32> {
    catch_cuda("initialize CUDA driver", || {
        CudaContext::device_count().map_err(anyhow::Error::new)
    })
}

pub(super) fn device_info_for_ordinal(device_id: i32) -> Result<CudaDeviceInfo> {
    let context = open_cuda_context(device_id)?;
    let name = context
        .name()
        .map_err(anyhow::Error::new)
        .with_context(|| format!("failed to read CUDA device {device_id} name"))?;
    let compute_capability = context
        .compute_capability()
        .map_err(anyhow::Error::new)
        .with_context(|| format!("failed to read CUDA device {device_id} compute capability"))?;
    Ok(CudaDeviceInfo {
        device_id,
        name,
        compute_capability,
    })
}

pub(super) fn validate_requested_cuda_device_ids(
    device_ids: &[i32],
    device_count: i32,
) -> Result<Vec<i32>> {
    if device_ids.is_empty() {
        return Err(anyhow!(
            "GPU native search requires at least one CUDA device id"
        ));
    }

    let mut selected = Vec::new();
    let mut seen = BTreeSet::new();
    for &device_id in device_ids {
        if !seen.insert(device_id) {
            continue;
        }
        if device_id < 0 || device_id >= device_count {
            return Err(anyhow!(
                "invalid CUDA device id {device_id}; available CUDA devices: {}",
                format_available_device_ids_from_count(device_count)
            ));
        }
        selected.push(device_id);
    }
    Ok(selected)
}

pub(super) fn assign_files_to_devices(
    files: &[PathBuf],
    devices: &[CudaDeviceInfo],
) -> Result<Vec<DeviceFileAssignment>> {
    let mut assignments = devices
        .iter()
        .cloned()
        .map(|device| DeviceFileAssignment {
            device,
            files: Vec::new(),
            assigned_bytes: 0,
        })
        .collect::<Vec<_>>();
    if assignments.is_empty() {
        return Ok(assignments);
    }

    let mut entries = files
        .iter()
        .enumerate()
        .map(|(order, path)| {
            Ok(SearchFileEntry {
                path: path.clone(),
                estimated_bytes: file_estimated_bytes(path)?,
                order,
            })
        })
        .collect::<Result<Vec<_>>>()?;
    if entries.is_empty() {
        return Ok(assignments);
    }

    entries.sort_by(|left, right| {
        right
            .estimated_bytes
            .cmp(&left.estimated_bytes)
            .then(left.order.cmp(&right.order))
    });

    let min_files_per_device = minimum_files_per_device(entries.len(), assignments.len());
    let seed_count = min_files_per_device
        .saturating_mul(assignments.len())
        .min(entries.len());

    let assignment_count = assignments.len();
    for (index, entry) in entries.drain(..seed_count).enumerate() {
        let target_index = index % assignment_count;
        add_file_assignment(&mut assignments[target_index], entry);
    }

    for entry in entries {
        let target_index = assignments
            .iter()
            .enumerate()
            .min_by(|(_, left), (_, right)| {
                left.assigned_bytes
                    .cmp(&right.assigned_bytes)
                    .then(left.files.len().cmp(&right.files.len()))
                    .then(left.device.device_id.cmp(&right.device.device_id))
            })
            .map(|(index, _)| index)
            .unwrap_or(0);
        add_file_assignment(&mut assignments[target_index], entry);
    }

    for assignment in &mut assignments {
        assignment.files.sort_by_key(|entry| entry.order);
    }

    Ok(assignments)
}

pub(super) fn minimum_files_per_device(total_files: usize, device_count: usize) -> usize {
    if total_files == 0 || device_count == 0 {
        return 0;
    }

    let mut minimum = total_files.div_ceil(10).max(1);
    while minimum > 0 && minimum.saturating_mul(device_count) > total_files {
        minimum -= 1;
    }
    minimum
}

pub(super) fn add_file_assignment(assignment: &mut DeviceFileAssignment, entry: SearchFileEntry) {
    assignment.assigned_bytes = assignment
        .assigned_bytes
        .saturating_add(entry.estimated_bytes);
    assignment.files.push(entry);
}

pub(super) fn ensure_capacity(capacity: usize, required: usize, path: &Path) -> Result<()> {
    if required <= capacity {
        return Ok(());
    }

    Err(anyhow!(
        "GPU pinned transfer buffer overflow while staging {}: required {} bytes, capacity {} bytes",
        path.display(),
        required,
        capacity
    ))
}

/// Bounded refuse-before-enumerate gate for the GPU-native engine's own root walk -- the
/// cuda-native sibling of `native_search::check_native_implicit_walk_ceiling` (audit #105) and
/// `rg_passthrough::check_implicit_walk_ceiling` (audit #100). Audit #109 found the GPU-native
/// engine had NO ceiling at all: `GpuSearchParams::path_was_implicit` (main.rs) was threaded into
/// the CPU-fallback `RipgrepSearchArgs`/`NativeSearchConfig` redirects (`execute_gpu_native_route`'s
/// unavailable-GPU fallback path) but never into `GpuNativeSearchConfig` itself, so a bare
/// implicit-path `tg search PAT --gpu-device-ids 0` on a huge root walked unbounded through this
/// engine even though the CPU engine (#105) and rg-passthrough engine (#100) were both already
/// bounded for the exact same shape of request.
///
/// Only meaningful when `config.path_was_implicit` -- an explicit, deliberately-scoped PATH is
/// never refused regardless of size. Called as the FIRST statement of `collect_walked_files`, the
/// only function in this module that ever hands a root to `WalkBuilder`.
pub(super) fn check_gpu_native_implicit_walk_ceiling(
    config: &GpuNativeSearchConfig,
    roots: &[PathBuf],
) -> Option<String> {
    if !config.path_was_implicit {
        return None;
    }
    let probe_roots: Vec<String> = roots
        .iter()
        .map(|root| root.to_string_lossy().into_owned())
        .collect();
    if crate::rg_passthrough::implicit_search_walk_exceeds_ceiling(
        &probe_roots,
        config.max_depth,
        config.no_ignore,
        config.hidden,
        crate::rg_passthrough::IMPLICIT_SEARCH_WALK_FILE_CEILING,
    ) {
        Some(
            crate::rg_passthrough::format_unbounded_implicit_search_walk_error(
                crate::rg_passthrough::IMPLICIT_SEARCH_WALK_FILE_CEILING,
            ),
        )
    } else {
        None
    }
}

/// Files the GPU walk collected, plus how many entries it could not read.
///
/// Task 316, the GPU twin of `native_search::WalkedFiles` (#276 slice B3) and introduced for the
/// same reason: `collect_walked_files` returned a bare `Vec<PathBuf>`, so it owned NO channel back
/// to its caller and the walk-error count died at the `eprintln!`. Without this the GPU envelope
/// could only ever say "complete", which is the silence #276 exists to end.
/// `Debug` is REQUIRED, not decorative: `collect_search_files`'s walk-ceiling tests call
/// `Result::expect_err`, which needs `T: Debug` to print the unexpected Ok. Before task 316 that
/// `T` was a bare `Vec<PathBuf>` (Debug for free), so swapping in a struct broke the tests --
/// caught by `cuda-feature-check`, the only oracle for this cuda-gated module.
#[derive(Debug)]
pub(super) struct GpuWalkedFiles {
    pub(super) files: Vec<PathBuf>,
    /// Same `is_io()` filter as both CPU walkers (`native_search.rs:1328`/`:1806`) -- a malformed
    /// global gitignore must NOT make a complete walk claim incompleteness.
    pub(super) walk_errors: usize,
}

pub(super) fn collect_search_files(config: &GpuNativeSearchConfig) -> Result<GpuWalkedFiles> {
    let mut files = Vec::new();
    let mut roots = Vec::new();
    let mut walk_errors = 0usize;

    for path in &config.paths {
        if !path.exists() {
            return Err(anyhow!(
                "GPU native search path does not exist: {}",
                path.display()
            ));
        }
        if path.is_file() {
            files.push(path.clone());
        } else {
            roots.push(path.clone());
        }
    }

    if !roots.is_empty() {
        let walked = collect_walked_files(config, &roots)?;
        files.extend(walked.files);
        walk_errors += walked.walk_errors;
    }

    files.sort_unstable();
    files.dedup();
    // The count is deliberately NOT deduped alongside the paths: like the CPU side's
    // `incomplete_paths_count`, it counts walk-error EVENTS, not distinct paths (task 320), so
    // overlapping roots can raise it more than once for the same directory -- which is exactly
    // what `rg` does, printing one line per visit.
    Ok(GpuWalkedFiles { files, walk_errors })
}

pub(super) fn collect_walked_files(
    config: &GpuNativeSearchConfig,
    roots: &[PathBuf],
) -> Result<GpuWalkedFiles> {
    if let Some(refusal) = check_gpu_native_implicit_walk_ceiling(config, roots) {
        return Err(anyhow!(refusal));
    }
    let builder = build_walk_builder(config, roots)?;
    let walked_files = Arc::new(std::sync::Mutex::new(Vec::new()));
    let shared_files = Arc::clone(&walked_files);
    // AtomicUsize rather than a Mutex<usize>, same reasoning as the CPU twin
    // (`native_search.rs:1767`): a pure counter on the hot walker, incremented under contention by
    // every worker thread. Relaxed ordering suffices -- no other memory is published through it,
    // and it is only read after `run()` has joined every worker.
    let walk_errors = Arc::new(AtomicUsize::new(0));
    let shared_walk_errors = Arc::clone(&walk_errors);

    builder.build_parallel().run(|| {
        let shared_files = Arc::clone(&shared_files);
        let shared_walk_errors = Arc::clone(&shared_walk_errors);
        Box::new(move |entry| {
            let entry = match entry {
                Ok(entry) => entry,
                Err(err) => {
                    // Byte-identical defect to the CPU collector in `native_search.rs`, and
                    // fixed the same way (task 280): the Err arm used to be dropped with no
                    // output, so a permission-denied subtree vanished from the file list
                    // completely silently. Real `rg` prints one stderr line per unreadable
                    // path and keeps walking.
                    //
                    // TASK 316 CLOSED THE PARITY GAP. Earlier revisions of this comment said the
                    // exit code and envelope marker were unwired here, and named two obstacles.
                    // Both are resolved; the chain now matches the CPU side end to end:
                    //   :493                    `walk_errors` on GpuNativeSearchStats
                    //   here                    the increment, behind the same `is_io()` gate
                    //   main.rs (GPU envelope)  result_incomplete / incomplete_reason_class /
                    //                           incomplete_paths_count, via the SHARED
                    //                           `incomplete_envelope_fields` -- not a parallel
                    //                           copy, so the GPU envelope cannot drift from the
                    //                           CPU one
                    //   main.rs (GPU exit arm)  exit 2 via the shared `walk_was_incomplete`
                    // Obstacle 1 (adding a field to a `Serialize` struct changes JSON shape) is
                    // answered by the envelope's existing `skip_serializing_if = "Option::is_none"`
                    // idiom: a complete GPU scan emits none of the three and stays byte-identical.
                    // Obstacle 2 (this module owns no envelope) is answered by identifying the
                    // route: `GpuNativeSearchResultJson` / `emit_gpu_native_json_results`.
                    //
                    // Same `is_io()` allow-list as both CPU walkers (`native_search.rs:1328`
                    // and `:1806`), for the reason recorded there: `ignore::Error` also covers a
                    // malformed ancestor/global gitignore (Glob / WithLineNumber via `add_parents`),
                    // plus UnrecognizedFileType / InvalidDefinition / Loop. Counting those would
                    // label a COMPLETE walk incomplete with a budget-non-remediable cause -- one bad
                    // glob in ~/.config/git/ignore would make every GPU `--json` search on that
                    // machine claim it could not finish. A false "incomplete" is worse than the
                    // silence #276 set out to fix. Non-I/O errors still PRINT (rg-parity, #263);
                    // they just do not claim the answer is partial.
                    if err.is_io() {
                        shared_walk_errors.fetch_add(1, Ordering::Relaxed);
                    }
                    eprintln!("tg: {err}");
                    return WalkState::Continue;
                }
            };
            if entry
                .file_type()
                .map(|kind| kind.is_file())
                .unwrap_or(false)
            {
                if let Ok(mut guard) = shared_files.lock() {
                    guard.push(entry.path().to_path_buf());
                }
            }
            WalkState::Continue
        })
    });

    let files = walked_files
        .lock()
        .map_err(|_| anyhow!("failed to collect GPU native search walk results"))?
        .clone();
    Ok(GpuWalkedFiles {
        files,
        walk_errors: walk_errors.load(Ordering::Relaxed),
    })
}

pub(super) fn build_walk_builder(
    config: &GpuNativeSearchConfig,
    roots: &[PathBuf],
) -> Result<WalkBuilder> {
    let first_root = roots[0].clone();
    let mut builder = WalkBuilder::new(&first_root);
    for root in roots.iter().skip(1) {
        builder.add(root);
    }
    builder.threads(0);
    builder.hidden(!config.hidden);
    builder.max_depth(config.max_depth);

    if config.no_ignore {
        builder.ignore(false);
        builder.git_ignore(false);
        builder.git_global(false);
        builder.git_exclude(false);
        builder.parents(false);
    } else {
        for root in roots {
            for ignore_name in [".ignore", ".gitignore", ".rgignore"] {
                let ignore_path = root.join(ignore_name);
                if ignore_path.is_file() {
                    builder.add_ignore(ignore_path);
                }
            }
        }
    }

    if !config.glob.is_empty() {
        let mut overrides = OverrideBuilder::new(&first_root);
        for glob in &config.glob {
            overrides
                .add(glob)
                .with_context(|| format!("failed to add GPU native glob override '{glob}'"))?;
        }
        builder.overrides(
            overrides
                .build()
                .context("failed to build GPU native ignore override matcher")?,
        );
    }

    Ok(builder)
}

pub(super) fn convert_offsets_to_line_matches(
    buffer: &[u8],
    batch: &[BatchedFile],
    offsets: &[PatternMatchPosition],
    all_patterns: &[String],
) -> Result<Vec<GpuNativeSearchMatch>> {
    let mut grouped_offsets = vec![Vec::new(); batch.len()];
    let mut batch_index = 0usize;

    for matched in offsets {
        let matched_offset = matched.byte_offset;
        while batch_index < batch.len() && matched_offset >= batch[batch_index].end {
            batch_index += 1;
        }
        if batch_index >= batch.len() {
            break;
        }

        let file = &batch[batch_index];
        if matched_offset < file.start || matched_offset >= file.end {
            continue;
        }

        grouped_offsets[batch_index].push(PatternMatchPosition {
            byte_offset: matched_offset - file.start,
            pattern_id: matched.pattern_id,
        });
    }

    let mut matches = Vec::new();
    for (file, file_offsets) in batch.iter().zip(grouped_offsets.into_iter()) {
        if file_offsets.is_empty() {
            continue;
        }
        let file_bytes = &buffer[file.start..file.end];
        matches.extend(line_matches_for_file(
            file,
            file_bytes,
            &file_offsets,
            all_patterns,
        ));
    }

    Ok(matches)
}

pub(super) fn line_matches_for_file(
    file: &BatchedFile,
    file_bytes: &[u8],
    offsets: &[PatternMatchPosition],
    all_patterns: &[String],
) -> Vec<GpuNativeSearchMatch> {
    if file.line_descriptors.is_empty() {
        return line_matches_for_file_by_scanning(&file.path, file_bytes, offsets, all_patterns);
    }

    let mut matches = Vec::new();
    let mut seen = std::collections::BTreeSet::new();
    for offset in offsets {
        let absolute_offset = file.start.saturating_add(offset.byte_offset);
        let line_index = file
            .line_descriptors
            .partition_point(|line| (line.start as usize) <= absolute_offset)
            .saturating_sub(1);
        let Some(line) = file.line_descriptors.get(line_index).copied() else {
            continue;
        };
        let line_start = line.start as usize;
        let line_len = line.len as usize;
        if absolute_offset < line_start || absolute_offset >= line_start.saturating_add(line_len) {
            continue;
        }
        if !seen.insert((line_start, offset.pattern_id)) {
            continue;
        }
        let relative_start = line_start.saturating_sub(file.start);
        let relative_end = relative_start
            .saturating_add(line_len)
            .min(file_bytes.len());
        let line_bytes = &file_bytes[relative_start..relative_end];
        matches.push(GpuNativeSearchMatch {
            path: file.path.clone(),
            line_number: line.line_number,
            raw: line_bytes.to_vec(),
            pattern_id: offset.pattern_id,
            pattern_text: all_patterns[offset.pattern_id].clone(),
        });
    }

    matches
}

pub(super) fn line_matches_for_file_by_scanning(
    path: &Path,
    file_bytes: &[u8],
    offsets: &[PatternMatchPosition],
    all_patterns: &[String],
) -> Vec<GpuNativeSearchMatch> {
    let newline_positions = memchr_iter(b'\n', file_bytes).collect::<Vec<_>>();
    let mut matches = Vec::new();
    let mut seen = std::collections::BTreeSet::new();

    for offset in offsets {
        let newline_index =
            newline_positions.partition_point(|position| *position < offset.byte_offset);
        let line_start = newline_index
            .checked_sub(1)
            .and_then(|index| newline_positions.get(index).copied())
            .map(|index| index + 1)
            .unwrap_or(0);
        if !seen.insert((line_start, offset.pattern_id)) {
            continue;
        }
        let line_end = newline_positions
            .get(newline_index)
            .copied()
            .unwrap_or(file_bytes.len());
        let line_bytes = &file_bytes[line_start..line_end];
        matches.push(GpuNativeSearchMatch {
            path: path.to_path_buf(),
            line_number: newline_index + 1,
            raw: line_bytes.to_vec(),
            pattern_id: offset.pattern_id,
            pattern_text: all_patterns[offset.pattern_id].clone(),
        });
    }

    matches
}

pub(super) struct CachedKernelPtx {
    pub(super) ptx: Ptx,
    pub(super) cache_path: PathBuf,
    pub(super) from_cache: bool,
}
