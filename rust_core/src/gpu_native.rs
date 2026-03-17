use anyhow::{anyhow, Context, Result};
use cudarc::driver::{
    result as cuda_result, sys, CudaContext, CudaEvent, CudaFunction, CudaModule, CudaSlice,
    CudaStream, DevicePtrMut, LaunchConfig, PinnedHostSlice, PushKernelArg,
};
use cudarc::nvrtc::{compile_ptx_with_opts, CompileOptions};
use ignore::{overrides::OverrideBuilder, WalkBuilder, WalkState};
use memchr::memchr_iter;
use rayon::prelude::*;
use std::collections::{BTreeSet, HashMap};
use std::env;
use std::fs;
use std::panic::{catch_unwind, AssertUnwindSafe};
use std::path::{Path, PathBuf};
use std::sync::Arc;
use std::sync::Once;
use std::time::Instant;

const POSITION_SEARCH_KERNEL_NAME: &str = "gpu_text_search_positions";
const SHORT_LINE_SEARCH_KERNEL_NAME: &str = "gpu_text_search_short_lines";
const WARP_LINE_SEARCH_KERNEL_NAME: &str = "gpu_text_search_warp_lines";
const BLOCK_LINE_SEARCH_KERNEL_NAME: &str = "gpu_text_search_block_lines";
const SEARCH_KERNEL_SOURCE: &str = r#"
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
const KERNEL_THREADS_PER_BLOCK: u32 = 256;
const SHORT_LINE_THREADS_PER_BLOCK: u32 = 128;
const WARP_LINE_THREADS_PER_BLOCK: u32 = 128;
const LONG_LINE_THREADS_PER_BLOCK: u32 = 256;
const SHORT_LINE_BYTES_THRESHOLD: u32 = 128;
const LONG_LINE_BYTES_THRESHOLD: u32 = 4 * 1024;
const WARP_SIZE: u32 = 32;
const WARPS_PER_BLOCK: u32 = WARP_LINE_THREADS_PER_BLOCK / WARP_SIZE;
const PIPELINE_SLOT_COUNT: usize = 2;
const DEFAULT_GPU_BATCH_BYTES: usize = 128 * 1024 * 1024;
const SHARED_PATTERN_MEMORY_LIMIT_BYTES: usize = 48 * 1024;
const MAX_SLOT_DEVICE_MEMORY_BYTES: usize = 1024 * 1024 * 1024;
static CUDA_LIBRARY_PATH_INIT: Once = Once::new();

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub struct MatchPosition {
    pub byte_offset: usize,
}

#[derive(Debug, Clone, Copy, PartialEq, Eq, PartialOrd, Ord)]
pub struct PatternMatchPosition {
    pub byte_offset: usize,
    pub pattern_id: usize,
}

#[derive(Debug, Clone, Default)]
pub struct GpuNativeSearchConfig {
    pub patterns: Vec<String>,
    pub paths: Vec<PathBuf>,
    pub no_ignore: bool,
    pub glob: Vec<String>,
    pub max_batch_bytes: Option<usize>,
}

#[derive(Debug, Clone, PartialEq, Eq)]
pub struct GpuNativeSearchMatch {
    pub path: PathBuf,
    pub line_number: usize,
    pub text: String,
    pub pattern_id: usize,
    pub pattern_text: String,
}

#[derive(Debug, Clone, PartialEq)]
pub struct GpuPipelineStats {
    pub pinned_host_buffers: bool,
    pub double_buffered: bool,
    pub stream_count: usize,
    pub batch_count: usize,
    pub overlapped_batches: usize,
    pub pattern_count: usize,
    pub pattern_batch_count: usize,
    pub single_dispatch: bool,
    pub short_line_count: usize,
    pub medium_line_count: usize,
    pub long_line_count: usize,
    pub warp_dispatch_count: usize,
    pub block_dispatch_count: usize,
    pub transfer_time_ms: f32,
    pub kernel_time_ms: f32,
    pub wall_time_ms: f64,
    pub transfer_throughput_bytes_s: f64,
}

impl Default for GpuPipelineStats {
    fn default() -> Self {
        Self {
            pinned_host_buffers: false,
            double_buffered: false,
            stream_count: 0,
            batch_count: 0,
            overlapped_batches: 0,
            pattern_count: 0,
            pattern_batch_count: 0,
            single_dispatch: false,
            short_line_count: 0,
            medium_line_count: 0,
            long_line_count: 0,
            warp_dispatch_count: 0,
            block_dispatch_count: 0,
            transfer_time_ms: 0.0,
            kernel_time_ms: 0.0,
            wall_time_ms: 0.0,
            transfer_throughput_bytes_s: 0.0,
        }
    }
}

#[derive(Debug, Clone, PartialEq)]
pub struct GpuPinnedTransferBenchmark {
    pub pinned_host_buffers: bool,
    pub double_buffered: bool,
    pub stream_count: usize,
    pub batch_count: usize,
    pub total_bytes: usize,
    pub batch_bytes: usize,
    pub transfer_time_ms: f32,
    pub wall_time_ms: f64,
    pub throughput_bytes_per_s: f64,
}

