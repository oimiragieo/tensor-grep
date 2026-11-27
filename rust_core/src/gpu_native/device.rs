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

pub fn enumerate_cuda_devices() -> Result<Vec<CudaDeviceInfo>> {
    let device_count = cuda_device_count()?;

    let mut devices = Vec::new();
    for ordinal in 0..device_count {
        devices.push(device_info_for_ordinal(ordinal)?);
    }

    Ok(devices)
}

pub fn detect_compute_capability(device_id: i32) -> Result<(i32, i32)> {
    validate_device_id(device_id)?;
    open_cuda_context(device_id)?
        .compute_capability()
        .map_err(anyhow::Error::new)
        .with_context(|| format!("failed to detect compute capability for CUDA device {device_id}"))
}

pub fn compile_search_kernel(device_id: i32) -> Result<()> {
    create_kernel_runtime(device_id).map(|_| ())
}

pub fn benchmark_pinned_transfer_throughput(
    device_id: i32,
    total_bytes: usize,
    batch_bytes: usize,
) -> Result<GpuPinnedTransferBenchmark> {
    if total_bytes == 0 {
        return Err(anyhow!(
            "GPU pinned transfer benchmark requires total_bytes > 0"
        ));
    }
    if batch_bytes == 0 {
        return Err(anyhow!(
            "GPU pinned transfer benchmark requires batch_bytes > 0"
        ));
    }

    validate_device_id(device_id)?;
    let context = open_cuda_context(device_id)?;
    let effective_batch_bytes = batch_bytes.min(total_bytes).max(1);
    let batch_count = total_bytes.div_ceil(effective_batch_bytes);
    let active_stream_count = batch_count.clamp(1, PIPELINE_SLOT_COUNT);
    let slot_capacity = effective_batch_bytes;
    let mut slots = (0..PIPELINE_SLOT_COUNT)
        .map(|_| create_transfer_benchmark_slot(&context, slot_capacity))
        .collect::<Result<Vec<_>>>()?;
    let mut pipeline_start = None;

    for slot in &mut slots {
        slot.host_buffer.as_mut_slice()?.fill(0x5a);
    }

    let started_at = Instant::now();
    let mut transfer_time_ms = 0.0f32;
    for batch_index in 0..batch_count {
        let slot = &mut slots[batch_index % PIPELINE_SLOT_COUNT];
        let remaining_bytes = total_bytes.saturating_sub(batch_index * effective_batch_bytes);
        let bytes_this_batch = remaining_bytes.min(effective_batch_bytes);
        finalize_transfer_benchmark_slot(slot, &mut transfer_time_ms)?;
        if pipeline_start.is_none() {
            pipeline_start = Some(record_timed_event(&slot.stream)?);
        }
        slot.transfer_start = Some(record_timed_event(&slot.stream)?);
        copy_pinned_host_to_device(
            &slot.stream,
            &slot.host_buffer,
            bytes_this_batch,
            &mut slot.device_buffer,
        )
        .context("failed to copy pinned benchmark buffer to CUDA device")?;
        slot.transfer_end = Some(record_timed_event(&slot.stream)?);
        slot.pending_transfer = true;
    }

    let pipeline_end = if let Some(start) = pipeline_start.as_ref() {
        let coordinator_stream = context.default_stream();
        for slot in &slots {
            if let Some(transfer_end) = slot.transfer_end.as_ref() {
                coordinator_stream
                    .wait(transfer_end)
                    .map_err(anyhow::Error::new)
                    .context("failed to coordinate CUDA transfer benchmark completion")?;
            }
        }
        let end = record_timed_event(&coordinator_stream)?;
        end.synchronize()
            .map_err(anyhow::Error::new)
            .context("failed to synchronize CUDA transfer benchmark end event")?;
        Some((start, end))
    } else {
        None
    };

    for slot in &mut slots {
        finalize_transfer_benchmark_slot(slot, &mut transfer_time_ms)?;
    }

    let wall_time_ms = if let Some((start, end)) = pipeline_end.as_ref() {
        f64::from(
            start
                .elapsed_ms(end)
                .map_err(anyhow::Error::new)
                .context("failed to measure CUDA transfer benchmark wall time")?,
        )
    } else {
        started_at.elapsed().as_secs_f64() * 1_000.0
    };
    let throughput_bytes_per_s = if wall_time_ms > 0.0 {
        total_bytes as f64 / (wall_time_ms / 1_000.0)
    } else {
        0.0
    };

    Ok(GpuPinnedTransferBenchmark {
        pinned_host_buffers: true,
        double_buffered: active_stream_count >= 2,
        stream_count: active_stream_count,
        batch_count,
        total_bytes,
        batch_bytes: effective_batch_bytes,
        transfer_time_ms,
        wall_time_ms,
        throughput_bytes_per_s,
    })
}

