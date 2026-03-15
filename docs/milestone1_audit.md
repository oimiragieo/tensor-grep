# Milestone 1: rust-native-control-plane — Startup & Control Plane Audit

## Executive Summary

**tg.exe already runs zero-Python for text search and AST search.**

The Rust binary (`main.rs`) never initializes PyO3 in-process. Python is only
ever spawned out-of-process via `python_sidecar.rs` for non-search commands
(`mcp`, `classify`, `scan`, `test`, `new`, `lsp`).

The architectural gap is **not** that the binary calls Python — it doesn't for
hot paths. The gap is that the **Python entry point** (`python -m tensor_grep`)
still exists as a parallel path with its own startup overhead, and the benchmark
harness sometimes measures that path instead of the native binary.

---

## 1. What Runs Before Search (tg.exe native path)

```
tg.exe PATTERN PATH
  ├─ Collect raw args                           (~0 overhead)
  ├─ should_use_positional_cli()                (string comparison vs 8 subcommand names)
  ├─ PositionalCli::parse_from() via clap       (fast, compiled arg parsing)
  ├─ ripgrep_is_available()                     (file-exists check for rg binary)
  └─ execute_ripgrep_search()                   (spawn rg child process)
```

**Total Rust-side overhead before search: <1ms.**
No Python. No PyO3. No interpreter init. No imports.

---

## 2. Command Ownership Classification

### Rust-only (zero Python)

| Command | Backend | Mechanism |
|---------|---------|-----------|
| `tg PATTERN PATH` (positional) | rg passthrough or CpuBackend | Rust native |
| `tg search PATTERN PATH` | rg passthrough | Spawns rg.exe |
| `tg run PATTERN PATH` | AstBackend | ast-grep-core in-process |
| `tg PATTERN PATH --force-cpu` | CpuBackend | memmap2 + regex + rayon |
| `tg PATTERN PATH --replace X` | CpuBackend | memmap2 + regex in-place |
| `tg PATTERN PATH --count` | CpuBackend | memmap2 + memchr fast count |
| `tg PATTERN PATH --json` | CpuBackend | count + JSON metadata |

### Python sidecar (out-of-process, cold paths)

| Command | Mechanism | Protocol |
|---------|-----------|----------|
| `tg classify FILE` | `python -m tensor_grep.sidecar` | JSON stdin/stdout |
| `tg scan` | `python -m tensor_grep.sidecar` | JSON stdin/stdout |
| `tg test` | `python -m tensor_grep.sidecar` | JSON stdin/stdout |
| `tg new` | `python -m tensor_grep.sidecar` | JSON stdin/stdout |
| `tg mcp` | `python -m tensor_grep mcp` | stdio passthrough |
| `tg lsp` | `python -m tensor_grep lsp` | stdio passthrough |

---

## 3. Where Python Exists in the Codebase

### PyO3 in-process (lib.rs + backend_gpu.rs) — NOT called from tg.exe

`lib.rs` defines `#[pymodule] fn rust_core` with:
- `read_mmap_to_arrow()` — zero-copy mmap→Arrow for cuDF path
- `read_mmap_to_arrow_chunked()` — chunked variant
- `RustBackend` pyclass — search/count/replace exposed to Python

`backend_gpu.rs` has `pyo3::prepare_freethreaded_python()` and `Python::with_gil`
calls, but **none of these functions are called from main.rs**. They exist only for
the cdylib (`.pyd`) consumed by Python-side imports.

**Key insight:** The `#[pymodule]` code is for the **reverse direction** — Python
importing Rust, not the tg.exe binary calling Python.

### python_sidecar.rs — Out-of-process only

Two patterns:
1. **Sidecar IPC**: Spawns `python -m tensor_grep.sidecar`, sends JSON on stdin,
   reads JSON response. Used by: classify, scan, test, new.
2. **Passthrough**: Spawns `python -m tensor_grep <cmd>` with inherited stdio.
   Used by: mcp, lsp.

