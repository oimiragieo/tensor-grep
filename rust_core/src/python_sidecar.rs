use crate::runtime_paths::{
    resolve_existing_relative_to_current_exe, resolve_explicit_file_override,
};
use serde::{Deserialize, Serialize};
use serde_json::Value;
use std::env;
use std::ffi::{OsStr, OsString};
use std::io::{self, Read, Write};
use std::path::{Path, PathBuf};
use std::process::{Child, Command, ExitStatus, Stdio};
use std::thread;
use std::time::{Duration, Instant};

const DEFAULT_SIDECAR_MODULE: &str = "tensor_grep.sidecar";
const DEFAULT_TENSOR_GREP_MODULE: &str = "tensor_grep";
const TG_SIDECAR_PYTHON_ENV: &str = "TG_SIDECAR_PYTHON";
const TG_SIDECAR_TIMEOUT_MS_ENV: &str = "TG_SIDECAR_TIMEOUT_MS";
const DEFAULT_SIDECAR_TIMEOUT_MS: u64 = 30_000;
const MAX_SOURCE_ROOT_ANCESTOR_DEPTH: usize = 4;

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

enum SidecarWaitOutcome {
    Exited(ExitStatus),
    TimedOut,
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
    configure_python_module_path(&mut child);
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
    let sidecar_timeout = resolve_sidecar_timeout();
    let request_bytes = serde_json::to_vec(&request).map_err(|err| SidecarError {
        exit_code: 1,
        message: format!("Failed to encode Python sidecar request: {err}"),
        stderr: String::new(),
    })?;

    let mut child = Command::new(&python);
    configure_python_module_path(&mut child);
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

    let wait_outcome = wait_for_sidecar_or_kill(&mut child, sidecar_timeout)?;

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

    if matches!(wait_outcome, SidecarWaitOutcome::TimedOut) {
        return Err(SidecarError {
            exit_code: 124,
            message: format!(
                "Python sidecar timed out after {} ms and was terminated",
                sidecar_timeout.as_millis()
            ),
            stderr: stderr_text,
        });
    }