pub fn benchmark_pageable_transfer_throughput(
    device_id: i32,
    total_bytes: usize,
    batch_bytes: usize,
) -> Result<GpuPinnedTransferBenchmark> {
    if total_bytes == 0 {
        return Err(anyhow!(
            "GPU pageable transfer benchmark requires total_bytes > 0"
        ));
    }
    if batch_bytes == 0 {
        return Err(anyhow!(
            "GPU pageable transfer benchmark requires batch_bytes > 0"
        ));
    }

    validate_device_id(device_id)?;
    let context = open_cuda_context(device_id)?;
    let effective_batch_bytes = batch_bytes.min(total_bytes).max(1);
    let batch_count = total_bytes.div_ceil(effective_batch_bytes);
    let active_stream_count = batch_count.clamp(1, PIPELINE_SLOT_COUNT);
    let slot_capacity = effective_batch_bytes;
    let mut slots = (0..PIPELINE_SLOT_COUNT)
        .map(|_| create_pageable_transfer_benchmark_slot(&context, slot_capacity))
        .collect::<Result<Vec<_>>>()?;
    let mut pipeline_start = None;

    for slot in &mut slots {
        slot.host_buffer.fill(0x5a);
    }

    let started_at = Instant::now();
    let mut transfer_time_ms = 0.0f32;
    for batch_index in 0..batch_count {
        let slot = &mut slots[batch_index % PIPELINE_SLOT_COUNT];
        let remaining_bytes = total_bytes.saturating_sub(batch_index * effective_batch_bytes);
        let bytes_this_batch = remaining_bytes.min(effective_batch_bytes);
        finalize_pageable_transfer_benchmark_slot(slot, &mut transfer_time_ms)?;
        if pipeline_start.is_none() {
            pipeline_start = Some(record_timed_event(&slot.stream)?);
        }
        slot.transfer_start = Some(record_timed_event(&slot.stream)?);
        copy_pageable_host_to_device(
            &slot.stream,
            &slot.host_buffer,
            bytes_this_batch,
            &mut slot.device_buffer,
        )
        .context("failed to copy pageable benchmark buffer to CUDA device")?;
        slot.transfer_end = Some(record_timed_event(&slot.stream)?);
        slot.pending_transfer = true;
    }

    let pipeline_end = if let Some(start) = pipeline_start.as_ref() {
        let coordinator_stream = context.default_stream();
        for slot in &slots {
            if let Some(transfer_end) = slot.transfer_end.as_ref() {
                coordinator_stream
                    .wait(transfer_end)
                    .map_err(anyhow::Error::new)
                    .context("failed to coordinate CUDA pageable transfer benchmark completion")?;
            }
        }
        let end = record_timed_event(&coordinator_stream)?;
        end.synchronize()
            .map_err(anyhow::Error::new)
            .context("failed to synchronize CUDA pageable transfer benchmark end event")?;
        Some((start, end))
    } else {
        None
    };

    for slot in &mut slots {
        finalize_pageable_transfer_benchmark_slot(slot, &mut transfer_time_ms)?;
    }

    let wall_time_ms = if let Some((start, end)) = pipeline_end.as_ref() {
        f64::from(
            start
                .elapsed_ms(end)
                .map_err(anyhow::Error::new)
                .context("failed to measure CUDA pageable transfer benchmark wall time")?,
        )
    } else {
        started_at.elapsed().as_secs_f64() * 1_000.0
    };
    let throughput_bytes_per_s = if wall_time_ms > 0.0 {
        total_bytes as f64 / (wall_time_ms / 1_000.0)
    } else {
        0.0
    };

    Ok(GpuPinnedTransferBenchmark {
        pinned_host_buffers: false,
        double_buffered: active_stream_count >= 2,
        stream_count: active_stream_count,
        batch_count,
        total_bytes,
        batch_bytes: effective_batch_bytes,
        transfer_time_ms,
        wall_time_ms,
        throughput_bytes_per_s,
    })
}

pub fn probe_device_allocation(device_id: i32, bytes: usize) -> Result<()> {
    if bytes == 0 {
        return Err(anyhow!("GPU allocation probe requires bytes > 0"));
    }

    validate_device_id(device_id)?;
    let context = open_cuda_context(device_id)?;
    let stream = context.default_stream();
    let allocation = catch_cuda(
        &format!("allocate {bytes} bytes on CUDA device {device_id}"),
        || unsafe { stream.alloc::<u8>(bytes.max(1)).map_err(anyhow::Error::new) },
    );
    match allocation {
        Ok(_buffer) => Ok(()),
        Err(err) => {
            let detail = err.to_string();
            let lower = detail.to_ascii_lowercase();
            let requested_gib = bytes as f64 / (1024.0 * 1024.0 * 1024.0);
            if lower.contains("out of memory") || lower.contains("cuda_error_out_of_memory") {
                return Err(anyhow!(
                    "CUDA out of memory while allocating {requested_gib:.2} GiB ({bytes} bytes) on device {device_id}"
                ));
            }
            Err(anyhow!(
                "failed to allocate {requested_gib:.2} GiB ({bytes} bytes) on CUDA device {device_id}: {detail}"
            ))
        }
    }
}

pub fn gpu_native_search_paths(
    config: &GpuNativeSearchConfig,
    device_id: i32,
) -> Result<GpuNativeSearchStats> {
    gpu_native_search_paths_multi(config, &[device_id])
}

pub fn gpu_native_search_paths_multi(
    config: &GpuNativeSearchConfig,
    device_ids: &[i32],
) -> Result<GpuNativeSearchStats> {
    gpu_native_search_paths_multi_with_options(
        config,
        device_ids,
        SearchExecutionOptions::default(),
    )
}

