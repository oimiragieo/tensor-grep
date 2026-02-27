# Phase 1: Preparation & Rust Refactoring (TDD Baseline)
- [ ] Move `tensor-grep-rs` contents into `tensor-grep/rust_core/` (or similar).
- [ ] Update `Cargo.toml` to include `pyo3` and configure `crate-type = ["cdylib", "rlib"]` for Python extension building, while maintaining `lib.rs` architecture for native Rust unit testing.
- [ ] Ensure existing Rust `cargo test` suite passes natively before touching Python.

# Phase 2: PyO3 Bindings (Rust-Side TDD)
- [ ] Write a Rust unit test that validates the `CpuBackend` can successfully search a dummy file.
- [ ] Implement the `#[pyclass]` and `#[pymethods]` macros around `CpuBackend` inside a new `src/lib.rs` bindings file.
- [ ] Expose the `search` function to Python. Ensure it accepts Python strings and paths.

# Phase 3: Maturin Integration & Python Unit Tests (Python-Side TDD)
- [ ] Update `pyproject.toml` or create a Maturin build configuration to compile the Rust extension.
- [ ] Run `maturin develop --release` to build the Python wheel and install it into the `uv` virtual environment.
- [ ] Create a new pytest file: `tests/unit/test_rust_core.py`.
- [ ] Write a failing pytest that attempts to `import tensor_grep.rust_core` and execute `CpuBackend.search()`.
- [ ] Validate the test passes using the compiled PyO3 extension.

# Phase 4: Integration into the Router (End-to-End TDD)
- [ ] Update `tests/integration/test_pipeline.py` to assert that pure string queries are routed to the `RustBackend`.
- [ ] Update `src/tensor_grep/core/pipeline.py` to instantiate the PyO3 `RustBackend` (replacing the old `CPUBackend` Python fallback).
- [ ] Run the full `pytest` suite (87+ tests) to ensure 100% parity with the previous Python regex behavior.
- [ ] Run the E2E characterization benchmarks to verify the `0.21s` speed is maintained through the Python CLI.
