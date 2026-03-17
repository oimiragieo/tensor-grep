use anyhow::{anyhow, Context, Result};
use cudarc::driver::{
    result as cuda_result, sys, CudaContext, CudaEvent, CudaFunction, CudaModule, CudaSlice,
    CudaStream, DevicePtrMut, LaunchConfig, PinnedHostSlice, PushKernelArg,
};
use cudarc::nvrtc::{compile_ptx_with_opts, CompileOptions};
use ignore::{overrides::OverrideBuilder, WalkBuilder, WalkState};
use memchr::memchr_iter;
use std::env;
use std::fs;
use std::panic::{catch_unwind, AssertUnwindSafe};
use std::path::{Path, PathBuf};
use std::sync::Arc;
use std::sync::Once;
use std::time::Instant;

const SEARCH_KERNEL_NAME: &str = "gpu_text_search";
const SEARCH_KERNEL_SOURCE: &str = r#"
extern "C" __global__ void gpu_text_search(
    const unsigned char* text,
    int text_len,
    const unsigned char* pattern,
    int pattern_len,
    unsigned int* match_positions,
    unsigned int max_matches,
    unsigned int* match_count
) {
    unsigned int idx = blockIdx.x * blockDim.x + threadIdx.x;
    if (pattern_len <= 0 || text_len <= 0 || pattern_len > text_len) {
        return;
    }

    unsigned int last_start = (unsigned int)(text_len - pattern_len);
    if (idx > last_start) {
        return;
    }

    bool matched = true;
    for (int pattern_index = 0; pattern_index < pattern_len; ++pattern_index) {
        if (text[idx + pattern_index] != pattern[pattern_index]) {
            matched = false;
            break;
        }
    }

    if (matched) {
        unsigned int slot = atomicAdd(match_count, 1u);
        if (slot < max_matches) {
            match_positions[slot] = idx;
        }
    }
}
"#;
const KERNEL_THREADS_PER_BLOCK: u32 = 256;
const PIPELINE_SLOT_COUNT: usize = 2;
const DEFAULT_GPU_BATCH_BYTES: usize = 128 * 1024 * 1024;
static CUDA_LIBRARY_PATH_INIT: Once = Once::new();

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub struct MatchPosition {
    pub byte_offset: usize,
}

#[derive(Debug, Clone, Default)]
pub struct GpuNativeSearchConfig {
    pub pattern: String,
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
}

#[derive(Debug, Clone, PartialEq)]
pub struct GpuPipelineStats {
    pub pinned_host_buffers: bool,
    pub double_buffered: bool,
    pub stream_count: usize,
    pub batch_count: usize,
    pub overlapped_batches: usize,
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
pub struct GpuNativeSearchStats {
    pub searched_files: usize,
    pub matched_files: usize,
    pub total_matches: usize,
    pub transfer_bytes: usize,
    pub selected_device: CudaDeviceInfo,
    pub matches: Vec<GpuNativeSearchMatch>,
    pub pipeline: GpuPipelineStats,
}

#[derive(Debug, Clone)]
struct BatchedFile {
    path: PathBuf,
    start: usize,
    end: usize,
}

#[derive(Debug, Clone)]
struct FileBatchPlan {
    files: Vec<PathBuf>,
    estimated_bytes: usize,
}

#[derive(Debug, Clone)]
struct LoadedFileBatch {
    files: Vec<BatchedFile>,
    bytes_used: usize,
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
    function: CudaFunction,
}

struct SearchPipelineSlot {
    stream: Arc<CudaStream>,
    host_buffer: RawPinnedHostBuffer,
    data_buffer: CudaSlice<u8>,
    match_positions_buffer: CudaSlice<u32>,
    match_count_buffer: CudaSlice<u32>,
    current_batch: Option<LoadedFileBatch>,
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
    searched_files: usize,
    transfer_bytes: usize,
    matches: Vec<GpuNativeSearchMatch>,
    pipeline: GpuPipelineStats,
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
    if config.pattern.is_empty() {
        return Err(anyhow!("GPU native search requires a non-empty pattern"));
    }
    if config.paths.is_empty() {
        return Err(anyhow!("GPU native search requires at least one search path"));
    }

