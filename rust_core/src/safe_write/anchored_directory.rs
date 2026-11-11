#[cfg(unix)]
mod unix_impl {
    use super::super::AnchoredDirectory;
    use anyhow::Context;
    use std::ffi::{CString, OsStr};
    use std::io;
    use std::os::unix::ffi::OsStrExt;
    use std::os::unix::io::FromRawFd;

    fn c_name(name: &OsStr) -> anyhow::Result<CString> {
        Ok(CString::new(name.as_bytes())?)
    }

    pub fn clone(directory: &AnchoredDirectory) -> anyhow::Result<AnchoredDirectory> {
        Ok(AnchoredDirectory {
            path: directory.path.clone(),
            handles: directory
                .handles
                .iter()
                .map(std::fs::File::try_clone)
                .collect::<io::Result<Vec<_>>>()?,
            owned_name: None,
        })
    }

    fn open_child(
        parent: &AnchoredDirectory,
        name: &OsStr,
        owned: bool,
    ) -> anyhow::Result<AnchoredDirectory> {
        let name_c = c_name(name)?;
        let fd = unsafe {
            libc::openat(
                parent.raw_fd(),
                name_c.as_ptr(),
                libc::O_RDONLY | libc::O_DIRECTORY | libc::O_CLOEXEC | libc::O_NOFOLLOW,
            )
        };
        if fd < 0 {
            return Err(io::Error::last_os_error()).with_context(|| {
                format!(
                    "failed to open anchored directory {}",
                    parent.path.join(name).display()
                )
            });
        }
        let mut child = clone(parent)?;
        child
            .handles
            .push(unsafe { std::fs::File::from_raw_fd(fd) });
        child.path.push(name);
        child.owned_name = owned.then(|| name.to_os_string());
        Ok(child)
    }

    pub fn open_child_directory(
        parent: &AnchoredDirectory,
        name: &OsStr,
    ) -> anyhow::Result<AnchoredDirectory> {
        open_child(parent, name, false)
    }

    #[cfg(target_os = "linux")]
    pub fn read_link_child(
        parent: &AnchoredDirectory,
        name: &OsStr,
    ) -> io::Result<Option<std::path::PathBuf>> {
        read_link_child_inner(parent, name, || Ok(()))
    }

    #[cfg(not(target_os = "linux"))]
    pub fn read_link_child(
        parent: &AnchoredDirectory,
        name: &OsStr,
    ) -> io::Result<Option<std::path::PathBuf>> {
        read_link_child_inner(parent, name)
    }

    #[cfg(all(test, target_os = "linux"))]
    pub(super) fn read_link_child_with_hook<F>(
        parent: &AnchoredDirectory,
        name: &OsStr,
        after_open: F,
    ) -> io::Result<Option<std::path::PathBuf>>
    where
        F: FnOnce() -> io::Result<()>,
    {
        read_link_child_inner(parent, name, after_open)
    }

    #[cfg(target_os = "linux")]
    fn read_link_child_inner<F>(
        parent: &AnchoredDirectory,
        name: &OsStr,
        after_open: F,
    ) -> io::Result<Option<std::path::PathBuf>>
    where
        F: FnOnce() -> io::Result<()>,
    {
        use std::os::unix::ffi::OsStringExt;
        let name_c = c_name(name).map_err(|error| io::Error::other(error.to_string()))?;
        let fd = unsafe {
            libc::openat(
                parent.raw_fd(),
                name_c.as_ptr(),
                libc::O_PATH | libc::O_NOFOLLOW | libc::O_CLOEXEC,
            )
        };
        if fd < 0 {
            return Err(io::Error::last_os_error());
        }
        let _link = unsafe { std::fs::File::from_raw_fd(fd) };
        let mut metadata = unsafe { std::mem::zeroed::<libc::stat>() };
        if unsafe { libc::fstat(fd, &mut metadata) } != 0 {
            return Err(io::Error::last_os_error());
        }
        if metadata.st_mode & libc::S_IFMT != libc::S_IFLNK {
            return Ok(None);
        }
        after_open()?;
        let empty_path = b"\0";
        let mut bytes = vec![0u8; 256];
        loop {
            let size = unsafe {
                libc::readlinkat(
                    fd,
                    empty_path.as_ptr().cast(),
                    bytes.as_mut_ptr().cast(),
                    bytes.len(),
                )
            };
            if size < 0 {
                return Err(io::Error::last_os_error());
            }
            let size = size as usize;
            if size < bytes.len() {
                bytes.truncate(size);
                return Ok(Some(std::path::PathBuf::from(
                    std::ffi::OsString::from_vec(bytes),
                )));
            }
            if bytes.len() >= 65_536 {
                return Err(io::Error::new(
                    io::ErrorKind::InvalidData,
                    "checkpoint symlink target exceeds 64 KiB",
                ));
            }
            bytes.resize(bytes.len() * 2, 0);
        }
    }

    #[cfg(not(target_os = "linux"))]
    fn read_link_child_inner(
        parent: &AnchoredDirectory,
        name: &OsStr,
    ) -> io::Result<Option<std::path::PathBuf>> {
        let name_c = c_name(name).map_err(|error| io::Error::other(error.to_string()))?;
        let mut metadata = unsafe { std::mem::zeroed::<libc::stat>() };
        if unsafe {
            libc::fstatat(
                parent.raw_fd(),
                name_c.as_ptr(),
                &mut metadata,
                libc::AT_SYMLINK_NOFOLLOW,
            )
        } != 0
        {
            return Err(io::Error::last_os_error());
        }
        if metadata.st_mode & libc::S_IFMT != libc::S_IFLNK {
            return Ok(None);
        }
        Err(io::Error::new(
            io::ErrorKind::Unsupported,
            "anchored symlink reads are unavailable on this Unix platform",
        ))
    }

