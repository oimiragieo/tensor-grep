# Rust Port Architecture & TDD Plan (v0.4.0)

## Objective
Migrate the `tensor-grep` orchestrator and primary search execution logic from Python to Rust to bypass interpreter startup latency (0ms vs 250ms). This will allow `tensor-grep` to beat `ripgrep` globally in all benchmarks, while dynamically calling out to embedded Python for semantic GPU (cuDF/CyBERT) workloads.

## The Architecture Shift
1. **Current:** `python main.py` -> `py_pipeline` -> (calls rust via PyO3, ripgrep via spawn, or cuDF via Python).
2. **Target:** `rust bin` -> `rust_pipeline` -> (calls raw `memchr`/`regex`, or calls Python/cuDF via `pyo3::Python::with_gil`).

## Test-Driven Development (TDD) Plan

### Phase 1: The Rust Bin Orchestrator
- **Test:** `cargo test` verifying CLI parsing using `clap` matches our old Typer definitions exactly.
- **Implement:** `rust_core/src/main.rs` binary entry point, `cli.rs` config struct.

### Phase 2: CPU Core Backends (Rust `regex` & `memchr`)
- **Test:** Write `tests/test_search.rs` verifying ripgrep parity exactly for `-c`, `-F`, `-v`, `-C` switches.
- **Implement:** Use the existing `rust_core` count logic and expand it with full regex lines extraction logic avoiding allocations.

### Phase 3: The Python GPU Bridge (`pyo3` embedded)
- **Test:** Verify `test_gpu_bridge.rs` can safely execute our existing `cuDFBackend` when heavy data is passed.
- **Implement:** Use `pyo3` in reverse. Instead of Python importing Rust as a library, the Rust binary embeds the Python interpreter and dynamically invokes our existing `cuDF/CyBERT` pipelines only when the GPU heuristic says so.

### Phase 4: Integration
- Build a Python wheel via Maturin that includes the standalone Rust binary or exposes it seamlessly.
- Run `benchmarks/run_benchmarks.py` to confirm the Rust binary beats ripgrep.
