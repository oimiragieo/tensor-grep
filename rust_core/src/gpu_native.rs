use anyhow::{anyhow, Context, Result};
use cudarc::driver::{
    CudaContext, CudaFunction, CudaModule, CudaStream, LaunchConfig, PushKernelArg,
};
use cudarc::nvrtc::{compile_ptx_with_opts, CompileOptions};
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
