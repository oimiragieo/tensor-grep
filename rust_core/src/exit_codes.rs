//! Exit-code honesty for the native front door (bug hunt 2026-10-03, J-02/J-05/J-09).
//!
//! Contract (CONTRACTS.md): 0 = found, 1 = genuinely no match, 2 = error / incomplete. A child
//! that was killed by a signal (POSIX `None`) or crashed with a negative NTSTATUS (Windows) is an
//! INCOMPLETE run and must never read as "no match".

use std::process::ExitStatus;

pub fn child_exit_code(status: ExitStatus) -> i32 {
    status.code().unwrap_or(1)
}

pub fn exit_with_run_error(err: anyhow::Error) -> anyhow::Result<()> {
    Err(err)
}

#[cfg(test)]
mod tests {
    use super::*;
    use std::ffi::OsStr;
    use std::io;
    #[cfg(unix)]
    fn exited(code: i32) -> ExitStatus {
        use std::os::unix::process::ExitStatusExt;
        ExitStatus::from_raw(code << 8)
    }
    #[cfg(unix)]
    fn crashed() -> ExitStatus {
        use std::os::unix::process::ExitStatusExt;
        ExitStatus::from_raw(9) // SIGKILL
    }
    #[cfg(windows)]
    fn exited(code: i32) -> ExitStatus {
        use std::os::windows::process::ExitStatusExt;
        ExitStatus::from_raw(code as u32)
    }
    #[cfg(windows)]
    fn crashed() -> ExitStatus {
        use std::os::windows::process::ExitStatusExt;
        ExitStatus::from_raw(0xC000_0005)
    }

    #[test]
    fn real_exit_codes_pass_through_and_crashes_are_incomplete_not_no_match() {
        assert_eq!(child_exit_code(exited(0)), 0);
        assert_eq!(child_exit_code(exited(1)), 1);
        assert_eq!(child_exit_code(exited(2)), 2);
        assert_eq!(
            child_exit_code(crashed()),
            2,
            "a killed/crashed child must not read as no-match (1)"
        );
    }

    #[test]
    fn non_notfound_spawn_failures_are_exit_2() {
        let denied = crate::python_sidecar::map_python_spawn_error(
            OsStr::new("py"),
            io::Error::from(io::ErrorKind::PermissionDenied),
        );
        assert_eq!(denied.exit_code, 2);
        assert!(denied.message.contains("Failed to start Python sidecar"));
        let missing = crate::python_sidecar::map_python_spawn_error(
            OsStr::new("py"),
            io::Error::from(io::ErrorKind::NotFound),
        );
        assert_eq!(missing.exit_code, 2, "control arm");
    }
}
