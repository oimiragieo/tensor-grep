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

pub(super) const POSITION_SEARCH_KERNEL_NAME: &str = "gpu_text_search_positions";
pub(super) const SHORT_LINE_SEARCH_KERNEL_NAME: &str = "gpu_text_search_short_lines";
pub(super) const WARP_LINE_SEARCH_KERNEL_NAME: &str = "gpu_text_search_warp_lines";
pub(super) const BLOCK_LINE_SEARCH_KERNEL_NAME: &str = "gpu_text_search_block_lines";
pub(super) const SEARCH_KERNEL_SOURCE: &str = r#"
__device__ __forceinline__ bool gpu_text_search_matches_at(
    const unsigned char* text,
    unsigned int start,
    const unsigned char* pattern,
    unsigned int pattern_len
) {
    for (unsigned int pattern_index = 0; pattern_index < pattern_len; ++pattern_index) {
        if (text[start + pattern_index] != pattern[pattern_index]) {
            return false;
        }
    }
    return true;
}

extern "C" __global__ void gpu_text_search_positions(
    const unsigned char* text,
    int text_len,
    const unsigned char* pattern_bytes,
    int pattern_blob_len,
    const unsigned int* pattern_offsets,
    const unsigned int* pattern_lengths,
    int pattern_count,
    unsigned int* match_positions,
    unsigned int* match_pattern_ids,
    unsigned int max_matches,
    unsigned int* match_count
) {
    if (pattern_count <= 0 || pattern_blob_len <= 0 || text_len <= 0) {
        return;
    }

    extern __shared__ unsigned char shared_bytes[];
    unsigned int* shared_offsets = reinterpret_cast<unsigned int*>(shared_bytes);
    unsigned int* shared_lengths = shared_offsets + pattern_count;
    unsigned char* shared_patterns = reinterpret_cast<unsigned char*>(shared_lengths + pattern_count);

    for (unsigned int index = threadIdx.x; index < (unsigned int)pattern_count; index += blockDim.x) {
        shared_offsets[index] = pattern_offsets[index];
        shared_lengths[index] = pattern_lengths[index];
    }
    for (unsigned int index = threadIdx.x; index < (unsigned int)pattern_blob_len; index += blockDim.x) {
        shared_patterns[index] = pattern_bytes[index];
    }
    __syncthreads();

    unsigned int idx = blockIdx.x * blockDim.x + threadIdx.x;
    if (idx >= (unsigned int)text_len) {
        return;
    }

    for (int local_pattern_id = 0; local_pattern_id < pattern_count; ++local_pattern_id) {
        unsigned int pattern_len = shared_lengths[local_pattern_id];
        if (pattern_len == 0 || idx + pattern_len > (unsigned int)text_len) {
            continue;
        }

        unsigned int pattern_offset = shared_offsets[local_pattern_id];
        if (gpu_text_search_matches_at(text, idx, shared_patterns + pattern_offset, pattern_len)) {
            unsigned int slot = atomicAdd(match_count, 1u);
            if (slot < max_matches) {
                match_positions[slot] = idx;
                match_pattern_ids[slot] = (unsigned int)local_pattern_id;
            }
        }
    }
}

extern "C" __global__ void gpu_text_search_short_lines(
    const unsigned char* text,
    const unsigned char* pattern_bytes,
    int pattern_blob_len,
    const unsigned int* pattern_offsets,
    const unsigned int* pattern_lengths,
    int pattern_count,
    const unsigned int* line_starts,
    const unsigned int* line_lengths,
    int line_count,
    unsigned int* match_positions,
    unsigned int* match_pattern_ids,
    unsigned int max_matches,
    unsigned int* match_count
) {
    if (line_count <= 0 || pattern_count <= 0 || pattern_blob_len <= 0) {
        return;
    }

    extern __shared__ unsigned char shared_bytes[];
    unsigned int* shared_offsets = reinterpret_cast<unsigned int*>(shared_bytes);
    unsigned int* shared_lengths = shared_offsets + pattern_count;
    unsigned char* shared_patterns = reinterpret_cast<unsigned char*>(shared_lengths + pattern_count);

    for (unsigned int index = threadIdx.x; index < (unsigned int)pattern_count; index += blockDim.x) {
        shared_offsets[index] = pattern_offsets[index];
        shared_lengths[index] = pattern_lengths[index];
    }
    for (unsigned int index = threadIdx.x; index < (unsigned int)pattern_blob_len; index += blockDim.x) {
        shared_patterns[index] = pattern_bytes[index];
    }
    __syncthreads();

    unsigned int line_index = blockIdx.x;
    if (line_index >= (unsigned int)line_count) {
        return;
    }

    unsigned int line_start = line_starts[line_index];
    unsigned int line_len = line_lengths[line_index];
    __shared__ int shared_match_pos;

    for (int local_pattern_id = 0; local_pattern_id < pattern_count; ++local_pattern_id) {
        if (threadIdx.x == 0) {
            shared_match_pos = 2147483647;
        }
        __syncthreads();

        unsigned int pattern_len = shared_lengths[local_pattern_id];
        if (pattern_len == 0 || pattern_len > line_len) {
            __syncthreads();
            continue;
        }

        unsigned int search_limit = line_len - pattern_len + 1;
        if (threadIdx.x < search_limit) {
            unsigned int pattern_offset = shared_offsets[local_pattern_id];
            if (gpu_text_search_matches_at(
                    text,
                    line_start + threadIdx.x,
                    shared_patterns + pattern_offset,
                    pattern_len)) {
                atomicMin(&shared_match_pos, (int)threadIdx.x);
            }
        }
        __syncthreads();

        if (threadIdx.x == 0 && shared_match_pos != 2147483647) {
            unsigned int slot = atomicAdd(match_count, 1u);
            if (slot < max_matches) {
                match_positions[slot] = line_start + (unsigned int)shared_match_pos;
                match_pattern_ids[slot] = (unsigned int)local_pattern_id;
            }
        }
        __syncthreads();
    }
}

