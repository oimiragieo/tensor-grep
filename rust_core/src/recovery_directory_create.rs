//! A handle-returning creation primitive; callers must independently trust and pin the parent.
use super::{
    managed_runtime_acl_reader, managed_runtime_windows_sid::LocalAllocation, windows, windows_file,
};
use std::ffi::OsStr;
use std::fs::File;
use std::io;
use std::os::windows::ffi::OsStrExt;
use std::os::windows::fs::MetadataExt;
use std::os::windows::io::{AsHandle, AsRawHandle, FromRawHandle};
use std::ptr::null_mut;
use windows_sys::Win32::Foundation::{HANDLE, INVALID_HANDLE_VALUE};
use windows_sys::Win32::Security::Authorization::{
    ConvertSecurityDescriptorToStringSecurityDescriptorW,
    ConvertStringSecurityDescriptorToSecurityDescriptorW, GetSecurityInfo, SE_FILE_OBJECT,
};
use windows_sys::Win32::Security::{
    GetSecurityDescriptorControl, DACL_SECURITY_INFORMATION, SECURITY_ATTRIBUTES,
    SE_DACL_AUTO_INHERITED, SE_DACL_PROTECTED,
};
use windows_sys::Win32::Storage::FileSystem::{
    GetFileType, FILE_ATTRIBUTE_REPARSE_POINT, FILE_LIST_DIRECTORY, FILE_READ_ATTRIBUTES,
    FILE_SHARE_READ, FILE_TYPE_DISK, READ_CONTROL, SYNCHRONIZE,
};
use windows_sys::Win32::System::LibraryLoader::{GetModuleHandleW, GetProcAddress};

type CreateDirectory2 =
    unsafe extern "system" fn(*const u16, u32, u32, u32, *const SECURITY_ATTRIBUTES) -> HANDLE;

fn invalid(message: &'static str) -> io::Error {
    io::Error::new(io::ErrorKind::InvalidData, message)
}

fn stage_owner_is_trusted(owner: &str, user: &str) -> bool {
    !user.is_empty() && owner == user
}

fn api() -> Option<CreateDirectory2> {
    let module: Vec<u16> = "kernel32.dll\0".encode_utf16().collect();
    let handle = unsafe { GetModuleHandleW(module.as_ptr()) };
    if handle.is_null() {
        return None;
    }
    let function = unsafe { GetProcAddress(handle, b"CreateDirectory2W\0".as_ptr()) }?;
    // SAFETY: the named Kernel32 export has the five-argument CreateDirectory2W ABI.
    Some(unsafe { std::mem::transmute(function) })
}

fn verified_dacl(file: &File, user: &str) -> io::Result<()> {
    let mut dacl = null_mut();
    let mut descriptor = null_mut();
    let status = unsafe {
        GetSecurityInfo(
            file.as_raw_handle(),
            SE_FILE_OBJECT,
            DACL_SECURITY_INFORMATION,
            null_mut(),
            null_mut(),
            &mut dacl,
            null_mut(),
            &mut descriptor,
        )
    };
    if status != 0 {
        return Err(io::Error::from_raw_os_error(status as i32));
    }
    let _descriptor = unsafe { LocalAllocation::from_raw(descriptor.cast()) }
        .ok_or_else(|| invalid("missing security descriptor"))?;
    if dacl.is_null() {
        return Err(invalid("missing DACL"));
    }
    let (mut control, mut revision) = (0u16, 0u32);
    if unsafe { GetSecurityDescriptorControl(descriptor, &mut control, &mut revision) } == 0
        || control & SE_DACL_PROTECTED == 0
    {
        return Err(invalid("DACL is not protected"));
    }
    let (mut text, mut length) = (null_mut(), 0u32);
    if unsafe {
        ConvertSecurityDescriptorToStringSecurityDescriptorW(
            descriptor,
            1,
            DACL_SECURITY_INFORMATION,
            &mut text,
            &mut length,
        )
    } == 0
    {
        return Err(io::Error::last_os_error());
    }
    let _text = unsafe { LocalAllocation::from_raw(text.cast()) }
        .ok_or_else(|| invalid("DACL text unavailable"))?;
    if length == 0 || length > 4096 {
        return Err(invalid("DACL text length invalid"));
    }
    // SAFETY: the OS returned this LocalFree-owned string and its bounded size.
    let actual = String::from_utf16(unsafe { std::slice::from_raw_parts(text, length as usize) })
        .map_err(|_| invalid("DACL text invalid"))?;
    let actual = actual.trim_end_matches('\0');
    let expected = format!("D:P(A;OICI;FA;;;{user})");
    // Server NTFS may mark this protected DACL auto-inherited without adding ACEs.
    let auto_inherited = expected.replacen("D:P", "D:PAI", 1);
    if actual != expected && (control & SE_DACL_AUTO_INHERITED == 0 || actual != auto_inherited) {
        #[cfg(test)]
        dbg!((actual.starts_with("D:P(A;OICI;FA;;;") as u8) * 2 + actual.ends_with(user) as u8);
        return Err(invalid("protected user-only DACL mismatch"));
    }
    Ok(())
}