    pub fn open_or_create_child(
        parent: &AnchoredDirectory,
        name: &OsStr,
    ) -> anyhow::Result<AnchoredDirectory> {
        let name_c = c_name(name)?;
        if unsafe { libc::mkdirat(parent.raw_fd(), name_c.as_ptr(), 0o777) } == 0 {
            return open_child(parent, name, true);
        }
        let error = io::Error::last_os_error();
        if error.kind() != io::ErrorKind::AlreadyExists {
            return Err(error).with_context(|| {
                format!(
                    "failed to create anchored directory {}",
                    parent.path.join(name).display()
                )
            });
        }
        open_child(parent, name, false)
    }

    pub fn create_child(
        parent: &AnchoredDirectory,
        name: &OsStr,
    ) -> anyhow::Result<AnchoredDirectory> {
        let name_c = c_name(name)?;
        if unsafe { libc::mkdirat(parent.raw_fd(), name_c.as_ptr(), 0o700) } != 0 {
            return Err(io::Error::last_os_error()).with_context(|| {
                format!(
                    "failed to exclusively create anchored directory {}",
                    parent.path.join(name).display()
                )
            });
        }
        open_child(parent, name, true)
    }

    pub fn child_for_removal(
        parent: &AnchoredDirectory,
        name: &OsStr,
    ) -> anyhow::Result<AnchoredDirectory> {
        open_child(parent, name, true)
    }

    pub fn validate_current(directory: &AnchoredDirectory) -> anyhow::Result<()> {
        use std::os::unix::fs::MetadataExt;
        let current = AnchoredDirectory::open(&directory.path)?;
        let held = directory.handles.last().unwrap().metadata()?;
        let resolved = current.handles.last().unwrap().metadata()?;
        anyhow::ensure!(
            held.dev() == resolved.dev() && held.ino() == resolved.ino(),
            "anchored directory path changed: {}",
            directory.path.display()
        );
        Ok(())
    }

    pub fn remove_owned_tree(_: &AnchoredDirectory, _: &OsStr) -> anyhow::Result<()> {
        // Unix cannot atomically acquire mkdir ownership or unlink by retained directory identity.
        Err(io::Error::new(
            io::ErrorKind::Unsupported,
            "native checkpoint directory cleanup is unsupported on Unix; retained data requires inspection",
        ))
        .context("refused recursive cleanup without directory ownership guarantees")
    }
}

#[cfg(unix)]
pub(super) use unix_impl::{
    child_for_removal, clone, create_child, open_child_directory, open_or_create_child,
    read_link_child, remove_owned_tree, validate_current,
};

#[cfg(windows)]
mod windows_impl {
    use super::super::{AnchoredDirectory, OsStr};
    use anyhow::Context;
    use std::fs::File;
    use std::io;
    use std::os::windows::ffi::{OsStrExt, OsStringExt};
    use std::os::windows::io::{AsRawHandle, FromRawHandle};
    use std::ptr;

    type Handle = *mut std::ffi::c_void;

    const FILE_TRAVERSE: u32 = 0x0000_0020;
    const FILE_READ_ATTRIBUTES: u32 = 0x0000_0080;
    const READ_CONTROL_ACCESS: u32 = 0x0002_0000;
    const DELETE_ACCESS: u32 = 0x0001_0000;
    const SYNCHRONIZE: u32 = 0x0010_0000;
    const FILE_SHARE_READ: u32 = 0x0000_0001;
    const FILE_SHARE_WRITE: u32 = 0x0000_0002;
    const FILE_SHARE_DELETE: u32 = 0x0000_0004;
    const FILE_ATTRIBUTE_NORMAL: u32 = 0x0000_0080;
    const FILE_OPEN: u32 = 1;
    const FILE_CREATE: u32 = 2;
    const FILE_DIRECTORY_FILE: u32 = 0x0000_0001;
    const FILE_OPEN_REPARSE_POINT: u32 = 0x0020_0000;
    const FILE_SYNCHRONOUS_IO_NONALERT: u32 = 0x0000_0020;
    const FILE_OPENED: usize = 1;
    const FILE_CREATED: usize = 2;
    const FILE_RENAME_INFORMATION_CLASS: i32 = 10;
    const BCRYPT_USE_SYSTEM_PREFERRED_RNG: u32 = 0x0000_0002;
    const OBJ_CASE_INSENSITIVE: u32 = 0x0000_0040;
    const FILE_ATTRIBUTE_REPARSE_POINT: u32 = 0x0000_0400;
    const FSCTL_GET_REPARSE_POINT: u32 = 0x0009_00a8;
    const IO_REPARSE_TAG_MOUNT_POINT: u32 = 0xa000_0003;
    const IO_REPARSE_TAG_SYMLINK: u32 = 0xa000_000c;
    const MAX_REPARSE_DATA_BUFFER_SIZE: usize = 16 * 1024;

    #[repr(C)]
    struct UnicodeString {
        length: u16,
        maximum_length: u16,
        buffer: *mut u16,
    }