    let selected_device = resolve_cuda_device(device_id)?;
    let files = collect_search_files(config)?;
    let batch_plans = plan_file_batches(&files, resolve_max_batch_bytes(config))?;
    let slot_capacity = batch_plans
        .iter()
        .map(|plan| plan.estimated_bytes)
        .max()
        .unwrap_or(0)
        .max(1);

    let outcome = if batch_plans.is_empty() {
        SearchPipelineOutcome {
            searched_files: 0,
            transfer_bytes: 0,
            matches: Vec::new(),
            pipeline: GpuPipelineStats::default(),
        }
    } else {
        let runtime = create_kernel_runtime(device_id)?;
        run_search_pipeline(&runtime, &config.pattern, &batch_plans, slot_capacity)?
    };

    let matched_files = outcome
        .matches
        .iter()
        .map(|matched| matched.path.clone())
        .collect::<std::collections::BTreeSet<_>>()
        .len();

    Ok(GpuNativeSearchStats {
        searched_files: outcome.searched_files,
        matched_files,
        total_matches: outcome.matches.len(),
        transfer_bytes: outcome.transfer_bytes,
        selected_device,
        matches: outcome.matches,
        pipeline: outcome.pipeline,
    })
}

pub fn gpu_native_search(pattern: &str, data: &[u8], device_id: i32) -> Result<Vec<MatchPosition>> {
    if pattern.is_empty() {
        return Err(anyhow!("GPU native search requires a non-empty pattern"));
    }
    if data.is_empty() || pattern.len() > data.len() {
        return Ok(Vec::new());
    }

    let data_len = i32::try_from(data.len()).context("GPU native search input exceeds i32 length")?;
    let pattern_bytes = pattern.as_bytes();
    let pattern_len = i32::try_from(pattern_bytes.len())
        .context("GPU native search pattern exceeds i32 length")?;
    let num_positions = data
        .len()
        .checked_sub(pattern_bytes.len())
        .and_then(|value| value.checked_add(1))
        .context("failed to compute GPU search launch size")?;
    let max_matches = u32::try_from(num_positions)
        .context("GPU native search exceeds u32 match capacity")?;

    let runtime = create_kernel_runtime(device_id)?;
    let stream = runtime.context.default_stream();
    let data_device = stream
        .clone_htod(data)
        .map_err(anyhow::Error::new)
        .context("failed to copy search data to CUDA device")?;
    let pattern_device = stream
        .clone_htod(pattern_bytes)
        .map_err(anyhow::Error::new)
        .context("failed to copy pattern to CUDA device")?;
    let mut match_positions_device = stream
        .alloc_zeros::<u32>(num_positions)
        .map_err(anyhow::Error::new)
        .context("failed to allocate CUDA match position buffer")?;
    let mut match_count_device = stream
        .alloc_zeros::<u32>(1)
        .map_err(anyhow::Error::new)
        .context("failed to allocate CUDA match counter")?;

    launch_search_kernel(
        &stream,
        &runtime.function,
        &data_device,
        data_len,
        &pattern_device,
        pattern_len,
        &mut match_positions_device,
        max_matches,
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
        return Ok(Vec::new());
    }

    let match_positions_view = match_positions_device
        .try_slice(0..match_count)
        .context("failed to slice CUDA match positions buffer")?;
    let positions = stream
        .clone_dtoh(&match_positions_view)
        .map_err(anyhow::Error::new)
        .context("failed to copy CUDA match positions back to host")?;

    Ok(positions
        .into_iter()
        .map(|byte_offset| MatchPosition {
            byte_offset: byte_offset as usize,
        })
        .collect())
}

fn create_kernel_runtime(device_id: i32) -> Result<KernelRuntime> {
    validate_device_id(device_id)?;
    let context = open_cuda_context(device_id)?;
    let module = compile_kernel_module(&context, device_id)?;
    let function = module
        .load_function(SEARCH_KERNEL_NAME)
        .map_err(anyhow::Error::new)
        .with_context(|| {
            format!(
                "failed to load CUDA kernel `{SEARCH_KERNEL_NAME}` for device {device_id}"
            )
        })?;

    Ok(KernelRuntime {
        context,
        _module: module,
        function,
    })
}

fn run_search_pipeline(
    runtime: &KernelRuntime,
    pattern: &str,
    batch_plans: &[FileBatchPlan],
    slot_capacity: usize,
) -> Result<SearchPipelineOutcome> {
    let mut slots = (0..PIPELINE_SLOT_COUNT)
        .map(|_| create_search_pipeline_slot(runtime, slot_capacity))
        .collect::<Result<Vec<_>>>()?;
    let pattern_stream = runtime.context.default_stream();
    let pattern_device = pattern_stream
        .clone_htod(pattern.as_bytes())
        .map_err(anyhow::Error::new)
        .context("failed to copy pattern to CUDA device for streamed search")?;
    let pattern_len = i32::try_from(pattern.len())
        .context("GPU native search pattern exceeds i32 length")?;
    let started_at = Instant::now();

    let mut searched_files = 0usize;
    let mut transfer_bytes = 0usize;
    let mut matches = Vec::new();
    let mut transfer_time_ms = 0.0f32;
    let mut kernel_time_ms = 0.0f32;
    let mut overlapped_device_time_ms = 0.0f32;
    let active_stream_count = batch_plans.len().clamp(1, PIPELINE_SLOT_COUNT);
    let mut pipeline_start = None;

    for (batch_index, plan) in batch_plans.iter().enumerate() {
        let slot = &mut slots[batch_index % PIPELINE_SLOT_COUNT];
        finalize_search_pipeline_slot(
            slot,
            &mut searched_files,
            &mut transfer_bytes,
            &mut matches,
            &mut transfer_time_ms,
            &mut kernel_time_ms,
            &mut overlapped_device_time_ms,
        )?;
        load_file_batch_into_slot(slot, plan)?;
        if pipeline_start.is_none() {
            pipeline_start = Some(record_timed_event(&slot.stream)?);
        }
        launch_slot_search(
            slot,
            runtime,
            &pattern_device,
            pattern_len,
        )?;
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
            &mut searched_files,
            &mut transfer_bytes,
            &mut matches,
            &mut transfer_time_ms,
            &mut kernel_time_ms,
            &mut overlapped_device_time_ms,
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
        searched_files,
        transfer_bytes,
        matches,
        pipeline: GpuPipelineStats {
            pinned_host_buffers: true,
            double_buffered: active_stream_count >= 2,
            stream_count: active_stream_count,
            batch_count: batch_plans.len(),
            overlapped_batches: batch_plans.len().saturating_sub(active_stream_count),
            transfer_time_ms,
            kernel_time_ms,
            wall_time_ms,
            transfer_throughput_bytes_s,
        },
    })
}