extern "C" __global__ void gpu_text_search_warp_lines(
    const unsigned char* text,
    const unsigned char* pattern_bytes,
    int pattern_blob_len,
    const unsigned int* pattern_offsets,
    const unsigned int* pattern_lengths,
    int pattern_count,
    const unsigned int* line_starts,
    const unsigned int* line_lengths,
    int line_count,
    unsigned int* match_positions,
    unsigned int* match_pattern_ids,
    unsigned int max_matches,
    unsigned int* match_count
) {
    if (line_count <= 0 || pattern_count <= 0 || pattern_blob_len <= 0) {
        return;
    }

    extern __shared__ unsigned char shared_bytes[];
    unsigned int* shared_offsets = reinterpret_cast<unsigned int*>(shared_bytes);
    unsigned int* shared_lengths = shared_offsets + pattern_count;
    unsigned char* shared_patterns = reinterpret_cast<unsigned char*>(shared_lengths + pattern_count);

    for (unsigned int index = threadIdx.x; index < (unsigned int)pattern_count; index += blockDim.x) {
        shared_offsets[index] = pattern_offsets[index];
        shared_lengths[index] = pattern_lengths[index];
    }
    for (unsigned int index = threadIdx.x; index < (unsigned int)pattern_blob_len; index += blockDim.x) {
        shared_patterns[index] = pattern_bytes[index];
    }
    __syncthreads();

    unsigned int warp_index = blockIdx.x * (blockDim.x / 32) + (threadIdx.x / 32);
    if (warp_index >= (unsigned int)line_count) {
        return;
    }

    unsigned int lane = threadIdx.x & 31;
    unsigned int line_start = line_starts[warp_index];
    unsigned int line_len = line_lengths[warp_index];

    for (int local_pattern_id = 0; local_pattern_id < pattern_count; ++local_pattern_id) {
        unsigned int pattern_len = shared_lengths[local_pattern_id];
        if (pattern_len == 0 || pattern_len > line_len) {
            continue;
        }

        unsigned int pattern_offset = shared_offsets[local_pattern_id];
        unsigned int search_limit = line_len - pattern_len + 1;
        int local_match = -1;
        for (unsigned int position = lane; position < search_limit; position += 32) {
            if (gpu_text_search_matches_at(
                    text,
                    line_start + position,
                    shared_patterns + pattern_offset,
                    pattern_len)) {
                local_match = (int)position;
                break;
            }
        }

        unsigned int found_mask = __ballot_sync(0xffffffffu, local_match >= 0);
        if (found_mask == 0) {
            continue;
        }

        int first_lane = __ffs((int)found_mask) - 1;
        int first_match = __shfl_sync(0xffffffffu, local_match, first_lane);
        if (lane == 0) {
            unsigned int slot = atomicAdd(match_count, 1u);
            if (slot < max_matches) {
                match_positions[slot] = line_start + (unsigned int)first_match;
                match_pattern_ids[slot] = (unsigned int)local_pattern_id;
            }
        }
    }
}

extern "C" __global__ void gpu_text_search_block_lines(
    const unsigned char* text,
    const unsigned char* pattern_bytes,
    int pattern_blob_len,
    const unsigned int* pattern_offsets,
    const unsigned int* pattern_lengths,
    int pattern_count,
    const unsigned int* line_starts,
    const unsigned int* line_lengths,
    int line_count,
    unsigned int* match_positions,
    unsigned int* match_pattern_ids,
    unsigned int max_matches,
    unsigned int* match_count
) {
    if (line_count <= 0 || pattern_count <= 0 || pattern_blob_len <= 0) {
        return;
    }

    extern __shared__ unsigned char shared_bytes[];
    unsigned int* shared_offsets = reinterpret_cast<unsigned int*>(shared_bytes);
    unsigned int* shared_lengths = shared_offsets + pattern_count;
    unsigned char* shared_patterns = reinterpret_cast<unsigned char*>(shared_lengths + pattern_count);

    for (unsigned int index = threadIdx.x; index < (unsigned int)pattern_count; index += blockDim.x) {
        shared_offsets[index] = pattern_offsets[index];
        shared_lengths[index] = pattern_lengths[index];
    }
    for (unsigned int index = threadIdx.x; index < (unsigned int)pattern_blob_len; index += blockDim.x) {
        shared_patterns[index] = pattern_bytes[index];
    }
    __syncthreads();

    unsigned int line_index = blockIdx.x;
    if (line_index >= (unsigned int)line_count) {
        return;
    }

    unsigned int line_start = line_starts[line_index];
    unsigned int line_len = line_lengths[line_index];
    __shared__ int shared_match_pos;

    for (int local_pattern_id = 0; local_pattern_id < pattern_count; ++local_pattern_id) {
        if (threadIdx.x == 0) {
            shared_match_pos = 2147483647;
        }
        __syncthreads();

        unsigned int pattern_len = shared_lengths[local_pattern_id];
        if (pattern_len == 0 || pattern_len > line_len) {
            __syncthreads();
            continue;
        }

        unsigned int pattern_offset = shared_offsets[local_pattern_id];
        unsigned int search_limit = line_len - pattern_len + 1;
        for (unsigned int position = threadIdx.x; position < search_limit; position += blockDim.x) {
            if (gpu_text_search_matches_at(
                    text,
                    line_start + position,
                    shared_patterns + pattern_offset,
                    pattern_len)) {
                atomicMin(&shared_match_pos, (int)position);
            }
        }
        __syncthreads();

        if (threadIdx.x == 0 && shared_match_pos != 2147483647) {
            unsigned int slot = atomicAdd(match_count, 1u);
            if (slot < max_matches) {
                match_positions[slot] = line_start + (unsigned int)shared_match_pos;
                match_pattern_ids[slot] = (unsigned int)local_pattern_id;
            }
        }
        __syncthreads();
    }
}
"#;
pub(super) const KERNEL_THREADS_PER_BLOCK: u32 = 256;
pub(super) const SHORT_LINE_THREADS_PER_BLOCK: u32 = 128;
pub(super) const WARP_LINE_THREADS_PER_BLOCK: u32 = 128;
pub(super) const LONG_LINE_THREADS_PER_BLOCK: u32 = 256;
pub(super) const SHORT_LINE_BYTES_THRESHOLD: u32 = 128;
pub(super) const LONG_LINE_BYTES_THRESHOLD: u32 = 4 * 1024;
pub(super) const WARP_SIZE: u32 = 32;
pub(super) const WARPS_PER_BLOCK: u32 = WARP_LINE_THREADS_PER_BLOCK / WARP_SIZE;
pub(super) const PIPELINE_SLOT_COUNT: usize = 2;
pub(super) const DEFAULT_GPU_BATCH_BYTES: usize = 128 * 1024 * 1024;
pub(super) const SHARED_PATTERN_MEMORY_LIMIT_BYTES: usize = 48 * 1024;
pub(super) const MAX_SLOT_DEVICE_MEMORY_BYTES: usize = 1024 * 1024 * 1024;
pub(super) const PTX_CACHE_VERSION: &str = "v1";
static CUDA_LIBRARY_PATH_INIT: Once = Once::new();