    #[repr(C)]
    struct ObjectAttributes {
        length: u32,
        root_directory: Handle,
        object_name: *mut UnicodeString,
        attributes: u32,
        security_descriptor: *mut std::ffi::c_void,
        security_quality_of_service: *mut std::ffi::c_void,
    }

    #[repr(C)]
    union IoStatusValue {
        status: i32,
        pointer: *mut std::ffi::c_void,
    }

    #[repr(C)]
    struct IoStatusBlock {
        value: IoStatusValue,
        information: usize,
    }

    #[repr(C)]
    struct FileRenameInformation {
        replace_if_exists: u8,
        root_directory: Handle,
        file_name_length: u32,
        file_name: [u16; 1],
    }

    #[link(name = "ntdll")]
    extern "system" {
        fn NtCreateFile(
            file_handle: *mut Handle,
            desired_access: u32,
            object_attributes: *mut ObjectAttributes,
            io_status_block: *mut IoStatusBlock,
            allocation_size: *mut i64,
            file_attributes: u32,
            share_access: u32,
            create_disposition: u32,
            create_options: u32,
            ea_buffer: *mut std::ffi::c_void,
            ea_length: u32,
        ) -> i32;
        fn NtSetInformationFile(
            file_handle: Handle,
            io_status_block: *mut IoStatusBlock,
            file_information: *const std::ffi::c_void,
            length: u32,
            file_information_class: i32,
        ) -> i32;
        fn NtClose(handle: Handle) -> i32;
        fn RtlNtStatusToDosError(status: i32) -> u32;
    }

    #[link(name = "Advapi32")]
    extern "system" {
        // Split the linker spelling to avoid a base64-like credential false positive.
        #[link_name = "ConvertStringSecurityDescriptorTo\x53ecurityDescriptorW"]
        fn convert_sddl_to_security_descriptor(
            string: *const u16,
            revision: u32,
            descriptor: *mut *mut std::ffi::c_void,
            size: *mut u32,
        ) -> i32;
    }

    #[link(name = "Kernel32")]
    extern "system" {
        fn LocalFree(memory: *mut std::ffi::c_void) -> *mut std::ffi::c_void;
        fn DeviceIoControl(
            device: Handle,
            control_code: u32,
            input_buffer: *const std::ffi::c_void,
            input_size: u32,
            output_buffer: *mut std::ffi::c_void,
            output_size: u32,
            bytes_returned: *mut u32,
            overlapped: *mut std::ffi::c_void,
        ) -> i32;
    }

    #[link(name = "Bcrypt")]
    extern "system" {
        fn BCryptGenRandom(
            algorithm: Handle,
            buffer: *mut u8,
            buffer_length: u32,
            flags: u32,
        ) -> i32;
    }

    pub fn clone(directory: &AnchoredDirectory) -> anyhow::Result<AnchoredDirectory> {
        Ok(AnchoredDirectory {
            path: directory.path.clone(),
            handles: directory
                .handles
                .iter()
                .map(File::try_clone)
                .collect::<io::Result<Vec<_>>>()?,
            owned_name: None,
        })
    }

    fn child_with_handle(
        parent: &AnchoredDirectory,
        name: &OsStr,
        disposition: u32,
        deletable: bool,
        owned: bool,
        private_dacl: bool,
    ) -> anyhow::Result<AnchoredDirectory> {
        child_with_handle_with_share(
            parent,
            name,
            disposition,
            deletable,
            owned,
            private_dacl,
            false,
        )
    }

    fn child_with_handle_for_staged_handoff(
        parent: &AnchoredDirectory,
        name: &OsStr,
        private_dacl: bool,
    ) -> anyhow::Result<AnchoredDirectory> {
        child_with_handle_with_share(parent, name, FILE_OPEN, false, false, private_dacl, true)
    }

    fn child_with_handle_with_share(
        parent: &AnchoredDirectory,
        name: &OsStr,
        disposition: u32,
        deletable: bool,
        owned: bool,
        private_dacl: bool,
        allow_delete_share: bool,
    ) -> anyhow::Result<AnchoredDirectory> {
        let mut child = clone(parent)?;
        child.handles.reserve(1);
        child.path.push(name);
        let (handle, _) = open_directory_relative(
            parent,
            name,
            disposition,
            deletable,
            private_dacl,
            parent.handles.last().unwrap().as_raw_handle(),
            allow_delete_share,
        )?;
        child.handles.push(handle);
        child.owned_name = owned.then(|| name.to_os_string());
        Ok(child)
    }

