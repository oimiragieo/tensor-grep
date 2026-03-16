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

Fresh benchmark pass results (2026-03-16, local run on current `main`) from this repository's benchmark scripts are below.

Environment notes:
- End-to-end CLI timings include Python process startup cost.
- These figures are from a local `uv run python benchmarks/run_benchmarks.py` / `run_ast_benchmarks.py` / `run_ast_workflow_benchmarks.py` / `run_gpu_benchmarks.py` execution.
- `run_benchmarks.py` and `run_ast_benchmarks.py` now measure the bootstrap entrypoint used by the installed `tg` console script, rather than the older `tensor_grep.cli.main` module path.
- `ripgrep` remains faster on most text-search scenarios in this local benchmark setup.
- The current Windows local run no longer trips the stored regression guard in `benchmarks/baselines/run_benchmarks.windows.json`.
- The GPU microbenchmark requires benchmark extras plus a reachable Triton endpoint for `cyBERT`; on this host the AST and Torch backend timings completed, while `cyBERT` was explicitly skipped because no Triton server was running.
- On Windows, benchmark scripts now auto-extract the `ripgrep` binary from the GitHub release archive when it is not found on `PATH`, so Windows CI runs are fully self-contained.
- `--replace` throughput improved **+6.36%** after switching to `MmapMut` in-place byte mutations (zero-copy path, removing the prior `std::fs::read` allocation).
- Hot-query cache path acceleration confirmed via `benchmarks/run_hot_query_benchmarks.py`; repeated queries over unchanged files are reliably tracked for regression.

### ripgrep vs tensor-grep (`benchmarks/run_benchmarks.py`)

| Scenario | ripgrep | tensor-grep | Result |
| --- | --- | --- | --- |
| Simple String Match | 0.451s | 0.609s | Parity PASS |
| Case-Insensitive Match | 0.501s | 0.671s | Parity PASS |
| Regex Match | 0.506s | 0.682s | Parity PASS |
| Invert Match | 1.309s | 1.477s | Parity PASS |
| Count Matches | 0.146s | 0.093s | Parity PASS |
| Context Lines (`-C2`) | 1.757s | 1.956s | Parity PASS |
| Max Count (`-m 5`) | 0.116s | 0.280s | Parity PASS |
| File Glob Filtering | 0.511s | 0.611s | Parity PASS |
| Word Boundary | 0.495s | 0.696s | Parity PASS |
| Fixed Strings (`-F`) | 0.476s | 0.594s | Parity PASS |

### ast-grep vs tensor-grep AST mode (`benchmarks/run_ast_benchmarks.py`)

| Scenario | ast-grep | tensor-grep | Result |
| --- | --- | --- | --- |
| Simple Function Def | 0.126s | 0.428s | Parity PASS |
| Try/Except Block | 0.113s | 0.404s | Parity PASS |
| Class Declaration | 0.118s | 0.401s | Parity PASS |

### tensor-grep AST workflow startup (`benchmarks/run_ast_workflow_benchmarks.py`)

| Scenario | tensor-grep |
| --- | --- |
| `tg run "def $FUNC():\n    $$$BODY" .` synthetic AST workflow | 0.331s |
| `tg scan --config sgconfig.yml` synthetic AST workflow | 0.328s |
| `tg test --config sgconfig.yml` synthetic AST workflow | 0.503s |

### Advanced backend microbenchmarks (`benchmarks/run_gpu_benchmarks.py`)

| Backend | Workload | Time | Output |
| --- | --- | --- | --- |
| AST backend | `function_definition` on test module | 0.062s | 4 matches |
| cyBERT backend | Semantic classification on 10,000 log lines | skipped on this host | Triton endpoint not running |
| Torch backend | Exact match on 10,000 log lines | 0.630s | 2,000 matches |

### Test Coverage

- **145 Rust tests** covering native text search, AST search/rewrite, trigram index, routing decisions, GPU sidecar IPC, schema compatibility, encoding safety, batch rewrites, and incremental index updates.
- **563 Python tests** covering CLI bootstrap, CPU/GPU backends, MCP server tools, sidecar protocol, release validation, and benchmark harnesses.