fn create_search_pipeline_slot(runtime: &KernelRuntime, capacity: usize) -> Result<SearchPipelineSlot> {
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
    let match_positions_buffer = unsafe { stream.alloc::<u32>(capacity) }
        .map_err(anyhow::Error::new)
        .context("failed to allocate device match position buffer for GPU search pipeline")?;
    let match_count_buffer = stream
        .alloc_zeros::<u32>(1)
        .map_err(anyhow::Error::new)
        .context("failed to allocate device match counter for GPU search pipeline")?;

    Ok(SearchPipelineSlot {
        stream,
        host_buffer,
        data_buffer,
        match_positions_buffer,
        match_count_buffer,
        current_batch: None,
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

fn finalize_search_pipeline_slot(
    slot: &mut SearchPipelineSlot,
    searched_files: &mut usize,
    transfer_bytes: &mut usize,
    matches: &mut Vec<GpuNativeSearchMatch>,
    transfer_time_ms: &mut f32,
    kernel_time_ms: &mut f32,
    overlapped_device_time_ms: &mut f32,
) -> Result<()> {
    let Some(batch) = slot.current_batch.take() else {
        slot.transfer_start = None;
        slot.transfer_end = None;
        slot.kernel_end = None;
        return Ok(());
    };

    *searched_files += batch.files.len();
    *transfer_bytes += batch.bytes_used;
    if batch.bytes_used == 0 {
        slot.transfer_start = None;
        slot.transfer_end = None;
        slot.kernel_end = None;
        return Ok(());
    }

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

    let match_positions_view = slot
        .match_positions_buffer
        .try_slice(0..match_count)
        .context("failed to slice GPU search pipeline match positions buffer")?;
    let positions = slot
        .stream
        .clone_dtoh(&match_positions_view)
        .map_err(anyhow::Error::new)
        .context("failed to copy GPU search pipeline match positions back to host")?;
    let mut positions = positions;
    positions.sort_unstable();
    let batch_matches = convert_offsets_to_line_matches(
        &slot.host_buffer.as_slice()?[..batch.bytes_used],
        &batch.files,
        &positions,
    )?;
    matches.extend(batch_matches);
    Ok(())
}

fn load_file_batch_into_slot(slot: &mut SearchPipelineSlot, plan: &FileBatchPlan) -> Result<()> {
    let host_buffer = slot.host_buffer.as_mut_slice()?;
    let mut cursor = 0usize;
    let mut batch_files = Vec::new();

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
    }

    slot.current_batch = Some(LoadedFileBatch {
        files: batch_files,
        bytes_used: cursor,
    });
    slot.transfer_start = None;
    slot.transfer_end = None;
    slot.kernel_end = None;
    Ok(())
}

fn launch_slot_search(
    slot: &mut SearchPipelineSlot,
    runtime: &KernelRuntime,
    pattern_device: &CudaSlice<u8>,
    pattern_len: i32,
) -> Result<()> {
    let Some(batch) = slot.current_batch.as_ref() else {
        return Ok(());
    };
    if batch.bytes_used == 0 {
        return Ok(());
    }

    let data_len = i32::try_from(batch.bytes_used).context("GPU search batch exceeds i32 length")?;
    let max_matches = u32::try_from(batch.bytes_used)
        .context("GPU search batch exceeds u32 match capacity")?;

    slot.stream
        .memset_zeros(&mut slot.match_count_buffer)
        .map_err(anyhow::Error::new)
        .context("failed to reset GPU search pipeline match counter")?;
    slot.transfer_start = Some(record_timed_event(&slot.stream)?);
    copy_pinned_host_to_device(&slot.stream, &slot.host_buffer, batch.bytes_used, &mut slot.data_buffer)
        .context("failed to copy pinned search batch to CUDA device")?;
    slot.transfer_end = Some(record_timed_event(&slot.stream)?);
    launch_search_kernel(
        &slot.stream,
        &runtime.function,
        &slot.data_buffer,
        data_len,
        pattern_device,
        pattern_len,
        &mut slot.match_positions_buffer,
        max_matches,
        &mut slot.match_count_buffer,
    )?;
    slot.kernel_end = Some(record_timed_event(&slot.stream)?);
    Ok(())
}

#[allow(clippy::too_many_arguments)]
fn launch_search_kernel(
    stream: &Arc<CudaStream>,
    function: &CudaFunction,
    data_device: &CudaSlice<u8>,
    data_len: i32,
    pattern_device: &CudaSlice<u8>,
    pattern_len: i32,
    match_positions_device: &mut CudaSlice<u32>,
    max_matches: u32,
    match_count_device: &mut CudaSlice<u32>,
) -> Result<()> {
    let launch_config = LaunchConfig {
        grid_dim: (
            max_matches.div_ceil(KERNEL_THREADS_PER_BLOCK),
            1,
            1,
        ),
        block_dim: (KERNEL_THREADS_PER_BLOCK, 1, 1),
        shared_mem_bytes: 0,
    };

    catch_cuda("launch GPU substring search kernel", || {
        let mut launch = stream.launch_builder(function);
        launch.arg(data_device);
        launch.arg(&data_len);
        launch.arg(pattern_device);
        launch.arg(&pattern_len);
        launch.arg(match_positions_device);
        launch.arg(&max_matches);
        launch.arg(match_count_device);
        unsafe { launch.launch(launch_config) }
            .map(|_| ())
            .map_err(anyhow::Error::new)
    })
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

fn resolve_cuda_device(device_id: i32) -> Result<CudaDeviceInfo> {
    let devices = enumerate_cuda_devices()?;
    devices
        .iter()
        .find(|device| device.device_id == device_id)
        .cloned()
        .ok_or_else(|| {
            anyhow!(
                "invalid CUDA device id {device_id}; available CUDA devices: {}",
                format_available_devices(&devices)
            )
        })
}

fn resolve_max_batch_bytes(config: &GpuNativeSearchConfig) -> usize {
    config.max_batch_bytes.unwrap_or(DEFAULT_GPU_BATCH_BYTES).max(1)
}

fn plan_file_batches(files: &[PathBuf], max_batch_bytes: usize) -> Result<Vec<FileBatchPlan>> {
    let mut batches = Vec::new();
    let mut current_files = Vec::new();
    let mut current_bytes = 0usize;

    for path in files {
        let estimated_bytes = usize::try_from(
            fs::metadata(path)
                .with_context(|| format!("failed to stat GPU native search file {}", path.display()))?
                .len(),
        )
        .with_context(|| format!("GPU native search file is too large: {}", path.display()))?;
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
    offsets: &[u32],
) -> Result<Vec<GpuNativeSearchMatch>> {
    let mut grouped_offsets = vec![Vec::new(); batch.len()];
    let mut batch_index = 0usize;

    for &matched in offsets {
        let matched = matched as usize;
        while batch_index < batch.len() && matched >= batch[batch_index].end {
            batch_index += 1;
        }
        if batch_index >= batch.len() {
            break;
        }

        let file = &batch[batch_index];
        if matched < file.start || matched >= file.end {
            continue;
        }

        grouped_offsets[batch_index].push(matched - file.start);
    }

    let mut matches = Vec::new();
    for (file, file_offsets) in batch.iter().zip(grouped_offsets.into_iter()) {
        if file_offsets.is_empty() {
            continue;
        }
        let file_bytes = &buffer[file.start..file.end];
        matches.extend(line_matches_for_file(&file.path, file_bytes, &file_offsets));
    }

    Ok(matches)
}

fn line_matches_for_file(path: &Path, file_bytes: &[u8], offsets: &[usize]) -> Vec<GpuNativeSearchMatch> {
    let newline_positions = memchr_iter(b'\n', file_bytes).collect::<Vec<_>>();
    let mut matches = Vec::new();
    let mut last_line_start = None;

    for &offset in offsets {
        let newline_index = newline_positions.partition_point(|position| *position < offset);
        let line_start = newline_index
            .checked_sub(1)
            .and_then(|index| newline_positions.get(index).copied())
            .map(|index| index + 1)
            .unwrap_or(0);
        if last_line_start == Some(line_start) {
            continue;
        }
        last_line_start = Some(line_start);
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
    let devices = enumerate_cuda_devices()?;
    if devices.is_empty() {
        return Err(anyhow!(
            "CUDA is unavailable: no CUDA devices were detected by cudarc"
        ));
    }
    if devices.iter().any(|device| device.device_id == device_id) {
        return Ok(());
    }

    Err(anyhow!(
        "invalid CUDA device id {device_id}; available CUDA devices: {}",
        format_available_devices(&devices)
    ))
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