#[derive(Debug, Clone, PartialEq)]
pub struct GpuNativeDeviceStats {
    pub device: CudaDeviceInfo,
    pub searched_files: usize,
    pub matched_files: usize,
    pub total_matches: usize,
    pub transfer_bytes: usize,
    pub pipeline: GpuPipelineStats,
}

#[derive(Debug, Clone, PartialEq)]
pub struct GpuNativeSearchStats {
    pub searched_files: usize,
    pub matched_files: usize,
    pub total_matches: usize,
    pub transfer_bytes: usize,
    pub pattern_count: usize,
    pub selected_device: CudaDeviceInfo,
    pub selected_devices: Vec<CudaDeviceInfo>,
    pub device_stats: Vec<GpuNativeDeviceStats>,
    pub matches: Vec<GpuNativeSearchMatch>,
    pub pipeline: GpuPipelineStats,
}

#[derive(Debug, Clone)]
struct SearchFileEntry {
    path: PathBuf,
    estimated_bytes: usize,
    order: usize,
}

#[derive(Debug, Clone)]
struct DeviceFileAssignment {
    device: CudaDeviceInfo,
    files: Vec<SearchFileEntry>,
    assigned_bytes: usize,
}

#[derive(Debug, Clone)]
struct BatchedFile {
    path: PathBuf,
    start: usize,
    end: usize,
}

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
struct LineDescriptor {
    start: u32,
    len: u32,
}

#[derive(Debug, Clone, Default)]
struct ClassifiedLineBatch {
    short_lines: Vec<LineDescriptor>,
    medium_lines: Vec<LineDescriptor>,
    long_lines: Vec<LineDescriptor>,
}

impl ClassifiedLineBatch {
    fn short_line_count(&self) -> usize {
        self.short_lines.len()
    }

    fn medium_line_count(&self) -> usize {
        self.medium_lines.len()
    }

    fn long_line_count(&self) -> usize {
        self.long_lines.len()
    }
}

#[derive(Debug, Clone)]
struct FileBatchPlan {
    files: Vec<PathBuf>,
    estimated_bytes: usize,
}

#[derive(Debug, Clone)]
struct PatternBatchPlan {
    global_pattern_ids: Vec<usize>,
    pattern_offsets: Vec<u32>,
    pattern_lengths: Vec<u32>,
    pattern_blob: Vec<u8>,
    shared_mem_bytes: usize,
}

#[derive(Debug, Clone)]
struct LoadedFileBatch {
    files: Vec<BatchedFile>,
    bytes_used: usize,
    classified_lines: ClassifiedLineBatch,
}

struct DevicePatternBatch {
    host: Arc<PatternBatchPlan>,
    pattern_blob_device: CudaSlice<u8>,
    pattern_offsets_device: CudaSlice<u32>,
    pattern_lengths_device: CudaSlice<u32>,
}

struct DeviceLineClassBuffers {
    line_starts_device: CudaSlice<u32>,
    line_lengths_device: CudaSlice<u32>,
    line_count: usize,
}

#[derive(Debug, Clone, Default)]
struct AdaptiveDispatchStats {
    short_line_count: usize,
    medium_line_count: usize,
    long_line_count: usize,
    warp_dispatch_count: usize,
    block_dispatch_count: usize,
}

struct SlotAdaptiveDispatch {
    short_lines: Option<DeviceLineClassBuffers>,
    medium_lines: Option<DeviceLineClassBuffers>,
    long_lines: Option<DeviceLineClassBuffers>,
    stats: AdaptiveDispatchStats,
}

impl SlotAdaptiveDispatch {
    fn total_lines(&self) -> usize {
        self.stats.short_line_count + self.stats.medium_line_count + self.stats.long_line_count
    }
}

#[derive(Debug, Clone)]
struct SearchDispatchPlan {
    file_batch: FileBatchPlan,
    pattern_batch_index: usize,
}

#[derive(Debug, Clone, PartialEq, Eq)]
pub struct CudaDeviceInfo {
    pub device_id: i32,
    pub name: String,
    pub compute_capability: (i32, i32),
}

struct KernelRuntime {
    context: Arc<CudaContext>,
    _module: Arc<CudaModule>,
    position_function: CudaFunction,
    short_line_function: CudaFunction,
    warp_line_function: CudaFunction,
    block_line_function: CudaFunction,
}

struct SearchPipelineSlot {
    stream: Arc<CudaStream>,
    host_buffer: RawPinnedHostBuffer,
    data_buffer: CudaSlice<u8>,
    match_positions_buffer: CudaSlice<u32>,
    match_pattern_ids_buffer: CudaSlice<u32>,
    match_count_buffer: CudaSlice<u32>,
    current_batch: Option<LoadedFileBatch>,
    adaptive_dispatch: Option<SlotAdaptiveDispatch>,
    current_pattern_batch_index: Option<usize>,
    transfer_start: Option<CudaEvent>,
    transfer_end: Option<CudaEvent>,
    kernel_end: Option<CudaEvent>,
}

struct TransferBenchmarkSlot {
    stream: Arc<CudaStream>,
    host_buffer: RawPinnedHostBuffer,
    device_buffer: CudaSlice<u8>,
    transfer_start: Option<CudaEvent>,
    transfer_end: Option<CudaEvent>,
    pending_transfer: bool,
}

struct RawPinnedHostBuffer {
    inner: PinnedHostSlice<u8>,
}

