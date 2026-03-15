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

What's actually missing for full Milestone 1:

### 5a. GPU sidecar routing (the real gap)

The `tg.exe` binary has no path to invoke GPU search. The GPU backends
(cuDF, Torch, cyBERT) only exist in the Python code. To complete Milestone 1:

- Add `--gpu` / `--gpu-device-ids` flags to `tg.exe` CLI
- Route GPU requests through the sidecar protocol (already exists)
- The sidecar spawns Python, which imports only GPU backends (not all backends)
- Sidecar returns match results as JSON

### 5b. Pipeline.py eager imports (Python-side optimization)

When the Python CLI path is entered (full CLI mode), `pipeline.py` eagerly imports
6 backends including CuDFBackend and RustCoreBackend. This is only relevant when
someone invokes `python -m tensor_grep` instead of `tg.exe`, but it matters for:

- Benchmark scripts that call through Python
- Users who haven't switched to the native binary yet

Fix: Make pipeline.py imports lazy (import inside routing branches).

### 5c. Benchmark scripts should use tg.exe directly

Current `run_benchmarks.py` already invokes `tg.exe` and `rg.exe` via subprocess.
This is correct. But some older scripts or test paths may still go through the
Python entry point. Ensure all benchmark paths measure the native binary.

---

## 6. Recommended Next Steps (in order)

1. **Add GPU sidecar routing to tg.exe** — `--gpu-device-ids` flag on positional
   and search commands, dispatch through existing sidecar protocol
2. **Make pipeline.py imports lazy** — so the Python-side path (when used) doesn't
   pay for backends it won't use
3. **Add a cold-start benchmark gate** — measure `tg.exe` startup time directly,
   not through Python
4. **Verify all benchmark scripts use native binary** — audit `run_benchmarks.py`,
   `run_hot_query_benchmarks.py`, etc. to confirm they invoke `tg.exe` not `python -m`

---

## 7. What Does NOT Need to Change

- **main.rs routing** — already correct, all search stays in Rust
- **Sidecar protocol** — already stable and working
- **PyO3 in lib.rs** — needed for Python→Rust direction (cuDF Arrow bridge), not on hot path
- **backend_gpu.rs** — dead code from tg.exe perspective, but harmless (only compiled into cdylib)
- **rg_passthrough.rs** — working correctly, resolves rg from multiple locations
- **CpuBackend** — working, used as fallback when rg unavailable
