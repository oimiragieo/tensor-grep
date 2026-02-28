pub mod backend_ast;
pub mod backend_cpu;
pub mod backend_gpu;
pub mod cli;
pub mod mmap_arrow;

use crate::backend_cpu::CpuBackend;
use arrow_array::Array;
use arrow_array::StringArray;
use mmap_arrow::create_arrow_string_array_from_mmap;
use pyo3::prelude::*;
use pyo3_arrow::error::PyArrowResult;
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
    let py_array = pyo3_arrow::PyArray::from_array_ref(Arc::new(string_array));

    // 3. Export to a Python Arrow object (returns a PyCapsule wrapping the C Data Interface)
    Ok(py_array.to_pyarrow(py)?.into())
}

/// Reads a file into a zero-copy Arrow StringArray and yields it in chunks (slices)
/// to prevent GPU Out-Of-Memory errors when the file exceeds available VRAM.
#[pyfunction]
#[pyo3(signature = (filepath, max_bytes))]
fn read_mmap_to_arrow_chunked(
    py: Python<'_>,
    filepath: &str,
    max_bytes: usize,
) -> PyArrowResult<Vec<PyObject>> {
    // 1. Map the entire file and build offsets (zero-copy)
    let string_array = py.allow_threads(|| {
        create_arrow_string_array_from_mmap(filepath)
            .map_err(|e| pyo3::exceptions::PyRuntimeError::new_err(format!("mmap failed: {}", e)))
    })?;

    let mut py_chunks = Vec::new();
    let total_len = string_array.len();
    let mut current_idx = 0;

    // 2. Slice the Arrow array based on the byte capacity
    // Slicing an Arrow array is zero-copy (it just adjusts the offset/length metadata)
    while current_idx < total_len {
        let mut slice_len = 0;
        let mut current_bytes = 0;

        // Find how many strings fit into `max_bytes`
        while current_idx + slice_len < total_len {
            // Note: In a highly optimized version, we could binary search the offsets
            // array directly instead of iterating string by string.
            let str_len = string_array.value_length(current_idx + slice_len) as usize;
            if current_bytes + str_len > max_bytes && slice_len > 0 {
                break; // Limit reached
            }
            current_bytes += str_len;
            slice_len += 1;
        }

        // Create a zero-copy slice of the StringArray
        let sliced_array =
            StringArray::from(string_array.slice(current_idx, slice_len).into_data());

        let py_array = pyo3_arrow::PyArray::from_array_ref(Arc::new(sliced_array));

        py_chunks.push(py_array.to_pyarrow(py)?.into());

        current_idx += slice_len;
    }

    Ok(py_chunks)
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
            .count_matches(pattern, path, ignore_case, fixed_strings, false)
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
    m.add_function(wrap_pyfunction!(read_mmap_to_arrow_chunked, m)?)?;
    Ok(())
}
