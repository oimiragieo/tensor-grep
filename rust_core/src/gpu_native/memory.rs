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

pub fn gpu_native_search(pattern: &str, data: &[u8], device_id: i32) -> Result<Vec<MatchPosition>> {
    Ok(gpu_native_search_patterns(&[pattern], data, device_id)?
        .into_iter()
        .map(|matched| MatchPosition {
            byte_offset: matched.byte_offset,
        })
        .collect())
}

pub fn gpu_native_search_patterns(
    patterns: &[&str],
    data: &[u8],
    device_id: i32,
) -> Result<Vec<PatternMatchPosition>> {
    if patterns.is_empty() || patterns.iter().any(|pattern| pattern.is_empty()) {
        return Err(anyhow!(
            "GPU native search requires at least one non-empty pattern"
        ));
    }
    if data.is_empty() {
        return Ok(Vec::new());
    }

    let pattern_strings = patterns
        .iter()
        .map(|pattern| (*pattern).to_string())
        .collect::<Vec<_>>();
    let pattern_batches = plan_pattern_batches(&pattern_strings)?;
    let runtime = create_kernel_runtime(device_id)?;
    let stream = runtime.context.default_stream();
    let data_device = stream
        .clone_htod(data)
        .map_err(anyhow::Error::new)
        .context("failed to copy search data to CUDA device")?;
    let max_pattern_count = pattern_batches
        .iter()
        .map(|batch| batch.global_pattern_ids.len())
        .max()
        .unwrap_or(1);
    let max_matches_capacity = resolve_max_match_capacity(data.len(), max_pattern_count)?;
    let max_matches_u32 = u32::try_from(max_matches_capacity)
        .context("GPU native search exceeds u32 match capacity")?;
    let mut match_positions_device = stream
        .alloc_zeros::<u32>(max_matches_capacity)
        .map_err(anyhow::Error::new)
        .context("failed to allocate CUDA match position buffer")?;
    let mut match_pattern_ids_device = stream
        .alloc_zeros::<u32>(max_matches_capacity)
        .map_err(anyhow::Error::new)
        .context("failed to allocate CUDA match pattern id buffer")?;
    let mut match_count_device = stream
        .alloc_zeros::<u32>(1)
        .map_err(anyhow::Error::new)
        .context("failed to allocate CUDA match counter")?;
    let device_pattern_batches = upload_pattern_batches(&runtime, &pattern_batches)?;

    let data_len =
        i32::try_from(data.len()).context("GPU native search input exceeds i32 length")?;
    let mut matches = Vec::new();
    for device_batch in &device_pattern_batches {
        stream
            .memset_zeros(&mut match_count_device)
            .map_err(anyhow::Error::new)
            .context("failed to reset CUDA match counter")?;
        launch_position_search_kernel(
            &stream,
            &runtime.position_function,
            &data_device,
            data_len,
            device_batch,
            &mut match_positions_device,
            &mut match_pattern_ids_device,
            max_matches_u32,
            &mut match_count_device,
        )?;

        let match_count = stream
            .clone_dtoh(&match_count_device)
            .map_err(anyhow::Error::new)
            .context("failed to copy CUDA match count back to host")?
            .into_iter()
            .next()
            .unwrap_or(0) as usize;
        if match_count == 0 {
            continue;
        }
        if match_count > max_matches_capacity {
            return Err(anyhow!(
                "GPU native search match buffer overflow: observed {match_count} matches with capacity {max_matches_capacity}"
            ));
        }

        let match_positions_view = match_positions_device
            .try_slice(0..match_count)
            .context("failed to slice CUDA match positions buffer")?;
        let match_pattern_ids_view = match_pattern_ids_device
            .try_slice(0..match_count)
            .context("failed to slice CUDA match pattern id buffer")?;
        let positions = stream
            .clone_dtoh(&match_positions_view)
            .map_err(anyhow::Error::new)
            .context("failed to copy CUDA match positions back to host")?;
        let pattern_ids = stream
            .clone_dtoh(&match_pattern_ids_view)
            .map_err(anyhow::Error::new)
            .context("failed to copy CUDA match pattern ids back to host")?;

        matches.extend(positions.into_iter().zip(pattern_ids.into_iter()).map(
            |(byte_offset, local_pattern_id)| PatternMatchPosition {
                byte_offset: byte_offset as usize,
                pattern_id: device_batch.host.global_pattern_ids[local_pattern_id as usize],
            },
        ));
    }

    matches.sort();
    Ok(matches)
}

