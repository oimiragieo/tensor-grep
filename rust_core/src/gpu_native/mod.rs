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

mod crossover;
mod device;
mod kernels;
mod memory;
use crossover::*;
use device::*;
pub use device::{
    benchmark_cuda_graph_search_paths, benchmark_pageable_transfer_throughput,
    benchmark_pinned_transfer_throughput, compile_search_kernel, detect_compute_capability,
    enumerate_cuda_devices, gpu_native_search_paths, gpu_native_search_paths_multi,
    probe_device_allocation,
};
use kernels::*;
use memory::*;
pub use memory::{gpu_native_search, gpu_native_search_patterns};

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
    /// Exact matched-line bytes. Never passed through `String::from_utf8_lossy`: genuinely
    /// invalid-UTF-8 source content (e.g. Latin-1) must survive to JSON/NDJSON output losslessly
    /// rather than being silently replaced with U+FFFD -- the identical lossy-conversion defect
    /// class task #266/#746 fixed in the CPU native-search emitter (`NativeSearchMatch::raw`),
    /// applied here to close the same gap in the CUDA-gated GPU-native path (task #273). Both
    /// producers (`line_matches_for_file`, `line_matches_for_file_by_scanning`) slice line bytes
    /// using a boundary that already excludes the line's trailing `\n` (`classify_file_lines`'s
    /// `LineDescriptor::len` is computed as `newline_index - line_start`; the scanning path's
    /// `line_end` is the `\n`'s own byte index), so unlike `strip_native_line_terminator`'s "at
    /// most one trailing `\n`" contract for the CPU emitter, no terminator stripping is needed
    /// here at all -- a genuine trailing `\r` from a CRLF source line is real line content and
    /// must never be trimmed. JSON/NDJSON emission derives `text`/`bytes` from this field via
    /// `native_json_text_fields` (`main.rs::gpu_native_match_json_entries`), mirroring the CPU
    /// emitter's own `text`-XOR-`bytes` protocol.
    pub raw: Vec<u8>,
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
    /// Entries the walk reported an I/O error for (task 316, the GPU twin of
    /// `native_search::SearchStats::walk_errors`). Non-zero means the file list is INCOMPLETE:
    /// `main.rs` turns it into the envelope's `result_incomplete` /
    /// `incomplete_reason_class` / `incomplete_paths_count` triple via the shared
    /// `incomplete_envelope_fields`, and into exit 2 via `walk_was_incomplete`.
    pub walk_errors: usize,
}

#[derive(Debug, Clone, PartialEq, Serialize)]
pub struct GpuCudaGraphBenchmark {
    pub baseline: GpuNativeSearchStats,
    pub graphed: GpuNativeSearchStats,
    pub results_identical: bool,
    pub wall_time_reduction_pct: f64,
}

#[derive(Debug, Clone)]
pub(super) struct SearchFileEntry {
    pub(super) path: PathBuf,
    pub(super) estimated_bytes: usize,
    pub(super) order: usize,
}

#[derive(Debug, Clone)]
pub(super) struct DeviceFileAssignment {
    pub(super) device: CudaDeviceInfo,
    pub(super) files: Vec<SearchFileEntry>,
    pub(super) assigned_bytes: usize,
}

#[derive(Debug, Clone)]
pub(super) struct BatchedFile {
    pub(super) path: PathBuf,
    pub(super) start: usize,
    pub(super) end: usize,
    pub(super) line_descriptors: Vec<LineDescriptor>,
}

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub(super) struct LineDescriptor {
    pub(super) start: u32,
    pub(super) len: u32,
    pub(super) line_number: usize,
}