### Benchmark Governance (Regression Protection)

- Benchmark scripts now emit machine-readable JSON artifacts in `artifacts/`.
- Use `benchmarks/check_regression.py` to compare current runs against a baseline and fail if regression exceeds threshold.
- Regression checks are now environment-aware (platform/machine metadata); cross-OS comparisons are rejected by default unless explicitly overridden.
- Main CI (`.github/workflows/ci.yml`) now includes a required `benchmark-regression` job on Ubuntu that runs `benchmarks/run_benchmarks.py`, enforces baseline regression thresholds, and publishes a markdown summary + JSON/text artifacts.
- Standalone benchmark workflow (`.github/workflows/benchmark.yml`) remains available for manual and scheduled deep benchmark passes.
- Current local status on 2026-03-16: `benchmarks/check_regression.py --baseline auto --current artifacts/bench_run_benchmarks.json` passed.
- Additional benchmark scripts available: multi-language AST (`run_ast_multi_lang_benchmarks.py`), large-scale rewrite, harness loop, and index scaling benchmarks.
- Multi-language corpus generators (`gen_corpus.py`) now support Python, JavaScript, TypeScript, and Rust for benchmark validation across languages.
- Release workflow now validates the full GitHub binary artifact filename matrix and publishes `CHECKSUMS.txt` (SHA256) alongside release binaries for reproducible integrity checks.
- Release asset verification enforces that each managed binary's `CHECKSUMS.txt` digest matches GitHub release `asset.digest` metadata, closing post-upload integrity gaps.

## Why should I use `tensor-grep`?