pub(super) fn create_kernel_runtime(device_id: i32) -> Result<KernelRuntime> {
    validate_device_id(device_id)?;
    let context = open_cuda_context(device_id)?;
    let module = compile_kernel_module(&context, device_id)?;
    let position_function = module
        .load_function(POSITION_SEARCH_KERNEL_NAME)
        .map_err(anyhow::Error::new)
        .with_context(|| {
            format!(
                "failed to load CUDA kernel `{POSITION_SEARCH_KERNEL_NAME}` for device {device_id}"
            )
        })?;
    let short_line_function = module
        .load_function(SHORT_LINE_SEARCH_KERNEL_NAME)
        .map_err(anyhow::Error::new)
        .with_context(|| {
            format!(
                "failed to load CUDA kernel `{SHORT_LINE_SEARCH_KERNEL_NAME}` for device {device_id}"
            )
        })?;
    let warp_line_function = module
        .load_function(WARP_LINE_SEARCH_KERNEL_NAME)
        .map_err(anyhow::Error::new)
        .with_context(|| {
            format!(
                "failed to load CUDA kernel `{WARP_LINE_SEARCH_KERNEL_NAME}` for device {device_id}"
            )
        })?;
    let block_line_function = module
        .load_function(BLOCK_LINE_SEARCH_KERNEL_NAME)
        .map_err(anyhow::Error::new)
        .with_context(|| {
            format!(
                "failed to load CUDA kernel `{BLOCK_LINE_SEARCH_KERNEL_NAME}` for device {device_id}"
            )
        })?;

    Ok(KernelRuntime {
        context,
        _module: module,
        position_function,
        short_line_function,
        warp_line_function,
        block_line_function,
    })
}

pub(super) fn run_search_pipeline(
    runtime: &KernelRuntime,
    dispatch_plans: &[SearchDispatchPlan],
    pattern_batches: &[PatternBatchPlan],
    slot_capacity: usize,
    _searched_files: usize,
    all_patterns: &[String],
    options: SearchExecutionOptions,
) -> Result<SearchPipelineOutcome> {
    let mut slots = (0..PIPELINE_SLOT_COUNT)
        .map(|_| create_search_pipeline_slot(runtime, slot_capacity, pattern_batches))
        .collect::<Result<Vec<_>>>()?;
    let coordinator_stream = runtime
        .context
        .new_stream()
        .map_err(anyhow::Error::new)
        .context("failed to create CUDA coordinator stream for GPU search pipeline")?;
    let started_at = Instant::now();

    let mut transfer_bytes = 0usize;
    let mut matches = Vec::new();
    let mut transfer_time_ms = 0.0f32;
    let mut kernel_time_ms = 0.0f32;
    let mut overlapped_device_time_ms = 0.0f32;
    let mut short_line_count = 0usize;
    let mut medium_line_count = 0usize;
    let mut long_line_count = 0usize;
    let mut warp_dispatch_count = 0usize;
    let mut block_dispatch_count = 0usize;
    let mut cuda_graph_captures = 0usize;
    let mut cuda_graph_replays = 0usize;
    let mut cpu_staging_bytes = 0usize;
    let mut pageable_host_staging_bytes = 0usize;
    let mut host_file_read_time_ms = 0.0f64;
    let mut host_preprocess_time_ms = 0.0f64;
    let mut host_to_pinned_copy_time_ms = 0.0f64;
    let active_stream_count = dispatch_plans.len().clamp(1, PIPELINE_SLOT_COUNT);
    let mut pipeline_start = None;

    for (dispatch_index, plan) in dispatch_plans.iter().enumerate() {
        let slot = &mut slots[dispatch_index % PIPELINE_SLOT_COUNT];
        finalize_search_pipeline_slot(
            slot,
            all_patterns,
            &mut transfer_bytes,
            &mut matches,
            &mut transfer_time_ms,
            &mut kernel_time_ms,
            &mut overlapped_device_time_ms,
            &mut short_line_count,
            &mut medium_line_count,
            &mut long_line_count,
            &mut warp_dispatch_count,
            &mut block_dispatch_count,
            &mut cuda_graph_captures,
            &mut cuda_graph_replays,
            &mut cpu_staging_bytes,
            &mut pageable_host_staging_bytes,
            &mut host_file_read_time_ms,
            &mut host_preprocess_time_ms,
            &mut host_to_pinned_copy_time_ms,
        )?;
        load_file_batch_into_slot(slot, &plan.file_batch)?;
        slot.current_pattern_batch_index = Some(plan.pattern_batch_index);
        if pipeline_start.is_none() {
            pipeline_start = Some(record_timed_event(&slot.stream)?);
        }
        launch_slot_search(slot, runtime, plan.pattern_batch_index, options)?;
    }

    let pipeline_end = if let Some(start) = pipeline_start.as_ref() {
        for slot in &slots {
            if let Some(kernel_end) = slot.kernel_end.as_ref() {
                coordinator_stream
                    .wait(kernel_end)
                    .map_err(anyhow::Error::new)
                    .context("failed to coordinate GPU search pipeline completion")?;
            }
        }
        let end = record_timed_event(&coordinator_stream)?;
        end.synchronize()
            .map_err(anyhow::Error::new)
            .context("failed to synchronize GPU search pipeline end event")?;
        Some((start, end))
    } else {
        None
    };

    for slot in &mut slots {
        finalize_search_pipeline_slot(
            slot,
            all_patterns,
            &mut transfer_bytes,
            &mut matches,
            &mut transfer_time_ms,
            &mut kernel_time_ms,
            &mut overlapped_device_time_ms,
            &mut short_line_count,
            &mut medium_line_count,
            &mut long_line_count,
            &mut warp_dispatch_count,
            &mut block_dispatch_count,
            &mut cuda_graph_captures,
            &mut cuda_graph_replays,
            &mut cpu_staging_bytes,
            &mut pageable_host_staging_bytes,
            &mut host_file_read_time_ms,
            &mut host_preprocess_time_ms,
            &mut host_to_pinned_copy_time_ms,
        )?;
    }

    let wall_time_ms = if overlapped_device_time_ms > 0.0 {
        f64::from(overlapped_device_time_ms)
    } else if let Some((start, end)) = pipeline_end.as_ref() {
        f64::from(
            start
                .elapsed_ms(end)
                .map_err(anyhow::Error::new)
                .context("failed to measure GPU search pipeline wall time")?,
        )
    } else {
        started_at.elapsed().as_secs_f64() * 1_000.0
    };
    let transfer_throughput_bytes_s = if transfer_time_ms > 0.0 {
        transfer_bytes as f64 / (f64::from(transfer_time_ms) / 1_000.0)
    } else {
        0.0
    };

    Ok(SearchPipelineOutcome {
        transfer_bytes,
        matches,
        pipeline: GpuPipelineStats {
            pinned_host_buffers: true,
            double_buffered: active_stream_count >= 2,
            stream_count: active_stream_count,
            batch_count: dispatch_plans.len(),
            overlapped_batches: dispatch_plans.len().saturating_sub(active_stream_count),
            cuda_graph_captures,
            cuda_graph_replays,
            pattern_count: all_patterns.len(),
            pattern_batch_count: pattern_batches.len(),
            single_dispatch: pattern_batches.len() == 1,
            short_line_count,
            medium_line_count,
            long_line_count,
            warp_dispatch_count,
            block_dispatch_count,
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
            transfer_throughput_bytes_s,
        },
    })
}