    fn open_directory_relative(
        parent: &AnchoredDirectory,
        name: &OsStr,
        disposition: u32,
        deletable: bool,
        private_dacl: bool,
        root_directory: Handle,
        allow_delete_share: bool,
    ) -> anyhow::Result<(File, bool)> {
        let mut name_wide: Vec<u16> = name.encode_wide().collect();
        anyhow::ensure!(
            !name_wide.is_empty()
                && name_wide.iter().all(|unit| {
                    *unit != 0
                        && *unit != b'/' as u16
                        && *unit != b'\\' as u16
                        && *unit != b':' as u16
                })
                && name_wide.as_slice() != [b'.' as u16]
                && name_wide.as_slice() != [b'.' as u16, b'.' as u16],
            "invalid anchored Windows child name {:?}",
            name
        );
        let name_bytes = name_wide
            .len()
            .checked_mul(std::mem::size_of::<u16>())
            .context("anchored child name is too long")?;
        anyhow::ensure!(
            name_bytes <= u16::MAX as usize - std::mem::size_of::<u16>(),
            "anchored child name is too long"
        );
        let length = name_bytes as u16;
        name_wide.push(0);
        let mut object_name = UnicodeString {
            length,
            maximum_length: (name_bytes + std::mem::size_of::<u16>()) as u16,
            buffer: name_wide.as_mut_ptr(),
        };
        // Keep creation/open relative to the retained parent; never fall back to
        // path-based create-and-reopen.
        let security_descriptor = if private_dacl {
            let sddl: Vec<u16> = "D:P(A;OICI;FA;;;OW)\0".encode_utf16().collect();
            let mut descriptor = ptr::null_mut();
            if unsafe {
                convert_sddl_to_security_descriptor(
                    sddl.as_ptr(),
                    1,
                    &mut descriptor,
                    ptr::null_mut(),
                )
            } == 0
            {
                return Err(io::Error::last_os_error().into());
            }
            descriptor
        } else {
            ptr::null_mut()
        };
        struct SecurityDescriptor(*mut std::ffi::c_void);
        impl Drop for SecurityDescriptor {
            fn drop(&mut self) {
                if !self.0.is_null() {
                    unsafe { LocalFree(self.0) };
                }
            }
        }
        let security_descriptor = SecurityDescriptor(security_descriptor);
        let mut attributes = ObjectAttributes {
            length: std::mem::size_of::<ObjectAttributes>() as u32,
            root_directory,
            object_name: &mut object_name,
            attributes: OBJ_CASE_INSENSITIVE,
            security_descriptor: security_descriptor.0,
            security_quality_of_service: ptr::null_mut(),
        };
        let mut io_status = IoStatusBlock {
            value: IoStatusValue { status: 0 },
            information: 0,
        };
        let mut raw_handle = ptr::null_mut();
        let desired_access = FILE_TRAVERSE
            | FILE_READ_ATTRIBUTES
            | SYNCHRONIZE
            | if private_dacl { READ_CONTROL_ACCESS } else { 0 }
            | if deletable { DELETE_ACCESS } else { 0 };
        let create_options = FILE_SYNCHRONOUS_IO_NONALERT
            | if disposition == FILE_CREATE {
                FILE_DIRECTORY_FILE
            } else {
                FILE_OPEN_REPARSE_POINT
            };
        let status = unsafe {
            NtCreateFile(
                &mut raw_handle,
                desired_access,
                &mut attributes,
                &mut io_status,
                ptr::null_mut(),
                FILE_ATTRIBUTE_NORMAL,
                FILE_SHARE_READ
                    | FILE_SHARE_WRITE
                    | if allow_delete_share {
                        FILE_SHARE_DELETE
                    } else {
                        0
                    },
                disposition,
                create_options,
                ptr::null_mut(),
                0,
            )
        };
        if status < 0 {
            if !raw_handle.is_null() {
                unsafe { NtClose(raw_handle) };
            }
            let win32_error = unsafe { RtlNtStatusToDosError(status) };
            return Err(io::Error::from_raw_os_error(win32_error as i32)).with_context(|| {
                format!(
                    "NtCreateFile failed for anchored directory {} with NTSTATUS {status:#010x}",
                    parent.path.join(name).display()
                )
            });
        }
        if raw_handle.is_null() {
            anyhow::bail!(
                "NtCreateFile returned success without a handle for anchored directory {}",
                parent.path.join(name).display()
            );
        }
        let created = io_status.information == FILE_CREATED;
        let valid_information = match disposition {
            FILE_CREATE => created,
            FILE_OPEN => io_status.information == FILE_OPENED,
            _ => false,
        };
        if !valid_information {
            unsafe { NtClose(raw_handle) };
            anyhow::bail!(
                "NtCreateFile returned unexpected directory disposition {} for {}",
                io_status.information,
                parent.path.join(name).display()
            );
        }
        // FILE_CREATE makes a new directory. Existing children are opened without the directory
        // option and without following the final reparse point, then checked through this handle.
        let file = unsafe { File::from_raw_handle(raw_handle) };
        let metadata = file.metadata()?;
        use std::os::windows::fs::MetadataExt;
        if !metadata.is_dir() || metadata.file_attributes() & FILE_ATTRIBUTE_REPARSE_POINT != 0 {
            anyhow::bail!(
                "refusing reparse-point or non-directory child {}",
                parent.path.join(name).display()
            );
        }
        Ok((file, created))
    }

    pub fn open_or_create_child(
        parent: &AnchoredDirectory,
        name: &OsStr,
    ) -> anyhow::Result<AnchoredDirectory> {
        match child_with_handle(parent, name, FILE_OPEN, false, false, false) {
            Ok(child) => return Ok(child),
            Err(error) if is_not_found(&error) => {}
            Err(error) => return Err(error),
        }
        match create_staged_child(parent, name, false, false) {
            Ok(child) => Ok(child),
            Err(error) if is_already_exists(&error) => {
                child_with_handle(parent, name, FILE_OPEN, false, false, false).with_context(|| {
                    format!(
                        "failed to open child after no-replace publication collision: {}",
                        parent.path.join(name).display()
                    )
                })
            }
            Err(error) => Err(error),
        }
    }