Python resolution priority:
1. `TG_SIDECAR_PYTHON` env var
2. `python.exe` adjacent to `tg.exe`
3. `.venv/Scripts/python.exe` relative to exe
4. System `python`

### The parallel Python CLI (bootstrap.py)

The Python entry point (`python -m tensor_grep`) has its own routing:

```
bootstrap.py::main_entry()
  ├─ --version → print, exit
  ├─ run/scan/test → ast_workflows.py
  ├─ _normalize_search_invocation()
  ├─ _requires_full_cli() → check for tg-only flags
  ├─ _resolve_rg_binary() → shutil.which("rg")
  └─ subprocess.run([rg, ...]) → exit
       OR
  └─ _run_full_cli() → import main.py (typer + all backends)
```

**The Python bootstrap fast path is lean** (only stdlib imports), but
it still has Python interpreter startup overhead (~30-80ms).

---

## 4. Sidecar Protocol (already implemented)

```rust
// Request (Rust → Python)
struct SidecarRequest {
    command: String,         // "classify", "scan", "test", "new"
    args: Vec<String>,       // positional args
    payload: Option<Value>,  // arbitrary JSON
}

// Response (Python → Rust)
struct SidecarResponse {
    exit_code: i32,
    stdout: String,
    stderr: String,
    pid: u32,
}
```

This is already a stable JSON-over-stdio protocol. It works for classify/scan/test/new.
No changes needed to extend it for GPU commands.

---

## 5. The Real Milestone 1 Gap

The binary already handles plain text search, AST search, count, replace, context,
and all rg flags purely in Rust. **The native control plane for search is done.**

## 5. Milestone 1 Completion Status: CLOSED

All gaps identified in the original audit have been resolved:

### 5a. GPU sidecar routing — DONE (2f8f96b)

`--gpu-device-ids` added to both positional and search CLIs. When present,
the Rust binary sends a `gpu_search` command through the JSON-over-stdio
sidecar protocol. The Python sidecar constructs a Pipeline with explicit
GPU device IDs and dispatches to cuDF/Torch backends. Fails loudly with
ConfigurationError when GPU backends are unavailable.

### 5b. Pipeline.py eager imports — CLOSED (no change needed)

Evaluated and rejected. Making CPUBackend, CuDFBackend, and MemoryManager
lazy would require rewriting ~40 `@patch` decorators across test_pipeline.py
and test_multi_gpu_distribution.py. The import cost (~5ms) is negligible
compared to Python interpreter startup (~50ms+). The native binary does not
enter Python at all for text search, so the sidecar path that does enter
Python always needs Pipeline anyway.

### 5c. Benchmark scripts measuring native binary — DONE (d8ce671, 831e765, f3e759b, 8ded52d)

- `run_benchmarks.py` now invokes `tg.exe` directly (was `python -m`)
- `run_ast_workflow_benchmarks.py` routes `run` through native binary,
  `scan`/`test` through sidecar (matching actual backend ownership)
- Baselines reset to native binary measurements
- `--output` flag added to match AGENTS.md contract

---

## 6. What Comes Next (product performance, not control-plane)

Milestone 1 is closed. The remaining work is performance and capability:

1. **Native AST speed vs sg** — tg run is real but still ~2.7x slower than sg
2. **Repeated-query hot-path recovery** — cache path numbers are below earlier accepted lines
3. **Editor/rewrite substrate** — after AST search is strong enough

---

## 7. What Does NOT Need to Change

- **main.rs routing** — correct, all search stays in Rust
- **Sidecar protocol** — stable, now also handles GPU dispatch
- **PyO3 in lib.rs** — needed for Python->Rust direction (cuDF Arrow bridge), not on hot path
- **backend_gpu.rs** — dead code from tg.exe perspective, harmless (only compiled into cdylib)
- **rg_passthrough.rs** — working correctly, resolves rg from multiple locations
- **CpuBackend** — working, used as fallback when rg unavailable
- **pipeline.py imports** — eager imports acceptable, no hot-path cost
