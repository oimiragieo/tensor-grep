<div align="center">
  <img src="docs/assets/logo.jpg" alt="tensor-grep logo" width="800"/>
</div>

# tensor-grep (tg)

Native search and rewrite tool for large text corpora and codebases. `tensor-grep` combines a Rust-native CPU text engine, Rust-native AST search/rewrite, indexed repeated-query acceleration, and a benchmark-governed native GPU path for large workloads.

`tensor-grep` has first class support on Windows, macOS and Linux. The native CPU engine embeds ripgrep's grep crates directly (no subprocess overhead) with chunk parallelism for large files. The native GPU engine uses Rust-native CUDA via `cudarc` with NVRTC JIT compilation, CUDA streams, pinned memory, and CUDA graphs. GPU routing stays opt-in unless local calibration proves a real end-to-end crossover.

Harness consumers should use the documented public contracts in [docs/harness_api.md](docs/harness_api.md) and the workflow guide in [docs/harness_cookbook.md](docs/harness_cookbook.md).

## Canonical Docs

Use these documents as the current product contract instead of relying on scattered examples:

- [docs/benchmarks.md](docs/benchmarks.md) for the accepted benchmark matrix, artifact naming, and regression rules
- [docs/tool_comparison.md](docs/tool_comparison.md) for the public workload-class comparison story against `rg`, `git grep`, `ast-grep`, and other comparator families
- [docs/gpu_crossover.md](docs/gpu_crossover.md) for the current native GPU crossover story and its limits
- [docs/routing_policy.md](docs/routing_policy.md) for current CPU/GPU/index/AST routing behavior
- [docs/harness_api.md](docs/harness_api.md) for machine-readable CLI and MCP contract shapes
- [docs/harness_cookbook.md](docs/harness_cookbook.md) for end-to-end harness workflows using `tg.exe search --json`, `tg.exe search --ndjson`, `tg.exe run --rewrite`, `tg.exe calibrate`, and `tg mcp`
- [docs/installation.md](docs/installation.md) for the supported install paths and operational install notes
- [docs/RELEASE_CHECKLIST.md](docs/RELEASE_CHECKLIST.md) for the current enterprise release and rollback runbook
- [docs/CI_PIPELINE.md](docs/CI_PIPELINE.md) for the current CI, release, audit, and dependency-maintenance automation

The project is benchmark-governed. Public claims should follow the canonical docs above, not historical README snapshots.

## Enterprise Docs

These documents define the operating and governance surface for teams running `tensor-grep` in production:

- [docs/SUPPORT_MATRIX.md](docs/SUPPORT_MATRIX.md) for supported platforms, runtimes, and distribution channels
- [docs/CONTRACTS.md](docs/CONTRACTS.md) for compatibility guarantees around configs, caches, and machine-readable outputs
- [docs/HOTFIX_PROCEDURE.md](docs/HOTFIX_PROCEDURE.md) for patch, rollback, and verification process
- [docs/EXPERIMENTAL.md](docs/EXPERIMENTAL.md) for hidden and opt-in features that are intentionally outside the stable public CLI surface
- [docs/CI_PIPELINE.md](docs/CI_PIPELINE.md) for CI workflow structure, Dependabot policy, and scheduled audit remediation
- [SECURITY.md](SECURITY.md) for vulnerability reporting expectations
- [CONTRIBUTING.md](CONTRIBUTING.md) for contribution, validation, and release-intent rules

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

For large internal-library roots, `tensor-grep` supports a bounded context-render path that keeps the AI handoff compact and actionable without letting symbol navigation escape the capped repo-map universe.

Current accepted production proof:

- [`artifacts/external_validation/agent_studio_patch_driver_validation_summary_capped.json`](artifacts/external_validation/agent_studio_patch_driver_validation_summary_capped.json)

What the bounded path preserves:

- compact primary target selection
- `navigation_pack`
- phased read groups
- repo-level validation command

What is now contract-tested:

- `include_edit_plan_seed=False` keeps the fast lightweight path
- `include_edit_plan_seed=True` returns full `edit_plan_seed`, `candidate_edit_targets`, and `navigation_pack` while honoring `max_repo_files`

Use this when you need a fast planner-to-executor handoff on broad roots before paying for deeper planning.