pub(super) fn launch_slot_search(
    slot: &mut SearchPipelineSlot,
    runtime: &KernelRuntime,
    pattern_batch_index: usize,
    options: SearchExecutionOptions,
) -> Result<()> {
    let Some(batch) = slot.current_batch.as_ref() else {
        return Ok(());
    };
    let bytes_used = batch.bytes_used;
    let short_line_count = batch.classified_lines.short_line_count();
    let medium_line_count = batch.classified_lines.medium_line_count();
    let long_line_count = batch.classified_lines.long_line_count();
    let classified_lines = batch.classified_lines.clone();

    if bytes_used == 0 {
        slot.adaptive_dispatch = Some(SlotAdaptiveDispatch {
            stats: AdaptiveDispatchStats::default(),
        });
        slot.current_launch_mode = Some(SearchLaunchMode::Standard);
        return Ok(());
    }

    let adaptive_dispatch = prepare_adaptive_dispatch(slot, &classified_lines)?;
    let total_lines = adaptive_dispatch.total_lines();
    let pattern_count = slot
        .pattern_batches
        .get(pattern_batch_index)
        .context("missing pattern batch buffers for GPU search launch")?
        .host
        .global_pattern_ids
        .len();
    let max_matches_usize = resolve_adaptive_match_capacity(total_lines, pattern_count)?;
    ensure_slot_match_capacity(slot, max_matches_usize)?;
    let max_matches =
        u32::try_from(max_matches_usize).context("GPU search batch exceeds u32 match capacity")?;
    slot.adaptive_dispatch = Some(adaptive_dispatch);
    if total_lines == 0 {
        slot.current_launch_mode = Some(SearchLaunchMode::Standard);
        return Ok(());
    }

    let signature = GraphCaptureSignature {
        bytes_used,
        short_line_count,
        medium_line_count,
        long_line_count,
        pattern_batch_index,
    };

    if !options.use_cuda_graphs {
        return launch_slot_search_standard(slot, runtime, pattern_batch_index, max_matches);
    }

    if slot
        .cuda_graph
        .as_ref()
        .map(|graph| graph.signature != signature)
        .unwrap_or(false)
    {
        slot.cuda_graph = None;
    }

    if slot.cuda_graph.is_none() {
        capture_slot_search_graph(slot, runtime, pattern_batch_index, max_matches, signature)?;
        return launch_slot_search_graph(slot, SearchLaunchMode::GraphCapture);
    }

    launch_slot_search_graph(slot, SearchLaunchMode::GraphReplay)
}

#[allow(clippy::too_many_arguments)]
pub(super) fn launch_position_search_kernel(
    stream: &Arc<CudaStream>,
    function: &CudaFunction,
    data_device: &CudaSlice<u8>,
    data_len: i32,
    pattern_batch: &DevicePatternBatch,
    match_positions_device: &mut CudaSlice<u32>,
    match_pattern_ids_device: &mut CudaSlice<u32>,
    max_matches: u32,
    match_count_device: &mut CudaSlice<u32>,
) -> Result<()> {
    let launch_config = LaunchConfig {
        grid_dim: (
            (u32::try_from(data_len).unwrap_or(0)).div_ceil(KERNEL_THREADS_PER_BLOCK),
            1,
            1,
        ),
        block_dim: (KERNEL_THREADS_PER_BLOCK, 1, 1),
        shared_mem_bytes: u32::try_from(pattern_batch.host.shared_mem_bytes)
            .context("GPU native search shared memory requirement exceeds u32")?,
    };
    let pattern_blob_len = i32::try_from(pattern_batch.host.pattern_blob.len())
        .context("GPU native search pattern blob exceeds i32 length")?;
    let pattern_count = i32::try_from(pattern_batch.host.global_pattern_ids.len())
        .context("GPU native search pattern count exceeds i32 length")?;

    catch_cuda("launch GPU substring search kernel", || {
        let mut launch = stream.launch_builder(function);
        launch.arg(data_device);
        launch.arg(&data_len);
        launch.arg(&pattern_batch.pattern_blob_device);
        launch.arg(&pattern_blob_len);
        launch.arg(&pattern_batch.pattern_offsets_device);
        launch.arg(&pattern_batch.pattern_lengths_device);
        launch.arg(&pattern_count);
        launch.arg(match_positions_device);
        launch.arg(match_pattern_ids_device);
        launch.arg(&max_matches);
        launch.arg(match_count_device);
        unsafe { launch.launch(launch_config) }
            .map(|_| ())
            .map_err(anyhow::Error::new)
    })
}

