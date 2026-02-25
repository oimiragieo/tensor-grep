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
# Run using uv to automatically pull PyTorch CUDA 12.4 hooks securely on Windows
uv run --python 3.12 --extra-index-url https://download.pytorch.org/whl/cu124 --index-strategy unsafe-best-match --with "torch==2.5.1+cu124" tg search "ERROR" /var/logs
```
`tensor-grep` will automatically detect Windows + PyTorch and dispatch workloads to the `TorchBackend`. 

#### ⚠️ Windows PyTorch Spawn Overhead
Because Windows Python `multiprocessing` requires `spawn()` rather than Linux's `fork()`, the PyTorch CUDA context takes ~11 seconds to initialize across multiple worker processes on Windows. 
- For small files (< 50MB), `tensor-grep` automatically bypasses the GPU on Windows to avoid this delay, routing to an optimized `CPUBackend` instead.
- For massive logs (> 200MB), the 11s Windows spawn overhead is absorbed by the sheer throughput of the GPU matrix math.

### Linux / Windows WSL2 (Maximum Enterprise Performance) 🚀
For absolute maximum performance using raw CUDA C++ string bindings (`cuDF`), **run tensor-grep inside WSL2 or Linux.**
Because Linux uses `fork()`, process initialization is practically instantaneous, meaning you will actually see sub-`0.02s` speeds across your dual GPUs!

```bash
# If using a RAPIDS conda environment:
conda activate rapids-24.04

# Or using uv to pull the linux cuDF wheels directly:
uv run --python 3.12 --extra-index-url https://pypi.nvidia.com --with "cudf-cu12" python run_benchmarks.py
```

Once installed, `tensor-grep` will automatically detect `cuDF`, discover your GPUs, and route all regex and string operations directly to your video cards' VRAM using the `CuDFBackend`.

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
