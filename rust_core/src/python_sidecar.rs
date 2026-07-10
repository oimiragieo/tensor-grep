use crate::runtime_paths::{resolve_existing_relative_to_exe, resolve_explicit_file_override};
use serde::{Deserialize, Serialize};
use serde_json::Value;
use std::env;
use std::ffi::{OsStr, OsString};
use std::fs;
use std::io::{self, Read, Write};
#[cfg(unix)]
use std::os::unix::process::CommandExt;
use std::path::{Path, PathBuf};
use std::process::{Child, Command, ExitStatus, Stdio};
use std::thread;
use std::time::{Duration, Instant};

const DEFAULT_SIDECAR_MODULE: &str = "tensor_grep.sidecar";
const DEFAULT_TENSOR_GREP_MODULE: &str = "tensor_grep";
const TG_SIDECAR_PYTHON_ENV: &str = "TG_SIDECAR_PYTHON";
const TG_SIDECAR_TIMEOUT_MS_ENV: &str = "TG_SIDECAR_TIMEOUT_MS";
const TG_NATIVE_TG_BINARY_ENV: &str = "TG_NATIVE_TG_BINARY";
const TG_HELP_PROBE_TIMEOUT_MS_ENV: &str = "TG_HELP_PROBE_TIMEOUT_MS";
// audit H5: execute_python_passthrough_command_inner used to call a raw, unbounded
// `child.wait()` -- a wedged parser/LSP/FS/pathological-regex on the Python side hung the
// ENTIRE `tg` invocation forever, with no recovery short of an external kill. This is the
// general one-shot passthrough dispatch target for ~35 subcommands (map/audit/scan/orient/
// session/doctor/checkpoint/defs/refs/callers/blast-radius/agent/context/rulesets/devices/
// lsp/etc. -- see run_command_cli in main.rs); it now shares the same bounded
// wait-or-kill machinery already proven for the sidecar (resolve_sidecar_timeout) and the
// --help probe (resolve_help_probe_timeout) paths below.
const TG_PASSTHROUGH_TIMEOUT_MS_ENV: &str = "TG_PASSTHROUGH_TIMEOUT_MS";
const DEFAULT_SIDECAR_TIMEOUT_MS: u64 = 30_000;
// audit #97 item 1: raised from 750ms -- measured `python -m tensor_grep --help` at ~500-550ms
// median even with a WARM filesystem cache on a fast dev box (see the fix commit's PR description
// for the raw measurements; rust_core/tests/test_sidecar_ipc.rs exercises the resulting behavior
// via test_help_probe_timeout_env_override_falls_back_fast_with_wedged_python and
// test_help_probe_default_timeout_recovers_with_enriched_fallback_when_python_is_wedged). 750ms
// left well under 250ms of slack, so a cold interpreter start, antivirus scanning a new process,
// or a loaded CI box could blow through it and silently swap the rich Typer help for the sparse
// clap fallback -- the bare `tg --help` instability this constant caused. 3000ms gives ~6x
// headroom over the measured warm-cache median while staying an order of magnitude below the
// general 30s sidecar timeout (a --help probe should still fail fast when Python is genuinely
// broken) and well under the 6s wall-clock budget the fallback-timeout test asserts.
const DEFAULT_HELP_PROBE_TIMEOUT_MS: u64 = 3_000;
// audit H5: a generous "safety ceiling, not typical case" wall-clock bound for the general
// one-shot Python passthrough dispatch. Mirrors the Python side's own precedent for a generic
// subprocess timeout -- TG_SUBPROCESS_TIMEOUT_SECONDS defaults to 600s for run_subprocess()
// (subprocess_policy.py:20-25), and the large-repo-scale-campaign numbers show every currently
// known one-shot command (map/audit/scan/callers/etc.), even on a large repo, completing in the
// tens of seconds, not minutes. 600_000ms gives wide headroom above any legitimate case while
// still guaranteeing eventual recovery from a truly-infinite hang instead of hanging forever.
const DEFAULT_PASSTHROUGH_TIMEOUT_MS: u64 = 600_000;
const MAX_SOURCE_ROOT_ANCESTOR_DEPTH: usize = 4;
const WINDOWS_EXE_BRIDGE_MARKER: &str = "tg.exe.tensor-grep-bridge";
const WINDOWS_EXE_BRIDGE_MARKER_CONTENT: &str = "tensor-grep managed tg.exe bridge";

// audit I4: cap captured child output to prevent a runaway child process from
// OOM-ing the parent. Sidecar responses are JSON blobs; passthrough captures are
// only used for the short --help probe. 64 MiB is a generous upper bound for
// either use-case; anything larger is treated as a protocol error.
const MAX_CAPTURED_OUTPUT_BYTES: u64 = 64 * 1024 * 1024;

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

#[derive(Debug, Clone)]
pub struct PythonPassthroughResult {
    pub exit_code: i32,
    pub stdout: String,
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
    execute_python_passthrough_command_inner(command, args, None)
}

pub fn execute_python_passthrough_command_with_stdin(
    command: &str,
    args: Vec<String>,
    stdin_bytes: Vec<u8>,
) -> Result<i32, SidecarError> {
    execute_python_passthrough_command_inner(command, args, Some(stdin_bytes))
}

