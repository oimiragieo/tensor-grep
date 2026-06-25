#![allow(clippy::useless_conversion)]

pub mod backend_ast;
pub mod backend_ast_workflow;
pub mod backend_cpu;
pub mod backend_gpu;
pub mod cli;
pub mod crossover;
pub mod editor_plane;
#[cfg(feature = "cuda")]
pub mod gpu_native;
pub mod index;
pub mod mmap_arrow;
pub mod native_search;
pub mod python_sidecar;
pub mod rg_passthrough;
pub mod routing;
pub mod runtime_paths;

use crate::backend_ast::AstBackend;
use crate::backend_cpu::CpuBackend;
use arrow_array::Array;
use arrow_array::StringArray;
use mmap_arrow::create_arrow_string_array_from_mmap;
use pyo3::prelude::*;
use std::sync::Arc;

/// Reads a file into a zero-copy Arrow StringArray and exports it as a PyCapsule
#[pyfunction]
fn read_mmap_to_arrow(py: Python<'_>, filepath: &str) -> PyResult<Py<PyAny>> {
    // 1. Release the GIL while we map the file and scan for newlines
    let string_array = py.detach(|| {
        create_arrow_string_array_from_mmap(filepath)
            .map_err(|e| pyo3::exceptions::PyRuntimeError::new_err(format!("mmap failed: {}", e)))
    })?;

    // 2. Wrap the zero-copy StringArray into a PyO3-Arrow PyArray
    let py_array = pyo3_arrow::PyArray::from_array_ref(Arc::new(string_array));

    // 3. Export to a Python Arrow object (returns a PyCapsule wrapping the C Data Interface)
    let exported = py_array.to_pyarrow(py)?;
    Ok(exported.unbind())
}