impl RawPinnedHostBuffer {
    fn new(context: &Arc<CudaContext>, len: usize) -> Result<Self> {
        let inner = catch_cuda("allocate CUDA pinned host buffer", || unsafe {
            context
                .alloc_pinned::<u8>(len.max(1))
                .map_err(anyhow::Error::new)
        })?;

        Ok(Self { inner })
    }

    fn as_slice(&self) -> Result<&[u8]> {
        self.inner
            .as_slice()
            .map_err(anyhow::Error::new)
            .context("failed to access CUDA pinned host buffer")
    }

    fn as_mut_slice(&mut self) -> Result<&mut [u8]> {
        self.inner
            .as_mut_slice()
            .map_err(anyhow::Error::new)
            .context("failed to access CUDA pinned host buffer mutably")
    }
}

struct SearchPipelineOutcome {
    transfer_bytes: usize,
    matches: Vec<GpuNativeSearchMatch>,
    pipeline: GpuPipelineStats,
}

struct DeviceSearchOutcome {
    stats: GpuNativeDeviceStats,
    matches: Vec<GpuNativeSearchMatch>,
}

pub fn enumerate_cuda_devices() -> Result<Vec<CudaDeviceInfo>> {
    let device_count = catch_cuda("initialize CUDA driver", || {
        CudaContext::device_count().map_err(anyhow::Error::new)
    })?;

    let mut devices = Vec::new();
    for ordinal in 0..device_count {
        let context = open_cuda_context(ordinal)?;
        let name = context
            .name()
            .map_err(anyhow::Error::new)
            .with_context(|| format!("failed to read CUDA device {ordinal} name"))?;
        let compute_capability = context
            .compute_capability()
            .map_err(anyhow::Error::new)
            .with_context(|| {
                format!("failed to read CUDA device {ordinal} compute capability")
            })?;
        devices.push(CudaDeviceInfo {
            device_id: ordinal,
            name,
            compute_capability,
        });
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
        return Err(anyhow!("GPU pinned transfer benchmark requires total_bytes > 0"));
    }
    if batch_bytes == 0 {
        return Err(anyhow!("GPU pinned transfer benchmark requires batch_bytes > 0"));
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
        copy_pinned_host_to_device(&slot.stream, &slot.host_buffer, bytes_this_batch, &mut slot.device_buffer)
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
    if config.patterns.is_empty() {
        return Err(anyhow!("GPU native search requires at least one non-empty pattern"));
    }
    if config.paths.is_empty() {
        return Err(anyhow!("GPU native search requires at least one search path"));
    }

    if config.patterns.iter().any(|pattern| pattern.is_empty()) {
        return Err(anyhow!(
            "GPU native search requires all patterns to be non-empty"
        ));
    }

    if device_ids.is_empty() {
        return Err(anyhow!("GPU native search requires at least one CUDA device id"));
    }

    let files = collect_search_files(config)?;
    let selected_devices = resolve_cuda_devices(device_ids)?;
    let assignments = assign_files_to_devices(&files, &selected_devices)?;
    let device_outcomes = assignments
        .into_par_iter()
        .map(|assignment| run_device_assignment(config, assignment))
        .collect::<Result<Vec<_>>>()?;
    let matches = merge_device_matches(&files, &device_outcomes);
    let matched_files = matches
        .iter()
        .map(|matched| matched.path.clone())
        .collect::<BTreeSet<_>>()
        .len();
    let transfer_bytes = device_outcomes.iter().map(|outcome| outcome.stats.transfer_bytes).sum();
    let device_stats = device_outcomes
        .iter()
        .map(|outcome| outcome.stats.clone())
        .collect::<Vec<_>>();
    let selected_device = selected_devices
        .first()
        .cloned()
        .context("GPU native search requires at least one resolved CUDA device")?;
    let pipeline = aggregate_device_pipeline_stats(&device_outcomes, config.patterns.len(), transfer_bytes);

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
    })
}

fn run_device_assignment(
    config: &GpuNativeSearchConfig,
    assignment: DeviceFileAssignment,
) -> Result<DeviceSearchOutcome> {
    let files = assignment
        .files
        .iter()
        .map(|entry| entry.path.clone())
        .collect::<Vec<_>>();
    let outcome = run_device_search(config, &files, &assignment.device)?;
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

fn run_device_search(
    config: &GpuNativeSearchConfig,
    files: &[PathBuf],
    selected_device: &CudaDeviceInfo,
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
    )
}

fn aggregate_device_pipeline_stats(
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
    let wall_time_ms = device_outcomes
        .iter()
        .map(|outcome| outcome.stats.pipeline.wall_time_ms)
        .fold(0.0f64, f64::max);
    let transfer_throughput_bytes_s = if device_outcomes.len() == 1 {
        device_outcomes[0].stats.pipeline.transfer_throughput_bytes_s
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
        transfer_time_ms,
        kernel_time_ms: device_outcomes
            .iter()
            .map(|outcome| outcome.stats.pipeline.kernel_time_ms)
            .sum(),
        wall_time_ms,
        transfer_throughput_bytes_s,
    }
}

