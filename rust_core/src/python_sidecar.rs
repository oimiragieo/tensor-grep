use crate::runtime_paths::{
    resolve_existing_relative_to_current_exe, resolve_explicit_file_override,
};
use serde::{Deserialize, Serialize};
use serde_json::Value;
use std::env;
use std::ffi::{OsStr, OsString};
use std::io::{self, Read, Write};
use std::path::PathBuf;
use std::process::{Command, Stdio};
use std::thread;

const DEFAULT_SIDECAR_MODULE: &str = "tensor_grep.sidecar";
const DEFAULT_TENSOR_GREP_MODULE: &str = "tensor_grep";
const TG_SIDECAR_PYTHON_ENV: &str = "TG_SIDECAR_PYTHON";

#[derive(Debug, Clone, Serialize)]
pub struct SidecarRequest {
    pub command: String,
    pub args: Vec<String>,
    pub payload: Option<Value>,
}

#[derive(Debug, Clone)]
pub struct SidecarCommandResult {
    pub exit_code: i32,
    pub stdout: String,
    pub stderr: String,
    pub sidecar_pid: u32,
}

#[derive(Debug, Clone)]
pub struct SidecarError {
    pub exit_code: i32,
    pub message: String,
    pub stderr: String,
}

#[derive(Debug, Deserialize)]
struct SidecarResponse {
    exit_code: i32,
    stdout: String,
    stderr: String,
    pid: u32,
}

enum PythonLaunchTarget {
    Module(String),
    Script(PathBuf),
}

pub fn execute_sidecar_command(
    command: &str,
    args: Vec<String>,
    payload: Option<Value>,
) -> Result<SidecarCommandResult, SidecarError> {
    invoke_sidecar(SidecarRequest {
        command: command.to_string(),
        args,
        payload,
    })
}

pub fn execute_python_passthrough_command(
    command: &str,
    args: Vec<String>,
) -> Result<i32, SidecarError> {
    let python = resolve_python_command();

    let mut child = Command::new(&python);
    child
        .arg("-m")
        .arg(DEFAULT_TENSOR_GREP_MODULE)
        .arg(command)
        .args(args)
        .stdin(Stdio::inherit())
        .stdout(Stdio::inherit())
        .stderr(Stdio::inherit());

    let status = child
        .status()
        .map_err(|err| map_python_spawn_error(&python, err))?;

    Ok(status.code().unwrap_or(1))
}

pub fn invoke_sidecar(request: SidecarRequest) -> Result<SidecarCommandResult, SidecarError> {
    let python = resolve_python_command();
    let launch_target = resolve_sidecar_target();
    let request_bytes = serde_json::to_vec(&request).map_err(|err| SidecarError {
        exit_code: 1,
        message: format!("Failed to encode Python sidecar request: {err}"),
        stderr: String::new(),
    })?;

    let mut child = Command::new(&python);
    match launch_target {
        PythonLaunchTarget::Module(module) => {
            child.arg("-m").arg(module);
        }
        PythonLaunchTarget::Script(script) => {
            child.arg(script);
        }
    }

    child
        .stdin(Stdio::piped())
        .stdout(Stdio::piped())
        .stderr(Stdio::piped());

    let mut child = child
        .spawn()
        .map_err(|err| map_python_spawn_error(&python, err))?;

    let mut stdin = child.stdin.take().ok_or_else(|| SidecarError {
        exit_code: 1,
        message: "Python sidecar stdin pipe was unavailable".to_string(),
        stderr: String::new(),
    })?;
    let stdout = child.stdout.take().ok_or_else(|| SidecarError {
        exit_code: 1,
        message: "Python sidecar stdout pipe was unavailable".to_string(),
        stderr: String::new(),
    })?;
    let stderr = child.stderr.take().ok_or_else(|| SidecarError {
        exit_code: 1,
        message: "Python sidecar stderr pipe was unavailable".to_string(),
        stderr: String::new(),
    })?;

    let writer = thread::spawn(move || -> io::Result<()> {
        stdin.write_all(&request_bytes)?;
        stdin.flush()?;
        drop(stdin);
        Ok(())
    });
    let stdout_reader = read_all_thread(stdout);
    let stderr_reader = read_all_thread(stderr);

    let status = child.wait().map_err(|err| SidecarError {
        exit_code: 1,
        message: format!("Failed while waiting for Python sidecar: {err}"),
        stderr: String::new(),
    })?;

    let write_result = writer.join().map_err(|_| SidecarError {
        exit_code: 1,
        message: "Python sidecar request writer thread panicked".to_string(),
        stderr: String::new(),
    })?;
    let stdout_bytes = stdout_reader.join().map_err(|_| SidecarError {
        exit_code: 1,
        message: "Python sidecar stdout reader thread panicked".to_string(),
        stderr: String::new(),
    })?;
    let stderr_bytes = stderr_reader.join().map_err(|_| SidecarError {
        exit_code: 1,
        message: "Python sidecar stderr reader thread panicked".to_string(),
        stderr: String::new(),
    })?;

    let stderr_text = bytes_to_string(stderr_bytes.map_err(|err| SidecarError {
        exit_code: 1,
        message: format!("Failed to read Python sidecar stderr: {err}"),
        stderr: String::new(),
    })?);

    if let Err(err) = write_result {
        if err.kind() != io::ErrorKind::BrokenPipe {
            return Err(SidecarError {
                exit_code: 1,
                message: format!("Failed to send request to Python sidecar: {err}"),
                stderr: stderr_text,
            });
        }
    }

    let stdout_bytes = stdout_bytes.map_err(|err| SidecarError {
        exit_code: 1,
        message: format!("Failed to read Python sidecar stdout: {err}"),
        stderr: stderr_text.clone(),
    })?;

    let child_exit_code = status.code().unwrap_or(1);
    let response: SidecarResponse = serde_json::from_slice(&stdout_bytes).map_err(|err| {
        let mut message = format!("Python sidecar returned invalid JSON: {err}");
        if child_exit_code != 0 {
            message = format!(
                "Python sidecar exited with code {child_exit_code} before returning valid JSON: {err}"
            );
        }

        SidecarError {
            exit_code: child_exit_code.max(1),
            message,
            stderr: stderr_text.clone(),
        }
    })?;

    if child_exit_code != 0 {
        return Err(SidecarError {
            exit_code: child_exit_code.max(1),
            message: format!("Python sidecar exited with code {child_exit_code}"),
            stderr: merge_stderr(&stderr_text, &response.stderr),
        });
    }

    Ok(SidecarCommandResult {
        exit_code: response.exit_code,
        stdout: response.stdout,
        stderr: response.stderr,
        sidecar_pid: response.pid,
    })
}

