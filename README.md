<div align="center">
  <img src="docs/assets/logo.jpg" alt="tensor-grep logo" width="800"/>
</div>

# tensor-grep (tg)

Native search and rewrite tool for large text corpora and codebases. `tensor-grep` combines a Rust-native CPU text engine, Rust-native AST search/rewrite, indexed repeated-query acceleration, and a benchmark-governed native GPU path for large workloads.

`tensor-grep` has first class support on Windows, macOS and Linux. The native CPU engine embeds ripgrep's grep crates directly (no subprocess overhead) with chunk parallelism for large files. The native GPU engine uses Rust-native CUDA via `cudarc` with NVRTC JIT compilation, CUDA streams, pinned memory, and CUDA graphs. Smart routing automatically selects the fastest backend based on measured crossover data.

Harness consumers should use the documented public contracts in [docs/harness_api.md](docs/harness_api.md) and the workflow guide in [docs/harness_cookbook.md](docs/harness_cookbook.md).

## Canonical Docs

Use these documents as the current product contract instead of relying on scattered examples:

- [docs/benchmarks.md](docs/benchmarks.md) for the accepted benchmark matrix, artifact naming, and regression rules
- [docs/gpu_crossover.md](docs/gpu_crossover.md) for the current native GPU crossover story and its limits
- [docs/routing_policy.md](docs/routing_policy.md) for current CPU/GPU/index/AST routing behavior
- [docs/harness_api.md](docs/harness_api.md) for machine-readable CLI and MCP contract shapes
- [docs/harness_cookbook.md](docs/harness_cookbook.md) for end-to-end harness workflows using `tg.exe search --json`, `tg.exe search --ndjson`, `tg.exe run --rewrite`, `tg.exe calibrate`, and `tg mcp`
- [docs/installation.md](docs/installation.md) for the supported install paths and operational install notes
- [docs/RELEASE_CHECKLIST.md](docs/RELEASE_CHECKLIST.md) for the current enterprise release and rollback runbook

The project is benchmark-governed. Public claims should follow the canonical docs above, not historical README snapshots.

## Stable Windows Test Confirmation

On this Windows host, the most reliable repo-wide confirmation path is the file-backed pytest runner:

```powershell
uv run python scripts/run_pytest_stable.py --log artifacts/pytest_full.log --report artifacts/pytest_full_report.json
```

Why this exists:

- raw long-running `uv run pytest -q` sessions can be noisy or ambiguous under Windows process/capture behavior
- the stable runner uses `--capture=tee-sys`, `console_output_style=classic`, and `faulthandler_timeout`
- it writes both a human-readable log and a machine-readable report artifact

Current accepted full-suite artifact:

- [`artifacts/pytest_full_report.json`](artifacts/pytest_full_report.json)

## Bounded Heavy-Root AI Handoff

For large internal-library roots where full edit-plan assembly is too expensive, `tensor-grep` now supports a bounded context-render path that keeps the AI handoff compact and actionable.

Current accepted production proof:

- [`artifacts/external_validation/agent_studio_patch_driver_validation_summary_capped.json`](artifacts/external_validation/agent_studio_patch_driver_validation_summary_capped.json)

What the bounded path preserves:

- compact primary target selection
- `navigation_pack`
- phased read groups
- repo-level validation command

What it intentionally skips:

- the expensive full `edit_plan_seed` on that fast path

Use this when you need a fast planner-to-executor handoff on broad roots before paying for deeper planning.

