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

To achieve the 3x-10x performance gains over traditional CPU tools, `tensor-grep` requires an NVIDIA GPU and the **NVIDIA RAPIDS** suite (`cuDF`).

**RAPIDS `cuDF` requires a Linux environment (or Windows WSL2).**

### 1. Install WSL2 (If on Windows)
If you are on Windows, you must run `tensor-grep` inside WSL2 to access the native C++ CUDA bindings:
```powershell
wsl --install
```

### 2. Install NVIDIA RAPIDS `cuDF`
Inside your Linux/WSL2 terminal, install the RAPIDS dependencies:
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