fn read_all_thread<R>(mut reader: R) -> thread::JoinHandle<io::Result<Vec<u8>>>
where
    R: Read + Send + 'static,
{
    thread::spawn(move || {
        let mut bytes = Vec::new();
        reader.read_to_end(&mut bytes)?;
        Ok(bytes)
    })
}

fn bytes_to_string(bytes: Vec<u8>) -> String {
    String::from_utf8_lossy(&bytes).into_owned()
}

fn merge_stderr(process_stderr: &str, response_stderr: &str) -> String {
    match (process_stderr.is_empty(), response_stderr.is_empty()) {
        (true, true) => String::new(),
        (false, true) => process_stderr.to_string(),
        (true, false) => response_stderr.to_string(),
        (false, false) => format!("{process_stderr}{response_stderr}"),
    }
}

fn resolve_python_command() -> OsString {
    if let Some(explicit) = resolve_explicit_file_override(TG_SIDECAR_PYTHON_ENV) {
        return explicit.into_os_string();
    }

    if let Some(runtime_relative_python) = resolve_existing_relative_to_current_exe(&[
        &[if cfg!(windows) {
            "python.exe"
        } else {
            "python"
        }],
        &[
            ".venv",
            if cfg!(windows) { "Scripts" } else { "bin" },
            if cfg!(windows) {
                "python.exe"
            } else {
                "python"
            },
        ],
    ]) {
        return runtime_relative_python.into_os_string();
    }

    OsString::from("python")
}

fn resolve_sidecar_target() -> PythonLaunchTarget {
    if let Some(script) = env::var_os("TG_SIDECAR_SCRIPT") {
        return PythonLaunchTarget::Script(PathBuf::from(script));
    }

    let module =
        env::var("TG_SIDECAR_MODULE").unwrap_or_else(|_| DEFAULT_SIDECAR_MODULE.to_string());
    PythonLaunchTarget::Module(module)
}

fn map_python_spawn_error(python: &OsStr, err: io::Error) -> SidecarError {
    if err.kind() == io::ErrorKind::NotFound {
        return SidecarError {
            exit_code: 2,
            message: format!(
                "Python sidecar not found. Tried `{}`. Set {} to an interpreter path or create `.venv` with `uv pip install -e \".[dev,ast,nlp]\"`.",
                python.to_string_lossy(),
                TG_SIDECAR_PYTHON_ENV
            ),
            stderr: String::new(),
        };
    }

    SidecarError {
        exit_code: 1,
        message: format!(
            "Failed to start Python sidecar with `{}`: {err}",
            python.to_string_lossy()
        ),
        stderr: String::new(),
    }
}
