use pyo3::prelude::*;
use pyo3::types::PyDict;

// Struct mirrored from main binary so the library can use it
pub struct CliFlags {
    pub count: bool,
    pub fixed_strings: bool,
    pub invert_match: bool,
    pub ignore_case: bool,
}

/// Evaluates if the current system has an NVIDIA GPU available and capable of cuDF acceleration
pub fn should_use_gpu_pipeline() -> bool {
    Python::with_gil(|py| -> PyResult<bool> {
        // Attempt to import tensor_grep's existing device detector
        let sys = py.import("sys")?;
        let _path = sys.getattr("path")?;

        let _builtins = py.import("builtins")?;

        // Let's try importing tensor_grep
        let tg_module = match py.import("tensor_grep.core.hardware.device_detect") {
            Ok(m) => m,
            Err(_) => return Ok(false), // Not installed in python environment
        };

        let detector_class = tg_module.getattr("DeviceDetector")?;
        let detector = detector_class.call0()?;

        let has_gpu: bool = detector.call_method0("has_gpu")?.extract()?;
        Ok(has_gpu)
    })
    .unwrap_or(false)
}

/// Fallback mechanism to invoke specific Python Typer subcommands directly from Rust
pub fn execute_python_module_fallback(command: &str, args: Vec<String>) -> anyhow::Result<()> {
    Python::with_gil(|py| -> PyResult<()> {
        let sys = py.import("sys")?;

        // Emulate sys.argv for the Typer entrypoint
        let mut sys_argv = vec!["tg".to_string(), command.to_string()];
        sys_argv.extend(args);
        sys.setattr("argv", sys_argv)?;

        let main_module = py.import("tensor_grep.cli.main")?;
        main_module.call_method0("main_entry")?;

        Ok(())
    })
    .map_err(|e| anyhow::anyhow!("Subcommand execution failed: {}", e))
}

/// Executes the cuDF Python Pipeline dynamically from Rust!
pub fn execute_gpu_pipeline(pattern: &str, path: &str, config: &CliFlags) -> anyhow::Result<()> {
    Python::with_gil(|py| -> PyResult<()> {
        let pipeline_module = py.import("tensor_grep.core.pipeline")?;
        let pipeline_class = pipeline_module.getattr("Pipeline")?;

        // Import config
        let config_module = py.import("tensor_grep.core.config")?;
        let config_class = config_module.getattr("SearchConfig")?;

        // kwargs for config
        let kwargs = PyDict::new(py);
        kwargs.set_item("count", config.count)?;
        kwargs.set_item("fixed_strings", config.fixed_strings)?;
        kwargs.set_item("invert_match", config.invert_match)?;
        kwargs.set_item("ignore_case", config.ignore_case)?;

        let search_config = config_class.call((), Some(&kwargs))?;

        // kwargs for pipeline
        let pipe_kwargs = PyDict::new(py);
        pipe_kwargs.set_item("force_cpu", false)?;
        pipe_kwargs.set_item("config", search_config.clone())?;

        let pipeline = pipeline_class.call((), Some(&pipe_kwargs))?;
        let backend = pipeline.call_method0("get_backend")?;

        // Execute Search
        let result = backend.call_method1("search", (path, pattern, search_config))?;

        if config.count {
            let matches: usize = result.getattr("total_matches")?.extract()?;
            println!("{}", matches);
        } else {
            let total_matches: usize = result.getattr("total_matches")?.extract()?;
            println!("Found {} matches via GPU.", total_matches);
        }

        Ok(())
    })
    .map_err(|e| anyhow::anyhow!("Python execution failed: {}", e))
}