pub(super) fn create_search_pipeline_slot(
    runtime: &KernelRuntime,
    capacity: usize,
    pattern_batches: &[PatternBatchPlan],
) -> Result<SearchPipelineSlot> {
    let stream = runtime
        .context
        .new_stream()
        .map_err(anyhow::Error::new)
        .context("failed to create CUDA stream for GPU search pipeline")?;
    let host_buffer = RawPinnedHostBuffer::new(&runtime.context, capacity)
        .context("failed to allocate pinned host buffer for GPU search pipeline")?;
    let data_buffer = unsafe { stream.alloc::<u8>(capacity) }
        .map_err(anyhow::Error::new)
        .context("failed to allocate device data buffer for GPU search pipeline")?;
    let match_buffer_capacity = 1usize;
    let match_positions_buffer = unsafe { stream.alloc::<u32>(match_buffer_capacity) }
        .map_err(anyhow::Error::new)
        .context("failed to allocate device match position buffer for GPU search pipeline")?;
    let match_pattern_ids_buffer = unsafe { stream.alloc::<u32>(match_buffer_capacity) }
        .map_err(anyhow::Error::new)
        .context("failed to allocate device match pattern id buffer for GPU search pipeline")?;
    let match_count_buffer = stream
        .alloc_zeros::<u32>(1)
        .map_err(anyhow::Error::new)
        .context("failed to allocate device match counter for GPU search pipeline")?;
    let pattern_batches = upload_pattern_batches_on_stream(&stream, pattern_batches)?;

    Ok(SearchPipelineSlot {
        stream,
        host_buffer,
        data_buffer,
        match_positions_buffer,
        match_pattern_ids_buffer,
        match_buffer_capacity,
        match_count_buffer,
        pattern_batches,
        current_batch: None,
        adaptive_dispatch: None,
        short_line_buffers: None,
        medium_line_buffers: None,
        long_line_buffers: None,
        current_pattern_batch_index: None,
        current_launch_mode: None,
        transfer_start: None,
        transfer_end: None,
        kernel_end: None,
        cuda_graph: None,
        graph_launch_started_at: None,
    })
}