#[allow(clippy::too_many_arguments)]
pub(super) fn launch_line_search_kernel(
    stream: &Arc<CudaStream>,
    function: &CudaFunction,
    threads_per_block: u32,
    line_starts_device: &CudaSlice<u32>,
    line_lengths_device: &CudaSlice<u32>,
    line_count: usize,
    data_device: &CudaSlice<u8>,
    pattern_batch: &DevicePatternBatch,
    match_positions_device: &mut CudaSlice<u32>,
    match_pattern_ids_device: &mut CudaSlice<u32>,
    max_matches: u32,
    match_count_device: &mut CudaSlice<u32>,
) -> Result<()> {
    let line_count = i32::try_from(line_count)
        .context("GPU native line search line count exceeds i32 length")?;
    let launch_config = LaunchConfig {
        grid_dim: (u32::try_from(line_count).unwrap_or(0), 1, 1),
        block_dim: (threads_per_block, 1, 1),
        shared_mem_bytes: u32::try_from(pattern_batch.host.shared_mem_bytes)
            .context("GPU native search shared memory requirement exceeds u32")?,
    };
    let pattern_blob_len = i32::try_from(pattern_batch.host.pattern_blob.len())
        .context("GPU native search pattern blob exceeds i32 length")?;
    let pattern_count = i32::try_from(pattern_batch.host.global_pattern_ids.len())
        .context("GPU native search pattern count exceeds i32 length")?;

    catch_cuda("launch adaptive GPU line search kernel", || {
        let mut launch = stream.launch_builder(function);
        launch.arg(data_device);
        launch.arg(&pattern_batch.pattern_blob_device);
        launch.arg(&pattern_blob_len);
        launch.arg(&pattern_batch.pattern_offsets_device);
        launch.arg(&pattern_batch.pattern_lengths_device);
        launch.arg(&pattern_count);
        launch.arg(line_starts_device);
        launch.arg(line_lengths_device);
        launch.arg(&line_count);
        launch.arg(match_positions_device);
        launch.arg(match_pattern_ids_device);
        launch.arg(&max_matches);
        launch.arg(match_count_device);
        unsafe { launch.launch(launch_config) }
            .map(|_| ())
            .map_err(anyhow::Error::new)
    })
}

#[allow(clippy::too_many_arguments)]
pub(super) fn launch_warp_line_search_kernel(
    stream: &Arc<CudaStream>,
    function: &CudaFunction,
    line_starts_device: &CudaSlice<u32>,
    line_lengths_device: &CudaSlice<u32>,
    line_count: usize,
    data_device: &CudaSlice<u8>,
    pattern_batch: &DevicePatternBatch,
    match_positions_device: &mut CudaSlice<u32>,
    match_pattern_ids_device: &mut CudaSlice<u32>,
    max_matches: u32,
    match_count_device: &mut CudaSlice<u32>,
) -> Result<()> {
    let line_count = i32::try_from(line_count)
        .context("GPU native warp line search count exceeds i32 length")?;
    let launch_config = LaunchConfig {
        grid_dim: (
            u32::try_from(line_count)
                .unwrap_or(0)
                .div_ceil(WARPS_PER_BLOCK),
            1,
            1,
        ),
        block_dim: (WARP_LINE_THREADS_PER_BLOCK, 1, 1),
        shared_mem_bytes: u32::try_from(pattern_batch.host.shared_mem_bytes)
            .context("GPU native search shared memory requirement exceeds u32")?,
    };
    let pattern_blob_len = i32::try_from(pattern_batch.host.pattern_blob.len())
        .context("GPU native search pattern blob exceeds i32 length")?;
    let pattern_count = i32::try_from(pattern_batch.host.global_pattern_ids.len())
        .context("GPU native search pattern count exceeds i32 length")?;

    catch_cuda("launch warp-cooperative GPU line search kernel", || {
        let mut launch = stream.launch_builder(function);
        launch.arg(data_device);
        launch.arg(&pattern_batch.pattern_blob_device);
        launch.arg(&pattern_blob_len);
        launch.arg(&pattern_batch.pattern_offsets_device);
        launch.arg(&pattern_batch.pattern_lengths_device);
        launch.arg(&pattern_count);
        launch.arg(line_starts_device);
        launch.arg(line_lengths_device);
        launch.arg(&line_count);
        launch.arg(match_positions_device);
        launch.arg(match_pattern_ids_device);
        launch.arg(&max_matches);
        launch.arg(match_count_device);
        unsafe { launch.launch(launch_config) }
            .map(|_| ())
            .map_err(anyhow::Error::new)
    })
}