pub fn benchmark_cuda_graph_search_paths(
    config: &GpuNativeSearchConfig,
    device_id: i32,
) -> Result<GpuCudaGraphBenchmark> {
    let baseline = run_cuda_graph_benchmark_search_paths(config, device_id, false)?;
    let graphed = run_cuda_graph_benchmark_search_paths(config, device_id, true)?;
    let baseline_wall_time_ms = baseline.pipeline.wall_time_ms;
    let graphed_wall_time_ms = graphed.pipeline.wall_time_ms;
    let wall_time_reduction_pct = if baseline_wall_time_ms > 0.0 {
        ((baseline_wall_time_ms - graphed_wall_time_ms) / baseline_wall_time_ms) * 100.0
    } else {
        0.0
    };
    let results_identical = baseline.matches == graphed.matches
        && baseline.total_matches == graphed.total_matches
        && baseline.matched_files == graphed.matched_files;

    Ok(GpuCudaGraphBenchmark {
        baseline,
        graphed,
        results_identical,
        wall_time_reduction_pct,
    })
}

pub(super) struct PositionGraphCaptureState {
    signature: GraphCaptureSignature,
    graph: CapturedCudaGraph,
    match_count_host: PinnedHostSlice<u32>,
    match_positions_host: PinnedHostSlice<u32>,
    match_pattern_ids_host: PinnedHostSlice<u32>,
}