fn create_with(
    create: Option<CreateDirectory2>,
    parent: &File,
    name: &str,
    identity: &super::managed_runtime_windows_token::VerifiedTokenIdentity,
) -> io::Result<File> {
    create_with_check(create, parent, name, identity, |_, _| Ok(()))
}

fn create_with_check(
    create: Option<CreateDirectory2>,
    parent: &File,
    name: &str,
    identity: &super::managed_runtime_windows_token::VerifiedTokenIdentity,
    extra_check: impl FnOnce(&File, &str) -> io::Result<()>,
) -> io::Result<File> {
    let user = identity.user_sid();
    let create = create.ok_or_else(|| {
        io::Error::new(io::ErrorKind::Unsupported, "CreateDirectory2W unavailable")
    })?;
    if name.is_empty()
        || !name.is_ascii()
        || name.contains(['\\', '/'])
        || !windows::safe_component(OsStr::new(name))
        || user.len() > 184
        || !user.starts_with("S-1-")
        || !user
            .bytes()
            .all(|byte| byte.is_ascii_digit() || byte == b'-' || byte == b'S')
    {
        return Err(invalid("invalid child name or identity"));
    }
    let metadata = parent.metadata()?;
    if !metadata.is_dir()
        || metadata.file_attributes() & FILE_ATTRIBUTE_REPARSE_POINT != 0
        || unsafe { GetFileType(parent.as_raw_handle()) } != FILE_TYPE_DISK
    {
        return Err(invalid("parent is not an ordinary disk directory"));
    }
    let parent_path = windows_file::final_path_for_handle(parent.as_handle())?;
    let path = parent_path.join(name);
    let wide: Vec<u16> = path.as_os_str().encode_wide().chain(Some(0)).collect();
    if wide.len() > 32_768 {
        return Err(invalid("child path too long"));
    }
    let sddl: Vec<u16> = format!("O:{user}D:P(A;OICI;FA;;;{user})\0")
        .encode_utf16()
        .collect();
    let mut descriptor = null_mut();
    if unsafe {
        ConvertStringSecurityDescriptorToSecurityDescriptorW(
            sddl.as_ptr(),
            1,
            &mut descriptor,
            null_mut(),
        )
    } == 0
    {
        return Err(io::Error::last_os_error());
    }
    let _descriptor = unsafe { LocalAllocation::from_raw(descriptor.cast()) }
        .ok_or_else(|| invalid("descriptor unavailable"))?;
    let attributes = SECURITY_ATTRIBUTES {
        nLength: std::mem::size_of::<SECURITY_ATTRIBUTES>() as u32,
        lpSecurityDescriptor: descriptor,
        bInheritHandle: 0,
    };
    // Do not add a pathname fallback: redirects or preexisting entries must fail before creation.
    let raw = unsafe {
        create(
            wide.as_ptr(),
            FILE_LIST_DIRECTORY | FILE_READ_ATTRIBUTES | READ_CONTROL | SYNCHRONIZE,
            FILE_SHARE_READ,
            1,
            &attributes,
        )
    };
    if raw.is_null() || raw == INVALID_HANDLE_VALUE {
        return Err(io::Error::last_os_error());
    }
    // SAFETY: successful CreateDirectory2W transfers ownership of the returned handle.
    let file = unsafe { File::from_raw_handle(raw) };
    let check = || -> io::Result<()> {
        let info = file.metadata()?;
        if !info.is_dir()
            || info.file_attributes() & FILE_ATTRIBUTE_REPARSE_POINT != 0
            || unsafe { GetFileType(file.as_raw_handle()) } != FILE_TYPE_DISK
            || !windows_file::is_held_direct_child(parent.as_handle(), file.as_handle(), &path)?
        {
            return Err(invalid("created directory identity invalid"));
        }
        let acl = managed_runtime_acl_reader::read_handle_acl(file.as_handle())?;
        if !stage_owner_is_trusted(&acl.owner, user) {
            return Err(invalid("created directory owner invalid"));
        }
        verified_dacl(&file, user)?;
        extra_check(&file, user)
    };
    check().map_err(|_| invalid("created directory could not be validated and may remain"))?;
    Ok(file)
}

pub(super) fn create_child(parent: &File, name: &str) -> io::Result<File> {
    let identity = super::managed_runtime_windows_token::current_token_sids()?;
    create_with(api(), parent, name, &identity)
}