pub(super) fn create_transfer_benchmark_slot(
    context: &Arc<CudaContext>,
    capacity: usize,
) -> Result<TransferBenchmarkSlot> {
    let stream = context
        .new_stream()
        .map_err(anyhow::Error::new)
        .context("failed to create CUDA stream for transfer benchmark")?;
    let host_buffer = RawPinnedHostBuffer::new(context, capacity)
        .context("failed to allocate pinned host buffer for transfer benchmark")?;
    let device_buffer = unsafe { stream.alloc::<u8>(capacity) }
        .map_err(anyhow::Error::new)
        .context("failed to allocate device transfer buffer for transfer benchmark")?;

    Ok(TransferBenchmarkSlot {
        stream,
        host_buffer,
        device_buffer,
        transfer_start: None,
        transfer_end: None,
        pending_transfer: false,
    })
}

pub(super) fn create_pageable_transfer_benchmark_slot(
    context: &Arc<CudaContext>,
    capacity: usize,
) -> Result<PageableTransferBenchmarkSlot> {
    let stream = context
        .new_stream()
        .map_err(anyhow::Error::new)
        .context("failed to create CUDA stream for pageable transfer benchmark")?;
    let host_buffer = vec![0u8; capacity.max(1)];
    let device_buffer = unsafe { stream.alloc::<u8>(capacity.max(1)) }
        .map_err(anyhow::Error::new)
        .context("failed to allocate device pageable transfer buffer for transfer benchmark")?;

    Ok(PageableTransferBenchmarkSlot {
        stream,
        host_buffer,
        device_buffer,
        transfer_start: None,
        transfer_end: None,
        pending_transfer: false,
    })
}

pub(super) fn finalize_transfer_benchmark_slot(
    slot: &mut TransferBenchmarkSlot,
    transfer_time_ms: &mut f32,
) -> Result<()> {
    if !slot.pending_transfer {
        slot.transfer_start = None;
        slot.transfer_end = None;
        return Ok(());
    }

    let transfer_start = slot
        .transfer_start
        .take()
        .context("missing transfer-start event for pinned transfer benchmark")?;
    let transfer_end = slot
        .transfer_end
        .take()
        .context("missing transfer-end event for pinned transfer benchmark")?;
    transfer_end
        .synchronize()
        .map_err(anyhow::Error::new)
        .context("failed to synchronize pinned transfer benchmark stream")?;
    *transfer_time_ms += transfer_start
        .elapsed_ms(&transfer_end)
        .map_err(anyhow::Error::new)
        .context("failed to measure pinned transfer benchmark event timing")?;
    slot.pending_transfer = false;
    Ok(())
}

pub(super) fn finalize_pageable_transfer_benchmark_slot(
    slot: &mut PageableTransferBenchmarkSlot,
    transfer_time_ms: &mut f32,
) -> Result<()> {
    if !slot.pending_transfer {
        slot.transfer_start = None;
        slot.transfer_end = None;
        return Ok(());
    }

    let transfer_start = slot
        .transfer_start
        .take()
        .context("missing transfer-start event for pageable transfer benchmark")?;
    let transfer_end = slot
        .transfer_end
        .take()
        .context("missing transfer-end event for pageable transfer benchmark")?;
    transfer_end
        .synchronize()
        .map_err(anyhow::Error::new)
        .context("failed to synchronize pageable transfer benchmark stream")?;
    *transfer_time_ms += transfer_start
        .elapsed_ms(&transfer_end)
        .map_err(anyhow::Error::new)
        .context("failed to measure pageable transfer benchmark event timing")?;
    slot.pending_transfer = false;
    Ok(())
}