pub(super) fn run_cuda_graph_benchmark_search_paths(
    config: &GpuNativeSearchConfig,
    device_id: i32,
    use_cuda_graphs: bool,
) -> Result<GpuNativeSearchStats> {
    if config.patterns.is_empty() {
        return Err(anyhow!(
            "GPU native search requires at least one non-empty pattern"
        ));
    }
    if config.paths.is_empty() {
        return Err(anyhow!(
            "GPU native search requires at least one search path"
        ));
    }
    if config.patterns.iter().any(|pattern| pattern.is_empty()) {
        return Err(anyhow!(
            "GPU native search requires all patterns to be non-empty"
        ));
    }

    // Task 316: the walk now returns its error count alongside the paths, so the GPU route
    // can disclose an incomplete file list instead of silently reporting a short answer.
    let walked = collect_search_files(config)?;
    let files = walked.files;
    let pattern_batches = plan_pattern_batches(&config.patterns)?;
    let batch_plans = plan_file_batches(
        &files,
        resolve_effective_max_batch_bytes(
            config,
            pattern_batches
                .iter()
                .map(|batch| batch.global_pattern_ids.len())
                .max()
                .unwrap_or(1),
        ),
    )?;
    let runtime = create_kernel_runtime(device_id)?;
    let stream = runtime
        .context
        .new_stream()
        .map_err(anyhow::Error::new)
        .context("failed to create CUDA stream for graph benchmark search")?;
    let selected_device = resolve_cuda_devices(&[device_id])?
        .into_iter()
        .next()
        .context("GPU native search requires one resolved CUDA device")?;
    let slot_capacity = batch_plans
        .iter()
        .map(|batch| batch.estimated_bytes)
        .max()
        .unwrap_or(1)
        .max(1);
    let max_patterns_per_dispatch = pattern_batches
        .iter()
        .map(|batch| batch.global_pattern_ids.len())
        .max()
        .unwrap_or(1);
    let max_match_capacity = resolve_max_match_capacity(slot_capacity, max_patterns_per_dispatch)?;
    let mut host_buffer = RawPinnedHostBuffer::new(&runtime.context, slot_capacity)
        .context("failed to allocate pinned host buffer for graph benchmark search")?;
    let mut data_buffer = unsafe { stream.alloc::<u8>(slot_capacity) }
        .map_err(anyhow::Error::new)
        .context("failed to allocate device data buffer for graph benchmark search")?;
    let mut match_positions_buffer = unsafe { stream.alloc::<u32>(max_match_capacity) }
        .map_err(anyhow::Error::new)
        .context("failed to allocate graph benchmark match position buffer")?;
    let mut match_pattern_ids_buffer = unsafe { stream.alloc::<u32>(max_match_capacity) }
        .map_err(anyhow::Error::new)
        .context("failed to allocate graph benchmark match pattern id buffer")?;
    let mut match_count_buffer = stream
        .alloc_zeros::<u32>(1)
        .map_err(anyhow::Error::new)
        .context("failed to allocate graph benchmark match counter")?;
    let device_pattern_batches = upload_pattern_batches_on_stream(&stream, &pattern_batches)?;

    let started_at = Instant::now();
    let mut transfer_bytes = 0usize;
    let mut matches = Vec::new();
    let mut transfer_time_ms = 0.0f32;
    let mut kernel_time_ms = 0.0f32;
    let mut cuda_graph_captures = 0usize;
    let mut cuda_graph_replays = 0usize;
    let mut cpu_staging_bytes = 0usize;
    let mut pageable_host_staging_bytes = 0usize;
    let mut host_file_read_time_ms = 0.0f64;
    let mut host_preprocess_time_ms = 0.0f64;
    let mut host_to_pinned_copy_time_ms = 0.0f64;
    let mut graph_state: Option<PositionGraphCaptureState> = None;

    for batch_plan in &batch_plans {
        let loaded = load_file_batch_into_host_buffer(&mut host_buffer, batch_plan)?;
        cpu_staging_bytes = cpu_staging_bytes.saturating_add(loaded.cpu_staging_bytes);
        pageable_host_staging_bytes =
            pageable_host_staging_bytes.saturating_add(loaded.pageable_host_staging_bytes);
        host_file_read_time_ms += loaded.host_file_read_time_ms;
        host_preprocess_time_ms += loaded.host_preprocess_time_ms;
        host_to_pinned_copy_time_ms += loaded.host_to_pinned_copy_time_ms;
        if loaded.bytes_used == 0 {
            continue;
        }
        for (pattern_batch_index, pattern_batch) in device_pattern_batches.iter().enumerate() {
            let max_matches = resolve_max_match_capacity(
                loaded.bytes_used,
                pattern_batch.host.global_pattern_ids.len(),
            )?;
            let max_matches_u32 = u32::try_from(max_matches)
                .context("CUDA graph benchmark exceeds u32 match capacity")?;
            let signature = GraphCaptureSignature {
                bytes_used: loaded.bytes_used,
                short_line_count: 0,
                medium_line_count: 0,
                long_line_count: 0,
                pattern_batch_index,
            };
            transfer_bytes = transfer_bytes.saturating_add(loaded.bytes_used);

            let (match_count, positions, pattern_ids, elapsed_ms) = if use_cuda_graphs {
                if graph_state
                    .as_ref()
                    .map(|state| state.signature != signature)
                    .unwrap_or(true)
                {
                    graph_state = Some(capture_position_graph_search(
                        &runtime,
                        &stream,
                        &host_buffer,
                        &mut data_buffer,
                        &mut match_positions_buffer,
                        &mut match_pattern_ids_buffer,
                        &mut match_count_buffer,
                        pattern_batch,
                        max_matches,
                        max_matches_u32,
                        signature,
                    )?);
                    cuda_graph_captures += 1;
                } else {
                    cuda_graph_replays += 1;
                }

                let state = graph_state
                    .as_ref()
                    .context("missing CUDA graph state for graph benchmark search")?;
                let launched_at = Instant::now();
                state.graph.launch()?;
                stream
                    .synchronize()
                    .map_err(anyhow::Error::new)
                    .context("failed to synchronize CUDA graph benchmark stream")?;
                let elapsed_ms = (launched_at.elapsed().as_secs_f64() * 1_000.0) as f32;
                let match_count = state
                    .match_count_host
                    .as_slice()
                    .map_err(anyhow::Error::new)
                    .context("failed to read graphed benchmark match count")?
                    .first()
                    .copied()
                    .unwrap_or(0) as usize;
                let positions = state
                    .match_positions_host
                    .as_slice()
                    .map_err(anyhow::Error::new)
                    .context("failed to read graphed benchmark match positions")?
                    .get(..match_count)
                    .unwrap_or(&[])
                    .to_vec();
                let pattern_ids = state
                    .match_pattern_ids_host
                    .as_slice()
                    .map_err(anyhow::Error::new)
                    .context("failed to read graphed benchmark match pattern ids")?
                    .get(..match_count)
                    .unwrap_or(&[])
                    .to_vec();
                (match_count, positions, pattern_ids, elapsed_ms)
            } else {
                let launched_at = Instant::now();
                stream
                    .memset_zeros(&mut match_count_buffer)
                    .map_err(anyhow::Error::new)
                    .context("failed to reset graph benchmark match counter")?;
                copy_pinned_host_to_device(
                    &stream,
                    &host_buffer,
                    loaded.bytes_used,
                    &mut data_buffer,
                )
                .context("failed to copy graph benchmark batch to CUDA device")?;
                let data_len = i32::try_from(loaded.bytes_used)
                    .context("graph benchmark batch exceeds i32 length")?;
                launch_position_search_kernel(
                    &stream,
                    &runtime.position_function,
                    &data_buffer,
                    data_len,
                    pattern_batch,
                    &mut match_positions_buffer,
                    &mut match_pattern_ids_buffer,
                    max_matches_u32,
                    &mut match_count_buffer,
                )?;
                stream
                    .synchronize()
                    .map_err(anyhow::Error::new)
                    .context("failed to synchronize CUDA graph benchmark baseline stream")?;
                let elapsed_ms = (launched_at.elapsed().as_secs_f64() * 1_000.0) as f32;
                let match_count = stream
                    .clone_dtoh(&match_count_buffer)
                    .map_err(anyhow::Error::new)
                    .context("failed to copy graph benchmark match count back to host")?
                    .into_iter()
                    .next()
                    .unwrap_or(0) as usize;
                let positions = if match_count == 0 {
                    Vec::new()
                } else {
                    let view = match_positions_buffer
                        .try_slice(0..match_count)
                        .context("failed to slice graph benchmark match positions buffer")?;
                    stream
                        .clone_dtoh(&view)
                        .map_err(anyhow::Error::new)
                        .context("failed to copy graph benchmark match positions back to host")?
                };
                let pattern_ids = if match_count == 0 {
                    Vec::new()
                } else {
                    let view = match_pattern_ids_buffer
                        .try_slice(0..match_count)
                        .context("failed to slice graph benchmark match pattern id buffer")?;
                    stream
                        .clone_dtoh(&view)
                        .map_err(anyhow::Error::new)
                        .context("failed to copy graph benchmark match pattern ids back to host")?
                };
                (match_count, positions, pattern_ids, elapsed_ms)
            };

            transfer_time_ms += elapsed_ms;
            kernel_time_ms += elapsed_ms;

            if match_count == 0 {
                continue;
            }
            let mut positions = positions
                .into_iter()
                .zip(pattern_ids.into_iter())
                .map(|(byte_offset, local_pattern_id)| PatternMatchPosition {
                    byte_offset: byte_offset as usize,
                    pattern_id: pattern_batch.host.global_pattern_ids[local_pattern_id as usize],
                })
                .collect::<Vec<_>>();
            positions.sort();
            matches.extend(convert_offsets_to_line_matches(
                &host_buffer.as_slice()?[..loaded.bytes_used],
                &loaded.files,
                &positions,
                &config.patterns,
            )?);
        }
    }

    let file_order = files
        .iter()
        .enumerate()
        .map(|(index, path)| (path.clone(), index))
        .collect::<HashMap<_, _>>();
    matches.sort_by(|left, right| {
        let left_index = file_order.get(&left.path).copied().unwrap_or(usize::MAX);
        let right_index = file_order.get(&right.path).copied().unwrap_or(usize::MAX);
        left_index
            .cmp(&right_index)
            .then(left.line_number.cmp(&right.line_number))
            .then(left.pattern_id.cmp(&right.pattern_id))
            .then(left.raw.cmp(&right.raw))
    });
    let mut seen = BTreeSet::new();
    matches.retain(|matched| {
        seen.insert((
            matched.path.clone(),
            matched.line_number,
            matched.raw.clone(),
            matched.pattern_id,
        ))
    });
    let matched_files = matches
        .iter()
        .map(|matched| matched.path.clone())
        .collect::<BTreeSet<_>>()
        .len();
    let wall_time_ms = started_at.elapsed().as_secs_f64() * 1_000.0;
    let pipeline = GpuPipelineStats {
        pinned_host_buffers: true,
        double_buffered: false,
        stream_count: 1,
        batch_count: batch_plans.len().saturating_mul(pattern_batches.len()),
        overlapped_batches: 0,
        cuda_graph_captures,
        cuda_graph_replays,
        pattern_count: config.patterns.len(),
        pattern_batch_count: pattern_batches.len(),
        single_dispatch: batch_plans.len() == 1 && pattern_batches.len() == 1,
        short_line_count: 0,
        medium_line_count: 0,
        long_line_count: 0,
        warp_dispatch_count: 0,
        block_dispatch_count: 0,
        cpu_staging_bytes,
        pageable_host_staging_bytes,
        host_file_read_time_ms,
        host_preprocess_time_ms,
        host_to_pinned_copy_time_ms,
        cpu_staging_time_ms: host_file_read_time_ms
            + host_preprocess_time_ms
            + host_to_pinned_copy_time_ms,
        transfer_time_ms,
        kernel_time_ms,
        wall_time_ms,
        transfer_throughput_bytes_s: if wall_time_ms > 0.0 {
            transfer_bytes as f64 / (wall_time_ms / 1_000.0)
        } else {
            0.0
        },
    };
    let device_stats = vec![GpuNativeDeviceStats {
        device: selected_device.clone(),
        searched_files: files.len(),
        matched_files,
        total_matches: matches.len(),
        transfer_bytes,
        pipeline: pipeline.clone(),
    }];

    Ok(GpuNativeSearchStats {
        searched_files: files.len(),
        matched_files,
        total_matches: matches.len(),
        transfer_bytes,
        pattern_count: config.patterns.len(),
        selected_device: selected_device.clone(),
        selected_devices: vec![selected_device],
        device_stats,
        matches,
        pipeline,
        walk_errors: walked.walk_errors,
    })
}