fn merge_device_matches(
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
            .then(left.text.cmp(&right.text))
    });

    let mut seen = BTreeSet::new();
    matches
        .into_iter()
        .filter(|matched| {
            seen.insert((
                matched.path.clone(),
                matched.line_number,
                matched.text.clone(),
                matched.pattern_id,
            ))
        })
        .collect()
}

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

    let pattern_strings = patterns.iter().map(|pattern| (*pattern).to_string()).collect::<Vec<_>>();
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

    let data_len = i32::try_from(data.len()).context("GPU native search input exceeds i32 length")?;
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

        matches.extend(positions.into_iter().zip(pattern_ids.into_iter()).map(|(byte_offset, local_pattern_id)| {
            PatternMatchPosition {
                byte_offset: byte_offset as usize,
                pattern_id: device_batch.host.global_pattern_ids[local_pattern_id as usize],
            }
        }));
    }

    matches.sort();
    Ok(matches)
}

fn create_kernel_runtime(device_id: i32) -> Result<KernelRuntime> {
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

fn run_search_pipeline(
    runtime: &KernelRuntime,
    dispatch_plans: &[SearchDispatchPlan],
    pattern_batches: &[PatternBatchPlan],
    slot_capacity: usize,
    _searched_files: usize,
    all_patterns: &[String],
) -> Result<SearchPipelineOutcome> {
    let max_patterns_per_dispatch = pattern_batches
        .iter()
        .map(|batch| batch.global_pattern_ids.len())
        .max()
        .unwrap_or(1);
    let device_pattern_batches = upload_pattern_batches(runtime, pattern_batches)?;
    let mut slots = (0..PIPELINE_SLOT_COUNT)
        .map(|_| create_search_pipeline_slot(runtime, slot_capacity, max_patterns_per_dispatch))
        .collect::<Result<Vec<_>>>()?;
    let pattern_stream = runtime.context.default_stream();
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
    let active_stream_count = dispatch_plans.len().clamp(1, PIPELINE_SLOT_COUNT);
    let mut pipeline_start = None;

    for (dispatch_index, plan) in dispatch_plans.iter().enumerate() {
        let slot = &mut slots[dispatch_index % PIPELINE_SLOT_COUNT];
        finalize_search_pipeline_slot(
            slot,
            &device_pattern_batches,
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
        )?;
        load_file_batch_into_slot(slot, &plan.file_batch)?;
        slot.current_pattern_batch_index = Some(plan.pattern_batch_index);
        if pipeline_start.is_none() {
            pipeline_start = Some(record_timed_event(&slot.stream)?);
        }
        launch_slot_search(slot, runtime, &device_pattern_batches[plan.pattern_batch_index])?;
    }

    let pipeline_end = if let Some(start) = pipeline_start.as_ref() {
        for slot in &slots {
            if let Some(kernel_end) = slot.kernel_end.as_ref() {
                pattern_stream
                    .wait(kernel_end)
                    .map_err(anyhow::Error::new)
                    .context("failed to coordinate GPU search pipeline completion")?;
            }
        }
        let end = record_timed_event(&pattern_stream)?;
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
            &device_pattern_batches,
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
            pattern_count: all_patterns.len(),
            pattern_batch_count: pattern_batches.len(),
            single_dispatch: pattern_batches.len() == 1,
            short_line_count,
            medium_line_count,
            long_line_count,
            warp_dispatch_count,
            block_dispatch_count,
            transfer_time_ms,
            kernel_time_ms,
            wall_time_ms,
            transfer_throughput_bytes_s,
        },
    })
}

fn create_search_pipeline_slot(
    runtime: &KernelRuntime,
    capacity: usize,
    max_pattern_count: usize,
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
    let match_buffer_capacity = resolve_max_match_capacity(capacity, max_pattern_count)?;
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

    Ok(SearchPipelineSlot {
        stream,
        host_buffer,
        data_buffer,
        match_positions_buffer,
        match_pattern_ids_buffer,
        match_count_buffer,
        current_batch: None,
        adaptive_dispatch: None,
        current_pattern_batch_index: None,
        transfer_start: None,
        transfer_end: None,
        kernel_end: None,
    })
}