[![CI Status](https://github.com/oimiragieo/tensor-grep/actions/workflows/ci.yml/badge.svg)](https://github.com/oimiragieo/tensor-grep/actions)
[![PyPI version](https://badge.fury.io/py/tensor-grep.svg)](https://pypi.org/project/tensor-grep/)

Dual-licensed under MIT or the UNLICENSE.

### CHANGELOG
Please see the [CHANGELOG.md](CHANGELOG.md) for a release history.

## Benchmark Snapshot

The canonical benchmark matrix lives in [docs/benchmarks.md](docs/benchmarks.md). One benchmark is never enough. The public comparison summary lives in [docs/tool_comparison.md](docs/tool_comparison.md), and the tables below are the current host-local snapshot on this Windows machine, not a universal claim.

Current quick tool comparison:

- artifact: [`artifacts/bench_tool_comparison.json`](artifacts/bench_tool_comparison.json)
- script: `uv run python benchmarks/run_tool_comparison_benchmarks.py --output artifacts/bench_tool_comparison.json`

| Scenario | ripgrep | `tg search` | `tg search --cpu` | `git grep --no-index` |
| --- | --- | --- | --- | --- |
| standard corpus | `0.217s` | `0.269s` | `0.243s` | `0.292s` |
| 200MB large file | `0.210s` | `0.218s` | `0.219s` | `0.204s` |

Current read:

- `rg` remains the cold generic text-search baseline
- fresh `v1.6.5` cold-path evidence now passes the frozen Windows regression gate (`artifacts/bench_run_benchmarks_v165_control_plane_current.json`, parity PASS on all 10 rows, median `tg_time_s = 0.260132s`)
- cold-path attribution now confirms benchmark claims should use the explicit repo native binary; shell-discovered `tg` can be stale and is treated as environment-drift evidence
- `tg search` is near `rg` on the 200MB row, but `git grep --no-index` won that specific host-local row in the latest run
- host-local peer rows currently include `rg` and `git grep --no-index`; `ag`, `ack`, `ugrep`, and `grep` are omitted on this host because they are not installed
- native AST search, AST rewrite, repeated-query acceleration, and GPU are separate benchmark surfaces and should not be conflated with cold plain-text search

Current repeated-query snapshot:

- artifact: [`artifacts/bench_hot_query_benchmarks.json`](artifacts/bench_hot_query_benchmarks.json)
- repeated fixed string: `0.6535s -> 0.1784s`
- repeated regex prefilter: `0.6425s -> 0.2147s`
- both rows now include fresh-process overhead
- local benchmark note: run `uv run --extra bench python benchmarks/run_hot_query_benchmarks.py` for the fully provisioned path; without the benchmark extras, the fixed-string row records `SKIP` with an install hint instead of crashing

Current AI handoff comparison snapshot:

- artifact: [`artifacts/external_validation/external_agent_patch_driver_scorecard.json`](artifacts/external_validation/external_agent_patch_driver_scorecard.json)
- mean compactness score: `1.0`
- mean validation-fit score: `1.0`
- mean parallel-read reduction score: `0.916667`
- mean overall score: `0.972222`
- current read-group heuristic: same-directory related/test reads are prefetched into the primary phase when they stay local to the edit slice

Current repo-map lexical retrieval snapshot:

- baseline artifact: `artifacts/bench_repo_retrieval_lexical_base.json`
- accepted feature artifact: `artifacts/bench_repo_retrieval_lexical_feature.json`
- curated retrieval line moved from `recall_at_5 = 0.0`, `mrr_at_5 = 0.0`, `ndcg_at_5 = 0.0` on clean `origin/main` to `recall_at_5 = 1.0`, `mrr_at_5 = 1.0`, `ndcg_at_5 = 1.0`, `file_f1 = 0.333333`, `line_f1 = 0.222222`
- current read: camelCase-to-snake_case symbol bridging and source-term fallback now recover the right planning file on the curated repo-map pack, while `context-render` and blast-radius remain in the same measured editor-plane band on this host instead of becoming a new cold-path speed claim

Current benchmark-governed strengths:

- native CPU benchmark line: with rg fallback disabled for native measurement, `tg --cpu` wins all four current native CPU rows, including `large_file_200mb_count` (`0.050s` vs `0.199s`) and `many_file_directory` (`0.048s` vs `0.204s`) in [`artifacts/bench_run_native_cpu_benchmarks.json`](artifacts/bench_run_native_cpu_benchmarks.json)
- native AST search beats `sg` on the current AST search surfaces in [docs/benchmarks.md](docs/benchmarks.md)
- AST rewrite remains functional and the one-shot apply path is back under the `sg` ratio gate on the current local benchmark (`0.831x` in `artifacts/bench_ast_rewrite.json`)
- repeated-query acceleration remains the strongest warm-path win on unchanged corpora

Current CLI correctness line:

- plain-text and `--json` invocations now share the same routed command surface for `doctor`, `map`, `session`, `checkpoint`, `rulesets`, `context-render`, `edit-plan`, and the blast-radius family
- `tg search --replace` rewrites emitted match text in ripgrep style without mutating files
- `tg search -o` now mirrors ripgrep single-file output formatting instead of forcing `file:line:text`
- `tg run --json` emits structured output even without `--apply`

Important constraint:

- do not treat internal GPU pipeline throughput as the same thing as end-to-end CLI crossover
- current GPU routing decisions should follow [docs/gpu_crossover.md](docs/gpu_crossover.md), not isolated microbenchmarks

## Product Contracts

`tensor-grep` enforces strict behavioral and output contracts to ensure reliable execution for both human users and AI agent harnesses.

- **ripgrep-Compatible Search Contract:** The current stable text-search contract is the validated common rg-compatible surface covered by the parity suite and contract benchmark runner, plus tensor-grep's documented `--ndjson` streaming extension. The rows currently covered are `-i/--ignore-case`, `-v/--invert-match`, `-C/--context`, `-A/--after-context`, `-B/--before-context`, `-g/--glob`, `-l/--files-with-matches`, `--files-without-match`, `--json`, `--ndjson`, `-F/--fixed-strings`, `-w/--word-regexp`, `-m/--max-count` including `--max-count=N`, `-t/--type`, `-./--hidden`, `-L/--follow`, `-S/--smart-case`, `-n/--line-number`, `--column`, `-c/--count`, `--count-matches`, and `-a/--text`. Additional rg-style flags may be exposed in `tg search --help`, but they are not part of the benchmarked compatibility claim until they are added to the contract suite.
- **Routing Parity:** `tensor-grep` maintains exact character-for-character parity for text search outputs across all supported launcher modes (`native`, `bootstrap`, `python-m`). The only exception is `--help` text, which differs in word-wrapping layout between Clap (Rust) and Typer (Python) but guarantees the presence of valid `Usage:` instructions.
- **Golden-Output Scope:** The test suite snapshots exact, raw, and deterministic groupings and file path output directly from the engines. Native `tg.exe` intentionally does not support `-a` text parsing of binary fixtures; that binary-text case is handled by the Python `ripgrep` fallback and explicitly skipped in native-only contract tests.
- **Launcher Behavior:** The native Rust binary (`tg.exe`) acts as the primary front door, embedding AST search and fast-path text search. Unimplemented complex flags fall back to the Python sidecar. The Python wrapper (`python -m tensor_grep`) delegates structural and plain search commands back down to the native binary when available to guarantee uniform performance and path resolution. On Windows, `tensor-grep` intentionally rejects `PythonXY\Scripts\tg.exe` console-entrypoint shims when resolving that native path; use a release binary, an in-tree build, or `TG_NATIVE_TG_BINARY` when you need to force a specific native executable.
- **Non-Contract Fields:** Absolute temporary directory paths (normalized to `<TMP_DIR>` in tests), non-deterministic multi-threaded file ordering (stabilized via `-j 1` in tests or sorting where applicable), and specific help-text layouts are intentional non-contract fields.

## Why should I use `tensor-grep`?

- **Native CPU engine with measured workload-class wins.** The Rust text engine embeds ripgrep's grep crates directly, avoids subprocess overhead in the native path, and adds chunk parallelism for large files. See [docs/tool_comparison.md](docs/tool_comparison.md) and [docs/benchmarks.md](docs/benchmarks.md) for the current measured line.
- **Native AST search and rewrite.** `tg run` stays fully native for structural search, rewrite planning, diff, apply, and verify. PyPI wheels also expose Rust rewrite plan/apply through the PyO3 extension so simple CLI and MCP rewrite plan/apply paths work even when a standalone native `tg` binary is not installed.
- **Repeated-query acceleration.** The trigram index gives warm-query wins on unchanged corpora without changing the public search contract.
- **Harness-first machine interfaces.** JSON, NDJSON, diff, batch rewrite, and MCP are documented and regression-tested. Start with [docs/harness_api.md](docs/harness_api.md) and [docs/harness_cookbook.md](docs/harness_cookbook.md).
- **Lexical-first repo-map retrieval for AI planning.** Exact symbol queries stay anchored to definition files, camelCase queries bridge to snake_case symbols, and source-term fallback only engages when parser/path signals are weak.
- **Smart routing with measured calibration.** `tg calibrate` writes the current CPU/GPU routing contract. The active routing rules are documented in [docs/routing_policy.md](docs/routing_policy.md).
- **Benchmark-governed GPU path.** Native CUDA support exists, but route selection stays tied to measured crossover data. The current GPU story is documented in [docs/gpu_crossover.md](docs/gpu_crossover.md).
- **Multi-pattern GPU search.** Pass multiple patterns with `-e pattern1 -e pattern2` for GPU-accelerated multi-pattern matching in a single pass.
- **Per-request GPU pinning from CLI.** `tg search ... --gpu-device-ids 0,1` pins the current command to selected GPUs with strict input validation.
- **It has a validated ripgrep-compatible common search surface.** `tg search` has a benchmarked compatibility contract for the day-to-day flags that matter most in code and log search, with the currently validated rows documented in [docs/CONTRACTS.md](docs/CONTRACTS.md).
- **Output replacement and actual rewrites are separate tools.** `tg search --replace` rewrites emitted match text in ripgrep style, while `tg run --rewrite ... --apply` performs real file edits through the AST rewrite path.
- **Native structural search and rewrite.** Run `tg run`, `tg scan`, and batch rewrite flows against the native AST backend instead of text-only matching.
- **Repeated-query acceleration.** Indexed literal and regex-prefilter paths are benchmarked separately from cold scans. See [docs/benchmarks.md](docs/benchmarks.md) for the current measured line instead of relying on stale microbench numbers.
- **Semantic Understanding:** `tg classify` uses `cyBERT` when the NLP stack is installed and reachable. Treat it as an optional path, not part of the default hot search loop.
- **Unified Harness API (NEW).** All JSON outputs (`--json` and `--ndjson`) share a common envelope (`version`, `routing_backend`, `routing_reason`, `sidecar_used`) so harnesses and AI agents can reliably parse routing decisions. Schema documentation and example artifacts are at [`docs/harness_api.md`](docs/harness_api.md) and [`docs/examples/`](docs/examples/). A Rust-side schema compatibility test locks the contract against accidental breakage.
- **NDJSON Streaming Output (NEW).** `tg search --ndjson` emits one JSON object per matching line, enabling streaming consumption for large result sets without buffering the entire response.
- **Batch AST Rewrite (NEW).** `tg run --batch-rewrite config.json` accepts multiple pattern/replacement/language rules in a single invocation. Cross-pattern overlaps are detected and reported without corrupting files.
- **One-shot rewrite apply (NEW).** The one-shot CLI fast path `tg run --rewrite ... --apply` uses fused single-read direct writes for safe simple apply shapes. The explicit planned-edit apply path still uses the safer atomic temp-file rename contract, and contract-heavy paths such as JSON, diff, checkpoint, audit, validation, verify, selector, and batch rewrite stay on the plan-first path. Current speed claims follow the AST rewrite benchmark gate in [docs/benchmarks.md](docs/benchmarks.md).
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
- Set `TENSOR_GREP_VERSION` to pin a specific stable version (example: `TENSOR_GREP_VERSION=1.1.3`).
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
TENSOR_GREP_VERSION=1.1.3 curl -LsSf https://raw.githubusercontent.com/oimiragieo/tensor-grep/main/scripts/install.sh | bash
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

The npm wrapper downloads the release-validated CPU binary for supported x64 platforms from GitHub Releases.

### Standalone Binaries (For IT/SecOps)
If you cannot run the install scripts or prefer a managed binary rollout, use the GitHub release assets and checksum manifest from the tagged release.

Current release assets include:
* `tg-windows-amd64-cpu.exe`
* `tg-windows-amd64-nvidia.exe`
* `tg-linux-amd64-cpu`
* `tg-linux-amd64-nvidia`
* `tg-macos-amd64-cpu`

Operational notes:
- Each tagged release also publishes `CHECKSUMS.txt` and a `package-manager-bundle/` for Homebrew and Winget submission flows.
- Prefer the Python install path if you want `tg update` / `tg upgrade` to self-update the installed package.
- Experimental features remain opt-in and are documented in [docs/EXPERIMENTAL.md](docs/EXPERIMENTAL.md), not surfaced in the top-level help output.

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

List files that do not contain a match while still honoring ignore rules by default:

```bash
$ tg search foobar . --files-without-match
```

Add `--no-ignore` when you want ignored files and directories included in the candidate set for this mode.

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

This measures search performance at various corpus sizes and writes a `.tg_crossover` config file. Only rely on automatic GPU routing when that local artifact shows a real end-to-end crossover; the current Windows benchmark keeps GPU search manual-only.

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
$ tg run --lang python "if ($A) { return $B; }" ./src
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

The `cuda` feature links against `cudarc` (Rust-native CUDA bindings) and compiles GPU kernels via NVRTC JIT at runtime. The current accepted benchmark line covers sm_89 (RTX 4070). RTX 50-series / sm_120 hosts need a CUDA 12.8+ compatible stack for PyTorch-backed sidecar flows and are not benchmark-promoted by device discovery alone.

## Hardware & Software Requirements

### CPU-only (no GPU needed)

The native CPU engine requires only a Rust toolchain. No GPU, CUDA, or Python runtime is needed for the native binary. Current performance claims should be taken from [docs/benchmarks.md](docs/benchmarks.md), not this README.

### GPU-accelerated (native CUDA)

To unlock GPU acceleration, your system must meet these requirements. End-to-end GPU routing is still benchmark-governed and host-specific; see [docs/gpu_crossover.md](docs/gpu_crossover.md) for the current measured line.

* **Hardware:**
  * NVIDIA GPU (RTX 30/40 series recommended; RTX 50-series / sm_120 support depends on the CUDA/PyTorch stack described in [docs/runbooks/gpu-troubleshooting.md](docs/runbooks/gpu-troubleshooting.md))
  * Minimum 4GB VRAM (8GB+ recommended for massive corpora)
  * Multi-GPU supported; current gains are workload-dependent and documented in [docs/gpu_crossover.md](docs/gpu_crossover.md)
* **Software / Drivers:**
  * **NVIDIA Display Drivers:** v535.xx or newer
  * **CUDA Toolkit:** 12.0 or newer (CUDA 12.4+ recommended for current accepted paths; CUDA 12.8+ is required for PyTorch-backed RTX 50-series / sm_120 compatibility)
* **Build:** `cargo build --release --features cuda` in the `rust_core` directory

### Python backends (optional)

The native CPU, AST, index, and primary GPU paths live in Rust. Python remains optional for NLP classification and compatibility sidecar paths:
* **Linux / WSL2:** NVIDIA RAPIDS `cuDF` (`cudf-cu12`) for optional sidecar-backed GPU integrations.
* **Windows Native:** PyTorch with CUDA 12 support for optional NLP and compatibility flows.
* **All platforms:** `uv pip install "tensor-grep[ast,nlp]"` for optional AST/NLP Python extras where needed.

## Future Work

The `v1.x` line is feature-complete for the current native search, AST, and editor-plane surface. The remaining work is intentionally narrow:

- add any lexical reranking or AST-shaped chunking only when it beats the accepted lexical-first repo-map line on both retrieval quality and editor-plane benchmarks
- add tighter multi-agent signal surfaces on top of the existing JSON/NDJSON, session, and MCP contracts instead of inventing another parallel agent protocol
- publish a broader reproducible comparator pack for tools such as `ag`, `ack`, `ugrep`, and GNU `grep` alongside the current `rg` and `git grep` rows
- graduate or retire the experimental resident AST worker based on benchmark-governed evidence, not intuition
- keep benchmark-governed security and compliance acceleration on top of the existing rulesets and audit surfaces
- keep managed provider / editor-plane integrations honest and contract-tested
- continue supply-chain hardening, package-manager validation, and operational docs for team ownership
- preserve benchmark history and rejected experiments so future work stays measurable instead of speculative

## Tips

### Routing first, forcing later

- use `tg calibrate` before considering auto GPU routing, and keep GPU manual-only unless the artifact shows a real crossover
- use `--gpu-device-ids` only when you have a workload that actually benefits
- use `--index` for warm repeated-query workflows
- use `--ndjson` for large result streams
- use plan -> diff -> apply+verify for structural edits

For current backend selection rules, see [docs/routing_policy.md](docs/routing_policy.md).