pub(super) fn load_file_batch_into_host_buffer(
    host_buffer: &mut RawPinnedHostBuffer,
    plan: &FileBatchPlan,
) -> Result<LoadedFileBatch> {
    load_file_batch_into_pinned_slice(host_buffer.as_mut_slice()?, plan, false)
}

pub(super) fn load_file_batch_into_pinned_slice(
    host: &mut [u8],
    plan: &FileBatchPlan,
    classify_lines: bool,
) -> Result<LoadedFileBatch> {
    let mut cursor = 0usize;
    let mut batch_files = Vec::new();
    let mut classified_lines = ClassifiedLineBatch::default();
    let mut cpu_staging_bytes = 0usize;
    let mut pageable_host_staging_bytes = 0usize;
    let mut host_file_read_time_ms = 0.0f64;
    let mut host_preprocess_time_ms = 0.0f64;
    let mut host_to_pinned_copy_time_ms = 0.0f64;
    let mut pageable_buffer = Vec::new();

    for path in &plan.files {
        let metadata = fs::metadata(path)
            .with_context(|| format!("failed to stat GPU native search file {}", path.display()))?;
        let file_len = usize::try_from(metadata.len()).with_context(|| {
            format!(
                "GPU native search file is too large for this platform: {}",
                path.display()
            )
        })?;
        let separator_len = usize::from(cursor > 0);
        let start = cursor
            .checked_add(separator_len)
            .with_context(|| format!("GPU native batch offset overflow for {}", path.display()))?;
        let end = start
            .checked_add(file_len)
            .with_context(|| format!("GPU native batch offset overflow for {}", path.display()))?;
        ensure_capacity(host.len(), end, path)?;

        let read_started_at = Instant::now();
        pageable_buffer.resize(file_len, 0);
        let mut file = fs::File::open(path)
            .with_context(|| format!("failed to open GPU native search file {}", path.display()))?;
        file.read_exact(&mut pageable_buffer[..file_len])
            .with_context(|| {
                format!(
                    "failed to read GPU native search file into host staging memory {}",
                    path.display()
                )
            })?;
        host_file_read_time_ms += read_started_at.elapsed().as_secs_f64() * 1_000.0;
        pageable_host_staging_bytes = pageable_host_staging_bytes.saturating_add(file_len);

        let preprocess_started_at = Instant::now();
        let file_bytes = &pageable_buffer[..file_len];
        if memchr(b'\0', file_bytes).is_some() {
            host_preprocess_time_ms += preprocess_started_at.elapsed().as_secs_f64() * 1_000.0;
            continue;
        }

        let line_descriptors = if classify_lines {
            classify_file_lines(start, file_bytes, &mut classified_lines)?
        } else {
            Vec::new()
        };
        host_preprocess_time_ms += preprocess_started_at.elapsed().as_secs_f64() * 1_000.0;

        let pinned_copy_started_at = Instant::now();
        if cursor > 0 {
            host[cursor] = 0;
        }
        host[start..end].copy_from_slice(file_bytes);
        host_to_pinned_copy_time_ms += pinned_copy_started_at.elapsed().as_secs_f64() * 1_000.0;
        cursor = end;
        cpu_staging_bytes = cpu_staging_bytes.saturating_add(file_len);

        batch_files.push(BatchedFile {
            path: path.clone(),
            start,
            end,
            line_descriptors,
        });
    }

    Ok(LoadedFileBatch {
        files: batch_files,
        bytes_used: cursor,
        classified_lines,
        cpu_staging_bytes,
        pageable_host_staging_bytes,
        host_file_read_time_ms,
        host_preprocess_time_ms,
        host_to_pinned_copy_time_ms,
    })
}