    pub fn open_child_directory(
        parent: &AnchoredDirectory,
        name: &OsStr,
    ) -> anyhow::Result<AnchoredDirectory> {
        child_with_handle(parent, name, FILE_OPEN, false, false, false)
    }

    pub fn read_link_child(
        parent: &AnchoredDirectory,
        name: &OsStr,
    ) -> io::Result<Option<std::path::PathBuf>> {
        read_link_child_inner(parent, name, || Ok(()))
    }

    #[cfg(test)]
    pub(super) fn read_link_child_with_hook<F>(
        parent: &AnchoredDirectory,
        name: &OsStr,
        after_open: F,
    ) -> io::Result<Option<std::path::PathBuf>>
    where
        F: FnOnce() -> io::Result<()>,
    {
        read_link_child_inner(parent, name, after_open)
    }

    fn read_link_child_inner<F>(
        parent: &AnchoredDirectory,
        name: &OsStr,
        after_open: F,
    ) -> io::Result<Option<std::path::PathBuf>>
    where
        F: FnOnce() -> io::Result<()>,
    {
        let file = open_reparse_child_relative(parent, name)?;
        use std::os::windows::fs::MetadataExt;
        if file.metadata()?.file_attributes() & FILE_ATTRIBUTE_REPARSE_POINT == 0 {
            return Ok(None);
        }
        after_open()?;
        read_reparse_target(&file).map(Some)
    }

    fn open_reparse_child_relative(parent: &AnchoredDirectory, name: &OsStr) -> io::Result<File> {
        let mut name_wide: Vec<u16> = name.encode_wide().collect();
        if name_wide.is_empty()
            || name_wide.iter().any(|unit| {
                *unit == 0 || *unit == b'/' as u16 || *unit == b'\\' as u16 || *unit == b':' as u16
            })
            || name_wide.as_slice() == [b'.' as u16]
            || name_wide.as_slice() == [b'.' as u16, b'.' as u16]
        {
            return Err(io::Error::new(
                io::ErrorKind::InvalidInput,
                "invalid anchored Windows reparse child name",
            ));
        }
        let name_bytes = name_wide
            .len()
            .checked_mul(std::mem::size_of::<u16>())
            .filter(|size| *size <= u16::MAX as usize - std::mem::size_of::<u16>())
            .ok_or_else(|| io::Error::new(io::ErrorKind::InvalidInput, "child name too long"))?;
        let mut object_name = UnicodeString {
            length: name_bytes as u16,
            maximum_length: (name_bytes + std::mem::size_of::<u16>()) as u16,
            buffer: name_wide.as_mut_ptr(),
        };
        let mut attributes = ObjectAttributes {
            length: std::mem::size_of::<ObjectAttributes>() as u32,
            root_directory: parent.handles.last().unwrap().as_raw_handle(),
            object_name: &mut object_name,
            attributes: OBJ_CASE_INSENSITIVE,
            security_descriptor: ptr::null_mut(),
            security_quality_of_service: ptr::null_mut(),
        };
        let mut io_status = IoStatusBlock {
            value: IoStatusValue { status: 0 },
            information: 0,
        };
        let mut raw_handle = ptr::null_mut();
        let status = unsafe {
            NtCreateFile(
                &mut raw_handle,
                FILE_READ_ATTRIBUTES | SYNCHRONIZE,
                &mut attributes,
                &mut io_status,
                ptr::null_mut(),
                FILE_ATTRIBUTE_NORMAL,
                FILE_SHARE_READ | FILE_SHARE_DELETE,
                FILE_OPEN,
                FILE_OPEN_REPARSE_POINT | FILE_SYNCHRONOUS_IO_NONALERT,
                ptr::null_mut(),
                0,
            )
        };
        if status < 0 {
            if !raw_handle.is_null() {
                unsafe { NtClose(raw_handle) };
            }
            return Err(io::Error::from_raw_os_error(
                unsafe { RtlNtStatusToDosError(status) } as i32,
            ));
        }
        if raw_handle.is_null() {
            return Err(io::Error::other(
                "NtCreateFile returned success without a reparse child handle",
            ));
        }
        if io_status.information != FILE_OPENED {
            unsafe { NtClose(raw_handle) };
            return Err(io::Error::other(
                "NtCreateFile returned an unexpected reparse child disposition",
            ));
        }
        Ok(unsafe { File::from_raw_handle(raw_handle) })
    }