pub(super) fn prepare_adaptive_dispatch(
    slot: &mut SearchPipelineSlot,
    classified_lines: &ClassifiedLineBatch,
) -> Result<SlotAdaptiveDispatch> {
    prepare_line_class_buffers(
        &slot.stream,
        &mut slot.short_line_buffers,
        &classified_lines.short_lines,
    )?;
    prepare_line_class_buffers(
        &slot.stream,
        &mut slot.medium_line_buffers,
        &classified_lines.medium_lines,
    )?;
    prepare_line_class_buffers(
        &slot.stream,
        &mut slot.long_line_buffers,
        &classified_lines.long_lines,
    )?;

    Ok(SlotAdaptiveDispatch {
        stats: AdaptiveDispatchStats {
            short_line_count: classified_lines.short_line_count(),
            medium_line_count: classified_lines.medium_line_count(),
            long_line_count: classified_lines.long_line_count(),
            warp_dispatch_count: usize::from(!classified_lines.medium_lines.is_empty()),
            block_dispatch_count: usize::from(!classified_lines.long_lines.is_empty()),
        },
    })
}

pub(super) fn prepare_line_class_buffers(
    stream: &Arc<CudaStream>,
    storage: &mut Option<ReusableLineClassBuffers>,
    lines: &[LineDescriptor],
) -> Result<()> {
    if lines.is_empty() {
        if let Some(storage) = storage.as_mut() {
            storage.len = 0;
        }
        return Ok(());
    }

    let needs_allocation = storage
        .as_ref()
        .map(|storage| storage.line_starts_host.len() < lines.len())
        .unwrap_or(true);
    if needs_allocation {
        let line_starts_device = unsafe { stream.alloc::<u32>(lines.len()) }
            .map_err(anyhow::Error::new)
            .context("failed to allocate adaptive line start buffer on CUDA device")?;
        let line_lengths_device = unsafe { stream.alloc::<u32>(lines.len()) }
            .map_err(anyhow::Error::new)
            .context("failed to allocate adaptive line length buffer on CUDA device")?;
        *storage = Some(ReusableLineClassBuffers {
            line_starts_host: vec![0; lines.len()],
            line_lengths_host: vec![0; lines.len()],
            line_starts_device,
            line_lengths_device,
            len: 0,
        });
    }

    let storage = storage
        .as_mut()
        .context("missing reusable adaptive line class buffers")?;
    for (index, line) in lines.iter().enumerate() {
        storage.line_starts_host[index] = line.start;
        storage.line_lengths_host[index] = line.len;
    }
    storage.len = lines.len();

    let mut starts_view = storage.line_starts_device.slice_mut(0..storage.len);
    let mut lengths_view = storage.line_lengths_device.slice_mut(0..storage.len);
    stream
        .memcpy_htod(&storage.line_starts_host[..storage.len], &mut starts_view)
        .map_err(anyhow::Error::new)
        .context("failed to copy adaptive line starts to CUDA device")?;
    stream
        .memcpy_htod(&storage.line_lengths_host[..storage.len], &mut lengths_view)
        .map_err(anyhow::Error::new)
        .context("failed to copy adaptive line lengths to CUDA device")?;

    Ok(())
}

pub(super) fn launch_slot_search_standard(
    slot: &mut SearchPipelineSlot,
    runtime: &KernelRuntime,
    pattern_batch_index: usize,
    max_matches: u32,
) -> Result<()> {
    let bytes_used = slot
        .current_batch
        .as_ref()
        .map(|batch| batch.bytes_used)
        .unwrap_or(0);
    slot.stream
        .memset_zeros(&mut slot.match_count_buffer)
        .map_err(anyhow::Error::new)
        .context("failed to reset GPU search pipeline match counter")?;
    slot.transfer_start = Some(record_timed_event(&slot.stream)?);
    copy_pinned_host_to_device(
        &slot.stream,
        &slot.host_buffer,
        bytes_used,
        &mut slot.data_buffer,
    )
    .context("failed to copy pinned search batch to CUDA device")?;
    slot.transfer_end = Some(record_timed_event(&slot.stream)?);
    launch_slot_search_kernels(slot, runtime, pattern_batch_index, max_matches)?;
    slot.kernel_end = Some(record_timed_event(&slot.stream)?);
    slot.graph_launch_started_at = None;
    slot.current_launch_mode = Some(SearchLaunchMode::Standard);
    Ok(())
}

pub(super) fn capture_slot_search_graph(
    slot: &mut SearchPipelineSlot,
    runtime: &KernelRuntime,
    pattern_batch_index: usize,
    max_matches: u32,
    signature: GraphCaptureSignature,
) -> Result<()> {
    let bytes_used = slot
        .current_batch
        .as_ref()
        .map(|batch| batch.bytes_used)
        .unwrap_or(0);
    let capture_input_ptr = slot.host_buffer.as_slice()?.as_ptr();
    let mut match_count_host = unsafe { runtime.context.alloc_pinned::<u32>(1) }
        .map_err(anyhow::Error::new)
        .context("failed to allocate graphed GPU match count host buffer")?;
    let match_count_host_ptr = match_count_host
        .as_mut_ptr()
        .map_err(anyhow::Error::new)
        .context("failed to access graphed GPU match count host buffer")?;

    slot.stream
        .synchronize()
        .map_err(anyhow::Error::new)
        .context("failed to synchronize GPU search pipeline stream before CUDA graph capture")?;
    let _event_tracking_guard = EventTrackingModeGuard::disable(&runtime.context);
    slot.stream
        .begin_capture(sys::CUstreamCaptureMode::CU_STREAM_CAPTURE_MODE_RELAXED)
        .map_err(anyhow::Error::new)
        .context("failed to begin CUDA graph capture for GPU search pipeline")?;

    let capture = (|| {
        let capture_input = unsafe { std::slice::from_raw_parts(capture_input_ptr, bytes_used) };
        copy_host_slice_to_device_during_capture(
            &slot.stream,
            capture_input,
            &mut slot.data_buffer,
        )
        .context("failed to copy graphed pinned search batch to CUDA device")?;
        launch_slot_search_kernels(slot, runtime, pattern_batch_index, max_matches)?;
        let match_count_host_slice =
            unsafe { std::slice::from_raw_parts_mut(match_count_host_ptr, 1) };
        copy_device_match_count_to_host_slice_during_capture(
            &slot.stream,
            &slot.match_count_buffer,
            match_count_host_slice,
        )
        .context("failed to enqueue graphed GPU match count copy to host")?;
        let graph = CapturedCudaGraph::from_captured_stream(&slot.stream)?
            .context("CUDA graph capture for GPU search pipeline produced no graph")?;

        Ok::<GraphCaptureState, anyhow::Error>(GraphCaptureState {
            signature,
            graph,
            match_count_host,
        })
    })();

    match capture {
        Ok(graph_state) => {
            slot.cuda_graph = Some(graph_state);
            slot.graph_launch_started_at = None;
            slot.transfer_start = None;
            slot.transfer_end = None;
            slot.kernel_end = None;
            Ok(())
        }
        Err(err) => {
            if let Ok(graph) = unsafe { cuda_result::stream::end_capture(slot.stream.cu_stream()) }
            {
                if !graph.is_null() {
                    let _ = unsafe { cuda_result::graph::destroy(graph) };
                }
            }
            Err(err)
        }
    }
}