#[cfg(test)]
mod tests {
    use super::super::{
        managed_runtime_windows_sid::LocalAllocation,
        managed_runtime_windows_token::current_token_sids, windows_file,
    };
    use std::fs;
    use std::os::windows::ffi::OsStrExt;
    use std::os::windows::io::FromRawHandle;
    use std::os::windows::process::CommandExt;
    use std::path::Path;

    #[test]
    fn stage_owner_must_be_the_current_user_even_if_default_owner_is_a_group() {
        let user = "S-1-5-21-1";
        assert!(super::stage_owner_is_trusted(user, user));
        assert!(!super::stage_owner_is_trusted("S-1-5-32-544", user));
        assert!(!super::stage_owner_is_trusted("S-1-5-21-2", user));
        assert!(!super::stage_owner_is_trusted("", ""));
    }

    #[test]
    fn directory_creation_explicitly_supplies_current_user_owner() {
        use std::sync::atomic::{AtomicBool, Ordering};
        use windows_sys::Win32::Security::{GetSecurityDescriptorOwner, IsValidSid};
        static OWNER: AtomicBool = AtomicBool::new(false);
        unsafe extern "system" fn stub(
            _: *const u16,
            _: u32,
            _: u32,
            _: u32,
            attributes: *const super::SECURITY_ATTRIBUTES,
        ) -> super::HANDLE {
            let descriptor = unsafe { (*attributes).lpSecurityDescriptor };
            let (mut owner, mut defaulted) = (std::ptr::null_mut(), 0);
            let valid =
                unsafe { GetSecurityDescriptorOwner(descriptor, &mut owner, &mut defaulted) } != 0
                    && !owner.is_null()
                    && unsafe { IsValidSid(owner) } != 0;
            let actual = valid.then(|| unsafe {
                super::super::managed_runtime_windows_sid::sid_to_string(owner, "owner unavailable")
                    .ok()
            });
            OWNER.store(
                actual
                    .flatten()
                    .zip(current_token_sids().ok())
                    .is_some_and(|(sid, identity)| sid == identity.user_sid() && defaulted == 0),
                Ordering::SeqCst,
            );
            super::INVALID_HANDLE_VALUE
        }
        let root = tempfile::tempdir().unwrap();
        let parent = windows_file::open_directory(root.path()).unwrap();
        let identity = current_token_sids().unwrap();
        assert!(super::create_with(Some(stub), &parent, "stage", &identity).is_err());
        assert!(
            OWNER.load(Ordering::SeqCst),
            "directory creation did not explicitly set user owner"
        );
        assert_eq!(fs::read_dir(root.path()).unwrap().count(), 0);
    }

    #[test]
    fn creates_protected_child_and_holds_its_identity() {
        let root = tempfile::tempdir().unwrap();
        let parent = windows_file::open_directory(root.path()).unwrap();
        if super::api().is_none() {
            assert_eq!(
                super::create_child(&parent, "stage").unwrap_err().kind(),
                std::io::ErrorKind::Unsupported
            );
            assert!(!root.path().join("stage").exists());
            return;
        }
        let child = super::create_child(&parent, "stage").unwrap();
        assert!(child.metadata().unwrap().is_dir());
        let nested = super::create_child(&child, "nested").unwrap();
        let stage = root.path().join("stage");
        let nested_path = stage.join("nested");
        assert!(nested.metadata().unwrap().is_dir());
        assert_eq!(
            fs::rename(&stage, root.path().join("moved"))
                .unwrap_err()
                .raw_os_error(),
            Some(32)
        );
        assert_eq!(
            fs::rename(&nested_path, stage.join("other"))
                .unwrap_err()
                .raw_os_error(),
            Some(32)
        );
        drop(nested);
        assert_eq!(
            fs::rename(&nested_path, stage.join("other"))
                .unwrap_err()
                .raw_os_error(),
            Some(32)
        );
        drop(child);
        fs::rename(&nested_path, stage.join("other")).unwrap();
        fs::rename(root.path().join("stage"), root.path().join("moved")).unwrap();
        eprintln!("protected directory and nested handle controls exercised");
    }

    #[test]
    fn refuses_invalid_child_names_before_any_creation() {
        if super::api().is_none() {
            return;
        }
        let root = tempfile::tempdir().unwrap();
        std::fs::create_dir(root.path().join("inner")).unwrap();
        let parent = windows_file::open_directory(root.path()).unwrap();
        drop(super::create_child(&parent, "valid").unwrap());
        for name in ["", "inner\\escape", "inner/escape", "..", "NUL", "C:escape"] {
            assert_eq!(
                super::create_child(&parent, name).unwrap_err().kind(),
                std::io::ErrorKind::InvalidData
            );
            assert!(!root.path().join("inner/escape").exists());
        }
    }

