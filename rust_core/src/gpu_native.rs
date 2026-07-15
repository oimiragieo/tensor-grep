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
const PTX_CACHE_VERSION: &str = "v1";
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
    pub hidden: bool,
    pub max_depth: Option<usize>,
    pub max_batch_bytes: Option<usize>,
    /// Whether the caller omitted an explicit PATH (the search root defaulted to `.` rather than
    /// a user-supplied path). Gates `check_gpu_native_implicit_walk_ceiling`, this engine's own
    /// refuse-before-enumerate guard (audit #109 -- the cuda-native-GPU sibling of
    /// `NativeSearchConfig::path_was_implicit`, audit #105, itself the native-CPU sibling of
    /// `RipgrepSearchArgs::path_was_implicit`, audit #100). An explicit, deliberately-scoped PATH
    /// must never be refused regardless of its size. `Default`'s `false` is NOT a safe fallback for
    /// the walk guard itself (it means "never refuse"); it only exists so ad hoc test fixtures and
    /// the always-explicit-`--path` diagnostic commands (`gpu-native-stats`, cuda-graph benchmark)
    /// get deterministic, non-refusing behavior, mirroring `NativeSearchConfig`'s convention.
    pub path_was_implicit: bool,
}

#[derive(Debug, Clone, PartialEq, Eq, Serialize)]
pub struct GpuNativeSearchMatch {
    pub path: PathBuf,
    pub line_number: usize,
    pub text: String,
    pub pattern_id: usize,
    pub pattern_text: String,
}

#[derive(Debug, Clone, PartialEq, Serialize)]
pub struct GpuPipelineStats {
    pub pinned_host_buffers: bool,
    pub double_buffered: bool,
    pub stream_count: usize,
    pub batch_count: usize,
    pub overlapped_batches: usize,
    pub cuda_graph_captures: usize,
    pub cuda_graph_replays: usize,
    pub pattern_count: usize,
    pub pattern_batch_count: usize,
    pub single_dispatch: bool,
    pub short_line_count: usize,
    pub medium_line_count: usize,
    pub long_line_count: usize,
    pub warp_dispatch_count: usize,
    pub block_dispatch_count: usize,
    pub cpu_staging_bytes: usize,
    pub pageable_host_staging_bytes: usize,
    pub host_file_read_time_ms: f64,
    pub host_preprocess_time_ms: f64,
    pub host_to_pinned_copy_time_ms: f64,
    pub cpu_staging_time_ms: f64,
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
            cuda_graph_captures: 0,
            cuda_graph_replays: 0,
            pattern_count: 0,
            pattern_batch_count: 0,
            single_dispatch: false,
            short_line_count: 0,
            medium_line_count: 0,
            long_line_count: 0,
            warp_dispatch_count: 0,
            block_dispatch_count: 0,
            cpu_staging_bytes: 0,
            pageable_host_staging_bytes: 0,
            host_file_read_time_ms: 0.0,
            host_preprocess_time_ms: 0.0,
            host_to_pinned_copy_time_ms: 0.0,
            cpu_staging_time_ms: 0.0,
            transfer_time_ms: 0.0,
            kernel_time_ms: 0.0,
            wall_time_ms: 0.0,
            transfer_throughput_bytes_s: 0.0,
        }
    }
}

#[derive(Debug, Clone, PartialEq, Serialize)]
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

#[derive(Debug, Clone, PartialEq, Serialize)]
pub struct GpuNativeDeviceStats {
    pub device: CudaDeviceInfo,
    pub searched_files: usize,
    pub matched_files: usize,
    pub total_matches: usize,
    pub transfer_bytes: usize,
    pub pipeline: GpuPipelineStats,
}

#[derive(Debug, Clone, PartialEq, Serialize)]
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

#[derive(Debug, Clone, PartialEq, Serialize)]
pub struct GpuCudaGraphBenchmark {
    pub baseline: GpuNativeSearchStats,
    pub graphed: GpuNativeSearchStats,
    pub results_identical: bool,
    pub wall_time_reduction_pct: f64,
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
    line_descriptors: Vec<LineDescriptor>,
}

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
struct LineDescriptor {
    start: u32,
    len: u32,
    line_number: usize,
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
    cpu_staging_bytes: usize,
    pageable_host_staging_bytes: usize,
    host_file_read_time_ms: f64,
    host_preprocess_time_ms: f64,
    host_to_pinned_copy_time_ms: f64,
}