#[allow(clippy::too_many_arguments)]
pub(super) fn capture_position_graph_search(
    runtime: &KernelRuntime,
    stream: &Arc<CudaStream>,
    host_buffer: &RawPinnedHostBuffer,
    data_buffer: &mut CudaSlice<u8>,
    match_positions_buffer: &mut CudaSlice<u32>,
    match_pattern_ids_buffer: &mut CudaSlice<u32>,
    match_count_buffer: &mut CudaSlice<u32>,
    pattern_batch: &DevicePatternBatch,
    max_matches: usize,
    max_matches_u32: u32,
    signature: GraphCaptureSignature,
) -> Result<PositionGraphCaptureState> {
    let mut match_count_host = unsafe { runtime.context.alloc_pinned::<u32>(1) }
        .map_err(anyhow::Error::new)
        .context("failed to allocate graphed benchmark match count host buffer")?;
    let mut match_positions_host =
        unsafe { runtime.context.alloc_pinned::<u32>(max_matches.max(1)) }
            .map_err(anyhow::Error::new)
            .context("failed to allocate graphed benchmark match positions host buffer")?;
    let mut match_pattern_ids_host =
        unsafe { runtime.context.alloc_pinned::<u32>(max_matches.max(1)) }
            .map_err(anyhow::Error::new)
            .context("failed to allocate graphed benchmark match pattern ids host buffer")?;
    let capture_input_ptr = host_buffer.as_slice()?.as_ptr();
    let match_count_host_ptr = match_count_host
        .as_mut_ptr()
        .map_err(anyhow::Error::new)
        .context("failed to access graphed benchmark match count host pointer")?;
    let match_positions_host_ptr = match_positions_host
        .as_mut_ptr()
        .map_err(anyhow::Error::new)
        .context("failed to access graphed benchmark match positions host pointer")?;
    let match_pattern_ids_host_ptr = match_pattern_ids_host
        .as_mut_ptr()
        .map_err(anyhow::Error::new)
        .context("failed to access graphed benchmark match pattern ids host pointer")?;

    stream
        .synchronize()
        .map_err(anyhow::Error::new)
        .context("failed to synchronize graph benchmark stream before capture")?;
    let _event_tracking_guard = EventTrackingModeGuard::disable(&runtime.context);
    stream
        .begin_capture(sys::CUstreamCaptureMode::CU_STREAM_CAPTURE_MODE_RELAXED)
        .map_err(anyhow::Error::new)
        .context("failed to begin CUDA graph capture for benchmark search")?;

    let capture = (|| {
        stream
            .memset_zeros(match_count_buffer)
            .map_err(anyhow::Error::new)
            .context("failed to reset graphed benchmark match counter")?;
        let capture_input =
            unsafe { std::slice::from_raw_parts(capture_input_ptr, signature.bytes_used) };
        copy_host_slice_to_device_during_capture(stream, capture_input, data_buffer)
            .context("failed to copy graphed benchmark batch to CUDA device")?;
        let data_len = i32::try_from(signature.bytes_used)
            .context("graphed benchmark batch exceeds i32 length")?;
        launch_position_search_kernel(
            stream,
            &runtime.position_function,
            data_buffer,
            data_len,
            pattern_batch,
            match_positions_buffer,
            match_pattern_ids_buffer,
            max_matches_u32,
            match_count_buffer,
        )?;
        let match_count_host_slice =
            unsafe { std::slice::from_raw_parts_mut(match_count_host_ptr, 1) };
        copy_device_match_count_to_host_slice_during_capture(
            stream,
            match_count_buffer,
            match_count_host_slice,
        )
        .context("failed to copy graphed benchmark match count to host")?;
        let match_positions_host_slice =
            unsafe { std::slice::from_raw_parts_mut(match_positions_host_ptr, max_matches.max(1)) };
        copy_device_u32_slice_to_host_during_capture(
            stream,
            match_positions_buffer,
            match_positions_host_slice,
            "copy graphed benchmark match positions to host",
        )?;
        let match_pattern_ids_host_slice = unsafe {
            std::slice::from_raw_parts_mut(match_pattern_ids_host_ptr, max_matches.max(1))
        };
        copy_device_u32_slice_to_host_during_capture(
            stream,
            match_pattern_ids_buffer,
            match_pattern_ids_host_slice,
            "copy graphed benchmark match pattern ids to host",
        )?;

        let graph = CapturedCudaGraph::from_captured_stream(stream)?
            .context("CUDA graph capture for benchmark search produced no graph")?;
        Ok::<PositionGraphCaptureState, anyhow::Error>(PositionGraphCaptureState {
            signature,
            graph,
            match_count_host,
            match_positions_host,
            match_pattern_ids_host,
        })
    })();

    match capture {
        Ok(state) => Ok(state),
        Err(err) => {
            if let Ok(graph) = unsafe { cuda_result::stream::end_capture(stream.cu_stream()) } {
                if !graph.is_null() {
                    let _ = unsafe { cuda_result::graph::destroy(graph) };
                }
            }
            Err(err)
        }
    }
}