    #[test]
    fn missing_api_fails_without_creating_a_child() {
        let root = tempfile::tempdir().unwrap();
        let parent = windows_file::open_directory(root.path()).unwrap();
        let identity = current_token_sids().unwrap();
        assert_eq!(
            super::create_with(None, &parent, "stage", &identity)
                .unwrap_err()
                .kind(),
            std::io::ErrorKind::Unsupported
        );
        assert!(!root.path().join("stage").exists());
    }

    #[test]
    fn collisions_and_dangling_junction_are_not_followed() {
        if super::api().is_none() {
            return;
        }
        let root = tempfile::tempdir().unwrap();
        let outside = tempfile::tempdir().unwrap();
        fs::create_dir(root.path().join("existing-dir")).unwrap();
        fs::write(root.path().join("existing-file"), b"preserve").unwrap();
        let junction = root.path().join("dangling");
        let missing = outside.path().join("missing");
        let cmd = Path::new(&std::env::var_os("SystemRoot").unwrap())
            .join("System32")
            .join("cmd.exe");
        let output = std::process::Command::new(cmd)
            .creation_flags(0x08000000)
            .args(["/d", "/c", "mklink", "/J"])
            .arg(&junction)
            .arg(&missing)
            .output()
            .unwrap();
        assert!(output.status.success());
        let parent = windows_file::open_directory(root.path()).unwrap();
        drop(super::create_child(&parent, "valid").unwrap());
        for name in ["existing-dir", "existing-file", "dangling"] {
            assert_eq!(
                super::create_child(&parent, name)
                    .unwrap_err()
                    .raw_os_error(),
                Some(183),
                "unexpected error for {name}"
            );
        }
        assert_eq!(
            fs::read(root.path().join("existing-file")).unwrap(),
            b"preserve"
        );
        assert!(!missing.exists());
        drop(parent);
        fs::remove_dir(junction).unwrap();
    }

    #[test]
    fn dacl_check_rejects_foreign_inherit_only_grants() {
        if super::api().is_none() {
            return;
        }
        let identity = current_token_sids().unwrap();
        let root = tempfile::tempdir().unwrap();
        let parent = windows_file::open_directory(root.path()).unwrap();
        let good = super::create_child(&parent, "good").unwrap();
        super::verified_dacl(&good, identity.user_sid()).unwrap();
        drop(good);
        let sddl: Vec<u16> = format!(
            "D:P(A;OICI;FA;;;{})(A;OICIIO;FA;;;WD)\0",
            identity.user_sid()
        )
        .encode_utf16()
        .collect();
        let mut descriptor = std::ptr::null_mut();
        assert_ne!(
            unsafe {
                super::ConvertStringSecurityDescriptorToSecurityDescriptorW(
                    sddl.as_ptr(),
                    1,
                    &mut descriptor,
                    std::ptr::null_mut(),
                )
            },
            0
        );
        let _allocation = unsafe { LocalAllocation::from_raw(descriptor.cast()) }.unwrap();
        let attributes = super::SECURITY_ATTRIBUTES {
            nLength: std::mem::size_of::<super::SECURITY_ATTRIBUTES>() as u32,
            lpSecurityDescriptor: descriptor,
            bInheritHandle: 0,
        };
        let path: Vec<u16> = root
            .path()
            .join("foreign")
            .as_os_str()
            .encode_wide()
            .chain(Some(0))
            .collect();
        let raw = unsafe {
            super::api().unwrap()(
                path.as_ptr(),
                super::FILE_LIST_DIRECTORY
                    | super::FILE_READ_ATTRIBUTES
                    | super::READ_CONTROL
                    | super::SYNCHRONIZE,
                super::FILE_SHARE_READ,
                1,
                &attributes,
            )
        };
        assert!(!raw.is_null() && raw != super::INVALID_HANDLE_VALUE);
        let file = unsafe { std::fs::File::from_raw_handle(raw) };
        assert!(super::verified_dacl(&file, identity.user_sid()).is_err());
    }

    #[test]
    fn validation_failure_leaves_the_created_directory_in_place() {
        if super::api().is_none() {
            return;
        }
        let root = tempfile::tempdir().unwrap();
        let parent = windows_file::open_directory(root.path()).unwrap();
        let identity = current_token_sids().unwrap();
        let result = super::create_with_check(super::api(), &parent, "stage", &identity, |_, _| {
            Err(std::io::Error::other("injected validation failure"))
        });
        assert_eq!(
            result.unwrap_err().to_string(),
            "created directory could not be validated and may remain"
        );
        assert!(root.path().join("stage").is_dir());
    }
}
