//! System tar process containment for an already-authorized recovery stage.
//! The caller must retain the verified, read-only archive and all authorized stage/ancestor
//! handles, and must establish archive integrity, directory confinement, and loader trust.
//! The deadline begins only after CreateProcessW returns. A parent-level watchdog is required
//! for a hard total bound, including process creation and potentially stalled OS calls.

use std::ffi::{c_void, OsStr, OsString};
use std::fs::File;
use std::io;
use std::mem::size_of;
use std::os::windows::ffi::{OsStrExt, OsStringExt};
use std::os::windows::io::{AsHandle, AsRawHandle};
use std::path::PathBuf;
use std::ptr::{null, null_mut};
use std::time::{Duration, Instant};

use super::windows_file;
use windows_sys::Win32::Foundation::{
    CloseHandle, DuplicateHandle, HANDLE as Handle, INVALID_HANDLE_VALUE as INVALID, WAIT_OBJECT_0,
    WAIT_TIMEOUT,
};
use windows_sys::Win32::Storage::FileSystem::{
    CreateFileW, GetFileInformationByHandle, BY_HANDLE_FILE_INFORMATION as FileInfo,
    FILE_FLAG_OPEN_REPARSE_POINT as OPEN_REPARSE, FILE_READ_ATTRIBUTES, FILE_READ_DATA,
    FILE_SHARE_READ as SHARE_READ, OPEN_EXISTING, SYNCHRONIZE,
};
use windows_sys::Win32::System::JobObjects::{
    AssignProcessToJobObject, CreateJobObjectW, JobObjectBasicAccountingInformation,
    JobObjectExtendedLimitInformation, QueryInformationJobObject, SetInformationJobObject,
    TerminateJobObject, JOBOBJECT_BASIC_ACCOUNTING_INFORMATION as Accounting,
    JOBOBJECT_EXTENDED_LIMIT_INFORMATION as ExtendedLimit, JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE,
};
use windows_sys::Win32::System::Pipes::{CreatePipe, PeekNamedPipe};
use windows_sys::Win32::System::SystemInformation::GetSystemDirectoryW;
use windows_sys::Win32::System::Threading::{
    CreateProcessW, DeleteProcThreadAttributeList, GetCurrentProcess, GetExitCodeProcess,
    InitializeProcThreadAttributeList, ResumeThread, TerminateProcess, UpdateProcThreadAttribute,
    WaitForSingleObject, CREATE_NO_WINDOW as NO_WINDOW, CREATE_SUSPENDED as SUSPENDED,
    CREATE_UNICODE_ENVIRONMENT as UNICODE_ENVIRONMENT,
    EXTENDED_STARTUPINFO_PRESENT as EXTENDED_STARTUP, PROCESS_INFORMATION as ProcessInformation,
    PROC_THREAD_ATTRIBUTE_HANDLE_LIST, STARTF_USESTDHANDLES as USE_STD_HANDLES,
    STARTUPINFOEXW as StartupInfoEx,
};
const READ_ACCESS: u32 = SYNCHRONIZE | FILE_READ_ATTRIBUTES | FILE_READ_DATA;

fn error(operation: &str) -> io::Error {
    io::Error::other(format!("{operation}: {}", io::Error::last_os_error()))
}

fn wide(text: &OsStr) -> io::Result<Vec<u16>> {
    let mut value: Vec<_> = text.encode_wide().collect();
    if value.contains(&0) || value.len() >= 32_767 {
        return Err(io::Error::new(
            io::ErrorKind::InvalidInput,
            "invalid Windows string",
        ));
    }
    value.push(0);
    Ok(value)
}

