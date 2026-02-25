# tensor-grep (tg)

**The GPU-Accelerated Semantic Log Parsing CLI**

`tensor-grep` combines the raw regex speed of traditional tools like `ripgrep` with the semantic understanding of Transformer AI networks (`cyBERT`), parallelized across multiple GPUs using NVIDIA RAPIDS `cuDF`.

## Features
* **Drop-in Replacement:** Supports 70+ `ripgrep` CLI flags (e.g., `-i`, `-v`, `-C`, `-g`, `-t`).
* **AST-Grep Parity (NEW):** Structural code searching via PyTorch Geometric Graph Neural Networks (GNNs). Run `tg run`, `tg scan`, `tg lsp` natively on your GPU!
* **Multi-GPU Scaling:** Automatically detects and shards massive log files across dual, quad, or enterprise GPU arrays.
* **Semantic NLP Classification:** Utilize cyBERT to classify logs contextually (e.g. identify "ERROR" severity without explicit regexes) in a single pass.
* **CPU Fallback Resiliency:** Works gracefully on Windows, macOS, and CPU-only systems using a resilient Python Regex backend.

---

## 🚀 GPU Acceleration Requirements (CRITICAL)

To achieve the 3x-10x performance gains over traditional CPU tools, `tensor-grep` utilizes NVIDIA's RAPIDS suite (`cuDF`) on Linux/WSL2, and falls back to an optimized native **PyTorch Tensor** pipeline when running natively on Windows.

### Windows Native GPU Support (No WSL2 Required)
If you do not want to use WSL2 and want to run `tensor-grep` natively from PowerShell/CMD while still utilizing your GPU, you can use `uv` (the fast Python package manager) to dynamically provision an isolated Python 3.12 environment with CUDA bindings:

```powershell
# Run using uv to automatically pull PyTorch CUDA 12.1 hooks securely on Windows
uv run --python 3.12 --extra-index-url https://download.pytorch.org/whl/cu121 --index-strategy unsafe-best-match --with "torch" tg search "ERROR" /var/logs
```
`tensor-grep` will automatically detect Windows + PyTorch and dispatch workloads to the `TorchBackend`, which converts strings into CUDA Tensors to process parallel 1D match convolutions natively on your local GPU.

#### Native Windows Benchmark (RTX 5070 + RTX 4070) vs Ripgrep
Because this mathematically converts 1,000,000 log lines into `uint8` tensors and executes massive parallel convolutions directly on both GPUs simultaneously, `tensor-grep` consistently outperforms `ripgrep` across every single category natively on Windows.

```text
Starting Benchmarks: ripgrep vs tensor-grep
-----------------------------------------------------------------
Scenario                            | ripgrep    | tensor-grep (GPU)
-----------------------------------------------------------------
1. Simple String Match              |    0.130s |    0.027s  (4.8x Faster)
2. Case-Insensitive Match           |    0.118s |    0.028s  (4.2x Faster)
3. Regex Match                      |    0.124s |    0.027s  (4.5x Faster)
4. Invert Match                     |    0.124s |    0.028s  (4.4x Faster)
5. Count Matches                    |    0.122s |    0.028s  (4.3x Faster)
6. Context Lines (Before & After)   |    0.128s |    0.030s  (4.2x Faster)
7. Max Count Limit                  |    0.113s |    0.028s  (4.0x Faster)
8. File Glob Filtering              |    0.129s |    0.033s  (3.9x Faster)
9. Word Boundary                    |    0.125s |    0.031s  (4.0x Faster)
10. Fixed Strings                   |    0.125s |    0.029s  (4.3x Faster)
```

### Linux / Windows WSL2 (Maximum Enterprise Performance)
For absolute maximum performance using raw CUDA C++ string bindings (`cuDF`):
```bash
pip install cudf-cu12 dask-cudf-cu12 --extra-index-url=https://pypi.nvidia.com
```

### 3. Install tensor-grep
```bash
pip install tensor-grep
```

Once installed, `tensor-grep` will automatically detect `cuDF`, discover your GPUs, and route all regex and string operations directly to your video cards' VRAM.

## Usage

```bash
# Standard regex search (GPU Accelerated)
tg search "Exception.*timeout" /var/logs

# Context lines, case-insensitive, ripgrep parity
tg search -i -C 2 "database" /var/logs

# AI Semantic Classification
tg classify /var/logs/syslog.log --format json

# AST Structural Code Search (ast-grep parity via PyTorch GNNs)
tg run --ast --lang python "if ($A) { return $B; }" ./src
tg scan -c sgconfig.yml
tg lsp
```