fn execute_python_passthrough_command_inner(
    command: &str,
    args: Vec<String>,
    stdin_bytes: Option<Vec<u8>>,
) -> Result<i32, SidecarError> {
    let python = resolve_python_command();
    // audit H5: decide the exemption BEFORE `args` is moved into the child's argv builder
    // below -- see is_long_running_passthrough_command's doc comment for the exact,
    // explicit, auditable exemption list (mcp / session serve / lsp server mode).
    let is_daemon_launch = is_long_running_passthrough_command(command, &args);

    let mut child = command_for_executable(&python);
    configure_python_child_environment(&mut child);
    let pipe_stdin = stdin_bytes.is_some();
    child
        .arg("-m")
        .arg(DEFAULT_TENSOR_GREP_MODULE)
        .arg(command)
        .args(args)
        .stdin(if pipe_stdin {
            Stdio::piped()
        } else {
            Stdio::inherit()
        })
        .stdout(Stdio::inherit())
        .stderr(Stdio::inherit());

    // audit H5 (Opus gate must-fix): place the child in its own process group on Unix so
    // terminate_passthrough_process's killpg(child.id(), SIGKILL) actually signals the whole
    // subtree on timeout. WITHOUT this, the child inherits tg's PGID, no process group with
    // id == child.id() exists, killpg returns ESRCH (swallowed, and the POSIX branch has no
    // child.kill() fallback), the wedged child is never signaled, and terminate's own
    // child.wait() blocks forever -- i.e. the hang would just move from the old bare wait to
    // the new one on Linux/macOS. The setpgid closure is byte-identical to invoke_sidecar's
    // audit-I8 block above; only the `if !is_daemon_launch` gate differs.
    //
    // The gate is deliberate, not incidental: only the bounded path is ever killpg'd, so only
    // it needs its own group. The daemon-exempt launches (mcp / session serve / lsp) take the
    // unbounded wait below (never killpg'd) AND read an inherited stdin, so leaving them in
    // tg's process group preserves today's TTY behavior -- a child in a NEW background process
    // group that reads the controlling terminal would take SIGTTIN and stop. The bounded
    // commands that reach here do not read a TTY stdin (analysis commands take args; the only
    // stdin-piping caller, `run`, uses Stdio::piped(), which is not a TTY), so this is
    // SIGTTIN-safe by construction. The `#[cfg(unix)] unsafe { child.pre_exec(...) }` wrapper
    // is byte-identical to invoke_sidecar's audit-I8 block above; only the inner
    // `if !is_daemon_launch` guard is added.
    #[cfg(unix)]
    unsafe {
        if !is_daemon_launch {
            child.pre_exec(|| {
                if libc::setpgid(0, 0) != 0 {
                    return Err(io::Error::last_os_error());
                }
                Ok(())
            });
        }
    }

    let mut child = child
        .spawn()
        .map_err(|err| map_python_spawn_error(&python, err))?;

    if let Some(stdin_bytes) = stdin_bytes {
        let mut stdin = child.stdin.take().ok_or_else(|| SidecarError {
            exit_code: 1,
            message: "Python passthrough stdin pipe was unavailable".to_string(),
            stderr: String::new(),
        })?;
        if let Err(err) = stdin.write_all(&stdin_bytes) {
            if err.kind() != io::ErrorKind::BrokenPipe {
                let _ = child.kill();
                let _ = child.wait();
                return Err(SidecarError {
                    exit_code: 1,
                    message: format!("Failed to forward stdin to Python passthrough: {err}"),
                    stderr: String::new(),
                });
            }
        }
        let _ = stdin.flush();
        drop(stdin);
    }

    // audit H5: a long-running server/daemon launch (tg mcp, tg session serve, tg lsp in
    // server mode) must never die on a timer -- fall back to the original unbounded wait for
    // exactly these explicit cases. Every other one-shot passthrough command gets the same
    // bounded wait-or-kill machinery already proven for the sidecar and --help probe paths.
    if is_daemon_launch {
        let status = child.wait().map_err(|err| SidecarError {
            exit_code: 1,
            message: format!(
                "Failed while waiting for Python passthrough with `{}`: {err}",
                python.to_string_lossy()
            ),
            stderr: String::new(),
        })?;

        return Ok(status.code().unwrap_or(1));
    }

    let timeout = resolve_passthrough_timeout();
    let wait_outcome = wait_for_passthrough_or_kill(&mut child, timeout)?;

    if matches!(wait_outcome, SidecarWaitOutcome::TimedOut) {
        return Err(SidecarError {
            exit_code: 124,
            message: format!(
                "Python passthrough command `{command}` timed out after {} ms and was terminated",
                timeout.as_millis()
            ),
            stderr: String::new(),
        });
    }

    let status = match wait_outcome {
        SidecarWaitOutcome::Exited(status) => status,
        SidecarWaitOutcome::TimedOut => unreachable!("timed out passthrough handled above"),
    };

    Ok(status.code().unwrap_or(1))
}

