use anyhow::{anyhow, Context, Result};
use cudarc::driver::{
    CudaContext, CudaFunction, CudaModule, CudaStream, LaunchConfig, PushKernelArg,
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
}

#[derive(Debug, Clone, PartialEq, Eq)]
pub struct GpuNativeSearchMatch {
    pub path: PathBuf,
    pub line_number: usize,
    pub text: String,
}

#[derive(Debug, Clone, PartialEq, Eq)]
pub struct GpuNativeSearchStats {
    pub searched_files: usize,
    pub matched_files: usize,
    pub total_matches: usize,
    pub transfer_bytes: usize,
    pub selected_device: CudaDeviceInfo,
    pub matches: Vec<GpuNativeSearchMatch>,
}

#[derive(Debug, Clone)]
struct BatchedFile {
    path: PathBuf,
    start: usize,
    end: usize,
}

#[derive(Debug, Clone, PartialEq, Eq)]
pub struct CudaDeviceInfo {
    pub device_id: i32,
    pub name: String,
    pub compute_capability: (i32, i32),
}

struct KernelRuntime {
    _context: Arc<CudaContext>,
    stream: Arc<CudaStream>,
    _module: Arc<CudaModule>,
    function: CudaFunction,
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
    let (buffer, batch) = build_batched_file_buffer(&files)?;
    if buffer.is_empty() {
        return Ok(GpuNativeSearchStats {
            searched_files: batch.len(),
            matched_files: 0,
            total_matches: 0,
            transfer_bytes: 0,
            selected_device,
            matches: Vec::new(),
        });
    }

    let mut positions = gpu_native_search(&config.pattern, &buffer, device_id)?;
    positions.sort_unstable_by_key(|matched| matched.byte_offset);

    let matches = convert_offsets_to_line_matches(&config.pattern, &buffer, &batch, &positions)?;
    let matched_files = matches
        .iter()
        .map(|matched| matched.path.clone())
        .collect::<std::collections::BTreeSet<_>>()
        .len();

    Ok(GpuNativeSearchStats {
        searched_files: batch.len(),
        matched_files,
        total_matches: matches.len(),
        transfer_bytes: buffer.len(),
        selected_device,
        matches,
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
    let max_matches = u32::try_from(num_positions).context("GPU native search exceeds u32 match capacity")?;

    let runtime = create_kernel_runtime(device_id)?;
    let data_device = runtime
        .stream
        .clone_htod(data)
        .map_err(anyhow::Error::new)
        .context("failed to copy search data to CUDA device")?;
    let pattern_device = runtime
        .stream
        .clone_htod(pattern_bytes)
        .map_err(anyhow::Error::new)
        .context("failed to copy pattern to CUDA device")?;
    let mut match_positions_device = runtime
        .stream
        .alloc_zeros::<u32>(num_positions)
        .map_err(anyhow::Error::new)
        .context("failed to allocate CUDA match position buffer")?;
    let mut match_count_device = runtime
        .stream
        .alloc_zeros::<u32>(1)
        .map_err(anyhow::Error::new)
        .context("failed to allocate CUDA match counter")?;

    let launch_config = LaunchConfig {
        grid_dim: (
            u32::try_from(num_positions)
                .context("GPU native search launch size exceeds u32 grid limits")?
                .div_ceil(KERNEL_THREADS_PER_BLOCK),
            1,
            1,
        ),
        block_dim: (KERNEL_THREADS_PER_BLOCK, 1, 1),
        shared_mem_bytes: 0,
    };

    catch_cuda("launch GPU substring search kernel", || {
        let mut launch = runtime.stream.launch_builder(&runtime.function);
        launch.arg(&data_device);
        launch.arg(&data_len);
        launch.arg(&pattern_device);
        launch.arg(&pattern_len);
        launch.arg(&mut match_positions_device);
        launch.arg(&max_matches);
        launch.arg(&mut match_count_device);
        unsafe { launch.launch(launch_config) }.map_err(anyhow::Error::new)
    })?;

    let match_count = runtime
        .stream
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
    let positions = runtime
        .stream
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
    let stream = context.default_stream();
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
        _context: context,
        stream,
        _module: module,
        function,
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

fn build_batched_file_buffer(files: &[PathBuf]) -> Result<(Vec<u8>, Vec<BatchedFile>)> {
    let mut buffer = Vec::new();
    let mut batch = Vec::new();

    for path in files {
        let bytes = fs::read(path)
            .with_context(|| format!("failed to read GPU native search file {}", path.display()))?;
        if bytes.contains(&b'\0') {
            continue;
        }
        if !buffer.is_empty() {
            buffer.push(0);
        }
        let start = buffer.len();
        buffer.extend_from_slice(&bytes);
        let end = buffer.len();
        batch.push(BatchedFile {
            path: path.clone(),
            start,
            end,
        });
    }

    Ok((buffer, batch))
}

fn convert_offsets_to_line_matches(
    pattern: &str,
    buffer: &[u8],
    batch: &[BatchedFile],
    offsets: &[MatchPosition],
) -> Result<Vec<GpuNativeSearchMatch>> {
    let pattern_len = pattern.len();
    let mut grouped_offsets = vec![Vec::new(); batch.len()];
    let mut batch_index = 0usize;

    for matched in offsets {
        while batch_index < batch.len() && matched.byte_offset >= batch[batch_index].end {
            batch_index += 1;
        }
        if batch_index >= batch.len() {
            break;
        }

        let file = &batch[batch_index];
        let end_offset = matched
            .byte_offset
            .checked_add(pattern_len)
            .ok_or_else(|| anyhow!("GPU native match offset overflowed usize"))?;
        if matched.byte_offset < file.start || end_offset > file.end {
            continue;
        }

        grouped_offsets[batch_index].push(matched.byte_offset - file.start);
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