#[derive(Debug, Clone, Default)]
pub(super) struct ClassifiedLineBatch {
    pub(super) short_lines: Vec<LineDescriptor>,
    pub(super) medium_lines: Vec<LineDescriptor>,
    pub(super) long_lines: Vec<LineDescriptor>,
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
pub(super) struct FileBatchPlan {
    pub(super) files: Vec<PathBuf>,
    pub(super) estimated_bytes: usize,
}

#[derive(Debug, Clone)]
pub(super) struct PatternBatchPlan {
    pub(super) global_pattern_ids: Vec<usize>,
    pub(super) pattern_offsets: Vec<u32>,
    pub(super) pattern_lengths: Vec<u32>,
    pub(super) pattern_blob: Vec<u8>,
    pub(super) shared_mem_bytes: usize,
}

#[derive(Debug, Clone)]
pub(super) struct LoadedFileBatch {
    pub(super) files: Vec<BatchedFile>,
    pub(super) bytes_used: usize,
    pub(super) classified_lines: ClassifiedLineBatch,
    pub(super) cpu_staging_bytes: usize,
    pub(super) pageable_host_staging_bytes: usize,
    pub(super) host_file_read_time_ms: f64,
    pub(super) host_preprocess_time_ms: f64,
    pub(super) host_to_pinned_copy_time_ms: f64,
}

pub(super) struct DevicePatternBatch {
    pub(super) host: Arc<PatternBatchPlan>,
    pub(super) pattern_blob_device: CudaSlice<u8>,
    pub(super) pattern_offsets_device: CudaSlice<u32>,
    pub(super) pattern_lengths_device: CudaSlice<u32>,
}

#[derive(Debug, Clone, Default)]
pub(super) struct AdaptiveDispatchStats {
    pub(super) short_line_count: usize,
    pub(super) medium_line_count: usize,
    pub(super) long_line_count: usize,
    pub(super) warp_dispatch_count: usize,
    pub(super) block_dispatch_count: usize,
}

#[derive(Debug, Clone)]
pub(super) struct SlotAdaptiveDispatch {
    pub(super) stats: AdaptiveDispatchStats,
}

impl SlotAdaptiveDispatch {
    fn total_lines(&self) -> usize {
        self.stats.short_line_count + self.stats.medium_line_count + self.stats.long_line_count
    }
}

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub(super) enum SearchLaunchMode {
    Standard,
    GraphCapture,
    GraphReplay,
}

#[derive(Debug, Clone, Copy, PartialEq, Eq, Default)]
pub(super) struct SearchExecutionOptions {
    // Default-off in production (`Default` => false). Exercised on demand by the
    // `tg gpu-cuda-graphs` benchmark path (benchmark_cuda_graph_search_paths runs
    // both false and true); pending promotion to a production default once validated.
    pub(super) use_cuda_graphs: bool,
}

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub(super) struct GraphCaptureSignature {
    pub(super) bytes_used: usize,
    pub(super) short_line_count: usize,
    pub(super) medium_line_count: usize,
    pub(super) long_line_count: usize,
    pub(super) pattern_batch_index: usize,
}

pub(super) struct GraphCaptureState {
    pub(super) signature: GraphCaptureSignature,
    pub(super) graph: CapturedCudaGraph,
    pub(super) match_count_host: PinnedHostSlice<u32>,
}

pub(super) struct CapturedCudaGraph {
    pub(super) graph: sys::CUgraph,
    pub(super) graph_exec: sys::CUgraphExec,
    pub(super) stream: Arc<CudaStream>,
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

pub(super) struct EventTrackingModeGuard {
    pub(super) context: Arc<CudaContext>,
    pub(super) restore_enabled: bool,
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

pub(super) struct ReusableLineClassBuffers {
    pub(super) line_starts_host: Vec<u32>,
    pub(super) line_lengths_host: Vec<u32>,
    pub(super) line_starts_device: CudaSlice<u32>,
    pub(super) line_lengths_device: CudaSlice<u32>,
    pub(super) len: usize,
}

#[derive(Debug, Clone)]
pub(super) struct SearchDispatchPlan {
    pub(super) file_batch: FileBatchPlan,
    pub(super) pattern_batch_index: usize,
}

#[derive(Debug, Clone, PartialEq, Eq, Serialize)]
pub struct CudaDeviceInfo {
    pub device_id: i32,
    pub name: String,
    pub compute_capability: (i32, i32),
}

pub(super) struct KernelRuntime {
    pub(super) context: Arc<CudaContext>,
    pub(super) _module: Arc<CudaModule>,
    pub(super) position_function: CudaFunction,
    pub(super) short_line_function: CudaFunction,
    pub(super) warp_line_function: CudaFunction,
    pub(super) block_line_function: CudaFunction,
}

pub(super) struct SearchPipelineSlot {
    pub(super) stream: Arc<CudaStream>,
    pub(super) host_buffer: RawPinnedHostBuffer,
    pub(super) data_buffer: CudaSlice<u8>,
    pub(super) match_positions_buffer: CudaSlice<u32>,
    pub(super) match_pattern_ids_buffer: CudaSlice<u32>,
    pub(super) match_buffer_capacity: usize,
    pub(super) match_count_buffer: CudaSlice<u32>,
    pub(super) pattern_batches: Vec<DevicePatternBatch>,
    pub(super) current_batch: Option<LoadedFileBatch>,
    pub(super) adaptive_dispatch: Option<SlotAdaptiveDispatch>,
    pub(super) short_line_buffers: Option<ReusableLineClassBuffers>,
    pub(super) medium_line_buffers: Option<ReusableLineClassBuffers>,
    pub(super) long_line_buffers: Option<ReusableLineClassBuffers>,
    pub(super) current_pattern_batch_index: Option<usize>,
    pub(super) current_launch_mode: Option<SearchLaunchMode>,
    pub(super) transfer_start: Option<CudaEvent>,
    pub(super) transfer_end: Option<CudaEvent>,
    pub(super) kernel_end: Option<CudaEvent>,
    pub(super) cuda_graph: Option<GraphCaptureState>,
    pub(super) graph_launch_started_at: Option<Instant>,
}

pub(super) struct TransferBenchmarkSlot {
    pub(super) stream: Arc<CudaStream>,
    pub(super) host_buffer: RawPinnedHostBuffer,
    pub(super) device_buffer: CudaSlice<u8>,
    pub(super) transfer_start: Option<CudaEvent>,
    pub(super) transfer_end: Option<CudaEvent>,
    pub(super) pending_transfer: bool,
}

pub(super) struct PageableTransferBenchmarkSlot {
    pub(super) stream: Arc<CudaStream>,
    pub(super) host_buffer: Vec<u8>,
    pub(super) device_buffer: CudaSlice<u8>,
    pub(super) transfer_start: Option<CudaEvent>,
    pub(super) transfer_end: Option<CudaEvent>,
    pub(super) pending_transfer: bool,
}

pub(super) struct RawPinnedHostBuffer {
    pub(super) inner: PinnedHostSlice<u8>,
    pub(super) ptr: *mut u8,
    pub(super) len: usize,
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

pub(super) struct SearchPipelineOutcome {
    pub(super) transfer_bytes: usize,
    pub(super) matches: Vec<GpuNativeSearchMatch>,
    pub(super) pipeline: GpuPipelineStats,
}

pub(super) struct DeviceSearchOutcome {
    pub(super) stats: GpuNativeDeviceStats,
    pub(super) matches: Vec<GpuNativeSearchMatch>,
}

#[cfg(test)]
mod tests;