pub(super) fn launch_slot_search_graph(
    slot: &mut SearchPipelineSlot,
    launch_mode: SearchLaunchMode,
) -> Result<()> {
    let graph_state = slot
        .cuda_graph
        .as_ref()
        .context("missing CUDA graph state for GPU search pipeline")?;
    slot.stream
        .memset_zeros(&mut slot.match_count_buffer)
        .map_err(anyhow::Error::new)
        .context("failed to reset graphed GPU search pipeline match counter before launch")?;
    graph_state.graph.launch()?;
    slot.graph_launch_started_at = Some(Instant::now());
    slot.current_launch_mode = Some(launch_mode);
    slot.transfer_start = None;
    slot.transfer_end = None;
    slot.kernel_end = None;
    Ok(())
}

pub(super) fn ensure_slot_match_capacity(
    slot: &mut SearchPipelineSlot,
    required: usize,
) -> Result<()> {
    let required = required.max(1);
    if slot.match_buffer_capacity >= required {
        return Ok(());
    }

    slot.match_positions_buffer = unsafe { slot.stream.alloc::<u32>(required) }
        .map_err(anyhow::Error::new)
        .context("failed to grow device match position buffer for GPU search pipeline")?;
    slot.match_pattern_ids_buffer = unsafe { slot.stream.alloc::<u32>(required) }
        .map_err(anyhow::Error::new)
        .context("failed to grow device match pattern id buffer for GPU search pipeline")?;
    slot.match_buffer_capacity = required;
    Ok(())
}

pub(super) fn launch_slot_search_kernels(
    slot: &mut SearchPipelineSlot,
    runtime: &KernelRuntime,
    pattern_batch_index: usize,
    max_matches: u32,
) -> Result<()> {
    let pattern_batch = slot
        .pattern_batches
        .get(pattern_batch_index)
        .context("missing pattern batch buffers for GPU search kernels")?;
    if let Some(short_lines) = slot
        .short_line_buffers
        .as_ref()
        .filter(|lines| lines.len > 0)
    {
        launch_line_search_kernel(
            &slot.stream,
            &runtime.short_line_function,
            SHORT_LINE_THREADS_PER_BLOCK,
            &short_lines.line_starts_device,
            &short_lines.line_lengths_device,
            short_lines.len,
            &slot.data_buffer,
            pattern_batch,
            &mut slot.match_positions_buffer,
            &mut slot.match_pattern_ids_buffer,
            max_matches,
            &mut slot.match_count_buffer,
        )?;
    }
    if let Some(medium_lines) = slot
        .medium_line_buffers
        .as_ref()
        .filter(|lines| lines.len > 0)
    {
        launch_warp_line_search_kernel(
            &slot.stream,
            &runtime.warp_line_function,
            &medium_lines.line_starts_device,
            &medium_lines.line_lengths_device,
            medium_lines.len,
            &slot.data_buffer,
            pattern_batch,
            &mut slot.match_positions_buffer,
            &mut slot.match_pattern_ids_buffer,
            max_matches,
            &mut slot.match_count_buffer,
        )?;
    }
    if let Some(long_lines) = slot
        .long_line_buffers
        .as_ref()
        .filter(|lines| lines.len > 0)
    {
        launch_line_search_kernel(
            &slot.stream,
            &runtime.block_line_function,
            LONG_LINE_THREADS_PER_BLOCK,
            &long_lines.line_starts_device,
            &long_lines.line_lengths_device,
            long_lines.len,
            &slot.data_buffer,
            pattern_batch,
            &mut slot.match_positions_buffer,
            &mut slot.match_pattern_ids_buffer,
            max_matches,
            &mut slot.match_count_buffer,
        )?;
    }

    Ok(())
}

pub(super) fn classify_file_lines(
    batch_start: usize,
    bytes: &[u8],
    classified_lines: &mut ClassifiedLineBatch,
) -> Result<Vec<LineDescriptor>> {
    let mut line_descriptors = Vec::new();
    let mut line_start = 0usize;
    let mut line_number = 1usize;
    for newline_index in memchr_iter(b'\n', bytes) {
        push_classified_line(
            batch_start.saturating_add(line_start),
            newline_index.saturating_sub(line_start),
            line_number,
            classified_lines,
            &mut line_descriptors,
        )?;
        line_start = newline_index.saturating_add(1);
        line_number = line_number.saturating_add(1);
    }
    if line_start < bytes.len() {
        push_classified_line(
            batch_start.saturating_add(line_start),
            bytes.len().saturating_sub(line_start),
            line_number,
            classified_lines,
            &mut line_descriptors,
        )?;
    }
    Ok(line_descriptors)
}