    let status = match wait_outcome {
        SidecarWaitOutcome::Exited(status) => status,
        SidecarWaitOutcome::TimedOut => unreachable!("timed out sidecar handled above"),
    };
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

fn wait_for_sidecar_or_kill(
    child: &mut Child,
    timeout: Duration,
) -> Result<SidecarWaitOutcome, SidecarError> {
    let deadline = Instant::now() + timeout;
    loop {
        match child.try_wait() {
            Ok(Some(status)) => return Ok(SidecarWaitOutcome::Exited(status)),
            Ok(None) => {
                if Instant::now() >= deadline {
                    terminate_sidecar_process(child)?;
                    return Ok(SidecarWaitOutcome::TimedOut);
                }
                thread::sleep(Duration::from_millis(10));
            }
            Err(err) => {
                return Err(SidecarError {
                    exit_code: 1,
                    message: format!("Failed while waiting for Python sidecar: {err}"),
                    stderr: String::new(),
                });
            }
        }
    }
}

fn terminate_sidecar_process(child: &mut Child) -> Result<(), SidecarError> {
    if let Err(err) = child.kill() {
        if err.kind() != io::ErrorKind::InvalidInput {
            return Err(SidecarError {
                exit_code: 1,
                message: format!("Failed to terminate timed-out Python sidecar: {err}"),
                stderr: String::new(),
            });
        }
    }

    child.wait().map_err(|err| SidecarError {
        exit_code: 1,
        message: format!("Failed to reap timed-out Python sidecar: {err}"),
        stderr: String::new(),
    })?;

    Ok(())
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

fn configure_python_module_path(command: &mut Command) {
    let Some(source_root) = resolve_repo_source_root() else {
        return;
    };

    command.env(
        "PYTHONPATH",
        merged_pythonpath(&source_root, env::var_os("PYTHONPATH")),
    );
}

fn resolve_repo_source_root() -> Option<PathBuf> {
    let current_exe = env::current_exe().ok()?;
    resolve_repo_source_root_relative_to_exe(&current_exe)
}

fn resolve_repo_source_root_relative_to_exe(exe_path: &Path) -> Option<PathBuf> {
    let exe_dir = exe_path.parent()?;

    for base in exe_dir.ancestors().take(MAX_SOURCE_ROOT_ANCESTOR_DEPTH + 1) {
        let candidate = base.join("src");
        if candidate.join("tensor_grep").is_dir() {
            return Some(candidate);
        }
    }

    None
}

fn merged_pythonpath(source_root: &Path, existing: Option<OsString>) -> OsString {
    let mut paths = vec![source_root.to_path_buf()];

    if let Some(existing) = existing {
        for path in env::split_paths(&existing) {
            if path != source_root {
                paths.push(path);
            }
        }
    }

    env::join_paths(&paths).unwrap_or_else(|_| source_root.as_os_str().to_os_string())
}

fn resolve_sidecar_target() -> PythonLaunchTarget {
    if let Some(script) = env::var_os("TG_SIDECAR_SCRIPT") {
        return PythonLaunchTarget::Script(PathBuf::from(script));
    }

    let module =
        env::var("TG_SIDECAR_MODULE").unwrap_or_else(|_| DEFAULT_SIDECAR_MODULE.to_string());
    PythonLaunchTarget::Module(module)
}

fn resolve_sidecar_timeout() -> Duration {
    let timeout_ms = env::var(TG_SIDECAR_TIMEOUT_MS_ENV)
        .ok()
        .and_then(|raw| raw.parse::<u64>().ok())
        .filter(|value| *value > 0)
        .unwrap_or(DEFAULT_SIDECAR_TIMEOUT_MS);
    Duration::from_millis(timeout_ms)
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

#[cfg(test)]
mod tests {
    use super::{merged_pythonpath, resolve_repo_source_root_relative_to_exe};
    use std::env;
    use std::fs;
    use std::path::Path;
    use tempfile::tempdir;

    #[test]
    fn resolves_repo_source_root_relative_to_native_binary() {
        let dir = tempdir().unwrap();
        let repo_root = dir.path().join("repo");
        let exe_path = repo_root
            .join("rust_core")
            .join("target")
            .join("debug")
            .join(if cfg!(windows) { "tg.exe" } else { "tg" });
        let source_root = repo_root.join("src");
        let rust_source_root = repo_root.join("rust_core").join("src");

        fs::create_dir_all(exe_path.parent().unwrap()).unwrap();
        fs::create_dir_all(source_root.join("tensor_grep")).unwrap();
        fs::create_dir_all(&rust_source_root).unwrap();
        fs::write(&exe_path, b"binary").unwrap();

        let resolved = resolve_repo_source_root_relative_to_exe(&exe_path);
        assert_eq!(resolved.as_deref(), Some(source_root.as_path()));
    }

    #[test]
    fn merged_pythonpath_prepends_source_root_and_preserves_existing_entries() {
        let source_root = Path::new("repo").join("src");
        let existing = env::join_paths([Path::new("alpha"), Path::new("beta")]).unwrap();

        let merged = merged_pythonpath(&source_root, Some(existing));
        let paths = env::split_paths(&merged).collect::<Vec<_>>();

        assert_eq!(paths[0], source_root);
        assert_eq!(paths[1], Path::new("alpha"));
        assert_eq!(paths[2], Path::new("beta"));
    }

    #[test]
    fn merged_pythonpath_dedupes_existing_source_root() {
        let source_root = Path::new("repo").join("src");
        let existing =
            env::join_paths([source_root.clone(), Path::new("beta").to_path_buf()]).unwrap();

        let merged = merged_pythonpath(&source_root, Some(existing));
        let paths = env::split_paths(&merged).collect::<Vec<_>>();

        assert_eq!(paths[0], source_root);
        assert_eq!(paths[1], Path::new("beta"));
        assert_eq!(paths.len(), 2);
    }
}