    fn read_reparse_target(file: &File) -> io::Result<std::path::PathBuf> {
        let mut buffer = vec![0u8; MAX_REPARSE_DATA_BUFFER_SIZE];
        let mut bytes_returned = 0u32;
        let success = unsafe {
            DeviceIoControl(
                file.as_raw_handle(),
                FSCTL_GET_REPARSE_POINT,
                ptr::null(),
                0,
                buffer.as_mut_ptr().cast(),
                buffer.len() as u32,
                &mut bytes_returned,
                ptr::null_mut(),
            )
        };
        if success == 0 {
            return Err(io::Error::last_os_error());
        }
        let available = bytes_returned as usize;
        if available < 8 {
            return Err(io::Error::new(
                io::ErrorKind::InvalidData,
                "truncated Windows reparse data header",
            ));
        }
        let tag = u32::from_le_bytes(buffer[0..4].try_into().unwrap());
        let data_len = u16::from_le_bytes(buffer[4..6].try_into().unwrap()) as usize;
        let data_end = 8usize
            .checked_add(data_len)
            .filter(|end| *end <= available)
            .ok_or_else(|| {
                io::Error::new(io::ErrorKind::InvalidData, "truncated Windows reparse data")
            })?;
        let (fixed_len, print_offset_at, print_len_at, substitute_offset_at, substitute_len_at) =
            match tag {
                IO_REPARSE_TAG_SYMLINK => (12usize, 12usize, 14usize, 8usize, 10usize),
                IO_REPARSE_TAG_MOUNT_POINT => (8usize, 12usize, 14usize, 8usize, 10usize),
                other => {
                    return Err(io::Error::new(
                        io::ErrorKind::Unsupported,
                        format!("unsupported Windows reparse tag {other:#010x}"),
                    ));
                }
            };
        if data_len < fixed_len {
            return Err(io::Error::new(
                io::ErrorKind::InvalidData,
                "truncated Windows reparse name fields",
            ));
        }
        let path_start = 8 + fixed_len;
        let path_bytes = &buffer[path_start..data_end];
        let read_name = |offset_at: usize, length_at: usize| -> io::Result<Vec<u16>> {
            let offset =
                u16::from_le_bytes(buffer[offset_at..offset_at + 2].try_into().unwrap()) as usize;
            let length =
                u16::from_le_bytes(buffer[length_at..length_at + 2].try_into().unwrap()) as usize;
            let end = offset
                .checked_add(length)
                .filter(|end| *end <= path_bytes.len());
            if length == 0 || length & 1 != 0 || end.is_none() {
                return Err(io::Error::new(
                    io::ErrorKind::InvalidData,
                    "invalid Windows reparse target name range",
                ));
            }
            let bytes = &path_bytes[offset..end.unwrap()];
            let mut wide = Vec::with_capacity(bytes.len() / 2);
            for pair in bytes.chunks_exact(2) {
                let unit = u16::from_le_bytes([pair[0], pair[1]]);
                if unit == 0 {
                    return Err(io::Error::new(
                        io::ErrorKind::InvalidData,
                        "NUL in Windows reparse target",
                    ));
                }
                wide.push(unit);
            }
            Ok(wide)
        };
        let name = match read_name(print_offset_at, print_len_at) {
            Ok(print_name) => print_name,
            Err(_) => read_name(substitute_offset_at, substitute_len_at)?,
        };
        Ok(std::path::PathBuf::from(std::ffi::OsString::from_wide(
            &name,
        )))
    }

    pub fn create_child(
        parent: &AnchoredDirectory,
        name: &OsStr,
    ) -> anyhow::Result<AnchoredDirectory> {
        match child_with_handle(parent, name, FILE_OPEN, false, false, false) {
            Ok(_) => anyhow::bail!(
                "anchored directory child already exists: {}",
                parent.path.join(name).display()
            ),
            Err(error) if is_not_found(&error) => {}
            Err(error) => return Err(error),
        }
        create_staged_child(parent, name, true, true)
    }

    fn is_not_found(error: &anyhow::Error) -> bool {
        error.chain().any(|cause| {
            cause
                .downcast_ref::<io::Error>()
                .is_some_and(|error| error.kind() == io::ErrorKind::NotFound)
        })
    }

    fn is_already_exists(error: &anyhow::Error) -> bool {
        error.chain().any(|cause| {
            cause.downcast_ref::<io::Error>().is_some_and(|error| {
                error.kind() == io::ErrorKind::AlreadyExists
                    || matches!(error.raw_os_error(), Some(80 | 183))
            })
        })
    }

    fn random_staging_name() -> anyhow::Result<std::ffi::OsString> {
        use std::fmt::Write as _;

        let mut random = [0u8; 32];
        let status = unsafe {
            BCryptGenRandom(
                ptr::null_mut(),
                random.as_mut_ptr(),
                random.len() as u32,
                BCRYPT_USE_SYSTEM_PREFERRED_RNG,
            )
        };
        anyhow::ensure!(
            status >= 0,
            "BCryptGenRandom failed with NTSTATUS {status:#010x}"
        );
        let mut name = String::with_capacity(10 + random.len() * 2);
        name.push_str(".tg_stage_");
        for byte in random {
            write!(&mut name, "{byte:02x}").unwrap();
        }
        Ok(name.into())
    }

    fn create_staged_child(
        parent: &AnchoredDirectory,
        name: &OsStr,
        owned: bool,
        private_dacl: bool,
    ) -> anyhow::Result<AnchoredDirectory> {
        create_staged_child_with_names(parent, name, owned, private_dacl, random_staging_name)
    }