#[allow(clippy::too_many_arguments)]
pub(super) fn finalize_search_pipeline_slot(
    slot: &mut SearchPipelineSlot,
    all_patterns: &[String],
    transfer_bytes: &mut usize,
    matches: &mut Vec<GpuNativeSearchMatch>,
    transfer_time_ms: &mut f32,
    kernel_time_ms: &mut f32,
    overlapped_device_time_ms: &mut f32,
    short_line_count: &mut usize,
    medium_line_count: &mut usize,
    long_line_count: &mut usize,
    warp_dispatch_count: &mut usize,
    block_dispatch_count: &mut usize,
    cuda_graph_captures: &mut usize,
    cuda_graph_replays: &mut usize,
    cpu_staging_bytes: &mut usize,
    pageable_host_staging_bytes: &mut usize,
    host_file_read_time_ms: &mut f64,
    host_preprocess_time_ms: &mut f64,
    host_to_pinned_copy_time_ms: &mut f64,
) -> Result<()> {
    let Some(batch) = slot.current_batch.take() else {
        slot.adaptive_dispatch = None;
        slot.current_pattern_batch_index = None;
        slot.current_launch_mode = None;
        slot.graph_launch_started_at = None;
        slot.transfer_start = None;
        slot.transfer_end = None;
        slot.kernel_end = None;
        return Ok(());
    };
    let adaptive_dispatch = slot
        .adaptive_dispatch
        .take()
        .context("missing adaptive dispatch metadata for GPU search pipeline")?;
    let pattern_batch_index = slot
        .current_pattern_batch_index
        .take()
        .context("missing pattern batch metadata for GPU search pipeline")?;
    let pattern_batch = &slot
        .pattern_batches
        .get(pattern_batch_index)
        .context("missing pattern batch buffers for GPU search pipeline")?
        .host;
    let launch_mode = slot
        .current_launch_mode
        .take()
        .unwrap_or(SearchLaunchMode::Standard);

    if pattern_batch_index == 0 {
        *short_line_count += adaptive_dispatch.stats.short_line_count;
        *medium_line_count += adaptive_dispatch.stats.medium_line_count;
        *long_line_count += adaptive_dispatch.stats.long_line_count;
    }
    *warp_dispatch_count += adaptive_dispatch.stats.warp_dispatch_count;
    *block_dispatch_count += adaptive_dispatch.stats.block_dispatch_count;
    match launch_mode {
        SearchLaunchMode::GraphCapture => *cuda_graph_captures += 1,
        SearchLaunchMode::GraphReplay => *cuda_graph_replays += 1,
        SearchLaunchMode::Standard => {}
    }
    *cpu_staging_bytes = cpu_staging_bytes.saturating_add(batch.cpu_staging_bytes);
    *pageable_host_staging_bytes =
        pageable_host_staging_bytes.saturating_add(batch.pageable_host_staging_bytes);
    *host_file_read_time_ms += batch.host_file_read_time_ms;
    *host_preprocess_time_ms += batch.host_preprocess_time_ms;
    *host_to_pinned_copy_time_ms += batch.host_to_pinned_copy_time_ms;

    if batch.bytes_used == 0 || adaptive_dispatch.total_lines() == 0 {
        slot.transfer_start = None;
        slot.transfer_end = None;
        slot.kernel_end = None;
        return Ok(());
    }
    *transfer_bytes += batch.bytes_used;

    let (batch_transfer_time_ms, batch_kernel_time_ms, match_count) =
        if launch_mode == SearchLaunchMode::Standard {
            let transfer_start = slot
                .transfer_start
                .take()
                .context("missing transfer-start event for GPU search pipeline")?;
            let transfer_end = slot
                .transfer_end
                .take()
                .context("missing transfer-end event for GPU search pipeline")?;
            let kernel_end = slot
                .kernel_end
                .take()
                .context("missing kernel-end event for GPU search pipeline")?;
            kernel_end
                .synchronize()
                .map_err(anyhow::Error::new)
                .context("failed to synchronize GPU search pipeline stream")?;

            let batch_transfer_time_ms = transfer_start
                .elapsed_ms(&transfer_end)
                .map_err(anyhow::Error::new)
                .context("failed to measure GPU search pipeline transfer timing")?;
            let batch_kernel_time_ms = transfer_end
                .elapsed_ms(&kernel_end)
                .map_err(anyhow::Error::new)
                .context("failed to measure GPU search pipeline kernel timing")?;
            let match_count = slot
                .stream
                .clone_dtoh(&slot.match_count_buffer)
                .map_err(anyhow::Error::new)
                .context("failed to copy GPU search pipeline match count back to host")?
                .into_iter()
                .next()
                .unwrap_or(0) as usize;
            (batch_transfer_time_ms, batch_kernel_time_ms, match_count)
        } else {
            let graph_state = slot
                .cuda_graph
                .as_ref()
                .context("missing CUDA graph state for graphed GPU search pipeline")?;
            let graph_launch_started_at = slot
                .graph_launch_started_at
                .take()
                .context("missing graphed GPU search pipeline launch timing")?;
            slot.stream
                .synchronize()
                .map_err(anyhow::Error::new)
                .context("failed to synchronize graphed GPU search pipeline stream")?;
            let elapsed_ms = (graph_launch_started_at.elapsed().as_secs_f64() * 1_000.0) as f32;
            let batch_transfer_time_ms = elapsed_ms;
            let batch_kernel_time_ms = elapsed_ms;
            let match_count = graph_state
                .match_count_host
                .as_slice()
                .map_err(anyhow::Error::new)
                .context("failed to read graphed GPU search pipeline match count host buffer")?
                .first()
                .copied()
                .unwrap_or(0) as usize;
            (batch_transfer_time_ms, batch_kernel_time_ms, match_count)
        };
    *transfer_time_ms += batch_transfer_time_ms;
    *kernel_time_ms += batch_kernel_time_ms;
    *overlapped_device_time_ms += batch_transfer_time_ms.max(batch_kernel_time_ms);
    if match_count == 0 {
        return Ok(());
    }
    let max_matches = adaptive_dispatch
        .total_lines()
        .checked_mul(pattern_batch.global_pattern_ids.len())
        .context("GPU search pipeline match capacity overflow")?;
    if match_count > max_matches {
        return Err(anyhow!(
            "GPU native search match buffer overflow: observed {match_count} matches with capacity {max_matches}"
        ));
    }

    let match_positions_view = slot
        .match_positions_buffer
        .try_slice(0..match_count)
        .context("failed to slice GPU search pipeline match positions buffer")?;
    let match_pattern_ids_view = slot
        .match_pattern_ids_buffer
        .try_slice(0..match_count)
        .context("failed to slice GPU search pipeline match pattern id buffer")?;
    let positions = slot
        .stream
        .clone_dtoh(&match_positions_view)
        .map_err(anyhow::Error::new)
        .context("failed to copy GPU search pipeline match positions back to host")?;
    let pattern_ids = slot
        .stream
        .clone_dtoh(&match_pattern_ids_view)
        .map_err(anyhow::Error::new)
        .context("failed to copy GPU search pipeline match pattern ids back to host")?;
    let mut positions = positions
        .into_iter()
        .zip(pattern_ids.into_iter())
        .map(|(byte_offset, local_pattern_id)| PatternMatchPosition {
            byte_offset: byte_offset as usize,
            pattern_id: pattern_batch.global_pattern_ids[local_pattern_id as usize],
        })
        .collect::<Vec<_>>();
    positions.sort();
    let batch_matches = convert_offsets_to_line_matches(
        &slot.host_buffer.as_slice()?[..batch.bytes_used],
        &batch.files,
        &positions,
        all_patterns,
    )?;
    matches.extend(batch_matches);
    slot.graph_launch_started_at = None;
    Ok(())
}