pub(super) fn gpu_native_search_paths_multi_with_options(
    config: &GpuNativeSearchConfig,
    device_ids: &[i32],
    options: SearchExecutionOptions,
) -> Result<GpuNativeSearchStats> {
    if config.patterns.is_empty() {
        return Err(anyhow!(
            "GPU native search requires at least one non-empty pattern"
        ));
    }
    if config.paths.is_empty() {
        return Err(anyhow!(
            "GPU native search requires at least one search path"
        ));
    }

    if config.patterns.iter().any(|pattern| pattern.is_empty()) {
        return Err(anyhow!(
            "GPU native search requires all patterns to be non-empty"
        ));
    }

    if device_ids.is_empty() {
        return Err(anyhow!(
            "GPU native search requires at least one CUDA device id"
        ));
    }

    // Task 316: the walk now returns its error count alongside the paths, so the GPU route
    // can disclose an incomplete file list instead of silently reporting a short answer.
    let walked = collect_search_files(config)?;
    let files = walked.files;
    let selected_devices = resolve_cuda_devices(device_ids)?;
    let assignments = assign_files_to_devices(&files, &selected_devices)?;
    let device_outcomes = assignments
        .into_par_iter()
        .map(|assignment| run_device_assignment(config, assignment, options))
        .collect::<Result<Vec<_>>>()?;
    let matches = merge_device_matches(&files, &device_outcomes);
    let matched_files = matches
        .iter()
        .map(|matched| matched.path.clone())
        .collect::<BTreeSet<_>>()
        .len();
    let transfer_bytes = device_outcomes
        .iter()
        .map(|outcome| outcome.stats.transfer_bytes)
        .sum();
    let device_stats = device_outcomes
        .iter()
        .map(|outcome| outcome.stats.clone())
        .collect::<Vec<_>>();
    let selected_device = selected_devices
        .first()
        .cloned()
        .context("GPU native search requires at least one resolved CUDA device")?;
    let pipeline =
        aggregate_device_pipeline_stats(&device_outcomes, config.patterns.len(), transfer_bytes);

    Ok(GpuNativeSearchStats {
        searched_files: files.len(),
        matched_files,
        total_matches: matches.len(),
        transfer_bytes,
        pattern_count: config.patterns.len(),
        selected_device,
        selected_devices,
        device_stats,
        matches,
        pipeline,
        walk_errors: walked.walk_errors,
    })
}

pub(super) fn run_device_assignment(
    config: &GpuNativeSearchConfig,
    assignment: DeviceFileAssignment,
    options: SearchExecutionOptions,
) -> Result<DeviceSearchOutcome> {
    let files = assignment
        .files
        .iter()
        .map(|entry| entry.path.clone())
        .collect::<Vec<_>>();
    let outcome = run_device_search(config, &files, &assignment.device, options)?;
    Ok(DeviceSearchOutcome {
        stats: GpuNativeDeviceStats {
            device: assignment.device,
            searched_files: files.len(),
            matched_files: outcome
                .matches
                .iter()
                .map(|matched| matched.path.clone())
                .collect::<BTreeSet<_>>()
                .len(),
            total_matches: outcome.matches.len(),
            transfer_bytes: outcome.transfer_bytes,
            pipeline: outcome.pipeline.clone(),
        },
        matches: outcome.matches,
    })
}

pub(super) fn run_device_search(
    config: &GpuNativeSearchConfig,
    files: &[PathBuf],
    selected_device: &CudaDeviceInfo,
    options: SearchExecutionOptions,
) -> Result<SearchPipelineOutcome> {
    let pattern_batches = plan_pattern_batches(&config.patterns)?;
    let max_patterns_per_dispatch = pattern_batches
        .iter()
        .map(|batch| batch.global_pattern_ids.len())
        .max()
        .unwrap_or(1);
    let batch_plans = plan_file_batches(
        files,
        resolve_effective_max_batch_bytes(config, max_patterns_per_dispatch),
    )?;
    let dispatch_plans = plan_search_dispatches(&batch_plans, pattern_batches.len());
    let slot_capacity = batch_plans
        .iter()
        .map(|plan| plan.estimated_bytes)
        .max()
        .unwrap_or(0)
        .max(1);

    if batch_plans.is_empty() {
        return Ok(SearchPipelineOutcome {
            transfer_bytes: 0,
            matches: Vec::new(),
            pipeline: GpuPipelineStats::default(),
        });
    }

    let runtime = create_kernel_runtime(selected_device.device_id)?;
    run_search_pipeline(
        &runtime,
        &dispatch_plans,
        &pattern_batches,
        slot_capacity,
        files.len(),
        &config.patterns,
        options,
    )
}