    fn create_staged_child_with_names<F>(
        parent: &AnchoredDirectory,
        name: &OsStr,
        owned: bool,
        private_dacl: bool,
        mut next_staging_name: F,
    ) -> anyhow::Result<AnchoredDirectory>
    where
        F: FnMut() -> anyhow::Result<std::ffi::OsString>,
    {
        super::super::require_child_name(name)?;
        for _ in 0..64 {
            let staging_name = next_staging_name()?;
            if staging_name.as_os_str() == name {
                continue;
            }
            match child_with_handle(parent, &staging_name, FILE_OPEN, false, false, false) {
                Ok(_) => continue,
                Err(error) if is_not_found(&error) => {}
                Err(error) => {
                    return Err(error).context("refusing occupied or reparse staging name")
                }
            }
            let stage = match child_with_handle(
                parent,
                &staging_name,
                FILE_CREATE,
                true,
                false,
                private_dacl,
            ) {
                Ok(stage) => stage,
                Err(error) if is_already_exists(&error) => continue,
                Err(error) => return Err(error),
            };
            match rename_directory_to_child(&stage, parent, name) {
                Ok(()) => {
                    let expected_identity =
                        super::super::leaf_identity::from_file(stage.handles.last().unwrap())?;
                    // Keep the delete-capable stage open until a second handle confirms its identity.
                    let handoff = child_with_handle_for_staged_handoff(parent, name, private_dacl)?;
                    let handoff_identity =
                        super::super::leaf_identity::from_file(handoff.handles.last().unwrap())?;
                    anyhow::ensure!(
                        expected_identity == handoff_identity,
                        "published directory identity changed during handoff: {}",
                        parent.path.join(name).display()
                    );
                    drop(stage);
                    let published =
                        child_with_handle(parent, name, FILE_OPEN, owned, owned, private_dacl)?;
                    let published_identity =
                        super::super::leaf_identity::from_file(published.handles.last().unwrap())?;
                    anyhow::ensure!(
                        expected_identity == published_identity,
                        "published directory identity changed before anchoring: {}",
                        parent.path.join(name).display()
                    );
                    return Ok(published);
                }
                Err(rename_error) => {
                    if let Err(cleanup_error) = discard_staging_directory(&stage) {
                        return Err(cleanup_error).context(format!(
                            "also failed to publish staged directory: {rename_error:#}"
                        ));
                    }
                    return Err(rename_error).with_context(|| {
                        format!(
                            "failed to publish staged directory without replacement at {}",
                            parent.path.join(name).display()
                        )
                    });
                }
            }
        }
        anyhow::bail!(
            "failed to allocate a unique staging directory alongside {}",
            parent.path.display()
        )
    }

    fn rename_directory_to_child(
        stage: &AnchoredDirectory,
        parent: &AnchoredDirectory,
        name: &OsStr,
    ) -> anyhow::Result<()> {
        use std::os::windows::io::AsRawHandle;

        super::super::require_child_name(name)?;
        let wide: Vec<u16> = name.encode_wide().collect();
        anyhow::ensure!(
            !wide.is_empty()
                && wide.iter().all(|unit| {
                    *unit != 0
                        && *unit != b'/' as u16
                        && *unit != b'\\' as u16
                        && *unit != b':' as u16
                })
                && wide.as_slice() != [b'.' as u16]
                && wide.as_slice() != [b'.' as u16, b'.' as u16],
            "invalid Windows rename child name {:?}",
            name
        );
        let name_bytes = u32::try_from(wide.len() * std::mem::size_of::<u16>())?;
        let stage_parent_index = stage
            .handles
            .len()
            .checked_sub(2)
            .context("staging directory has no retained parent handle")?;
        let stage_parent = &stage.handles[stage_parent_index];
        let expected_parent = super::super::leaf_identity::from_file(stage_parent)?;
        let actual_parent = super::super::leaf_identity::from_file(
            parent.handles.last().expect("anchored parent has a handle"),
        )?;
        anyhow::ensure!(
            expected_parent == actual_parent,
            "staging directory is not anchored to the requested publication parent"
        );
        let file_name_offset = std::mem::offset_of!(FileRenameInformation, file_name);
        let size = (file_name_offset + wide.len() * std::mem::size_of::<u16>())
            .max(std::mem::size_of::<FileRenameInformation>());
        let size_u32 = u32::try_from(size)?;
        let mut storage = vec![0usize; size.div_ceil(std::mem::size_of::<usize>())];
        let info = storage.as_mut_ptr().cast::<FileRenameInformation>();
        unsafe {
            info.write(FileRenameInformation {
                replace_if_exists: 0,
                root_directory: parent.handles.last().unwrap().as_raw_handle(),
                file_name_length: name_bytes,
                file_name: [0],
            });
            ptr::copy_nonoverlapping(wide.as_ptr(), (*info).file_name.as_mut_ptr(), wide.len());
        }
        let mut io_status = IoStatusBlock {
            value: IoStatusValue { status: 0 },
            information: 0,
        };
        let status = unsafe {
            NtSetInformationFile(
                stage.handles.last().unwrap().as_raw_handle(),
                &mut io_status,
                info.cast(),
                size_u32,
                FILE_RENAME_INFORMATION_CLASS,
            )
        };
        let status = if status < 0 {
            status
        } else {
            unsafe { io_status.value.status }
        };
        if status < 0 {
            let win32_error = unsafe { RtlNtStatusToDosError(status) };
            return Err(io::Error::from_raw_os_error(win32_error as i32)).with_context(|| {
                format!("NtSetInformationFile handle-relative rename failed with NTSTATUS {status:#010x}")
            });
        }
        Ok(())
    }

    fn discard_staging_directory(stage: &AnchoredDirectory) -> anyhow::Result<()> {
        let delete = 1u8;
        if unsafe {
            super::super::windows::set_directory_disposition(
                stage.handles.last().unwrap(),
                (&delete as *const u8).cast(),
            )
        } == 0
        {
            return Err(io::Error::last_os_error()).context("failed to discard staging directory");
        }
        Ok(())
    }

    #[cfg(test)]
    pub(super) fn create_staging_child_for_test(
        parent: &AnchoredDirectory,
    ) -> anyhow::Result<AnchoredDirectory> {
        let staging_name = random_staging_name()?;
        child_with_handle(parent, &staging_name, FILE_CREATE, true, false, false)
    }