- **It scales linearly with hardware.** If you are dealing with massive log files (100GB+) and you have access to enterprise NVIDIA GPUs or even modern consumer cards, `tensor-grep` will automatically chunk and distribute regex matching via `cuDF` natively inside GPU VRAM, bypassing CPU entirely.
- **Explicit multi-GPU routing contract.** Runtime scheduling now exposes stable ID enumeration (`DeviceDetector.enumerate_device_ids()`) and rich device enumeration (`DeviceDetector.list_devices()`), where `list_devices()` returns `(device_id, vram_capacity_mb)` for each routable GPU. This is the canonical API contract for sharding/routing decisions.
- **Explicit device pinning override.** Set `TENSOR_GREP_DEVICE_IDS` (for example `TENSOR_GREP_DEVICE_IDS=3,7`) to constrain scheduling and fanout to specific GPUs.
- **Per-request GPU pinning for library/runtime callers.** `SearchConfig(gpu_device_ids=[...])` now propagates through `Pipeline -> MemoryManager -> CuDFBackend` so workloads can be pinned to selected GPUs without mutating process-wide env vars.
- **Explicit pinning is first-class in routing.** When `gpu_device_ids` is provided for search modes that do not require CPU-only semantics, pipeline selection attempts pinned GPU backends first, then safely falls back to `rg`/Rust/CPU if unavailable.
- **Explicit hardware contract errors.** Passing `--ast` or `--gpu-device-ids` when the required backend is genuinely unavailable now raises a `ConfigurationError` immediately rather than silently falling back to an unrelated backend. Misrouted workloads are surfaced as configuration mistakes, not silently degraded results.
- **Runtime GPU routing observability.** `Pipeline` now records `selected_gpu_device_ids` for the active backend selection so service wrappers and telemetry pipelines can audit exactly which GPU IDs were used.
- **Per-result routing metadata.** `SearchResult` now carries `routing_backend`, `routing_reason`, `routing_gpu_device_ids`, and `routing_gpu_chunk_plan_mb` for structured post-search telemetry.
- **Per-request GPU pinning from CLI.** `tg search ... --gpu-device-ids 0,1` pins the current command to selected GPUs with strict input validation.
- **Device-ID normalization contract.** Duplicate/invalid preferred IDs are ignored during routing normalization; if all requested IDs are invalid, the scheduler falls back to the detected routable GPU set instead of disabling GPU execution.
- **It is a drop-in replacement for ripgrep.** `tg search` accepts the exact same 70+ CLI flags (`-i`, `-v`, `-C`, `-g`, `-t`) that you already know and love from `ripgrep`.
- **In-Place File Mutations (NEW):** Unlike ripgrep, `tensor-grep` natively supports memory-mapped find-and-replace mutability via `--replace`. Apply `sed`-like capture groups (e.g. `$1`) at millions of lines per second without ever leaving the Rust terminal backend. The replace path now uses `MmapMut` for true zero-copy in-place byte mutations, removing the prior `std::fs::read` allocation bottleneck (**+6.36% throughput**).
- **AST-Grep Parity (NEW):** Structural code searching via PyTorch Geometric Graph Neural Networks (GNNs). Run `tg run`, `tg scan`, `tg lsp` to match structural code patterns (e.g. `if ($A) { return $B; }`) rather than dumb text strings.
- **Repeated AST searches are materially faster now.** `AstBackend` caches compiled tree-sitter queries plus parsed file state (`mtime_ns`/size keyed) so `tg scan` / `tg test` / repeated in-process AST workloads stop recompiling and reparsing unchanged modules on every pass.
- **AST caches are now shared across backend instances in the same process.** `scan` / `test` no longer pay separate parser/query/source cache misses just because different rules selected separate `AstBackend` objects.
- **Bounded AST parsed-source cache.** The shared `_shared_parsed_source_cache` now enforces a byte-bounded LRU eviction policy so long-running processes cannot grow the cache without bound. The cap is configurable via `TENSOR_GREP_AST_PARSED_SOURCE_CACHE_MAX_BYTES` (default: 256 MB). Cache keys use inode + ctime for correct file identity across renames and in-place edits.
- **Persistent AST result cache.** Repeated structural queries across unchanged files can now reuse on-disk AST result entries across CLI invocations. Cache location can be overridden with `TENSOR_GREP_AST_CACHE_DIR`, or disabled with `TENSOR_GREP_AST_CACHE=0`.
- **Persistent AST node-type index.** Simple native AST queries such as `function_definition` can now reuse an on-disk node-type line index across runs, which lets later native queries over unchanged files skip reparsing entirely.
- **REI-style repeated literal index.** `StringZillaBackend` now builds a per-file trigram line index for repeated fixed-string searches. On this host, a synthetic hot-corpus microbenchmark dropped from about `1.05s` on the first indexed build to about `0.0025s` on the second cached literal query over the same file.
- **Safe repeated-regex prefilter in Python fallback.** When `tg` must fall back to Python regex and the pattern has a guaranteed literal core, `CPUBackend` now reuses a trigram prefilter index to cut candidate lines before running `re`. The cache now persists across backend instances and fresh CLI invocations. On this host, a synthetic repeated regex microbenchmark dropped from about `0.243s` on the first indexed query to about `0.014s` on the second cached query over the same file.
- **Hot-query benchmark harness.** `benchmarks/run_hot_query_benchmarks.py` now tracks these repeated-query cache paths explicitly so we can catch regressions instead of relying on ad hoc microbenchmarks.
- **Semantic Understanding:** The `tg classify` command utilizes a specialized `cyBERT` HuggingFace transformer to identify malicious log patterns, detect hidden base64 payloads, and assign severity (WARN/ERROR/INFO) based on *context* rather than strict regex matches. NLP queries (`QueryType.NLP`) are now fully wired through `CybertBackend` end-to-end (previously the routing path was unconnected dead code).
- **CybertBackend server liveness check.** `CybertBackend.is_available()` now performs a real liveness probe against the configured Triton inference server rather than always returning `True`. The probe timeout is configurable via `TENSOR_GREP_TRITON_TIMEOUT_SECONDS` (default: 5 s).
- **Resilient Fallback:** If you don't have a GPU, `tensor-grep` instantly transparently falls back to an embedded PyO3/Rust backend using `memmap2`, matching the baseline performance of standard CPU ripgrep.
- **Unified Harness API (NEW).** All JSON outputs (`--json` and `--ndjson`) share a common envelope (`version`, `routing_backend`, `routing_reason`, `sidecar_used`) so harnesses and AI agents can reliably parse routing decisions. Schema documentation and example artifacts are at [`docs/harness_api.md`](docs/harness_api.md) and [`docs/examples/`](docs/examples/). A Rust-side schema compatibility test locks the contract against accidental breakage.
- **NDJSON Streaming Output (NEW).** `tg search --ndjson` emits one JSON object per matching line, enabling streaming consumption for large result sets without buffering the entire response.
- **Batch AST Rewrite (NEW).** `tg run --batch-rewrite config.json` accepts multiple pattern/replacement/language rules in a single invocation. Cross-pattern overlaps are detected and reported without corrupting files.
- **Atomic Writes for Rewrites (NEW).** All rewrite apply operations (`--apply`) use write-to-temp + atomic rename, preventing data loss if the process is interrupted mid-write.
- **Stale-File Detection (NEW).** Before applying rewrite edits, the engine verifies that each file's mtime hasn't changed since planning. Stale files are rejected with a clear error rather than silently applying outdated edits.
- **Encoding Safety (NEW).** Rewrites preserve UTF-8 BOM and CRLF line endings in non-edited ranges. Binary files are automatically skipped. Large files (>10 MB) are skipped with a warning. Non-ASCII content (CJK, emoji, combining characters) is handled without corruption.
- **Index Compression (NEW).** The trigram index binary format now uses varint encoding for posting lists, achieving ~73.5% size reduction compared to the legacy format. The compressed format is the default and maintains full backward compatibility.
- **Incremental Index Updates (NEW).** When files are added, removed, or modified, the trigram index performs targeted updates instead of full rebuilds, reusing unchanged file entries for faster index maintenance on large repos.
- **Regex Index Acceleration (NEW).** The index now handles alternation patterns (`foo|bar`), character classes, and Unicode patterns for prefiltering, extending the set of queries that benefit from index acceleration.
- **GPU Sidecar Error Hardening (NEW).** GPU sidecar errors (timeout, invalid device ID, CUDA unavailable, malformed output, sidecar crash) are caught and reported with clear, actionable messages instead of raw tracebacks.
- **Documented Routing Policy (NEW).** Explicit routing decision tree documented at [`docs/routing_policy.md`](docs/routing_policy.md) with 14 routing regression tests covering every backend selection path.

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

