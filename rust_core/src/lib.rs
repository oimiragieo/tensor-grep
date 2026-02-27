pub mod backend_ast;
pub mod backend_cpu;
// pub mod backend_gpu;
pub mod cli;
pub mod mmap_arrow;

use crate::backend_cpu::CpuBackend;
use mmap_arrow::create_arrow_string_array_from_mmap;
use pyo3::prelude::*;
use pyo3_arrow::error::PyArrowResult;
use pyo3_arrow::PyArray;
use std::sync::Arc;

/// Reads a file into a zero-copy Arrow StringArray and exports it as a PyCapsule
#[pyfunction]
fn read_mmap_to_arrow(py: Python<'_>, filepath: &str) -> PyArrowResult<PyObject> {
    // 1. Release the GIL while we map the file and scan for newlines
    let string_array = py.allow_threads(|| {
        create_arrow_string_array_from_mmap(filepath)
            .map_err(|e| pyo3::exceptions::PyRuntimeError::new_err(format!("mmap failed: {}", e)))
    })?;

    // 2. Wrap the zero-copy StringArray into a PyO3-Arrow PyArray
    let py_array = PyArray::new(
        Arc::new(string_array),
        py.get_type::<pyo3::types::PyCapsule>(),
    );

    // 3. Export to a Python Arrow object (returns a PyCapsule wrapping the C Data Interface)
    py_array.to_pyarrow(py)
}

/// Python bindings for the Rust CPU backend
#[pyclass]
pub struct RustBackend {
    inner: CpuBackend,
}

#[pymethods]
impl RustBackend {
    #[new]
    fn new() -> Self {
        RustBackend {
            inner: CpuBackend::new(),
        }
    }

    /// Search a file or directory using the Rust memmap2 backend
    fn search(
        &self,
        pattern: &str,
        path: &str,
        ignore_case: bool,
        fixed_strings: bool,
    ) -> PyResult<Vec<(usize, String)>> {
        let results = self
            .inner
            .search(pattern, path, ignore_case, fixed_strings)
            .map_err(|e| {
                pyo3::exceptions::PyRuntimeError::new_err(format!("Rust search failed: {}", e))
            })?;
        Ok(results)
    }

    /// Fast-path count implementation that only returns the number of matches
    fn count_matches(
        &self,
        pattern: &str,
        path: &str,
        ignore_case: bool,
        fixed_strings: bool,
    ) -> PyResult<usize> {
        let count = self
            .inner
            .count_matches(pattern, path, ignore_case, fixed_strings)
            .map_err(|e| {
                pyo3::exceptions::PyRuntimeError::new_err(format!("Rust count failed: {}", e))
            })?;
        Ok(count)
    }
}

/// A Python module implemented in Rust.
#[pymodule]
fn rust_core(m: &Bound<'_, PyModule>) -> PyResult<()> {
    m.add_class::<RustBackend>()?;
    m.add_function(wrap_pyfunction!(read_mmap_to_arrow, m)?)?;
    Ok(())
}