/// Recognizes the passthrough subcommands that legitimately launch a long-running
/// server/daemon and must NOT be killed by `resolve_passthrough_timeout()` (audit H5). Keep
/// this list explicit and auditable -- do not widen it to a heuristic (e.g. "no args" or
/// "took a while to print anything"), and do not add an entry without citing the Python
/// command it exempts.
///
/// - `mcp` -- `tg mcp` starts the Model Context Protocol server (`mcp_server()` ->
///   `run_mcp_server()`, `src/tensor_grep/cli/main.py:11898-11903`) and blocks on stdio
///   JSON-RPC for the lifetime of the client connection. Rust's `Commands::Mcp` variant takes
///   no args, so `command == "mcp"` alone is sufficient.
/// - `session` + `serve` -- `tg session serve <id>` starts a JSONL request/response loop on
///   stdin/stdout (`session_serve` -> `serve_session_stream`,
///   `src/tensor_grep/cli/main.py:10854-10880`) for repeated repo-map/symbol queries. Every
///   OTHER `tg session ...` subcommand (open/list/show/refresh/context/edit-plan/blast-radius/
///   importers/`daemon start|status|stop`) is one-shot and already internally bounded (e.g.
///   `session_daemon_start` spawns a DETACHED background process and returns within
///   `_DAEMON_START_TIMEOUT_SECONDS` = 5s, `src/tensor_grep/cli/session_daemon.py:61,654-696`)
///   -- only literally `serve` is exempt.
/// - `lsp` without `--debug-trace` -- `tg lsp` starts the structural-search Language Server
///   Protocol server (`lsp()` -> `run_lsp()`, `src/tensor_grep/cli/main.py:11762-11833`) and
///   blocks on the LSP stdio protocol. `tg lsp --debug-trace LANGUAGE` is the one documented
///   one-shot exception -- a health probe that returns instead of starting the server
///   (`src/tensor_grep/cli/main.py:11819-11831`) -- and MUST stay bounded.
fn is_long_running_passthrough_command(command: &str, args: &[String]) -> bool {
    match command {
        "mcp" => true,
        "session" => args.first().map(String::as_str) == Some("serve"),
        // Both the space form (`--debug-trace python`) and Click's equals form
        // (`--debug-trace=python`, a single trailing_var_arg token) turn `tg lsp` into a
        // one-shot health probe that returns instead of starting the server -- both MUST stay
        // bounded, so neither may count as a server launch.
        "lsp" => !args
            .iter()
            .any(|arg| arg == "--debug-trace" || arg.starts_with("--debug-trace=")),
        _ => false,
    }
}

