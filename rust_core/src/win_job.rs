//! Windows Job Object containment for spawned child processes.

use std::io;
use std::process::{Child, Command};

#[cfg(windows)]
use std::mem::size_of;
#[cfg(windows)]
use std::os::windows::io::{AsRawHandle, FromRawHandle, OwnedHandle};
#[cfg(windows)]
use std::os::windows::process::CommandExt;
#[cfg(windows)]
use windows_sys::Win32::Foundation::INVALID_HANDLE_VALUE;
#[cfg(windows)]
use windows_sys::Win32::System::Diagnostics::ToolHelp::{
    CreateToolhelp32Snapshot, Thread32First, Thread32Next, TH32CS_SNAPTHREAD, THREADENTRY32,
};
#[cfg(windows)]
use windows_sys::Win32::System::JobObjects::{
    AssignProcessToJobObject, CreateJobObjectW, JobObjectExtendedLimitInformation,
    SetInformationJobObject, JOBOBJECT_EXTENDED_LIMIT_INFORMATION, JOB_OBJECT_LIMIT_BREAKAWAY_OK,
    JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE,
};
#[cfg(windows)]
use windows_sys::Win32::System::Threading::{
    OpenThread, ResumeThread, WaitForSingleObject, CREATE_SUSPENDED, THREAD_SUSPEND_RESUME,
};

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum JobBreakawayPolicy {
    DenyBreakaway,
    AllowBreakaway,
}