struct DevicePatternBatch {
    host: Arc<PatternBatchPlan>,
    pattern_blob_device: CudaSlice<u8>,
    pattern_offsets_device: CudaSlice<u32>,
    pattern_lengths_device: CudaSlice<u32>,
}

#[derive(Debug, Clone, Default)]
struct AdaptiveDispatchStats {
    short_line_count: usize,
    medium_line_count: usize,
    long_line_count: usize,
    warp_dispatch_count: usize,
    block_dispatch_count: usize,
}

#[derive(Debug, Clone)]
struct SlotAdaptiveDispatch {
    stats: AdaptiveDispatchStats,
}

impl SlotAdaptiveDispatch {
    fn total_lines(&self) -> usize {
        self.stats.short_line_count + self.stats.medium_line_count + self.stats.long_line_count
    }
}

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
enum SearchLaunchMode {
    Standard,
    GraphCapture,
    GraphReplay,
}

#[derive(Debug, Clone, Copy, PartialEq, Eq, Default)]
struct SearchExecutionOptions {
    // Default-off in production (`Default` => false). Exercised on demand by the
    // `tg gpu-cuda-graphs` benchmark path (benchmark_cuda_graph_search_paths runs
    // both false and true); pending promotion to a production default once validated.
    use_cuda_graphs: bool,
}

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
struct GraphCaptureSignature {
    bytes_used: usize,
    short_line_count: usize,
    medium_line_count: usize,
    long_line_count: usize,
    pattern_batch_index: usize,
}

struct GraphCaptureState {
    signature: GraphCaptureSignature,
    graph: CapturedCudaGraph,
    match_count_host: PinnedHostSlice<u32>,
}

struct CapturedCudaGraph {
    graph: sys::CUgraph,
    graph_exec: sys::CUgraphExec,
    stream: Arc<CudaStream>,
}

impl CapturedCudaGraph {
    fn from_captured_stream(stream: &Arc<CudaStream>) -> Result<Option<Self>> {
        let graph = unsafe { cuda_result::stream::end_capture(stream.cu_stream()) }
            .map_err(anyhow::Error::new)
            .context("failed to end CUDA graph capture for GPU search pipeline")?;
        if graph.is_null() {
            return Ok(None);
        }

        let mut graph_exec = std::mem::MaybeUninit::uninit();
        unsafe { sys::cuGraphInstantiateWithFlags(graph_exec.as_mut_ptr(), graph, 0) }
            .result()
            .map_err(anyhow::Error::new)
            .context("failed to instantiate CUDA graph for GPU search pipeline")?;

        Ok(Some(Self {
            graph,
            graph_exec: unsafe { graph_exec.assume_init() },
            stream: Arc::clone(stream),
        }))
    }

    fn launch(&self) -> Result<()> {
        unsafe { cuda_result::graph::launch(self.graph_exec, self.stream.cu_stream()) }
            .map_err(anyhow::Error::new)
            .context("failed to launch CUDA graph for GPU search pipeline")
    }
}

impl Drop for CapturedCudaGraph {
    fn drop(&mut self) {
        let context = self.stream.context();
        context.record_err(context.bind_to_thread());
        if !self.graph_exec.is_null() {
            context.record_err(unsafe { cuda_result::graph::exec_destroy(self.graph_exec) });
            self.graph_exec = std::ptr::null_mut();
        }
        if !self.graph.is_null() {
            context.record_err(unsafe { cuda_result::graph::destroy(self.graph) });
            self.graph = std::ptr::null_mut();
        }
    }
}

struct EventTrackingModeGuard {
    context: Arc<CudaContext>,
    restore_enabled: bool,
}

impl EventTrackingModeGuard {
    fn disable(context: &Arc<CudaContext>) -> Self {
        let restore_enabled = context.is_event_tracking();
        if restore_enabled {
            // SAFETY: We only disable cudarc's event-tracking shim around CUDA graph
            // capture/replay submission where we manually synchronize the relevant slot
            // stream and avoid sharing the captured buffers across streams.
            unsafe {
                context.disable_event_tracking();
            }
        }
        Self {
            context: Arc::clone(context),
            restore_enabled,
        }
    }
}

impl Drop for EventTrackingModeGuard {
    fn drop(&mut self) {
        if self.restore_enabled {
            // SAFETY: Restoring cudarc's default event-tracking mode is safe once graph
            // submission setup is complete and preserves the context's prior behavior.
            unsafe {
                self.context.enable_event_tracking();
            }
        }
    }
}

