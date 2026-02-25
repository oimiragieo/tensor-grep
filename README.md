# tensor-grep (tg)

**The GPU-Accelerated Semantic Log Parsing CLI**

`tensor-grep` combines the raw regex speed of traditional tools like `ripgrep` with the semantic understanding of Transformer AI networks (`cyBERT`), parallelized across multiple GPUs using NVIDIA RAPIDS `cuDF`.

## Features
* **Drop-in Replacement:** Supports 70+ `ripgrep` CLI flags (e.g., `-i`, `-v`, `-C`, `-g`, `-t`).
* **Multi-GPU Scaling:** Automatically detects and shards massive log files across dual, quad, or enterprise GPU arrays.
* **Semantic NLP Classification:** Utilize cyBERT to classify logs contextually (e.g. identify "ERROR" severity without explicit regexes) in a single pass.
* **CPU Fallback Resiliency:** Works gracefully on Windows, macOS, and CPU-only systems using a resilient Python Regex backend.

---

## 🚀 GPU Acceleration Requirements (CRITICAL)

To achieve the 3x-10x performance gains over traditional CPU tools, `tensor-grep` utilizes NVIDIA's RAPIDS suite (`cuDF`) on Linux/WSL2, and falls back to an optimized native **PyTorch Tensor** pipeline when running natively on Windows.

### Windows Native GPU Support (No WSL2 Required)
If you do not want to use WSL2 and want to run `tensor-grep` natively from PowerShell/CMD while still utilizing your GPU:
```powershell
# Install PyTorch with CUDA 12.1 or 12.4 support
pip3 install torch torchvision torchaudio --index-url https://download.pytorch.org/whl/cu121
```
`tensor-grep` will automatically detect Windows + PyTorch and dispatch workloads to the `TorchBackend`, which converts strings into CUDA Tensors to process parallel 1D match convolutions natively on your local GPU.

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
```