pub fn execute_python_passthrough_command_captured(
    command: &str,
    args: Vec<String>,
) -> Result<PythonPassthroughResult, SidecarError> {
    let python = resolve_python_command();
    let passthrough_timeout = resolve_help_probe_timeout();

    let mut child = command_for_executable(&python);
    configure_python_child_environment(&mut child);
    #[cfg(unix)]
    unsafe {
        child.pre_exec(|| {
            if libc::setpgid(0, 0) != 0 {
                return Err(io::Error::last_os_error());
            }
            Ok(())
        });
    }
    child
        .arg("-m")
        .arg(DEFAULT_TENSOR_GREP_MODULE)
        .arg(command)
        .args(args)
        .stdin(Stdio::null())
        .stdout(Stdio::piped())
        .stderr(Stdio::piped());

    let mut child = child
        .spawn()
        .map_err(|err| map_python_spawn_error(&python, err))?;

    let stdout = child.stdout.take().ok_or_else(|| SidecarError {
        exit_code: 1,
        message: "Python passthrough stdout pipe was unavailable".to_string(),
        stderr: String::new(),
    })?;
    let stderr = child.stderr.take().ok_or_else(|| SidecarError {
        exit_code: 1,
        message: "Python passthrough stderr pipe was unavailable".to_string(),
        stderr: String::new(),
    })?;

    let stdout_reader = read_all_thread(stdout);
    let stderr_reader = read_all_thread(stderr);
    let wait_outcome = wait_for_passthrough_or_kill(&mut child, passthrough_timeout)?;

    let stdout_bytes = stdout_reader.join().map_err(|_| SidecarError {
        exit_code: 1,
        message: "Python passthrough stdout reader thread panicked".to_string(),
        stderr: String::new(),
    })?;
    let stderr_bytes = stderr_reader.join().map_err(|_| SidecarError {
        exit_code: 1,
        message: "Python passthrough stderr reader thread panicked".to_string(),
        stderr: String::new(),
    })?;

    let stderr_text = bytes_to_string(stderr_bytes.map_err(|err| SidecarError {
        exit_code: 1,
        message: format!("Failed to read Python passthrough stderr: {err}"),
        stderr: String::new(),
    })?);
    let stdout_text = bytes_to_string(stdout_bytes.map_err(|err| SidecarError {
        exit_code: 1,
        message: format!("Failed to read Python passthrough stdout: {err}"),
        stderr: stderr_text.clone(),
    })?);

    if matches!(wait_outcome, SidecarWaitOutcome::TimedOut) {
        return Err(SidecarError {
            exit_code: 124,
            message: format!(
                "Python passthrough timed out after {} ms and was terminated",
                passthrough_timeout.as_millis()
            ),
            stderr: stderr_text,
        });
    }

    let status = match wait_outcome {
        SidecarWaitOutcome::Exited(status) => status,
        SidecarWaitOutcome::TimedOut => unreachable!("timed out passthrough handled above"),
    };

    Ok(PythonPassthroughResult {
        exit_code: status.code().unwrap_or(1),
        stdout: stdout_text,
        stderr: stderr_text,
    })
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

    let mut child = command_for_executable(&python);
    configure_python_child_environment(&mut child);
    if let Some(device_ids) = gpu_device_ids_env_value(&request) {
        child.env("TENSOR_GREP_DEVICE_IDS", device_ids);
    }
    match launch_target {
        PythonLaunchTarget::Module(module) => {
            child.arg("-m").arg(module);
        }
        PythonLaunchTarget::Script(script) => {
            child.arg(script);
        }
    }

    // audit I8: place the sidecar child in its own process group on Unix so
    // that terminate_sidecar_process can kill descendants via killpg.
    #[cfg(unix)]
    unsafe {
        child.pre_exec(|| {
            if libc::setpgid(0, 0) != 0 {
                return Err(io::Error::last_os_error());
            }
            Ok(())
        });
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

fn wait_for_passthrough_or_kill(
    child: &mut Child,
    timeout: Duration,
) -> Result<SidecarWaitOutcome, SidecarError> {
    let deadline = Instant::now() + timeout;
    loop {
        match child.try_wait() {
            Ok(Some(status)) => return Ok(SidecarWaitOutcome::Exited(status)),
            Ok(None) => {
                if Instant::now() >= deadline {
                    terminate_passthrough_process(child)?;
                    return Ok(SidecarWaitOutcome::TimedOut);
                }
                thread::sleep(Duration::from_millis(10));
            }
            Err(err) => {
                return Err(SidecarError {
                    exit_code: 1,
                    message: format!("Failed while waiting for Python passthrough: {err}"),
                    stderr: String::new(),
                });
            }
        }
    }
}

// audit I8: use a tree-kill to ensure the entire descendant process group is
// reaped on timeout, matching the strategy already used by
// terminate_passthrough_process.  The sidecar may spawn sub-processes (e.g.
// GPU workers) that would otherwise become orphans.
fn terminate_sidecar_process(child: &mut Child) -> Result<(), SidecarError> {
    #[cfg(windows)]
    {
        let pid = child.id().to_string();
        let status = Command::new("taskkill")
            .args(["/PID", &pid, "/T", "/F"])
            .stdin(Stdio::null())
            .stdout(Stdio::null())
            .stderr(Stdio::null())
            .status();
        match status {
            Ok(status) if status.success() => {}
            Ok(_) | Err(_) => {
                if let Err(err) = child.kill() {
                    if err.kind() != io::ErrorKind::InvalidInput {
                        return Err(SidecarError {
                            exit_code: 1,
                            message: format!(
                                "Failed to terminate timed-out Python sidecar tree: {err}"
                            ),
                            stderr: String::new(),
                        });
                    }
                }
            }
        }
    }

    #[cfg(not(windows))]
    {
        let pid = child.id() as i32;
        let kill_status = unsafe { libc::killpg(pid, libc::SIGKILL) };
        if kill_status != 0 {
            let err = io::Error::last_os_error();
            if err.raw_os_error() != Some(libc::ESRCH) && err.kind() != io::ErrorKind::InvalidInput
            {
                return Err(SidecarError {
                    exit_code: 1,
                    message: format!(
                        "Failed to terminate timed-out Python sidecar process group: {err}"
                    ),
                    stderr: String::new(),
                });
            }
        }
    }

    child.wait().map_err(|err| SidecarError {
        exit_code: 1,
        message: format!("Failed to reap timed-out Python sidecar: {err}"),
        stderr: String::new(),
    })?;

    Ok(())
}

fn terminate_passthrough_process(child: &mut Child) -> Result<(), SidecarError> {
    #[cfg(windows)]
    {
        let pid = child.id().to_string();
        let status = Command::new("taskkill")
            .args(["/PID", &pid, "/T", "/F"])
            .stdin(Stdio::null())
            .stdout(Stdio::null())
            .stderr(Stdio::null())
            .status();
        match status {
            Ok(status) if status.success() => {}
            Ok(_) | Err(_) => {
                if let Err(err) = child.kill() {
                    if err.kind() != io::ErrorKind::InvalidInput {
                        return Err(SidecarError {
                            exit_code: 1,
                            message: format!(
                                "Failed to terminate timed-out Python passthrough tree: {err}"
                            ),
                            stderr: String::new(),
                        });
                    }
                }
            }
        }
    }

    #[cfg(not(windows))]
    {
        let pid = child.id() as i32;
        let kill_status = unsafe { libc::killpg(pid, libc::SIGKILL) };
        if kill_status != 0 {
            let err = io::Error::last_os_error();
            if err.raw_os_error() != Some(libc::ESRCH) && err.kind() != io::ErrorKind::InvalidInput
            {
                return Err(SidecarError {
                    exit_code: 1,
                    message: format!(
                        "Failed to terminate timed-out Python passthrough process group: {err}"
                    ),
                    stderr: String::new(),
                });
            }
        }
    }

    child.wait().map_err(|err| SidecarError {
        exit_code: 1,
        message: format!("Failed to reap timed-out Python passthrough: {err}"),
        stderr: String::new(),
    })?;

    Ok(())
}

// audit I4: read at most MAX_CAPTURED_OUTPUT_BYTES from the child stream.
// If the child produces more, return an io::Error so callers surface a clear
// truncation error rather than silently growing without bound.
fn read_all_thread<R>(mut reader: R) -> thread::JoinHandle<io::Result<Vec<u8>>>
where
    R: Read + Send + 'static,
{
    thread::spawn(move || {
        let mut bytes = Vec::new();
        // Read up to the cap + 1 so we can detect whether the limit was hit.
        let n = reader
            .by_ref()
            .take(MAX_CAPTURED_OUTPUT_BYTES + 1)
            .read_to_end(&mut bytes)?;
        if n as u64 > MAX_CAPTURED_OUTPUT_BYTES {
            return Err(io::Error::other(format!(
                "child process output exceeded the {} MiB capture limit",
                MAX_CAPTURED_OUTPUT_BYTES / (1024 * 1024)
            )));
        }
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

    let current_exe = env::current_exe().ok();
    resolve_python_command_for_context(current_exe.as_deref(), &managed_home_dirs_from_env())
}

fn command_for_executable(program: &OsStr) -> Command {
    #[cfg(windows)]
    {
        let path = Path::new(program);
        if is_windows_batch_script(path) {
            let mut command = Command::new("cmd");
            command.arg("/d").arg("/c").arg(path);
            return command;
        }
    }
    Command::new(program)
}

#[cfg(windows)]
fn is_windows_batch_script(program: &Path) -> bool {
    program
        .extension()
        .and_then(OsStr::to_str)
        .is_some_and(|extension| {
            extension.eq_ignore_ascii_case("cmd") || extension.eq_ignore_ascii_case("bat")
        })
}

fn resolve_python_command_for_context(
    current_exe: Option<&Path>,
    home_dirs: &[PathBuf],
) -> OsString {
    if current_exe.is_some_and(is_windows_com_bridge) {
        for home_dir in home_dirs {
            if let Some(managed_python) = managed_install_python_from_home(home_dir) {
                return managed_python.into_os_string();
            }
        }
    }

    if let Some(current_exe) = current_exe {
        if let Some(runtime_relative_python) = resolve_existing_relative_to_exe(
            current_exe,
            &[
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
            ],
        ) {
            return runtime_relative_python.into_os_string();
        }
    }

    if current_exe.is_some_and(|path| is_managed_windows_exe_bridge(path, home_dirs)) {
        for home_dir in home_dirs {
            if let Some(managed_python) = managed_install_python_from_home(home_dir) {
                return managed_python.into_os_string();
            }
        }
    }

    OsString::from("python")
}

fn is_windows_com_bridge(path: &Path) -> bool {
    path.file_name()
        .and_then(OsStr::to_str)
        .is_some_and(|name| name.eq_ignore_ascii_case("tg.com"))
}

fn is_external_windows_exe_bridge(path: &Path) -> bool {
    if !cfg!(windows) {
        return false;
    }
    path.file_name()
        .and_then(OsStr::to_str)
        .is_some_and(|name| name.eq_ignore_ascii_case("tg.exe"))
}

fn is_managed_windows_exe_bridge(path: &Path, home_dirs: &[PathBuf]) -> bool {
    if !is_external_windows_exe_bridge(path) {
        return false;
    }
    let Some(parent) = path.parent() else {
        return false;
    };
    if !home_dirs.iter().any(|home_dir| {
        paths_equivalent(parent, &home_dir.join("bin"))
            || paths_equivalent(parent, &home_dir.join(".local").join("bin"))
    }) {
        return false;
    }
    fs::read_to_string(path.with_file_name(WINDOWS_EXE_BRIDGE_MARKER))
        .is_ok_and(|content| content.trim() == WINDOWS_EXE_BRIDGE_MARKER_CONTENT)
}

fn managed_home_dirs_from_env() -> Vec<PathBuf> {
    let env_names = if cfg!(windows) {
        ["USERPROFILE", "HOME"]
    } else {
        ["HOME", "USERPROFILE"]
    };
    let mut dirs = Vec::new();
    for env_name in env_names {
        let Some(value) = env::var_os(env_name) else {
            continue;
        };
        if value.is_empty() {
            continue;
        }
        let dir = PathBuf::from(value);
        if !dirs.iter().any(|existing| existing == &dir) {
            dirs.push(dir);
        }
    }
    dirs
}

fn managed_install_python_from_home(home_dir: &Path) -> Option<PathBuf> {
    let candidate = home_dir
        .join(".tensor-grep")
        .join(".venv")
        .join(if cfg!(windows) { "Scripts" } else { "bin" })
        .join(if cfg!(windows) {
            "python.exe"
        } else {
            "python"
        });
    candidate.is_file().then_some(candidate)
}

fn managed_install_native_binary_from_home(home_dir: &Path) -> Option<PathBuf> {
    let candidate = home_dir
        .join(".tensor-grep")
        .join("bin")
        .join(if cfg!(windows) { "tg.exe" } else { "tg-native" });
    candidate.is_file().then_some(candidate)
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

fn configure_python_child_environment(command: &mut Command) {
    configure_python_module_path(command);
    // Mark the spawned Python as a re-exec OF the native front door. The Python launcher
    // checks this and refuses to delegate search back to the native binary — otherwise
    // `tg --json <native-passthrough-flag>` (e.g. --debug/--stats) ping-pongs
    // native<->python forever (the C3 fork-bomb, which render-flag guards alone did not
    // fully close). This breaks the mutual-delegation cycle for ALL flag combinations.
    command.env("TG_REEXEC_GUARD", "1");
    if let Some(native_tg_binary) = native_tg_binary_env_override(
        env::var_os(TG_NATIVE_TG_BINARY_ENV),
        env::current_exe().ok(),
    ) {
        command.env(TG_NATIVE_TG_BINARY_ENV, native_tg_binary);
    }
}

fn native_tg_binary_env_override(
    existing: Option<OsString>,
    current_exe: Option<PathBuf>,
) -> Option<OsString> {
    native_tg_binary_env_override_for_context(existing, current_exe, &managed_home_dirs_from_env())
}

fn native_tg_binary_env_override_for_context(
    existing: Option<OsString>,
    current_exe: Option<PathBuf>,
    home_dirs: &[PathBuf],
) -> Option<OsString> {
    if existing.is_some() {
        return None;
    }
    if current_exe
        .as_ref()
        .is_some_and(|path| is_windows_com_bridge(path))
    {
        for home_dir in home_dirs {
            if let Some(managed_native) = managed_install_native_binary_from_home(home_dir) {
                return Some(managed_native.into_os_string());
            }
        }
    }
    if current_exe
        .as_ref()
        .is_some_and(|path| is_managed_windows_exe_bridge(path, home_dirs))
    {
        for home_dir in home_dirs {
            if let Some(managed_native) = managed_install_native_binary_from_home(home_dir) {
                if !paths_equivalent(
                    current_exe.as_ref().expect("checked current_exe"),
                    &managed_native,
                ) {
                    return Some(managed_native.into_os_string());
                }
            }
        }
    }
    current_exe.map(Into::into)
}

fn paths_equivalent(left: &Path, right: &Path) -> bool {
    if cfg!(windows) {
        left.to_string_lossy()
            .eq_ignore_ascii_case(&right.to_string_lossy())
    } else {
        left == right
    }
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

fn gpu_device_ids_env_value(request: &SidecarRequest) -> Option<OsString> {
    if request.command != "gpu_search" {
        return None;
    }

    let payload = request.payload.as_ref()?;
    let gpu_device_ids = payload.get("gpu_device_ids")?.as_array()?;
    let device_ids = gpu_device_ids
        .iter()
        .filter_map(|value| value.as_i64())
        .map(|device_id| device_id.to_string())
        .collect::<Vec<_>>();

    if device_ids.is_empty() {
        return None;
    }

    Some(OsString::from(device_ids.join(",")))
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

fn resolve_help_probe_timeout() -> Duration {
    let timeout_ms = env::var(TG_HELP_PROBE_TIMEOUT_MS_ENV)
        .ok()
        .and_then(|raw| raw.parse::<u64>().ok())
        .filter(|value| *value > 0)
        .unwrap_or(DEFAULT_HELP_PROBE_TIMEOUT_MS);
    Duration::from_millis(timeout_ms)
}

fn resolve_passthrough_timeout() -> Duration {
    let timeout_ms = env::var(TG_PASSTHROUGH_TIMEOUT_MS_ENV)
        .ok()
        .and_then(|raw| raw.parse::<u64>().ok())
        .filter(|value| *value > 0)
        .unwrap_or(DEFAULT_PASSTHROUGH_TIMEOUT_MS);
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
    use super::{
        gpu_device_ids_env_value, is_long_running_passthrough_command,
        managed_install_native_binary_from_home, managed_install_python_from_home,
        merged_pythonpath, native_tg_binary_env_override,
        native_tg_binary_env_override_for_context, read_all_thread,
        resolve_python_command_for_context, resolve_repo_source_root_relative_to_exe,
        SidecarRequest, MAX_CAPTURED_OUTPUT_BYTES, WINDOWS_EXE_BRIDGE_MARKER,
        WINDOWS_EXE_BRIDGE_MARKER_CONTENT,
    };
    use serde_json::json;
    use std::env;
    use std::ffi::{OsStr, OsString};
    use std::fs;
    use std::io::Cursor;
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

    #[test]
    fn gpu_device_ids_env_value_serializes_request_gpu_ids() {
        let request = SidecarRequest {
            command: "gpu_search".to_string(),
            args: vec![],
            payload: Some(json!({
                "gpu_device_ids": [0, 2, 4],
            })),
        };

        assert_eq!(
            gpu_device_ids_env_value(&request),
            Some(OsString::from("0,2,4"))
        );
    }

    #[test]
    fn native_tg_binary_env_override_uses_current_exe_only_when_unset() {
        let current_exe = Path::new("managed").join("bin").join("tg.exe");

        assert_eq!(
            native_tg_binary_env_override(None, Some(current_exe.clone())),
            Some(current_exe.into_os_string())
        );
        assert_eq!(
            native_tg_binary_env_override(
                Some(OsString::from("explicit-native.exe")),
                Some(Path::new("managed").join("bin").join("tg.exe"))
            ),
            None
        );
        assert_eq!(native_tg_binary_env_override(None, None), None);
    }

    #[test]
    fn resolves_managed_home_python_for_external_windows_bridge() {
        let dir = tempdir().unwrap();
        let home = dir.path().join("home");
        let python = home
            .join(".tensor-grep")
            .join(".venv")
            .join(if cfg!(windows) { "Scripts" } else { "bin" })
            .join(if cfg!(windows) {
                "python.exe"
            } else {
                "python"
            });
        let bridge = dir.path().join("Python314").join("Scripts").join("tg.com");
        fs::create_dir_all(python.parent().unwrap()).unwrap();
        fs::create_dir_all(bridge.parent().unwrap()).unwrap();
        fs::write(&python, b"python").unwrap();
        fs::write(&bridge, b"native").unwrap();
        fs::write(
            bridge.with_file_name(WINDOWS_EXE_BRIDGE_MARKER),
            WINDOWS_EXE_BRIDGE_MARKER_CONTENT,
        )
        .unwrap();
        fs::write(
            bridge.with_file_name(WINDOWS_EXE_BRIDGE_MARKER),
            WINDOWS_EXE_BRIDGE_MARKER_CONTENT,
        )
        .unwrap();
        fs::write(
            bridge
                .parent()
                .unwrap()
                .parent()
                .unwrap()
                .join("python.exe"),
            b"ambient",
        )
        .unwrap();

        assert_eq!(
            managed_install_python_from_home(&home).as_deref(),
            Some(python.as_path())
        );
        assert_eq!(
            resolve_python_command_for_context(Some(&bridge), &[home]).as_os_str(),
            python.as_os_str()
        );
    }

    #[test]
    fn external_com_bridge_points_sidecar_back_to_managed_native_frontdoor() {
        let dir = tempdir().unwrap();
        let home = dir.path().join("home");
        let managed_native = home
            .join(".tensor-grep")
            .join("bin")
            .join(if cfg!(windows) { "tg.exe" } else { "tg-native" });
        let bridge = dir.path().join("Python314").join("Scripts").join("tg.com");
        fs::create_dir_all(managed_native.parent().unwrap()).unwrap();
        fs::create_dir_all(bridge.parent().unwrap()).unwrap();
        fs::write(&managed_native, b"native").unwrap();
        fs::write(&bridge, b"bridge").unwrap();
        fs::write(
            bridge.with_file_name(WINDOWS_EXE_BRIDGE_MARKER),
            WINDOWS_EXE_BRIDGE_MARKER_CONTENT,
        )
        .unwrap();

        assert_eq!(
            managed_install_native_binary_from_home(&home).as_deref(),
            Some(managed_native.as_path())
        );
        assert_eq!(
            native_tg_binary_env_override_for_context(None, Some(bridge), &[home]).as_deref(),
            Some(managed_native.as_os_str())
        );
    }

    #[cfg(windows)]
    #[test]
    fn external_exe_bridge_resolves_managed_home_python() {
        let dir = tempdir().unwrap();
        let home = dir.path().join("home");
        let python = home
            .join(".tensor-grep")
            .join(".venv")
            .join("Scripts")
            .join("python.exe");
        let bridge = home.join("bin").join("tg.exe");
        fs::create_dir_all(python.parent().unwrap()).unwrap();
        fs::create_dir_all(bridge.parent().unwrap()).unwrap();
        fs::write(&python, b"python").unwrap();
        fs::write(&bridge, b"native").unwrap();
        fs::write(
            bridge.with_file_name(WINDOWS_EXE_BRIDGE_MARKER),
            WINDOWS_EXE_BRIDGE_MARKER_CONTENT,
        )
        .unwrap();

        assert_eq!(
            resolve_python_command_for_context(Some(&bridge), &[home]).as_os_str(),
            python.as_os_str()
        );
    }

    #[cfg(windows)]
    #[test]
    fn external_exe_bridge_points_sidecar_back_to_managed_native_frontdoor() {
        let dir = tempdir().unwrap();
        let home = dir.path().join("home");
        let managed_native = home.join(".tensor-grep").join("bin").join("tg.exe");
        let bridge = home.join("bin").join("tg.exe");
        fs::create_dir_all(managed_native.parent().unwrap()).unwrap();
        fs::create_dir_all(bridge.parent().unwrap()).unwrap();
        fs::write(&managed_native, b"native").unwrap();
        fs::write(&bridge, b"bridge").unwrap();
        fs::write(
            bridge.with_file_name(WINDOWS_EXE_BRIDGE_MARKER),
            WINDOWS_EXE_BRIDGE_MARKER_CONTENT,
        )
        .unwrap();

        assert_eq!(
            native_tg_binary_env_override_for_context(None, Some(bridge), &[home]).as_deref(),
            Some(managed_native.as_os_str())
        );
    }

    #[cfg(windows)]
    #[test]
    fn unmarked_home_bin_exe_does_not_redirect_to_managed_install() {
        let dir = tempdir().unwrap();
        let home = dir.path().join("home");
        let python = home
            .join(".tensor-grep")
            .join(".venv")
            .join("Scripts")
            .join("python.exe");
        let managed_native = home.join(".tensor-grep").join("bin").join("tg.exe");
        let local_exe = home.join("bin").join("tg.exe");
        fs::create_dir_all(python.parent().unwrap()).unwrap();
        fs::create_dir_all(managed_native.parent().unwrap()).unwrap();
        fs::create_dir_all(local_exe.parent().unwrap()).unwrap();
        fs::write(&python, b"python").unwrap();
        fs::write(&managed_native, b"managed").unwrap();
        fs::write(&local_exe, b"local").unwrap();

        assert_eq!(
            resolve_python_command_for_context(Some(&local_exe), std::slice::from_ref(&home))
                .as_os_str(),
            OsStr::new("python")
        );
        assert_eq!(
            native_tg_binary_env_override_for_context(None, Some(local_exe.clone()), &[home])
                .as_deref(),
            Some(local_exe.as_os_str())
        );
    }

    // --- audit I4: output capture cap tests ---

    /// Exact-limit data (one byte under cap + 1) must succeed.
    #[test]
    fn read_all_thread_accepts_output_at_or_below_cap() {
        let data = vec![0u8; MAX_CAPTURED_OUTPUT_BYTES as usize];
        let cursor = Cursor::new(data.clone());
        let handle = read_all_thread(cursor);
        let result = handle.join().expect("thread should not panic");
        assert!(result.is_ok(), "output at cap should succeed");
        assert_eq!(result.unwrap().len(), data.len());
    }

    /// Output one byte over the cap must return an error.
    #[test]
    fn read_all_thread_rejects_output_exceeding_cap() {
        // Use a tiny synthetic cap by feeding cap+1 bytes via a Cursor.
        // We test the exact edge using the real constant but with a small
        // slice that exceeds it by one byte — we fake exceeding MAX by
        // wrapping a reader that reports exactly cap+1 bytes.
        let over_cap = vec![0u8; MAX_CAPTURED_OUTPUT_BYTES as usize + 1];
        let cursor = Cursor::new(over_cap);
        let handle = read_all_thread(cursor);
        let result = handle.join().expect("thread should not panic");
        assert!(
            result.is_err(),
            "output exceeding cap should return an error"
        );
        let err = result.unwrap_err();
        assert!(
            err.to_string().contains("capture limit"),
            "error message should mention capture limit, got: {err}"
        );
    }

    /// Small output well under the cap must pass through unchanged.
    #[test]
    fn read_all_thread_passes_through_small_output() {
        let payload = b"hello sidecar\n".to_vec();
        let cursor = Cursor::new(payload.clone());
        let handle = read_all_thread(cursor);
        let result = handle.join().expect("thread should not panic").unwrap();
        assert_eq!(result, payload);
    }

    /// Empty output must succeed and return an empty vec.
    #[test]
    fn read_all_thread_handles_empty_output() {
        let cursor = Cursor::new(Vec::<u8>::new());
        let handle = read_all_thread(cursor);
        let result = handle.join().expect("thread should not panic").unwrap();
        assert!(result.is_empty());
    }

    // --- audit H5: daemon/server-launch exemption tests ---
    //
    // These pin the exact, explicit exemption list from is_long_running_passthrough_command
    // (mcp / session serve / lsp server mode) so a future edit cannot silently widen it (which
    // would reopen the unbounded-hang bug for a one-shot command) or narrow it (which would
    // kill a legitimate long-running server on the timer -- the load-bearing failure mode this
    // fix must never introduce).

    #[test]
    fn mcp_server_launch_is_exempt() {
        assert!(is_long_running_passthrough_command("mcp", &[]));
    }

    #[test]
    fn session_serve_is_exempt() {
        let args = vec!["serve".to_string(), "abc123".to_string()];
        assert!(is_long_running_passthrough_command("session", &args));
    }

    #[test]
    fn session_open_is_not_exempt() {
        let args = vec!["open".to_string(), ".".to_string()];
        assert!(!is_long_running_passthrough_command("session", &args));
    }

    #[test]
    fn session_daemon_start_is_not_exempt() {
        // `tg session daemon start` spawns a DETACHED background process and returns within
        // _DAEMON_START_TIMEOUT_SECONDS=5s (session_daemon.py) -- it is one-shot, not itself a
        // server loop, and must stay bounded like any other one-shot passthrough command.
        let args = vec!["daemon".to_string(), "start".to_string()];
        assert!(!is_long_running_passthrough_command("session", &args));
    }

    #[test]
    fn session_with_no_subcommand_is_not_exempt() {
        assert!(!is_long_running_passthrough_command("session", &[]));
    }

    #[test]
    fn bare_lsp_is_exempt() {
        assert!(is_long_running_passthrough_command("lsp", &[]));
    }

    #[test]
    fn lsp_with_provider_flag_is_exempt() {
        let args = vec!["--provider".to_string(), "hybrid".to_string()];
        assert!(is_long_running_passthrough_command("lsp", &args));
    }

    #[test]
    fn lsp_debug_trace_is_not_exempt() {
        let args = vec!["--debug-trace".to_string(), "python".to_string()];
        assert!(!is_long_running_passthrough_command("lsp", &args));
    }

    #[test]
    fn lsp_debug_trace_equals_form_is_not_exempt() {
        // Click accepts the equals form, which trailing_var_arg passes through as a single
        // token -- it must NOT be mistaken for a server launch and left unbounded.
        let args = vec!["--debug-trace=python".to_string()];
        assert!(!is_long_running_passthrough_command("lsp", &args));
    }

    #[test]
    fn lsp_debug_trace_equals_form_after_other_flags_is_not_exempt() {
        let args = vec![
            "--provider".to_string(),
            "native".to_string(),
            "--debug-trace=rust".to_string(),
        ];
        assert!(!is_long_running_passthrough_command("lsp", &args));
    }

    #[test]
    fn unrelated_one_shot_commands_are_not_exempt() {
        assert!(!is_long_running_passthrough_command("doctor", &[]));
        assert!(!is_long_running_passthrough_command(
            "map",
            &["--json".to_string()]
        ));
        assert!(!is_long_running_passthrough_command(
            "checkpoint",
            &["create".to_string()]
        ));
        assert!(!is_long_running_passthrough_command("upgrade", &[]));
    }
}
