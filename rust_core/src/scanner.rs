use pyo3::prelude::*;
use ignore::WalkBuilder;
use std::path::Path;

#[pyclass]
pub struct RustDirectoryScanner {
    hidden: bool,
    max_depth: Option<usize>,
}

#[pymethods]
impl RustDirectoryScanner {
    #[new]
    pub fn new(hidden: bool, max_depth: Option<usize>) -> Self {
        Self {
            hidden,
            max_depth,
        }
    }

    pub fn walk(&self, path_str: &str) -> PyResult<Vec<String>> {
        let mut builder = WalkBuilder::new(path_str);
        
        // Match the Python logic + add gitignore superpowers natively
        builder.hidden(!self.hidden);
        builder.max_depth(self.max_depth);
        builder.git_ignore(true);
        builder.ignore(true);

        let mut files = Vec::new();

        for result in builder.build() {
            match result {
                Ok(entry) => {
                    if entry.file_type().map_or(false, |ft| ft.is_file()) {
                        if let Some(path_str) = entry.path().to_str() {
                            files.push(path_str.to_string());
                        }
                    }
                }
                Err(_) => continue, // Skip unreadable paths silently like standard ripgrep
            }
        }

        Ok(files)
    }
}