fn create_transfer_benchmark_slot(
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

fn finalize_transfer_benchmark_slot(
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

#[allow(clippy::too_many_arguments)]
fn finalize_search_pipeline_slot(
    slot: &mut SearchPipelineSlot,
    device_pattern_batches: &[DevicePatternBatch],
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
) -> Result<()> {
    let Some(batch) = slot.current_batch.take() else {
        slot.adaptive_dispatch = None;
        slot.current_pattern_batch_index = None;
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
    let pattern_batch = &device_pattern_batches[pattern_batch_index].host;

    if pattern_batch_index == 0 {
        *short_line_count += adaptive_dispatch.stats.short_line_count;
        *medium_line_count += adaptive_dispatch.stats.medium_line_count;
        *long_line_count += adaptive_dispatch.stats.long_line_count;
    }
    *warp_dispatch_count += adaptive_dispatch.stats.warp_dispatch_count;
    *block_dispatch_count += adaptive_dispatch.stats.block_dispatch_count;

    if batch.bytes_used == 0 || adaptive_dispatch.total_lines() == 0 {
        slot.transfer_start = None;
        slot.transfer_end = None;
        slot.kernel_end = None;
        return Ok(());
    }
    *transfer_bytes += batch.bytes_used;

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
    *transfer_time_ms += batch_transfer_time_ms;
    *kernel_time_ms += batch_kernel_time_ms;
    *overlapped_device_time_ms += batch_transfer_time_ms.max(batch_kernel_time_ms);

    let match_count = slot
        .stream
        .clone_dtoh(&slot.match_count_buffer)
        .map_err(anyhow::Error::new)
        .context("failed to copy GPU search pipeline match count back to host")?
        .into_iter()
        .next()
        .unwrap_or(0) as usize;
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
    Ok(())
}

fn load_file_batch_into_slot(slot: &mut SearchPipelineSlot, plan: &FileBatchPlan) -> Result<()> {
    let host_buffer = slot.host_buffer.as_mut_slice()?;
    let mut cursor = 0usize;
    let mut batch_files = Vec::new();
    let mut classified_lines = ClassifiedLineBatch::default();

    for path in &plan.files {
        let bytes = fs::read(path)
            .with_context(|| format!("failed to read GPU native search file {}", path.display()))?;
        if bytes.contains(&b'\0') {
            continue;
        }

        if cursor > 0 {
            ensure_capacity(host_buffer.len(), cursor.saturating_add(1), path)?;
            host_buffer[cursor] = 0;
            cursor += 1;
        }

        let start = cursor;
        let end = start.saturating_add(bytes.len());
        ensure_capacity(host_buffer.len(), end, path)?;
        host_buffer[start..end].copy_from_slice(&bytes);
        cursor = end;

        batch_files.push(BatchedFile {
            path: path.clone(),
            start,
            end,
        });
        classify_file_lines(start, &bytes, &mut classified_lines)?;
    }

    slot.current_batch = Some(LoadedFileBatch {
        files: batch_files,
        bytes_used: cursor,
        classified_lines,
    });
    slot.adaptive_dispatch = None;
    slot.transfer_start = None;
    slot.transfer_end = None;
    slot.kernel_end = None;
    Ok(())
}

fn launch_slot_search(
    slot: &mut SearchPipelineSlot,
    runtime: &KernelRuntime,
    pattern_batch: &DevicePatternBatch,
) -> Result<()> {
    let Some(batch) = slot.current_batch.as_ref() else {
        return Ok(());
    };
    if batch.bytes_used == 0 {
        slot.adaptive_dispatch = Some(SlotAdaptiveDispatch {
            short_lines: None,
            medium_lines: None,
            long_lines: None,
            stats: AdaptiveDispatchStats::default(),
        });
        return Ok(());
    }

    let adaptive_dispatch = upload_adaptive_dispatch(&slot.stream, &batch.classified_lines)?;
    let total_lines = adaptive_dispatch.total_lines();
    let max_matches = u32::try_from(
        total_lines
            .checked_mul(pattern_batch.host.global_pattern_ids.len())
            .context("GPU search batch match capacity overflow")?,
    )
    .context("GPU search batch exceeds u32 match capacity")?;
    slot.adaptive_dispatch = Some(adaptive_dispatch);
    if total_lines == 0 {
        return Ok(());
    }

    slot.stream
        .memset_zeros(&mut slot.match_count_buffer)
        .map_err(anyhow::Error::new)
        .context("failed to reset GPU search pipeline match counter")?;
    slot.transfer_start = Some(record_timed_event(&slot.stream)?);
    copy_pinned_host_to_device(&slot.stream, &slot.host_buffer, batch.bytes_used, &mut slot.data_buffer)
        .context("failed to copy pinned search batch to CUDA device")?;
    slot.transfer_end = Some(record_timed_event(&slot.stream)?);
    let dispatch = slot
        .adaptive_dispatch
        .as_ref()
        .context("missing adaptive dispatch buffers for GPU search launch")?;
    if let Some(short_lines) = dispatch.short_lines.as_ref() {
        launch_line_search_kernel(
            &slot.stream,
            &runtime.short_line_function,
            SHORT_LINE_THREADS_PER_BLOCK,
            short_lines,
            &slot.data_buffer,
            pattern_batch,
            &mut slot.match_positions_buffer,
            &mut slot.match_pattern_ids_buffer,
            max_matches,
            &mut slot.match_count_buffer,
        )?;
    }
    if let Some(medium_lines) = dispatch.medium_lines.as_ref() {
        launch_warp_line_search_kernel(
            &slot.stream,
            &runtime.warp_line_function,
            medium_lines,
            &slot.data_buffer,
            pattern_batch,
            &mut slot.match_positions_buffer,
            &mut slot.match_pattern_ids_buffer,
            max_matches,
            &mut slot.match_count_buffer,
        )?;
    }
    if let Some(long_lines) = dispatch.long_lines.as_ref() {
        launch_line_search_kernel(
            &slot.stream,
            &runtime.block_line_function,
            LONG_LINE_THREADS_PER_BLOCK,
            long_lines,
            &slot.data_buffer,
            pattern_batch,
            &mut slot.match_positions_buffer,
            &mut slot.match_pattern_ids_buffer,
            max_matches,
            &mut slot.match_count_buffer,
        )?;
    }
    slot.kernel_end = Some(record_timed_event(&slot.stream)?);
    Ok(())
}

#[allow(clippy::too_many_arguments)]
fn launch_position_search_kernel(
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
        grid_dim: ((u32::try_from(data_len).unwrap_or(0)).div_ceil(KERNEL_THREADS_PER_BLOCK), 1, 1),
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
fn launch_line_search_kernel(
    stream: &Arc<CudaStream>,
    function: &CudaFunction,
    threads_per_block: u32,
    line_buffers: &DeviceLineClassBuffers,
    data_device: &CudaSlice<u8>,
    pattern_batch: &DevicePatternBatch,
    match_positions_device: &mut CudaSlice<u32>,
    match_pattern_ids_device: &mut CudaSlice<u32>,
    max_matches: u32,
    match_count_device: &mut CudaSlice<u32>,
) -> Result<()> {
    let line_count = i32::try_from(line_buffers.line_count)
        .context("GPU native line search line count exceeds i32 length")?;
    let launch_config = LaunchConfig {
        grid_dim: (u32::try_from(line_buffers.line_count).unwrap_or(0), 1, 1),
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
        launch.arg(&line_buffers.line_starts_device);
        launch.arg(&line_buffers.line_lengths_device);
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
fn launch_warp_line_search_kernel(
    stream: &Arc<CudaStream>,
    function: &CudaFunction,
    line_buffers: &DeviceLineClassBuffers,
    data_device: &CudaSlice<u8>,
    pattern_batch: &DevicePatternBatch,
    match_positions_device: &mut CudaSlice<u32>,
    match_pattern_ids_device: &mut CudaSlice<u32>,
    max_matches: u32,
    match_count_device: &mut CudaSlice<u32>,
) -> Result<()> {
    let line_count = i32::try_from(line_buffers.line_count)
        .context("GPU native warp line search count exceeds i32 length")?;
    let launch_config = LaunchConfig {
        grid_dim: (
            u32::try_from(line_buffers.line_count)
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
        launch.arg(&line_buffers.line_starts_device);
        launch.arg(&line_buffers.line_lengths_device);
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

fn upload_adaptive_dispatch(
    stream: &Arc<CudaStream>,
    classified_lines: &ClassifiedLineBatch,
) -> Result<SlotAdaptiveDispatch> {
    Ok(SlotAdaptiveDispatch {
        short_lines: upload_line_class_buffers(stream, &classified_lines.short_lines)?,
        medium_lines: upload_line_class_buffers(stream, &classified_lines.medium_lines)?,
        long_lines: upload_line_class_buffers(stream, &classified_lines.long_lines)?,
        stats: AdaptiveDispatchStats {
            short_line_count: classified_lines.short_line_count(),
            medium_line_count: classified_lines.medium_line_count(),
            long_line_count: classified_lines.long_line_count(),
            warp_dispatch_count: usize::from(!classified_lines.medium_lines.is_empty()),
            block_dispatch_count: usize::from(!classified_lines.long_lines.is_empty()),
        },
    })
}

fn upload_line_class_buffers(
    stream: &Arc<CudaStream>,
    lines: &[LineDescriptor],
) -> Result<Option<DeviceLineClassBuffers>> {
    if lines.is_empty() {
        return Ok(None);
    }

    let line_starts = lines.iter().map(|line| line.start).collect::<Vec<_>>();
    let line_lengths = lines.iter().map(|line| line.len).collect::<Vec<_>>();
    let line_starts_device = stream
        .clone_htod(line_starts.as_slice())
        .map_err(anyhow::Error::new)
        .context("failed to copy adaptive line starts to CUDA device")?;
    let line_lengths_device = stream
        .clone_htod(line_lengths.as_slice())
        .map_err(anyhow::Error::new)
        .context("failed to copy adaptive line lengths to CUDA device")?;

    Ok(Some(DeviceLineClassBuffers {
        line_starts_device,
        line_lengths_device,
        line_count: lines.len(),
    }))
}

fn classify_file_lines(
    batch_start: usize,
    bytes: &[u8],
    classified_lines: &mut ClassifiedLineBatch,
) -> Result<()> {
    let mut line_start = 0usize;
    for newline_index in memchr_iter(b'\n', bytes) {
        push_classified_line(
            batch_start.saturating_add(line_start),
            newline_index.saturating_sub(line_start),
            classified_lines,
        )?;
        line_start = newline_index.saturating_add(1);
    }
    if line_start < bytes.len() {
        push_classified_line(
            batch_start.saturating_add(line_start),
            bytes.len().saturating_sub(line_start),
            classified_lines,
        )?;
    }
    Ok(())
}

fn push_classified_line(
    absolute_start: usize,
    line_len: usize,
    classified_lines: &mut ClassifiedLineBatch,
) -> Result<()> {
    if line_len == 0 {
        return Ok(());
    }

    let descriptor = LineDescriptor {
        start: u32::try_from(absolute_start)
            .context("GPU native line start exceeds u32 range")?,
        len: u32::try_from(line_len).context("GPU native line length exceeds u32 range")?,
    };

    if descriptor.len < SHORT_LINE_BYTES_THRESHOLD {
        classified_lines.short_lines.push(descriptor);
    } else if descriptor.len <= LONG_LINE_BYTES_THRESHOLD {
        classified_lines.medium_lines.push(descriptor);
    } else {
        classified_lines.long_lines.push(descriptor);
    }

    Ok(())
}

fn record_timed_event(stream: &Arc<CudaStream>) -> Result<CudaEvent> {
    stream
        .record_event(Some(sys::CUevent_flags::CU_EVENT_DEFAULT))
        .map_err(anyhow::Error::new)
        .context("failed to record CUDA timing event")
}

fn copy_pinned_host_to_device(
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

fn resolve_max_batch_bytes(config: &GpuNativeSearchConfig) -> usize {
    config.max_batch_bytes.unwrap_or(DEFAULT_GPU_BATCH_BYTES).max(1)
}

fn resolve_effective_max_batch_bytes(
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

fn resolve_max_match_capacity(bytes_used: usize, pattern_count: usize) -> Result<usize> {
    bytes_used
        .checked_mul(pattern_count.max(1))
        .context("GPU native search match capacity overflow")
}

fn plan_pattern_batches(patterns: &[String]) -> Result<Vec<PatternBatchPlan>> {
    let mut batches = Vec::new();
    let mut current_ids = Vec::new();
    let mut current_offsets = Vec::new();
    let mut current_lengths = Vec::new();
    let mut current_blob = Vec::new();

    for (pattern_id, pattern) in patterns.iter().enumerate() {
        let pattern_len = u32::try_from(pattern.len())
            .with_context(|| format!("GPU native search pattern is too large: {} bytes", pattern.len()))?;
        let candidate_pattern_count = current_ids.len() + 1;
        let candidate_blob_len = current_blob.len().saturating_add(pattern.len());
        let candidate_shared_mem = resolve_pattern_shared_mem_bytes(candidate_pattern_count, candidate_blob_len)?;

        if !current_ids.is_empty() && candidate_shared_mem > SHARED_PATTERN_MEMORY_LIMIT_BYTES {
            batches.push(PatternBatchPlan {
                global_pattern_ids: current_ids,
                pattern_offsets: current_offsets,
                pattern_lengths: current_lengths,
                pattern_blob: current_blob,
                shared_mem_bytes: resolve_pattern_shared_mem_bytes(candidate_pattern_count - 1, candidate_blob_len - pattern.len())?,
            });
            current_ids = Vec::new();
            current_offsets = Vec::new();
            current_lengths = Vec::new();
            current_blob = Vec::new();
        }

        let shared_mem_bytes = resolve_pattern_shared_mem_bytes(current_ids.len() + 1, current_blob.len() + pattern.len())?;
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

fn resolve_pattern_shared_mem_bytes(pattern_count: usize, pattern_blob_len: usize) -> Result<usize> {
    pattern_count
        .checked_mul(std::mem::size_of::<u32>() * 2)
        .and_then(|metadata| metadata.checked_add(pattern_blob_len))
        .context("GPU native search shared memory requirement overflow")
}

fn plan_search_dispatches(
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

fn upload_pattern_batches(
    runtime: &KernelRuntime,
    pattern_batches: &[PatternBatchPlan],
) -> Result<Vec<DevicePatternBatch>> {
    let stream = runtime.context.default_stream();
    pattern_batches
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
        .collect()
}

fn plan_file_batches(files: &[PathBuf], max_batch_bytes: usize) -> Result<Vec<FileBatchPlan>> {
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

        if !current_files.is_empty() && current_bytes.saturating_add(additional_bytes) > max_batch_bytes {
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

fn file_estimated_bytes(path: &Path) -> Result<usize> {
    usize::try_from(
        fs::metadata(path)
            .with_context(|| format!("failed to stat GPU native search file {}", path.display()))?
            .len(),
    )
    .with_context(|| format!("GPU native search file is too large: {}", path.display()))
}

fn resolve_cuda_devices(device_ids: &[i32]) -> Result<Vec<CudaDeviceInfo>> {
    if device_ids.is_empty() {
        return Err(anyhow!("GPU native search requires at least one CUDA device id"));
    }

    let devices = enumerate_cuda_devices()?;
    if devices.is_empty() {
        return Err(anyhow!(
            "CUDA is unavailable: no CUDA devices were detected by cudarc"
        ));
    }

    let mut selected = Vec::new();
    let mut seen = BTreeSet::new();
    for &device_id in device_ids {
        if !seen.insert(device_id) {
            continue;
        }
        let device = devices
            .iter()
            .find(|candidate| candidate.device_id == device_id)
            .cloned()
            .ok_or_else(|| {
                anyhow!(
                    "invalid CUDA device id {device_id}; available CUDA devices: {}",
                    format_available_devices(&devices)
                )
            })?;
        selected.push(device);
    }

    Ok(selected)
}

fn assign_files_to_devices(
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

fn minimum_files_per_device(total_files: usize, device_count: usize) -> usize {
    if total_files == 0 || device_count == 0 {
        return 0;
    }

    let mut minimum = total_files.div_ceil(10).max(1);
    while minimum > 0 && minimum.saturating_mul(device_count) > total_files {
        minimum -= 1;
    }
    minimum
}

fn add_file_assignment(assignment: &mut DeviceFileAssignment, entry: SearchFileEntry) {
    assignment.assigned_bytes = assignment.assigned_bytes.saturating_add(entry.estimated_bytes);
    assignment.files.push(entry);
}

fn ensure_capacity(capacity: usize, required: usize, path: &Path) -> Result<()> {
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

fn collect_search_files(config: &GpuNativeSearchConfig) -> Result<Vec<PathBuf>> {
    let mut files = Vec::new();
    let mut roots = Vec::new();

    for path in &config.paths {
        if !path.exists() {
            return Err(anyhow!("GPU native search path does not exist: {}", path.display()));
        }
        if path.is_file() {
            files.push(path.clone());
        } else {
            roots.push(path.clone());
        }
    }

    if !roots.is_empty() {
        files.extend(collect_walked_files(config, &roots)?);
    }

    files.sort_unstable();
    files.dedup();
    Ok(files)
}

fn collect_walked_files(config: &GpuNativeSearchConfig, roots: &[PathBuf]) -> Result<Vec<PathBuf>> {
    let builder = build_walk_builder(config, roots)?;
    let walked_files = Arc::new(std::sync::Mutex::new(Vec::new()));
    let shared_files = Arc::clone(&walked_files);

    builder.build_parallel().run(|| {
        let shared_files = Arc::clone(&shared_files);
        Box::new(move |entry| {
            if let Ok(entry) = entry {
                if entry.file_type().map(|kind| kind.is_file()).unwrap_or(false) {
                    if let Ok(mut guard) = shared_files.lock() {
                        guard.push(entry.path().to_path_buf());
                    }
                }
            }
            WalkState::Continue
        })
    });

    let files = walked_files
        .lock()
        .map_err(|_| anyhow!("failed to collect GPU native search walk results"))?
        .clone();
    Ok(files)
}

fn build_walk_builder(config: &GpuNativeSearchConfig, roots: &[PathBuf]) -> Result<WalkBuilder> {
    let first_root = roots[0].clone();
    let mut builder = WalkBuilder::new(&first_root);
    for root in roots.iter().skip(1) {
        builder.add(root);
    }
    builder.threads(0);

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

fn convert_offsets_to_line_matches(
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
            &file.path,
            file_bytes,
            &file_offsets,
            all_patterns,
        ));
    }

    Ok(matches)
}

fn line_matches_for_file(
    path: &Path,
    file_bytes: &[u8],
    offsets: &[PatternMatchPosition],
    all_patterns: &[String],
) -> Vec<GpuNativeSearchMatch> {
    let newline_positions = memchr_iter(b'\n', file_bytes).collect::<Vec<_>>();
    let mut matches = Vec::new();
    let mut seen = std::collections::BTreeSet::new();

    for offset in offsets {
        let newline_index = newline_positions.partition_point(|position| *position < offset.byte_offset);
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
        let text = String::from_utf8_lossy(line_bytes)
            .trim_end_matches('\r')
            .to_string();
        matches.push(GpuNativeSearchMatch {
            path: path.to_path_buf(),
            line_number: newline_index + 1,
            text,
            pattern_id: offset.pattern_id,
            pattern_text: all_patterns[offset.pattern_id].clone(),
        });
    }

    matches
}

fn compile_kernel_module(context: &Arc<CudaContext>, device_id: i32) -> Result<Arc<CudaModule>> {
    let ptx = catch_cuda("compile CUDA substring kernel via NVRTC", || {
        compile_ptx_with_opts(
            SEARCH_KERNEL_SOURCE,
            CompileOptions {
                name: Some("gpu_native_search.cu".to_string()),
                ..Default::default()
            },
        )
        .map_err(anyhow::Error::new)
    })
    .with_context(|| format!("failed to compile CUDA substring kernel for device {device_id}"))?;

    context
        .load_module(ptx)
        .map_err(anyhow::Error::new)
        .with_context(|| format!("failed to load compiled CUDA module for device {device_id}"))
}

fn open_cuda_context(device_id: i32) -> Result<Arc<CudaContext>> {
    let ordinal = usize::try_from(device_id)
        .map_err(|_| anyhow!("CUDA device id {device_id} must be non-negative"))?;
    catch_cuda(&format!("open CUDA context for device {device_id}"), || {
        CudaContext::new(ordinal).map_err(anyhow::Error::new)
    })
}

fn validate_device_id(device_id: i32) -> Result<()> {
    resolve_cuda_devices(&[device_id]).map(|_| ())
}

fn format_available_devices(devices: &[CudaDeviceInfo]) -> String {
    devices
        .iter()
        .map(|device| {
            format!(
                "{}:{} (sm_{}{})",
                device.device_id,
                device.name,
                device.compute_capability.0,
                device.compute_capability.1
            )
        })
        .collect::<Vec<_>>()
        .join(", ")
}

fn catch_cuda<T, F>(operation: &str, action: F) -> Result<T>
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

fn panic_payload_to_string(payload: Box<dyn std::any::Any + Send>) -> String {
    if let Some(message) = payload.downcast_ref::<String>() {
        return message.clone();
    }
    if let Some(message) = payload.downcast_ref::<&str>() {
        return (*message).to_string();
    }
    "unknown panic payload".to_string()
}

fn ensure_cuda_library_path() {
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
    for env_var in ["CUDA_PATH", "CUDA_HOME", "CUDA_ROOT", "CUDA_TOOLKIT_ROOT_DIR"] {
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
    for suffix in [PathBuf::from("bin"), PathBuf::from(r"bin\x64"), PathBuf::new()] {
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