struct ReusableLineClassBuffers {
    line_starts_host: Vec<u32>,
    line_lengths_host: Vec<u32>,
    line_starts_device: CudaSlice<u32>,
    line_lengths_device: CudaSlice<u32>,
    len: usize,
}

#[derive(Debug, Clone)]
struct SearchDispatchPlan {
    file_batch: FileBatchPlan,
    pattern_batch_index: usize,
}

#[derive(Debug, Clone, PartialEq, Eq, Serialize)]
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
    match_buffer_capacity: usize,
    match_count_buffer: CudaSlice<u32>,
    pattern_batches: Vec<DevicePatternBatch>,
    current_batch: Option<LoadedFileBatch>,
    adaptive_dispatch: Option<SlotAdaptiveDispatch>,
    short_line_buffers: Option<ReusableLineClassBuffers>,
    medium_line_buffers: Option<ReusableLineClassBuffers>,
    long_line_buffers: Option<ReusableLineClassBuffers>,
    current_pattern_batch_index: Option<usize>,
    current_launch_mode: Option<SearchLaunchMode>,
    transfer_start: Option<CudaEvent>,
    transfer_end: Option<CudaEvent>,
    kernel_end: Option<CudaEvent>,
    cuda_graph: Option<GraphCaptureState>,
    graph_launch_started_at: Option<Instant>,
}

struct TransferBenchmarkSlot {
    stream: Arc<CudaStream>,
    host_buffer: RawPinnedHostBuffer,
    device_buffer: CudaSlice<u8>,
    transfer_start: Option<CudaEvent>,
    transfer_end: Option<CudaEvent>,
    pending_transfer: bool,
}

struct PageableTransferBenchmarkSlot {
    stream: Arc<CudaStream>,
    host_buffer: Vec<u8>,
    device_buffer: CudaSlice<u8>,
    transfer_start: Option<CudaEvent>,
    transfer_end: Option<CudaEvent>,
    pending_transfer: bool,
}

struct RawPinnedHostBuffer {
    inner: PinnedHostSlice<u8>,
    ptr: *mut u8,
    len: usize,
}

impl RawPinnedHostBuffer {
    fn new(context: &Arc<CudaContext>, len: usize) -> Result<Self> {
        let mut inner = catch_cuda("allocate CUDA pinned host buffer", || unsafe {
            context
                .alloc_pinned::<u8>(len.max(1))
                .map_err(anyhow::Error::new)
        })?;
        let ptr = inner
            .as_mut_ptr()
            .map_err(anyhow::Error::new)
            .context("failed to access CUDA pinned host buffer pointer")?;
        let len = inner.len();

        Ok(Self { inner, ptr, len })
    }

    fn as_slice(&self) -> Result<&[u8]> {
        let _keep_alive = &self.inner;
        Ok(unsafe { std::slice::from_raw_parts(self.ptr.cast_const(), self.len) })
    }

