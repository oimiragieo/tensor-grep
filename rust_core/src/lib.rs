pub mod backend_ast;
pub mod backend_cpu;
// pub mod backend_gpu;
pub mod cli;

use pyo3::prelude::*;
use crate::backend_cpu::CpuBackend;

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
    fn search(&self, pattern: &str, path: &str, ignore_case: bool, fixed_strings: bool) -> PyResult<Vec<(usize, String)>> {
        let results = self.inner.search(pattern, path, ignore_case, fixed_strings).map_err(|e| {
            pyo3::exceptions::PyRuntimeError::new_err(format!("Rust search failed: {}", e))
        })?;
        Ok(results)
    }

    /// Fast-path count implementation that only returns the number of matches
    fn count_matches(&self, pattern: &str, path: &str, ignore_case: bool, fixed_strings: bool) -> PyResult<usize> {
        let count = self.inner.count_matches(pattern, path, ignore_case, fixed_strings).map_err(|e| {
            pyo3::exceptions::PyRuntimeError::new_err(format!("Rust count failed: {}", e))
        })?;
        Ok(count)
    }
}

/// A Python module implemented in Rust.
#[pymodule]
fn rust_core(m: &Bound<'_, PyModule>) -> PyResult<()> {
    m.add_class::<RustBackend>()?;
    Ok(())
}