pub(super) fn aggregate_device_pipeline_stats(
    device_outcomes: &[DeviceSearchOutcome],
    pattern_count: usize,
    transfer_bytes: usize,
) -> GpuPipelineStats {
    if device_outcomes.is_empty() {
        return GpuPipelineStats::default();
    }

    let transfer_time_ms = device_outcomes
        .iter()
        .map(|outcome| outcome.stats.pipeline.transfer_time_ms)
        .sum::<f32>();
    let host_file_read_time_ms = device_outcomes
        .iter()
        .map(|outcome| outcome.stats.pipeline.host_file_read_time_ms)
        .sum::<f64>();
    let host_preprocess_time_ms = device_outcomes
        .iter()
        .map(|outcome| outcome.stats.pipeline.host_preprocess_time_ms)
        .sum::<f64>();
    let host_to_pinned_copy_time_ms = device_outcomes
        .iter()
        .map(|outcome| outcome.stats.pipeline.host_to_pinned_copy_time_ms)
        .sum::<f64>();
    let wall_time_ms = device_outcomes
        .iter()
        .map(|outcome| outcome.stats.pipeline.wall_time_ms)
        .fold(0.0f64, f64::max);
    let transfer_throughput_bytes_s = if device_outcomes.len() == 1 {
        device_outcomes[0]
            .stats
            .pipeline
            .transfer_throughput_bytes_s
    } else if wall_time_ms > 0.0 {
        transfer_bytes as f64 / (wall_time_ms / 1_000.0)
    } else {
        0.0
    };

    GpuPipelineStats {
        pinned_host_buffers: device_outcomes
            .iter()
            .all(|outcome| outcome.stats.pipeline.pinned_host_buffers),
        double_buffered: device_outcomes
            .iter()
            .any(|outcome| outcome.stats.pipeline.double_buffered),
        stream_count: device_outcomes
            .iter()
            .map(|outcome| outcome.stats.pipeline.stream_count)
            .sum(),
        batch_count: device_outcomes
            .iter()
            .map(|outcome| outcome.stats.pipeline.batch_count)
            .sum(),
        overlapped_batches: device_outcomes
            .iter()
            .map(|outcome| outcome.stats.pipeline.overlapped_batches)
            .sum(),
        cuda_graph_captures: device_outcomes
            .iter()
            .map(|outcome| outcome.stats.pipeline.cuda_graph_captures)
            .sum(),
        cuda_graph_replays: device_outcomes
            .iter()
            .map(|outcome| outcome.stats.pipeline.cuda_graph_replays)
            .sum(),
        pattern_count,
        pattern_batch_count: device_outcomes
            .iter()
            .map(|outcome| outcome.stats.pipeline.pattern_batch_count)
            .max()
            .unwrap_or(0),
        single_dispatch: device_outcomes
            .iter()
            .all(|outcome| outcome.stats.pipeline.single_dispatch),
        short_line_count: device_outcomes
            .iter()
            .map(|outcome| outcome.stats.pipeline.short_line_count)
            .sum(),
        medium_line_count: device_outcomes
            .iter()
            .map(|outcome| outcome.stats.pipeline.medium_line_count)
            .sum(),
        long_line_count: device_outcomes
            .iter()
            .map(|outcome| outcome.stats.pipeline.long_line_count)
            .sum(),
        warp_dispatch_count: device_outcomes
            .iter()
            .map(|outcome| outcome.stats.pipeline.warp_dispatch_count)
            .sum(),
        block_dispatch_count: device_outcomes
            .iter()
            .map(|outcome| outcome.stats.pipeline.block_dispatch_count)
            .sum(),
        cpu_staging_bytes: device_outcomes
            .iter()
            .map(|outcome| outcome.stats.pipeline.cpu_staging_bytes)
            .sum(),
        pageable_host_staging_bytes: device_outcomes
            .iter()
            .map(|outcome| outcome.stats.pipeline.pageable_host_staging_bytes)
            .sum(),
        host_file_read_time_ms,
        host_preprocess_time_ms,
        host_to_pinned_copy_time_ms,
        cpu_staging_time_ms: host_file_read_time_ms
            + host_preprocess_time_ms
            + host_to_pinned_copy_time_ms,
        transfer_time_ms,
        kernel_time_ms: device_outcomes
            .iter()
            .map(|outcome| outcome.stats.pipeline.kernel_time_ms)
            .sum(),
        wall_time_ms,
        transfer_throughput_bytes_s,
    }
}

pub(super) fn merge_device_matches(
    files: &[PathBuf],
    device_outcomes: &[DeviceSearchOutcome],
) -> Vec<GpuNativeSearchMatch> {
    let file_order = files
        .iter()
        .enumerate()
        .map(|(index, path)| (path.clone(), index))
        .collect::<HashMap<_, _>>();
    let mut matches = device_outcomes
        .iter()
        .flat_map(|outcome| outcome.matches.clone())
        .collect::<Vec<_>>();
    matches.sort_by(|left, right| {
        let left_index = file_order.get(&left.path).copied().unwrap_or(usize::MAX);
        let right_index = file_order.get(&right.path).copied().unwrap_or(usize::MAX);
        left_index
            .cmp(&right_index)
            .then(left.line_number.cmp(&right.line_number))
            .then(left.pattern_id.cmp(&right.pattern_id))
            .then(left.raw.cmp(&right.raw))
    });

    let mut seen = BTreeSet::new();
    matches
        .into_iter()
        .filter(|matched| {
            seen.insert((
                matched.path.clone(),
                matched.line_number,
                matched.raw.clone(),
                matched.pattern_id,
            ))
        })
        .collect()
}