/// Reads a file into a zero-copy Arrow StringArray and yields it in chunks (slices)
/// to prevent GPU Out-Of-Memory errors when the file exceeds available VRAM.
#[pyfunction]
#[pyo3(signature = (filepath, max_bytes))]
fn read_mmap_to_arrow_chunked(
    py: Python<'_>,
    filepath: &str,
    max_bytes: usize,
) -> PyResult<Vec<Py<PyAny>>> {
    // 1. Map the entire file and build offsets (zero-copy)
    let string_array = py.detach(|| {
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

        let exported = py_array.to_pyarrow(py)?;
        py_chunks.push(exported.unbind());

        current_idx += slice_len;
    }

    Ok(py_chunks)
}

fn rewrite_error(err: impl std::fmt::Display) -> PyErr {
    pyo3::exceptions::PyRuntimeError::new_err(format!("AST rewrite failed: {err}"))
}

/// Return native AST rewrite plan JSON through the PyO3 wheel extension.
#[pyfunction]
fn ast_rewrite_plan_json(
    pattern: &str,
    replacement: &str,
    lang: &str,
    path: &str,
) -> PyResult<String> {
    let backend = AstBackend::new();
    let plan = backend
        .plan_rewrites(pattern, replacement, lang, path)
        .map_err(rewrite_error)?;
    serde_json::to_string_pretty(&plan).map_err(rewrite_error)
}

/// Apply native AST rewrites through the PyO3 wheel extension.
#[pyfunction]
fn ast_rewrite_apply_json(
    pattern: &str,
    replacement: &str,
    lang: &str,
    path: &str,
) -> PyResult<String> {
    let backend = AstBackend::new();
    let plan = backend
        .plan_and_apply(pattern, replacement, lang, path)
        .map_err(rewrite_error)?;
    let payload = serde_json::json!({
        "version": plan.version,
        "routing_backend": plan.routing_backend,
        "routing_reason": plan.routing_reason,
        "sidecar_used": plan.sidecar_used,
        "checkpoint": null,
        "audit_manifest": null,
        "plan": &plan,
        "verification": null,
        "validation": null,
    });
    serde_json::to_string_pretty(&payload).map_err(rewrite_error)
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
    #[allow(clippy::useless_conversion)]
    fn search(
        &self,
        pattern: &str,
        path: &str,
        ignore_case: bool,
        fixed_strings: bool,
        invert_match: bool,
    ) -> PyResult<Vec<(usize, String)>> {
        self.inner
            .search(pattern, path, ignore_case, fixed_strings, invert_match)
            .map_err(|e| {
                pyo3::exceptions::PyRuntimeError::new_err(format!("Rust search failed: {}", e))
            })
    }

    /// Fast-path count implementation that only returns the number of matches
    #[allow(clippy::useless_conversion)]
    fn count_matches(
        &self,
        pattern: &str,
        path: &str,
        ignore_case: bool,
        fixed_strings: bool,
    ) -> PyResult<usize> {
        self.inner
            .count_matches(pattern, path, ignore_case, fixed_strings, false)
            .map_err(|e| {
                pyo3::exceptions::PyRuntimeError::new_err(format!("Rust count failed: {}", e))
            })
    }

    /// Passthrough to the system ripgrep binary with full flag support
    #[allow(clippy::too_many_arguments)]
    fn execute_ripgrep(
        &self,
        patterns: Vec<String>,
        path: String,
        ignore_case: bool,
        fixed_strings: bool,
        invert_match: bool,
        count: bool,
        count_matches: bool,
        line_number: bool,
        column: bool,
        only_matching: bool,
        context: Option<usize>,
        before_context: Option<usize>,
        after_context: Option<usize>,
        max_count: Option<usize>,
        word_regexp: bool,
        smart_case: bool,
        globs: Vec<String>,
        no_ignore: bool,
        no_ignore_dot: bool,
        no_ignore_exclude: bool,
        no_ignore_files: bool,
        no_ignore_global: bool,
        no_ignore_parent: bool,
        no_ignore_vcs: bool,
        hidden: bool,
        follow: bool,
        text: bool,
        files_with_matches: bool,
        files_without_match: bool,
        file_types: Vec<String>,
        color: Option<String>,
        replace: Option<String>,
        passthru: bool,
        no_config: bool,
        pcre2: bool,
        max_filesize: Option<String>,
    ) -> PyResult<i32> {
        use crate::rg_passthrough::{execute_ripgrep_search, RipgrepSearchArgs};
        let args = RipgrepSearchArgs {
            files: false,
            json: false,
            ignore_case,
            fixed_strings,
            no_fixed_strings: false,
            invert_match,
            no_invert_match: false,
            count,
            count_matches,
            line_number,
            no_line_number: false,
            column,
            only_matching,
            context,
            before_context,
            after_context,
            max_count,
            word_regexp,
            smart_case,
            globs,
            ignore: false,
            no_ignore,
            no_ignore_dot,
            no_ignore_exclude,
            no_ignore_files,
            no_ignore_global,
            no_ignore_parent,
            no_ignore_vcs,
            require_git: false,
            hidden,
            no_hidden: false,
            follow,
            text,
            files_with_matches,
            files_without_match,
            file_types,
            color,
            path_separator: None,
            replace,
            vimgrep: false,
            passthru,
            no_config,
            sort: None,
            sort_reverse: None,
            sort_files: false,
            max_depth: None,
            null: false,
            null_data: false,
            multiline: false,
            no_multiline: false,
            multiline_dotall: false,
            no_multiline_dotall: false,
            patterns,
            paths: vec![path],
            pcre2,
            no_pcre2: false,
            pcre2_unicode: false,
            no_pcre2_unicode: false,
            no_crlf: false,
            no_encoding: false,
            no_mmap: false,
            no_pre: false,
            no_search_zip: false,
            auto_hybrid_regex: false,
            no_auto_hybrid_regex: false,
            unicode: false,
            no_text: false,
            no_binary: false,
            no_follow: false,
            no_glob_case_insensitive: false,
            no_ignore_file_case_insensitive: false,
            ignore_dot: false,
            ignore_exclude: false,
            ignore_files: false,
            ignore_global: false,
            ignore_messages: false,
            ignore_parent: false,
            ignore_vcs: false,
            no_one_file_system: false,
            no_block_buffered: false,
            no_byte_offset: false,
            no_column: false,
            no_context_separator: false,
            no_include_zero: false,
            no_line_buffered: false,
            no_max_columns_preview: false,
            no_trim: false,
            no_json: false,
            messages: false,
            no_stats: false,
            max_filesize,
        };
        execute_ripgrep_search(&args).map_err(|e| {
            pyo3::exceptions::PyRuntimeError::new_err(format!("Ripgrep passthrough failed: {}", e))
        })
    }
}

/// A Python module implemented in Rust.
// gil_used pinned `true` (conservative). The free-threaded build (gil_used = false, #266) broke
// Linux `agent-readiness` in CI — that PR's run was cancelled by a force-push, so it merged
// unverified. Re-enable free-threading only behind a full green CI run (Linux extension load).
#[pymodule(gil_used = true)]
fn rust_core(m: &Bound<'_, PyModule>) -> PyResult<()> {
    m.add_class::<RustBackend>()?;
    m.add_function(wrap_pyfunction!(read_mmap_to_arrow, m)?)?;
    m.add_function(wrap_pyfunction!(read_mmap_to_arrow_chunked, m)?)?;
    m.add_function(wrap_pyfunction!(ast_rewrite_plan_json, m)?)?;
    m.add_function(wrap_pyfunction!(ast_rewrite_apply_json, m)?)?;
    Ok(())
}
