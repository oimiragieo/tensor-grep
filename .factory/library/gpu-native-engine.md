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