    fn as_mut_slice(&mut self) -> Result<&mut [u8]> {
        let _keep_alive = &self.inner;
        Ok(unsafe { std::slice::from_raw_parts_mut(self.ptr, self.len) })
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

struct PositionGraphCaptureState {
    signature: GraphCaptureSignature,
    graph: CapturedCudaGraph,
    match_count_host: PinnedHostSlice<u32>,
    match_positions_host: PinnedHostSlice<u32>,
    match_pattern_ids_host: PinnedHostSlice<u32>,
}

fn run_cuda_graph_benchmark_search_paths(
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

    let files = collect_search_files(config)?;
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
            .then(left.text.cmp(&right.text))
    });
    let mut seen = BTreeSet::new();
    matches.retain(|matched| {
        seen.insert((
            matched.path.clone(),
            matched.line_number,
            matched.text.clone(),
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
    })
}

fn load_file_batch_into_host_buffer(
    host_buffer: &mut RawPinnedHostBuffer,
    plan: &FileBatchPlan,
) -> Result<LoadedFileBatch> {
    load_file_batch_into_pinned_slice(host_buffer.as_mut_slice()?, plan, false)
}

fn load_file_batch_into_pinned_slice(
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
fn capture_position_graph_search(
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

fn gpu_native_search_paths_multi_with_options(
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

    let files = collect_search_files(config)?;
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
    })
}

fn run_device_assignment(
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

fn run_device_search(
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

fn create_search_pipeline_slot(
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

fn create_pageable_transfer_benchmark_slot(
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

fn finalize_pageable_transfer_benchmark_slot(
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
fn finalize_search_pipeline_slot(
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

fn load_file_batch_into_slot(slot: &mut SearchPipelineSlot, plan: &FileBatchPlan) -> Result<()> {
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

fn launch_slot_search(
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
fn launch_line_search_kernel(
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
fn launch_warp_line_search_kernel(
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

fn prepare_adaptive_dispatch(
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

fn prepare_line_class_buffers(
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

fn launch_slot_search_standard(
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

fn capture_slot_search_graph(
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

fn launch_slot_search_graph(
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

fn ensure_slot_match_capacity(slot: &mut SearchPipelineSlot, required: usize) -> Result<()> {
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

fn launch_slot_search_kernels(
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

fn classify_file_lines(
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

fn push_classified_line(
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

fn copy_pageable_host_to_device(
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

fn copy_host_slice_to_device_during_capture(
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

fn copy_device_match_count_to_host_slice_during_capture(
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

fn copy_device_u32_slice_to_host_during_capture(
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

fn resolve_max_batch_bytes(config: &GpuNativeSearchConfig) -> usize {
    config
        .max_batch_bytes
        .unwrap_or(DEFAULT_GPU_BATCH_BYTES)
        .max(1)
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

fn resolve_adaptive_match_capacity(line_count: usize, pattern_count: usize) -> Result<usize> {
    line_count
        .checked_mul(pattern_count.max(1))
        .map(|capacity| capacity.max(1))
        .context("GPU native adaptive line match capacity overflow")
}

fn plan_pattern_batches(patterns: &[String]) -> Result<Vec<PatternBatchPlan>> {
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

fn resolve_pattern_shared_mem_bytes(
    pattern_count: usize,
    pattern_blob_len: usize,
) -> Result<usize> {
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

fn upload_pattern_batches_on_stream(
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

fn file_estimated_bytes(path: &Path) -> Result<usize> {
    usize::try_from(
        fs::metadata(path)
            .with_context(|| format!("failed to stat GPU native search file {}", path.display()))?
            .len(),
    )
    .with_context(|| format!("GPU native search file is too large: {}", path.display()))
}

fn resolve_cuda_devices(device_ids: &[i32]) -> Result<Vec<CudaDeviceInfo>> {
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

fn cuda_device_count() -> Result<i32> {
    catch_cuda("initialize CUDA driver", || {
        CudaContext::device_count().map_err(anyhow::Error::new)
    })
}

fn device_info_for_ordinal(device_id: i32) -> Result<CudaDeviceInfo> {
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

fn validate_requested_cuda_device_ids(device_ids: &[i32], device_count: i32) -> Result<Vec<i32>> {
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
    assignment.assigned_bytes = assignment
        .assigned_bytes
        .saturating_add(entry.estimated_bytes);
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
fn check_gpu_native_implicit_walk_ceiling(
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

fn collect_search_files(config: &GpuNativeSearchConfig) -> Result<Vec<PathBuf>> {
    let mut files = Vec::new();
    let mut roots = Vec::new();

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
        files.extend(collect_walked_files(config, &roots)?);
    }

    files.sort_unstable();
    files.dedup();
    Ok(files)
}

fn collect_walked_files(config: &GpuNativeSearchConfig, roots: &[PathBuf]) -> Result<Vec<PathBuf>> {
    if let Some(refusal) = check_gpu_native_implicit_walk_ceiling(config, roots) {
        return Err(anyhow!(refusal));
    }
    let builder = build_walk_builder(config, roots)?;
    let walked_files = Arc::new(std::sync::Mutex::new(Vec::new()));
    let shared_files = Arc::clone(&walked_files);

    builder.build_parallel().run(|| {
        let shared_files = Arc::clone(&shared_files);
        Box::new(move |entry| {
            if let Ok(entry) = entry {
                if entry
                    .file_type()
                    .map(|kind| kind.is_file())
                    .unwrap_or(false)
                {
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
            file,
            file_bytes,
            &file_offsets,
            all_patterns,
        ));
    }

    Ok(matches)
}

fn line_matches_for_file(
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
        let text = String::from_utf8_lossy(line_bytes)
            .trim_end_matches('\r')
            .to_string();
        matches.push(GpuNativeSearchMatch {
            path: file.path.clone(),
            line_number: line.line_number,
            text,
            pattern_id: offset.pattern_id,
            pattern_text: all_patterns[offset.pattern_id].clone(),
        });
    }

    matches
}

fn line_matches_for_file_by_scanning(
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

struct CachedKernelPtx {
    ptx: Ptx,
    cache_path: PathBuf,
    from_cache: bool,
}

fn compile_kernel_module(context: &Arc<CudaContext>, device_id: i32) -> Result<Arc<CudaModule>> {
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

fn load_or_compile_search_kernel_ptx(
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

fn compile_search_kernel_ptx(
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

fn write_cached_search_kernel_ptx(path: &Path, bytes: &[u8]) {
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

fn cached_search_kernel_ptx_path(
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

fn cuda_ptx_cache_dir() -> PathBuf {
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

fn kernel_compile_options_for_compute_capability(
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

fn nvrtc_architecture_option_for_compute_capability(
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

fn format_available_device_ids_from_count(device_count: i32) -> String {
    if device_count <= 0 {
        return "none".to_string();
    }
    (0..device_count)
        .map(|device_id| device_id.to_string())
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

#[cfg(test)]
mod tests {
    use super::{
        cached_search_kernel_ptx_path, check_gpu_native_implicit_walk_ceiling,
        collect_search_files, kernel_compile_options_for_compute_capability, line_matches_for_file,
        load_file_batch_into_pinned_slice, resolve_adaptive_match_capacity,
        resolve_max_match_capacity, validate_requested_cuda_device_ids, BatchedFile, FileBatchPlan,
        GpuNativeSearchConfig, LineDescriptor, PatternMatchPosition,
    };
    use std::fs;
    use std::path::{Path, PathBuf};

    // --- Audit #109: GPU-native implicit-walk-ceiling gate --------------------------------
    // Mirrors native_search.rs's audit #105 test suite for `check_native_implicit_walk_ceiling`.
    // #105 hoisted a walk-ceiling gate into the native-CPU engine but left the GPU-native engine
    // (this module) with NO ceiling at all -- `GpuNativeSearchConfig` did not even have a
    // `path_was_implicit` field, so a bare implicit-path `--gpu-device-ids` search on a huge root
    // walked unbounded through `collect_walked_files`.

    fn make_stub_file_dir(dir: &Path, file_count: usize) {
        for index in 0..file_count {
            fs::write(
                dir.join(format!("stub_{index}.py")),
                "nothing interesting\n",
            )
            .unwrap();
        }
    }

    fn config_with_paths(paths: Vec<PathBuf>, path_was_implicit: bool) -> GpuNativeSearchConfig {
        GpuNativeSearchConfig {
            patterns: vec!["TODO".to_string()],
            paths,
            path_was_implicit,
            ..GpuNativeSearchConfig::default()
        }
    }

    #[test]
    fn check_gpu_native_implicit_walk_ceiling_refuses_oversized_implicit_walk() {
        // RED-before-fix: this is the exact shape of the #109 bypass -- an implicit-path search
        // (no explicit PATH positional) on a root over the 1500-file ceiling.
        let dir = tempfile::tempdir().unwrap();
        make_stub_file_dir(dir.path(), 1600);
        let roots = vec![dir.path().to_path_buf()];
        let config = config_with_paths(roots.clone(), true);

        let refusal = check_gpu_native_implicit_walk_ceiling(&config, &roots);

        assert!(
            refusal.is_some(),
            "an oversized implicit-path walk must be refused"
        );
    }

    #[test]
    fn check_gpu_native_implicit_walk_ceiling_allows_explicit_path_even_when_oversized() {
        // Non-regression (Trap #3 parity, mirrors native_search.rs/rg_passthrough.rs): an
        // EXPLICIT, deliberately-scoped PATH must never be refused regardless of size.
        let dir = tempfile::tempdir().unwrap();
        make_stub_file_dir(dir.path(), 1600);
        let roots = vec![dir.path().to_path_buf()];
        let config = config_with_paths(roots.clone(), false);

        let refusal = check_gpu_native_implicit_walk_ceiling(&config, &roots);

        assert!(
            refusal.is_none(),
            "an explicit path must run uninhibited even when the walk exceeds the ceiling"
        );
    }

    #[test]
    fn check_gpu_native_implicit_walk_ceiling_allows_implicit_path_under_ceiling() {
        // Normal-case non-regression: an implicit path under the ceiling is unaffected -- a
        // typical repo must never be refused.
        let dir = tempfile::tempdir().unwrap();
        make_stub_file_dir(dir.path(), 50);
        let roots = vec![dir.path().to_path_buf()];
        let config = config_with_paths(roots.clone(), true);

        let refusal = check_gpu_native_implicit_walk_ceiling(&config, &roots);

        assert!(
            refusal.is_none(),
            "a 50-file implicit root must not be refused"
        );
    }

    #[test]
    fn collect_search_files_refuses_oversized_implicit_walk_before_enumerating() {
        // Hermetic end-to-end test of the actual `collect_search_files` production entry point
        // `gpu_native_search_paths_multi_with_options` calls before ever touching a CUDA device
        // (see that function: `collect_search_files(config)?` runs BEFORE `resolve_cuda_devices`),
        // so this test exercises the real bug with no GPU/CUDA runtime involved. Bounded per
        // anti-hang-test-protocol: run on a joined worker thread with an explicit timeout so a
        // regression (the gate silently stops firing) that falls through to the unbounded
        // parallel walk cannot hang the test runner -- it fails fast with a clear panic instead.
        let dir = tempfile::tempdir().unwrap();
        make_stub_file_dir(dir.path(), 1600);
        let config = config_with_paths(vec![dir.path().to_path_buf()], true);

        let (tx, rx) = std::sync::mpsc::channel();
        std::thread::spawn(move || {
            let result = collect_search_files(&config).map_err(|error| error.to_string());
            let _ = tx.send(result);
        });
        let result = rx.recv_timeout(std::time::Duration::from_secs(10)).expect(
            "collect_search_files must return well within 10s -- a hang here means the \
             walk-ceiling gate did not fire before an unbounded parallel walk",
        );

        let err = result.expect_err("an oversized implicit-path walk must be refused, not Ok");
        assert!(
            crate::rg_passthrough::is_unbounded_implicit_search_walk_refusal(&err),
            "unexpected error (expected the walk-ceiling refusal): {err}"
        );
    }

    #[test]
    fn collect_search_files_does_not_refuse_explicit_oversized_path() {
        // Non-regression: an explicit PATH (even oversized) must complete normally, not be
        // refused -- fail-open for explicit scoping is the whole point of the guard (Trap #3
        // parity). Bounded per anti-hang-test-protocol.
        let dir = tempfile::tempdir().unwrap();
        make_stub_file_dir(dir.path(), 1600);
        let config = config_with_paths(vec![dir.path().to_path_buf()], false);

        let (tx, rx) = std::sync::mpsc::channel();
        std::thread::spawn(move || {
            let result = collect_search_files(&config).map_err(|error| error.to_string());
            let _ = tx.send(result);
        });
        let result = rx
            .recv_timeout(std::time::Duration::from_secs(20))
            .expect("collect_search_files must return well within 20s for an explicit path");

        result.expect("an explicit oversized path must not be refused");
    }

    #[test]
    fn validate_requested_cuda_device_ids_preserves_selected_subset_without_expanding() {
        let selected = validate_requested_cuda_device_ids(&[0, 0], 2).unwrap();

        assert_eq!(selected, vec![0]);
    }

    #[test]
    fn validate_requested_cuda_device_ids_rejects_out_of_range_with_available_ids() {
        let err = validate_requested_cuda_device_ids(&[3], 2).unwrap_err();
        let message = err.to_string();

        assert!(message.contains("invalid CUDA device id 3"));
        assert!(message.contains("available CUDA devices: 0, 1"));
    }

    #[test]
    fn adaptive_line_match_capacity_scales_with_lines_not_batch_bytes() {
        let five_gb_shard_bytes = 671_088_644usize;
        let generated_line_count = 182_324usize;

        let byte_capacity = resolve_max_match_capacity(five_gb_shard_bytes, 1).unwrap();
        let adaptive_capacity = resolve_adaptive_match_capacity(generated_line_count, 1).unwrap();

        assert_eq!(adaptive_capacity, generated_line_count);
        assert!(byte_capacity > adaptive_capacity * 1_000);
    }

    #[test]
    fn kernel_compile_options_include_selected_compute_capability_architecture() {
        let options = kernel_compile_options_for_compute_capability((12, 0)).unwrap();

        assert_eq!(options.name.as_deref(), Some("gpu_native_search.cu"));
        assert!(options
            .options
            .contains(&"--gpu-architecture=compute_120".to_string()));
    }

    #[test]
    fn kernel_compile_options_reject_invalid_capability_without_downgrade() {
        let err = kernel_compile_options_for_compute_capability((0, 0)).unwrap_err();
        let message = err.to_string();

        assert!(message.contains("invalid CUDA compute capability 0.0"));
        assert!(message.contains("not falling back"));
    }

    #[test]
    fn file_batch_loading_preprocesses_pageable_memory_before_pinned_copy() {
        let temp = tempfile::tempdir().unwrap();
        let text_path = temp.path().join("text.log");
        let binary_path = temp.path().join("binary.log");
        fs::write(&text_path, b"INFO start\nERROR target\n").unwrap();
        fs::write(&binary_path, b"INFO\0binary\nERROR ignored\n").unwrap();
        let text_len = fs::metadata(&text_path).unwrap().len() as usize;
        let binary_len = fs::metadata(&binary_path).unwrap().len() as usize;
        let plan = FileBatchPlan {
            files: vec![text_path.clone(), binary_path],
            estimated_bytes: text_len + binary_len + 1,
        };
        let mut host = vec![0u8; plan.estimated_bytes + 16];

        let loaded = load_file_batch_into_pinned_slice(&mut host, &plan, true).unwrap();

        assert_eq!(loaded.files.len(), 1);
        assert_eq!(loaded.files[0].path, text_path);
        assert_eq!(loaded.cpu_staging_bytes, text_len);
        assert_eq!(loaded.pageable_host_staging_bytes, text_len + binary_len);
        assert!(loaded.host_to_pinned_copy_time_ms >= 0.0);
        assert_eq!(loaded.classified_lines.short_line_count(), 2);
        assert_eq!(&host[..text_len], b"INFO start\nERROR target\n");
    }

    #[test]
    fn ptx_cache_path_is_scoped_by_architecture_and_kernel_hash() {
        let cache_path =
            cached_search_kernel_ptx_path((12, 0), &["--gpu-architecture=compute_120".to_string()])
                .unwrap();
        let cache_file = cache_path
            .file_name()
            .and_then(|name| name.to_str())
            .unwrap();

        assert!(cache_file.starts_with("gpu_native_search_compute_120_"));
        assert!(cache_file.ends_with(".ptx"));
        assert!(cache_file.len() > "gpu_native_search_compute_120_.ptx".len());
    }

    #[test]
    fn line_match_materialization_uses_loaded_line_descriptors() {
        let path = PathBuf::from("sample.log");
        let bytes = b"INFO start\nERROR target\n";
        let file = BatchedFile {
            path: path.clone(),
            start: 100,
            end: 100 + bytes.len(),
            line_descriptors: vec![
                LineDescriptor {
                    start: 100,
                    len: 10,
                    line_number: 1,
                },
                LineDescriptor {
                    start: 111,
                    len: 12,
                    line_number: 2,
                },
            ],
        };
        let offsets = vec![PatternMatchPosition {
            byte_offset: 11,
            pattern_id: 0,
        }];
        let patterns = vec!["ERROR".to_string()];

        let matches = line_matches_for_file(&file, bytes, &offsets, &patterns);

        assert_eq!(matches.len(), 1);
        assert_eq!(matches[0].path, path);
        assert_eq!(matches[0].line_number, 2);
        assert_eq!(matches[0].text, "ERROR target");
    }

    #[test]
    fn line_match_materialization_preserves_line_numbers_after_blank_lines() {
        let path = PathBuf::from("blank-lines.log");
        let bytes = b"INFO start\n\nERROR target\n";
        let file = BatchedFile {
            path: path.clone(),
            start: 100,
            end: 100 + bytes.len(),
            line_descriptors: vec![
                LineDescriptor {
                    start: 100,
                    len: 10,
                    line_number: 1,
                },
                LineDescriptor {
                    start: 112,
                    len: 12,
                    line_number: 3,
                },
            ],
        };
        let offsets = vec![PatternMatchPosition {
            byte_offset: 12,
            pattern_id: 0,
        }];
        let patterns = vec!["ERROR".to_string()];

        let matches = line_matches_for_file(&file, bytes, &offsets, &patterns);

        assert_eq!(matches.len(), 1);
        assert_eq!(matches[0].path, path);
        assert_eq!(matches[0].line_number, 3);
        assert_eq!(matches[0].text, "ERROR target");
    }
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
