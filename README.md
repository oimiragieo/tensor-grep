<div align="center">
  <img src="docs/assets/logo.jpg" alt="tensor-grep logo" width="800"/>
</div>

# tensor-grep (tg)

Line oriented search tool using PyTorch and NVIDIA RAPIDS cuDF to accelerate regex matching and structural AST searching via Graph Neural Networks. Combines the raw performance of ripgrep with the semantic power of Transformer AI networks.

`tensor-grep` has first class support on Windows, macOS and Linux, gracefully routing workloads to pure Rust CPU backends when GPUs are unavailable, or scaling across massive multi-GPU arrays instantly via PCIe NVLink when running on enterprise hardware.

[![CI Status](https://github.com/oimiragieo/tensor-grep/actions/workflows/ci.yml/badge.svg)](https://github.com/oimiragieo/tensor-grep/actions)
[![PyPI version](https://badge.fury.io/py/tensor-grep.svg)](https://pypi.org/project/tensor-grep/)

Dual-licensed under MIT or the UNLICENSE.

### CHANGELOG
Please see the [CHANGELOG.md](CHANGELOG.md) for a release history.

## Quick examples comparing tools

This example benchmark demonstrates the raw throughput advantage of keeping data entirely in GPU memory, avoiding CPU PCIe bus bottlenecks. Timings were collected on a system with an AMD Ryzen 7 5800XT, 64GB RAM, and Dual RTX 4070/5070 cards using `cuDF` via WS2.

| Tool | Command | Line count | Time |
| --- | --- | --- | --- |
| tensor-grep (GPU) | `tg search -n -w '[A-Z]+_SUSPEND'` | 450 | **0.034s** |
| ripgrep | `rg -n -w '[A-Z]+_SUSPEND'` | 450 | 0.134s |
| ag (Silver Searcher) | `ag -w '[A-Z]+_SUSPEND'` | 450 | 0.753s |
| git grep | `LC_ALL=C git grep -E -n -w '[A-Z]+_SUSPEND'` | 450 | 0.823s |

Here's a straight-up comparison performing semantic NLP classification on a single large 500MB web-server log utilizing the NVIDIA Morpheus `cyBERT` transformer model:

| Tool | Command | Time |
| --- | --- | --- |
| tensor-grep (NLP) | `tg classify /var/logs/nginx.log` | **1.210s** |
| Python Regex Script | `python parse_logs.py /var/logs/nginx.log` | 18.143s |

## Why should I use `tensor-grep`?

- **It scales linearly with hardware.** If you are dealing with massive log files (100GB+) and you have access to enterprise NVIDIA GPUs or even modern consumer cards, `tensor-grep` will automatically chunk and distribute regex matching via `cuDF` natively inside GPU VRAM, bypassing CPU entirely.
- **It is a drop-in replacement for ripgrep.** `tg search` accepts the exact same 70+ CLI flags (`-i`, `-v`, `-C`, `-g`, `-t`) that you already know and love from `ripgrep`.
- **AST-Grep Parity (NEW):** Structural code searching via PyTorch Geometric Graph Neural Networks (GNNs). Run `tg run`, `tg scan`, `tg lsp` to match structural code patterns (e.g. `if ($A) { return $B; }`) rather than dumb text strings.
- **Semantic Understanding:** The `tg classify` command utilizes a specialized `cyBERT` HuggingFace transformer to identify malicious log patterns, detect hidden base64 payloads, and assign severity (WARN/ERROR/INFO) based on *context* rather than strict regex matches.
- **Resilient Fallback:** If you don't have a GPU, `tensor-grep` instantly transparently falls back to an embedded PyO3/Rust backend using `memmap2`, matching the baseline performance of standard CPU ripgrep.

## Why shouldn't I use `tensor-grep`?

I'd like to try to convince you why you *shouldn't* use `tensor-grep`. This should give you a glimpse at some important downsides.

- **You only search small files.** For small codebases, the overhead of moving memory across the PCIe bus into GPU VRAM actually makes `tensor-grep` marginally slower than standard CPU-bound `ripgrep`. It only shines when the dataset is massive.
- **You are on Windows Native.** While we support Windows native PyTorch CUDA, Windows `multiprocessing` uses `spawn()` rather than Linux's `fork()`. This adds an unavoidable ~11 second overhead to boot the CUDA context. (Use WSL2 instead for instant initialization!).
- **You need pure standalone binaries.** While we provide Nuitka-compiled standalone executables, they are ~3GB in size because they must statically bundle PyTorch and the CUDA toolkit.
- **You don't want heavy dependencies.** A full `tensor-grep` installation with AST and NLP capabilities requires installing `torch`, `torch-geometric`, `transformers`, and NVIDIA drivers. If you just want a 3MB fast search tool, stick to pure `ripgrep`.

## Installation

The binary name for `tensor-grep` is `tg`.

### Zero-Dependency Installation (Recommended)
To ensure PyTorch bindings and CUDA/ROCm versions exactly match your hardware without conflicting with your system Python, we recommend using our automated install scripts. These scripts use `uv` to intelligently probe your GPU and build a highly isolated Python 3.12 environment in the background.

**Windows (PowerShell):**
```powershell
irm https://raw.githubusercontent.com/oimiragieo/tensor-grep/main/scripts/install.ps1 | iex
```

**Linux & macOS (Bash):**
```bash
curl -LsSf https://raw.githubusercontent.com/oimiragieo/tensor-grep/main/scripts/install.sh | bash
```

### Python Package Managers (pip/uv)
If you're a Python programmer, `tensor-grep` can be installed via `pip` or `uv`.

```bash
# Basic CPU fallback installation
pip install tensor-grep

# Full installation with AST matching, NLP, and Linux GPU RAPIDS dependencies
uv pip install "tensor-grep[ast,nlp]" cudf-cu12 --extra-index-url https://pypi.nvidia.com
```

### Node.js (npx)
```bash
npx tensor-grep search "ERROR" .
```

### Standalone Binaries (For IT/SecOps)
If you cannot run scripts or prefer not to use `uv`, download the monolithic standalone executables from the GitHub Releases page. These `~3GB` files are built via Nuitka and contain Python, PyTorch, and the CUDA drivers completely bundled together:
* `tg-windows-amd64-nvidia.exe`
* `tg-linux-amd64-nvidia.bin`
* `tg-macos-amd64-cpu.bin`

### Docker
```bash
docker run --gpus all -v $(pwd):/workspace factory/tensor-grep:latest-cuda search "ERROR" /workspace/logs
```

## Whirlwind tour

The command line usage of `tensor-grep` doesn't differ much from other tools that perform a similar function. The full details can be found in `tg --help`.

To recursively search the current directory, while respecting all `.gitignore` files, ignore hidden files and directories and skip binary files:

```bash
$ tg foobar
```

(Note: Because `tensor-grep` perfectly intercepts `sys.argv`, you don't even need to type `tg search foobar`. Just typing `tg foobar` routes exactly as `rg foobar` does!)

Make the search case insensitive with `-i`, invert the search with `-v` or show the 2 lines before and after every search result with `-C2`:

```bash
$ tg -i -v -C2 foobar
```

Force all matches to be surrounded by word boundaries with `-w`:

```bash
$ tg -w foobar
```

Search only Python and Javascript files:

```bash
$ tg -tpy -tjs foobar
```

### AST / Structural Searching
Run semantic code structure searches that ignore formatting, whitespace, and comments:

```bash
$ tg run --ast --lang python "if ($A) { return $B; }" ./src
```

### NLP Log Classification
Scan a system log and rely on the CyBERT NLP model to automatically cluster and print warnings, ignoring explicit Regex patterns entirely:

```bash
$ tg classify /var/logs/syslog
```

## Building & Developing

`tensor-grep` uses a hybrid Rust & Python architecture.

```bash
$ git clone https://github.com/oimiragieo/tensor-grep
$ cd tensor-grep

# Install dependencies using uv
$ uv pip install -e ".[dev,ast,nlp]"

# Build the Rust PyO3 core locally via Maturin
$ python -m maturin develop --release

# Run the test suite
$ pytest tests/
```

## Hardware & Software Requirements

To unlock its 3x-10x GPU-accelerated speeds, your system must meet these requirements:

* **Hardware:**
  * NVIDIA GPU (GTX 10-Series or newer, RTX 30/40/50 series recommended)
  * Minimum 4GB VRAM (8GB+ recommended for massive logs)
* **Software / Drivers:**
  * **NVIDIA Display Drivers:** v535.xx or newer
  * **CUDA Toolkit:** 12.0 or newer (CUDA 12.4 highly recommended)
* **Python Environments:**
  * **Linux / WSL2:** Requires NVIDIA RAPIDS `cuDF` (`cudf-cu12`) for maximum throughput.
  * **Windows Native:** Requires PyTorch with CUDA 12 support.

## Tips

### Windows PyTorch Spawn Overhead
Because Windows Python `multiprocessing` requires `spawn()` rather than Linux's `fork()`, the PyTorch CUDA context takes ~11 seconds to initialize across multiple worker processes on Windows. 
- For small files (< 50MB), `tensor-grep` automatically bypasses the GPU on Windows to avoid this delay, routing to an optimized `CPUBackend` instead.
- For massive logs (> 200MB), the 11s Windows spawn overhead is absorbed by the sheer throughput of the GPU matrix math.

To achieve maximum enterprise performance on a Windows machine, **run tensor-grep inside WSL2**, where `fork()` allows instantaneous CUDA bindings.