#[cfg(windows)]
#[derive(Debug)]
pub struct JobHandle(#[allow(dead_code)] OwnedHandle);

#[cfg(not(windows))]
#[derive(Debug)]
pub struct JobHandle;

#[cfg(windows)]
fn terminate_and_wait_bounded(mut child: Child, job: OwnedHandle) {
    let _ = child.kill();
    drop(job);
    unsafe {
        WaitForSingleObject(child.as_raw_handle(), 5000);
    }
}

/// Spawn `cmd` inside a fresh anonymous Job Object configured with
/// `JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE` and, when `breakaway` is
/// `JobBreakawayPolicy::AllowBreakaway`, `JOB_OBJECT_LIMIT_BREAKAWAY_OK`.
///
/// On Windows, the child is spawned `CREATE_SUSPENDED`, assigned to the job
/// before its main thread executes any code (preventing grandchild escape), and
/// then resumed. Keeping the returned `JobHandle` alive keeps the child tree
/// running; dropping it terminates all non-breakaway processes in the job.
pub fn spawn_in_kill_on_close_job(
    cmd: &mut Command,
    breakaway: JobBreakawayPolicy,
) -> io::Result<(JobHandle, Child)> {
    #[cfg(not(windows))]
    {
        let _ = breakaway;
        cmd.spawn().map(|child| (JobHandle, child))
    }

    #[cfg(windows)]
    {
        let raw_job = unsafe { CreateJobObjectW(std::ptr::null(), std::ptr::null()) };
        if raw_job.is_null() {
            return Err(io::Error::last_os_error());
        }
        let job = unsafe { OwnedHandle::from_raw_handle(raw_job) };

        let mut limits: JOBOBJECT_EXTENDED_LIMIT_INFORMATION = unsafe { std::mem::zeroed() };
        let mut limit_flags = JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE;
        if breakaway == JobBreakawayPolicy::AllowBreakaway {
            limit_flags |= JOB_OBJECT_LIMIT_BREAKAWAY_OK;
        }
        limits.BasicLimitInformation.LimitFlags = limit_flags;

        let ok = unsafe {
            SetInformationJobObject(
                job.as_raw_handle(),
                JobObjectExtendedLimitInformation,
                (&limits as *const JOBOBJECT_EXTENDED_LIMIT_INFORMATION).cast(),
                size_of::<JOBOBJECT_EXTENDED_LIMIT_INFORMATION>() as u32,
            )
        };
        if ok == 0 {
            return Err(io::Error::last_os_error());
        }

        cmd.creation_flags(CREATE_SUSPENDED);
        match breakaway {
            JobBreakawayPolicy::DenyBreakaway => {
                cmd.env("TG_WIN_JOB_NO_BREAKAWAY", "1");
            }
            JobBreakawayPolicy::AllowBreakaway => {
                cmd.env_remove("TG_WIN_JOB_NO_BREAKAWAY");
            }
        }
        let child = cmd.spawn()?;

        if unsafe { AssignProcessToJobObject(job.as_raw_handle(), child.as_raw_handle()) } == 0 {
            let err = io::Error::last_os_error();
            terminate_and_wait_bounded(child, job);
            return Err(err);
        }

        if let Err(err) = resume_process_main_thread(child.id()) {
            terminate_and_wait_bounded(child, job);
            return Err(err);
        }

        Ok((JobHandle(job), child))
    }
}

#[cfg(windows)]
fn resume_process_main_thread(pid: u32) -> io::Result<()> {
    let raw_snapshot = unsafe { CreateToolhelp32Snapshot(TH32CS_SNAPTHREAD, 0) };
    if raw_snapshot == INVALID_HANDLE_VALUE || raw_snapshot.is_null() {
        return Err(io::Error::last_os_error());
    }
    let snapshot = unsafe { OwnedHandle::from_raw_handle(raw_snapshot) };

    let mut entry: THREADENTRY32 = unsafe { std::mem::zeroed() };
    entry.dwSize = size_of::<THREADENTRY32>() as u32;

    if unsafe { Thread32First(snapshot.as_raw_handle(), &mut entry) } == 0 {
        return Err(io::Error::last_os_error());
    }

    loop {
        if entry.th32OwnerProcessID == pid {
            let raw_thread = unsafe { OpenThread(THREAD_SUSPEND_RESUME, 0, entry.th32ThreadID) };
            if raw_thread.is_null() {
                return Err(io::Error::last_os_error());
            }
            let thread = unsafe { OwnedHandle::from_raw_handle(raw_thread) };
            if unsafe { ResumeThread(thread.as_raw_handle()) } == u32::MAX {
                return Err(io::Error::last_os_error());
            }
            return Ok(());
        }
        if unsafe { Thread32Next(snapshot.as_raw_handle(), &mut entry) } == 0 {
            break;
        }
    }

    Err(io::Error::other(format!(
        "suspended main thread not found for pid {pid}"
    )))
}

#[cfg(all(test, windows))]
mod tests {
    use super::{spawn_in_kill_on_close_job, JobBreakawayPolicy};
    use std::io::{self, BufRead, BufReader, Read};
    use std::os::windows::io::{AsRawHandle, FromRawHandle, OwnedHandle};
    use std::process::{Child, ChildStderr, ChildStdout, Command, Stdio};
    use std::sync::mpsc;
    use std::time::Duration;
    use windows_sys::Win32::Foundation::{WAIT_OBJECT_0, WAIT_TIMEOUT};
    use windows_sys::Win32::System::Threading::{
        OpenProcess, TerminateProcess, WaitForSingleObject, PROCESS_QUERY_LIMITED_INFORMATION,
        PROCESS_SYNCHRONIZE, PROCESS_TERMINATE,
    };

    fn read_line_with_timeout(stdout: ChildStdout, timeout: Duration) -> io::Result<String> {
        let (tx, rx) = mpsc::channel();
        std::thread::spawn(move || {
            let mut reader = BufReader::new(stdout);
            let mut line = String::new();
            let res = reader.read_line(&mut line).map(|_| line);
            let _ = tx.send(res);
        });
        rx.recv_timeout(timeout).map_err(|err| {
            io::Error::new(
                io::ErrorKind::TimedOut,
                format!("timed out waiting for child stdout line: {err}"),
            )
        })?
    }

    fn read_to_string_with_timeout(
        mut stderr: ChildStderr,
        timeout: Duration,
    ) -> io::Result<String> {
        let (tx, rx) = mpsc::channel();
        std::thread::spawn(move || {
            let mut buf = String::new();
            let res = stderr.read_to_string(&mut buf).map(|_| buf);
            let _ = tx.send(res);
        });
        rx.recv_timeout(timeout).map_err(|err| {
            io::Error::new(
                io::ErrorKind::TimedOut,
                format!("timed out waiting for child stderr: {err}"),
            )
        })?
    }

    fn wait_child_bounded(mut child: Child, timeout: Duration) {
        let wait_ms = timeout.as_millis().min(u32::MAX as u128) as u32;
        let wait_res = unsafe { WaitForSingleObject(child.as_raw_handle(), wait_ms) };
        if wait_res != WAIT_OBJECT_0 {
            let _ = child.kill();
            let _ = unsafe { WaitForSingleObject(child.as_raw_handle(), 1000) };
        }
        let _ = child.try_wait();
    }

    fn open_process_for_check(pid: u32) -> Option<OwnedHandle> {
        let raw = unsafe {
            OpenProcess(
                PROCESS_SYNCHRONIZE | PROCESS_QUERY_LIMITED_INFORMATION | PROCESS_TERMINATE,
                0,
                pid,
            )
        };
        if raw.is_null() {
            None
        } else {
            Some(unsafe { OwnedHandle::from_raw_handle(raw) })
        }
    }

    fn is_process_alive(handle: &OwnedHandle) -> bool {
        unsafe { WaitForSingleObject(handle.as_raw_handle(), 0) == WAIT_TIMEOUT }
    }

    #[test]
    fn dropping_job_handle_terminates_child_and_descendant() {
        let mut cmd = Command::new("powershell.exe");
        cmd.args([
            "-NoProfile",
            "-Command",
            "$p = Start-Process powershell.exe -ArgumentList '-NoProfile','-Command','Start-Sleep -Seconds 30' -PassThru -WindowStyle Hidden; Write-Output $p.Id; Start-Sleep -Seconds 30",
        ])
        .stdin(Stdio::null())
        .stdout(Stdio::piped())
        .stderr(Stdio::null());

        let (job, mut child) =
            spawn_in_kill_on_close_job(&mut cmd, JobBreakawayPolicy::DenyBreakaway).unwrap();
        let stdout = child.stdout.take().unwrap();
        let line = read_line_with_timeout(stdout, Duration::from_secs(30))
            .expect("timed out or failed reading grandchild PID");
        let grandchild_pid: u32 = line.trim().parse().expect("expected grandchild PID");

        let grandchild_handle =
            open_process_for_check(grandchild_pid).expect("grandchild should exist before drop");
        assert!(
            is_process_alive(&grandchild_handle),
            "grandchild should be alive before job drop"
        );

        drop(job);

        wait_child_bounded(child, Duration::from_secs(5));
        let wait_gc = unsafe { WaitForSingleObject(grandchild_handle.as_raw_handle(), 5000) };
        assert_eq!(
            wait_gc, WAIT_OBJECT_0,
            "grandchild should be terminated when JobHandle is dropped"
        );
    }

    #[test]
    fn breakaway_grandchild_survives_job_close() {
        let member_def = concat!(
            "[StructLayout(LayoutKind.Sequential, CharSet = CharSet.Unicode)] ",
            "public struct STARTUPINFO { ",
            "public int cb; public string lpReserved; public string lpDesktop; public string lpTitle; ",
            "public int dwX; public int dwY; public int dwXSize; public int dwYSize; ",
            "public int dwXCountChars; public int dwYCountChars; public int dwFillAttribute; ",
            "public int dwFlags; public short wShowWindow; public short cbReserved2; ",
            "public IntPtr lpReserved2; public IntPtr hStdInput; public IntPtr hStdOutput; public IntPtr hStdError; ",
            "} ",
            "[StructLayout(LayoutKind.Sequential)] ",
            "public struct PROCESS_INFORMATION { ",
            "public IntPtr hProcess; public IntPtr hThread; public int dwProcessId; public int dwThreadId; ",
            "} ",
            "[DllImport(\"kernel32.dll\", SetLastError = true, CharSet = CharSet.Unicode)] ",
            "public static extern bool CreateProcessW(",
            "string lpApplicationName, string lpCommandLine, IntPtr lpProcessAttributes, IntPtr lpThreadAttributes, ",
            "bool bInheritHandles, uint dwCreationFlags, IntPtr lpEnvironment, string lpCurrentDirectory, ",
            "ref STARTUPINFO lpStartupInfo, out PROCESS_INFORMATION lpProcessInformation); ",
            "[DllImport(\"kernel32.dll\", SetLastError = true)] ",
            "public static extern bool CloseHandle(IntPtr hObject);"
        );
        let ps_script = format!(
            "$ErrorActionPreference = 'Stop'; \
             Add-Type -Name WinProc -Namespace TgTest -MemberDefinition '{member_def}'; \
             $si = New-Object TgTest.WinProc+STARTUPINFO; \
             $si.cb = [System.Runtime.InteropServices.Marshal]::SizeOf($si); \
             $pi = New-Object TgTest.WinProc+PROCESS_INFORMATION; \
             $flags = 0x01000000 -bor 0x08000000; \
             $exe = (Get-Process -Id $PID).Path; \
             $ok = [TgTest.WinProc]::CreateProcessW($exe, '\"' + $exe + '\" -NoProfile -Command \"Start-Sleep -Seconds 30\"', [IntPtr]::Zero, [IntPtr]::Zero, $false, $flags, [IntPtr]::Zero, [NullString]::Value, [ref]$si, [ref]$pi); \
             if (-not $ok) {{ throw \"CreateProcessW failed: $([System.Runtime.InteropServices.Marshal]::GetLastWin32Error())\" }}; \
             [TgTest.WinProc]::CloseHandle($pi.hThread) | Out-Null; \
             [TgTest.WinProc]::CloseHandle($pi.hProcess) | Out-Null; \
             Write-Output $pi.dwProcessId; \
             Start-Sleep -Seconds 30"
        );

        let mut cmd = Command::new("powershell.exe");
        cmd.args(["-NoProfile", "-Command", &ps_script])
            .stdin(Stdio::null())
            .stdout(Stdio::piped())
            .stderr(Stdio::piped());

        let (job, mut child) =
            spawn_in_kill_on_close_job(&mut cmd, JobBreakawayPolicy::AllowBreakaway).unwrap();
        let stdout = child.stdout.take().unwrap();
        let line = read_line_with_timeout(stdout, Duration::from_secs(30))
            .expect("timed out or failed reading breakaway PID");
        let breakaway_pid: u32 = line.trim().parse().unwrap_or_else(|e| {
            let err_buf = child
                .stderr
                .take()
                .and_then(|stderr| read_to_string_with_timeout(stderr, Duration::from_secs(2)).ok())
                .unwrap_or_default();
            panic!(
                "expected breakaway PID, got {:?} (stderr: {:?}): {e}",
                line, err_buf
            );
        });

        let breakaway_handle =
            open_process_for_check(breakaway_pid).expect("breakaway grandchild should exist");

        // Drop the job handle: the direct child must die, while the breakaway grandchild survives.
        drop(job);
        wait_child_bounded(child, Duration::from_secs(5));

        let survived = is_process_alive(&breakaway_handle);
        // Always clean up the surviving grandchild before asserting.
        unsafe {
            TerminateProcess(breakaway_handle.as_raw_handle(), 0);
            WaitForSingleObject(breakaway_handle.as_raw_handle(), 2000);
        }
        assert!(
            survived,
            "CREATE_BREAKAWAY_FROM_JOB grandchild should survive JobHandle drop"
        );
    }

    #[test]
    fn job_breakaway_policy_sets_and_clears_no_breakaway_env() {
        let mut deny_cmd = Command::new("powershell.exe");
        deny_cmd
            .args([
                "-NoProfile",
                "-Command",
                "Write-Output ($env:TG_WIN_JOB_NO_BREAKAWAY)",
            ])
            .stdin(Stdio::null())
            .stdout(Stdio::piped())
            .stderr(Stdio::null());
        let (deny_job, mut deny_child) =
            spawn_in_kill_on_close_job(&mut deny_cmd, JobBreakawayPolicy::DenyBreakaway).unwrap();
        let deny_stdout = deny_child.stdout.take().unwrap();
        let deny_line = read_line_with_timeout(deny_stdout, Duration::from_secs(30))
            .expect("timed out reading deny env");
        drop(deny_job);
        wait_child_bounded(deny_child, Duration::from_secs(5));
        assert_eq!(deny_line.trim(), "1");

        let mut allow_cmd = Command::new("powershell.exe");
        allow_cmd
            .env("TG_WIN_JOB_NO_BREAKAWAY", "1")
            .args([
                "-NoProfile",
                "-Command",
                "Write-Output ([string]::IsNullOrEmpty($env:TG_WIN_JOB_NO_BREAKAWAY))",
            ])
            .stdin(Stdio::null())
            .stdout(Stdio::piped())
            .stderr(Stdio::null());
        let (allow_job, mut allow_child) =
            spawn_in_kill_on_close_job(&mut allow_cmd, JobBreakawayPolicy::AllowBreakaway).unwrap();
        let allow_stdout = allow_child.stdout.take().unwrap();
        let allow_line = read_line_with_timeout(allow_stdout, Duration::from_secs(30))
            .expect("timed out reading allow env");
        drop(allow_job);
        wait_child_bounded(allow_child, Duration::from_secs(5));
        assert_eq!(allow_line.trim(), "True");
    }
}
