//! Keep selected runtime owners alive until a delegated Windows process settles.
#![cfg(windows)]

use crate::frontdoor_hops::TG_FRONTDOOR_HOPS_ENV;
use crate::managed_runtime_python::configure_managed_runtime_command;
use crate::managed_runtime_resolver::ManagedRuntime;
use std::ffi::OsString;
use std::io;
use std::os::windows::io::{AsRawHandle, FromRawHandle, OwnedHandle};
use std::process::{Child, Command, Stdio};
use std::time::Duration;
use windows_sys::Win32::Foundation::{
    DuplicateHandle, DUPLICATE_SAME_ACCESS, WAIT_FAILED, WAIT_OBJECT_0, WAIT_TIMEOUT,
};
use windows_sys::Win32::System::Threading::{
    GetCurrentProcess, TerminateProcess, WaitForSingleObject,
};

#[derive(Debug)]
pub struct UnsettledChild {
    pub error: io::Error,
    pub runtime: ManagedRuntime,
    pub child: Child,
    pub process_handle: Option<OwnedHandle>,
}

/// Fail closed when process termination cannot be confirmed.
pub(crate) fn fail_stop_unsettled(unsettled: UnsettledChild) -> ! {
    let UnsettledChild {
        runtime,
        child,
        process_handle,
        error,
    } = unsettled;
    std::mem::forget((runtime, child, process_handle));
    let _ = std::panic::catch_unwind(std::panic::AssertUnwindSafe(|| {
        use std::io::Write;
        let _ = writeln!(
            std::io::stderr().lock(),
            "fatal: termination requested; child settlement unproven: {error}"
        );
    }));
    std::process::exit(2)
}

pub(crate) enum LaunchFailure {
    Io(io::Error),
    Unsettled(Box<UnsettledChild>),
}

pub(crate) fn launch_selected_native(
    runtime: ManagedRuntime,
    args: &[OsString],
    hops: u32,
) -> Result<i32, LaunchFailure> {
    let mut command = Command::new(&runtime.native);
    command
        .args(args)
        .stdin(Stdio::inherit())
        .stdout(Stdio::inherit())
        .stderr(Stdio::inherit())
        .env(TG_FRONTDOOR_HOPS_ENV, hops.to_string());
    configure_managed_runtime_command(&mut command, &runtime.python, &runtime.native);
    let mut child = command.spawn().map_err(LaunchFailure::Io)?;
    let status = match child.wait() {
        Ok(status) => status,
        Err(wait_error) => {
            let process_handle = duplicate_process_handle(&child).ok();
            let recovery = recover_wait_failure(wait_error, || match process_handle.as_ref() {
                Some(handle) => terminate_and_confirm(handle, Duration::from_secs(10)),
                None => {
                    let _ = child.kill();
                    Err(io::Error::other("could not duplicate child process handle"))
                }
            });
            match recovery {
                WaitRecoveryError::Settled(error) => return Err(LaunchFailure::Io(error)),
                WaitRecoveryError::Unconfirmed(error) => {
                    return Err(LaunchFailure::Unsettled(Box::new(UnsettledChild {
                        error,
                        runtime,
                        child,
                        process_handle,
                    })))
                }
            }
        }
    };
    Ok(status.code().unwrap_or(1))
}

fn duplicate_process_handle(child: &Child) -> io::Result<OwnedHandle> {
    let mut duplicate = std::ptr::null_mut();
    let current = unsafe { GetCurrentProcess() };
    if unsafe {
        DuplicateHandle(
            current,
            child.as_raw_handle(),
            current,
            &mut duplicate,
            0,
            0,
            DUPLICATE_SAME_ACCESS,
        )
    } == 0
    {
        return Err(io::Error::last_os_error());
    }
    Ok(unsafe { OwnedHandle::from_raw_handle(duplicate) })
}

fn terminate_and_confirm(handle: &OwnedHandle, timeout: Duration) -> io::Result<bool> {
    let raw = handle.as_raw_handle();
    let _ = unsafe { TerminateProcess(raw, 2) };
    let timeout_ms = timeout.as_millis().min(u32::MAX as u128) as u32;
    match unsafe { WaitForSingleObject(raw, timeout_ms) } {
        WAIT_OBJECT_0 => Ok(true),
        WAIT_TIMEOUT => Ok(false),
        WAIT_FAILED => Err(io::Error::last_os_error()),
        _ => Err(io::Error::new(
            io::ErrorKind::InvalidData,
            "unexpected process wait result",
        )),
    }
}

fn wait_failure_error(wait_error: io::Error) -> io::Error {
    io::Error::other(format!(
        "managed child wait failed after termination was confirmed: {wait_error}"
    ))
}

enum WaitRecoveryError {
    Settled(io::Error),
    Unconfirmed(io::Error),
}

fn recover_wait_failure(
    wait_error: io::Error,
    confirm: impl FnOnce() -> io::Result<bool>,
) -> WaitRecoveryError {
    match confirm() {
        Ok(true) => WaitRecoveryError::Settled(wait_failure_error(wait_error)),
        Ok(false) => WaitRecoveryError::Unconfirmed(wait_error),
        Err(confirm_error) => WaitRecoveryError::Unconfirmed(io::Error::other(format!(
            "{wait_error}; termination confirmation failed: {confirm_error}"
        ))),
    }
}

#[cfg(test)]
mod tests {
    use super::{
        duplicate_process_handle, recover_wait_failure, terminate_and_confirm, wait_failure_error,
        WaitRecoveryError,
    };
    use std::io;
    use std::process::Command;
    use std::time::Duration;

    #[test]
    fn wait_failure_settlement_is_a_nonzero_error_preserving_original() {
        let mut child = Command::new("powershell.exe")
            .args(["-NoProfile", "-Command", "Start-Sleep -Seconds 30"])
            .spawn()
            .unwrap();
        let handle = duplicate_process_handle(&child).unwrap();

        let result = recover_wait_failure(io::Error::other("injected primary wait failure"), || {
            terminate_and_confirm(&handle, Duration::from_secs(10))
        });

        match result {
            WaitRecoveryError::Settled(error) => {
                assert_eq!(error.kind(), io::ErrorKind::Other);
                assert!(error.to_string().contains("injected primary wait failure"));
            }
            WaitRecoveryError::Unconfirmed(error) => panic!("child settlement failed: {error}"),
        }
        assert!(child.wait().is_ok());
    }

    #[test]
    fn unconfirmed_wait_failure_retains_original_diagnostic() {
        let result = recover_wait_failure(io::Error::other("injected primary wait failure"), || {
            Ok(false)
        });

        match result {
            WaitRecoveryError::Unconfirmed(error) => {
                assert_eq!(error.to_string(), "injected primary wait failure");
            }
            WaitRecoveryError::Settled(error) => panic!("unexpected settlement: {error}"),
        }
        assert!(wait_failure_error(io::Error::other("injected"))
            .to_string()
            .contains("injected"));
    }
}