// A single attempt per raw handle: a FALSE return leaves ownership uncertain and must not
// be retried after the handle number may have been reused.
struct Handles(Vec<Handle>);
impl Handles {
    fn new() -> Self {
        Self(Vec::new())
    }
    fn own(&mut self, handle: Handle, operation: &str) -> io::Result<Handle> {
        if handle.is_null() || handle == INVALID {
            return Err(error(operation));
        }
        self.0.push(handle);
        Ok(handle)
    }
    fn close_all(&mut self) -> Vec<String> {
        let mut errors = Vec::new();
        while let Some(handle) = self.0.pop() {
            if unsafe { CloseHandle(handle) } == 0 {
                errors.push(error("CloseHandle").to_string());
            }
        }
        errors
    }
}
impl Drop for Handles {
    fn drop(&mut self) {
        let _ = self.close_all();
    }
}

fn quote(value: &OsStr) -> io::Result<Vec<u16>> {
    let chars = wide(value)?;
    let mut result = vec![b'"' as u16];
    let mut slash = 0;
    for ch in chars.into_iter().take_while(|ch| *ch != 0) {
        if ch == b'\\' as u16 {
            slash += 1;
            continue;
        }
        if ch == b'"' as u16 {
            result.extend(std::iter::repeat(b'\\' as u16).take(slash * 2 + 1));
        } else {
            result.extend(std::iter::repeat(b'\\' as u16).take(slash));
        }
        slash = 0;
        result.push(ch);
    }
    result.extend(std::iter::repeat(b'\\' as u16).take(slash * 2));
    result.push(b'"' as u16);
    Ok(result)
}

fn system_directory() -> io::Result<PathBuf> {
    let mut buffer = vec![0u16; 32_768];
    let length = unsafe { GetSystemDirectoryW(buffer.as_mut_ptr(), buffer.len() as u32) };
    if length == 0 || length as usize >= buffer.len() {
        return Err(error("system directory"));
    }
    Ok(PathBuf::from(OsString::from_wide(
        &buffer[..length as usize],
    )))
}

fn duplicate_read_only(handles: &mut Handles, original: Handle, access: u32) -> io::Result<Handle> {
    let process = unsafe { GetCurrentProcess() };
    let mut duplicate = null_mut();
    if unsafe { DuplicateHandle(process, original, process, &mut duplicate, access, 1, 0) } == 0 {
        return Err(error("DuplicateHandle"));
    }
    handles.own(duplicate, "duplicate handle")
}

fn remaining(deadline: Instant) -> u32 {
    let millis = deadline
        .saturating_duration_since(Instant::now())
        .as_millis();
    millis.min((u32::MAX - 1) as u128) as u32
}

fn active(job: Handle) -> io::Result<u32> {
    let mut info = Accounting::default();
    if unsafe {
        QueryInformationJobObject(
            job,
            JobObjectBasicAccountingInformation,
            (&mut info as *mut Accounting).cast(),
            size_of::<Accounting>() as u32,
            null_mut(),
        )
    } == 0
    {
        return Err(error("job accounting"));
    }
    Ok(info.ActiveProcesses)
}

fn check_output(pipe: Handle) -> io::Result<()> {
    let mut available = 0u32;
    if unsafe { PeekNamedPipe(pipe, null_mut(), 0, null_mut(), &mut available, null_mut()) } == 0 {
        return Err(error("inspect tar output"));
    }
    if available != 0 {
        return Err(io::Error::other("system tar produced stdout or stderr"));
    }
    Ok(())
}