pub(super) fn load_file_batch_into_slot(
    slot: &mut SearchPipelineSlot,
    plan: &FileBatchPlan,
) -> Result<()> {
    slot.current_batch = Some(load_file_batch_into_pinned_slice(
        slot.host_buffer.as_mut_slice()?,
        plan,
        true,
    )?);
    slot.adaptive_dispatch = None;
    slot.current_launch_mode = None;
    slot.graph_launch_started_at = None;
    slot.transfer_start = None;
    slot.transfer_end = None;
    slot.kernel_end = None;
    Ok(())
}

pub(super) fn record_timed_event(stream: &Arc<CudaStream>) -> Result<CudaEvent> {
    stream
        .record_event(Some(sys::CUevent_flags::CU_EVENT_DEFAULT))
        .map_err(anyhow::Error::new)
        .context("failed to record CUDA timing event")
}

pub(super) fn copy_pinned_host_to_device(
    stream: &Arc<CudaStream>,
    host_buffer: &RawPinnedHostBuffer,
    num_bytes: usize,
    device_buffer: &mut CudaSlice<u8>,
) -> Result<()> {
    let (device_ptr, _record_dst) = device_buffer.device_ptr_mut(stream);
    let host_slice = &host_buffer.as_slice()?[..num_bytes];
    catch_cuda("copy pinned host buffer to CUDA device", || unsafe {
        cuda_result::memcpy_htod_async(device_ptr, host_slice, stream.cu_stream())
            .map_err(anyhow::Error::new)
    })
}

pub(super) fn copy_pageable_host_to_device(
    stream: &Arc<CudaStream>,
    host_buffer: &[u8],
    num_bytes: usize,
    device_buffer: &mut CudaSlice<u8>,
) -> Result<()> {
    let mut device_view = device_buffer.slice_mut(0..num_bytes);
    stream
        .memcpy_htod(&host_buffer[..num_bytes], &mut device_view)
        .map_err(anyhow::Error::new)
        .context("failed to copy pageable host buffer to CUDA device")
}

pub(super) fn copy_host_slice_to_device_during_capture(
    stream: &Arc<CudaStream>,
    host_slice: &[u8],
    device_buffer: &mut CudaSlice<u8>,
) -> Result<()> {
    let (device_ptr, _record_dst) = device_buffer.device_ptr_mut(stream);
    catch_cuda(
        "copy pinned host buffer to CUDA device during graph capture",
        || unsafe {
            cuda_result::memcpy_htod_async(device_ptr, host_slice, stream.cu_stream())
                .map_err(anyhow::Error::new)
        },
    )
}

pub(super) fn copy_device_match_count_to_host_slice_during_capture(
    stream: &Arc<CudaStream>,
    match_count_buffer: &CudaSlice<u32>,
    match_count_host: &mut [u32],
) -> Result<()> {
    let (device_ptr, _record_src) = match_count_buffer.device_ptr(stream);
    catch_cuda(
        "copy GPU match count to pinned host buffer during graph capture",
        || unsafe {
            cuda_result::memcpy_dtoh_async(match_count_host, device_ptr, stream.cu_stream())
                .map_err(anyhow::Error::new)
        },
    )
}

pub(super) fn copy_device_u32_slice_to_host_during_capture(
    stream: &Arc<CudaStream>,
    device_buffer: &CudaSlice<u32>,
    host_buffer: &mut [u32],
    description: &str,
) -> Result<()> {
    let (device_ptr, _record_src) = device_buffer.device_ptr(stream);
    catch_cuda(description, || unsafe {
        cuda_result::memcpy_dtoh_async(host_buffer, device_ptr, stream.cu_stream())
            .map_err(anyhow::Error::new)
    })
}

