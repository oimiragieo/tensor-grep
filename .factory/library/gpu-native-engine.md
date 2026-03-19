# Native GPU Engine (cudarc)

## cudarc API patterns
```rust
use cudarc::driver::{CudaContext, LaunchConfig, PushKernelArg};
use cudarc::nvrtc::compile_ptx;

// Init device
let ctx = CudaContext::new(device_id)?;
let stream = ctx.default_stream();

// Compile kernel at runtime (JIT)
let ptx = compile_ptx(KERNEL_SOURCE)?;
let module = ctx.load_module(ptx)?;
let kernel = module.load_function("text_search")?;

// Transfer data
let data_dev = stream.clone_htod(&data_bytes)?;
let mut results_dev = stream.alloc_zeros::<i32>(max_matches)?;

// Launch kernel
let cfg = LaunchConfig::for_num_elems(data_len as u32);
let mut builder = stream.launch_builder(&kernel);
builder.arg(&data_dev);
builder.arg(&mut results_dev);
builder.arg(&pattern_len);
unsafe { builder.launch(cfg) }?;

// Get results
let results = stream.clone_dtoh(&results_dev)?;
```

## Brute-force substring search kernel
```cuda
extern "C" __global__ void text_search(
    const char* text, int text_len,
    const char* pattern, int pattern_len,
    int* match_positions, int* match_count
) {
    int idx = blockIdx.x * blockDim.x + threadIdx.x;
    if (idx + pattern_len <= text_len) {
        bool match = true;
        for (int j = 0; j < pattern_len; j++) {
            if (text[idx + j] != pattern[j]) { match = false; break; }
        }
        if (match) {
            int pos = atomicAdd(match_count, 1);
            match_positions[pos] = idx;
        }
    }
}
```

## Bandwidth math (RTX 4070)
- GPU memory: 504 GB/s -> scan 1GB in ~2ms
- PCIe 4.0 x16: ~22 GB/s -> copy 1GB in ~45ms
- Total for 1GB: ~48ms (vs rg ~200-300ms)
- Crossover: GPU wins at ~50MB+ for substring

## Feature gating
- All CUDA code behind `cfg(feature = "cuda")`
- `cudarc` uses dynamic loading — no compile-time CUDA SDK needed
- NVRTC JIT handles sm_89 (RTX 4070) and sm_120 (RTX 5070)
- Graceful Err return when CUDA unavailable (never panic)

## Advanced optimizations (M3)
- Pinned memory: cuMemAllocHost for ~2x transfer throughput
- CUDA streams: overlap transfer[N+1] with kernel[N]
- Multi-pattern: all patterns in shared memory, single dispatch
- Multi-GPU: split files across devices via rayon threads
- Warp-parallel: 32 threads cooperate on long lines
- CUDA graphs: capture+replay for reduced launch overhead

## Native explicit GPU routing integration notes (2026-03-16)
- `--gpu-device-ids` now routes to native Rust CUDA search when `rust_core` is built with `--features cuda`; non-CUDA builds still use the Python sidecar path.
- The current native path batches all discovered files into one contiguous host buffer with `\0` file separators, does one host-to-device transfer, runs one kernel launch, then maps byte offsets back to `(file, line_number, line_text)` matches on the host.
- Current native GPU routing intentionally falls back to the Python sidecar for unsupported search modes (`--ignore-case`, regex syntax, context lines, `--max-count`, `-w`, invert-match). This keeps the explicit GPU flag working while advanced GPU features are still pending.
- On this Windows host, `CUDA_VISIBLE_DEVICES=""` did not reliably hide devices from the native cudarc path during tests. Future fallback work should not depend on that env var alone for negative-path coverage.

## Testing and Mocking
- Test hooks: `TG_TEST_CUDA_BEHAVIOR` and `TG_TEST_NATIVE_SEARCH_FORCE_ERROR` environment variables can be used to mock GPU initialization failures, CUDA unavailability, and CPU search engine failures during testing. Since `CUDA_VISIBLE_DEVICES=""` does not reliably hide devices from the native cudarc path on Windows, use `TG_TEST_CUDA_BEHAVIOR=not_found` or `TG_TEST_CUDA_BEHAVIOR=init_failure` instead.

## Windows DLL Dynamic Loading Quirk
- `cudarc` dynamic loading on Windows requires `nvrtc64*.dll` to be locatable. If the CUDA bin directory is not on the `PATH`, `cudarc` can panic or fail.
- The current implementation attempts to locate the CUDA toolkit and temporarily modifies the process `PATH` using `env::set_var` at runtime before calling `CudaContext::new`. Note that modifying the environment at runtime is not thread-safe in Rust and can race with other threads.