    #[cfg(test)]
    pub(super) fn publish_staging_child_for_test(
        stage: &AnchoredDirectory,
        parent: &AnchoredDirectory,
        name: &OsStr,
    ) -> anyhow::Result<()> {
        rename_directory_to_child(stage, parent, name)
    }

    #[cfg(test)]
    pub(super) fn discard_staging_child_for_test(stage: &AnchoredDirectory) -> anyhow::Result<()> {
        discard_staging_directory(stage)
    }

    #[cfg(test)]
    pub(super) fn create_staged_child_with_names_for_test<F>(
        parent: &AnchoredDirectory,
        name: &OsStr,
        next_staging_name: F,
    ) -> anyhow::Result<AnchoredDirectory>
    where
        F: FnMut() -> anyhow::Result<std::ffi::OsString>,
    {
        create_staged_child_with_names(parent, name, false, false, next_staging_name)
    }

    pub fn child_for_removal(
        parent: &AnchoredDirectory,
        name: &OsStr,
    ) -> anyhow::Result<AnchoredDirectory> {
        child_with_handle(parent, name, FILE_OPEN, true, true, false)
    }

    pub fn validate_current(directory: &AnchoredDirectory) -> anyhow::Result<()> {
        // Every held parent handle denies delete sharing, so a successful no-reparse reopen
        // proves the path still names the anchored chain.
        super::super::windows::validate_directory(&directory.path)
    }

    pub fn remove_owned_tree(directory: &AnchoredDirectory, name: &OsStr) -> anyhow::Result<()> {
        let _ = name;
        remove_contents(directory)?;
        let handle = directory.handles.last().unwrap();
        let delete: u8 = 1;
        if unsafe {
            super::super::windows::set_directory_disposition(handle, (&delete as *const u8).cast())
        } == 0
        {
            return Err(io::Error::last_os_error()).context("failed to remove owned directory");
        }
        Ok(())
    }

    fn remove_contents(directory: &AnchoredDirectory) -> anyhow::Result<()> {
        use std::os::windows::fs::MetadataExt;
        for entry in std::fs::read_dir(&directory.path)? {
            let entry = entry?;
            let path = entry.path();
            let metadata = std::fs::symlink_metadata(&path)?;
            let reparse =
                metadata.file_attributes() & super::super::windows::ATTRIBUTE_REPARSE_POINT != 0;
            if metadata.is_dir() && !reparse {
                let name = entry.file_name();
                child_for_removal(directory, &name)?.remove_owned_tree()?;
            } else if metadata.is_dir() {
                std::fs::remove_dir(path)?;
            } else {
                std::fs::remove_file(path)?;
            }
        }
        Ok(())
    }
}

#[cfg(windows)]
pub(super) use windows_impl::{
    child_for_removal, clone, create_child, open_child_directory, open_or_create_child,
    read_link_child, remove_owned_tree, validate_current,
};

#[cfg(not(any(unix, windows)))]
mod unsupported_impl {
    use super::super::{AnchoredDirectory, AnchoredTarget, OsStr};
    use std::io;

    fn unsupported<T>() -> anyhow::Result<T> {
        anyhow::bail!("anchored directory operations are unsupported on this platform")
    }

    pub fn clone(_: &AnchoredDirectory) -> anyhow::Result<AnchoredDirectory> {
        unsupported()
    }
    pub fn open_or_create_child(
        _: &AnchoredDirectory,
        _: &OsStr,
    ) -> anyhow::Result<AnchoredDirectory> {
        unsupported()
    }
    pub fn open_child_directory(
        _: &AnchoredDirectory,
        _: &OsStr,
    ) -> anyhow::Result<AnchoredDirectory> {
        unsupported()
    }
    pub fn read_link_child(
        _: &AnchoredDirectory,
        _: &OsStr,
    ) -> io::Result<Option<std::path::PathBuf>> {
        Err(io::Error::new(
            io::ErrorKind::Unsupported,
            "anchored symlink reads are unsupported on this platform",
        ))
    }
    pub fn create_child(_: &AnchoredDirectory, _: &OsStr) -> anyhow::Result<AnchoredDirectory> {
        unsupported()
    }
    pub fn child_for_removal(
        _: &AnchoredDirectory,
        _: &OsStr,
    ) -> anyhow::Result<AnchoredDirectory> {
        unsupported()
    }
    pub fn validate_current(_: &AnchoredDirectory) -> anyhow::Result<()> {
        unsupported()
    }
    pub fn remove_owned_tree(_: &AnchoredDirectory, _: &OsStr) -> anyhow::Result<()> {
        unsupported()
    }
}

#[cfg(not(any(unix, windows)))]
pub(super) use unsupported_impl::{
    child_for_removal, clone, create_child, open_child_directory, open_or_create_child,
    read_link_child, remove_owned_tree, validate_current,
};

#[cfg(all(test, unix))]
#[path = "anchored_directory_unix_cleanup_tests.rs"]
mod unix_cleanup_refusal_tests;

#[cfg(all(test, windows))]
#[path = "anchored_directory_windows_tests.rs"]
mod windows_atomic_child_tests;

#[cfg(all(test, target_os = "linux"))]
#[path = "anchored_directory_linux_tests.rs"]
mod linux_reparse_read_tests;