pub(super) fn push_classified_line(
    absolute_start: usize,
    line_len: usize,
    line_number: usize,
    classified_lines: &mut ClassifiedLineBatch,
    line_descriptors: &mut Vec<LineDescriptor>,
) -> Result<()> {
    if line_len == 0 {
        return Ok(());
    }

    let descriptor = LineDescriptor {
        start: u32::try_from(absolute_start).context("GPU native line start exceeds u32 range")?,
        len: u32::try_from(line_len).context("GPU native line length exceeds u32 range")?,
        line_number,
    };

    if descriptor.len < SHORT_LINE_BYTES_THRESHOLD {
        classified_lines.short_lines.push(descriptor);
    } else if descriptor.len <= LONG_LINE_BYTES_THRESHOLD {
        classified_lines.medium_lines.push(descriptor);
    } else {
        classified_lines.long_lines.push(descriptor);
    }
    line_descriptors.push(descriptor);

    Ok(())
}

pub(super) fn compile_kernel_module(
    context: &Arc<CudaContext>,
    device_id: i32,
) -> Result<Arc<CudaModule>> {
    let compute_capability = context
        .compute_capability()
        .map_err(anyhow::Error::new)
        .with_context(|| {
            format!("failed to detect compute capability for CUDA device {device_id}")
        })?;
    let architecture_option = nvrtc_architecture_option_for_compute_capability(compute_capability)?;
    let compile_options = kernel_compile_options_for_compute_capability(compute_capability)?;
    let cached_ptx = load_or_compile_search_kernel_ptx(
        device_id,
        compute_capability,
        &architecture_option,
        compile_options,
    )?;

    match context.load_module(cached_ptx.ptx) {
        Ok(module) => Ok(module),
        Err(_err) if cached_ptx.from_cache => {
            let _ = fs::remove_file(&cached_ptx.cache_path);
            let fresh_ptx = compile_search_kernel_ptx(
                device_id,
                compute_capability,
                &architecture_option,
                kernel_compile_options_for_compute_capability(compute_capability)?,
                Some(&cached_ptx.cache_path),
            )?;
            context
                .load_module(fresh_ptx)
                .map_err(anyhow::Error::new)
                .with_context(|| {
                    format!("failed to load freshly compiled CUDA module for device {device_id}")
                })
        }
        Err(err) => Err(anyhow::Error::new(err))
            .with_context(|| format!("failed to load compiled CUDA module for device {device_id}")),
    }
}

pub(super) fn load_or_compile_search_kernel_ptx(
    device_id: i32,
    compute_capability: (i32, i32),
    architecture_option: &str,
    compile_options: CompileOptions,
) -> Result<CachedKernelPtx> {
    let cache_path = cached_search_kernel_ptx_path(compute_capability, &compile_options.options)?;
    if cache_path.is_file()
        && cache_path
            .metadata()
            .map(|meta| meta.len() > 0)
            .unwrap_or(false)
    {
        return Ok(CachedKernelPtx {
            ptx: Ptx::from_file(cache_path.clone()),
            cache_path,
            from_cache: true,
        });
    }

    let ptx = compile_search_kernel_ptx(
        device_id,
        compute_capability,
        architecture_option,
        compile_options,
        Some(&cache_path),
    )?;
    Ok(CachedKernelPtx {
        ptx,
        cache_path,
        from_cache: false,
    })
}

pub(super) fn compile_search_kernel_ptx(
    device_id: i32,
    compute_capability: (i32, i32),
    architecture_option: &str,
    compile_options: CompileOptions,
    cache_path: Option<&Path>,
) -> Result<Ptx> {
    let ptx = catch_cuda("compile CUDA substring kernel via NVRTC", || {
        compile_ptx_with_opts(SEARCH_KERNEL_SOURCE, compile_options).map_err(|err| {
            anyhow!(
                "CUDA kernel compilation failed: NVRTC could not compile the native search kernel \
                 for CUDA device {device_id} with {architecture_option} (compute capability {}.{}); \
                 not falling back to a lower GPU architecture because that can produce an \
                 incompatible kernel image. Install CUDA/NVRTC 12.8+ for Blackwell/RTX 50-series \
                 or a toolkit that supports this compute capability. NVRTC error: {err}",
                compute_capability.0,
                compute_capability.1
            )
        })
    })?;
    if let (Some(path), Some(bytes)) = (cache_path, ptx.as_bytes()) {
        write_cached_search_kernel_ptx(path, bytes);
    }
    Ok(ptx)
}

pub(super) fn write_cached_search_kernel_ptx(path: &Path, bytes: &[u8]) {
    let Some(parent) = path.parent() else {
        return;
    };
    if fs::create_dir_all(parent).is_err() {
        return;
    }
    let temp_path = path.with_extension("ptx.tmp");
    if fs::write(&temp_path, bytes).is_err() {
        return;
    }
    let _ = fs::remove_file(path);
    if fs::rename(&temp_path, path).is_err() {
        let _ = fs::remove_file(&temp_path);
    }
}