pub(super) fn resolve_max_batch_bytes(config: &GpuNativeSearchConfig) -> usize {
    config
        .max_batch_bytes
        .unwrap_or(DEFAULT_GPU_BATCH_BYTES)
        .max(1)
}

pub(super) fn resolve_effective_max_batch_bytes(
    config: &GpuNativeSearchConfig,
    max_patterns_per_dispatch: usize,
) -> usize {
    let configured = resolve_max_batch_bytes(config);
    let per_byte_cost = 1usize.saturating_add(max_patterns_per_dispatch.saturating_mul(8));
    let bounded = MAX_SLOT_DEVICE_MEMORY_BYTES
        .checked_div(per_byte_cost.max(1))
        .unwrap_or(1)
        .max(1);
    configured.min(bounded).max(1)
}

pub(super) fn resolve_max_match_capacity(bytes_used: usize, pattern_count: usize) -> Result<usize> {
    bytes_used
        .checked_mul(pattern_count.max(1))
        .context("GPU native search match capacity overflow")
}

pub(super) fn resolve_adaptive_match_capacity(
    line_count: usize,
    pattern_count: usize,
) -> Result<usize> {
    line_count
        .checked_mul(pattern_count.max(1))
        .map(|capacity| capacity.max(1))
        .context("GPU native adaptive line match capacity overflow")
}

pub(super) fn plan_pattern_batches(patterns: &[String]) -> Result<Vec<PatternBatchPlan>> {
    let mut batches = Vec::new();
    let mut current_ids = Vec::new();
    let mut current_offsets = Vec::new();
    let mut current_lengths = Vec::new();
    let mut current_blob = Vec::new();

    for (pattern_id, pattern) in patterns.iter().enumerate() {
        let pattern_len = u32::try_from(pattern.len()).with_context(|| {
            format!(
                "GPU native search pattern is too large: {} bytes",
                pattern.len()
            )
        })?;
        let candidate_pattern_count = current_ids.len() + 1;
        let candidate_blob_len = current_blob.len().saturating_add(pattern.len());
        let candidate_shared_mem =
            resolve_pattern_shared_mem_bytes(candidate_pattern_count, candidate_blob_len)?;

        if !current_ids.is_empty() && candidate_shared_mem > SHARED_PATTERN_MEMORY_LIMIT_BYTES {
            batches.push(PatternBatchPlan {
                global_pattern_ids: current_ids,
                pattern_offsets: current_offsets,
                pattern_lengths: current_lengths,
                pattern_blob: current_blob,
                shared_mem_bytes: resolve_pattern_shared_mem_bytes(
                    candidate_pattern_count - 1,
                    candidate_blob_len - pattern.len(),
                )?,
            });
            current_ids = Vec::new();
            current_offsets = Vec::new();
            current_lengths = Vec::new();
            current_blob = Vec::new();
        }

        let shared_mem_bytes = resolve_pattern_shared_mem_bytes(
            current_ids.len() + 1,
            current_blob.len() + pattern.len(),
        )?;
        if shared_mem_bytes > SHARED_PATTERN_MEMORY_LIMIT_BYTES && current_ids.is_empty() {
            return Err(anyhow!(
                "GPU native search pattern set exceeds shared memory budget even for a single pattern: {} bytes required",
                shared_mem_bytes
            ));
        }

        let offset = u32::try_from(current_blob.len())
            .context("GPU native search pattern blob exceeds u32 length")?;
        current_ids.push(pattern_id);
        current_offsets.push(offset);
        current_lengths.push(pattern_len);
        current_blob.extend_from_slice(pattern.as_bytes());
    }

    if !current_ids.is_empty() {
        let shared_mem_bytes =
            resolve_pattern_shared_mem_bytes(current_offsets.len(), current_blob.len())?;
        batches.push(PatternBatchPlan {
            global_pattern_ids: current_ids,
            pattern_offsets: current_offsets,
            pattern_lengths: current_lengths,
            pattern_blob: current_blob,
            shared_mem_bytes,
        });
    }

    Ok(batches)
}

pub(super) fn resolve_pattern_shared_mem_bytes(
    pattern_count: usize,
    pattern_blob_len: usize,
) -> Result<usize> {
    pattern_count
        .checked_mul(std::mem::size_of::<u32>() * 2)
        .and_then(|metadata| metadata.checked_add(pattern_blob_len))
        .context("GPU native search shared memory requirement overflow")
}