[![CI Status](https://github.com/oimiragieo/tensor-grep/actions/workflows/ci.yml/badge.svg)](https://github.com/oimiragieo/tensor-grep/actions)
[![PyPI version](https://badge.fury.io/py/tensor-grep.svg)](https://pypi.org/project/tensor-grep/)

Dual-licensed under MIT or the UNLICENSE.

### CHANGELOG
Please see the [CHANGELOG.md](CHANGELOG.md) for a release history.

## Benchmark Snapshot

The canonical benchmark matrix lives in [docs/benchmarks.md](docs/benchmarks.md). The short version:

- cold generic text search stays near `rg`
- native CPU large-file search is the main measured win over `rg`
- native AST search/rewrite is benchmarked separately from plain text search
- indexed repeated-query paths are benchmarked separately from cold scans and now run under the CI benchmark job with the `bench` extra installed
- native GPU remains benchmark-governed and hardware-specific

Current repeated-query snapshot:

- artifact: [`artifacts/bench_hot_query_benchmarks_post_hotfix.json`](artifacts/bench_hot_query_benchmarks_post_hotfix.json)
- repeated fixed string: `0.7119s -> 0.2379s`
- repeated regex prefilter: `0.7188s -> 0.1951s`
- both rows now include fresh-process overhead
- local benchmark note: run `uv run --extra dev python benchmarks/run_hot_query_benchmarks.py` for the fully provisioned path; without the benchmark extras, the fixed-string row records `SKIP` with an install hint instead of crashing

Important constraint:

- do not treat internal GPU pipeline throughput as the same thing as end-to-end CLI crossover
- current GPU routing decisions should follow [docs/gpu_crossover.md](docs/gpu_crossover.md), not isolated microbenchmarks

## Why should I use `tensor-grep`?

- **Native CPU engine with real large-file wins.** The Rust text engine embeds ripgrep's grep crates directly, avoids subprocess overhead, and adds chunk parallelism for large files. See [docs/benchmarks.md](docs/benchmarks.md) for the accepted benchmark matrix.
- **Native AST search and rewrite.** `tg run` stays fully native for structural search, rewrite planning, diff, apply, and verify.
- **Repeated-query acceleration.** The trigram index gives warm-query wins on unchanged corpora without changing the public search contract.
- **Harness-first machine interfaces.** JSON, NDJSON, diff, batch rewrite, and MCP are documented and regression-tested. Start with [docs/harness_api.md](docs/harness_api.md) and [docs/harness_cookbook.md](docs/harness_cookbook.md).
- **Smart routing with measured calibration.** `tg calibrate` writes the current CPU/GPU routing contract. The active routing rules are documented in [docs/routing_policy.md](docs/routing_policy.md).
- **Benchmark-governed GPU path.** Native CUDA support exists, but route selection stays tied to measured crossover data. The current GPU story is documented in [docs/gpu_crossover.md](docs/gpu_crossover.md).
- **Multi-pattern GPU search.** Pass multiple patterns with `-e pattern1 -e pattern2` for GPU-accelerated multi-pattern matching in a single pass.
- **Per-request GPU pinning from CLI.** `tg search ... --gpu-device-ids 0,1` pins the current command to selected GPUs with strict input validation.
- **It is a drop-in replacement for ripgrep.** `tg search` accepts the exact same 70+ CLI flags (`-i`, `-v`, `-C`, `-g`, `-t`) that you already know and love from `ripgrep`.
- **In-place file mutations.** Unlike ripgrep, `tensor-grep` supports native find-and-replace mutability via `--replace` on the Rust path.
- **Native structural search and rewrite.** Run `tg run`, `tg scan`, and batch rewrite flows against the native AST backend instead of text-only matching.
- **Repeated-query acceleration.** Indexed literal and regex-prefilter paths are benchmarked separately from cold scans. See [docs/benchmarks.md](docs/benchmarks.md) for the current measured line instead of relying on stale microbench numbers.
- **Semantic Understanding:** `tg classify` uses `cyBERT` when the NLP stack is installed and reachable. Treat it as an optional path, not part of the default hot search loop.
- **Unified Harness API (NEW).** All JSON outputs (`--json` and `--ndjson`) share a common envelope (`version`, `routing_backend`, `routing_reason`, `sidecar_used`) so harnesses and AI agents can reliably parse routing decisions. Schema documentation and example artifacts are at [`docs/harness_api.md`](docs/harness_api.md) and [`docs/examples/`](docs/examples/). A Rust-side schema compatibility test locks the contract against accidental breakage.
- **NDJSON Streaming Output (NEW).** `tg search --ndjson` emits one JSON object per matching line, enabling streaming consumption for large result sets without buffering the entire response.
- **Batch AST Rewrite (NEW).** `tg run --batch-rewrite config.json` accepts multiple pattern/replacement/language rules in a single invocation. Cross-pattern overlaps are detected and reported without corrupting files.
- **Fast one-shot rewrite apply (NEW).** The one-shot CLI fast path `tg run --rewrite ... --apply` uses fused single-read direct writes to stay competitive with `sg`. The explicit planned-edit apply path still uses the safer atomic temp-file rename contract.
- **Stale-File Detection (NEW).** Before applying rewrite edits, the engine verifies that each file's mtime hasn't changed since planning. Stale files are rejected with a clear error rather than silently applying outdated edits.
- **Encoding Safety (NEW).** Rewrites preserve UTF-8 BOM and CRLF line endings in non-edited ranges. Binary files are automatically skipped. Large files (>100 MB) are skipped with a warning. Non-ASCII content (CJK, emoji, combining characters) is handled without corruption.
- **Index Compression (NEW).** The trigram index binary format now uses varint encoding for posting lists, achieving ~73.5% size reduction compared to the legacy format. The compressed format is the default and maintains full backward compatibility.
- **Incremental Index Updates (NEW).** When files are added, removed, or modified, the trigram index performs targeted updates instead of full rebuilds, reusing unchanged file entries for faster index maintenance on large repos.
- **Regex Index Acceleration (NEW).** The index now handles alternation patterns (`foo|bar`), character classes, and Unicode patterns for prefiltering, extending the set of queries that benefit from index acceleration.
- **GPU Sidecar Error Hardening (NEW).** GPU sidecar errors (timeout, invalid device ID, CUDA unavailable, malformed output, sidecar crash) are caught and reported with clear, actionable messages instead of raw tracebacks.
- **Documented Routing Policy (NEW).** Explicit routing decision tree documented at [`docs/routing_policy.md`](docs/routing_policy.md) with 14 routing regression tests covering every backend selection path.

## Why shouldn't I use `tensor-grep`?

I'd like to try to convince you why you *shouldn't* use `tensor-grep`. This should give you a glimpse at some important downsides.

- **You only search small files.** `rg` is still the baseline for tiny cold searches, and `tensor-grep` is designed to win on larger files, repeated queries, AST workflows, and harness loops.
- **You want GPU to win automatically on every host.** It does not. GPU routing is benchmark-governed and hardware-specific. Read [docs/gpu_crossover.md](docs/gpu_crossover.md) before forcing a GPU claim.
- **You need tiny standalone binaries.** The fully bundled release artifacts are still large because they carry optional Python/NLP/CUDA compatibility layers for non-native paths.
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

Installer defaults and channels:
- Default behavior installs the latest stable PyPI release.
- Set `TENSOR_GREP_VERSION` to pin a specific stable version (example: `TENSOR_GREP_VERSION=0.2.1`).
- Set `TENSOR_GREP_CHANNEL=main` to install directly from the GitHub `main` branch.
- At completion, the installer prints `tg --version` and returns to the directory where you started the script.
- Windows installer now installs `tg.cmd` shims in `~/.local/bin` and `~/bin`, updates both PowerShell 7 and Windows PowerShell profiles, and replaces stale aliases.

If `tg --version` still reports an older version, check command resolution:
```powershell
Get-Command tg
where.exe tg
```

Examples:
```powershell
# Windows PowerShell: install from main
$env:TENSOR_GREP_CHANNEL = "main"
irm https://raw.githubusercontent.com/oimiragieo/tensor-grep/main/scripts/install.ps1 | iex
```

```bash
# Linux/macOS: install a specific stable release
TENSOR_GREP_VERSION=0.2.1 curl -LsSf https://raw.githubusercontent.com/oimiragieo/tensor-grep/main/scripts/install.sh | bash
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

Force the native CPU engine (bypasses GPU even if available):

```bash
$ tg --cpu foobar
$ tg --force-cpu foobar
```

Select specific GPU devices for search:

```bash
$ tg --gpu-device-ids 0 foobar
$ tg --gpu-device-ids 0,1 foobar
```

Search for multiple patterns in a single pass (GPU-accelerated):

```bash
$ tg -e "ERROR" -e "FATAL" -e "PANIC" ./logs
```

Calibrate CPU vs GPU crossover thresholds for your hardware:

```bash
$ tg calibrate
```

This measures search performance at various corpus sizes and writes a `.tg_crossover` config file. After calibration, `tg` automatically routes to GPU when the corpus is large enough for GPU to be faster, and stays on CPU otherwise.

Inspect routable multi-GPU inventory and VRAM sizing:

```bash
$ tg devices
$ tg devices --format json
$ tg devices --json
```

### Streaming & Batch Operations

Emit search results as newline-delimited JSON (one object per match) for streaming consumption:

```bash
$ tg search --ndjson "ERROR" ./logs
```

Apply multiple AST rewrite rules in a single pass with a JSON config file:

```bash
$ tg run --batch-rewrite rewrites.json ./src
$ tg run --batch-rewrite rewrites.json --apply ./src
$ tg run --batch-rewrite rewrites.json --apply --verify --json ./src
```

Example `rewrites.json`:
```json
{
  "rewrites": [
    {"pattern": "def $F($$$ARGS): return $EXPR", "replacement": "lambda $$$ARGS: $EXPR", "lang": "python"},
    {"pattern": "console.log($X)", "replacement": "logger.info($X)", "lang": "javascript"}
  ],
  "verify": true
}
```

### AI Assistant Integration (MCP)
`tensor-grep` includes a native Model Context Protocol (MCP) server! This allows modern AI assistants (like Claude Desktop or Cursor) to directly utilize our GPU-accelerated regex engine, structural AST parsers, and cyBERT NLP log classifiers right inside their context windows.

To use it with Claude Desktop, just add this to your `claude_desktop_config.json`:
```json
{
  "mcpServers": {
    "tensor-grep": {
      "command": "tg",
      "args": ["mcp"]
    }
  }
}
```

Available MCP tools now include:
- `tg_search`
- `tg_ast_search`
- `tg_classify_logs`
- `tg_devices` (returns routable GPU IDs and VRAM inventory; supports JSON output)
- `tg_index_search` (trigram-indexed text search with auto-build/rebuild)
- `tg_rewrite_plan` (dry-run AST rewrite, returns JSON edit plan)
- `tg_rewrite_apply` (apply AST rewrite edits with optional byte-level verification)
- `tg_rewrite_diff` (unified diff preview of planned rewrites)

For machine consumers of CLI JSON output (`tg search ... --json`), routing metadata is included:
- `version` (contract version, currently `1`)
- `routing_backend`
- `routing_reason`
- `sidecar_used`
- `routing_gpu_device_ids`
- `routing_gpu_chunk_plan_mb`
- `routing_distributed`
- `routing_worker_count`

For streaming consumption, use `tg search ... --ndjson` to emit one JSON object per matching line (newline-delimited), ideal for piping to AI agents or large-result processing.

**AI Prompt Configuration:**
If you are building custom AI agents or bots, we provide an optimized prompt template explicitly outlining when and how AI models should use `tensor-grep`. Check out the [`SKILL.md`](SKILL.md) file to seamlessly inject our capabilities into your agent's system prompt!

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

`tensor-grep` uses a hybrid Rust & Python architecture with a native Rust binary for performance-critical paths.

### Python + Rust (PyO3) development

```bash
$ git clone https://github.com/oimiragieo/tensor-grep
$ cd tensor-grep

# Install dependencies using uv
$ uv pip install -e ".[dev,ast,nlp]"

# Build the Rust PyO3 core locally via Maturin
$ python -m maturin develop --release

# Run the Python test suite
$ pytest tests/
```

### Native Rust binary (CPU-only)

```bash
$ cd rust_core
$ cargo build --release
$ cargo test
```

### Native Rust binary with CUDA GPU support

Requires CUDA Toolkit 12.0+ installed and `nvcc` on PATH.

```bash
$ cd rust_core
$ cargo build --release --features cuda
$ cargo test --features cuda
```

The `cuda` feature links against `cudarc` (Rust-native CUDA bindings) and compiles GPU kernels via NVRTC JIT at runtime. Supported architectures include sm_89 (RTX 4070) and sm_120 (RTX 5070).

## Hardware & Software Requirements

### CPU-only (no GPU needed)

The native CPU engine requires only a Rust toolchain. No GPU, CUDA, or Python runtime is needed for the native binary. Current performance claims should be taken from [docs/benchmarks.md](docs/benchmarks.md), not this README.

### GPU-accelerated (native CUDA)

To unlock GPU acceleration, your system must meet these requirements. End-to-end GPU routing is still benchmark-governed and host-specific; see [docs/gpu_crossover.md](docs/gpu_crossover.md) for the current measured line.

* **Hardware:**
  * NVIDIA GPU (RTX 30/40/50 series recommended; tested on RTX 4070 sm_89 and RTX 5070 sm_120)
  * Minimum 4GB VRAM (8GB+ recommended for massive corpora)
  * Multi-GPU supported; current gains are workload-dependent and documented in [docs/gpu_crossover.md](docs/gpu_crossover.md)
* **Software / Drivers:**
  * **NVIDIA Display Drivers:** v535.xx or newer
  * **CUDA Toolkit:** 12.0 or newer (CUDA 12.4+ recommended; `nvcc` must be on PATH for JIT compilation)
* **Build:** `cargo build --release --features cuda` in the `rust_core` directory

### Python backends (optional)

The native CPU, AST, index, and primary GPU paths live in Rust. Python remains optional for NLP classification and compatibility sidecar paths:
* **Linux / WSL2:** NVIDIA RAPIDS `cuDF` (`cudf-cu12`) for optional sidecar-backed GPU integrations.
* **Windows Native:** PyTorch with CUDA 12 support for optional NLP and compatibility flows.
* **All platforms:** `uv pip install "tensor-grep[ast,nlp]"` for optional AST/NLP Python extras where needed.

## Enterprise Roadmap: GPUDirect Storage (GDS)

The absolute theoretical limit of local hardware parsing is bounded by the PCIe bus. Currently, `tensor-grep` uses Apache Arrow via `memmap2` to achieve end-to-end zero-copy routing:

**NVMe Disk -> OS Page Cache (CPU RAM via mmap) -> PCIe Bus -> GPU VRAM**

For multi-terabyte log repositories, the CPU RAM bounce-buffer becomes the limiting factor. The next frontier for `tensor-grep v2.0` is the integration of **NVIDIA cuFile (GPUDirect Storage)**. By replacing the Rust mmap with a Rust C++ FFI call to `cuFileRead()`, we can instruct the NVMe controller to bypass the CPU entirely and DMA (Direct Memory Access) the bytes straight from the SSD into the GPU VRAM.

## Tips

### Routing first, forcing later

- use `tg calibrate` before relying on auto GPU routing
- use `--gpu-device-ids` only when you have a workload that actually benefits
- use `--index` for warm repeated-query workflows
- use `--ndjson` for large result streams
- use plan -> diff -> apply+verify for structural edits

For current backend selection rules, see [docs/routing_policy.md](docs/routing_policy.md).