/// Extract only the caller's already-verified, held archive into its authorized private stage.
/// `archive` must be a no-write/no-delete-share pin positioned at offset zero; `stage` must be
/// a retained ordinary protected directory with trusted pinned ancestry, initially empty except
/// for a verified held anchor if one is used. The caller must establish that precondition.
/// The caller keeps all handles alive and checks the extracted tree before any use. Any stdout
/// or stderr fails. No other thread may start a child with unrestricted handle inheritance.
pub(super) fn extract(archive: &File, stage: &File, timeout: Duration) -> io::Result<()> {
    if timeout < Duration::from_millis(100) || timeout > Duration::from_secs(3600) {
        return Err(io::Error::new(
            io::ErrorKind::InvalidInput,
            "invalid extraction deadline",
        ));
    }
    let mut handles = Handles::new();
    let mut attribute_list: *mut c_void = null_mut();
    let mut attribute_storage: Vec<usize> = Vec::new();
    // UpdateProcThreadAttribute retains this buffer until the attribute list is deleted.
    let mut inherited = [null_mut(); 2];
    let mut started = false;
    let mut assigned = false;
    let mut process = null_mut();
    let mut job = null_mut();
    let mut deadline = None;
    let result = (|| -> io::Result<()> {
        let system = system_directory()?;
        let tar = system.join("tar.exe");
        let tar_wide = wide(tar.as_os_str())?;
        let stage_path = windows_file::final_path_for_handle(stage.as_handle())?;
        let mut stage_info = FileInfo::default();
        if unsafe { GetFileInformationByHandle(stage.as_raw_handle().cast(), &mut stage_info) } == 0
            || stage_info.dwFileAttributes & 0x10 == 0
            || stage_info.dwFileAttributes & 0x400 != 0
        {
            return Err(io::Error::other("stage is not an ordinary directory"));
        }
        let tar_handle = handles.own(
            unsafe {
                CreateFileW(
                    tar_wide.as_ptr(),
                    READ_ACCESS,
                    SHARE_READ,
                    null(),
                    OPEN_EXISTING,
                    OPEN_REPARSE,
                    null_mut(),
                )
            },
            "open system tar",
        )?;
        let mut tar_info = FileInfo::default();
        if unsafe { GetFileInformationByHandle(tar_handle, &mut tar_info) } == 0
            || tar_info.dwFileAttributes & (0x10 | 0x400) != 0
        {
            return Err(io::Error::other("system tar is not an ordinary file"));
        }
        let input = duplicate_read_only(&mut handles, archive.as_raw_handle().cast(), READ_ACCESS)?;
        let mut output_read = null_mut();
        let mut output_write = null_mut();
        if unsafe { CreatePipe(&mut output_read, &mut output_write, null(), 0) } == 0 {
            return Err(error("create output pipe"));
        }
        let output_read = handles.own(output_read, "output read handle")?;
        let output_write = handles.own(output_write, "output write handle")?;
        // Duplicate only the pipe's write end and the archive read handle for inheritance.
        let output = duplicate_read_only(&mut handles, output_write, 0x0010_0002)?;
        job = handles.own(unsafe { CreateJobObjectW(null(), null()) }, "create job")?;
        let mut limits = ExtendedLimit::default();
        limits.BasicLimitInformation.LimitFlags = JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE;
        if unsafe {
            SetInformationJobObject(
                job,
                JobObjectExtendedLimitInformation,
                (&limits as *const ExtendedLimit).cast(),
                size_of::<ExtendedLimit>() as u32,
            )
        } == 0
        {
            return Err(error("set job limits"));
        }
        let mut length = 0usize;
        unsafe { InitializeProcThreadAttributeList(null_mut(), 1, 0, &mut length) };
        if length == 0 || length > 1024 * 1024 {
            return Err(io::Error::other("attribute list size"));
        }
        let words = (length + size_of::<usize>() - 1) / size_of::<usize>();
        attribute_storage.resize(words, 0);
        attribute_list = attribute_storage.as_mut_ptr().cast();
        if unsafe { InitializeProcThreadAttributeList(attribute_list, 1, 0, &mut length) } == 0 {
            attribute_list = null_mut();
            return Err(error("initialize attribute list"));
        }
        inherited[0] = input;
        inherited[1] = output;
        if unsafe {
            UpdateProcThreadAttribute(
                attribute_list,
                0,
                PROC_THREAD_ATTRIBUTE_HANDLE_LIST as usize,
                inherited.as_mut_ptr().cast(),
                size_of::<[Handle; 2]>(),
                null_mut(),
                null_mut(),
            )
        } == 0
        {
            return Err(error("set handle list"));
        }
        let mut startup = StartupInfoEx::default();
        startup.lpAttributeList = attribute_list;
        startup.StartupInfo.cb = size_of::<StartupInfoEx>() as u32;
        startup.StartupInfo.dwFlags = USE_STD_HANDLES;
        startup.StartupInfo.hStdInput = input;
        startup.StartupInfo.hStdOutput = output;
        startup.StartupInfo.hStdError = output;
        let mut command = quote(tar.as_os_str())?;
        command.extend(" -x -k -m -f - -C ".encode_utf16());
        command.extend(quote(stage_path.as_os_str())?);
        command.push(0);
        let root = system
            .parent()
            .ok_or_else(|| io::Error::other("missing Windows root"))?;
        let mut env: Vec<u16> = OsString::from(format!("SystemRoot={}", root.display()))
            .encode_wide()
            .collect();
        env.extend([0, 0]);
        let cwd = wide(stage_path.as_os_str())?;
        let mut info = ProcessInformation::default();
        let created = unsafe {
            CreateProcessW(
                tar_wide.as_ptr(),
                command.as_mut_ptr(),
                null(),
                null(),
                1,
                NO_WINDOW | SUSPENDED | EXTENDED_STARTUP | UNICODE_ENVIRONMENT,
                env.as_mut_ptr().cast(),
                cwd.as_ptr(),
                &mut startup.StartupInfo,
                &mut info,
            )
        };
        if created == 0 {
            return Err(error("create system tar"));
        }
        deadline = Some(Instant::now() + timeout);
        started = true;
        process = handles.own(info.hProcess, "tar process handle")?;
        let thread = handles.own(info.hThread, "tar thread handle")?;
        if unsafe { AssignProcessToJobObject(job, process) } == 0 {
            return Err(error("assign tar job"));
        }
        assigned = true;
        if remaining(deadline.unwrap()) == 0 {
            return Err(io::Error::new(
                io::ErrorKind::TimedOut,
                "tar deadline before resume",
            ));
        }
        if unsafe { ResumeThread(thread) } == u32::MAX {
            return Err(error("resume tar"));
        }
        let end = deadline.unwrap();
        let work_end = end - timeout.min(Duration::from_secs(2)) / 4;
        loop {
            check_output(output_read)?;
            let left = remaining(work_end);
            if left == 0 {
                return Err(io::Error::new(io::ErrorKind::TimedOut, "tar deadline"));
            }
            match unsafe { WaitForSingleObject(process, left.min(25)) } {
                WAIT_OBJECT_0 => {
                    if active(job)? == 0 {
                        check_output(output_read)?;
                        break;
                    }
                    std::thread::sleep(Duration::from_millis(10));
                }
                WAIT_TIMEOUT => {}
                _ => return Err(error("wait for tar")),
            }
        }
        let mut exit = 0;
        if unsafe { GetExitCodeProcess(process, &mut exit) } == 0 {
            return Err(error("tar exit code"));
        }
        if exit != 0 {
            return Err(io::Error::other(format!("system tar failed ({exit})")));
        }
        Ok(())
    })();
    if !attribute_list.is_null() {
        unsafe { DeleteProcThreadAttributeList(attribute_list) };
    }
    let mut cleanup = Vec::new();
    if result.is_err() && started {
        let killed = if assigned {
            unsafe { TerminateJobObject(job, 1) }
        } else {
            unsafe { TerminateProcess(process, 1) }
        };
        if killed == 0 {
            cleanup.push(error("terminate tar").to_string());
        }
        let end = deadline.unwrap();
        if unsafe { WaitForSingleObject(process, remaining(end)) } != WAIT_OBJECT_0 {
            cleanup.push("tar process settlement unverified".to_owned());
        }
        if assigned {
            loop {
                match active(job) {
                    Ok(0) => break,
                    Ok(_) if remaining(end) > 0 => std::thread::sleep(Duration::from_millis(10)),
                    Ok(_) => {
                        cleanup.push("tar job settlement timed out".to_owned());
                        break;
                    }
                    Err(exc) => {
                        cleanup.push(format!("tar job settlement unverified: {exc}"));
                        break;
                    }
                }
            }
        }
    }
    cleanup.extend(handles.close_all());
    if cleanup.is_empty() {
        result
    } else {
        Err(io::Error::other(format!(
            "{}; cleanup uncertain: {}",
            result
                .err()
                .map_or_else(|| "tar completed".to_owned(), |e| e.to_string()),
            cleanup.join("; ")
        )))
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use std::fs;
    use std::io::{Read, Seek};

    fn tar_entry(name: &str) -> Vec<u8> {
        let mut tar = vec![0u8; 2048];
        tar[..name.len()].copy_from_slice(name.as_bytes());
        tar[100..108].copy_from_slice(b"0000644\0");
        tar[108..116].copy_from_slice(b"0000000\0");
        tar[116..124].copy_from_slice(b"0000000\0");
        tar[124..136].copy_from_slice(b"00000000001\0");
        tar[136..148].copy_from_slice(b"00000000000\0");
        tar[148..156].fill(b' ');
        tar[156] = b'0';
        tar[257..263].copy_from_slice(b"ustar\0");
        tar[263..265].copy_from_slice(b"00");
        let sum: u32 = tar[..512].iter().map(|byte| u32::from(*byte)).sum();
        tar[148..156].copy_from_slice(format!("{sum:06o}\0 ").as_bytes());
        tar[512] = b'x';
        tar
    }

    #[test]
    fn quotes_windows_arguments_without_losing_backslashes() {
        let result = quote(OsStr::new("a b\\\"c\\")).unwrap();
        assert_eq!(String::from_utf16(&result).unwrap(), "\"a b\\\\\\\"c\\\\\"");
    }

    #[test]
    fn invalid_deadline_is_rejected_before_inspecting_the_stage() {
        let file = File::open(std::env::current_exe().unwrap()).unwrap();
        for timeout in [Duration::ZERO, Duration::from_secs(3601)] {
            assert_eq!(
                extract(&file, &file, timeout).unwrap_err().kind(),
                io::ErrorKind::InvalidInput
            );
        }
    }

    #[test]
    fn system_tar_accepts_valid_archive_and_refuses_warning() {
        for warning in [false, true] {
            let fixture = tempfile::tempdir().unwrap();
            let stage_path = fixture.path().join("stage");
            fs::create_dir(&stage_path).unwrap();
            let archive_path = fixture.path().join("input.tar");
            let mut expected = tar_entry("ok");
            if warning {
                expected.truncate(1024);
                expected.extend(tar_entry("../outside/new-file"));
                assert_eq!(&expected[1024..1043], b"../outside/new-file");
            }
            fs::write(&archive_path, &expected).unwrap();
            let outside = fixture.path().join("outside");
            fs::create_dir(&outside).unwrap();
            fs::write(outside.join("sentinel"), b"preserve").unwrap();
            assert!(!outside.join("new-file").exists());
            let mut archive = windows_file::open_executable(&archive_path).unwrap();
            let mut observed = Vec::new();
            (&mut archive)
                .take((expected.len() + 1) as u64)
                .read_to_end(&mut observed)
                .unwrap();
            assert_eq!(observed, expected);
            let stage = windows_file::open_directory(&stage_path).unwrap();
            archive.rewind().unwrap();
            let result = extract(&archive, &stage, Duration::from_secs(30));
            if !warning {
                result.unwrap();
            } else {
                assert_eq!(
                    result.unwrap_err().to_string(),
                    "system tar produced stdout or stderr"
                );
            }
            assert_eq!(fs::read(stage_path.join("ok")).unwrap(), b"x");
            assert_eq!(fs::read(outside.join("sentinel")).unwrap(), b"preserve");
            assert!(!outside.join("new-file").exists());
        }
    }
}