pub(super) fn plan_search_dispatches(
    file_batches: &[FileBatchPlan],
    pattern_batch_count: usize,
) -> Vec<SearchDispatchPlan> {
    let mut dispatches = Vec::new();
    for file_batch in file_batches {
        for pattern_batch_index in 0..pattern_batch_count {
            dispatches.push(SearchDispatchPlan {
                file_batch: file_batch.clone(),
                pattern_batch_index,
            });
        }
    }
    dispatches
}

pub(super) fn upload_pattern_batches(
    runtime: &KernelRuntime,
    pattern_batches: &[PatternBatchPlan],
) -> Result<Vec<DevicePatternBatch>> {
    let stream = runtime
        .context
        .new_stream()
        .map_err(anyhow::Error::new)
        .context("failed to create CUDA stream for pattern uploads")?;
    let uploaded = pattern_batches
        .iter()
        .map(|batch| {
            let host = Arc::new(batch.clone());
            let pattern_blob_device = stream
                .clone_htod(host.pattern_blob.as_slice())
                .map_err(anyhow::Error::new)
                .context("failed to copy GPU native pattern blob to CUDA device")?;
            let pattern_offsets_device = stream
                .clone_htod(host.pattern_offsets.as_slice())
                .map_err(anyhow::Error::new)
                .context("failed to copy GPU native pattern offsets to CUDA device")?;
            let pattern_lengths_device = stream
                .clone_htod(host.pattern_lengths.as_slice())
                .map_err(anyhow::Error::new)
                .context("failed to copy GPU native pattern lengths to CUDA device")?;

            Ok(DevicePatternBatch {
                host,
                pattern_blob_device,
                pattern_offsets_device,
                pattern_lengths_device,
            })
        })
        .collect::<Result<Vec<_>>>()?;
    stream
        .synchronize()
        .map_err(anyhow::Error::new)
        .context("failed to synchronize CUDA pattern upload stream")?;
    Ok(uploaded)
}

pub(super) fn upload_pattern_batches_on_stream(
    stream: &Arc<CudaStream>,
    pattern_batches: &[PatternBatchPlan],
) -> Result<Vec<DevicePatternBatch>> {
    let uploaded = pattern_batches
        .iter()
        .map(|batch| {
            let host = Arc::new(batch.clone());
            let pattern_blob_device = stream
                .clone_htod(host.pattern_blob.as_slice())
                .map_err(anyhow::Error::new)
                .context("failed to copy GPU native pattern blob to CUDA device")?;
            let pattern_offsets_device = stream
                .clone_htod(host.pattern_offsets.as_slice())
                .map_err(anyhow::Error::new)
                .context("failed to copy GPU native pattern offsets to CUDA device")?;
            let pattern_lengths_device = stream
                .clone_htod(host.pattern_lengths.as_slice())
                .map_err(anyhow::Error::new)
                .context("failed to copy GPU native pattern lengths to CUDA device")?;

            Ok(DevicePatternBatch {
                host,
                pattern_blob_device,
                pattern_offsets_device,
                pattern_lengths_device,
            })
        })
        .collect::<Result<Vec<_>>>()?;
    stream
        .synchronize()
        .map_err(anyhow::Error::new)
        .context("failed to synchronize CUDA pattern upload stream")?;
    Ok(uploaded)
}

pub(super) fn plan_file_batches(
    files: &[PathBuf],
    max_batch_bytes: usize,
) -> Result<Vec<FileBatchPlan>> {
    let mut batches = Vec::new();
    let mut current_files = Vec::new();
    let mut current_bytes = 0usize;

    for path in files {
        let estimated_bytes = file_estimated_bytes(path)?;
        let additional_bytes = if current_files.is_empty() {
            estimated_bytes
        } else {
            estimated_bytes.saturating_add(1)
        };

        if !current_files.is_empty()
            && current_bytes.saturating_add(additional_bytes) > max_batch_bytes
        {
            batches.push(FileBatchPlan {
                files: current_files,
                estimated_bytes: current_bytes.max(1),
            });
            current_files = Vec::new();
            current_bytes = 0;
        }

        current_bytes = if current_files.is_empty() {
            estimated_bytes
        } else {
            current_bytes.saturating_add(additional_bytes)
        };
        current_files.push(path.clone());

        if current_bytes >= max_batch_bytes {
            batches.push(FileBatchPlan {
                files: current_files,
                estimated_bytes: current_bytes.max(1),
            });
            current_files = Vec::new();
            current_bytes = 0;
        }
    }

    if !current_files.is_empty() {
        batches.push(FileBatchPlan {
            files: current_files,
            estimated_bytes: current_bytes.max(1),
        });
    }

    Ok(batches)
}

pub(super) fn file_estimated_bytes(path: &Path) -> Result<usize> {
    usize::try_from(
        fs::metadata(path)
            .with_context(|| format!("failed to stat GPU native search file {}", path.display()))?
            .len(),
    )
    .with_context(|| format!("GPU native search file is too large: {}", path.display()))
}