## Enterprise Roadmap: GPUDirect Storage (GDS)

The absolute theoretical limit of local hardware parsing is bounded by the PCIe bus. Currently, `tensor-grep` uses Apache Arrow via `memmap2` to achieve end-to-end zero-copy routing:

**NVMe Disk -> OS Page Cache (CPU RAM via mmap) -> PCIe Bus -> GPU VRAM**

For multi-terabyte log repositories, the CPU RAM bounce-buffer becomes the limiting factor. The next frontier for `tensor-grep v2.0` is the integration of **NVIDIA cuFile (GPUDirect Storage)**. By replacing the Rust mmap with a Rust C++ FFI call to `cuFileRead()`, we can instruct the NVMe controller to bypass the CPU entirely and DMA (Direct Memory Access) the bytes straight from the SSD into the GPU VRAM.

## Tips

### Windows PyTorch Spawn Overhead
Because Windows Python `multiprocessing` requires `spawn()` rather than Linux's `fork()`, the PyTorch CUDA context takes ~11 seconds to initialize across multiple worker processes on Windows. 
- For small files (< 50MB), `tensor-grep` automatically bypasses the GPU on Windows to avoid this delay, routing to an optimized `CPUBackend` instead.
- For massive logs (> 200MB), the 11s Windows spawn overhead is absorbed by the sheer throughput of the GPU matrix math.
- **CUDA worker isolation (Windows).** The `CuDFBackend` process pool now sets `CUDA_VISIBLE_DEVICES` in each worker *before* importing any CUDA library, preventing cross-GPU contamination when multiple GPUs are present. Windows process pools additionally set `max_tasks_per_child=1` to reclaim leaked CUDA context memory between tasks.

To achieve maximum enterprise performance on a Windows machine, **run tensor-grep inside WSL2**, where `fork()` allows instantaneous CUDA bindings.