pub(super) fn cached_search_kernel_ptx_path(
    compute_capability: (i32, i32),
    compile_options: &[String],
) -> Result<PathBuf> {
    let architecture_option = nvrtc_architecture_option_for_compute_capability(compute_capability)?;
    let mut hasher = Sha256::new();
    hasher.update(PTX_CACHE_VERSION.as_bytes());
    hasher.update([0]);
    hasher.update(SEARCH_KERNEL_SOURCE.as_bytes());
    for option in compile_options {
        hasher.update([0]);
        hasher.update(option.as_bytes());
    }
    let digest = hasher.finalize();
    let digest_hex = digest
        .iter()
        .take(12)
        .map(|byte| format!("{byte:02x}"))
        .collect::<String>();

    Ok(cuda_ptx_cache_dir().join(format!(
        "gpu_native_search_{}_{}.ptx",
        architecture_option.trim_start_matches("--gpu-architecture="),
        digest_hex
    )))
}

pub(super) fn cuda_ptx_cache_dir() -> PathBuf {
    if let Some(path) = env::var_os("TG_CUDA_PTX_CACHE_DIR") {
        return PathBuf::from(path);
    }
    if let Some(path) = env::var_os("LOCALAPPDATA") {
        return PathBuf::from(path)
            .join("tensor-grep")
            .join("cuda-ptx-cache");
    }
    if let Some(path) = env::var_os("XDG_CACHE_HOME") {
        return PathBuf::from(path)
            .join("tensor-grep")
            .join("cuda-ptx-cache");
    }
    if let Some(path) = env::var_os("HOME") {
        return PathBuf::from(path)
            .join(".cache")
            .join("tensor-grep")
            .join("cuda-ptx-cache");
    }
    env::temp_dir().join("tensor-grep").join("cuda-ptx-cache")
}

pub(super) fn kernel_compile_options_for_compute_capability(
    compute_capability: (i32, i32),
) -> Result<CompileOptions> {
    Ok(CompileOptions {
        name: Some("gpu_native_search.cu".to_string()),
        options: vec![nvrtc_architecture_option_for_compute_capability(
            compute_capability,
        )?],
        ..Default::default()
    })
}

pub(super) fn nvrtc_architecture_option_for_compute_capability(
    compute_capability: (i32, i32),
) -> Result<String> {
    let (major, minor) = compute_capability;
    if major <= 0 || !(0..=9).contains(&minor) {
        return Err(anyhow!(
            "invalid CUDA compute capability {major}.{minor}; not falling back to a lower GPU architecture"
        ));
    }

    Ok(format!("--gpu-architecture=compute_{major}{minor}"))
}

pub(super) fn open_cuda_context(device_id: i32) -> Result<Arc<CudaContext>> {
    let ordinal = usize::try_from(device_id)
        .map_err(|_| anyhow!("CUDA device id {device_id} must be non-negative"))?;
    catch_cuda(&format!("open CUDA context for device {device_id}"), || {
        CudaContext::new(ordinal).map_err(anyhow::Error::new)
    })
}

pub(super) fn validate_device_id(device_id: i32) -> Result<()> {
    resolve_cuda_devices(&[device_id]).map(|_| ())
}

pub(super) fn format_available_device_ids_from_count(device_count: i32) -> String {
    if device_count <= 0 {
        return "none".to_string();
    }
    (0..device_count)
        .map(|device_id| device_id.to_string())
        .collect::<Vec<_>>()
        .join(", ")
}

pub(super) fn catch_cuda<T, F>(operation: &str, action: F) -> Result<T>
where
    F: FnOnce() -> Result<T>,
{
    ensure_cuda_library_path();
    match catch_unwind(AssertUnwindSafe(action)) {
        Ok(result) => result,
        Err(payload) => Err(anyhow!(
            "{operation} failed because CUDA libraries could not be loaded: {}",
            panic_payload_to_string(payload)
        )),
    }
}

pub(super) fn panic_payload_to_string(payload: Box<dyn std::any::Any + Send>) -> String {
    if let Some(message) = payload.downcast_ref::<String>() {
        return message.clone();
    }
    if let Some(message) = payload.downcast_ref::<&str>() {
        return (*message).to_string();
    }
    "unknown panic payload".to_string()
}

pub(super) fn ensure_cuda_library_path() {
    CUDA_LIBRARY_PATH_INIT.call_once(|| {
        let current_path = env::var_os("PATH").unwrap_or_default();
        let mut paths = env::split_paths(&current_path).collect::<Vec<_>>();
        let mut added_any = false;

        for candidate in cuda_library_search_paths() {
            if candidate.is_dir() && !paths.iter().any(|existing| existing == &candidate) {
                paths.insert(0, candidate);
                added_any = true;
            }
        }

        if added_any {
            if let Ok(updated_path) = env::join_paths(paths) {
                env::set_var("PATH", updated_path);
            }
        }
    });
}

fn cuda_library_search_paths() -> Vec<PathBuf> {
    let mut candidates = Vec::new();
    for env_var in [
        "CUDA_PATH",
        "CUDA_HOME",
        "CUDA_ROOT",
        "CUDA_TOOLKIT_ROOT_DIR",
    ] {
        if let Some(value) = env::var_os(env_var) {
            let base = PathBuf::from(value);
            push_cuda_bin_candidates(&mut candidates, &base);
        }
    }

    let default_root = PathBuf::from(r"C:\Program Files\NVIDIA GPU Computing Toolkit\CUDA");
    if let Ok(entries) = fs::read_dir(default_root) {
        for entry in entries.flatten() {
            push_cuda_bin_candidates(&mut candidates, &entry.path());
        }
    }

    candidates
}

fn push_cuda_bin_candidates(candidates: &mut Vec<PathBuf>, base: &Path) {
    for suffix in [
        PathBuf::from("bin"),
        PathBuf::from(r"bin\x64"),
        PathBuf::new(),
    ] {
        let candidate = if suffix.as_os_str().is_empty() {
            base.to_path_buf()
        } else {
            base.join(&suffix)
        };
        if !candidates.iter().any(|existing| existing == &candidate) {
            candidates.push(candidate);
        }
    }
}
